//! Copytrader: follow a target trader and replicate their fills.
//!
//! - `monitor`   — the target's fills, via Data-API polling (`run`) or the
//!   activity WebSocket (`run_ws`); selected by `DATA_SOURCE` (rest is the path
//!   that works from datacenter IPs, ws is faster on an allowed IP).
//! - `brownfox`  — the one-fixed-share-buy-per-market strategy (ported from the
//!   hardened Python implementation, holdings-driven and restart-safe).
//! - `replicator`— general proportional buy/sell copy (+ `weather`, `autosell`,
//!   `stale`).
//! - `store`     — JSON persistence for dedup + brownfox state + trade log.
pub mod autosell;
pub mod brownfox;
pub mod dashboard;
pub mod fvg;
pub mod liquidity;
pub mod monitor;
pub mod ninetynine;
pub mod replicator;
pub mod scanner;
pub mod sellwithtarget;
pub mod stale;
pub mod store;
pub mod weather;

/// Buy or sell.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

/// A directional avoidance signal from an indicator filter for one coin's current 5m
/// window. The block is DIRECTIONAL: resistance above (a high swing level / a bearish
/// FVG) blocks buying the **Up** outcome; support below (a low level / a bullish FVG)
/// blocks buying **Down**. `evaluable` is false when the filter is on but has no data
/// to judge (no Binance pair, or candles not seeded) — the caller treats that as "skip
/// the buy" but never as a reason to dump a held position. A disabled filter returns
/// `{false, false, evaluable: true}` so it never gates.
#[derive(Clone, Copy, Debug, Default)]
pub struct FilterSignal {
    pub block_up: bool,
    pub block_down: bool,
    pub evaluable: bool,
}

/// One observed fill from the target trader.
#[derive(Clone, Debug)]
pub struct TargetTrade {
    #[allow(dead_code)] // dedup key set by the monitor; kept on the model for tracing
    pub trade_id: String, // ws_<txhash> (stable per on-chain trade)
    pub market_id: String, // conditionId
    pub token_id: String,  // outcome asset id
    pub side: Side,
    pub price: f64,
    pub size: f64,
    pub title: String,  // human-readable market name
    pub outcome: String, // outcome bought/sold, e.g. "Yes" / "No"
    pub slug: String,    // market/event slug, e.g. "btc-updown-5m-1781699700"
    /// Monotonic instant we parsed this trade off the feed — for the detect→place
    /// latency (the part WE control: channel hop + book reads + order POST).
    pub detected_at: std::time::Instant,
    /// The target trade's server timestamp in ms (0 = unknown) — for the total
    /// target→place lag (how far behind the target we land; needs synced clocks).
    pub trade_ts_ms: u64,
}
