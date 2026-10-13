# -*- coding: utf-8 -*-
"""服务端分析看板:把收集到的【聚合玩家样本】渲染成自包含 HTML(作者在 VPS 上看)。

左导航(功能区)+ 右详情。嵌入匿名样本原始行, 浏览器端按 地图族/地图/英雄/时间 动态聚合:
  · 总览        — KPI + 按地图/英雄分布
  · 爆率快照    — 每图每品质 平均件数/均格/加权均价(当前掉率)
  · 时间趋势    — 按天/周, 每品质均件随时间变化(看改版前后掉率漂移)
  · 地图对比    — 多图品质件数/价值并排对比
  · 藏品占比    — 品质件数/价值占比(堆叠条)
  · 玩家        — 按昵称(游戏内显示名)聚合: 场次/掉率构成/均拍品值/活跃区间; 点行钻取该玩家

UI 使用情况 / 单藏品(物品级)占比 暂无: 上传只发对局 gameplay 品质聚合, 未采 UI 事件 / 物品明细 -> 后续补字段即可加。

用法:
  python scripts/sample_dashboard.py --samples data/collected/samples.jsonl --out dashboard.html
collector 内置 GET /dashboard 动态生成(见 sample_collector_server.py)。
"""
from __future__ import annotations
import argparse, json, math, re, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_match_records import (  # noqa: E402
    _load_map_meta, _family_from_id, _FEE_BY_FAMILY, categorize_skill, paid_items,
)
from public_stats import _sample_hero_slug, _source_is_mixed_sample  # noqa: E402

# q2(绿)并入 q1, 与引擎/reconstruct 一致。
_QKEY = {"q1": "q1", "q2": "q1", "q3": "q3", "q4": "q4", "q5": "q5", "q6": "q6"}
_QORDER = ["q1", "q3", "q4", "q5", "q6"]  # 白(含绿)/蓝/紫/金/红
_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _qmap(s) -> dict:
    """'q1=1;q2=10;q3=17' -> {'q1':11,'q3':17}(q2 并入 q1)。"""
    out: dict = defaultdict(float)
    for part in str(s or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            k = _QKEY.get(k.strip())
            if k:
                try:
                    out[k] += float(v)
                except ValueError:
                    pass
    return {k: round(v, 3) for k, v in out.items()}


def _sample_unknown_quality_observations(sample: dict) -> tuple[dict, dict]:
    """Parse one sample's unknown quality diagnostics once.

    ``build_dashboard_html`` used to reopen and parse the complete JSONL source three
    times: core rows, warning totals and anomaly rows. Returning both diagnostic
    views from one per-sample pass keeps the public contract while letting the server
    consume a large snapshot exactly once.
    """
    count_hits: dict = {}
    bad: dict = {}
    for field, sub in (
        ("final_quality_counts", "cnt"),
        ("final_quality_cells", "cel"),
        ("final_quality_values", "val"),
    ):
        for part in str(sample.get(field) or "").split(";"):
            key, _, raw_value = part.partition("=")
            key = key.strip()
            if not key or key in _QKEY:
                continue
            entry = bad.setdefault(key, {})
            try:
                value = float(raw_value)
                entry[sub] = round(value, 3)
                if sub == "cnt":
                    count_hits[key] = count_hits.get(key, 0.0) + value
            except ValueError:
                entry.setdefault(sub, None)
                if sub == "cnt":
                    count_hits.setdefault(key, 0.0)
    return count_hits, bad


def _record_unknown_quality_diagnostics(
    sample: dict,
    *,
    warnings: dict | None,
    anomalies: list | None,
    map_meta: dict | None = None,
) -> None:
    count_hits, bad = _sample_unknown_quality_observations(sample)
    if warnings is not None:
        for key, count in count_hits.items():
            entry = warnings.setdefault(
                key, {"games": 0, "items": 0.0, "ex": f"{key}={count:g}"}
            )
            entry["games"] += 1
            entry["items"] += count
    if anomalies is None or not bad:
        return
    meta = map_meta or {}
    map_id = sample.get("map_id")
    map_row = meta.get(map_id) or {}
    anomalies.append({
        "map": map_id,
        "name": map_row.get("name") or str(map_id),
        "fam": map_row.get("family") or _family_from_id(map_id),
        "hero": _sample_hero_slug(sample.get("hero")),
        "round": sample.get("round"),
        "sess": sample.get("session_hash"),
        "ts": _ts(sample),
        "bad": bad,
    })


def _scan_unknown_qualities(samples: Iterable[dict]) -> dict:
    """扫描结算【件数】串里 _QKEY 没覆盖的品质档,返回 {key:{games,items,ex}}。

    为什么:游戏新版可能引入新品质档(如 06-25 神秘箱子/排位 S1 专属藏品)。_qmap 只认 q1-q6,
    没见过的档会被静默丢弃——但上传发的是原始串、服务器原样存进样本,数据【不丢】。此扫描纯诊断,
    让看板顶部弹横幅一眼发现新档,提醒补 _QKEY/_QORDER 后重建即可追溯渲染。只扫件数串(代表新一类
    藏品出现),sum 同档累加、games 记出现局数、ex 留一个示例片段。"""
    warnings: dict = {}
    for sample in samples:
        _record_unknown_quality_diagnostics(
            sample, warnings=warnings, anomalies=None
        )
    return warnings


def _collect_anomaly_samples(samples: Iterable[dict]) -> list:
    """含【未知品质档】(_QKEY 未覆盖 = 非 q1-q6, 如单件解析毛刺 q10)的样本清单 —— 单独列到看板
    "异常样本"侧栏, 专门筛出问题部分(可回放/待查), 不污染正常界面观测。**正常聚合本就不含这些**:
    `_qmap`/`avgQ` 只认 _QKEY 的档, 未知档已从 cnt/cel/val 天然剔除(此处只是把"哪些样本有异常"留档)。
    匿名(无玩家 id/原始 session; 带 session_hash 供回放定位)。"""
    meta = _load_map_meta()
    anomalies: list = []
    for sample in samples:
        _record_unknown_quality_diagnostics(
            sample, warnings=None, anomalies=anomalies, map_meta=meta
        )
    anomalies.sort(key=lambda row: row.get("ts") or 0, reverse=True)
    return anomalies


def _ts(rec):
    # 优先【游玩时间】(client_ts/created_at)而非【收样时间】(received_ts):时间趋势/玩家活跃区间
    # 要按对局【实际发生】时间, 否则旧样本今天重传后全挤在上传当天(作者反馈"今天打的还在0624")。
    # received_ts 仅作无游玩时间时的回退。
    for k in ("client_ts", "created_at", "received_ts"):
        v = rec.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _row_for_client(
    sample: dict, *, map_meta: dict, include_details: bool
) -> dict | None:
    map_id = sample.get("map_id")
    if map_id is None or sample.get("settlement_incomplete"):
        return None
    map_row = map_meta.get(map_id) or {}
    latest_bids = (
        sample.get("latest_bids")
        if isinstance(sample.get("latest_bids"), dict)
        else {}
    )
    players = sample.get("players") or list(latest_bids.keys())
    winner = sample.get("winner") or sample.get("winner_name")
    if not winner and latest_bids:
        winner = max(
            latest_bids.items(),
            key=lambda pair: pair[1] if isinstance(pair[1], (int, float)) else -1,
        )[0]
    tools: list = []
    hero_skills: list = []
    if include_details:
        tools = list(sample.get("tools") or [])
        hero_skills = list(sample.get("hskills") or [])
        if not tools and not hero_skills and (
            isinstance(sample.get("skill_reveal_rows"), list)
            or isinstance(sample.get("action_result_rows"), list)
        ):
            for reveal in sample.get("skill_reveal_rows") or []:
                if not isinstance(reveal, dict):
                    continue
                category, name = categorize_skill(
                    reveal.get("skill_id"),
                    reveal.get("tool"),
                    sample.get("hero") or "?",
                )
                target = tools if category == "道具" else hero_skills
                if name and name not in target:
                    target.append(name)
            for name in paid_items(sample):
                if name not in tools:
                    tools.append(name)
    family = map_row.get("family") or _family_from_id(map_id)
    fee = _FEE_BY_FAMILY.get(family)
    if fee is None:
        fee = map_row.get("entry_fee") or 0
    welfare = sample.get("settlement_welfare")
    if (
        not isinstance(welfare, (int, float))
        or isinstance(welfare, bool)
        or not math.isfinite(float(welfare))
    ):
        welfare = None
    row = {
        "map": map_id,
        "name": map_row.get("name") or str(map_id),
        "fam": family,
        "fee": fee,
        "hero": _sample_hero_slug(sample.get("hero")),
        "player": sample.get("player") or sample.get("self_name") or "?",
        "parts": players,
        "winner": winner,
        "wbid": sample.get("winner_bid"),
        # 仅代表上传者本人(player)在未拿仓路径收到的结算帧 f5；不得套给其他参与者。
        "welfare": welfare,
        "rank_after": sample.get("rank_after"),
        "rank_pts": sample.get("rank_points"),
        "rank_delta": sample.get("rank_delta"),
        "rank_tier": sample.get("rank_tier"),
        "ts": _ts(sample),
        "lot": sample.get("known_value_sum") or 0,
        "cnt": _qmap(sample.get("final_quality_counts")),
        "cel": _qmap(sample.get("final_quality_cells")),
        "val": _qmap(sample.get("final_quality_values")),
        "source_mixed": _source_is_mixed_sample(sample),
    }
    if include_details:
        row.update({
            "rare": sample.get("rare_items") or [],
            "tools": tools,
            "hskills": hero_skills,
            "player_ranks": sample.get("player_ranks") or {},
        })
    return row


def _dashboard_sample_allowed(sample: object) -> bool:
    """Keep explicit server-quarantine rows out of every dashboard surface."""

    return (
        type(sample) is dict  # noqa: E721 - reject coercive mapping subclasses
        and dict.get(sample, "server_quarantine_reason") is None
    )


def _iter_rows_for_client(
    samples: Iterable[dict],
    *,
    include_details: bool = True,
) -> Iterator[dict]:
    """逐条样本生成紧凑行；调用方可直接打包/抽取 detail，不保留完整原始 dict 列表。"""
    map_meta = _load_map_meta()
    for sample in samples:
        if not _dashboard_sample_allowed(sample):
            continue
        row = _row_for_client(
            sample, map_meta=map_meta, include_details=include_details
        )
        if row is not None:
            yield row


def _rows_for_client(samples: Iterable[dict], *, include_details: bool = True) -> list:
    """兼容离线调用；生产服务器优先消费 _iter_rows_for_client。"""
    return list(_iter_rows_for_client(samples, include_details=include_details))


def _pack_rows_for_client(rows: Iterable[dict]) -> dict:
    """服务器大库首屏用的紧凑 wire shape；浏览器 hydrate 后恢复旧 row contract。"""
    names: list[str] = []
    name_index: dict[str, int] = {}

    def intern(value) -> int:
        if value is None:
            return -1
        text = str(value)
        if text not in name_index:
            name_index[text] = len(names)
            names.append(text)
        return name_index[text]

    map_meta: dict[str, list] = {}
    packed: list[list] = []
    for row in rows:
        mid = row.get("map")
        map_meta[str(mid)] = [row.get("name"), row.get("fam"), row.get("fee")]
        packed.append([
            mid,
            row.get("hero"),
            intern(row.get("player")),
            [intern(p) for p in (row.get("parts") or [])],
            intern(row.get("winner")),
            row.get("wbid"),
            row.get("rank_after"),
            row.get("rank_pts"),
            row.get("rank_delta"),
            row.get("rank_tier"),
            row.get("ts"),
            row.get("lot"),
            [(row.get("cnt") or {}).get(q) for q in _QORDER],
            [(row.get("cel") or {}).get(q) for q in _QORDER],
            [(row.get("val") or {}).get(q) for q in _QORDER],
            bool(row.get("source_mixed")),
            row.get("welfare"),
        ])
    return {"rows": packed, "row_format": "compact-v1", "names": names, "map_meta": map_meta}


def dashboard_detail_payload(
    samples: Iterable[dict], kind: str, *, max_items: int = 10_000,
) -> dict:
    """按模块返回有上限的稀疏详情；索引与当前服务端窗口核心 rows 一致。"""
    if kind not in {"rare", "tools", "player_ranks"}:
        raise ValueError(f"unsupported dashboard detail kind: {kind}")
    items: list[list] = []
    matched_items = 0
    for index, row in enumerate(_iter_rows_for_client(samples, include_details=True)):
        if kind == "tools":
            tools = row.get("tools") or []
            hskills = row.get("hskills") or []
            if tools or hskills:
                matched_items += 1
                if len(items) < max_items:
                    items.append([index, tools, hskills])
        else:
            value = row.get(kind) or ([] if kind == "rare" else {})
            if value:
                matched_items += 1
                if len(items) < max_items:
                    items.append([index, value])
    return {"kind": kind, "items": items, "matched_items": matched_items,
            "truncated": matched_items > len(items), "max_items": max_items}


def build_dashboard_html(
    samples: Iterable[dict], *, detail_urls: dict[str, str] | None = None,
    server_window: dict | None = None, quarantine_summary: dict | None = None, **_,
) -> str:
    # payload 放 <script type="application/json"> + 前端 JSON.parse: 大 payload(全库 ~30MB)下
    # JSON.parse 比 JS 对象字面量解析明显更快、峰值内存更低(引擎按数据而非代码路径解析)。
    # 转义只需 "</"→"<\/"(防 </script> 提前闭合): textContent 原样取回, 而 \/ 本就是合法 JSON 转义。
    # 英雄中文名: 从 public_stats 取【唯一真源】, 绝不在这里再抄一份 ——
    # 两份表迟早会漂(引擎加了英雄, 只更一边), 到时候两个页面显示不同的名字。
    from public_stats import _HERO_CN  # noqa: PLC0415

    payload = {"q_order": _QORDER, "hero_cn": dict(_HERO_CN)}
    warnings: dict = {}
    anomalies: list = []
    map_meta = _load_map_meta()
    include_details = not bool(detail_urls)

    def rows_with_diagnostics() -> Iterator[dict]:
        for sample in samples:
            if not _dashboard_sample_allowed(sample):
                continue
            _record_unknown_quality_diagnostics(
                sample,
                warnings=warnings,
                anomalies=anomalies,
                map_meta=map_meta,
            )
            row = _row_for_client(
                sample,
                map_meta=map_meta,
                include_details=include_details,
            )
            if row is not None:
                yield row

    if detail_urls:
        payload.update(_pack_rows_for_client(rows_with_diagnostics()))
        payload["detail_urls"] = dict(detail_urls)
    else:
        payload["rows"] = list(rows_with_diagnostics())
    anomalies.sort(key=lambda row: row.get("ts") or 0, reverse=True)
    payload["warn_quals"] = warnings
    payload["anomaly_samples"] = anomalies
    if server_window is not None:
        payload["server_window"] = {
            **server_window,
            "matched_rows": len(payload.get("rows") or []),
            "parsed_rows": int(getattr(samples, "last_parsed_count", 0)),
            "unknown_date_count": int(getattr(samples, "last_unknown_date_count", 0)),
        }
    if quarantine_summary is not None:
        payload["quarantine_summary"] = {
            key: int(quarantine_summary.get(key) or 0)
            for key in (
                "total",
                "warehouse_source_pending",
                "settlement_incomplete",
                "declared_shortfall",
                "invalid_value",
                "other",
                "unreadable",
                "bytes",
            )
        }
    return _TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))


