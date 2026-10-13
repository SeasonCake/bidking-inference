# -*- coding: utf-8 -*-
"""个人对局记录(0.2.7 · uid-scope)—— 玩家用 token 登录,只看得到自己的数据。

与 /public 的区别:
  · /public = 公开聚合,谁都能看,30 分钟才重算一次(长期统计,不需要实时)。
  · /my     = 私人数据,必须 token 登录,**实时**(作者 0712: 打完一局就想看到)。

身份链: token --verify_token(HMAC)--> uid_hash --私有签名上传索引--> session_hash --样本。
新链路的 uid_hash 不进入公共/隔离 JSONL；旧客户端自报身份只作 legacy_unverified，
不能进入私有索引。未部署私有索引的 rollback 模式仍保留旧读法。

⚡性能: 全库 4.5 万行,但我的记录只有几十条。若逐行 json.loads 找,要 ~1.4s(而且这是【实时】端点,
每次请求都得扫)。故先用【字符串预筛】: uid_hash 是 16 位十六进制,直接在原始行里找 needle,
命中才 json.loads。扫描退化成字符串搜索(~0.1s),只解析真正属于我的那几十行。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent

# uid_hash 必须长这样(防注入: 它会被拼进字符串搜索的 needle)
_UID_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _map_meta():
    from public_stats import _map_meta as resolve

    return resolve()


def _shanghai_day(value):
    from public_stats import _shanghai_day as resolve

    return resolve(value)


def _shanghai_format(value, fmt):
    from public_stats import _shanghai_format as resolve

    return resolve(value, fmt)


class UserSampleList(list[dict]):
    parsed_count: int = 0
    unknown_date_count: int = 0
    payload_read_bytes: int = 0
    payload_read_rows: int = 0
    total_payload_read_bytes: int = 0
    total_payload_read_rows: int = 0


def uid_hash_of(uid: str) -> str:
    """生成与服务端 upload lease 一致的付费 UID 私有别名。"""
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:16]


def _record_ts(rec: dict) -> float | None:
    """游玩时间优先，收样时间只兜底；旧样本重传不能把历史对局挤到今天。"""
    for key in ("client_ts", "created_at", "received_ts"):
        value = rec.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def load_user_samples(
    path: Path, uid_hash: str, *, limit: int | None = None,
    start: date | None = None, end: date | None = None,
    identity_store=None,
    history_index=None, snapshot_bytes: int | None = None,
) -> UserSampleList:
    """流式取出属于该 uid_hash 的样本(最新的在前)。

    字符串预筛见模块 docstring。uid_hash 形状不合法 -> 直接返回空(绝不把任意字符串拼进搜索)。"""
    if not _UID_HASH_RE.match(uid_hash or ""):
        return UserSampleList()
    p = Path(path)
    if not p.is_file():
        return UserSampleList()

    trusted_sessions = None
    if identity_store is not None:
        try:
            trusted_sessions = identity_store.sessions_for_uid_hash(uid_hash)
        except Exception:  # noqa: BLE001 - private identity lookup fails closed
            return UserSampleList()
        if not trusted_sessions:
            return UserSampleList()
    if history_index is not None:
        if p.resolve() != history_index.sources["main"].resolve():
            raise ValueError("history index source mismatch")
        scope = ({"legacy_uid_hash": uid_hash} if trusted_sessions is None else
                 {"session_hashes": trusted_sessions})
        selected = history_index.rows(view="my", start=start, end=end,
                                      snapshot_bytes=snapshot_bytes, **scope)
        out = UserSampleList()
        for rec in selected:
            # The anonymous tag is never a grant: preserve the actual record
            # owner check and the existing signed-session authority.
            if not isinstance(rec, dict) or not (
                rec.get("uid_hash") == uid_hash if trusted_sessions is None else
                rec.get("session_hash") in trusted_sessions
            ):
                from sample_history_index import HistoryIndexError
                raise HistoryIndexError("private_consumer_owner_mismatch")
            if start is not None or end is not None:
                ts = _record_ts(rec)
                if ts is None:
                    continue
                try:
                    day = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
                except (OverflowError, OSError, TypeError, ValueError):
                    continue
                if start is not None and day < start:
                    continue
                if end is not None and day > end:
                    continue
            out.append(rec)
        out.sort(key=lambda r: _record_ts(r) or 0, reverse=True)
        if limit is not None:
            del out[max(0, limit):]
        out.parsed_count = selected.last_parsed_count
        out.unknown_date_count = selected.last_unknown_date_count
        out.payload_read_bytes, out.payload_read_rows = selected.bytes_read, selected.rows_read
        out.total_payload_read_bytes, out.total_payload_read_rows = selected.bytes_read, selected.rows_read
        return out
    needle = f'"uid_hash": "{uid_hash}"'
    alt = f'"uid_hash":"{uid_hash}"'
    out = UserSampleList()
    parsed = unknown = 0
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if trusted_sessions is None:
                if needle not in line and alt not in line:
                    continue
            elif not any(session_hash in line for session_hash in trusted_sessions):
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            owned = bool(
                isinstance(rec, dict)
                and (
                    rec.get("uid_hash") == uid_hash
                    if trusted_sessions is None
                    else rec.get("session_hash") in trusted_sessions
                )
            )
            if owned:
                parsed += 1
                if start is not None or end is not None:
                    ts = _record_ts(rec)
                    if ts is None:
                        unknown += 1
                        continue
                    try:
                        day = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
                    except (OverflowError, OSError, TypeError, ValueError):
                        unknown += 1
                        continue
                    if start is not None and day < start:
                        continue
                    if end is not None and day > end:
                        continue
                out.append(rec)
    out.sort(key=lambda r: _record_ts(r) or 0, reverse=True)
    if limit is not None:
        del out[max(0, limit):]
    out.parsed_count = parsed
    out.unknown_date_count = unknown
    return out


def _qmap(s: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(s, str):
        return out
    for part in s.split(";"):
        m = re.fullmatch(r"\s*(q[1-6])\s*=\s*(-?\d+)\s*", part)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _won_by_self(rec: dict) -> bool | None:
    """优先使用客户端按 player_id 判定的结果；旧记录再用同帧昵称回填。"""
    explicit = rec.get("winner_is_self")
    if isinstance(explicit, bool):
        return explicit
    winner = rec.get("winner") or rec.get("winner_name")
    me = rec.get("player") or rec.get("self_name")
    if winner and me:
        return str(winner) == str(me)
    return None


def _self_bid_of(rec: dict) -> float | None:
    """新记录直接读 self_bid；旧的获胜记录可安全回填为 winner_bid。"""
    value = rec.get("self_bid")
    if _number(value):
        return float(value)
    if _won_by_self(rec) is True and _number(rec.get("winner_bid")):
        return float(rec["winner_bid"])
    return None


def _map_label_html(map_id: Any, meta: dict) -> str:
    """真实地图名为主，稳定代号作为次级标签；目录缺失时 fail-open 显示代号。"""
    code = "-" if map_id in (None, "") else str(map_id)
    row = meta.get(map_id) or meta.get(str(map_id)) or {}
    name = str(row.get("name") or "").strip()
    if not name or name == code:
        return escape(code)
    return f'{escape(name)}<span class="mid">{escape(code)}</span>'


def _entry_fee(rec: dict) -> int:
    """与 match records/dashboard 共用族门票真源，活动图也按原图族收费。"""
    try:
        from build_match_records import _FEE_BY_FAMILY, _family_from_id, _load_map_meta  # noqa: PLC0415
        meta = _load_map_meta()
        mid = rec.get("map_id")
        row = meta.get(mid) or meta.get(str(mid)) or {}
        family = row.get("family") or _family_from_id(mid)
        fee = _FEE_BY_FAMILY.get(family)
        return int(fee if fee is not None else (row.get("entry_fee") or 0))
    except Exception:  # noqa: BLE001 - 地图表缺失时仍可安全展示，未知门票按 0 并由收益缺失门控制
        return 0


def _profit_of(rec: dict) -> tuple[float | None, bool]:
    """返回 (本局收益, 是否估算)。缺关键结算字段返回 None，绝不把缺失当 0。

    每位玩家(含拿仓者)都收 10% * max(0, 赢家出价 - 真值)；拍下者的分红按确认规则
    计算，未拍下者优先用结算帧 welfare 真值，老样本才按同一规则回退估算。
    """
    lot = rec.get("known_value_sum")
    won = _won_by_self(rec)
    if won is None or not _number(lot):
        return None, False
    fee = _entry_fee(rec)
    from build_match_records import _loss_share_units  # noqa: PLC0415

    if won:
        bid = _self_bid_of(rec)
        if bid is None:
            return None, False
        dividend = _loss_share_units(bid, lot)
        return float(lot) - bid + (dividend or 0) - fee, False
    welfare = rec.get("settlement_welfare")
    if _number(welfare):
        return float(welfare) - fee, False
    winner_bid = rec.get("winner_bid")
    if _number(winner_bid):
        dividend = _loss_share_units(winner_bid, lot)
        return (dividend - fee if dividend is not None else None), True
    return None, False


def summarize_user(samples: list[dict]) -> dict:
    """个人统计: 打了多少局、总价值、最常打的图、最贵的一局、拿到过的稀有藏品。

    ⚠️ 这里可以安全地展示昵称/赢家/出价 —— 因为这是【他自己的】数据, 只有他能看到
    (token 验签过)。这与 /public 的隐私红线不冲突: 那里是公开的, 这里是私人的。"""
    if not samples:
        return {"n": 0}

    total_value = 0
    per_map: dict[int, dict] = {}
    best: dict | None = None
    rare: dict[str, dict] = {}
    wins = 0
    n_with_winner = 0
    profit_total = 0.0
    profit_n = 0
    estimated_profit_n = 0
    daily: dict[str, dict] = {}

    for r in samples:
        v = r.get("known_value_sum")
        v = v if isinstance(v, (int, float)) else 0
        total_value += v

        mid = r.get("map_id")
        if mid is not None:
            m = per_map.setdefault(int(mid), {"map_id": int(mid), "n": 0, "value": 0})
            m["n"] += 1
            m["value"] += v

        if best is None or v > (best.get("known_value_sum") or 0):
            best = r

        # 胜率(赢家 == 本机昵称)。player 是本机昵称, winner 是本局赢家。
        won_by_self = _won_by_self(r)
        if won_by_self is not None:
            n_with_winner += 1
            if won_by_self:
                wins += 1

        profit, estimated = _profit_of(r)
        if profit is not None:
            profit_total += profit
            profit_n += 1
            estimated_profit_n += int(estimated)
        ts = _record_ts(r)
        if ts:
            day = _shanghai_day(ts)
            if day is not None:
                d = daily.setdefault(day, {"day": day, "n": 0, "value": 0.0,
                                           "value_n": 0, "profit": 0.0, "profit_n": 0,
                                           "estimated_n": 0})
                d["n"] += 1
                if isinstance(r.get("known_value_sum"), (int, float)):
                    d["value"] += float(r["known_value_sum"])
                    d["value_n"] += 1
                if profit is not None:
                    d["profit"] += profit
                    d["profit_n"] += 1
                    d["estimated_n"] += int(estimated)

        for it in (r.get("rare_items") or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("n") or "").strip()
            if not name:
                continue
            row = rare.setdefault(name, {"name": name, "quality": it.get("q"),
                                         "value": it.get("v"), "seen": 0})
            row["seen"] += 1

    maps = sorted(per_map.values(), key=lambda m: -m["n"])
    rare_rows = sorted(rare.values(), key=lambda r: (-(r["quality"] or 0), -(r["value"] or 0)))
    timeline = []
    cumulative = 0.0
    for day in sorted(daily):
        d = daily[day]
        cumulative += d["profit"]
        timeline.append({"day": day, "n": d["n"],
                         "avg_value": round(d["value"] / d["value_n"]) if d["value_n"] else None,
                         "profit": round(d["profit"]) if d["profit_n"] else None,
                         "profit_n": d["profit_n"], "estimated_n": d["estimated_n"],
                         "cumulative_profit": round(cumulative)})

    return {
        "n": len(samples),
        "total_value": total_value,
        "avg_value": round(total_value / len(samples)) if samples else 0,
        "maps": maps[:8],
        "best": best,
        "rare": rare_rows[:20],
        "wins": wins,
        "n_with_winner": n_with_winner,
        "win_rate": round(wins / n_with_winner, 4) if n_with_winner else None,
        "profit_total": round(profit_total) if profit_n else None,
        "profit_n": profit_n,
        "estimated_profit_n": estimated_profit_n,
        "timeline": timeline,
        "date_start": timeline[0]["day"] if timeline else None,
        "date_end": timeline[-1]["day"] if timeline else None,
    }


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def build_login_html(error: str = "") -> str:
    """token 登录页。

    ⚠️ token 必须走 POST 表单(不是 URL 查询串)—— URL 会进浏览器历史、Referer、服务器访问日志,
    而 token 是能激活软件的凭证, 泄露 = 被人白嫖。"""
    import sys
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import web_ui as ui

    err = f'<div class="err">{error}</div>' if error else ""
    body = f"""
