//! latebutfast — Rust Polymarket near-resolution sniper.
//!
//! Rests a 0.999 GTC buy the instant a fast-resolving market's best ask hits
//! 0.999/1.00, across BTC/ETH/SOL/XRP/DOGE/HYPE/BNB 5m markets + FIFA World Cup
//! (moneyline/spreads/totals/btts) — all in ONE instance. See ARCHITECTURE.md.
mod config;
mod copytrader;
mod dashboard;
mod markets;
mod models;
mod state;
mod strategy;

mod clob;

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use chrono::Utc;
use clob::executor::Executor;
use config::{Config, DataSource};
use state::BotState;

#[tokio::main]
async fn main() -> Result<()> {
    let cfg = Config::from_env()?;

    // `cargo run -- selftest` : print a deterministic signing vector for the
    // py-clob-client-v2 cross-check gate, then exit. No network.
    if std::env::args().any(|a| a == "selftest" || a == "selftest-signing") {
        if cfg.private_key.is_empty() {
            return Err(anyhow::anyhow!("selftest needs PRIVATE_KEY set"));
        }
        return clob::signing::selftest(&cfg.private_key, &cfg.funder_address);
    }

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(&cfg.log_level)),
        )
        .compact()
        .init();

    banner(&cfg);

    // Build + auth the executor up-front (fails fast on bad key/creds). Needed
    // for LIVE, and for the copytrader (even in dry-run, for read endpoints; its
    // writes are guarded by dry_run inside the executor).
    let need_exec = !cfg.dry_run || cfg.copytrader_enabled;
    let executor: Option<Arc<Executor>> = if need_exec && !cfg.private_key.is_empty() {
        tracing::info!("authenticating with the CLOB…");
        Some(Arc::new(Executor::new(&cfg).await?))
    } else {
        None
    };

    // ── copytrader mode: follow a target, run brownfox. Skips the sniper. ──
    if cfg.copytrader_enabled {
        let data_dir = std::env::var("COPY_DATA_DIR").unwrap_or_else(|_| "./data".into());
        let store = Arc::new(copytrader::store::Store::open(&data_dir));
        let (tx, rx) = tokio::sync::mpsc::channel(256);
        // DATA_SOURCE=ws -> activity WebSocket (faster, but 429s on datacenter
        // IPs); DATA_SOURCE=rest -> Data-API polling (reliable on AWS).
        match cfg.data_source {
            config::DataSource::Ws => {
                tracing::info!("copytrader: WebSocket monitor (DATA_SOURCE=ws)");
                tokio::spawn(copytrader::monitor::run_ws(cfg.clone(), store.clone(), tx));
            }
            config::DataSource::Rest => {
                tracing::info!("copytrader: REST polling monitor (DATA_SOURCE=rest)");
                tokio::spawn(copytrader::monitor::run(cfg.clone(), store.clone(), tx));
            }
        }
        match executor.clone() {
            Some(exec) => {
                if cfg.brownfox_enabled {
                    // brownfox manages its own sells; no auto-sell/stale loops.
                    let bf = copytrader::brownfox::Brownfox::new(cfg.clone(), exec, store.clone());
                    tokio::spawn(bf.run(rx));
                } else {
                    // general copy replicator + optional auto-sell / stale sweep.
                    tokio::spawn(copytrader::replicator::run(cfg.clone(), exec.clone(), store.clone(), rx));
                    if cfg.auto_sell_enabled {
                        tokio::spawn(copytrader::autosell::run(cfg.clone(), exec.clone(), store.clone()));
                    }
                    if cfg.stale_sweep_enabled {
                        tokio::spawn(copytrader::stale::run(cfg.clone(), exec.clone()));
                    }
                }
            }
            None => tracing::warn!("copytrader needs PRIVATE_KEY/auth — monitor only"),
        }
        tracing::info!("copytrader running — Ctrl-C to stop");
        let h_dash = tokio::spawn(dashboard::serve(cfg.clone(), BotState::new(Utc::now())));
        tokio::select! {
            _ = tokio::signal::ctrl_c() => tracing::info!("shutdown requested"),
            _ = h_dash => tracing::error!("dashboard exited"),
        }
        return Ok(());
    }

    let state = BotState::new(Utc::now());

    // Discovery: keep the monitored-token set fresh (crypto 5m + World Cup).
    let h_disc = tokio::spawn(markets::discovery::run(cfg.clone(), state.clone()));

    // Market-data feed: WS for realtime + a slow REST resync as a safety net;
    // or pure REST polling when DATA_SOURCE=rest.
    let mut feeds = Vec::new();
    match cfg.data_source {
        DataSource::Ws => {
            feeds.push(tokio::spawn(clob::ws::run(cfg.clone(), state.clone())));
            let mut safety = cfg.clone();
            safety.rest_poll = Duration::from_secs(5);
            feeds.push(tokio::spawn(clob::rest::poll_books(safety, state.clone())));
        }
        DataSource::Rest => {
            feeds.push(tokio::spawn(clob::rest::poll_books(cfg.clone(), state.clone())));
        }
    }

    // Open positions (funder wallet) for the dashboard — only if a funder is set.
    let has_funder = !cfg.funder_address.is_empty() && !cfg.funder_address.starts_with("0xYOUR");
    if has_funder {
        tokio::spawn(clob::positions::run(cfg.clone(), state.clone()));
    }

    // Stale-cancel: drop crypto resting orders older than the configured age.
    tokio::spawn(strategy::cancel_stale_crypto(cfg.clone(), state.clone(), executor.clone()));

    // Strategy: rest a 0.999 GTC buy the instant an ask is 99.9c / 100c.
    tokio::spawn(strategy::run(cfg.clone(), state.clone(), executor));

    // Dashboard (the user-facing liveness surface).
    let h_dash = tokio::spawn(dashboard::serve(cfg.clone(), state.clone()));

    // The background loops (discovery, feed, strategy, positions) self-restart;
    // we stay alive until Ctrl-C or a fatal dashboard error.
    tracing::info!("running — Ctrl-C to stop");
    let _ = &h_disc;
    tokio::select! {
        _ = tokio::signal::ctrl_c() => tracing::info!("shutdown requested"),
        r = h_dash  => match r {
            Ok(Ok(())) => tracing::error!("dashboard exited unexpectedly"),
            Ok(Err(e)) => tracing::error!("dashboard error: {e}"),
            Err(e)     => tracing::error!("dashboard join error: {e}"),
        },
    }
    for f in feeds {
        f.abort();
    }

    Ok(())
}

