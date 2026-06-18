//! "sell-with-target" mode — same ENTRY as brownfox (one fixed-SHARE buy per
//! market at the target's price), but a different EXIT: NO resting +1c sell, NO
//! markup, NO forced-exit-on-below-mark. Instead, on the target's FIRST sell in
//! that market (or, as a backstop, when the target no longer holds it), we
//! MARKET-SELL ALL our shares regardless of price — we exit *with* the target.
//!
//! The market-sell runs in a DETACHED worker task (bounded by a semaphore) so it
//! never blocks detection/entry of other markets — unlike brownfox's inline
//! forced_exit which stalls the whole strategy for seconds.
//!
//! Concurrency invariant: the strategy task processes exactly ONE event (a
//! TargetTrade or a reconcile tick) fully before the next, and it exclusively
//! owns `State` (no locks). When an exit is handed off, the main task REMOVES the
//! position from its map (keeping the market in `entered` so it's never
//! re-bought) and a single detached worker owns the sell. So each market is
//! driven by exactly one actor at a time → no double-sell, no races. Restart:
//! EXITING records resume (one worker each); DONE/ABORTED/STUCK are terminal.
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use futures_util::FutureExt;
use tokio::sync::mpsc::Receiver;
use tokio::sync::Semaphore;
use tokio::time::{interval, sleep};
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};
use crate::copytrader::{Side, TargetTrade};

const EPS: f64 = 0.01;
const MIN_SHARES: f64 = 5.0;
/// Cap on concurrent exit workers so a burst of target sells can't stampede the
/// exchange into 429s (which would wreck fills); excess exits queue on the permit.
const MAX_CONCURRENT_EXITS: usize = 6;

#[derive(Clone)]
struct Pos {
    market_id: String,
    token_id: String,
    neg_risk: bool,
    tick: f64,
    buy_order_id: Option<String>,
    bought: f64, // authoritative matched fill (from the buy order), not the lagging /positions
    status: String, // ACTIVE / EXITING / DONE / ABORTED / STUCK
    title: String,
    outcome: String,
}

#[derive(Default)]
struct State {
    positions: HashMap<String, Pos>,
    entered: HashSet<String>,
}

/// Owned snapshot handed to a detached exit worker (borrows nothing from State).
struct ExitJob {
    market: String,
    token: String,
    neg_risk: bool,
    tick: f64,
    known_shares: f64,
    title: String,
    outcome: String,
}

pub struct SellWithTarget {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    sem: Arc<Semaphore>,
}

impl SellWithTarget {
    pub fn new(cfg: Config, exec: Arc<Executor>, store: Arc<Store>) -> Self {
        SellWithTarget { cfg, exec, store, sem: Arc::new(Semaphore::new(MAX_CONCURRENT_EXITS)) }
    }

    pub async fn run(self, mut rx: Receiver<TargetTrade>) {
        let mut st = self.load().await;
        let mut tick = interval(self.cfg.sell_with_target_reconcile);
        info!(
            "🤝 sell-with-target: {} shares/market at the target's price; on the target's sell, place ONE GTC sell of all shares @ {:.2} (marketable — sweeps the book down to the floor, rests remainder there). non-blocking workers, placement retries={}",
            self.cfg.sell_with_target_trade_size_shares, self.cfg.sell_with_target_exit_price, self.cfg.sell_with_target_market_sell_retries
        );
        loop {
            tokio::select! {
                maybe = rx.recv() => match maybe {
                    Some(t) => self.on_trade(&mut st, t).await,
                    None => { warn!("🤝 sell-with-target: trade channel closed"); break; }
                },
                _ = tick.tick() => self.reconcile(&mut st).await,
            }
        }
    }