<div class="login">
  <div class="card">
    <h2>查看我的对局记录</h2>
    <p>用你的激活 token 登录，只能看到<b>你自己</b>的数据。</p>
    <form method="post" action="/my" autocomplete="on">
      <label for="my-token" style="display:block;margin-bottom:7px;color:var(--dim);font-size:12px">激活 token</label>
      <input id="my-token" type="password" name="token" placeholder="粘贴你的 token"
        autocomplete="current-password" aria-label="激活 token" required>
      <button type="submit">确认并查看</button>
    </form>
    {err}
    <div class="hint">
      · 浏览器可保存并自动填充 token；每次进入仍需由你点击确认。<br>
      · token 就是购买时发给你的那串激活码(也在软件的激活框里)。<br>
      · “我的记录”向有效的 <b>90 天及以上订阅</b>和<b>历史永久版</b>开放；不足 90 天的档位不包含此权限，永久档已不再新售。<br>
      · 只有 <b>0.2.7 及之后</b>的版本上传的对局才会记到你名下 —— 更早的数据没有身份标记,
        无法归属到人(它们只进公共统计)。<br>
      · 公版默认在结算后自动上传;网络/上传失败或仍用旧版本时,这里可能暂时没有数据。
    </div>
  </div>
</div>
"""
    return ui.page(title="我的记录 · lemyes&加菲计算器", active="my", body=body)


def _trend_svg(
    points: list[tuple[str, float]], *, color: str, label: str,
    details: list[str] | None = None,
) -> str:
    """自包含单序列趋势图；日期是自然日，收益曲线允许跨 0。"""
    if not points:
        return '<div class="pad" style="color:var(--dim)">暂无可用时间数据</div>'
    base_width, height = 900, 220
    left, right, top, bottom = 66, 14, 15, 30
    required_width = left + right + 46 * max(1, len(points))
    width = max(base_width, required_width)
    values = [float(v) for _, v in points]
    lo, hi = min([0.0, *values]), max([0.0, *values])
    if lo == hi:
        hi = lo + 1.0
    inner_w, inner_h = width - left - right, height - top - bottom

    slot = inner_w / max(1, len(points))
    hit_width = slot * 0.98

    def x(i: int) -> float:
        # Center each point in its own non-overlapping interaction cell.  This keeps
        # the visible endpoints and their hit targets inside the SVG at every density.
        return left + slot * (i + 0.5)

    def y(v: float) -> float:
        return top + inner_h * (hi - v) / (hi - lo)

    def axis_label(value: float) -> str:
        """纵轴使用紧凑中文单位；hover 继续保留逐点完整精度。"""
        magnitude = abs(value)
        if magnitude >= 100_000_000:
            return f"{value / 100_000_000:.1f}亿".replace(".0亿", "亿")
        if magnitude >= 10_000:
            return f"{value / 10_000:.1f}万".replace(".0万", "万")
        return f"{value:,.0f}"

    coords = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(points))
    ticks = [lo + (hi - lo) * i / 4 for i in range(5)]
    if lo < 0 < hi and all(abs(tick) > (hi - lo) / 1000 for tick in ticks):
        ticks.append(0.0)
    ticks.sort(reverse=True)
    parts = []
    for tick in ticks:
        tick_y = y(tick)
        is_zero = abs(tick) <= (hi - lo) / 1_000_000
        line_class = "trend-zero-line" if is_zero else "trend-y-grid"
        dash = ' stroke-dasharray="4 4"' if is_zero else ""
        parts.append(
            f'<line class="{line_class}" x1="{left}" y1="{tick_y:.1f}" '
            f'x2="{width-right}" y2="{tick_y:.1f}" stroke="var(--line)"{dash}/>'
        )
        parts.append(
            f'<text class="trend-y-label" x="{left-7}" y="{tick_y+3.5:.1f}" '
            f'text-anchor="end" font-size="10" fill="var(--dim)">{axis_label(tick)}</text>'
        )
    parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5"/>')
    for i, (day, value) in enumerate(points):
        detail = details[i] if details and i < len(details) else f"{day} · {label} {value:,.0f}"
        tip = escape(detail, quote=True)
        parts.append(
            f'<g class="trend-point" tabindex="0" role="img" aria-label="{tip}" '
            f'data-tip="{tip}" data-x="{x(i):.1f}" data-y="{y(value):.1f}">'
            f'<line class="trend-guide trend-guide-x" x1="{left}" y1="{y(value):.1f}" '
            f'x2="{width-right}" y2="{y(value):.1f}"/>'
            f'<line class="trend-guide trend-guide-y" x1="{x(i):.1f}" y1="{top}" '
            f'x2="{x(i):.1f}" y2="{top+inner_h}"/>'
            f'<rect class="trend-hit" x="{x(i)-hit_width/2:.4f}" y="{top}" '
            f'width="{hit_width:.4f}" height="{inner_h}" rx="4"/>'
            f'<circle class="trend-dot" cx="{x(i):.1f}" cy="{y(value):.1f}" r="4" fill="{color}"/>'
            f'<title>{tip}</title></g>'
        )
    step = max(1, (len(points) + 7) // 8)
    for i, (day, _) in enumerate(points):
        if i % step == 0 or i == len(points) - 1:
            parts.append(f'<text class="trend-x-label" x="{x(i):.1f}" y="{height-9}" text-anchor="middle" '
                         f'font-size="10" fill="var(--dim)">{day[5:]}</text>')
    fixed_canvas_style = f"width:{width}px;min-width:{width}px;" if width > base_width else ""
    return (f'<svg class="records-trend-chart" viewBox="0 0 {width} {height}" width="100%" '
            f'style="--records-chart-min-width:{required_width}px;{fixed_canvas_style}'
            f'height:auto;display:block" '
            f'role="img" aria-label="{escape(label, quote=True)}趋势图">'
            + "".join(parts) + "</svg>")


def _profit_bar_svg(timeline: list[dict], *, limit: int = 21) -> str:
    """Render recent daily profit without coercing missing profit to zero."""
    days = list(timeline[-max(1, int(limit)):])
    if not days:
        return '<div class="pad" style="color:var(--dim)">暂无可用时间数据</div>'

    width, height = 900, 250
    left, right, top, bottom = 66, 14, 24, 34
    inner_w, inner_h = width - left - right, height - top - bottom
    known = [float(day["profit"]) for day in days if day.get("profit") is not None]
    magnitude = max([1.0, *(abs(value) for value in known)])
    zero_y = top + inner_h / 2
    half_h = inner_h / 2 - 12
    slot = inner_w / max(1, len(days))
    hit_width = slot * 0.98
    bar_w = min(32.0, max(7.0, slot * 0.58))

    def y(value: float) -> float:
        return zero_y - half_h * value / magnitude

    def axis_label(value: float) -> str:
        absolute = abs(value)
        if absolute >= 100_000_000:
            return f"{value / 100_000_000:.1f}亿".replace(".0亿", "亿")
        if absolute >= 10_000:
            return f"{value / 10_000:.1f}万".replace(".0万", "万")
        return f"{value:,.0f}"

    parts: list[str] = []
    for index in range(5):
        tick = magnitude - (2 * magnitude * index / 4)
        tick_y = y(tick)
        is_zero = abs(tick) <= magnitude / 1_000_000
        line_class = "profit-zero-line" if is_zero else "profit-y-grid"
        dash = ' stroke-dasharray="4 4"' if is_zero else ""
        parts.append(
            f'<line class="{line_class}" x1="{left}" y1="{tick_y:.1f}" '
            f'x2="{width-right}" y2="{tick_y:.1f}" stroke="var(--line)"{dash}/>'
        )
        parts.append(
            f'<text class="profit-y-label" x="{left-7}" y="{tick_y+3.5:.1f}" '
            f'text-anchor="end" font-size="10" fill="var(--dim)">{axis_label(tick)}</text>'
        )

    label_step = max(1, (len(days) + 6) // 7)
    for index, day in enumerate(days):
        center = left + slot * (index + 0.5)
        value = day.get("profit")
        estimated_n = int(day.get("estimated_n") or 0)
        day_label = str(day.get("day") or "-")
        cumulative = day.get("cumulative_profit")
        cumulative_text = (
            "-" if cumulative is None else f"{float(cumulative):+,.0f}"
        )
        estimate_text = f' · 其中 {estimated_n} 局估算' if estimated_n else ""
        if value is None:
            tip_text = (
                f'{day_label} · {int(day.get("n") or 0)} 局 · 当日无可计算盈亏 · '
                f'累计收益 {cumulative_text}'
            )
        else:
            tip_text = (
                f'{day_label} · {int(day.get("n") or 0)} 局 · '
                f'当日收益 {float(value):+,.0f} · 累计收益 {cumulative_text}{estimate_text}'
            )
        tip = escape(tip_text, quote=True)
        parts.append(
            f'<g class="profit-point" tabindex="0" role="img" aria-label="{tip}" '
            f'data-tip="{tip}" data-x="{center:.1f}" data-y="{zero_y:.1f}">'
            f'<rect class="profit-hit" x="{center-hit_width/2:.4f}" y="{top}" '
            f'width="{hit_width:.4f}" height="{inner_h:.1f}"/>'
            f'<line class="profit-guide" x1="{center:.1f}" y1="{top}" '
            f'x2="{center:.1f}" y2="{top+inner_h}"/>'
        )
        if value is None:
            parts.append(
                f'<line class="profit-missing" x1="{center-5:.1f}" y1="{zero_y-5:.1f}" '
                f'x2="{center+5:.1f}" y2="{zero_y+5:.1f}" stroke="var(--dim)" '
                f'stroke-width="1.5"/><line class="profit-missing" x1="{center+5:.1f}" '
                f'y1="{zero_y-5:.1f}" x2="{center-5:.1f}" y2="{zero_y+5:.1f}" '
                f'stroke="var(--dim)" stroke-width="1.5"/>'
            )
        else:
            numeric = float(value)
            bar_y = y(numeric)
            rect_y = min(zero_y, bar_y)
            rect_h = max(2.0, abs(zero_y - bar_y))
            color = "var(--pos)" if numeric >= 0 else "var(--neg)"
            parts.append(
                f'<rect class="profit-bar" x="{center-bar_w/2:.1f}" y="{rect_y:.1f}" '
                f'width="{bar_w:.1f}" height="{rect_h:.1f}" rx="2" fill="{color}"/>'
            )
        parts.append(f'<title>{tip}</title></g>')
        if index % label_step == 0 or index == len(days) - 1:
            short_day = day_label[5:] if len(day_label) >= 10 else day_label
            parts.append(
                f'<text class="profit-x-label" x="{center:.1f}" y="{height-10}" '
                f'text-anchor="middle" font-size="10" fill="var(--dim)">{escape(short_day)}</text>'
            )
    coarse_width = math.ceil(width * 46 * max(1, len(days)) / inner_w)
    return (
        f'<svg class="profit-bars" viewBox="0 0 {width} {height}" width="100%" '
        f'style="--records-chart-min-width:{coarse_width}px;height:auto;display:block" '
        f'role="img" aria-label="最近游玩日盈亏图">'
        + "".join(parts) + "</svg>"
    )


def build_my_html(summary: dict, samples: list[dict], *, server_window: dict | None = None) -> str:
    """个人数据页。这里可以安全显示昵称/赢家/出价 —— 是他自己的数据, 只有他看得到(token 验签过)。"""
    import sys
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import web_ui as ui

    n = summary.get("n", 0)
    window_note = ""
    if server_window:
        window_note = (f'服务端范围 {server_window.get("start") or "-"} ~ '
                       f'{server_window.get("end") or "-"}（北京时间、两端包含） · '
                       f'命中 {len(samples):,} 局 · 未知日期 '
                       f'{int(server_window.get("unknown_date_count") or 0):,} 局已排除')
    if not n:
        body = f"""
