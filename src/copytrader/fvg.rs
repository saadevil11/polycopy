//! Fair-Value-Gap filter for the 99c scanner — a Rust port of the "Fair Value Gap
//! [LuxAlgo]" TradingView indicator, used as a second AVOIDANCE gate alongside the
//! liquidity-levels filter.
//!
//! For each coin it pulls Binance candles on **5m / 15m** (native intervals, no
//! resampling) and finds **fair value gaps** — the 3-candle imbalance LuxAlgo draws:
//!   * **bullish FVG** at bar `i`: `low[i] > high[i-2]` and `close[i-1] > high[i-2]`
//!     and the gap `(low[i]-high[i-2])/high[i-2]` exceeds the threshold → zone
//!     `[high[i-2], low[i]]`.
//!   * **bearish FVG**: `high[i] < low[i-2]` and `close[i-1] < low[i-2]` and
//!     `(low[i-2]-high[i])/high[i]` exceeds the threshold → zone `[high[i], low[i-2]]`.
//! A gap is **mitigated** (dropped) once a later candle **closes** beyond its far edge
//! (bull: close < zone.min; bear: close > zone.max) — the indicator's own rule.
//!
//! It emits a **directional** signal for the coin's current 5-minute candle (the
//! market's window) vs the un-mitigated FVGs pooled from 5m + 15m: inside a **bearish**
//! FVG (down-imbalance / resistance) blocks buying **Up**; inside a **bullish** FVG
//! (up-imbalance / support) blocks buying **Down**. Same data path as the liquidity
//! filter: live price (forming-candle wick) via the Binance kline WebSocket, the slower
//! FVG zones recomputed from REST every `SCANNER_LIQ_POLL_SECS`. Coins with no Binance
//! USDT pair (e.g. HYPE) have no data → the signal is not evaluable (the scanner skips).
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

const TFS: [&str; 2] = ["5m", "15m"];
const LIMIT: u32 = 120; // candles fetched per (coin,tf): plenty of history for FVGs near price

#[derive(Default, Clone)]
struct FvgSeries {
    zones: Vec<(f64, f64, bool)>, // active (un-mitigated) FVG bands: (min, max, is_bullish)
    cur_hi: f64,                  // current (forming) candle running high (0 = no data yet)
    cur_lo: f64,                  // current (forming) candle running low
}

pub struct Fvg {
    enabled: bool,
    rest_url: String,
    ws_url: String,
    poll: Duration,
    thr: f64,  // gap threshold as a fraction of price (0 = any gap, the indicator default)
    auto: bool, // auto threshold = mean bar range (overrides `thr` when true)
    /// coins we filter (those with a Binance USDT pair).
    coins: Vec<String>,
    http: reqwest::Client,
    /// "coin:tf" -> FvgSeries.
    series: Mutex<HashMap<String, FvgSeries>>,
}

