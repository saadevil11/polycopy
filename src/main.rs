//! polybotshadow — Rust Polymarket copytrader.
//!
//! Follows a target trader and replicates their fills via the validated CLOB v2
//! signing path. Supports brownfox mode (one fixed-share buy per market with a
//! marked resting sell + managed exit) and general proportional copy with
//! optional weather price-chasing, auto-sell limits and a stale-order sweep.
//! Data source (activity WebSocket vs Data-API REST polling) is env-selectable.
//! See ARCHITECTURE.md.
mod clob;
mod config;
mod copytrader;

use std::sync::Arc;

use anyhow::Result;
use chrono::Utc;
use clob::executor::Executor;
use config::{Config, DataSource};

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

    // Authenticate the executor up-front (fails fast on bad key/creds). Needed
    // for read endpoints even in dry-run; its writes are guarded by dry_run.
    let executor: Option<Arc<Executor>> = if !cfg.private_key.is_empty() {
        tracing::info!("authenticating with the CLOB…");
        Some(Arc::new(Executor::new(&cfg).await?))
    } else {
        None
    };

    let data_dir = std::env::var("COPY_DATA_DIR").unwrap_or_else(|_| "./data".into());
    let store = Arc::new(copytrader::store::Store::open(&data_dir));

    if cfg.scanner_enabled {
        // ── 99c order-book SCANNER: self-driven, NO target trader / monitor. It
        // watches the 5m order books directly and buys at 0.99 when the ask hits it.
        match executor.clone() {
            Some(exec) => {
                // Liquidity-level filter (Binance 5m/15m/30m) — runs alongside the scanner.
                let liq = Arc::new(copytrader::liquidity::Liquidity::new(&cfg));
                tokio::spawn(liq.clone().run());
                let sc = copytrader::scanner::Scanner::new(cfg.clone(), exec, store.clone(), liq);
                tokio::spawn(sc.run());
            }
            None => tracing::warn!("99c-scanner needs PRIVATE_KEY/auth — nothing to do"),
        }
    } else {
        // ── monitor: stream the target trader's fills (WS or REST) into a channel ──
        let (tx, rx) = tokio::sync::mpsc::channel(256);
        // 99c restricts to its asset list; pass it to the monitor so non-matching
        // markets are dropped before logging/dispatch (cleaner logs, less work).
        let log_filter = if cfg.ninetynine_enabled { cfg.ninetynine_assets.clone() } else { Vec::new() };
        // DATA_SOURCE=ws -> activity WebSocket (+ /activity safety-net poll);
        // DATA_SOURCE=rest -> Data-API polling (reliable on AWS).
        match cfg.data_source {
            DataSource::Ws => {
                tracing::info!("monitor: WebSocket (primary) + /activity safety net (DATA_SOURCE=ws)");
                tokio::spawn(copytrader::monitor::run_ws(cfg.clone(), store.clone(), tx.clone(), log_filter.clone()));
                tokio::spawn(copytrader::monitor::run(cfg.clone(), store.clone(), tx, log_filter));
            }
            DataSource::Rest => {
                tracing::info!("monitor: REST polling (DATA_SOURCE=rest)");
                tokio::spawn(copytrader::monitor::run(cfg.clone(), store.clone(), tx, log_filter));
            }
        }

        // ── strategy: 99c / sell-with-target / brownfox / general replicator ──
        match executor.clone() {
            Some(exec) => {
                if cfg.ninetynine_enabled {
                    let nn = copytrader::ninetynine::NinetyNine::new(cfg.clone(), exec, store.clone());
                    tokio::spawn(nn.run(rx));
                } else if cfg.sell_with_target_enabled {
                    let swt = copytrader::sellwithtarget::SellWithTarget::new(cfg.clone(), exec, store.clone());
                    tokio::spawn(swt.run(rx));
                } else if cfg.brownfox_enabled {
                    let bf = copytrader::brownfox::Brownfox::new(cfg.clone(), exec, store.clone());
                    tokio::spawn(bf.run(rx));
                } else {
                    tokio::spawn(copytrader::replicator::run(cfg.clone(), exec.clone(), store.clone(), rx));
                    if cfg.auto_sell_enabled {
                        tokio::spawn(copytrader::autosell::run(cfg.clone(), exec.clone(), store.clone()));
                    }
                    if cfg.stale_sweep_enabled {
                        tokio::spawn(copytrader::stale::run(cfg.clone(), exec.clone()));
                    }
                }
            }
            None => tracing::warn!("no PRIVATE_KEY/auth — monitor only (no orders placed)"),
        }
    }

    tracing::info!("copytrader running — Ctrl-C to stop");

    // ── dashboard: positions + recent copy trades + portfolio (needs executor) ──
    let started = Utc::now();
    match executor {
        Some(exec) => {
            let h_dash = tokio::spawn(copytrader::dashboard::serve(cfg.clone(), exec, store.clone(), started));
            tokio::select! {
                _ = tokio::signal::ctrl_c() => tracing::info!("shutdown requested"),
                r = h_dash => match r {
                    Ok(Ok(())) => tracing::error!("dashboard exited unexpectedly"),
                    Ok(Err(e)) => tracing::error!("dashboard error: {e}"),
                    Err(e)     => tracing::error!("dashboard join error: {e}"),
                },
            }
        }
        None => {
            tokio::signal::ctrl_c().await.ok();
            tracing::info!("shutdown requested");
        }
    }
    Ok(())
}

