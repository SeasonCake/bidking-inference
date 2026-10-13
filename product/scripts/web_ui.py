# -*- coding: utf-8 -*-
"""网页外壳(0.2.7)—— /public 与 /my 共用的一套设计系统。

为什么单独抽出来: 两个页面各写各的 CSS,很快就会长歪(同一个"卡片"两种圆角、品质色不一致)。
统一外壳后,加第三个页面只需要写内容。

约束(和其余前端一致):
  · **完全自包含**: 不引外部 CSS/JS/字体/图标 —— 群友网络环境不可控(有人挂加速器、有人在手机流量下),
    任何外链都可能加载失败,把页面搞成裸 HTML。
  · **手机优先**: 群友多半在手机上看。表格一律可横向滚动,不挤压。
  · 新用户默认暖光; 右上角可切暖纸/暖夜, 明确选择后持久化。
"""
from __future__ import annotations

from html import escape

# 品质配色(暖色系 —— 与 /dashboard 的 THEMES 同一套色值, 别自创)。
# ⚠️【别把品质色和语义色混用】: 这里 q5=金 是【品质】的金(#c08c2c), 而 --acc=陶土橙(#bf5d2c)
# 是【强调色】。dashboard 里强调色的变量名恰好也叫 --gold —— 名字像, 含义完全不同。
# 曾经 /public 的 QC 把 q3(蓝) 映射到 --acc、q5(金) 映射到 --gold, 于是"换个暖色皮肤"
# 会把【蓝品质染成橙色】。故品质色一律走【专用变量 --q1..--q6】, 与语义色彻底解耦。
QUALITY_COLORS = {
    "q1": "#9b9685",   # 白(暖灰)
    "q2": "#4ec96a",   # 绿
    "q3": "#3f88cc",   # 蓝
    "q4": "#9a5cc8",   # 紫
    "q5": "#c08c2c",   # 金
    "q6": "#c94f3f",   # 红
}
QUALITY_COLORS_DARK = {
    "q1": "#c9ced8", "q2": "#5cba6a", "q3": "#4f9be0",
    "q4": "#b06fe0", "q5": "#e0a64f", "q6": "#e0594f",
}
QUALITY_LABELS = {"q1": "白", "q2": "绿", "q3": "蓝", "q4": "紫", "q5": "金", "q6": "红"}

# 三套主题, 与 /dashboard 的 THEMES 一一对应(作者要求两个页面观感统一)。
# 新用户固定从暖光开始; 点右上角按钮可手动循环, 选择存 localStorage。
_THEMES = {
    "warm": {  # ☀ 暖光(奶油底 + 陶土橙) = dashboard 默认
        "bg": "#ece4d6", "card": "#f5efe4", "card2": "#efe6d5", "line": "#ddd2bd",
        "fg": "#3c3930", "dim": "#8a8067", "faint": "#b3a78c", "acc": "#bf5d2c",
        "pos": "#3f9460", "neg": "#c94f3f", "track": "#e0d6c2", "onaccent": "#fff6ee",
        "shadow": "0 1px 3px rgba(80,60,35,.08)",
    },
    "cream": {  # ◐ 暖纸
        "bg": "#f3ede1", "card": "#fbf7ef", "card2": "#f4ecdc", "line": "#e3dac8",
        "fg": "#423d31", "dim": "#928876", "faint": "#bcb199", "acc": "#c4632f",
        "pos": "#43966a", "neg": "#cd5444", "track": "#e8dfcc", "onaccent": "#fff6ee",
        "shadow": "0 1px 3px rgba(80,60,35,.07)",
    },
    "dark": {  # ☾ 暖夜
        "bg": "#1b1714", "card": "#241e17", "card2": "#2c251c", "line": "#3a3025",
        "fg": "#ece5d9", "dim": "#9c9379", "faint": "#6c6452", "acc": "#d98f5a",
        "pos": "#5cba6a", "neg": "#e0594f", "track": "#120f0b", "onaccent": "#1b1714",
        "shadow": "0 1px 2px rgba(0,0,0,.35)",
    },
}
_THEME_ORDER = ["warm", "cream", "dark"]
_THEME_LABEL = {"warm": "☀ 暖光", "cream": "◐ 暖纸", "dark": "☾ 暖夜"}


