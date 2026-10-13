"""Synthetic draw-hero sample framework.

>>> 权威完整记录(系统总览/机制/引擎通道接入纪律/源池/保真度/脚本/重生成流程):
>>>   docs/HANDOFF_synthetic_sample_system_2026-06-27.zh-CN.md
>>> memory: aisha-synth-fidelity-channels (保真度+通道纪律) / aisha-synthetic-samples-mechanic (机制权威)。
>>> 改机制或引擎通道接入后必跑 calibration/audit_synth_channels.py + compare_synth_vs_real_aisha.py。

Sample-starved draw-heroes (sophie / gabriela) reveal little and we have few real games, but we
have hundreds of SETTLED warehouses (ground truth: every item's quality / value / cells / pos).
This tool simulates a chosen draw-hero PLAYING a real settled warehouse -- replaying that hero's
skill + tool reveal mechanic to expose a growing random subset of items per round, reusing the
source warehouse's public info -- and emits an engine-consumable snapshot per round. Settlement
gives the truth to validate against, so each real warehouse yields many synthetic per-round
draw-hero samples for calibration / P2.

The reveal MECHANIC is documented + user-confirmed in docs/draw_hero_reveal_mechanics.zh-CN.md.
Confirm against that doc before trusting generated data. Summary:
- Granularity tiers: (1) quality+rough-pos, (2) +outline/shape, (3) +value/identity.
- Sources (all uniform-random over the warehouse):
    sophie skill : R1 = 5 (tier1, dup-OK) + 2 (tier1, no-dup); each later round +2 (tier1, no-dup).
    gabriela skill: each round +2 (tier2 outline, no-dup).
    tools (<=1/round, used most rounds): 宝光四鉴 100136 (4,t1,dup), 宝光2鉴 100135 (2,t1,dup),
        抽检2 100129 (2,t3 value,dup), 抽检1 100128 (1,t3,dup), 至宝寻踪 100168 (1,t3, targets
        MAX-QUALITY item), 巨物抽样 100101 (1,t3, targets MAX-FOOTPRINT item).
    public info: reuse the source settlement's exposed reveals (real items of this warehouse).
- Duplicates allowed everywhere EXCEPT sophie R2-R5 skill and gabriela skill (no-dup).

Read-only w.r.t. the sample library. Reusable as a module + CLI.
Run: python scripts/synthesize_draw_hero_samples.py validate sophie [n]
     python scripts/synthesize_draw_hero_samples.py cadence sophie [n]
     python scripts/synthesize_draw_hero_samples.py dump sophie <sample.json> <round>
"""

from __future__ import annotations

import glob
import json
import os
import random
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from audit_aisha_gap import _item_values, _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    _hero_mode_from_state,
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
QMAP = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}

T1, T2, T3 = "quality", "outline", "full"  # reveal granularity tiers

# Public info_id -> ITEM-reveal granularity (only ids that expose actual items). The 公开揭示族
# (轮廓 200001-3 / 至宝 200048 / 巨物 200050) live OUTSIDE 200009-200038 and were previously dropped.
PUBLIC_REVEAL_GRAN = {
    200001: T2, 200002: T2, 200003: T2,  # 轮廓 (品质+轮廓)
    200021: T3, 200022: T3, 200023: T3,  # 详细(随机) 全信息含价值
    200026: T1, 200027: T1, 200028: T1,  # 品质 (只品质)
    200048: T3, 200050: T3,  # 至宝(最高品质) / 巨物(最大占位) 全信息
}
PUBLIC_INFO_LABEL = {
    200001: "公开·轮廓", 200002: "公开·轮廓", 200003: "公开·轮廓",
    200021: "公开·详细×2", 200022: "公开·详细×4", 200023: "公开·详细×6",
    200026: "公开·品质×3", 200027: "公开·品质×6", 200028: "公开·品质×9",
    200048: "公开·至宝(最高品质)", 200050: "公开·巨物(最大占位)",
    200009: "公开·总格", 200017: "公开·总件数",
    200013: "公开·紫均格", 200014: "公开·全均格", 200015: "公开·金均格", 200016: "公开·红均格",
    200018: "公开·紫件数", 200019: "公开·极品存量(金件)",
    200035: "公开·全均价", 200036: "公开·紫均价", 200037: "公开·金均价", 200038: "公开·红均价",
    200031: "公开·随机3均价", 200032: "公开·随机6均价", 200033: "公开·随机9均价", 200034: "公开·随机12均价",
}
# Numeric aggregate facts (no item reveal) + the item-reveal family above.
WAREHOUSE_PUBLIC_IDS = set(range(200009, 200039)) | set(PUBLIC_REVEAL_GRAN)

# tool -> (draws, tier, dup_allowed, target)  target: "random" | "max_quality" | "max_cells"
TOOLS: dict[str, tuple[int, str, bool, str]] = {
    "baoguang4": (4, T1, True, "random"),  # 宝光四鉴: 4件, 只品质
    "baoguang2": (2, T1, True, "random"),  # 宝光2鉴: 2件, 只品质
    "inspect2": (2, T3, True, "random"),  # 抽检2: 2件, 全信息含价值
    "inspect1": (1, T3, True, "random"),  # 抽检1: 1件, 全信息含价值
    "zhibao": (1, T2, False, "rand_top_quality"),  # 至宝寻踪(蓝, id=100161): 最高品质件(红→金→紫), 位置+轮廓, 无价格
    "juwu": (1, T3, False, "max_cells"),  # 巨物抽样(id=100168): 全仓占位最高那件, 全信息含价值
    "appraise": (1, T3, False, "rand_top_quality"),  # 至宝估价(紫, id=100163)=定向抽检1: 最高品质件红→金→紫, 位置+轮廓+价值(黑欧泊只给价=parser去重artifact, 非道具粒度)
    "goldscan": (0, T3, False, "all_q5"),  # 金色扫描(艾莎R4): 揭示全仓 q5(金)全件, 全信息含价值
    "none": (0, T1, True, "random"),
}
# AISHA (structured hero, NOT draw): skill progressively reveals NON-red qualities by tier (R1 白/q1,
# R2 绿/q2, R3 蓝/q3, R4 紫/q4 — cumulative, ALL items of that tier, outline+quality), gold via the R4
# 金色扫描 tool, RED (q6) is NEVER revealed (the whole aisha hidden-red problem). Tool schedule
# (author 2026-06-27): R1 宝光四鉴, R2 抽检2, R3 抽检1, R4 金色扫描, R5 none. By R4-R5 every non-red is
# known -> strong negative evidence (k=all non-red) = exactly the LEVER1 no-red-damp scenario.
AISHA_SKILL_TIER_BY_ROUND = {1: 1, 2: 2, 3: 3, 4: 4}  # quality tier the skill reveals THIS round (R5 无技能)
AISHA_TOOL_BY_ROUND = {1: "baoguang4", 2: "inspect2", 3: "inspect1", 4: "goldscan", 5: "none"}
TOOL_LABEL = {
    "baoguang4": "宝光四鉴", "baoguang2": "宝光2鉴", "inspect2": "抽检2", "inspect1": "抽检1",
    "zhibao": "至宝寻踪", "juwu": "巨物抽样", "appraise": "至宝估价", "goldscan": "金色扫描", "none": "未用",
}
# Short source glyph for the matrix cell (来源一眼可辨, 上标=抽取件数以区分同名道具):
# 宝光四鉴→宝⁴  宝光2鉴→宝²  抽检2→检²  抽检1→检¹  至宝寻踪→寻  巨物→巨  至宝估价→估
TOOL_SRC = {
    "baoguang4": "宝⁴", "baoguang2": "宝²", "inspect2": "检²", "inspect1": "检¹",
    "zhibao": "寻", "juwu": "巨", "appraise": "估", "goldscan": "金扫",
}
# Real game action ids per tool (mechanics doc; used in synthesized action_result_rows so the engine's
# coarse/shape/value reveal floors + min_counts populate the SAME as live).
TOOL_ACTION_ID = {
    "baoguang4": 100136, "baoguang2": 100135, "inspect2": 100129, "inspect1": 100128,
    "zhibao": 100161, "juwu": 100168, "appraise": 100163,
}
SKILL_REVEAL_ID = {"sophie": 100107, "gabriela": 1001041}  # representative skill id per hero
# Ethan = grid-primary (数格子). Engine consumes the FULL-outline skill (all footprints -> total count
# + total grid via _apply_ethan_skill_evidence), then estimates the per-quality split. Constants mirror
# the engine (ETHAN_HERO_ID / ETHAN_SKILL_FULL_OUTLINE / ETHAN_SKILL_R1_OUTLINE).
ETHAN_HERO_ID = 208
ETHAN_SKILL_FULL_OUTLINE = 1002085  # R5 full outline -> engine sets total_count + total_grid_target
ETHAN_SKILL_R1_OUTLINE = 1002081    # R1 partial outline (note only; no totals)
# Tool inventory + choice rules (author-confirmed 2026-06-22):
# - Each game carries the FOUR cheap tools (宝光4/2, 抽检2/1), one each, plus ONE rare tool
#   (至宝寻踪 OR 巨物抽样). Every tool is SINGLE-USE per game (used once, then gone) -- the same
#   tool never repeats across rounds.
# - Draw heroes ALWAYS use a tool in R1-R3 (they do not skip early). R4+ may skip.
# - The rare tool is SAVED for the late game (only eligible R3+), and is low-frequency.
# Per-round tool WINDOWS (author-confirmed 2026-06-22). Every tool single-use per game.
#   宝光四鉴 / 抽检2  -> R1-R2 (前两轮优先)
#   宝光2鉴 / 抽检1   -> R3-R4
#   巨物抽样 / 至宝寻踪 / 至宝估价 (rare) -> R4(低概率) / R5; AT MOST ONE rare per game.
#   R4: mostly a MID tool, low prob one rare. R5: if a rare was used in R4 -> remaining MID; else a rare.
EARLY_TOOLS = ("baoguang4", "inspect2")
MID_TOOLS = ("baoguang2", "inspect1")
RARE_TOOLS = ("juwu", "zhibao", "appraise")
RARE_R4_PROB = 0.20  # R4 chance of pulling a rare instead of a mid tool
TOOL_PICK_WEIGHT = {  # relative weight among still-available tools in a pool
    "baoguang4": 0.58, "inspect2": 0.42,
    "baoguang2": 0.52, "inspect1": 0.48,
    "juwu": 0.38, "zhibao": 0.34, "appraise": 0.28,
}
SKILL_BASE_T1 = {"sophie": 5}  # sophie R1 base skill draws (tier1, dup-OK)
SKILL_PER_ROUND = {  # (draws, tier) added each round, no-dup
    "sophie": (2, T1),
    "gabriela": (2, T2),
}