class JsonlDashboardSource:
    """按服务端日期窗口重复流式扫描 dashboard 行；未知日期明确计数并排除。"""

    def __init__(
        self,
        path: Path,
        *,
        start: date | None = None,
        end: date | None = None,
        snapshot_bytes: int | None = None,
        history_index=None,
    ):
        self.path = Path(path)
        self.start = start
        self.end = end
        if snapshot_bytes is None:
            try:
                snapshot_bytes = self.path.stat().st_size
            except OSError:
                snapshot_bytes = 0
        self.snapshot_bytes = max(0, int(snapshot_bytes))
        self.last_parsed_count = 0
        self.last_matched_count = 0
        self.last_unknown_date_count = 0
        self.passes = 0
        self.payload_read_bytes = self.payload_read_rows = 0
        self.total_payload_read_bytes = self.total_payload_read_rows = 0
        self._indexed = None
        if history_index is not None:
            if self.path.resolve() != history_index.sources["main"].resolve():
                raise ValueError("history index source mismatch")
            self._indexed = history_index.rows(view="dashboard", start=start, end=end, snapshot_bytes=snapshot_bytes)

    def __len__(self) -> int:
        """兼容离线/测试调用；只流式计数，不保留逐局对象。"""
        return sum(1 for _ in self)

    def __iter__(self) -> Iterator[dict]:
        parsed = matched = unknown = 0
        self.passes += 1
        self.payload_read_bytes = self.payload_read_rows = 0
        if self._indexed is not None:
            for rec in self._indexed:
                # Retain the original consumer checks; the projection is only
                # an offset-selection accelerator, not a new qualification.
                if not _dashboard_sample_allowed(rec) or not rec.get("final_quality_counts"):
                    continue
                if self.start is not None or self.end is not None:
                    ts = _ts(rec)
                    if ts is None:
                        continue
                    try:
                        day_value = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
                    except (OverflowError, OSError, TypeError, ValueError):
                        continue
                    if self.start is not None and day_value < self.start:
                        continue
                    if self.end is not None and day_value > self.end:
                        continue
                matched += 1
                yield rec
            self.last_parsed_count = self._indexed.last_parsed_count
            self.last_matched_count = matched
            self.last_unknown_date_count = self._indexed.last_unknown_date_count
            self.payload_read_bytes = self._indexed.bytes_read
            self.payload_read_rows = self._indexed.rows_read
            self.total_payload_read_bytes += self.payload_read_bytes
            self.total_payload_read_rows += self.payload_read_rows
            return
        try:
            stream = self.path.open("rb")
        except OSError:
            self.last_parsed_count = self.last_matched_count = self.last_unknown_date_count = 0
            return
        with stream:
            remaining = self.snapshot_bytes
            while remaining > 0:
                raw_line = stream.readline(remaining)
                if not raw_line:
                    break
                remaining -= len(raw_line)
                self.payload_read_bytes += len(raw_line)
                self.payload_read_rows += 1
                self.total_payload_read_bytes += len(raw_line)
                self.total_payload_read_rows += 1
                line = raw_line.decode("utf-8", errors="replace")
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if (
                    not _dashboard_sample_allowed(rec)
                    or not rec.get("final_quality_counts")
                ):
                    continue
                parsed += 1
                if self.start is not None or self.end is not None:
                    ts = _ts(rec)
                    if ts is None:
                        unknown += 1
                        continue
                    try:
                        day_value = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
                    except (OverflowError, OSError, TypeError, ValueError):
                        unknown += 1
                        continue
                    if self.start is not None and day_value < self.start:
                        continue
                    if self.end is not None and day_value > self.end:
                        continue
                matched += 1
                yield rec
        self.last_parsed_count = parsed
        self.last_matched_count = matched
        self.last_unknown_date_count = unknown


def _load_samples(
    path: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    snapshot_bytes: int | None = None,
    history_index=None,
) -> JsonlDashboardSource:
    return JsonlDashboardSource(
        path,
        start=start,
        end=end,
        snapshot_bytes=snapshot_bytes,
        history_index=history_index,
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>仓库账本 · 数据台</title>
<style>
  /* 默认=Claude 暖光(奶油底+陶土橙);JS applyTheme 可切暖夜。变量驱动, 全站换肤。 */
  :root{
    --bg:#ece4d6; --rail:#e6dcc8; --panel:#f5efe4; --panel2:#efe6d5; --line:#ddd2bd;
    --txt:#3c3930; --dim:#8a8067; --faint:#b3a78c; --gold:#bf5d2c;
    --q1:#9b9685; --q3:#3f88cc; --q4:#9a5cc8; --q5:#c08c2c; --q6:#c94f3f;
    --pos:#3f9460; --neg:#c94f3f; --track:#e0d6c2; --hover:#e7dec9; --onaccent:#fff6ee;
    --mono:ui-monospace,"Cascadia Code",Consolas,"Courier New",monospace;
    --sans:"Microsoft YaHei","Segoe UI",system-ui,sans-serif;
  }
  *{box-sizing:border-box;} html,body{height:100%;}
  body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 var(--sans);display:flex;
    transition:background .2s, color .2s;}
  /* 左导航: 稀有度轨道 + 功能区。用背景分层而非硬边框分隔(避免侧栏硬边线)。 */
  .side{width:224px;flex:0 0 224px;background:var(--rail);box-shadow:1px 0 0 rgba(0,0,0,.08);
    display:flex;flex-direction:column;position:sticky;top:0;height:100vh;}
  .brand{padding:20px 20px 16px;}
  .brand .mk{font-family:var(--mono);font-size:10.5px;letter-spacing:4px;color:var(--gold);text-transform:uppercase;}
  .brand .nm{font-size:18px;font-weight:700;letter-spacing:2px;margin-top:3px;}
  .brand .sb{font-size:11px;color:var(--faint);margin-top:3px;letter-spacing:.5px;}
  nav{padding:10px 0;flex:1;overflow:auto;}
  .nav-eyebrow{font-size:10px;letter-spacing:2px;color:var(--faint);padding:8px 18px 4px;text-transform:uppercase;}
  .nav-item{display:flex;align-items:center;gap:10px;padding:9px 18px;cursor:pointer;color:var(--dim);
    border-left:2px solid transparent;border-radius:0 9px 9px 0;font-size:13.5px;user-select:none;}
  .nav-item:hover{color:var(--txt);background:var(--hover);}
  .nav-item.on{color:var(--txt);background:var(--panel);border-left-color:var(--gold);font-weight:600;}
  .nav-item .dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto;}
  .nav-item.lock{color:var(--faint);} .nav-item.lock:hover{color:var(--dim);}
  .side .foot{padding:12px 18px;border-top:1px solid var(--line);font-size:11px;color:var(--faint);}
  .side .foot a{display:inline-block;margin-top:7px;color:var(--dim);text-decoration:none;}
  .side .foot a:hover{color:var(--gold);}
  /* 稀有度图例条(轨道底部) */
  .spectrum{display:flex;height:4px;margin:0;}
  .spectrum span{flex:1;}
  /* 右侧 */
  main{flex:1;min-width:0;display:flex;flex-direction:column;height:100vh;overflow:hidden;}
  .topbar{padding:14px 26px;border-bottom:1px solid var(--line);background:var(--bg);
    display:flex;align-items:center;gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:2;}
  .topbar h2{margin:0;font-size:16px;font-weight:700;letter-spacing:.5px;}
  .topbar .vsub{color:var(--dim);font-size:12px;}
  .filters{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
  .filters label{font-size:11px;color:var(--faint);margin-right:-4px;}
  select,.seg{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;
    padding:5px 9px;font:12.5px var(--sans);cursor:pointer;}
  input.search{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;
    padding:6px 11px;font:13px var(--sans);width:240px;outline:none;}
  input.search:focus{border-color:var(--gold);}
  input.search::placeholder{color:var(--faint);}
  input.dt{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;
    padding:4px 7px;font:12px var(--sans);outline:none;color-scheme:dark;}
  input.dt:focus{border-color:var(--gold);}
  .hovcol:hover .hovline{opacity:.7;}   /* 折线图悬停列显示竖向引导线 */
  .hovcol:hover rect{fill:rgba(191,93,44,.06);}
  .seg{display:inline-flex;padding:0;overflow:hidden;}
  .seg b{padding:5px 10px;font-weight:500;color:var(--dim);border-right:1px solid var(--line);}
  .seg b:last-child{border-right:0;} .seg b.on{background:var(--gold);color:var(--onaccent);font-weight:700;}
  .btn{background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:7px;
    padding:5px 11px;font:12.5px var(--sans);cursor:pointer;} .btn:hover{border-color:var(--gold);}
  .theme-btn{min-width:62px;font-weight:600;}
  /* 图表 */
  .cards-row{display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start;}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;}
  .chart-svg{display:block;} .chart-svg text{font-family:var(--sans);}
  .donut-cap{text-align:center;font-size:12px;color:var(--dim);margin-top:6px;}
  .content{padding:20px 26px 40px;overflow:auto;flex:1;}
  /* 卡片 / 表 / 条 */
  .kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px;}
  .kpi{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
    border-radius:11px;padding:13px 18px;min-width:128px;}
  .kpi .v{font-family:var(--mono);font-size:23px;font-weight:600;color:var(--gold);line-height:1.1;}
  .kpi .k{font-size:11.5px;color:var(--dim);margin-top:3px;letter-spacing:.3px;}
  .sec{font-size:11px;letter-spacing:2px;color:var(--faint);text-transform:uppercase;margin:22px 0 9px;
    display:flex;align-items:center;gap:8px;}
  .sec::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--gold);}
  table{border-collapse:collapse;width:100%;font-size:13px;}
  th,td{padding:7px 11px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;}
  th:first-child,td:first-child,.l{text-align:left;}
  thead th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.4px;position:sticky;top:0;background:var(--bg);}
  tbody tr:hover{background:var(--panel);} td.num,th.num{font-family:var(--mono);}
  .tag{display:inline-block;padding:1px 7px;border-radius:10px;background:var(--panel2);color:var(--dim);font-size:11px;}
  .qbar{display:inline-flex;height:9px;border-radius:3px;overflow:hidden;vertical-align:middle;min-width:46px;background:var(--track);}
  .qbar i{display:block;height:100%;}
  .stack{display:flex;height:18px;border-radius:5px;overflow:hidden;background:var(--track);font-size:0;}
  .stack i{height:100%;}
  .legend{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--dim);margin:4px 0 14px;}
  .legend b{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:4px;vertical-align:-1px;}
  .empty{padding:50px;text-align:center;color:var(--faint);}
  .lockbox{max-width:620px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;}
  .lockbox h3{margin:0 0 8px;color:var(--gold);font-size:15px;}
  .lockbox p{color:var(--dim);font-size:13px;margin:6px 0;}
  .pill{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:3px 11px;font-size:11px;color:var(--faint);margin-top:6px;}
  .muted{color:var(--faint);font-size:12px;}
  /* 标准差后缀。踩过两版:
     ① font-size:10px 内联 -> 真截图里【和均值糊成一个数】("13.11±4.29" 看着像 13.114.29)。
     ② 换行了但仍是 10px + faint + opacity:.8 -> 【作者反馈"没看到, 只看到单一数字"】= 等于没做。
     现在: 12px + 中性色 + 淡底色块, 让它成为一个能被【一眼注意到】的独立标记。
     教训: "我截图里看得见" ≠ "用户看得见"。以用户实际看到的为准。 */
  .pm{display:block;width:fit-content;margin:3px 0 0 auto;padding:1px 5px;border-radius:3px;
      color:var(--dim);font-size:12px;line-height:1.3;white-space:nowrap;
      background:rgba(140,128,103,.14);}
  .spark{display:flex;align-items:flex-end;gap:2px;height:34px;}
  .spark i{width:7px;background:var(--gold);border-radius:1px 1px 0 0;min-height:1px;opacity:.85;}
  /* 未知品质档告警条(游戏新版引入新档时弹, 平时 :empty 隐藏; ✕ 记住本批签名, 新档出现会重新弹) */
  #warnbar:empty{display:none;}
  #warnbar{padding:9px 26px;background:rgba(191,93,44,.14);border-bottom:1px solid var(--gold);
    color:var(--txt);font-size:12.5px;line-height:1.5;display:flex;align-items:center;gap:14px;}
  #warnbar b{color:var(--gold);}
  #warnbar .warnx{flex:0 0 auto;cursor:pointer;color:var(--gold);font-size:15px;line-height:1;
    padding:3px 8px;border-radius:4px;user-select:none;}
  #warnbar .warnx:hover{background:rgba(191,93,44,.22);}
  /* 参与排序的表头: 框住(作者0711交互) —— 内描边+淡底, 配合角标 1▼ 2▲ 显示联合排序优先级 */
  th.sorton{box-shadow:inset 0 0 0 1.5px var(--gold);background:rgba(191,93,44,.10);border-radius:4px;}
</style></head><body>
<aside class="side">
  <div class="brand"><div class="mk">BidKing</div><div class="nm">仓库账本</div><div class="sb">玩家数据 · 掉率台</div></div>
  <nav id="nav"></nav>
  <div class="spectrum"><span style="background:var(--q1)"></span><span style="background:var(--q3)"></span><span style="background:var(--q4)"></span><span style="background:var(--q5)"></span><span style="background:var(--q6)"></span></div>
  <div class="foot" id="sidefoot"></div>
</aside>
<main>
  <div class="topbar">
    <div><h2 id="vtitle"></h2><div class="vsub" id="vsub"></div></div>
    <div class="filters" id="filters"></div>
    <button class="btn theme-btn" id="themeBtn" onclick="cycleTheme()" title="切换配色"></button>
  </div>
  <div id="warnbar"></div>
  <div class="content" id="content"></div>