def _vars(name: str) -> str:
    """一套主题 -> CSS 变量串。品质色跟着明暗走(暗色下要提亮, 否则糊在深底上)。"""
    t = _THEMES[name]
    q = QUALITY_COLORS_DARK if name == "dark" else QUALITY_COLORS
    out = "".join(f"--{k}:{v};" for k, v in t.items())
    out += "".join(f"--{k}:{v};" for k, v in q.items())
    return out


# :root 变量块是 f-string(要插 _vars 的产物, 且它自己的 {} 已双写转义)。
# ⚠️ 到 :root[...dark] 为止就【必须结束】—— 后面 CSS 正文的几百个单 {} 不能进 f-string
# (会被当成 f-string 表达式, 直接 SyntaxError)。所以拆开: f-string 只管 :root, 正文用普通字符串拼。
_ROOT_CSS = f""":root{{color-scheme:light;--radius:12px;{_vars("warm")}
  /* 语义别名 -> 品质/正负色。历史遗留: 老代码用 --red/--gold/--purple/--ok 当"红/金/紫/绿"。
     现在它们只是【别名】, 指向真正的品质色(--q*)与正负色, 【不再】和 --acc 强调色混淆 ——
     这正是"蓝品质被染成橙"那个 bug 的根。别把它们当强调色用。 */
  --red:var(--q6);--gold:var(--q5);--purple:var(--q4);--ok:var(--pos);--warn:var(--q5);}}
/* 公版默认暖光; 只有用户明确选择才进暖纸/暖夜。 */
:root[data-theme="warm"]{{color-scheme:light;{_vars("warm")}}}
:root[data-theme="cream"]{{color-scheme:light;{_vars("cream")}}}
:root[data-theme="dark"]{{color-scheme:dark;{_vars("dark")}}}"""

