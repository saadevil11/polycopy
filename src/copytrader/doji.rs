//! Doji filter for the 99c scanner — a Rust port of the "Doji signals" indicator, used as
//! a third (NON-directional) AVOIDANCE gate alongside liquidity levels and FVGs.
//!
//! A 5-minute candle is a **doji** when its body is tiny relative to its range:
//!   `|open - close| <= (high - low) * precision`   (indicator default precision = 0.15).
//! A doji = indecision / a near-tie close — for a 99c up/down market the outcome is fragile
//! and can flip. ENTRY: block **both** Up and Down (don't buy a near-tie at 0.99, whichever
//! way it leans). STOP (held position): **DIRECTIONAL** — dump only if the doji leans
//! AGAINST the side we hold (held Up + a **bearish** doji, or held Down + a **bullish**
//! doji). A doji leaning our way (or neutral) is left to ride — it may still pay the full
//! 1.0, so we don't throw away a winner (this is the BNB fix).
//!
//! The indicator only "fires" at candle CLOSE, but we don't need to wait: the Binance kline
//! WebSocket streams the FORMING candle's open / high / low / **close (= current price)** on
//! every tick, so we evaluate the doji formula LIVE. Near the window end (when the 0.99 buy
//! fires) the candle is ~fully formed, so the live read matches the final one; and we keep
//! evaluating it for the stop. Only the **5m** candle (the market window) is used. Coins
//! with no Binance USDT pair (e.g. HYPE) have no data → the signal is not evaluable.
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::protocol::Message;
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::copytrader::FilterSignal;

const TF: &str = "5m";
const LIMIT: u32 = 2; // we only need the current forming candle

#[derive(Default, Clone)]
struct Candle {
    open: f64,
    high: f64,
    low: f64,
    close: f64, // forming candle's current close = latest price
}

pub struct Doji {
    enabled: bool,         // entry filter (SCANNER_DOJI_FILTER): block a 0.99 buy on a doji
    stop_enabled: bool,    // non-directional held-position stop (SCANNER_DOJI_STOP_ENABLED)
    min_move_enabled: bool, // the scanner's min-move entry gate needs this candle feed (open+price)
    precision: f64,
    rest_url: String,
    ws_url: String,
    poll: Duration,
    coins: Vec<String>,
    http: reqwest::Client,
    cur: Mutex<HashMap<String, Candle>>, // coin -> forming 5m candle
}

