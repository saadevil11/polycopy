//! "99c scanner" — a SELF-DRIVEN (no target trader) sibling of `ninetynine`. It
//! discovers all open `<asset>-updown-5m` markets from the Gamma API, subscribes to
//! their order books on the CLOB **market** WebSocket
//! (`wss://ws-subscriptions-clob.polymarket.com/ws/market`, public), and when a
//! token's best ASK reaches `SCANNER_TRIGGER_ASK` (0.99) and is `< 1.0`, places ONE
//! GTC buy of `SCANNER_TRADE_SIZE_SHARES` at `SCANNER_BUY_PRICE` (0.99) and HOLDS to
//! resolution — buy-and-hold like 99c, but driven by live book updates, not a copy.
//!
//! Concurrency/safety: each token is ATOMICALLY claimed in the store
//! (`claim_scan`, the single linearization point) before any placement, so a burst
//! of book updates can never double-buy; placements run on a detached,
//! semaphore-bounded worker so the WS read loop never blocks. Fills come from CLOB
//! order data (`order_matched`), NEVER the laggy `/positions`. Restart-safe: the
//! store dedups, and `reconcile` resumes fill-tracking / stale-cancels.
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::Semaphore;
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::Message;
use tracing::{info, warn};

use crate::clob::executor::Executor;
use crate::clob::signing::{snap_price, SIDE_BUY};
use crate::config::Config;
use crate::copytrader::liquidity::Liquidity;
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};

const MIN_SHARES: f64 = 5.0;
const EPS: f64 = 0.01;
const GAMMA: &str = "https://gamma-api.polymarket.com";
const DEFAULT_ASSETS: [&str; 7] = ["btc", "eth", "sol", "xrp", "bnb", "doge", "hype"];

#[derive(Clone)]
struct MarketInfo {
    asset: String, // btc / eth / … (from the slug) — keys the liquidity filter
    title: String,
    outcome: String, // Up / Down
    tick: f64,
}

pub struct Scanner {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    liq: Arc<Liquidity>,
    /// token_id -> info for currently-live markets (replaced by the discovery loop).
    markets: Mutex<HashMap<String, MarketInfo>>,
    /// bumped whenever the live token SET changes, so the WS reconnects with it.
    generation: AtomicU64,
    /// tokens we've already logged a liquidity-skip for (avoids per-update log spam).
    skip_logged: Mutex<HashSet<String>>,
    sem: Arc<Semaphore>,
    http: reqwest::Client,
}

impl Scanner {
    pub fn new(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, liq: Arc<Liquidity>) -> Self {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .user_agent("polybotshadow/0.1")
            .build()
            .expect("http client");
        let permits = cfg.ninetynine_max_concurrent_buys.max(1);
        Scanner {
            cfg,
            exec,
            store,
            liq,
            markets: Mutex::new(HashMap::new()),
            generation: AtomicU64::new(0),
            skip_logged: Mutex::new(HashSet::new()),
            sem: Arc::new(Semaphore::new(permits)),
            http,
        }
    }

    fn assets(&self) -> Vec<String> {
        if self.cfg.ninetynine_assets.is_empty() {
            DEFAULT_ASSETS.iter().map(|s| s.to_string()).collect()
        } else {
            self.cfg.ninetynine_assets.clone()
        }
    }

    pub async fn run(self) {
        let st = Arc::new(self);
        info!(
            "🔭 99c-scanner: watching {} updown-5m order books; buy {} sh GTC @ {:.2} when best ask ≥ {:.2} (<1.0), hold to resolution. discovery {}s, stale-cancel {}",
            st.assets().join("/"),
            st.cfg.scanner_trade_size_shares.max(MIN_SHARES),
            st.cfg.scanner_buy_price,
            st.cfg.scanner_trigger_ask,
            st.cfg.scanner_discovery.as_secs(),
            match st.cfg.ninetynine_buy_max_age {
                Some(d) => format!("{}s", d.as_secs()),
                None => "off".into(),
            }
        );

        // Initial discovery so the first WS connect has tokens; resume persisted state.
        st.discover().await;
        st.reconcile().await;

        // Discovery loop — new 5m markets open every 5 min, old ones resolve.
        {
            let st = st.clone();
            tokio::spawn(async move {
                let mut t = interval(st.cfg.scanner_discovery);
                loop {
                    t.tick().await;
                    st.discover().await;
                }
            });
        }
        // Reconcile loop — fill-check (order data) + stale-cancel.
        {
            let st = st.clone();
            tokio::spawn(async move {
                let mut t = interval(st.cfg.ninetynine_reconcile);
                loop {
                    t.tick().await;
                    st.reconcile().await;
                }
            });
        }
        // Market WS — reconnect forever (and on token-set change).
        st.ws_loop().await;
    }