_CSS = _ROOT_CSS + """
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
  "Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased;padding-bottom:40px}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px}
/* 正文里的裸 <a>(页脚 / 口径说明里的锚点)。以前没有这条规则, 于是它们落到浏览器默认的 #0000EE ——
   在暖夜主题的近黑底上几乎读不出来。链接必须跟着主题走。nav a / .side a 特异性更高, 不受影响。 */
a{color:var(--acc);text-decoration-color:color-mix(in srgb,var(--acc) 45%,transparent)}

/* ---- 顶栏 ---- */
header{border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:10;
  backdrop-filter:saturate(1.2) blur(6px)}
.hd{display:flex;align-items:center;gap:18px;height:56px}
.brand{font-weight:700;font-size:16px;letter-spacing:.2px;white-space:nowrap}
.brand span{color:var(--acc)}
nav{display:flex;gap:4px;margin-left:auto}
nav a{color:var(--dim);text-decoration:none;padding:7px 13px;border-radius:8px;font-size:14px;
  font-weight:500;white-space:nowrap;transition:.15s}
nav a:hover{color:var(--fg);background:var(--card2)}
nav a.on{color:var(--fg);background:var(--card2)}
/* ⚠️ 下面的 width/margin-top/flex/background/color 【不是多余的】: 本文件底部有个给登录表单写的
   【裸元素选择器】 button{width:100%;margin-top:10px;background:var(--acc);color:#fff} —— 它会一并扫中
   顶栏这颗按钮。实测: 不写 width:auto 就会在 flex 顶栏里撑满整条(把 nav 的 margin-left:auto 挤没)。
   改这里前先看一眼那条 button{} 规则。 */
.themebtn{flex:none;width:auto;margin:0 0 0 10px;background:var(--card2);color:var(--dim);
  border:1px solid var(--line);border-radius:8px;padding:7px 11px;font-size:13px;font-weight:600;
  cursor:pointer;white-space:nowrap;font-family:inherit;transition:.15s}
.themebtn:hover{color:var(--fg);border-color:var(--acc)}

/* ---- 标题区 ---- */
.head{padding:26px 0 18px}
h1{font-size:22px;font-weight:700;letter-spacing:-.2px}
.sub{color:var(--dim);font-size:13px;margin-top:5px}
.sub b{color:var(--fg);font-weight:600}

/* ---- KPI ---- */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:13px 15px;box-shadow:var(--shadow)}
.kpi b{display:block;font-size:22px;font-weight:700;line-height:1.25;
  font-variant-numeric:tabular-nums;letter-spacing:-.3px}
.kpi span{color:var(--dim);font-size:12px;display:block;margin-top:2px}
.kpi.acc b{color:var(--acc)} .kpi.red b{color:var(--red)} .kpi.gold b{color:var(--gold)}

/* ---- 卡片 ---- */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  margin-bottom:18px;box-shadow:var(--shadow);overflow:hidden}
.card > h2{font-size:14px;font-weight:600;padding:14px 16px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:8px}
.card > h2 small{color:var(--dim);font-weight:400;font-size:12px;margin-left:auto}
.card .pad{padding:14px 16px}
.filter-toggle{width:auto;margin:0 0 0 auto;padding:5px 10px;border:1px solid var(--line);
  border-radius:7px;background:var(--card2);color:var(--dim);font:600 12px inherit;cursor:pointer}
.filter-toggle:hover{color:var(--fg);border-color:var(--acc);filter:none}
.rrfilter{margin:0;border-width:0 0 1px;border-radius:0;box-shadow:none}
.note{color:var(--dim);font-size:12px;line-height:1.7;padding:12px 16px;
  border-top:1px solid var(--line);background:var(--card2)}

/* ---- 表格 ---- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line)}
tbody tr:last-child td{border-bottom:none}
/* 吸顶表头: top:56px 是为了让开那条 56px 高的 sticky 顶栏。⚠️ 只在【页面本身是滚动容器】时成立。 */
th{color:var(--dim);font-weight:600;font-size:12px;cursor:pointer;user-select:none;
  background:var(--card);position:sticky;top:56px}
/* 🔴 在 .scroll 里必须把 th 降回 static —— 【这不是可选的美化, 是修 bug】。
   overflow-x:auto 会把 .scroll 变成滚动容器(按规范, 另一轴的 visible 会被算成 auto), 于是 th 的
   sticky 基准从【页面视口】换成【这个壳】: 表头被摆到壳顶下方 56px, 直接【压在头几行数据上】,
   还在它本该在的位置留一条空带。/my 的"最近对局"表长期就是这个样子(HEAD 上可复现)。
   代价: 套了滚动壳的宽表没有吸顶表头了 —— /dashboard 的宽表其实也没有(它 top:0, 错位刚好不可见)。
   别为了"把吸顶表头找回来"去掉这条: 要么表头压数据, 要么手机整页横滚, 没有第三种。 */
.scroll th{position:static}
th:hover{color:var(--acc)}
th::after{content:attr(data-sort-indicator);opacity:.9;font-size:9px;margin-left:4px;color:var(--acc)}
th.sorton{box-shadow:inset 0 -2px var(--acc);color:var(--fg)}
th:first-child,td:first-child{text-align:left}
tbody tr:hover{background:var(--card2)}
td.name{white-space:normal;min-width:140px;font-weight:500}
.num{font-variant-numeric:tabular-nums}
.strong{font-weight:650}
.mid{color:var(--dim);font-size:11px;margin-left:6px;font-weight:400}
.tag{background:var(--line);color:var(--dim);font-size:10px;padding:1px 6px;
  border-radius:5px;margin-left:6px;font-weight:500}
tr.lowconf{opacity:.5}

/* 品质徽标 */
.q{display:inline-block;min-width:22px;text-align:center;font-weight:700;font-size:11px;
  padding:2px 6px;border-radius:5px;border:1px solid currentColor}
.q1{color:var(--q1)}.q2{color:var(--q2)}.q3{color:var(--q3)}
.q4{color:var(--q4)}.q5{color:var(--q5)}.q6{color:var(--q6)}

/* 条形(纯 CSS, 不引图表库) */
.bar{position:relative;height:5px;border-radius:3px;background:var(--line);
  margin-top:5px;overflow:hidden}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;display:block}
.inbar{display:flex;align-items:center;gap:9px;justify-content:flex-end}
.inbar .track{width:56px;height:5px;border-radius:3px;background:var(--line);overflow:hidden;flex:none}
.inbar .track i{display:block;height:100%;border-radius:3px}

/* ---- 登录 ---- */
.login{max-width:430px;margin:44px auto;text-align:center}
.login .card{padding:26px 22px;text-align:left}
.login h2{border:none;padding:0 0 4px;font-size:17px}
.login p{color:var(--dim);font-size:13px;margin-bottom:16px;line-height:1.65}
input[type=password],input[type=text]{width:100%;padding:11px 13px;border-radius:9px;
  border:1px solid var(--line);background:var(--bg);color:var(--fg);font-size:14px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
input:focus{outline:none;border-color:var(--acc)}
button{width:100%;margin-top:10px;padding:11px;border-radius:9px;border:none;cursor:pointer;
  background:var(--acc);color:#fff;font-size:14px;font-weight:600;transition:.15s}
button:hover{filter:brightness(1.08)}
.err{color:var(--red);font-size:13px;margin-top:10px}
.hint{color:var(--dim);font-size:12px;margin-top:14px;line-height:1.65;
  padding-top:14px;border-top:1px solid var(--line)}
footer{color:var(--dim);font-size:12px;line-height:1.7;text-align:center;padding:26px 16px 0}

/* ---- 侧边栏布局(0.2.7: 公开页从"一页塞满"改成 分区 + 侧栏) ---- */
.shell{display:flex;gap:24px;align-items:flex-start}
.side{width:190px;flex:none;position:sticky;top:72px;padding:14px 0}
.side .grp{font-size:10px;letter-spacing:1.6px;color:var(--dim);text-transform:uppercase;
  padding:12px 12px 5px;font-weight:600}
.side a{display:flex;align-items:center;gap:9px;color:var(--dim);text-decoration:none;
  padding:8px 12px;border-radius:8px;font-size:14px;cursor:pointer;transition:.14s;
  border-left:2px solid transparent}
.side a:hover{color:var(--fg);background:var(--card2)}
.side a.on{color:var(--fg);background:var(--card2);border-left-color:var(--acc);font-weight:600}
.side .dot{width:7px;height:7px;border-radius:50%;flex:none}
.main{flex:1;min-width:0}
.sect{display:none} .sect.on{display:block}
/* σ(标准差)标记: 跟在均值下面独立一行。⚠️踩过坑 —— 10px 内联会和均值糊成一个数,
   作者实测"根本没看到"。必须够大、够独立。 */
.pm{display:block;width:fit-content;margin:3px 0 0 auto;padding:1px 5px;border-radius:4px;
  color:var(--dim);font-size:11px;line-height:1.3;white-space:nowrap;background:var(--card2)}
/* ---- 联合筛选 + 图表(/public 的"探索"分区) ---- */
.fltbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:16px;
  padding:12px 14px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);box-shadow:var(--shadow)}
.fltbar .fl{color:var(--dim);font-size:11px;font-weight:600;letter-spacing:.5px}
.fltbar select{background:var(--card2);color:var(--fg);border:1px solid var(--line);
  border-radius:8px;padding:6px 9px;font:13px inherit;font-family:inherit;cursor:pointer;max-width:210px}
.fltbar select:focus{outline:none;border-color:var(--acc)}
.fltbar .rst{width:auto;margin:0 0 0 auto;padding:6px 12px;font-size:12.5px;border-radius:8px;
  background:var(--card2);color:var(--dim);border:1px solid var(--line);font-family:inherit;
  font-weight:600;cursor:pointer}
.fltbar .rst:hover{color:var(--fg);border-color:var(--acc);filter:none}
/* k-匿名门: 样本太少时【整块】盖掉统计。视觉上必须一眼看出"这里被有意挡住了", 不能像是加载失败。 */
.gate{padding:26px 20px;background:var(--card);border:1px dashed var(--line);
  border-radius:var(--radius);color:var(--dim);font-size:13.5px;line-height:1.8;text-align:center}
.gate b{color:var(--fg)}
/* 环图 + 右侧表 并排; 窄屏自动堆叠 */
.dsplit{display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap}
.dsplit .dtbl{flex:1;min-width:280px}
.dsplit svg{display:block}
.stk{display:flex;height:16px;border-radius:5px;overflow:hidden;background:var(--track);
  margin:10px 0 12px;font-size:0}
.stk i{display:block;height:100%}
/* 表格行内的小构成条。⚠️ 长度已归一(每根一样长)—— 看的是【构成比例】不是【数量】。 */
.stk.sm{height:9px;width:92px;margin:0;border-radius:3px}
.stkcell{width:104px}
th.nosort{cursor:default}
th.nosort:hover{color:var(--dim)}
.lgd{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--dim)}
.lgd span{display:inline-flex;align-items:center;gap:5px}
.lgd b{width:9px;height:9px;border-radius:2px;display:inline-block}
@media(max-width:820px){
  .shell{display:block}
  .side{width:auto;position:static;display:flex;gap:6px;overflow-x:auto;padding:10px 0;
    border-bottom:1px solid var(--line);margin-bottom:16px}
  .side .grp{display:none}
  .side a{white-space:nowrap;border-left:none;border-bottom:2px solid transparent;border-radius:8px}
  .side a.on{border-left:none;border-bottom-color:var(--acc)}
  /* 顶栏在窄屏会被 [品牌 + 2 个 nav + 主题按钮] 顶爆 —— 里面每一项都是 nowrap, 谁也不肯让。
     实测 497px 视口就已经溢出 8px(真手机 360~390 更多), 溢出的是整个【页面】在横滚。
     解法: 品牌是唯一可牺牲的(它长且不承载功能), 给它 min-width:0 + 省略号让它收缩;
     nav 和按钮保持 flex:none 不被压扁。⚠️ flex 子项默认 min-width:auto, 【不写 min-width:0
     就不会收缩】—— 这是 flex 最经典的坑, 别删。 */
  .hd{gap:10px}
  .brand{min-width:0;overflow:hidden;text-overflow:ellipsis}
  nav{flex:none;gap:2px}
  nav a{padding:7px 10px}
  .themebtn{margin-left:4px;padding:7px 9px}
}
@media(max-width:600px){
  .brand{display:none}
  .hd{gap:6px}
  nav{margin-left:0;flex:1;overflow-x:auto;scrollbar-width:none}
  nav::-webkit-scrollbar{display:none}
  nav a{display:flex;align-items:center;min-height:44px;padding:8px 10px}
  .themebtn{min-height:44px;margin-left:0}
}
@media(max-width:420px){
  .themebtn{width:44px;max-width:44px;overflow:hidden;padding:7px}
}
"""