@dataclass
class Warehouse:
    source: str
    map_id: int | None
    items: list[dict[str, Any]]
    public_reveals: list[dict[str, Any]]  # items exposed by the source's public info
    public_info_rows: list[dict[str, Any]]
    truth_counts: dict[int, int] = field(default_factory=dict)
    truth_total_value: int = 0
    raw_count: int = 0  # ALL settled items incl q<=0 (称号/头像) — public 总件数 may count these
    raw_cells: int = 0  # ALL settled cells incl q<=0
    unrecoverable_items: int = 0  # settled items with missing wire quality AND unknown item_id (bad源)
    source_rounds: int = 0  # rounds the SOURCE game reached (max state.round_index) — for source filtering
    source_hero: str | None = None  # hero who played the SOURCE game (skip aisha sources — already real)

    @property
    def total_count(self) -> int:
        return len(self.items)


def _settled_geometry(events: Any, map_id: int | None) -> dict[Any, tuple[int, int, int, int]]:
    """runtime_id -> (row, col, width, height) from the SETTLED minimap (real packed layout).

    inventory_items carry only local_index+cells (no shape), so the footprint can't be drawn from
    them. The settled snapshot's minimap_grid_items DO carry row/col/width/height for every item."""
    allb = live_batches_from_fatbeans_events(events)
    if not allb:
        return {}
    snap = _snapshot_at_batch(events=events, pre_batches=allb, batch_no=len(allb), map_id=map_id)
    geo: dict[Any, tuple[int, int, int, int]] = {}
    for it in snap.get("minimap_grid_items") or []:
        r, c, w, h = it.get("row"), it.get("col"), it.get("width"), it.get("height")
        if r and c and w and h:
            geo[it.get("runtime_id")] = (int(r), int(c), int(w), int(h))
    return geo


def _public_round(src_round: int, map_id: int | None) -> int:
    """Canonical round a public reveal is SHOWN, by map theme (作者 2026-07-11 口径 + 40局/族 census):
    沉船(theme5)/排位56xx+hidden26xx(theme6): 单一公开轮 = R1(census: 沉船40/40·56xx40/40·hidden39/40
    首现全在 wire round 1; 旧 2026-06-22 "沉船仅R3" 规则被实测推翻)。别墅(theme4): R1(首发)+R3(刷新),
    无 R2 —— 原始 wire 戳是 [1,2], 用艾莎逐档揭示锚定证明 R2+ 的揭示帧在上一轮末尾到达(戳滞后玩家可见
    轮一轮), 故 wire 2 = 玩家可见 R3; src_round 只用于分首发 vs 其后(按相对序, 不信戳的绝对值)。
    未标定旧图族(21xx-23xx)保守沿用别墅双波规则。census 工装: scripts/_tmp_public_round_census.py。"""
    theme = ((map_id or 0) // 100) % 10
    if theme in (5, 6):  # 沉船 / 排位56xx / hidden26xx: 单一公开轮 R1
        return 1
    return 1 if src_round <= 1 else 3  # 别墅(及未标定旧族): 首波→R1, 其后并入 R3 (无 R2)


_ITEMS_META: dict[int, tuple[int, int, int]] | None = None  # item_id -> (quality, cells, value)


def _items_meta() -> dict[int, tuple[int, int, int]]:
    """items.json catalog: item_id -> (quality, cells=shape_w*shape_h, value). Used to RECOVER a
    settled item whose wire quality field was dropped (parsed as q<=0) — the item IS a real collectible
    (e.g. 输液泵 q4), the capture just lost its quality/shape, so look up the authoritative catalog."""
    global _ITEMS_META
    if _ITEMS_META is None:
        data = json.loads((ROOT / "data/processed/items.json").read_text(encoding="utf-8"))
        _ITEMS_META = {
            int(e["item_id"]): (int(e.get("quality") or 0),
                                max(1, int(e.get("shape_w") or 1) * int(e.get("shape_h") or 1)),
                                int(e.get("value") or 0))
            for e in data
        }
    return _ITEMS_META


def load_warehouse(sample_path: Path, *, with_geometry: bool = False) -> Warehouse | None:
    try:
        events = parse_fatbeans_capture(sample_path)
    except Exception:
        return None
    values_by_id = _item_values()
    meta_by_id = _items_meta()
    settled = next((s for s in reversed(events.states) if s.inventory_items), None)
    if settled is None:
        return None
    map_id = _map_id_from_events(events)
    # Real packed footprint geometry (only built when a caller needs the truth matrix).
    geo = _settled_geometry(events, map_id) if with_geometry else {}
    items: list[dict[str, Any]] = []
    unrecoverable = 0  # settled items whose wire quality is missing AND not in items.json -> can't fix
    by_runtime: dict[Any, int] = {}
    truth_counts: dict[int, int] = {}
    total_value = 0
    raw_count = len(settled.inventory_items)  # incl q<=0 (称号/头像): public 总件数/总格 may count these
    raw_cells = sum(int(it.cells or 0) for it in settled.inventory_items)
    for it in settled.inventory_items:
        q = int(it.quality or 0)
        item_id = int(it.item_id or 0)
        cells = int(it.cells or 1)
        value = int(values_by_id.get(item_id, 0))
        if q <= 0:
            # Wire dropped this item's quality (capture artifact) — it IS a real collectible. Recover
            # quality + cells + value from items.json by item_id; the wire cells are also unreliable here
            # (e.g. 输液泵: wire cells=1 vs real 2x2=4), so prefer the catalog shape.
            cat = meta_by_id.get(item_id)
            if cat and cat[0] > 0:
                q, cells, value = cat
            else:  # unknown item_id (not in items.json) -> can't recover -> mark sample unrecoverable
                unrecoverable += 1
                continue
        rc = geo.get(it.runtime_id)
        rec = {
            "local_index": it.local_index,
            "quality": q,
            "cells": cells,
            "value": value,
            "item_id": it.item_id,
            "runtime_id": it.runtime_id,
            "row": rc[0] if rc else None,
            "col": rc[1] if rc else None,
            "width": rc[2] if rc else None,
            "height": rc[3] if rc else None,
        }
        by_runtime[it.runtime_id] = len(items)
        items.append(rec)
        truth_counts[q] = truth_counts.get(q, 0) + 1
        total_value += value
    if not items:
        return None
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    public_rows: list[dict[str, Any]] = []
    reveal_gran: dict[int, str] = {}  # item idx -> best public reveal granularity
    reveal_src: dict[int, int] = {}  # item idx -> info_id that gave that granularity
    _rank = {T1: 1, T2: 2, T3: 3}
    # Public info appears PROGRESSIVELY across game rounds. The REAL 1-based game round is
    # state.round_index (NOT the batch index — _snapshot_at_batch sets context.round=batch_no which is
    # just a frame counter). So derive first-appearance round per public reveal from the raw states.
    reveal_round: dict[int, int] = {}  # item idx -> first game round its public reveal appeared
    info_round: dict[int, int] = {}  # info_id -> first game round it appeared
    for st in events.states:
        rnd = st.round_index
        if rnd is None:
            continue
        for pi in st.public_infos:
            info_id = int(pi.info_id or 0)
            if info_id not in WAREHOUSE_PUBLIC_IDS:
                continue
            if info_id not in info_round or rnd < info_round[info_id]:
                info_round[info_id] = rnd
            if PUBLIC_REVEAL_GRAN.get(info_id) is not None:
                for oi in pi.observed_items:
                    idx = by_runtime.get(oi.runtime_id)
                    if idx is not None and (idx not in reveal_round or rnd < reveal_round[idx]):
                        reveal_round[idx] = rnd
    if pre:
        snap = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id)
        for row in snap.get("public_info_rows") or []:
            info_id = int(row.get("info_id") or 0)
            if info_id not in WAREHOUSE_PUBLIC_IDS:
                continue
            gran = PUBLIC_REVEAL_GRAN.get(info_id)
            n_rev = 0
            if gran is not None:
                for det in row.get("revealed_items_detail") or []:
                    idx = by_runtime.get(det.get("runtime_id"))
                    if idx is None:
                        continue
                    n_rev += 1
                    if idx not in reveal_gran or _rank[gran] > _rank[reveal_gran[idx]]:
                        reveal_gran[idx] = gran
                        reveal_src[idx] = info_id
            public_rows.append({
                "info_id": info_id,
                "label": PUBLIC_INFO_LABEL.get(info_id, f"公开·{info_id}"),
                "value": row.get("value"),
                "revealed": n_rev,
                "gran": gran,
                "round": info_round.get(info_id, 1),
            })
    # 公开信息出现轮次按地图类型固定(作者 2026-06-22): 别墅=R1(首发)+R3(刷新,无R2); 沉船=仅R3。
    # 源仓 state 轮次只用来判别墅的"首发 vs 其后"(首轮→R1, 其后并入R3)。
    for r in public_rows:
        r["round"] = _public_round(info_round.get(r["info_id"], 1), map_id)
    public_reveals = [
        {"idx": i, "gran": reveal_gran[i], "info_id": reveal_src[i],
         "round": _public_round(reveal_round.get(i, 1), map_id)}
        for i in reveal_gran
    ]
    return Warehouse(
        source=sample_path.name,
        map_id=map_id,
        items=items,
        public_reveals=public_reveals,
        public_info_rows=public_rows,
        truth_counts=truth_counts,
        truth_total_value=total_value,
        raw_count=raw_count,
        raw_cells=raw_cells,
        unrecoverable_items=unrecoverable,
        # round_index is 0-based (a 3-round game maxes at 2); +1 -> the round COUNT (matches filename
        # _Nrounds_). default -1 -> 0 for captures with no round_index (1-round/no-state).
        source_rounds=max((int(s.round_index) for s in events.states if s.round_index is not None),
                          default=-1) + 1,
        source_hero=next((h for s in events.states if (h := _hero_mode_from_state(s))), None),
    )


def _weighted_pick(pool: tuple[str, ...], used: set[str], rng: random.Random) -> str | None:
    avail = [t for t in pool if t not in used]
    if not avail:
        return None
    weighted = [(t, TOOL_PICK_WEIGHT.get(t, 1.0)) for t in avail]
    total = sum(w for _, w in weighted)
    r, cum = rng.random() * total, 0.0
    for t, w in weighted:
        cum += w
        if r <= cum:
            return t
    return avail[-1]


