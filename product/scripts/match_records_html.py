# -*- coding: utf-8 -*-
"""U5 对局记录界面:把 match_records 渲染成**数据内嵌的自包含离线 HTML**。

为什么内嵌数据而不是 fetch json:浏览器 file:// 直接 fetch 本地 json 被 CORS 挡;
把 payload 直接写进 <script> 里 -> 双击即开、完全离线、不经客户端进程(数据来自本地文件)。

富表 + 顶部地图族筛选 tab + 列点击排序 + 导出(CSV / JSON,当前筛选视图)。

用法:
  python scripts/match_records_html.py            # 从 sessions.jsonl 生成并用默认浏览器打开
  python scripts/match_records_html.py --no-open  # 只生成不打开
  python scripts/match_records_html.py --records data/logs/live/match_records.json  # 用已有 json
"""
from __future__ import annotations
import argparse, json, sys, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_match_records import build_payload  # noqa: E402


def render_html(payload: dict) -> str:
    """payload(build_match_records 的 dict)-> 自包含 HTML 字符串。"""
    # </ 转义防止 JSON 里的字符串提前闭合 <script>。
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    n = payload.get("n", 0)
    return _TEMPLATE.replace("__DATA__", data_json).replace("__N__", str(n))


def build_and_open(
    sessions_path: Path,
    out_html: Path,
    *,
    open_browser: bool = True,
    payload: dict | None = None,
    target_session_id: str | None = None,
) -> Path:
    """生成 HTML 到 out_html，并可定位 overlay 当前点击的结算局。"""
    if payload is None:
        payload = build_payload(sessions_path)
    else:
        payload = dict(payload)
    if target_session_id is not None:
        payload["target_session_id"] = str(target_session_id).strip()
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(payload), encoding="utf-8")
    if open_browser:
        webbrowser.open(out_html.resolve().as_uri())
    return out_html