_SECTION_JS = """
(function(){
  function show(id){
    document.querySelectorAll('.sect').forEach(function(s){s.classList.toggle('on', s.id===id);});
    document.querySelectorAll('.side a').forEach(function(a){a.classList.toggle('on', a.dataset.s===id);});
    if(history.replaceState) history.replaceState(null,'','#'+id);
    window.scrollTo(0,0);
  }
  document.querySelectorAll('.side a').forEach(function(a){
    a.addEventListener('click', function(e){ e.preventDefault(); show(a.dataset.s); });
  });
  var h=(location.hash||'').replace('#','');
  show(document.getElementById(h) ? h : (document.querySelector('.sect') || {}).id);
  // 直接开 /public#mp(书签/刷新/分享链接)时, 浏览器会在目标 section 由 display:none 变可见后
  // 【补做一次 fragment 滚动】, 把 section 顶推到视口顶 —— 于是标题被吸顶 header 盖掉。
  // show() 里那次 scrollTo(0,0) 发生在它之前, 会被覆盖。所以下一帧 + load 后各补一次。
  // (只有内容超过一屏的分区会中招, 而那恰好是地图/藏品这两张主表。)
  function top0(){ window.scrollTo(0,0); }
  if(window.requestAnimationFrame) requestAnimationFrame(top0);
  window.addEventListener('load', top0);
})();
"""


