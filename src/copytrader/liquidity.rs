//! Liquidity-level filter for the 99c scanner — a Rust port of the "Liquidity
//! Levels (Sonarlab)" TradingView indicator, used as an AVOIDANCE gate.
//!
//! For each coin it pulls Binance candles on **5m / 15m / 30m** (native intervals,
//! no resampling), finds swing-high / swing-low **pivots** (15 left / 5 right bars =
//! the indicator's defaults) as liquidity levels, drops a level once price **wicks**
//! through it (mitigation by high/low, not close — so we react the moment price
//! touches it), and answers one question: **has the coin's current 5-minute candle
//! (the market's window) touched any active level pooled from all three timeframes
//! since the window opened?** If so, the scanner SKIPS that 5m market's 0.99 buy
//! (price at a liquidity level = reversal risk). The 5m candle's range covers the
//! whole window, so one touch disqualifies the market for its entire life.
//!
//! Data: the live price (forming-candle high/low) streams from the **Binance combined
//! kline WebSocket** (`BINANCE_WS_URL`, sub-second) so a touch is caught in real time;
//! the slower-moving **levels** are recomputed from the klines **REST** every
//! `SCANNER_LIQ_POLL_SECS` (which also self-heals any WS gap). Coins with no Binance
//! USDT pair (e.g. HYPE) have no data → `clear_to_trade` is false (we don't trade what
//! we can't filter).
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::protocol::Message;
use tracing::{debug, info, warn};

use crate::config::Config;

const TFS: [&str; 3] = ["5m", "15m", "30m"];
const LEFT: usize = 15;
const RIGHT: usize = 5;
const LIMIT: u32 = 120; // candles fetched per (coin,tf): plenty for 15+5 pivots

#[derive(Default, Clone)]
struct Series {
    highs: Vec<f64>, // active (un-mitigated) high liquidity levels
    lows: Vec<f64>,  // active low liquidity levels
    cur_hi: f64,     // current (forming) candle running high (0 = no data yet)
    cur_lo: f64,     // current (forming) candle running low
}

pub struct Liquidity {
    enabled: bool,
    rest_url: String,
    ws_url: String,
    poll: Duration,
    /// coins we filter (those with a Binance USDT pair), e.g. ["btc","eth",...].
    coins: Vec<String>,
    http: reqwest::Client,
    /// "coin:tf" -> Series.
    series: Mutex<HashMap<String, Series>>,
}

impl Liquidity {
    pub fn new(cfg: &Config) -> Self {
        // Only coins with a Binance symbol get filtered (HYPE has none → dropped).
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
        Liquidity {
            enabled: cfg.scanner_liquidity_filter,
            rest_url: cfg.binance_rest_url.clone(),
            ws_url: cfg.binance_ws_url.clone(),
            poll: cfg.scanner_liq_poll,
            coins,
            http,
            series: Mutex::new(HashMap::new()),
        }
    }

    /// Keep the filter's view of every (coin, timeframe) fresh: the live forming-candle
    /// price via the Binance kline WebSocket (sub-second), the levels via a slower REST
    /// refresh (which also seeds the closed-bar history and self-heals WS gaps).
    pub async fn run(self: Arc<Self>) {
        if !self.enabled {
            info!("🪙 liquidity filter: OFF (SCANNER_LIQUIDITY_FILTER=false)");
            return;
        }
        info!(
            "🪙 liquidity filter: ON — skip a 0.99 buy if {}'s current 5m candle has touched ANY swing level pooled from {} (pivots {}L/{}R, wick) since the window opened; live price via Binance WS, levels refreshed every {}s",
            self.coins.join("/"),
            TFS.join("/"),
            LEFT,
            RIGHT,
            self.poll.as_secs()
        );
        let work: Vec<(String, &'static str, &'static str)> = self
            .coins
            .iter()
            .filter_map(|c| binance_symbol(c).map(|s| (c.clone(), s)))
            .flat_map(|(c, s)| TFS.iter().map(move |tf| (c.clone(), *tf, s)))
            .collect();
        if work.is_empty() {
            warn!("🪙 liquidity filter: no coins with a Binance pair — nothing to track");
            return;
        }

        // Seed levels + price once via REST so the filter has data before the WS warms up.
        for (coin, tf, sym) in &work {
            self.refresh(coin, tf, sym).await;
        }

        // REST levels refresh loop (slow; the WS owns the fast live price).
        {
            let me = self.clone();
            let work = work.clone();
            tokio::spawn(async move {
                let mut t = interval(me.poll);
                t.tick().await; // first tick is immediate; we already seeded above
                loop {
                    t.tick().await;
                    for (coin, tf, sym) in &work {
                        me.refresh(coin, tf, sym).await;
                    }
                }
            });
        }

        // Live forming-candle price via the Binance kline WebSocket (reconnect forever).
        self.ws_loop().await;
    }

