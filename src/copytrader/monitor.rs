//! Target monitor: poll the Data API `/activity` for the target trader and emit
//! each NEW trade (deduped by trade id) onto a channel. Seeds the dedup set on
//! the first poll so we never replay history.
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::Message;
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::copytrader::store::Store;
use crate::copytrader::{Side, TargetTrade};

/// Real-time monitor via the activity WebSocket. Faster than REST (push, not
/// poll), but Polymarket rate-limits this socket (HTTP 429) on datacenter IPs -
/// it only connects reliably from a residential/allowed IP. On repeated failure
/// it just keeps retrying (set DATA_SOURCE=rest to use polling instead).
pub async fn run_ws(cfg: Config, store: Arc<Store>, tx: Sender<TargetTrade>) {
    if cfg.target_trader.is_empty() {
        warn!("copytrader ws monitor: TARGET_TRADER_ADDRESS not set — idle");
        return;
    }
    let mut backoff = 2u64;
    loop {
        match ws_session(&cfg, &store, &tx).await {
            Ok(_) => backoff = 2,
            Err(e) => {
                warn!("copytrader ws session ended ({e}); reconnecting in {backoff}s (if this is a 429, your IP is rate-limited — use DATA_SOURCE=rest)");
                sleep(Duration::from_secs(backoff)).await;
                backoff = (backoff * 2).min(60);
            }
        }
    }
}

async fn ws_session(cfg: &Config, store: &Arc<Store>, tx: &Sender<TargetTrade>) -> anyhow::Result<()> {
    // Browser-like headers so Cloudflare lets the handshake reach the app.
    let mut req = cfg.data_ws_url.as_str().into_client_request()?;
    req.headers_mut().insert("Origin", "https://polymarket.com".parse()?);
    req.headers_mut().insert(
        "User-Agent",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36".parse()?,
    );
    let (ws, _) = tokio_tungstenite::connect_async(req).await?;
    let (mut write, mut read) = ws.split();

    let sub = json!({"action":"subscribe","subscriptions":[{"topic":"activity","type":"trades"}]});
    write.send(Message::Text(sub.to_string())).await?;
    info!("🛰️  copytrader ws: connected, subscribed to activity/trades for {}", cfg.target_trader);

    let mut ping = interval(Duration::from_secs(20));
    loop {
        tokio::select! {
            msg = read.next() => match msg {
                Some(Ok(Message::Text(t))) => handle_ws_frame(cfg, store, tx, &t).await,
                Some(Ok(Message::Binary(b))) => {
                    if let Ok(t) = String::from_utf8(b) { handle_ws_frame(cfg, store, tx, &t).await; }
                }
                Some(Ok(Message::Ping(p))) => { let _ = write.send(Message::Pong(p)).await; }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(e.into()),
                None => return Ok(()),
            },
            _ = ping.tick() => {
                if write.send(Message::Text("PING".into())).await.is_err() { return Ok(()); }
            }
        }
    }
}

async fn handle_ws_frame(cfg: &Config, store: &Arc<Store>, tx: &Sender<TargetTrade>, text: &str) {
    let t = text.trim();
    if t.is_empty() || t == "PONG" || t == "ping" {
        return;
    }
    let v: Value = match serde_json::from_str(t) {
        Ok(v) => v,
        Err(_) => return,
    };
    // {topic:"activity", type:"trades", payload:{...}}
    if v.get("topic").and_then(|x| x.as_str()) != Some("activity") {
        return;
    }
    let payload = match v.get("payload") {
        Some(p) => p,
        None => return,
    };
    // Only our target trader.
    if payload.get("proxyWallet").and_then(|x| x.as_str()).map(|s| s.to_lowercase()) != Some(cfg.target_trader.clone()) {
        return;
    }
    let id = match activity_id(payload) {
        Some(i) => i,
        None => return,
    };
    if store.seen(&id) {
        return;
    }
    store.mark_seen(&id);
    if let Some(trade) = parse_trade(payload) {
        info!("👁  target {}", trade_line(&trade));
        let _ = tx.send(trade).await;
    }
}