def _pick_tool(round_no: int, used: set[str], rng: random.Random) -> str:
    """One UNUSED tool per round, single-use, by round window (see EARLY/MID/RARE above).
    R1-R3 always use a tool; rare tools only R4(low)/R5, at most one per game."""
    if round_no <= 2:
        return _weighted_pick(EARLY_TOOLS, used, rng) or "none"
    if round_no == 3:
        return _weighted_pick(MID_TOOLS, used, rng) or "none"
    rare_used = any(t in used for t in RARE_TOOLS)
    if round_no == 4:
        if not rare_used and rng.random() < RARE_R4_PROB:
            t = _weighted_pick(RARE_TOOLS, used, rng)
            if t:
                return t
        return _weighted_pick(MID_TOOLS, used, rng) or _weighted_pick(RARE_TOOLS, used, rng) or "none"
    if round_no == 5:
        if rare_used:  # a rare already spent in R4 -> R5 can only use remaining MID
            return _weighted_pick(MID_TOOLS, used, rng) or "none"
        return _weighted_pick(RARE_TOOLS, used, rng) or _weighted_pick(MID_TOOLS, used, rng) or "none"
    return "none"


def _draw(warehouse: Warehouse, k: int, dup_ok: bool, target: str, taken: set[int], rng: random.Random) -> list[int]:
    n = len(warehouse.items)
    if target == "max_quality":
        best = max(warehouse.items, key=lambda it: (it["quality"], it["value"]))
        return [warehouse.items.index(best)]
    if target == "max_cells":
        best = max(warehouse.items, key=lambda it: (it["cells"], it["value"]))
        return [warehouse.items.index(best)]
    if target == "rand_top_quality":  # 至宝估价: 随机一件, 优先红(q6)→金(q5)→紫(q4); 都没则取最高可得品质
        for q in (6, 5, 4):
            pool = [i for i, it in enumerate(warehouse.items) if it["quality"] == q]
            if pool:
                return [rng.choice(pool)]
        maxq = max(it["quality"] for it in warehouse.items)
        pool = [i for i, it in enumerate(warehouse.items) if it["quality"] == maxq]
        return [rng.choice(pool)] if pool else []
    if dup_ok:
        # 单次使用内部不重复(自己不抽自己), 但可与"先前已揭示"的件重叠(跨来源有放回)。
        return rng.sample(range(n), min(max(0, k), n))
    pool = [i for i in range(n) if i not in taken]  # 不重复来源(技能): 排除已揭示
    rng.shuffle(pool)
    return pool[: max(0, k)]


