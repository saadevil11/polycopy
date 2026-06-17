//! Discovers the markets we monitor and refreshes them on a timer:
//!   * crypto 5-minute up/down for each configured asset (current + next slots)
//!   * FIFA World Cup game markets (moneyline / spreads / totals / btts)
//!
//! All work is read-only HTTP against Gamma; results are upserted into BotState.
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::Value;
use tokio::time::sleep;
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::models::{MarketKind, OutcomeToken};
use crate::state::BotState;

const DISCOVERY_INTERVAL: Duration = Duration::from_secs(15);
const SLOT_SECS: i64 = 300; // 5 minutes
/// How many upcoming 5m slots (incl. the current one) to pre-register per asset.
const SLOTS_AHEAD: i64 = 2;

pub async fn run(cfg: Config, state: BotState) {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent("latebutfast/0.1")
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            warn!("discovery: cannot build http client: {e}");
            return;
        }
    };

    loop {
        let before = state.tokens.len();
        if let Err(e) = discover_5m(&cfg, &state, &client).await {
            warn!("discovery(5m): {e}");
        }
        if cfg.enable_worldcup {
            if let Err(e) = discover_worldcup(&cfg, &state, &client).await {
                warn!("discovery(worldcup): {e}");
            }
        }
        state.prune_expired(Utc::now());
        let after = state.tokens.len();
        if after != before {
            info!("discovery: monitoring {after} tokens");
        }
        sleep(DISCOVERY_INTERVAL).await;
    }
}

// ── crypto 5-minute up/down ─────────────────────────────────────────────────

async fn discover_5m(cfg: &Config, state: &BotState, client: &reqwest::Client) -> anyhow::Result<()> {
    let now = Utc::now().timestamp();
    let base = (now / SLOT_SECS) * SLOT_SECS;

    let mut slugs = Vec::new();
    for asset in &cfg.assets {
        for k in 0..=SLOTS_AHEAD {
            slugs.push(format!("{asset}-updown-5m-{}", base + k * SLOT_SECS));
        }
    }

    // Fetch all slugs concurrently.
    let fetches = slugs.iter().map(|slug| fetch_market(cfg, client, slug));
    let results = futures_util::future::join_all(fetches).await;

    for (slug, res) in slugs.iter().zip(results) {
        let markets = match res {
            Ok(m) => m,
            Err(e) => {
                debug!("5m fetch {slug}: {e}");
                continue;
            }
        };
        for m in markets {
            let asset = slug.split('-').next().unwrap_or("?").to_string();
            register_market(state, &m, MarketKind::Crypto5m { asset });
        }
    }
    Ok(())
}

// ── FIFA World Cup game markets ──────────────────────────────────────────────

async fn discover_worldcup(
    cfg: &Config,
    state: &BotState,
    client: &reqwest::Client,
) -> anyhow::Result<()> {
    // Per-GAME World Cup markets live under series `soccer-fifwc` (NOT the
    // `world-cup` tag, which is only tournament/group FUTURES). Each game is
    // split across events: base slug = moneyline (3-way negRisk group),
    // `{base}-more-markets` = spreads/totals/btts. Paginate (limit caps at 100).
    let mut offset = 0usize;
    let mut kept = 0usize;
    loop {
        let url = format!(
            "{}/events?series_slug=soccer-fifwc&closed=false&limit=100&offset={offset}&order=startDate&ascending=true",
            cfg.gamma_url
        );
        // Tolerant parse: a page past the end can return a non-array; stop then.
        let txt = client.get(&url).send().await?.text().await?;
        let events: Vec<Value> = serde_json::from_str::<Value>(&txt)
            .ok()
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default();
        let n = events.len();
        if n == 0 {
            break;
        }
        for ev in &events {
            let Some(markets) = ev["markets"].as_array() else {
                continue;
            };
            for m in markets {
                if !tradable(m) {
                    continue;
                }
                let Some(mtype) = classify_worldcup(m) else {
                    continue; // futures / half-time / team-total / other props
                };
                if !cfg.worldcup_market_types.iter().any(|t| t == &mtype) {
                    continue;
                }
                register_market(state, m, MarketKind::WorldCup { market_type: mtype });
                kept += 1;
            }
        }
        offset += 100;
        if n < 100 || offset > 2000 {
            break;
        }
    }
    debug!("worldcup: kept {kept} game-market sides across the schedule");
    Ok(())
}

/// A market is worth monitoring if its order book is live and it isn't closed.
fn tradable(m: &Value) -> bool {
    m["active"].as_bool().unwrap_or(true)
        && !m["closed"].as_bool().unwrap_or(false)
        && m["enableOrderBook"].as_bool().unwrap_or(true)
}

