//! "99c" mode — a buy-and-hold copier (e.g. for a target that buys BTC 5-minute
//! markets at ~0.99 and holds them to resolution). For each market the target
//! ENTERS, place ONE fixed-share GTC buy at the target's price and HOLD: no
//! resting sell, no markup, no forced exit, no target-exit detection — the
//! target holds to resolution and so do we.
//!
//! - One buy per market, restart-safe (reconstructed from the store + on-chain
//!   holdings/open orders, so a restart never double-buys).
//! - Target SELLs are ignored — we hold to resolution.
//! - An UNFILLED resting buy is cancelled after `NINETYNINE_BUY_MAX_AGE_SECS`
//!   (0 = never) so a buy whose market already resolved doesn't strand capital.
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use tokio::sync::mpsc::Receiver;
use tokio::time::interval;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY};
use crate::config::Config;
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};
use crate::copytrader::{Side, TargetTrade};

const EPS: f64 = 0.01;
const MIN_SHARES: f64 = 5.0;

// A market we've entered and are still waiting on the buy for. Buy-and-hold has
// nothing to re-place, so reconcile only needs the order id, size, and age —
// terminal positions (filled/cancelled) are dropped from the live set entirely.
#[derive(Clone)]
struct Pos {
    market_id: String,
    token_id: String,
    buy_order_id: Option<String>,
    buy_size: f64,
    placed_at_ms: u64, // wall-clock ms (persisted) so stale-cancel survives restarts
    partial_logged: bool,
    title: String,
    outcome: String,
}