    async fn load(&self) -> State {
        let mut st = State::default();
        st.entered = self.store.swt_markets(); // every market ever entered — never re-buy
        for (market, rec) in self.store.active_swt() {
            // active_swt() returns ONLY status ACTIVE or EXITING (DONE/ABORTED/STUCK
            // are terminal — STUCK is NOT auto-resumed, to avoid a duplicate racing
            // worker; it requires a manual restart of the position).
            let neg_risk = self.exec.token_neg_risk(&rec.token_id).await;
            let tick = self.exec.tick_size(&rec.token_id).await;
            let (buy_oid, matched) = self.find_order(&rec.token_id, "BUY", rec.buy_price).await;
            // STARTUP RECOVERY ONLY (not the live path): after a restart the buy may
            // have filled and left the book, so we can't reconstruct our size from
            // order data alone — read /positions ONCE here to recover it. Every LIVE
            // decision below uses CLOB order fills, never /positions.
            let holdings = self.exec.token_holdings(&rec.token_id).await;

            if rec.status == "EXITING" {
                // An exit was in progress when we stopped — resume it (exactly one
                // worker; the main task does NOT also track it).
                let known = matched.max(holdings);
                info!("🤝 sell-with-target: resuming in-progress exit for {} ({:.2} sh)", short(&market), known);
                self.spawn_exit(ExitJob {
                    market: market.clone(),
                    token: rec.token_id.clone(),
                    neg_risk,
                    tick,
                    known_shares: known,
                    title: rec.title.clone(),
                    outcome: String::new(),
                });
                continue;
            }
            // ACTIVE: rebuild and keep waiting for the target's sell.
            st.positions.insert(
                market.clone(),
                Pos {
                    market_id: market,
                    token_id: rec.token_id.clone(),
                    neg_risk,
                    tick,
                    buy_order_id: buy_oid,
                    bought: matched.max(holdings),
                    status: "ACTIVE".into(),
                    title: rec.title.clone(),
                    outcome: String::new(),
                },
            );
        }
        info!("🤝 sell-with-target: loaded {} entered markets, {} active", st.entered.len(), st.positions.len());
        st
    }

    async fn on_trade(&self, st: &mut State, t: TargetTrade) {
        match t.side {
            Side::Buy => self.on_buy(st, t).await,
            Side::Sell => self.on_sell(st, t).await,
        }
    }

    async fn on_buy(&self, st: &mut State, t: TargetTrade) {
        let market = t.market_id.clone();
        if market.is_empty() || t.token_id.is_empty() {
            return;
        }
        if st.entered.contains(&market) || st.positions.contains_key(&market) {
            info!("🤝 sell-with-target: already entered {} — ignoring repeat target buy", label(&t.title, &t.outcome, &market));
            return;
        }
        st.entered.insert(market.clone());

        let tick = self.exec.tick_size(&t.token_id).await;
        let buy_price = snap_price(t.price, tick);
        if buy_price <= 0.0 || buy_price >= 1.0 {
            warn!("🤝 sell-with-target: target price {:.4} not placeable in {} — skipping", t.price, label(&t.title, &t.outcome, &market));
            st.entered.remove(&market);
            return;
        }
        let size = self.cfg.sell_with_target_trade_size_shares.max(MIN_SHARES);
        let neg_risk = self.exec.token_neg_risk(&t.token_id).await;

        // Persist the reservation BEFORE placing (crash-safe: never double-buy).
        self.store.record_swt(
            &market,
            BrownfoxRecord {
                token_id: t.token_id.clone(),
                buy_price,
                sell_price: 0.0,
                status: "ACTIVE".into(),
                title: t.title.clone(),
                placed_at_ms: 0,
                order_id: String::new(),
            },
        );
        info!("🤝 sell-with-target: target BOUGHT {} @ {:.4} ({:.0} sh) → placing {} sh buy @ {:.4}, will sell WITH the target", label(&t.title, &t.outcome, &market), t.price, t.size, size, buy_price);

        let mut pos = Pos {
            market_id: market.clone(),
            token_id: t.token_id.clone(),
            neg_risk,
            tick,
            buy_order_id: None,
            bought: 0.0,
            status: "ACTIVE".into(),
            title: t.title.clone(),
            outcome: t.outcome.clone(),
        };
        match self.exec.place_gtc(&pos.token_id, neg_risk, SIDE_BUY, buy_price, size, tick).await {
            Ok(oid) => {
                pos.buy_order_id = Some(oid);
                self.store.record_trade(TradeLog {
                    time: now_str(),
                    market: market.clone(),
                    title: t.title.clone(),
                    side: "BUY".into(),
                    size,
                    price: buy_price,
                    status: "RESTING".into(),
                });
                st.positions.insert(market, pos);
            }
            Err(e) => {
                let (found, _) = self.find_order(&pos.token_id, "BUY", buy_price).await;
                if let Some(oid) = found {
                    pos.buy_order_id = Some(oid);
                    warn!("🤝 sell-with-target: place errored ({e}) but found resting buy in {} - adopting", label(&t.title, &t.outcome, &market));
                    st.positions.insert(market, pos);
                } else {
                    warn!("🤝 sell-with-target: buy placement failed in {} ({e}) - releasing market", label(&t.title, &t.outcome, &market));
                    self.store.delete_swt(&market);
                    st.entered.remove(&market);
                }
            }
        }
    }

