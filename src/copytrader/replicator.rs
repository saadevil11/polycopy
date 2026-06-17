//! General copy replicator: copy the target's buys/sells proportionally.
//! Ported from the Python trade_replicator + the weather/auto-sell sell rules.
//!  - BUY  : copy_percentage of their size, executed via weather chase (or GTC).
//!  - SELL : capped at our holdings; if we hold an auto-sell limit at the standard
//!           price and they sold there -> skip; else match their exact price when
//!           >= threshold; else chase down. Respects MAX_POSITIONS (new markets)
//!           and a min target-trade value.
use std::sync::Arc;

use tokio::sync::mpsc::Receiver;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::store::Store;
use crate::copytrader::{Side, TargetTrade};
use crate::copytrader::weather;

const MIN_SHARES: f64 = 5.0;

fn floor_to_tick(price: f64, tick: f64) -> f64 {
    if tick <= 0.0 {
        return price;
    }
    ((price / tick + 1e-9).floor() * tick * 1e10).round() / 1e10
}

pub async fn run(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, mut rx: Receiver<TargetTrade>) {
    info!("📋 copy replicator: {}% of target size, weather={}", cfg.copy_percentage * 100.0, cfg.weather_enabled);
    while let Some(t) = rx.recv().await {
        match t.side {
            Side::Buy => handle_buy(&cfg, &exec, &store, &t).await,
            Side::Sell => handle_sell(&cfg, &exec, &store, &t).await,
        }
    }
}

async fn handle_buy(cfg: &Config, exec: &Arc<Executor>, store: &Arc<Store>, t: &TargetTrade) {
    if t.token_id.is_empty() {
        return;
    }
    let amount_usd = t.price * t.size;
    if amount_usd < cfg.min_target_trade_value_usd {
        info!("📋 skip BUY: target ${:.2} < min ${:.2}", amount_usd, cfg.min_target_trade_value_usd);
        return;
    }
    // MAX_POSITIONS only gates NEW markets; scale-ins into a held market are fine.
    let new_market = !store.has_copied_market(&t.market_id);
    if new_market && cfg.max_positions > 0 && store.copied_count() >= cfg.max_positions {
        info!("📋 skip BUY: MAX_POSITIONS {} reached", cfg.max_positions);
        return;
    }
    let mut size = t.size * cfg.copy_percentage;
    if size < MIN_SHARES {
        size = MIN_SHARES; // exchange minimum (small overbuy, matches Python)
    }
    let tick = exec.tick_size(&t.token_id).await;
    let neg_risk = exec.token_neg_risk(&t.token_id).await;

    let filled = if cfg.weather_enabled {
        weather::chase_buy(exec, &t.token_id, neg_risk, t.price, size, tick, cfg).await
    } else {
        // GTC at the target's exact price (marketable if the ask <= price).
        match exec.place_gtc(&t.token_id, neg_risk, crate::clob::signing::SIDE_BUY, t.price, size, tick).await {
            Ok(_) => size,
            Err(e) => {
                warn!("📋 BUY place failed: {e}");
                0.0
            }
        }
    };
    if filled > 0.0 || !new_market {
        store.mark_copied(&t.market_id, &t.token_id);
    }
    info!("📋 copied BUY {:.2}/{:.2} sh in {} @ ~{:.4}", filled, size, &t.market_id[..t.market_id.len().min(10)], t.price);
}

async fn handle_sell(cfg: &Config, exec: &Arc<Executor>, store: &Arc<Store>, t: &TargetTrade) {
    if t.token_id.is_empty() {
        return;
    }
    let holdings = exec.token_holdings(&t.token_id).await;
    if holdings < MIN_SHARES {
        return; // can't place a sub-5 sell
    }
    let mut copy_size = t.size * cfg.copy_percentage;
    if copy_size > holdings {
        copy_size = holdings; // sell the max we hold (never over-sell)
    }
    if copy_size < MIN_SHARES {
        return;
    }
    let tick = exec.tick_size(&t.token_id).await;
    let neg_risk = exec.token_neg_risk(&t.token_id).await;

    if cfg.auto_sell_enabled {
        let sell_price = floor_to_tick(cfg.auto_sell_price, tick);
        let has_limit = exec
            .open_orders(&t.token_id)
            .await
            .into_iter()
            .any(|(_, s, p, orig, matched)| s == "SELL" && (p - sell_price).abs() < 1e-9 && (orig - matched) > 0.0);
        if has_limit && t.price >= cfg.auto_sell_price - 1e-6 {
            info!("📋 SELL: resting auto-sell limit captures this exit - skip");
            return;
        }
        if has_limit {
            // stale limit (won't fill below it) -> cancel and follow them
            for (id, s, p, ..) in exec.open_orders(&t.token_id).await {
                if s == "SELL" && (p - sell_price).abs() < 1e-9 {
                    let _ = exec.cancel_confirmed(&id, &t.token_id).await;
                }
            }
        }
        if t.price >= cfg.auto_sell_exact_threshold {
            // match their exact price
            let snapped = snap_price(t.price, tick);
            match exec.place_gtc(&t.token_id, neg_risk, SIDE_SELL, snapped, copy_size, tick).await {
                Ok(_) => info!("📋 SELL {:.2} @ exact {:.4} in {}", copy_size, snapped, &t.market_id[..t.market_id.len().min(10)]),
                Err(e) => warn!("📋 exact SELL failed: {e}"),
            }
            return;
        }
    }

    // Off-price / no auto-sell: chase down (or GTC at their price).
    if cfg.weather_enabled {
        let sold = weather::chase_sell(exec, &t.token_id, neg_risk, t.price, copy_size, tick, cfg).await;
        info!("📋 copied SELL {:.2}/{:.2} (chase) in {}", sold, copy_size, &t.market_id[..t.market_id.len().min(10)]);
    } else {
        let snapped = snap_price(t.price, tick);
        match exec.place_gtc(&t.token_id, neg_risk, SIDE_SELL, snapped, copy_size, tick).await {
            Ok(_) => info!("📋 SELL {:.2} @ {:.4} in {}", copy_size, snapped, &t.market_id[..t.market_id.len().min(10)]),
            Err(e) => warn!("📋 SELL failed: {e}"),
        }
    }
}
