//! Liquidity-level filter for the 99c scanner — a Rust port of the "Liquidity
//! Levels (Sonarlab)" TradingView indicator, used as an AVOIDANCE gate.
//!
//! For each coin it pulls Binance candles on **5m / 15m / 30m** (native intervals,
//! no resampling), finds swing-high / swing-low **pivots** (15 left / 5 right bars =
//! the indicator's defaults) as liquidity levels, drops a level once price **wicks**
//! through it (mitigation by high/low, not close — so we react the moment price
//! touches it), and answers one question: **is the coin's live price currently
//! touching any active level on any of the 3 timeframes?** If so, the scanner SKIPS
//! that 5m market's 0.99 buy (price at a liquidity level = reversal risk).
//!
//! Data: Binance public klines REST (no auth), polled every `SCANNER_LIQ_POLL_SECS`.
//! Coins with no Binance USDT pair (e.g. HYPE) have no data → `clear_to_trade` is
//! false for them (we don't trade what we can't filter).
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;
use tokio::time::interval;
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
            poll: cfg.scanner_liq_poll,
            coins,
            http,
            series: Mutex::new(HashMap::new()),
        }
    }

    /// Poll Binance klines for every (coin, timeframe) and recompute levels + the
    /// live price. Runs until cancelled.
    pub async fn run(self: Arc<Self>) {
        if !self.enabled {
            info!("🪙 liquidity filter: OFF (SCANNER_LIQUIDITY_FILTER=false)");
            return;
        }
        info!(
            "🪙 liquidity filter: ON — avoid 0.99 buys when {} price touches a {} swing level (pivots {}L/{}R, wick); polling Binance every {}s",
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

        let mut t = interval(self.poll);
        loop {
            for (coin, tf, sym) in &work {
                self.refresh(coin, tf, sym).await;
            }
            t.tick().await;
        }
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

    /// True if it's safe to trade this coin's 0.99 (filter off, or price is clear of
    /// every active level on all timeframes). False if at a level OR data is missing
    /// (we never trade a coin we can't evaluate — e.g. HYPE, or Binance unreachable).
    pub fn clear_to_trade(&self, coin: &str) -> bool {
        if !self.enabled {
            return true;
        }
        if binance_symbol(coin).is_none() {
            return false; // no candle source (e.g. HYPE) → don't trade
        }
        let map = self.series.lock().unwrap();
        for tf in TFS {
            match map.get(&format!("{coin}:{tf}")) {
                Some(s) if s.cur_hi > 0.0 => {
                    if touched(s) {
                        return false; // price is at a liquidity level on this TF
                    }
                }
                _ => return false, // not seeded yet / fetch failing → conservative skip
            }
        }
        true
    }
}

/// A coin's price "touches" a level when the forming candle's wick reaches it: its
/// high reached up to an active high level, or its low reached down to an active low.
fn touched(s: &Series) -> bool {
    if s.cur_hi <= 0.0 {
        return false;
    }
    s.highs.iter().any(|&h| s.cur_hi >= h) || s.lows.iter().any(|&l| s.cur_lo <= l)
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
    fn touched_uses_forming_wick() {
        let s = Series { highs: vec![110.0], lows: vec![5.0], cur_hi: 109.0, cur_lo: 100.0 };
        assert!(!touched(&s)); // 109 < 110 and 100 > 5 -> clear
        let s2 = Series { cur_hi: 110.0, ..s.clone() };
        assert!(touched(&s2)); // forming high reached the 110 level
        let s3 = Series { cur_lo: 5.0, ..s };
        assert!(touched(&s3)); // forming low reached the 5 level
    }
}
