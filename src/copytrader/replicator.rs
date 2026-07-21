//! General copy replicator: copy the target's buys/sells proportionally.
//! Ported from the Python trade_replicator + the weather/auto-sell sell rules.
//!  - BUY  : copy_percentage of their size.
//!  - SELL : capped at OUR position (from our own fills — never the laggy /positions).
//!
//! Order type is selectable for EVERY order (`COPY_ORDER_TYPE`):
//!  - `gtc` (default) — a GTC limit at the target's exact price (purest mirror; rests when
//!    the book has already moved past it). Optional weather price-chasing applies here.
//!  - `fak` — fill-and-kill AT THE LIVE QUOTE: crosses and fills now, remainder killed, so
//!    nothing of ours ever rests. No "ahead" buffer is used: a FAK that crosses nothing
//!    because the quote moved is simply RE-QUOTED against a fresh book by the retry loop.
//!
//! Every placement is retried on TRANSIENT failures (network/5xx/rate-limit) with backoff and
//! gives up immediately on GENUINE ones (insufficient balance/allowance, closed market, …).
//! Each target trade runs on its own detached, semaphore-bounded worker, so a slow retry in
//! one market never blocks copying another. Duplicate protection: the monitor dedups target
//! trades, and before any retry we check whether the previous attempt actually landed (a
//! resting order at our price for GTC, or a moved position for FAK) and never re-place blind.
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::mpsc::Receiver;
use tokio::sync::Semaphore;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::store::{Store, TradeLog};
use crate::copytrader::weather;
use crate::copytrader::{Side, TargetTrade};

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

fn short(s: &str) -> &str {
    &s[..s.len().min(10)]
}

/// A placement failure that is GENUINE — retrying it can never help, so give up at once.
/// Anything else (network, 5xx, timeout, rate-limit) is transient and worth retrying.
fn terminal_err(msg: &str) -> bool {
    let m = msg.to_lowercase();
    [
        "balance",
        "allowance",
        "insufficient",
        "not enough",
        "minimum",
        "min size",
        "too small",
        "invalid",
        "closed",
        "expired",
        "not found",
        "unauthorized",
        "forbidden",
    ]
    .iter()
    .any(|k| m.contains(k))
}

/// The market's calendar date (ET) for `COPY_MARKET_FROM_DATE`. Preferred source is the
/// up/down slug's trailing unix timestamp (`btc-updown-5m-1781699700`), which is exact;
/// otherwise a `Month DD` in the title. `None` = we can't date it (then we don't filter).
fn market_date(t: &TargetTrade) -> Option<chrono::NaiveDate> {
    if let Some(last) = t.slug.rsplit('-').next() {
        if let Ok(ts) = last.parse::<i64>() {
            if ts > 1_000_000_000 {
                // Market windows are labelled in ET; ET = EDT = UTC-4.
                return chrono::DateTime::from_timestamp(ts - 4 * 3600, 0).map(|d| d.date_naive());
            }
        }
    }
    // Fallback: "… - June 20, 10:00PM-10:05PM ET"
    let title = t.title.to_lowercase();
    for (idx, name) in [
        "january", "february", "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december",
    ]
    .iter()
    .enumerate()
    {
        if let Some(pos) = title.find(name) {
            let rest = &title[pos + name.len()..];
            let day: String = rest.chars().skip_while(|c| !c.is_ascii_digit()).take_while(|c| c.is_ascii_digit()).collect();
            if let Ok(d) = day.parse::<u32>() {
                let year = chrono::Datelike::year(&chrono::Utc::now());
                return chrono::NaiveDate::from_ymd_opt(year, idx as u32 + 1, d);
            }
        }
    }
    None
}

/// Live-quote price for a FAK: AT the best ask (buy) / best bid (sell) — no buffer. If the
/// quote is missing we fall back to the extreme so the order can still cross.
async fn fak_price(exec: &Arc<Executor>, token: &str, is_buy: bool, tick: f64) -> f64 {
    let tick = if tick > 0.0 { tick } else { 0.01 };
    let (bid, ask) = exec.book(token).await;
    let raw = if is_buy {
        if ask > 0.0 { ask } else { (1.0 - tick).max(tick) }
    } else if bid > 0.0 {
        bid
    } else {
        tick
    };
    snap_price(raw.max(tick), tick).min(snap_price((1.0 - tick).max(tick), tick))
}