fn banner(cfg: &Config) {
    let mode = if cfg.dry_run { "DRY-RUN (no orders placed)" } else { "LIVE" };
    let src = match cfg.data_source {
        DataSource::Ws => "WebSocket (CLOB market) + REST resync",
        DataSource::Rest => "REST polling",
    };
    tracing::info!("════════════════════════════════════════════════════════");
    tracing::info!("  latebutfast — Polymarket near-resolution sniper");
    tracing::info!("  mode        : {mode}");
    tracing::info!("  data source : {src}");
    tracing::info!("  assets (5m) : {}", cfg.assets.join(", "));
    tracing::info!(
        "  world cup   : {}",
        if cfg.enable_worldcup {
            cfg.worldcup_market_types.join(", ")
        } else {
            "disabled".into()
        }
    );
    tracing::info!(
        "  rule        : ask in {:?} & tick places {} exactly -> GTC BUY @ {}",
        cfg.trigger_asks, cfg.buy_price, cfg.buy_price
    );
    tracing::info!(
        "  size        : crypto {} sh  |  world cup {} sh",
        cfg.crypto_order_size_shares, cfg.worldcup_order_size_shares
    );
    tracing::info!(
        "  stale-cancel: crypto orders older than {}",
        match cfg.crypto_order_max_age {
            Some(d) => format!("{} min", d.as_secs() / 60),
            None => "disabled".into(),
        }
    );
    tracing::info!("  sig type    : {}  funder: {}", cfg.signature_type,
        mask(&cfg.funder_address));
    tracing::info!("  dashboard   : http://localhost:{}", cfg.dashboard_port);
    tracing::info!("════════════════════════════════════════════════════════");
    if !cfg.dry_run {
        tracing::warn!("LIVE MODE: real orders will be placed once strategy is wired.");
    }
}

fn mask(addr: &str) -> String {
    if addr.len() > 10 {
        format!("{}…{}", &addr[..6], &addr[addr.len() - 4..])
    } else {
        addr.to_string()
    }
}
