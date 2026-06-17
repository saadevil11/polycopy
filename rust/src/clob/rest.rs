//! REST market-data feed: polls top-of-book for every monitored token via the
//! CLOB `POST /books` batch endpoint. Used directly in `DATA_SOURCE=rest`, and
//! as the always-on fallback while the WebSocket feed is wired (phase 3).
use std::time::Duration;

use chrono::Utc;
use serde_json::{json, Value};
use tokio::time::sleep;
use tracing::{debug, warn};

use crate::config::Config;
use crate::models::BookTop;
use crate::state::BotState;

/// Max token_ids per `POST /books` request.
const BOOK_BATCH: usize = 100;

pub async fn poll_books(cfg: Config, state: BotState) {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        .user_agent("latebutfast/0.1")
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            warn!("rest feed: cannot build http client: {e}");
            return;
        }
    };

    loop {
        let tokens: Vec<String> = state.tokens.iter().map(|e| e.key().clone()).collect();
        if !tokens.is_empty() {
            for chunk in tokens.chunks(BOOK_BATCH) {
                if let Err(e) = poll_chunk(&cfg, &client, &state, chunk).await {
                    debug!("rest feed batch: {e}");
                }
            }
        }
        sleep(cfg.rest_poll).await;
    }
}

async fn poll_chunk(
    cfg: &Config,
    client: &reqwest::Client,
    state: &BotState,
    tokens: &[String],
) -> anyhow::Result<()> {
    let body: Vec<Value> = tokens.iter().map(|t| json!({ "token_id": t })).collect();
    let url = format!("{}/books", cfg.clob_url);
    let resp = client.post(&url).json(&body).send().await?;
    if !resp.status().is_success() {
        anyhow::bail!("/books -> HTTP {}", resp.status());
    }
    let books: Vec<Value> = resp.json().await?;
    for b in &books {
        if let Some((tid, top)) = parse_book(b) {
            state.set_book(&tid, top);
            if let Some(t) = b["tick_size"].as_f64().or_else(|| b["tick_size"].as_str().and_then(|s| s.parse().ok())) {
                state.set_tick(&tid, t);
            }
        }
    }
    Ok(())
}

/// Extract (token_id, best_bid/best_ask) from a CLOB book object. Asks/bids are
/// arrays of {price, size} strings; we take the lowest ask and highest bid so we
/// are robust to either ordering.
fn parse_book(b: &Value) -> Option<(String, BookTop)> {
    let tid = b["asset_id"]
        .as_str()
        .or_else(|| b["token_id"].as_str())?
        .to_string();

    let best_ask = b["asks"]
        .as_array()
        .map(|a| a.iter().filter_map(level_price).fold(f64::MAX, f64::min))
        .filter(|v| *v != f64::MAX)
        .unwrap_or(0.0);
    let best_bid = b["bids"]
        .as_array()
        .map(|a| a.iter().filter_map(level_price).fold(0.0_f64, f64::max))
        .unwrap_or(0.0);

    Some((
        tid,
        BookTop {
            best_bid,
            best_ask,
            updated: Some(Utc::now()),
        },
    ))
}

fn level_price(level: &Value) -> Option<f64> {
    match &level["price"] {
        Value::String(s) => s.parse().ok(),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}