</main>
<script type="application/json" id="__P__">__DATA__</script>
<script>
const P = JSON.parse(document.getElementById("__P__").textContent);
const SERVER_WINDOW = P.server_window || {};
const QUARANTINE_SUMMARY = P.quarantine_summary || {};
// 时间戳仍是绝对 epoch；日/周/小时口径固定为北京时间。先平移 8 小时，再只用 UTC getter，
// 这样访问者在美加或 VPS 使用 UTC 时，同一局仍会落入同一个北京时间自然日。
const SHANGHAI_OFFSET_MS = 8*60*60*1000;
const QO = P.q_order;
function hydrateRows(p){
  if(p.row_format!=="compact-v1") return p.rows||[];
  const names=p.names||[], maps=p.map_meta||{};
  const quality=a=>{ const out={}; (p.q_order||[]).forEach((q,i)=>{ if(a&&a[i]!=null) out[q]=a[i]; }); return out; };
  return (p.rows||[]).map(a=>{ const m=maps[String(a[0])]||[];
    return {map:a[0],name:m[0]||String(a[0]),fam:m[1],fee:m[2]==null?0:m[2],hero:a[1],
      player:names[a[2]]||"?",parts:(a[3]||[]).map(i=>names[i]).filter(Boolean),
      winner:a[4]>=0?(names[a[4]]||null):null,wbid:a[5],rank_after:a[6],rank_pts:a[7],
      rank_delta:a[8],rank_tier:a[9],ts:a[10],lot:a[11],cnt:quality(a[12]),cel:quality(a[13]),val:quality(a[14]),
      source_mixed:Boolean(a[15]),welfare:a[16]};
  });
}
const ROWS = hydrateRows(P).map(r=>({...r, _d:r.ts?new Date(r.ts*1000+SHANGHAI_OFFSET_MS):null}));
// ---- 主题(Claude 暖色: 默认暖光, 可切暖夜)----
const THEMES = {
  warm: { label:"☀ 暖光", v:{ bg:"#ece4d6",rail:"#e6dcc8",panel:"#f5efe4",panel2:"#efe6d5",line:"#ddd2bd",
    txt:"#3c3930",dim:"#8a8067",faint:"#b3a78c",gold:"#bf5d2c",track:"#e0d6c2",hover:"#e7dec9",onaccent:"#fff6ee",
    q1:"#9b9685",q3:"#3f88cc",q4:"#9a5cc8",q5:"#c08c2c",q6:"#c94f3f",pos:"#3f9460",neg:"#c94f3f" }},
  cream:{ label:"◐ 暖纸", v:{ bg:"#f3ede1",rail:"#ece4d4",panel:"#fbf7ef",panel2:"#f4ecdc",line:"#e3dac8",
    txt:"#423d31",dim:"#928876",faint:"#bcb199",gold:"#c4632f",track:"#e8dfcc",hover:"#efe7d6",onaccent:"#fff6ee",
    q1:"#a39d8b",q3:"#4189c9",q4:"#9d5fc6",q5:"#c2902f",q6:"#cd5444",pos:"#43966a",neg:"#cd5444" }},
  dark: { label:"☾ 暖夜", v:{ bg:"#1b1714",rail:"#15110d",panel:"#241e17",panel2:"#2c251c",line:"#3a3025",
    txt:"#ece5d9",dim:"#9c9379",faint:"#6c6452",gold:"#d98f5a",track:"#120f0b",hover:"#231d15",onaccent:"#1b1714",
    q1:"#c9ced8",q3:"#4f9be0",q4:"#b06fe0",q5:"#e0a64f",q6:"#e0594f",pos:"#5cba6a",neg:"#e0594f" }},
};
const THEME_ORDER = ["warm","cream","dark"];
let curTheme = "warm";
// 品质色只引用 CSS 变量。主题切换时浏览器会直接重绘这些颜色，不需要重新扫描
// 近 5 万行样本并重建整页（线上 0713 快照实测一次主题切换约 0.67s）。
const QC = {q1:"var(--q1)",q3:"var(--q3)",q4:"var(--q4)",q5:"var(--q5)",q6:"var(--q6)"};
function applyTheme(name){
  const t = THEMES[name] || THEMES.warm; curTheme = THEMES[name] ? name : "warm";
  const r = document.documentElement.style;
  for(const k in t.v) r.setProperty("--"+k, t.v[k]);
  const b=document.getElementById("themeBtn"); if(b) b.textContent=t.label;
  try{ localStorage.setItem("bk_theme", curTheme); }catch(e){}
}
function cycleTheme(){ const i=THEME_ORDER.indexOf(curTheme); applyTheme(THEME_ORDER[(i+1)%THEME_ORDER.length]); }
const QL = {q1:"白(含绿)",q3:"蓝",q4:"紫",q5:"金",q6:"红"};
const QS = {q1:"白",q3:"蓝",q4:"紫",q5:"金",q6:"红"};
// 英雄中文名。真源在 public_stats._HERO_CN(Python 侧), 由 payload 带过来 —— 这里【不许】再写死一份。
// 样本里抓不到英雄时 hero 就是 "?" -> 显示"未识别", 别把问号直接怼给读者。
const HERO_CN = P.hero_cn || {};
const heroName = h => (!h || h==="?") ? "未识别" : (HERO_CN[h] || h);
const esc = s => String(s==null?"":s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt = v => (v==null||v==="")?"":Math.round(v).toLocaleString("en-US");
const fmt1 = v => (v==null)?"":(Math.round(v*100)/100).toLocaleString("en-US");
const pl = v => v==null?"":`<span style="color:${v>0?'var(--pos)':(v<0?'var(--neg)':'var(--dim)')}">${v>0?'+':''}${fmt(v)}</span>`;
const ymd = d => d? `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}-${String(d.getUTCDate()).padStart(2,"0")}`:"—";
const cnTime = d => d? `${ymd(d)} ${String(d.getUTCHours()).padStart(2,"0")}:${String(d.getUTCMinutes()).padStart(2,"0")}:${String(d.getUTCSeconds()).padStart(2,"0")}`:"—";

const VIEWS = [
  {id:"overview", t:"总览", sub:"全局概况", dot:"var(--gold)", g:"概况"},
  {id:"snapshot", t:"爆率快照", sub:"每图每品质 当前掉率", dot:"var(--q5)", g:"掉率"},
  {id:"trend",    t:"时间趋势", sub:"掉率随时间变化", dot:"var(--q3)", g:"掉率"},
  {id:"prop",     t:"藏品占比", sub:"品质件数/价值占比", dot:"var(--q6)", g:"掉率"},
  {id:"compare",  t:"地图对比", sub:"多图品质构成并排", dot:"var(--q4)", g:"对比"},
  {id:"heroes",   t:"英雄", sub:"战绩 · 掉率列=选图偏好, 非英雄属性", dot:"var(--q3)", g:"对比"},
  {id:"tools",    t:"道具使用", sub:"道具/技能使用频次", dot:"var(--q4)", g:"行为"},
  {id:"players",  t:"玩家", sub:"按昵称 · 场次/胜率", dot:"var(--q1)", g:"行为"},
  {id:"rank",     t:"排位", sub:"排位分走势 · 按玩家", dot:"var(--q5)", g:"行为"},
  {id:"anomaly",  t:"异常样本", sub:"未知品质档 · 待查/可回放", dot:"var(--q6)", g:"诊断"},
];
const F = {view:"overview", fam:"全部", map:"全部", hero:"全部", player:"全部", psearch:"", pside:"全部", bucket:"day",
           metric:"count", pcap:50, rcap:50, dfrom:"", dto:""};
// ---- 通用表格排序(全部子模块表格统一走这套; 作者0711定交互) ----
// SORT[table] = 排序键栈 [{k,dir}, ...] —— 依次比较=【联合排序】; dir: 1升 / -1降。
// 点表头 = 三态循环: 未参与→加入联合排序(▼降序, 表头框住); 已参与▼→切升序▲; ▲→移出(取消该列)。
// 点其他列 = 直接追加进联合排序(角标 1▼ 2▲ 显示优先级)。Shift+点 = 只按这一列(清其余, 快捷重置)。
const SORT = {};
function setSort(view, k, solo){ let s=SORT[view]; if(!Array.isArray(s)) s=[];
  const i=s.findIndex(e=>e.k===k);
  if(solo){                                       // Shift+点: 只按这一列(已是唯一键则切方向)
    s = (i>=0 && s.length===1) ? [{k,dir:-s[0].dir}] : [{k,dir:-1}];
  } else if(i<0){ s=s.concat([{k,dir:-1}]);       // 未参与 → 加入联合排序(默认降序)
  } else if(s[i].dir===-1){ s=s.map((e,j)=>j===i?{k,dir:1}:e);   // ▼ → ▲
  } else { s=s.filter((_,j)=>j!==i); }            // ▲ → 移出(取消该列排序)
  SORT[view]=s; invalidateViewCache(); render(); }
function clearSort(view){ delete SORT[view]; invalidateViewCache(); render(); }
// 多键稳定比较: 依次比主键→次级键, 第一个非零决定; 全等保持原序。
function sortArr(view, arr, getters){ const s=SORT[view];
  if(!Array.isArray(s)||!s.length) return arr;
  const keys=s.filter(e=>getters[e.k]); if(!keys.length) return arr;
  return arr.slice().sort((a,b)=>{ for(const e of keys){ const g=getters[e.k], x=g(a), y=g(b);
    let c; if(typeof x==="number"&&typeof y==="number") c=x-y;
    else c=String(x==null?"":x).localeCompare(String(y==null?"":y),"zh");
    if(c) return c*e.dir; } return 0; }); }
// 可排序表头(只给有 getter 的列点)。参与排序的列【框住】(sorton), 多键时角标=优先级+方向(如 1▼ 2▲)。
function sh(view, k, label, cls){ const s=SORT[view]; const arr=Array.isArray(s)?s:[];
  const i=arr.findIndex(e=>e.k===k);
  const ar = i>=0 ? ((arr.length>1?(i+1):"")+(arr[i].dir<0?"▼":"▲")) : "";
  return `<th class="${cls||""}${i>=0?" sorton":""}" style="cursor:pointer;user-select:none" onclick="setSort('${view}','${k}',event.shiftKey)" title="点击: 加入排序▼ → 切升序▲ → 取消 · 点其他列=联合排序 · Shift+点=只按此列">`+
    `${label}<span style="color:var(--gold);font-size:10px"> ${ar}</span></th>`; }
// 供玩家搜索按钮/回车提交(非输入即搜, 避免拼音 IME 合成中间态误触发)。
function commitPsearch(v){ F.psearch=(v||"").trim(); F.pcap=50; render(); }

// ---- 聚合 ----
// 这些维度在页面生命周期内不会变化。玩家下拉可有数千项，过去每切一个视图都会
// 重扫 ROWS 并重建它；现在只在启动时物化一次。
const FAMILIES = ["全部", ...[...new Set(ROWS.map(r=>r.fam).filter(Boolean))]];
const MAPS_BY_FAMILY = new Map();
function _buildMapDimensions(){
  const all=new Map();
  ROWS.forEach(r=>{
    all.set(r.map,r.name);
    if(!MAPS_BY_FAMILY.has(r.fam)) MAPS_BY_FAMILY.set(r.fam,new Map());
    MAPS_BY_FAMILY.get(r.fam).set(r.map,r.name);
  });
  MAPS_BY_FAMILY.set("全部",all);
}
_buildMapDimensions();
const HEROES = ["全部", ...[...new Set(ROWS.map(r=>r.hero).filter(Boolean))]
  .map(h=>[h, heroName(h)])];
const _PLAYER_SET = new Set();
ROWS.forEach(r=>(r.parts||[]).forEach(p=>{if(p&&p!=="?")_PLAYER_SET.add(p);}));
const PLAYERS = ["全部", ...[..._PLAYER_SET].sort()];
const SELF_PLAYERS = new Set(ROWS.map(r=>r.player).filter(p=>p&&p!=="?"));
function families(){ return FAMILIES; }
function mapsIn(fam){ return [...(MAPS_BY_FAMILY.get(fam)||new Map()).entries()]; }
// [slug, 中文] 对 —— 筛选值仍是 slug(F.hero 与 r.hero 比), 只是显示中文。opt() 认这个形状。
function heroes(){ return HEROES; }
function players(){ return PLAYERS; }
function inRange(r){ if(!F.dfrom && !F.dto) return true; if(!r._d) return false;  // 日期区间筛选(游玩时间)
  const k=ymd(r._d); return (!F.dfrom||k>=F.dfrom) && (!F.dto||k<=F.dto); }
let FILTER_CACHE = {key:null, rows:[], minDate:null, maxDate:null};
function filtered(){
  const key=JSON.stringify([F.fam,F.map,F.hero,F.player,F.dfrom,F.dto]);
  if(FILTER_CACHE.key===key) return FILTER_CACHE.rows;
  const rows=[]; let minDate=null,maxDate=null;
  for(const r of ROWS){
    if(!inRange(r) || (F.fam!=="全部"&&r.fam!==F.fam) ||
       (F.map!=="全部"&&String(r.map)!==String(F.map)) ||
       (F.hero!=="全部"&&r.hero!==F.hero) ||
       (F.player!=="全部"&&!(r.parts||[]).includes(F.player))) continue;
    rows.push(r);
    if(r._d){ if(!minDate||r._d<minDate) minDate=r._d; if(!maxDate||r._d>maxDate) maxDate=r._d; }
  }
  FILTER_CACHE={key,rows,minDate,maxDate};
  return rows;
}
function avgQ(rows, kind){ const a={}; QO.forEach(q=>a[q]=0); if(!rows.length) return a;
  rows.forEach(r=>QO.forEach(q=>a[q]+=(r[kind]||{})[q]||0)); QO.forEach(q=>a[q]=a[q]/rows.length); return a; }
// 每品质【每局件数】的样本标准差。均值只说"平均能开出几个红", 方差才说"这图稳不稳" ——
// 均红 2.0±0.4 和 2.0±2.5 是完全不同的两张图(后者要么空要么爆), 出价策略也应不同。
// 用样本标准差(n-1)。⚠️n<2 -> null(【算不出】), 不是 0 —— 见 sdTxt。
function sdQ(rows, kind){ const s={};
  if(rows.length<2){ QO.forEach(q=>s[q]=null); return s; }
  const m=avgQ(rows,kind);
  QO.forEach(q=>{ let v=0; rows.forEach(r=>{ const d=((r[kind]||{})[q]||0)-m[q]; v+=d*d; });
    s[q]=Math.sqrt(v/(rows.length-1)); });
  return s; }
// ⚠️ n<2 时印 "±0" 是在骗人: 读起来像"这图每局雷打不动开 3 个红", 实际【只有一个样本】。
// 零波动(n>=2 且每局真的一样, 如新手图恒不出金红)和【算不出波动】必须能分开 -> 后者印 "±?"。
const sdTxt = sd => sd==null ? "?" : fmt1(sd);
// 均值 ±标准差 的紧凑显示: 主数大、±小而淡, 不挤爆表格。
function pm(mean, sd){ return `${fmt1(mean)}<span class="pm">±${sdTxt(sd)}</span>`; }
// 均值的【标准误】= σ/√n。⚠️别和 sdQ 的标准差搞混:
//   标准差  回答"单局波动多大"(掉率列用它 —— 这图稳不稳)
//   标准误  回答"这个均值本身有多不准"(收益列用它 —— 单局盈亏几十万级、几百局的均值全是噪音)
// 只印 "均收益 +38,635" 不印 ±SEM, 读者会当成结论; 实测全库 32 个段位档里 29 档的 95% 区间跨 0。
function sem(vals){ const n=vals.length; if(n<2) return null;   // n<2 -> 算不出, 不是"误差为 0"
  const m=vals.reduce((s,v)=>s+v,0)/n;
  const v=vals.reduce((s,x)=>s+(x-m)*(x-m),0)/(n-1);
  return Math.sqrt(v/n); }
// ⚠️ n<2 时【绝不能印 "±0"】—— 那读起来像"这个数字精确到零误差", 实际是"样本太少, 算不出误差"。
// 印 "±?" 明说算不出。(海琳娜 1 局 "+10,561 ±0" 就是这么骗人的。)
function pmSem(s){ return `<span class="pm">±${s==null?"?":fmt(s)}</span>`; }
// 本机(上传者)在这一局的盈亏。与「玩家」视图的口径完全一致(别分叉):
//   每位玩家(含拿仓者)都收赢家亏损×10%的分红。
//   拍下 = 藏品真值 − 自己出价 + 分红 − 门票 ; 没拍下 = 分红 − 门票。
function selfProfit(r){ return playerProfit(r,r.player).value; }
function playerProfit(r,p){ const fee=r.fee||0, lot=r.lot||0;
  if(typeof r.wbid!=="number") return {value:-fee,estimated:true,source:"missing_winner_bid"}; // 没抓到出价 -> 已知下限
  const dividend=Math.round(0.10*Math.max(0,r.wbid-lot));
  if(r.winner===p) return {value:lot-r.wbid+dividend-fee,estimated:false,source:"mechanic_10pct"};
  if(p===r.player && typeof r.welfare==="number") return {value:r.welfare-fee,estimated:false,source:"settlement_direct"};
  return {value:dividend-fee,estimated:false,source:"mechanic_10pct"}; }
function sumQ(rows, kind){ const a={}; QO.forEach(q=>a[q]=0);
  rows.forEach(r=>QO.forEach(q=>a[q]+=(r[kind]||{})[q]||0)); return a; }
function bucketKey(d){ if(!d) return "?";
  if(F.bucket==="week"){ const o=new Date(d); o.setUTCDate(o.getUTCDate()-((o.getUTCDay()+6)%7)); return ymd(o); }
  if(F.bucket==="hour"){ return ymd(d)+" "+String(d.getUTCHours()).padStart(2,"0")+":00"; }  // 北京时间当天分时段
  return ymd(d); }

// ---- 小部件 ----
function qbar(v, max){ const w=max>0?Math.round(100*v/max):0;
  return `<span class="qbar"><i style="width:${w}%;background:currentColor"></i></span>`; }
// 条+数字定宽单元(条左端对齐, 数字右对齐成列): 解决英雄/快照表里条参差不齐。
function qnum(v, max, color, tip){ const w=max>0?Math.round(100*v/max):0;
  return `<span${tip?` title="${tip}"`:""} style="display:inline-flex;align-items:center;gap:7px;justify-content:flex-end;color:${color}">`+
    `<span class="qbar" style="width:46px;flex:none"><i style="width:${w}%;background:currentColor"></i></span>`+
    `<span style="display:inline-block;width:30px;text-align:right">${fmt1(v)}</span></span>`; }
function stack(dist){ const tot=QO.reduce((s,q)=>s+(dist[q]||0),0)||1;
  return `<div class="stack">`+QO.map(q=>{const p=100*(dist[q]||0)/tot;
    return p>0?`<i title="${QS[q]} ${fmt1(dist[q])} (${p.toFixed(1)}%)" style="width:${p}%;background:${QC[q]}"></i>`:""}).join("")+`</div>`; }
function legend(){ return `<div class="legend">`+QO.map(q=>`<span><b style="background:${QC[q]}"></b>${QL[q]}</span>`).join("")+`</div>`; }
// 环形图(品质占比)
function donut(dist, size){ size=size||168; const tot=QO.reduce((s,q)=>s+(dist[q]||0),0);
  const r=size/2-3, cx=size/2, cy=size/2, inner=r*0.6;
  if(tot<=0) return `<svg class="chart-svg" width="${size}" height="${size}"></svg>`;
  let a0=-Math.PI/2, segs="";
  for(const q of QO){ const v=dist[q]||0; if(v<=0) continue; const f=v/tot, a1=a0+f*2*Math.PI, big=f>0.5?1:0;
    const p=(rad,a)=>[cx+rad*Math.cos(a),cy+rad*Math.sin(a)];
    const[x0,y0]=p(r,a0),[x1,y1]=p(r,a1),[xi1,yi1]=p(inner,a1),[xi0,yi0]=p(inner,a0);
    segs+=`<path d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${big} 1 ${x1.toFixed(1)} ${y1.toFixed(1)} L${xi1.toFixed(1)} ${yi1.toFixed(1)} A${inner} ${inner} 0 ${big} 0 ${xi0.toFixed(1)} ${yi0.toFixed(1)} Z" fill="${QC[q]}" stroke="var(--panel)" stroke-width="1"><title>${QS[q]} ${(100*f).toFixed(1)}%</title></path>`;
    a0=a1; }
  return `<svg class="chart-svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${segs}`+
    `<text x="${cx}" y="${cy-2}" text-anchor="middle" font-size="20" font-weight="700" fill="var(--txt)">${fmt(tot)}</text>`+
    `<text x="${cx}" y="${cy+15}" text-anchor="middle" font-size="10" fill="var(--dim)">合计</text></svg>`; }
// 折线图(每品质随时间)
function lineChart(keys, seriesByQ, w, h){ w=w||640; h=h||190; const pad={l:38,r:12,t:12,b:24};
  const iw=w-pad.l-pad.r, ih=h-pad.t-pad.b, mx=Math.max(1,...QO.flatMap(q=>seriesByQ[q]||[]));
  const X=i=>pad.l+(keys.length<=1?iw/2:iw*i/(keys.length-1)), Y=v=>pad.t+ih*(1-v/mx);
  let g=`<line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${pad.t+ih}" stroke="var(--line)"/>`+
        `<line x1="${pad.l}" y1="${pad.t+ih}" x2="${w-pad.r}" y2="${pad.t+ih}" stroke="var(--line)"/>`+
        `<text x="${pad.l-4}" y="${pad.t+8}" text-anchor="end" font-size="9" fill="var(--dim)">${fmt1(mx)}</text>`;
  for(const q of QO){ const s=seriesByQ[q]||[]; if(!s.some(v=>v>0)) continue;
    g+=`<polyline points="${s.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}" fill="none" stroke="${QC[q]}" stroke-width="2"/>`;
    g+=s.map((v,i)=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.6" fill="${QC[q]}"><title>${QS[q]} ${keys[i]}: ${fmt1(v)}</title></circle>`).join(""); }
  g+=keys.map((k,i)=>`<text x="${X(i).toFixed(1)}" y="${h-7}" text-anchor="middle" font-size="9" fill="var(--dim)">${esc(k.slice(5)||k)}</text>`).join("");
  // 每列透明 hover 区 + 竖线: 悬停看该时间桶全品质均件(比逐点 title 清晰)。
  g+=keys.map((k,i)=>{ const cx=X(i), colw=keys.length>1?iw/(keys.length-1):iw;
    const tip=QO.filter(q=>(seriesByQ[q]||[])[i]>0).map(q=>QS[q]+" "+fmt1(seriesByQ[q][i])).join("  ");
    return `<g class="hovcol"><rect x="${(cx-colw/2).toFixed(1)}" y="${pad.t}" width="${colw.toFixed(1)}" height="${ih}" fill="transparent" style="cursor:crosshair"></rect>`+
      `<line class="hovline" x1="${cx.toFixed(1)}" y1="${pad.t}" x2="${cx.toFixed(1)}" y2="${pad.t+ih}" stroke="var(--gold)" stroke-width="1" stroke-dasharray="3 3" opacity="0"/>`+
      `<title>${esc(k)}\n${esc(tip)}</title></g>`; }).join("");
  return `<svg class="chart-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${g}</svg>`; }
// 单序列曲线(玩家累计收益 / 每日均拍品值)。支持负值并画 0 基线；不复用品质色多折线，避免语义混淆。
function singleLineChart(keys, vals, color, tipLabel, w, h){ w=w||Math.max(520,keys.length*54); h=h||190;
  const pad={l:62,r:14,t:14,b:26}, iw=w-pad.l-pad.r, ih=h-pad.t-pad.b;
  let lo=Math.min(0,...vals), hi=Math.max(0,...vals); if(lo===hi){hi=lo+1;}
  const X=i=>pad.l+(keys.length<=1?iw/2:iw*i/(keys.length-1)), Y=v=>pad.t+ih*(hi-v)/(hi-lo);
  let g=`<line x1="${pad.l}" y1="${Y(0)}" x2="${w-pad.r}" y2="${Y(0)}" stroke="var(--line)" stroke-dasharray="4 4"/>`+
    `<text x="${pad.l-5}" y="${pad.t+8}" text-anchor="end" font-size="9" fill="var(--dim)">${fmt(hi)}</text>`+
    `<text x="${pad.l-5}" y="${pad.t+ih}" text-anchor="end" font-size="9" fill="var(--dim)">${fmt(lo)}</text>`;
  if(vals.length) g+=`<polyline points="${vals.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}" fill="none" stroke="${color}" stroke-width="2.2"/>`+
    vals.map((v,i)=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.8" fill="${color}"><title>${esc(keys[i])} ${esc(tipLabel)} ${fmt(v)}</title></circle>`).join("");
  const step=Math.max(1,Math.ceil(keys.length/9));
  g+=keys.map((k,i)=>i%step?"":`<text x="${X(i).toFixed(1)}" y="${h-7}" text-anchor="middle" font-size="9" fill="var(--dim)">${esc(k.slice(5)||k)}</text>`).join("");
  return `<svg class="chart-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${g}</svg>`; }

// ---- 渲染骨架 ----
function renderNav(){
  let html="", lastG=null;
  for(const v of VIEWS){
    if(v.g!==lastG){ html+=`<div class="nav-eyebrow">${v.g}</div>`; lastG=v.g; }
    html+=`<div class="nav-item ${v.id===F.view?"on":""}" onclick="go('${v.id}')"><span class="dot" style="background:${v.dot}"></span><span>${v.t}</span></div>`;
  }
  document.getElementById("nav").innerHTML=html;
  const rs=filtered();
  document.getElementById("sidefoot").innerHTML =
    `样本 ${rs.length} / ${ROWS.length}<br>${FILTER_CACHE.minDate?ymd(FILTER_CACHE.minDate)+" ~ "+ymd(FILTER_CACHE.maxDate):"无时间数据"}`+
    `<br>服务端 ${esc(SERVER_WINDOW.start||"-")} ~ ${esc(SERVER_WINDOW.end||"-")}`+
    `<br>命中 ${Number(SERVER_WINDOW.matched_rows||0).toLocaleString("en-US")} 局 · 未知日期 ${Number(SERVER_WINDOW.unknown_date_count||0).toLocaleString("en-US")}`+
    `<br><span title="隔离池只保留残缺、短缺、非法值或来源字段冲突；完整玩家仓/来源未知样本进入正常大数据并按地图标注">隔离池 ${Number(QUARANTINE_SUMMARY.total||0).toLocaleString("en-US")} 局 · 来源冲突 ${Number(QUARANTINE_SUMMARY.warehouse_source_pending||0).toLocaleString("en-US")}</span>`+
    `<br><a href="/private?logout=1">退出私有登录</a>`;
}
function loadServerPreset(days){ location.assign(`/dashboard?window=${days}`); }
function loadServerCustom(){
  const start=document.getElementById("server-start")?.value||"";
  const end=document.getElementById("server-end")?.value||"";
  if(!start||!end||start>end){ alert("请选择有效的北京时间起止日期"); return; }
  location.assign(`/dashboard?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
}
let LAST_FILTER_UI_KEY = null;
function renderFilters(){
  const v=VIEWS.find(x=>x.id===F.view);
  document.getElementById("vtitle").textContent=v.t;
  document.getElementById("vsub").textContent=v.sub;
  if(F.view==="anomaly"){
    if(LAST_FILTER_UI_KEY!=="anomaly") document.getElementById("filters").innerHTML="";
    LAST_FILTER_UI_KEY="anomaly"; return;
  }  // 静态诊断视图, 不吃筛选
  const extraKey=F.view==="trend"?`trend:${F.bucket}`:
    ((F.view==="prop"||F.view==="compare")?`metric:${F.metric}`:"common");
  const uiKey=JSON.stringify([F.fam,F.map,F.hero,F.player,F.dfrom,F.dto,Boolean(anyFilter()),extraKey]);
  // 多数侧栏切换共享同一组筛选控件。避免反复销毁/重建包含数千玩家 option 的 select。
  if(LAST_FILTER_UI_KEY===uiKey) return;
  const opt=(arr,cur,fn)=>arr.map(x=>{const[val,lab]=Array.isArray(x)?x:[x,x];
    return `<option value="${esc(val)}" ${String(val)===String(cur)?"selected":""}>${esc(lab)}</option>`}).join("");
  const activeDays=String(SERVER_WINDOW.mode||"").startsWith("recent-")?String(SERVER_WINDOW.mode).slice(7):"";
  let h=`<label title="该范围在服务端读取、计算和序列化之前生效">服务端范围</label>`+
        `<span class="seg">${[7,30,90].map(d=>`<b class="${activeDays===String(d)?"on":""}" onclick="loadServerPreset(${d})">${d}天</b>`).join("")}</span>`+
        `<input id="server-start" type="date" class="dt" value="${esc(SERVER_WINDOW.start||"")}" title="北京时间起始日期（两端包含）">`+
        `<span class="muted" style="font-size:11px">~</span>`+
        `<input id="server-end" type="date" class="dt" value="${esc(SERVER_WINDOW.end||"")}" title="北京时间结束日期（两端包含）">`+
        `<button class="btn" onclick="loadServerCustom()">加载</button>`+
        `<span class="muted" title="无日期或非法日期不会混入窗口">已载 ${Number(SERVER_WINDOW.matched_rows||0).toLocaleString("en-US")} 局 · 未知日期 ${Number(SERVER_WINDOW.unknown_date_count||0).toLocaleString("en-US")}</span>`+
        `<span class="muted" title="完整玩家仓/来源未知样本进入正常大数据并按地图标注；这里只显示仍需隔离的冲突或残缺样本">隔离 ${Number(QUARANTINE_SUMMARY.total||0).toLocaleString("en-US")} · 来源冲突 ${Number(QUARANTINE_SUMMARY.warehouse_source_pending||0).toLocaleString("en-US")}</span>`+
        `<label>族</label><select onchange="setF('fam',this.value)">${opt(families(),F.fam)}</select>`+
        `<label>地图</label><select onchange="setF('map',this.value)">`+
          `<option value="全部">全部</option>${opt(mapsIn(F.fam),F.map)}</select>`+
        `<label>英雄</label><select onchange="setF('hero',this.value)">${opt(heroes(),F.hero)}</select>`+
        `<label>玩家</label><select onchange="setF('player',this.value)">${opt(players(),F.player)}</select>`+
        `<label>页内时间</label><input type="date" class="dt" value="${F.dfrom}" onchange="setF('dfrom',this.value)" title="仅在当前已加载服务端窗口内二次筛选">`+
        `<span class="muted" style="font-size:11px">~</span>`+
        `<input type="date" class="dt" value="${F.dto}" onchange="setF('dto',this.value)" title="北京时间结束日期">`+
        (anyFilter()?`<b class="seg" style="cursor:pointer;padding:5px 12px;color:var(--gold)" onclick="clearF()" title="清除所有筛选条件">✕ 清除筛选</b>`:``);
  if(F.view==="trend") h+=`<span class="seg">${[["hour","按时"],["day","按天"],["week","按周"]].map(([b,l])=>`<b class="${F.bucket===b?"on":""}" onclick="setF('bucket','${b}')">${l}</b>`).join("")}</span>`;
  if(F.view==="prop"||F.view==="compare") h+=`<span class="seg">${[["count","件数"],["value","价值"]].map(([m,l])=>`<b class="${F.metric===m?"on":""}" onclick="setF('metric','${m}')">${l}</b>`).join("")}</span>`;
  document.getElementById("filters").innerHTML=h;
  LAST_FILTER_UI_KEY=uiKey;
}
const DETAIL_URLS=P.detail_urls||{};
const DETAIL_STATE={rare:DETAIL_URLS.rare?"idle":"ready",tools:DETAIL_URLS.tools?"idle":"ready",
                    player_ranks:DETAIL_URLS.player_ranks?"idle":"ready"};
const DETAIL_TRUNCATED={};
const DETAIL_PROMISE={};
function detailKindForView(v){ return v==="prop"?"rare":(v==="tools"?"tools":(v==="players"?"player_ranks":null)); }
async function ensureDetail(kind){
  if(!kind||DETAIL_STATE[kind]==="ready") return true;
  if(DETAIL_PROMISE[kind]) return await DETAIL_PROMISE[kind];
  const host=document.getElementById("content");
  host.innerHTML=`<div class="empty">正在加载${kind==="rare"?"稀有藏品":(kind==="tools"?"道具/技能":"玩家排位")}明细…</div>`;
  DETAIL_STATE[kind]="loading";
  DETAIL_PROMISE[kind]=(async()=>{
    try{
      const response=await fetch(DETAIL_URLS[kind],{credentials:"same-origin",headers:{"Accept":"application/json"}});
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload=await response.json();
      for(const item of (payload.items||[])){
        const row=ROWS[item[0]]; if(!row) continue;
        if(kind==="tools"){ row.tools=item[1]||[]; row.hskills=item[2]||[]; }
        else row[kind]=item[1];
      }
      if(payload.truncated) DETAIL_TRUNCATED[kind]=Number(payload.max_items||0);
      DETAIL_STATE[kind]="ready"; invalidateViewCache(); return true;
    }catch(error){
      DETAIL_STATE[kind]="idle";
      host.innerHTML=`<div class="empty">明细加载失败，请检查网络后重试<br><button onclick="go(F.view)" style="margin-top:12px">重新加载</button></div>`;
      return false;
    }finally{ delete DETAIL_PROMISE[kind]; }
  })();
  return await DETAIL_PROMISE[kind];
}
async function go(v){
  if(VIEWS.find(x=>x.id===v).lock){F.view=v;render();return;}
  F.view=v; renderNav(); renderFilters();
  if(!(await ensureDetail(detailKindForView(v)))) return;
  render();
}
function setF(k,val){ F[k]=val; if(k==="fam") F.map="全部"; render(); }
function anyFilter(){ return F.fam!=="全部"||F.map!=="全部"||F.hero!=="全部"||F.player!=="全部"||F.dfrom||F.dto||F.psearch; }
function clearF(){ F.fam="全部";F.map="全部";F.hero="全部";F.player="全部";F.dfrom="";F.dto="";F.psearch="";F.pside="全部";F.pcap=50; render(); }
const VIEW_CACHE = new Map(), VIEW_CACHE_LIMIT = 10;
function invalidateViewCache(){ VIEW_CACHE.forEach(function(node){node.remove();}); VIEW_CACHE.clear(); }
function render(){ renderNav(); renderFilters();
  const started=Date.now(), cacheKey=JSON.stringify(F);
  const host=document.getElementById("content");
  let node=VIEW_CACHE.get(cacheKey);
  const cacheHit=Boolean(node);
  if(!node){ let html=({
    overview:vOverview, snapshot:vSnapshot, trend:vTrend, compare:vCompare, prop:vProp,
    heroes:vHeroes, tools:vTools, players:vPlayers, rank:vRank, anomaly:vAnomaly
  })[F.view]();
    const detailKind=detailKindForView(F.view), detailCap=DETAIL_TRUNCATED[detailKind];
    if(detailCap) html=`<div id="detail-cap-warning" style="padding:9px 12px;margin-bottom:12px;border:1px solid var(--gold);border-radius:8px;background:var(--panel2)">当前服务端窗口的该类明细超过 ${detailCap.toLocaleString("en-US")} 项；页面仅加载前 ${detailCap.toLocaleString("en-US")} 项，请缩小服务端日期范围后重试。</div>`+html;
    node=document.createElement("div"); node.className="view-pane"; node.innerHTML=html; host.appendChild(node);
    VIEW_CACHE.set(cacheKey,node);
    if(VIEW_CACHE.size>VIEW_CACHE_LIMIT){ const oldKey=VIEW_CACHE.keys().next().value, old=VIEW_CACHE.get(oldKey);
      VIEW_CACHE.delete(oldKey); if(old) old.remove(); }
  } else {
    // 命中直接复用已解析 DOM，不再 innerHTML 重建整张表；Map 插入顺序同时承担 LRU。
    VIEW_CACHE.delete(cacheKey); VIEW_CACHE.set(cacheKey,node);
  }
  Array.from(host.children).forEach(function(p){p.hidden=p!==node;});
  host.dataset.renderMs=String(Date.now()-started); host.dataset.cacheHit=cacheHit?"1":"0";
}
// 异常样本记录: 含未知品质档(_QKEY 未覆盖=非 q1-q6, 多为单件解析毛刺如 q10)的样本, 单独留档供
// 排查/回放。正常聚合本就不含这些档(_qmap/avgQ 只认 q1-q6), 故不影响其它视图的观测。静态·不随筛选。
function vAnomaly(){
  const a=P.anomaly_samples||[];
  if(!a.length) return `<div class="empty">无异常样本 · 所有品质档都在 q1–q6 正常范围内</div>`;
  let h=`<div class="sec">异常样本记录 · ${a.length} 局含未知品质档</div>`;
  h+=`<div class="muted" style="margin:-4px 0 12px">未知档(_QKEY 未覆盖, 多为单件品质解析毛刺, 如 q10)已从正常聚合(均件/均价/占比)【天然剔除】, 此处单独留档供回放/排查 —— 原始样本不丢。若某未知档【反复出现】, 可能是游戏新版真新档, 补 _QKEY/_QORDER 后重建即可正常渲染。</div>`;
  const arows=sortArr("anom", a.slice(), {name:r=>r.name||"", fam:r=>r.fam||"", hero:r=>r.hero||"",
    round:r=>r.round!=null?r.round:-1, ts:r=>r.ts||0});
  h+=`<div style="overflow-x:auto"><table><thead><tr>`+sh("anom","name","地图","l")+sh("anom","fam","族","l")+
     sh("anom","hero","英雄","l")+sh("anom","round","轮","num")+`<th class="l">未知品质档(件/格/值)</th><th class="l">会话</th>`+
     sh("anom","ts","时间(北京)","l")+`</tr></thead><tbody>`;
  for(const r of arows){
    const bad=Object.entries(r.bad).map(([k,v])=>`<span class="tag" style="background:var(--q6);color:#fff">${k}</span> ${v.cnt??'?'}件/${v.cel??'?'}格/${v.val!=null?fmt(v.val):'?'}`).join("&nbsp;&nbsp;");
    const tm=r.ts?cnTime(new Date(r.ts*1000+SHANGHAI_OFFSET_MS)):'-';
    h+=`<tr><td class="l">${esc(r.name)}</td><td class="l"><span class="tag">${r.fam}</span></td><td class="l">${esc(heroName(r.hero))}</td><td class="num">R${r.round??'?'}</td><td class="l">${bad}</td><td class="l muted" style="font-family:monospace">${r.sess||'-'}</td><td class="l muted">${tm}</td></tr>`;
  }
  return h+`</tbody></table></div>`;
}

// ---- 各视图 ----
function vOverview(){
  const rs=filtered(); if(!rs.length) return `<div class="empty">无样本</div>`;
  const byMap={}, byHero={}; let lot=0;
  rs.forEach(r=>{ const m=byMap[r.map]=byMap[r.map]||{n:0,name:r.name,fam:r.fam,lot:0,q:{},mixed:0};
    m.n++; m.lot+=r.lot; if(r.source_mixed)m.mixed++; QO.forEach(q=>m.q[q]=(m.q[q]||0)+((r.cnt||{})[q]||0));
    byHero[r.hero]=(byHero[r.hero]||0)+1; lot+=r.lot; });
  let maps=Object.entries(byMap).map(([mid,m])=>({...m, map:mid}));
  if(!SORT.ov) SORT.ov=[{k:"n",dir:-1}];
  maps=sortArr("ov", maps, {name:m=>m.name, fam:m=>m.fam, n:m=>m.n, avg:m=>m.lot/m.n});
  const kpi=(v,k)=>`<div class="kpi"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  let h=(F.player!=="全部"?playerOverview(rs,F.player):"")+`<div class="kpis">${kpi(rs.length,"有效样本")}${kpi(maps.length,"覆盖地图")}${kpi(Object.keys(byHero).length,"英雄")}${kpi(fmt(lot/rs.length),"均拍品价值")}</div>`;
  h+=`<div class="sec">按地图</div><table><thead><tr>`+
     sh("ov","name","地图","l")+sh("ov","fam","族","l")+sh("ov","n","样本","num")+sh("ov","avg","均拍品价值","num")+
     `<th class="l">品质构成(均件)</th></tr></thead><tbody>`;
  for(const m of maps){ const a={}; QO.forEach(q=>a[q]=(m.q[q]||0)/m.n);
    h+=`<tr><td class="l">${esc(m.name)}${m.mixed?` <span class="tag">含玩家仓/来源混合</span>`:""}</td><td class="l"><span class="tag">${m.fam}</span></td><td class="num">${m.n}</td><td class="num">${fmt(m.lot/m.n)}</td><td class="l" style="min-width:160px">${stack(a)}</td></tr>`; }
  h+=`</tbody></table>${legend()}<div class="muted" style="margin-top:8px">“含玩家仓/来源混合”表示该地图包含玩家自建仓或暂时无法区分来源的完整结算，不代表纯官方随机仓掉率。</div>`;
  let hs=Object.entries(byHero).map(([k,v])=>({hero:k,n:v}));
  if(!SORT.ovh) SORT.ovh=[{k:"n",dir:-1}];
  hs=sortArr("ovh", hs, {hero:o=>o.hero, n:o=>o.n});
  h+=`<div class="sec">按英雄</div><table style="max-width:340px"><thead><tr>`+sh("ovh","hero","英雄","l")+sh("ovh","n","样本","num")+`</tr></thead><tbody>`+
     hs.map(o=>`<tr><td class="l">${esc(heroName(o.hero))}</td><td class="num">${o.n}</td></tr>`).join("")+`</tbody></table>`;
  return h;
}
function playerOverview(rs,p){
  const dated={}, vals=[]; let wins=0, estimated=0;
  rs.forEach(r=>{ if(r.winner===p) wins++; const pr=playerProfit(r,p); vals.push(pr.value); if(pr.estimated) estimated++;
    if(r._d){ const d=ymd(r._d), a=dated[d]=dated[d]||{n:0,lot:0,profit:0}; a.n++;a.lot+=r.lot||0;a.profit+=pr.value; } });
  const days=Object.keys(dated).sort(), cum=[]; let running=0;
  days.forEach(d=>{running+=dated[d].profit;cum.push(running);});
  const avglot=days.map(d=>dated[d].lot/dated[d].n), total=vals.reduce((s,v)=>s+v,0);
  const kpi=(v,k)=>`<div class="kpi"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  let h=`<div class="sec" style="margin-top:0">${esc(p)} · 个人时间记录</div>`+
    `<div class="kpis">${kpi(rs.length,"出现对局")}${kpi(Math.round(100*wins/Math.max(1,rs.length))+"%","拿仓率")}`+
    `${kpi((total>=0?"+":"")+fmt(total),estimated?"观测收益下限*":"观测总收益")}${kpi(fmt(rs.reduce((s,r)=>s+(r.lot||0),0)/rs.length),"平均拍品价值")}</div>`;
  if(days.length){ h+=`<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px">`+
    `<div class="panel" style="flex:1 1 440px;overflow-x:auto"><div class="muted" style="margin-bottom:6px">累计收益走势 · ${days.length} 天</div>${singleLineChart(days,cum,"var(--gold)","累计收益")}</div>`+
    `<div class="panel" style="flex:1 1 440px;overflow-x:auto"><div class="muted" style="margin-bottom:6px">每日平均拍品价值</div>${singleLineChart(days,avglot,"var(--q3)","均拍品值")}</div></div>`; }
  else h+=`<div class="empty" style="margin-bottom:14px">该玩家样本没有可用游玩时间，暂不能画趋势</div>`;
  if(estimated) h+=`<div class="muted" style="margin:-8px 0 14px">* ${estimated} 局没有赢家出价，只计已知门票成本，因此总收益是<b>可确认下限</b>，不是精确结算。</div>`;
  return h;
}
function vSnapshot(){
  const rs=filtered(); if(!rs.length) return `<div class="empty">无样本</div>`;
  const byMap={}; rs.forEach(r=>(byMap[r.map]=byMap[r.map]||[]).push(r));
  let maps=Object.entries(byMap).map(([mid,rr])=>{ const c=avgQ(rr,"cnt"),ce=avgQ(rr,"cel"),va=avgQ(rr,"val");
    return {mid,rr,name:rr[0].name,fam:rr[0].fam,n:rr.length,c,ce,va,sd:sdQ(rr,"cnt"),
            totc:QO.reduce((s,q)=>s+c[q],0), totce:QO.reduce((s,q)=>s+ce[q],0)}; });
  if(!SORT.snap) SORT.snap=[{k:"n",dir:-1}];
  const sget={name:m=>m.name,fam:m=>m.fam,n:m=>m.n,totc:m=>m.totc,totce:m=>m.totce};
  QO.forEach(q=>{ sget["c_"+q]=m=>m.c[q];
    sget["ce_"+q]=m=>m.c[q]>0?m.ce[q]/m.c[q]:-1;      // 每件均格(无该品质=-1 沉底)
    sget["va_"+q]=m=>m.c[q]>0?m.va[q]/m.c[q]:-1; });  // 每件均价
  maps=sortArr("snap", maps, sget);
  const maxc=Math.max(1,...maps.flatMap(m=>QO.map(q=>m.c[q])));
  let h=`${legend()}<div style="overflow-x:auto"><table><thead><tr>`+
    sh("snap","name","地图","l")+sh("snap","fam","族","l")+sh("snap","n","样本","num")+
    sh("snap","totc","均总件","num")+sh("snap","totce","均总格","num")+
    QO.map(q=>sh("snap","c_"+q,QS[q]+"件","num")).join("")+QO.map(q=>sh("snap","ce_"+q,QS[q]+"均格","num")).join("")+
    QO.map(q=>sh("snap","va_"+q,QS[q]+"均价","num")).join("")+`</tr></thead><tbody>`;
  for(const m of maps){ const c=m.c,ce=m.ce,va=m.va;
    h+=`<tr><td class="l">${esc(m.name)}</td><td class="l"><span class="tag">${m.fam}</span></td><td class="num">${m.n}</td>`+
      `<td class="num" style="font-weight:600">${fmt1(m.totc)}</td><td class="num" style="font-weight:600">${fmt1(m.totce)}</td>`+
      QO.map(q=>`<td class="num" title="${QS[q]} 每局 ${fmt1(c[q])} ±${sdTxt(m.sd[q])} 件">${qnum(c[q],maxc,QC[q],QS[q]+" "+fmt1(c[q])+"±"+sdTxt(m.sd[q])+"件/局")}<span class="pm">±${sdTxt(m.sd[q])}</span></td>`).join("")+
      QO.map(q=>`<td class="num muted">${c[q]>0?fmt1(ce[q]/c[q]):"—"}</td>`).join("")+
      QO.map(q=>`<td class="num muted">${c[q]>0?fmt(va[q]/c[q]):"—"}</td>`).join("")+`</tr>`; }
  return h+`</tbody></table></div><div class="muted" style="margin-top:8px"><b>均总件/均总格=每局平均</b>总件数/总格子数(<b>非累计总和</b>, 多样本时为均值, 看不同地图体量); 件=每品质每局均件, 均格/均价=每件平均(白含绿)。<br><b>±是标准差(波动)</b> —— 均红 <b>2.0±0.4</b> 和 <b>2.0±2.5</b> 是完全不同的两张图: 前者稳定开 2 个红, 后者要么空要么爆。<b>波动大 = 该图更该按保守档出价</b>。样本 &lt;2 局时无从谈波动, 显示 ±0。</div>`;
}
function vTrend(){
  const rs=filtered(); if(!rs.length) return `<div class="empty">无样本(选具体地图看趋势更清晰)</div>`;
  const bk={}; rs.forEach(r=>{const k=bucketKey(r._d); (bk[k]=bk[k]||[]).push(r);});
  const asc=Object.keys(bk).filter(k=>k!=="?").sort();      // 时间正序(折线图用)
  if(!asc.length) return `<div class="empty">样本无时间戳</div>`;
  const bl=F.bucket==="hour"?"时段":(F.bucket==="week"?"周":"天");
  let h=`${legend()}<div class="muted" style="margin-bottom:10px">北京时间口径 · 左:全时段(按${bl}) · 右:今天/最新日按小时阶梯(今日样本不足则回退到最近有数据日) · 每品质每局平均件数(折线悬停看明细)</div>`;
  // 左图: 各品质均件随时间(全时段, 当前粒度)
  const series={}; QO.forEach(q=>series[q]=asc.map(k=>avgQ(bk[k],"cnt")[q]));
  const leftChart=`<div class="panel" style="overflow-x:auto"><div class="muted" style="margin-bottom:6px">全时段 · ${asc.length} 桶(按${bl})</div>${lineChart(asc,series,Math.max(440,asc.length*64))}</div>`;
  // 右图: 默认【今天/最新日】按小时; 若最新日不足 2 个时段(画不出趋势), 自动回退到【最近一个有≥2时段的日】并标注。
  const dated=rs.filter(r=>r._d), byday={};
  dated.forEach(r=>{const dk=ymd(r._d); (byday[dk]=byday[dk]||[]).push(r);});
  const daysDesc=Object.keys(byday).sort().reverse();
  const hoursOf=rr=>{const hb={}; rr.forEach(r=>{const hk=String(r._d.getUTCHours()).padStart(2,"0")+":00"; (hb[hk]=hb[hk]||[]).push(r);}); return hb;};
  let today=daysDesc[0]||"", hbk=today?hoursOf(byday[today]):{}, fellback=false;
  if(Object.keys(hbk).length<2){ for(let i=1;i<daysDesc.length;i++){ const hb=hoursOf(byday[daysDesc[i]]); if(Object.keys(hb).length>=2){ today=daysDesc[i]; hbk=hb; fellback=true; break; } } }
  const hkeys=Object.keys(hbk).sort();
  const hseries={}; QO.forEach(q=>hseries[q]=hkeys.map(k=>avgQ(hbk[k],"cnt")[q]));
  const rlabel=fellback?`最近有数据日 <b>${today}</b> <span class="muted">(今日样本不足)</span>`:`最新日 ${today||"—"}`;
  const rightChart=`<div class="panel" style="overflow-x:auto"><div class="muted" style="margin-bottom:6px">${rlabel} · 按小时 · ${hkeys.length} 时段</div>`+
    (hkeys.length>=2?lineChart(hkeys,hseries,Math.max(440,hkeys.length*64)):(hkeys.length===1?`<div class="empty">仅 1 时段(${hkeys[0]})· 样本不足看趋势</div>`:`<div class="empty">无样本</div>`))+`</div>`;
  h+=`<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px"><div style="flex:1 1 440px;min-width:320px">${leftChart}</div><div style="flex:1 1 440px;min-width:320px">${rightChart}</div></div>`;
  let trows=asc.map(k=>{ const rr=bk[k], c=avgQ(rr,"cnt");
    return {k, rr, c, n:rr.length, lot:rr.reduce((s,r)=>s+r.lot,0)/rr.length}; });
  if(!SORT.trend) SORT.trend=[{k:"bucket",dir:-1}];          // 默认最近在上
  const tget={bucket:o=>o.k, n:o=>o.n, lot:o=>o.lot};
  QO.forEach(q=>tget["c_"+q]=o=>o.c[q]);
  trows=sortArr("trend", trows, tget);
  h+=`<table><thead><tr>`+sh("trend","bucket","时间桶","l")+sh("trend","n","样本","num")+sh("trend","lot","均拍品价值","num")+
     QO.map(q=>sh("trend","c_"+q,QS[q],"num")).join("")+`<th class="l">构成</th></tr></thead><tbody>`;
  for(const o of trows){ const c=o.c;
    h+=`<tr><td class="l">${o.k}</td><td class="num">${o.n}</td><td class="num">${fmt(o.lot)}</td>`+
      QO.map(q=>`<td class="num" style="color:${QC[q]}">${fmt1(c[q])}</td>`).join("")+
      `<td class="l" style="min-width:140px">${stack(c)}</td></tr>`; }
  return h+`</tbody></table>`;
}
function vCompare(){
  const fam=F.fam, rs=ROWS.filter(r=>(fam==="全部"||r.fam===fam)&&(F.hero==="全部"||r.hero===F.hero));
  if(!rs.length) return `<div class="empty">无样本</div>`;
  const kind=F.metric==="value"?"val":"cnt";
  const byMap={}; rs.forEach(r=>(byMap[r.map]=byMap[r.map]||[]).push(r));
  let maps=Object.entries(byMap).map(([mid,rr])=>{ const a=avgQ(rr,kind);
    return {rr,name:rr[0].name,fam:rr[0].fam,n:rr.length,a,tot:QO.reduce((s,q)=>s+a[q],0)}; });
  if(!SORT.cmp) SORT.cmp=[{k:"n",dir:-1}];
  maps=sortArr("cmp", maps, {name:m=>m.name,fam:m=>m.fam,n:m=>m.n,tot:m=>m.tot});
  let h=`${legend()}<div class="muted" style="margin-bottom:10px">按${F.metric==="value"?"价值":"件数"}的品质构成 · 各图每局平均 · 堆叠条等比 · 点表头排序</div>`;
  h+=`<table><thead><tr>`+sh("cmp","name","地图","l")+sh("cmp","fam","族","l")+sh("cmp","n","样本","num")+
     sh("cmp","tot","均"+(F.metric==="value"?"拍品价值":"件数"),"num")+`<th class="l" style="width:46%">品质构成</th></tr></thead><tbody>`;
  for(const m of maps){
    h+=`<tr><td class="l">${esc(m.name)}</td><td class="l"><span class="tag">${m.fam}</span></td><td class="num">${m.n}</td><td class="num">${fmt(m.tot)}</td><td class="l">${stack(m.a)}</td></tr>`; }
  return h+`</tbody></table>`;
}
function vProp(){
  const rs=filtered(); if(!rs.length) return `<div class="empty">无样本</div>`;
  const kind=F.metric==="value"?"val":"cnt"; const tot=sumQ(rs,kind);
  const sum=QO.reduce((s,q)=>s+tot[q],0)||1;
  let h=`<div class="muted" style="margin-bottom:12px">当前筛选下 全部样本合计的品质${F.metric==="value"?"价值":"件数"}占比 · 环图 + 明细</div>`;
  h+=`<div class="cards-row">`;
  h+=`<div class="panel"><div>${donut(tot,184)}</div><div class="donut-cap">品质${F.metric==="value"?"价值":"件数"}构成</div></div>`;
  let pq=QO.map((q,qi)=>({q,qi,v:tot[q],p:100*tot[q]/sum}));
  pq=sortArr("propq", pq, {q:o=>o.qi, v:o=>o.v});
  h+=`<div class="panel" style="flex:1;min-width:320px"><table style="width:100%"><thead><tr>`+sh("propq","q","品质","l")+sh("propq","v","合计"+(F.metric==="value"?"价值":"件数"),"num")+`<th class="num">占比</th><th class="l" style="min-width:150px">占比图</th></tr></thead><tbody>`+
    pq.map(o=>{const q=o.q,p=o.p; return `<tr><td class="l"><b style="display:inline-block;width:11px;height:11px;border-radius:3px;background:${QC[q]};margin-right:6px;vertical-align:-1px"></b>${QL[q]}</td><td class="num">${fmt1(o.v)}</td><td class="num">${p.toFixed(1)}%</td><td class="l"><span class="qbar" style="width:150px;color:${QC[q]}"><i style="width:${p.toFixed(1)}%;background:currentColor"></i></span></td></tr>`}).join("")+
    `</tbody></table></div></div>`;
  // 稀有品(红/金)专项: 出现局数 / 爆率(出现该品质的局占比) / 累计件数 / 每局均件 / 单件均价。
  const rare=q=>{ const g=rs.filter(r=>(r.cnt[q]||0)>0).length,
      cn=rs.reduce((s,r)=>s+(r.cnt[q]||0),0), vv=rs.reduce((s,r)=>s+((r.val||{})[q]||0),0);
    return {g, rate:rs.length?g/rs.length:0, cn, per:rs.length?cn/rs.length:0, unit:cn>0?vv/cn:0}; };
  h+=`<div class="sec" style="margin-top:18px">稀有品专项(红 / 金)</div>`;
  let rq=["q6","q5"].map(q=>({q,x:rare(q)}));
  rq=sortArr("prop2", rq, {g:o=>o.x.g, rate:o=>o.x.rate, cn:o=>o.x.cn, per:o=>o.x.per, unit:o=>o.x.unit});
  h+=`<table style="max-width:760px"><thead><tr><th class="l">品质</th>`+sh("prop2","g","出现局数","num")+
     sh("prop2","rate","爆率","num")+sh("prop2","cn","累计件数","num")+sh("prop2","per","每局均件","num")+sh("prop2","unit","品质均价","num")+
     `<th class="l" style="min-width:120px">出现局占比</th></tr></thead><tbody>`+
     rq.map(({q,x})=>{ return `<tr>`+
       `<td class="l"><b style="display:inline-block;width:11px;height:11px;border-radius:3px;background:${QC[q]};margin-right:6px;vertical-align:-1px"></b>${QL[q]}</td>`+
       `<td class="num">${x.g} / ${rs.length}</td>`+
       `<td class="num" style="color:${QC[q]};font-weight:600">${(100*x.rate).toFixed(1)}%</td>`+
       `<td class="num">${fmt1(x.cn)}</td><td class="num">${x.per.toFixed(2)}</td>`+
       `<td class="num">${x.unit>0?fmt(x.unit):"—"}</td>`+
       `<td class="l"><span class="qbar" style="width:120px;color:${QC[q]}"><i style="width:${(100*x.rate).toFixed(1)}%;background:currentColor"></i></span></td></tr>`}).join("")+
     `</tbody></table>`;
  h+=`<div class="muted" style="margin-top:12px"><b>爆率</b>=出现该品质的局数 / 总样本局数(非物品级概率);<b>品质均价</b>=该品质总价值 / 总件数(整档平均, 区别于下方"具体藏品"表的<b>单品均价</b>=逐藏品单件价)。</div>`;
  // 具体红/金藏品(物品级): rare_items 聚合(新版客户端起采, 旧样本无)。
  const ia={}; rs.forEach(r=>(r.rare||[]).forEach(it=>{ const k=it.n+"|"+it.q;
    const o=ia[k]=ia[k]||{n:it.n,q:it.q,cnt:0,vsum:0,vn:0};
    o.cnt++; if(typeof it.v==="number"){o.vsum+=it.v;o.vn++;} }));
  let items=Object.values(ia); const withRare=rs.filter(r=>(r.rare||[]).length).length;
  h+=`<div class="sec" style="margin-top:20px">具体红/金藏品 · 出现次数</div>`;
  if(items.length){
    if(!SORT.rare) SORT.rare=[{k:"cnt",dir:-1}];
    items=sortArr("rare", items, {name:o=>o.n,q:o=>o.q,cnt:o=>o.cnt,val:o=>o.vn?o.vsum/o.vn:0});
    const mx=Math.max(1,...items.map(o=>o.cnt));
    const tot=items.length, rcap=F.rcap||50, rshown=items.slice(0,rcap);
    h+=`<div class="muted" style="margin:-4px 0 8px">来自带物品明细的 ${withRare}/${rs.length} 局, 共 ${tot} 种藏品。出现次数=该藏品在这些局累计出现件数。点表头排序, 悬停看明细。</div>`;
    h+=`<table style="max-width:720px"><thead><tr><th class="num">#</th>`+
       sh("rare","name","藏品","l")+sh("rare","q","品质","l")+sh("rare","cnt","出现次数","num")+sh("rare","val","单品均价","num")+
       `<th class="l" style="width:30%">次数</th></tr></thead><tbody>`;
    rshown.forEach((o,i)=>{ h+=`<tr title="${esc(o.n)} · ${o.q===6?'红':'金'} · 出现${o.cnt}件${o.vn?' · 单价'+fmt(o.vsum/o.vn):''}"><td class="num muted">${i+1}</td><td class="l">${esc(o.n)}</td>`+
       `<td class="l"><b style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${QC['q'+o.q]};margin-right:5px;vertical-align:-1px"></b>${o.q===6?'红':'金'}</td>`+
       `<td class="num" style="font-weight:600">${o.cnt}</td><td class="num">${o.vn?fmt(o.vsum/o.vn):'—'}</td>`+
       `<td class="l"><span class="qbar" style="width:100%;max-width:220px;color:${QC['q'+o.q]}" title="${o.cnt}件"><i style="width:${Math.round(100*o.cnt/mx)}%;background:currentColor"></i></span></td></tr>`; });
    h+=`</tbody></table>`;
    if(tot>rcap) h+=`<div style="text-align:left;padding:8px 0"><b class="seg" style="cursor:pointer;padding:6px 16px" onclick="F.rcap=${rcap+50};render()">展示更多 ${tot-rcap} 种 ▾</b></div>`;
    else if(rcap>50) h+=`<div style="text-align:left;padding:6px 0"><b class="seg" style="cursor:pointer;padding:5px 12px" onclick="F.rcap=50;render()">收起 ▴</b></div>`;
  } else {
    h+=`<div class="muted" style="margin:-4px 0 8px">物品级明细(具体藏品名)需新版客户端上传 <code>rare_items</code> 字段, 旧样本未采 → 新打的局起按具体红/金藏品名统计。</div>`;
  }
  return h;
}
function vHeroes(){
  const rs=filtered(); if(!rs.length) return `<div class="empty">无样本</div>`;
  const byH={}; rs.forEach(r=>(byH[r.hero]=byH[r.hero]||[]).push(r));
  const NR=rs.length;
  let hs=Object.entries(byH).map(([hero,rr])=>{
    // 出场率 / 胜率 / 均收益 —— 全部站在【上传者本机】视角(r.player), 与「玩家」视图同口径。
    const withP=rr.filter(r=>r.player&&r.player!=="?"&&r.winner);
    const wins=withP.filter(r=>r.winner===r.player).length;
    const pv = rr.map(selfProfit);
    return {hero,rr,n:rr.length,c:avgQ(rr,"cnt"),sd:sdQ(rr,"cnt"),
      pick:rr.length/NR,                                            // 出场率
      winR: withP.length?wins/withP.length:-1,                      // 胜率(-1=无从判定, 沉底)
      prof: pv.reduce((s,v)=>s+v,0)/pv.length,                      // 均收益(每局)
      profSem: sem(pv),                                             // 均值的标准误(跨0 = 分不出来)
      lot:rr.reduce((s,r)=>s+r.lot,0)/rr.length,
      redR:rr.filter(r=>(r.cnt.q6||0)>0).length/rr.length, goldR:rr.filter(r=>(r.cnt.q5||0)>0).length/rr.length};
  });
  if(!SORT.hero) SORT.hero=[{k:"n",dir:-1}];
  const hget={hero:o=>o.hero,n:o=>o.n,pick:o=>o.pick,winR:o=>o.winR,prof:o=>o.prof,
              lot:o=>o.lot,redR:o=>o.redR,goldR:o=>o.goldR};
  QO.forEach(q=>hget["c_"+q]=o=>o.c[q]);
  hs=sortArr("hero", hs, hget);
  const maxc=Math.max(1,...hs.flatMap(o=>QO.map(q=>o.c[q])));
  let h=`${legend()}<div class="muted" style="margin-bottom:10px">英雄<b>战绩</b> + 各自的掉率画像 · 出场率=该英雄占全部样本的比例 · <b>胜率/均收益 = 上传者本机视角</b>(与「玩家」视图同一套口径) · 点表头排序</div>`;
  h+=`<table><thead><tr>`+sh("hero","hero","英雄","l")+sh("hero","n","样本","num")+sh("hero","pick","出场率","num")+
    sh("hero","winR","胜率","num")+sh("hero","prof","均收益","num")+sh("hero","lot","均拍品值","num")+
    QO.map(q=>sh("hero","c_"+q,QS[q]+"件","num")).join("")+sh("hero","redR","红率","num")+sh("hero","goldR","金率","num")+`<th class="l">构成</th></tr></thead><tbody>`;
  for(const o of hs){ const c=o.c;
    const pc = o.prof>=0 ? "var(--q5)" : "var(--q6)";               // 赚=金 / 亏=红
    h+=`<tr><td class="l">${esc(heroName(o.hero))} <span class="muted" style="font-size:10px">${esc(o.hero)}</span></td><td class="num">${o.n}</td>`+
      `<td class="num">${(100*o.pick).toFixed(1)}%</td>`+
      `<td class="num">${o.winR<0?"—":(100*o.winR).toFixed(0)+"%"}</td>`+
      `<td class="num"><span style="color:${pc};font-weight:600">${o.prof>=0?"+":""}${fmt(o.prof)}</span>${pmSem(o.profSem)}</td>`+
      `<td class="num">${fmt(o.lot)}</td>`+
      QO.map(q=>`<td class="num" title="${QS[q]} 每局 ${fmt1(c[q])} ±${sdTxt(o.sd[q])} 件">${qnum(c[q],maxc,QC[q],QS[q]+" "+fmt1(c[q])+"±"+sdTxt(o.sd[q])+"件/局")}<span class="pm">±${sdTxt(o.sd[q])}</span></td>`).join("")+
      `<td class="num" style="color:${QC.q6}">${(100*o.redR).toFixed(0)}%</td><td class="num" style="color:${QC.q5}">${(100*o.goldR).toFixed(0)}%</td>`+
      `<td class="l" style="min-width:150px">${stack(c)}</td></tr>`; }
  return h+`</tbody></table><div class="muted" style="margin-top:8px">🔴 <b>掉率列不是英雄属性</b>: 掉落只由<b>地图</b>决定(英雄技能只改变你<b>看到</b>多少信息, 不改变箱子里有什么)。这几列的英雄间差异 = <b>"玩该英雄的人爱打哪张图"</b>的投影 —— 陈美红 8.9/局, 是因为她的玩家扎堆打隐秘拍卖会(那张图本身红 9.3), 不是"选她能多出红"。真掉率看「地图」视图。<br><b>均收益</b>=每局平均盈亏:每位玩家(含拿仓者)均获赢家亏损×10%的分红;拍下=藏品真值−自己出价+分红−门票,未拍下=分红−门票。上传者本人未拿仓时优先消费结算帧直出福利，其他玩家按规则推导并保留来源标签。<b>正=金 / 负=红</b>; <b>掉率列的 ± 是标准差(波动)</b>, <b>均收益的 ± 是均值标准误</b> —— 跨 0 就说明和"不赚不亏"分不开(实测全库 14 个英雄里 12 个跨 0)。<br>⚠️样本少的英雄(出场率低)其胜率/收益只是少数几局的结果, 别当定论。</div>`;
}
function vTools(){
  const rs=filtered();
  function freq(key){ const c={}; let w=0; rs.forEach(r=>{ const a=r[key]; if(a&&a.length){ w++; new Set(a).forEach(t=>c[t]=(c[t]||0)+1); } });
    return {items:Object.entries(c).sort((x,y)=>y[1]-x[1]), w}; }
  const dao=freq("tools"), her=freq("hskills");
  if(!dao.items.length && !her.items.length) return `<div class="empty">暂无道具/技能数据<br><span class="muted">旧样本未带;新版客户端上传会带, 新打的局起有</span></div>`;
  function block(title, f, color, tid){
    if(!f.items.length) return `<div class="sec">${title}</div><div class="muted" style="margin:-4px 0 8px">(暂无)</div>`;
    const mx=Math.max(1,...f.items.map(e=>e[1]));
    let rows=f.items.map(([t,n])=>({t,n}));
    if(!SORT[tid]) SORT[tid]=[{k:"n",dir:-1}];
    rows=sortArr(tid, rows, {name:o=>o.t, n:o=>o.n});
    return `<div class="sec">${title} · ${f.w} 局有</div><table style="max-width:680px;margin-bottom:8px"><thead><tr><th class="num">#</th>`+sh(tid,"name","名称","l")+sh(tid,"n","局数","num")+`<th class="l" style="width:48%">频次</th></tr></thead><tbody>`+
      rows.map((o,i)=>`<tr><td class="num muted">${i+1}</td><td class="l">${esc(o.t)}</td><td class="num">${o.n}</td><td class="l"><span class="qbar" style="width:100%;max-width:260px;color:${color}"><i style="width:${Math.round(100*o.n/mx)}%;background:currentColor"></i></span></td></tr>`).join("")+`</tbody></table>`;
  }
  let h=`<div class="muted" style="margin-bottom:6px"><b>付费道具</b>=玩家主动用的;<b>英雄技能</b>=每轮自动揭示。频次=出现该项的局数。(公开信息不计入)</div>`;
  h+=block("付费道具", dao, "var(--q5)", "toolsd");
  h+=block("英雄技能 / 揭示", her, "var(--q3)", "toolsh");
  return h;
}
let _proster=[];
function pickP(i){ F.player=_proster[i]; F.view="overview"; render(); }
function vPlayers(){
  // 参与者维度: 尊重 族/图/英雄/时间 筛选, 不受 player 筛选限制。
  const rs=ROWS.filter(r=>inRange(r)&&(F.fam==="全部"||r.fam===F.fam)&&(F.map==="全部"||String(r.map)===String(F.map))&&(F.hero==="全部"||r.hero===F.hero));
  const P={}; const self=SELF_PLAYERS;
  rs.forEach(r=>{ (r.parts||[]).forEach(p=>{ if(!p||p==="?") return;
    const o=P[p]=P[p]||{p, n:0,win:0,profit:0,hasprofit:false,fams:{},d:[], rank:null,ranktier:null,ranklast:-1,rankfirst:-1,rankbase:null,rankn:0};
    o.n++;
    if(r.winner===p) o.win++;
    const pp=playerProfit(r,p); o.profit+=pp.value; o.hasprofit=true;
    // 排位分(取最新一次观测): 本机/上传者用赛后分(rank_after,更精确); 非本机用其出价块赛前分(player_ranks[昵称])。
    let _pr=null, _pt=null;
    if(p===r.player && r.rank_after!=null){ o.rankn++; _pr=r.rank_after; _pt=r.rank_tier;
      // 净变化基线: 取最早一局的赛前分(rank_pts)。端点法(最新赛后分−最早赛前分)gap-robust:
      // 每局上报绝对分, 跨未观测局也准, 不累加涨跌(累加会漏未观测局)。仅我方有赛前/赛后双值。
      const _tb=r._d?+r._d:0;
      if(r.rank_pts!=null && (o.rankfirst<0 || _tb<o.rankfirst)){ o.rankfirst=_tb; o.rankbase=r.rank_pts; } }
    else if(r.player_ranks && r.player_ranks[p]!=null){ _pr=r.player_ranks[p].p; _pt=r.player_ranks[p].t; }
    if(_pr!=null){ const t=r._d?+r._d:0; if(o.ranklast<0||t>=o.ranklast){ o.ranklast=t; o.rank=_pr; o.ranktier=_pt; } }
    o.fams[r.fam]=(o.fams[r.fam]||0)+1; if(r._d)o.d.push(r._d); }); });
  let ps=Object.values(P);
  const total=ps.length;                                         // 出现过的玩家(全体·KPI 用)
  if(F.pside==="self") ps=ps.filter(o=>self.has(o.p));          // 我方筛选: 仅本工具上传者
  else if(F.pside==="other") ps=ps.filter(o=>!self.has(o.p));   //           仅非我方
  const sideTotal=ps.length;                                     // 当前"我方侧"人数(搜索前)=match 比例分母
  const q=(F.psearch||"").trim().toLowerCase();
  if(q) ps=ps.filter(o=>o.p.toLowerCase().includes(q));   // 按昵称模糊搜索
  // 默认排序: 我方(本工具上传者)在前, 组内按昵称字母/拼音序(作者 2026-06-26: 别让我方玩家因场次少被排到后面)。
  // 点表头才切到该列排序(SORT.players 一旦设置=非空数组, 下面 sortArr 接管, 此默认让位)。
  if(!SORT.players || !SORT.players.length)
    ps.sort((a,b)=>{ const sa=self.has(a.p)?0:1, sb=self.has(b.p)?0:1; return sa!==sb ? sa-sb : a.p.localeCompare(b.p,"zh"); });
  ps.forEach(o=>{ const t=Object.entries(o.fams).sort((a,b)=>b[1]-a[1])[0]; o.topfam=t?t[0]:""; });
  ps=sortArr("players", ps, {player:o=>o.p, n:o=>o.n, win:o=>o.win, wr:o=>o.n?o.win/o.n:0,
                             profit:o=>o.hasprofit?o.profit:-1e18, rank:o=>o.rank!=null?o.rank:-1e18,
                             fam:o=>o.topfam, last:o=>o.d.length?+o.d[o.d.length-1]:0});
  const matched=ps.length, cap=F.pcap||50, shown=ps.slice(0,cap);
  _proster=shown.map(o=>o.p);                              // 与点行 pickP 下标对齐(显示的)
  const games=rs.length;
  const kpi=(v,k)=>`<div class="kpi"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  let h=`<div class="kpis">${kpi(total,"出现过的玩家")}${kpi(games,"对局样本")}${kpi(fmt(ps.reduce((s,o)=>s+o.n,0)/Math.max(1,matched)),"人均出现场次")}</div>`;
  // 搜索: 【点搜索按钮 或 回车】才搜(非输入即搜)——拼音 IME 合成中间态(jiafei→加菲)不再误触发;
  // event.isComposing 保证合成期回车(选字)不提交。我方筛选: 全部/我方(本工具上传者)/非我方。
  h+=`<div style="margin:2px 0 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">`+
     `<input class="search" id="psearch" type="text" placeholder="搜索玩家昵称(回车或点搜索)…" value="${esc(F.psearch||"")}" `+
     `onkeydown="if(event.key==='Enter'&&!event.isComposing){commitPsearch(this.value)}">`+
     `<b class="seg" style="cursor:pointer;padding:6px 14px" onclick="commitPsearch(document.getElementById('psearch').value)">搜索</b>`+
     (F.psearch?`<b class="seg" style="cursor:pointer;padding:6px 12px;color:var(--gold)" onclick="commitPsearch('')">✕ 清除</b>`:``)+
     `<span class="muted" style="margin-left:4px">我方</span><span class="seg">`+
       [["全部","全部"],["self","我方"],["other","非我方"]].map(([v,l])=>`<b class="${(F.pside||"全部")===v?"on":""}" style="cursor:pointer" onclick="F.pside='${v}';F.pcap=50;render()">${l}</b>`).join("")+`</span>`+
     ((q||(F.pside&&F.pside!=="全部"))?` <span class="muted" style="margin-left:4px">${q?`匹配 ${matched}/${sideTotal}`:`共 ${sideTotal}`} 名玩家</span>`:``)+`</div>`;
  if(!ps.length) return h+`<div class="empty">${q?"无匹配玩家":"无玩家数据"}</div>`;
  h+=`<div class="muted" style="margin-bottom:9px">按昵称聚合本地观测到的对局(<b>场次</b>=出现局数, <b>拍下/拿仓率</b>=该玩家赢下的局/占比=拿仓次数/出现次数(所有玩家都显示;场次少时占比参考意义有限,旁边并列「拍下/场次」自行判断)。<b>总收益</b>=每局结算累加:每位玩家(含拿仓者)均获赢家亏损×10%的<b>规则分红</b>;拍下局=拍品价值−出价+分红−门票,未拍下局=分红−门票。正=赚 负=亏;<b>无出价数据</b>(bids未捕获)时仅扣门票=盈亏下限。拿仓率<b>颜色</b>:橙=我方、金=非我方。<b>排位分</b>=该玩家<b>最新观测排位分 + 段位</b>(<span class="tag">我方</span>用赛后分,非我方用其出价时赛前分;均以最新上传为准);括号内为<b>本段净变化</b>(最新赛后分−最早赛前分,端点法 gap-robust,仅我方有此双值,期间缺局不冒充单局变化)。我方详细走势见左侧「排位」视图。非排位局为—。点行→该玩家全部对局。<span class="tag">我方</span>=曾用本工具上传者。点表头排序。</div>`;
  h+=`<table><thead><tr><th class="num">#</th>`+
     sh("players","player","玩家","l")+sh("players","n","场次","num")+sh("players","win","拍下","num")+
     sh("players","wr","拿仓率","l")+sh("players","profit","总收益","num")+sh("players","rank","排位分","num")+
     sh("players","fam","常去族","l")+sh("players","last","活跃区间","num")+`</tr></thead><tbody>`;
  shown.forEach((o,i)=>{ const wr=o.n?o.win/o.n:0; const top=Object.entries(o.fams).sort((a,b)=>b[1]-a[1])[0];
    o.d.sort((x,y)=>x-y);
    h+=`<tr style="cursor:pointer" onclick="pickP(${i})">`+
      `<td class="num muted">${i+1}</td>`+
      `<td class="l">${esc(o.p)}${self.has(o.p)?' <span class="tag">我方</span>':''}</td>`+
      `<td class="num">${o.n}</td><td class="num">${o.win}</td>`+
      // 拿仓率: 所有玩家都显示(拍下/出现 = 拿仓次数/出现次数)。彩色% + 柔和细条 + 灰字分数。
      // 颜色: 我方(本工具上传者)橙、非我方金。% 右对齐定宽 + 细条定宽 -> 多行对齐; 单场即 0/100%(场次已并列, 自行判断置信)。
      `<td class="l">${(()=>{ const _wc=self.has(o.p)?'var(--gold)':'var(--q5)'; const _p=Math.round(100*wr);
        return `<span style="display:inline-flex;align-items:center;gap:7px">`
          + `<b style="color:${_wc};min-width:32px;text-align:right">${_p}%</b>`
          + `<span style="display:inline-block;width:52px;height:6px;border-radius:3px;background:var(--track);overflow:hidden">`
          + `<span style="display:block;height:100%;width:${_p}%;background:${_wc};opacity:.6"></span></span>`
          + `<span class="muted" style="font-size:11px">${o.win}/${o.n}</span></span>`; })()}</td>`+
      `<td class="num">${o.hasprofit?pl(o.profit):'<span class="muted">—</span>'}</td>`+
      // 排位分: 分数(右对齐定宽) + 净变化(定宽, 我方才有赛前/赛后双值) + 段位(灰字定宽)。
      // 三段定宽 -> 跨行的分数/涨跌/段位各自成列对齐, 比内联拼接整齐(作者 2026-06-26 反馈)。
      `<td class="num">${o.rank!=null?(()=>{ const _nv=(o.rankbase!=null)?Math.round(o.rank-o.rankbase):null;
        return `<span style="display:inline-flex;align-items:baseline;gap:6px;justify-content:flex-end">`
          + `<b style="min-width:34px;text-align:right">${Math.round(o.rank)}</b>`
          + `<span class="${_nv>0?'pos':(_nv<0?'neg':'muted')}" style="font-size:11px;min-width:40px;text-align:right">${(_nv!=null&&_nv!==0)?`(${_nv>0?'+':''}${_nv})`:''}</span>`
          + `<span class="muted" style="font-size:11px;min-width:44px;text-align:left">${o.ranktier!=null?tierName(o.ranktier):''}</span></span>`; })():'<span class="muted">—</span>'}</td>`+
      `<td class="l muted">${top?esc(top[0]):"—"}</td>`+
      `<td class="num muted">${o.d.length?ymd(o.d[0]).slice(5)+" ~ "+ymd(o.d[o.d.length-1]).slice(5):"—"}</td></tr>`; });
  h+=`</tbody></table>`;
  if(matched>cap) h+=`<div style="text-align:center;padding:12px"><b class="seg" style="cursor:pointer;padding:6px 16px" onclick="F.pcap=${cap+50};render()">展示剩余 ${matched-cap} 名 ▾</b></div>`;
  else if(cap>50) h+=`<div style="text-align:center;padding:8px"><b class="seg" style="cursor:pointer;padding:5px 12px" onclick="F.pcap=50;render()">收起 ▴</b></div>`;
  return h;
}

// ---- 排位视图: 逐玩家排位分走势(断断续续上传 -> 按观测局连点) ----
let _rankroster=[];
function pickRank(i){ F.player=_rankroster[i]; render(); }
function rankChart(pts, w, h, mini){   // pts=[{date,before,after,delta}] 已按 t 升序; mini=概览走势缩略(无轴/无标签)
  w=w||620; h=h||240; const n=pts.length; if(!n) return "";
  const pad = mini ? {l:5,r:5,t:6,b:6} : {l:50,r:14,t:16,b:30};
  const ys=pts.flatMap(p=>[p.after,p.before]).filter(v=>typeof v==="number");
  let lo=Math.min(...ys), hi=Math.max(...ys); if(lo===hi){lo-=20;hi+=20;}
  const iw=w-pad.l-pad.r, ih=h-pad.t-pad.b;
  const X=i=> pad.l + (n<=1 ? iw/2 : i*iw/(n-1));
  const Y=v=> pad.t + (1-(v-lo)/(hi-lo))*ih;
  const path=pts.map((p,i)=>`${i?"L":"M"}${X(i).toFixed(1)} ${Y(p.after).toFixed(1)}`).join(" ");
  const r=mini?2.3:3.8;
  const dots=pts.map((p,i)=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(p.after).toFixed(1)}" r="${r}" fill="var(--q5)"${mini?"":' stroke="var(--panel)" stroke-width="1.5"'}><title>${p.date} 赛前${Math.round(p.before)}→赛后${Math.round(p.after)} (${p.delta>=0?"+":""}${Math.round(p.delta)})</title></circle>`).join("");
  let g=`<path d="${path}" fill="none" stroke="var(--q5)" stroke-width="${mini?1.6:2}"/>${dots}`;
  if(!mini){
    g+=`<text x="6" y="${(Y(hi)+4).toFixed(1)}" font-size="11" fill="var(--dim)">${Math.round(hi)}</text>`+
       `<text x="6" y="${(Y(lo)+4).toFixed(1)}" font-size="11" fill="var(--dim)">${Math.round(lo)}</text>`+
       `<text x="${X(0).toFixed(1)}" y="${h-9}" text-anchor="${n<=1?"middle":"start"}" font-size="11" fill="var(--dim)">${pts[0].date}</text>`+
       (n>1?`<text x="${X(n-1).toFixed(1)}" y="${h-9}" text-anchor="end" font-size="11" fill="var(--dim)">${pts[n-1].date}</text>`:"");
    // 每点透明 hover 区(整列, 鼠标好触发): 悬停看 赛前→赛后(涨跌)
    g+=pts.map((p,i)=>{ const colw=n>1?iw/(n-1):iw;
      return `<rect x="${(X(i)-colw/2).toFixed(1)}" y="${pad.t}" width="${colw.toFixed(1)}" height="${ih}" fill="transparent" style="cursor:crosshair"><title>${p.date}  赛前 ${Math.round(p.before)} → 赛后 ${Math.round(p.after)}  (${p.delta>=0?"+":""}${Math.round(p.delta)})</title></rect>`; }).join("");
  }
  return `<svg width="${w}" height="${h}"${mini?"":` viewBox="0 0 ${w} ${h}"`}>${g}</svg>`;
}
function _rankByPlayer(){
  const rs=ROWS.filter(r=>inRange(r) && r.rank_after!=null && (F.fam==="全部"||r.fam===F.fam) && (F.hero==="全部"||r.hero===F.hero) && (F.map==="全部"||String(r.map)===String(F.map)));
  const byp={};
  rs.forEach(r=>{ const p=r.player; if(!p||p==="?") return;
    (byp[p]=byp[p]||[]).push({t:r._d?+r._d:0, date:r._d?ymd(r._d).slice(5):"?", before:(r.rank_pts!=null?r.rank_pts:r.rank_after), after:r.rank_after, delta:(r.rank_delta!=null?r.rank_delta:0), tier:r.rank_tier, map:r.name||r.map}); });
  Object.values(byp).forEach(a=>a.sort((x,y)=>x.t-y.t));
  return byp;
}
// rank_tier(1..32) -> 大段位+小段位。结构是从 4.5 万条真实数据反推的, 不是猜的:
//   档位 1~5   分数 3~1200      每档 ~100 分宽(档位5 中位 = 1200 = 账号初始积分)
//   档位 6~30  分数 1201~2200   每档【正好 40 分】宽
//   档位 31    分数 2201~2300
//   档位 32    分数 2301+       (封顶开放段)
// 32 = 6 个大段位 × 5 小段 + 竞拍大师 + 竞拍之王(这两档无小段) —— 与游戏内公示的 8 大段位
// (黑铁/青铜/白银/黄金/白金/钻石/竞拍大师/竞拍之王)严丝合缝。
// ⚠️小段位【1 = 该大段位最高】(作者 0712: "这游戏段位和英雄联盟对齐" —— LoL 里 黑铁IV→黑铁I 往上爬)。
//   所以档位1(3~794分, 最低) = 黑铁5, 档位5(1101~1200, 初始分) = 黑铁1(打到 1200 就是黑铁顶, 再升进青铜)。
//   表里【段位名和分数区间一起显示】: 万一名字错一档, 对着区间一眼就能看出来并订正。
const RANK_BIG=["黑铁","青铜","白银","黄金","白金","钻石"];
function tierName(t){
  t=+t;
  if(t===31) return "竞拍大师";
  if(t>=32)  return "竞拍之王";
  if(t>=1 && t<=30){ const b=Math.floor((t-1)/5), s=5-((t-1)%5); return RANK_BIG[b]+s; }
  return "段位 "+t;
}
// 段位【分布】(聚合口径, 与下方"按玩家走势"是两回事): 这一段位有多少局、平均排位分、
// 平均拍品价值、平均收益。回答"我这个段位的人一般打什么价、赚不赚" —— 走势图回答不了这个。
function vRankDist(rs){
  const byT={};
  rs.forEach(r=>{ const t=r.rank_tier; if(t==null) return;
    const o=byT[t]=byT[t]||{t,n:0,pts:0,ptsn:0,lot:0,pv:[],lo:Infinity,hi:-Infinity};
    o.n++; o.lot+=r.lot||0; o.pv.push(selfProfit(r));
    const p=(r.rank_pts!=null?r.rank_pts:r.rank_after);
    if(typeof p==="number"){ o.pts+=p; o.ptsn++; o.lo=Math.min(o.lo,p); o.hi=Math.max(o.hi,p); } });
  let ts=Object.values(byT).map(o=>({...o, avgPts:o.ptsn?o.pts/o.ptsn:-1, avgLot:o.lot/o.n,
    avgProf:o.pv.reduce((s,v)=>s+v,0)/o.pv.length, profSem:sem(o.pv)}));
  if(!ts.length) return "";
  if(!SORT.rkd) SORT.rkd=[{k:"t",dir:1}];
  ts=sortArr("rkd", ts, {t:o=>o.t,n:o=>o.n,avgPts:o=>o.avgPts,avgLot:o=>o.avgLot,avgProf:o=>o.avgProf});
  const mx=Math.max(1,...ts.map(o=>o.n)), tot=ts.reduce((s,o)=>s+o.n,0);
  let h=`<div class="sec">段位分布 · ${tot} 局有段位</div>`;
  h+=`<div class="muted" style="margin:-4px 0 10px">段位名按<b>英雄联盟式</b>映射(小段位 <b>1 = 该大段位最高</b>)。<b>分数区间</b>一并列出 —— 名字若与游戏内对不上, 对着区间即可订正。<br>账号<b>初始积分 1200 = 黑铁1</b>(所以那一档占比特别高: 大多是没打过排位/刚重置的号, <b>属正常</b>)。均收益口径同「英雄」/「玩家」视图。<br>⚠️<b>均收益的 ± 是均值标准误</b>: 单局盈亏几十万级、每档只有几百局 —— 实测全库 <b>32 档里 29 档的 95% 区间跨 0</b>, 段位间的收益差异<b>基本分不出来</b>。而且在 32 个档上比, 就算真实差异全为 0 也会有一两档纯靠运气显得"显著" —— <b>别拿单档的正负号当结论</b>。</div>`;
  h+=`<table style="max-width:960px"><thead><tr>`+sh("rkd","t","段位","l")+`<th class="l">分数区间</th>`+sh("rkd","n","局数","num")+
    `<th class="l" style="width:24%">占比</th>`+sh("rkd","avgPts","均排位分","num")+
    sh("rkd","avgLot","均拍品值","num")+sh("rkd","avgProf","均收益","num")+`</tr></thead><tbody>`;
  for(const o of ts){ const pc=o.avgProf>=0?"var(--q5)":"var(--q6)";
    const rg = o.ptsn ? `${fmt(o.lo)} ~ ${fmt(o.hi)}` : "—";
    h+=`<tr><td class="l"><b>${tierName(o.t)}</b> <span class="muted">#${o.t}</span></td>`+
      `<td class="l muted">${rg}</td><td class="num">${o.n}</td>`+
      `<td class="l"><span class="qbar" style="width:100%;max-width:200px;color:var(--q5)"><i style="width:${Math.round(100*o.n/mx)}%;background:currentColor"></i></span> <span class="muted">${(100*o.n/tot).toFixed(1)}%</span></td>`+
      `<td class="num">${o.avgPts<0?"—":fmt(o.avgPts)}</td><td class="num">${fmt(o.avgLot)}</td>`+
      `<td class="num"><span style="color:${pc};font-weight:600">${o.avgProf>=0?"+":""}${fmt(o.avgProf)}</span>${pmSem(o.profSem)}</td></tr>`; }
  return h+`</tbody></table>`;
}
function vRank(){
  const byp=_rankByPlayer();
  const players=Object.keys(byp).sort((a,b)=>byp[b].length-byp[a].length);
  if(!players.length) return `<div class="empty">无排位数据<br><span class="muted">仅 <span class="tag">我方</span> 上传者的排位局有;排位分选块修复后新打的排位局起准</span></div>`;
  let h=vRankDist(filtered());
  h+=`<div class="muted" style="margin:14px 0 10px">排位分走势(归上传者/<span class="tag">我方</span>)。玩家断断续续上传 → 折线按<b>观测到的局</b>连点(每局上报真实当前分=真实快照;点间可能跨未观测局)。顶部「玩家」筛选 或 点下方某行 → 看该玩家详细走势。</div>`;
  const sel = (F.player!=="全部" && byp[F.player]) ? F.player : (players.length===1 ? players[0] : null);
  if(sel){
    const a=byp[sel], last=a[a.length-1], first=a[0], net=Math.round(last.after-first.before);
    h+=`<div class="sec">${esc(sel)} · ${last.tier!=null?tierName(last.tier):"—"} · 最新赛后分 <b>${Math.round(last.after)}</b> · 观测区间净 <span style="color:${net>=0?"var(--pos)":"var(--neg)"}">${net>=0?"+":""}${net}</span> · ${a.length} 局 · ${first.date}~${last.date}</div>`;
    h+=`<div class="panel" style="overflow-x:auto;margin:6px 0 12px">${rankChart(a, Math.max(560, a.length*64))}</div>`;
    if(!SORT.rankd) SORT.rankd=[{k:"t",dir:-1}];   // 默认最近在上
    const drows=sortArr("rankd", a.slice(), {t:p=>p.t, map:p=>String(p.map), before:p=>p.before, after:p=>p.after, delta:p=>p.delta});
    h+=`<table style="max-width:720px"><thead><tr>`+sh("rankd","t","时间","l")+sh("rankd","map","地图","l")+
       sh("rankd","before","赛前","num")+sh("rankd","after","赛后","num")+sh("rankd","delta","涨跌","num")+`</tr></thead><tbody>`;
    drows.forEach(p=>{ h+=`<tr><td class="l">${p.date}</td><td class="l muted">${esc(String(p.map))}</td><td class="num muted">${Math.round(p.before)}</td><td class="num">${Math.round(p.after)}</td><td class="num"><span style="color:${p.delta>=0?"var(--pos)":"var(--neg)"}">${p.delta>=0?"+":""}${Math.round(p.delta)}</span></td></tr>`; });
    return h+`</tbody></table><div style="margin-top:10px"><b class="seg" style="cursor:pointer;padding:5px 12px" onclick="setF('player','全部')">← 全部排位玩家</b></div>`;
  }
  let rrows=players.map(p=>{ const a=byp[p], last=a[a.length-1], first=a[0];
    return {p, a, tier:last.tier, after:last.after, net:Math.round(last.after-first.before),
            n:a.length, first:first.t, span:first.date+"~"+last.date}; });
  if(!SORT.rank) SORT.rank=[{k:"n",dir:-1}];
  rrows=sortArr("rank", rrows, {player:o=>o.p, tier:o=>o.tier!=null?o.tier:-1, after:o=>o.after,
                                net:o=>o.net, n:o=>o.n, first:o=>o.first});
  _rankroster=rrows.map(o=>o.p);   // 点行下标与显示顺序对齐
  h+=`<table><thead><tr><th class="num">#</th>`+sh("rank","player","玩家","l")+sh("rank","tier","段位","num")+
     sh("rank","after","最新赛后分","num")+sh("rank","net","观测区间净","num")+sh("rank","n","观测局","num")+
     sh("rank","first","时间跨度","l")+`<th class="l">走势</th></tr></thead><tbody>`;
  rrows.forEach((o,i)=>{
    h+=`<tr style="cursor:pointer" onclick="pickRank(${i})">`+
      `<td class="num muted">${i+1}</td><td class="l">${esc(o.p)} <span class="tag">我方</span></td>`+
      `<td class="num">${o.tier!=null?tierName(o.tier):"—"}</td><td class="num"><b>${Math.round(o.after)}</b></td>`+
      `<td class="num"><span style="color:${o.net>=0?"var(--pos)":"var(--neg)"}">${o.net>=0?"+":""}${o.net}</span></td>`+
      `<td class="num">${o.n}</td><td class="l muted">${o.span}</td>`+
      `<td class="l">${rankChart(o.a, 170, 40, true)}</td></tr>`; });
  return h+`</tbody></table>`;
}
// 未知品质档告警条: 游戏新版引入 _QKEY 没覆盖的档(如神秘箱子)时一眼可见。静态(不随筛选变), 初始化渲一次。
// ✕ 可关闭: localStorage 记住【本批档位签名】——同一批不再弹(作者已知, 样本在"未知品质档"页有专门留档);
// 出现【新的】未知档(签名变)会重新弹, 保住横幅"抓新档"的本职。
function renderWarnBar(){
  const el=document.getElementById("warnbar"); if(!el) return;
  const w=P.warn_quals||{}; const ks=Object.keys(w).sort();
  if(!ks.length){ el.innerHTML=""; return; }
  const sig=ks.join(",");
  let seen=null; try{ seen=localStorage.getItem("bk_warn_dismissed"); }catch(e){}
  if(seen===sig){ el.innerHTML=""; return; }
  const parts=ks.map(k=>`<b>${esc(k)}</b>(${w[k].games}局/${Math.round(w[k].items)}件, 例 ${esc(w[k].ex)})`).join("、");
  el.innerHTML=`<span style="flex:1">⚠ 发现未知品质档: ${parts} — 看板暂未渲染该档(原始数据已存样本不丢)。在 _QKEY/_QORDER 补该档后重建看板即可追溯显示。</span>`+
    `<span class="warnx" title="知道了, 这批不再提示(出现新档会重新弹; 样本在「未知品质档」页留档)" onclick="dismissWarnBar('${sig}')">✕</span>`;
}
function dismissWarnBar(sig){
  try{ localStorage.setItem("bk_warn_dismissed", sig); }catch(e){}
  const el=document.getElementById("warnbar"); if(el) el.innerHTML="";
}
applyTheme((function(){ try{ return localStorage.getItem("bk_theme"); }catch(e){ return null; } })() || "warm");
renderNav(); renderWarnBar(); render();
</script></body></html>
"""


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=str(ROOT / "data" / "collected" / "samples.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "collected" / "dashboard.html"))
    args = ap.parse_args(argv[1:])
    sp = Path(args.samples)
    if not sp.exists():
        print(f"无 {sp}"); return 1
    samples = _load_samples(sp)
    Path(args.out).write_text(build_dashboard_html(samples), encoding="utf-8")
    print(f"看板: {len(samples)} 条样本 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
