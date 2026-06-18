//! Environment-driven configuration for the copytrader. Hosts/addresses are the
//! VERIFIED values ported from the working CLOB v2 copy-trader (see ARCHITECTURE.md).
use anyhow::{anyhow, Result};
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DataSource {
    Ws,
    Rest,
}

#[derive(Clone, Debug)]
pub struct Config {
    // wallet / auth
    pub private_key: String,
    pub funder_address: String,
    pub signature_type: u8,

    // safety gates
    pub dry_run: bool,
    /// Enforced in `from_env` (live trading refused unless true); retained for
    /// audit/inspection of the loaded config.
    #[allow(dead_code)]
    pub live_signing_validated: bool,

    // target / monitor
    pub data_source: DataSource,
    pub target_trader: String, // address we copy
    pub copy_poll: Duration,   // Data-API poll cadence for the target

    // brownfox mode (one fixed-share buy per market, +marked resting sell, exit)
    pub brownfox_enabled: bool,
    pub brownfox_trade_size_shares: f64,
    pub brownfox_sell_markup: f64,
    pub brownfox_reconcile: Duration,
    pub brownfox_market_sell_retries: u32,

    // 99c mode (one fixed-share buy per market at the target's price, HOLD to
    // resolution — no sell, no exit). For "buy near 1.0 and hold" targets.
    pub ninetynine_enabled: bool,
    pub ninetynine_trade_size_shares: f64,
    pub ninetynine_reconcile: Duration,
    pub ninetynine_buy_max_age: Option<Duration>, // cancel an unfilled buy after this (None = never)
    /// Only copy `<asset>-updown-5m-*` markets for these assets (empty = no filter).
    pub ninetynine_assets: Vec<String>,
    /// Max concurrent 99c buy placements (parallel across markets; bounds 429s).
    pub ninetynine_max_concurrent_buys: usize,

    // sell-with-target mode (brownfox entry, but exit = market-sell ALL on the
    // target's first sell, run on a non-blocking worker).
    pub sell_with_target_enabled: bool,
    pub sell_with_target_trade_size_shares: f64,
    pub sell_with_target_reconcile: Duration,
    pub sell_with_target_market_sell_retries: u32,

    // general copy replicator (proportional buy/sell copying)
    pub copy_percentage: f64,            // fraction of the target's size to copy
    pub max_positions: usize,            // distinct markets cap (0 = unlimited)
    pub min_target_trade_value_usd: f64, // skip target trades smaller than this

    // weather execution (price-chasing)
    pub weather_enabled: bool,
    pub weather_buy_ahead: f64, // place a BUY this far above the target (1 cent)
    pub weather_max_chases: u32,
    pub weather_fill_wait: Duration,
    pub weather_max_buy_price: f64,

    // auto-sell limit (resting standard-price sell on every copied position)
    pub auto_sell_enabled: bool,
    pub auto_sell_price: f64,
    pub auto_sell_maintain: Duration,
    pub auto_sell_exact_threshold: f64,

    // stale-order sweep (cancel resting BUYs once their market is near 1.0)
    pub stale_sweep_enabled: bool,
    pub stale_cancel_price: f64,
    pub stale_sweep: Duration,

    // infra
    pub dashboard_port: u16,
    pub log_level: String,

    // fixed Polymarket endpoints / network (verified from py-clob-client-v2)
    pub clob_url: String,
    pub data_api_url: String,
    pub data_ws_url: String, // activity socket (pushes a trader's fills)
    pub chain_id: u64,
    pub exchange_v2: String,          // standard CTF Exchange V2 (negRisk=false)
    pub neg_risk_exchange_v2: String, // Neg-Risk CTF Exchange V2 (negRisk=true)
}

fn env_str(key: &str, default: &str) -> String {
    std::env::var(key).ok().filter(|s| !s.is_empty()).unwrap_or_else(|| default.to_string())
}

fn env_bool(key: &str, default: bool) -> bool {
    match std::env::var(key) {
        Ok(v) => matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on"),
        Err(_) => default,
    }
}

fn env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    std::env::var(key).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(default)
}