    async fn on_sell(&self, st: &mut State, t: TargetTrade) {
        let market = t.market_id.clone();
        // Only the FIRST sell of an ACTIVE position triggers an exit; once handed
        // off the position is gone from the map, so later sells are ignored.
        let mut pos = match st.positions.get(&market) {
            Some(p) if p.status == "ACTIVE" => p.clone(),
            _ => return,
        };
        info!("🤝 sell-with-target: target SOLD {} @ {:.4} — exiting WITH them (market-sell all)", label(&t.title, &t.outcome, &market), t.price);
        self.begin_exit(st, &mut pos).await;
    }

    /// Finalize the fill, then hand the exit off to a detached worker (or ABORT
    /// if our buy never filled). After this the main task no longer tracks the
    /// market (it stays in `entered`, never re-bought).
    async fn begin_exit(&self, st: &mut State, pos: &mut Pos) {
        // Cancel the resting buy so no more fills land after handoff, then read the
        // authoritative matched fill (lag-free) + current holdings.
        if let Some(oid) = pos.buy_order_id.clone() {
            let _ = self.exec.cancel_confirmed(&oid, &pos.token_id).await;
        }
        // Authoritative size from the buy ORDER's matched fill (CLOB order data,
        // lag-free). The buy is now cancelled, so order_matched reflects the FINAL
        // filled amount — no late fills can land. NO Data-API /positions read: it
        // lags and would either stall the exit or abort a real position.
        self.refresh_bought(pos).await;
        let known = pos.bought;

        if known < MIN_SHARES {
            // Target left before our buy filled (or only dust) — nothing to sell.
            self.store.update_swt_status(&pos.market_id, "ABORTED");
            st.positions.remove(&pos.market_id); // stays in `entered`
            info!("🤝 sell-with-target: {} — buy never filled, nothing to sell, aborted", label(&pos.title, &pos.outcome, &pos.market_id));
            return;
        }
        // Persist EXITING BEFORE spawning (restart-safe), hand off, keep in `entered`.
        self.store.update_swt_status(&pos.market_id, "EXITING");
        let job = ExitJob {
            market: pos.market_id.clone(),
            token: pos.token_id.clone(),
            neg_risk: pos.neg_risk,
            tick: pos.tick,
            known_shares: known,
            title: pos.title.clone(),
            outcome: pos.outcome.clone(),
        };
        st.positions.remove(&pos.market_id);
        self.spawn_exit(job);
    }

    /// Periodic: keep our authoritative buy fill (`bought`, from CLOB order data)
    /// fresh while we wait for the target's sell. The old missed-sell backstop —
    /// which polled the laggy Data-API /positions for the TARGET and fired minutes
    /// late — is GONE: a dropped WS sell is now caught in ~1-2s by the /activity
    /// safety-net poll (see monitor), which delivers it here as a normal Sell event.
    async fn reconcile(&self, st: &mut State) {
        let markets: Vec<String> = st.positions.keys().cloned().collect();
        for market in markets {
            let mut pos = match st.positions.get(&market) {
                Some(p) if p.status == "ACTIVE" => p.clone(),
                _ => continue,
            };
            self.refresh_bought(&mut pos).await;
            st.positions.insert(market, pos);
        }
    }