    /// Refresh the set of live `<asset>-updown-5m` markets from Gamma (deterministic
    /// by slug: the current + next 300s windows for each asset, one batched request).
    async fn discover(&self) {
        let now = chrono::Utc::now().timestamp().max(0) as u64;
        let cur = ((now / 300) + 1) * 300; // window the accepting market ends at
        let windows = [cur, cur + 300];
        let mut query: Vec<(&str, String)> = Vec::new();
        for a in self.assets() {
            for w in windows {
                query.push(("slug", format!("{a}-updown-5m-{w}")));
            }
        }
        let v: Value = match self.http.get(format!("{GAMMA}/markets")).query(&query).send().await {
            Ok(r) => r.json().await.unwrap_or(Value::Null),
            Err(e) => {
                warn!("🔭 99c-scanner: gamma discovery failed: {e}");
                return;
            }
        };
        let arr = v.as_array().cloned().unwrap_or_default();
        let mut next: HashMap<String, MarketInfo> = HashMap::new();
        for m in &arr {
            let live = m.get("active").and_then(|x| x.as_bool()).unwrap_or(false)
                && !m.get("closed").and_then(|x| x.as_bool()).unwrap_or(true)
                && m.get("acceptingOrders").and_then(|x| x.as_bool()).unwrap_or(false)
                && m.get("enableOrderBook").and_then(|x| x.as_bool()).unwrap_or(false);
            if !live {
                continue;
            }
            let slug = m.get("slug").and_then(|x| x.as_str()).unwrap_or("");
            let asset = slug.split('-').next().unwrap_or("").to_string(); // "btc" from "btc-updown-5m-…"
            let title = m.get("question").and_then(|x| x.as_str()).unwrap_or(slug).to_string();
            let tick = m.get("orderPriceMinTickSize").and_then(num_f64).unwrap_or(0.01);
            let toks = str_array(m.get("clobTokenIds")); // ["UpTok","DownTok"]
            let outs = str_array(m.get("outcomes")); // ["Up","Down"]
            for (i, tok) in toks.iter().enumerate() {
                if tok.is_empty() {
                    continue;
                }
                next.insert(
                    tok.clone(),
                    MarketInfo {
                        asset: asset.clone(),
                        title: title.clone(),
                        outcome: outs.get(i).cloned().unwrap_or_default(),
                        tick,
                    },
                );
            }
        }
        if next.is_empty() {
            return; // transient empty fetch — keep the current set
        }
        let new_set: HashSet<String> = next.keys().cloned().collect();
        let old_set: HashSet<String> = {
            let mut guard = self.markets.lock().unwrap();
            let old: HashSet<String> = guard.keys().cloned().collect();
            *guard = next;
            old
        };
        if new_set != old_set {
            self.generation.fetch_add(1, Ordering::SeqCst);
            info!("🔭 99c-scanner: watching {} order-book tokens across the live 5m markets", new_set.len());
        }
    }

    async fn ws_loop(&self) {
        let mut backoff = 2u64;
        loop {
            let tokens: Vec<String> = self.markets.lock().unwrap().keys().cloned().collect();
            if tokens.is_empty() {
                sleep(Duration::from_secs(2)).await;
                continue;
            }
            match self.ws_session(&tokens).await {
                Ok(_) => backoff = 2,
                Err(e) => {
                    warn!("🔭 99c-scanner: market WS ended ({e}); reconnecting in {backoff}s");
                    sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(30);
                }
            }
        }
    }

    /// One WS connection: subscribe to `tokens`, process book/price_change events,
    /// return (to reconnect) when the live token set changes or the socket drops.
    async fn ws_session(&self, tokens: &[String]) -> anyhow::Result<()> {
        let gen0 = self.generation.load(Ordering::SeqCst);
        let mut req = self.cfg.clob_ws_url.as_str().into_client_request()?;
        req.headers_mut().insert("Origin", "https://polymarket.com".parse()?);
        req.headers_mut().insert(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36".parse()?,
        );
        let (ws, _) = tokio_tungstenite::connect_async(req).await?;
        let (mut write, mut read) = ws.split();
        // Subscribe immediately (the server may close an idle un-subscribed socket).
        let sub = json!({"assets_ids": tokens, "type": "market", "initial_dump": true});
        write.send(Message::Text(sub.to_string())).await?;
        info!("🔭 99c-scanner: market WS connected, subscribed to {} tokens", tokens.len());

        let mut ping = interval(Duration::from_secs(10));
        let mut check = interval(Duration::from_secs(3));
        loop {
            tokio::select! {
                msg = read.next() => match msg {
                    Some(Ok(Message::Text(t))) => self.handle_frame(&t).await,
                    Some(Ok(Message::Binary(b))) => {
                        if let Ok(t) = String::from_utf8(b) { self.handle_frame(&t).await; }
                    }
                    Some(Ok(Message::Ping(p))) => { let _ = write.send(Message::Pong(p)).await; }
                    Some(Ok(_)) => {}
                    Some(Err(e)) => return Err(e.into()),
                    None => return Ok(()),
                },
                _ = ping.tick() => {
                    if write.send(Message::Text("PING".into())).await.is_err() { return Ok(()); }
                }
                _ = check.tick() => {
                    if self.generation.load(Ordering::SeqCst) != gen0 {
                        return Ok(()); // token set changed — reconnect with the new list
                    }
                }
            }
        }
    }