impl Fvg {
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
        Fvg {
            enabled: cfg.scanner_fvg_filter,
            rest_url: cfg.binance_rest_url.clone(),
            ws_url: cfg.binance_ws_url.clone(),
            poll: cfg.scanner_liq_poll,
            thr: (cfg.scanner_fvg_threshold_pct / 100.0).max(0.0),
            auto: cfg.scanner_fvg_auto,
            coins,
            http,
            series: Mutex::new(HashMap::new()),
        }
    }

    /// Keep the filter fresh: the live forming-candle price via the Binance kline WS,
    /// the FVG zones via a slower REST refresh (which seeds history + self-heals gaps).
    pub async fn run(self: Arc<Self>) {
        if !self.enabled {
            info!("🟦 fvg filter: OFF (SCANNER_FVG_FILTER=false)");
            return;
        }
        info!(
            "🟦 fvg filter: ON — skip a 0.99 buy if {}'s current 5m candle is inside an un-mitigated fair value gap on {} (threshold {}); live price via Binance WS, zones refreshed every {}s",
            self.coins.join("/"),
            TFS.join("/"),
            if self.auto { "auto".to_string() } else { format!("{:.3}%", self.thr * 100.0) },
            self.poll.as_secs()
        );
        let work: Vec<(String, &'static str, &'static str)> = self
            .coins
            .iter()
            .filter_map(|c| binance_symbol(c).map(|s| (c.clone(), s)))
            .flat_map(|(c, s)| TFS.iter().map(move |tf| (c.clone(), *tf, s)))
            .collect();
        if work.is_empty() {
            warn!("🟦 fvg filter: no coins with a Binance pair — nothing to track");
            return;
        }

        // Seed zones + price once via REST so the filter has data before the WS warms up.
        for (coin, tf, sym) in &work {
            self.refresh(coin, tf, sym).await;
        }

        // REST zone refresh loop (slow; the WS owns the fast live price).
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
                    warn!("🟦 fvg: Binance kline WS ended ({e}); reconnecting in {backoff}s");
                    sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(30);
                }
            }
        }
    }

    async fn ws_session(&self, url: &str, n: usize) -> anyhow::Result<()> {
        let (ws, _) = tokio_tungstenite::connect_async(url).await?;
        let (mut write, mut read) = ws.split();
        info!("🟦 fvg: Binance kline WS connected ({n} streams, live price)");
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

    /// One kline frame → update that (coin, tf)'s forming-candle running high/low. Zones
    /// are owned by the REST refresh; we never touch them here.
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
                    debug!("🟦 fvg: {sym} {tf} decode failed: {e}");
                    return;
                }
            },
            Err(e) => {
                warn!("🟦 fvg: Binance fetch {sym} {tf} failed ({e}) — check api.binance.com reachability/geo");
                return;
            }
        };
        let rows = match v.as_array() {
            Some(a) if !a.is_empty() => a,
            _ => return,
        };
        // Each row: [openTime, "open","high","low","close", ...]. The LAST row is the
        // still-forming candle → use it for the live price, exclude it from FVG detection.
        let mut bars: Vec<(f64, f64, f64)> = Vec::with_capacity(rows.len());
        for row in &rows[..rows.len() - 1] {
            let hi = row.get(2).and_then(num_f64).unwrap_or(0.0);
            let lo = row.get(3).and_then(num_f64).unwrap_or(0.0);
            let cl = row.get(4).and_then(num_f64).unwrap_or(0.0);
            if hi > 0.0 && lo > 0.0 && cl > 0.0 {
                bars.push((hi, lo, cl));
            }
        }
        let forming = &rows[rows.len() - 1];
        let cur_hi = forming.get(2).and_then(num_f64).unwrap_or(0.0);
        let cur_lo = forming.get(3).and_then(num_f64).unwrap_or(0.0);
        let zones = compute_fvgs(&bars, self.thr, self.auto);

        let mut map = self.series.lock().unwrap();
        let s = map.entry(format!("{coin}:{tf}")).or_default();
        s.zones = zones;
        s.cur_hi = cur_hi;
        s.cur_lo = cur_lo;
    }

    /// Directional avoidance signal for this coin's current 5m window. The current 5m
    /// candle is tested for overlap against every un-mitigated FVG pooled from 5m + 15m:
    ///   * inside a **bearish** FVG (resistance / down-imbalance) → `block_up`,
    ///   * inside a **bullish** FVG (support / up-imbalance)       → `block_down`.
    /// `evaluable` is false when we can't judge (no Binance pair, or a timeframe not yet
    /// seeded); a disabled filter is trivially evaluable and blocks nothing.
    pub fn signal(&self, coin: &str) -> FilterSignal {
        if !self.enabled {
            return FilterSignal { block_up: false, block_down: false, evaluable: true };
        }
        if binance_symbol(coin).is_none() {
            return FilterSignal::default();
        }
        let map = self.series.lock().unwrap();
        let (cur_hi, cur_lo) = match map.get(&format!("{coin}:5m")) {
            Some(s) if s.cur_hi > 0.0 => (s.cur_hi, s.cur_lo),
            _ => return FilterSignal::default(),
        };
        let mut zones: Vec<(f64, f64, bool)> = Vec::new();
        for tf in TFS {
            match map.get(&format!("{coin}:{tf}")) {
                Some(s) => zones.extend(s.zones.iter().copied()),
                None => return FilterSignal::default(),
            }
        }
        FilterSignal {
            block_up: in_zone(cur_hi, cur_lo, &zones, false),  // bearish FVG → block Up
            block_down: in_zone(cur_hi, cur_lo, &zones, true), // bullish FVG → block Down
            evaluable: true,
        }
    }
}

/// True if the 5m window candle (`cur_hi`/`cur_lo`) overlaps any FVG band of the wanted
/// direction (`want_bull`): its range reached into the gap. cur_hi==0 → no live data.
fn in_zone(cur_hi: f64, cur_lo: f64, zones: &[(f64, f64, bool)], want_bull: bool) -> bool {
    if cur_hi <= 0.0 {
        return false;
    }
    zones
        .iter()
        .any(|&(min, max, is_bull)| is_bull == want_bull && cur_hi >= min && cur_lo <= max)
}

