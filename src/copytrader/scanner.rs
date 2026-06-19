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
use crate::clob::signing::{snap_price, SIDE_BUY, SIDE_SELL};
use crate::config::Config;
use crate::copytrader::doji::Doji;
use crate::copytrader::fvg::Fvg;
use crate::copytrader::FilterSignal;
use crate::copytrader::liquidity::Liquidity;
use crate::copytrader::store::{BrownfoxRecord, Store, TradeLog};

const MIN_SHARES: f64 = 5.0;
const EPS: f64 = 0.01;
const GAMMA: &str = "https://gamma-api.polymarket.com";
const DEFAULT_ASSETS: [&str; 7] = ["btc", "eth", "sol", "xrp", "bnb", "doge", "hype"];
/// Binary-book invariant (demogui `_prices_sane`): the two outcomes' best asks sum to ≈1
/// plus spread. Over this = a stale / one-sided / incoherent book → don't trade it.
const SANE_ASK_SUM_MAX: f64 = 1.10;

#[derive(Clone)]
pub struct MarketInfo {
    pub asset: String, // btc / eth / … (from the slug) — keys the liquidity filter
    pub title: String,
    pub outcome: String, // Up / Down
    pub tick: f64,
    pub market: String, // the slug — groups the Up/Down pair (one 5m market)
    pub end_ts: u64,     // window end unix ts (slug ts + 300) — for display
    pub sister: String,  // the OTHER outcome's token id — for book-sanity (Up_ask+Down_ask)
}

/// Live scanner state shared with the dashboard: the currently-watched markets (by token)
/// and the latest best ask per token, both pushed straight off the order-book WebSocket.
#[derive(Default)]
pub struct ScannerView {
    pub markets: Mutex<HashMap<String, MarketInfo>>,
    pub asks: Mutex<HashMap<String, f64>>,
}

impl ScannerView {
    pub fn new() -> Arc<Self> {
        Arc::new(ScannerView::default())
    }
}

pub struct Scanner {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    liq: Arc<Liquidity>,
    fvg: Arc<Fvg>,
    doji: Arc<Doji>,
    /// live markets (token -> info) + latest best ask per token — shared with the dashboard.
    view: Arc<ScannerView>,
    /// bumped whenever the live token SET changes, so the WS reconnects with it.
    generation: AtomicU64,
    /// tokens we've already logged a skip for (avoids per-update log spam).
    skip_logged: Mutex<HashSet<String>>,
    sem: Arc<Semaphore>,
    http: reqwest::Client,
}