<div class="head"><h1>我的记录</h1></div>
<div class="card"><div class="pad">
  <p style="color:var(--dim);line-height:1.8">{window_note}<br><br>
    还没有你的对局数据。可能的原因:<br><br>
    · 你还在用 <b>0.2.7 之前</b>的版本 —— 更早的上传不带身份标记,无法归属到人。<br>
    · 当前版本过旧,或最近一局还没结算/上传成功。<br>
    · 公版默认自动上传,网络异常时可稍后刷新或联系作者排查。<br><br>
    打一局带结算的对局后回来刷新即可。
  </p>
</div></div>
<div style="text-align:center"><a href="/my" style="color:var(--dim);font-size:13px">返回并重新验证</a></div>
"""
        return ui.page(title="我的记录", active="my", body=body)

    wr = summary.get("win_rate")
    wr_txt = f"{wr*100:.0f}%" if wr is not None else "-"
    best = summary.get("best") or {}
    timeline = summary.get("timeline") or []
    profit_total = summary.get("profit_total")
    profit_txt = ("-" if profit_total is None else f'{"+" if profit_total >= 0 else ""}{ui.money(profit_total)}')
    estimated_n = summary.get("estimated_profit_n", 0)
    profit_label = "累计收益" + ("(含估算)" if estimated_n else "")
    estimate_note = (f"其中 {estimated_n} 局使用估算回退。" if estimated_n
                     else "当前曲线未使用福利估算回退。")
    profit_points = [(d["day"], d["cumulative_profit"]) for d in timeline]
    profit_details = []
    for d in timeline:
        daily_profit = d.get("profit")
        daily_profit_text = "-" if daily_profit is None else f"{daily_profit:+,.0f}"
        estimated_text = f' · 其中 {d["estimated_n"]} 局估算' if d.get("estimated_n") else ""
        profit_details.append(
            f'{d["day"]} · {d["n"]} 局 · 当日收益 {daily_profit_text} · '
            f'累计收益 {d["cumulative_profit"]:+,.0f}{estimated_text}'
        )
    value_days = [d for d in timeline if d.get("avg_value") is not None]
    value_points = [(d["day"], d["avg_value"]) for d in value_days]
    value_details = [f'{d["day"]} · {d["n"]} 局 · 日均仓库价值 {d["avg_value"]:,.0f}' for d in value_days]
    profit_svg = _trend_svg(profit_points, color="var(--acc)", label="累计收益", details=profit_details)
    daily_profit_svg = _profit_bar_svg(timeline)
    daily_profit_days = min(21, len(timeline))
    value_svg = _trend_svg(value_points, color="var(--q3)", label="平均仓库价值", details=value_details)
    date_start = summary.get("date_start")
    date_end = summary.get("date_end")
    if date_start and date_end:
        date_range = date_start if date_start == date_end else f"{date_start} 至 {date_end}"
        date_range_note = f"数据覆盖：{date_range}（{len(timeline)} 个北京时间游玩日）"
        if date_start == date_end:
            date_range_note += "；当前为单日样本，曲线只显示一个日期点"
    else:
        date_range_note = "数据覆盖：缺少可用游玩时间"

    map_meta = _map_meta()

    # 常打的地图(条形)
    mrows = []
    mmax = max((m["n"] for m in summary.get("maps", [])), default=1) or 1
    for m in summary.get("maps", []):
        w = round(m["n"] / mmax * 100)
        avg = round(m["value"] / m["n"]) if m["n"] else 0
        mrows.append(
            f'<tr><td class="name">{_map_label_html(m["map_id"], map_meta)}</td>'
            f'<td data-v="{m["n"]}" class="num strong">'
            f'<div class="inbar"><span>{m["n"]} 局</span>'
            f'<span class="track"><i style="width:{w}%;background:var(--acc)"></i></span></div></td>'
            f'<td data-v="{avg}" class="num">{ui.money(avg)}</td></tr>')

    # 最近对局：首屏 10 局；第一次展开到最新一局所在北京时间自然日往前 3 天；第二次最多 500 局。
    # 以最新游玩日为锚而不是服务器“今天”，避免用户隔几天没玩后打开页面得到空的“近 3 天”。
    rrows = []
    rendered_samples = samples[:500]
    latest_day = next(
        (_shanghai_day(ts) for r in rendered_samples if (ts := _record_ts(r)) and _shanghai_day(ts)),
        None,
    )
    cutoff_day = (
        (datetime.strptime(latest_day, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
        if latest_day else None
    )
    recent_three_day_count = 0
    for index, r in enumerate(rendered_samples):
        q = _qmap(r.get("final_quality_counts"))
        red, gold = q.get("q6", 0), q.get("q5", 0)
        ts = _record_ts(r) or 0
        day = _shanghai_day(ts) if ts else None
        in_recent_three_days = bool(day and cutoff_day and day >= cutoff_day)
        if index < 10 or in_recent_three_days:
            recent_three_day_count += 1
        when = (_shanghai_format(ts, "%m-%d %H:%M") or "-") if ts else "-"
        v = r.get("known_value_sum") or 0
        won = _won_by_self(r) is True
        my_bid = _self_bid_of(r)
        profit, profit_estimated = _profit_of(r)
        bid_cell = (
            f'<td data-v="{my_bid}" class="num">{ui.money(my_bid)}</td>'
            if my_bid is not None else '<td class="num" style="color:var(--dim)">—</td>'
        )
        if profit is None:
            profit_cell = '<td class="num" style="color:var(--dim)">—</td>'
        else:
            profit_rounded = round(profit)
            profit_prefix = "≈" if profit_estimated else ""
            profit_color = "var(--pos)" if profit_rounded >= 0 else "var(--neg)"
            profit_title = ' title="老记录缺少福利真值，按赢家亏损的10%估算"' if profit_estimated else ""
            profit_cell = (
                f'<td data-v="{profit}" class="num strong" style="color:{profit_color}"{profit_title}>'
                f'{profit_prefix}{profit_rounded:+,}</td>'
            )
        hidden = "" if index < 10 else " hidden"
        rrows.append(
            f'<tr class="record-row" data-recent="{int(in_recent_three_days)}"{hidden}>'
            f'<td data-v="{ts}" class="name">{when}</td>'
            f'<td class="name">{_map_label_html(r.get("map_id"), map_meta)}</td>'
            f'<td class="num">{r.get("hero", "-")}</td>'
            f'<td data-v="{v}" class="num strong">{ui.money(v)}</td>'
            f'{bid_cell}{profit_cell}'
            f'<td data-v="{red}" class="num" style="color:var(--red)">{red or ""}</td>'
            f'<td data-v="{gold}" class="num" style="color:var(--gold)">{gold or ""}</td>'
            f'<td>{"🏆" if won else ""}</td></tr>')
    rendered_count = len(rendered_samples)
    initial_count = min(10, rendered_count)
    recent_three_day_count = max(initial_count, recent_three_day_count)
    records_button = ""
    if rendered_count > initial_count:
        records_button = (
            '<button type="button" id="records-more" class="records-more" data-stage="0" '
            f'data-recent-count="{recent_three_day_count}" data-rendered-count="{rendered_count}">'
            f'显示最近 3 天（{recent_three_day_count} 局）</button>'
        )

    # 我拿到过的稀有藏品
    irows = []
    for i in summary.get("rare", []):
        qq = i.get("quality") or 0
        irows.append(
            f'<tr><td class="name">{i["name"]}</td>'
            f'<td data-v="{qq}"><span class="q q{qq}">{QLAB.get(qq, "?")}</span></td>'
            f'<td data-v="{i.get("value") or 0}" class="num strong">{ui.money(i.get("value"))}</td>'
            f'<td data-v="{i["seen"]}" class="num">{i["seen"]}</td></tr>')

    body = f"""