# 自包含模板。__DATA__ 替换成 payload JSON, __N__ 替换成记录数。
# 纯 vanilla JS,无任何外部依赖 -> 完全离线。
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>对局记录 · BidKing</title>
<style>
  /* 默认=Claude 暖光(奶油底+陶土橙);JS applyTheme 可切暖夜。变量驱动换肤。 */
  :root { --bg:#ece4d6; --panel:#f5efe4; --line:#ddd2bd; --txt:#3c3930; --dim:#8a8067;
          --pos:#3f9460; --neg:#c94f3f; --accent:#bf5d2c; --chip:#e6dcc8;
          --hover:#e7dec9; --open:#efe6d5; --detailbg:#f1e9d9; --onaccent:#fff6ee;
          --q1:#9b9685; --q2:#4f9d5e; --q3:#3f88cc; --q4:#9a5cc8; --q5:#c08c2c; --q6:#c94f3f; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); transition:background .2s,color .2s;
         font:14px/1.5 "Microsoft YaHei","Segoe UI",system-ui,sans-serif; }
  header { padding:14px 20px 6px; }
  h1 { margin:0; font-size:18px; font-weight:600; }
  h1 .sub { color:var(--dim); font-size:12px; font-weight:400; margin-left:10px; }
  .bar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px 20px; }
  .tab { padding:4px 12px; border-radius:14px; background:var(--chip); color:var(--dim);
         cursor:pointer; user-select:none; font-size:13px; border:1px solid transparent; }
  .tab:hover { color:var(--txt); }
  .tab.on { background:var(--accent); color:var(--onaccent); font-weight:600; }
  .spacer { flex:1; }
  .btn { padding:4px 12px; border-radius:6px; background:var(--chip); color:var(--txt);
         border:1px solid var(--line); cursor:pointer; font-size:13px; }
  .btn:hover { border-color:var(--accent); }
  .wrap { padding:4px 14px 24px; overflow:auto; }
  table { border-collapse:collapse; width:100%; font-size:13px; white-space:nowrap; }
  th,td { padding:6px 10px; border-bottom:1px solid var(--line); text-align:right; }
  th:first-child,td:first-child,th.l,td.l { text-align:left; }
  thead th { position:sticky; top:0; background:var(--panel); cursor:pointer; user-select:none;
             color:var(--dim); font-weight:600; z-index:1; }
  thead th:hover { color:var(--txt); }
  th .arr { color:var(--accent); font-size:10px; margin-left:3px; }
  tbody tr:hover { background:var(--hover); }
  .pos { color:var(--pos); } .neg { color:var(--neg); } .dim { color:var(--dim); }
  .me { color:var(--accent); font-weight:600; }
  .flag { color:var(--neg); font-size:11px; }
  .target-note { margin:0 20px 6px; padding:7px 10px; border-left:3px solid var(--accent);
                 background:var(--panel); color:var(--txt); font-size:12px; }
  .dashboard-kpis { display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:10px;
                    margin:6px 20px 12px; }
  .dashboard-kpi { min-width:0; padding:10px 12px; border:1px solid var(--line); border-radius:9px;
                   background:var(--panel); }
  .dashboard-kpi b { display:block; overflow:hidden; color:var(--txt); font-size:20px;
                     font-weight:650; line-height:1.25; text-overflow:ellipsis; white-space:nowrap; }
  .dashboard-kpi span { color:var(--dim); font-size:11px; }
  .dashboard-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:10px;
                    margin:0 20px 12px; }
  .dashboard-card { min-width:0; padding:11px 12px 7px; border:1px solid var(--line); border-radius:9px;
                    background:var(--panel); }
  .dashboard-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between;
                    gap:4px 10px; margin-bottom:2px; }
  .dashboard-head h2 { margin:0; font-size:14px; font-weight:600; }
  .dashboard-head span { color:var(--dim); font-size:11px; }
  .local-chart { min-height:180px; overflow:hidden; }
  .local-chart svg { display:block; width:100%; height:auto; min-height:180px; }
  .local-chart-point { cursor:help; outline:none; }
  .local-chart-hit { fill:transparent; pointer-events:all; }
  .local-chart-mark,.local-chart-guide { pointer-events:none; }
  .local-chart-guide { stroke:var(--dim); stroke-width:1; stroke-dasharray:3 4; opacity:0; }
  .local-chart-point:hover .local-chart-mark,.local-chart-point:focus .local-chart-mark,
  .local-chart-point.is-active .local-chart-mark { opacity:.78; stroke:var(--txt); stroke-width:2; }
  .local-chart-point:hover .local-chart-guide,.local-chart-point:focus .local-chart-guide,
  .local-chart-point.is-active .local-chart-guide { opacity:.42; }
  .local-chart-point.is-current .local-chart-mark { stroke:var(--accent); stroke-width:2.5; }
  .local-chart-tooltip { position:fixed; z-index:20; display:none; max-width:320px; padding:8px 10px;
                         border:1px solid var(--line); border-radius:8px; background:var(--panel);
                         color:var(--txt); box-shadow:0 8px 24px #0002; font-size:12px;
                         line-height:1.5; pointer-events:none; }
  tbody tr.row.current { box-shadow:inset 3px 0 0 var(--accent); background:var(--open); }
  .empty { padding:40px; text-align:center; color:var(--dim); }
  .foot { padding:0 20px 18px; color:var(--dim); font-size:12px; }
  tbody tr.row { cursor:pointer; }
  tbody tr.row .exp { color:var(--dim); font-size:11px; }
  tbody tr.open { background:var(--open); }
  tr.detail > td { background:var(--detailbg); border-bottom:2px solid var(--line); padding:0; }
  .det { display:flex; flex-wrap:wrap; gap:22px; padding:14px 18px; }
  .det h4 { margin:0 0 8px; font-size:13px; color:var(--accent); font-weight:600; }
  .mmwrap { flex:0 0 auto; }
  .mm { display:grid; gap:1px; background:var(--line); border:1px solid var(--line); width:max-content; }
  /* 不能钉死 width/height: 多格物品靠 grid-row/column 的 span 拉伸填满整个 footprint,
     固定 24px 会让它只占左上角一格(= 形状不完整 bug)。stretch 是 grid item 默认对齐。 */
  .mm > div { min-width:0; min-height:0; display:flex; align-items:center; justify-content:center;
              font-size:9px; color:#11141a; overflow:hidden; border-radius:2px; }
  .mm .cap { font-size:9px; line-height:1; text-align:center; word-break:break-all; padding:1px; }
  .legend { display:flex; gap:10px; margin-top:8px; font-size:11px; color:var(--dim); flex-wrap:wrap; }
  .legend span b { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:3px;
                   vertical-align:middle; }
  .stats { flex:1 1 280px; min-width:260px; }
  .stats table { width:auto; }
  .stats td { border:0; padding:3px 14px 3px 0; white-space:normal; }
  .stats td.k { color:var(--dim); text-align:left; }
  .stats td.v { text-align:left; font-weight:600; }
  .bids { margin-top:8px; font-size:12px; color:var(--dim); }
  .bids .b { display:inline-block; margin:2px 8px 2px 0; }
  @media (max-width:760px) {
    .dashboard-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); margin-left:14px; margin-right:14px; }
    .dashboard-grid { grid-template-columns:minmax(0,1fr); margin-left:14px; margin-right:14px; }
    .dashboard-kpi:last-child { grid-column:1 / -1; }
    .dashboard-head span { flex-basis:100%; }
  }
  @media (hover:none) and (pointer:coarse), (max-width:520px) {
    .local-chart { overflow-x:auto; overscroll-behavior-inline:contain; }
    .local-chart svg { min-width:1160px; }
  }
</style>
</head>
<body>
<header>
  <h1>对局记录 <span class="sub" id="meta"></span></h1>
</header>
<div class="bar" id="tabs"></div>
<div class="bar" style="padding-top:0">
  <span class="dim" id="count"></span>
  <span class="spacer"></span>
  <button class="btn" id="themeBtn" onclick="cycleTheme()" title="切换配色">☀ 暖光</button>
  <button class="btn" onclick="exportCSV()">导出 CSV</button>
  <button class="btn" onclick="exportJSON()">导出 JSON</button>
</div>
<div class="target-note" id="targetNote" style="display:none"></div>
<section class="dashboard-kpis" id="localKpis" aria-label="当前筛选概览"></section>
<div class="dashboard-grid">
  <section class="dashboard-card" aria-labelledby="profitChartTitle">
    <div class="dashboard-head"><h2 id="profitChartTitle">近期对局盈亏</h2><span id="profitChartMeta"></span></div>
    <div class="local-chart" id="profitChart"></div>
  </section>
  <section class="dashboard-card" aria-labelledby="valueChartTitle">
    <div class="dashboard-head"><h2 id="valueChartTitle">拍品价值趋势</h2><span id="valueChartMeta"></span></div>
    <div class="local-chart" id="valueChart"></div>
  </section>
</div>
<div class="wrap"><table>
  <thead><tr id="head"></tr></thead>
  <tbody id="body"></tbody>