def sidebar(groups) -> str:
    """groups = [(组名, [(id, 标签, 圆点色), ...]), ...] -> 侧栏 HTML。第一个 section 默认选中。"""
    out = []
    for gname, links in groups:
        out.append(f'<div class="grp">{gname}</div>')
        for sid, label, color in links:
            out.append(f'<a data-s="{sid}" href="#{sid}">'
                       f'<span class="dot" style="background:{color}"></span>{label}</a>')
    return '<aside class="side">' + "".join(out) + "</aside>"

# 主题初始化: 【必须在 <head> 内联、body 渲染前跑】—— 否则会先闪一下默认色再切, 很难看。
# 只读 localStorage 决定 data-theme; 没存过或值损坏都明确落到暖光, 避免系统暗色改变产品默认。
_THEME_INIT_JS = """
(function(){try{var t=localStorage.getItem("bk_theme");
  document.documentElement.setAttribute("data-theme",(t==="warm"||t==="cream"||t==="dark")?t:"warm");
}catch(e){document.documentElement.setAttribute("data-theme","warm");}})();
"""

# 主题循环: 与 /dashboard 同一套三档(暖光 -> 暖纸 -> 暖夜), 选择存 localStorage。
# 起点: 已手动选过就从那个开始; 没选过固定从暖光开始。
_THEME_CYCLE_JS = """
var _TORDER=["warm","cream","dark"], _TLABEL={warm:"\\u2600 \\u6696\\u5149",cream:"\\u25d0 \\u6696\\u7eb8",dark:"\\u263e \\u6696\\u591c"};
function _effTheme(){
  var a=document.documentElement.getAttribute("data-theme");
  return _TLABEL[a] ? a : "warm";
}
function _applyTheme(name){
  document.documentElement.setAttribute("data-theme",name);
  var b=document.getElementById("themeBtn"); if(b) b.textContent=_TLABEL[name]||name;
  try{localStorage.setItem("bk_theme",name);}catch(e){}
}
function cycleTheme(){
  _applyTheme(_TORDER[(_TORDER.indexOf(_effTheme())+1)%_TORDER.length]);
}
(function(){var b=document.getElementById("themeBtn"); if(b) b.textContent=_TLABEL[_effTheme()];})();
"""