/// Classify a Gamma market into one of our 4 full-game World Cup types, or None.
/// Uses `sportsMarketType` + `marketMetadata.opticOddsMarketId` (verified live).
/// Excludes half-time / team-total / exact-score / correct-score variants.
fn classify_worldcup(m: &Value) -> Option<String> {
    let st = m["sportsMarketType"].as_str().unwrap_or("").to_lowercase();
    let optic = m["marketMetadata"]["opticOddsMarketId"]
        .as_str()
        .unwrap_or("")
        .to_lowercase();

    // Exclusions (half/team/score props).
    if optic.starts_with("1st_half")
        || optic.starts_with("2nd_half")
        || optic == "team_total"
        || st.contains("first_half")
        || st.contains("second_half")
        || matches!(
            st.as_str(),
            "soccer_team_totals" | "soccer_halftime_result" | "soccer_exact_score" | "correct_score"
        )
    {
        return None;
    }

    let t = match st.as_str() {
        "moneyline" => "moneyline",
        "spreads" => "spreads",
        "totals" => "totals",
        "both_teams_to_score" => "btts",
        _ => match optic.as_str() {
            "moneyline_3-way" | "moneyline" => "moneyline",
            "asian_handicap" => "spreads",
            "total_goals" => "totals",
            "both_teams_to_score" => "btts",
            _ => return None,
        },
    };
    Some(t.to_string())
}

// ── shared helpers ───────────────────────────────────────────────────────────

/// GET /markets?slug=… → the (usually single-element) array of market objects.
async fn fetch_market(
    cfg: &Config,
    client: &reqwest::Client,
    slug: &str,
) -> anyhow::Result<Vec<Value>> {
    let url = format!("{}/markets?slug={}", cfg.gamma_url, slug);
    let arr: Vec<Value> = client.get(&url).send().await?.json().await?;
    Ok(arr)
}

/// Parse a Gamma market object and upsert its outcome tokens into state.
fn register_market(state: &BotState, m: &Value, kind: MarketKind) {
    if m["closed"].as_bool().unwrap_or(false) {
        return;
    }
    let condition_id = m["conditionId"].as_str().unwrap_or_default().to_string();
    if condition_id.is_empty() {
        return;
    }
    let slug = m["slug"].as_str().unwrap_or_default().to_string();
    let neg_risk = m["negRisk"].as_bool().unwrap_or(false);
    let tick = m["orderPriceMinTickSize"].as_f64().unwrap_or(0.01);
    // Kickoff for sports markets. crypto 5m markets have no gameStartTime -> None.
    let start_time = parse_dt(&m["gameStartTime"])
        .or_else(|| parse_dt(&m["startTime"]))
        .or_else(|| parse_dt(&m["startDate"]));
    let end_time = parse_dt(&m["endDate"]).or_else(|| parse_dt(&m["endDateIso"]));

    let outcomes = str_array(&m["outcomes"]);
    let token_ids = str_array(&m["clobTokenIds"]);
    if token_ids.is_empty() {
        return;
    }

    for (i, tid) in token_ids.iter().enumerate() {
        let outcome = outcomes.get(i).cloned().unwrap_or_else(|| format!("#{i}"));
        state.upsert_token(OutcomeToken {
            token_id: tid.clone(),
            condition_id: condition_id.clone(),
            market_slug: slug.clone(),
            outcome,
            neg_risk,
            kind: kind.clone(),
            tick_size: tick,
            start_time,
            end_time,
        });
    }
}

/// Gamma encodes `outcomes` / `clobTokenIds` as a JSON-array *string*; some
/// endpoints return a native array. Handle both.
fn str_array(v: &Value) -> Vec<String> {
    match v {
        Value::Array(a) => a.iter().filter_map(|x| x.as_str().map(String::from)).collect(),
        Value::String(s) => serde_json::from_str::<Vec<String>>(s).unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// Parse a timestamp tolerant of both ISO RFC3339 ("2026-06-17T01:00:00Z") and
/// Gamma's `gameStartTime` form ("2026-06-17 01:00:00+00" — space, short offset).
fn parse_dt(v: &Value) -> Option<DateTime<Utc>> {
    let s = v.as_str()?.trim();
    if s.is_empty() {
        return None;
    }
    if let Ok(d) = DateTime::parse_from_rfc3339(s) {
        return Some(d.with_timezone(&Utc));
    }
    // Normalize "YYYY-MM-DD HH:MM:SS+00" -> "YYYY-MM-DDTHH:MM:SS+00:00".
    let mut t = s.replacen(' ', "T", 1);
    if let Some(pos) = t.rfind(['+', '-']) {
        if pos > 10 {
            let off_len = t.len() - pos;
            if off_len == 3 {
                t.push_str(":00"); // "+00" -> "+00:00"
            } else if off_len == 5 && !t[pos..].contains(':') {
                t.insert(pos + 3, ':'); // "+0000" -> "+00:00"
            }
        }
    }
    DateTime::parse_from_rfc3339(&t)
        .ok()
        .map(|d| d.with_timezone(&Utc))
}
