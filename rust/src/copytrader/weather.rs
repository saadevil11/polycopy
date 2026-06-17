//! Weather-mode execution: chase the price until filled. BUY rests 1 cent above
//! the reference and re-prices up as the ask moves (capped); SELL rests 1 tick
//! below and chases down. Ported from the hardened Python weather chaser, with
//! the duplicate-proof invariant: only re-place after a CONFIRMED cancel.
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY, SIDE_SELL};
use crate::config::Config;

fn round10(x: f64) -> f64 {
    (x * 1e10).round() / 1e10
}

/// Chase a BUY of `size` shares ~1 cent ahead of `reference` until filled.
/// Returns shares filled. Leaves a resting order if it can't improve further.
pub async fn chase_buy(
    exec: &Executor,
    token: &str,
    neg_risk: bool,
    reference: f64,
    size: f64,
    tick: f64,
    cfg: &Config,
) -> f64 {
    let tick = if tick > 0.0 { tick } else { 0.01 };
    let buy_ahead = ((cfg.weather_buy_ahead / tick).round().max(1.0)) * tick;
    let max_price = cfg.weather_max_buy_price.min(round10(1.0 - tick));
    let mut reference = reference;
    let mut active: Option<String> = None;
    let mut total = 0.0;
    let mut last_limit = 0.0;

    for chase in 0..cfg.weather_max_chases {
        let remaining = size - total;
        if remaining < 0.01 {
            break;
        }
        // Reclaim a prior resting order (confirmed) before re-pricing.
        if let Some(oid) = active.take() {
            let matched = exec.order_matched(&oid).await.unwrap_or(0.0);
            if !exec.cancel_confirmed(&oid, token).await {
                total += matched;
                break; // can't confirm -> stop, never duplicate
            }
            total += matched;
            if size - total < 0.01 {
                break;
            }
        }
        let limit = round10((snap_price(reference, tick) + buy_ahead).min(max_price));
        let place_size = (size - total).max(5.0);
        let oid = match exec.place_gtc(token, neg_risk, SIDE_BUY, limit, place_size, tick).await {
            Ok(o) => o,
            Err(e) => {
                let m = e.to_string().to_lowercase();
                if m.contains("balance") || m.contains("allowance") {
                    warn!("weather buy: insufficient balance - stopping");
                    break;
                }
                let (_, ask) = exec.book(token).await;
                if ask > 0.0 {
                    reference = ask;
                    continue;
                }
                break;
            }
        };
        last_limit = limit;
        info!("🌦️  weather chase {}/{}: BUY {:.2} @ {:.4} (1c ahead of {:.4})", chase + 1, cfg.weather_max_chases, place_size, limit, reference);
        tokio::time::sleep(cfg.weather_fill_wait).await;

        let matched = exec.order_matched(&oid).await.unwrap_or(0.0);
        if matched >= place_size - 0.01 {
            total += matched;
            return total;
        }
        active = Some(oid);

        let (_, ask) = exec.book(token).await;
        if ask <= 0.0 {
            break; // no quote -> leave resting
        }
        // Still marketable (our bid >= ask) -> it will fill; don't churn.
        if limit >= ask - 1e-9 {
            break;
        }
        // At the cap -> can't go higher.
        if limit >= max_price - 1e-9 {
            break;
        }
        reference = ask; // moved away -> chase up next loop
    }
    let _ = last_limit;
    // Capture fills on any order we left resting.
    if let Some(oid) = &active {
        total += exec.order_matched(oid).await.unwrap_or(0.0);
    }
    total
}

/// Chase a SELL of `size` shares down from `reference` (1 tick below, floored)
/// until filled. Used for off-standard-price sells. Returns shares filled.
pub async fn chase_sell(
    exec: &Executor,
    token: &str,
    neg_risk: bool,
    reference: f64,
    size: f64,
    tick: f64,
    cfg: &Config,
) -> f64 {
    let tick = if tick > 0.0 { tick } else { 0.01 };
    let min_price = tick;
    let mut reference = reference;
    let mut active: Option<String> = None;
    let mut total = 0.0;

    for _ in 0..cfg.weather_max_chases {
        let remaining = size - total;
        if remaining < 5.0 {
            break;
        }
        if let Some(oid) = active.take() {
            let matched = exec.order_matched(&oid).await.unwrap_or(0.0);
            if !exec.cancel_confirmed(&oid, token).await {
                total += matched;
                break;
            }
            total += matched;
            if size - total < 5.0 {
                break;
            }
        }
        let limit = round10((snap_price(reference, tick) - tick).max(min_price));
        let oid = match exec.place_gtc(token, neg_risk, SIDE_SELL, limit, size - total, tick).await {
            Ok(o) => o,
            Err(_) => break,
        };
        tokio::time::sleep(cfg.weather_fill_wait).await;
        let matched = exec.order_matched(&oid).await.unwrap_or(0.0);
        if matched >= (size - total) - 0.01 {
            total += matched;
            return total;
        }
        active = Some(oid);
        let (bid, _) = exec.book(token).await;
        if bid <= 0.0 || limit <= bid + 1e-9 {
            break; // marketable / no quote -> leave resting
        }
        if limit <= min_price + 1e-9 {
            break;
        }
        reference = bid;
    }
    if let Some(oid) = &active {
        total += exec.order_matched(oid).await.unwrap_or(0.0);
    }
    total
}
