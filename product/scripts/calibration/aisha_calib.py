"""统一校准核心 —— 所有 aisha 校准脚本的单一真相源，避免各脚本各写一份、口径漂移。

三件事集中在这里:
1) 样本有效性: 只用 valid_aisha(mixed 全是坏真值，已隔离); settlement「揭示⊆真值」体检。
   见 memory fatbeans-mixed-samples-bad-truth。
2) 长尾鉴别与规范化(**仅红 q6**; 用户 2026-06-27: 金及以下不归一化): 判据两条 —
   ① 形状-floor: 同(品质,**朝向无关**形状)内 value >= 5x floor 即长尾, 替换=同组非长尾均值;
   ② 单格兜底: 单格价值 >= 3x 红典型单格(抓形状唯一、组内无对照的"新大红"如世界杯/赛季件,
      形状-floor 漏掉的), 替换=红典型单格 x 本件格数。红不可见、引擎按期望估, 长尾局真值被撑高
   -> 假欠估, 评估前须把长尾替换成期望值。见 memory long-tail-red-contaminates-truth。
3) **clean / normalized 分块**: 规范化替换值是「估计」不是真值。含长尾的样本标记为 'normalized'
   (真值经估计替换)，与 'clean'(真值原样未动)分开，避免估计值污染后续干净归档审查。

设计: 上半段(物品目录/长尾)只用 stdlib，overlay/打包环境可安全 import; 下半段(样本库/真值)
依赖 audit 工具，仅校准环境用。
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ITEMS_JSON = ROOT / "data/processed/items.json"
ITEMS_DROPPABLE_JSON = ROOT / "data/processed/items_droppable.json"
RETIRED_WORLD_CUP_Q6_ENABLE_ENV = (
    "BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES"
)
RETIRED_WORLD_CUP_Q6_ITEM_IDS = frozenset(
    {1016007, 1036006, 1036007, 1036008}
)

LONGTAIL_MULTIPLE = 5.0          # value >= 5x 同组 floor => 长尾(用户口径 2026-06-20)
QUALITIES_WITH_LONGTAIL = (6,)   # **仅红 q6**(用户 2026-06-27: 金色及以下不需要归一化, 只有红
# 才有那种单件价值远超常态的长尾大红/赛季件; 金 q5 单格分布连续到 49k, 归一化反而错杀正常高价金)。
# 形状按朝向归一(3x2 与 2x3 = 同一占位, 同组), 否则唯一朝向的件各成单件组、永远是自己的 floor、
# 永不被判长尾。见用户 2026-06-27。
PERCELL_LONGTAIL_MULTIPLE = 3.0  # 单格价值 >= 3x 该品质典型单格 => 长尾(形状唯一件的兜底判据,
# 形状 floor 判据漏掉的"新大红"靠它兜住; 阈值高到只抓单格极端, 正常大件(单格普通)不动)。


def _sk(shp) -> tuple:
    """朝向无关形状键: (3,2) 与 (2,3) 归一为 (2,3)。"""
    a, b = int(shp[0] or 0), int(shp[1] or 0)
    return (a, b) if a <= b else (b, a)


def _cells(shp) -> int:
    return max(1, int(shp[0] or 0)) * max(1, int(shp[1] or 0))


# ----------------------------------------------------------------------------
# 物品目录 + 长尾鉴别/规范化 (stdlib only — overlay/打包安全)
# ----------------------------------------------------------------------------
_CATALOG: dict[int, tuple[int, tuple, int, str]] | None = None       # id -> (value, shape, quality, name)
_REFERENCE_ITEM_IDS: frozenset[int] = frozenset()                    # current droppable/reference population
_REFERENCE_ENABLE_RETIRED: bool | None = None
_SHAPE_FLOOR: dict[tuple, int] | None = None                          # (quality,_sk(shape)) -> min value
_REPLACEMENT: dict[tuple, int] | None = None                         # (quality,_sk(shape)) -> 非长尾均值
_QMED_PERCELL: dict[int, float] | None = None                        # quality -> 典型(非长尾)单格价值中位


def _reference_item_ids() -> frozenset[int]:
    """Exact current normalization population.

    ``items.json`` remains the historical lookup catalog so an already revealed
    retired item can still be decoded.  Only the packaged droppable catalog may
    contribute floors, medians, replacements, and current map-value priors.
    The exact ``"1"`` rollback switch adds the four canonical World Cup rows;
    localization keys are never item IDs and cannot enter this set.
    """
    try:
        value = json.loads(ITEMS_DROPPABLE_JSON.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    rows = list(value.values()) if value.__class__ is dict else value
    if rows.__class__ is not list:
        return frozenset()
    item_ids: set[int] = set()
    for row in rows:
        if row.__class__ is not dict:
            return frozenset()
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in item_ids:
            return frozenset()
        item_ids.add(item_id)
    if os.environ.get(RETIRED_WORLD_CUP_Q6_ENABLE_ENV) == "1":
        item_ids.update(RETIRED_WORLD_CUP_Q6_ITEM_IDS)
    return frozenset(item_ids)


def _catalog() -> dict[int, tuple[int, tuple, int, str]]:
    global _CATALOG, _REFERENCE_ITEM_IDS, _REFERENCE_ENABLE_RETIRED
    global _SHAPE_FLOOR, _REPLACEMENT, _QMED_PERCELL
    if _CATALOG is None:
        items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
        cat: dict[int, tuple[int, tuple, int, str]] = {}
        for e in items:
            iid = e.get("item_id")
            if iid is None:
                continue
            cat[int(iid)] = (
                int(e.get("value") or 0),
                (e.get("shape_w"), e.get("shape_h")),
                int(e.get("quality", 0) or 0),
                e.get("name") or "",
            )
        _CATALOG = cat
        _REFERENCE_ENABLE_RETIRED = None

    enable_retired = os.environ.get(RETIRED_WORLD_CUP_Q6_ENABLE_ENV) == "1"
    if _REFERENCE_ENABLE_RETIRED == enable_retired:
        return _CATALOG
    reference_ids = _reference_item_ids()
    reference = {
        item_id: _CATALOG[item_id]
        for item_id in reference_ids
        if item_id in _CATALOG
    }
    if len(reference) != len(reference_ids):
        reference = {}
        reference_ids = frozenset()
    # floor / 非长尾均值 按【朝向无关】形状键 _sk 聚合(3x2 与 2x3 同组)
    floor: dict[tuple, int] = defaultdict(lambda: 10 ** 12)
    for v, shp, q, nm in reference.values():
        if v > 0 and shp != (0, 0):
            floor[(q, _sk(shp))] = min(floor[(q, _sk(shp))], v)
    normals: dict[tuple, list[int]] = defaultdict(list)
    pc_normals: dict[int, list[float]] = defaultdict(list)
    for v, shp, q, nm in reference.values():
        if v > 0 and shp != (0, 0) and v < LONGTAIL_MULTIPLE * floor[(q, _sk(shp))]:
            normals[(q, _sk(shp))].append(v)           # 形状-floor 非长尾
            pc_normals[q].append(v / _cells(shp))       # 其单格价值 = 该品质"典型"单格
    repl = {key: int(sum(vs) / len(vs)) for key, vs in normals.items()}
    import statistics as _st
    qmed = {q: _st.median(vs) for q, vs in pc_normals.items() if vs}
    _REFERENCE_ITEM_IDS = reference_ids
    _REFERENCE_ENABLE_RETIRED = enable_retired
    _SHAPE_FLOOR, _REPLACEMENT, _QMED_PERCELL = dict(floor), repl, qmed
    return _CATALOG


def _shape_floor_longtail(item_id: int) -> bool:
    """经典判据: 同(品质,朝向无关形状)内 value >= 5x floor(组内有便宜同形件作对照)。"""
    meta = _CATALOG.get(int(item_id))
    if meta is None:
        return False
    v, shp, q, _ = meta
    if q not in QUALITIES_WITH_LONGTAIL or v <= 0 or shp == (0, 0):
        return False
    fl = _SHAPE_FLOOR.get((q, _sk(shp)))
    return bool(fl and v >= LONGTAIL_MULTIPLE * fl)


def item_meta(item_id: int) -> tuple[int, tuple, int, str] | None:
    """(value, shape, quality, name) 或 None。"""
    return _catalog().get(int(item_id))


def _percell_longtail(item_id: int) -> bool:
    """兜底判据(仅红 q6): 单格价值 >= 3x 红典型单格 => 长尾。形状唯一(组内无对照)、形状-floor 漏掉的
    "新大红"(世界杯/赛季里单格远超常态的件)靠它兜住; 阈值高到只抓单格极端, 普通大红不动。"""
    meta = _CATALOG.get(int(item_id))
    if meta is None:
        return False
    v, shp, q, _ = meta
    if q not in QUALITIES_WITH_LONGTAIL or v <= 0 or shp == (0, 0):
        return False
    med = (_QMED_PERCELL or {}).get(q)
    return bool(med and (v / _cells(shp)) >= PERCELL_LONGTAIL_MULTIPLE * med)


def is_longtail(item_id: int) -> bool:
    """该物品是否长尾(金/红): 形状-floor 判据 或 单格兜底判据。其他品质/0格 一律 False。"""
    _catalog()
    return _shape_floor_longtail(item_id) or _percell_longtail(item_id)


def replacement_value(item_id: int) -> int:
    """长尾物的规范化替换值。形状-floor 长尾 -> 同(品质,朝向无关形状)非长尾均值(组内有对照)。
    仅单格兜底命中的(形状唯一、无组内对照) -> 该品质典型单格 x 本件格数(把单格溢价削回常态)。"""
    _catalog()
    meta = _CATALOG.get(int(item_id))
    if meta is None:
        return 0
    v, shp, q, _ = meta
    if _shape_floor_longtail(item_id):
        return _REPLACEMENT.get((q, _sk(shp)), v)
    med = (_QMED_PERCELL or {}).get(q)
    if med:
        return int(med * _cells(shp))
    return _REPLACEMENT.get((q, _sk(shp)), v)


def normalized_item_value(item_id: int) -> int:
    """规范化后的单件价值: 0格物->0; 长尾->同组非长尾均值; 否则原值。"""
    meta = item_meta(item_id)
    if meta is None:
        return 0
    v, shp, q, _ = meta
    if shp == (0, 0):       # 称号/头像框/成就图标 — 非仓库物品
        return 0
    if is_longtail(item_id):
        return replacement_value(item_id)
    return v


def longtail_catalog() -> list[dict[str, Any]]:
    """所有长尾物清单(金+红)，供审阅替换值合理性。"""
    _catalog()
    out = []
    for iid, (v, shp, q, nm) in _CATALOG.items():
        if is_longtail(iid):
            fl = _SHAPE_FLOOR.get((q, _sk(shp))) or v
            crit = "percell" if (not _shape_floor_longtail(iid)) else "shape_floor"
            out.append({"item_id": iid, "name": nm, "quality": q, "shape": list(shp),
                        "value": v, "replacement": replacement_value(iid),
                        "x_floor": round(v / fl, 1), "criterion": crit})
    return sorted(out, key=lambda d: -d["x_floor"])


def max_normal_per_item(quality: int) -> int:
    """该品质「普通(非长尾)」单件价值上限 — 给只有聚合真值(无 item_id)的场景(如 export 复盘)
    做启发式长尾判断: 若真值里某品质 value/count 超过此值，疑似含长尾。"""
    _catalog()
    vals = [normalized_item_value(iid) for iid, (v, shp, q, nm) in _CATALOG.items()
            if iid in _REFERENCE_ITEM_IDS and q == quality and shp != (0, 0) and v > 0]
    return max(vals) if vals else 0


# ----------------------------------------------------------------------------
# 样本库 + 真值 (依赖 audit 工具 — 仅校准环境)
# ----------------------------------------------------------------------------
def valid_aisha_samples(rounds: int = 4) -> list[Path]:
    """当前 clean-eval 的 Aisha 样本；rounds<=0 表示不限轮次。"""
    import sys

    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from build_clean_eval_set import clean_eval_files

    samples = [Path(path) for path in clean_eval_files("aisha")]
    if rounds > 0:
        token = f"_{rounds}rounds_"
        samples = [path for path in samples if token in path.name]
    return sorted(samples)


def _events(sample: Path):
    from bidking_lab.live.fatbeans import parse_fatbeans_capture
    return parse_fatbeans_capture(sample)


def settlement_breakdown(events) -> dict[str, Any]:
    """裸结算真值(聚合)。注意: 含长尾红/金时 value 被撑高 — 评估请用 normalized_truth。"""
    import sys
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    from audit_aisha_gap import _settlement_breakdown
    return _settlement_breakdown(events) or {}


def settlement_is_valid(events, revealed_cells: int | None = None) -> bool:
    """体检: breakdown 真值是否完整(揭示⊆真值的物理铁律)。revealed_cells 给定时，
    breakdown_cells < revealed => 坏样本(部分结算)。"""
    bd = settlement_breakdown(events)
    cells = int(bd.get("total_cells") or 0)
    if cells <= 0:
        return False
    if revealed_cells is not None and cells < revealed_cells:
        return False
    return True


# 公开「精确件数」info_id -> quality. 游戏公开面板直接给该品质的确切件数(引擎
# PUBLIC_EXACT_QUALITY_COUNT_INFO 同此映射)。全库 1391 源扫描: value==结算真值件数 ~90%
# (200018紫 73/81, 200019金/极品存量 92/97)。不符的 ~10% = 结算不全(抓包漏件)的坏真值仓 ——
# 公开件数是游戏权威值, 结算却少了件, 这类样本真值不可信。用户 2026-06-27 确认此判据。
PUBLIC_EXACT_COUNT_QUALITY = {200018: 4, 200019: 5, 200020: 6}
# 公开「精确占格」info_id -> quality (引擎 PUBLIC_EXACT_QUALITY_CELLS_INFO): 各品质总占格。
# 全库验: 200010紫格 137/144(95%), 200011金格 137/139(99%); 不符=结算不全。用户 2026-06-27 问 200010。
PUBLIC_EXACT_CELLS_QUALITY = {200010: 4, 200011: 5, 200012: 6}
# 公开聚合权威值(非 per-品质): 200009=总格(全库 94% value==真值总格), 200017=总件数(97%==真值件数)。
# 不符同样 = 结算不全的坏真值。用户 2026-06-27 追加: 公开总格也要查。
PUBLIC_EXACT_TOTAL_CELLS = 200009
PUBLIC_EXACT_TOTAL_COUNT = 200017


def public_exact_count_mismatches(events) -> list[dict[str, Any]]:
    """公开权威值 vs 结算真值的所有不符项(空 = 一致, 好样本):
      · per-品质精确件数 200018紫/200019金/200020红
      · 公开总格 200009 vs 结算总占格
      · 公开总件数 200017 vs 结算总件数
    一般公开信息只在 R1/R3 出现(作者 2026-06-27); round 一并返回供核查。"""
    settled = next((s for s in reversed(events.states) if getattr(s, "inventory_items", None)), None)
    if settled is None:
        return [{"info_id": -1, "reason": "no_settlement"}]
    # q0 件 = 抓包丢了品质字段的真藏品(非称号): 用 items.json 按 item_id 恢复品质/格子, 计入真值。
    # item_id 也未知(不在 items.json)的 = 无法恢复 -> unrecoverable -> 整样本坏真值。
    truth_q: dict[int, int] = defaultdict(int)
    truth_cells_q: dict[int, int] = defaultdict(int)
    cells = count = unrecoverable = 0
    for it in settled.inventory_items:
        q = int(getattr(it, "quality", 0) or 0)
        c = int(getattr(it, "cells", 0) or 0)
        if q <= 0:
            cat = item_meta(int(getattr(it, "item_id", 0) or 0))  # (value, shape, quality, name)
            if cat and int(cat[2]) > 0:
                q = int(cat[2])
                c = max(1, int(cat[1][0] or 1)) * max(1, int(cat[1][1] or 1))
            else:
                unrecoverable += 1
                continue
        truth_q[q] += 1
        truth_cells_q[q] += c
        count += 1
        cells += c
    pub_val: dict[int, int] = {}
    pub_round: dict[int, int] = {}
    watch = (set(PUBLIC_EXACT_COUNT_QUALITY) | set(PUBLIC_EXACT_CELLS_QUALITY)
             | {PUBLIC_EXACT_TOTAL_CELLS, PUBLIC_EXACT_TOTAL_COUNT})
    for st in events.states:
        for pi in getattr(st, "public_infos", None) or []:
            iid = int(getattr(pi, "info_id", 0) or 0)
            if iid in watch and getattr(pi, "value", None) is not None:
                pub_val[iid] = int(pi.value)
                if getattr(st, "round_index", None) is not None and iid not in pub_round:
                    pub_round[iid] = int(st.round_index)
    out: list[dict[str, Any]] = []
    if unrecoverable > 0:  # 真值不可补全(未知 item_id) -> 删
        out.append({"info_id": 0, "kind": "unrecoverable_item", "public": unrecoverable, "truth": 0, "round": None})
    for iid, q in PUBLIC_EXACT_COUNT_QUALITY.items():
        if iid in pub_val and pub_val[iid] != truth_q.get(q, 0):
            out.append({"info_id": iid, "kind": f"q{q}_count", "public": pub_val[iid],
                        "truth": truth_q.get(q, 0), "round": pub_round.get(iid)})
    for iid, q in PUBLIC_EXACT_CELLS_QUALITY.items():
        if iid in pub_val and pub_val[iid] != truth_cells_q.get(q, 0):
            out.append({"info_id": iid, "kind": f"q{q}_cells", "public": pub_val[iid],
                        "truth": truth_cells_q.get(q, 0), "round": pub_round.get(iid)})
    if PUBLIC_EXACT_TOTAL_CELLS in pub_val and pub_val[PUBLIC_EXACT_TOTAL_CELLS] != cells:
        out.append({"info_id": PUBLIC_EXACT_TOTAL_CELLS, "kind": "total_cells",
                    "public": pub_val[PUBLIC_EXACT_TOTAL_CELLS], "truth": cells,
                    "round": pub_round.get(PUBLIC_EXACT_TOTAL_CELLS)})
    if PUBLIC_EXACT_TOTAL_COUNT in pub_val and pub_val[PUBLIC_EXACT_TOTAL_COUNT] != count:
        out.append({"info_id": PUBLIC_EXACT_TOTAL_COUNT, "kind": "total_count",
                    "public": pub_val[PUBLIC_EXACT_TOTAL_COUNT], "truth": count,
                    "round": pub_round.get(PUBLIC_EXACT_TOTAL_COUNT)})
    return out


def public_exact_count_consistent(events) -> bool:
    """公开权威值(per-品质件数 + 总格 + 总件数)与结算真值是否全部一致(无不符 = 可信样本)。
    无任何公开权威值时视为通过(此判据无话可说, 不误杀)。"""
    return not public_exact_count_mismatches(events)


def normalized_truth(events) -> dict[str, Any]:
    """规范化真值: 逐件把长尾(金/红)替换成同形状期望、0格物剔除。
    返回 raw_value / norm_value / has_longtail / longtail(替换明细) / category。
    category: 'clean'(无长尾,真值原样) | 'normalized'(真值经估计替换) —— 供分块归档，
    避免估计值污染干净审查。"""
    state = next((s for s in reversed(events.states) if s.inventory_items), None)
    if state is None:
        return {"category": "invalid", "raw_value": 0, "norm_value": 0, "has_longtail": False, "longtail": []}
    raw = 0
    norm = 0
    tails: list[dict[str, Any]] = []
    per_q_norm: dict[int, int] = defaultdict(int)
    for it in state.inventory_items:
        iid = int(it.item_id)
        meta = item_meta(iid)
        q = meta[2] if meta else int(it.quality or 0)
        raw_v = meta[0] if meta else 0
        raw += raw_v
        nv = normalized_item_value(iid)
        norm += nv
        if q > 0:
            per_q_norm[q] += nv
        if meta and is_longtail(iid):
            tails.append({"name": meta[3], "quality": q, "shape": list(meta[1]),
                          "raw": raw_v, "replacement": nv})
    return {
        "category": "normalized" if tails else "clean",
        "raw_value": raw, "norm_value": norm,
        "has_longtail": bool(tails), "longtail": tails,
        "per_quality_norm": dict(per_q_norm),
    }


def classify_samples(rounds: int = 4) -> dict[str, Any]:
    """把 valid_aisha 样本分成 clean / normalized / invalid 三块，写明细。
    供后续「干净归档」只取 clean，或在审查时知道 normalized 的 value 是估计替换。"""
    samples = valid_aisha_samples(rounds)
    from build_clean_eval_set import sample_selection_metadata

    out: dict[str, Any] = {
        "_selection": sample_selection_metadata(
            samples, selector=f"clean_eval:aisha:rounds={rounds if rounds > 0 else 'all'}"
        ),
        "clean": [],
        "normalized": [],
        "invalid": [],
    }
    for s in samples:
        try:
            ev = _events(s)
            nt = normalized_truth(ev)
        except Exception:
            out["invalid"].append({"sample": s.name, "reason": "parse/truth error"})
            continue
        cat = nt["category"]
        if cat == "invalid":
            out["invalid"].append({"sample": s.name, "reason": "no settlement inventory"})
        elif cat == "normalized":
            out["normalized"].append({"sample": s.name, "raw_value": nt["raw_value"],
                                       "norm_value": nt["norm_value"], "longtail": nt["longtail"]})
        else:
            out["clean"].append({"sample": s.name, "value": nt["raw_value"]})
    return out


def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="校准核心自检 + 生成样本分块清单")
    ap.add_argument("--manifest", action="store_true", help="生成 clean/normalized/invalid 分块清单 json")
    ap.add_argument("--rounds", type=int, default=4)
    args = ap.parse_args()

    print(f"长尾阈值 = {LONGTAIL_MULTIPLE}x floor; 品质 {QUALITIES_WITH_LONGTAIL}")
    tails = longtail_catalog()
    print(f"长尾物共 {len(tails)} 个 (金{sum(1 for t in tails if t['quality']==5)} / 红{sum(1 for t in tails if t['quality']==6)}):")
    for t in tails[:8]:
        print(f"  {t['name'][:20]:20s} q{t['quality']} {t['value']//1000:>6}k -> {t['replacement']//1000}k  ({t['x_floor']}x)")
    if args.manifest:
        cls = classify_samples(args.rounds)
        out = ROOT / "scripts/calibration/sample_classification.json"
        out.write_text(json.dumps(cls, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n分块: clean={len(cls['clean'])}  normalized={len(cls['normalized'])}  invalid={len(cls['invalid'])}")
        print(f"清单 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
