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
use crate::clob::signing::{snap_price, SIDE_BUY, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::store::{Store, TradeLog};
use crate::copytrader::{Side, TargetTrade};
use crate::copytrader::weather;

const MIN_SHARES: f64 = 5.0;

fn log_trade(store: &Store, t: &TargetTrade, side: &str, size: f64, price: f64, status: &str) {
    store.record_trade(TradeLog {
        time: chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        market: t.market_id.clone(),
        title: t.title.clone(),
        side: side.into(),
        size,
        price,
        status: status.into(),
    });
}

fn floor_to_tick(price: f64, tick: f64) -> f64 {
    if tick <= 0.0 {
        return price;
    }
    ((price / tick + 1e-9).floor() * tick * 1e10).round() / 1e10
}

/// Place the copy SELL honouring `COPY_ORDER_TYPE`: a marketable **FAK** (crosses the bid,
/// fills NOW, kills the remainder — nothing rests) or a **GTC** limit at `gtc_price`.
/// Returns `(shares_filled_or_placed, price_used, ok)`.
async fn place_copy_sell(
    cfg: &Config,
    exec: &Arc<Executor>,
    token: &str,
    neg_risk: bool,
    gtc_price: f64,
    size: f64,
    tick: f64,
) -> (f64, f64, bool) {
    if cfg.copy_use_fak {
        let price = exec.marketable_sell_price(token, cfg.copy_fak_ahead, tick).await;
        match exec.place_order(token, neg_risk, SIDE_SELL, price, size, tick, "FAK").await {
            Ok(oid) => {
                let m = exec.order_matched(&oid).await.unwrap_or(0.0);
                (m, price, m > 0.0)
            }
            Err(e) => {
                warn!("📋 SELL (FAK) place failed: {e}");
                (0.0, price, false)
            }
        }
    } else {
        let price = snap_price(gtc_price, tick);
        let ok = exec.place_gtc(token, neg_risk, SIDE_SELL, price, size, tick).await.is_ok();
        (if ok { size } else { 0.0 }, price, ok)
    }
}

pub async fn run(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, mut rx: Receiver<TargetTrade>) {
    info!(
        "📋 copy replicator: {}% of target size, orders={}, weather={}",
        cfg.copy_percentage * 100.0,
        if cfg.copy_use_fak {
            format!("FAK marketable ({:.2} through the quote)", cfg.copy_fak_ahead)
        } else {
            "GTC limit at the target's price".to_string()
        },
        if cfg.copy_use_fak { false } else { cfg.weather_enabled }
    );
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

    let filled = if cfg.copy_use_fak {
        // FAK "market" buy: priced THROUGH the ask so it crosses and fills NOW; the unfilled
        // remainder is killed (nothing rests). Overrides weather chasing.
        let price = exec.marketable_buy_price(&t.token_id, cfg.copy_fak_ahead, tick).await;
        match exec.place_order(&t.token_id, neg_risk, SIDE_BUY, price, size, tick, "FAK").await {
            Ok(oid) => exec.order_matched(&oid).await.unwrap_or(0.0),
            Err(e) => {
                warn!("📋 BUY (FAK) place failed: {e}");
                0.0
            }
        }
    } else if cfg.weather_enabled {
        weather::chase_buy(exec, &t.token_id, neg_risk, t.price, size, tick, cfg).await
    } else {
        // GTC at the target's exact price (marketable if the ask <= price).
        match exec.place_gtc(&t.token_id, neg_risk, SIDE_BUY, t.price, size, tick).await {
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
    log_trade(store, t, "BUY", if filled > 0.0 { filled } else { size }, t.price,
              if filled > 0.0 { "FILLED" } else { "RESTING" });
    info!("📋 copied BUY {:.2}/{:.2} sh in {} @ ~{:.4}", filled, size, &t.market_id[..t.market_id.len().min(10)], t.price);
}

async fn handle_sell(cfg: &Config, exec: &Arc<Executor>, store: &Arc<Store>, t: &TargetTrade) {
    if t.token_id.is_empty() {
        return;
    }
    // Our position from OUR OWN fills (lag-free `GET /data/trades`) — NOT the `/positions`
    // snapshot, which lags minutes and would make us SKIP the target's exit right after we
    // copied their buy (hard rule #8). /positions is only a fallback if the trade feed errors,
    // because silently reading "0 holdings" would strand us in the position.
    let holdings = match exec.filled_position(&t.token_id).await {
        Some(h) => h,
        None => {
            warn!("📋 SELL: /data/trades unavailable — falling back to the laggy /positions snapshot");
            exec.token_holdings(&t.token_id).await
        }
    };
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
            // match their exact price (or cross it with a FAK when COPY_ORDER_TYPE=fak)
            let (sold, price, ok) = place_copy_sell(cfg, exec, &t.token_id, neg_risk, t.price, copy_size, tick).await;
            let shown = if sold > 0.0 { sold } else { copy_size };
            log_trade(store, t, "SELL", shown, price, if ok { "FILLED" } else { "FAILED" });
            info!("📋 SELL {:.2} @ {:.4} in {} ({})", shown, price, &t.market_id[..t.market_id.len().min(10)], if ok {"ok"} else {"fail"});
            return;
        }
    }

    // Off-price / no auto-sell: FAK market sell, weather chase down, or a GTC at their price.
    if !cfg.copy_use_fak && cfg.weather_enabled {
        let sold = weather::chase_sell(exec, &t.token_id, neg_risk, t.price, copy_size, tick, cfg).await;
        log_trade(store, t, "SELL", sold, t.price, if sold > 0.0 { "FILLED" } else { "RESTING" });
        info!("📋 copied SELL {:.2}/{:.2} (chase) in {}", sold, copy_size, &t.market_id[..t.market_id.len().min(10)]);
    } else {
        let (sold, price, ok) = place_copy_sell(cfg, exec, &t.token_id, neg_risk, t.price, copy_size, tick).await;
        let shown = if sold > 0.0 { sold } else { copy_size };
        log_trade(store, t, "SELL", shown, price, if ok { "FILLED" } else { "FAILED" });
        info!("📋 SELL {:.2} @ {:.4} in {} ({})", shown, price, &t.market_id[..t.market_id.len().min(10)], if ok {"ok"} else {"fail"});
    }
}
