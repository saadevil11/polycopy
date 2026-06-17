//! Open-position tracking via the Data API. Polls the funder wallet's positions
//! (held to resolution — this bot never sells) for the dashboard.
use std::time::Duration;

use serde_json::Value;
use tokio::time::sleep;
use tracing::debug;

use crate::config::Config;
use crate::models::Position;
use crate::state::BotState;

const POLL: Duration = Duration::from_secs(15);

pub async fn run(cfg: Config, state: BotState) {
    if cfg.funder_address.is_empty() || cfg.funder_address.starts_with("0xYOUR") {
        debug!("positions: no funder address — position polling disabled");
        return;
    }
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent("latebutfast/0.1")
        .build()
    {
        Ok(c) => c,
        Err(_) => return,
    };
    let url = format!(
        "{}/positions?user={}&sizeThreshold=0.1",
        cfg.data_api_url, cfg.funder_address
    );

    loop {
        if let Err(e) = poll(&client, &url, &state).await {
            debug!("positions poll: {e}");
        }
        sleep(POLL).await;
    }
}

async fn poll(client: &reqwest::Client, url: &str, state: &BotState) -> anyhow::Result<()> {
    let arr: Vec<Value> = client.get(url).send().await?.json().await?;
    state.positions.clear();
    for p in &arr {
        let token_id = sget(p, &["asset", "tokenId", "token_id"]);
        if token_id.is_empty() {
            continue;
        }
        state.upsert_position(Position {
            token_id,
            condition_id: sget(p, &["conditionId", "condition_id"]),
            title: sget(p, &["title"]),
            outcome: sget(p, &["outcome"]),
            size: nget(p, &["size"]),
            avg_price: nget(p, &["avgPrice", "avg_price"]),
            cur_price: nget(p, &["curPrice", "cur_price"]),
            cash_pnl: nget(p, &["cashPnl", "cash_pnl", "realizedPnl"]),
        });
    }
    Ok(())
}

fn sget(v: &Value, keys: &[&str]) -> String {
    for k in keys {
        if let Some(s) = v.get(*k).and_then(|x| x.as_str()) {
            return s.to_string();
        }
    }
    String::new()
}

fn nget(v: &Value, keys: &[&str]) -> f64 {
    for k in keys {
        match v.get(*k) {
            Some(Value::Number(n)) => return n.as_f64().unwrap_or(0.0),
            Some(Value::String(s)) => {
                if let Ok(f) = s.parse() {
                    return f;
                }
            }
            _ => {}
        }
    }
    0.0
}
