//! Copytrader dashboard: an axum server with `GET /` (self-contained HTML that
//! polls) and `GET /api/state` (JSON: status, live positions, recent copy
//! trades, portfolio value/PnL). Reads positions live from the executor and
//! trades from the store.
use std::collections::HashMap;
use std::sync::Arc;

use axum::{extract::State, response::Html, routing::get, Json, Router};
use chrono::{DateTime, Utc};
use serde::Serialize;
use tracing::info;

use crate::clob::executor::{Executor, PositionInfo};
use crate::config::Config;
use crate::copytrader::scanner::ScannerView;
use crate::copytrader::store::{Store, TradeLog};

#[derive(Clone)]
struct Ctx {
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    started: DateTime<Utc>,
    scanner: Option<Arc<ScannerView>>,
}

pub async fn serve(
    cfg: Config,
    exec: Arc<Executor>,
    store: Arc<Store>,
    started: DateTime<Utc>,
    scanner: Option<Arc<ScannerView>>,
) -> anyhow::Result<()> {
    let port = cfg.dashboard_port;
    let ctx = Ctx { cfg, exec, store, started, scanner };
    let app = Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
        .route("/api/scanner", get(api_scanner))
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
    let mode = if ctx.cfg.ninetynine_enabled {
        "99c"
    } else if ctx.cfg.sell_with_target_enabled {
        "sell-with-target"
    } else if ctx.cfg.brownfox_enabled {
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

// ── 99c-scanner live view: one card per 5m market (book + our order status) ──
#[derive(Serialize)]
struct ScanCard {
    coin: String,
    end_ts: u64,
    slug: String,
    up_ask: f64,
    down_ask: f64,
    valid: bool,
    up_status: String,   // "" | resting | holding | exited | cancelled
    down_status: String,
    up_buy: f64,
    down_buy: f64,
}

#[derive(Serialize)]
struct ScanSnap {
    enabled: bool,
    trigger: f64,
    cards: Vec<ScanCard>,
}

fn status_label(s: &str) -> String {
    match s {
        "ACTIVE" => "resting",
        "FILLED" => "holding",
        "EXITED" => "exited",
        "CANCELLED" => "cancelled",
        _ => "",
    }
    .to_string()
}

async fn api_scanner(State(ctx): State<Ctx>) -> Json<ScanSnap> {
    let trigger = ctx.cfg.scanner_trigger_ask;
    let view = match &ctx.scanner {
        Some(v) => v,
        None => return Json(ScanSnap { enabled: false, trigger, cards: vec![] }),
    };
    let orders = ctx.store.scan_all(); // token -> (status, buy_price)
    let mut by_slug: HashMap<String, ScanCard> = HashMap::new();
    {
        let markets = view.markets.lock().unwrap();
        let asks = view.asks.lock().unwrap();
        for (token, info) in markets.iter() {
            let ask = asks.get(token).copied().unwrap_or(0.0);
            let (status, buy) = orders
                .get(token)
                .map(|(s, b)| (status_label(s), *b))
                .unwrap_or((String::new(), 0.0));
            let card = by_slug.entry(info.market.clone()).or_insert_with(|| ScanCard {
                coin: info.asset.to_uppercase(),
                end_ts: info.end_ts,
                slug: info.market.clone(),
                up_ask: 0.0,
                down_ask: 0.0,
                valid: false,
                up_status: String::new(),
                down_status: String::new(),
                up_buy: 0.0,
                down_buy: 0.0,
            });
            if info.outcome.eq_ignore_ascii_case("up") {
                card.up_ask = ask;
                card.up_status = status;
                card.up_buy = buy;
            } else {
                card.down_ask = ask;
                card.down_status = status;
                card.down_buy = buy;
            }
        }
    }
    let mut cards: Vec<ScanCard> = by_slug
        .into_values()
        .map(|mut c| {
            c.valid = c.up_ask > 0.0 && c.up_ask < 1.0 && c.down_ask > 0.0 && c.down_ask < 1.0 && (c.up_ask + c.down_ask) <= 1.10;
            c
        })
        .collect();
    // closest-to-trigger first
    cards.sort_by(|a, b| {
        b.up_ask
            .max(b.down_ask)
            .partial_cmp(&a.up_ask.max(a.down_ask))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    Json(ScanSnap { enabled: true, trigger, cards })
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
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:12px;padding:14px 16px}
.card{background:#0e1424;border:1px solid var(--line);border-radius:12px;padding:12px}
.card.hot{border-color:var(--grn);box-shadow:0 0 0 1px var(--grn) inset}
.card.bad{opacity:.5}
.card .ch{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.card .coin{font-weight:800;font-size:15px}.card .win{font-size:11px;color:var(--mut)}
.row{display:flex;align-items:center;padding:4px 0;font-size:13px}
.side{color:var(--mut);font-size:11px;width:42px}
.bar{flex:1;height:5px;background:#1b2236;border-radius:3px;margin:0 8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
.ask{font-variant-numeric:tabular-nums;font-weight:700;width:48px;text-align:right}.ask.t{color:var(--grn)}
.ostat{font-size:10px;padding:2px 6px;border-radius:6px;font-weight:700;margin-left:7px}
.s-resting{background:#6d8bff22;color:var(--acc)}.s-holding{background:#27d3a222;color:var(--grn)}.s-exited,.s-cancelled{background:#ff5d7322;color:var(--red)}
</style></head><body><div class="wrap">
<header><div class="brand"><div class="logo">P</div><div><div style="font-weight:600;font-size:17px">Copytrader (Rust)</div><div class="mut" style="font-size:11.5px" id="target">—</div></div></div>
<div class="pills"><span class="pill"><span class="dot"></span><span id="mode">—</span></span><span class="pill" id="src">—</span><span class="pill" id="dry">—</span><span class="pill" id="up">—</span></div></header>
<div class="grid" id="kpis"></div>
<div class="sec" id="scanSec" style="display:none"><h2>5-min Markets (live book) <span class="c" id="scanc"></span></h2><div id="cards" class="cards"><div class="empty">loading…</div></div></div>
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
function ostat(s){return s?`<span class="ostat s-${s}">${s}</span>`:'';}
async function scanTick(){
 let s;try{s=await (await fetch('/api/scanner',{cache:'no-store'})).json();}catch(e){return;}
 if(!s||!s.enabled){$('#scanSec').style.display='none';return;}
 $('#scanSec').style.display='';$('#scanc').textContent=(s.cards.length||0)+' live';
 const trig=num(s.trigger)||0.99;
 const row=(side,ask,st)=>{const tr=ask>=trig&&ask<1;return `<div class="row"><span class="side">${side}</span><div class="bar"><i style="width:${Math.min(100,Math.max(0,ask*100))}%"></i></div><span class="ask ${tr?'t':''}">${num(ask).toFixed(3)}</span>${ostat(st)}</div>`;};
 $('#cards').innerHTML=s.cards.length? s.cards.map(c=>{
   const u=num(c.up_ask),d=num(c.down_ask);
   const hot=c.valid&&((u>=trig&&u<1)||(d>=trig&&d<1));
   const t=c.end_ts?new Date(c.end_ts*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'';
   const cls=hot?'card hot':(c.valid?'card':'card bad');
   return `<div class="${cls}"><div class="ch"><span class="coin">${c.coin}</span><span class="win">${t}${c.valid?'':' ⚠ bad book'}</span></div>${row('UP',u,c.up_status)}${row('DOWN',d,c.down_status)}</div>`;
 }).join(''):'<div class="empty">No live 5-min markets right now.</div>';
}
scanTick();setInterval(scanTick,1500);
</script></body></html>"#;