def reveal_trace(
    warehouse: Warehouse, hero: str, round_no: int, rng: random.Random
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Per-round, per-SOURCE reveal log + per-item meta. revealed[i] keeps the HIGHEST-granularity
    reveal for item i: {"gran": tier, "round": r, "stype": skill|tool_q|tool_v|public}."""
    rank = {T1: 1, T2: 2, T3: 3}
    revealed: dict[int, dict[str, Any]] = {}
    log: list[dict[str, Any]] = []

    def add(idxs: list[int], gran: str, rnd: int, stype: str, src: str, info_id: int | None = None) -> None:  # noqa: E306
        """Keep the HIGHEST-granularity reveal as the 'best' display, but accumulate EVERY reveal
        (round, src) so overlapping reveals on the same item can be shown."""
        for i in idxs:
            cur = revealed.get(i)
            if cur is None:
                revealed[i] = {"gran": gran, "round": rnd, "stype": stype, "src": src,
                               "info_id": info_id, "hits": [(rnd, src, gran)]}
            else:
                cur["hits"].append((rnd, src, gran))
                if rank[gran] > rank[cur["gran"]]:
                    cur.update(gran=gran, round=rnd, stype=stype, src=src, info_id=info_id)

    def dup_flags(d: list[int]) -> list[bool]:
        """Per drawn item: True if it was ALREADY revealed before this draw (跨轮/跨来源重复) OR
        repeats earlier in the same draw list (同轮重复, 有放回道具才可能)."""
        seen, local, flags = set(revealed), set(), []
        for i in d:
            flags.append(i in seen or i in local)
            local.add(i)
        return flags

    pub = warehouse.public_reveals  # [{idx, gran, info_id, round}]; revealed progressively per round

    if hero == "aisha":
        # AISHA (structured): skill reveals ALL items of quality == tier[r] (white/green/blue/purple,
        # q1-q4) as outline+quality (T2; count/pos known, cells inferred); NO gold/red via skill -> both
        # phantom. Gold only from the R4 金色扫描 tool (all q5, full info). Red (q6) NEVER. Apart from the
        # revealed items there is NO whole-vault info (total count/cells unknown). Tools: R1 宝光四鉴 /
        # R2 抽检2 / R3 抽检1 / R4 金色扫描 / R5 none. (author 2026-06-27, doc red line: confirm via matrix.)
        for r in range(1, int(round_no) + 1):
            events = []
            rpub = [p for p in pub if p.get("round", 1) == r]
            if rpub:
                pflags = dup_flags([p["idx"] for p in rpub])
                for p in rpub:
                    add([p["idx"]], p["gran"], r, "public", "公", p.get("info_id"))
                events.append({"kind": "public", "items": [p["idx"] for p in rpub], "dups": pflags})
            tq = AISHA_SKILL_TIER_BY_ROUND.get(r)
            if tq:
                idxs = [i for i, it in enumerate(warehouse.items) if int(it.get("quality", 0) or 0) == tq]
                if idxs:
                    flags = dup_flags(idxs)
                    # T2 = 品质 + 轮廓 + 位置 + 件数 (aisha 看得到轮廓). The COMPLETE tier -> per-quality COUNT
                    # is EXACT, so it is also LOCKED (fixed_counts, see synthesize_snapshot) -- outline alone
                    # only floors the count, which the prior overshoots (q1 16 vs 真值 11).
                    add(idxs, T2, r, "skill", "技")
                    events.append({"kind": "skill", "label": f"技能·q{tq}", "gran": T2, "items": idxs, "dups": flags})
            tool = AISHA_TOOL_BY_ROUND.get(r, "none")
            if tool == "goldscan":
                # 极品扫描(金色扫描): ONLY the TOTAL q5 cell count (aggregate). NO gold count / position /
                # identity -> gold items stay HIDDEN (like red). Fed to the engine as quality_cells.q5.
                gold_idxs = [i for i, it in enumerate(warehouse.items) if int(it.get("quality", 0) or 0) == 5]
                gcells = sum(int(warehouse.items[i].get("cells", 0) or 0) for i in gold_idxs)
                events.append({"kind": "tool", "tool": "goldscan", "gran": "agg_q5_cells", "items": [],
                               "q5_cells_total": gcells, "q5_n": len(gold_idxs)})
            elif tool != "none":
                draws, tier, dup_ok, target = TOOLS[tool]
                d = _draw(warehouse, draws, dup_ok, target, set(revealed), rng)
                flags = dup_flags(d)
                add(d, tier, r, "tool_v" if tier == T3 else "tool_q", TOOL_SRC.get(tool, "具"))
                events.append({"kind": "tool", "tool": tool, "gran": tier, "items": d, "target": target, "dups": flags})
            else:
                events.append({"kind": "tool", "tool": "none", "gran": None, "items": []})
            log.append({"round": r, "events": events})
        return revealed, log

    base = SKILL_BASE_T1.get(hero, 0)
    per_round, per_tier = SKILL_PER_ROUND[hero]
    used_tools: set[str] = set()  # single-use per game
    for r in range(1, int(round_no) + 1):
        events: list[dict[str, Any]] = []
        # 1) 公开信息最先: 仅本轮(源仓该轮)新出现的公开揭示
        rpub = [p for p in pub if p.get("round", 1) == r]
        if rpub:
            pflags = dup_flags([p["idx"] for p in rpub])
            for p in rpub:
                add([p["idx"]], p["gran"], r, "public", "公", p.get("info_id"))
            events.append({"kind": "public", "items": [p["idx"] for p in rpub], "dups": pflags})
        if r == 1 and base:
            # 索菲 R1: 技能就抽 base(=5) 件(只品质, dup-OK), 本轮不再 +2。加布里无 base, 走 else 每轮 2 件。
            d = _draw(warehouse, base, True, "random", set(revealed), rng)
            flags = dup_flags(d)
            add(d, T1, r, "skill", "技")
            events.append({"kind": "skill", "label": "技能·R1基础", "gran": T1, "items": d, "dups": flags})
        else:
            d = _draw(warehouse, per_round, False, "random", set(revealed), rng)  # per-round skill, no-dup
            flags = dup_flags(d)
            add(d, per_tier, r, "skill", "技")
            events.append({"kind": "skill", "label": "技能·本轮", "gran": per_tier, "items": d, "dups": flags})
        tool = _pick_tool(r, used_tools, rng)
        draws, tier, dup_ok, target = TOOLS[tool]
        if tool != "none" and (draws or target != "random"):
            used_tools.add(tool)
            d = _draw(warehouse, draws, dup_ok, target, set(revealed), rng)
            flags = dup_flags(d)
            add(d, tier, r, "tool_v" if tier == T3 else "tool_q", TOOL_SRC.get(tool, "具"))
            events.append({"kind": "tool", "tool": tool, "gran": tier, "items": d, "target": target, "dups": flags})
        else:
            events.append({"kind": "tool", "tool": "none", "gran": None, "items": []})
        log.append({"round": r, "events": events})
    return revealed, log


def reveal_state(warehouse: Warehouse, hero: str, round_no: int, rng: random.Random) -> dict[int, str]:
    """item index -> best reveal granularity the hero has by round_no (skill + tools + public)."""
    return {i: m["gran"] for i, m in reveal_trace(warehouse, hero, round_no, rng)[0].items()}


def _shape_code(it: dict[str, Any]) -> int | None:
    """Live shape_code encoding (engine _shape_cells: width=code//10, height=code%10). Single-digit dims."""
    w, h = it.get("width"), it.get("height")
    if w and h and 1 <= int(w) <= 9 and 1 <= int(h) <= 9:
        return int(w) * 10 + int(h)
    return None


def _reveal_detail(wh: Warehouse, i: int, gran: str, *, with_quality: bool, with_value: bool) -> dict[str, Any]:
    """One revealed_items_detail entry mirroring live. Coarse(T1)=quality only, NO shape → increments the
    engine's coarse min_counts. T2/T3 carry shape_code → cell floor (NOT coarse, like live gabriela skill).
    T3 carries value → value floor. zhibao(至宝寻踪, T2 无品质) omits quality."""
    it = wh.items[i]
    d: dict[str, Any] = {"local_index": it["local_index"], "runtime_id": it["runtime_id"]}
    if with_quality:
        d["quality"] = it["quality"]
    if gran in (T2, T3):
        sc = _shape_code(it)
        if sc is not None:
            d["shape_code"] = sc
    if with_value:
        d["value"] = it["value"]
    return d


def _reveal_source_rows(
    wh: Warehouse, hero: str, log: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build skill_reveal_rows + action_result_rows from the per-source reveal log so the engine's reveal
    floors + min_counts populate the SAME way as live. The synthetic minimap alone does NOT feed
    _iter_quality_reveal_item_rows (it reads public/action/skill rows), so without these the engine sees
    0 revealed quality (min_counts empty) and e.g. the draw-hero no-red damp never fires."""
    skill_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    skill_id = SKILL_REVEAL_ID.get(hero, 100107)
    for entry in log:
        for ev in entry.get("events") or []:
            items = ev.get("items") or []
            if not items:
                continue
            gran = ev.get("gran")
            if ev.get("kind") == "skill":
                detail = [_reveal_detail(wh, i, gran, with_quality=True, with_value=False) for i in items]
                skill_rows.append({"skill_id": skill_id, "observed_items": detail, "revealed_items_detail": detail})
            elif ev.get("kind") == "tool":
                tool = ev.get("tool")
                if not tool or tool == "none":
                    continue
                with_quality = tool != "zhibao"  # 至宝寻踪: 位置+轮廓, 无品质
                detail = [_reveal_detail(wh, i, gran, with_quality=with_quality, with_value=(gran == T3)) for i in items]
                action_rows.append({"action_id": TOOL_ACTION_ID.get(tool, 0), "tool": tool, "revealed_items_detail": detail})
    return skill_rows, action_rows


def _synthesize_ethan_snapshot(warehouse: Warehouse, round_no: int, rng: random.Random,
                               revealed_idxs: set[int] | None = None,
                               feed_appraisal_value: bool = True) -> dict[str, Any]:
    """Ethan grid-primary (数格子) synth. Two modes:
    - revealed_idxs=None (legacy): FULL outline (skill 1002085) reveals EVERY item's footprint -> R5 state.
    - revealed_idxs given (synth-B, author 2026-07-03): PARTIAL reveal — only the passed subset's
      footprints are outlined (skill 1002080+round_no), mirroring a real R{round_no} decision snapshot
      (R1-R4 partial, R5 full). The caller (emit_ethan_synth) builds the subset = coverage-calibrated
      skill sample ∪ all public-revealed items (public is prior->skill-outlined by the decision round;
      see memory ethan-synthesis-b-reveal-mechanism). Footprint quality=NULL (伊森 0 品质); public
      qualities ride in via public_info_rows unchanged. Engine: 1002085 sets total; 1002081-84 note-only
      (R1-R4 fall to prior — the case the occupancy estimator will fix)."""
    items = warehouse.items
    idxs = sorted(revealed_idxs) if revealed_idxs is not None else list(range(len(items)))
    skill_id = ETHAN_SKILL_FULL_OUTLINE if int(round_no) >= 5 else (1002080 + int(round_no))
    outline, mm = [], []
    for i in idxs:
        it = items[i]
        cells = int(it.get("cells") or 0)
        sc = _shape_code(it)
        # full outline = FOOTPRINT known, quality NULL (gold/red hidden; low/mid quality not given here)
        detail = {"runtime_id": it["runtime_id"], "local_index": it["local_index"],
                  "cells": cells, "quality": None}
        if sc is not None:
            detail["shape_code"] = sc
        outline.append(detail)
        mm.append({"quality": None, "category": None, "category_label": "", "item_id": None,
                   "item_name": "", "runtime_id": it["runtime_id"], "local_index": it["local_index"],
                   "cells": cells, "shape_key": str(sc) if sc is not None else None,
                   "row": it.get("row"), "col": it.get("col"), "width": it.get("width"),
                   "height": it.get("height"), "render_mode": "footprint", "source": "synthetic_ethan"})
    skill_rows = [{"skill_id": skill_id, "hero_id": ETHAN_HERO_ID,
                   "observed_items": outline, "revealed_items_detail": outline}]
    # Public AVG cells/values bridge (author 2026-06-27: avg IS meaningful info the engine can now use,
    # WIRE it). ahmad_ref_engine consumes bucket.q.avg_cells / avg_value field_updates (verified: produces
    # field_update_q{4,5,6}_avg_cells/value notes -> constrains the gold/red residual split into count,
    # the lever on ethan's value over-estimate). For ethan the FULL outline gives the total grid but NOT
    # the per-quality split, so 紫/金/红 avg格/avg价 are the only signal that pins the high-tier residual.
    # NOTE (live-monitor follow-up): the live v2/v3 path does NOT yet bridge avg for ethan (checked: 21
    # real ethan carry 0 field_updates, incl 2 with public avg) — that is a MONITOR gap to fix (the engine
    # already supports it), after which real ethan matches this synth. Wiring it here so the synth exploits
    # the engine capability for ethan tuning now. q4(200013)/q5(200015)/q6(200016) avg_cells +
    # q4(200036)/q5(200037)/q6(200038) avg_value + 200014 total_avg_cells.
    ETHAN_AVG_CELLS = {200013: 4, 200015: 5, 200016: 6}
    ETHAN_AVG_VALUE = {200036: 4, 200037: 5, 200038: 6}
    fu: list[dict[str, Any]] = []
    for r in warehouse.public_info_rows:
        iid = int(r.get("info_id") or 0)
        val = r.get("value")
        if val is None or float(val) <= 0:
            continue
        if iid in ETHAN_AVG_CELLS:
            fu.append({"path": ["bucket", ETHAN_AVG_CELLS[iid], "avg_cells"], "value": float(val)})
        if iid in ETHAN_AVG_VALUE:
            fu.append({"path": ["bucket", ETHAN_AVG_VALUE[iid], "avg_value"], "value": float(val)})
    # 估价 tools (author 2026-06-27, the CANONICAL ethan play): 优品估价 -> 紫(q4) value sum, 极品估价 ->
    # 金(q5) value sum. The tool reveals the TOTAL value of all purple / all gold items; the engine turns
    # that (via expectation×likelihood) into the q4/q5 cell-count estimate. RED (q6) is deliberately NOT
    # appraised — its 珍品估价 tool is expensive; the engine's value-add is reverse-inferring red cells/value
    # cheaply, so leave red to the residual. Fed as quality_values via bucket.q.value field_updates.
    q_value_sum: dict[int, float] = {}
    for it in warehouse.items:
        q_value_sum[it["quality"]] = q_value_sum.get(it["quality"], 0.0) + float(it.get("value") or 0)
    # Round-gated by the recommended cadence (author 2026-07-03): 优品估价(q4) 用于 R3+, 极品估价(q5) R4+.
    # Legacy full-outline path (round_no>=5) feeds both. red(q6) never appraised (engine reverse-infers).
    # feed_appraisal_value=False (grid-tuning batch): the q4/q5 value constraint can make the engine's
    # combo search INFEASIBLE (all-None result, ~10% of vaults) and constrains the quality SPLIT not the
    # GRID — so the occupancy/total-grid synth omits it; re-enable for value tuning after the engine gets
    # value-constraint relax-and-retry (see no-reachable-high-tier-grid-target-fallback).
    if feed_appraisal_value:
        for q, min_round in ((4, 3), (5, 4)):
            if q in q_value_sum and int(round_no) >= min_round:
                fu.append({"path": ["bucket", q, "value"], "value": float(q_value_sum[q])})
    # 低中品扫描 (普品/良品扫描 -> q1/q3 total CELLS) NOT fed: it pins low/mid grid but the cell-residual
    # red value depends on a CLEAN red grid residual, which the count-from-cells ambiguity corrupts (q1
    # count collapses, phantom red inflates the red grid). Needs the ethan cell-residual path to compute
    # the residual EXPLICITLY (not via the combo grid) + red per-cell calibration — see
    # docs/HANDOFF_ethan_cell_centric_plan_2026-06-27. Until then, low/mid stays on the full-outline total
    # + prior split (decision-round residual); value is count-based (median ~+8%, better than the WIP A).
    constraints: dict[str, Any] = {}
    tac = next((r.get("value") for r in warehouse.public_info_rows
                if int(r.get("info_id") or 0) == PUBLIC_TOTAL_AVG_CELLS_INFO and r.get("value") is not None), None)
    if tac is not None and float(tac) > 0:
        constraints["public_info"] = {"public_numeric_facts": [{"semantic": "total_avg_cells", "value": float(tac)}]}
    return {
        "ui_contract": {
            "context": {"hero": "ethan", "map_id": warehouse.map_id, "phase": "bidding", "round": int(round_no)},
            "constraints": constraints,
        },
        "map_id": warehouse.map_id,
        "minimap_grid_items": mm,
        "public_info_rows": warehouse.public_info_rows,
        "skill_reveal_rows": skill_rows,
        "action_result_rows": [],
        "structured_ref_inputs": {"field_updates": fu} if fu else {},
        "_synthetic": {"source": warehouse.source, "revealed_unique": len(idxs), "round": round_no, "hero": "ethan"},
    }


def synthesize_snapshot(warehouse: Warehouse, hero: str, round_no: int, rng: random.Random) -> dict[str, Any]:
    if hero == "ethan":
        return _synthesize_ethan_snapshot(warehouse, round_no, rng)
    revealed_meta, log = reveal_trace(warehouse, hero, round_no, rng)
    revealed = {i: m["gran"] for i, m in revealed_meta.items()}
    skill_rows, action_rows = _reveal_source_rows(warehouse, hero, log)  # feed engine reveal floors/min_counts
    minimap_items = []
    for i, tier in sorted(revealed.items()):
        it = warehouse.items[i]
        # Live parity (monitor._minimap_rows_from_batch): a reveal whose OUTLINE is known (T2 品质+轮廓
        # / T3 全信息) carries its real footprint row/col/width/height, so the engine reads the true
        # bottom row (row+height-1) — not just the local_index anchor. T1 (品质 only, anchor-only) has no
        # shape in live → row/col/width/height stay None and depth falls back to the local_index anchor.
        # Geometry is present only when the warehouse was loaded with_geometry=True (trace path).
        shape_known = tier in (T2, T3)
        geo = shape_known and all(it.get(k) for k in ("row", "col", "width", "height"))
        row = {
            "quality": it["quality"],
            "local_index": it["local_index"],
            "runtime_id": it["runtime_id"],
            "render_mode": "footprint",
            "source": "synthetic_draw_hero",
            "cells": it["cells"] if shape_known else None,
            "value": it["value"] if tier == T3 else None,
            "item_id": it["item_id"] if tier == T3 else None,
            "row": it["row"] if geo else None,
            "col": it["col"] if geo else None,
            "width": it["width"] if geo else None,
            "height": it["height"] if geo else None,
        }
        minimap_items.append(row)
    sri: dict[str, Any] = {}
    fu: list[dict[str, Any]] = []
    if hero == "aisha":
        # Aisha skill reveals the COMPLETE non-red, non-gold tier each round -> the per-quality COUNT is
        # EXACT (件数 known), so LOCK it (fixed_counts) instead of leaving a floor the prior over-shoots.
        # Engine q1 bucket = white+green COLLAPSED, but it has a dedicated white/green SPLIT channel
        # (bucket_split). Aisha reveals white (q1) at R1 and green (q2) at R2 — separately — so feed them
        # through the split channel, NOT the merged bucket1 lock:
        #   R1: split white = exact (green still hidden) -> engine floors q1 >= white and estimates green,
        #       so R1 STILL produces a bid (lower precision is fine, but it must compute). White/green are
        #       kept separate, as the player experiences them.
        #   R2: + split green = exact -> both split counts present -> engine MERGES and locks q1 = white+green
        #       exactly (no prior overshoot). Locking the merged bucket1 to white-only at R1 was a hard-
        #       conflict bug (a green tool/public reveal floors q1>=1 vs the 0/under-count lock -> 0/0/0).
        # q3/q4 are single-quality buckets -> lock directly when revealed. 金/红 never skill-revealed.
        tc = warehouse.truth_counts
        if int(round_no) >= 1:  # white (q1) fully revealed at R1 -> exact white via the split channel
            fu.append({"path": ["bucket_split", "white", "count"], "value": int(tc.get(1, 0))})
        if int(round_no) >= 2:
            # green (q2) revealed at R2 -> the merged q1 bucket count is now exact. Lock it via the direct
            # bucket path (fixed_counts.q1) rather than a split-green field_update: a coarse tool reveal
            # (宝光四鉴 = quality-only) tags the split channel "coarse" and downgrades split counts to FLOORS,
            # so a split-green lock would NOT pin q1. With the exact merged q1 + split-white, the engine
            # DERIVES green = q1_exact - white (note split_low_quality_green_count_from_q1_exact), keeping the
            # white/green split exact AND pinning q1 — no prior overshoot.
            fu.append({"path": ["bucket", 1, "count"], "value": int(tc.get(1, 0)) + int(tc.get(2, 0))})
        if int(round_no) >= 3:
            fu.append({"path": ["bucket", 3, "count"], "value": int(tc.get(3, 0))})
        if int(round_no) >= 4:
            fu.append({"path": ["bucket", 4, "count"], "value": int(tc.get(4, 0))})
        # Aisha reveals the FULL tier WITH outlines -> the tier's TOTAL cells are EXACT too, not just
        # floored. Feeding only cell FLOORS (skill_rows shape) leaves the engine grid room for phantom
        # gold/red (diagnosed: real snapshot had exact quality_cells q1/q3/q4, synth had only q5 from the
        # scan -> synth invented 7 phantom red on a 0-red 56xx vault). Set exact quality_cells per locked
        # tier (same rounds as the count lock). q1 = white+green merged (R2+ when both revealed).
        cells_by_q: dict[int, int] = {}
        for it in warehouse.items:
            cells_by_q[it["quality"]] = cells_by_q.get(it["quality"], 0) + int(it.get("cells", 0) or 0)
        if int(round_no) >= 2:
            fu.append({"path": ["bucket", 1, "cells"], "value": cells_by_q.get(1, 0) + cells_by_q.get(2, 0)})
        if int(round_no) >= 3:
            fu.append({"path": ["bucket", 3, "cells"], "value": cells_by_q.get(3, 0)})
        if int(round_no) >= 4:
            fu.append({"path": ["bucket", 4, "cells"], "value": cells_by_q.get(4, 0)})
    # 极品扫描(金色扫描) -> q5 TOTAL cells only (no count/pos). Inject as quality_cells.q5.
    for entry in log:
        for ev in entry.get("events") or []:
            if ev.get("tool") == "goldscan" and int(ev.get("q5_cells_total", 0) or 0) > 0:
                fu.append({"path": ["bucket", 5, "cells"], "value": int(ev["q5_cells_total"])})
    # Public AVG cells / AVG values: ahmad_ref_engine consumes these via bucket.q.avg_cells /
    # bucket.q.avg_value field_updates — the LIVE monitor bridges the public info_ids (200013-16 /
    # 200035-38) to those paths (v2/v3 registry); the synth must MIRROR it or this public info is
    # silently dropped (the engine does NOT map these info_ids from public_info_rows directly). Feed:
    #   · 金/红 avg_cells (200015/200016): with the 金扫 q5 TOTAL cells, the engine can DERIVE the gold
    #     COUNT exactly (total/avg) — high value. (q4 avg_cells 200013 skipped: q4 cells already locked.)
    #   · 紫/金/红 avg_value (200036/200037/200038): anchors the hidden-tier per-item value (skill T2
    #     reveals no value), the main lever on the value over-estimate.
    for r in warehouse.public_info_rows:
        iid = int(r.get("info_id") or 0)
        val = r.get("value")
        if val is None:
            continue
        q = PUBLIC_AVG_CELLS_INFO.get(iid)
        if q is not None:
            fu.append({"path": ["bucket", q, "avg_cells"], "value": float(val)})
        qv = PUBLIC_AVG_VALUE_INFO.get(iid)
        if qv is not None:
            fu.append({"path": ["bucket", qv, "avg_value"], "value": float(val)})
    if fu:
        sri["field_updates"] = fu
    # 全均格(200014) -> 引擎 semantic total_avg_cells(constraints.public_info.public_numeric_facts):
    # 用作总格<->件数合理化约束(count≈grid/总均格), 限定艾莎隐藏金/红的件数估计范围。
    constraints: dict[str, Any] = {}
    tac = next((r.get("value") for r in warehouse.public_info_rows
                if int(r.get("info_id") or 0) == PUBLIC_TOTAL_AVG_CELLS_INFO and r.get("value") is not None), None)
    if tac is not None:
        constraints["public_info"] = {"public_numeric_facts": [{"semantic": "total_avg_cells", "value": float(tac)}]}
    return {
        "ui_contract": {
            "context": {"hero": hero, "map_id": warehouse.map_id, "phase": "bidding", "round": int(round_no)},
            "constraints": constraints,
        },
        "map_id": warehouse.map_id,
        "minimap_grid_items": minimap_items,
        "public_info_rows": warehouse.public_info_rows,
        "skill_reveal_rows": skill_rows,
        "action_result_rows": action_rows,
        "structured_ref_inputs": sri,
        "_synthetic": {"source": warehouse.source, "revealed_unique": len(revealed), "round": round_no},
    }


def _engine_total_and_q(result: dict[str, Any]) -> tuple[int, dict[str, int]]:
    ranges = result.get("quality_count_ranges") or {}
    qmid = {q: int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QMAP}
    return sum(qmid.values()), qmid


def cadence(hero: str, n_warehouses: int = 80, seed: int = 11) -> int:
    rng = random.Random(seed)
    files = sorted(glob.glob(str(SAMPLE_DIR / "*.json")))
    rng.shuffle(files)
    by_round: dict[int, list[int]] = {r: [] for r in (1, 2, 3, 4)}
    used = 0
    for f in files:
        if used >= n_warehouses:
            break
        if not _valid_source(Path(f).name):
            continue
        wh = load_warehouse(Path(f))
        if wh is None or wh.total_count <= 0 or not _truth_consistent(wh):
            continue
        inst_seed = rng.random()
        for r in (1, 2, 3, 4):
            by_round[r].append(len(reveal_state(wh, hero, r, random.Random((inst_seed, r).__hash__()))))
        used += 1
    print(f"=== synthetic {hero} unique-reveal cadence (n={used}) ===")
    for r in (1, 2, 3, 4):
        c = by_round[r]
        if c:
            print(f"  round {r}: median={statistics.median(c):.0f} mean={statistics.mean(c):.1f} [{min(c)}..{max(c)}]")
    return 0


def validate(hero: str, n_warehouses: int = 60, round_no: int = 4, seed: int = 7) -> int:
    rng = random.Random(seed)
    files = sorted(glob.glob(str(SAMPLE_DIR / "*.json")))
    rng.shuffle(files)
    tot_bias: list[int] = []
    q_bias: dict[str, list[int]] = {q: [] for q in QMAP}
    used = 0
    for f in files:
        if used >= n_warehouses:
            break
        if not _valid_source(Path(f).name):
            continue
        wh = load_warehouse(Path(f))
        if wh is None or wh.total_count <= 0 or not _truth_consistent(wh):
            continue
        snap = synthesize_snapshot(wh, hero, round_no, random.Random(rng.random()))
        res = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()
        est_total, qmid = _engine_total_and_q(res)
        tot_bias.append(est_total - wh.total_count)
        for q, qs in QMAP.items():
            t = sum(wh.truth_counts.get(x, 0) for x in qs)
            q_bias[q].append(qmid[q] - t)
        used += 1
    if not tot_bias:
        print("no warehouses produced synthetic samples")
        return 1
    print(f"=== synthetic {hero} @ round {round_no}: n={used} ===")
    print(
        f"  TOTAL count bias mean={statistics.mean(tot_bias):+.1f} "
        f"MAE={statistics.mean(abs(x) for x in tot_bias):.1f} "
        f"std={statistics.pstdev(tot_bias) if len(tot_bias) > 1 else 0:.1f}"
    )
    for q in ("q1", "q3", "q4", "q5", "q6"):
        if q_bias[q]:
            print(f"  {q} bias={statistics.mean(q_bias[q]):+.1f} MAE={statistics.mean(abs(x) for x in q_bias[q]):.1f}")
    return 0


def _dump(hero: str, sample: str, round_no: int) -> int:
    wh = load_warehouse(Path(sample), with_geometry=True)  # geometry needed for the truth matrix
    if wh is None:
        print("could not load warehouse")
        return 1
    rng = random.Random(0)
    revealed, log = reveal_trace(wh, hero, round_no, rng)
    snap = synthesize_snapshot(wh, hero, round_no, random.Random(0))
    res = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()
    est_total, qmid = _engine_total_and_q(res)
    print(f"warehouse {wh.source} map={wh.map_id} truth_total={wh.total_count} truth_counts={wh.truth_counts}")
    print(f"synthetic {hero} @R{round_no}: revealed_unique={snap['_synthetic']['revealed_unique']} public_rows={len(wh.public_info_rows)}")
    print(f"engine est_total={est_total} qmid={qmid}")
    print(f"value p50={res.get('value_p50')} truth_total_value={wh.truth_total_value}")
    # Matrix report (truth footprint + per-round revealed overlay) for mechanic review.
    out: list[str] = [
        f"# 合成 {hero} 矩阵 — {wh.source}",
        f"图 map={wh.map_id} · 真值 {wh.total_count} 件 / 总价 {wh.truth_total_value:,} · 真值件数 {wh.truth_counts}",
        f"合成 @R{round_no}: 揭示 {snap['_synthetic']['revealed_unique']} 件; 引擎估 {est_total} 件 qmid={qmid} · 估价 {res.get('value_p50'):,}",
        "",
    ]
    out += _public_info_md(wh)
    for entry in log:
        out += _round_events_md(wh, entry)
    out.append("")
    out.append(_reveal_matrices_html(wh, revealed))
    report = ROOT / "data" / "reports" / f"synth_{hero}_dump_{Path(sample).stem[:30]}_R{round_no}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"[matrix] {report}")
    return 0


QUALITY_SYMBOLS = {1: "w", 2: "v", 3: "b", 4: "p", 5: "G", 6: "R"}  # w白 v绿 b蓝 p紫 G金 R红
TIER_LABEL = {T1: "品质", T2: "品质+轮廓", T3: "全信息(含价值)"}
GRID_COLS = 10


def _decode_rc(local_index: Any) -> tuple[int, int] | None:
    """1-based (row, col) from local_index (col 1=leftmost). Matches minimap_audit_matrix."""
    if local_index is None:
        return None
    li = int(local_index)
    return li // GRID_COLS + 1, li % GRID_COLS + 1


def _item_label(warehouse: Warehouse, idx: int, *, with_value: bool) -> str:
    it = warehouse.items[idx]
    sym = QUALITY_SYMBOLS.get(it["quality"], "?")
    s = f"{sym}{it['cells']}格@li{it['local_index']}"
    if with_value:
        s += f"={it['value']:,}"
    return s


def _item_footprint(it: dict[str, Any]) -> list[tuple[int, int]]:
    """All (row, col) cells the item occupies. Uses real settled geometry (row/col/width/height);
    falls back to the single local_index anchor cell when geometry is absent."""
    r, c, w, h = it.get("row"), it.get("col"), it.get("width"), it.get("height")
    if r and c and w and h:
        return [(rr, cc) for rr in range(r, r + h) for cc in range(c, c + w)]
    rc = _decode_rc(it.get("local_index"))
    return [rc] if rc else []


# Public info_ids that reveal ALL items of ONE quality at outline granularity (品质定向全轮廓).
QUALITY_OUTLINE_INFO = {200001, 200002, 200003}


# ---- colored HTML matrix (renders in VSCode markdown preview / browser) ----
QUALITY_BG = {1: "#eeeeee", 2: "#7cb342", 3: "#1e88e5", 4: "#8e24aa", 5: "#fdd835", 6: "#e53935"}
QUALITY_NAME = {1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}
_DARK_TEXT_Q = {1, 5}  # 白/金底用深字, 其余用白字
GRAN_BORDER = {T1: "1px solid #b0b0b0", T2: "2px dashed #1a1a1a", T3: "3px double #000"}
ROUND_PILL = {1: "#3949ab", 2: "#00897b", 3: "#ef6c00", 4: "#c62828", 5: "#6a1b9a"}


def _cell_html(quality: int, meta: dict[str, Any] | None, qletter: str) -> str:
    bg = QUALITY_BG.get(quality, "#ffffff")
    txt = "#222" if quality in _DARK_TEXT_Q else "#fff"
    if meta is None:  # 有藏品但未揭示: 半透明品质底 + ·
        return f'<td style="background:{bg}55;border:1px solid #e0e0e0;color:#999">·</td>'
    rnd, src = meta["round"], meta["src"]
    pill = (f'<span style="background:{ROUND_PILL.get(rnd, "#555")};color:#fff;border-radius:3px;'
            f'padding:0 3px;font-weight:bold">{rnd}</span>')
    letter = qletter if (meta["stype"] == "public" and meta.get("info_id") in QUALITY_OUTLINE_INFO) else ""
    hits = meta.get("hits", [])
    ov = f'<sup style="color:{txt}">{len(hits)}</sup>' if len(hits) > 1 else ""  # 重叠: 揭示次数
    tip = " / ".join(f"R{r}{s}" for r, s, _ in hits)
    return (f'<td title="{tip}" style="background:{bg};border:{GRAN_BORDER[meta["gran"]]};'
            f'color:{txt};padding:1px 2px">{pill}<b>{src}{letter}</b>{ov}</td>')


def _anchor_cell(it: dict[str, Any]) -> tuple[int, int] | None:
    """该件的'大致位置'锚点格(footprint 左上, 在则用 row/col, 否则解码 local_index)。"""
    if it.get("row") and it.get("col"):
        return (int(it["row"]), int(it["col"]))
    return _decode_rc(it.get("local_index"))


def _reveal_matrices_html(warehouse: Warehouse, revealed: dict[int, dict[str, Any]]) -> str:
    """ONE colored grid: 底色=真值品质(满铺), 格内=轮次色块+来源字, 边框=粒度, 角标=重叠揭示次数.
    ⚠️ 粒度差异: ①只品质(T1, 索菲技能/宝光/公开只品质)=**无轮廓**, 只标'大致位置'锚点一格;
    ②品质+轮廓(T2)/③全信息(T3)=形状已知, 满铺整个 footprint。"""
    cell_owner: dict[tuple[int, int], int] = {}
    max_row = 1
    for idx, it in enumerate(warehouse.items):
        for cell in _item_footprint(it):
            cell_owner[cell] = idx  # footprints don't overlap in a settled vault; any wins
            max_row = max(max_row, cell[0])
    # cells where the reveal marker is actually KNOWN to the hero (T1 -> anchor only, no outline).
    reveal_cells: dict[tuple[int, int], dict[str, Any]] = {}
    for idx, meta in revealed.items():
        it = warehouse.items[idx]
        if meta["gran"] == T1:  # 只品质+大致位置, 无轮廓 -> 仅锚点
            rc = _anchor_cell(it)
            if rc is not None:
                reveal_cells[rc] = meta
        else:  # 轮廓/全信息 -> 满铺已知形状
            for cell in _item_footprint(it):
                reveal_cells[cell] = meta
    rows = ['<table style="border-collapse:collapse;font:12px/1.15 monospace;text-align:center">']
    rows.append('<tr><th style="width:24px"></th>'
                + "".join(f'<th style="width:46px;color:#888">{c}</th>' for c in range(1, GRID_COLS + 1)) + "</tr>")
    for r in range(1, max_row + 1):
        cells = [f'<th style="color:#888">r{r}</th>']
        for c in range(1, GRID_COLS + 1):
            idx = cell_owner.get((r, c))
            if idx is None:
                cells.append('<td style="border:1px solid #f3f3f3"></td>')  # 无藏品
            else:
                q = warehouse.items[idx]["quality"]
                cells.append(_cell_html(q, reveal_cells.get((r, c)), QUALITY_SYMBOLS.get(q, "").upper()))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def _matrix_legend_html() -> str:
    q = " ".join(
        f'<span style="background:{QUALITY_BG[k]};color:{"#222" if k in _DARK_TEXT_Q else "#fff"};'
        f'padding:1px 5px;border-radius:3px">{QUALITY_NAME[k]}</span>'
        for k in (1, 2, 3, 4, 5, 6)
    )
    rnd = " ".join(
        f'<span style="background:{ROUND_PILL[k]};color:#fff;padding:0 4px;border-radius:3px">{k}</span>'
        for k in (1, 2, 3, 4, 5)
    )
    return (
        "<b>图例</b><br>\n"
        f'底色=真值品质(满铺): {q}<br>\n'
        f'格内数字色块=揭示轮次: {rnd}<br>\n'
        "格内汉字=信息来源: 技=技能 公=公开 宝⁴=宝光四鉴 宝²=宝光2鉴 检²=抽检2 检¹=抽检1 寻=至宝寻踪 巨=巨物 估=至宝估价<br>\n"
        "边框=粒度: <span style=\"border:1px solid #b0b0b0;padding:0 3px\">细实线 只品质①</span> "
        "<span style=\"border:2px dashed #1a1a1a;padding:0 3px\">虚线 品质+轮廓②</span> "
        "<span style=\"border:3px double #000;padding:0 3px\">双线 全信息含价值③</span>"
        "（①**无轮廓**, 只标'大致位置'一格; ②③形状已知, 满铺整件）<br>\n"
        "公开品质定向轮廓(200001-3)在来源字后追加品质字母(如 <b>公P</b>=公开紫轮廓); "
        "右上角标=该件被揭示次数(>1 即重叠); 半透明+· = 有藏品未揭示; 空格 = 无藏品。"
    )


def _public_info_md(warehouse: Warehouse) -> list[str]:
    """List the source warehouse's public info (numeric facts + item reveals) so it's visible."""
    rows = warehouse.public_info_rows
    if not rows:
        return ["- **公开信息**: 源仓无保留的公开信息"]
    facts = [r for r in rows if r.get("gran") is None]
    reveals = [r for r in rows if r.get("gran") is not None]
    out = ["- **公开信息（源仓暴露，按轮次出现）**:"]
    if facts:
        out.append("  - 数值情报: " + " · ".join(f"{r['label']}({r['info_id']})={r.get('value')}" for r in facts))
    for r in sorted(reveals, key=lambda x: x.get("round", 1)):
        out.append(f"  - **R{r.get('round', 1)}** {r['label']}({r['info_id']}) "
                   f"[{TIER_LABEL.get(r['gran'])}]: 揭示 {r.get('revealed', 0)} 件")
    return out


ROUND_WEIGHTS = {3: 0.38, 4: 0.42, 5: 0.20}  # 合成样本轮次分布: R3/R4 多, R5 少


def _pick_round(rng: random.Random) -> int:
    r, cum = rng.random(), 0.0
    for rnd, w in ROUND_WEIGHTS.items():
        cum += w
        if r <= cum:
            return rnd
    return 4


_ROUNDS_RE = re.compile(r"_(\d+)rounds_")
_MAP_RE = re.compile(r"_(\d{4})_\d+rounds_")
MIN_SOURCE_ROUNDS = 3  # only synthesize from sources that themselves reached >=3 rounds (richer public info)
# Valid source map THEMES (by hundreds digit): 4=别墅villa, 5=残骸/沉船shipwreck.
# Exclude 1=快递station, 3=集装箱container, 6=隐秘拍卖(2601). Draw-hero calibration is villa/shipwreck only.
KEEP_MAP_THEMES = {4, 5}


def _source_rounds(name: str) -> int:
    m = _ROUNDS_RE.search(name)
    return int(m.group(1)) if m else 0


def _source_map(name: str) -> int:
    m = _MAP_RE.search(name)
    return int(m.group(1)) if m else 0


def _valid_source(name: str) -> bool:
    return _source_rounds(name) >= MIN_SOURCE_ROUNDS and (_source_map(name) // 100) % 10 in KEEP_MAP_THEMES


# Public EXACT per-quality count info_ids (engine PUBLIC_EXACT_QUALITY_COUNT_INFO): the game shows the
# precise item count for that quality. Verified across 1391 aisha sources: value == settled truth count
# for ~90% (200018紫 73/81, 200019金/极品存量 92/97). The ~10% that DISAGREE are incomplete-settlement
# vaults (capture missed items) — their parsed truth is wrong, so a synthetic sample built on that truth
# would (a) lock the wrong tier count and (b) hard-conflict against the (correct) public count at R4.
PUBLIC_EXACT_COUNT_QUALITY = {200018: 4, 200019: 5, 200020: 6}
PUBLIC_EXACT_CELLS_QUALITY = {200010: 4, 200011: 5, 200012: 6}  # 各品质精确占格(95-99%==真值格)
# 公开均格/均价(引擎按 bucket.q.avg_cells/avg_value 消费, 经 field_updates 喂入)。q4均格200013 跳过
# (q4 已锁精确格); 金红均格(200015/16)+ 金扫总格 -> 反推件数; 紫金红均价(200036/37/38) -> 价值锚。
PUBLIC_AVG_CELLS_INFO = {200015: 5, 200016: 6}
PUBLIC_AVG_VALUE_INFO = {200036: 4, 200037: 5, 200038: 6}
PUBLIC_TOTAL_AVG_CELLS_INFO = 200014  # 全均格: 引擎按 semantic total_avg_cells 用作总格/件数合理化约束
#   (count≈grid/avg, 或 avg×count 锁总格) —— 经 constraints.public_info.public_numeric_facts 喂入。
PUBLIC_EXACT_TOTAL_CELLS = 200009  # 公开总格 (全库 94% == 结算总格)
PUBLIC_EXACT_TOTAL_COUNT = 200017  # 公开总件数 (97% == 结算总件数)


def _truth_consistent(wh: "Warehouse") -> bool:
    """A source is trustworthy only if every public AUTHORITATIVE value it exposes matches settled truth:
    per-quality exact counts (200018紫/200019金/200020红), public total cells (200009) and total count
    (200017). A mismatch = incomplete-settlement truth (bad source) -> skip, so synthetic samples are
    internally consistent (no public-vs-skill conflict). (author 2026-06-27: also check 公开总格.)"""
    # 含 wire 品质丢失且 item_id 也未知(不在 items.json)的件 = 无法恢复真值 -> 坏源, 删。
    # (可恢复的 q0 件已在 load_warehouse 用 items.json 补回品质/格子/价值, 计入 truth。)
    if wh.unrecoverable_items > 0:
        return False
    # 恢复后 wh.items 已含全部真藏品 -> 公开权威值与结算真值必须严格一致(q0 不再是口径差, 是抓包丢件)。
    cells_pos = sum(int(it.get("cells", 0) or 0) for it in wh.items)
    cells_by_q: dict[int, int] = {}
    for it in wh.items:
        cells_by_q[it["quality"]] = cells_by_q.get(it["quality"], 0) + int(it.get("cells", 0) or 0)
    for r in wh.public_info_rows:
        iid = int(r.get("info_id") or 0)
        if r.get("value") is None:
            continue
        v = int(r["value"])
        q = PUBLIC_EXACT_COUNT_QUALITY.get(iid)
        if q is not None and v != int(wh.truth_counts.get(q, 0)):
            return False
        qc = PUBLIC_EXACT_CELLS_QUALITY.get(iid)
        if qc is not None and v != int(cells_by_q.get(qc, 0)):
            return False
        if iid == PUBLIC_EXACT_TOTAL_CELLS and v != cells_pos:
            return False
        if iid == PUBLIC_EXACT_TOTAL_COUNT and v != int(wh.total_count):
            return False
    return True


def detail(hero: str, n_warehouses: int = 12, seed: int = 3) -> int:
    """Per-sample (round drawn from R3-R5) markdown: 公开信息明细 + 逐轮技能/道具 + 满铺/揭示矩阵.
    For auditing the mechanic against docs/draw_hero_reveal_mechanics.zh-CN.md."""
    rng = random.Random(seed)
    files = sorted(glob.glob(str(SAMPLE_DIR / "*.json")))
    rng.shuffle(files)
    out: list[str] = [
        f"# 合成 {hero} 逐轮揭示明细 + 矩阵核对（n={n_warehouses}, 轮次 R3-R5 浮动）",
        "",
        "> 机制表: docs/draw_hero_reveal_mechanics.zh-CN.md。每件: 品质符号w白v绿b蓝p紫G金R红 + 格数 + li + 值(全信息档才有)。",
        "> 矩阵需用 **Markdown 预览**(VSCode 预览 / 浏览器)查看彩色表格。",
        "",
        _matrix_legend_html(),
        "",
    ]
    # --- select sources; GUARANTEE >=1 carries a 品质定向公开轮廓 (200001-003) so the ^**P marker
    # is always showcased for the author's audit (a purely random batch often misses it). ---
    def _has_outline_pub(wh: Warehouse) -> bool:
        return any(p.get("info_id") in QUALITY_OUTLINE_INFO for p in wh.public_reveals)

    valid = (f for f in files if _valid_source(Path(f).name))  # villa/shipwreck, >=3 rounds
    picks: list[Warehouse] = []
    for f in valid:
        if len(picks) >= n_warehouses:
            break
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is not None and wh.total_count > 0 and _truth_consistent(wh):
            picks.append(wh)
    if picks and not any(_has_outline_pub(w) for w in picks):
        for f in valid:  # generator resumes past the batch; swap one outline source to the front
            wh = load_warehouse(Path(f), with_geometry=True)
            if wh is None or wh.total_count <= 0 or not _truth_consistent(wh):
                continue
            if _has_outline_pub(wh):
                picks = [wh] + picks[:-1]
                break

    for used, wh in enumerate(picks):
        sample_round = _pick_round(rng)
        inst = random.Random(rng.random())
        revealed, log = reveal_trace(wh, hero, sample_round, inst)
        tc = {QUALITY_SYMBOLS[q]: wh.truth_counts.get(q, 0) for q in (1, 2, 3, 4, 5, 6) if wh.truth_counts.get(q, 0)}
        out += [
            f"## {used + 1}. 源仓 `{wh.source}` · map {wh.map_id} · 合成到 **R{sample_round}**",
            f"- **真值**: 总{wh.total_count}件 · " + " ".join(f"{k}{v}" for k, v in tc.items())
            + f" · 总值{wh.truth_total_value:,}",
            f"- **累计唯一揭示@R{sample_round}**: {len(revealed)} 件",
        ]
        out += _public_info_md(wh)
        out.append("")
        for entry in log:
            parts: list[str] = []
            for ev in entry["events"]:
                items = ev.get("items", [])
                dups = ev.get("dups", [False] * len(items))
                n_dup = sum(dups)
                if ev["kind"] == "public":
                    dtag = f"，其中 {n_dup} 件重复(已被先前公开揭示)" if n_dup else ""
                    parts.append(f"- 公开揭示 ×{len(items)}（见上「公开信息」）{dtag}")
                    continue
                if ev["kind"] == "tool" and ev["tool"] == "none":
                    parts.append("- 道具: 未用")
                    continue
                with_val = ev["gran"] == T3
                tlabel = TIER_LABEL.get(ev["gran"], "-")
                labs = ", ".join(
                    _item_label(wh, i, with_value=with_val) + ("(重复)" if dup else "")
                    for i, dup in zip(items, dups)
                ) or "（空）"
                dtag = f" [含{n_dup}重复]" if n_dup else ""
                if ev["kind"] == "skill":
                    parts.append(f"- {ev['label']} [{tlabel}] ×{len(items)}{dtag}: {labs}")
                else:
                    tgt = f" 目标={ev['target']}" if ev.get("target") and ev["target"] != "random" else ""
                    parts.append(f"- 道具·{TOOL_LABEL.get(ev['tool'], ev['tool'])} [{tlabel}{tgt}] ×{len(items)}{dtag}: {labs}")
            out.append(f"**R{entry['round']}**")
            out += parts
            out.append("")
        out.append(_reveal_matrices_html(wh, revealed))
        out.append("")
    report = ROOT / "data" / "reports" / f"synth_draw_{hero}_detail.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"[wrote] {report}  ({len(picks)} samples)")
    return 0


def _round_events_md(wh: "Warehouse", entry: dict[str, Any]) -> list[str]:
    """One round's reveal lines (技能/道具/公开 + 重复标注), shared by detail/trace."""
    parts: list[str] = []
    for ev in entry["events"]:
        items = ev.get("items", [])
        dups = ev.get("dups", [False] * len(items))
        n_dup = sum(dups)
        if ev["kind"] == "public":
            dtag = f"，其中 {n_dup} 件重复" if n_dup else ""
            parts.append(f"- 公开揭示 ×{len(items)}（见上「公开信息」）{dtag}")
            continue
        if ev["kind"] == "tool" and ev["tool"] == "none":
            parts.append("- 道具: 未用")
            continue
        if ev["kind"] == "tool" and ev["tool"] == "goldscan":
            parts.append(f"- 道具·金色扫描 [仅金色总格数]: 金色占 {int(ev.get('q5_cells_total', 0))} 格（件数/位置/身份均不揭示）")
            continue
        with_val = ev["gran"] == T3
        tlabel = TIER_LABEL.get(ev["gran"], "-")
        labs = ", ".join(
            _item_label(wh, i, with_value=with_val) + ("(重复)" if dup else "")
            for i, dup in zip(items, dups)
        ) or "（空）"
        dtag = f" [含{n_dup}重复]" if n_dup else ""
        if ev["kind"] == "skill":
            parts.append(f"- {ev['label']} [{tlabel}] ×{len(items)}{dtag}: {labs}")
        else:
            tgt = f" 目标={ev['target']}" if ev.get("target") and ev["target"] != "random" else ""
            parts.append(f"- 道具·{TOOL_LABEL.get(ev['tool'], ev['tool'])} [{tlabel}{tgt}] ×{len(items)}{dtag}: {labs}")
    return parts


def _fmt_v(x: Any) -> str:
    return f"{int(x):,}" if isinstance(x, (int, float)) and x else "-"


def _count_mid_lo_hi(res: dict[str, Any]) -> tuple[int, int, int]:
    r = res.get("quality_count_ranges") or {}
    return (sum(int((r.get(q) or [0, 0, 0])[0] or 0) for q in QMAP),
            sum(int((r.get(q) or [0, 0, 0])[1] or 0) for q in QMAP),
            sum(int((r.get(q) or [0, 0, 0])[2] or 0) for q in QMAP))


def _engine_est_md(res_default: dict[str, Any], res_nocap: dict[str, Any]) -> str:
    # 三档统一口径: 保守=低端(件少/格少/估值低) / 参考=中位 / 激进=高端. 件数/格子取引擎 range 的
    # [lo, mid, hi]; 价格取 conservative/balanced/aggressive(同 live UI 出价旋钮). 主显=已发布行为(占格夹
    # 默认 ON); "(无夹 …)"=关掉占格夹的 rollback 参考, 仅在数值不同时附上. 占格夹只动件数+价值, 不动格子.
    clo, cmid, chi = _count_mid_lo_hi(res_default)
    nlo, nmid, nhi = _count_mid_lo_hi(res_nocap)
    g = res_default.get("total_grid_range") or [None, None, None]

    def _tiers(res: dict[str, Any]) -> str:
        return f"{_fmt_v(res.get('conservative'))} / {_fmt_v(res.get('balanced'))} / {_fmt_v(res.get('aggressive'))}"

    count = f"{clo} / {cmid} / {chi}"
    if (nlo, nmid, nhi) != (clo, cmid, chi):
        count += f"  (无夹 {nlo} / {nmid} / {nhi})"
    grid = f"{g[0]} / {g[1]} / {g[2]}"
    t_def, t_nc = _tiers(res_default), _tiers(res_nocap)
    price = t_def + (f"  (无夹 {t_nc})" if t_nc != t_def else "")
    return ("- **引擎该轮估计**（保守 / 参考 / 激进，已含占格夹）:\n"
            f"  - 件数 = {count}\n"
            f"  - 格子 = {grid}\n"
            f"  - 价格 = {price}")


def trace(hero: str, n_warehouses: int = 5, seed: int = 23) -> int:
    """Per-ROUND trace for manual reasonableness review: for each vault, R1..R_last shows
    reveals + 引擎该轮估计(件数/格子/价值 range) + 揭示矩阵 + 末尾真值. Draw-hero per-round estimates
    track the ROUND's revealed state (not necessarily the final settlement) — author judges per-round
    reasonableness. ENGINE runs once per (vault, round): ~6-9s/run, so n=5 vaults ×~4 rounds ≈ 3-5 min.
    Output: data/reports/synth_draw_{hero}_trace.md."""
    rng = random.Random(seed)
    files = sorted(glob.glob(str(SAMPLE_DIR / "*.json")))
    rng.shuffle(files)
    out: list[str] = [
        f"# 合成 {hero} 逐轮 trace（人工评估每轮合理性, n={n_warehouses}）",
        "",
        "> 抽奖英雄每轮估计=和**当回合揭示状态**对得上且合理即可,不必贴最终结算。每轮给 揭示+引擎估计+矩阵,末尾附最终真值参考。",
        "> 矩阵需 Markdown 预览(VSCode Ctrl+Shift+V)看彩色。",
        "",
        _matrix_legend_html(),
        "",
    ]
    used = 0
    for f in files:
        if used >= n_warehouses:
            break
        if not _valid_source(Path(f).name):
            continue
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None or wh.total_count <= 0 or not _truth_consistent(wh):
            continue
        last_round = min(_source_rounds(wh.source), 5)
        if last_round < 1:
            continue
        vault_seed = rng.random()
        truth_cells = sum(int(it.get("cells") or 0) for it in wh.items)
        tc = {QUALITY_SYMBOLS[q]: wh.truth_counts.get(q, 0) for q in (1, 2, 3, 4, 5, 6) if wh.truth_counts.get(q, 0)}
        out.append(f"## {used + 1}. 源仓 `{wh.source}` · map {wh.map_id} · 源仓{last_round}轮")
        out.append(f"- **最终真值(参考)**: 总{wh.total_count}件 · {truth_cells}格 · "
                   + " ".join(f"{k}{v}" for k, v in tc.items()) + f" · 总值{wh.truth_total_value:,}")
        out += _public_info_md(wh)
        out.append("")
        for r in range(1, last_round + 1):
            revealed, log = reveal_trace(wh, hero, r, random.Random(vault_seed))
            snap = synthesize_snapshot(wh, hero, r, random.Random(vault_seed))
            os.environ["DRAW_HERO_DISABLE_OCC_CAP"] = "1"  # 无夹(rollback 参考)
            res_nocap = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()
            os.environ.pop("DRAW_HERO_DISABLE_OCC_CAP", None)  # 默认: 占格夹 ON(已发布行为)
            res_default = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()
            out.append(f"### R{r} · 累计唯一揭示 {len(revealed)} 件")
            out += _round_events_md(wh, log[-1])  # this round's new reveals
            out.append(_engine_est_md(res_default, res_nocap))
            out.append("")
            out.append(_reveal_matrices_html(wh, revealed))
            out.append("")
        used += 1
    report = ROOT / "data" / "reports" / f"synth_draw_{hero}_trace.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"[trace] {report}  ({used} vaults)")
    return 0


SYNTH_DATASET_DIR = ROOT / "data" / "samples" / "synthetic_draw"
# Source pools for synth generation, each tagged with its drop-rate ERA. The engine now uses the
# post-0625 NEW drop-table prior, so red/value validation is era-correct ONLY on NEW warehouses;
# OLD warehouses (pre-0625 rates) still validate the count mechanic (counts are locked from truth).
# 56xx (ranked, theme 6) is excluded by KEEP_MAP_THEMES — synth is villa/shipwreck only.
NEWRATE_DIR = ROOT / "data" / "samples" / "group_post0625_newrate_classic"
RANKED_56XX_DIR = ROOT / "data" / "samples" / "group_0625_56xx"
SOURCE_POOLS: list[tuple[Path, str]] = [
    (SAMPLE_DIR, "OLD"),       # fatbeans: pre-0625 villa/shipwreck batch
    (NEWRATE_DIR, "NEW"),      # post-0625 classic villa/shipwreck (new rates)
    (RANKED_56XX_DIR, "NEW"),  # post-0625 56xx ranked (隐秘拍卖, red ~2x classic — aisha's hardest case)
]


def _keep_map(map_id: int | None) -> bool:
    """Synth source map families: villa(theme4) / shipwreck(theme5) + the 56xx ranked family (隐秘拍卖,
    map//100==56, post-0625). 56xx is multi-round with clean truth + front-loaded R1 public, so the
    aisha mechanic applies; its ~2x red rate makes it the key high-red validation. Excludes
    快递站(1)/集装箱(3)/旧隐秘拍卖 2601 (which is theme6 but NOT the 56xx family)."""
    if not map_id:
        return False
    return ((map_id // 100) % 10) in KEEP_MAP_THEMES or (map_id // 100) == 56


def _source_ok(wh: "Warehouse | None", era: str = "OLD") -> bool:
    """Unified source validity (works for all pools — newrate/56xx filenames have no _Nrounds_ token, so
    rounds come from events via wh.source_rounds, not the filename).

    NEW (post-0625) sources are era-correct for the live drop-table prior and SCARCE, so use ALL of them
    regardless of how many rounds the player reached (>=1): a short game just misses the R3 public-info
    refresh, which is a small error and the warehouse truth (settlement) is complete either way. OLD
    sources are plentiful, so keep the >=3-round bar for richer public info. (author 2026-06-27)"""
    if wh is None:
        return False
    # Skip sources that are ALREADY aisha games — we have the REAL aisha sample for that warehouse, so a
    # synthetic aisha-on-aisha-warehouse is redundant AND would make the synth-validation pool share
    # warehouses with the real-aisha tuning pool (not independent). (author 2026-06-27)
    if wh.source_hero == "aisha" or "aisha" in (wh.source or ""):
        return False
    min_rounds = 1 if era == "NEW" else MIN_SOURCE_ROUNDS
    return (
        wh.total_count > 0
        and _keep_map(wh.map_id)
        and wh.source_rounds >= min_rounds
        and _truth_consistent(wh)  # public authoritative == settled truth (q0 recovered, else dropped)
    )


def emit(hero: str, n: int, seed: int = 17) -> int:
    """Persist up to N synthetic draw-hero samples (engine snapshot + settlement truth + ERA tag) to
    data/samples/synthetic_draw/{hero}/ for OFF/ON engine A-B evaluation. Rounds drawn R3-R5, one
    sample per source vault (max diversity) over BOTH the OLD (fatbeans) and NEW (post-0625 newrate)
    villa/shipwreck pools — each record carries era=OLD|NEW so rate-sensitive metrics can slice NEW."""
    import time

    rng = random.Random(seed)
    files: list[tuple[str, str]] = []
    for d, era in SOURCE_POOLS:
        files.extend((f, era) for f in glob.glob(str(d / "*.json")))
    rng.shuffle(files)
    out_dir = SYNTH_DATASET_DIR / hero
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("synth_*.json"):  # clean stale dataset
        old.unlink()
    written, skipped_badtruth, t0 = 0, 0, time.time()
    era_written: dict[str, int] = {"OLD": 0, "NEW": 0}
    for f, era in files:
        if written >= n:
            break
        # with_geometry=True so T2/T3 reveals carry real footprint width/height in the snapshot
        # (live parity, see synthesize_snapshot) — otherwise the persisted dataset under-reads grid depth.
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None or wh.total_count <= 0:
            continue
        if not _source_ok(wh, era):
            if not _truth_consistent(wh):
                skipped_badtruth += 1
            continue
        # Ethan synth is FULL-outline only (= R5 reveal state, grid-primary calibration target); other
        # heroes draw R3-R5 for round diversity.
        round_no = 5 if hero == "ethan" else _pick_round(rng)
        snap = synthesize_snapshot(wh, hero, round_no, random.Random(rng.random()))
        record = {
            "snapshot": snap,
            "hero": hero,
            "round": round_no,
            "era": era,  # OLD (pre-0625 rates) | NEW (post-0625 rates, era-correct for the live engine)
            "source": wh.source,
            "map_id": wh.map_id,
            "truth_count": wh.total_count,
            "truth_counts": {str(k): v for k, v in wh.truth_counts.items()},
            "truth_total_value": wh.truth_total_value,
            "truth_cells": sum(int(it.get("cells") or 0) for it in wh.items),  # 真值总占格(grid 维度)
        }
        (out_dir / f"synth_{hero}_{written:04d}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        written += 1
        era_written[era] += 1
        if written % 100 == 0:
            print(f"  [{hero}] {written}/{n} ({time.time() - t0:.0f}s)", flush=True)
    print(f"[emit] {out_dir}  ({written} samples [OLD={era_written['OLD']} NEW={era_written['NEW']}], "
          f"{time.time() - t0:.0f}s; skipped {skipped_badtruth} bad-truth sources)")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    mode, hero = argv[1], argv[2]
    if mode == "emit":
        return emit(hero, n=int(argv[3]) if len(argv) > 3 else 500)
    if mode == "validate":
        return validate(
            hero,
            n_warehouses=int(argv[3]) if len(argv) > 3 else 60,
            round_no=int(argv[4]) if len(argv) > 4 else 4,
        )
    if mode == "cadence":
        return cadence(hero, n_warehouses=int(argv[3]) if len(argv) > 3 else 80)
    if mode == "dump":
        return _dump(hero, argv[3], int(argv[4]) if len(argv) > 4 else 4)
    if mode == "detail":
        return detail(hero, n_warehouses=int(argv[3]) if len(argv) > 3 else 12)
    if mode == "trace":
        return trace(hero, n_warehouses=int(argv[3]) if len(argv) > 3 else 5)
    print(f"unknown mode {mode!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