<style>
.trend-wrap{{position:relative;overflow-x:auto;overscroll-behavior-inline:contain}}
.records-trend-chart{{min-width:max(100%,var(--records-chart-min-width,100%))}}
.trend-point{{cursor:help;outline:none}}
.trend-hit{{fill:transparent;pointer-events:all}}
.trend-dot{{stroke:var(--card);stroke-width:2;pointer-events:none;transition:r .12s ease,stroke-width .12s ease}}
.trend-guide{{stroke:var(--dim);stroke-width:1;stroke-dasharray:3 4;opacity:0;pointer-events:none}}
.trend-point:hover .trend-dot,.trend-point:focus .trend-dot,.trend-point.is-active .trend-dot{{r:6;stroke-width:3}}
.trend-point:hover .trend-guide,.trend-point:focus .trend-guide,.trend-point.is-active .trend-guide{{opacity:.42}}
.trend-y-grid{{opacity:.55}}
.trend-zero-line{{stroke-width:1.35}}
.profit-point{{cursor:help;outline:none}}
.profit-hit{{fill:transparent;pointer-events:all}}
.profit-bar,.profit-missing{{pointer-events:none}}
.profit-guide{{stroke:var(--dim);stroke-width:1;stroke-dasharray:3 4;opacity:0;pointer-events:none}}
.profit-point:hover .profit-bar,.profit-point:focus .profit-bar,.profit-point.is-active .profit-bar{{opacity:.78;stroke:var(--fg);stroke-width:2}}
.profit-point:hover .profit-guide,.profit-point:focus .profit-guide,.profit-point.is-active .profit-guide{{opacity:.42}}
.profit-y-grid{{opacity:.55}}
.profit-zero-line{{stroke-width:1.35}}
.trend-tooltip{{position:fixed;z-index:20;display:none;max-width:320px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);box-shadow:0 8px 24px #0002;font-size:12px;line-height:1.5;pointer-events:none}}
.profit-switch{{display:flex;gap:6px;align-items:center;flex-wrap:wrap}}
.profit-switch button{{width:auto;margin:0;padding:5px 11px;border:1px solid var(--line);border-radius:999px;background:transparent;color:var(--dim);font-size:12px}}
.profit-switch button[aria-selected="true"]{{background:var(--acc);border-color:var(--acc);color:white}}
.profit-panel[hidden]{{display:none}}
.records-actions{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;border-top:1px solid var(--line)}}
.records-status{{color:var(--dim);font-size:12px}}
.records-more{{width:auto;margin:0;padding:8px 14px;font-size:12px}}
@media (hover:none) and (pointer:coarse), (max-width:520px){{
  .profit-bars{{min-width:max(100%,var(--records-chart-min-width,100%))}}
  .profit-switch button{{min-width:46px;min-height:46px}}
}}
</style>
<div class="head">
  <h1>我的记录</h1>
  <div class="sub">只有你能看到这一页 · <b>实时</b>更新(打完一局结算后即可刷新看到) · 时间均为北京时间(UTC+8)<br><b>{window_note or date_range_note}</b><br>{date_range_note}</div>