/// Active (un-mitigated) fair value gaps over closed `bars` (each `(high, low, close)`,
/// oldest→newest). Port of "Fair Value Gap [LuxAlgo]" detect() + mitigation. `thr_pct` is
/// the gap threshold as a fraction of price (0 = any gap); `auto` overrides it with the
/// mean bar range. Returns surviving bands `(min, max, is_bullish)`.
fn compute_fvgs(bars: &[(f64, f64, f64)], thr_pct: f64, auto: bool) -> Vec<(f64, f64, bool)> {
    let n = bars.len();
    if n < 3 {
        return Vec::new();
    }
    let thr = if auto {
        let (mut sum, mut cnt) = (0.0f64, 0.0f64);
        for &(h, l, _) in bars {
            if l > 0.0 {
                sum += (h - l) / l;
                cnt += 1.0;
            }
        }
        if cnt > 0.0 { sum / cnt } else { 0.0 }
    } else {
        thr_pct
    };
    let mut out: Vec<(f64, f64, bool)> = Vec::new();
    for i in 2..n {
        let (h0, l0, _c0) = bars[i];
        let c1 = bars[i - 1].2;
        let (h2, l2) = (bars[i - 2].0, bars[i - 2].1);
        // bull: gap between high[i-2] and low[i]; bear: gap between high[i] and low[i-2].
        let bull = h2 > 0.0 && l0 > h2 && c1 > h2 && (l0 - h2) / h2 > thr;
        let bear = h0 > 0.0 && h0 < l2 && c1 < l2 && (l2 - h0) / h0 > thr;
        let (min, max, isbull) = if bull {
            (h2, l0, true)
        } else if bear {
            (h0, l2, false)
        } else {
            continue;
        };
        // Mitigated once a LATER closed candle closes beyond the far edge.
        let mitigated = bars[i + 1..]
            .iter()
            .any(|&(_, _, c)| if isbull { c < min } else { c > max });
        if !mitigated {
            out.push((min, max, isbull));
        }
    }
    out
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
    fn bull_fvg_detected_and_mitigation() {
        // 3 bars forming a bullish gap: high[0]=100, then a gap up so low[2]=103 > high[0]=100.
        // bars: (high, low, close)
        let bars = vec![
            (100.0, 98.0, 99.0),   // i-2: high=100
            (105.0, 101.0, 104.0), // i-1: close=104 > 100
            (110.0, 103.0, 108.0), // i:   low=103 > 100 -> bull FVG [100,103]
        ];
        let z = compute_fvgs(&bars, 0.0, false);
        assert_eq!(z, vec![(100.0, 103.0, true)]); // bullish

        // Mitigate: a later candle closes below the zone min (100).
        let mut bars2 = bars.clone();
        bars2.push((104.0, 96.0, 99.0)); // close=99 < 100 -> bull mitigated
        assert!(compute_fvgs(&bars2, 0.0, false).is_empty());
    }

    #[test]
    fn bear_fvg_detected_and_mitigation() {
        let bars = vec![
            (110.0, 108.0, 109.0), // i-2: low=108
            (105.0, 101.0, 102.0), // i-1: close=102 < 108
            (100.0, 96.0, 98.0),   // i:   high=100 < 108 -> bear FVG [100,108]
        ];
        let z = compute_fvgs(&bars, 0.0, false);
        assert_eq!(z, vec![(100.0, 108.0, false)]); // bearish

        // Mitigate: a later candle closes above the zone max (108).
        let mut bars2 = bars.clone();
        bars2.push((112.0, 104.0, 110.0)); // close=110 > 108 -> bear mitigated
        assert!(compute_fvgs(&bars2, 0.0, false).is_empty());
    }

    #[test]
    fn threshold_filters_tiny_gaps() {
        // Gap of (103-100)/100 = 3%. A 5% threshold rejects it; 1% keeps it.
        let bars = vec![
            (100.0, 98.0, 99.0),
            (105.0, 101.0, 104.0),
            (110.0, 103.0, 108.0),
        ];
        assert!(compute_fvgs(&bars, 0.05, false).is_empty()); // 3% < 5% threshold
        assert_eq!(compute_fvgs(&bars, 0.01, false), vec![(100.0, 103.0, true)]); // 3% > 1%
    }

    #[test]
    fn in_zone_is_directional_overlap() {
        let bull = [(100.0, 103.0, true)]; // a bullish gap band
        let bear = [(100.0, 103.0, false)]; // same band, bearish
        // overlap + matching direction
        assert!(in_zone(104.0, 99.0, &bull, true)); // candle straddles bull gap → block Down
        assert!(in_zone(101.0, 100.5, &bull, true)); // inside the gap
        // overlap but WRONG direction → not a hit for that side
        assert!(!in_zone(104.0, 99.0, &bull, false)); // a bull gap never blocks Up
        assert!(in_zone(104.0, 99.0, &bear, false)); // a bear gap blocks Up
        assert!(!in_zone(104.0, 99.0, &bear, true)); // a bear gap never blocks Down
        // no overlap
        assert!(!in_zone(110.0, 104.0, &bull, true)); // price entirely above
        assert!(!in_zone(99.0, 95.0, &bull, true)); // price entirely below
        assert!(!in_zone(0.0, 0.0, &bull, true)); // no live data
    }
}