pub async fn run(cfg: Config, store: Arc<Store>, tx: Sender<TargetTrade>) {
    if cfg.target_trader.is_empty() {
        warn!("copytrader monitor: TARGET_TRADER_ADDRESS not set — monitor idle");
        return;
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent("polybotshadow/0.1")
        .build()
        .expect("http client");

    let url = format!("{}/activity?user={}&limit=30", cfg.data_api_url, cfg.target_trader);
    info!("🛰️  copytrader monitor: polling {} every {:?}", cfg.target_trader, cfg.copy_poll);

    // Seed: mark everything currently in the feed as already-seen.
    if let Some(items) = fetch(&client, &url).await {
        for it in &items {
            if let Some(id) = activity_id(it) {
                store.mark_seen(&id);
            }
        }
        info!("🛰️  copytrader monitor: seeded {} recent items", items.len());
    }

    loop {
        sleep(cfg.copy_poll).await;
        let items = match fetch(&client, &url).await {
            Some(v) => v,
            None => continue,
        };
        // Oldest-first so trades dispatch in chronological order.
        for it in items.iter().rev() {
            let id = match activity_id(it) {
                Some(i) => i,
                None => continue,
            };
            if store.seen(&id) {
                continue;
            }
            store.mark_seen(&id);
            if let Some(t) = parse_trade(it) {
                info!("👁  target {}", trade_line(&t));
                let _ = tx.send(t).await;
            }
        }
    }
}

async fn fetch(client: &reqwest::Client, url: &str) -> Option<Vec<Value>> {
    match client.get(url).send().await {
        Ok(r) => match r.json::<Value>().await {
            Ok(Value::Array(a)) => Some(a),
            _ => None,
        },
        Err(e) => {
            debug!("monitor fetch failed: {e}");
            None
        }
    }
}

/// Stable key per activity item (tx hash + asset + type).
fn activity_id(it: &Value) -> Option<String> {
    let tx = it.get("transactionHash").and_then(|x| x.as_str()).unwrap_or("");
    if tx.is_empty() {
        return None;
    }
    let asset = it.get("asset").and_then(|x| x.as_str()).unwrap_or("");
    let ty = it.get("type").and_then(|x| x.as_str()).unwrap_or("");
    Some(format!("ws_{tx}:{asset}:{ty}"))
}

fn parse_trade(it: &Value) -> Option<TargetTrade> {
    // Only TRADE activity with a real BUY/SELL side.
    let side = match it.get("side").and_then(|x| x.as_str()).map(|s| s.to_uppercase()) {
        Some(ref s) if s == "BUY" => Side::Buy,
        Some(ref s) if s == "SELL" => Side::Sell,
        _ => return None,
    };
    let tx = it.get("transactionHash").and_then(|x| x.as_str())?;
    let token_id = it.get("asset").and_then(|x| x.as_str()).unwrap_or("").to_string();
    if token_id.is_empty() {
        return None;
    }
    Some(TargetTrade {
        trade_id: format!("ws_{tx}"),
        market_id: it.get("conditionId").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        token_id,
        side,
        price: num(it, "price"),
        size: num(it, "size"),
        title: it.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        outcome: it.get("outcome").and_then(|x| x.as_str()).unwrap_or("").to_string(),
    })
}

fn num(v: &Value, key: &str) -> f64 {
    match v.get(key) {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

/// One-line, human-readable summary of a detected target trade for the logs.
fn trade_line(t: &TargetTrade) -> String {
    let side = match t.side {
        Side::Buy => "BUY ",
        Side::Sell => "SELL",
    };
    let market = if t.title.is_empty() {
        "(unknown market)".to_string()
    } else {
        let oc = if t.outcome.is_empty() { String::new() } else { format!(" · {}", t.outcome) };
        format!("\"{}\"{}", t.title, oc)
    };
    format!("{side} {:.2} sh @ {:.4}  {market}", t.size, t.price)
}
