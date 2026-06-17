//! Stale-order sweep: cancel resting BUY orders once their market's price has
//! reached the cancel threshold (a buy left below a market that ran to ~1.0 will
//! never fill and just locks capital). BUY-only — never touches resting sells.
use std::collections::HashMap;
use std::sync::Arc;

use tokio::time::interval;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::config::Config;

pub async fn run(cfg: Config, exec: Arc<Executor>) {
    let mut tick = interval(cfg.stale_sweep);
    info!("🧹 stale-order sweep: cancel BUYs at market >= {} every {:?}", cfg.stale_cancel_price, cfg.stale_sweep);
    loop {
        tick.tick().await;
        let orders = exec.all_open_orders().await;
        let mut price_cache: HashMap<String, f64> = HashMap::new();
        for (id, token, side, _price) in orders {
            if side != "BUY" || token.is_empty() {
                continue;
            }
            let mid = if let Some(m) = price_cache.get(&token) {
                *m
            } else {
                let (bid, ask) = exec.book(&token).await;
                let m = if bid > 0.0 && ask > 0.0 { (bid + ask) / 2.0 } else { bid.max(ask) };
                price_cache.insert(token.clone(), m);
                m
            };
            if mid >= cfg.stale_cancel_price {
                if exec.cancel_order(&id).await.is_ok() {
                    info!("🧹 cancelled stale BUY {} (market {:.4} >= {})", &id[..id.len().min(10)], mid, cfg.stale_cancel_price);
                }
            }
        }
    }
}