/// An open order of `side` at `price` for this token — the anti-duplication check before a
/// retry: if the previous attempt actually landed, we must NOT place a second one.
async fn resting_at(exec: &Arc<Executor>, token: &str, side: u8, price: f64) -> bool {
    let want = if side == SIDE_BUY { "BUY" } else { "SELL" };
    exec.open_orders(token)
        .await
        .into_iter()
        .any(|(_, s, p, orig, matched)| s == want && (p - price).abs() < 1e-9 && (orig - matched) > 0.0)
}

/// Place one copy order, honouring `COPY_ORDER_TYPE`, retrying transient failures with
/// backoff and never duplicating. Returns `(shares_filled_or_placed, price_used)`.
async fn place_copy(
    cfg: &Config,
    exec: &Arc<Executor>,
    token: &str,
    neg_risk: bool,
    side: u8,
    ref_price: f64,
    size: f64,
    tick: f64,
) -> (f64, f64) {
    let is_buy = side == SIDE_BUY;
    let label = if is_buy { "BUY" } else { "SELL" };
    // Baseline position so an ambiguous FAK failure can be checked for "did it land?".
    let base = if cfg.copy_use_fak { exec.filled_position(token).await } else { None };
    let mut delay = Duration::from_millis(400);
    let mut last_price = ref_price;

    for attempt in 1..=cfg.copy_place_retries {
        // Re-quote every attempt: FAK sits at the LIVE quote, GTC mirrors their price.
        let price = if cfg.copy_use_fak {
            fak_price(exec, token, is_buy, tick).await
        } else {
            snap_price(ref_price, tick)
        };
        last_price = price;
        if price <= 0.0 || price >= 1.0 {
            warn!("📋 {label}: unusable price {price:.4} — skipping");
            return (0.0, price);
        }
        let otype = if cfg.copy_use_fak { "FAK" } else { "GTC" };
        match exec.place_order(token, neg_risk, side, price, size, tick, otype).await {
            Ok(oid) => {
                if !cfg.copy_use_fak {
                    return (size, price); // GTC accepted — fills now or rests
                }
                let matched = exec.order_matched(&oid).await.unwrap_or(0.0);
                if matched > 0.0 {
                    return (matched, price);
                }
                // FAK crossed nothing (the quote moved between our read and the order
                // landing). Nothing rests, so re-quoting can never duplicate.
                warn!("📋 {label} FAK crossed nothing @ {price:.4} (attempt {attempt}/{}) — re-quoting", cfg.copy_place_retries);
            }
            Err(e) => {
                let msg = e.to_string();
                if terminal_err(&msg) {
                    warn!("📋 {label} failed — genuine reason, not retrying: {msg}");
                    return (0.0, price);
                }
                // Transient/ambiguous: the POST may still have landed. Never re-place blind.
                if !cfg.copy_use_fak && resting_at(exec, token, side, price).await {
                    warn!("📋 {label} errored but our order IS resting @ {price:.4} — not duplicating");
                    return (size, price);
                }
                if let (Some(b), Some(now)) = (base, exec.filled_position(token).await) {
                    let delta = if is_buy { now - b } else { b - now };
                    if delta > 0.01 {
                        warn!("📋 {label} errored but the fill landed ({delta:.2} sh) — not duplicating");
                        return (delta, price);
                    }
                }
                warn!("📋 {label} failed — transient (attempt {attempt}/{}): {msg}", cfg.copy_place_retries);
            }
        }
        tokio::time::sleep(delay).await;
        delay = (delay * 2).min(Duration::from_secs(5));
    }
    warn!("📋 {label}: gave up after {} attempts", cfg.copy_place_retries);
    (0.0, last_price)
}

