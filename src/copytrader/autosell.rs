//! Auto-sell limit maintainer: keep a resting GTC sell at the standard price
//! (e.g. 0.999) covering our FULL holdings in every copied market, so exits
//! match the target with no lag. Ported from the hardened Python maintainer:
//! holdings-driven, only places when the tick can hit the target price (never
//! snaps below into a dump), capped by holdings (exchange rejects over-balance).
use std::sync::Arc;

use tokio::time::interval;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::SIDE_SELL;
use crate::config::Config;
use crate::copytrader::store::Store;

fn floor_to_tick(price: f64, tick: f64) -> f64 {
    if tick <= 0.0 {
        return price;
    }
    ((price / tick + 1e-9).floor() * tick * 1e10).round() / 1e10
}

pub async fn run(cfg: Config, exec: Arc<Executor>, store: Arc<Store>) {
    let mut tick_iv = interval(cfg.auto_sell_maintain);
    info!("🎯 auto-sell: resting sell @ {} for full position, every {:?}", cfg.auto_sell_price, cfg.auto_sell_maintain);
    loop {
        tick_iv.tick().await;
        for token in store.copied_tokens() {
            let holdings = exec.token_holdings(&token).await;
            if holdings < 5.0 {
                continue;
            }
            let tick = exec.tick_size(&token).await;
            let sell_price = floor_to_tick(cfg.auto_sell_price, tick);
            // Only place if the tick can hit the target price exactly. If it
            // snaps below (e.g. 0.999 -> 0.99 on a 0.01 tick), a lower sell
            // would dump into the bid - skip (the copy sell handles those).
            if sell_price < cfg.auto_sell_price - 1e-6 || sell_price <= 0.0 || sell_price >= 1.0 {
                continue;
            }
            let neg_risk = exec.token_neg_risk(&token).await;
            let resting: f64 = exec
                .open_orders(&token)
                .await
                .into_iter()
                .filter(|(_, s, p, ..)| s == "SELL" && (*p - sell_price).abs() < 1e-9)
                .map(|(_, _, _, orig, matched)| (orig - matched).max(0.0))
                .sum();
            let uncovered = ((holdings - resting) * 100.0).round() / 100.0;
            if uncovered >= 5.0 {
                match exec.place_gtc(&token, neg_risk, SIDE_SELL, sell_price, uncovered, tick).await {
                    Ok(_) => info!("🎯 auto-sell: resting {:.2} @ {:.4} for {}", uncovered, sell_price, &token[..token.len().min(10)]),
                    Err(e) => warn!("🎯 auto-sell place failed: {e}"),
                }
            }
        }
    }
}
