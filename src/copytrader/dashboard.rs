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
    cost_basis: f64,
    open_pnl: f64,
    position_count: usize,
    copied_markets: usize,
    positions: Vec<PositionInfo>,
    trades: Vec<TradeLog>,
}

async fn api_state(State(ctx): State<Ctx>) -> Json<Snap> {
    let positions = ctx.exec.positions().await;
    let portfolio_value: f64 = positions.iter().map(|p| p.value).sum();
    let cost_basis: f64 = positions.iter().map(|p| p.cost).sum();
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
        cost_basis,
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

fn status_label(status: &str, order_id: &str) -> String {
    match status {
        // A claim with no order id yet = placement in flight (no real order on the book).
        "ACTIVE" if order_id.is_empty() => "placing",
        // dryrun-… = DRY_RUN simulated; there is NO real order on Polymarket.
        "ACTIVE" if order_id.starts_with("dryrun-") => "dry-run",
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
                .map(|(s, b, oid)| (status_label(s, oid), *b))
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
            // demogui binary invariant: both asks present and sum ≈1 (≤1.10). A 1.000 side
            // (settling) is still a valid book — we don't require each ask < 1.0.
            c.valid = c.up_ask > 0.0 && c.down_ask > 0.0 && (c.up_ask + c.down_ask) <= 1.10;
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

const INDEX_HTML: &str = r#"<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>polybotshadow — copytrader</title>
<style>
:root{
--bg:#08090c;--panel:#0e1014;--panel2:#0b0d11;--head:#101318;
--line:#1a1d24;--line2:#232830;--txt:#e8eaee;--mut:#79828f;--dim:#4d5563;
--grn:#00c76e;--grn-d:#0a1f18;--red:#ff4757;--red-d:#221014;--amb:#e3a13c;--acc:#3d8bfd;
--mono:ui-monospace,"SF Mono","Cascadia Mono","Segoe UI Mono",Menlo,Consolas,monospace;
--ui:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:13px/1.45 var(--ui);
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.app{max-width:1320px;margin:0 auto;padding:0 22px 40px}

/* ── top bar ─────────────────────────────────────────────── */
.top{display:flex;align-items:center;justify-content:space-between;gap:20px;
padding:16px 0 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.id{display:flex;align-items:baseline;gap:11px}
.mark{font-family:var(--mono);font-size:15px;font-weight:600;letter-spacing:-.02em;color:var(--txt)}
.mark b{color:var(--grn);font-weight:600}
.ver{font-size:10.5px;color:var(--dim);letter-spacing:.06em;text-transform:uppercase}
.stat{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.s{display:flex;align-items:center;gap:7px;font-size:10.5px;letter-spacing:.07em;
text-transform:uppercase;color:var(--dim)}
.s b{font-family:var(--mono);font-size:11.5px;font-weight:600;letter-spacing:0;
text-transform:none;color:var(--mut)}
.s b.on{color:var(--grn)}.s b.warn{color:var(--amb)}
.led{width:6px;height:6px;border-radius:50%;background:var(--grn);
box-shadow:0 0 0 3px rgba(0,199,110,.13)}
.led.idle{background:var(--dim);box-shadow:none}

/* ── target strip ────────────────────────────────────────── */
.tgt{display:flex;align-items:center;gap:10px;padding:11px 0 16px;
font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--dim)}
.tgt span{font-family:var(--mono);font-size:12px;letter-spacing:0;
text-transform:none;color:var(--mut)}

/* ── kpi strip ───────────────────────────────────────────── */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:3px;overflow:hidden;margin-bottom:22px}
.kpi{background:var(--panel);padding:15px 17px 16px;position:relative}
.kpi .l{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--dim)}
.kpi .v{font-family:var(--mono);font-variant-numeric:tabular-nums;
font-size:25px;font-weight:600;letter-spacing:-.03em;margin-top:9px;line-height:1}
.kpi .sub{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:7px}
.kpi.acc{box-shadow:inset 2px 0 0 var(--grn)}
.pos{color:var(--grn)}.neg{color:var(--red)}

/* ── panels ──────────────────────────────────────────────── */
.panel{border:1px solid var(--line);border-radius:3px;background:var(--panel);
margin-bottom:18px;overflow:hidden}
.ph{display:flex;align-items:center;justify-content:space-between;
padding:11px 16px;background:var(--head);border-bottom:1px solid var(--line)}
.ph h2{margin:0;font-size:11px;font-weight:600;letter-spacing:.09em;
text-transform:uppercase;color:var(--mut)}
.ph .c{font-family:var(--mono);font-size:10.5px;color:var(--dim)}

/* ── tables ──────────────────────────────────────────────── */
table{width:100%;border-collapse:collapse}
th{font-size:9.5px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;
color:var(--dim);text-align:left;padding:9px 16px;background:var(--panel2);
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:rgba(255,255,255,.016)}
.r{text-align:right}.mut{color:var(--mut)}
.mkt{max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.oc{font-family:var(--mono);font-size:11px;color:var(--mut);
border:1px solid var(--line2);border-radius:2px;padding:1px 6px}

/* ── badges ──────────────────────────────────────────────── */
.badge{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.04em;
padding:2px 7px;border-radius:2px;display:inline-block}
.buy{background:var(--grn-d);color:var(--grn);box-shadow:inset 0 0 0 1px rgba(0,199,110,.22)}
.sell{background:var(--red-d);color:var(--red);box-shadow:inset 0 0 0 1px rgba(255,71,87,.22)}
.st{font-family:var(--mono);font-size:10px;color:var(--mut)}
.st i{font-style:normal;color:var(--grn)}

.empty{padding:34px 16px;text-align:center;color:var(--dim);font-size:12px}

/* ── scanner cards ───────────────────────────────────────── */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(204px,1fr));
gap:9px;padding:13px}
.card{background:var(--panel2);border:1px solid var(--line);border-radius:3px;padding:12px 13px 11px}
.card.hot{background:linear-gradient(180deg,rgba(0,199,110,.06),var(--panel2) 65%);
border-color:rgba(0,199,110,.42)}
.card.bad{opacity:.38}
.ch{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:11px}
.coin{font-family:var(--mono);font-size:13px;font-weight:700;letter-spacing:.02em}
.win{font-family:var(--mono);font-size:10px;color:var(--dim)}
.row{display:flex;align-items:center;gap:8px;padding:3px 0}
.side{font-size:9.5px;letter-spacing:.09em;color:var(--dim);width:34px}
.bar{flex:1;height:3px;background:var(--line2);border-radius:1px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--dim);transition:width .3s ease}
.bar i.t{background:var(--grn)}
.ask{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12px;
font-weight:600;width:42px;text-align:right;color:var(--mut)}
.ask.t{color:var(--grn)}
.ostat{font-family:var(--mono);font-size:9px;font-weight:700;letter-spacing:.03em;
padding:1px 5px;border-radius:2px;text-transform:uppercase}
.s-resting{background:rgba(61,139,253,.13);color:var(--acc)}
.s-holding{background:var(--grn-d);color:var(--grn)}
.s-exited,.s-cancelled{background:var(--red-d);color:var(--red)}
.s-placing{background:rgba(121,130,143,.13);color:var(--mut)}
.s-dry-run{background:rgba(227,161,60,.13);color:var(--amb)}

.foot{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;
padding-top:6px;font-family:var(--mono);font-size:10.5px;color:var(--dim)}
@media(max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.app{padding:0 14px 30px}}
</style></head><body><div class="app">

<header class="top">
  <div class="id">
    <span class="mark">poly<b>bot</b>shadow</span>
    <span class="ver">Polymarket Copytrader &middot; Rust &middot; CLOB v2</span>
  </div>
  <div class="stat">
    <span class="s"><i class="led" id="led"></i><b id="dry" class="on">&mdash;</b></span>
    <span class="s">Mode <b id="mode">&mdash;</b></span>
    <span class="s">Feed <b id="src">&mdash;</b></span>
    <span class="s">Uptime <b id="up">&mdash;</b></span>
  </div>
</header>

<div class="tgt" id="tgtline">Copying <span id="target">&mdash;</span></div>

<section class="kpis" id="kpis"></section>

<section class="panel" id="scanSec" style="display:none">
  <div class="ph"><h2>Live Order Books</h2><span class="c" id="scanc"></span></div>
  <div id="cards" class="cards"><div class="empty">Waiting for markets&hellip;</div></div>
</section>

<section class="panel">
  <div class="ph"><h2>Open Positions</h2><span class="c" id="pc"></span></div>
  <div id="pos"><div class="empty">Loading&hellip;</div></div>
</section>

<section class="panel">
  <div class="ph"><h2 id="trh">Copied Trades</h2><span class="c" id="tc"></span></div>
  <div id="tr"><div class="empty">Loading&hellip;</div></div>
</section>

<div class="foot">
  <span>Signing validated byte-for-byte against py-clob-client-v2</span>
  <span id="clock">&mdash;</span>
</div>
</div>
<script>
const $=s=>document.querySelector(s),
num=n=>Number(n||0),
cls=n=>num(n)>0?'pos':num(n)<0?'neg':'',
money=n=>(n<0?'-$':'$')+Math.abs(num(n)).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}),
sign=n=>(num(n)>0?'+':'')+money(n);
function badge(s){return (s||'').toUpperCase()==='SELL'
 ?'<span class="badge sell">SELL</span>':'<span class="badge buy">BUY</span>';}
