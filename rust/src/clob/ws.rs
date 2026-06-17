//! Real-time top-of-book via the CLOB **market** socket
//! (wss://ws-subscriptions-clob.polymarket.com/ws/market). Subscribes to every
//! monitored token, parses `best_bid_ask` / `price_change` / `book` events, and
//! keeps BotState's book map current. Reconnects when the watch set changes.
use std::time::Duration;

use chrono::Utc;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::protocol::Message;
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::models::BookTop;
use crate::state::BotState;

pub async fn run(cfg: Config, state: BotState) {
    loop {
        let tokens: Vec<String> = state.tokens.iter().map(|e| e.key().clone()).collect();
        if tokens.is_empty() {
            sleep(Duration::from_secs(2)).await;
            continue;
        }
        if let Err(e) = session(&cfg, &state, &tokens).await {
            warn!("ws session ended: {e}");
        }
        sleep(Duration::from_secs(2)).await; // backoff before reconnect
    }
}

async fn session(cfg: &Config, state: &BotState, tokens: &[String]) -> anyhow::Result<()> {
    let (ws, _) = tokio_tungstenite::connect_async(&cfg.clob_ws_url).await?;
    let (mut write, mut read) = ws.split();

    let sub = json!({
        "assets_ids": tokens,
        "type": "market",
        "custom_feature_enabled": true,
    });
    write.send(Message::Text(sub.to_string())).await?;
    info!("ws connected: subscribed {} tokens", tokens.len());

    let mut ping = interval(Duration::from_secs(7));
    let mut watch_check = interval(Duration::from_secs(10));
    let start_len = tokens.len();

    loop {
        tokio::select! {
            msg = read.next() => match msg {
                Some(Ok(Message::Text(t))) => handle_frame(state, &t),
                Some(Ok(Message::Binary(b))) => {
                    if let Ok(t) = String::from_utf8(b) { handle_frame(state, &t); }
                }
                Some(Ok(Message::Ping(p))) => { let _ = write.send(Message::Pong(p)).await; }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(e.into()),
                None => return Ok(()), // closed
            },
            _ = ping.tick() => {
                if write.send(Message::Text("PING".into())).await.is_err() {
                    return Ok(());
                }
            }
            _ = watch_check.tick() => {
                // Reconnect with the full list if the monitored set changed.
                if state.tokens.len() != start_len {
                    debug!("ws: watch set changed ({start_len} -> {}), reconnecting", state.tokens.len());
                    let _ = write.send(Message::Close(None)).await;
                    return Ok(());
                }
            }
        }
    }
}

fn handle_frame(state: &BotState, text: &str) {
    let t = text.trim();
    if t.is_empty() || t == "PONG" || t == "PING" {
        return;
    }
    let v: Value = match serde_json::from_str(t) {
        Ok(v) => v,
        Err(_) => return,
    };
    match v {
        Value::Array(arr) => {
            for ev in &arr {
                handle_event(state, ev);
            }
        }
        Value::Object(_) => handle_event(state, &v),
        _ => {}
    }
}

fn handle_event(state: &BotState, ev: &Value) {
    match ev.get("event_type").and_then(|x| x.as_str()).unwrap_or("") {
        "best_bid_ask" => {
            if let Some(tid) = ev.get("asset_id").and_then(|x| x.as_str()) {
                let bid = fstr(ev.get("best_bid"));
                let ask = fstr(ev.get("best_ask"));
                set_top(state, tid, bid, ask);
            }
        }
        "price_change" => {
            if let Some(changes) = ev.get("price_changes").and_then(|x| x.as_array()) {
                for c in changes {
                    if let Some(tid) = c.get("asset_id").and_then(|x| x.as_str()) {
                        set_top(state, tid, fstr(c.get("best_bid")), fstr(c.get("best_ask")));
                    }
                }
            }
        }
        "book" => {
            if let Some(tid) = ev.get("asset_id").and_then(|x| x.as_str()) {
                // arrays sorted ascending: best ask = asks.last, best bid = bids.last
                let best_ask = ev.get("asks").and_then(|a| a.as_array()).and_then(|a| a.last()).map(level).unwrap_or(0.0);
                let best_bid = ev.get("bids").and_then(|b| b.as_array()).and_then(|b| b.last()).map(level).unwrap_or(0.0);
                set_top(state, tid, best_bid, best_ask);
                let tick = fstr(ev.get("tick_size"));
                if tick > 0.0 {
                    state.set_tick(tid, tick);
                }
            }
        }
        "tick_size_change" => {
            if let Some(tid) = ev.get("asset_id").and_then(|x| x.as_str()) {
                let tick = fstr(ev.get("new_tick_size"));
                if tick > 0.0 {
                    state.set_tick(tid, tick);
                }
            }
        }
        _ => {} // last_trade_price, etc.
    }
}

fn set_top(state: &BotState, token_id: &str, bid: f64, ask: f64) {
    if !state.tokens.contains_key(token_id) {
        return; // ignore stale/unmonitored tokens
    }
    state.set_book(
        token_id,
        BookTop { best_bid: bid, best_ask: ask, updated: Some(Utc::now()) },
    );
}

fn fstr(v: Option<&Value>) -> f64 {
    match v {
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn level(l: &Value) -> f64 {
    fstr(l.get("price"))
}
