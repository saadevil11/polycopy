//! "99c" mode — a buy-and-hold copier (e.g. a target that buys BTC 5-minute
//! markets at ~0.99 and holds to resolution). For each market the target ENTERS,
//! place ONE fixed-share buy at the target's price and HOLD: no sell, no markup.
//!
//! CONCURRENT execution: one task drains the trade channel, but each buy is
//! ATOMICALLY claimed (`store.claim_99c`, the single linearization point) and
//! then placed on its OWN spawned task, so a slow buy never delays detecting or
//! placing the next market's buy — critical at ~0.99 near resolution. There is no
//! shared mutable in-memory state: the claim guards against double-buys, and
//! reconcile is store-driven, so spawned tasks share only `Arc<Executor>` +
//! `Arc<Store>`. A semaphore bounds concurrent placements (avoids 429s).
//!
//! One buy per market (the claim covers active AND terminal records, so a market
//! is never re-bought — restart-safe). Target SELLs are ignored. An UNFILLED buy
//! is cancelled after `NINETYNINE_BUY_MAX_AGE_SECS` so a resolved-market buy
//! doesn't strand capital.
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tokio::sync::mpsc::Receiver;
use tokio::sync::Semaphore;
use tokio::time::interval;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY};
use crate::config::Config;
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};
use crate::copytrader::{Side, TargetTrade};

const EPS: f64 = 0.01;
const MIN_SHARES: f64 = 5.0;

pub struct NinetyNine {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
}

impl NinetyNine {
    pub fn new(cfg: Config, exec: Arc<Executor>, store: Arc<Store>) -> Self {
        NinetyNine { cfg, exec, store }
    }

    pub async fn run(self, mut rx: Receiver<TargetTrade>) {
        let st = Arc::new(self);
        let sem = Arc::new(Semaphore::new(st.cfg.ninetynine_max_concurrent_buys));
        info!(
            "💯 99c: {} shares/market at the target's price, buy-and-hold; parallel execution (≤{} concurrent buys). stale-cancel: {}",
            st.cfg.ninetynine_trade_size_shares,
            st.cfg.ninetynine_max_concurrent_buys,
            match st.cfg.ninetynine_buy_max_age {
                Some(d) => format!("{}s", d.as_secs()),
                None => "off".into(),
            }
        );
        if st.cfg.ninetynine_assets.is_empty() {
            info!("💯 99c: market filter OFF — copying the target's buys in ALL markets");
        } else {
            info!("💯 99c: market filter — only {}-updown-5m markets", st.cfg.ninetynine_assets.join("/"));
        }

        // Resume any persisted positions up-front (mark filled / stale-cancel).
        st.reconcile().await;

        // Single reconcile at a time (idempotent + store-driven, but skip overlap
        // so a slow reconcile can't pile up under the timer).
        let reconciling = Arc::new(AtomicBool::new(false));
        let mut tick = interval(st.cfg.ninetynine_reconcile);

        loop {
            tokio::select! {
                maybe = rx.recv() => match maybe {
                    Some(t) => {
                        // Only the target's BUYs matter (buy-and-hold; SELLs ignored).
                        // Spawn so the slow placement never blocks the next trade.
                        if let Side::Buy = t.side {
                            let st = st.clone();
                            let sem = sem.clone();
                            tokio::spawn(async move { st.handle_buy(sem, t).await });
                        }
                    }
                    None => { warn!("💯 99c: trade channel closed"); break; }
                },
                _ = tick.tick() => {
                    if !reconciling.swap(true, Ordering::SeqCst) {
                        let st = st.clone();
                        let flag = reconciling.clone();
                        tokio::spawn(async move {
                            st.reconcile().await;
                            flag.store(false, Ordering::SeqCst);
                        });
                    }
                }
            }
        }
    }