function tshort(t){if(!t)return'&mdash;';
 // The bot records trade times as UTC with no zone marker ("YYYY-MM-DD HH:MM:SS").
 // JS would read a bare date-time as LOCAL, showing every fill off by the host's
 // UTC offset, so tag it explicitly before parsing and render in the viewer's zone.
 let s=(t+'').trim().replace(' ','T');
 if(!/([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) s+='Z';
 let d=new Date(s);
 return isNaN(d)?t:d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});}
function hms(u){u=u|0;const h=u/3600|0,m=(u%3600)/60|0;return h?h+'h '+m+'m':m+'m';}
function esc(s){return (s==null?'':''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

async function tick(){
 let s;try{s=await (await fetch('/api/state',{cache:'no-store'})).json();}
 catch(e){$('#led').className='led idle';return;}
 $('#led').className='led';
 const selfd=/scanner/i.test(s.mode||'')||!s.target;
 // Truncate the address the way wallets/explorers do: readable, and safe to leave
 // on screen while screen-sharing or screenshotting. Full value on hover.
 const short=a=>a&&a.length>14?a.slice(0,6)+'…'+a.slice(-4):a;
 $('#tgtline').innerHTML=selfd?'Self-driven &middot; <span>no target trader</span>'
   :'Copying <span title="'+esc(s.target)+'">'+esc(short(s.target))+'</span>';
 $('#trh').textContent=selfd?'Executed Trades':'Copied Trades';
 $('#mode').textContent=s.mode||'—';
 $('#src').textContent=(s.data_source||'—').toUpperCase();
 const d=$('#dry');d.textContent=s.dry_run?'DRY-RUN':'LIVE';d.className=s.dry_run?'warn':'on';
 $('#up').textContent=hms(s.uptime_secs);
 const pnl=num(s.open_pnl),inv=num(s.portfolio_value)-pnl;
 $('#kpis').innerHTML=[
  ['Portfolio Value',money(s.portfolio_value),'',inv>0?'cost basis '+money(inv):'','acc'],
  ['Unrealised P&amp;L',sign(pnl),cls(pnl),inv>0?(pnl/inv*100).toFixed(2)+'% return':'',''],
  ['Open Positions',s.position_count,'','across live markets',''],
  [selfd?'Markets Traded':'Markets Copied',s.copied_markets,'','since start','']
 ].map(k=>`<div class="kpi ${k[4]}"><div class="l">${k[0]}</div><div class="v ${k[2]}">${k[1]}</div><div class="sub">${k[3]}</div></div>`).join('');

 $('#pc').textContent=s.positions.length?s.positions.length+' open':'';
 $('#pos').innerHTML=s.positions.length?
  `<table><thead><tr><th>Market</th><th>Outcome</th><th class="r">Shares</th><th class="r">Avg</th>
  <th class="r">Mark</th><th class="r">Value</th><th class="r">P&amp;L</th></tr></thead><tbody>`+
  s.positions.map(p=>`<tr><td class="mkt">${esc(p.title)||'—'}</td>
  <td><span class="oc">${esc(p.outcome)}</span></td>
  <td class="r num">${num(p.size).toFixed(2)}</td>
  <td class="r num mut">${num(p.avg_price).toFixed(3)}</td>
  <td class="r num">${num(p.cur_price).toFixed(3)}</td>
  <td class="r num">${money(p.value)}</td>
  <td class="r num ${cls(p.pnl)}">${sign(p.pnl)}</td></tr>`).join('')+
  '</tbody></table>':'<div class="empty">No open positions.</div>';

 $('#tc').textContent=s.trades.length?s.trades.length+' recent':'';
 $('#tr').innerHTML=s.trades.length?
  `<table><thead><tr><th>Time</th><th>Market</th><th>Side</th><th class="r">Shares</th>
  <th class="r">Price</th><th>Status</th></tr></thead><tbody>`+
  s.trades.map(t=>`<tr><td class="num mut">${tshort(t.time)}</td>
  <td class="mkt">${esc(t.title||t.market)||'—'}</td><td>${badge(t.side)}</td>
  <td class="r num">${num(t.size).toFixed(2)}</td>
  <td class="r num mut">${num(t.price).toFixed(3)}</td>
  <td class="st">${esc(t.status)}</td></tr>`).join('')+
  '</tbody></table>':'<div class="empty">No trades yet.</div>';
 $('#clock').textContent=new Date().toLocaleTimeString(undefined,{hour12:false})+' local';
}
function ostat(s){return s?`<span class="ostat s-${esc(s)}">${esc(s)}</span>`:'';}
async function scanTick(){
 let s;try{s=await (await fetch('/api/scanner',{cache:'no-store'})).json();}catch(e){return;}
 if(!s||!s.enabled){$('#scanSec').style.display='none';return;}
 $('#scanSec').style.display='';
 $('#scanc').textContent=(s.cards.length||0)+' live';
 const trig=num(s.trigger)||0.99;
 const row=(side,ask,st)=>{const t=ask>=trig&&ask<1;
  return `<div class="row"><span class="side">${side}</span>
  <div class="bar"><i class="${t?'t':''}" style="width:${Math.min(100,Math.max(0,ask*100))}%"></i></div>
  <span class="ask ${t?'t':''}">${num(ask).toFixed(3)}</span>${ostat(st)}</div>`;};
 $('#cards').innerHTML=s.cards.length?s.cards.map(c=>{
   const u=num(c.up_ask),d=num(c.down_ask);
   const hot=c.valid&&((u>=trig&&u<1)||(d>=trig&&d<1));
   const t=c.end_ts?new Date(c.end_ts*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',hour12:false}):'';
   return `<div class="${hot?'card hot':(c.valid?'card':'card bad')}">
   <div class="ch"><span class="coin">${esc(c.coin)}</span>
   <span class="win">${t}${c.valid?'':' BAD BOOK'}</span></div>
   ${row('UP',u,c.up_status)}${row('DOWN',d,c.down_status)}</div>`;
 }).join(''):'<div class="empty">No live markets in this window.</div>';
}
tick();setInterval(tick,5000);
scanTick();setInterval(scanTick,1500);
</script></body></html>"#;