</div>

<div class="kpis">
  <div class="kpi acc"><b>{n}</b><span>对局数</span></div>
  <div class="kpi gold"><b>{ui.money(summary.get('avg_value'))}</b><span>平均仓库价值</span></div>
  <div class="kpi"><b>{wr_txt}</b><span>拿下率{'' if wr is not None else '(无数据)'}</span></div>
  <div class="kpi {'acc' if profit_total is None or profit_total >= 0 else 'red'}"><b>{profit_txt}</b><span>{profit_label}</span></div>
  <div class="kpi red"><b>{ui.money((best.get('known_value_sum') or 0))}</b><span>最高一局</span></div>
</div>

<div class="card">
  <h2>盈亏走势 <small>{date_range_note} · {summary.get('profit_n', 0)} 局可计算</small></h2>
  <div class="pad profit-switch" role="tablist" aria-label="盈亏图表视图">
    <button type="button" role="tab" aria-selected="true" data-profit-panel="daily-profit">每日盈亏</button>
    <button type="button" role="tab" aria-selected="false" data-profit-panel="cumulative-profit">累计收益</button>
    <span style="color:var(--dim);font-size:12px">每日图显示最近 {daily_profit_days} 个北京时间游玩日 · <b style="color:var(--pos)">绿=盈利</b> / <b style="color:var(--neg)">红=亏损</b></span>
  </div>
  <div class="pad trend-wrap profit-panel" id="daily-profit">{daily_profit_svg}</div>
  <div class="pad trend-wrap profit-panel" id="cumulative-profit" hidden>{profit_svg}</div>
  <p class="note">每位玩家(含拿仓者)均获赢家亏损的 10% 分红；拍下 = 仓库真值 − 赢家出价 + 分红 − 门票，未拍下优先用结算帧福利真值。{estimate_note}</p>