    async fn handle_frame(&self, text: &str) {
        let t = text.trim();
        if t.is_empty() || t == "PONG" || t == "PING" {
            return;
        }
        let v: Value = match serde_json::from_str(t) {
            Ok(v) => v,
            Err(_) => return,
        };
        // A frame is either a single event object or an array of them.
        let items: Vec<Value> = match v {
            Value::Array(a) => a,
            other => vec![other],
        };
        for ev in items {
            match ev.get("event_type").and_then(|x| x.as_str()).unwrap_or("") {
                // Full snapshot: best ask = min(ask price) (order-independent).
                "book" => {
                    if let Some(tok) = ev.get("asset_id").and_then(|x| x.as_str()) {
                        let ask = min_price(ev.get("asks"));
                        if ask > 0.0 {
                            self.maybe_trigger(tok, ask).await;
                        }
                    }
                }
                // Delta: each item carries the per-asset best_ask directly (lag-free).
                "price_change" => {
                    if let Some(arr) = ev.get("price_changes").and_then(|x| x.as_array()) {
                        for pc in arr {
                            if let Some(tok) = pc.get("asset_id").and_then(|x| x.as_str()) {
                                let ask = pc.get("best_ask").and_then(num_f64).unwrap_or(0.0);
                                if ask > 0.0 {
                                    self.maybe_trigger(tok, ask).await;
                                }
                            }
                        }
                    }
                }
                _ => {} // tick_size_change / last_trade_price / etc. — not needed for the trigger
            }
        }
    }

    /// If `best_ask` is in [trigger, 1.0) and we haven't claimed this token yet,
    /// atomically claim it and place the GTC buy on a detached worker.
    async fn maybe_trigger(&self, token: &str, best_ask: f64) {
        if !(best_ask >= self.cfg.scanner_trigger_ask && best_ask < 1.0) {
            return;
        }
        if self.store.scan_claimed(token) {
            return; // already handled (fast path; claim_scan is the real guard)
        }
        let info = match self.markets.lock().unwrap().get(token).cloned() {
            Some(i) => i,
            None => return, // not a token we're tracking
        };
        // Liquidity-level avoidance: skip the 0.99 if the coin's current 5m candle has
        // touched any swing level (pooled from 5m/15m/30m) during this window (reversal
        // risk), or if we have no candle data to evaluate it.
        if !self.liq.clear_to_trade(&info.asset) {
            if self.skip_logged.lock().unwrap().insert(token.to_string()) {
                info!("🔭 99c-scanner: SKIP {} — {} 5m candle touched a liquidity level (5m/15m/30m) this window, or no candle data", label(&info.title, &info.outcome, token), info.asset.to_uppercase());
            }
            return;
        }
        let buy_price = snap_price(self.cfg.scanner_buy_price, info.tick);
        if buy_price <= 0.0 || buy_price >= 1.0 {
            return;
        }
        // ATOMIC claim — only the winner proceeds to place.
        let claimed = self.store.claim_scan(
            token,
            BrownfoxRecord {
                token_id: token.to_string(),
                buy_price,
                sell_price: 0.0,
                status: "ACTIVE".into(),
                title: info.title.clone(),
                placed_at_ms: now_ms(),
                order_id: String::new(),
            },
        );
        if !claimed {
            return;
        }
        let size = self.cfg.scanner_trade_size_shares.max(MIN_SHARES);
        info!(
            "🔭 99c-scanner: {} best ask {:.4} ≥ {:.2} → placing {:.0} sh GTC buy @ {:.4}, holding to resolution",
            label(&info.title, &info.outcome, token), best_ask, self.cfg.scanner_trigger_ask, size, buy_price
        );

        // Detached, semaphore-bounded placement so the WS read loop never blocks.
        let exec = self.exec.clone();
        let store = self.store.clone();
        let sem = self.sem.clone();
        let token = token.to_string();
        let title = info.title.clone();
        let outcome = info.outcome.clone();
        tokio::spawn(async move {
            let _permit = match sem.acquire_owned().await {
                Ok(p) => p,
                Err(_) => return,
            };
            let neg_risk = exec.token_neg_risk(&token).await;
            let tick = exec.tick_size(&token).await; // live tick (drops to 0.001 near 1.0)
            let price = snap_price(buy_price, tick);
            match exec.place_gtc(&token, neg_risk, SIDE_BUY, price, size, tick).await {
                Ok(oid) => {
                    store.set_scan_order_id(&token, &oid);
                    store.record_trade(TradeLog {
                        time: now_str(),
                        market: token.clone(),
                        title: title.clone(),
                        side: "BUY".into(),
                        size,
                        price,
                        status: "RESTING".into(),
                    });
                    info!("🔭 99c-scanner: placed {:.0} sh GTC buy @ {:.4} for {}", size, price, label(&title, &outcome, &token));
                }
                Err(e) => {
                    // Adopt a live resting buy if it actually landed; else RELEASE the
                    // claim so a later trigger in this token can retry.
                    if let Some(oid) = open_buy_order(&exec, &token, price).await {
                        store.set_scan_order_id(&token, &oid);
                        warn!("🔭 99c-scanner: place errored ({e}) but found resting buy in {} - keeping", label(&title, &outcome, &token));
                    } else {
                        warn!("🔭 99c-scanner: buy placement failed for {} ({e}) - releasing", label(&title, &outcome, &token));
                        store.delete_scan(&token);
                    }
                }
            }
        });
    }

