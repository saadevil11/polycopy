//! Copytrader dashboard: an axum server with `GET /` (self-contained HTML that
//! polls) and `GET /api/state` (JSON: status, live positions, recent copy
//! trades, portfolio value/PnL). Reads positions live from the executor and
//! trades from the store.
use std::sync::Arc;

use axum::{extract::State, response::Html, routing::get, Json, Router};
use chrono::{DateTime, Utc};
use serde::Serialize;
use tracing::info;

use crate::clob::executor::{Executor, PositionInfo};
use crate::config::Config;
use crate::copytrader::store::{Store, TradeLog};

#[derive(Clone)]
struct Ctx {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    started: DateTime<Utc>,
}

pub async fn serve(cfg: Config, exec: Arc<Executor>, store: Arc<Store>, started: DateTime<Utc>) -> anyhow::Result<()> {
    let port = cfg.dashboard_port;
    let ctx = Ctx { cfg, exec, store, started };
    let app = Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
        .with_state(ctx);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    info!("📊 copytrader dashboard at http://localhost:{port}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

#[derive(Serialize)]
struct Snap {
    target: String,
    mode: String,
    data_source: String,
    dry_run: bool,
    uptime_secs: i64,
    portfolio_value: f64,
    open_pnl: f64,
    position_count: usize,
    copied_markets: usize,
    positions: Vec<PositionInfo>,
    trades: Vec<TradeLog>,
}

async fn api_state(State(ctx): State<Ctx>) -> Json<Snap> {
    let positions = ctx.exec.positions().await;
    let portfolio_value: f64 = positions.iter().map(|p| p.value).sum();
    let open_pnl: f64 = positions.iter().map(|p| p.pnl).sum();
    let mode = if ctx.cfg.brownfox_enabled {
        "brownfox"
    } else if ctx.cfg.weather_enabled {
        "weather"
    } else {
        "copy"
    };
    Json(Snap {
        target: ctx.cfg.target_trader.clone(),
        mode: mode.into(),
        data_source: format!("{:?}", ctx.cfg.data_source).to_lowercase(),
        dry_run: ctx.cfg.dry_run,
        uptime_secs: (Utc::now() - ctx.started).num_seconds(),
        position_count: positions.len(),
        copied_markets: ctx.store.copied_count(),
        portfolio_value,
        open_pnl,
        positions,
        trades: ctx.store.recent_trades(),
    })
}

const INDEX_HTML: &str = r#"<!DOCTYPE html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>Copytrader</title>
<style>
:root{--bg:#0b0f1a;--card:#141b2e;--line:#232c44;--txt:#e7ecf5;--mut:#8a96b3;--grn:#27d3a2;--red:#ff5d73;--acc:#6d8bff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px -apple-system,Segoe UI,Roboto,Arial}
.wrap{max-width:1200px;margin:0 auto;padding:20px}
header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:11px}.logo{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,#6d8bff,#9b7bff);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff}
.pills{display:flex;gap:8px;flex-wrap:wrap;font-size:12px}.pill{background:var(--card);border:1px solid var(--line);padding:6px 11px;border-radius:999px;color:var(--mut)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--grn);display:inline-block;margin-right:6px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}@media(max-width:700px){.grid{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.kpi .l{font-size:11px;color:var(--mut);text-transform:uppercase}.kpi .v{font-size:23px;font-weight:700;margin-top:5px}
.pos{color:var(--grn)}.neg{color:var(--red)}
.sec{background:var(--card);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;overflow:hidden}
.sec h2{font-size:14px;margin:0;padding:13px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.sec h2 .c{color:var(--mut);font-weight:500;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th{font-size:11px;text-transform:uppercase;color:#5d6885;text-align:left;padding:9px 16px;border-bottom:1px solid var(--line)}
td{padding:11px 16px;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:none}.right{text-align:right}.mut{color:var(--mut)}
.badge{padding:3px 8px;border-radius:7px;font-size:11px;font-weight:700}.buy{background:#27d3a21f;color:var(--grn)}.sell{background:#ff5d731f;color:var(--red)}
.empty{padding:30px;text-align:center;color:var(--mut)}
</style></head><body><div class="wrap">
<header><div class="brand"><div class="logo">P</div><div><div style="font-weight:600;font-size:17px">Copytrader (Rust)</div><div class="mut" style="font-size:11.5px" id="target">—</div></div></div>
<div class="pills"><span class="pill"><span class="dot"></span><span id="mode">—</span></span><span class="pill" id="src">—</span><span class="pill" id="dry">—</span><span class="pill" id="up">—</span></div></header>
<div class="grid" id="kpis"></div>
<div class="sec"><h2>Open Positions <span class="c" id="pc"></span></h2><div id="pos"><div class="empty">loading…</div></div></div>
<div class="sec"><h2>Recent Copy Trades <span class="c" id="tc"></span></h2><div id="tr"><div class="empty">loading…</div></div></div>
</div>
<script>
const $=s=>document.querySelector(s),money=n=>(n<0?'-$':'$')+Math.abs(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}),num=n=>Number(n||0),cls=n=>num(n)>0?'pos':num(n)<0?'neg':'';
function badge(s){return (s||'').toUpperCase()==='SELL'?'<span class="badge sell">SELL</span>':'<span class="badge buy">BUY</span>';}
function tshort(t){if(!t)return'—';let d=new Date((t+'').replace(' ','T'));return isNaN(d)?t:d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
async function tick(){
 let s;try{s=await (await fetch('/api/state',{cache:'no-store'})).json();}catch(e){return;}
 $('#target').textContent='Target '+(s.target||'—');$('#mode').textContent=s.mode+' mode';$('#src').textContent=s.data_source;
 $('#dry').textContent=s.dry_run?'DRY-RUN':'LIVE';let u=s.uptime_secs|0;$('#up').textContent='up '+(u/3600|0)+'h '+((u%3600)/60|0)+'m';
 $('#kpis').innerHTML=[['Portfolio',money(s.portfolio_value),''],['Open P&L',money(s.open_pnl),cls(s.open_pnl)],['Open Positions',s.position_count,''],['Copied Markets',s.copied_markets,'']].map(k=>`<div class="kpi"><div class="l">${k[0]}</div><div class="v ${k[2]}">${k[1]}</div></div>`).join('');
 $('#pc').textContent=s.positions.length?s.positions.length+' open':'';
 $('#pos').innerHTML=s.positions.length?`<table><thead><tr><th>Market</th><th>Outcome</th><th class="right">Shares</th><th class="right">Avg</th><th class="right">Now</th><th class="right">Value</th><th class="right">P&L</th></tr></thead><tbody>${s.positions.map(p=>`<tr><td>${p.title||'—'}</td><td class="mut">${p.outcome||''}</td><td class="right">${num(p.size).toFixed(2)}</td><td class="right mut">${num(p.avg_price).toFixed(3)}</td><td class="right">${num(p.cur_price).toFixed(3)}</td><td class="right">${money(p.value)}</td><td class="right ${cls(p.pnl)}">${money(p.pnl)}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">No open positions.</div>';
 $('#tc').textContent=s.trades.length?s.trades.length+' shown':'';
 $('#tr').innerHTML=s.trades.length?`<table><thead><tr><th>Time</th><th>Market</th><th>Side</th><th class="right">Shares</th><th class="right">Price</th><th>Status</th></tr></thead><tbody>${s.trades.map(t=>`<tr><td class="mut">${tshort(t.time)}</td><td>${t.title||t.market||'—'}</td><td>${badge(t.side)}</td><td class="right">${num(t.size).toFixed(2)}</td><td class="right mut">${num(t.price).toFixed(3)}</td><td class="mut">${t.status}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">No copy trades yet.</div>';
}
tick();setInterval(tick,5000);
</script></body></html>"#;