fn env_list(key: &str, default: &str) -> Vec<String> {
    env_str(key, default)
        .split(',')
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .collect()
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let _ = dotenvy::dotenv();

        let dry_run = env_bool("DRY_RUN", true);
        let live_signing_validated = env_bool("LIVE_SIGNING_VALIDATED", false);

        let private_key = env_str("PRIVATE_KEY", "");
        let funder_address = env_str("FUNDER_ADDRESS", "");
        // Auth is only strictly required for live trading.
        if !dry_run {
            if private_key.is_empty() || private_key.starts_with("0xYOUR") {
                return Err(anyhow!("PRIVATE_KEY not set (required when DRY_RUN=false)"));
            }
            if funder_address.is_empty() || funder_address.starts_with("0xYOUR") {
                return Err(anyhow!("FUNDER_ADDRESS not set (required when DRY_RUN=false)"));
            }
            if !live_signing_validated {
                return Err(anyhow!(
                    "Refusing live trading: LIVE_SIGNING_VALIDATED=false. Pass the signing \
                     validation gate (Rust vs py-clob-client-v2 identical hash+sig) first."
                ));
            }
        }

        let data_source = match env_str("DATA_SOURCE", "rest").as_str() {
            "ws" => DataSource::Ws,
            _ => DataSource::Rest,
        };

        Ok(Config {
            private_key,
            funder_address,
            signature_type: env_u64("SIGNATURE_TYPE", 2) as u8,
            dry_run,
            live_signing_validated,
            data_source,
            target_trader: env_str("TARGET_TRADER_ADDRESS", "").to_lowercase(),
            copy_poll: Duration::from_millis(env_u64("COPY_POLL_MS", 1000)),
            brownfox_enabled: env_bool("USE_BROWNFOX_MODE", false),
            brownfox_trade_size_shares: env_f64("BROWNFOX_TRADE_SIZE_SHARES", 50.0).max(5.0),
            brownfox_sell_markup: env_f64("BROWNFOX_SELL_MARKUP", 0.01),
            brownfox_reconcile: Duration::from_millis(env_u64("BROWNFOX_RECONCILE_MS", 3000)),
            brownfox_market_sell_retries: env_u64("BROWNFOX_MARKET_SELL_RETRIES", 8) as u32,
            ninetynine_enabled: env_bool("USE_99C_MODE", false),
            ninetynine_trade_size_shares: env_f64("NINETYNINE_TRADE_SIZE_SHARES", 50.0).max(5.0),
            ninetynine_reconcile: Duration::from_millis(env_u64("NINETYNINE_RECONCILE_MS", 5000)),
            ninetynine_buy_max_age: match env_u64("NINETYNINE_BUY_MAX_AGE_SECS", 600) {
                0 => None,
                s => Some(Duration::from_secs(s)),
            },
            ninetynine_assets: env_list("NINETYNINE_ASSETS", ""),
            ninetynine_max_concurrent_buys: (env_u64("NINETYNINE_MAX_CONCURRENT_BUYS", 8) as usize).max(1),
            sell_with_target_enabled: env_bool("USE_SELL_WITH_TARGET_MODE", false),
            sell_with_target_trade_size_shares: env_f64("SELL_WITH_TARGET_TRADE_SIZE_SHARES", 50.0).max(5.0),
            sell_with_target_reconcile: Duration::from_millis(env_u64("SELL_WITH_TARGET_RECONCILE_MS", 3000)),
            sell_with_target_market_sell_retries: env_u64("SELL_WITH_TARGET_MARKET_SELL_RETRIES", 8) as u32,
            copy_percentage: env_f64("COPY_PERCENTAGE", 0.1),
            max_positions: env_u64("MAX_POSITIONS", 0) as usize,
            min_target_trade_value_usd: env_f64("MIN_TARGET_TRADE_VALUE_USD", 4.0),
            weather_enabled: env_bool("USE_WEATHER_MODE", false),
            weather_buy_ahead: env_f64("WEATHER_BUY_AHEAD", 0.01),
            weather_max_chases: env_u64("WEATHER_MAX_CHASES", 5) as u32,
            weather_fill_wait: Duration::from_millis(env_u64("WEATHER_FILL_WAIT_MS", 2000)),
            weather_max_buy_price: env_f64("WEATHER_MAX_BUY_PRICE", 0.996),
            auto_sell_enabled: env_bool("AUTO_SELL_ENABLED", false),
            auto_sell_price: env_f64("AUTO_SELL_PRICE", 0.999),
            auto_sell_maintain: Duration::from_millis(env_u64("AUTO_SELL_MAINTAIN_MS", 20000)),
            auto_sell_exact_threshold: env_f64("AUTO_SELL_EXACT_THRESHOLD", 0.97),
            stale_sweep_enabled: env_bool("STALE_ORDER_SWEEP_ENABLED", false),
            stale_cancel_price: env_f64("STALE_ORDER_CANCEL_PRICE", 0.995),
            stale_sweep: Duration::from_secs(env_u64("STALE_ORDER_SWEEP_SECS", 30)),
            dashboard_port: env_u64("DASHBOARD_PORT", 8090) as u16,
            log_level: env_str("LOG_LEVEL", "info"),
            // verified constants (py-clob-client-v2 config.py, chain 137)
            clob_url: "https://clob.polymarket.com".into(),
            data_api_url: "https://data-api.polymarket.com".into(),
            data_ws_url: "wss://ws-live-data.polymarket.com".into(),
            chain_id: 137,
            exchange_v2: "0xE111180000d2663C0091e4f400237545B87B996B".into(),
            neg_risk_exchange_v2: "0xe2222d279d744050d28e00520010520000310F59".into(),
        })
    }
}