    /// Claim the market atomically, then (off the drain loop) place the buy.
    async fn handle_buy(&self, sem: Arc<Semaphore>, t: TargetTrade) {
        let market = t.market_id.clone();
        if market.is_empty() || t.token_id.is_empty() {
            return;
        }
        // Defense in depth (the monitor already drops non-matching markets when a
        // filter is set).
        if !slug_allowed(&t.slug, &self.cfg.ninetynine_assets) {
            return;
        }

        let tick = self.exec.tick_size(&t.token_id).await;
        let buy_price = snap_price(t.price, tick);
        if buy_price <= 0.0 || buy_price >= 1.0 {
            warn!("💯 99c: target price {:.4} not placeable in {} — skipping", t.price, label(&t.title, &t.outcome, &market));
            return;
        }

        // ATOMIC claim — persist the SNAPPED price so reconcile's find_order matches
        // the resting order exactly. Only the winner of the claim proceeds.
        let claimed = self.store.claim_99c(
            &market,
            BrownfoxRecord {
                token_id: t.token_id.clone(),
                buy_price,
                sell_price: 0.0,
                status: "ACTIVE".into(),
                title: t.title.clone(),
                placed_at_ms: now_ms(),
                order_id: String::new(),
            },
        );
        if !claimed {
            info!("💯 99c: already entered {} — ignoring repeat target buy", label(&t.title, &t.outcome, &market));
            return;
        }
        info!("💯 99c: target BOUGHT {} @ {:.4} ({:.0} sh) → placing {} sh buy @ {:.4}, holding to resolution", label(&t.title, &t.outcome, &market), t.price, t.size, self.cfg.ninetynine_trade_size_shares.max(MIN_SHARES), buy_price);

        // Bound concurrent placements (claim is already done, so this only throttles
        // the network span, never the dedup).
        let _permit = match sem.acquire().await {
            Ok(p) => p,
            Err(_) => return,
        };
        let size = self.cfg.ninetynine_trade_size_shares.max(MIN_SHARES);
        let neg_risk = self.exec.token_neg_risk(&t.token_id).await;
        match self.exec.place_gtc(&t.token_id, neg_risk, SIDE_BUY, buy_price, size, tick).await {
            Ok(oid) => {
                // Persist the order id so reconcile checks the fill via order_matched
                // (CLOB order data, lag-free) instead of the laggy /positions.
                self.store.set_99c_order_id(&market, &oid);
                self.store.record_trade(TradeLog {
                    time: now_str(),
                    market: market.clone(),
                    title: t.title.clone(),
                    side: "BUY".into(),
                    size,
                    price: buy_price,
                    status: "RESTING".into(),
                });
            }
            Err(e) => {
                // Adopt a live resting buy if it actually landed; else RELEASE the
                // claim so a later target buy in this market can retry.
                let (found, _) = self.find_order(&t.token_id, "BUY", buy_price).await;
                if let Some(id) = &found {
                    self.store.set_99c_order_id(&market, id);
                    warn!("💯 99c: place errored ({e}) but found resting buy in {} - keeping", label(&t.title, &t.outcome, &market));
                } else {
                    warn!("💯 99c: buy placement failed in {} ({e}) - releasing market", label(&t.title, &t.outcome, &market));
                    self.store.delete_99c(&market);
                }
            }
        }
    }