#[derive(Default)]
struct State {
    positions: HashMap<String, Pos>,
    entered: HashSet<String>,
}

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
        let mut st = self.load().await;
        let mut tick = interval(self.cfg.ninetynine_reconcile);
        info!(
            "💯 99c: {} shares/market at the target's price, buy-and-hold (no sell). stale-cancel: {}",
            self.cfg.ninetynine_trade_size_shares,
            match self.cfg.ninetynine_buy_max_age {
                Some(d) => format!("{}s", d.as_secs()),
                None => "off".into(),
            }
        );
        loop {
            tokio::select! {
                maybe = rx.recv() => match maybe {
                    // Only target BUYs matter; SELLs are ignored (we hold to resolution).
                    Some(t) => if let Side::Buy = t.side { self.on_buy(&mut st, t).await },
                    None => { warn!("💯 99c: trade channel closed"); break; }
                },
                _ = tick.tick() => self.reconcile(&mut st).await,
            }
        }
    }

    async fn load(&self) -> State {
        let mut st = State::default();
        st.entered = self.store.ninetynine_markets();
        for (market, rec) in self.store.active_99c() {
            let holdings = self.exec.token_holdings(&rec.token_id).await;
            let (buy_oid, _matched) = self.find_order(&rec.token_id, "BUY", rec.buy_price).await;
            if holdings > EPS {
                // Already filled before the restart — we're holding to resolution.
                self.store.update_99c_status(&market, "FILLED");
            } else if buy_oid.is_some() {
                // Buy still resting — resume managing it.
                st.positions.insert(
                    market.clone(),
                    Pos {
                        market_id: market.clone(),
                        token_id: rec.token_id.clone(),
                        buy_order_id: buy_oid,
                        buy_size: self.cfg.ninetynine_trade_size_shares.max(MIN_SHARES),
                        // Keep the original placement time so the stale-cancel window
                        // is honoured across restarts (fallback to now for old records).
                        placed_at_ms: if rec.placed_at_ms > 0 { rec.placed_at_ms } else { now_ms() },
                        partial_logged: false,
                        title: rec.title.clone(),
                        outcome: String::new(),
                    },
                );
            } else {
                // No holdings and no resting buy: it filled and resolved, or never
                // filled. Nothing to manage — and we never re-buy (`entered` holds it).
                self.store.update_99c_status(&market, "CANCELLED");
            }
        }
        info!(
            "💯 99c: loaded {} entered markets, resumed {} pending buys",
            st.entered.len(),
            st.positions.len()
        );
        st
    }

    async fn on_buy(&self, st: &mut State, t: TargetTrade) {
        let market = t.market_id.clone();
        if market.is_empty() || t.token_id.is_empty() {
            return;
        }
        if st.entered.contains(&market) || st.positions.contains_key(&market) {
            info!("💯 99c: already placed our one buy in {} — ignoring repeat target buy", label(&t.title, &t.outcome, &market));
            return;
        }
        st.entered.insert(market.clone());

        let tick = self.exec.tick_size(&t.token_id).await;
        let buy_price = snap_price(t.price, tick);
        if buy_price <= 0.0 || buy_price >= 1.0 {
            warn!("💯 99c: target price {:.4} not placeable in {} — skipping", t.price, label(&t.title, &t.outcome, &market));
            st.entered.remove(&market);
            return;
        }
        let size = self.cfg.ninetynine_trade_size_shares.max(MIN_SHARES);
        let neg_risk = self.exec.token_neg_risk(&t.token_id).await;
        let placed = now_ms();

        // Persist the reservation BEFORE placing (crash-safe: never double-buy).
        self.store.record_99c(
            &market,
            BrownfoxRecord {
                token_id: t.token_id.clone(),
                buy_price,
                sell_price: 0.0,
                status: "ACTIVE".into(),
                title: t.title.clone(),
                placed_at_ms: placed,
            },
        );
        info!("💯 99c: target BOUGHT {} @ {:.4} ({:.0} sh) → placing {} sh buy @ {:.4}, holding to resolution", label(&t.title, &t.outcome, &market), t.price, t.size, size, buy_price);

        let make_pos = |oid: String| Pos {
            market_id: market.clone(),
            token_id: t.token_id.clone(),
            buy_order_id: Some(oid),
            buy_size: size,
            placed_at_ms: placed,
            partial_logged: false,
            title: t.title.clone(),
            outcome: t.outcome.clone(),
        };

        match self.exec.place_gtc(&t.token_id, neg_risk, SIDE_BUY, buy_price, size, tick).await {
            Ok(oid) => {
                self.store.record_trade(TradeLog {
                    time: chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string(),
                    market: market.clone(),
                    title: t.title.clone(),
                    side: "BUY".into(),
                    size,
                    price: buy_price,
                    status: "RESTING".into(),
                });
                st.positions.insert(market.clone(), make_pos(oid));
            }
            Err(e) => {
                // Maybe it actually landed (transient error) — adopt a live buy.
                let (found, _) = self.find_order(&t.token_id, "BUY", buy_price).await;
                if let Some(oid) = found {
                    warn!("💯 99c: place errored ({e}) but found resting buy in {} - adopting", label(&t.title, &t.outcome, &market));
                    st.positions.insert(market.clone(), make_pos(oid));
                } else {
                    warn!("💯 99c: buy placement failed in {} ({e}) - releasing market", label(&t.title, &t.outcome, &market));
                    self.store.delete_99c(&market);
                    st.entered.remove(&market);
                }
            }
        }
    }

    async fn reconcile(&self, st: &mut State) {
        let markets: Vec<String> = st.positions.keys().cloned().collect();
        for market in markets {
            let mut pos = match st.positions.get(&market).cloned() {
                Some(p) => p,
                None => continue,
            };
            let holdings = self.exec.token_holdings(&pos.token_id).await;
            let matched = match &pos.buy_order_id {
                Some(oid) => self.exec.order_matched(oid).await.unwrap_or(0.0),
                None => 0.0,
            };
            let filled = holdings.max(matched);

            // Fully filled -> holding to resolution; stop managing.
            if filled >= pos.buy_size - EPS {
                info!("💯 99c: filled {:.2} sh in {} — holding to resolution ✅", filled, label(&pos.title, &pos.outcome, &pos.market_id));
                self.store.update_99c_status(&pos.market_id, "FILLED");
                st.positions.remove(&market);
                continue;
            }

            // Past the stale window -> cancel the resting buy so we don't strand
            // capital on a market that's likely already resolved.
            let age_ms = now_ms().saturating_sub(pos.placed_at_ms);
            let stale = self
                .cfg
                .ninetynine_buy_max_age
                .map(|m| age_ms as u128 > m.as_millis())
                .unwrap_or(false);
            if stale {
                if let Some(oid) = pos.buy_order_id.clone() {
                    let _ = self.exec.cancel_confirmed(&oid, &pos.token_id).await;
                }
                let held = self.exec.token_holdings(&pos.token_id).await;
                if held > EPS {
                    info!("💯 99c: {} filled {:.2}/{:.0} sh before timeout — cancelled the rest, holding", label(&pos.title, &pos.outcome, &pos.market_id), held, pos.buy_size);
                    self.store.update_99c_status(&pos.market_id, "FILLED");
                } else {
                    let secs = self.cfg.ninetynine_buy_max_age.map(|d| d.as_secs()).unwrap_or(0);
                    info!("💯 99c: {} buy unfilled after {}s — cancelled to free capital", label(&pos.title, &pos.outcome, &pos.market_id), secs);
                    self.store.update_99c_status(&pos.market_id, "CANCELLED");
                }
                st.positions.remove(&market);
                continue;
            }

            // Still resting, partial-or-no fill, not yet stale: note the first
            // partial fill once, then keep waiting for the rest.
            if filled > EPS && !pos.partial_logged {
                info!("💯 99c: partial fill {:.2}/{:.0} sh in {} — waiting for the rest", filled, pos.buy_size, label(&pos.title, &pos.outcome, &pos.market_id));
                pos.partial_logged = true;
            }
            st.positions.insert(market, pos);
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

/// Wall-clock milliseconds (persisted so the stale-cancel window survives restarts).
fn now_ms() -> u64 {
    chrono::Utc::now().timestamp_millis().max(0) as u64
}

fn short(s: &str) -> &str {
    &s[..s.len().min(10)]
}
/// Human-readable market label for logs: `"<title> · <outcome> [0xabc123…]"`.
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
