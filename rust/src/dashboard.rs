//! Live dashboard: an axum server exposing `GET /` (a self-contained HTML page
//! that polls every second) and `GET /api/state` (the JSON snapshot). Shows
//! every monitored market's top-of-book, which sides are armed/triggered, the
//! resting (or dry-run) orders, and open positions.
use std::sync::atomic::Ordering;

use axum::{extract::State, response::Html, routing::get, Json, Router};
use chrono::Utc;
use serde::Serialize;
use tracing::info;

use crate::config::Config;
use crate::state::BotState;

#[derive(Clone)]
struct Ctx {
    cfg: Config,
    state: BotState,
}

pub async fn serve(cfg: Config, state: BotState) -> anyhow::Result<()> {
    let port = cfg.dashboard_port;
    let ctx = Ctx { cfg, state };
    let app = Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
        .with_state(ctx);

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    info!("dashboard live at http://localhost:{port}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

// ── JSON snapshot ────────────────────────────────────────────────────────────

#[derive(Serialize)]
struct Snapshot {
    mode: String,
    data_source: String,
    uptime_secs: i64,
    markets_seen: u64,
    monitored: usize,
    armed: usize,
    blocked_tick: usize,
    pregame: usize,
    crypto_size: f64,
    worldcup_size: f64,
    orders_placed: u64,
    open_positions: usize,
    markets: Vec<MarketRow>,
    orders: Vec<OrderRow>,
    positions: Vec<PositionRow>,
}

#[derive(Serialize)]
struct MarketRow {
    label: String,
    outcome: String,
    slug: String,
    best_bid: f64,
    best_ask: f64,
    tick: f64,
    armed: bool,        // ask in band AND tick places 0.999 AND game started
    blocked_tick: bool, // ask in band but tick can't reach 0.999 (won't place)
    pregame: bool,      // ask in band + tick ok but the game hasn't kicked off
    triggered: bool,
    secs_left: Option<i64>,
    starts_in: Option<i64>, // secs until kickoff (sports only)
}

#[derive(Serialize)]
struct OrderRow {
    market_label: String,
    outcome: String,
    price: f64,
    size: f64,
    filled: f64,
    status: String,
    placed_at: String,
}

#[derive(Serialize)]
struct PositionRow {
    title: String,
    outcome: String,
    size: f64,
    avg_price: f64,
    cur_price: f64,
    cash_pnl: f64,
}

async fn api_state(State(ctx): State<Ctx>) -> Json<Snapshot> {
    let state = &ctx.state;
    let cfg = &ctx.cfg;
    let now = Utc::now();

    let trigger_min = cfg.trigger_asks.iter().cloned().fold(f64::MAX, f64::min);

    let mut markets: Vec<MarketRow> = Vec::new();
    let mut armed = 0usize;
    let mut blocked_tick = 0usize;
    let mut pregame = 0usize;
    for e in state.tokens.iter() {
        let t = e.value();
        let top = state.books.get(t.token_id.as_str()).map(|b| *b.value());
        let (bid, ask) = top.map(|b| (b.best_bid, b.best_ask)).unwrap_or((0.0, 0.0));
        let tick = state.effective_tick(&t.token_id);
        let in_band = ask > 1e-6 && ask + 1e-6 >= trigger_min && ask <= 1.0 + 1e-6;
        let placeable = crate::clob::signing::price_representable(cfg.buy_price, tick);
        let started = t.started(now);
        let is_armed = in_band && placeable && started;
        let is_blocked = in_band && !placeable;
        let is_pregame = in_band && placeable && !started;
        if is_armed {
            armed += 1;
        }
        if is_blocked {
            blocked_tick += 1;
        }
        if is_pregame {
            pregame += 1;
        }
        markets.push(MarketRow {
            label: t.kind.label(),
            outcome: t.outcome.clone(),
            slug: t.market_slug.clone(),
            best_bid: bid,
            best_ask: ask,
            tick,
            armed: is_armed,
            blocked_tick: is_blocked,
            pregame: is_pregame,
            triggered: state.triggered.contains_key(&t.token_id),
            secs_left: t.end_time.map(|et| (et - now).num_seconds()),
            starts_in: t.start_time.map(|st| (st - now).num_seconds()),
        });
    }
    // Armed first, then by ask descending — most-interesting on top.
    markets.sort_by(|a, b| {
        b.armed
            .cmp(&a.armed)
            .then(b.best_ask.partial_cmp(&a.best_ask).unwrap_or(std::cmp::Ordering::Equal))
    });

    let mut orders: Vec<OrderRow> = state
        .orders
        .iter()
        .map(|e| {
            let o = e.value();
            OrderRow {
                market_label: o.market_label.clone(),
                outcome: o.outcome.clone(),
                price: o.price,
                size: o.size,
                filled: o.filled,
                status: format!("{:?}", o.status),
                placed_at: o.placed_at.format("%H:%M:%S").to_string(),
            }
        })
        .collect();
    orders.sort_by(|a, b| b.placed_at.cmp(&a.placed_at));

    let positions: Vec<PositionRow> = state
        .positions
        .iter()
        .map(|e| {
            let p = e.value();
            PositionRow {
                title: p.title.clone(),
                outcome: p.outcome.clone(),
                size: p.size,
                avg_price: p.avg_price,
                cur_price: p.cur_price,
                cash_pnl: p.cash_pnl,
            }
        })
        .collect();

    Json(Snapshot {
        mode: if cfg.dry_run { "DRY-RUN".into() } else { "LIVE".into() },
        data_source: format!("{:?}", cfg.data_source),
        uptime_secs: (now - state.started_at).num_seconds(),
        markets_seen: state.markets_seen.load(Ordering::Relaxed),
        monitored: state.tokens.len(),
        armed,
        blocked_tick,
        pregame,
        crypto_size: cfg.crypto_order_size_shares,
        worldcup_size: cfg.worldcup_order_size_shares,
        orders_placed: state.orders_placed.load(Ordering::Relaxed),
        open_positions: positions.len(),
        markets,
        orders,
        positions,
    })
}

const INDEX_HTML: &str = r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>latebutfast</title>
<style>
  :root{--bg:#0b0e14;--panel:#131825;--line:#222a3a;--txt:#cdd6e4;--dim:#7c879c;
        --green:#39d98a;--amber:#ffcf5c;--red:#ff6b6b;--blue:#5cc8ff;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:13px/1.45 ui-monospace,Menlo,Consolas,monospace}
  header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:24px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0;letter-spacing:.5px}
  h1 b{color:var(--blue)}
  .badge{padding:2px 8px;border-radius:6px;font-weight:700}
  .badge.dry{background:#23314a;color:var(--blue)}
  .badge.live{background:#3a2330;color:var(--red)}
  .stats{display:flex;gap:22px;flex-wrap:wrap;color:var(--dim)}
  .stats b{color:var(--txt)}
  main{padding:16px 20px;display:grid;gap:18px;grid-template-columns:1fr;max-width:1280px}
  .grid2{display:grid;gap:18px;grid-template-columns:1fr 1fr}
  @media(max-width:900px){.grid2{grid-template-columns:1fr}}
  section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .hd{padding:9px 14px;border-bottom:1px solid var(--line);color:var(--dim);text-transform:uppercase;letter-spacing:.8px;font-size:11px;display:flex;justify-content:space-between}
  table{width:100%;border-collapse:collapse}
  th,td{padding:6px 12px;text-align:left;white-space:nowrap}
  th{color:var(--dim);font-weight:500;border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase}
  tbody tr:nth-child(even){background:rgba(255,255,255,.02)}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .pill{padding:1px 7px;border-radius:20px;font-size:11px;font-weight:700}
  .arm{background:rgba(255,207,92,.16);color:var(--amber)}
  .trg{background:rgba(57,217,138,.16);color:var(--green)}
  .blk{background:rgba(124,135,156,.18);color:var(--dim)}
  .pre{background:rgba(92,200,255,.16);color:var(--blue)}
  .idle{color:var(--dim)}
  .ask-hi{color:var(--amber);font-weight:700}
  .pos{color:var(--green)} .neg{color:var(--red)}
  .empty{padding:18px 14px;color:var(--dim)}
  footer{padding:8px 20px;color:var(--dim);font-size:11px}
</style>
</head>
<body>
<header>
  <h1><b>latebutfast</b> · near-resolution sniper</h1>
  <span id="mode" class="badge dry">…</span>
  <div class="stats" id="stats"></div>
</header>
<main>
  <section>
    <div class="hd"><span>Monitored markets — top of book</span><span id="mcount"></span></div>
    <table>
      <thead><tr><th>Market</th><th>Outcome</th><th class="num">Bid</th><th class="num">Ask</th><th class="num">Tick</th><th>State</th><th class="num">Closes in</th><th>Slug</th></tr></thead>
      <tbody id="markets"></tbody>
    </table>
  </section>
  <div class="grid2">
    <section>
      <div class="hd"><span>Orders (resting / dry-run)</span><span id="ocount"></span></div>
      <table>
        <thead><tr><th>Market</th><th>Outcome</th><th class="num">Px</th><th class="num">Sz</th><th class="num">Filled</th><th>Status</th><th>At</th></tr></thead>
        <tbody id="orders"></tbody>
      </table>
    </section>
    <section>
      <div class="hd"><span>Positions</span><span id="pcount"></span></div>
      <table>
        <thead><tr><th>Title</th><th>Outcome</th><th class="num">Size</th><th class="num">Avg</th><th class="num">Cur</th><th class="num">PnL</th></tr></thead>
        <tbody id="positions"></tbody>
      </table>
    </section>
  </div>
</main>
<footer id="foot">connecting…</footer>
<script>
const $ = id => document.getElementById(id);
const f4 = x => (x||0).toFixed(3);
const f2 = x => (x||0).toFixed(2);
function dur(s){ if(s==null) return "—"; if(s<0) return "closed"; const m=Math.floor(s/60), r=s%60; return m+"m "+String(r).padStart(2,"0")+"s"; }
async function tick(){
  let d; try{ d = await (await fetch('/api/state')).json(); }catch(e){ $('foot').textContent='disconnected — retrying'; return; }
  const mb=$('mode'); mb.textContent=d.mode; mb.className='badge '+(d.mode==='LIVE'?'live':'dry');
  $('stats').innerHTML = [
    ['monitored',d.monitored],['armed',d.armed],['tick-blocked',d.blocked_tick],
    ['pre-game',d.pregame],['orders',d.orders_placed],['positions',d.open_positions],
    ['size c/wc',`${d.crypto_size}/${d.worldcup_size}`],
    ['feed',d.data_source],['uptime',dur(d.uptime_secs)]
  ].map(([k,v])=>`${k} <b>${v}</b>`).join('');
  $('mcount').textContent=d.markets.length; $('ocount').textContent=d.orders.length; $('pcount').textContent=d.positions.length;

  $('markets').innerHTML = d.markets.length ? d.markets.map(m=>{
    const st = m.triggered    ? `<span class="pill trg">TRIGGERED</span>`
             : m.armed        ? `<span class="pill arm">ARMED</span>`
             : m.pregame       ? `<span class="pill pre" title="game hasn't kicked off — won't place until it's live/ended">PRE-GAME ${dur(m.starts_in)}</span>`
             : m.blocked_tick  ? `<span class="pill blk" title="ask is 0.999/1.00 but tick can't place 0.999 exactly">TICK&nbsp;0.01</span>`
             : `<span class="idle">·</span>`;
    const askCls = m.armed ? 'num ask-hi' : 'num';
    return `<tr><td>${m.label}</td><td>${m.outcome}</td><td class="num">${f4(m.best_bid)}</td>
      <td class="${askCls}">${f4(m.best_ask)}</td><td class="num idle">${m.tick||'—'}</td><td>${st}</td>
      <td class="num">${dur(m.secs_left)}</td><td class="idle">${m.slug}</td></tr>`;
  }).join('') : `<tr><td colspan="8" class="empty">discovering markets…</td></tr>`;

  $('orders').innerHTML = d.orders.length ? d.orders.map(o=>
    `<tr><td>${o.market_label}</td><td>${o.outcome}</td><td class="num">${f4(o.price)}</td>
      <td class="num">${o.size}</td><td class="num">${o.filled}</td>
      <td><span class="pill ${o.status==='DryRun'?'arm':o.status==='Cancelled'?'blk':'trg'}">${o.status}</span></td>
      <td class="idle">${o.placed_at}</td></tr>`).join('')
    : `<tr><td colspan="7" class="empty">no orders yet — waiting for a 99.9c / 100c ask</td></tr>`;

  $('positions').innerHTML = d.positions.length ? d.positions.map(p=>
    `<tr><td>${p.title}</td><td>${p.outcome}</td><td class="num">${f2(p.size)}</td>
      <td class="num">${f4(p.avg_price)}</td><td class="num">${f4(p.cur_price)}</td>
      <td class="num ${p.cash_pnl>=0?'pos':'neg'}">${p.cash_pnl>=0?'+':''}${f2(p.cash_pnl)}</td></tr>`).join('')
    : `<tr><td colspan="6" class="empty">no open positions</td></tr>`;

  $('foot').textContent = 'updated '+new Date().toLocaleTimeString();
}
tick(); setInterval(tick, 1000);
</script>
</body>
</html>"#;