    /// Spawn the detached, panic-isolated exit worker.
    fn spawn_exit(&self, job: ExitJob) {
        let exec = self.exec.clone();
        let store = self.store.clone();
        let sem = self.sem.clone();
        let retries = self.cfg.sell_with_target_market_sell_retries;
        let exit_price = self.cfg.sell_with_target_exit_price;
        tokio::spawn(async move {
            let market = job.market.clone();
            let store2 = store.clone();
            let outcome = std::panic::AssertUnwindSafe(exit_worker(exec, store, sem, retries, exit_price, job))
                .catch_unwind()
                .await;
            if outcome.is_err() {
                warn!("⛔ sell-with-target: exit worker PANICKED for {} — marking STUCK, MANUAL ACTION NEEDED", short(&market));
                store2.update_swt_status(&market, "STUCK");
            }
        });
    }

    async fn refresh_bought(&self, pos: &mut Pos) {
        if let Some(oid) = &pos.buy_order_id {
            if let Some(m) = self.exec.order_matched(oid).await {
                if m > pos.bought {
                    pos.bought = m;
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

/// Detached worker: place ONE GTC SELL limit for our whole position at the exit
/// FLOOR price. Priced low (e.g. 0.10) so it's MARKETABLE — the matching engine
/// crosses the bids from the top down (filling at 0.50, 0.49, … at the BID prices,
/// not the floor) and rests any unfilled remainder at the floor until a bid returns
/// or the market resolves. This is a plain limit order the exchange accepts; we do
/// NOT chase the bid / send a "market" order (those get rejected on a fast-moving
/// book). Size + fills are all from CLOB order data — never the laggy /positions.
/// Bounded by the shared semaphore.
async fn exit_worker(
    exec: Arc<Executor>,
    store: Arc<Store>,
    sem: Arc<Semaphore>,
    retries: u32,
    exit_price: f64,
    job: ExitJob,
) {
    let _permit = match sem.acquire_owned().await {
        Ok(p) => p,
        Err(_) => return,
    };
    let lbl = label(&job.title, &job.outcome, &job.market);
    let size = job.known_shares;
    if size < MIN_SHARES {
        store.update_swt_status(&job.market, "DONE");
        info!("🤝 sell-with-target: nothing to sell in {} ({:.2} sh) — done", lbl, size);
        return;
    }
    info!("🤝 sell-with-target: exiting {} — placing GTC sell of {:.2} sh @ {:.2} (marketable: fills every bid from the top down to {:.2})", lbl, size, exit_price, exit_price);

    // Retry only the PLACEMENT (transient POST errors). Once the order is live it
    // does the sweeping itself and rests the remainder — no per-fill retry loop.
    for attempt in 1..=retries.max(1) {
        match exec.place_gtc(&job.token, job.neg_risk, SIDE_SELL, exit_price, size, job.tick).await {
            Ok(oid) => {
                // Let the marketable order cross, then record what it swept
                // (authoritative order fill — never /positions).
                sleep(Duration::from_millis(1500)).await;
                let filled = exec.order_matched(&oid).await.unwrap_or(0.0);
                if filled > EPS {
                    store.record_trade(TradeLog {
                        time: now_str(),
                        market: job.market.clone(),
                        title: job.title.clone(),
                        side: "SELL".into(),
                        size: filled,
                        price: exit_price,
                        status: "FILLED".into(),
                    });
                }
                store.update_swt_status(&job.market, "DONE");
                info!(
                    "🤝 sell-with-target: exit order live for {} ✅ — swept {:.2}/{:.2} sh now; any remainder rests at {:.2} until a bid returns / resolution",
                    lbl, filled, size, exit_price
                );
                return;
            }
            Err(e) => {
                warn!("🤝 sell-with-target: {} exit-sell placement {}/{} errored ({e}) - retry", lbl, attempt, retries.max(1));
                sleep(Duration::from_millis(500)).await;
            }
        }
    }
    store.update_swt_status(&job.market, "STUCK");
    warn!("⛔ sell-with-target: could NOT place the {:.2} sh exit sell @ {:.2} in {} after {} tries — MANUAL ACTION NEEDED", size, exit_price, lbl, retries.max(1));
}

fn now_str() -> String {
    chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string()
}
fn short(s: &str) -> &str {
    &s[..s.len().min(10)]
}
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