    /// Store-driven reconcile: mark FILLED positions, stale-cancel unfilled buys.
    /// Idempotent — safe to run repeatedly and to resume from after a restart.
    async fn reconcile(&self) {
        let size = self.cfg.ninetynine_trade_size_shares.max(MIN_SHARES);
        for (market, rec) in self.store.active_99c() {
            // Fill check from the buy ORDER's matched size (CLOB order data, lag-free)
            // — NEVER the laggy Data-API /positions. Legacy records with no stored id
            // fall back to the resting order's matched amount.
            let matched = if !rec.order_id.is_empty() {
                self.exec.order_matched(&rec.order_id).await.unwrap_or(0.0)
            } else {
                self.find_order(&rec.token_id, "BUY", rec.buy_price).await.1
            };

            if matched >= size - EPS {
                self.store.update_99c_status(&market, "FILLED");
                info!("💯 99c: filled {:.2} sh in {} — holding to resolution ✅", matched, label(&rec.title, "", &market));
                continue;
            }
            let stale = self
                .cfg
                .ninetynine_buy_max_age
                .map(|m| now_ms().saturating_sub(rec.placed_at_ms) as u128 > m.as_millis())
                .unwrap_or(false);
            if stale {
                // Cancel the resting buy (by stored id, else find it on the book).
                let oid = if !rec.order_id.is_empty() {
                    Some(rec.order_id.clone())
                } else {
                    self.find_order(&rec.token_id, "BUY", rec.buy_price).await.0
                };
                if let Some(id) = &oid {
                    let _ = self.exec.cancel_confirmed(id, &rec.token_id).await;
                }
                // Final fill before the cancel, from order data (not /positions).
                let filled = match &oid {
                    Some(id) => self.exec.order_matched(id).await.unwrap_or(0.0),
                    None => 0.0,
                };
                if filled > EPS {
                    self.store.update_99c_status(&market, "FILLED");
                    info!("💯 99c: {} filled {:.2}/{:.0} sh before timeout — cancelled the rest, holding", label(&rec.title, "", &market), filled, size);
                } else {
                    let secs = self.cfg.ninetynine_buy_max_age.map(|d| d.as_secs()).unwrap_or(0);
                    self.store.update_99c_status(&market, "CANCELLED");
                    info!("💯 99c: {} buy unfilled after {}s — cancelled to free capital", label(&rec.title, "", &market), secs);
                }
            }
        }
    }

    async fn find_order(&self, token: &str, side: &str, price: f64) -> (Option<String>, f64) {
        for (id, s, p, _orig, matched) in self.exec.open_orders(token).await {
            if s == side && (p - price).abs() < 1e-9 {
                return (Some(id), matched);
            }
        }
        (None, 0.0)
    }
}

fn now_ms() -> u64 {
    chrono::Utc::now().timestamp_millis().max(0) as u64
}
fn now_str() -> String {
    chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string()
}
fn short(s: &str) -> &str {
    &s[..s.len().min(10)]
}
/// Human-readable market label for logs.
fn label(title: &str, outcome: &str, market: &str) -> String {
    let id = short(market);
    if title.is_empty() {
        return format!("[{id}]");
    }
    let t: String = if title.chars().count() > 60 {
        title.chars().take(59).chain(std::iter::once('…')).collect()
    } else {
        title.to_string()
    };
    if outcome.is_empty() {
        format!("\"{t}\" [{id}]")
    } else {
        format!("\"{t}\" · {outcome} [{id}]")
    }
}

/// True if the market's slug is one of the allowed `<asset>-updown-5m-*` markets.
/// Empty `assets` = no filter (copy everything). Shared with the monitor so it can
/// drop (and not log) non-matching markets before they ever reach the strategy.
pub(crate) fn slug_allowed(slug: &str, assets: &[String]) -> bool {
    if assets.is_empty() {
        return true;
    }
    let s = slug.to_lowercase();
    assets.iter().any(|a| s.starts_with(&format!("{a}-updown-5m")))
}

#[cfg(test)]
mod tests {
    use super::slug_allowed;

    fn assets() -> Vec<String> {
        ["btc", "eth", "xrp", "doge", "bnb", "hype"].iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn filter_matches_only_listed_5m_markets() {
        let a = assets();
        assert!(slug_allowed("btc-updown-5m-1781742900", &a));
        assert!(slug_allowed("eth-updown-5m-1781742900", &a));
        assert!(slug_allowed("hype-updown-5m-1", &a));
        assert!(!slug_allowed("sol-updown-5m-1", &a)); // sol not in the list
        assert!(!slug_allowed("btc-updown-1h-1", &a)); // hourly, not 5m
        assert!(!slug_allowed("will-trump-win-2024", &a));
        assert!(!slug_allowed("", &a)); // unknown slug never matches when filtered
        assert!(slug_allowed("anything-at-all", &[])); // empty filter = allow all
    }
}
