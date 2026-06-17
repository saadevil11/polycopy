//! The strategy: for every monitored token, the instant its best ask is one of
//! the trigger prices (0.999 / 1.00) AND the live tick can place EXACTLY 0.999,
//! rest a GTC BUY at `buy_price` (0.999) — exactly once per token. Share size is
//! per market kind (crypto vs World Cup). In dry-run we log the order we *would*
//! place; live placement is delegated to the CLOB executor, with copytrader-style
//! dedup (never double-place on a token we already hold or have a resting order on).
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use tokio::time::sleep;
use tracing::{debug, info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::price_representable;
use crate::config::Config;
use crate::models::{OrderStatus, OutcomeToken, RestingOrder};
use crate::state::BotState;

/// How often we re-scan every book for a trigger.
const SCAN_INTERVAL: Duration = Duration::from_millis(120);
const EPS: f64 = 1e-6;

pub async fn run(cfg: Config, state: BotState, executor: Option<Arc<Executor>>) {
    let trigger_min = cfg.trigger_asks.iter().cloned().fold(f64::MAX, f64::min);
    info!(
        "strategy armed: ask >= {:.4} AND live tick can place {:.4} exactly -> GTC BUY \
         (crypto {} sh / worldcup {} sh, one-shot/token)",
        trigger_min, cfg.buy_price, cfg.crypto_order_size_shares, cfg.worldcup_order_size_shares
    );

    loop {
        let now = Utc::now();
        // Snapshot trigger candidates first, then act — keeps the DashMap
        // iterator short and avoids holding refs across the await.
        let mut hits: Vec<String> = Vec::new();
        for entry in state.books.iter() {
            let tid = entry.key();
            let ask = entry.value().best_ask;
            if !is_trigger(ask, trigger_min) || state.triggered.contains_key(tid) {
                continue;
            }
            // Only fire where the LIVE tick can place 0.999 exactly (never 0.99).
            if !price_representable(cfg.buy_price, state.effective_tick(tid)) {
                continue;
            }
            // Sports markets: only once the game has STARTED (live or ended);
            // never pre-kickoff. Crypto 5m is always eligible. (Gate BEFORE the
            // one-shot claim so a pre-game token stays eligible after kickoff.)
            if !state.tokens.get(tid).map(|t| t.started(now)).unwrap_or(false) {
                continue;
            }
            hits.push(tid.clone());
        }

        for tid in hits {
            // Atomically claim it so we fire exactly once per token (dedup #1).
            if !state.mark_triggered(&tid) {
                continue;
            }
            let Some(token) = state.tokens.get(&tid).map(|t| t.clone()) else {
                continue;
            };
            place(&cfg, &state, &token, executor.as_deref()).await;
        }

        sleep(SCAN_INTERVAL).await;
    }
}

/// Trigger when the best ask is at/above the lowest trigger price (0.999) and a
/// real, ≤ 1.0 ask — i.e. the book shows 99.9c or 100c on that side.
fn is_trigger(best_ask: f64, trigger_min: f64) -> bool {
    best_ask > EPS && best_ask + EPS >= trigger_min && best_ask <= 1.0 + EPS
}

/// Crypto market label (e.g. "BTC-5m"). World Cup labels are "WC-…" — exempt.
fn is_crypto_label(label: &str) -> bool {
    label.ends_with("-5m")
}

/// Cancel CRYPTO resting orders older than `cfg.crypto_order_max_age` — an
/// unfilled 0.999 buy on a 5-minute market that has long since resolved is just
/// stranded capital. Sports markets are exempt. Runs until shutdown.
pub async fn cancel_stale_crypto(cfg: Config, state: BotState, executor: Option<Arc<Executor>>) {
    let Some(max_age_std) = cfg.crypto_order_max_age else {
        info!("stale-cancel: disabled (CRYPTO_ORDER_MAX_AGE_MINS=0)");
        return;
    };
    let max_age = chrono::Duration::from_std(max_age_std)
        .unwrap_or_else(|_| chrono::Duration::minutes(45));
    info!(
        "stale-cancel armed: crypto resting orders older than {} min will be cancelled",
        max_age.num_minutes()
    );
    let check = Duration::from_secs(30);
    loop {
        sleep(check).await;
        let now = Utc::now();
        let stale: Vec<(String, String)> = state
            .orders
            .iter()
            .filter(|e| {
                let o = e.value();
                is_crypto_label(&o.market_label)
                    && matches!(o.status, OrderStatus::Resting | OrderStatus::DryRun)
                    && (now - o.placed_at) > max_age
            })
            .map(|e| (e.key().clone(), e.value().market_label.clone()))
            .collect();

        for (oid, label) in stale {
            let short = &oid[..oid.len().min(12)];
            if cfg.dry_run || executor.is_none() {
                info!("[DRY-RUN] would cancel stale crypto order {short} ({label})");
                state.mark_order_cancelled(&oid);
            } else {
                match executor.as_deref().unwrap().cancel_order(&oid).await {
                    Ok(()) => {
                        info!("[LIVE] cancelled stale crypto order {short} ({label})");
                        state.mark_order_cancelled(&oid);
                    }
                    Err(e) => warn!("stale-cancel failed for {short}: {e}"),
                }
            }
        }
    }
}

async fn place(cfg: &Config, state: &BotState, token: &OutcomeToken, executor: Option<&Executor>) {
    let n = state.orders_placed.load(Ordering::Relaxed) + 1;
    let label = token.kind.label();
    let short = &token.token_id[..token.token_id.len().min(8)];
    let size = cfg.size_for(&token.kind);
    let tick = state.effective_tick(&token.token_id);

    // Hard guard: only ever place at EXACTLY buy_price (0.999). If the tick can
    // only reach 0.99, do NOT place (per spec — we never settle for 0.99).
    if !price_representable(cfg.buy_price, tick) {
        debug!(
            "skip {label} {} : tick {tick} cannot place {:.4} exactly",
            token.outcome, cfg.buy_price
        );
        return; // already claimed in `triggered`; won't retry this token
    }

    if cfg.dry_run || executor.is_none() {
        let order = RestingOrder {
            order_id: format!("dry-{n}-{short}"),
            token_id: token.token_id.clone(),
            market_label: label.clone(),
            outcome: token.outcome.clone(),
            price: cfg.buy_price,
            size,
            filled: 0.0,
            status: OrderStatus::DryRun,
            placed_at: Utc::now(),
        };
        info!(
            "[DRY-RUN] would BUY {} sh @ {:.4} | {} {} | {}",
            order.size, order.price, label, token.outcome, token.market_slug
        );
        state.record_order(order);
        return;
    }

    // LIVE. Dedup like the copytrader: never double-place on a token we already
    // hold (position) or already have a resting order on (authoritative checks
    // that survive restarts, on top of the in-memory one-shot claim above).
    let ex = executor.unwrap();
    if state.positions.get(&token.token_id).map(|p| p.size > 0.0).unwrap_or(false) {
        info!("skip {label} {}: already hold a position (no double-buy)", token.outcome);
        return;
    }
    if ex.has_existing_order(&token.token_id).await {
        info!("skip {label} {}: already have a resting order (no double-buy)", token.outcome);
        return;
    }

    match ex.place_gtc_buy(token, cfg.buy_price, size, tick).await {
        Ok(order_id) => {
            info!(
                "[LIVE] BUY {} sh @ {:.4} | {} {} | order {}",
                size, cfg.buy_price, label, token.outcome, order_id
            );
            state.record_order(RestingOrder {
                order_id,
                token_id: token.token_id.clone(),
                market_label: label,
                outcome: token.outcome.clone(),
                price: cfg.buy_price,
                size,
                filled: 0.0,
                status: OrderStatus::Resting,
                placed_at: Utc::now(),
            });
        }
        Err(e) => {
            warn!("[LIVE] order FAILED on {} {}: {e} — will retry on next tick", label, token.outcome);
            state.clear_triggered(&token.token_id); // allow a retry
        }
    }
}