    /// Connect the Binance combined kline stream and feed live forming-candle highs/lows
    /// into `series`. Reconnects with backoff; runs until the process exits.
    async fn ws_loop(&self) {
        let streams: Vec<String> = self
            .coins
            .iter()
            .filter_map(|c| binance_symbol(c))
            .flat_map(|s| TFS.iter().map(move |tf| format!("{}@kline_{}", s.to_lowercase(), tf)))
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
                    warn!("🪙 liquidity: Binance kline WS ended ({e}); reconnecting in {backoff}s");
                    sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(30);
                }
            }
        }
    }

    async fn ws_session(&self, url: &str, n: usize) -> anyhow::Result<()> {
        let (ws, _) = tokio_tungstenite::connect_async(url).await?;
        let (mut write, mut read) = ws.split();
        info!("🪙 liquidity: Binance kline WS connected ({n} streams, live price)");
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

    /// One kline frame → update that (coin, tf)'s forming-candle running high/low. We use
    /// the live high/low (wick) every tick, so a touch registers the instant it happens.
    /// Levels (highs/lows) are owned by the REST refresh; we never touch them here.
    fn handle_kline(&self, text: &str) {
        let v: Value = match serde_json::from_str(text) {
            Ok(v) => v,
            Err(_) => return,
        };
        // Combined-stream frames wrap the event in {"stream":..,"data":{..}}.
        let data = v.get("data").unwrap_or(&v);
        let k = match data.get("k") {
            Some(k) => k,
            None => return,
        };
        let sym = data.get("s").and_then(|x| x.as_str()).unwrap_or("");
        let interval = k.get("i").and_then(|x| x.as_str()).unwrap_or("");
        let hi = k.get("h").and_then(num_f64).unwrap_or(0.0);
        let lo = k.get("l").and_then(num_f64).unwrap_or(0.0);
        if sym.is_empty() || interval.is_empty() || hi <= 0.0 || lo <= 0.0 {
            return;
        }
        let coin = match sym.strip_suffix("USDT") {
            Some(c) => c.to_lowercase(),
            None => return,
        };
        let mut map = self.series.lock().unwrap();
        let s = map.entry(format!("{coin}:{interval}")).or_default();
        s.cur_hi = hi;
        s.cur_lo = lo;
    }

    async fn refresh(&self, coin: &str, tf: &str, sym: &str) {
        let url = format!("{}/api/v3/klines?symbol={}&interval={}&limit={}", self.rest_url, sym, tf, LIMIT);
        let v: Value = match self.http.get(&url).send().await {
            Ok(r) => match r.json().await {
                Ok(v) => v,
                Err(e) => {
                    debug!("🪙 liquidity: {sym} {tf} decode failed: {e}");
                    return;
                }
            },
            Err(e) => {
                warn!("🪙 liquidity: Binance fetch {sym} {tf} failed ({e}) — check api.binance.com reachability/geo");
                return;
            }
        };
        let rows = match v.as_array() {
            Some(a) if !a.is_empty() => a,
            _ => return,
        };
        // Each row: [openTime, "open","high","low","close", ...]. The LAST row is the
        // still-forming candle → use it for the live price, exclude it from pivots.
        let mut bars: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
        for row in &rows[..rows.len() - 1] {
            let hi = row.get(2).and_then(num_f64).unwrap_or(0.0);
            let lo = row.get(3).and_then(num_f64).unwrap_or(0.0);
            if hi > 0.0 && lo > 0.0 {
                bars.push((hi, lo));
            }
        }
        let forming = &rows[rows.len() - 1];
        let cur_hi = forming.get(2).and_then(num_f64).unwrap_or(0.0);
        let cur_lo = forming.get(3).and_then(num_f64).unwrap_or(0.0);
        let (highs, lows) = pivots(&bars);

        let mut map = self.series.lock().unwrap();
        let s = map.entry(format!("{coin}:{tf}")).or_default();
        s.highs = highs;
        s.lows = lows;
        s.cur_hi = cur_hi;
        s.cur_lo = cur_lo;
    }

    /// True if it's safe to trade this coin's 0.99. The check: the CURRENT 5-minute
    /// candle (= the market's window; its running high/low is the price range from the
    /// window's open until now) must NOT have touched ANY active level pooled from the
    /// 5m + 15m + 30m timeframes. Touch = the 5m candle's high reached up to a high
    /// level, or its low reached down to a low level (wick). False if it touched OR data
    /// is missing (we never trade a coin we can't evaluate — HYPE, or Binance down).
    /// Because the 5m candle's range grows over the whole window, a touch anywhere in
    /// the window keeps the market disqualified until that window closes.
    pub fn clear_to_trade(&self, coin: &str) -> bool {
        if !self.enabled {
            return true;
        }
        if binance_symbol(coin).is_none() {
            return false; // no candle source (e.g. HYPE) → don't trade
        }
        let map = self.series.lock().unwrap();
        // The 5m candle is the market's window — test ITS range against all levels.
        let (cur_hi, cur_lo) = match map.get(&format!("{coin}:5m")) {
            Some(s) if s.cur_hi > 0.0 => (s.cur_hi, s.cur_lo),
            _ => return false, // 5m not live yet → conservative skip
        };
        // Pool every active level across all timeframes (5m/15m/30m).
        let (mut highs, mut lows): (Vec<f64>, Vec<f64>) = (Vec::new(), Vec::new());
        for tf in TFS {
            match map.get(&format!("{coin}:{tf}")) {
                Some(s) => {
                    highs.extend(s.highs.iter().copied());
                    lows.extend(s.lows.iter().copied());
                }
                None => return false, // a timeframe's levels not seeded → conservative skip
            }
        }
        !level_touched(cur_hi, cur_lo, &highs, &lows)
    }

    /// For the post-entry LIQUIDITY STOP: true iff there IS live data and the coin's
    /// current 5m candle has touched a pooled level. Unlike `clear_to_trade`, missing
    /// data returns false (we never DUMP a held position just because data is absent).
    pub fn at_level(&self, coin: &str) -> bool {
        if !self.enabled || binance_symbol(coin).is_none() {
            return false;
        }
        let map = self.series.lock().unwrap();
        let (cur_hi, cur_lo) = match map.get(&format!("{coin}:5m")) {
            Some(s) if s.cur_hi > 0.0 => (s.cur_hi, s.cur_lo),
            _ => return false,
        };
        let (mut highs, mut lows): (Vec<f64>, Vec<f64>) = (Vec::new(), Vec::new());
        for tf in TFS {
            if let Some(s) = map.get(&format!("{coin}:{tf}")) {
                highs.extend(s.highs.iter().copied());
                lows.extend(s.lows.iter().copied());
            }
        }
        level_touched(cur_hi, cur_lo, &highs, &lows)
    }
}

