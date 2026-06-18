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
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};
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
    /// Latched true once we've positively seen the target hold this token (via
    /// the Data API). Until then, an absence is treated as positions-API lag —
    /// NOT an exit — so the lag window right after entry can't false-abort us.
    target_confirmed_held: bool,
    title: String,   // human-readable market name (for logging)
    outcome: String, // outcome we're trading, e.g. "Yes" / "No"
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
                target_confirmed_held: false,
                title: rec.title.clone(),
                outcome: String::new(),
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
            // We already hold/filled here, so we've demonstrably been in this
            // market — seed the confirmed-held latch so a resumed position can
            // exit on a real target departure without re-confirming first.
            pos.target_confirmed_held = pos.bought > EPS || holdings > EPS || resting > EPS;
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
            info!("🦊 brownfox: already hold our one position in {} — ignoring repeat target buy", label(&t.title, &t.outcome, &market));
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
            info!("🦊 brownfox: skipping {} — buy @ {:.4} too high to place a +markup sell", label(&t.title, &t.outcome, &market), buy_price);
            st.entered.remove(&market);
            return;
        }
        let size = (self.cfg.brownfox_trade_size_shares).max(MIN_SHARES);
        let neg_risk = self.exec.token_neg_risk(&t.token_id).await;

        // Persist the reservation + state BEFORE placing (no crash double-buy).
        self.store.record_brownfox(
            &market,
            BrownfoxRecord {
                token_id: t.token_id.clone(),
                buy_price,
                sell_price,
                status: "ACTIVE".into(),
                title: t.title.clone(),
                placed_at_ms: 0, // brownfox manages by reconcile/holdings, not a placement timer
            },
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
            target_confirmed_held: false,
            title: t.title.clone(),
            outcome: t.outcome.clone(),
        };
        info!("🦊 brownfox: target BOUGHT {} @ {:.4} ({:.0} sh) → placing {} sh buy @ {:.4}, resting sell @ {:.4}", label(&t.title, &t.outcome, &market), t.price, t.size, size, buy_price, sell_price);
        match self.exec.place_gtc(&pos.token_id, neg_risk, SIDE_BUY, buy_price, size, tick).await {
            Ok(oid) => {
                pos.buy_order_id = Some(oid);
                self.store.record_trade(TradeLog {
                    time: chrono::Utc::now().format("%Y-%m-%d %H:%M:%S").to_string(),
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
                // None / rejection: look for a live resting buy before giving up.
                let (found, _) = self.find_order(&pos.token_id, "BUY", buy_price).await;
                if let Some(oid) = found {
                    pos.buy_order_id = Some(oid);
                    warn!("🦊 brownfox: place errored ({e}) but found resting buy in {} - adopting", label(&t.title, &t.outcome, &market));
                    st.positions.insert(market, pos);
                } else {
                    warn!("🦊 brownfox: buy placement failed in {} ({e}) - releasing market", label(&t.title, &t.outcome, &market));
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
            info!("🦊 brownfox: target SOLD {} @ {:.4} (≥ our sell {:.4}) — resting sell handles it", label(&t.title, &t.outcome, &market), t.price, pos.sell_price);
            self.write(st, pos);
            return;
        }
        // Below our +mark.
        if holdings < EPS && pos.bought < EPS {
            if self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &pos.token_id).await {
                pos.buy_done = true;
                if self.exec.token_holdings(&pos.token_id).await < EPS {
                    self.set_status(st, &mut pos, "ABORTED");
                    info!("🦊 brownfox: target EXITED {} before our buy filled — cancelled", label(&t.title, &t.outcome, &market));
                    self.write(st, pos);
                    return;
                }
            }
        }
        info!("🦊 brownfox: target SOLD {} @ {:.4} (below our +mark {:.4}) — forcing exit", label(&t.title, &t.outcome, &market), t.price, pos.sell_price);
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

        // Target-exit detection — a BACKSTOP for a missed/dropped WS sell event,
        // NOT the primary exit (on_sell is). We must FIRST positively confirm the
        // target held this token; otherwise the Data-API /positions lag right
        // after entry would false-trigger an exit before our buy even fills.
        let held = self.target_holds(st, &token).await;
        if held {
            pos.target_confirmed_held = true; // latch: positively observed the hold
        } else if pos.target_confirmed_held {
            if !pos.buy_done && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
                pos.buy_done = true;
            }
            if pos.buy_done {
                let (bid, _) = self.exec.book(&token).await;
                // Only treat a low /positions read as "nothing held" if we also
                // never recorded a fill — otherwise it's API lag and abandoning
                // would strand a real position (the forced-exit money bug).
                let had_fill = pos.bought > EPS || pos.peak_holdings > EPS;
                if holdings < EPS && !had_fill {
                    self.set_status(st, pos, "ABORTED");
                    info!("🦊 brownfox: target left {} before our buy filled — cancelled", label(&pos.title, &pos.outcome, &pos.market_id));
                    return;
                }
                if holdings < MIN_SHARES && !had_fill {
                    self.cancel_all_sells(pos).await;
                    self.set_status(st, pos, "DONE");
                    info!("🦊 brownfox: target left {} — no position held, closed", label(&pos.title, &pos.outcome, &pos.market_id));
                    return;
                }
                // We hold (or our buy filled per the order record): exit if the
                // market is below our +mark, else let the resting sell ride.
                if bid < pos.sell_price - 1e-9 {
                    info!("🦊 brownfox: target left {} & market below +mark — forcing exit", label(&pos.title, &pos.outcome, &pos.market_id));
                    pos.status = "EXITING".into();
                    self.store.update_brownfox_status(&pos.market_id, "EXITING");
                    self.forced_exit(pos).await;
                    return;
                }
                // else market at/above +mark -> let the resting sell ride
            }
        }
        // (!held && !target_confirmed_held = the post-entry positions-lag window:
        //  do nothing here; the buy rests and the resting-sell logic below still
        //  runs. on_sell remains the instant exit if the target really sells.)

        // Cancel the rest of the buy once selling has started.
        if pos.buy_order_id.is_some() && !pos.buy_done {
            let (bid, _) = self.exec.book(&token).await;
            let started = (pos.peak_holdings - holdings > EPS) || (bid >= pos.sell_price - 1e-9);
            if started && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
                pos.buy_done = true;
                info!("🦊 brownfox: selling started — cancelled remaining buy for {}", label(&pos.title, &pos.outcome, &pos.market_id));
            }
        }

        // Cover all held shares with resting sells (capped by holdings; the
        // exchange rejects any over-balance sell, so this can't over-sell).
        let resting = self.resting_sell(&token, pos.sell_price).await;
        let uncovered = round2(holdings - resting);
        if uncovered >= MIN_SHARES {
            match self.exec.place_gtc(&token, pos.neg_risk, SIDE_SELL, pos.sell_price, uncovered, pos.tick).await {
                Ok(_) => info!("🦊 brownfox: resting sell {:.2} sh @ {:.4} for {} (holding {:.2})", uncovered, pos.sell_price, label(&pos.title, &pos.outcome, &pos.market_id), holdings),
                Err(e) => warn!("🦊 brownfox: sell place failed for {}: {e}", label(&pos.title, &pos.outcome, &pos.market_id)),
            }
        }

        if pos.buy_done && holdings < EPS && resting < EPS {
            self.set_status(st, pos, "DONE");
            info!("🦊 brownfox: {} fully closed ✅", label(&pos.title, &pos.outcome, &pos.market_id));
        } else if pos.buy_done && holdings < MIN_SHARES && resting < EPS && pos.bought > EPS {
            self.set_status(st, pos, "DONE");
            info!("🦊 brownfox: {} residual dust {:.2} sh — closing", label(&pos.title, &pos.outcome, &pos.market_id), holdings);
        }
    }

    // ── forced exit ────────────────────────────────────────────────────────────

    async fn forced_exit(&self, pos: &mut Pos) {
        pos.exit_attempts += 1;
        let token = pos.token_id.clone();
        let lbl = label(&pos.title, &pos.outcome, &pos.market_id);

        // Cancel our resting sell(s) + any unfilled buy first.
        let sells_gone = self.cancel_all_sells(pos).await;
        if !pos.buy_done && self.exec.cancel_confirmed(pos.buy_order_id.as_deref().unwrap_or(""), &token).await {
            pos.buy_done = true;
        }
        if !sells_gone || !pos.buy_done {
            warn!("🦊 brownfox: {} orders not all confirmed cancelled — retry next cycle (attempt {})", lbl, pos.exit_attempts);
            if pos.exit_attempts >= 12 {
                pos.status = "STUCK".into();
                self.store.update_brownfox_status(&pos.market_id, "STUCK");
                warn!("⛔ brownfox: {} could not cancel orders after {} attempts — MANUAL ACTION NEEDED", lbl, pos.exit_attempts);
            }
            return;
        }

        // How much we ACTUALLY bought — from the order's matched size (authoritative,
        // does NOT lag like the Data-API /positions read). on_sell refreshes this
        // before calling us; refresh again since reconcile also calls forced_exit.
        self.refresh_bought(pos).await;
        let had_fill = pos.bought > EPS || pos.peak_holdings > EPS;

        let mut holdings = self.exec.token_holdings(&token).await;
        if holdings < MIN_SHARES && had_fill {
            // We KNOW our buy filled, but /positions reads ~0 — almost certainly
            // Data-API lag right after the fill. Re-read before trusting it, so we
            // never abandon a position we actually hold (the bug that lost money).
            warn!("🦊 brownfox: {} positions read {:.2} sh but our buy filled {:.2} sh — likely API lag, re-checking", lbl, holdings, pos.bought);
            sleep(Duration::from_millis(1500)).await;
            holdings = self.exec.token_holdings(&token).await;
        }
        if holdings < MIN_SHARES && !had_fill {
            // Genuinely never held anything to sell — clean finish (now LOGGED).
            pos.status = "DONE".into();
            self.store.update_brownfox_status(&pos.market_id, "DONE");
            info!("🦊 brownfox: {} forced exit — buy never filled, nothing to sell, closed", lbl);
            return;
        }

        // Sweep the position. Sell the LARGER of the live read and our known fill,
        // so a lagging /positions value can't shrink the sell below what we hold
        // (the exchange caps at real balance, so this cannot over-sell).
        let target = holdings.max(pos.bought);
        info!("🦊 brownfox: {} forcing exit — selling up to {:.2} sh (positions {:.2}, filled {:.2})", lbl, target, holdings, pos.bought);
        let left = self.market_sell_all(pos, target).await;
        if left < MIN_SHARES {
            pos.status = "DONE".into();
            self.store.update_brownfox_status(&pos.market_id, "DONE");
            info!("🦊 brownfox: forced exit complete for {} ✅ ({:.2} sh left)", lbl, left);
        } else if pos.exit_attempts >= 12 {
            pos.status = "STUCK".into();
            self.store.update_brownfox_status(&pos.market_id, "STUCK");
            warn!("⛔ brownfox: could NOT exit {:.2} sh in {} after {} attempts — MANUAL ACTION NEEDED", left, lbl, pos.exit_attempts);
        } else {
            warn!("🦊 brownfox: forced exit left {:.2} sh in {} — retry next cycle", left, lbl);
        }
    }

    /// Sell everything into the book, retrying/walking down. Local-remaining
    /// tracking avoids the Data-API-lag double-sell. Returns shares left.
    async fn market_sell_all(&self, pos: &Pos, known: f64) -> f64 {
        let token = pos.token_id.clone();
        let lbl = label(&pos.title, &pos.outcome, &pos.market_id);
        let retries = self.cfg.brownfox_market_sell_retries;
        let mut remaining = if known > 0.0 { known } else { self.exec.token_holdings(&token).await };
        for attempt in 1..=retries {
            if remaining < EPS || remaining < MIN_SHARES {
                break; // done, or sub-min dust (unsellable)
            }
            let (bid, _) = self.exec.book(&token).await;
            if bid <= 0.0 {
                warn!("🦊 brownfox: {} no bid to sell into — waiting (sweep {}/{})", lbl, attempt, retries);
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
                        info!("🦊 brownfox: {} remainder below min size — stopping sweep", lbl);
                        break;
                    }
                    warn!("🦊 brownfox: {} sweep {}/{} sell errored ({e}) — retry", lbl, attempt, retries);
                    sleep(Duration::from_millis(400)).await;
                    continue;
                }
            };
            sleep(Duration::from_millis(1000)).await;
            let matched = self.exec.order_matched(&oid).await.unwrap_or(0.0);
            // Always cancel the remainder + confirm before the next sweep. Track
            // `remaining` locally (NOT via a fresh /positions read) to avoid the
            // Data-API-lag double-sell.
            if !self.exec.cancel_confirmed(&oid, &token).await {
                remaining = self.exec.token_holdings(&token).await;
                continue;
            }
            remaining = (remaining - matched).max(0.0);
            info!("🦊 brownfox: {} sweep {}/{}: sold {:.2} sh near bid {:.4} ({:.2} sh to go)", lbl, attempt, retries, matched, bid, remaining);
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
/// Human-readable market label for logs: `"<title> · <outcome> [0xabc123…]"`,
/// falling back to just the short conditionId when the title is unknown (e.g. a
/// position resumed from an older state file that didn't persist the title).
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
fn round10(x: f64) -> f64 {
    (x * 1e10).round() / 1e10
}
fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}
fn snap(price: f64, tick: f64) -> f64 {
    crate::clob::signing::snap_price(price, tick)
}