</table><div class="empty" id="empty" style="display:none">无记录</div>
<div id="more" style="text-align:center;padding:12px"></div></div>
<div class="foot">
  估价=结算前最后一回合的「原始价值 P50」。<b>盈亏均已扣门票</b>(入场费, 按图不同:别墅1w/沉船2.5w/集装箱5000…)。
  <b>我的盈亏</b>(主参考):每位玩家(含拿仓者)均获赢家亏损的10%分红;<b>我拍下</b>=拍品价值−我的出价+分红−门票;<b>别人拍下</b>=本场福利−门票。
  展示盈亏(=真值−估价−门票, 我们估价口径)已移至展开详情, 主表不再单列。
  <b>正=赚、负=亏</b>。已自动剔除抓包不完整的异常样本;旧样本无估价/出价的为空, 需新打的局。
</div>
<div id="localChartTooltip" class="local-chart-tooltip" role="tooltip"></div>
<script>
const PAYLOAD = __DATA__;
const RECORDS = PAYLOAD.records || [];
const SELF_NAME = PAYLOAD.self_name || null;   // 本机昵称(全局), 供「赢家=我方」标记(老样本逐条无 self_name)
const TARGET_SESSION_ID = String(PAYLOAD.target_session_id || "");
let targetFocusPending = Boolean(TARGET_SESSION_ID);
// 列定义: key, 表头, 数字?, 渲染器
const fmt = v => (v==null||v==="") ? "" : Number(v).toLocaleString("en-US");
const pl = v => { if (v==null) return ""; const c=v>0?"pos":(v<0?"neg":"dim");
                  return `<span class="${c}">${v>0?"+":""}${fmt(v)}</span>`; };
