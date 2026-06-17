//! Brownfox mode in Rust — a faithful port of the hardened Python state machine
//! (src/core/brownfox.py), driven off live holdings + the buy order's matched
//! amount, restart-safe, confirmed cancels, target-exit detection, guaranteed
//! retried market-sell. Runs as a single task that owns its state (no locks).
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::mpsc::Receiver;
use tokio::time::{interval, sleep};
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{SIDE_BUY, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::store::{BrownfoxRecord, Store};
use crate::copytrader::{Side, TargetTrade};

const EPS: f64 = 0.01;
const MIN_SHARES: f64 = 5.0;

#[derive(Clone)]
struct Pos {
    market_id: String,
    token_id: String,
    neg_risk: bool,
    tick: f64,
    buy_price: f64,
    sell_price: f64,
    status: String, // ACTIVE / EXITING / DONE / ABORTED / STUCK
    buy_order_id: Option<String>,
    buy_size: f64,
    buy_done: bool,
    bought: f64,
    peak_holdings: f64,
    exit_attempts: u32,
}

#[derive(Default)]
struct State {
    positions: HashMap<String, Pos>,
    entered: HashSet<String>,
    target_tokens: Option<HashSet<String>>,
    target_at: Option<Instant>,
}

pub struct Brownfox {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
}

impl Brownfox {
    pub fn new(cfg: Config, exec: Arc<Executor>, store: Arc<Store>) -> Self {
        Brownfox { cfg, exec, store }
    }

    pub async fn run(self, mut rx: Receiver<TargetTrade>) {
        let mut st = self.load().await;
        let mut tick = interval(self.cfg.brownfox_reconcile);
        info!(
            "🦊 brownfox: {} shares/market, sell +{}, reconcile {:?}",
            self.cfg.brownfox_trade_size_shares, self.cfg.brownfox_sell_markup, self.cfg.brownfox_reconcile
        );
        loop {
            tokio::select! {
                maybe = rx.recv() => match maybe {
                    Some(trade) => self.on_trade(&mut st, trade).await,
                    None => { warn!("🦊 brownfox: trade channel closed"); break; }
                },
                _ = tick.tick() => self.reconcile(&mut st).await,
            }
        }
    }

    async fn load(&self) -> State {
        let mut st = State::default();
        st.entered = self.store.brownfox_markets();
        for (market, rec) in self.store.active_brownfox() {
            let neg_risk = self.exec.token_neg_risk(&rec.token_id).await;
            let tick = self.exec.tick_size(&rec.token_id).await;
            let mut pos = Pos {
                market_id: market.clone(),
                token_id: rec.token_id.clone(),
                neg_risk,
                tick,
                buy_price: rec.buy_price,
                sell_price: rec.sell_price,
                status: if rec.status == "STUCK" { "EXITING".into() } else { rec.status.clone() },
                buy_order_id: None,
                buy_size: (self.cfg.brownfox_trade_size_shares).max(MIN_SHARES),
                buy_done: false,
                bought: 0.0,
                peak_holdings: 0.0,
                exit_attempts: 0,
            };
            // Re-discover live state from the exchange.
            let (buy_oid, buy_matched) = self.find_order(&pos.token_id, "BUY", pos.buy_price).await;
            let resting = self.resting_sell(&pos.token_id, pos.sell_price).await;
            let holdings = self.exec.token_holdings(&pos.token_id).await;
            if let Some(oid) = buy_oid {
                pos.buy_order_id = Some(oid);
                pos.bought = buy_matched.max(holdings + resting);
            } else {
                pos.buy_done = true;
                pos.bought = holdings + resting;
            }
            pos.peak_holdings = holdings; // owned only; resting sells aren't a drop
            st.positions.insert(market, pos);
        }
        info!("🦊 brownfox: loaded {} entered, resumed {} positions", st.entered.len(), st.positions.len());
        st
    }

    // ── events ───────────────────────────────────────────────────────────────

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
            info!("🦊 brownfox: already took our one buy in {} - ignoring", short(&market));
            return;
        }
        st.entered.insert(market.clone());

        let tick = self.exec.tick_size(&t.token_id).await;
        let buy_price = snap(t.price, tick);
        let max_price = round10(1.0 - tick);
        if buy_price <= 0.0 || buy_price >= 1.0 {
            st.entered.remove(&market);
            return;
        }
        let markup = self.cfg.brownfox_sell_markup.max(tick);
        let mut sell_price = snap(buy_price + markup, tick);
        if sell_price <= buy_price + 1e-9 {
            sell_price = round10(buy_price + tick);
        }
        if sell_price > max_price + 1e-9 {
            info!("🦊 brownfox: buy @ {:.4} too high to place +markup sell - skipping {}", buy_price, short(&market));
            st.entered.remove(&market);
            return;
        }
        let size = (self.cfg.brownfox_trade_size_shares).max(MIN_SHARES);
        let neg_risk = self.exec.token_neg_risk(&t.token_id).await;

        // Persist the reservation + state BEFORE placing (no crash double-buy).
        self.store.record_brownfox(
            &market,
            BrownfoxRecord { token_id: t.token_id.clone(), buy_price, sell_price, status: "ACTIVE".into() },
        );
        let mut pos = Pos {
            market_id: market.clone(),
            token_id: t.token_id.clone(),
            neg_risk,
            tick,
            buy_price,
            sell_price,
            status: "ACTIVE".into(),
            buy_order_id: None,
            buy_size: size,
            buy_done: false,
            bought: 0.0,
            peak_holdings: 0.0,
            exit_attempts: 0,
        };
        info!("🦊 brownfox: target bought {} @ {:.4} -> buy {} sh @ {:.4} (sell @ {:.4})", short(&market), t.price, size, buy_price, sell_price);
        match self.exec.place_gtc(&pos.token_id, neg_risk, SIDE_BUY, buy_price, size, tick).await {
            Ok(oid) => {
                pos.buy_order_id = Some(oid);
                st.positions.insert(market, pos);
            }
            Err(e) => {
                // None / rejection: look for a live resting buy before giving up.
                let (found, _) = self.find_order(&pos.token_id, "BUY", buy_price).await;
                if let Some(oid) = found {
                    pos.buy_order_id = Some(oid);
                    warn!("🦊 brownfox: place errored ({e}) but found resting buy - adopting");
                    st.positions.insert(market, pos);
                } else {
                    warn!("🦊 brownfox: buy placement failed ({e}) - releasing market");
                    self.store.delete_brownfox(&market);
                    st.entered.remove(&market);
                }
            }
        }
    }

    async fn on_sell(&self, st: &mut State, t: TargetTrade) {
        let market = t.market_id.clone();
        let mut pos = match st.positions.get(&market).cloned() {
            Some(p) => p,
            None => return,
        };
        if matches!(pos.status.as_str(), "DONE" | "ABORTED" | "STUCK" | "EXITING") {
            return;
        }
        self.refresh_bought(&mut pos).await;
        let holdings = self.exec.token_holdings(&pos.token_id).await;

        if t.price >= pos.sell_price - 1e-9 {
            info!("🦊 brownfox: target sold @ {:.4} >= our sell {:.4} - resting sell handles it", t.price, pos.sell_price);
            self.write(st, pos);
            return;
        }
        // Below our +mark.
        if holdings < EPS && pos.bought < EPS {
            if self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &pos.token_id).await {
                pos.buy_done = true;
                if self.exec.token_holdings(&pos.token_id).await < EPS {
                    self.set_status(st, &mut pos, "ABORTED");
                    info!("🦊 brownfox: target exited {} before our buy filled - cancelled", short(&market));
                    self.write(st, pos);
                    return;
                }
            }
        }
        info!("🦊 brownfox: target sold @ {:.4} below +mark {:.4} - forcing exit", t.price, pos.sell_price);
        pos.status = "EXITING".into();
        self.store.update_brownfox_status(&market, "EXITING");
        self.forced_exit(&mut pos).await;
        self.write(st, pos);
    }

    // ── reconcile ──────────────────────────────────────────────────────────────

    async fn reconcile(&self, st: &mut State) {
        let markets: Vec<String> = st.positions.keys().cloned().collect();
        for market in markets {
            let mut pos = match st.positions.get(&market).cloned() {
                Some(p) => p,
                None => continue,
            };
            if matches!(pos.status.as_str(), "DONE" | "ABORTED" | "STUCK") {
                continue;
            }
            if pos.status == "EXITING" {
                self.forced_exit(&mut pos).await;
            } else {
                self.manage(st, &mut pos).await;
            }
            self.write(st, pos);
        }
    }

    async fn manage(&self, st: &mut State, pos: &mut Pos) {
        let token = pos.token_id.clone();
        let holdings = self.exec.token_holdings(&token).await;
        pos.peak_holdings = pos.peak_holdings.max(holdings);
        self.refresh_bought(pos).await;

        if pos.buy_size > 0.0 && pos.bought >= pos.buy_size - EPS {
            pos.buy_done = true;
        }

        // Target-exit detection (independent of the sell event).
        if !self.target_holds(st, &token).await {
            if !pos.buy_done && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
                pos.buy_done = true;
            }
            if pos.buy_done {
                let (bid, _) = self.exec.book(&token).await;
                if holdings < EPS {
                    self.set_status(st, pos, "ABORTED");
                    info!("🦊 brownfox: target left {} before our buy filled - cancelled", short(&pos.market_id));
                    return;
                }
                if holdings < MIN_SHARES {
                    self.cancel_all_sells(pos).await;
                    self.set_status(st, pos, "DONE");
                    return;
                }
                if bid < pos.sell_price - 1e-9 {
                    info!("🦊 brownfox: target left {} & market below +mark - forcing exit", short(&pos.market_id));
                    pos.status = "EXITING".into();
                    self.store.update_brownfox_status(&pos.market_id, "EXITING");
                    self.forced_exit(pos).await;
                    return;
                }
                // else market at/above +mark -> let the resting sell ride
            }
        }

        // Cancel the rest of the buy once selling has started.
        if pos.buy_order_id.is_some() && !pos.buy_done {
            let (bid, _) = self.exec.book(&token).await;
            let started = (pos.peak_holdings - holdings > EPS) || (bid >= pos.sell_price - 1e-9);
            if started && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
                pos.buy_done = true;
                info!("🦊 brownfox: selling started - cancelled remaining buy for {}", short(&pos.market_id));
            }
        }

        // Cover all held shares with resting sells (capped by holdings; the
        // exchange rejects any over-balance sell, so this can't over-sell).
        let resting = self.resting_sell(&token, pos.sell_price).await;
        let uncovered = round2(holdings - resting);
        if uncovered >= MIN_SHARES {
            match self.exec.place_gtc(&token, pos.neg_risk, SIDE_SELL, pos.sell_price, uncovered, pos.tick).await {
                Ok(_) => info!("🦊 brownfox: resting sell {:.2} @ {:.4} for {} (held {:.2})", uncovered, pos.sell_price, short(&pos.market_id), holdings),
                Err(e) => warn!("🦊 brownfox: sell place failed for {}: {e}", short(&pos.market_id)),
            }
        }

        if pos.buy_done && holdings < EPS && resting < EPS {
            self.set_status(st, pos, "DONE");
            info!("🦊 brownfox: {} fully closed", short(&pos.market_id));
        } else if pos.buy_done && holdings < MIN_SHARES && resting < EPS && pos.bought > EPS {
            self.set_status(st, pos, "DONE");
            info!("🦊 brownfox: {} residual dust {:.2} - closing", short(&pos.market_id), holdings);
        }
    }

    // ── forced exit ────────────────────────────────────────────────────────────

    async fn forced_exit(&self, pos: &mut Pos) {
        pos.exit_attempts += 1;
        let token = pos.token_id.clone();
        let sells_gone = self.cancel_all_sells(pos).await;
        if !pos.buy_done && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
            pos.buy_done = true;
        }
        if !sells_gone || !pos.buy_done {
            warn!("🦊 brownfox: {} orders not all confirmed cancelled - retry next cycle", short(&pos.market_id));
            if pos.exit_attempts >= 12 {
                pos.status = "STUCK".into();
                self.store.update_brownfox_status(&pos.market_id, "STUCK");
            }
            return;
        }
        let holdings = self.exec.token_holdings(&token).await;
        if holdings < MIN_SHARES {
            pos.status = "DONE".into();
            self.store.update_brownfox_status(&pos.market_id, "DONE");
            return;
        }
        let left = self.market_sell_all(pos, holdings).await;
        if left < MIN_SHARES {
            pos.status = "DONE".into();
            self.store.update_brownfox_status(&pos.market_id, "DONE");
            info!("🦊 brownfox: forced exit complete for {}", short(&pos.market_id));
        } else if pos.exit_attempts >= 12 {
            pos.status = "STUCK".into();
            self.store.update_brownfox_status(&pos.market_id, "STUCK");
            warn!("⛔ brownfox: could NOT exit {:.2} sh in {} - manual action", left, short(&pos.market_id));
        } else {
            warn!("🦊 brownfox: forced exit left {:.2} sh in {} - retry", left, short(&pos.market_id));
        }
    }

    /// Sell everything into the book, retrying/walking down. Local-remaining
    /// tracking avoids the Data-API-lag double-sell. Returns shares left.
    async fn market_sell_all(&self, pos: &Pos, known: f64) -> f64 {
        let token = pos.token_id.clone();
        let mut remaining = if known > 0.0 { known } else { self.exec.token_holdings(&token).await };
        for _ in 0..self.cfg.brownfox_market_sell_retries {
            if remaining < EPS {
                break;
            }
            if remaining < MIN_SHARES {
                break; // sub-min dust, unsellable
            }
            let (bid, _) = self.exec.book(&token).await;
            if bid <= 0.0 {
                sleep(Duration::from_millis(800)).await;
                continue;
            }
            let oid = match self.exec.market_sell(&token, pos.neg_risk, remaining, bid, pos.tick).await {
                Ok(o) => o,
                Err(e) => {
                    let msg = e.to_string().to_lowercase();
                    if msg.contains("balance") || msg.contains("allowance") {
                        remaining = self.exec.token_holdings(&token).await;
                    } else if msg.contains("minimum") || msg.contains("too small") {
                        break;
                    }
                    sleep(Duration::from_millis(400)).await;
                    continue;
                }
            };
            sleep(Duration::from_millis(1000)).await;
            let matched = self.exec.order_matched(&oid).await.unwrap_or(0.0);
            // Always cancel the remainder + confirm before the next sweep.
            if !self.exec.cancel_confirmed(&oid, &token).await {
                remaining = self.exec.token_holdings(&token).await;
                continue;
            }
            remaining = (remaining - matched).max(0.0);
        }
        self.exec.token_holdings(&token).await
    }

    // ── helpers ────────────────────────────────────────────────────────────────

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

    async fn resting_sell(&self, token: &str, price: f64) -> f64 {
        self.exec
            .open_orders(token)
            .await
            .into_iter()
            .filter(|(_, s, p, ..)| s == "SELL" && (*p - price).abs() < 1e-9)
            .map(|(_, _, _, orig, matched)| (orig - matched).max(0.0))
            .sum()
    }

    async fn cancel_all_sells(&self, pos: &Pos) -> bool {
        let mut all = true;
        for (id, s, p, ..) in self.exec.open_orders(&pos.token_id).await {
            if s == "SELL" && (p - pos.sell_price).abs() < 1e-9 && !self.exec.cancel_confirmed(&id, &pos.token_id).await {
                all = false;
            }
        }
        all
    }

    async fn target_holds(&self, st: &mut State, token: &str) -> bool {
        if self.cfg.target_trader.is_empty() {
            return true;
        }
        let stale = st.target_at.map(|t| t.elapsed() > Duration::from_secs(15)).unwrap_or(true);
        if st.target_tokens.is_none() || stale {
            if let Some(set) = self.exec.token_holdings_set(&self.cfg.target_trader).await {
                st.target_tokens = Some(set);
                st.target_at = Some(Instant::now());
            }
        }
        match &st.target_tokens {
            Some(s) => s.contains(token),
            None => true,
        }
    }

    fn set_status(&self, _st: &mut State, pos: &mut Pos, status: &str) {
        pos.status = status.to_string();
        self.store.update_brownfox_status(&pos.market_id, status);
    }

    fn write(&self, st: &mut State, pos: Pos) {
        if matches!(pos.status.as_str(), "DONE" | "ABORTED") {
            st.positions.remove(&pos.market_id);
        } else {
            st.positions.insert(pos.market_id.clone(), pos);
        }
    }
}

fn short(s: &str) -> &str {
    &s[..s.len().min(10)]
}
fn round10(x: f64) -> f64 {
    (x * 1e10).round() / 1e10
}
fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}
fn snap(price: f64, tick: f64) -> f64 {
    crate::clob::signing::snap_price(price, tick)
}