impl Doji {
    pub fn new(cfg: &Config) -> Self {
        let coins: Vec<String> = cfg
            .ninetynine_assets
            .iter()
            .filter(|a| binance_symbol(a).is_some())
            .cloned()
            .collect();
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(8))
            .user_agent("polybotshadow/0.1")
            .build()
            .expect("http client");
        Doji {
            enabled: cfg.scanner_doji_filter,
            stop_enabled: cfg.scanner_doji_stop_enabled,
            min_move_enabled: cfg.scanner_min_move_pct > 0.0,
            precision: cfg.scanner_doji_precision.max(0.0001),
            rest_url: cfg.binance_rest_url.clone(),
            ws_url: cfg.binance_ws_url.clone(),
            poll: cfg.scanner_liq_poll,
            coins,
            http,
            cur: Mutex::new(HashMap::new()),
        }
    }

    /// Live forming-candle OHLC via the Binance kline WS (sub-second); a slow REST seed so
    /// there's data before the WS warms up and to self-heal a WS gap.
    pub async fn run(self: Arc<Self>) {
        if !self.enabled && !self.stop_enabled && !self.min_move_enabled {
            info!("🕯️ doji: OFF (no entry filter, doji stop, or min-move gate)");
            return;
        }
        if self.min_move_enabled {
            info!("🕯️ doji: streaming the 5m candle feed for the scanner's min-move entry gate");
        }
        if self.enabled {
            info!(
                "🕯️ doji ENTRY filter: ON — skip a 0.99 buy if {}'s 5m candle is a doji (|open-close| ≤ {:.0}% of range), live via Binance WS",
                self.coins.join("/"),
                self.precision * 100.0
            );
        }
        if self.stop_enabled {
            info!("🕯️ doji STOP: ON (SCANNER_DOJI_STOP_ENABLED) — dump a HELD position with a GTC sell @ the exit price on ANY doji (non-directional), while the market window is live");
        }
        let work: Vec<(String, &'static str)> = self
            .coins
            .iter()
            .filter_map(|c| binance_symbol(c).map(|s| (c.clone(), s)))
            .collect();
        if work.is_empty() {
            warn!("🕯️ doji filter: no coins with a Binance pair — nothing to track");
            return;
        }

        for (coin, sym) in &work {
            self.refresh(coin, sym).await;
        }
        {
            let me = self.clone();
            let work = work.clone();
            tokio::spawn(async move {
                let mut t = interval(me.poll);
                t.tick().await;
                loop {
                    t.tick().await;
                    for (coin, sym) in &work {
                        me.refresh(coin, sym).await;
                    }
                }
            });
        }
        self.ws_loop().await;
    }

    async fn ws_loop(&self) {
        let streams: Vec<String> = self
            .coins
            .iter()
            .filter_map(|c| binance_symbol(c))
            .map(|s| format!("{}@kline_{}", s.to_lowercase(), TF))
            .collect();
        if streams.is_empty() {
            return;
        }
        let url = format!("{}?streams={}", self.ws_url, streams.join("/"));
        let mut backoff = 2u64;
        loop {
            match self.ws_session(&url, streams.len()).await {
                Ok(()) => backoff = 2,
                Err(e) => {
                    warn!("🕯️ doji: Binance kline WS ended ({e}); reconnecting in {backoff}s");
                    sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(30);
                }
            }
        }
    }

    async fn ws_session(&self, url: &str, n: usize) -> anyhow::Result<()> {
        let (ws, _) = tokio_tungstenite::connect_async(url).await?;
        let (mut write, mut read) = ws.split();
        info!("🕯️ doji: Binance kline WS connected ({n} streams, live price)");
        while let Some(msg) = read.next().await {
            match msg? {
                Message::Text(t) => self.handle_kline(&t),
                Message::Ping(p) => {
                    let _ = write.send(Message::Pong(p)).await;
                }
                Message::Close(_) => return Ok(()),
                _ => {}
            }
        }
        Ok(())
    }

    /// One kline frame → update that coin's forming 5m candle (open/high/low/close). The
    /// `c` field is the candle's current close (= latest price), which is what we need.
    fn handle_kline(&self, text: &str) {
        let v: Value = match serde_json::from_str(text) {
            Ok(v) => v,
            Err(_) => return,
        };
        let data = v.get("data").unwrap_or(&v);
        let k = match data.get("k") {
            Some(k) => k,
            None => return,
        };
        let sym = data.get("s").and_then(|x| x.as_str()).unwrap_or("");
        let interval = k.get("i").and_then(|x| x.as_str()).unwrap_or("");
        if interval != TF {
            return;
        }
        let open = k.get("o").and_then(num_f64).unwrap_or(0.0);
        let high = k.get("h").and_then(num_f64).unwrap_or(0.0);
        let low = k.get("l").and_then(num_f64).unwrap_or(0.0);
        let close = k.get("c").and_then(num_f64).unwrap_or(0.0);
        let coin = match sym.strip_suffix("USDT") {
            Some(c) => c.to_lowercase(),
            None => return,
        };
        if open <= 0.0 || high <= 0.0 || low <= 0.0 || close <= 0.0 {
            return;
        }
        self.cur.lock().unwrap().insert(coin, Candle { open, high, low, close });
    }

    async fn refresh(&self, coin: &str, sym: &str) {
        let url = format!("{}/api/v3/klines?symbol={}&interval={}&limit={}", self.rest_url, sym, TF, LIMIT);
        let v: Value = match self.http.get(&url).send().await {
            Ok(r) => match r.json().await {
                Ok(v) => v,
                Err(e) => {
                    debug!("🕯️ doji: {sym} decode failed: {e}");
                    return;
                }
            },
            Err(e) => {
                warn!("🕯️ doji: Binance fetch {sym} failed ({e}) — check api.binance.com reachability/geo");
                return;
            }
        };
        let rows = match v.as_array() {
            Some(a) if !a.is_empty() => a,
            _ => return,
        };
        // Last row = the still-forming candle: [openTime,"open","high","low","close",...].
        let row = &rows[rows.len() - 1];
        let open = row.get(1).and_then(num_f64).unwrap_or(0.0);
        let high = row.get(2).and_then(num_f64).unwrap_or(0.0);
        let low = row.get(3).and_then(num_f64).unwrap_or(0.0);
        let close = row.get(4).and_then(num_f64).unwrap_or(0.0);
        if open > 0.0 && high > 0.0 && low > 0.0 && close > 0.0 {
            self.cur.lock().unwrap().insert(coin.to_string(), Candle { open, high, low, close });
        }
    }

    /// ENTRY signal: a doji blocks BOTH sides (non-directional — don't buy a near-tie at
    /// 0.99, whichever way it leans). `evaluable` false with no pair / no data; a disabled
    /// filter is trivially evaluable and blocks nothing.
    pub fn signal(&self, coin: &str) -> FilterSignal {
        if !self.enabled {
            return FilterSignal { block_up: false, block_down: false, evaluable: true };
        }
        match self.eval(coin) {
            Some((doji, _bull, _bear)) => FilterSignal { block_up: doji, block_down: doji, evaluable: true },
            None => FilterSignal::default(),
        }
    }

    /// DIRECTIONAL STOP signal for HELD positions: dump only if the candle is a doji AND
    /// leaning AGAINST that side — `block_up` = a BEARISH doji (close<open, Up losing),
    /// `block_down` = a BULLISH doji (close>open, Down losing). A doji leaning the held
    /// side's way (or neutral) blocks nothing → hold it (it may still pay 1.0). Always
    /// directional; the blanket toggle does not apply to it.
    pub fn stop_signal(&self, coin: &str) -> FilterSignal {
        if !self.enabled {
            return FilterSignal::default();
        }
        match self.eval(coin) {
            Some((doji, bullish, bearish)) => FilterSignal {
                block_up: doji && bearish,
                block_down: doji && bullish,
                evaluable: true,
            },
            None => FilterSignal::default(),
        }
    }

    /// Signed % of the live forming 5m candle's current price vs its open:
    /// (close-open)/open*100. Positive = Up leading, negative = Down leading. For the
    /// scanner's min-move entry gate (how far the favorite is from the window open). None if
    /// no Binance pair / no data / open<=0. Independent of the entry-filter `enabled` flag.
    pub fn lead_pct(&self, coin: &str) -> Option<f64> {
        if binance_symbol(coin).is_none() {
            return None;
        }
        let map = self.cur.lock().unwrap();
        match map.get(coin) {
            Some(c) if c.open > 0.0 && c.close > 0.0 => Some((c.close - c.open) / c.open * 100.0),
            _ => None,
        }
    }

    /// NON-directional: true if the coin's live forming 5m candle is currently a doji
    /// (regardless of lean). For the SCANNER_DOJI_STOP_ENABLED held-position stop. Independent
    /// of the entry-filter `enabled` flag; needs the candle feed (runs when either toggle is
    /// on). None = not evaluable (no Binance pair / no data).
    pub fn is_doji(&self, coin: &str) -> Option<bool> {
        self.eval(coin).map(|(doji, _, _)| doji)
    }

    /// (is_doji, bullish, bearish) for the live forming candle, or None if not evaluable
    /// (no Binance pair / no live data / degenerate zero-range candle).
    fn eval(&self, coin: &str) -> Option<(bool, bool, bool)> {
        if binance_symbol(coin).is_none() {
            return None;
        }
        let map = self.cur.lock().unwrap();
        let c = match map.get(coin) {
            Some(c) if c.high > c.low && c.high > 0.0 => c,
            _ => return None,
        };
        let doji = (c.open - c.close).abs() <= (c.high - c.low) * self.precision;
        Some((doji, c.close > c.open, c.close < c.open))
    }
}