_SORT_JS = """
document.querySelectorAll('table[data-sortable]').forEach(function(tb){
  // 点击顺序就是优先级: 先点样本、再点胜率 => 先按样本, 同样本内再按胜率。
  // 三态: 未参与 -> 降序 -> 升序 -> 移出; Shift+点只保留当前列。
  var defaultCol=tb.getAttribute('data-default-sort-col');
  var defaultDir=Number(tb.getAttribute('data-default-sort-dir')||'-1');
  var keys=(defaultCol===null||defaultCol==='')?[]:[{i:Number(defaultCol),dir:defaultDir<0?-1:1}];
  function refresh(){
    tb.querySelectorAll('th').forEach(function(o,oi){
      var j=keys.findIndex(function(k){return k.i===oi;});
      o.classList.toggle('sorton',j>=0);
      if(j<0) o.removeAttribute('data-sort-indicator');
      else o.setAttribute('data-sort-indicator',(keys.length>1?String(j+1):'')+(keys[j].dir<0?'▼':'▲'));
    });
  }
  tb.querySelectorAll('th').forEach(function(th,i){
    // .nosort = 该列没有可比较的值(如"品质构成"是一根条)。不跳过的话点表头会按 textContent
    // 排序 —— 而那一格的 textContent 是空串, 于是整张表被打乱成随机顺序, 看着像 bug。
    if(th.classList.contains('nosort')) return;
    th.title='点击: 加入排序▼ → 切升序▲ → 取消 · 点其他列=联合排序 · Shift+点=只按此列';
    th.addEventListener('click',function(ev){
      var body=tb.querySelector('tbody');
      var rows=[].slice.call(body.querySelectorAll('tr'));
      var at=keys.findIndex(function(k){return k.i===i;});
      if(ev.shiftKey){
        keys=(at>=0&&keys.length===1) ? [{i:i,dir:-keys[0].dir}] : [{i:i,dir:-1}];
      }else if(at<0) keys=keys.concat([{i:i,dir:-1}]);
      else if(keys[at].dir<0) keys=keys.map(function(k,j){return j===at?{i:i,dir:1}:k;});
      else keys=keys.filter(function(_,j){return j!==at;});
      rows=rows.map(function(r,j){return {r:r,j:j};});
      rows.sort(function(A,B){
        for(var z=0;z<keys.length;z++){
          var k=keys[z],x=A.r.cells[k.i],y=B.r.cells[k.i],cmp=0;
          if(!x||!y) continue;
          var xv=x.dataset.v,yv=y.dataset.v;
          if(xv!==undefined&&yv!==undefined&&!isNaN(Number(xv))&&!isNaN(Number(yv))) cmp=Number(xv)-Number(yv);
          else cmp=(x.textContent||'').localeCompare(y.textContent||'','zh');
          if(cmp) return cmp*k.dir;
        }
        return A.j-B.j;
      });
      rows.forEach(function(o){body.appendChild(o.r);});
      refresh();
    });
  });
  refresh();
});
"""