</div>

<div class="card">
  <h2>仓库价值走势 <small>{date_range_note} · 按北京时间游玩日平均</small></h2>
  <div class="pad trend-wrap">{value_svg}</div>
</div>

<div class="card">
  <h2>最近对局 <small>默认 10 局 · 第一次展开最近 3 天 · 全部最多 500 局 · 点表头排序</small></h2>
  <div class="scroll"><table data-sortable>
    <thead><tr><th>时间(北京)</th><th>地图</th><th>英雄</th><th>仓库价值</th><th>我的出价</th><th>当局盈亏</th><th>红</th><th>金</th><th>拿下</th></tr></thead>
    <tbody>
{chr(10).join(rrows)}
    </tbody>
  </table></div>
  <div class="records-actions">
    <span id="records-status" class="records-status">当前显示 {initial_count} / {rendered_count} 局 · ≈ 为旧记录福利估算</span>
    {records_button}
  </div>
</div>

<div class="card">
  <h2>常打的地图</h2>
  <div class="scroll"><table data-sortable>
    <thead><tr><th>地图</th><th>局数</th><th>平均仓库价值</th></tr></thead>
    <tbody>
{chr(10).join(mrows)}
    </tbody>
  </table></div>
</div>

<div class="card">
  <h2>我遇到过的稀有藏品 <small>{len(summary.get('rare', []))} 种</small></h2>
  <div class="scroll"><table data-sortable>
    <thead><tr><th>藏品</th><th>品质</th><th>价值</th><th>遇到次数</th></tr></thead>
    <tbody>
{chr(10).join(irows)}
    </tbody>
  </table></div>
