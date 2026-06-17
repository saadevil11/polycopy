//! Copytrader: follow a target trader and replicate their fills.
//!
//! - `monitor`  — polls the Data API for the target's activity (WS-vs-REST is a
//!   later add; polling is the path that works from datacenter IPs).
//! - `brownfox` — the one-fixed-share-buy-per-market strategy (ported from the
//!   hardened Python implementation, holdings-driven and restart-safe).
//! - `store`    — JSON persistence for dedup + brownfox state.
pub mod autosell;
pub mod brownfox;
pub mod dashboard;
pub mod monitor;
pub mod replicator;
pub mod stale;
pub mod store;
pub mod weather;

/// Buy or sell.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

/// One observed fill from the target trader.
#[derive(Clone, Debug)]
pub struct TargetTrade {
    pub trade_id: String, // ws_<txhash> (stable per on-chain trade)
    pub market_id: String, // conditionId
    pub token_id: String,  // outcome asset id
    pub side: Side,
    pub price: f64,
    pub size: f64,
    pub title: String,
}