def money(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "-"


def page(*, title: str, active: str, body: str, foot: str = "", side: str = "",
         wide: bool = False, extra: str = "") -> str:
    """统一外壳。active = 'public' | 'private' | 'my'(高亮顶部导航)。

    side  = sidebar(...) 的产物。给了就走 侧栏 + 分区(.sect) 布局, 并注入切换 JS。
    wide  = 放宽内容区(带侧栏的宽表需要)。
    extra = 追加到 body 末尾的 <script>(页面自己的数据+逻辑, 如 /public 的联合筛选)。
            ⚠️放在最后 —— 它要操作的 DOM 得先存在。
    """
    def _n(key: str, href: str, label: str) -> str:
        cls = ' class="on"' if active == key else ""
        return f'<a href="{href}"{cls}>{label}</a>'

    nav = (_n("public", "/public", "拍卖大数据")
           + _n("private", "/private", "私有大数据")
           + _n("my", "/my", "我的记录"))
    inner = f'<div class="shell">{side}<div class="main">{body}</div></div>' if side else body
    wrap_style = ' style="max-width:1420px"' if wide else ""
    sect_js = f"<script>{_SECTION_JS}</script>" if side else ""
    # 第 3 层防索引(前两层在 sample_collector_server: X-Robots-Tag 响应头 + /robots.txt)。
    # 作者定: /public 是【群内自用】, 后续要收成"正式用户才能访问"。它现在还免登录 ⇒
    # 链接一旦流出就会被爬。搜索引擎索引【收不回来】: 就算之后加了 token 门, 快照/摘要仍留在结果页里。
    # 三层都要: 头对非 HTML(/export /stats)也生效; robots.txt 是爬虫的第一站; meta 在页面被镜像/另存时仍在。
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive,nosnippet">
<title>{title}</title>
<script>{_THEME_INIT_JS}</script>
<style>{_CSS}</style></head><body>
<header><div class="wrap hd"{wrap_style}>
  <div class="brand">lemyes<span>&amp;</span>加菲 · 拍卖计算器</div>
  <nav>{nav}</nav>
  <button type="button" id="themeBtn" class="themebtn" onclick="cycleTheme()"
    title="切换配色(暖光 / 暖纸 / 暖夜)">☀ 暖光</button>
</div></header>
<div class="wrap"{wrap_style}>
{inner}
<footer>{foot}</footer>
</div>
<script>{_THEME_CYCLE_JS}</script>
<script>{_SORT_JS}</script>
{sect_js}
{extra}
</body></html>"""


def private_dashboard_login_html(error: str = "") -> str:
    """365 天订阅用户进入完整私有 dashboard 的 token 登录页。"""
    err = f'<div class="err" role="alert">{escape(error)}</div>' if error else ""
    body = f"""
<div class="login">
  <div class="card">
    <h2>进入私有大数据</h2>
    <p>完整样本、玩家、排位和异常诊断仅向<b>有效的 365 天订阅</b>或<b>历史永久版</b>开放。</p>
    <form method="post" action="/private" autocomplete="on">
      <label for="private-token" style="display:block;margin-bottom:7px;color:var(--dim);font-size:12px">365 天激活 token</label>
      <input id="private-token" type="password" name="token" placeholder="粘贴你的 365 天激活 token"
        autocomplete="current-password" aria-label="365 天激活 token" required>
      <button type="submit">确认并进入</button>
    </form>
    {err}
    <div class="hint">
      · 30/90 天档不包含私有大数据权限。<br>
      · 历史永久版保留私有大数据权限，但公版已不再新售永久档。<br>
      · 首次从这里使用 365 天权益，也会启动与客户端共用的 365 天订阅时钟。<br>
      · 浏览器可保存并自动填充 token；每次从“私有大数据”入口进入仍需由你点击确认。<br>
      · token 只通过 POST 验证，不写入网址、页面或登录 cookie。
    </div>
  </div>
</div>
"""
    return page(title="私有大数据 · lemyes&加菲计算器", active="private", body=body,
                foot="私有页包含完整逐局数据，请勿共享登录态或页面内容。")
