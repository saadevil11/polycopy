//! Core data structures shared across discovery, data feed, strategy, dashboard.
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Which kind of market an outcome token belongs to.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub enum MarketKind {
    /// Crypto 5-minute up/down (asset = btc/eth/sol/xrp/doge/hype/bnb).
    Crypto5m { asset: String },
    /// FIFA World Cup game market (market_type = moneyline|spreads|totals|btts).
    WorldCup { market_type: String },
}

impl MarketKind {
    pub fn label(&self) -> String {
        match self {
            MarketKind::Crypto5m { asset } => format!("{}-5m", asset.to_uppercase()),
            MarketKind::WorldCup { market_type } => format!("WC-{market_type}"),
        }
    }
}

/// A single tradable outcome token we monitor and may rest a 0.999 buy on.
#[derive(Clone, Debug, Serialize)]
pub struct OutcomeToken {
    pub token_id: String,
    pub condition_id: String,
    pub market_slug: String,
    pub outcome: String,
    pub neg_risk: bool, // use the neg-risk exchange for signing if true
    pub kind: MarketKind,
    pub tick_size: f64, // 0.01 / 0.001 etc; resolved from CLOB
    pub start_time: Option<DateTime<Utc>>, // kickoff for sports; None for crypto 5m
    pub end_time: Option<DateTime<Utc>>,
}

impl OutcomeToken {
    /// Eligible to trade NOW? Crypto 5m always. Sports markets (World Cup, etc.)
    /// ONLY once the game has STARTED — i.e. it is live or has ended; never
    /// pre-kickoff. Unknown start time on a sports market => not eligible.
    pub fn started(&self, now: DateTime<Utc>) -> bool {
        match &self.kind {
            MarketKind::Crypto5m { .. } => true,
            _ => self.start_time.map(|s| now >= s).unwrap_or(false),
        }
    }
}

/// Top-of-book snapshot for a token.
#[derive(Clone, Copy, Debug, Default, Serialize)]
pub struct BookTop {
    pub best_bid: f64,
    pub best_ask: f64,
    pub updated: Option<DateTime<Utc>>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum OrderStatus {
    Resting,
    Filled,
    Cancelled,
    DryRun, // logged-only (DRY_RUN); never sent
}

/// A buy limit we have placed (or would have placed in dry-run).
#[derive(Clone, Debug, Serialize)]
pub struct RestingOrder {
    pub order_id: String,
    pub token_id: String,
    pub market_label: String,
    pub outcome: String,
    pub price: f64,
    pub size: f64,
    pub filled: f64,
    pub status: OrderStatus,
    pub placed_at: DateTime<Utc>,
}

/// An open position (from the Data API), held until the market resolves.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Position {
    pub token_id: String,
    pub condition_id: String,
    pub title: String,
    pub outcome: String,
    pub size: f64,
    pub avg_price: f64,
    pub cur_price: f64,
    pub cash_pnl: f64,
}