fn num_f64(v: &Value) -> Option<f64> {
    match v {
        Value::String(s) => s.parse().ok(),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Polymarket asset code -> Binance USDT spot symbol. None = no Binance pair (HYPE).
fn binance_symbol(asset: &str) -> Option<&'static str> {
    match asset {
        "btc" => Some("BTCUSDT"),
        "eth" => Some("ETHUSDT"),
        "sol" => Some("SOLUSDT"),
        "xrp" => Some("XRPUSDT"),
        "bnb" => Some("BNBUSDT"),
        "doge" => Some("DOGEUSDT"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    fn is_doji(open: f64, high: f64, low: f64, close: f64, precision: f64) -> bool {
        high > low && (open - close).abs() <= (high - low) * precision
    }

    #[test]
    fn doji_formula() {
        // body 0.1 vs range 2.0 → 5% ≤ 15% → doji
        assert!(is_doji(100.0, 101.0, 99.0, 100.1, 0.15));
        // body 0.5 vs range 2.0 → 25% > 15% → NOT a doji
        assert!(!is_doji(100.0, 101.0, 99.0, 100.5, 0.15));
        // exactly at the boundary: body 0.3 vs range 2.0 = 15% → doji (<=)
        assert!(is_doji(100.0, 101.0, 99.0, 100.3, 0.15));
        // a strong directional candle (big body) → not a doji
        assert!(!is_doji(100.0, 100.6, 99.9, 100.5, 0.15));
        // degenerate flat candle (range 0) → not evaluable as a doji
        assert!(!is_doji(100.0, 100.0, 100.0, 100.0, 0.15));
    }

    // Mirrors stop_signal's directional mapping (the counterintuitive part: a BEARISH doji
    // blocks UP, because close<open means Up is losing).
    fn stop_blocks(doji: bool, bullish: bool, bearish: bool) -> (bool, bool) {
        (doji && bearish, doji && bullish) // (block_up, block_down)
    }

    #[test]
    fn directional_doji_stop() {
        // doji + BEARISH (close<open) → dump held Up, not Down
        assert_eq!(stop_blocks(true, false, true), (true, false));
        // doji + BULLISH (close>open) → dump held Down, not Up  (the BNB case: held Up holds)
        assert_eq!(stop_blocks(true, true, false), (false, true));
        // a doji leaning our way never dumps us: bullish doji + held Up → block_up=false
        assert_eq!(stop_blocks(true, true, false).0, false);
        // not a doji → nothing dumps
        assert_eq!(stop_blocks(false, true, false), (false, false));
        assert_eq!(stop_blocks(false, false, true), (false, false));
    }
}
