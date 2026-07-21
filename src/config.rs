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
    // Pre-created CLOB API credentials (optional; REQUIRED for sig type 3 deposit
    // wallets, which can't be L1-derived). If all three are set they're used as-is.
    pub clob_api_key: String,
    pub clob_api_secret: String,
    pub clob_api_passphrase: String,

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
    /// Only copy `<asset>-updown-<dur>-*` markets for these assets (empty = no filter).
    pub ninetynine_assets: Vec<String>,
    /// Which up/down market durations to copy, e.g. ["5m","15m"]. Set
    /// NINETYNINE_DURATIONS=5m to disable 15m (or =15m for only 15m).
    pub ninetynine_durations: Vec<String>,
    /// Max concurrent 99c buy placements (parallel across markets; bounds 429s).
    pub ninetynine_max_concurrent_buys: usize,

    // 99c order-book SCANNER (no target trader): watch the order books of all
    // <asset>-updown-5m markets via the CLOB market websocket and place a GTC buy at
    // 0.99 when the best ASK reaches it; hold to resolution. Uses ninetynine_assets +
    // ninetynine_buy_max_age + ninetynine_reconcile for the shared knobs.
    pub scanner_enabled: bool,
    pub scanner_trade_size_shares: f64, // shares per market (>= 5)
    pub scanner_buy_price: f64,         // GTC buy limit price (default 0.99)
    pub scanner_trigger_ask: f64,       // fire when best_ask >= this and < 1.0 (default 0.99)
    // MIN-MOVE entry gate: only take the 0.99 buy if the favorite is already at least this %
    // away from the 5m window open (buy Up needs spot >= open+pct, Down needs spot <= open-pct).
    // Near-ties are coin-flips we overpay 0.99 for; a 2.8-day backtest: 0 -> 14% loss rate,
    // 0.10 -> ~break-even, 0.15 -> 0.3% losses. 0 = off (default). Spot from the Binance 5m feed.
    pub scanner_min_move_pct: f64,
    // No-trade hours window in ET (24h), start-inclusive / end-exclusive, wraps midnight.
    // Some((21,3)) = place NO buys from 9pm to 3am ET. None = always trade. ET assumed EDT
    // (UTC-4); shift the configured hours by 1 for EST (winter). Set via SCANNER_NO_TRADE_ET.
    pub scanner_no_trade_et: Option<(u8, u8)>,
    pub scanner_exit_price: f64,        // liquidity-stop GTC sell price if a held market touches a level (0.10)
    pub scanner_discovery: Duration,    // Gamma re-discovery cadence (new 5m markets open every 5m)
    // Liquidity-level avoidance filter: skip a 0.99 buy when the coin's price is
    // touching a Binance 5m/15m/30m swing level. Coins with no Binance pair (HYPE)
    // are not traded while this is on.
    pub scanner_liquidity_filter: bool,
    pub scanner_liq_poll: Duration,     // Binance LEVELS refresh cadence (REST); the live price comes from the WS
    pub binance_rest_url: String,       // Binance SPOT host (api) — closest proxy to Polymarket's Chainlink
    pub binance_ws_url: String,         // Binance spot combined kline stream (live forming-candle price)
    // Fair-Value-Gap avoidance filter (LuxAlgo port): skip a 0.99 buy when the coin's
    // current 5m candle is inside an un-mitigated FVG on Binance 5m/15m. Runs ALONGSIDE
    // the liquidity filter — a market must be clear on BOTH to be bought.
    pub scanner_fvg_filter: bool,
    pub scanner_fvg_threshold_pct: f64, // min gap size as % of price (used when auto is OFF; 0 = any gap)
    pub scanner_fvg_auto: bool,         // adaptive threshold = auto_factor × the coin's avg candle range
    pub scanner_fvg_auto_factor: f64,   // 1.0 = a full avg candle (few gaps), 0.7 = good middle, 0 = all
    // Binance timeframes the FVG filter pools gaps from (default "5m,15m"). Add "1m,3m" to also
    // block on finer-TF gaps. WARNING: 1m gaps are tiny + numerous; with threshold 0 (every gap)
    // they will tap nearly every window and can block most buys — raise SCANNER_FVG_THRESHOLD_PCT
    // when widening. "5m" is always included (it is the acting window candle).
    pub scanner_fvg_tfs: Vec<String>,
    // BLANKET mode: when true, ANY liquidity level OR FVG hit blocks BOTH sides of that
    // market (no Up, no Down) instead of only the directional side. Applies to entry,
    // the resting-buy cancel, and the held-position stop.
    pub scanner_filter_blanket: bool,
    // Doji filter (non-directional): a 5m candle whose body is tiny vs its range
    // (|open-close| <= (high-low) * precision) is indecision/near-tie → block BOTH sides
    // (no trade) and dump a held position. Evaluated live on the forming candle.
    pub scanner_doji_filter: bool,
    pub scanner_doji_precision: f64, // doji max body as a fraction of range (indicator default 0.15)
    // Held-position STOP master switch. The liquidity/FVG/doji filters ALWAYS gate ENTRY
    // (don't buy a blocked side); this controls whether they ALSO dump a position we
    // already hold. DEFAULT OFF: a 66/66 audit (2026-06-20) found EVERY stop was a
    // winner-dump — we buy the 0.99 favorite (which ~always wins) and the filters fire on
    // its own move toward winning, so the stop only ever sold winners (~$2k lost in 2 days).
    // Filters are entry-only; leave this off unless the stop is re-validated.
    pub scanner_stop_enabled: bool,
    // NON-directional DOJI stop (operator request, independent of scanner_stop_enabled):
    // when true, dump a held position with a GTC sell at scanner_exit_price the moment the
    // coin's live 5m candle is a doji — regardless of lean (held Up OR Down, bullish OR
    // bearish doji). Theory: losses come from a hard pull-back that prints a doji. Needs the
    // doji candle feed, which runs whenever this OR scanner_doji_filter is on. Default OFF.
    pub scanner_doji_stop_enabled: bool,

    // sell-with-target mode (brownfox entry, but exit = market-sell ALL on the
    // target's first sell, run on a non-blocking worker).
    pub sell_with_target_enabled: bool,
    pub sell_with_target_trade_size_shares: f64,
    pub sell_with_target_reconcile: Duration,
    pub sell_with_target_market_sell_retries: u32,
    /// Exit = one GTC SELL limit at THIS price for all our shares. Priced low so it's
    /// marketable: it fills against every bid from the top down to this floor (e.g.
    /// 0.50, 0.49, …, 0.10), resting any remainder here. Not a "market order" (those
    /// get rejected on a moving book) — a plain limit the exchange accepts.
    pub sell_with_target_exit_price: f64,
    /// ENTRY is a MARKET buy: a marketable limit placed this far ABOVE the best ask so
    /// it crosses and fills now (a limit at the target's stale price rarely fills — the
    /// book has already moved up). Capped below 1.0.
    pub sell_with_target_buy_ahead: f64,

    // general copy replicator (proportional buy/sell copying)
    pub copy_percentage: f64,            // fraction of the target's size to copy
    // Replicator order type. false (COPY_ORDER_TYPE=gtc, default) = GTC limit at the target's
    // exact price for EVERY order (rests if not marketable). true (=fak) = a marketable
    // fill-and-kill for EVERY order: priced `copy_fak_ahead` THROUGH the quote so it crosses
    // and fills now, killing the remainder (nothing ever rests). FAK overrides weather chasing.
    pub copy_use_fak: bool,
    pub copy_fak_ahead: f64, // how far through the quote to price a FAK (only when copy_use_fak)
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
    pub clob_ws_url: String, // CLOB market order-book socket (99c scanner)
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
            clob_api_key: env_str("CLOB_API_KEY", ""),
            clob_api_secret: env_str("CLOB_API_SECRET", ""),
            clob_api_passphrase: env_str("CLOB_API_PASSPHRASE", ""),
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
            ninetynine_durations: env_list("NINETYNINE_DURATIONS", "5m,15m"),
            ninetynine_max_concurrent_buys: (env_u64("NINETYNINE_MAX_CONCURRENT_BUYS", 8) as usize).max(1),
            scanner_enabled: env_bool("USE_99C_SCANNER_MODE", false),
            scanner_trade_size_shares: env_f64("SCANNER_TRADE_SIZE_SHARES", 10.0).max(5.0),
            scanner_buy_price: env_f64("SCANNER_BUY_PRICE", 0.99),
            scanner_trigger_ask: env_f64("SCANNER_TRIGGER_ASK", 0.99),
            scanner_min_move_pct: env_f64("SCANNER_MIN_MOVE_PCT", 0.0).max(0.0),
            scanner_no_trade_et: {
                // "FROM-TO" in ET 24h hours, e.g. "21-3" = no trading 9pm..3am ET. Empty = off.
                let raw = env_str("SCANNER_NO_TRADE_ET", "");
                let raw = raw.trim();
                if raw.is_empty() {
                    None
                } else {
                    let p: Vec<&str> = raw.split('-').collect();
                    match (
                        p.first().and_then(|s| s.trim().parse::<u8>().ok()),
                        p.get(1).and_then(|s| s.trim().parse::<u8>().ok()),
                    ) {
                        (Some(f), Some(t)) if p.len() == 2 && f < 24 && t < 24 => Some((f, t)),
                        _ => None,
                    }
                }
            },
            scanner_exit_price: env_f64("SCANNER_EXIT_PRICE", 0.10),
            scanner_discovery: Duration::from_secs(env_u64("SCANNER_DISCOVERY_SECS", 30).max(5)),
            scanner_liquidity_filter: env_bool("SCANNER_LIQUIDITY_FILTER", true),
            scanner_liq_poll: Duration::from_secs(env_u64("SCANNER_LIQ_POLL_SECS", 15).max(2)),
            binance_rest_url: env_str("BINANCE_REST_URL", "https://api.binance.com"),
            binance_ws_url: env_str("BINANCE_WS_URL", "wss://stream.binance.com:9443/stream"),
            scanner_fvg_filter: env_bool("SCANNER_FVG_FILTER", true),
            // Default: FIXED threshold (0 = the indicator's default, every gap counts). Set
            // a % to ignore tiny gaps, e.g. 0.05. Auto is OFF by default.
            scanner_fvg_threshold_pct: env_f64("SCANNER_FVG_THRESHOLD_PCT", 0.0).max(0.0),
            // OPTIONAL adaptive mode (off by default): if SCANNER_FVG_AUTO=true, the
            // threshold becomes AUTO_FACTOR × the coin's avg candle range instead of the % above.
            scanner_fvg_auto: env_bool("SCANNER_FVG_AUTO", false),
            scanner_fvg_auto_factor: env_f64("SCANNER_FVG_AUTO_FACTOR", 0.7).max(0.0),
            scanner_fvg_tfs: {
                let mut v = env_list("SCANNER_FVG_TFS", "5m,15m");
                if !v.iter().any(|t| t == "5m") {
                    v.push("5m".to_string()); // the acting candle is always the 5m window candle
                }
                v
            },
            scanner_filter_blanket: env_bool("SCANNER_FILTER_BLANKET", false),
            scanner_doji_filter: env_bool("SCANNER_DOJI_FILTER", false),
            scanner_doji_precision: env_f64("SCANNER_DOJI_PRECISION", 0.15).max(0.0001),
            scanner_stop_enabled: env_bool("SCANNER_STOP_ENABLED", false),
            scanner_doji_stop_enabled: env_bool("SCANNER_DOJI_STOP_ENABLED", false),
            sell_with_target_enabled: env_bool("USE_SELL_WITH_TARGET_MODE", false),
            sell_with_target_trade_size_shares: env_f64("SELL_WITH_TARGET_TRADE_SIZE_SHARES", 50.0).max(5.0),
            sell_with_target_reconcile: Duration::from_millis(env_u64("SELL_WITH_TARGET_RECONCILE_MS", 3000)),
            sell_with_target_market_sell_retries: env_u64("SELL_WITH_TARGET_MARKET_SELL_RETRIES", 8) as u32,
            sell_with_target_exit_price: env_f64("SELL_WITH_TARGET_EXIT_PRICE", 0.10),
            sell_with_target_buy_ahead: env_f64("SELL_WITH_TARGET_BUY_AHEAD", 0.03),
            copy_percentage: env_f64("COPY_PERCENTAGE", 0.1),
            copy_use_fak: env_str("COPY_ORDER_TYPE", "gtc").trim().eq_ignore_ascii_case("fak"),
            copy_fak_ahead: env_f64("COPY_FAK_AHEAD", 0.03).max(0.0),
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
            clob_ws_url: "wss://ws-subscriptions-clob.polymarket.com/ws/market".into(),
            chain_id: 137,
            exchange_v2: "0xE111180000d2663C0091e4f400237545B87B996B".into(),
            neg_risk_exchange_v2: "0xe2222d279d744050d28e00520010520000310F59".into(),
        })
    }
}