impl Scanner {
    pub fn new(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, liq: Arc<Liquidity>, fvg: Arc<Fvg>, doji: Arc<Doji>, view: Arc<ScannerView>) -> Self {
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
            fvg,
            doji,
            view,
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
        if st.cfg.scanner_filter_blanket {
            info!("🔭 99c-scanner: filter mode = BLANKET — any liquidity level / FVG hit blocks BOTH sides (no up, no down)");
        }

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
        // Reconcile loop — fill-check (order data) + stale-cancel + directional stop.
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
        // Heartbeat loop — log the live order books so it's visible what's being watched.
        {
            let st = st.clone();
            tokio::spawn(async move {
                let mut t = interval(Duration::from_secs(15));
                loop {
                    t.tick().await;
                    st.heartbeat();
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
        // ONE market per coin: the CURRENT in-progress window (slug ts = now floored to
        // 300; it ends at ts+300). When the window ends we move to the next on the next
        // discovery — and because the markets map isn't replaced until then, we keep
        // watching a just-ended market for up to one discovery interval while it settles
        // toward 0.99, so the edge entry isn't missed despite tracking only "the current".
        let base = (now / 300) * 300;
        let windows = [base];
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
            // slug ts is the window START; the window ends 300s later.
            let end_ts = slug.rsplit('-').next().and_then(|s| s.parse::<u64>().ok()).map(|t| t + 300).unwrap_or(0);
            for (i, tok) in toks.iter().enumerate() {
                if tok.is_empty() {
                    continue;
                }
                // sister = the other outcome's token id (for the Up_ask+Down_ask sanity check)
                let sister = toks.iter().enumerate().find(|(j, t)| *j != i && !t.is_empty()).map(|(_, t)| t.clone()).unwrap_or_default();
                next.insert(
                    tok.clone(),
                    MarketInfo {
                        asset: asset.clone(),
                        title: title.clone(),
                        outcome: outs.get(i).cloned().unwrap_or_default(),
                        tick,
                        market: slug.to_string(),
                        end_ts,
                        sister,
                    },
                );
            }
        }
        if next.is_empty() {
            warn!(
                "🔭 99c-scanner: discovery found 0 live 5m markets for assets [{}] windows {:?} — between windows, or Gamma flags not live yet / unreachable",
                self.assets().join(","),
                windows
            );
            return;
        }
        let new_set: HashSet<String> = next.keys().cloned().collect();
        let mut coins: Vec<String> = next.values().map(|i| i.asset.to_uppercase()).collect();
        coins.sort();
        coins.dedup();
        let old_set: HashSet<String> = {
            let mut guard = self.view.markets.lock().unwrap();
            let old: HashSet<String> = guard.keys().cloned().collect();
            *guard = next;
            old
        };
        if new_set != old_set {
            self.generation.fetch_add(1, Ordering::SeqCst);
            // Drop skip-log + ask-cache entries for tokens we no longer watch.
            self.skip_logged.lock().unwrap().retain(|t| new_set.contains(t));
            self.view.asks.lock().unwrap().retain(|t, _| new_set.contains(t));
            info!(
                "🔭 99c-scanner: now watching {} tokens across {} coins: {}",
                new_set.len(),
                coins.len(),
                coins.join("/")
            );
        }
    }

    async fn ws_loop(&self) {
        // The 0.99 trigger is time-critical (the favorite is at 0.99 only in the final
        // seconds of a window), so reconnect FAST: 1s, capped at 5s — never leave the book
        // unwatched for long. The CLOB market WS is a firehose and resets occasionally,
        // especially at window close; a slow backoff there could miss the entry.
        let mut backoff = 1u64;
        loop {
            let tokens: Vec<String> = self.view.markets.lock().unwrap().keys().cloned().collect();
            if tokens.is_empty() {
                sleep(Duration::from_secs(2)).await;
                continue;
            }
            match self.ws_session(&tokens).await {
                Ok(_) => backoff = 1,
                Err(e) => {
                    warn!("🔭 99c-scanner: market WS ended ({e}); reconnecting in {backoff}s");
                    sleep(Duration::from_secs(backoff)).await;
                    backoff = (backoff * 2).min(5);
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
                            self.record_ask(tok, ask);
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
                                    self.record_ask(tok, ask);
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

    /// Cache the latest best ask for a token (drives the heartbeat log).
    fn record_ask(&self, token: &str, ask: f64) {
        self.view.asks.lock().unwrap().insert(token.to_string(), ask);
    }

    /// Periodic order-book heartbeat so it's visible WHAT the scanner sees and how close
    /// each market is to the 0.99 trigger. Groups the live tokens by coin into Up/Down
    /// best asks, sorted by the side nearest 1.0 first.
    fn heartbeat(&self) {
        // ONE row per MARKET (slug) — never conflate the 3 live windows of a coin.
        // market -> (coin, end_ts, up_ask, down_ask)
        let mut by_market: HashMap<String, (String, u64, f64, f64)> = HashMap::new();
        {
            let markets = self.view.markets.lock().unwrap();
            let asks = self.view.asks.lock().unwrap();
            for (token, info) in markets.iter() {
                let ask = asks.get(token).copied().unwrap_or(0.0);
                let e = by_market.entry(info.market.clone()).or_insert((info.asset.clone(), info.end_ts, 0.0, 0.0));
                if info.outcome.eq_ignore_ascii_case("up") {
                    e.2 = ask;
                } else {
                    e.3 = ask;
                }
            }
        }
        if by_market.is_empty() {
            info!("🔭 99c-scanner: no live markets right now (waiting for the next 5m window)");
            return;
        }
        let mut rows: Vec<(String, u64, f64, f64)> = by_market.into_values().collect();
        // closest-to-trigger market first
        rows.sort_by(|a, b| b.2.max(b.3).partial_cmp(&a.2.max(a.3)).unwrap_or(std::cmp::Ordering::Equal));
        let trig = self.cfg.scanner_trigger_ask;
        let parts: Vec<String> = rows
            .iter()
            .take(12)
            .map(|(coin, end_ts, u, d)| {
                let valid = book_sane(u.max(*d), u.min(*d));
                let hot = valid && ((*u >= trig && *u < 1.0) || (*d >= trig && *d < 1.0));
                let mark = if hot { "🔥" } else if !valid { "⚠" } else { "" };
                let hhmm = chrono::DateTime::from_timestamp(*end_ts as i64, 0)
                    .map(|dt| dt.format("%H:%M").to_string())
                    .unwrap_or_default();
                format!("{}{}@{} U{:.3} D{:.3}", mark, coin.to_uppercase(), hhmm, u, d)
            })
            .collect();
        info!("🔭 99c-scanner books (ask; trigger {:.2}; 🔥=tradable ⚠=bad book): {}", trig, parts.join("  |  "));
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
        let info = match self.view.markets.lock().unwrap().get(token).cloned() {
            Some(i) => i,
            None => return, // not a token we're tracking
        };
        // BOOK SANITY (demogui's _prices_sane binary invariant): only trade a coherent
        // book. The two outcomes' best asks must both be real (0<ask<1) and sum to ≈1
        // (≤ SANE_ASK_SUM_MAX). A side showing 1.000 (no real offer) or a sum far over 1
        // means a stale/one-sided book — skip until it's correct.
        let sister_ask = self.view.asks.lock().unwrap().get(&info.sister).copied().unwrap_or(0.0);
        if !book_sane(best_ask, sister_ask) {
            if self.skip_logged.lock().unwrap().insert(token.to_string()) {
                info!(
                    "🔭 99c-scanner: SKIP {} — book not valid (this ask {:.3} + other side {:.3} = {:.3}; need both 0<ask<1 and sum ≤ {:.2})",
                    label(&info.title, &info.outcome, token),
                    best_ask,
                    sister_ask,
                    best_ask + sister_ask,
                    SANE_ASK_SUM_MAX
                );
            }
            return;
        }
        // Avoidance: the side we'd buy must be clear on BOTH the liquidity and FVG filters.
        // Directional (default): resistance (high level / bearish FVG) blocks Up; support
        // (low level / bullish FVG) blocks Down. BLANKET (SCANNER_FILTER_BLANKET): ANY
        // hit blocks BOTH sides. Missing candle data => not evaluable => skip.
        let is_up = info.outcome.eq_ignore_ascii_case("up");
        let blanket = self.cfg.scanner_filter_blanket;
        let liq = self.liq.signal(&info.asset);
        let fvg = self.fvg.signal(&info.asset);
        let doji = self.doji.signal(&info.asset); // non-directional: a doji blocks both sides
        let evaluable = liq.evaluable && fvg.evaluable && doji.evaluable;
        let liq_block = signal_blocks(&liq, is_up, blanket);
        let fvg_block = signal_blocks(&fvg, is_up, blanket);
        let doji_block = signal_blocks(&doji, is_up, blanket);
        if !evaluable || liq_block || fvg_block || doji_block {
            if self.skip_logged.lock().unwrap().insert(token.to_string()) {
                let reason = if !evaluable {
                    "no Binance candle data yet (or no pair)".to_string()
                } else if blanket {
                    let mut why: Vec<&str> = Vec::new();
                    if liq_block {
                        why.push("a liquidity level");
                    }
                    if fvg_block {
                        why.push("an FVG");
                    }
                    if doji_block {
                        why.push("a doji candle");
                    }
                    format!("market hit {} — no trade (both sides blocked)", why.join(" + "))
                } else {
                    let side = if is_up { "Up" } else { "Down" };
                    let mut why: Vec<String> = Vec::new();
                    if liq_block {
                        why.push(if is_up { "at a high level".into() } else { "at a low level".into() });
                    }
                    if fvg_block {
                        why.push(if is_up { "in a bearish FVG".into() } else { "in a bullish FVG".into() });
                    }
                    if doji_block {
                        why.push("the 5m candle is a doji".into());
                    }
                    format!("buying {side} blocked — {}", why.join(" + "))
                };
                info!("🔭 99c-scanner: SKIP {} — {}", label(&info.title, &info.outcome, token), reason);
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
                // flight) — or it was dropped/crashed before placing. An empty order_id can
                // never become a position, so reclaim it after a hard ceiling EVEN when
                // stale-cancel is off (NINETYNINE_BUY_MAX_AGE_SECS=0) so claims can't leak.
                let ceiling_ms = self
                    .cfg
                    .ninetynine_buy_max_age
                    .map(|m| m.as_millis())
                    .unwrap_or(0)
                    .max(10 * 60 * 1000);
                if (now_ms().saturating_sub(rec.placed_at_ms) as u128) > ceiling_ms {
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
            // DIRECTIONAL CANCEL: a resting unfilled buy whose side a liquidity level / FVG
            // now blocks is cancelled (we no longer want to enter that side). A partial fill
            // becomes a held position the stop then manages. ONLY while the market's 5m
            // window is still LIVE (now < end) — after it ends the live candle belongs to the
            // NEXT window, so a tap there is irrelevant to this market.
            let info = self
                .view
                .markets
                .lock()
                .unwrap()
                .get(&token)
                .map(|i| (i.asset.clone(), i.outcome.eq_ignore_ascii_case("up"), i.end_ts));
            if let Some((asset, is_up, end_ts)) = info {
                if (now_ms() / 1000) < end_ts {
                    let blanket = self.cfg.scanner_filter_blanket;
                    let liq = self.liq.signal(&asset);
                    let fvg = self.fvg.signal(&asset);
                    let doji = self.doji.signal(&asset);
                    let liq_block = signal_blocks(&liq, is_up, blanket);
                    let fvg_block = signal_blocks(&fvg, is_up, blanket);
                    let doji_block = signal_blocks(&doji, is_up, blanket);
                    if liq_block || fvg_block || doji_block {
                        let side_str = if is_up { "Up" } else { "Down" };
                        let mut why: Vec<&str> = Vec::new();
                        if liq_block {
                            why.push("a liquidity level");
                        }
                        if fvg_block {
                            why.push("an FVG");
                        }
                        if doji_block {
                            why.push("a doji");
                        }
                        let reason = why.join(" + ");
                        let _ = self.exec.cancel_confirmed(&rec.order_id, &token).await;
                        let filled = self.exec.order_matched(&rec.order_id).await.unwrap_or(0.0);
                        if filled > EPS {
                            self.store.update_scan_status(&token, "FILLED");
                            info!("🔭 99c-scanner: {} — {} hit {} while the buy rested; cancelled the rest, keeping {:.2} sh filled (stop manages)", label(&rec.title, side_str, &token), side_str, reason, filled);
                        } else {
                            self.store.update_scan_status(&token, "CANCELLED");
                            info!("🔭 99c-scanner: {} — {} hit {} while a buy rested unfilled → cancelled it", label(&rec.title, side_str, &token), side_str, reason);
                        }
                        continue;
                    }
                }
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

        // STOP: for positions we HOLD whose market is still live, if the coin moves AGAINST
        // the side we hold — held Up + (high level / bearish FVG), or held Down + (low
        // level / bullish FVG) — dump with a GTC sell at the exit price (sweeps the bids
        // to the floor). Entry already avoided blocked sides; this catches a hit that
        // happens AFTER we bought, mid-window. Either filter firing for our side is enough.
        if self.cfg.scanner_liquidity_filter || self.cfg.scanner_fvg_filter {
            for (token, rec) in self.store.scan_filled() {
                // asset+side are only known while the market is live (in the discovery
                // map); once it resolves the token drops out and the position settles.
                let (asset, is_up, end_ts) = match self
                    .view
                    .markets
                    .lock()
                    .unwrap()
                    .get(&token)
                    .map(|i| (i.asset.clone(), i.outcome.eq_ignore_ascii_case("up"), i.end_ts))
                {
                    Some(x) => x,
                    None => continue,
                };
                // Only stop while the market's 5m window is still LIVE. Once it ends the
                // outcome is locked and the live candle is the NEXT window's — a tap there
                // must NOT dump this position (it just rides to resolution now).
                if (now_ms() / 1000) >= end_ts {
                    continue;
                }
                // signal() returns no blocks when a filter is disabled or has no data.
                // Blanket mode dumps on ANY hit (either side); directional only on our side.
                let blanket = self.cfg.scanner_filter_blanket;
                let liq = self.liq.signal(&asset);
                let fvg = self.fvg.signal(&asset);
                let liq_hit = signal_blocks(&liq, is_up, blanket);
                let fvg_hit = signal_blocks(&fvg, is_up, blanket);
                // Doji stop is ALWAYS directional (ignores blanket): dump only if the doji
                // leans AGAINST the held side — held Up + bearish doji, or Down + bullish.
                // A doji leaning our way is left to ride (it may still pay the full 1.0).
                let dstop = self.doji.stop_signal(&asset);
                let doji_hit = if is_up { dstop.block_up } else { dstop.block_down };
                if !liq_hit && !fvg_hit && !doji_hit {
                    continue;
                }
                let side = if is_up { "Up" } else { "Down" };
                let mut w: Vec<String> = Vec::new();
                if liq_hit {
                    w.push(if blanket { "a liquidity level".into() } else if is_up { "a high level".into() } else { "a low level".into() });
                }
                if fvg_hit {
                    w.push(if blanket { "an FVG".into() } else if is_up { "a bearish FVG".into() } else { "a bullish FVG".into() });
                }
                if doji_hit {
                    w.push(if is_up { "a bearish doji".into() } else { "a bullish doji".into() });
                }
                let what = format!("held {side} hit {}", w.join(" + "));
                let mut held = self.exec.order_matched(&rec.order_id).await.unwrap_or(0.0);
                if held < MIN_SHARES {
                    held = size; // fallback to the configured size; exchange caps at balance
                }
                let tick = self.exec.tick_size(&token).await;
                let neg_risk = self.exec.token_neg_risk(&token).await;
                let price = snap_price(self.cfg.scanner_exit_price, tick);
                match self.exec.place_gtc(&token, neg_risk, SIDE_SELL, price, held, tick).await {
                    Ok(_) => {
                        self.store.update_scan_status(&token, "EXITED");
                        self.store.record_trade(TradeLog {
                            time: now_str(),
                            market: token.clone(),
                            title: rec.title.clone(),
                            side: "SELL".into(),
                            size: held,
                            price,
                            status: "FILLED".into(),
                        });
                        info!("🔭 99c-scanner: STOP — {} {}, sold {:.0} sh GTC @ {:.2}", label(&rec.title, asset.to_uppercase().as_str(), &token), what, held, price);
                    }
                    Err(e) => warn!("🔭 99c-scanner: stop sell failed for {} ({e}) — will retry", label(&rec.title, "", &token)),
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

/// demogui's binary invariant (`_prices_sane`): a coherent up/down book has both outcomes'
/// best asks present (> 0) and summing to ≈1 (≤ SANE_ASK_SUM_MAX). We do NOT require each
/// ask < 1.0 — a settling market with one side at 1.000 and the other near 0 still sums to
/// ~1 and is a real book (just not buyable on the 1.000 side; the < 1.0 trigger handles
/// that). Only a sum well over 1 (one-sided / stale / crossed) is rejected.
/// Whether a filter signal should block this trade. Directional (default): only the side
/// being traded (Up ← resistance, Down ← support). Blanket (SCANNER_FILTER_BLANKET on):
/// ANY hit on either side blocks — no Up, no Down on that market.
fn signal_blocks(sig: &FilterSignal, is_up: bool, blanket: bool) -> bool {
    if blanket {
        sig.block_up || sig.block_down
    } else if is_up {
        sig.block_up
    } else {
        sig.block_down
    }
}

fn book_sane(ask: f64, sister_ask: f64) -> bool {
    ask > 0.0 && sister_ask > 0.0 && (ask + sister_ask) <= SANE_ASK_SUM_MAX
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn book_sanity_invariant() {
        assert!(book_sane(0.99, 0.02)); // favorite 0.99 + loser 0.02 = 1.01 → ok
        assert!(book_sane(0.52, 0.50)); // fresh ~50/50 = 1.02 → ok
        assert!(book_sane(0.05, 1.0)); // settling: one side 1.000, sum 1.05 ≤ 1.10 → OK now
        assert!(!book_sane(1.0, 0.26)); // sum 1.26 > 1.10 → one-sided/stale
        assert!(!book_sane(0.99, 0.0)); // sister ask missing → bad
        assert!(!book_sane(0.99, 1.0)); // sum 1.99 > 1.10 → bad
        assert!(!book_sane(0.74, 0.50)); // sum 1.24 > 1.10 → stale/one-sided
    }
}