</div>

<div style="text-align:center"><a href="/my" style="color:var(--dim);font-size:13px">返回并重新验证</a></div>
<div id="trend-tooltip" class="trend-tooltip" role="tooltip"></div>
<script>
(function(){{
  const tip=document.getElementById("trend-tooltip");
  let pinned=null;
  let focusSuppressed=null;
  function moveTip(e){{
    const pad=14, w=tip.offsetWidth||260, h=tip.offsetHeight||42;
    const maxX=Math.max(pad,window.innerWidth-w-pad);
    const maxY=Math.max(pad,window.innerHeight-h-pad);
    const x=Math.min(maxX, Math.max(pad,(Number(e.clientX)||pad)+12));
    const y=Math.min(maxY, Math.max(pad,(Number(e.clientY)||pad)+12));
    tip.style.left=x+"px"; tip.style.top=y+"px";
  }}
  function hide(point){{
    if(point)point.classList.remove("is-active");
    tip.style.display="none";
  }}
  const points=Array.from(document.querySelectorAll(".trend-point,.profit-point"));
  function visibleChartPeers(point){{
    const svg=point.closest("svg");
    if(!svg||svg.closest("[hidden]"))return [];
    return Array.from(svg.querySelectorAll(".trend-point,.profit-point")).filter(function(peer){{
      return !peer.closest("[hidden]");
    }});
  }}
  function ownedChartHit(point){{
    if(!point||!point.querySelector)return null;
    return point.classList.contains("profit-point")
      ?point.querySelector(".profit-hit")
      :point.querySelector(".trend-hit");
  }}
  function revealOwnedChartHit(point){{
    const hit=ownedChartHit(point);
    const wrap=point&&point.closest?point.closest(".trend-wrap"):null;
    if(!hit||!wrap)return hit;
    const r=hit.getBoundingClientRect();
    const viewport=wrap.getBoundingClientRect();
    const inset=2;
    const left=viewport.left+inset;
    const right=Math.min(viewport.right,viewport.left+wrap.clientWidth)-inset;
    if(r.left<left)wrap.scrollLeft-=left-r.left;
    else if(r.right>right)wrap.scrollLeft+=r.right-right;
    return hit;
  }}
  function showFromOwnedHit(point){{
    const hit=revealOwnedChartHit(point);
    if(!hit)return;
    const r=hit.getBoundingClientRect();
    tip.textContent=point.dataset.tip||"";
    tip.style.display="block";
    moveTip({{clientX:r.left+r.width/2,clientY:r.top+r.height/2}});
  }}
  points.forEach(function(point){{
    function show(e){{tip.textContent=point.dataset.tip||"";tip.style.display="block";moveTip(e);}}
    function showFromCenter(){{showFromOwnedHit(point);}}
    function showFromPointer(e){{focusSuppressed=null;show(e);}}
    point.addEventListener("pointerenter",showFromPointer);
    point.addEventListener("pointermove",moveTip);
    point.addEventListener("pointerleave",function(){{if(pinned!==point)hide(point);}});
    // 部分内嵌浏览器只派发传统 mouse 事件；同时绑定以保证 hover 浮层可见。
    point.addEventListener("mouseenter",showFromPointer);
    point.addEventListener("mousemove",moveTip);
    point.addEventListener("mouseleave",function(){{if(pinned!==point)hide(point);}});
    point.addEventListener("focus",function(){{if(focusSuppressed!==point)showFromCenter();}});
    point.addEventListener("blur",function(){{if(focusSuppressed===point)focusSuppressed=null;if(pinned!==point)hide(point);}});
    point.addEventListener("keydown",function(e){{
      if(e.key==="Escape"){{
        focusSuppressed=point;
        if(pinned===point)pinned=null;
        hide(point);e.preventDefault();e.stopPropagation();return;
      }}
      if(e.key==="Enter"||e.key===" "||e.key.startsWith("Arrow")){{
        focusSuppressed=null;
        if(e.key.startsWith("Arrow")){{
          const delta=e.key==="ArrowLeft"||e.key==="ArrowUp"?-1:1;
          const peers=visibleChartPeers(point);
          const index=peers.indexOf(point);
          if(index>=0){{
            const next=peers[Math.max(0,Math.min(peers.length-1,index+delta))];
            const alreadyFocused=document.activeElement===next;
            next.focus({{preventScroll:true}});
            if(alreadyFocused)showFromOwnedHit(next);
          }}
        }}else showFromCenter();
        e.preventDefault();e.stopPropagation();
      }}
    }});
    point.addEventListener("click",function(e){{
      e.stopPropagation();
      focusSuppressed=null;
      if(pinned===point){{pinned=null;hide(point);return;}}
      if(pinned)pinned.classList.remove("is-active");
      pinned=point;point.classList.add("is-active");show(e);
    }});
  }});
  document.addEventListener("click",function(){{if(pinned)hide(pinned);pinned=null;}});
  document.addEventListener("keydown",function(e){{if(e.key==="Escape"){{const focused=document.activeElement&&document.activeElement.closest?document.activeElement.closest(".trend-point,.profit-point"):null;if(focused){{focusSuppressed=focused;hide(focused);}}if(pinned)hide(pinned);pinned=null;}}}});

  document.querySelectorAll("[data-profit-panel]").forEach(function(button){{
    button.addEventListener("click",function(e){{
      e.stopPropagation();
      const active=document.activeElement;
      const focusWasInPanel=!!(active&&active.closest&&active.closest(".profit-panel"));
      if(pinned)hide(pinned);pinned=null;
      focusSuppressed=null;
      document.querySelectorAll("[data-profit-panel]").forEach(function(other){{
        other.setAttribute("aria-selected",String(other===button));
      }});
      document.querySelectorAll(".profit-panel").forEach(function(panel){{
        panel.hidden=panel.id!==button.dataset.profitPanel;
      }});
      if(focusWasInPanel)button.focus();
    }});
  }});

  const button=document.getElementById("records-more");
  const rows=Array.from(document.querySelectorAll("tr.record-row"));
  const status=document.getElementById("records-status");
  if(!button)return;
  button.addEventListener("click",function(){{
    const stage=Number(button.dataset.stage||0)+1;
    button.dataset.stage=String(stage);
    if(stage===1){{
      rows.forEach(function(row,index){{row.hidden=!(index<10||row.dataset.recent==="1");}});
      const count=rows.filter(function(row){{return !row.hidden;}}).length;
      status.textContent="当前显示最近 3 天 "+count+" / "+rows.length+" 局";
      if(count<rows.length){{button.textContent="显示全部（最多 500 局）";}}
      else{{button.hidden=true;}}
    }}else{{
      rows.forEach(function(row){{row.hidden=false;}});
      status.textContent="当前显示全部 "+rows.length+" 局";
      button.hidden=true;
    }}
  }});
}})();
</script>
"""
    return ui.page(title="我的记录 · lemyes&加菲计算器", active="my", body=body,
                   foot="这一页的数据只有你自己看得到(用你的 token 验证身份)。公开的大数据页不包含任何个人信息。")


QLAB = {1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}