fn banner(cfg: &Config) {
    let mode = if cfg.dry_run { "DRY-RUN (no orders placed)" } else { "LIVE" };
    let strat = if cfg.scanner_enabled {
        "99c-scanner (order-book driven, NO target; buy 0.99 when ask hits it, hold)"
    } else if cfg.ninetynine_enabled {
        "99c (fixed-share buy at target price, hold to resolution)"
    } else if cfg.sell_with_target_enabled {
        "sell-with-target (buy at target price; market-sell all on target's sell)"
    } else if cfg.brownfox_enabled {
        "brownfox (fixed-share buy + marked sell + managed exit)"
    } else if cfg.weather_enabled {
        "copy + weather chase"
    } else {
        "copy"
    };
    let src = match cfg.data_source {
        DataSource::Ws => "activity WebSocket",
        DataSource::Rest => "Data-API REST polling",
    };
    tracing::info!("════════════════════════════════════════════════════════");
    tracing::info!("  polybotshadow — Polymarket copytrader (Rust)");
    tracing::info!("  mode        : {mode}");
    tracing::info!("  strategy    : {strat}");
    tracing::info!("  data source : {src}");
    tracing::info!("  target      : {}", mask(&cfg.target_trader));
    if cfg.scanner_enabled {
        tracing::info!("  scan size   : {} sh/market @ {:.2} when best ask ≥ {:.2}", cfg.scanner_trade_size_shares, cfg.scanner_buy_price, cfg.scanner_trigger_ask);
    } else if cfg.ninetynine_enabled {
        tracing::info!("  99c size    : {} shares/market (hold to resolution)", cfg.ninetynine_trade_size_shares);
    } else if cfg.sell_with_target_enabled {
        tracing::info!("  swt size    : {} shares/market (sell all on target's sell)", cfg.sell_with_target_trade_size_shares);
    } else if cfg.brownfox_enabled {
        tracing::info!("  bf size     : {} shares/market", cfg.brownfox_trade_size_shares);
    } else {
        tracing::info!(
            "  copy %      : {:.0}%  |  max positions: {}",
            cfg.copy_percentage * 100.0,
            if cfg.max_positions == 0 { "∞".into() } else { cfg.max_positions.to_string() }
        );
    }
    tracing::info!("  sig type    : {}  funder: {}", cfg.signature_type, mask(&cfg.funder_address));
    tracing::info!("  dashboard   : http://localhost:{}", cfg.dashboard_port);
    tracing::info!("════════════════════════════════════════════════════════");
    if !cfg.dry_run {
        tracing::warn!("LIVE MODE: real orders will be placed.");
    }
}

fn mask(addr: &str) -> String {
    if addr.len() > 10 {
        format!("{}…{}", &addr[..6], &addr[addr.len() - 4..])
    } else {
        addr.to_string()
    }
}