/// True if the 5m window candle (`cur_hi`/`cur_lo`) reached any pooled level: its high
/// touched up to a high level, or its low touched down to a low level (wick). cur_hi==0
/// means no live data → not touched (the caller treats missing data as skip separately).
fn level_touched(cur_hi: f64, cur_lo: f64, highs: &[f64], lows: &[f64]) -> bool {
    if cur_hi <= 0.0 {
        return false;
    }
    highs.iter().any(|&h| cur_hi >= h) || lows.iter().any(|&l| cur_lo <= l)
}

/// Swing-high / swing-low pivots over closed `bars` (each `(high, low)`), 15 left + 5
/// right (the indicator's defaults). A high pivot is the strict local max of its
/// window; it's dropped (mitigated) if any LATER bar's high wicked above it. Mirror
/// for lows. Returns the active (un-mitigated) high and low levels.
fn pivots(bars: &[(f64, f64)]) -> (Vec<f64>, Vec<f64>) {
    let n = bars.len();
    let (mut highs, mut lows) = (Vec::new(), Vec::new());
    if n < LEFT + RIGHT + 1 {
        return (highs, lows);
    }
    for i in LEFT..n - RIGHT {
        let (hi, lo) = bars[i];
        let pivot_high = (i - LEFT..i).all(|j| bars[j].0 < hi) && (i + 1..=i + RIGHT).all(|j| bars[j].0 < hi);
        if pivot_high && !(i + 1..n).any(|j| bars[j].0 > hi) {
            highs.push(hi);
        }
        let pivot_low = (i - LEFT..i).all(|j| bars[j].1 > lo) && (i + 1..=i + RIGHT).all(|j| bars[j].1 > lo);
        if pivot_low && !(i + 1..n).any(|j| bars[j].1 < lo) {
            lows.push(lo);
        }
    }
    (highs, lows)
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
    use super::*;

    #[test]
    fn pivot_high_and_low_detected_and_mitigation() {
        // 21 bars: a clear swing high at index 15 (value 110) and swing low at 15 (5).
        let mut bars = vec![(100.0, 90.0); 21];
        bars[15] = (110.0, 5.0); // pivot at i=15 (needs 15 left, 5 right -> n=21 ok)
        let (highs, lows) = pivots(&bars);
        assert!(highs.contains(&110.0), "swing high level detected");
        assert!(lows.contains(&5.0), "swing low level detected");

        // Mitigate the high: a later bar wicks above 110 -> level dropped.
        let mut bars2 = bars.clone();
        bars2.push((115.0, 95.0));
        let (highs2, _) = pivots(&bars2);
        assert!(!highs2.contains(&110.0), "mitigated high level removed");
    }

    #[test]
    fn level_touch_uses_forming_wick() {
        let highs = [110.0];
        let lows = [5.0];
        assert!(!level_touched(109.0, 100.0, &highs, &lows)); // 109<110, 100>5 -> clear
        assert!(level_touched(110.0, 100.0, &highs, &lows)); // 5m high reached the 110 level
        assert!(level_touched(109.0, 5.0, &highs, &lows)); // 5m low reached the 5 level
        assert!(!level_touched(0.0, 0.0, &highs, &lows)); // no live data
        // pooled-across-TFs: a 30m level the 5m candle reaches still counts
        assert!(level_touched(109.5, 100.0, &[110.0, 109.2], &[5.0])); // touches the 109.2 level
    }
}