pub async fn run(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, mut rx: Receiver<TargetTrade>) {
    info!(
        "📋 copy replicator: {}% of target size, orders={}, retries={}, max in-flight={}",
        cfg.copy_percentage * 100.0,
        if cfg.copy_use_fak { "FAK at the live quote" } else { "GTC at the target's price" },
        cfg.copy_place_retries,
        cfg.copy_max_concurrent
    );
    if let Some(d) = cfg.copy_market_from_date {
        info!("📋 market filter: only copying markets dated {d} or later (COPY_MARKET_FROM_DATE)");
    }
    let sem = Arc::new(Semaphore::new(cfg.copy_max_concurrent));
    while let Some(t) = rx.recv().await {
        // DATE FILTER — don't join a target's run mid-stream. Markets we can't date are
        // allowed through (the up/down slugs we care about always carry a timestamp).
        if let Some(from) = cfg.copy_market_from_date {
            if let Some(d) = market_date(&t) {
                if d < from {
                    info!("📋 skip {}: market dated {d} is before {from}", short(&t.market_id));
                    continue;
                }
            }
        }
        // Detached, bounded worker: a slow retry in one market never blocks the next trade.
        let permit = match sem.clone().acquire_owned().await {
            Ok(p) => p,
            Err(_) => break,
        };
        let (cfg2, exec2, store2) = (cfg.clone(), exec.clone(), store.clone());
        tokio::spawn(async move {
            let _permit = permit;
            match t.side {
                Side::Buy => handle_buy(&cfg2, &exec2, &store2, &t).await,
                Side::Sell => handle_sell(&cfg2, &exec2, &store2, &t).await,
            }
        });
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

    let (filled, price) = if !cfg.copy_use_fak && cfg.weather_enabled {
        let f = weather::chase_buy(exec, &t.token_id, neg_risk, t.price, size, tick, cfg).await;
        (f, t.price)
    } else {
        place_copy(cfg, exec, &t.token_id, neg_risk, SIDE_BUY, t.price, size, tick).await
    };

    if filled > 0.0 || !new_market {
        store.mark_copied(&t.market_id, &t.token_id);
    }
    log_trade(store, t, "BUY", if filled > 0.0 { filled } else { size }, price,
              if filled > 0.0 { "FILLED" } else { "RESTING" });
    info!("📋 copied BUY {:.2}/{:.2} sh in {} @ {:.4}", filled, size, short(&t.market_id), price);
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
            let (sold, price) = place_copy(cfg, exec, &t.token_id, neg_risk, SIDE_SELL, t.price, copy_size, tick).await;
            let shown = if sold > 0.0 { sold } else { copy_size };
            log_trade(store, t, "SELL", shown, price, if sold > 0.0 { "FILLED" } else { "FAILED" });
            info!("📋 SELL {:.2} @ {:.4} in {}", shown, price, short(&t.market_id));
            return;
        }
    }

    // Off-price / no auto-sell: weather chase down (gtc mode), else FAK/GTC via place_copy.
    if !cfg.copy_use_fak && cfg.weather_enabled {
        let sold = weather::chase_sell(exec, &t.token_id, neg_risk, t.price, copy_size, tick, cfg).await;
        log_trade(store, t, "SELL", sold, t.price, if sold > 0.0 { "FILLED" } else { "RESTING" });
        info!("📋 copied SELL {:.2}/{:.2} (chase) in {}", sold, copy_size, short(&t.market_id));
    } else {
        let (sold, price) = place_copy(cfg, exec, &t.token_id, neg_risk, SIDE_SELL, t.price, copy_size, tick).await;
        let shown = if sold > 0.0 { sold } else { copy_size };
        log_trade(store, t, "SELL", shown, price, if sold > 0.0 { "FILLED" } else { "FAILED" });
        info!("📋 SELL {:.2} @ {:.4} in {}", shown, price, short(&t.market_id));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn genuine_errors_are_not_retried() {
        assert!(terminal_err("not enough balance"));
        assert!(terminal_err("insufficient allowance for token"));
        assert!(terminal_err("market is closed"));
        assert!(terminal_err("invalid order size"));
        // transient / worth retrying
        assert!(!terminal_err("error sending request: connection reset"));
        assert!(!terminal_err("502 Bad Gateway"));
        assert!(!terminal_err("timed out"));
        assert!(!terminal_err("too many requests"));
    }
}
