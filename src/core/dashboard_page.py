"""
Self-contained HTML dashboard served by BotAPI at '/'.

Single-page app (embedded CSS + JS) that polls the bot's JSON API
(/api/status, /api/metrics, /api/positions, /api/trades) and renders a live
monitoring view: portfolio KPIs, open positions with P&L, and per-position
trade history. No build step, no external assets.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Polycopy Monitor</title>
<style>
:root{
  --bg:#0b0f1a; --bg2:#0f1424; --card:#141b2e; --card2:#1a2238;
  --line:#232c44; --txt:#e7ecf5; --mut:#8a96b3; --mut2:#5d6885;
  --acc:#6d8bff; --acc2:#9b7bff; --grn:#27d3a2; --red:#ff5d73; --yel:#ffc857;
  --shadow:0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#19204022,transparent),
  radial-gradient(1000px 500px at -10% 0%,#3a1f6022,transparent),var(--bg);
  color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100vh}
a{color:var(--acc);text-decoration:none}
.wrap{max-width:1280px;margin:0 auto;padding:22px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--acc),var(--acc2));
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:18px;color:#fff;box-shadow:var(--shadow)}
.brand h1{font-size:18px;margin:0;letter-spacing:.2px}
.brand .sub{color:var(--mut);font-size:12px}
.statusbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);
  padding:7px 12px;border-radius:999px;font-size:12.5px;color:var(--mut)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--mut2)}
.dot.on{background:var(--grn);box-shadow:0 0 0 4px #27d3a233;animation:pulse 2s infinite}
.dot.off{background:var(--red);box-shadow:0 0 0 4px #ff5d7333}
@keyframes pulse{0%{box-shadow:0 0 0 0 #27d3a255}70%{box-shadow:0 0 0 6px #27d3a200}100%{box-shadow:0 0 0 0 #27d3a200}}
.btn{cursor:pointer;background:var(--card);border:1px solid var(--line);color:var(--txt);
  padding:8px 13px;border-radius:10px;font-size:12.5px;transition:.15s}
.btn:hover{border-color:var(--acc);color:#fff}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
@media(max-width:920px){.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.grid{grid-template-columns:1fr}}
.kpi{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
  border-radius:16px;padding:16px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::after{content:"";position:absolute;inset:0 0 auto 0;height:3px;
  background:linear-gradient(90deg,var(--acc),var(--acc2));opacity:.85}
.kpi .lbl{color:var(--mut);font-size:12px;letter-spacing:.3px;text-transform:uppercase}
.kpi .val{font-size:25px;font-weight:750;margin-top:6px;letter-spacing:.3px}
.kpi .sub{font-size:12px;color:var(--mut);margin-top:3px}
.pos{color:var(--grn)} .neg{color:var(--red)} .neu{color:var(--txt)}
.section{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
  border-radius:16px;box-shadow:var(--shadow);margin-bottom:22px;overflow:hidden}
.section h2{font-size:14.5px;margin:0;padding:15px 18px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between}
.section h2 .cnt{color:var(--mut);font-weight:500;font-size:12.5px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut2);text-align:left;
  padding:11px 16px;border-bottom:1px solid var(--line);font-weight:600;white-space:nowrap}
td{padding:13px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:none}
tbody tr{transition:.12s}
tbody.click tr{cursor:pointer}
tbody.click tr:hover{background:var(--card2)}
.mkt{font-weight:600;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mut{color:var(--mut)} .mono{font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 9px;border-radius:7px;font-size:11.5px;font-weight:700;letter-spacing:.3px}
.b-buy{background:#27d3a21f;color:var(--grn)} .b-sell{background:#ff5d731f;color:var(--red)}
.b-exec{background:#27d3a21f;color:var(--grn)} .b-fail{background:#ff5d731f;color:var(--red)}
.b-skip{background:#8a96b31f;color:var(--mut)} .b-part{background:#ffc8571f;color:var(--yel)}
.right{text-align:right} .center{text-align:center}
.trow{background:#0c1120}
.trow td{padding:0}
.thist{padding:6px 16px 12px 16px}
.thist .ti{display:flex;gap:14px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font-size:12.5px}
.thist .ti:last-child{border-bottom:none}
.empty{padding:34px;text-align:center;color:var(--mut)}
.spark{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;vertical-align:middle}
.bar{height:6px;border-radius:4px;background:var(--line);overflow:hidden;width:90px;display:inline-block;vertical-align:middle}
.bar > i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2))}
footer{color:var(--mut2);text-align:center;font-size:12px;padding:8px 0 26px}
.flash{animation:flash .8s}
@keyframes flash{from{background:#6d8bff22}to{background:transparent}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">P</div>
      <div>
        <h1>Polycopy Monitor</h1>
        <div class="sub" id="target">—</div>
      </div>
    </div>
    <div class="statusbar">
      <span class="pill"><span class="dot" id="d-run"></span><span id="s-run">connecting…</span></span>
      <span class="pill"><span class="dot" id="d-feed"></span><span id="s-feed">feed</span></span>
      <span class="pill" id="s-up">uptime —</span>
      <span class="pill" id="s-updated">—</span>
      <button class="btn" id="btn-refresh">↻ Refresh</button>
      <button class="btn" id="btn-auto">⏸ Auto</button>
    </div>
  </header>

  <div class="grid" id="kpis"></div>

  <div class="section">
    <h2>Open Positions <span class="cnt" id="pos-cnt"></span></h2>
    <div id="pos-wrap"><div class="empty">Loading positions…</div></div>
  </div>

  <div class="section">
    <h2>Recent Trades <span class="cnt" id="tr-cnt"></span></h2>
    <div id="tr-wrap"><div class="empty">Loading trades…</div></div>
  </div>

  <footer>Polycopy • auto-refresh <span id="ival">10</span>s • <span id="err"></span></footer>
</div>

<script>
const $=s=>document.querySelector(s), money=n=>(n<0?'-$':'$')+Math.abs(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const num=n=>Number(n||0), pc=n=>(n>=0?'+':'')+num(n).toFixed(2), cls=n=>num(n)>0?'pos':num(n)<0?'neg':'neu';
let AUTO=true, IVAL=10, allTrades=[];

function badgeSide(s){s=(s||'').toUpperCase();return s==='SELL'?'<span class="badge b-sell">SELL</span>':'<span class="badge b-buy">BUY</span>';}
function badgeStatus(s){s=(s||'').toLowerCase();
  if(s==='executed')return '<span class="badge b-exec">FILLED</span>';
  if(s==='failed')return '<span class="badge b-fail">FAILED</span>';
  if(s==='skipped')return '<span class="badge b-skip">SKIPPED</span>';
  if(s.indexOf('partial')>-1)return '<span class="badge b-part">PARTIAL</span>';
  return '<span class="badge b-skip">'+(s||'—').toUpperCase()+'</span>';}
function tshort(t){if(!t)return '—';let d=new Date((t+'').replace(' ','T'));if(isNaN(d))return t;
  return d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}

async function getJSON(u){const r=await fetch(u,{cache:'no-store'});const j=await r.json();if(!j.success)throw new Error(j.error||'err');return j.data;}

function renderKPIs(m){
  const cards=[
    ['Total Portfolio',money(num(m.balance)),'cash + open positions','neu'],
    ['Cash (pUSD)',money(num(m.cash_balance)),'available to trade','neu'],
    ['Positions Value',money(num(m.total_position_value)),(m.total_positions||0)+' open','neu'],
    ['Daily P&L',money(num(m.daily_pnl)),'today','pnl:'+m.daily_pnl],
    ['All-Time P&L',money(num(m.total_pnl)),'since start','pnl:'+m.total_pnl],
    ['Win Rate',num(m.success_rate).toFixed(0)+'%',
      '<div class="bar"><i style="width:'+Math.min(100,num(m.success_rate))+'%"></i></div>','neu'],
    ['Trades Today',(m.trades_today||0),(m.total_trades||0)+' all-time','neu'],
    ['Open Positions',(m.total_positions||0),'limit '+(m.max_positions||'—'),'neu'],
  ];
  $('#kpis').innerHTML=cards.map(c=>{
    let cl='neu';if((c[3]||'').startsWith('pnl:'))cl=cls(parseFloat(c[3].slice(4)));
    return `<div class="kpi"><div class="lbl">${c[0]}</div>
      <div class="val ${cl}">${c[1]}</div><div class="sub">${c[2]}</div></div>`;}).join('');
}

function renderPositions(ps){
  $('#pos-cnt').textContent=ps.length?ps.length+' open':'';
  if(!ps.length){$('#pos-wrap').innerHTML='<div class="empty">No open positions.</div>';return;}
  ps.sort((a,b)=>num(b.current_value)-num(a.current_value));
  let rows=ps.map((p,i)=>{
    const pnl=num(p.unrealized_pnl), pnlPct=p.cost_basis>0?(pnl/p.cost_basis*100):0;
    const tid=(p.token_id||'')+'';
    return `<tr data-token="${tid}" data-i="${i}">
      <td><div class="mkt">${p.market_title||'Unknown'}</div>
          <div class="mut" style="font-size:11.5px">${p.outcome||''}</div></td>
      <td>${badgeSide(p.side)}</td>
      <td class="right mono">${num(p.size).toFixed(2)}</td>
      <td class="right mono mut">${num(p.avg_price).toFixed(3)}</td>
      <td class="right mono">${num(p.current_price).toFixed(3)}</td>
      <td class="right mono">${money(num(p.current_value))}</td>
      <td class="right mono ${cls(pnl)}">${pc(pnl)}<div class="mut" style="font-size:11px">${pc(pnlPct)}%</div></td>
    </tr><tr class="trow" id="exp-${i}" style="display:none"><td colspan="7"><div class="thist" id="exph-${i}"></div></td></tr>`;}).join('');
  $('#pos-wrap').innerHTML=`<table><thead><tr>
    <th>Market</th><th>Side</th><th class="right">Shares</th><th class="right">Avg</th>
    <th class="right">Now</th><th class="right">Value</th><th class="right">Unreal. P&L</th>
    </tr></thead><tbody class="click">${rows}</tbody></table>`;
  $('#pos-wrap').querySelectorAll('tbody.click tr[data-token]').forEach(tr=>{
    tr.onclick=()=>{const i=tr.dataset.i, ex=$('#exp-'+i);
      const open=ex.style.display!=='none';ex.style.display=open?'none':'table-row';
      if(!open)$('#exph-'+i).innerHTML=tradesForToken(tr.dataset.token);};
  });
}

function tradesForToken(tid){
  const t=allTrades.filter(x=>(x.token_id+'')===(tid+''));
  if(!t.length)return '<div class="mut">No recorded copy trades for this position.</div>';
  return t.map(x=>`<div class="ti">
    <span class="spark" style="background:${(x.side||'').toUpperCase()==='SELL'?'var(--red)':'var(--grn)'}"></span>
    <span style="width:120px" class="mut">${tshort(x.time)}</span>
    ${badgeSide(x.side)}
    <span class="mono" style="width:90px">${num(x.copy_size).toFixed(2)} sh</span>
    <span class="mono" style="width:90px">@ ${num(x.execution_price||x.target_price).toFixed(3)}</span>
    <span class="mono" style="width:80px">${money(num(x.copy_amount_usd))}</span>
    ${badgeStatus(x.status)}
    <span class="mut" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${x.error||''}</span>
  </div>`).join('');
}

function renderTrades(ts){
  allTrades=ts;
  $('#tr-cnt').textContent=ts.length?ts.length+' shown':'';
  if(!ts.length){$('#tr-wrap').innerHTML='<div class="empty">No trades yet.</div>';return;}
  const rows=ts.map(x=>`<tr>
    <td class="mut mono" style="white-space:nowrap">${tshort(x.time)}</td>
    <td><div class="mkt">${x.market_title||'Unknown'}</div></td>
    <td>${badgeSide(x.side)}</td>
    <td class="right mono">${num(x.copy_size).toFixed(2)}</td>
    <td class="right mono">${money(num(x.copy_amount_usd))}</td>
    <td class="right mono mut">${num(x.execution_price||x.target_price).toFixed(3)}</td>
    <td class="center">${badgeStatus(x.status)}</td>
  </tr>`).join('');
  $('#tr-wrap').innerHTML=`<table><thead><tr>
    <th>Time</th><th>Market</th><th>Side</th><th class="right">Shares</th>
    <th class="right">Amount</th><th class="right">Price</th><th class="center">Status</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

function setStatus(s){
  if(!s)return;
  const run=s.running, ms=s.monitoring_status||{}, feed=ms.connected||ms.monitoring;
  $('#d-run').className='dot '+(run?'on':'off'); $('#s-run').textContent=run?'Running':'Stopped';
  $('#d-feed').className='dot '+(feed?'on':'off');
  $('#s-feed').textContent=feed?(ms.method==='data-api-polling'?'Polling feed':'Live feed'):'Feed down';
  const tgt=(s.configuration&&s.configuration.target_trader)||ms.target_address;
  if(tgt)$('#target').textContent='Target '+tgt.slice(0,10)+'…'+tgt.slice(-4);
  if(s.uptime_seconds!=null){let u=Math.floor(s.uptime_seconds);
    const h=Math.floor(u/3600),m=Math.floor(u%3600/60);$('#s-up').textContent='uptime '+h+'h '+m+'m';}
}

async function refresh(){
  try{
    const [st,me,ps,tr]=await Promise.all([
      getJSON('/api/status').catch(()=>null),
      getJSON('/api/metrics').catch(()=>({})),
      getJSON('/api/positions').catch(()=>[]),
      getJSON('/api/trades?limit=100').catch(()=>[]),
    ]);
    if(st)setStatus(st);
    renderKPIs(me||{}); renderTrades(tr||[]); renderPositions(ps||[]);
    $('#s-updated').textContent='updated '+new Date().toLocaleTimeString();
    $('#err').textContent='';
  }catch(e){$('#err').textContent='⚠ '+e.message;}
}

$('#btn-refresh').onclick=refresh;
$('#btn-auto').onclick=()=>{AUTO=!AUTO;$('#btn-auto').textContent=(AUTO?'⏸':'▶')+' Auto';};
$('#ival').textContent=IVAL;
setInterval(()=>{if(AUTO)refresh();},IVAL*1000);
refresh();
</script>
</body>
</html>"""