// 排位分只消费 monitor 已按 slot8 选定的 self_*；跨未开计算器的对局不拼接、不补差。
const corrRank = r => {
  if (!r) return null;
  if (r.rank_observation) return r.rank_observation;
  const rs = r.rank_summary;
  if (!rs) return null;
  const complete = rs.self_before!=null && rs.self_delta!=null && rs.self_after!=null &&
    Math.abs((Number(rs.self_before)+Number(rs.self_delta))-Number(rs.self_after))<0.5;
  if (complete) return {before:Number(rs.self_before),delta:Number(rs.self_delta),after:Number(rs.self_after),latest:Number(rs.self_after),tier:rs.self_rank_tier,source:"settlement_self_slot8"};
  if (rs.self_rank_points!=null) return {before:Number(rs.self_rank_points),delta:null,after:null,latest:Number(rs.self_rank_points),tier:rs.self_rank_tier,source:"bidding_self_points"};
  return null;
};
const COLS = [
  {k:"time", t:"时间", l:true, r:r=>r._time},
  {k:"map_name", t:"地图", l:true, r:r=>r.map_name||r.map_id||""},
  {k:"family", t:"族", l:true, r:r=>r.family||""},
  {k:"hero", t:"英雄", l:true, r:r=>esc(r.hero||"")},
  {k:"round", t:"轮", num:true, r:r=>r.round==null?"":("R"+r.round)},
  {k:"skills", t:"道具逐轮", l:true, r:r=>`<span class="dim">${(r.skills||"").replace(/</g,"&lt;")}</span>`},
  {k:"lot_value", t:"拍品价值", num:true, r:r=>fmt(r.lot_value)},
  {k:"estimate", t:"估价", num:true, r:r=>r.estimate==null?'<span class="dim">—</span>':fmt(r.estimate)},
  // 展示盈亏(=真值−估价−门票, 我们估价口径)列已隐藏(作者 2026-06-26: 主参考是「我的盈亏」真实结算);
  // 仍在展开详情面板保留作辅助。
  {k:"my_bid", t:"我的出价", num:true, r:r=>r.my_bid==null?'<span class="dim">—</span>':`<span class="me">${fmt(r.my_bid)}</span>`},
  {k:"my_pl", t:"我的盈亏", num:true, r:r=>pl(r.my_pl)},
  {k:"won_by_me", t:"拍下", r:r=>r.won_by_me===true?'<span class="me">✓</span>':(r.won_by_me===false?'<span class="dim">✗</span>':"")},
  {k:"rank_delta", t:"排位分", num:true, r:r=>{const cr=corrRank(r); if(!cr)return'<span class="dim">—</span>';
    if(cr.delta!=null && cr.after!=null){const d=cr.delta,c=d>0?"pos":(d<0?"neg":"dim");
      return `${cr.after.toFixed(0)} <span class="${c}">(${d>0?"+":""}${d.toFixed(0)})</span>`;}
    return cr.before!=null?`<span class="dim">${cr.before.toFixed(0)}</span>`:'<span class="dim">—</span>';}},
  {k:"winner_name", t:"赢家", l:true, r:r=>{
    // 我方=结算判定我拍下(won_by_me) 或 赢家昵称==本机昵称(SELF_NAME 全局, 兼容无 won_by_me 老样本)。
    const sn = r.self_name || SELF_NAME;
    const me = r.won_by_me===true || (sn && r.winner_name && r.winner_name===sn);
    return esc(r.winner_name||"") + (me?' <span class="me">我方</span>':"");}},
  {k:"winner_bid", t:"赢家价", num:true, r:r=>fmt(r.winner_bid)},
  {k:"settlement_incomplete", t:"", r:r=>r.settlement_incomplete?'<span class="flag">残缺</span>':""},
];
// 时间戳 -> 可读
RECORDS.forEach(r => {
  let t = r.time;
  if (typeof t === "number") { const d = new Date(t*1000);
    r._time = `${d.getMonth()+1}-${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; }
  else r._time = t || "";
  // 排位分排序键: 本局涨跌(无则赛后分, 再无则 null)— 用修正后的块(slot8)
  const _cr = corrRank(r);
  r.rank_delta = _cr ? (_cr.delta != null ? _cr.delta : (_cr.before != null ? _cr.before : null)) : null;
});
const PAGE = 50;  // 默认显示最新 50 行, 底部「再展开 50」加载
const DASHBOARD_LIMIT = 21;
let curFam = "全部", sortKey = "time", sortDir = -1, shown = PAGE;
const families = ["全部", ...Array.from(new Set(RECORDS.map(r=>r.family).filter(Boolean)))];

function familyRows() {
  return curFam==="全部" ? RECORDS.slice() : RECORDS.filter(r=>r.family===curFam);
}
function view() {
  let rows = familyRows();
  rows.sort((a,b)=>{ let x=a[sortKey], y=b[sortKey];
    if (x==null) return 1; if (y==null) return -1;
    if (typeof x==="string"||typeof y==="string") return sortDir*String(x).localeCompare(String(y));
    return sortDir*(x-y); });
  return rows;
}
const esc = s => String(s==null?"":s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// ---- 主题(Claude 暖色: 默认暖光, 可切暖夜)----
const THEMES = {
  warm:{label:"☀ 暖光",v:{bg:"#ece4d6",panel:"#f5efe4",line:"#ddd2bd",txt:"#3c3930",dim:"#8a8067",pos:"#3f9460",neg:"#c94f3f",accent:"#bf5d2c",chip:"#e6dcc8",hover:"#e7dec9",open:"#efe6d5",detailbg:"#f1e9d9",onaccent:"#fff6ee",q1:"#9b9685",q2:"#4f9d5e",q3:"#3f88cc",q4:"#9a5cc8",q5:"#c08c2c",q6:"#c94f3f"}},
  cream:{label:"◐ 暖纸",v:{bg:"#f3ede1",panel:"#fbf7ef",line:"#e3dac8",txt:"#423d31",dim:"#928876",pos:"#43966a",neg:"#cd5444",accent:"#c4632f",chip:"#ece4d4",hover:"#efe7d6",open:"#f4ecdc",detailbg:"#f6efe1",onaccent:"#fff6ee",q1:"#a39d8b",q2:"#52a061",q3:"#4189c9",q4:"#9d5fc6",q5:"#c2902f",q6:"#cd5444"}},
  dark:{label:"☾ 暖夜",v:{bg:"#1b1714",panel:"#241e17",line:"#3a3025",txt:"#ece5d9",dim:"#9c9379",pos:"#5cba6a",neg:"#e0594f",accent:"#d98f5a",chip:"#2c251c",hover:"#231d15",open:"#2c251c",detailbg:"#1f1a13",onaccent:"#1b1714",q1:"#c8cdd6",q2:"#5cba6a",q3:"#4a9be0",q4:"#b06fe0",q5:"#e0a64f",q6:"#e0594f"}},
};
const THEME_ORDER=["warm","cream","dark"]; let curTheme="warm";
let QCOLOR={};
function applyTheme(name){ const t=THEMES[name]||THEMES.warm; curTheme=THEMES[name]?name:"warm";
  const r=document.documentElement.style; for(const k in t.v) r.setProperty("--"+k,t.v[k]);
  QCOLOR={1:t.v.q1,2:t.v.q2,3:t.v.q3,4:t.v.q4,5:t.v.q5,6:t.v.q6};
  const b=document.getElementById("themeBtn"); if(b) b.textContent=t.label;
  try{ localStorage.setItem("bk_theme_mr",curTheme); }catch(e){} }
function cycleTheme(){ const i=THEME_ORDER.indexOf(curTheme); applyTheme(THEME_ORDER[(i+1)%THEME_ORDER.length]); render(); }
const QNAME = {1:"白",2:"绿",3:"蓝",4:"紫",5:"金",6:"红"};
function qualityLegend(){
  return [1,2,3,4,5,6].map(q=>`<span><b style="background:${QCOLOR[q]}"></b>${QNAME[q]}</span>`).join("")
    + `<span title="只有揭示锚点(local_index)、无确切占位的品质件,位置按 local_index 近似"><b style="background:transparent;border:1px dashed #999;border-radius:50%"></b>锚点(近似)</span>`;
}
function renderMinimap(mm){
  if (!mm || !mm.cells || !mm.cells.length) return '<div class="dim">无结算小地图数据(旧样本)</div>';
  const cells = mm.cells.map(it=>{
    const col = QCOLOR[it.q] || "#555";
    const title = `${QNAME[it.q]||""} ${it.n||""} ${it.cells||""}格${it.v?(" "+fmt(it.v)):""}${it.m?" · 锚点(位置近似)":""}`;
    if (it.m)  // 锚点-only 品质件: 位置由 local_index 近似 -> 虚线小点, 区别于已确认 footprint。
      return `<div style="grid-row:${it.r};grid-column:${it.c};border:1px dashed ${col};display:flex;align-items:center;justify-content:center" title="${esc(title)}"><span style="width:9px;height:9px;border-radius:50%;background:${col}"></span></div>`;
    return `<div style="grid-row:${it.r}/span ${it.h};grid-column:${it.c}/span ${it.w};background:${col}" title="${esc(title)}"><span class="cap">${esc((it.n||"").slice(0,4))}</span></div>`;
  }).join("");
  return `<div class="mm" style="grid-template-columns:repeat(${mm.cols||10},24px);grid-auto-rows:24px">${cells}</div>`;
}
function renderDetail(r){
  const kv = (k,v)=>`<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`;
  const na = '<span class="dim">—(需新打的局)</span>';
  const stats = `<table>
    ${kv("估价 (原始价值 P50)", r.estimate==null?na:fmt(r.estimate))}
    ${kv("拍品价值 (结算真值)", fmt(r.lot_value))}
    ${kv("门票 (入场费)", r.entry_fee!=null?fmt(r.entry_fee):"—")}
    ${kv("展示盈亏 (真值−估价−门票)", r.display_pl==null?'<span class="dim">—</span>':pl(r.display_pl))}
    ${kv("我的出价", r.my_bid==null?na:`<span class="me">${fmt(r.my_bid)}</span>`)}
    ${r.loss_share!=null ? kv("本场分红 (赢家亏损×10%)"+(r.profit_source==='settlement_direct'?' <span class="me">结算直出</span>':' <span class="dim">规则计算</span>'), r.loss_share>0?fmt(r.loss_share):'<span class="dim">0 (赢家没亏)</span>') : ""}
    ${kv(r.won_by_me===false?"我的盈亏 (分红−门票)":"我的盈亏 (真值−出价+分红−门票)", r.my_pl==null?'<span class="dim">—</span>':pl(r.my_pl))}
    ${kv("是否我拍下", r.won_by_me===true?'<span class="me">✓ 是</span>':(r.won_by_me===false?"否(只扣门票/收福利)":'<span class="dim">—</span>'))}
    ${(()=>{const cr=corrRank(r); return cr&&cr.latest!=null ? kv("🏆 最新观测排位分", `${cr.latest.toFixed(0)}${cr.tier!=null?(' <span class="dim">· 段位'+cr.tier+'</span>'):''}`) : "";})()}
    ${(()=>{const cr=corrRank(r); return (cr&&cr.delta!=null&&cr.after!=null) ? kv("本局排位分变化", `${cr.before.toFixed(0)} → <b>${cr.after.toFixed(0)}</b> <span class="${cr.delta>=0?'me':'neg'}">(${cr.delta>=0?'+':''}${cr.delta.toFixed(0)})</span>`) : "";})()}
    ${(()=>{const cr=corrRank(r); return cr&&cr.gap_from_previous ? kv("观测间隔", `<span class="dim">上次 ${cr.previous_observed_rank.toFixed(0)}，本局开始 ${cr.current_observed_before.toFixed(0)}；期间对局未记录，不补算单局涨跌</span>`) : "";})()}
    ${kv("赢家", `${esc(r.winner_name||"—")}${r.winner_bid!=null?(" "+fmt(r.winner_bid)):""}`)}
    ${kv("道具逐轮", esc(r.skills||"—"))}
  </table>`;
  const ab = r.all_bids || {};
  const bids = Object.keys(ab).length
    ? `<div class="bids">各家出价:${Object.entries(ab).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<span class="b">${esc(k)} <b>${fmt(v)}</b></span>`).join("")}</div>` : "";
  return `<div class="det">
    <div class="mmwrap"><h4>结算小地图</h4>${renderMinimap(r.minimap)}<div class="legend">${qualityLegend()}</div></div>
    <div class="stats"><h4>明细</h4>${stats}${bids}</div>
  </div>`;
}
function toggle(i){
  const d=document.getElementById("d"+i), x=document.getElementById("x"+i);
  if(!d) return;
  const open = d.style.display==="none";
  d.style.display = open?"":"none";
  if(x) x.textContent = open?"▾":"▸";
  if(d.previousElementSibling) d.previousElementSibling.classList.toggle("open", open);
}
function compactMoney(v) {
  const n=Number(v), a=Math.abs(n);
  if(a>=100000000){const q=n/100000000;return `${n>0?"+":""}${Math.abs(q)>=100?q.toFixed(0):q.toFixed(1)}亿`;}
  if(a>=10000){const q=n/10000;return `${n>0?"+":""}${Math.abs(q)>=100?q.toFixed(0):q.toFixed(1)}万`;}
  return `${n>0?"+":""}${fmt(n)}`;
}
function validChartRows(field) {
  return familyRows()
    .filter(r=>typeof r[field]==="number" && Number.isFinite(r[field]))
    .sort((a,b)=>(Number(a.time)||0)-(Number(b.time)||0));
}
function chartRows(field) {
  return validChartRows(field).slice(-DASHBOARD_LIMIT);
}
function chartLabel(r, valueLabel, value) {
  return `${r._time||"时间未知"} · ${r.map_name||r.map_id||"未知地图"} · ${r.hero||"未知英雄"} · ${valueLabel} ${compactMoney(value)}`;
}
function renderProfitChart() {
  const rows=chartRows("my_pl"), all=familyRows(), valid=validChartRows("my_pl");
  const missing=all.length-valid.length;
  document.getElementById("profitChartMeta").textContent=`最近 ${rows.length} / 共 ${valid.length} 个可计算对局${missing>0?` · ${missing} 局缺失`:""}`;
  const container=document.getElementById("profitChart");
  if(!rows.length){container.innerHTML='<div class="empty" style="padding:58px 8px">当前筛选暂无可计算盈亏；缺失记录不会按 0 统计。</div>';return;}
  const coarseChart=window.matchMedia("(hover:none) and (pointer:coarse), (max-width:520px)").matches;
  const W=Math.max(coarseChart?1160:340,Math.round(container.clientWidth||900)),H=220,L=W<520?50:62,R=12,T=20,B=34,IW=W-L-R,IH=H-T-B;
  const magnitude=Math.max(1,...rows.map(r=>Math.abs(Number(r.my_pl))));
  const zero=T+IH/2,half=IH/2-9,slot=IW/rows.length,barW=Math.min(28,Math.max(7,slot*.56));
  const y=v=>zero-half*Number(v)/magnitude;
  const parts=[];
  [magnitude,magnitude/2,0,-magnitude/2,-magnitude].forEach((tick,index)=>{
    const yy=y(tick),zeroClass=index===2?' stroke-dasharray="4 4"':'';
    parts.push(`<line x1="${L}" y1="${yy.toFixed(1)}" x2="${W-R}" y2="${yy.toFixed(1)}" stroke="var(--line)"${zeroClass}/><text x="${L-7}" y="${(yy+3.5).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--dim)">${esc(compactMoney(tick).replace(/^\+/,''))}</text>`);
  });
  const labelStep=Math.max(1,Math.ceil(rows.length/7));
  rows.forEach((r,i)=>{
    const value=Number(r.my_pl),cx=L+slot*(i+.5),yy=y(value),rectY=Math.min(zero,yy),rectH=Math.max(2,Math.abs(zero-yy));
    const tip=esc(chartLabel(r,"当局盈亏",value)+(TARGET_SESSION_ID&&String(r.session_id||"")===TARGET_SESSION_ID?" · 当前结算":""));
    const current=TARGET_SESSION_ID&&String(r.session_id||"")===TARGET_SESSION_ID?' is-current':'';
    parts.push(`<g class="local-chart-point${current}" tabindex="0" role="img" aria-label="${tip}" data-tip="${tip}"><rect class="local-chart-hit" x="${(cx-slot/2).toFixed(1)}" y="${T}" width="${slot.toFixed(1)}" height="${IH}"/><line class="local-chart-guide" x1="${cx.toFixed(1)}" y1="${T}" x2="${cx.toFixed(1)}" y2="${T+IH}"/><rect class="local-chart-mark" x="${(cx-barW/2).toFixed(1)}" y="${rectY.toFixed(1)}" width="${barW.toFixed(1)}" height="${rectH.toFixed(1)}" rx="2" fill="${value>=0?'var(--pos)':'var(--neg)'}"/><title>${tip}</title></g>`);
    if(i%labelStep===0||i===rows.length-1)parts.push(`<text x="${cx.toFixed(1)}" y="${H-9}" text-anchor="middle" font-size="10" fill="var(--dim)">${esc((r._time||"-").split(" ")[0])}</text>`);
  });
  container.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="近期对局盈亏图">${parts.join("")}</svg>`;
}
function renderValueChart() {
  const rows=chartRows("lot_value"), all=familyRows(), valid=validChartRows("lot_value");
  const missing=all.length-valid.length;
  document.getElementById("valueChartMeta").textContent=`最近 ${rows.length} / 共 ${valid.length} 个有效对局${missing>0?` · ${missing} 局缺失`:""}`;
  const container=document.getElementById("valueChart");
  if(!rows.length){container.innerHTML='<div class="empty" style="padding:58px 8px">当前筛选暂无拍品价值数据；缺失记录不会按 0 统计。</div>';return;}
  const coarseChart=window.matchMedia("(hover:none) and (pointer:coarse), (max-width:520px)").matches;
  const W=Math.max(coarseChart?1160:340,Math.round(container.clientWidth||900)),H=220,L=W<520?50:62,R=12,T=20,B=34,IW=W-L-R,IH=H-T-B;
  const values=rows.map(r=>Number(r.lot_value)),lo=Math.min(...values),hi=Math.max(...values);
  const padding=hi===lo?Math.max(1,Math.abs(hi)*.1):0,axisLo=lo-padding,axisHi=hi+padding,span=Math.max(1,axisHi-axisLo);
  const x=i=>L+(rows.length===1?IW/2:IW*i/(rows.length-1)),y=v=>T+IH*(axisHi-Number(v))/span;
  const valueSpacing=rows.length===1?IW:IW/(rows.length-1),valueHitWidth=Math.min(46,valueSpacing);
  const coords=rows.map((r,i)=>`${x(i).toFixed(1)},${y(r.lot_value).toFixed(1)}`).join(" ");
  const parts=[];
  for(let index=0;index<5;index++){
    const tick=axisHi-(span*index/4),yy=y(tick);
    parts.push(`<line x1="${L}" y1="${yy.toFixed(1)}" x2="${W-R}" y2="${yy.toFixed(1)}" stroke="var(--line)"/><text x="${L-7}" y="${(yy+3.5).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--dim)">${esc(compactMoney(tick).replace(/^\+/,''))}</text>`);
  }
  parts.push(`<polyline points="${coords}" fill="none" stroke="var(--q3)" stroke-width="2.5"/>`);
  const labelStep=Math.max(1,Math.ceil(rows.length/7));
  rows.forEach((r,i)=>{
    const cx=x(i),cy=y(r.lot_value),tip=esc(chartLabel(r,"拍品价值",r.lot_value));
    const current=TARGET_SESSION_ID&&String(r.session_id||"")===TARGET_SESSION_ID?' is-current':'';
    parts.push(`<g class="local-chart-point${current}" tabindex="0" role="img" aria-label="${tip}" data-tip="${tip}"><rect class="local-chart-hit" x="${(cx-valueHitWidth/2).toFixed(1)}" y="${(cy-23).toFixed(1)}" width="${valueHitWidth.toFixed(1)}" height="46" rx="4"/><line class="local-chart-guide" x1="${cx.toFixed(1)}" y1="${T}" x2="${cx.toFixed(1)}" y2="${T+IH}"/><circle class="local-chart-mark" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="4" fill="var(--q3)"/><title>${tip}</title></g>`);
    if(i%labelStep===0||i===rows.length-1)parts.push(`<text x="${cx.toFixed(1)}" y="${H-9}" text-anchor="middle" font-size="10" fill="var(--dim)">${esc((r._time||"-").split(" ")[0])}</text>`);
  });
  container.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="拍品价值趋势图">${parts.join("")}</svg>`;
}
function renderDashboard() {
  const rows=familyRows();
  const decided=rows.filter(r=>typeof r.won_by_me==="boolean"),wins=decided.filter(r=>r.won_by_me===true).length;
  const profitRows=rows.filter(r=>typeof r.my_pl==="number" && Number.isFinite(r.my_pl));
  const valueRows=rows.filter(r=>typeof r.lot_value==="number" && Number.isFinite(r.lot_value));
  const profitTotal=profitRows.reduce((sum,r)=>sum+Number(r.my_pl),0);
  const avgValue=valueRows.length?valueRows.reduce((sum,r)=>sum+Number(r.lot_value),0)/valueRows.length:null;
  const ratio=decided.length?`${(wins/decided.length*100).toFixed(1)}%`:"-";
  const cards=[
    [fmt(rows.length),"筛选对局"],
    [`${wins} / ${Math.max(0,decided.length-wins)}`,"拿下 / 未拿下"],
    [ratio,"拿下率"],
    [profitRows.length?compactMoney(profitTotal):"-",`合计盈亏 · ${profitRows.length} 局`],
    [avgValue==null?"-":fmt(Math.round(avgValue)),`平均拍品价值 · ${valueRows.length} 局`],
  ];
  document.getElementById("localKpis").innerHTML=cards.map(([value,label])=>`<div class="dashboard-kpi"><b title="${esc(value)}">${esc(value)}</b><span>${esc(label)}</span></div>`).join("");
  renderProfitChart();renderValueChart();bindLocalChartPoints();
}
function render() {
  document.getElementById("meta").textContent = `共 ${PAYLOAD.n} 局 · 离线快照 · 点行展开结算小地图`;
  document.getElementById("tabs").innerHTML = families.map(f=>
    `<span class="tab ${f===curFam?'on':''}" onclick="setFam('${f}')">${f}</span>`).join("");
  document.getElementById("head").innerHTML = '<th class="l"></th>'+COLS.map(c=>{
    const arr = c.k===sortKey ? `<span class="arr">${sortDir<0?"▼":"▲"}</span>` : "";
    return `<th class="${c.l?'l':''}" onclick="setSort('${c.k}')">${c.t}${arr}</th>`; }).join("");
  const all = view();
  renderDashboard();
  const targetIndex = TARGET_SESSION_ID
    ? all.findIndex(r=>String(r.session_id || "")===TARGET_SESSION_ID)
    : -1;
  if (targetIndex >= shown) shown = Math.ceil((targetIndex + 1) / PAGE) * PAGE;
  const rows = all.slice(0, shown);                       // 只渲染前 shown 行(默认 50)
  const targetExists = TARGET_SESSION_ID && RECORDS.some(
    r=>String(r.session_id || "")===TARGET_SESSION_ID
  );
  const targetNote = document.getElementById("targetNote");
  if (TARGET_SESSION_ID) {
    targetNote.style.display = "block";
    targetNote.textContent = targetExists
      ? "已定位并展开刚结束的这一局"
      : "刚结束的这一局尚未写入完整结算记录；当前显示历史记录。";
  } else {
    targetNote.style.display = "none";
  }
  document.getElementById("count").textContent = `显示 ${rows.length} / ${all.length} 条`;
  document.getElementById("empty").style.display = all.length?"none":"block";
  document.getElementById("body").innerHTML = rows.map((r,i)=>{
    const current = TARGET_SESSION_ID && String(r.session_id || "")===TARGET_SESSION_ID;
    return `<tr class="row${current?' open current':''}"${current?' id="currentMatchRow"':''} onclick="toggle(${i})"><td class="l"><span class="exp" id="x${i}">${current?'▾':'▸'}</span></td>`
      + COLS.map(c=>`<td class="${c.l?'l':''}">${c.r(r)}</td>`).join("")
      + `</tr><tr class="detail" id="d${i}" style="display:${current?'':'none'}"><td colspan="${COLS.length+1}">${renderDetail(r)}</td></tr>`;
  }).join("");
  if (targetFocusPending) {
    const targetRow = document.getElementById("currentMatchRow");
    if (targetRow) {
      // Wait one frame for the newly inserted table to acquire real geometry, then position
      // immediately.  A smooth animation can outlive file:// page handoff; a synchronous call
      // here runs too early and may scroll against the pre-layout geometry.
      requestAnimationFrame(()=>targetRow.scrollIntoView({block:"center"}));
      targetFocusPending = false;
    } else if (!targetExists) {
      targetFocusPending = false;
    }
  }
  const rest = all.length - rows.length;
  document.getElementById("more").innerHTML = rest>0
    ? `<button class="btn" onclick="more()">再展开 ${Math.min(PAGE, rest)} 条 (还剩 ${rest})</button>` : "";
}
function more(){ shown += PAGE; render(); }
function setFam(f){ curFam=f; shown=PAGE; render(); }
function setSort(k){ if(k===sortKey) sortDir=-sortDir; else { sortKey=k; sortDir=-1; } shown=PAGE; render(); }
function dl(name, text, type){ const b=new Blob([text],{type}); const u=URL.createObjectURL(b);
  const a=document.createElement("a"); a.href=u; a.download=name; a.click(); URL.revokeObjectURL(u); }
function exportJSON(){ dl("match_records.json", JSON.stringify({...PAYLOAD, records:view()}, null, 1), "application/json"); }
function exportCSV(){
  const cols = COLS.filter(c=>c.t);
  const head = cols.map(c=>c.t).join(",");
  const lines = view().map(r=>cols.map(c=>{
    let v = c.k==="time"?r._time:r[c.k];
    if (v==null) v="";
    v = String(v).replace(/"/g,'""');
    return /[",\n]/.test(v) ? `"${v}"` : v;
  }).join(","));
  dl("match_records.csv", "﻿"+[head,...lines].join("\r\n"), "text/csv");
}
const localChartTooltip=document.getElementById("localChartTooltip");
let localChartPinned=null;
let localChartFocusSuppressed=null;
function moveLocalChartTooltip(e){
  const pad=14,w=localChartTooltip.offsetWidth||260,h=localChartTooltip.offsetHeight||42;
  const maxX=Math.max(pad,window.innerWidth-w-pad),maxY=Math.max(pad,window.innerHeight-h-pad);
  localChartTooltip.style.left=Math.min(maxX,Math.max(pad,(Number(e.clientX)||pad)+12))+"px";
  localChartTooltip.style.top=Math.min(maxY,Math.max(pad,(Number(e.clientY)||pad)+12))+"px";
}
function hideLocalChartPoint(point){
  if(point)point.classList.remove("is-active");
  localChartTooltip.style.display="none";
}
function bindLocalChartPoints(){
  if(localChartPinned)hideLocalChartPoint(localChartPinned);
  localChartPinned=null;
  localChartFocusSuppressed=null;
  const points=Array.from(document.querySelectorAll(".local-chart-point"));
  points.forEach(function(point,index){
    function show(e){localChartTooltip.textContent=point.dataset.tip||"";localChartTooltip.style.display="block";moveLocalChartTooltip(e);}
    function showFromCenter(){const r=point.getBoundingClientRect();show({clientX:r.left+r.width/2,clientY:r.top+r.height/2});}
    function showFromPointer(e){localChartFocusSuppressed=null;show(e);}
    point.addEventListener("pointerenter",showFromPointer);
    point.addEventListener("pointermove",moveLocalChartTooltip);
    point.addEventListener("pointerleave",function(){if(localChartPinned!==point)hideLocalChartPoint(point);});
    point.addEventListener("mouseenter",showFromPointer);
    point.addEventListener("mousemove",moveLocalChartTooltip);
    point.addEventListener("mouseleave",function(){if(localChartPinned!==point)hideLocalChartPoint(point);});
    point.addEventListener("focus",function(){if(localChartFocusSuppressed!==point)showFromCenter();});
    point.addEventListener("blur",function(){if(localChartFocusSuppressed===point)localChartFocusSuppressed=null;if(localChartPinned!==point)hideLocalChartPoint(point);});
    point.addEventListener("keydown",function(e){
      if(e.key==="Escape"){
        localChartFocusSuppressed=point;
        if(localChartPinned===point)localChartPinned=null;
        hideLocalChartPoint(point);e.preventDefault();e.stopPropagation();return;
      }
      if(e.key==="Enter"||e.key===" "||e.key.startsWith("Arrow")){
        localChartFocusSuppressed=null;
        if(e.key.startsWith("Arrow")){
          const delta=e.key==="ArrowLeft"||e.key==="ArrowUp"?-1:1;
          points[Math.max(0,Math.min(points.length-1,index+delta))].focus();
        }else showFromCenter();
        e.preventDefault();e.stopPropagation();
      }
    });
    point.addEventListener("click",function(e){
      e.stopPropagation();
      localChartFocusSuppressed=null;
      if(localChartPinned===point){localChartPinned=null;hideLocalChartPoint(point);return;}
      if(localChartPinned)localChartPinned.classList.remove("is-active");
      localChartPinned=point;point.classList.add("is-active");show(e);
    });
  });
}
document.addEventListener("click",function(){if(localChartPinned)hideLocalChartPoint(localChartPinned);localChartPinned=null;});
document.addEventListener("keydown",function(e){if(e.key==="Escape"){const focused=document.activeElement&&document.activeElement.closest?document.activeElement.closest(".local-chart-point"):null;if(focused){localChartFocusSuppressed=focused;hideLocalChartPoint(focused);}if(localChartPinned)hideLocalChartPoint(localChartPinned);localChartPinned=null;}});
applyTheme((function(){ try{ return localStorage.getItem("bk_theme_mr"); }catch(e){ return null; } })() || "warm");
render();
</script>
</body>
</html>
"""


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default=str(ROOT / "data" / "logs" / "live" / "sessions.jsonl"))
    ap.add_argument("--records", default="", help="直接用已有 match_records.json(优先于 --sessions)")
    ap.add_argument("--out", default=str(ROOT / "data" / "logs" / "live" / "match_records.html"))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--target-session", default="", help="生成后定位并展开指定 session_id")
    args = ap.parse_args(argv[1:])
    if args.records:
        payload = json.loads(Path(args.records).read_text(encoding="utf-8"))
    else:
        sp = Path(args.sessions)
        if not sp.exists():
            print(f"无 {sp}"); return 1
        payload = build_payload(sp)
    out = build_and_open(Path(args.sessions), Path(args.out),
                         open_browser=not args.no_open, payload=payload,
                         target_session_id=args.target_session or None)
    print(f"对局记录 HTML: {payload.get('n',0)} 局 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