    /// Store-driven: mark FILLED (from order data), stale-cancel unfilled buys, and
    /// prune old terminal records so scanner.json stays bounded.
    async fn reconcile(&self) {
        // Resolved 5m markets never recur; drop terminal records older than 2h.
        self.store.prune_scan(now_ms().saturating_sub(2 * 60 * 60 * 1000));
        let size = self.cfg.scanner_trade_size_shares.max(MIN_SHARES);
        for (token, rec) in self.store.active_scan() {
            if rec.order_id.is_empty() {
                // Claimed but the placement worker hasn't recorded an order id yet (in
                // flight) — or it failed and released. Clean up a stuck claim if old.
                let age_stale = self
                    .cfg
                    .ninetynine_buy_max_age
                    .map(|m| now_ms().saturating_sub(rec.placed_at_ms) as u128 > m.as_millis())
                    .unwrap_or(false);
                if age_stale {
                    self.store.delete_scan(&token);
                }
                continue;
            }
            let matched = self.exec.order_matched(&rec.order_id).await.unwrap_or(0.0);
            if matched >= size - EPS {
                self.store.update_scan_status(&token, "FILLED");
                info!("🔭 99c-scanner: filled {:.2} sh in {} — holding to resolution ✅", matched, label(&rec.title, "", &token));
                continue;
            }
            let stale = self
                .cfg
                .ninetynine_buy_max_age
                .map(|m| now_ms().saturating_sub(rec.placed_at_ms) as u128 > m.as_millis())
                .unwrap_or(false);
            if stale {
                let _ = self.exec.cancel_confirmed(&rec.order_id, &token).await;
                let filled = self.exec.order_matched(&rec.order_id).await.unwrap_or(0.0);
                if filled > EPS {
                    self.store.update_scan_status(&token, "FILLED");
                    info!("🔭 99c-scanner: {} filled {:.2}/{:.0} sh before timeout — cancelled the rest, holding", label(&rec.title, "", &token), filled, size);
                } else {
                    self.store.update_scan_status(&token, "CANCELLED");
                    info!("🔭 99c-scanner: {} buy unfilled — cancelled to free capital", label(&rec.title, "", &token));
                }
            }
        }
    }
}

async fn open_buy_order(exec: &Arc<Executor>, token: &str, price: f64) -> Option<String> {
    for (id, s, p, _orig, _matched) in exec.open_orders(token).await {
        if s == "BUY" && (p - price).abs() < 1e-9 {
            return Some(id);
        }
    }
    None
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

/// Best ask from a WS `book` event's `asks` array = min price (order-independent).
fn min_price(v: Option<&Value>) -> f64 {
    v.and_then(|x| x.as_array())
        .map(|a| a.iter().filter_map(|l| l.get("price").and_then(num_f64)).fold(f64::MAX, f64::min))
        .filter(|x| *x != f64::MAX)
        .unwrap_or(0.0)
}

/// Polymarket sends numbers as JSON strings; accept string or number.
fn num_f64(v: &Value) -> Option<f64> {
    match v {
        Value::String(s) => s.parse().ok(),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Gamma's `clobTokenIds`/`outcomes` are JSON-ENCODED STRINGS of arrays — parse the
/// string into a Vec. Tolerate an already-decoded array too.
fn str_array(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::String(s)) => serde_json::from_str::<Vec<String>>(s).unwrap_or_default(),
        Some(Value::Array(a)) => a.iter().filter_map(|x| x.as_str().map(String::from)).collect(),
        _ => Vec::new(),
    }
}

fn label(title: &str, outcome: &str, token: &str) -> String {
    let id = short(token);
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
