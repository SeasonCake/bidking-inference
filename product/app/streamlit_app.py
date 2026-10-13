"""bidking-lab Streamlit playground.

Run locally:

    cd bidking-lab
    pip install -e ".[ui]"
    streamlit run app/streamlit_app.py

The app reads game tables from ``data/raw/tables`` (copied from the
installed game directory; see ``PROGRESS.md`` for the copy workflow)
and exposes four panels:

1. **\u8bfb\u6570\u8f93\u5165** — bucket-cell / value inputs (split per hero)
2. **\u51fa\u4ef7 hint** — conditional MC and analytical estimate cards
3. **\u8054\u5408\u7b5b\u9009** — top joint warehouse composition hypotheses
4. **\u9053\u5177 ROI** — leave-one-out ROI table for Ethan default kit
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

_SRC = Path(__file__).resolve().parent.parent / "src"
_APP = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from bidking_lab.extract.bid_map_table import BidMap, load_bid_map_table
from bidking_lab.extract.drop_table import DropPool, load_drop_table
from bidking_lab.extract.item_table import Item, load_item_table
from bidking_lab.inference.ground_truth import prepare_session_sampler
from bidking_lab.inference.display import Reading, parse_reading
from bidking_lab.inference.joint import candidates_for_bucket
from bidking_lab.inference.observation import (
    EXACT_VALUE_TOL_PCT,
    FALLBACK_VALUE_TOL_PCT,
    HUGE_CELLS_PER_QUALITY,
    JOINT_CONSTRAINT_RELAX_THRESHOLD,
    QualityBucketObs,
    SessionObs,
    _single_item_match_names,
    active_reading_constraint_count,
    lookup_single_item_value,
    recommended_warehouse_tolerance,
)
from chart_style import apply_bidking_chart_style, style_roi_barh, style_value_hist
from ui_loading import loading_slot, render_status_banner

apply_bidking_chart_style()


def _format_db_match_suffix(quality: int, value: int | None,
                            cells: int | None) -> str:
    """Build a "可能为 [name1, name2]" suffix using Item.txt lookup.

    Returns empty string if no value, no DB match, or quality has no items
    near the given value.
    """
    if value is None or value <= 0:
        return ""
    lookup = lookup_single_item_value(quality, value)
    if lookup.over_max:
        return ""
    tol = (
        EXACT_VALUE_TOL_PCT
        if lookup.tier == "exact"
        else FALLBACK_VALUE_TOL_PCT
    )
    matches = _single_item_match_names(
        quality, value, cells=cells, tol_pct=tol,
    )
    if not matches and lookup.tier == "none":
        matches = _single_item_match_names(
            quality, value, cells=cells, tol_pct=FALLBACK_VALUE_TOL_PCT,
        )
    if not matches:
        return ""
    names = [n for n, _, _ in matches[:3]]
    suffix = "\u3001".join(names)
    if len(matches) > 3:
        suffix += f"\u7b49 {len(matches)} \u4ef6"
    if lookup.ambiguous:
        return f" \u2014 \u53ef\u80fd\u4e3a\u4ee5\u4e0b\u4e4b\u4e00\uff1a**{suffix}**"
    return f" \u2014 \u53ef\u80fd\u4e3a **{suffix}**"


# Per-quality sanity: map「均价」误填成「总价」时提示（仅预览层）
_AVG_VALUE_LOOKS_LIKE_SUM: dict[int, int] = {
    4: 25_000,
    5: 80_000,
    6: 200_000,
}


def _avg_value_engine_hint(bucket: QualityBucketObs) -> str | None:
    """Explain how enumeration will treat ``bucket.avg_value`` (preview only)."""
    av = bucket.avg_value
    if av is None or av <= 0:
        return None
    from bidking_lab.inference.display import (
        avg_value_shows_fractional_cents,
        best_count_for_avg_value_integer_leak,
    )
    from bidking_lab.inference.observation import integer_leak_allowed_counts

    if bucket.value_sum is not None and bucket.value_sum > 0:
        return None
    if not avg_value_shows_fractional_cents(av):
        return (
            f"引擎按 **整数均价 {av:,.0f}** 处理（PCV 软约束，候选偏多）。"
            "若游戏显示小数请用 **文本框** 填全，如 `6328.75`。"
        )
    leak = integer_leak_allowed_counts(bucket, max_count=35)
    if not leak:
        return f"已解析 **{av}**；小数分未匹配到件数，仍用 PCV 软约束。"
    best = best_count_for_avg_value_integer_leak(av, max_count=35)
    sample = ", ".join(str(c) for c in sorted(leak)[:8])
    more = "…" if len(leak) > 8 else ""
    return (
        f"已解析 **{av:g}** → **小数分泄漏**：件数仅 `{sample}{more}`"
        + (f"，首选 **{best} 件**" if best else "")
        + "（非整数 `6328` 的五千解）。"
    )


def _render_candidate_preview(
    bucket: QualityBucketObs | None,
    warehouse_capacity: int,
    quality_label: str,
    *,
    other_known_cells: int = 0,
) -> None:
    """Show enumeration candidates or a confirmation for a bucket.

    Three render modes:
      1. **Fully pinned** (cells + count both given): green ✅ confirmation,
         optionally with DB-matched item name when count==1.
      2. **DB-confirmed top** (cands[0].is_db_matched): green ✅ "DB 命中"
         even if more candidates exist.
      3. **Multiple candidates**: blue 💡 with top-3 enumeration.
      4. **Single candidate**: green ✅ "已唯一锁定".
    """
    if bucket is None:
        return
    if bucket.total_cells == 0:
        st.info(
            f"\u2139\ufe0f **{quality_label} \u603b\u683c\u6570\u4e3a 0**\uff1a"
            "\u6309\u300c\u786e\u8ba4\u65e0\u8be5\u54c1\u8d28\u300d\u5904\u7406\uff0c\u4e0d\u505a\u683c\u6570\u679a\u4e3e\u3002"
            "\uff08\u82e5\u662f OCR \u6b8b\u7559\u7684 0\uff0c\u8bf7\u6e05\u7a7a\u603b\u683c\u6570\u6846\u6216\u91cd\u65b0\u6293\u5c4f\u3002\uff09"
        )
        return
    # Mode 1: fully pinned by user input — show confirmation directly.
    if bucket.total_cells is not None and bucket.count is not None:
        suffix = ""
        if bucket.count == 1 and bucket.value_sum:
            suffix = _format_db_match_suffix(
                bucket.quality, bucket.value_sum, cells=bucket.total_cells
            )
        st.success(
            f"\u2705 **{quality_label} \u5df2\u5b8c\u6574\u9501\u5b9a**\uff1a"
            f"`{bucket.total_cells}` \u683c / `{bucket.count}` \u4ef6"
            + suffix
        )
        return
    # Only meaningful when some reading constraint exists.
    if (bucket.avg_cells is None and bucket.value_sum is None
            and bucket.total_cells is None and bucket.count is None
            and bucket.avg_value is None):
        return
    from bidking_lab.inference.observation import relax_bucket_for_enumeration_preview

    budget = max(0, warehouse_capacity - other_known_cells)
    if warehouse_capacity > 0 and other_known_cells > 0:
        st.caption(
            f"\u2139\ufe0f **{quality_label} \u679a\u4e3e\u683c\u6570\u4e0a\u9650**"
            f"\uff1a\u4ed3\u5e93 `{warehouse_capacity}` \u2212 "
            f"\u5df2\u586b\u4f4e\u54c1 `{other_known_cells}` "
            f"= **\u81f3\u591a `{budget}` \u683c**"
            "\uff08\u4ec5\u7edf\u8ba1\u5df2\u586b\u603b\u683c\u6570\u7684\u54c1\u8d28\uff09\u3002"
        )
    if (
        bucket.avg_value is not None
        and bucket.avg_value > 0
        and (bucket.value_sum is None or bucket.value_sum <= 0)
    ):
        cap = _AVG_VALUE_LOOKS_LIKE_SUM.get(bucket.quality, 99_999)
        if bucket.avg_value > cap:
            st.warning(
                f"\u26a0\ufe0f **{quality_label}\u5747\u4ef7** `{bucket.avg_value:,.0f}` "
                "\u504f\u5927\uff0c\u66f4\u50cf\u300c\u603b\u4f30\u503c\u300d\u3002"
                f"\u8bf7\u6539\u586b **{quality_label}\u603b\u4f30\u503c**\uff1b"
                "\u5747\u4ef7\u5e94\u4e3a\u6bcf\u4ef6 silver\uff08\u901a\u5e38\u8fdc\u5c0f\u4e8e\u603b\u4ef7\uff09\u3002"
            )
        else:
            _hint = _avg_value_engine_hint(bucket)
            if _hint:
                st.caption(f"\u2139\ufe0f {_hint}")

    bucket, _ocr_dropped = relax_bucket_for_enumeration_preview(
        bucket,
        warehouse_capacity=warehouse_capacity,
        other_known_cells=other_known_cells,
    )
    if _ocr_dropped:
        st.caption(
            "\u2139\ufe0f \u4e0b\u65b9\u679a\u4e3e\u5df2\u5ffd\u7565\u4e0e\u603b\u683c\u51b2\u7a81\u7684 OCR \u6b8b\u7559\u7ea6\u675f\uff08"
            + ", ".join(_ocr_dropped)
            + "\uff09\uff1b**\u4e0d\u5f71\u54cd** MC \u8fc7\u6ee4\u3002"
        )
    n_constraints = active_reading_constraint_count(bucket)
    if n_constraints >= JOINT_CONSTRAINT_RELAX_THRESHOLD:
        st.caption(
            f"\u2139\ufe0f \u5df2\u586b\u5199 {n_constraints} \u9879\u7ea6\u675f\uff1b"
            "\u5747\u4ef7\u5bb9\u5dee\u5df2\u81ea\u52a8\u653e\u5bbd\uff08\u4ec5\u5f71\u54cd\u4e0b\u65b9\u5019\u9009\u9884\u89c8\uff0c"
            "**\u4e0d\u6539\u53d8** \u4e0a\u65b9 MC \u4ed3\u5e93\u4ef7\u503c\u533a\u95f4 / bucket \u540e\u9a8c\uff09\u3002"
        )
    try:
        cands = candidates_for_bucket(
            bucket,
            warehouse_capacity=warehouse_capacity,
            other_known_cells=other_known_cells,
        )
    except Exception as exc:                                # noqa: BLE001
        st.warning(f"{quality_label} \u5019\u9009\u679a\u4e3e\u51fa\u9519: {exc}")
        return
    if (
        len(cands) == 1
        and cands[0].total_cells <= 0
        and bucket.total_cells is None
    ):
        st.warning(
            f"\u26a0\ufe0f {quality_label}\uff1a\u4ec5\u6709\u5747\u4ef7/\u5747\u683c\u7b49\u6b8b\u7559\u7ea6\u675f\uff0c"
            "\u8bf7\u5148\u586b **\u603b\u683c\u6570** \u6216\u6e05\u7a7a\u5747\u4ef7\u540e\u518d\u770b\u679a\u4e3e\u3002"
        )
        return
    if not cands:
        if warehouse_capacity <= 0:
            st.warning(
                f"\u26a0\ufe0f {quality_label}\uff1a\u4ed3\u5e93\u603b\u683c\u6570\u672a\u751f\u6548\uff0c"
                "\u5019\u9009\u679a\u4e3e\u6682\u65f6\u6ca1\u6709\u53ef\u7528\u5bb9\u91cf\u4e0a\u9650\u3002"
                "\u8bf7\u786e\u8ba4\u5de6\u4fa7\u300c\u4ed3\u5e93\u603b\u683c\u6570\u300d\u6709\u503c\uff0c"
                "\u6216\u91cd\u65b0\u6293\u5c4f\u3002"
            )
            return
        st.warning(
            f"\u26a0\ufe0f {quality_label}\uff1a\u5f53\u524d\u7ea6\u675f\u4e0b\u65e0\u5408\u6cd5\u5019\u9009\u3002"
            "\u68c0\u67e5\u662f\u5426\u8bef\u586b\u5747\u683c\uff08\u4f8b 4.00 \u4ec5\u5728\u88ab\u820d\u5165\u65f6\u51fa\u73b0\uff0c"
            "\u5982\u679c\u603b\u683c=12 \u4e14\u5747\u683c=4 \u4e94\u4e94 \u5e94\u586b\u300c4\u300d\u800c\u975e\u300c4.00\u300d\uff09\u3002"
        )
        return
    top = cands[:3]
    lines = [
        f"`{c.total_cells}` \u683c / `{c.count}` \u4ef6  "
        f"(\u5747\u683c {c.total_cells/c.count:.4f}, composite={c.composite:.3f})"
        for c in top
    ]
    msg = "  \u00b7  ".join(lines)
    # Mode 2: DB-matched top candidate → green ✅ even if more candidates exist.
    if bucket.value_sum:
        _lu = lookup_single_item_value(bucket.quality, bucket.value_sum)
        if _lu.ambiguous:
            suffix = _format_db_match_suffix(
                bucket.quality, bucket.value_sum, cells=None,
            )
            st.info(
                f"\u2139\ufe0f **{quality_label} \u4ef7\u683c\u547d\u4e2d\u591a\u4ef6\u540c\u4ef7\u7269**\uff0c"
                f"\u672a\u81ea\u52a8\u9501\u5b9a\u683c\u6570\uff1b\u8bf7\u7ed3\u5408\u626b\u63cf/\u5747\u683c\u3002"
                + (suffix or "")
            )
    if cands[0].is_db_matched and bucket.value_sum:
        suffix = _format_db_match_suffix(
            bucket.quality, bucket.value_sum, cells=cands[0].total_cells
        )
        tail = ""
        if len(lines) > 1:
            tail = "\u3002\u540e\u7eed\u5907\u9009\uff1a " + "  \u00b7  ".join(lines[1:])
        st.success(
            f"\u2705 **{quality_label} DB \u5355\u4ef6\u547d\u4e2d**\uff1a"
            f"{lines[0]}"
            + suffix
            + tail
        )
        return
    # Mode 3 / 4: original info / success based on len(cands).
    if len(cands) > 1:
        st.info(
            f"\U0001F4A1 **{quality_label} \u5f15\u64ce\u679a\u4e3e\u51fa "
            f"{len(cands)} \u79cd\u53ef\u80fd\u89e3**\uff0c\u6309\u8054\u5408\u8bc4\u5206\u6392\u5e8f\uff1a "
            + msg
            + "\u3002\u586b\u5165\u603b\u683c\u6570 / \u4ef6\u6570 / \u4ef7\u503c \u53ef\u8fdb\u4e00\u6b65\u9501\u5b9a\u3002"
        )
    else:
        st.success(
            f"\u2705 **{quality_label} \u5df2\u552f\u4e00\u9501\u5b9a**\uff1a{lines[0]}"
        )


def _try_parse_reading(text: str | None) -> Reading | None:
    """Parse '2.90' into a Reading; empty/whitespace/invalid → None.

    UI shows a soft warning if invalid; the caller decides whether to surface.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        return parse_reading(s)
    except ValueError:
        return None


def _try_parse_silver_amount(raw: str | float | int | None) -> float | None:
    """Parse per-item silver price; supports decimals (e.g. 32507.6)."""
    if raw is None:
        return None
    from bidking_lab.capture.parser import parse_silver_amount

    try:
        v = parse_silver_amount(raw)
    except (ValueError, TypeError):
        return None
    return v if v > 0 else None
from bidking_lab.inference.roi import compute_tool_roi
from bidking_lab.inference.snipe import (
    compute_pass_recommendation,
    compute_snipe_recommendation,
)


# ---------- Constants ----------

# Snipe / pass recommendation cards: backend in ``inference/snipe.py`` is kept
# for future work but UI is off until tier logic is stable (see OBSERVATIONS
# Checkpoint #31, TROUBLESHOOTING #30). Same policy as unknown-quality huge.
_ENABLE_SNIPE_PASS_HINTS = False
_MC_TRIALS_AUTO_AFTER_CAPTURE = 3000
_MC_TRIALS_MANUAL_DEFAULT = 3000

# Map categories by ID prefix.
# 24xx/34xx/44xx = mansion (\u522b\u5885), 25xx/35xx/45xx = shipwreck (\u6c89\u8239).
# Each category has 30 variants across 3 difficulty tiers.
MAP_CATEGORIES: dict[str, tuple[str, ...]] = {
    "mansion": ("24", "34", "44"),
    "shipwreck": ("25", "35", "45"),
}

CATEGORY_LABELS: dict[str, str] = {
    "mansion": "\U0001F3DB\uFE0F \u522b\u5885",
    "shipwreck": "\U0001F6A2 \u6c89\u8239",
}

# Difficulty tier labels (first digit of map_id).
TIER_LABELS: dict[str, str] = {"2": "T2", "3": "T3", "4": "T4"}

HUGE_BANDS = ("none", "1", "2-3", "4+")
HUGE_BAND_LABELS: dict[str, str] = {
    "none": "\u65e0",
    "1": "1\u4e2a",
    "2-3": "2-3\u4e2a",
    "4+": "4\u4e2a\u53ca\u4ee5\u4e0a",
}


def _shape_to_cells(shape: str) -> int:
    """Parse a BIG_ITEMS_BY_SHAPE key like '3\u00d74 = 12 \u683c' to 12."""
    try:
        return int(shape.split("=")[1].strip().split()[0])
    except (IndexError, ValueError):
        return 0


def _items_for_quality(q: int) -> list[dict]:
    """Items eligible as a "huge / big" item for this quality.

    Filters by per-quality cell threshold: purple ≥10, gold/red ≥12.
    A 5×2=10 gold item (e.g. 巴雷特狙击枪) does not count as gold huge —
    only purple consumes the relaxed 10-cell row.
    """
    threshold = HUGE_CELLS_PER_QUALITY.get(q, 12)
    out: list[dict] = []
    for shape, cands in BIG_ITEMS_BY_SHAPE.items():
        cells = _shape_to_cells(shape)
        if cells < threshold:
            continue
        for c in cands:
            if c["q"] == q:
                out.append({"name": c["name"], "cells": cells, "value": c["value"]})
    out.sort(key=lambda x: (x["cells"], -x["value"]))
    return out


_HUGE_SELECTBOX_HELP_TAIL = (
    "只选「1个/2-3个/4+」、不选★时：枚举/分析按该品质**最小巨物占格**"
    "（紫10、金/红12）作下限；游艇等须选★。"
    "件数 band 会进 MC；★格数主要影响下方候选枚举，不进 MC 单件格数过滤。"
)


def _huge_options_for_quality(q: int) -> tuple[list[str], dict[str, str]]:
    """Returns (options_list, labels_map) for the huge-band selectbox.

    Extends HUGE_BANDS with ``item:<name>`` keys for every concretely
    identifiable huge item of this quality from BIG_ITEMS_BY_SHAPE.
    """
    options: list[str] = list(HUGE_BANDS)
    labels: dict[str, str] = dict(HUGE_BAND_LABELS)
    for item in _items_for_quality(q):
        key = f"item:{item['name']}"
        options.append(key)
        labels[key] = (
            f"\u2605 {item['name']} ({item['cells']}\u683c\u00b7{item['value']:,})"
        )
    return options, labels


def _resolve_huge_selection(raw: str, quality: int) -> tuple[str, int]:
    """Map UI selection string to ``(huge_band, huge_cells_override)``.

    * ``"none"`` / ``"1"`` / ``"2-3"`` / ``"4+"`` → unchanged, override=0.
    * ``"item:<name>"`` → ``("1", item.cells)`` if name matches a known
      huge item of this quality; else falls back to ``("none", 0)``.
    """
    if not raw:
        return "none", 0
    if not raw.startswith("item:"):
        return raw, 0
    name = raw[len("item:"):]
    for item in _items_for_quality(quality):
        if item["name"] == name:
            return "1", item["cells"]
    return "none", 0

HERO_LABELS: dict[str, str] = {
    "ethan": "\u4f0a\u68ee (Ethan)",
    "aisha": "\u827e\u838e (Aisha)",
}

# Big-item dictionary keyed by shape (>= 12 cells).
# Auto-derived from Item.txt; surfaced in the UI as a shape-lookup expander.
# Items marked "is_unique" are the sole candidate for their (shape, quality)
# pair, so identifying the shape pins down the item exactly. The rest are
# multi-candidate (3-5 items at the same shape+quality) and the player must
# use other tools / context to discriminate.
QUALITY_NAME: dict[int, str] = {
    1: "\u767d", 2: "\u7eff", 3: "\u84dd",
    4: "\u7d2b", 5: "\u91d1", 6: "\u7ea2",
}

BIG_ITEMS_BY_SHAPE: dict[str, list[dict]] = {
    "5\u00d72 = 10 \u683c": [
        # Purple uses relaxed ≥10 threshold; gold/red still ≥12 so the
        # gold 巴雷特 here will NOT appear in gold's huge dropdown.
        {"q": 4, "name": "\u52a0\u7279\u6797\u91cd\u673a\u67aa", "value": 31_688, "unique": False},
        {"q": 5, "name": "\u5df4\u96f7\u7279\u53cd\u5668\u6750\u72d9\u51fb\u6b65\u67aa", "value": 67_600, "unique": False},
    ],
    "3\u00d74 = 12 \u683c": [
        {"q": 2, "name": "\u7535\u52a8\u4e09\u8f6e\u8f66", "value": 5129, "unique": True},
        {"q": 4, "name": "\u53ef\u6298\u53e0\u9ad8\u97e7\u6027\u9632\u62a4\u76fe", "value": 20082, "unique": True},
        {"q": 5, "name": "\u91cd\u578b\u5168\u751f\u6001\u4f5c\u6218\u9632\u5f39\u8863", "value": 74745, "unique": False},
        {"q": 5, "name": "\u6ce2\u65af\u6bef", "value": 85800, "unique": False},
        {"q": 5, "name": "\u5168\u81ea\u52a8\u751f\u5316\u5206\u6790\u4eea", "value": 86130, "unique": False},
        {"q": 5, "name": "\u65e0\u4eba\u4f5c\u6218\u8f66", "value": 93753, "unique": False},
        {"q": 6, "name": "\u5355\u5175\u5916\u9aa8\u9abc\u52a9\u529b\u7cfb\u7edf", "value": 305920, "unique": False},
        {"q": 6, "name": "\u91cd\u578b\u5de1\u822a\u6469\u6258\u8f66", "value": 357040, "unique": False},
        {"q": 6, "name": "\u76f8\u63a7\u9635\u96f7\u8fbe", "value": 1003000, "unique": False},
    ],
    "3\u00d75 = 15 \u683c": [
        {"q": 3, "name": "\u5c0f\u578b\u9762\u5305\u8f66", "value": 14659, "unique": True},
        {"q": 5, "name": "\u670d\u52a1\u5668\u673a\u67dc", "value": 97382, "unique": True},
        {"q": 6, "name": "\u65f6\u5c1a\u8f7f\u8dd1", "value": 293400, "unique": False},
        {"q": 6, "name": "\u5927\u76ee\u91d1\u67aa\u9c7c", "value": 294000, "unique": False},
        {"q": 6, "name": "GPU\u8ba1\u7b97\u67dc", "value": 375000, "unique": False},
        {"q": 6, "name": "\u6c11\u7528\u5782\u76f4\u8d77\u964d\u98de\u884c\u5668", "value": 452800, "unique": False},
        {"q": 6, "name": "\u84dd\u9c4d\u91d1\u67aa\u9c7c", "value": 1552500, "unique": False},
    ],
    "4\u00d74 = 16 \u683c": [
        {"q": 3, "name": "\u77f3\u72ee\u5b50", "value": 9168, "unique": True},
        {"q": 5, "name": "\u8f7b\u91cf\u5316\u9502\u7535\u6c60", "value": 199900, "unique": True},
        {"q": 6, "name": "\u7ea2\u6728\u5c4f\u98ce", "value": 361000, "unique": False},
        {"q": 6, "name": "\u78b3\u7ea4\u7ef4\u5355\u4f53\u58f3\u8f66\u8eab", "value": 444000, "unique": False},
        {"q": 6, "name": "\u7fe1\u7fe0\u5c4f\u98ce", "value": 844000, "unique": False},
    ],
    "3\u00d76 = 18 \u683c": [
        {"q": 5, "name": "\u5355\u4eba\u90ca\u6e38\u5feb\u8247\uff08\u6e38\u8247\uff09", "value": 106500,
         "unique": True},
    ],
    "4\u00d75 = 20 \u683c": [
        {"q": 3, "name": "\u5899\u9762\u6d82\u9e26\u5899", "value": 8880, "unique": True},
    ],
}

# Tool-naming convention (verified against BattleItem.txt 2026-05-15):
#   普品 = q1+q2 white+green | 良品 = q3 blue | 优品 = q4 purple
#   极品 = q5 gold           | 珍品 = q6 red
# Tool's silver-cost tier is independent of target quality:
#   优品* tools cost ~20K (blue-tier), 极品* cost ~35K (purple-tier),
#   珍品* cost ~50K+ (gold-tier).
ETHAN_KIT = ("\u666e\u54c1\u626b\u63cf", "\u826f\u54c1\u626b\u63cf",
             "\u4f18\u54c1\u4f30\u4ef7", "\u4f18\u54c1\u5747\u683c",
             "\u6781\u54c1\u4f30\u4ef7")

# Aisha 通过 R1-R4 轮廓免费拿到白/绿/蓝/紫 cells + count（include_aisha_outline=True
# 在 ROI 引擎里把这层加进去）。所以她带的"道具"主要是给 q=5 加价格 +
# 总仓储那种 SessionObs 级别 hint。
AISHA_KIT = ("\u6781\u54c1\u4f30\u4ef7",         # 极品估价 → 金品总价
             "\u603b\u4ed3\u50a8\u7a7a\u95f4")    # 总仓储空间 → warehouse cells

# All tools the ROI tab can evaluate. Pinned to those with concrete
# engine wiring in synth_readings.TOOL_SPECS / SESSION_TOOL_SPECS.
ALL_TOOLS = (
    "\u666e\u54c1\u626b\u63cf",      # 普品扫描 white+green scan, 1,200
    "\u826f\u54c1\u626b\u63cf",      # 良品扫描 blue scan, 2,500
    "\u4f18\u54c1\u626b\u63cf",      # 优品扫描 purple scan, 20,000
    "\u4f18\u54c1\u4f30\u4ef7",      # 优品估价 purple appraise, 20,000
    "\u4f18\u54c1\u5747\u683c",      # 优品均格 purple avg-cells, 20,000
    "\u6781\u54c1\u626b\u63cf",      # 极品扫描 gold scan, 35,000
    "\u6781\u54c1\u4f30\u4ef7",      # 极品估价 gold appraise, 35,000
    "\u6781\u54c1\u5747\u683c",      # 极品均格 gold avg-cells, 35,000
    "\u73cd\u54c1\u626b\u63cf",      # 珍品扫描 red scan, 50,000
    "\u73cd\u54c1\u4f30\u4ef7",      # 珍品估价 red appraise, 50,000
    "\u603b\u4ed3\u50a8\u7a7a\u95f4", # 总仓储空间 warehouse total, 55,000
)
# English aliases for chart labels (matplotlib + Chinese in Streamlit
# is a known font-fallback nightmare; English keeps the chart readable).
TOOL_EN_LABEL = {
    "\u666e\u54c1\u626b\u63cf": "White Scan",
    "\u826f\u54c1\u626b\u63cf": "Green Scan",
    "\u4f18\u54c1\u626b\u63cf": "Purple Scan",
    "\u4f18\u54c1\u4f30\u4ef7": "Purple Appraise",
    "\u4f18\u54c1\u5747\u683c": "Purple AvgCells",
    "\u6781\u54c1\u626b\u63cf": "Gold Scan",
    "\u6781\u54c1\u4f30\u4ef7": "Gold Appraise",
    "\u6781\u54c1\u5747\u683c": "Gold AvgCells",
    "\u73cd\u54c1\u626b\u63cf": "Red Scan",
    "\u73cd\u54c1\u4f30\u4ef7": "Red Appraise",
    "\u603b\u4ed3\u50a8\u7a7a\u95f4": "Warehouse Size",
}
# Default prices (from observation.TOOL_PRICE_BY_RARITY); user can
# override prices for blue+ on the fly.
TOOL_DEFAULT_PRICE = {
    "\u666e\u54c1\u626b\u63cf":  1_200,
    "\u826f\u54c1\u626b\u63cf":  2_500,
    "\u4f18\u54c1\u626b\u63cf":  20_000,
    "\u4f18\u54c1\u4f30\u4ef7":  20_000,
    "\u4f18\u54c1\u5747\u683c":  20_000,
    "\u6781\u54c1\u626b\u63cf":  35_000,
    "\u6781\u54c1\u4f30\u4ef7":  35_000,
    "\u6781\u54c1\u5747\u683c":  35_000,
    "\u73cd\u54c1\u626b\u63cf":  50_000,
    "\u73cd\u54c1\u4f30\u4ef7":  50_000,
    "\u603b\u4ed3\u50a8\u7a7a\u95f4": 55_000,
}
# Blue tier and above can have player-overridden prices (white/green
# are stable cheap items).
TOOL_PRICE_OVERRIDABLE = {
    "\u4f18\u54c1\u626b\u63cf", "\u4f18\u54c1\u4f30\u4ef7", "\u4f18\u54c1\u5747\u683c",
    "\u6781\u54c1\u626b\u63cf", "\u6781\u54c1\u4f30\u4ef7", "\u6781\u54c1\u5747\u683c",
    "\u73cd\u54c1\u626b\u63cf", "\u73cd\u54c1\u4f30\u4ef7",
    "\u603b\u4ed3\u50a8\u7a7a\u95f4",
}


# ---------- Data loading (cached) ----------

@st.cache_resource
def _load_tables() -> tuple[
    Mapping[int, BidMap], Mapping[int, DropPool], Mapping[int, Item]
]:
    tables_in = Path(__file__).resolve().parent.parent / "data" / "raw" / "tables"
    maps = load_bid_map_table(tables_in / "BidMap.txt")
    drops = load_drop_table(tables_in / "Drop.txt")
    items = load_item_table(tables_in / "Item.txt")
    return maps, drops, items


@st.cache_data(max_entries=16, show_spinner=False)
def _sample_truths_cached(map_id: int, *, n_trials: int, seed: int) -> list:
    """Cache MC samples for a (map_id, n_trials, seed) tuple.

    Sampling 2000 sessions takes ~3s; without this cache, every Streamlit
    rerun triggered by an unrelated widget would re-sample the entire batch.
    """
    maps, drops, items = _load_tables()
    rng = np.random.default_rng(seed)
    sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
    return [sampler.sample(rng=rng) for _ in range(n_trials)]


# ---------- Helpers ----------

def _maps_for_category(
    maps: Mapping[int, BidMap], category: str
) -> dict[int, str]:
    """Return ``{map_id: 'T<tier> map_id - name'}`` for maps matching prefixes."""
    prefixes = MAP_CATEGORIES[category]
    out: dict[int, str] = {}
    for mid in sorted(maps.keys()):
        s = str(mid)
        if s[:2] in prefixes:
            tier = TIER_LABELS.get(s[0], s[0])
            name = maps[mid].name.replace(chr(0x200b), "")
            out[mid] = f"[{tier}] {mid} \u2014 {name}"
    return out


def _map_select_widget_key() -> str:
    rev = int(st.session_state.get("obs_map_select_rev", 0))
    return f"obs_map_select__r{rev}"


def _bump_map_select_widget() -> None:
    st.session_state["obs_map_select_rev"] = (
        int(st.session_state.get("obs_map_select_rev", 0)) + 1
    )


def _sync_map_select_widget_value(map_id: int | None) -> None:
    """Write map id into the current versioned selectbox key after a rev bump."""
    mk = _map_select_widget_key()
    if map_id is not None:
        st.session_state[mk] = map_id
    else:
        st.session_state.pop(mk, None)


def _resolved_map_select(map_choices: dict[int, str]) -> int | None:
    """Current map id from versioned selectbox key (int only)."""
    raw = st.session_state.get(_map_select_widget_key())
    if raw is None:
        raw = st.session_state.get("obs_map_select")
    return _coerce_map_select(raw, map_choices)


def _effective_map_id(
    map_choices: dict[int, str],
    *,
    selectbox_return: object = None,
    obs: dict | None = None,
) -> int | None:
    """Resolve map id without treating a transient ``selectbox`` return of None as cleared.

    On tab switches the widget ``key`` and ``obs['map_id']`` often still hold the
  choice while the selectbox return value is briefly None — using only the return
  used to call ``reset_obs_for_manual_map_change`` and wipe readings.
    """
    obs = obs or {}
    for raw in (
        st.session_state.get(_map_select_widget_key()),
        selectbox_return,
        st.session_state.get("obs_map_select"),
        obs.get("map_id"),
        st.session_state.get("_tracked_map_id"),
    ):
        mid = _coerce_map_select(raw, map_choices)
        if mid is not None:
            return mid
    return None


def _coerce_map_select(
    raw: object,
    map_choices: dict[int, str],
) -> int | None:
    """Normalize obs_map_select (int id, label str, or legacy formatted text)."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw in map_choices else None
    if isinstance(raw, str):
        s = raw.strip()
        if s in map_choices.values():
            for mid, label in map_choices.items():
                if label == s:
                    return mid
        try:
            mid = int(s)
            return mid if mid in map_choices else None
        except ValueError:
            pass
        m = re.search(r"\b(\d{4})\b", s)
        if m:
            mid = int(m.group(1))
            return mid if mid in map_choices else None
    return None


def _warehouse_cells_is_estimate(state: dict[str, Any]) -> bool:
    return (
        state.get("hero") == "ethan"
        and str(state.get("warehouse_cells_mode") or "").strip().lower()
        == "estimate"
    )


def _warehouse_cells_tolerance(state: dict[str, Any]) -> int:
    if not _warehouse_cells_is_estimate(state):
        return 0
    try:
        return max(0, int(state.get("warehouse_cells_tolerance") or 0))
    except (TypeError, ValueError):
        return 0


def _maybe_red_bucket(state) -> QualityBucketObs | None:
    """Build a q=6 bucket only when something meaningful was entered.

    Treats lo/hi == 0 as "not given" (since 0 is the streamlit number_input
    default) and skips creating a value_range with zero bounds — passing
    (0, 0) to ``value_consistency_score`` triggers a ZeroDivisionError.

    The 2026-05-16 redesign adds two new gates:

    * ``red_confirmed_none``: when True, the player asserts "I have
      accounted for every cell in the warehouse and there is no red
      item" → returns a bucket with ``total_cells=0``, ``count=0``,
      ``huge_band="none"``. This is the dominant fix for the
      over-estimation bug.
    * ``small_warehouse_confirmed``: when True, the player confirms
      this is a small warehouse → cap red cells to ~5% of warehouse.
    * ``red_cells_total``: explicit reading (typically from 珍品扫描
      or a map hint). 0 means "not provided" unless
      ``red_confirmed_none`` is True.
    """
    confirmed_none = bool(state.get("red_confirmed_none"))
    if confirmed_none:
        return QualityBucketObs(
            quality=6, total_cells=0, count=0, huge_band="none",
        )
    small_wh = bool(state.get("small_warehouse_confirmed"))
    if small_wh:
        warehouse = int(state.get("warehouse_cells") or 0)
        red_cap = max(2, warehouse // 20)
        return QualityBucketObs(
            quality=6, total_cells=red_cap,
        )
    lo = int(state.get("red_value_lo") or 0)
    hi = int(state.get("red_value_hi") or 0)
    huge_raw = state.get("red_huge_band", "none")
    huge_band, huge_override = _resolve_huge_selection(huge_raw, 6)
    cells_raw = state.get("red_cells_total")
    cells = int(cells_raw) if cells_raw is not None else None
    has_value = lo > 0 and hi > 0 and hi >= lo
    has_huge = huge_band != "none"
    has_cells = cells is not None
    if not (has_value or has_huge or has_cells):
        return None
    return QualityBucketObs(
        quality=6,
        total_cells=cells if has_cells else None,
        value_range=(lo, hi) if has_value else None,
        huge_band=huge_band,
        huge_cells_override=huge_override,
    )


def _maybe_gold_bucket(
    state,
    *,
    allow_huge: bool,
    ui_state: Any | None = None,
) -> QualityBucketObs | None:
    if ui_state is not None:
        from bidking_lab.capture.apply import (
            effective_number_field_for_preview,
            effective_text_field_for_preview,
        )

        cells_raw = effective_number_field_for_preview(
            state, ui_state,
            obs_key="gold_cells", base_widget_key="obs_reading_gold_cells",
        )
        count_raw = effective_number_field_for_preview(
            state, ui_state,
            obs_key="gold_count", base_widget_key="obs_reading_gold_count",
        )
        value_raw = effective_number_field_for_preview(
            state, ui_state,
            obs_key="gold_value", base_widget_key="obs_reading_gold_value",
        )
        avg_value_raw = effective_text_field_for_preview(
            state, ui_state,
            obs_key="gold_avg_value", base_widget_key="gold_avg_value_widget",
        )
        avg_raw = effective_text_field_for_preview(
            state, ui_state,
            obs_key="gold_avg_raw", base_widget_key="gold_avg_raw_widget",
        )
        value = int(value_raw or 0)
    else:
        value = int(state.get("gold_value") or 0)
        cells_raw = state.get("gold_cells")
        count_raw = state.get("gold_count")
        avg_value_raw = state.get("gold_avg_value")
        avg_raw = state.get("gold_avg_raw")
    cells = int(cells_raw) if cells_raw is not None else None
    count = int(count_raw) if count_raw is not None and int(count_raw) > 0 else None
    avg_value = _try_parse_silver_amount(avg_value_raw)
    avg = _try_parse_reading(avg_raw) if avg_raw else None
    huge_raw = state.get("gold_huge_band", "none") if allow_huge else "none"
    huge_band, huge_override = _resolve_huge_selection(huge_raw, 5)
    has_any = (
        value > 0
        or cells is not None
        or count is not None
        or avg is not None
        or avg_value is not None
        or huge_band != "none"
    )
    if not has_any:
        return None
    return QualityBucketObs(
        quality=5,
        total_cells=cells if cells is not None and cells >= 0 else None,
        count=count if count is not None and count > 0 else None,
        avg_cells=avg,
        value_sum=value if value > 0 else None,
        avg_value=avg_value,
        huge_band=huge_band,
        huge_cells_override=huge_override,
    )


def _mc_active_bucket_qs(state: dict, hero: str) -> list[int]:
    """Quality buckets actually passed into MC (for UI + debug)."""
    if hero == "ethan":
        return sorted(_build_buckets_for_ethan(state).keys())
    return sorted(_build_buckets_for_aisha(state).keys())


def _format_mc_readings_summary(state: dict, hero: str) -> str:
    """Human-readable list of non-empty readings feeding MC filters."""
    _parts: list[str] = []

    def _add(label: str, *keys: str) -> None:
        vals = []
        for k in keys:
            v = state.get(k)
            if v is None or v == "" or v == 0:
                continue
            if k.endswith("_band") and v == "none":
                continue
            vals.append(f"{k}={v}")
        if vals:
            _parts.append(f"**{label}**: " + ", ".join(vals))

    _add("\u767d+\u7eff", "wg_cells", "white_cells", "green_cells")
    _add("\u84dd", "blue_cells", "blue_count")
    _add(
        "\u7d2b",
        "purple_cells", "purple_count", "purple_avg_raw",
        "purple_value", "purple_avg_value", "purple_huge_band",
    )
    _add(
        "\u91d1",
        "gold_cells", "gold_count", "gold_avg_raw",
        "gold_value", "gold_avg_value", "gold_huge_band",
    )
    _add(
        "\u7ea2",
        "red_cells_total", "red_value_lo", "red_value_hi",
        "red_huge_band", "red_confirmed_none",
    )
    if state.get("total_item_count"):
        _parts.append(f"**\u603b\u4ef6\u6570**: {state['total_item_count']}")
    qs = _mc_active_bucket_qs(state, hero)
    _parts.append(f"**MC bucket q**: {qs or '（仅仓库容量）'}")
    return "\u00a0\u00b7\u00a0".join(_parts) if _parts else "\u4ec5\u4ed3\u5e93\u683c\u6570 / \u5730\u56fe\u7ea6\u675f"


def _build_buckets_for_ethan(state) -> dict[int, QualityBucketObs]:
    buckets: dict[int, QualityBucketObs] = {}
    if state.get("wg_cells") is not None and state["wg_cells"] > 0:
        buckets[1] = QualityBucketObs(
            quality=1, total_cells=state["wg_cells"],
        )
    if state.get("blue_cells") is not None and state["blue_cells"] > 0:
        buckets[3] = QualityBucketObs(
            quality=3, total_cells=state["blue_cells"],
        )
    purple_cells_raw = state.get("purple_cells")
    purple_count_raw = state.get("purple_count")
    purple_avg_value_raw = state.get("purple_avg_value")
    purple_cells_in = int(purple_cells_raw) if purple_cells_raw is not None else None
    purple_count_in = int(purple_count_raw) if purple_count_raw is not None else None
    purple_avg_value = _try_parse_silver_amount(purple_avg_value_raw)
    purple_avg = _try_parse_reading(state.get("purple_avg_raw"))
    purple_value = state.get("purple_value") or 0
    purple_huge_raw = state.get("purple_huge_band", "none")
    purple_huge, purple_huge_override = _resolve_huge_selection(purple_huge_raw, 4)
    has_purple = (
        purple_cells_in is not None
        or (purple_count_in is not None and purple_count_in > 0)
        or purple_avg is not None
        or purple_value > 0
        or purple_avg_value is not None
        or purple_huge != "none"
    )
    if has_purple:
        buckets[4] = QualityBucketObs(
            quality=4,
            total_cells=purple_cells_in if purple_cells_in is not None and purple_cells_in >= 0 else None,
            count=purple_count_in if purple_count_in is not None and purple_count_in > 0 else None,
            avg_cells=purple_avg,
            value_sum=purple_value if purple_value > 0 else None,
            avg_value=purple_avg_value,
            huge_band=purple_huge,
            huge_cells_override=purple_huge_override,
        )
    gold = _maybe_gold_bucket(state, allow_huge=True)
    if gold is not None:
        buckets[5] = gold
    red = _maybe_red_bucket(state)
    if red is not None:
        buckets[6] = red
    return buckets


def _build_buckets_for_aisha(state) -> dict[int, QualityBucketObs]:
    """Aisha can either give white+green merged (Ethan-style) OR split.

    Aisha 通过 R1-R4 轮廓自己数件数 + 数格子，所以低品质段也接受 count 输入。
    """
    buckets: dict[int, QualityBucketObs] = {}

    def _bucket(q: int, cells_key: str, count_key: str) -> QualityBucketObs | None:
        cells = state.get(cells_key) or 0
        count = state.get(count_key) or 0
        if cells <= 0 and count <= 0:
            return None
        return QualityBucketObs(
            quality=q,
            total_cells=cells if cells > 0 else None,
            count=count if count > 0 else None,
        )

    if state.get("aisha_split"):
        w = _bucket(1, "white_cells", "white_count")
        if w is not None:
            buckets[1] = w
        g = _bucket(2, "green_cells", "green_count")
        if g is not None:
            buckets[2] = g
    else:
        merged_cells = (state.get("white_cells") or 0) + (state.get("green_cells") or 0)
        merged_count = (state.get("white_count") or 0) + (state.get("green_count") or 0)
        if merged_cells > 0 or merged_count > 0:
            buckets[1] = QualityBucketObs(
                quality=1,
                total_cells=merged_cells if merged_cells > 0 else None,
                count=merged_count if merged_count > 0 else None,
            )

    b = _bucket(3, "blue_cells", "blue_count")
    if b is not None:
        buckets[3] = b

    purple_cells_raw = state.get("purple_cells")
    purple_count_raw = state.get("purple_count")
    purple_avg_value_raw = state.get("purple_avg_value")
    purple_cells_in = int(purple_cells_raw) if purple_cells_raw is not None else None
    purple_count_in = int(purple_count_raw) if purple_count_raw is not None else None
    purple_avg_value = _try_parse_silver_amount(purple_avg_value_raw)
    purple_avg = _try_parse_reading(state.get("purple_avg_raw"))
    purple_value = state.get("purple_value") or 0
    purple_huge_raw = state.get("purple_huge_band", "none")
    purple_huge, purple_huge_override = _resolve_huge_selection(purple_huge_raw, 4)
    has_purple = (
        purple_cells_in is not None
        or (purple_count_in is not None and purple_count_in > 0)
        or purple_avg is not None
        or purple_value > 0
        or purple_avg_value is not None
        or purple_huge != "none"
    )
    if has_purple:
        buckets[4] = QualityBucketObs(
            quality=4,
            total_cells=purple_cells_in if purple_cells_in is not None and purple_cells_in >= 0 else None,
            count=purple_count_in if purple_count_in is not None and purple_count_in > 0 else None,
            avg_cells=purple_avg,
            value_sum=purple_value if purple_value > 0 else None,
            avg_value=purple_avg_value,
            huge_band=purple_huge,
            huge_cells_override=purple_huge_override,
        )
    gold = _maybe_gold_bucket(state, allow_huge=False)
    if gold is not None:
        buckets[5] = gold
    red_state = dict(state)
    red_state["red_huge_band"] = "none"
    red = _maybe_red_bucket(red_state)
    if red is not None:
        buckets[6] = red
    return buckets


def _build_session(state, maps: Mapping[int, BidMap]) -> SessionObs:
    if state["hero"] == "ethan":
        buckets = _build_buckets_for_ethan(state)
    else:
        buckets = _build_buckets_for_aisha(state)

    warehouse = state.get("warehouse_cells") or 0
    warehouse_is_estimate = _warehouse_cells_is_estimate(state)

    # Auto-detect "no red" or infer red cells from residual:
    # If user has NOT explicitly set any red info, but we can compute
    # red cells from warehouse minus all other specified bucket cells,
    # add the inferred red bucket automatically.
    if 6 not in buckets and warehouse > 0 and not warehouse_is_estimate:
        # Only auto-infer red when ALL non-red buckets are accounted for.
        # Ethan: need q1(wg), q3(blue), q4(purple), q5(gold).
        # Aisha: same set (q1 may be split into q1+q2).
        required_qs = {1, 3, 4, 5}
        provided_qs = set(buckets.keys())
        if 2 in provided_qs:
            required_qs.add(2)
        all_buckets_filled = required_qs.issubset(provided_qs)
        if all_buckets_filled:
            # First sum explicit total_cells. Then for buckets without
            # explicit cells, use enumeration (count/value_sum/huge_band/
            # avg_cells) to derive cells before computing red residual.
            explicit_sum = sum(
                b.total_cells for b in buckets.values()
                if b.total_cells is not None and b.total_cells > 0
            )
            derived_sum = 0
            for q, b in buckets.items():
                if b.total_cells is not None or q in (1, 2):
                    continue
                has_info = (
                    (b.value_sum is not None and b.value_sum > 0)
                    or b.huge_band != "none"
                    or b.avg_cells is not None
                    or b.count is not None
                    or (b.avg_value is not None and b.avg_value > 0)
                )
                if not has_info:
                    continue
                cands = candidates_for_bucket(
                    b, warehouse_capacity=warehouse,
                    other_known_cells=explicit_sum + derived_sum,
                )
                if cands:
                    derived_sum += cands[0].total_cells
                elif b.huge_band != "none":
                    derived_sum += b.min_huge_cells()
            known_sum = explicit_sum + derived_sum
            if known_sum > 0:
                red_residual = warehouse - known_sum
                if red_residual == 0:
                    buckets[6] = QualityBucketObs(
                        quality=6, total_cells=0, count=0, huge_band="none",
                    )
                elif red_residual > 0:
                    buckets[6] = QualityBucketObs(
                        quality=6, total_cells=red_residual,
                    )

    mid = state.get("map_id")
    if mid is None:
        raise ValueError("map_id is required to build a session")
    tic = state.get("total_item_count") or 0
    return SessionObs(
        map_id=int(mid),
        hero=state["hero"],
        warehouse_total_cells=None if warehouse_is_estimate else (warehouse or None),
        warehouse_total_cells_approx=warehouse if warehouse_is_estimate else None,
        warehouse_total_cells_tolerance=(
            _warehouse_cells_tolerance(state) if warehouse_is_estimate else None
        ),
        total_item_count=tic if tic > 0 else None,
        buckets=buckets,
    )


def _build_session_for_inference(state, maps: Mapping[int, BidMap]) -> SessionObs:
    if state.get("_use_live_canonical_input"):
        session = state.get("_live_canonical_session")
        if isinstance(session, SessionObs):
            return session
    return _build_session(state, maps)


# ---------- UI ----------

st.set_page_config(
    page_title="bidking-lab inference UI",
    page_icon="\U0001F3DB\uFE0F",
    layout="wide",
)

from ui_theme import (
    hint_tab_label,
    hint_tab_status_line,
    inject_app_theme,
    layout_width_kwargs,
    muted_caption,
    render_main_tab_nav,
    render_tab_status_line,
    sidebar_divider,
    sidebar_section,
    tab_lead,
)

inject_app_theme()

from ui_log import configure_ui_logging

configure_ui_logging()

_hdr_title, _hdr_link = st.columns([5, 1], vertical_alignment="top")
with _hdr_title:
    st.title("\u7ade\u62cd\u4e4b\u738b\u63a8\u65ad\u5b9e\u9a8c\u53f0 \u00b7 BidKing Inference UI")
with _hdr_link:
    st.link_button(
        "GitHub",
        "https://github.com/SeasonCake/bidking-lab",
        **layout_width_kwargs("link_button", stretch=True),
        help=(
            "bidking-lab \u6e90\u4ee3\u7801\u4e0e\u6587\u6863\u3002"
            "\u89c9\u5f97\u505a\u5f97\u8fd8\u4e0d\u9519\uff1f\u6b22\u8fce\u7ed9\u4f5c\u8005\u4e00\u4e2a\u514d\u8d39\u7684 \u2b50 Star\uff01"
        ),
    )
muted_caption(
    "\u4ec5\u7528\u4e8e\u672a\u516c\u5f00\u7684\u4e2a\u4eba\u5b9e\u9a8c\u6027\u5206\u6790\uff0c\u4e0d\u9644\u9001\u4efb\u4f55\u6e38\u620f\u8d44\u4ea7\u3002"
)
muted_caption(
    "\u8f93\u5165\u82f1\u96c4 / \u5730\u56fe / \u89c2\u6d4b\u5230\u7684 cells \u4e0e\u4f30\u4ef7\uff0c"
    "\u5e73\u53f0\u4f1a\u8de8 4 \u4e2a tab \u7ed9\u51fa\u8054\u5408\u63a8\u65ad\u7684\u4ed3\u5e93\u7ec4\u6210"
    "\u3001\u79d2\u4ed3 / \u653e\u4ed3\u51fa\u4ef7\u5efa\u8bae\u3001\u4ee5\u53ca\u9053\u5177\u6027\u4ef7\u6bd4 ROI\u3002"
    "\u64cd\u4f5c\u6b65\u9aa4\u89c1\u9996\u6b21\u52a0\u8f7d\u65f6\u300c\u64cd\u4f5c\u8bf4\u660e\u300d\u94fe\u63a5\uff08\u6d4f\u89c8\u5668\u6253\u5f00\uff09\uff1b"
    "\u5de5\u7a0b\u8fdb\u5ea6\u89c1 PROGRESS.md\u3002"
)
def _maybe_switch_to_hint_tab_after_capture() -> None:
    """Land on 出价推荐 when OCR auto-infer starts (not after MC finishes)."""
    if st.session_state.get("auto_infer_after_capture", True):
        st.session_state["_main_tab"] = "hint"


def _render_persisted_tab_nav(
    *,
    infer_status: str = "idle",
    done_flash: bool = False,
) -> str:
    """Session-persisted tab bar; call once per run before blocking OCR."""
    _hl = hint_tab_label(infer_status=infer_status, done_flash=done_flash)
    _status = hint_tab_status_line(infer_status=infer_status, done_flash=done_flash)
    if infer_status == "running":
        _status = ""
    render_tab_status_line(_status)
    _keys = ["obs", "hint", "joint", "roi"]
    _labels = {
        "obs": "\U0001f4dd \u8bfb\u6570\u8f93\u5165",
        "hint": _hl,
        "joint": "\U0001f50e \u8054\u5408\u7b5b\u9009",
        "roi": "\U0001f4b0 \u9053\u5177 ROI",
    }
    st.session_state.setdefault("_main_tab", "obs")
    return render_main_tab_nav(keys=_keys, labels=_labels)


def _schedule_deferred_capture(job: dict) -> None:
    """Queue OCR on next run; switch to hint tab immediately when auto-infer is on."""
    st.session_state["_pre_ocr_ui_snapshot"] = _snapshot_sidebar_ui()
    st.session_state["_deferred_capture_job"] = job
    st.session_state["_capture_in_progress"] = True
    if st.session_state.get("auto_infer_after_capture", True):
        st.session_state["_main_tab"] = "hint"
    st.rerun()


def _materialize_deferred_debug_png() -> None:
    """Build diagnostic panel PNG on a later rerun (keeps OCR path off the hot path)."""
    pending = st.session_state.pop("_defer_panel_png_src", None)
    if not pending:
        return
    data, crop_panel = pending
    dbg = st.session_state.get("_capture_debug")
    if not dbg:
        return
    from bidking_lab.capture.ocr import prepare_image_for_ocr

    _t0 = time.perf_counter()
    dbg["panel_png"] = _png_for_debug_store(
        prepare_image_for_ocr(data, crop_panel=crop_panel),
    )
    dbg["debug_png_ms"] = int((time.perf_counter() - _t0) * 1000)
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_materialize_deferred_debug_png",
        message="deferred debug png materialized",
        data={
            "debug_png_ms": dbg.get("debug_png_ms"),
            "panel_png_bytes": len(dbg.get("panel_png") or b""),
        },
        hypothesis_id="H2",
        run_id="post-fix",
    )
    # #endregion


@st.cache_resource(show_spinner=False)
def _cached_ocr_engine():
    """One lazy RapidOCR instance per Streamlit server process."""
    from bidking_lab.capture.ocr import (
        bind_ocr_engine,
        create_ocr_engine,
    )

    eng = create_ocr_engine()
    bind_ocr_engine(eng)
    return eng


# Sidebar - hero, map (2-step), capture, warehouse, MC settings ---------------
maps, drops, items = _load_tables()
_map_names: dict[int, str] = {m.map_id: m.name for m in maps.values()}

if "obs" not in st.session_state:
    st.session_state.obs = {}
state = st.session_state.obs

from agent_debug_log import agent_debug_enabled, agent_debug_log, agent_phase_log

# #region agent log
if agent_debug_enabled():
    _agent_run_t0 = time.perf_counter()
    st.session_state["_agent_run_t0"] = _agent_run_t0
    agent_debug_log(
        location="streamlit_app.py:run_start",
        message="script run begin",
        data={
            "main_tab": st.session_state.get("_main_tab"),
            "pending_capture": "_pending_capture" in st.session_state,
            "request_bg_hint_capture": bool(
                st.session_state.get("_request_bg_hint_capture")
            ),
            "request_bg_hint_manual": bool(
                st.session_state.get("_request_bg_hint_manual")
            ),
            "bg_infer_status": st.session_state.get("_bg_infer_status"),
        },
        hypothesis_id="H1,H2,H4",
    )
else:
    _agent_run_t0 = 0.0
# #endregion

if "_optional_int_migrated" not in st.session_state:
    for _k in ("obs_warehouse_cells", "obs_total_item_count"):
        if st.session_state.get(_k) == 0:
            st.session_state[_k] = None
    st.session_state["_optional_int_migrated"] = True


def _session_int(key: str) -> int:
    raw = st.session_state.get(key)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _warehouse_capacity() -> int:
    capacity = _session_int("obs_warehouse_cells") or int(
        state.get("warehouse_cells") or 0
    )
    if (
        state.get("hero") == "ethan"
        and st.session_state.get("obs_warehouse_cells_mode") == "estimate"
    ):
        return capacity + _session_int("obs_warehouse_cells_tolerance")
    return capacity


def _lower_bucket_cells_for_preview(state: dict, quality: int) -> int:
    from bidking_lab.inference.observation import explicit_lower_bucket_cells_from_state

    hero = str(state.get("hero") or "ethan")
    return explicit_lower_bucket_cells_from_state(state, quality, hero=hero)


def _png_for_debug_store(data: bytes, *, max_width: int = 720) -> bytes:
    """Shrink large panel PNGs kept in session (faster reruns, smaller wire)."""
    if len(data) < 350_000:
        return data
    try:
        import io as _io

        from PIL import Image

        img = Image.open(_io.BytesIO(data))
        if img.width <= max_width:
            return data
        scale = max_width / img.width
        img = img.resize(
            (max_width, max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
        out = _io.BytesIO()
        img.save(out, format="PNG", compress_level=1, optimize=False)
        return out.getvalue()
    except Exception:
        return data


def _queue_capture_from_bytes(
    data: bytes,
    *,
    map_names: dict[int, str],
    crop_panel: bool = True,
    source: str = "upload",
    capture_debug: dict | None = None,
) -> str | None:
    """OCR image → parse; store pending result. Returns error message or None."""
    from bidking_lab.capture.log_util import LOG, configure_capture_logging
    from bidking_lab.capture.ocr import (
        image_bytes_to_text,
        last_ocr_panel_meta,
        prepare_image_for_ocr,
    )
    from bidking_lab.capture.parser import parse_panel_text
    from bidking_lab.capture.screen import INFO_PANEL_CROP_FRAC

    configure_capture_logging()
    import time as _time

    _prep_ms = 0
    _t0 = _time.perf_counter()
    try:
        engine = _cached_ocr_engine()
    except Exception as exc:  # noqa: BLE001
        st.session_state["ocr_ui_status"] = "error"
        st.session_state["ocr_ui_error"] = str(exc)
        return f"OCR 引擎加载失败：{exc}"
    st.session_state["ocr_ui_status"] = "ready"
    ocr_text, ocr_err = image_bytes_to_text(data, crop_panel=crop_panel, engine=engine)
    _ocr_ms = int((_time.perf_counter() - _t0) * 1000)
    _ocr_meta = last_ocr_panel_meta()
    st.session_state["_defer_panel_png_src"] = (data, crop_panel)
    _panel_debug = b""
    _debug_ms = 0
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_queue_capture_from_bytes:timing",
        message="ocr complete; debug png deferred",
        data={
            "ocr_ms": _ocr_ms,
            "defer_debug_png": True,
            "ocr_input_shape": _ocr_meta.get("shape"),
            "ocr_resize_applied": _ocr_meta.get("resize_applied"),
            "ocr_crop_applied": _ocr_meta.get("crop_applied"),
            "ocr_decoded_size": _ocr_meta.get("decoded_size"),
        },
        hypothesis_id="H2",
        run_id="post-fix",
    )
    # #endregion
    if source.startswith("screen") and not st.session_state.get(
        "_ocr_first_grab_notice_done",
    ):
        st.session_state["_ocr_show_first_grab_notice"] = True
    debug: dict = {
        "source": source,
        "crop_panel": crop_panel,
        "crop_frac": INFO_PANEL_CROP_FRAC if crop_panel else None,
        "panel_png": _panel_debug,
        "ocr_text": "",
        "ocr_error": ocr_err,
        "parse_keys": [],
        "map_id": None,
        "map_name": None,
        "map_diag_status": None,
        "ocr_ms": 0,
    }
    if capture_debug:
        debug.update(capture_debug)
    debug["ocr_ms"] = _ocr_ms
    if ocr_err:
        st.session_state["_capture_debug"] = debug
        return ocr_err
    if not ocr_text:
        debug["ocr_text"] = ""
        st.session_state["_capture_debug"] = debug
        return "OCR 未识别到文字，请检查截图区域。"
    parsed = parse_panel_text(ocr_text, map_names=map_names)
    st.session_state["_pending_capture"] = parsed
    st.session_state["_last_capture_source"] = source
    st.session_state["_last_capture_crop"] = crop_panel
    debug["ocr_text"] = ocr_text
    _smap = parsed.suggestion_map()
    debug["parse_keys"] = list(_smap.keys())
    debug["parse_values"] = dict(_smap)
    debug["map_id"] = parsed.map_id
    debug["map_name"] = parsed.map_name
    if parsed.map_diag is not None:
        debug["map_diag_status"] = parsed.map_diag.status
    st.session_state["_capture_debug"] = debug
    if st.session_state.pop("_ocr_show_first_grab_notice", False):
        st.session_state["_ocr_first_grab_toast"] = True
        st.session_state["_ocr_first_grab_notice_done"] = True
    if LOG.isEnabledFor(logging.INFO):
        _grab_ms = int(capture_debug.get("grab_ms") or 0) if capture_debug else 0
        _timing = (
            f"抓屏 {_grab_ms}ms · OCR {_ocr_ms}ms · 诊断图 {_debug_ms}ms"
            if _grab_ms
            else f"OCR {_ocr_ms}ms · 诊断图 {_debug_ms}ms"
        )
        st.session_state["_capture_pipeline_log"] = [
            f"来源 {source} · crop_panel={crop_panel}",
            _timing,
            f"OCR {len(ocr_text.splitlines())} 行",
            f"解析字段 {debug['parse_keys']}",
        ]
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_queue_capture_from_bytes",
        message="OCR queued pending_capture",
        data={
            "source": source,
            "prep_ms": _prep_ms,
            "ocr_ms": _ocr_ms,
            "parse_keys": debug["parse_keys"],
        },
        hypothesis_id="H12,H13",
        run_id="post-fix",
    )
    # #endregion
    return None


def _render_bg_infer_debug_panel() -> None:
    """Background MC poll log (ring buffer). Set BIDKING_UI_LOG=1 for terminal too."""
    lines = st.session_state.get("_bg_infer_log")
    if not lines:
        return
    with st.expander("后台推断监视", expanded=False):
        st.caption(
            "终端可设环境变量 BIDKING_UI_LOG=1 同步输出。"
            "若长时间 running，看是否反复 cancelled（读数指纹变化）。"
        )
        for ln in lines[-20:]:
            st.text(ln)


def _render_capture_debug_panel() -> None:
    """Show last capture monitor ROI + OCR text (diagnostic)."""
    dbg = st.session_state.get("_capture_debug")
    if not dbg:
        return
    expanded = st.session_state.pop("_capture_debug_expand", False)
    with st.expander("抓屏 / OCR 诊断（上次）", expanded=expanded):
        st.caption(
            "红框 = 该显示器上的 OCR 区域（比例 ROI）。"
            "游戏在哪个屏就开哪个屏；Streamlit 在副屏不影响，关键是下拉选对显示器。"
        )
        if dbg.get("monitor_label"):
            st.markdown(f"**显示器** {dbg['monitor_label']}")
        if dbg.get("crop_box"):
            l, t, r, b = dbg["crop_box"]
            st.caption(
                f"ROI 比例 {dbg.get('crop_frac')} → 像素 ({l},{t})–({r},{b}) · "
                f"crop_panel={dbg.get('crop_panel')}",
            )
        prev = dbg.get("monitor_preview_png") or b""
        panel = dbg.get("panel_png")
        if prev and panel:
            c1, c2 = st.columns(2)
            with c1:
                st.image(
                    prev,
                    caption="整屏预览（红框=OCR 区域）",
                    **layout_width_kwargs("image", stretch=True),
                )
            with c2:
                st.image(
                    panel,
                    caption="送入 OCR 的裁切图",
                    **layout_width_kwargs("image", stretch=True),
                )
        elif panel:
            st.image(
                panel,
                caption="送入 OCR 的裁切图",
                **layout_width_kwargs("image", stretch=True),
            )
        if dbg.get("ocr_error"):
            st.warning(str(dbg["ocr_error"]))
        if dbg.get("grab_ms"):
            st.caption(f"上次抓屏约 **{dbg['grab_ms']}** ms")
        if dbg.get("ocr_ms"):
            st.caption(f"上次 OCR 约 **{dbg['ocr_ms']}** ms")
        keys = dbg.get("parse_keys") or []
        st.markdown(
            f"**解析字段** {keys if keys else '（无）'} · "
            f"地图 {dbg.get('map_name') or '—'} "
            f"(`{dbg.get('map_diag_status') or '—'}`)",
        )
        _pvals = dbg.get("parse_values") or {}
        if _pvals:
            st.caption(
                "解析取值："
                + " · ".join(f"{k}={v!r}" for k, v in _pvals.items()),
            )
        ocr_text = dbg.get("ocr_text") or ""
        if ocr_text:
            st.text_area(
                "OCR 原文",
                value=ocr_text,
                height=min(280, 80 + len(ocr_text.splitlines()) * 18),
                disabled=True,
            )
        else:
            st.caption("OCR 未产出文字（常见原因：选错显示器或 ROI 未盖住信息面板）。")


def _snapshot_sidebar_ui() -> dict:
    """Remember sidebar values before OCR rerun (widgets may not have flushed yet)."""
    _mk = _map_select_widget_key()
    return {
        "obs_warehouse_cells": st.session_state.get("obs_warehouse_cells"),
        "obs_warehouse_cells_mode": st.session_state.get("obs_warehouse_cells_mode"),
        "obs_warehouse_cells_tolerance": st.session_state.get(
            "obs_warehouse_cells_tolerance"
        ),
        "obs_total_item_count": st.session_state.get("obs_total_item_count"),
        "obs_map_category": st.session_state.get("obs_map_category"),
        "_map_widget_key": _mk,
        "_map_widget_val": st.session_state.get(_mk),
    }


def _pick_preserved_int(
    key: str,
    *,
    widget_val: int | None,
    snap: dict,
) -> int | None:
    """Pick first positive int from session_state, widget return, or pre-OCR snapshot."""
    for raw in (st.session_state.get(key), widget_val, snap.get(key)):
        if raw is None:
            continue
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def _record_live_observation_snapshot(
    obs_state: dict[str, Any],
    *,
    source: str,
    event_kind: str,
    previous: dict[str, Any] | None = None,
) -> None:
    """Mirror legacy UI updates into the live reducer without driving UI yet."""
    from bidking_lab.live import (
        LiveSessionState,
        apply_observation_batch,
        live_batch_from_legacy_obs,
        summarize_blocked_field_updates,
    )

    prior = (
        previous
        if previous is not None
        else st.session_state.get("_live_legacy_snapshot", {})
    )
    current_effective = dict(obs_state)
    prior_effective = dict(prior)
    for effective in (current_effective, prior_effective):
        current_hero = effective.get("hero")
        for prefix, quality in (("purple", 4), ("gold", 5), ("red", 6)):
            key = f"{prefix}_huge_band"
            if key not in effective:
                continue
            allowed = quality == 4 or current_hero != "aisha"
            raw = str(effective.get(key) or "none") if allowed else "none"
            band, override = _resolve_huge_selection(raw, quality)
            effective[key] = band
            effective[f"{prefix}_huge_cells_override"] = override
    batch = live_batch_from_legacy_obs(
        current_effective,
        previous=prior_effective,
        source=source,
        event_kind=event_kind,
    )
    st.session_state["_live_legacy_snapshot"] = dict(obs_state)
    if not batch.field_updates:
        st.session_state["_last_live_blocked_updates"] = []
        return
    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        live_state = LiveSessionState()
    st.session_state["_last_live_blocked_updates"] = list(
        summarize_blocked_field_updates(live_state, batch, limit=24)
    )
    st.session_state["_live_session_state"] = apply_observation_batch(
        live_state,
        batch,
    )
    st.session_state["_last_live_observation_batch"] = batch


def _render_records_table(rows: Sequence[Mapping[str, Any]]) -> None:
    """Render small diagnostic tables without requiring pyarrow."""
    if not rows:
        return
    columns = list(dict.fromkeys(key for row in rows for key in row))

    def cell(value: Any) -> str:
        text = "" if value is None else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(cell(row.get(column)) for column in columns) + " |"
        for row in rows
    ]
    st.markdown("\n".join([header, divider, *body]))


def _render_live_source_summary() -> None:
    """Show shadow live-field provenance without changing inference input."""
    from bidking_lab.live import LiveSessionState, summarize_field_sources

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        return
    rows = list(summarize_field_sources(live_state, limit=24))
    if not rows:
        return
    with st.expander("\u89c2\u6d4b\u6765\u6e90\uff08shadow\uff09", expanded=False):
        st.caption(
            "\u5f53\u524d\u63a8\u8350\u4ecd\u8d70 legacy obs\uff1b\u8fd9\u91cc\u53ea\u7528\u6765\u6838\u5bf9\u624b\u586b/OCR/"
            "packet \u5207\u6362\u524d\u7684\u5b57\u6bb5\u6765\u6e90\u4e0e\u8986\u76d6\u89c4\u5219\u3002"
        )
        _render_records_table(rows)
        blocked = st.session_state.get("_last_live_blocked_updates") or []
        if blocked:
            st.caption(
                "最近一次观测里，以下字段被当前更高优先级来源保留。"
                "这用于提前核对 packet > manual > OCR > derived 规则。"
            )
            _render_records_table(blocked)


def _render_obs_source_summary() -> None:
    """Show key reading provenance near the input form."""
    from bidking_lab.live import (
        LiveSessionState,
        summarize_selected_field_sources,
    )

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        return
    rows = list(
        summarize_selected_field_sources(
            live_state,
            {
                "地图": ("session", "map_id"),
                "英雄": ("session", "hero"),
                "仓库总格": ("session", "warehouse_total_cells"),
                "总藏品件数": ("session", "total_item_count"),
                "白/绿总格": ("bucket", "1", "total_cells"),
                "白品件数": ("bucket", "1", "count"),
                "绿品总格": ("bucket", "2", "total_cells"),
                "绿品件数": ("bucket", "2", "count"),
                "蓝品总格": ("bucket", "3", "total_cells"),
                "蓝品件数": ("bucket", "3", "count"),
                "紫品总格": ("bucket", "4", "total_cells"),
                "紫品件数": ("bucket", "4", "count"),
                "紫品均格": ("bucket", "4", "avg_cells"),
                "紫品总价": ("bucket", "4", "value_sum"),
                "金品总格": ("bucket", "5", "total_cells"),
                "金品件数": ("bucket", "5", "count"),
                "金品均格": ("bucket", "5", "avg_cells"),
                "金品总价": ("bucket", "5", "value_sum"),
                "红品总格": ("bucket", "6", "total_cells"),
                "红品价值区间": ("bucket", "6", "value_range"),
            },
            limit=24,
        )
    )
    if not rows:
        return
    with st.expander("当前关键读数来源（shadow）", expanded=False):
        st.caption(
            "当前推荐仍走 legacy obs；这里用于核对手填/OCR/packet 切换前的来源。"
        )
        _render_records_table(rows)


def _live_session_snapshot_for(obs_state: dict[str, Any]) -> SessionObs | None:
    from bidking_lab.live import (
        LiveSessionState,
        live_state_to_session_obs,
    )

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        return None
    session = live_state_to_session_obs(live_state)
    if session is None:
        return None

    ui_map = obs_state.get("map_id")
    if ui_map is not None and int(ui_map) != session.map_id:
        return None

    ui_warehouse = int(obs_state.get("warehouse_cells") or 0)
    if ui_warehouse > 0:
        if session.warehouse_total_cells is not None:
            if session.warehouse_total_cells != ui_warehouse:
                return None
        elif session.warehouse_total_cells_approx != ui_warehouse:
            return None
    return session


def _attach_inference_session_source(obs_state: dict[str, Any]) -> None:
    """Attach the selected canonical input snapshot for current inference."""
    use_live = bool(st.session_state.get("use_live_canonical_input", False))
    obs_state["_use_live_canonical_input"] = use_live
    obs_state.pop("_live_canonical_session", None)
    source = "legacy"
    if use_live:
        live_session = _live_session_snapshot_for(obs_state)
        if live_session is not None:
            obs_state["_live_canonical_session"] = live_session
            obs_state["map_id"] = live_session.map_id
            obs_state["hero"] = live_session.hero
            if live_session.warehouse_total_cells is not None:
                obs_state["warehouse_cells"] = live_session.warehouse_total_cells
                obs_state["warehouse_cells_mode"] = "strict"
            elif live_session.warehouse_total_cells_approx is not None:
                obs_state["warehouse_cells"] = live_session.warehouse_total_cells_approx
                obs_state["warehouse_cells_mode"] = "estimate"
                obs_state["warehouse_cells_tolerance"] = (
                    live_session.warehouse_total_cells_tolerance or 0
                )
            if live_session.total_item_count is not None:
                obs_state["total_item_count"] = live_session.total_item_count
            source = "live_shadow"
        else:
            source = "legacy_fallback"
    obs_state["_canonical_input_source"] = source
    st.session_state["_canonical_input_source"] = source


def _mark_live_inference_ready() -> None:
    from bidking_lab.live import LiveSessionState, mark_ready

    live_state = st.session_state.get("_live_session_state")
    if isinstance(live_state, LiveSessionState):
        st.session_state["_live_session_state"] = mark_ready(live_state)
        st.session_state["_live_auto_recompute_armed"] = True


def _render_live_inference_status() -> None:
    from bidking_lab.live import LiveSessionState, live_inference_status

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        return

    status = live_inference_status(
        live_state,
        worker_running=(
            (st.session_state.get("_bg_infer_box") or {}).get("status")
            == "running"
        ),
        has_result=st.session_state.get("_hint_bundle") is not None,
        has_error=str(st.session_state.get("_bg_infer_status", "")) == "error",
    )
    status_labels = {
        "idle": "未运行",
        "dirty": "输入已变化",
        "running": "后台推理中",
        "ready": "结果可用",
        "error": "推理错误",
    }
    with st.expander("live 推理状态", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("状态", status_labels[status])
        c2.metric("live version", live_state.version)
        c3.metric("dirty", "是" if live_state.dirty else "否")
        st.caption(
            "dirty 表示 live 观测已经变化，当前推荐可能需要重算；"
            "running 表示后台 MC 正在运行；ready 表示已有结果且 live 未标脏。"
        )


@st.cache_data(max_entries=1, show_spinner=False)
def _battle_item_names_by_id() -> dict[int, str]:
    path = Path(__file__).resolve().parent.parent / "data" / "processed" / "battle_items.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - optional diagnostic lookup only
        return {}
    return {
        int(row["battle_item_id"]): str(row["name"])
        for row in rows
        if isinstance(row, Mapping) and row.get("battle_item_id") is not None
    }


def _fatbeans_inventory_summary_rows(
    inventory_items: Sequence[Any],
) -> tuple[list[dict[str, Any]], int, list[int]]:
    """Summarize settlement inventory using the local Item table when possible."""
    rows: list[dict[str, Any]] = []
    total_value = 0
    missing_item_ids: list[int] = []
    for quality in (1, 2, 3, 4, 5, 6):
        quality_items = [
            item for item in inventory_items
            if item.quality == quality
        ]
        if not quality_items:
            continue
        value_sum = 0
        missing_for_quality: list[int] = []
        for item in quality_items:
            item_row = items.get(item.item_id)
            if item_row is None:
                missing_for_quality.append(item.item_id)
                continue
            value_sum += item_row.value
        total_value += value_sum
        missing_item_ids.extend(missing_for_quality)
        rows.append(
            {
                "品质": f"{quality} {QUALITY_NAME.get(quality, '')}",
                "件数": len(quality_items),
                "格数": sum(item.cells for item in quality_items),
                "已知价值": value_sum,
                "缺失item": ",".join(str(x) for x in sorted(set(missing_for_quality))),
            }
        )
    return rows, total_value, sorted(set(missing_item_ids))


def _apply_live_packet_batches(batches: Sequence[Any]) -> int:
    """Merge packet-derived live batches into the shadow reducer."""
    from bidking_lab.live import (
        LiveSessionState,
        apply_observation_batch,
        summarize_blocked_field_updates,
    )

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        live_state = LiveSessionState()
    blocked: list[dict[str, Any]] = []
    applied = 0
    for batch in batches:
        blocked.extend(summarize_blocked_field_updates(live_state, batch, limit=24))
        live_state = apply_observation_batch(live_state, batch)
        applied += 1
    st.session_state["_live_session_state"] = live_state
    st.session_state["_last_live_blocked_updates"] = blocked[:24]
    if batches:
        st.session_state["_last_live_observation_batch"] = batches[-1]
    return applied


def _format_quantile_interval(summary: Any) -> str:
    if summary is None:
        return ""
    return f"{summary.p10:,.0f} / {summary.p50:,.0f} / {summary.p90:,.0f}"


def _format_quantile_width(summary: Any) -> str:
    if summary is None:
        return ""
    return f"{summary.p90 - summary.p10:,.0f}"


def _candidate_map_ids_for_likelihood(
    session: SessionObs,
    maps: Mapping[int, BidMap],
) -> tuple[int, ...]:
    if session.map_id in maps:
        return (session.map_id,)
    return (session.map_id,)


def _map_likelihood_result_rows(results: Sequence[Any], label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results[:5]:
        rows.append(
            {
                "证据": label,
                "地图": f"{result.map_id} {result.map_name}",
                "匹配": f"{result.n_matched}/{result.n_total}",
                "后验": f"{result.posterior_probability:.1%}",
                "总格 P10/P50/P90": _format_quantile_interval(result.total_cells),
                "价值 P10/P50/P90": _format_quantile_interval(result.total_value),
            }
        )
    return rows


def _warehouse_estimate_rows(estimate: Any) -> list[dict[str, Any]]:
    if estimate is None:
        return []
    rows = [
        {
            "范围": "跨候选地图汇总",
            "匹配": f"{estimate.n_matched}/{estimate.n_total}",
            "置信度": estimate.confidence,
            "总格 P10/P50/P90": _format_quantile_interval(estimate.total_cells),
            "价值 P10/P50/P90": _format_quantile_interval(estimate.total_value),
            "说明": estimate.reason,
        }
    ]
    for row in estimate.map_contributions[:5]:
        rows.append(
            {
                "范围": f"{row.map_id} {row.map_name}",
                "匹配": f"{row.n_matched}/{row.n_total}",
                "置信度": f"地图后验 {row.posterior_probability:.1%}",
                "总格 P10/P50/P90": _format_quantile_interval(row.total_cells),
                "价值 P10/P50/P90": "",
                "说明": "",
            }
        )
    return rows


def _brief_warehouse_estimate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    row = rows[0]
    return [
        {
            "匹配": row.get("匹配", ""),
            "置信度": row.get("置信度", ""),
            "总格 P10/P50/P90": row.get("总格 P10/P50/P90", ""),
            "价值 P10/P50/P90": row.get("价值 P10/P50/P90", ""),
            "说明": row.get("说明", ""),
        }
    ]


def _tool_info_roi_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:8]:
        out.append(
            {
                "道具": row.tool_name,
                "价格": f"{row.silver_cost:,}",
                "匹配样本": row.n_matched,
                "价值区间压缩": f"{row.value_width_gain:,.0f}",
                "仓储区间压缩": f"{row.cells_width_gain:,.0f}",
                "信息ROI": f"{row.roi_value:.2f}",
                "说明": row.note,
            }
        )
    return out


def _brief_tool_info_roi_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:3]:
        out.append(
            {
                "道具": row.get("道具", ""),
                "价格": row.get("价格", ""),
                "价值压缩": row.get("价值区间压缩", ""),
                "仓储压缩": row.get("仓储区间压缩", ""),
                "信息ROI": row.get("信息ROI", ""),
            }
        )
    return out


def _decision_summary_rows(
    *,
    bid_rows: Sequence[Mapping[str, Any]],
    warehouse_rows: Sequence[Mapping[str, Any]],
    tool_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    from bidking_lab.runtime import (
        tactical_snapshot_from_rows,
        tactical_summary_rows,
    )

    snapshot = tactical_snapshot_from_rows(
        bid_rows=bid_rows,
        warehouse_rows=warehouse_rows,
        tool_rows=tool_rows,
    )
    return [
        {
            "问题": row.topic,
            "结论": row.conclusion,
            "依据": row.detail,
        }
        for row in tactical_summary_rows(snapshot)
    ]


def _render_decision_summary(
    *,
    bid_rows: Sequence[Mapping[str, Any]] | None = None,
    warehouse_rows: Sequence[Mapping[str, Any]] | None = None,
    tool_rows: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    rows = _decision_summary_rows(
        bid_rows=bid_rows or (),
        warehouse_rows=warehouse_rows or (),
        tool_rows=tool_rows or (),
    )
    if not any(row["结论"] not in ("暂无", "暂无后验", "暂无出价后验") for row in rows):
        return
    st.markdown("### 决策小结")
    _render_records_table(rows)


def _bid_advice_rows(
    *,
    latest_bids: Mapping[str, int],
    value_summary: Any,
    evidence_label: str,
    session: SessionObs | None = None,
    round_no: int | None = None,
    total_rounds: int | None = None,
    posterior_samples: int = 0,
    warehouse_estimate: Any = None,
) -> list[dict[str, Any]]:
    if value_summary is None or not latest_bids:
        return []
    from bidking_lab.inference.bid_strategy import recommend_bid_strategy

    report = recommend_bid_strategy(
        latest_bids=latest_bids,
        value_summary=value_summary,
        evidence_label=evidence_label,
        session=session,
        round_no=round_no,
        total_rounds=total_rounds,
        posterior_samples=posterior_samples,
        warehouse_estimate=warehouse_estimate,
    )
    if report is None:
        return []
    thresholds = report.thresholds
    rows = [
        {
            "证据": report.evidence_label,
            "轮次": report.round_label,
            "信息强度": report.info_strength,
            "仓储": report.warehouse_status,
            "当前最高": f"{report.leader} {report.highest_bid:,}",
            "风险带": report.risk_band,
            "探价(P10)": f"{thresholds.probe_bid:,}",
            "防守价": f"{thresholds.defend_bid:,}",
            "抢仓上限": f"{thresholds.attack_bid:,}",
            "停止价": f"{thresholds.stop_bid:,}",
            "依据": report.rationale,
            "补信息": report.next_info_hint,
            "建议": report.action,
        }
    ]
    for player in report.player_risks:
        rows.append(
            {
                "证据": "玩家价位",
                "轮次": "",
                "信息强度": "",
                "仓储": "",
                "当前最高": f"{player.name} {player.bid:,}",
                "风险带": player.risk_band,
                "探价(P10)": "",
                "防守价": "",
                "抢仓上限": "",
                "停止价": "",
                "依据": "",
                "补信息": "",
                "建议": "",
            }
        )
    return rows


def _latest_fatbeans_bids() -> dict[str, int]:
    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return {}
    raw = summary.get("latest_bids")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, int] = {}
    for name, value in raw.items():
        try:
            out[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _latest_fatbeans_round() -> int | None:
    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return None
    try:
        value = int(summary.get("round") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _latest_warehouse_estimate() -> Any:
    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return None
    map_likelihood = summary.get("map_likelihood")
    if not isinstance(map_likelihood, Mapping):
        return None
    return map_likelihood.get("warehouse_estimate")


def _rounds_total_for_session(session: SessionObs | None) -> int | None:
    return None


def _bid_advice_rows_from_hint_bundle(bundle: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(bundle, Mapping):
        return []
    latest_bids = _latest_fatbeans_bids()
    if not latest_bids:
        return []
    values = bundle.get("conditional_values")
    if not values:
        values = bundle.get("all_values")
    if not values:
        return []

    from types import SimpleNamespace

    p10, p50, p90 = np.percentile(np.asarray(values, dtype=np.float64), [10, 50, 90])
    value_summary = SimpleNamespace(p10=float(p10), p50=float(p50), p90=float(p90))
    source = str(bundle.get("canonical_input_source") or "legacy")
    session = bundle.get("session")
    session_obs = session if isinstance(session, SessionObs) else None
    return _bid_advice_rows(
        latest_bids=latest_bids,
        value_summary=value_summary,
        evidence_label=f"主推理 {source}",
        session=session_obs,
        round_no=_latest_fatbeans_round(),
        total_rounds=_rounds_total_for_session(session_obs),
        posterior_samples=len(values),
        warehouse_estimate=_latest_warehouse_estimate(),
    )


def _render_main_warehouse_estimate_panel() -> None:
    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return
    map_likelihood = summary.get("map_likelihood")
    if not isinstance(map_likelihood, Mapping):
        return
    rows = map_likelihood.get("warehouse_estimate_rows")
    if not isinstance(rows, list) or not rows:
        return
    st.markdown("### 仓储估计")
    st.caption(
        "不使用最终结算总格数，只用当前 live/Fatbeans 证据反推总格区间；"
        "区间越宽，出价策略越保守。"
    )
    layout_note = map_likelihood.get("layout_note")
    if layout_note:
        st.caption(str(layout_note))
    _render_records_table(_brief_warehouse_estimate_rows(rows))
    tool_rows = map_likelihood.get("tool_info_roi_rows")
    if isinstance(tool_rows, list) and tool_rows:
        st.caption(
            "补信息 ROI：比较当前局面下一张道具对价值/仓储区间的预期压缩。"
        )
        _render_records_table(_brief_tool_info_roi_rows(tool_rows))
    with st.expander("展开仓储 / 补信息 ROI 详细诊断", expanded=False):
        _render_records_table(rows)
        if isinstance(tool_rows, list) and tool_rows:
            _render_records_table(tool_rows)


def _main_warehouse_and_tool_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return [], []
    map_likelihood = summary.get("map_likelihood")
    if not isinstance(map_likelihood, Mapping):
        return [], []
    warehouse_rows = map_likelihood.get("warehouse_estimate_rows")
    tool_rows = map_likelihood.get("tool_info_roi_rows")
    return (
        _brief_warehouse_estimate_rows(warehouse_rows)
        if isinstance(warehouse_rows, list)
        else [],
        _brief_tool_info_roi_rows(tool_rows)
        if isinstance(tool_rows, list)
        else [],
    )


def _render_main_bid_advice_panel(bundle: Mapping[str, Any] | None) -> None:
    rows = _bid_advice_rows_from_hint_bundle(bundle)
    warehouse_summary_rows, tool_summary_rows = _main_warehouse_and_tool_rows()
    if rows:
        _render_decision_summary(
            bid_rows=rows[:1],
            warehouse_rows=warehouse_summary_rows,
            tool_rows=tool_summary_rows,
        )
    _render_main_warehouse_estimate_panel()
    if rows:
        st.markdown("### 实时出价建议 v1")
        st.caption(
            "用当前 Fatbeans 最高价对比本页 MC 条件后验。"
            "只读建议，不自动出价；阈值会按轮次、仓储状态和信息强度调整。"
        )
        _render_records_table(rows)
        return

    summary = st.session_state.get("_last_fatbeans_import_summary")
    if not isinstance(summary, Mapping):
        return
    fallback_rows = summary.get("bid_advice_rows")
    if isinstance(fallback_rows, list) and fallback_rows:
        _render_decision_summary(
            bid_rows=fallback_rows[:1],
            warehouse_rows=warehouse_summary_rows,
            tool_rows=tool_summary_rows,
        )
        st.markdown("### 实时出价建议 v1")
        st.caption(
            "当前还没有本页 MC 后验，先显示 Fatbeans 导入诊断中的地图似然估值。"
            "运行出价 hint 后会切换为本页条件后验。"
        )
        _render_records_table(fallback_rows)


def _render_fatbeans_detail_tables(summary: Mapping[str, Any]) -> None:
    with st.expander("展开 inventory / 轮次 / 道具 / 出价明细", expanded=False):
        grid_summary_rows = summary.get("grid_summary_rows")
        if isinstance(grid_summary_rows, list) and grid_summary_rows:
            st.caption(
                "当前轮廓证据（优先使用结算前最后一批 grid_items；未知品质只作诊断）："
            )
            _render_records_table(grid_summary_rows)
        grid_batches = summary.get("grid_batches")
        if isinstance(grid_batches, tuple):
            _render_fatbeans_grid_preview(grid_batches)
        grid_position_rows = summary.get("grid_position_rows")
        if isinstance(grid_position_rows, list) and grid_position_rows:
            st.caption(
                "轮廓坐标证据（行/列为 1-based；local=None 按 top-left 0 处理）："
            )
            _render_records_table(grid_position_rows)
        quality_rows = summary.get("quality_rows")
        if isinstance(quality_rows, list) and quality_rows:
            st.caption("结算 inventory 按品质汇总：")
            _render_records_table(quality_rows)
        state_round_rows = summary.get("state_round_rows")
        if isinstance(state_round_rows, list) and state_round_rows:
            st.caption(
                "状态包轮次审计（使用顶层状态轮次；技能块内部轮次不作为展示轮次）："
            )
            _render_records_table(state_round_rows)
        tool_rows = summary.get("tool_rows")
        if isinstance(tool_rows, list) and tool_rows:
            st.caption("本局发送的道具/动作：")
            _render_records_table(tool_rows)
        result_rows = summary.get("result_rows")
        if isinstance(result_rows, list) and result_rows:
            st.caption("最新状态中的道具结果：")
            _render_records_table(result_rows)
        bid_rows = summary.get("bid_rows")
        if isinstance(bid_rows, list) and bid_rows:
            st.caption(
                "结算/最新状态中的玩家出价候选（诊断用，不参与推理；"
                "values 不保证按时间排序）。"
            )
            _render_records_table(bid_rows)
        replay_rows = summary.get("layout_replay_rows")
        if isinstance(replay_rows, list) and replay_rows:
            st.caption(
                "布局回放评估：按状态切片对比最终结算真值；边界误差不是预测，只是布局深度诊断。"
            )
            _render_records_table(replay_rows)


def _render_fatbeans_tactical_summary(
    summary: Mapping[str, Any],
    *,
    warehouse_summary_rows: Sequence[Mapping[str, Any]],
    tool_summary_rows: Sequence[Mapping[str, Any]],
    bid_advice_rows: Sequence[Mapping[str, Any]],
) -> None:
    from bidking_lab.runtime import tactical_panel_from_rows

    layout_stage_rows = summary.get("layout_stage_rows")
    layout_stage_rows = (
        layout_stage_rows
        if isinstance(layout_stage_rows, list)
        else []
    )
    map_likelihood = summary.get("map_likelihood")
    layout_note = ""
    if isinstance(map_likelihood, Mapping):
        layout_note = str(map_likelihood.get("layout_note") or "")
    panel = tactical_panel_from_rows(
        bid_rows=bid_advice_rows,
        warehouse_rows=warehouse_summary_rows,
        tool_rows=tool_summary_rows,
        layout_stage_rows=layout_stage_rows,
        layout_note=layout_note,
    )
    st.markdown("### Fatbeans 实战摘要")
    if panel.layout_note:
        st.caption(panel.layout_note)
    _render_records_table(
        [
            {
                "项目": row.topic,
                "结论": row.conclusion,
                "补充": row.detail,
            }
            for row in panel.summary_rows
        ]
    )
    grid_batches = summary.get("grid_batches")
    if isinstance(grid_batches, tuple):
        _render_fatbeans_grid_preview(grid_batches)
    if panel.layout_stages:
        st.caption("布局阶段摘要：")
        _render_records_table(
            [
                {
                    "阶段": stage.stage,
                    "已知格": stage.known_cells,
                    "覆盖": stage.coverage,
                    "最深行": stage.deepest_row,
                    "布局估计": stage.estimate,
                    "置信": stage.confidence,
                    "风险": stage.risk,
                }
                for stage in panel.layout_stages
            ]
        )
    if panel.warehouse_rows:
        st.caption("仓储估计（摘要）：")
        _render_records_table(panel.warehouse_rows)
    if panel.tool_rows:
        st.caption("补信息 ROI（摘要，Top 3）：")
        _render_records_table(panel.tool_rows)
    if panel.bid_rows:
        st.caption("实时出价建议 v1（摘要）：")
        _render_records_table(panel.bid_rows)


def _brief_layout_stage_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        estimate = row.get("样本拟合估计") or row.get("布局估计", "")
        confidence = row.get("样本拟合置信") or row.get("估计置信", "")
        out.append(
            {
                "阶段": f"R{row.get('R') or '?'} / sort {row.get('sort')}",
                "已知格": row.get("已知格", ""),
                "覆盖": row.get("已知覆盖", ""),
                "最深行": row.get("最深行", ""),
                "布局估计": estimate,
                "置信": confidence,
                "风险": row.get("风险", ""),
            }
        )
    return out


def _fatbeans_state_round_rows(events: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in events.states:
        if not (
            state.action_results
            or state.skill_reveals
            or state.public_infos
            or state.inventory_items
        ):
            continue
        rows.append(
            {
                "sort": state.sort_id,
                "时间": state.capture_time[-15:],
                "状态轮次(R)": state.round_no,
                "消息": hex(state.message_id),
                "道具结果": ", ".join(
                    str(result.action_id) for result in state.action_results
                ),
                "技能": ", ".join(
                    str(reveal.skill_id) for reveal in state.skill_reveals
                ),
                "结算物品": len(state.inventory_items) or "",
            }
        )
    return rows[-8:]


def _latest_fatbeans_grid_batch(batches: Sequence[Any]) -> Any | None:
    from bidking_lab.live.layout import latest_grid_batch

    return latest_grid_batch(batches)


def _fatbeans_grid_item_summary_rows(
    batches: Sequence[Any],
) -> list[dict[str, Any]]:
    selected = _latest_fatbeans_grid_batch(batches)
    if selected is None:
        return []
    by_quality: dict[int | None, dict[str, Any]] = {}
    for item in selected.grid_items:
        row = by_quality.setdefault(
            item.quality,
            {
                "_quality_order": item.quality if item.quality is not None else 99,
                "状态": selected.sequence,
                "品质": (
                    f"{item.quality} {QUALITY_NAME.get(item.quality, '')}"
                    if item.quality is not None
                    else "未知品质轮廓"
                ),
                "件数": 0,
                "格数": 0,
                "示例形状": [],
                "说明": (
                    "可进入品质桶"
                    if item.quality is not None
                    else "只记录轮廓，暂不枚举品质组合"
                ),
            },
        )
        row["件数"] += 1
        row["格数"] += item.cells
        footprint = item.footprint()
        if footprint is not None:
            row["最深行"] = max(int(row.get("最深行") or 0), footprint.bottom_row)
        if item.shape_key and len(row["示例形状"]) < 6:
            row["示例形状"].append(item.shape_key)
    rows = list(by_quality.values())
    for row in rows:
        row["示例形状"] = ", ".join(row["示例形状"])
    rows.sort(key=lambda row: int(row.pop("_quality_order")))
    return rows


def _fatbeans_grid_position_rows(
    batches: Sequence[Any],
    *,
    limit: int = 16,
) -> list[dict[str, Any]]:
    selected = _latest_fatbeans_grid_batch(batches)
    if selected is None:
        return []
    rows: list[dict[str, Any]] = []
    for item in selected.grid_items:
        footprint = item.footprint()
        if footprint is None:
            continue
        rows.append(
            {
                "状态": selected.sequence,
                "品质": (
                    f"{item.quality} {QUALITY_NAME.get(item.quality, '')}"
                    if item.quality is not None
                    else "未知"
                ),
                "item": item.item_id or "",
                "local": 0 if item.local_index is None else item.local_index,
                "形状": f"{footprint.width}x{footprint.height}",
                "行": f"{footprint.row}-{footprint.bottom_row}",
                "列": f"{footprint.col}-{footprint.right_col}",
                "格数": item.cells,
            }
        )
    rows.sort(
        key=lambda row: (
            int(str(row["行"]).split("-")[0]),
            int(str(row["列"]).split("-")[0]),
            str(row["品质"]),
        )
    )
    return rows[:limit]


_GRID_QUALITY_STYLE: dict[int | None, tuple[str, str]] = {
    None: ("rgba(148, 163, 184, 0.28)", "#64748b"),
    1: ("rgba(241, 245, 249, 0.58)", "#94a3b8"),
    2: ("rgba(34, 197, 94, 0.30)", "#22c55e"),
    3: ("rgba(59, 130, 246, 0.30)", "#3b82f6"),
    4: ("rgba(168, 85, 247, 0.32)", "#a855f7"),
    5: ("rgba(234, 179, 8, 0.32)", "#eab308"),
    6: ("rgba(239, 68, 68, 0.34)", "#ef4444"),
}


def _render_fatbeans_grid_preview(batches: Sequence[Any]) -> None:
    from bidking_lab.live.layout import (
        layout_evidence_from_batches,
        layout_grid_view,
    )

    evidence = layout_evidence_from_batches(batches)
    if evidence is None:
        return
    view = layout_grid_view(evidence)

    base_cells: list[str] = []
    for row in range(1, view.rows + 1):
        for col in range(1, view.columns + 1):
            base_cells.append(
                '<div style="'
                f"grid-row:{row};grid-column:{col};"
                "border:1px solid rgba(148,163,184,.22);"
                "background:rgba(15,23,42,.10);"
                '"></div>'
            )

    item_cells: list[str] = []
    for item in view.items:
        bg, border = _GRID_QUALITY_STYLE.get(item.quality, _GRID_QUALITY_STYLE[None])
        label = html.escape(item.label).replace("\n", "<br>")
        title = html.escape(item.tooltip)
        item_cells.append(
            '<div title="'
            + title
            + '" style="'
            f"grid-row:{item.row} / span {item.height};"
            f"grid-column:{item.col} / span {item.width};"
            f"background:{bg};border:2px solid {border};"
            "box-sizing:border-box;border-radius:4px;"
            "display:flex;align-items:center;justify-content:center;"
            "font-size:10px;line-height:1.15;text-align:center;"
            "color:#0f172a;font-weight:600;overflow:hidden;"
            f"z-index:{item.z_index};"
            '">'
            + label
            + "</div>"
        )

    st.caption(view.summary)
    st.markdown(
        '<div style="overflow:auto;max-height:720px;max-width:100%;">'
        '<div style="'
        "display:grid;"
        f"grid-template-columns:repeat({view.columns},34px);"
        "grid-auto-rows:24px;"
        "gap:1px;"
        "padding:6px;"
        "background:rgba(15,23,42,.04);"
        "width:max-content;"
        '">'
        + "".join(base_cells)
        + "".join(item_cells)
        + "</div></div>",
        unsafe_allow_html=True,
    )


def _fatbeans_grid_layout_note(
    batches: Sequence[Any],
) -> str:
    from bidking_lab.live.layout import (
        layout_evidence_from_batches,
        layout_risk_label,
    )

    evidence = layout_evidence_from_batches(batches)
    if evidence is None:
        return ""
    return (
        f"轮廓坐标：已解析 {len(evidence.items)} 件 / {evidence.total_cells} 格，"
        f"最深到第 {evidence.max_row} 行，边界空洞率约 {evidence.sparsity_ratio:.0%}。"
        f"{layout_risk_label(evidence)}；这是布局深度证据，不直接等同于总格数。"
    )


def _fatbeans_layout_replay_rows(events: Any) -> list[dict[str, Any]]:
    from bidking_lab.live import SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY
    from bidking_lab.live.replay import layout_replay_stages
    from bidking_lab.runtime import layout_replay_rows_from_stages

    return list(
        layout_replay_rows_from_stages(
            layout_replay_stages(events),
            comparison_policy=SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY,
        )
    )


def _fatbeans_map_likelihood_summary(
    batches: Sequence[Any],
) -> dict[str, Any] | None:
    """Estimate map likelihood for the latest useful pre-settlement state."""
    from dataclasses import replace

    from bidking_lab.inference.map_likelihood import estimate_map_likelihood
    from bidking_lab.inference.tool_info_roi import estimate_tool_info_roi
    from bidking_lab.inference.warehouse_estimator import estimate_warehouse_cells
    from bidking_lab.live import (
        LiveSessionState,
        apply_observation_batch,
        live_state_to_session_obs,
    )

    maps_, drops_, items_ = _load_tables()
    final_state = LiveSessionState()
    pre_settlement_state = LiveSessionState()
    saw_pre_settlement = False
    for batch in batches:
        final_state = apply_observation_batch(final_state, batch)
        if getattr(batch, "phase", None) != "settled":
            pre_settlement_state = apply_observation_batch(
                pre_settlement_state,
                batch,
            )
            saw_pre_settlement = True

    base_session = (
        live_state_to_session_obs(pre_settlement_state)
        if saw_pre_settlement
        else None
    )
    evidence_label = "结算前最后状态"
    if base_session is None:
        base_session = live_state_to_session_obs(final_state)
        evidence_label = "最新状态"
    if base_session is None:
        return None

    final_session = live_state_to_session_obs(final_state)
    candidate_map_ids = _candidate_map_ids_for_likelihood(base_session, maps_)
    n_trials = 500
    seed = 20260528
    cells_tol = 8
    count_tol = 3
    without_warehouse = replace(
        base_session,
        warehouse_total_cells=None,
        warehouse_total_cells_approx=None,
        warehouse_total_cells_tolerance=None,
        total_item_count=None,
    )
    base_results = estimate_map_likelihood(
        candidate_map_ids,
        without_warehouse,
        maps=maps_,
        drops=drops_,
        items=items_,
        n_trials=n_trials,
        seed=seed,
        cells_tol=cells_tol,
        count_tol=count_tol,
    )
    warehouse_estimate = estimate_warehouse_cells(
        candidate_map_ids,
        without_warehouse,
        maps=maps_,
        drops=drops_,
        items=items_,
        n_trials=n_trials,
        seed=seed,
        cells_tol=cells_tol,
        count_tol=count_tol,
    )
    tool_info_roi = estimate_tool_info_roi(
        candidate_map_ids,
        without_warehouse,
        maps=maps_,
        drops=drops_,
        items=items_,
        n_trials=250,
        seed=seed + 1,
        cells_tol=cells_tol,
        count_tol=count_tol,
    )

    with_results: list[Any] = []
    compression_rows: list[dict[str, Any]] = []
    final_warehouse = (
        final_session.warehouse_total_cells
        if final_session is not None
        else None
    )
    if final_warehouse is not None:
        with_warehouse = replace(
            without_warehouse,
            warehouse_total_cells=final_warehouse,
            warehouse_total_cells_approx=None,
            warehouse_total_cells_tolerance=0,
        )
        with_results = estimate_map_likelihood(
            candidate_map_ids,
            with_warehouse,
            maps=maps_,
            drops=drops_,
            items=items_,
            n_trials=n_trials,
            seed=seed,
            cells_tol=cells_tol,
            count_tol=count_tol,
        )
        before = base_results[0] if base_results else None
        after = with_results[0] if with_results else None
        compression_rows.append(
            {
                "对比": "加入总仓储格数",
                "总仓储": final_warehouse,
                "Top后验 前": (
                    f"{before.posterior_probability:.1%}" if before else ""
                ),
                "Top后验 后": (
                    f"{after.posterior_probability:.1%}" if after else ""
                ),
                "Top价值宽度 前": (
                    _format_quantile_width(before.total_value) if before else ""
                ),
                "Top价值宽度 后": (
                    _format_quantile_width(after.total_value) if after else ""
                ),
            }
        )

    rows = _map_likelihood_result_rows(base_results, evidence_label)
    best_result = base_results[0] if base_results else None
    best_evidence_label = evidence_label
    if with_results:
        rows.extend(_map_likelihood_result_rows(with_results, f"{evidence_label}+总仓储"))
        if with_results[0].total_value is not None:
            best_result = with_results[0]
            best_evidence_label = f"{evidence_label}+总仓储"
    return {
        "evidence_label": evidence_label,
        "candidate_count": len(candidate_map_ids),
        "n_trials": n_trials,
        "tolerance": f"cells±{cells_tol}, count±{count_tol}",
        "rows": rows,
        "compression_rows": compression_rows,
        "warehouse_estimate": warehouse_estimate,
        "warehouse_estimate_rows": _warehouse_estimate_rows(warehouse_estimate),
        "tool_info_roi_rows": _tool_info_roi_rows(tool_info_roi),
        "layout_note": _fatbeans_grid_layout_note(batches),
        "best_value_summary": (
            best_result.total_value if best_result is not None else None
        ),
        "best_evidence_label": best_evidence_label,
    }


def _render_fatbeans_json_importer() -> None:
    """Import a Fatbeans JSON export into live shadow state."""
    from bidking_lab.live import (
        LiveSessionState,
        latest_player_bids,
        live_batches_from_fatbeans_capture_payload,
        live_state_to_session_obs,
        parse_fatbeans_capture_payload,
    )

    with st.expander("Fatbeans JSON 导入（packet shadow）", expanded=False):
        st.caption(
            "导入 Fatbeans 导出的 JSON；只写入本地 live shadow，当前不会自动控制游戏。"
            "对手出价先作为诊断展示，暂不进入推理输入。"
        )
        rev = int(st.session_state.get("fatbeans_upload_rev", 0))
        upload = st.file_uploader(
            "选择 Fatbeans JSON",
            type=["json"],
            key=f"fatbeans_json_uploader_{rev}",
        )
        c1, c2, c3 = st.columns(3)
        import_clicked = c1.button(
            "导入到 live shadow",
            disabled=upload is None,
            key="fatbeans_import_live_shadow",
            **layout_width_kwargs("button", stretch=True),
        )
        clear_clicked = c2.button(
            "清空选择",
            key="fatbeans_clear_upload",
            **layout_width_kwargs("button", stretch=True),
        )
        reset_clicked = c3.button(
            "重置 shadow",
            key="fatbeans_reset_live_shadow",
            **layout_width_kwargs("button", stretch=True),
        )
        if clear_clicked:
            st.session_state["fatbeans_upload_rev"] = rev + 1
            st.rerun()
        if reset_clicked:
            for key in (
                "_live_session_state",
                "_last_live_blocked_updates",
                "_last_live_observation_batch",
                "_last_fatbeans_import_summary",
            ):
                st.session_state.pop(key, None)
            st.rerun()

        if import_clicked and upload is not None:
            try:
                from bidking_lab.runtime import (
                    action_result_rows_from_results,
                    packet_action_rows_from_sends,
                    player_bid_candidate_rows_from_bids,
                )

                raw = upload.getvalue()
                events = parse_fatbeans_capture_payload(raw)
                batches = live_batches_from_fatbeans_capture_payload(raw)
                applied = _apply_live_packet_batches(batches)
                live_state = st.session_state.get("_live_session_state")
                live_session = (
                    live_state_to_session_obs(live_state)
                    if isinstance(live_state, LiveSessionState)
                    else None
                )
                st.session_state["use_live_canonical_input"] = True
                latest_state = events.states[-1] if events.states else None
                inventory_items = (
                    latest_state.inventory_items
                    if latest_state is not None
                    else ()
                )
                quality_rows, known_value_sum, missing_item_ids = (
                    _fatbeans_inventory_summary_rows(inventory_items)
                )
                battle_item_names = _battle_item_names_by_id()
                map_likelihood = _fatbeans_map_likelihood_summary(batches)
                latest_bids = latest_player_bids(events.states)
                bid_advice_rows = (
                    _bid_advice_rows(
                        latest_bids=latest_bids,
                        value_summary=map_likelihood.get("best_value_summary"),
                        evidence_label=str(
                            map_likelihood.get("best_evidence_label") or ""
                        ),
                        round_no=latest_state.round_no if latest_state else None,
                        total_rounds=None,
                        posterior_samples=int(map_likelihood.get("n_trials") or 0),
                        warehouse_estimate=map_likelihood.get("warehouse_estimate"),
                        session=live_session,
                    )
                    if isinstance(map_likelihood, Mapping)
                    else []
                )
                layout_replay_rows = _fatbeans_layout_replay_rows(events)
                st.session_state["_last_fatbeans_import_summary"] = {
                    "file": upload.name,
                    "packets": len(events.packets),
                    "frames": len(events.frames),
                    "states": len(events.states),
                    "batches": len(batches),
                    "applied": applied,
                    "map_id": latest_state.map_id if latest_state else None,
                    "round": latest_state.round_no if latest_state else None,
                    "inventory_count": len(inventory_items),
                    "inventory_cells": sum(item.cells for item in inventory_items),
                    "known_value_sum": known_value_sum,
                    "missing_item_ids": missing_item_ids,
                    "quality_rows": quality_rows,
                    "grid_summary_rows": _fatbeans_grid_item_summary_rows(batches),
                    "grid_batches": batches,
                    "grid_position_rows": _fatbeans_grid_position_rows(batches),
                    "layout_replay_rows": layout_replay_rows,
                    "layout_stage_rows": _brief_layout_stage_rows(
                        layout_replay_rows,
                    ),
                    "state_round_rows": _fatbeans_state_round_rows(events),
                    "map_likelihood": map_likelihood,
                    "latest_bids": dict(latest_bids),
                    "bid_advice_rows": bid_advice_rows,
                    "tool_rows": list(
                        packet_action_rows_from_sends(
                            events.sends,
                            battle_item_names,
                        )
                    ),
                    "result_rows": list(
                        action_result_rows_from_results(
                            latest_state.action_results if latest_state else (),
                            battle_item_names,
                        )
                    ),
                    "bid_rows": list(
                        player_bid_candidate_rows_from_bids(
                            latest_state.bids if latest_state else (),
                        )
                    ),
                }
                st.success(f"已导入 {applied} 个 packet live batch。")
            except Exception as exc:  # noqa: BLE001 - user-facing import boundary
                st.error(f"Fatbeans JSON 解析失败：{exc}")

        summary = st.session_state.get("_last_fatbeans_import_summary")
        if isinstance(summary, Mapping):
            from bidking_lab.runtime import import_overview_from_summary

            overview = import_overview_from_summary(summary)
            rows = [
                {
                    "文件": overview.file,
                    "packets": overview.packets,
                    "frames": overview.frames,
                    "states": overview.states,
                    "live batches": overview.live_batches,
                    "map": overview.map_id,
                    "round": overview.round_no,
                    "结算件数": overview.settlement_items,
                    "结算格数": overview.settlement_cells,
                    "已知战利品价值": overview.known_loot_value,
                }
            ]
            _render_records_table(rows)
            missing_item_ids = summary.get("missing_item_ids")
            if isinstance(missing_item_ids, list) and missing_item_ids:
                st.warning(
                    "有结算 item_id 不在当前本地 Item 表中，战利品价值为已知部分合计："
                    + ", ".join(str(x) for x in missing_item_ids)
                )
            map_likelihood = summary.get("map_likelihood")
            if isinstance(map_likelihood, Mapping):
                warehouse_rows = map_likelihood.get("warehouse_estimate_rows")
                warehouse_summary_rows = (
                    _brief_warehouse_estimate_rows(warehouse_rows)
                    if isinstance(warehouse_rows, list)
                    else []
                )
                tool_info_rows = map_likelihood.get("tool_info_roi_rows")
                tool_summary_rows = (
                    _brief_tool_info_roi_rows(tool_info_rows)
                    if isinstance(tool_info_rows, list)
                    else []
                )
                bid_advice_rows = summary.get("bid_advice_rows")
                bid_summary_rows = (
                    bid_advice_rows
                    if isinstance(bid_advice_rows, list)
                    else []
                )
                _render_fatbeans_tactical_summary(
                    summary,
                    warehouse_summary_rows=warehouse_summary_rows,
                    tool_summary_rows=tool_summary_rows,
                    bid_advice_rows=bid_summary_rows,
                )
                with st.expander("展开 Fatbeans 推理详细诊断", expanded=False):
                    rows = map_likelihood.get("rows")
                    if isinstance(rows, list) and rows:
                        st.caption(
                            "地图似然诊断：基于 live shadow 里当前可用的品质桶/道具证据。"
                        )
                        st.caption(
                            f"候选地图 {map_likelihood.get('candidate_count')} 张；"
                            f"每图 MC {map_likelihood.get('n_trials')} 次；"
                            f"探索容差 {map_likelihood.get('tolerance')}。"
                        )
                        _render_records_table(rows)
                    if isinstance(warehouse_rows, list) and warehouse_rows:
                        st.caption(
                            "仓储估计：不使用最终总格数，只用当前品质桶/道具证据反推总格区间。"
                        )
                        _render_records_table(warehouse_rows)
                    if isinstance(tool_info_rows, list) and tool_info_rows:
                        st.caption(
                            "补信息 ROI：估算再用一张道具能压缩多少价值/仓储区间，"
                            "当前优先用于比较低成本常态道具。"
                        )
                        _render_records_table(tool_info_rows)
                    compression_rows = map_likelihood.get("compression_rows")
                    if isinstance(compression_rows, list) and compression_rows:
                        st.caption("总仓储空间的信息压缩效果（用结算总格模拟该道具读数）：")
                        _render_records_table(compression_rows)
            bid_advice_rows = summary.get("bid_advice_rows")
            if not isinstance(map_likelihood, Mapping) and isinstance(bid_advice_rows, list) and bid_advice_rows:
                st.caption(
                    "实时出价建议 v1（诊断）：用当前最高价对比价值区间，"
                    "不自动出价；停止价暂按 P90 × 1.05。"
                )
                _render_records_table(bid_advice_rows[:1])
            _render_fatbeans_detail_tables(summary)


def _render_canonical_input_diagnostic(
    obs_state: dict[str, Any],
    maps: Mapping[int, BidMap],
) -> None:
    from bidking_lab.live import (
        LiveSessionState,
        compare_session_obs,
        live_session_matches_context,
        live_state_to_session_obs,
    )

    source = str(st.session_state.get("_canonical_input_source", "legacy"))
    live_state = st.session_state.get("_live_session_state")
    live_session = (
        live_state_to_session_obs(live_state)
        if isinstance(live_state, LiveSessionState)
        else None
    )
    live_context_ok = live_session_matches_context(
        live_session,
        map_id=obs_state.get("map_id"),
        warehouse_total_cells=obs_state.get("warehouse_cells"),
    )
    legacy_session = None
    legacy_error = None
    try:
        legacy_session = _build_session(obs_state, maps)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        legacy_error = str(exc)

    rows = list(compare_session_obs(legacy_session, live_session))
    with st.expander("canonical input 对照诊断", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("当前实际输入", source)
        c2.metric("live 可用", "是" if live_session is not None else "否")
        c3.metric(
            "live 上下文",
            "匹配" if live_context_ok else "不匹配",
        )

        if legacy_error:
            st.warning(f"legacy session 暂不可构建：{legacy_error}")
        if live_session is None:
            st.info("live shadow 还没有足够字段构建 SessionObs。")
            return
        if not live_context_ok:
            st.warning(
                "live shadow 的地图或仓库总格与当前 UI 不一致；"
                "灰度开关打开时会自动回退 legacy。"
            )
        if not rows and legacy_error is None:
            st.success("live 与 legacy 的 SessionObs 字段一致。")
            return
        if rows:
            st.caption(
                "以下差异只比较推理输入，不代表 MC 已运行。"
                "若差异符合预期，可继续观察；若不符合，应先修 adapter 再默认切 live。"
            )
            _render_records_table(rows)


def _apply_pending_capture(
    obs_state: dict,
    *,
    warehouse_cells: int | None = None,
    total_item_count: int | None = None,
    map_names: dict[int, str],
) -> None:
    """Apply queued OCR parse before map/warehouse widgets render."""
    if "_pending_capture" not in st.session_state:
        return
    from bidking_lab.capture.apply import (
        READING_KEYS,
        READING_WIDGET_KEYS,
        apply_capture_result,
        clear_readings_for_map_change,
        ocr_should_clear_readings,
    )

    snap = st.session_state.pop("_pre_ocr_ui_snapshot", {})
    _before_capture_obs = dict(obs_state)
    _map_before_ocr = obs_state.get("map_id")
    _cap_result = st.session_state.pop("_pending_capture")
    from bidking_lab.inference.readings_validate import check_warehouse_cell_budget

    if ocr_should_clear_readings(_cap_result, _map_before_ocr):
        clear_readings_for_map_change(obs_state, st.session_state)
    _apply_log = apply_capture_result(
        _cap_result, obs_state, st.session_state, map_names=map_names,
    )
    _pipe = st.session_state.pop("_capture_pipeline_log", None)
    if _pipe:
        st.session_state["_capture_apply_log"] = list(_pipe) + _apply_log
    else:
        st.session_state["_capture_apply_log"] = _apply_log
    _applied_keys = {s.key for s in _cap_result.suggestions}
    _keep_wh = _pick_preserved_int(
        "obs_warehouse_cells", widget_val=warehouse_cells, snap=snap,
    )
    _keep_tic = _pick_preserved_int(
        "obs_total_item_count", widget_val=total_item_count, snap=snap,
    )
    if _cap_result.map_id is None:
        if snap.get("obs_map_category") is not None:
            st.session_state["obs_map_category"] = snap["obs_map_category"]
        _mk = snap.get("_map_widget_key") or _map_select_widget_key()
        _mv = snap.get("_map_widget_val")
        if _mv is not None:
            st.session_state[_mk] = _mv
            _mid = _mv if isinstance(_mv, int) else None
            if _mid is not None:
                obs_state["map_id"] = _mid
    if "warehouse_cells" not in _applied_keys and _keep_wh is not None:
        st.session_state["obs_warehouse_cells"] = _keep_wh
        obs_state["warehouse_cells"] = _keep_wh
        if snap.get("obs_warehouse_cells_mode") is not None:
            st.session_state["obs_warehouse_cells_mode"] = snap[
                "obs_warehouse_cells_mode"
            ]
            obs_state["warehouse_cells_mode"] = snap["obs_warehouse_cells_mode"]
        if snap.get("obs_warehouse_cells_tolerance") is not None:
            st.session_state["obs_warehouse_cells_tolerance"] = snap[
                "obs_warehouse_cells_tolerance"
            ]
            obs_state["warehouse_cells_tolerance"] = snap[
                "obs_warehouse_cells_tolerance"
            ]
    elif "warehouse_cells" in _applied_keys:
        st.session_state["obs_warehouse_cells_mode"] = "strict"
        obs_state["warehouse_cells_mode"] = "strict"
        obs_state.pop("warehouse_cells_tolerance", None)
    if "total_item_count" not in _applied_keys and _keep_tic is not None:
        st.session_state["obs_total_item_count"] = _keep_tic
        obs_state["total_item_count"] = _keep_tic
    if _cap_result.map_id is not None:
        from bidking_lab.capture.apply import mark_ocr_map_applied_to_ui

        mark_ocr_map_applied_to_ui(
            st.session_state,
            int(_cap_result.map_id),
            category=st.session_state.get("obs_map_category"),
        )
    _record_live_observation_snapshot(
        obs_state,
        source="ocr",
        event_kind="ocr_update",
        previous=_before_capture_obs,
    )
    st.session_state["_capture_just_applied"] = True
    st.session_state["_force_hydrate_avg_raw"] = True
    st.session_state["_ocr_refill_numeric"] = True
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_apply_pending_capture:done",
        message="pending_capture applied to obs",
        data={
            "main_tab": st.session_state.get("_main_tab"),
            "apply_log_lines": len(_apply_log),
            "obs_keys": sorted(
                k for k in obs_state
                if k in READING_KEYS
                or k.endswith("_avg_raw")
                or k.endswith("_avg_value")
            ),
            "gold_count": obs_state.get("gold_count"),
            "gold_avg_value": obs_state.get("gold_avg_value"),
            "gold_avg_raw": obs_state.get("gold_avg_raw"),
        },
        hypothesis_id="H12",
        run_id="post-fix",
    )
    # #endregion
    _ocr_map_miss = (
        _cap_result.map_id is None
        and bool(_cap_result.suggestions)
        and obs_state.get("map_id") is None
    )
    if _ocr_map_miss:
        st.session_state["_capture_map_miss"] = True
    else:
        st.session_state.pop("_capture_map_miss", None)
    try:
        from bidking_lab.capture.diag import MapResolutionDiag, record_capture_session

        _diag = _cap_result.map_diag or MapResolutionDiag(status="no_map_line")
        _map_after = obs_state.get("map_id")
        _auto_sw = (
            _cap_result.map_id is not None
            and (
                _map_before_ocr is None
                or int(_cap_result.map_id) != int(_map_before_ocr)
            )
        )
        record_capture_session(
            source=str(st.session_state.pop("_last_capture_source", "unknown")),
            crop_panel=bool(st.session_state.pop("_last_capture_crop", True)),
            map_diag=_diag,
            suggestion_keys=list(_cap_result.suggestion_map().keys()),
            apply_map_id_before=int(_map_before_ocr) if _map_before_ocr is not None else None,
            apply_map_id_after=int(_map_after) if _map_after is not None else None,
            map_auto_switched=_auto_sw if _cap_result.map_id is not None else False,
            user_map_preserved=_cap_result.map_id is None and _map_after is not None,
        )
    except Exception as _diag_exc:
        logging.getLogger("bidking_lab.capture").warning(
            "capture diag log failed: %s", _diag_exc,
        )
    if st.session_state.get("auto_infer_after_capture", True):
        st.session_state["_request_bg_hint_capture"] = True
        st.session_state["_pending_hint_nudge"] = True
        _maybe_switch_to_hint_tab_after_capture()
        if int(obs_state.get("warehouse_cells") or 0) <= 0:
            st.session_state["_awaiting_warehouse_for_infer"] = True
    st.session_state.pop("_hint_bundle", None)
    st.session_state.pop("_bg_infer_box", None)
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_apply_pending_capture",
        message="OCR capture applied",
        data={
            "main_tab": st.session_state.get("_main_tab"),
            "auto_infer": bool(st.session_state.get("auto_infer_after_capture", True)),
            "map_id_after": obs_state.get("map_id"),
            "inference_ready_wh": obs_state.get("warehouse_cells"),
        },
        hypothesis_id="H1,H2,H14",
    )
    # #endregion


def _clear_capture_upload() -> None:
    """Force user to re-upload after manual map change."""
    old_rev = int(st.session_state.get("capture_upload_rev", 0))
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith("capture_file_uploader_"):
            st.session_state.pop(key, None)
    st.session_state["capture_upload_rev"] = old_rev + 1
    st.session_state.pop("_pending_capture", None)


def _clear_hint_done_flash() -> None:
    """Clear completed-state banners only (keep nudge when starting a new run)."""
    for _k in (
        "_hint_tab_done_flash",
        "_hint_infer_until",
        "_hint_done_toast_shown",
    ):
        st.session_state.pop(_k, None)


def _clear_hint_ui_banners() -> None:
    """Drop all hint banners (map change / manual cancel)."""
    _clear_hint_done_flash()
    st.session_state.pop("_nudge_hint_tab", None)
    st.session_state.pop("_pending_hint_nudge", None)


def _cancel_background_hint() -> None:
    box = st.session_state.get("_bg_infer_box")
    if box and box.get("cancel") is not None:
        box["cancel"].set()
    st.session_state.pop("_bg_infer_box", None)
    st.session_state.pop("_hint_bundle", None)
    st.session_state.pop("_request_bg_hint_capture", None)
    st.session_state.pop("_request_bg_hint_manual", None)
    st.session_state.pop("_request_bg_hint_live_dirty", None)
    st.session_state.pop("_awaiting_warehouse_for_infer", None)
    _clear_hint_ui_banners()
    st.session_state["_bg_infer_status"] = "idle"


def _on_map_context_changed(
    new_mid: int | None,
    prev_mid: int | None,
    *,
    category_changed: bool = False,
) -> None:
    if not category_changed:
        if prev_mid is None:
            return
        if new_mid is not None and new_mid == prev_mid:
            return
    from bidking_lab.capture.apply import reset_obs_for_manual_map_change

    reset_obs_for_manual_map_change(state, st.session_state, new_map_id=new_mid)
    _bump_map_select_widget()
    _sync_map_select_widget_value(new_mid)
    _clear_capture_upload()
    _cancel_background_hint()
    st.session_state.pop("_capture_apply_log", None)
    if "seen_shapes" in st.session_state:
        st.session_state["seen_shapes"] = {
            s: 0 for s in st.session_state["seen_shapes"]
        }
    st.session_state["_map_change_toast"] = True


def _on_obs_map_category_changed() -> None:
    cat = st.session_state.get("obs_map_category")
    prev_cat = st.session_state.get("_tracked_map_category")
    prev_mid = st.session_state.get("_tracked_map_id")
    if prev_cat is not None and cat != prev_cat:
        _on_map_context_changed(None, prev_mid, category_changed=True)
        st.session_state["_tracked_map_id"] = None
    st.session_state["_tracked_map_category"] = cat


def _execute_deferred_capture(
    job: dict,
    *,
    map_names: dict[int, str],
) -> None:
    """Run capture+OCR on a lightweight rerun (before sidebar / tab bodies)."""
    import time as _time

    kind = str(job.get("kind", ""))
    _t0 = _time.perf_counter()
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_execute_deferred_capture:begin",
        message="deferred capture begin",
        data={"kind": kind, "main_tab": st.session_state.get("_main_tab")},
        hypothesis_id="H12,H13",
        run_id="post-fix",
    )
    # #endregion
    _qerr: str | None = None
    _status_lbl = job.get("status_label", "\u6293\u5c4f\u4e0e OCR \u8bc6\u522b\u4e2d\u2026")
    if st.session_state.get("_main_tab") == "hint":
        _status_lbl = f"{_status_lbl}\uff08\u51fa\u4ef7\u63a8\u8350\u6807\u7b7e\u5df2\u9009\u4e2d\uff09"
    try:
        with st.status(_status_lbl, expanded=False) as _cap_status:
            if st.session_state.get("_ocr_show_first_grab_notice"):
                st.caption(
                    "\u2139\ufe0f \u9996\u6b21 OCR \u6293\u5c4f\u901a\u5e38\u6bd4\u4e4b\u540e\u6162\uff1a"
                    "\u8bc6\u522b\u6a21\u578b\u5c06\u5728\u7b2c\u4e00\u6b21\u5b9e\u9645\u4f7f\u7528\u65f6\u52a0\u8f7d\u3002"
                )
            if kind == "screen":
                from bidking_lab.capture.screen import (
                    ScreenCaptureConfig,
                    capture_monitor_panel,
                )

                _delay = int(job.get("delay_sec") or 0)
                if _delay > 0:
                    st.caption(
                        f"\u8bf7\u5207\u6362\u5230\u6e38\u620f\u7a97\u53e3\uff0c**{_delay}** \u79d2\u540e\u6293\u5c4f\u2026",
                    )
                    _time.sleep(_delay)
                _mon_idx = int(job["monitor_index"])
                _t_cap = _time.perf_counter()
                _cap = capture_monitor_panel(
                    ScreenCaptureConfig(
                        monitor_index=_mon_idx,
                        include_monitor_preview=False,
                    ),
                )
                _grab_ms = int((_time.perf_counter() - _t_cap) * 1000)
                _cap_status.update(label="\u6293\u5c4f\u5b8c\u6210\uff0cOCR \u8bc6\u522b\u4e2d\u2026")
                _qerr = _queue_capture_from_bytes(
                    _cap.panel_png,
                    map_names=map_names,
                    crop_panel=False,
                    source=(
                        f"screen #{_cap.monitor.index} "
                        f"{_cap.monitor.width}x{_cap.monitor.height}"
                    ),
                    capture_debug={
                        "monitor_index": _cap.monitor.index,
                        "monitor_label": job.get("monitor_label", ""),
                        "crop_frac": _cap.crop_frac,
                        "crop_box": _cap.crop_box,
                        "monitor_preview_png": _cap.monitor_preview_png,
                        "grab_ms": _grab_ms,
                    },
                )
            elif kind == "clipboard":
                from bidking_lab.capture.clipboard import clipboard_image_bytes

                _clip_data, _clip_err = clipboard_image_bytes()
                if _clip_err:
                    st.warning(_clip_err)
                    _qerr = _clip_err
                else:
                    _qerr = _queue_capture_from_bytes(
                        _clip_data, map_names=map_names, source="clipboard",
                    )
            elif kind == "upload":
                _qerr = _queue_capture_from_bytes(
                    job["data"],
                    map_names=map_names,
                    source="upload",
                )
            else:
                st.warning(f"\u672a\u77e5\u6293\u5c4f\u4efb\u52a1: {kind}")
        st.session_state["_capture_debug_expand"] = True
    finally:
        st.session_state.pop("_capture_in_progress", None)
    if _qerr:
        st.warning(_qerr)
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:_execute_deferred_capture:end",
        message="deferred capture end",
        data={
            "kind": kind,
            "elapsed_ms": int((_time.perf_counter() - _t0) * 1000),
            "has_pending": "_pending_capture" in st.session_state,
            "qerr": bool(_qerr),
        },
        hypothesis_id="H12,H13",
        run_id="post-fix",
    )
    # #endregion
    # #region agent log
    agent_phase_log(
        phase="deferred_capture_end",
        hypothesis_id="H1,H12",
        kind=kind,
        elapsed_ms=int((_time.perf_counter() - _t0) * 1000),
    )
    # #endregion


def _on_obs_map_select_changed() -> None:
    cat = st.session_state.get("obs_map_category", "mansion")
    choices = _maps_for_category(maps, cat)
    new_mid = _coerce_map_select(
        st.session_state.get(_map_select_widget_key()), choices,
    )
    prev_mid = st.session_state.get("_tracked_map_id")
    if prev_mid is not None and new_mid != prev_mid:
        _on_map_context_changed(new_mid, prev_mid)
    elif prev_mid is None and new_mid is not None:
        pass
    st.session_state["_tracked_map_id"] = new_mid
    if new_mid is not None:
        st.session_state.pop("_capture_map_miss", None)


# Apply OCR results before sidebar/main widgets so first paint has filled values.
_apply_pending_capture(state, map_names=_map_names)
# #region agent log
agent_phase_log(
    phase="after_apply_pending_capture",
    hypothesis_id="H1,H4",
    request_bg_hint_capture=bool(
        st.session_state.get("_request_bg_hint_capture")
    ),
    request_bg_hint_manual=bool(
        st.session_state.get("_request_bg_hint_manual")
    ),
    pending_nudge=bool(st.session_state.get("_pending_hint_nudge")),
)
# #endregion

_deferred_capture_pending = st.session_state.pop("_deferred_capture_job", None)

# Tab strip before sidebar so obs↔hint switches paint the main column first (sidebar still loads this run).
_infer_status_for_ui = (
    "running"
    if st.session_state.get("_bg_infer_box") is not None
    else str(st.session_state.get("_bg_infer_status", "idle"))
)
_main_tab = _render_persisted_tab_nav(
    infer_status=_infer_status_for_ui,
    done_flash=bool(st.session_state.get("_hint_tab_done_flash")),
)

# #region agent log
agent_phase_log(phase="before_sidebar", hypothesis_id="H1,H3")
# #endregion

with st.sidebar:
    if st.session_state.pop("_ocr_first_grab_toast", False):
        st.toast(
            "首次 OCR 已完成。同一会话内再次抓屏通常会更快。",
            icon="\u2139\ufe0f",
        )
    if st.session_state.get("ocr_ui_status") == "ready":
        render_status_banner(
            kind="ready",
            message="OCR 已就绪",
            detail="",
        )
    elif st.session_state.get("ocr_ui_status") == "error":
        render_status_banner(
            kind="error",
            message="OCR 引擎未就绪",
            detail=str(st.session_state.get("ocr_ui_error", ""))[:200],
        )
    else:
        st.caption("OCR 按需加载：手填可立即使用；首次点击 OCR 时模型才会初始化。")

    sidebar_section("\u4f1a\u8bdd", variant="session", icon="\U0001f3ae")

    hero = st.radio(
        "\u82f1\u96c4",
        options=["ethan", "aisha"],
        format_func=lambda h: HERO_LABELS[h],
        horizontal=True,
        key="obs_hero",
    )

    category = st.radio(
        "\u5730\u56fe\u7c7b\u578b",
        options=list(MAP_CATEGORIES.keys()),
        format_func=lambda c: CATEGORY_LABELS[c],
        horizontal=True,
        label_visibility="collapsed",
        key="obs_map_category",
        on_change=_on_obs_map_category_changed,
    )
    map_choices = _maps_for_category(maps, category)
    if not map_choices:
        st.error(
            "\u672a\u80fd\u5728 BidMap.txt \u4e2d\u627e\u5230\u8be5\u7c7b\u578b\u5730\u56fe\u3002"
        )
        st.stop()
    _map_key = _map_select_widget_key()
    if "obs_map_select" in st.session_state and _map_key not in st.session_state:
        st.session_state[_map_key] = st.session_state.pop("obs_map_select")
    _stale_raw = st.session_state.get(_map_key)
    _stale_coerced = _coerce_map_select(_stale_raw, map_choices)
    if _stale_raw is not None and _stale_coerced is None:
        from bidking_lab.capture.apply import reset_obs_for_manual_map_change

        reset_obs_for_manual_map_change(state, st.session_state, new_map_id=None)
        _cancel_background_hint()
        _bump_map_select_widget()
        _map_key = _map_select_widget_key()
        st.session_state.pop(_map_key, None)
        st.session_state.pop("obs_map_select", None)
        st.session_state["_tracked_map_id"] = None
        st.warning(
            "\u5f53\u524d\u5730\u56fe\u4e0e\u300c\u522b\u5885/\u6c89\u8239\u300d\u7c7b\u522b\u4e0d\u5339\u914d\uff0c"
            "\u5df2\u6e05\u7a7a\u8bfb\u6570\u4e0e\u51fa\u4ef7\u7f13\u5b58\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u3002"
        )
    elif _stale_coerced is not None and _stale_raw != _stale_coerced:
        st.session_state[_map_key] = _stale_coerced
    if "_tracked_map_id" not in st.session_state:
        st.session_state["_tracked_map_id"] = _resolved_map_select(map_choices)
    if "_tracked_map_category" not in st.session_state:
        st.session_state["_tracked_map_category"] = category
    # Pre-seed selectbox from obs before widget exists (cannot set key after st.selectbox).
    if _map_key not in st.session_state:
        _pre_mid = _coerce_map_select(state.get("map_id"), map_choices)
        if _pre_mid is None:
            _pre_mid = _coerce_map_select(
                st.session_state.get("_tracked_map_id"), map_choices,
            )
        if _pre_mid is not None:
            st.session_state[_map_key] = _pre_mid
    map_id = st.selectbox(
        "\u5177\u4f53\u5730\u56fe",
        options=list(map_choices.keys()),
        format_func=lambda mid: map_choices[mid],
        index=None,
        placeholder="\u8bf7\u9009\u62e9\u5177\u4f53\u5730\u56fe...",
        key=_map_key,
        on_change=_on_obs_map_select_changed,
        help="\u6309\u96be\u5ea6\u00d7\u53d8\u79cd\u6392\u5e8f\u3002\u624b\u52a8\u6362\u56fe\u6216\u70b9 \u00d7 \u6e05\u7a7a\u4f1a\u91cd\u7f6e\u8bfb\u6570\u4e0e\u622a\u56fe\u3002",
    )
    _resolved_mid = _effective_map_id(
        map_choices, selectbox_return=map_id, obs=state,
    )
    _tracked_mid = st.session_state.get("_tracked_map_id")
    if (
        _resolved_mid is None
        and _tracked_mid is not None
        and not st.session_state.get("_suppress_map_change_reset")
    ):
        _on_map_context_changed(None, _tracked_mid)
        _tracked_mid = None
    elif _resolved_mid is not None:
        st.session_state["_tracked_map_id"] = _resolved_mid
    _post_sync_reset = (
        _tracked_mid is not None
        and _resolved_mid is not None
        and _resolved_mid != _tracked_mid
        and not st.session_state.get("_map_change_toast")
    )
    if _post_sync_reset:
        if st.session_state.pop("_suppress_map_change_reset", False):
            st.session_state["_tracked_map_id"] = _resolved_mid
        else:
            # #region agent log
            agent_debug_log(
                location="streamlit_app.py:sidebar:post_sync_reset",
                message="map select out of sync; resetting readings",
                data={
                    "tracked_mid": _tracked_mid,
                    "resolved_mid": _resolved_mid,
                    "main_tab": st.session_state.get("_main_tab"),
                },
                hypothesis_id="H15,H16",
                run_id="post-fix",
            )
            # #endregion
            _on_map_context_changed(_resolved_mid, _tracked_mid)
            _tracked_mid = _resolved_mid
    if map_id is None:
        st.caption("\U0001f446 \u8bf7\u9009\u62e9\u5177\u4f53\u5730\u56fe")
    else:
        _map = maps.get(map_id)
        if _map is not None:
            with st.expander("\U0001F4CD \u5730\u56fe\u9759\u6001\u4fe1\u606f",
                              expanded=False):
                tier_label = {
                    "ui_value_low": "\u4f4e\u4ef7\u4f4d",
                    "ui_value_higher": "\u4e2d\u9ad8\u4ef7\u4f4d",
                    "ui_value_high": "\u9ad8\u4ef7\u4f4d",
                }.get(_map.value_tier_ui, _map.value_tier_ui)
                skill_groups_display = " / ".join(
                    f"R{i+1}=\u4fe1\u606f\u6280\u80fd\u7ec4 {group_id}"
                    for i, group_id in enumerate(_map.round_skill_group_ids)
                    if group_id != 0
                ) or "\u65e0\u63d0\u793a"
                ladder = ",".join(f"{x:,}" for x in _map.bid_price_ladder)
                st.markdown(
                    f"**\u4ef6\u6570\u8303\u56f4**\uff1a{_map.items_per_session_min}\u2013"
                    f"{_map.items_per_session_max} \u4ef6\n\n"
                    f"**\u8d77\u6b65\u9884\u7b97**\uff1a{_map.starting_budget_silver:,} silver\n\n"
                    f"**\u5165\u573a\u8d39**\uff1a{_map.entry_fee_silver:,} silver\n\n"
                    f"**\u4ef7\u503c\u6863\u6b21**\uff1a{tier_label}\n\n"
                    f"**\u9010\u8f6e\u4fe1\u606f\u6280\u80fd\u7ec4**\uff1a{skill_groups_display}\n\n"
                    f"**\u51fa\u4ef7\u68af\u5ea6**\uff1a{ladder}"
                )
                st.caption(
                    "\u8fd9\u4e9b\u6570\u503c\u662f\u5730\u56fe\u9884\u8bbe\u7684\u9759\u6001\u5148\u9a8c\uff0c"
                    "\u4e0d\u4f1a\u968f\u573a\u6b21\u53d8\u3002\u73a9\u5bb6\u5b9e\u9645\u770b\u5230\u7684 "
                    "\u300cX \u4ef6\u5747\u4ef7 / Y \u603b\u4ef7\u300d \u662f\u5f00\u5c40\u540e\u52a8\u6001\u751f\u6210\uff0c"
                    "\u8bf7\u5230\u300c\u8bfb\u6570\u8f93\u5165\u300d tab \u624b\u52a8\u586b\u3002"
                    "\u4fe1\u606f\u6280\u80fd\u7ec4\u4ec5\u51b3\u5b9a\u5404\u8f6e\u4fe1\u606f\u6280\u80fd\u5019\u9009\uff0c"
                    "\u4e0d\u4ee3\u8868\u85cf\u54c1\u6389\u843d\u7c7b\u522b\u6216\u6982\u7387\u3002"
                )

    sidebar_divider()
    sidebar_section("\u4ed3\u5e93\u4e0e\u4ef6\u6570", variant="warehouse", icon="\U0001f4e6")
    warehouse_cells = st.number_input(
        "\u4ed3\u5e93\u603b\u683c\u6570 *",
        min_value=0, step=1,
        value=None,
        placeholder="\u5fc5\u586b",
        key="obs_warehouse_cells",
        help="\u5fc5\u586b\u3002\u9762\u677f\u300c\u6240\u6709\u85cf\u54c1\u603b\u5360\u7528\u2026\u683c\u300d\u53ef OCR \u81ea\u52a8\u586b\u5165\u3002",
    )
    _wh = _session_int("obs_warehouse_cells")
    if hero == "ethan":
        st.radio(
            "\u4ed3\u5e93\u683c\u6570\u7ea6\u675f",
            options=["strict", "estimate"],
            format_func=lambda mode: (
                "\u4e25\u683c\u7b49\u4e8e\u624b\u586b/OCR"
                if mode == "strict"
                else "\u4f30\u8ba1\u503c + \u5bb9\u5dee"
            ),
            key="obs_warehouse_cells_mode",
            horizontal=True,
            help=(
                "OCR \u8bc6\u522b\u5230\u9762\u677f\u603b\u683c\u65f6\u5efa\u8bae\u4fdd\u6301\u4e25\u683c\u3002"
                "\u4f0a\u68ee R1-R4 \u53ea\u6839\u636e\u5e95\u90e8\u8f6e\u5ed3\u624b\u4f30\u65f6\uff0c"
                "\u53ef\u9009\u300c\u4f30\u8ba1\u503c+\u5bb9\u5dee\u300d\uff0c\u907f\u514d\u628a\u4f30\u9519\u5f53\u786c\u4e0a\u9650\u3002"
            ),
        )
        if st.session_state.get("obs_warehouse_cells_mode") == "estimate":
            _default_wh_tol = recommended_warehouse_tolerance(_wh)
            st.session_state.setdefault(
                "obs_warehouse_cells_tolerance",
                _default_wh_tol,
            )
            if st.button(
                "\u6309\u4ed3\u5e93\u5927\u5c0f\u91cd\u7f6e\u63a8\u8350\u5bb9\u5dee",
                key="obs_warehouse_tol_reset",
                **layout_width_kwargs("button", stretch=True),
            ):
                st.session_state["obs_warehouse_cells_tolerance"] = _default_wh_tol
            st.slider(
                "\u624b\u4f30\u4ed3\u5e93\u5bb9\u5dee \u00b1\u683c",
                min_value=0,
                max_value=30,
                step=1,
                key="obs_warehouse_cells_tolerance",
                help=(
                    "\u9ed8\u8ba4\u63a8\u8350\u4f1a\u968f\u4ed3\u5e93\u5927\u5c0f\u589e\u957f\uff1b"
                    "\u5c0f\u4ed3\u5e93\u4e0d\u5efa\u8bae\u7ed9\u592a\u5927\u5bb9\u5dee\uff0c"
                    "\u5426\u5219\u4f1a\u628a\u660e\u663e\u4e0d\u540c\u7684\u4ed3\u5e93\u89c4\u6a21\u6df7\u5728\u4e00\u8d77\u3002"
                ),
            )
            st.caption(
                f"\u5f53\u524d\u6309 `{_wh}` \u683c\u4f30\u8ba1\uff0c"
                f"\u526a\u679d\u4e0a\u9650\u4e3a `{_wh + _session_int('obs_warehouse_cells_tolerance')}` \u683c\u3002"
            )
    else:
        st.session_state["obs_warehouse_cells_mode"] = "strict"
    if _wh <= 0:
        st.caption(
            "<span style='color:#c62828;font-weight:600'>"
            "\u5fc5\u586b\uff0c\u7559\u7a7a\u65f6\u65e0\u6cd5\u8fd0\u884c\u51fa\u4ef7 hint"
            "</span>",
            unsafe_allow_html=True,
        )
    total_item_count = st.number_input(
        "\u603b\u85cf\u54c1\u4ef6\u6570",
        min_value=0, step=1,
        value=None,
        placeholder="\u53ef\u9009",
        key="obs_total_item_count",
        help="\u5730\u56fe R1 hint \u6216\u91d1\u54c1\u9053\u5177\u53ef\u63d0\u4f9b\uff1b\u7559\u7a7a\u8868\u793a\u672a\u63d0\u4f9b\u3002",
    )

    sidebar_divider()
    sidebar_section("\u9762\u677f\u5bfc\u5165", variant="capture", icon="\U0001f4f7")
    with st.expander("\u5bfc\u5165\u8bf4\u660e\u4e0e\u6293\u5c4f\u9690\u79c1", expanded=False):
        st.markdown(
            "**\u81ea\u52a8\u5199\u5165\u8bfb\u6570 tab**\uff1a\u5730\u56fe\uff08\u9700\u300c\u5730\u56fe\u540d:\u7ade\u62cd\u4fe1\u606f\u300d\uff09\u3001"
            "\u4ed3\u5e93\u603b\u683c\u3001\u626b\u63cf\u683c\u6570\u3001\u7d2b/\u91d1\u5747\u683c\u00b7\u5747\u4ef7\u00b7\u603b\u4ef7\u3001\u603b\u4ef6\u6570\u3002\n\n"
            "**\u987b\u624b\u9009/\u624b\u586b**\uff1a\u82f1\u96c4\uff1b\u8bfb\u6570 tab \u91cc\u7d2b/\u91d1/\u7ea2\u5de8\u7269\u4e0e\u2605\u5177\u4f53\u7269\u3001"
            "\u7ea2\u54c1\u603b\u683c/\u4ef7\u503c\u533a\u95f4\u3002\n\n"
            "**\u6293\u53d6\u5f53\u524d\u5c4f\u5e55**\uff1a\u4ec5\u672c\u673a\u622a\u53d6\u6240\u9009\u663e\u793a\u5668\u5de6\u4fa7\u6e38\u620f\u4fe1\u606f\u533a\uff08ROI\uff09"
            "\u505a OCR\uff1b\u4e0d\u4e0a\u4f20\u3001\u4e0d\u5b58\u6863\u3002\u8bf7\u907f\u5f00\u542b\u94f6\u884c\u5361\u3001\u804a\u5929\u7b49\u654f\u611f\u754c\u9762\u3002"
            "\u9996\u6b21 OCR \u4f1a\u6309\u9700\u52a0\u8f7d\u6a21\u578b\uff0c\u901a\u5e38\u6bd4\u540e\u7eed\u8bc6\u522b\u6162\uff1b"
            "\u4f46\u542f\u52a8\u9875\u548c\u624b\u586b\u8def\u5f84\u4e0d\u518d\u7b49\u5f85 OCR \u6696\u673a\u3002\n\n"
            "**OCR \u586b\u5165**\uff1a\u4ec5\u8986\u76d6\u672c\u6b21\u8bc6\u522b\u5230\u7684\u5b57\u6bb5\uff1b"
            "\u672a\u51fa\u73b0\u5728\u9762\u677f\u4e0a\u7684\u9879\uff08\u5982\u81ea\u4f30\u7d2b\u54c1\u683c\u6570\uff09\u4f1a\u4fdd\u7559\u3002"
            "\u8bc6\u522b\u5230\u7684\u9879\u4f1a\u8986\u76d6\u65e7\u503c\uff08\u542b\u4e0a\u6b21 OCR \u6216\u624b\u586b\uff09\u3002"
        )
    try:
        from bidking_lab.capture.screen import (
            ScreenCaptureConfig,
            capture_monitor_panel,
            list_monitors,
            monitor_label,
        )

        _mons = list_monitors()
    except RuntimeError as _mon_exc:
        _mons = []
        st.caption(f"\u26a0\ufe0f {_mon_exc}")
    _single_monitor = len(_mons) == 1
    if _single_monitor:
        st.info(
            "\u26a0\ufe0f **\u5355\u5c4f\u6a21\u5f0f**\uff1a\u672a\u68c0\u6d4b\u5230\u7b2c\u2c5f\u663e\u793a\u5668\u3002"
            "\u70b9\u300c\u6293\u53d6\u5f53\u524d\u5c4f\u5e55\u300d\u524d\u8bf7\u5148\u5207\u5230\u6e38\u620f\u7a97\u53e3\uff1b"
            "\u4e0b\u65b9\u53ef\u8bbe\u7b49\u5f85\u79d2\u6570\uff08\u9ed8\u8ba4 2 \u79d2\uff09\uff0c"
            "\u907f\u514d\u6293\u5230\u672c\u9875\u9762\u3002\u53cc\u5c4f\u7528\u6237\u53ef\u8bbe\u4e3a 0\u3002",
            icon="\u2139\ufe0f",
        )
        st.session_state.setdefault("capture_delay_sec", 2)
        st.slider(
            "\u6293\u5c4f\u524d\u7b49\u5f85\uff08\u79d2\uff09",
            min_value=0,
            max_value=5,
            step=1,
            key="capture_delay_sec",
            help="\u5355\u5c4f\u65f6\u7559\u51fa\u4ece\u6d4f\u89c8\u5668\u5207\u5230\u6e38\u620f\u7684\u65f6\u95f4\u3002\u53cc\u5c4f\u53ef\u8bbe 0\u3002",
        )
    if _mons:
        _mon_labels = {m.index: monitor_label(m) for m in _mons}
        _default_mon = next(
            (m.index for m in _mons if m.is_primary),
            _mons[0].index,
        )
        if "obs_capture_monitor_index" not in st.session_state:
            st.session_state["obs_capture_monitor_index"] = _default_mon
        st.selectbox(
            "\u6293\u54ea\u4e2a\u663e\u793a\u5668",
            options=list(_mon_labels.keys()),
            format_func=lambda i: _mon_labels[int(i)],
            key="obs_capture_monitor_index",
            help=(
                "\u6293\u53d6\u6b64\u5904\u9009\u4e2d\u7684\u663e\u793a\u5668\u4e0a\u7684\u6e38\u620f\u4fe1\u606f\u533a\uff08ROI\uff09\uff1b"
                "\u4e0e\u672c\u9875\u5728\u54ea\u5757\u5c4f\u65e0\u5173\u3002"
                "\u5efa\u8bae\u628a BidKing \u653e\u526f\u5c4f\u3001\u6e38\u620f\u7559\u5728\u4e3b\u5c4f\uff08\u6216\u6e38\u620f\u5728\u526f\u5c4f\u5219\u5728\u6b64\u9009\u526f\u5c4f\uff09\u3002"
            ),
        )
    _screen_clicked = st.button(
        "\u6293\u53d6\u5f53\u524d\u5c4f\u5e55",
        key="capture_run_screen",
        **layout_width_kwargs("button", stretch=True),
        disabled=not _mons,
    )
    if _screen_clicked and _mons:
        _delay = (
            int(st.session_state.get("capture_delay_sec", 2))
            if _single_monitor
            else 0
        )
        _mon_idx = int(
            st.session_state.get("obs_capture_monitor_index", _mons[0].index),
        )
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:screen_capture",
            message="screen capture deferred",
            data={
                "single_monitor": _single_monitor,
                "monitor_count": len(_mons),
                "delay_sec": _delay,
                "main_tab": st.session_state.get("_main_tab"),
            },
            hypothesis_id="H5,H12",
            run_id="post-fix",
        )
        # #endregion
        _schedule_deferred_capture({
            "kind": "screen",
            "monitor_index": _mon_idx,
            "delay_sec": _delay,
            "monitor_label": monitor_label(
                next(m for m in _mons if m.index == _mon_idx),
            ),
            "status_label": "\u6293\u5c4f\u4e0e OCR \u8bc6\u522b\u4e2d\u2026",
        })
    _upload_rev = int(st.session_state.get("capture_upload_rev", 0))
    _cap_upload = st.file_uploader(
        "\u4e0a\u4f20\u622a\u56fe",
        type=["png", "jpg", "jpeg", "webp"],
        label_visibility="collapsed",
        key=f"capture_file_uploader_{_upload_rev}",
    )
    _btn_clip, _btn_ocr = st.columns(2)
    with _btn_clip:
        _clip_clicked = st.button(
            "\u526a\u8d34\u677f OCR",
            key="capture_run_clipboard",
            **layout_width_kwargs("button", stretch=True),
            help="Win+Shift+S \u6216\u6e38\u620f\u622a\u56fe\u540e\u5148\u590d\u5236\u5230\u526a\u8d34\u677f",
        )
    with _btn_ocr:
        _ocr_clicked = st.button(
            "\u622a\u56fe OCR",
            key="capture_run_ocr",
            type="primary",
            **layout_width_kwargs("button", stretch=True),
            disabled=_cap_upload is None,
            help=(
                "\u5148\u5728\u4e0a\u65b9\u9009\u62e9\u622a\u56fe\u6587\u4ef6\u540e\u518d\u70b9\u51fb"
                if _cap_upload is None
                else "\u5bf9\u5df2\u9009\u622a\u56fe\u8fd0\u884c OCR"
            ),
        )
    if _clip_clicked:
        _schedule_deferred_capture({
            "kind": "clipboard",
            "status_label": "\u526a\u8d34\u677f OCR \u4e2d\u2026",
        })
    if _ocr_clicked:
        _schedule_deferred_capture({
            "kind": "upload",
            "data": _cap_upload.getvalue(),
            "status_label": "\u622a\u56fe OCR \u8bc6\u522b\u4e2d\u2026",
        })
    _render_capture_debug_panel()
    _render_bg_infer_debug_panel()
    st.checkbox(
        "OCR \u540e\u540e\u53f0\u63a8\u65ad",
        value=True,
        key="auto_infer_after_capture",
        help=(
            "\u586b\u5165\u540e\u540e\u53f0\u8dd1 MC\uff08\u56fa\u5b9a 1000 \u6837\u672c\uff0c\u4e0e\u4fa7\u8fb9\u680f\u624b\u52a8\u63a8\u65ad\u7684\u6837\u672c\u6570\u65e0\u5173\uff09\uff1b"
            "\u6539\u8bfb\u6570/\u5730\u56fe\u4f1a\u53d6\u6d88\u3002"
            "\u5c31\u7eea\u65f6\u4f1a\u81ea\u52a8\u5207\u5230\u300c\u51fa\u4ef7\u63a8\u8350\u300d\u5e76\u542f\u52a8\u63a8\u65ad\u3002"
            "\u63a8\u65ad\u4e2d\u53ef\u7ee7\u7eed\u6539\u8bfb\u6570\uff08\u975e\u5f53\u524d\u6807\u7b7e\u53ef\u80fd\u7565\u7070\uff0c\u5c5e Streamlit \u5237\u65b0\u6001\uff0c\u4e0d\u4f1a\u9501\u5b9a\u8f93\u5165\uff09\u3002"
        ),
    )
    st.checkbox(
        "live 输入变化后自动重算",
        value=True,
        key="auto_infer_after_live_dirty",
        help=(
            "仅在已有一次成功推理结果后启用；"
            "手填/OCR/后续 packet 事件让 live 输入变 dirty 时，自动排队一次后台 MC。"
            "初次打开 app 不会因此预热。"
        ),
    )
    _render_fatbeans_json_importer()
    _prev_auto_infer = st.session_state.get("_prev_auto_infer_after_capture")
    if _prev_auto_infer and not st.session_state.get("auto_infer_after_capture", True):
        _cancel_background_hint()
    st.session_state["_prev_auto_infer_after_capture"] = bool(
        st.session_state.get("auto_infer_after_capture", True),
    )
    if _resolved_mid is not None:
        st.session_state.pop("_capture_map_miss", None)
    elif st.session_state.get("_capture_map_miss"):
        st.caption(
            "\u26a0\ufe0f \u672c\u6b21 OCR \u672a\u8bc6\u522b\u5730\u56fe\u540d\uff0c"
            "\u8bf7\u5728\u4e0a\u65b9\u624b\u52a8\u9009\u56fe\u3002"
        )
    _wh_after_cap = _session_int("obs_warehouse_cells")
    if st.session_state.get("_capture_apply_log") and _wh_after_cap <= 0:
        st.caption(
            "\u26a0\ufe0f \u672a\u8bc6\u522b\u4ed3\u5e93\u603b\u683c\u6570\uff08\u9876\u90e8\u300c\u6240\u6709\u85cf\u54c1\u603b\u5360\u7528\u2026\u683c\u300d\uff09\uff0c"
            "\u8bf7\u624b\u52a8\u586b\u5199\u4ef6\u5fc5\u586b\u9879\u3002"
        )
    st.session_state["_tracked_map_id"] = _resolved_mid
    st.session_state["_tracked_map_category"] = category
    if st.session_state.pop("_map_change_toast", False):
        st.toast(
            "\u5730\u56fe\u5df2\u5207\u6362\uff0c\u8bfb\u6570\u4e0e\u622a\u56fe\u5df2\u91cd\u7f6e\uff1b\u8bf7\u91cd\u65b0\u4e0a\u4f20\u3002",
            icon="\U0001F501",
        )

    sidebar_divider()
    sidebar_section("MC \u4e0e\u9ad8\u7ea7", variant="advanced", icon="\u2699\ufe0f")
    with st.expander("\u9ad8\u7ea7\uff1a MC \u91c7\u6837\u53c2\u6570", expanded=False):
        n_trials = st.slider(
            "MC \u6837\u672c\u6570\uff08samples\uff09",
            500,
            5000,
            _MC_TRIALS_MANUAL_DEFAULT,
            step=250,
            help="\u9009\u6863\u8bf4\u660e\uff1a"
                 "**500** = \u5feb\u901f\u4f30\u7b97\uff0c\u7cbe\u5ea6\u504f\u4f4e\uff08\u5c3e\u90e8\u5206\u5e03\u7684\u4ed3\u5e93\u7ec4\u5408\u53ef\u80fd\u5339\u914d\u4e0d\u8db3\uff09\uff1b"
                 "**1000** = \u5feb\u901f\u4e0e\u7cbe\u5ea6\u5e73\u8861\uff1b"
                 "**1500-2000** = \u66f4\u7a33\u7684\u5206\u4f4d\u6570\uff1b"
                 "**3000** = \u9ed8\u8ba4 / \u63a8\u8350\uff0c\u4ee5\u7cbe\u5ea6\u4e3a\u4e3b\uff1b"
                 "**4000-5000** = \u5927\u4ed3\u6216\u5f3a\u7ea6\u675f\u573a\u666f\u5907\u9009\u3002"
                 "\u7f13\u5b58\u5bbd\u5bb9\uff1a(map\\_id, n\\_trials, seed) \u540c\u4e00\u7ec4\u53c2\u6570\u4e0d\u4f1a\u91cd\u7b97\u3002",
        )
        warehouse_tol = st.slider(
            "\u4ed3\u5e93\u5bb9\u5dee \u00b1\u683c\u6570", 4, 20, 8,
        )
        purple_tol = st.slider(
            "\u7d2b\u54c1 cells \u5bb9\u5dee \u00b1\u683c\u6570\uff08\u63d0\u4f9b\u7d2b\u54c1 cells \u65f6\u751f\u6548\uff09",
            0, 12, 4,
        )
        per_bucket_top = st.slider(
            "\u8054\u5408\u641c\u7d22\u5bbd\u5ea6 (search width)", 4, 12, 6,
        )
        seed_lock = st.checkbox(
            "\u56fa\u5b9a\u968f\u673a\u79cd\u5b50\uff08\u52fe\u9009 = \u540c\u8f93\u5165\u591a\u6b21\u70b9\u51fb\u7ed3\u679c\u4e00\u81f4\uff09",
            value=False,
            key="obs_seed_lock",
            help="\u9ed8\u8ba4\u4e0d\u9501\u5b9a\u3002\u4e0d\u52fe\u9009\u65f6\u6bcf\u6b21\u70b9\u51fb\u90fd\u91cd\u65b0\u968f\u673a\u91c7\u6837\uff0cp25/p50/p75 \u4f1a\u968f\u673a\u8df3\u52a8 \u00b1\u51e0\u4e2a %\u3002"
                 "\u9501\u5b9a\u540e\u540c\u8f93\u5165 \u2192 \u540c\u7ed3\u679c\uff0c\u9002\u5408\u4f60\u68c0\u6d4b\u300c\u586b\u8fd9\u4e2a\u5b57\u6bb5\u80fd\u4e0d\u80fd\u52a8\u300d\u65f6\u52fe\u9009\u3002"
                 "\u672a\u9501\u5b9a\u65f6\u540e\u53f0\u63a8\u65ad\u4e0d\u56e0\u6bcf\u6b21\u5237\u65b0\u800c\u53d6\u6d88\u3002",
        )
        seed = st.number_input(
            "\u968f\u673a\u79cd\u5b50 seed", value=20260515, step=1,
            disabled=not seed_lock,
        )
        if not seed_lock:
            # OS entropy on every click → cache key per-rerun (won't hit cache)
            import time as _time
            seed = int(_time.time_ns() & 0xFFFFFFFF)
        use_live_canonical_input = st.checkbox(
            "灰度：使用 live shadow 作为推理输入",
            value=False,
            key="use_live_canonical_input",
            help=(
                "默认关闭，保持 legacy obs 推理路径。打开后，出价 hint 与联合筛选会优先使用 "
                "LiveSessionState -> SessionObs；若 live state 与当前地图/仓库不匹配，会自动回退 legacy。"
            ),
        )
        st.caption(
            "当前推理输入："
            + (
                "live shadow（灰度）"
                if use_live_canonical_input
                else "legacy obs（默认）"
            )
        )

    if st.session_state.get("_capture_apply_log"):
        with st.expander("\u4e0a\u6b21\u5bfc\u5165", expanded=False):
            for ln in st.session_state["_capture_apply_log"]:
                st.caption(ln)

# #region agent log
agent_phase_log(phase="after_sidebar", hypothesis_id="H1,H3")
# #endregion

state["hero"] = hero
# 以 widget session_state 为准（避免 number_input 返回值与 key 不同步）
if "obs_warehouse_cells" in st.session_state:
    _wh_sync = _session_int("obs_warehouse_cells")
    if _wh_sync > 0:
        state["warehouse_cells"] = _wh_sync
else:
    _wh_sync = int(state.get("warehouse_cells") or 0)
state["warehouse_cells_mode"] = st.session_state.get(
    "obs_warehouse_cells_mode",
    "strict",
)
state["warehouse_cells_tolerance"] = _session_int(
    "obs_warehouse_cells_tolerance",
)
_resolved_mid = _effective_map_id(
    map_choices, selectbox_return=map_id, obs=state,
)
if _resolved_mid is not None:
    state["map_id"] = _resolved_mid
    st.session_state["_tracked_map_id"] = _resolved_mid
elif state.get("map_id") is None:
    state.pop("map_id", None)
_tic_sync = _session_int("obs_total_item_count")
state["total_item_count"] = _tic_sync if _tic_sync > 0 else 0
from bidking_lab.capture.apply import (
    hydrate_reading_widgets_from_obs,
    sync_obs_from_reading_widgets,
)
from bidking_lab.inference.readings_validate import check_warehouse_cell_budget

hydrate_reading_widgets_from_obs(state, st.session_state)
sync_obs_from_reading_widgets(state, st.session_state, allow_clear=False)
from bidking_lab.capture.apply import hydrate_huge_bands_from_obs, sync_huge_bands_to_obs

sync_huge_bands_to_obs(state, st.session_state)
_record_live_observation_snapshot(
    state,
    source="manual",
    event_kind="manual_update",
)
_attach_inference_session_source(state)
with st.sidebar:
    _render_live_source_summary()
    _render_canonical_input_diagnostic(state, maps)
    _render_live_inference_status()
# #region agent log
agent_debug_log(
    location="streamlit_app.py:after_global_hydrate_sync",
    message="obs snapshot after hydrate (no clear)",
    data={
        "obs_reading_keys": sorted(
            k for k in state if k in (
                "wg_cells", "white_cells", "blue_cells", "purple_cells",
                "gold_cells", "purple_avg_raw", "gold_avg_raw",
            )
        ),
        "wg_cells": state.get("wg_cells"),
        "blue_cells": state.get("blue_cells"),
        "purple_cells": state.get("purple_cells"),
        "purple_avg_raw": state.get("purple_avg_raw"),
        "gold_avg_raw": state.get("gold_avg_raw"),
        "gold_cells": state.get("gold_cells"),
        "red_cells_total": state.get("red_cells_total"),
        "mc_bucket_q": _mc_active_bucket_qs(state, hero),
    },
    hypothesis_id="H6,H11",
    run_id="post-fix",
)
# #endregion
_cells_budget_err = check_warehouse_cell_budget(state)
warehouse_ready = int(state.get("warehouse_cells") or 0) > 0
map_ready = state.get("map_id") is not None
inference_ready = warehouse_ready and map_ready and _cells_budget_err is None
if st.session_state.get("_pending_hint_nudge") and inference_ready:
    st.session_state.pop("_pending_hint_nudge", None)
    st.session_state["_nudge_hint_tab"] = True
    _maybe_switch_to_hint_tab_after_capture()
elif not inference_ready and st.session_state.get("_pending_hint_nudge"):
    st.session_state["_nudge_hint_tab"] = True

if (
    st.session_state.pop("_awaiting_warehouse_for_infer", False)
    and inference_ready
    and st.session_state.get("_request_bg_hint_capture")
):
    st.toast(
        "\u4ed3\u5e93\u683c\u6570\u5df2\u5c31\u7eea\uff0c\u6b63\u5728\u542f\u52a8\u51fa\u4ef7\u63a8\u65ad\u2026",
        icon="\u23f3",
    )

if st.session_state.pop("_capture_just_applied", False):
    _cap_lines = st.session_state.get("_capture_apply_log") or []
    _has_purple_cnt = any(
        "\u7d2b\u54c1\u4ef6\u6570" in ln or "purple_count" in ln
        for ln in _cap_lines
    )
    _on_hint_tab = st.session_state.get("_main_tab") == "hint"
    _toast = "\u5df2\u8bc6\u522b\u5e76\u586b\u5165\u8bfb\u6570\uff08\u8bf7\u6838\u5bf9\u4ed3\u5e93\u683c\u6570\uff09"
    if _has_purple_cnt and not _on_hint_tab:
        _toast += "\uff1b\u7d2b\u54c1\u4ef6\u6570\u5728\u300c\u8bfb\u6570\u8f93\u5165\u300d\u9875"
    elif _has_purple_cnt:
        _toast += "\uff1b\u7d2b\u54c1\u4ef6\u6570\u8bf7\u5728\u300c\u8bfb\u6570\u8f93\u5165\u300d\u6838\u5bf9"
    if (
        inference_ready
        and st.session_state.get("auto_infer_after_capture", True)
        and (
            st.session_state.get("_request_bg_hint_capture")
            or st.session_state.get("_request_bg_hint_manual")
            or st.session_state.get("_nudge_hint_tab")
        )
    ):
        if _on_hint_tab:
            _toast += "\uff1b\u5df2\u5207\u5230\u300c\u51fa\u4ef7\u63a8\u8350\u300d\uff0c\u540e\u53f0\u63a8\u65ad\u8fdb\u884c\u4e2d"
        else:
            _toast += "\uff1b\u540e\u53f0\u63a8\u65ad\u5df2\u542f\u52a8\uff08\u5c06\u81ea\u52a8\u5207\u5230\u300c\u51fa\u4ef7\u63a8\u8350\u300d\uff09"
    st.toast(_toast, icon="\u2705")


def _infer_status() -> str:
    return str(st.session_state.get("_bg_infer_status", "idle"))


def _mc_ui_running() -> bool:
    """True only while the background MC thread box is actively running."""
    box = st.session_state.get("_bg_infer_box")
    return box is not None and box.get("status") == "running"


def _maybe_queue_live_dirty_recompute(*, inference_ready: bool) -> bool:
    from bidking_lab.live import LiveSessionState
    from live_recompute import maybe_schedule_live_dirty_recompute

    live_state = st.session_state.get("_live_session_state")
    if not isinstance(live_state, LiveSessionState):
        return False
    import time as _time

    queued = maybe_schedule_live_dirty_recompute(
        st.session_state,
        dirty=live_state.dirty,
        version=live_state.version,
        inference_ready=inference_ready,
        worker_running=_mc_ui_running(),
        auto_enabled=bool(st.session_state.get("auto_infer_after_live_dirty", True)),
        armed=bool(st.session_state.get("_live_auto_recompute_armed", False)),
        has_pending_request=(
            bool(st.session_state.get("_request_bg_hint_capture"))
            or bool(st.session_state.get("_request_bg_hint_manual"))
        ),
        now=_time.time(),
    )
    if queued:
        _clear_hint_done_flash()
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:live_dirty_auto_recompute",
            message="live dirty queued background hint",
            data={
                "live_version": live_state.version,
                "main_tab": st.session_state.get("_main_tab"),
            },
            hypothesis_id="live-auto",
        )
        # #endregion
    return queued


def _hint_banner_show() -> bool:
    """Whether the top banner strip should stay visible."""
    import time as _ht

    if _infer_status() == "running":
        return True
    if st.session_state.get("_nudge_hint_tab"):
        return True
    if st.session_state.get("_hint_tab_done_flash"):
        if float(st.session_state.get("_hint_infer_until", 0)) <= _ht.time():
            st.session_state.pop("_hint_tab_done_flash", None)
            st.session_state.pop("_hint_infer_until", None)
            return False
        return True
    return False


def _bg_infer_poll_needed() -> bool:
    return st.session_state.get("_bg_infer_box") is not None


def _paint_hint_banner(banner_slot) -> None:
    """Paint or clear the main-area banner above tabs."""
    from ui_loading import render_status_banner

    if (
        st.session_state.get("_request_bg_hint_capture")
        and not _mc_ui_running()
        and not inference_ready
        and int(st.session_state.get("obs_warehouse_cells") or 0) <= 0
    ):
        with banner_slot:
            render_status_banner(
                kind="loading",
                message="OCR 已完成，等待填写仓库总格数",
                detail=(
                    "请在左侧栏填写「仓库总格数」后，将自动启动出价推断；"
                    "也可先回到「读数输入」核对 OCR 读数"
                ),
            )
        return

    if st.session_state.get("_capture_in_progress"):
        _first_ocr = not st.session_state.get("_ocr_first_grab_notice_done")
        with banner_slot.container():
            render_status_banner(
                kind="loading",
                message="\u6b63\u5728\u8bc6\u522b\u6e38\u620f\u5de6\u4fa7\u9762\u677f",
                detail=(
                    "\u9996\u6b21\u7ea6 10 \u79d2\uff0c\u540c\u4f1a\u8bdd\u5185\u518d\u6b21\u6293\u5c4f\u901a\u5e38\u66f4\u5feb"
                    if _first_ocr
                    else "\u5b8c\u6210\u540e\u5c06\u81ea\u52a8\u586b\u5165\u8bfb\u6570\u5e76\u542f\u52a8\u51fa\u4ef7\u63a8\u65ad"
                ),
            )
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_paint_hint_banner",
            message="banner painted",
            data={
                "branch": "ocr_capture",
                "first_ocr": _first_ocr,
                "main_tab": st.session_state.get("_main_tab"),
            },
            hypothesis_id="H-ghost",
            run_id="post-fix",
        )
        # #endregion
        return

    status = _infer_status()
    _banner_branch = "clear"
    if status == "running" and _mc_ui_running():
        _banner_branch = "running"
        _mc_run = _mc_sidebar_params()
        _n_run = int(_mc_run.get("n_trials", 0))
        with banner_slot:
            render_status_banner(
                kind="loading",
                message=f"MC \u91c7\u6837\u4e2d\uff08{_n_run} \u6837\u672c\uff09",
                detail=(
                    "\u53ef\u7ee7\u7eed\u6539\u8bfb\u6570\uff1b\u6362\u5730\u56fe\u6216\u6539\u4ed3\u5e93\u683c\u6570\u4f1a\u53d6\u6d88\u672c\u6b21\u63a8\u65ad\u3002"
                    "\u4ec5\u6539\u626b\u63cf\u8bfb\u6570\u65f6\u7ed3\u679c\u53ef\u80fd\u4f5c\u5e9f\uff0c\u5b8c\u6210\u540e\u8bf7\u91cd\u70b9\u300c\u8fd0\u884c\u51fa\u4ef7 hint\u300d\u3002"
                    "\u9875\u5185\u300c\u53d6\u6d88\u63a8\u65ad\u300d\u53ef\u653e\u5f03\u7ed3\u679c\uff1b"
                    "Streamlit \u9876\u680f Stop \u4e0d\u4f1a\u7acb\u5373\u4e2d\u65ad\u8ba1\u7b97\u3002"
                ),
            )
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_paint_hint_banner",
            message="banner painted",
            data={
                "branch": _banner_branch,
                "main_tab": st.session_state.get("_main_tab"),
                "run_elapsed_ms": int((time.perf_counter() - _agent_run_t0) * 1000),
            },
            hypothesis_id="H4,H13",
            run_id="post-fix",
        )
        # #endregion
        return
    if status == "running" and not _mc_ui_running():
        st.session_state["_bg_infer_status"] = "idle"
        status = "idle"
    if (
        st.session_state.get("_hint_tab_done_flash")
        and float(st.session_state.get("_hint_infer_until", 0)) > time.time()
    ):
        _banner_branch = "done"
        _on_hint = st.session_state.get("_main_tab") == "hint"
        with banner_slot:
            render_status_banner(
                kind="ready",
                message="\u51fa\u4ef7\u63a8\u65ad\u5df2\u5b8c\u6210",
                detail=(
                    "\u7ed3\u679c\u5df2\u66f4\u65b0\u4e8e\u672c\u9875\u4e0b\u65b9"
                    if _on_hint
                    else "\u53ef\u5207\u5230\u300c\u51fa\u4ef7\u63a8\u8350\u300d\u6807\u7b7e\u67e5\u770b\u7ed3\u679c"
                ),
            )
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_paint_hint_banner",
            message="banner painted",
            data={
                "branch": _banner_branch,
                "main_tab": st.session_state.get("_main_tab"),
                "run_elapsed_ms": int((time.perf_counter() - _agent_run_t0) * 1000),
            },
            hypothesis_id="H4,H13",
            run_id="post-fix",
        )
        # #endregion
        return
    if st.session_state.get("_nudge_hint_tab"):
        _banner_branch = "nudge"
        st.session_state.pop("_nudge_hint_tab", None)
        _on_hint = st.session_state.get("_main_tab") == "hint"
        with banner_slot:
            render_status_banner(
                kind="ready",
                message="\u5df2\u542f\u52a8\u540e\u53f0\u63a8\u65ad",
                detail=(
                    "\u8bf7\u5728\u672c\u9875\u7b49\u5f85\u63a8\u65ad\u7ed3\u679c"
                    if _on_hint
                    else "\u53ef\u5207\u5230\u300c\u51fa\u4ef7\u63a8\u8350\u300d\u6807\u7b7e\u67e5\u770b\u8fdb\u5ea6\u4e0e\u7ed3\u679c"
                ),
            )
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_paint_hint_banner",
            message="banner painted",
            data={
                "branch": _banner_branch,
                "main_tab": st.session_state.get("_main_tab"),
                "run_elapsed_ms": int((time.perf_counter() - _agent_run_t0) * 1000),
            },
            hypothesis_id="H2,H4,H13",
            run_id="post-fix",
        )
        # #endregion
        return
    banner_slot.empty()


def _mc_sidebar_params(*, for_auto_capture: bool = False) -> dict:
    trials = (
        _MC_TRIALS_AUTO_AFTER_CAPTURE
        if for_auto_capture
        else int(n_trials)
    )
    return {
        "n_trials": trials,
        "seed": seed,
        "warehouse_tol": warehouse_tol,
        "purple_tol": purple_tol,
    }


def _mc_fingerprint_params(
    *,
    seed_locked: bool,
    for_auto_capture: bool = False,
) -> dict:
    """MC params for background cancel fingerprint (stable seed unless locked)."""
    base = _mc_sidebar_params(for_auto_capture=for_auto_capture)
    if seed_locked:
        return base
    return {**base, "seed": 0}


def _sync_obs_from_widgets(obs_state: dict) -> None:
    """Merge sidebar widget session_state into obs before MC / fingerprint."""
    from bidking_lab.capture.apply import sync_obs_from_reading_widgets

    wh = int(st.session_state.get("obs_warehouse_cells") or 0)
    if wh > 0:
        obs_state["warehouse_cells"] = wh
    obs_state["warehouse_cells_mode"] = st.session_state.get(
        "obs_warehouse_cells_mode",
        "strict",
    )
    obs_state["warehouse_cells_tolerance"] = int(
        st.session_state.get("obs_warehouse_cells_tolerance") or 0
    )
    tic = int(st.session_state.get("obs_total_item_count") or 0)
    obs_state["total_item_count"] = tic if tic > 0 else 0
    cat = st.session_state.get("obs_map_category", "mansion")
    mid = _resolved_map_select(_maps_for_category(maps, cat))
    if mid is not None:
        obs_state["map_id"] = mid
    sync_obs_from_reading_widgets(
        obs_state, st.session_state, allow_clear=False,
    )
    from bidking_lab.capture.apply import sync_huge_bands_to_obs

    sync_huge_bands_to_obs(obs_state, st.session_state)


def _compute_hint_bundle_ui(obs_state: dict):
    from hint_pipeline import compute_hint_bundle

    return compute_hint_bundle(
        obs_state,
        _mc_sidebar_params(),
        maps=maps,
        drops=drops,
        items=items,
        build_session=_build_session_for_inference,
        sample_truths=_sample_truths_cached,
        enable_snipe_pass=_ENABLE_SNIPE_PASS_HINTS,
    )


def _hint_bundle_is_stale(
    bundle: dict | None,
    obs_state: dict,
    *,
    inference_ready: bool,
) -> bool:
    """True when cached MC results no longer match sidebar readings."""
    from bg_inference import hint_bundle_stale_report

    return hint_bundle_stale_report(
        bundle, obs_state, inference_ready=inference_ready,
    )["stale"]


def _pending_bg_hint_start() -> tuple[bool, bool]:
    """(should_start, use_auto_capture_trial_count)."""
    cap = bool(st.session_state.get("_request_bg_hint_capture"))
    manual = bool(st.session_state.get("_request_bg_hint_manual"))
    return cap or manual, cap


def _tick_background_hint() -> str:
    from bg_inference import (
        inference_fingerprint,
        start_background_hint,
        sync_background_hint,
    )
    from hint_pipeline import compute_hint_bundle

    seed_locked = bool(st.session_state.get("obs_seed_lock", False))
    _want_start, _for_auto_capture = _pending_bg_hint_start()
    mc = _mc_sidebar_params(for_auto_capture=_for_auto_capture)
    fp_mc = _mc_fingerprint_params(
        seed_locked=seed_locked,
        for_auto_capture=_for_auto_capture,
    )
    status = sync_background_hint(
        st.session_state, state=state, mc=mc, mc_fingerprint=fp_mc,
    )
    if _want_start and inference_ready:
        st.session_state.pop("_request_bg_hint_capture", None)
        st.session_state.pop("_request_bg_hint_manual", None)
        st.session_state.pop("_request_bg_hint_live_dirty", None)
        if _for_auto_capture:
            _maybe_switch_to_hint_tab_after_capture()
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_tick_background_hint:start",
            message="starting background hint",
            data={"main_tab": st.session_state.get("_main_tab")},
            hypothesis_id="H2,H4",
        )
        # #endregion
        def _compute(st_snap: dict, mc_p: dict):
            return compute_hint_bundle(
                st_snap,
                mc_p,
                maps=maps,
                drops=drops,
                items=items,
                build_session=_build_session_for_inference,
                sample_truths=_sample_truths_cached,
                enable_snipe_pass=_ENABLE_SNIPE_PASS_HINTS,
            )

        import time as _time

        st.session_state["_bg_infer_box"] = start_background_hint(
            state=dict(state),
            mc=mc,
            mc_fingerprint=fp_mc,
            compute_fn=_compute,
            session_state=st.session_state,
        )
        st.session_state["_hint_bundle"] = None
        _clear_hint_done_flash()
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_tick_background_hint:mc_buckets",
            message="MC started with bucket qs",
            data={
                "hero": state.get("hero"),
                "mc_bucket_q": _mc_active_bucket_qs(state, str(state.get("hero", "ethan"))),
                "purple_avg_raw": state.get("purple_avg_raw"),
                "purple_cells": state.get("purple_cells"),
                "gold_cells": state.get("gold_cells"),
                "red_cells_total": state.get("red_cells_total"),
            },
            hypothesis_id="H11",
            run_id="post-fix",
        )
        # #endregion
        return "running"
    return status


def _poll_background_hint() -> str:
    """Poll MC thread on each main script run (no st.fragment timer)."""
    from bg_inference import sync_background_hint

    if st.session_state.get("_bg_infer_box") is None:
        return str(st.session_state.get("_bg_infer_status", "idle"))
    seed_locked = bool(st.session_state.get("obs_seed_lock", False))
    mc = _mc_sidebar_params()
    fp_mc = _mc_fingerprint_params(seed_locked=seed_locked)
    had_bundle = st.session_state.get("_hint_bundle") is not None
    status = sync_background_hint(
        st.session_state, state=state, mc=mc, mc_fingerprint=fp_mc,
    )
    st.session_state["_bg_infer_status"] = status
    if status == "done" and not had_bundle:
        import time as _time

        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:_poll_background_hint:done",
            message="bg hint finished",
            data={
                "main_tab": st.session_state.get("_main_tab"),
                "had_bundle": had_bundle,
                "has_bundle_now": st.session_state.get("_hint_bundle") is not None,
            },
            hypothesis_id="H1,H2",
        )
        # #endregion
        st.session_state.pop("_nudge_hint_tab", None)
        st.session_state["_hint_tab_done_flash"] = True
        st.session_state["_hint_infer_until"] = _time.time() + 8
        st.session_state["_hint_results_ready_rerun"] = True
        if st.session_state.get("_hint_bundle") is not None:
            _mark_live_inference_ready()
            if not st.session_state.get("_hint_done_toast_shown"):
                st.toast(
                    "\u540e\u53f0\u63a8\u65ad\u5b8c\u6210\uff0c\u7ed3\u679c\u5df2\u66f4\u65b0\u4e8e\u672c\u9875",
                    icon="\u2705",
                )
                st.session_state["_hint_done_toast_shown"] = True
        else:
            st.session_state.pop("_hint_tab_done_flash", None)
            st.session_state["_bg_infer_status"] = "skipped"
    if status in ("idle", "cancelled", "error", "skipped"):
        st.session_state.pop("_hint_done_toast_shown", None)
    if status in ("cancelled", "error", "skipped"):
        st.session_state.pop("_hint_infer_until", None)
        st.session_state.pop("_hint_tab_done_flash", None)
    return status


def _poll_bg_hint_and_maybe_rerun(*, had_bundle_before: bool | None = None) -> str:
    """Promote finished MC; rerun so hint tab paints results on this session."""
    if st.session_state.get("_bg_infer_box") is None:
        return str(st.session_state.get("_bg_infer_status", "idle"))
    if had_bundle_before is None:
        had_bundle_before = st.session_state.get("_hint_bundle") is not None
    status = _poll_background_hint()
    st.session_state["_bg_infer_status"] = status
    if status == "done" and not had_bundle_before:
        if st.session_state.get("auto_infer_after_capture", True):
            st.session_state["_main_tab"] = "hint"
        st.rerun()
    return status


_hint_banner_slot = st.empty()
# #region agent log
agent_phase_log(
    phase="before_tick_background_hint",
    hypothesis_id="H1,H3,H4",
    request_bg_hint_capture=bool(
        st.session_state.get("_request_bg_hint_capture")
    ),
    request_bg_hint_manual=bool(
        st.session_state.get("_request_bg_hint_manual")
    ),
)
# #endregion

_maybe_queue_live_dirty_recompute(inference_ready=inference_ready)
_bg_infer_status = _tick_background_hint()
_poll_bg_hint_and_maybe_rerun()
_bg_infer_status = str(st.session_state.get("_bg_infer_status", _bg_infer_status))
st.session_state["_bg_infer_status"] = _bg_infer_status
# #region agent log
agent_phase_log(
    phase="after_tick_background_hint",
    hypothesis_id="H1,H3",
    bg_infer_status=_bg_infer_status,
    mc_running=_mc_ui_running(),
)
agent_debug_log(
    location="streamlit_app.py:after_tab_nav",
    message="tab selected for render",
    data={
        "main_tab": _main_tab,
        "session_main_tab": st.session_state.get("_main_tab"),
        "infer_status": _bg_infer_status,
        "mc_running": _mc_ui_running(),
        "wh_sync": _wh_sync,
        "map_id": state.get("map_id"),
        "run_elapsed_ms": int((time.perf_counter() - _agent_run_t0) * 1000),
    },
    hypothesis_id="H1,H3,H4",
    run_id="post-fix",
)
# #endregion
_paint_hint_banner(_hint_banner_slot)
_materialize_deferred_debug_png()
tab_joint = None

_tab_pane = st.empty() if _main_tab == "joint" else None
# #region agent log
agent_debug_log(
    location="streamlit_app.py:tab_pane",
    message="tab_pane_slot_created",
    data={"main_tab": _main_tab},
    hypothesis_id="H-ghost",
    run_id="post-fix",
)
# #endregion

if _deferred_capture_pending is not None:
    if _tab_pane is not None:
        _tab_pane.empty()
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:tab_pane",
        message="tab_pane_cleared_for_ocr",
        data={"main_tab": _main_tab},
        hypothesis_id="H-ghost",
        run_id="post-fix",
    )
    # #endregion
    _execute_deferred_capture(
        _deferred_capture_pending,
        map_names=_map_names,
    )
    # Apply on next run only — early _apply_pending_capture() runs before widgets.
    # #region agent log
    agent_debug_log(
        location="streamlit_app.py:deferred_capture_after_ocr",
        message="OCR done; rerun to apply pending_capture before widgets",
        data={"has_pending": "_pending_capture" in st.session_state},
        hypothesis_id="H-ghost,H12",
        run_id="post-fix",
    )
    # #endregion
    st.rerun()

# ===== Tab 1: \u8bfb\u6570\u8f93\u5165 =====
if _main_tab == "obs":
    @st.fragment
    def _render_obs_tab_fragment() -> None:
        with st.container():
            from bidking_lab.capture.apply import (
                hydrate_reading_widgets_from_obs,
                reading_widget_key as _rwk,
                reconcile_avg_raw_widget_return,
                reconcile_optional_number_field,
                sync_obs_from_reading_widgets,
            )

            hydrate_reading_widgets_from_obs(
                state,
                st.session_state,
                force_numeric=bool(
                    st.session_state.pop("_ocr_refill_numeric", False)
                ),
                force_avg_raw=bool(
                    st.session_state.pop("_force_hydrate_avg_raw", False)
                ),
            )

            tab_lead(
                "\u9762\u677f\u5bfc\u5165\u4e0d\u542b\u5de8\u7269\u4fe1\u606f\uff1a\u8bf7\u624b\u52a8\u586b\u7d2b/\u91d1/\u7ea2 "
                "\u300c\u5de8\u7269\u6570\u91cf\u300d\u3001\u2605 \u5177\u4f53\u7269\u3001\u7ea2\u54c1\u4ef7\u503c\u533a\u95f4\u3002"
            )
            _render_obs_source_summary()
            if _cells_budget_err:
                st.error(_cells_budget_err)
            st.subheader("\u4f4e\u54c1\u533a\uff08q\u22643\uff09")
            if hero == "ethan":
                st.markdown(
                    "Ethan \u4f7f\u7528 **\u666e\u54c1\u626b\u63cf** \u540c\u65f6\u7ed9\u51fa "
                    "\u767d+\u7eff **\u5408\u5e76\u603b cells**\uff1bAisha "
                    "\u7528 R1/R2 **\u8f6e\u5ed3\u5206\u522b\u7ed9** \u767d\u548c\u7eff\u3002"
                )
                c1, c2 = st.columns(2)
                state["wg_cells"] = c1.number_input(
                    "\u767d+\u7eff \u5408\u5e76\u603b\u683c\u6570\uff08\u666e\u54c1\u626b\u63cf\u4e00\u6b21\u7ed9\u51fa\uff09",
                    min_value=0, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u666e\u54c1\u626b\u63cf\u6216\u76ee\u6d4b\u7ed9\u51fa\u3002\u7559\u7a7a = \u672a\u63d0\u4f9b\u3002",
                    key=_rwk("obs_reading_wg_cells", st.session_state),
                )
                state["blue_cells"] = c2.number_input(
                    "\u84dd\u54c1\u603b\u683c\u6570\uff08\u826f\u54c1\u626b\u63cf\uff09",
                    min_value=0, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u826f\u54c1\u626b\u63cf\u7ed9\u51fa\u3002\u7559\u7a7a = \u672a\u63d0\u4f9b\u3002",
                    key=_rwk("obs_reading_blue_cells", st.session_state),
                )
                state["aisha_split"] = False
            else:
                state["aisha_split"] = st.checkbox(
                    "\u62c6\u5206\u767d / \u7eff \u8f6e\u5ed3\uff08R1 \u8f6e\u5ed3 = \u767d\uff0cR2 \u8f6e\u5ed3 = \u7eff\uff09",
                    value=True,
                    help="\u52fe\u9009\u540e\u53ef\u4ee5\u4e3a\u767d / \u7eff / \u84dd \u5206\u522b\u586b\u683c\u6570\uff1b"
                         "\u4e0d\u52fe\u9009\u5219\u6309 Ethan \u98ce\u683c\u5c06\u767d\u7eff\u5408\u5e76\u3002",
                )
                st.caption(
                    "\u827e\u838e\u9760\u8f6e\u5ed3\u81ea\u5df1\u6570\u683c\u5b50 + \u6570\u4ef6\u6570\u3002\u4ef6\u6570\u53ef\u9009\u586b\uff0c"
                    "\u586b\u4e86\u53ef\u8ba9\u8054\u5408\u63a8\u65ad / \u603b\u85cf\u54c1\u6570\u5316\u7b80\u4e00\u4e2a\u91cf\u3002"
                )
                if state["aisha_split"]:
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        state["white_cells"] = st.number_input(
                            "\u767d\u54c1\u683c\u6570\uff08R1 \u8f6e\u5ed3\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_white_cells", st.session_state),
                        )
                        state["white_count"] = st.number_input(
                            "\u767d\u54c1\u4ef6\u6570\uff08\u53ef\u9009\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_white_count", st.session_state),
                        )
                    with c2:
                        state["green_cells"] = st.number_input(
                            "\u7eff\u54c1\u683c\u6570\uff08R2 \u8f6e\u5ed3\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_green_cells", st.session_state),
                        )
                        state["green_count"] = st.number_input(
                            "\u7eff\u54c1\u4ef6\u6570\uff08\u53ef\u9009\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_green_count", st.session_state),
                        )
                    with c3:
                        state["blue_cells"] = st.number_input(
                            "\u84dd\u54c1\u683c\u6570\uff08R3 \u8f6e\u5ed3\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_blue_cells", st.session_state),
                        )
                        state["blue_count"] = st.number_input(
                            "\u84dd\u54c1\u4ef6\u6570\uff08\u53ef\u9009\uff09",
                            min_value=0, max_value=20, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_blue_count", st.session_state),
                        )
                else:
                    c1, c2 = st.columns(2)
                    with c1:
                        state["white_cells"] = st.number_input(
                            "\u767d+\u7eff \u5408\u5e76\u603b\u683c\u6570",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_white_cells", st.session_state),
                        )
                        state["white_count"] = st.number_input(
                            "\u767d+\u7eff \u5408\u5e76\u4ef6\u6570\uff08\u53ef\u9009\uff09",
                            min_value=0, max_value=40, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_white_count", st.session_state),
                        )
                    state["green_cells"] = 0
                    state["green_count"] = 0
                    with c2:
                        state["blue_cells"] = st.number_input(
                            "\u84dd\u54c1\u683c\u6570\uff08R3 \u8f6e\u5ed3\uff09",
                            min_value=0, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_blue_cells", st.session_state),
                        )
                        state["blue_count"] = st.number_input(
                            "\u84dd\u54c1\u4ef6\u6570\uff08\u53ef\u9009\uff09",
                            min_value=0, max_value=20, step=1,
                            placeholder="0",
                            key=_rwk("obs_reading_blue_count", st.session_state),
                        )

            st.divider()
            with st.expander("\u2139\ufe0f \u4ec0\u4e48\u7b97\u300c\u5de8\u7269 / \u5927\u4ef6\u300d\uff1f", expanded=False):
                st.markdown(
                    "\u54c1\u8d28\u9608\u503c\u4e0d\u4e00\u6837\uff0c\u56e0\u4e3a\u6e38\u620f\u6570\u636e\u91cc\u4e0d\u540c\u54c1\u8d28\u7684\u5927\u4ef6\u5206\u5e03\u4e0d\u540c\uff1a\n\n"
                    "- **\u7d2b\u54c1\uff1a\u2265 10 \u683c** \u7b97\u5927\u4ef6\u3002\u6e38\u620f\u91cc\u7d2b\u54c1 \u2265 12 \u683c\u53ea\u6709 1 \u4ef6\uff08\u53ef\u6298\u53e0\u9ad8\u97e7\u6027\u9632\u62a4\u76fe 3\u00d74\uff09\uff0c\u4f46 5\u00d72=10 \u683c\u7684\u52a0\u7279\u6797\u91cd\u673a\u67aa\u73a9\u5bb6\u5bb9\u6613\u8bc6\u522b\uff0c\u6240\u4ee5\u9608\u503c\u653e\u5bbd\u5230 10\u3002\n"
                    "- **\u91d1\u54c1\uff1a\u2265 12 \u683c** (3\u00d74)\u3002\u9632\u5f39\u8863 / \u6ce2\u65af\u6bef / \u751f\u5316\u5206\u6790\u4eea / \u670d\u52a1\u5668\u673a\u67dc / \u9502\u7535\u6c60 / \u5feb\u8247\u90fd\u662f\u3002\n"
                    "- **\u7ea2\u54c1\uff1a\u2265 12 \u683c** (3\u00d74)\u3002\u5c4f\u98ce / \u96f7\u8fbe / \u91d1\u67aa\u9c7c / \u8dd1\u8f66 / \u98de\u884c\u5668\u90fd\u662f\u3002\n\n"
                    "\u5f15\u64ce\u9ed8\u8ba4\u7528\u8be5\u54c1\u8d28\u7684\u6700\u5c0f\u5de8\u7269\u5360\u5730\u4f5c\u4e3a\u4e0b\u9650\uff08\u975e\u2605\uff09\uff1b"
                    "\u9009 \u300c\u2605 \u5177\u4f53\u7269\u54c1\u300d \u540e\u7528\u8be5\u7269\u54c1\u51c6\u786e\u683c\u6570\u3002"
                    "\u4f30\u503c\u4fa7\u7528\u6df7\u5408\u5148\u9a8c\uff08\u91d1\u22487000/\u683c\u3001\u7ea2\u224830000/\u683c\uff09\uff0c\u4e0e\u5360\u683c\u4e0b\u9650\u5206\u5f00\u3002"
                    "\u8be6\u89c1 TROUBLESHOOTING #33\u3002\n\n"
                    "**\u53ef\u89c1\u6027**\uff1aEthan \u80fd\u770b\u5230\u7d2b/\u91d1/\u7ea2 \u4e09\u8272\u8f6e\u5ed3\uff1bAisha \u53ea\u80fd\u770b\u5230\u7d2b \u5927\u4ef6\uff08\u91d1/\u7ea2 \u9700\u731c\uff0c\u91d1/\u7ea2 selectbox \u88ab\u9501\uff09\u3002"
                )
            st.subheader("\u7d2b\u54c1\uff08q=4\uff0c\u53ef\u9009\uff09")
            st.info(
                "**\u5b57\u6bb5\u4f5c\u7528\u8303\u56f4**\uff1a\u4e0a\u65b9\u300c\u51fa\u4ef7\u63a8\u8350\u300d\u91cc\u7684 **\u4ed3\u5e93\u4ef7\u503c\u533a\u95f4 / bucket \u540e\u9a8c** "
                "\u6765\u81ea MC\uff08\u683c\u6570\u3001\u4ef6\u6570\u3001\u603b\u4f30\u4ef7\u3001\u5de8\u7269 **\u4ef6\u6570 band**\uff09\u3002"
                "**\u5747\u683c / \u5747\u4ef7** \u53ea\u7528\u4e8e\u672c\u533a\u4e0b\u65b9\u300c\u5f15\u64ce\u679a\u4e3e\u300d\u9884\u89c8\u4e0e\u5206\u6790\u63a8\u683c\uff0c"
                "**\u4e0d\u4f1a** \u6539\u53d8 MC \u5206\u4f4d\u6570\u3002"
                "\u300c\u2605 \u5177\u4f53\u5de8\u7269\u300d\u4e3b\u8981\u9501\u5b9a\u6bcf\u4ef6\u5360\u683c\uff08\u679a\u4e3e\u7528\uff09\uff1b"
                "\u300c1 \u4e2a / 2\u20133 \u4e2a\u300d\u7c7b\u5de8\u7269\u6570\u4f1a\u8fdb\u5165 MC \u8fc7\u6ee4\u3002",
                icon="\u2139\ufe0f",
            )
            st.info(
                "\u5747\u683c\u586b\u5199\u63d0\u793a\uff1a\u300c**2.9**\u300d \u4e0e \u300c**2.90**\u300d \u4e0d\u540c\u3002"
                "\u300c2.9\u300d = \u6070\u597d 2.9\uff08\u7cbe\u786e\u503c\uff09\uff1b"
                "\u300c2.90\u300d = \u88ab\u622a\u65ad\u8fc7\u7684\u8fd1\u4f3c\u503c\uff08\u4f8b 2.9090909... = 32 \u683c 11 \u4ef6\uff09\u3002"
                "\u8bf7\u4e25\u683c\u6309\u6e38\u620f\u539f\u6837\u586b\u5165\uff0c\u5c3e\u96f6\u4f1a\u88ab\u5f15\u64ce\u7528\u6765\u9501\u4f4f (cells, count) \u5019\u9009\u3002",
                icon="\u2139\ufe0f",
            )
            st.caption(
                "\U0001F4A1 \u5355\u4ef6\u7269\u54c1\u52a0\u901f\uff08\u9002\u7528\u4e8e\u7d2b/\u91d1\uff0c**\u4ec5\u5f71\u54cd\u4e0b\u65b9\u5019\u9009\u6392\u5e8f**\uff0c\u4e0d\u6539 MC \u4ed3\u5e93\u4ef7\u503c\u533a\u95f4\uff09\uff1a"
                "\u5f53 **\u4ef6\u6570 = 1** \u4e14\u586b\u4e86 **\u603b\u4f30\u503c** \u65f6\uff0c"
                "\u5f15\u64ce\u67e5 Item.txt\uff1b\u4ef7\u503c \u00b12% \u547d\u4e2d\u5355\u4ef6\u65f6\u5019\u9009\u4f1a\u9876\u7f6e\u8be5 (cells,count)\uff08"
                "\u4f8b\uff1a\u91d1\u54c1 value=24435 \u2192 2\u683c/1\u4ef6\uff09\u3002"
                "\u591a\u4ef6 (count\u22652) \u4e0d\u8d70\u6b64\u52a0\u901f\u3002"
            )
            # Row 1: cells / count / huge_band (基础格件 + 巨物)
            pr1c1, pr1c2, pr1c3 = st.columns([1, 1, 1.6])
            # Row 2: avg_cells / value_sum / avg_value (读数 / 估价 / 均价)
            pr2c1, pr2c2, pr2c3 = st.columns([1.2, 1.2, 1.2])
            state["purple_cells"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="purple_cells",
                base_widget_key="obs_reading_purple_cells",
                widget_return=pr1c1.number_input(
                    "\u7d2b\u54c1\u603b\u683c\u6570",
                    min_value=0, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u4f18\u54c1\u626b\u63cf \u6216 \u7d2b\u54c1\u8f6e\u5ed3\u6570\u51fa\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff1b\u586b 0 = \u786e\u8ba4\u65e0\u7d2b\u54c1\u3002",
                    key=_rwk("obs_reading_purple_cells", st.session_state),
                ),
                widgets_live=True,
            )
            state["purple_count"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="purple_count",
                base_widget_key="obs_reading_purple_count",
                widget_return=pr1c2.number_input(
                    "\u7d2b\u54c1\u4ef6\u6570",
                    min_value=0, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u827e\u838e R4 \u8f6e\u5ed3\u53ef\u6570\u51fa\uff1b\u4f0a\u68ee\u5728\u7d2b\u54c1\u626b\u63cf\u540e\u4e5f\u80fd\u6570\u3002"
                         "\u586b\u4e86\u540e\u8054\u5408\u63a8\u65ad\u7684\u7d2b\u54c1 bucket \u4f1a\u88ab\u552f\u4e00\u9501\u5b9a\u3002",
                    key=_rwk("obs_reading_purple_count", st.session_state),
                ),
                widgets_live=True,
            )
            _pav_wkey = _rwk("purple_avg_raw_widget", st.session_state)
            state["purple_avg_raw"] = reconcile_avg_raw_widget_return(
                state,
                st.session_state,
                "purple_avg_raw",
                "purple_avg_raw_widget",
                pr2c1.text_input(
                    "\u7d2b\u54c1\u5747\u683c\uff08\u4f18\u54c1\u5747\u683c \u9053\u5177\u8bfb\u6570\uff09",
                    placeholder="\u4f8b 2.90 \u6216 3.43",
                    help="\u300c2.9\u300d\u548c\u300c2.90\u300d\u4e0d\u540c\uff01\u300c2.9\u300d=\u6e38\u620f\u51fa\u7684\u662f\u6070\u597d 2.9 \u7684\u7cbe\u786e\u503c\uff1b"
                         "\u300c2.90\u300d=\u771f\u5b9e\u503c\u88ab\u622a\u65ad\u5728\u7b2c\u4e8c\u4f4d\u5c0f\u6570\uff08\u4f8b\u5982 2.9090909... = 32 \u683c 11 \u4ef6\uff09\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff08\u6587\u672c\u6846\u8bf7\u5168\u9009\u5220\u9664\uff09\u3002",
                    key=_pav_wkey,
                ),
            )
            if str(state.get("purple_avg_raw") or "").strip():
                st.caption(
                    f"\u2713 \u5f53\u524d\u5747\u683c\u8bfb\u6570\uff1a**{state['purple_avg_raw']}**"
                )
            # #region agent log
            agent_debug_log(
                location="streamlit_app.py:purple_avg_text_input",
                message="purple avg widget vs obs after reconcile",
                data={
                    "obs_purple_avg_raw": state.get("purple_avg_raw"),
                    "widget_key": _pav_wkey,
                    "widget_val": st.session_state.get(_pav_wkey),
                },
                hypothesis_id="H19",
                run_id="post-fix",
            )
            # #endregion
            state["purple_value"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="purple_value",
                base_widget_key="obs_reading_purple_value",
                widget_return=pr2c2.number_input(
                    "\u7d2b\u54c1\u603b\u4f30\u503c\uff08\u4f18\u54c1\u4f30\u4ef7 \u00b7 value sum\uff09",
                    min_value=0, max_value=2_000_000, value=None, step=1000,
                    placeholder="\u53ef\u9009",
                    help="\u7559\u7a7a = \u672a\u63d0\u4f9b\uff1b\u586b 0 = \u786e\u8ba4\u65e0\u7d2b\u54c1\u3002",
                    key=_rwk("obs_reading_purple_value", st.session_state),
                ),
                widgets_live=True,
            )
            _pavg_wkey = _rwk("purple_avg_value_widget", st.session_state)
            state["purple_avg_value"] = reconcile_avg_raw_widget_return(
                state,
                st.session_state,
                "purple_avg_value",
                "purple_avg_value_widget",
                pr2c3.text_input(
                    "\u7d2b\u54c1\u5747\u4ef7\uff08\u6bcf\u4ef6 silver\uff09",
                    placeholder="\u4f8b 6328.75 \u6216 9400",
                    help="\u5fc5\u987b\u7528\u672c\u6846\u624b\u52a8\u8f93\u5165\u5c0f\u6570\uff08\u6587\u672c\uff0c\u4e0d\u662f\u6b65\u8fdb\u6570\u5b57\u6846\uff09\u3002"
                         "\u300c6328\u300d\u4e0e\u300c6328.75\u300d\u5f15\u64ce\u4e0d\u540c\uff1a\u5c0f\u6570\u5206\u624d\u9501\u4ef6\u6570\u3002"
                         "\u652f\u6301 6328,75 \u6216 6328.75\u3002"
                         "\u4ec5\u6536\u7d27\u4e0b\u65b9\u5019\u9009\u679a\u4e3e\uff0c\u4e0d\u6539 MC \u4ef7\u503c\u533a\u95f4\u3002"
                         "\u4e0e\u603b\u4f30\u4ef7\u8054\u5408\u65f6\u7528 \u00d7\u4ef6\u6570\u2248\u603b\u4ef7 \u9501\u4ef6\u6570\uff08\u00b11%\uff0c\u22654 \u9879\u540c\u586b\u653e\u5bbd\u81f3 3%\uff09\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff08\u6587\u672c\u6846\u8bf7\u5168\u9009\u5220\u9664\uff09\u3002",
                    key=_pavg_wkey,
                ),
            )
            if str(state.get("purple_avg_value") or "").strip():
                st.caption(
                    f"\u2713 \u5f53\u524d\u5747\u4ef7\uff1a**{state['purple_avg_value']}** silver"
                )
            _purple_opts, _purple_lbls = _huge_options_for_quality(4)
            hydrate_huge_bands_from_obs(state, st.session_state)
            state["purple_huge_band"] = pr1c3.selectbox(
                "\u7d2b\u54c1\u5de8\u7269\u6570\u91cf\uff08\u5df2\u786e\u8ba4\u4e3a\u7d2b\u8272\uff09",
                options=_purple_opts, index=0,
                key="obs_purple_huge_band",
                format_func=lambda b: _purple_lbls[b],
                help="\u53ea\u5728\u901a\u8fc7\u7d2b\u54c1\u8f6e\u5ed3 \u6216 \u4f18\u54c1\u626b\u63cf "
                     "\u786e\u8ba4\u5de8\u7269\u4e3a\u7d2b\u8272\u540e\u586b\u3002\u672a\u786e\u8ba4\u5219\u4fdd\u6301\u300c\u65e0\u300d\u3002"
                     "\u9009\u300c\u2605 \u5177\u4f53\u7269\u54c1\u300d\u53ef\u51c6\u786e\u9501\u5b9a\u683c\u6570\u3002"
                     + _HUGE_SELECTBOX_HELP_TAIL,
            )

            # ---- 紫品候选预览 ----
            sync_obs_from_reading_widgets(
                state, st.session_state, allow_clear=True,
            )
            _prev_huge_raw = state.get("purple_huge_band", "none")
            _prev_huge_band, _prev_huge_override = _resolve_huge_selection(_prev_huge_raw, 4)
            from bidking_lab.capture.apply import (
                avg_raw_obs_widget_drift,
                effective_number_field_for_preview,
                effective_text_field_for_preview,
            )

            _purple_drift = avg_raw_obs_widget_drift(
                state, st.session_state,
                obs_key="purple_avg_raw", base_widget_key="purple_avg_raw_widget",
            )
            if _purple_drift:
                st.caption(f"\u26a0\ufe0f {_purple_drift}")
            _pc = effective_number_field_for_preview(
                state, st.session_state,
                obs_key="purple_cells", base_widget_key="obs_reading_purple_cells",
            )
            _pk = effective_number_field_for_preview(
                state, st.session_state,
                obs_key="purple_count", base_widget_key="obs_reading_purple_count",
            )
            _pv = effective_number_field_for_preview(
                state, st.session_state,
                obs_key="purple_value", base_widget_key="obs_reading_purple_value",
            )
            _pavg_raw = effective_text_field_for_preview(
                state, st.session_state,
                obs_key="purple_avg_raw", base_widget_key="purple_avg_raw_widget",
            )
            _pavg_val = effective_text_field_for_preview(
                state, st.session_state,
                obs_key="purple_avg_value", base_widget_key="purple_avg_value_widget",
            )
            _purple_preview_bucket = QualityBucketObs(
                quality=4,
                total_cells=_pc,
                count=_pk,
                avg_cells=_try_parse_reading(_pavg_raw) if _pavg_raw else None,
                value_sum=_pv,
                avg_value=_try_parse_silver_amount(_pavg_val) if _pavg_val else None,
                huge_band=_prev_huge_band,
                huge_cells_override=_prev_huge_override,
            )
            _has_any = (
                _purple_preview_bucket.total_cells is not None
                or _purple_preview_bucket.count is not None
                or _purple_preview_bucket.avg_cells is not None
                or _purple_preview_bucket.value_sum is not None
                or _purple_preview_bucket.avg_value is not None
                or _purple_preview_bucket.huge_band != "none"
            )
            if _has_any:
                _render_candidate_preview(
                    _purple_preview_bucket,
                    warehouse_capacity=_warehouse_capacity(),
                    quality_label="\u7d2b\u54c1",
                    other_known_cells=_lower_bucket_cells_for_preview(state, 4),
                )

            st.divider()
            st.subheader("\u91d1\u54c1\uff08q=5\uff0c\u53ef\u9009\uff09")
            st.caption(
                "\u91d1\u54c1\u603b\u683c\u6570 / \u603b\u4ef7 / \u5de8\u7269\u4ef6\u6570 \u4f1a\u8fdb\u5165 MC \u8fc7\u6ee4\u3002"
                "\u5747\u683c\u3001\u5747\u4ef7\u3001\u2605 \u5177\u4f53\u5de8\u7269 \u540c\u7d2b\u54c1\u8bf4\u660e\uff08\u5747\u4ef7\u53ea\u6536\u7d27\u4e0b\u65b9\u679a\u4e3e\uff09\u3002"
            )
            # Row 1: cells / count / huge_band
            gr1c1, gr1c2, gr1c3 = st.columns([1, 1, 1.6])
            # Row 2: avg_cells / value_sum / avg_value
            gr2c1, gr2c2, gr2c3 = st.columns([1.2, 1.2, 1.2])
            state["gold_cells"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="gold_cells",
                base_widget_key="obs_reading_gold_cells",
                widget_return=gr1c1.number_input(
                    "\u91d1\u54c1\u603b\u683c\u6570",
                    min_value=0, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u5730\u56fe\u63d0\u4f9b\u300c\u91d1\u8272\u85cf\u54c1\u603b\u683c\u6570\u300d\u63d0\u793a\u65f6\u586b\u5165\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff1b\u586b 0 = \u786e\u8ba4\u65e0\u91d1\u54c1\u3002",
                    key=_rwk("obs_reading_gold_cells", st.session_state),
                ),
                widgets_live=True,
            )
            state["gold_count"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="gold_count",
                base_widget_key="obs_reading_gold_count",
                widget_return=gr1c2.number_input(
                    "\u91d1\u54c1\u4ef6\u6570",
                    min_value=0, max_value=15, value=None, step=1,
                    placeholder="\u53ef\u9009",
                    help="\u67d0\u4e9b\u5730\u56fe\u4f1a\u63d0\u4f9b\u91d1\u8272\u85cf\u54c1\u4ef6\u6570 hint\u3002\u7559\u7a7a = \u672a\u63d0\u4f9b\u3002",
                    key=_rwk("obs_reading_gold_count", st.session_state),
                ),
                widgets_live=True,
            )
            _gav_wkey = _rwk("gold_avg_raw_widget", st.session_state)
            state["gold_avg_raw"] = reconcile_avg_raw_widget_return(
                state,
                st.session_state,
                "gold_avg_raw",
                "gold_avg_raw_widget",
                gr2c1.text_input(
                    "\u91d1\u54c1\u5747\u683c\uff08\u6781\u54c1\u5747\u683c \u9053\u5177\u8bfb\u6570\uff09",
                    placeholder="\u4f8b 3.5 \u6216 4.25",
                    help="\u540c\u7d2b\u54c1\u5747\u683c\u89c4\u5219\uff1a\u300c3.5\u300d\u662f\u7cbe\u786e\u503c\u3001\u300c3.50\u300d\u662f\u88ab\u622a\u65ad\u8fc7\u7684\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff08\u6587\u672c\u6846\u8bf7\u5168\u9009\u5220\u9664\uff09\u3002",
                    key=_gav_wkey,
                ),
            )
            if str(state.get("gold_avg_raw") or "").strip():
                st.caption(
                    f"\u2713 \u5f53\u524d\u5747\u683c\u8bfb\u6570\uff1a**{state['gold_avg_raw']}**"
                )
            state["gold_value"] = reconcile_optional_number_field(
                state,
                st.session_state,
                obs_key="gold_value",
                base_widget_key="obs_reading_gold_value",
                widget_return=gr2c2.number_input(
                    "\u91d1\u54c1\u603b\u4f30\u503c\uff08\u6781\u54c1\u4f30\u4ef7 \u00b7 value sum\uff09",
                    min_value=0, max_value=5_000_000, value=None, step=5000,
                    placeholder="\u53ef\u9009",
                    help="\u67d0\u4e9b\u5730\u56fe\u4f1a\u76f4\u63a5\u7ed9\u51fa\u91d1\u54c1\u603b\u4ef7\uff0c"
                         "\u8bf7\u4f18\u5148\u586b\u8be5\u503c\u3002\u7559\u7a7a = \u672a\u63d0\u4f9b\uff1b"
                         "\u586b 0 = \u786e\u8ba4\u65e0\u91d1\u54c1\u3002"
                         "\u4ef7\u503c\u8fdb MC \u8fc7\u6ee4\uff1b\u82e5\u4ef6\u6570=1 \u4e14\u80fd\u547d\u4e2d Item.txt \u5355\u4ef6\uff0c"
                         "\u4ec5\u5f71\u54cd\u4e0b\u65b9\u5019\u9009\u6392\u5e8f\uff08\u89c1\u7d2b\u54c1\u4e0a\u65b9\u8bf4\u660e\uff09\u3002",
                    key=_rwk("obs_reading_gold_value", st.session_state),
                ),
                widgets_live=True,
            )
            _gavg_wkey = _rwk("gold_avg_value_widget", st.session_state)
            state["gold_avg_value"] = reconcile_avg_raw_widget_return(
                state,
                st.session_state,
                "gold_avg_value",
                "gold_avg_value_widget",
                gr2c3.text_input(
                    "\u91d1\u54c1\u5747\u4ef7\uff08\u6bcf\u4ef6 silver\uff09",
                    placeholder="\u4f8b 32507.6",
                    help="\u67d0\u4e9b\u5730\u56fe R3 \u4f1a\u63d0\u793a\u300c\u91d1\u54c1\u5747\u4ef7 X silver\u300d\u3002"
                         "\u652f\u6301\u5c0f\u6570\uff08\u5982 32507.6\uff09\u3002"
                         "\u4ec5\u6536\u7d27\u4e0b\u65b9\u5019\u9009\u679a\u4e3e\uff0c\u4e0d\u6539 MC \u4ef7\u503c\u533a\u95f4\u3002"
                         "\u8054\u5408\u603b\u4f30\u4ef7\u65f6 \u00d7\u4ef6\u6570\u2248\u603b\u4ef7 \u9501\u4ef6\u6570\u3002"
                         "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff08\u6587\u672c\u6846\u8bf7\u5168\u9009\u5220\u9664\uff09\u3002",
                    key=_gavg_wkey,
                ),
            )
            if str(state.get("gold_avg_value") or "").strip():
                st.caption(
                    f"\u2713 \u5f53\u524d\u91d1\u54c1\u5747\u4ef7\uff1a**{state['gold_avg_value']}** silver"
                )
            _gc = state.get("gold_cells")
            if (
                str(state.get("gold_avg_value") or "").strip()
                and (_gc is None or int(_gc or 0) <= 0)
            ):
                st.caption(
                    "\u26a0\ufe0f \u4ec5\u586b\u91d1\u54c1\u5747\u4ef7\u65f6\uff1a"
                    "**\u4e0d\u8fdb MC \u51fa\u4ef7\u533a\u95f4**\uff08\u9700\u91d1\u54c1\u603b\u683c\u6570/\u603b\u4ef7/\u4ef6\u6570\uff09\uff1b"
                    "\u5747\u4ef7\u53ea\u7528\u4e8e\u4e0b\u65b9\u5019\u9009\u679a\u4e3e\u4e0e\u5206\u6790\u4f30\u7b97\u7684\u91d1\u54c1\u63a8\u683c\u3002"
                )
            _gold_opts, _gold_lbls = _huge_options_for_quality(5)
            hydrate_huge_bands_from_obs(state, st.session_state)
            state["gold_huge_band"] = gr1c3.selectbox(
                "\u91d1\u54c1\u5de8\u7269\u6570\u91cf\uff08\u5df2\u786e\u8ba4\u4e3a\u91d1\u8272\uff09",
                options=_gold_opts, index=0,
                key="obs_gold_huge_band",
                format_func=lambda b: _gold_lbls[b],
                disabled=(hero == "aisha"),
                help="\u827e\u838e\u770b\u4e0d\u5230\u91d1\u54c1\u5de8\u7269\u8f6e\u5ed3\uff0c\u8be5\u9009\u9879\u88ab\u9501\u5b9a\u3002"
                     "Ethan \u53ef\u901a\u8fc7\u6781\u54c1\u626b\u63cf / R5 \u5168\u91cf\u8f6e\u5ed3\u786e\u8ba4\u3002"
                     "\u9009\u300c\u2605 \u5355\u4eba\u90ca\u6e38\u5feb\u8247\u300d\u7b49\u5177\u4f53\u7269\u54c1\u53ef\u51c6\u786e\u9501\u5b9a\u683c\u6570\u3002"
                     + _HUGE_SELECTBOX_HELP_TAIL,
            )

            # ---- 金品候选预览 ----
            sync_obs_from_reading_widgets(
                state, st.session_state, allow_clear=True,
            )
            from bidking_lab.capture.apply import avg_raw_obs_widget_drift

            _gold_drift = avg_raw_obs_widget_drift(
                state, st.session_state,
                obs_key="gold_avg_raw", base_widget_key="gold_avg_raw_widget",
            )
            if _gold_drift:
                st.caption(f"\u26a0\ufe0f {_gold_drift}")
            from bidking_lab.capture.apply import (
                effective_number_field_for_preview,
                effective_text_field_for_preview,
            )

            _g_cells_w = effective_number_field_for_preview(
                state, st.session_state,
                obs_key="gold_cells", base_widget_key="obs_reading_gold_cells",
            )
            _g_avg_w = effective_text_field_for_preview(
                state, st.session_state,
                obs_key="gold_avg_value", base_widget_key="gold_avg_value_widget",
            )
            _gold_hidden = []
            for k in ("gold_count", "gold_value", "gold_avg_raw", "gold_avg_value"):
                ov = state.get(k)
                if ov in (None, "", 0):
                    continue
                if k == "gold_avg_value" and str(ov) == str(_g_avg_w or "").strip():
                    continue
                if k.endswith("_cells"):
                    continue
                _gold_hidden.append(f"{k}={ov}")
            if int(state.get("gold_cells") or -1) == 0 and _g_cells_w is None:
                _gold_hidden.append("gold_cells=0 (session 残留，输入框为空)")
            if _gold_hidden:
                st.caption(
                    "\u2139\ufe0f \u5185\u90e8 obs \u4e0e\u754c\u9762\u4e0d\u4e00\u81f4\uff08\u4e0d\u8fdb MC\uff0c"
                    "\u53ef\u80fd\u5e72\u6270\u679a\u4e3e\uff09\uff1a"
                    + ", ".join(_gold_hidden)
                )
            _gold_preview_bucket = _maybe_gold_bucket(
                state, allow_huge=(hero == "ethan"), ui_state=st.session_state,
            )
            if _gold_preview_bucket is not None:
                _render_candidate_preview(
                    _gold_preview_bucket,
                    warehouse_capacity=_warehouse_capacity(),
                    quality_label="\u91d1\u54c1",
                    other_known_cells=_lower_bucket_cells_for_preview(state, 5),
                )

            st.divider()
            st.subheader("\u7ea2\u54c1\uff08q=6\uff0c\u53ef\u9009\uff09")
            st.caption(
                "\u7ea2\u54c1\u51e0\u4e4e\u4e0d\u4f1a\u88ab\u4f30\u4ef7\u9053\u5177\u51c6\u786e\u8bfb\u51fa "
                "\u2014 \u63a8\u8350\u586b\u4e2a\u4ef7\u503c\u533a\u95f4\uff08\u67d0\u4e9b\u5730\u56fe\u4f1a\u63d0\u4f9b\uff09\u3002"
                "\u7ea2\u54c1\u5de8\u7269 **\u4ef6\u6570 band** \u4f1a\u8fdb\u5165 MC \u8fc7\u6ee4\uff08\u4e0e\u683c\u6570\u3001\u4ef7\u503c\u533a\u95f4\u8054\u5408\uff09\u3002"
                "\u2605 \u5177\u4f53\u7ea2\u8272\u5de8\u7269\u540c\u7d2b/\u91d1\uff1a\u4e3b\u8981\u5f71\u54cd\u679a\u4e3e\u63a8\u683c\u3002"
            )

            c_chk1, c_chk2 = st.columns(2)
            state["small_warehouse_confirmed"] = c_chk1.checkbox(
                "\U0001F4E6 \u786e\u8ba4\u5c0f\u4ed3\uff08\u7ea2\u54c1\u6781\u5c11\uff09",
                value=False,
                key="obs_small_warehouse_confirmed",
                help="\u52fe\u9009\u540e\uff0c\u5f15\u64ce\u4f1a\u9650\u5236\u7ea2\u54c1\u683c\u6570\u4e0a\u9650\u4e3a\u4ed3\u5e93\u7684 5%\uff08"
                     "\u4f8b\u5982 80\u683c\u4ed3\u5e93 \u2192 \u7ea2\u54c1 \u2264 4\u683c\uff09\u3002"
                     "\u9002\u7528\u4e8e\u4f60\u80fd\u786e\u8ba4\u8fd9\u662f\u5c0f\u4ed3\u3001\u7ea2\u54c1\u5f88\u5c11\u6216\u6ca1\u6709\u7684\u573a\u666f\u3002",
            )
            state["red_confirmed_none"] = c_chk2.checkbox(
                "\u2705 \u5df2\u786e\u8ba4\u65e0\u7ea2\u54c1\uff08\u7ed3\u7b97\u786e\u8ba4\uff09",
                value=False,
                key="obs_red_confirmed_none",
                help="\u52fe\u9009\u540e\uff0c\u5f15\u64ce\u5f3a\u5236 q=6 cells=0\uff08MC \u786c\u7ea6\u675f\uff0c\u4f1a\u663e\u8457\u538b\u4f4e\u4ed3\u5e93\u4ef7\u503c\u533a\u95f4\uff0c\u5c5e\u6b63\u786e\u884c\u4e3a\uff09\u3002"
                     "\u9002\u7528\u4e8e\u7ed3\u7b97\u540e\u786e\u8ba4\u65e0\u7ea2\u54c1\u3001\u6216\u767d+\u7eff+\u84dd+\u7d2b+\u91d1 = \u4ed3\u5e93\u603b\u683c\u6570\u3002",
            )
            red_locked = state["red_confirmed_none"] or state["small_warehouse_confirmed"]

            state["red_cells_total"] = st.number_input(
                "\u7ea2\u54c1\u603b\u683c\u6570\uff08\u73cd\u54c1\u626b\u63cf / \u5730\u56fe hint\uff09",
                min_value=0, value=None, step=1,
                placeholder="\u53ef\u9009",
                disabled=red_locked,
                help="\u4f0a\u68ee \u73cd\u54c1\u626b\u63cf \u9053\u5177\u8bfb\u51fa\u7684\u7ea2\u54c1\u603b\u683c\u6570\u3002"
                     "\u7559\u7a7a = \u672a\u63d0\u4f9b\uff1b\u586b 0 = \u786e\u8ba4\u65e0\u7ea2\u54c1\u3002"
                     "\u586b\u5165\u540e MC \u4f1a\u989d\u5916\u8fc7\u6ee4 |truth.q6\u683c - \u4f60\u586b\u7684\u503c| \u2264 \u5bb9\u5dee\u3002"
                     "\u82e5\u4f60\u52fe\u9009\u4e86\u4e0a\u9762\u300c\u5df2\u786e\u8ba4\u65e0\u7ea2\u54c1\u300d\uff0c\u8fd9\u91cc\u4f1a\u88ab\u9501\u5b9a\u4e3a 0\u3002",
                key=_rwk("obs_reading_red_cells_total", st.session_state),
            )

            c1, c2 = st.columns(2)
            state["red_value_lo"] = c1.number_input(
                "\u7ea2\u54c1\u4ef7\u503c\u4e0b\u9650\uff08silver\uff09",
                min_value=0, max_value=10_000_000, value=None, step=10000,
                placeholder="\u53ef\u9009",
                disabled=red_locked,
                help="\u7559\u7a7a = \u672a\u63d0\u4f9b\u3002\u4e0a\u4e0b\u9650\u90fd\u586b\u624d\u4f1a\u542f\u7528\u4ef7\u503c\u8fc7\u6ee4\u3002",
                key=_rwk("obs_reading_red_value_lo", st.session_state),
            )
            state["red_value_hi"] = c2.number_input(
                "\u7ea2\u54c1\u4ef7\u503c\u4e0a\u9650\uff08silver\uff09",
                min_value=0, max_value=10_000_000, value=None, step=10000,
                placeholder="\u53ef\u9009",
                disabled=red_locked,
                help="\u7559\u7a7a = \u672a\u63d0\u4f9b\u3002\u4e0a\u4e0b\u9650\u90fd\u586b\u624d\u4f1a\u542f\u7528\u4ef7\u503c\u8fc7\u6ee4\u3002",
                key=_rwk("obs_reading_red_value_hi", st.session_state),
            )
            _red_opts, _red_lbls = _huge_options_for_quality(6)
            state["red_huge_band"] = st.selectbox(
                "\u7ea2\u54c1\u5de8\u7269\u6570\u91cf\uff08\u5df2\u786e\u8ba4\u4e3a\u7ea2\u8272\uff09",
                options=_red_opts, index=0,
                key="obs_red_huge_band",
                format_func=lambda b: _red_lbls[b],
                disabled=(hero == "aisha") or red_locked,
                help="\u827e\u838e\u770b\u4e0d\u5230\u7ea2\u54c1\u5de8\u7269\u8f6e\u5ed3\u3002\u4f0a\u68ee "
                     "\u53ef\u4ee5\u901a\u8fc7\u73cd\u54c1\u626b\u63cf\uff08\u7ea2\u54c1\u603b\u683c\u6570\uff09 / R5 \u5168\u91cf\u8f6e\u5ed3"
                     "\u3001\u6216\u6839\u636e 4\u00d74 \u5de8\u7269\u6392\u9664\u77f3\u72ee\u5b50\u540e\u786e\u8ba4\u3002"
                     "\u9009\u300c\u2605 \u5177\u4f53\u7269\u54c1\u300d\u53ef\u51c6\u786e\u9501\u5b9a\u683c\u6570\u3002"
                     + _HUGE_SELECTBOX_HELP_TAIL,
            )

            st.divider()
            st.markdown("### \U0001F4D0 \u672a\u786e\u8ba4\u54c1\u8d28\u7684\u5de8\u7269 / \u5927\u4ef6\uff08\u6309\u5f62\u72b6\uff09")
            st.warning(
                "\U0001F9EA **\u6d4b\u8bd5\u529f\u80fd\uff0c\u6682\u672a\u63a5\u5165\u63a8\u65ad\u63a5\u53e3**\u3002\u672c\u533a\u4ec5\u8bb0\u5f55\u4f60\u770b\u5230\u7684\u5f62\u72b6\u6570\u91cf\uff0c"
                "\u4e0d\u4f1a\u88ab\u63a8\u65ad\u5f15\u64ce\u4f7f\u7528\u3002\u82e5\u80fd\u786e\u8ba4\u54c1\u8d28\uff0c"
                "\u8bf7\u5728\u4e0a\u65b9\u5bf9\u5e94 bucket \u7684\u300c\u5de8\u7269\u6570\u91cf\u300d\u4e0b\u62c9\u6846\u9009\u62e9\u300c\u2605 \u5177\u4f53\u7269\u54c1\u300d\uff0c\u5f15\u64ce\u4f1a\u7acb\u5373\u4f7f\u7528\u3002"
            )
            if "seen_shapes" not in st.session_state:
                st.session_state["seen_shapes"] = {s: 0 for s in BIG_ITEMS_BY_SHAPE}
            seen = st.session_state["seen_shapes"]

            shape_cols = st.columns(len(BIG_ITEMS_BY_SHAPE))
            for col, (shape, cands) in zip(shape_cols, BIG_ITEMS_BY_SHAPE.items()):
                seen[shape] = col.number_input(
                    shape, min_value=0, max_value=6, value=seen.get(shape, 0),
                    step=1, key=_rwk(f"shape_{shape}", st.session_state),
                    help=f"{len(cands)} \u4ef6\u5019\u9009\uff0c\u5c55\u5f00\u5b57\u5178\u770b\u8be6\u60c5",
                )

            with st.expander("\U0001F4D6 \u5f62\u72b6 \u2192 \u7269\u54c1 \u5019\u9009\u5b57\u5178", expanded=False):
                for shape, cands in BIG_ITEMS_BY_SHAPE.items():
                    lines = [f"**{shape}**\uff1a\u5171 {len(cands)} \u4ef6\u5019\u9009"]
                    for c in cands:
                        uniq = "\u2606" if c["unique"] else " "
                        lines.append(
                            f"- {uniq} {QUALITY_NAME[c['q']]}\u54c1 (q={c['q']})  \u00b7  "
                            f"**{c['name']}**  \u00b7  {c['value']:,} silver"
                        )
                    st.markdown("\n".join(lines))

            sync_obs_from_reading_widgets(
                state, st.session_state, allow_clear=True,
            )
            _record_live_observation_snapshot(
                state,
                source="manual",
                event_kind="manual_update",
            )
            _attach_inference_session_source(state)
            # #region agent log
            agent_debug_log(
                location="streamlit_app.py:obs_tab_after_sync",
                message="obs after obs-tab widget sync",
                data={
                    "wg_cells": state.get("wg_cells"),
                    "blue_cells": state.get("blue_cells"),
                    "purple_cells": state.get("purple_cells"),
                    "purple_count": state.get("purple_count"),
                    "purple_value": state.get("purple_value"),
                    "purple_avg_raw": state.get("purple_avg_raw"),
                    "wg_widget": st.session_state.get(
                        _rwk("obs_reading_wg_cells", st.session_state),
                    ),
                    "purple_count_widget": st.session_state.get(
                        _rwk("obs_reading_purple_count", st.session_state),
                    ),
                    "purple_avg_widget": st.session_state.get(
                        _rwk("purple_avg_raw_widget", st.session_state),
                    ),
                },
            hypothesis_id="H6,H15,H18",
                run_id="post-fix",
            )
            # #endregion

    _render_obs_tab_fragment()


def _render_hint_tab_impl() -> None:
    from bidking_lab.capture.apply import sync_obs_from_reading_widgets

    _hint_cat = st.session_state.get("obs_map_category", "mansion")
    _hint_map_choices = _maps_for_category(maps, _hint_cat)
    _wh_live = _session_int("obs_warehouse_cells") or int(
        state.get("warehouse_cells") or 0,
    )
    _mid_live = _effective_map_id(_hint_map_choices, obs=state)
    warehouse_ready = _wh_live > 0
    map_ready = _mid_live is not None
    sync_obs_from_reading_widgets(
        state, st.session_state, allow_clear=False,
    )
    _hint_budget_err = check_warehouse_cell_budget(state)
    inference_ready = warehouse_ready and map_ready and _hint_budget_err is None
    if _mid_live is not None:
        state["map_id"] = _mid_live
    state["warehouse_cells"] = _wh_live
    state["warehouse_cells_mode"] = st.session_state.get(
        "obs_warehouse_cells_mode",
        "strict",
    )
    state["warehouse_cells_tolerance"] = int(
        st.session_state.get("obs_warehouse_cells_tolerance") or 0
    )

    st.subheader("\u51fa\u4ef7\u5efa\u8bae")
    st.caption(
        "\u57fa\u4e8e\u6761\u4ef6 Monte-Carlo\uff1a\u91c7\u6837 N \u6b21\u672c\u56fe\u4ed3\u5e93\uff0c"
        "\u4fdd\u7559\u4ed3\u5e93\u5bb9\u91cf\u00b1\u5bb9\u5dee\u4e14\u4f4e\u54c1 cells \u5339\u914d\u7684\u6837\u672c\uff0c"
        "\u5148\u770b\u4ef7\u503c\u53ef\u80fd\u533a\u95f4\uff08\u540e\u9a8c bucket \u5206\u4f4d\u6570\uff09\u3002"
    )
    st.caption(
        "\U0001F4A1 \u51fa\u4ef7\u533a\u95f4\u4f1a\u540c\u65f6\u6309\u4ed3\u5e93\u603b\u683c\u6570 + \u4f60\u586b\u7684\u6bcf\u4e2a bucket "
        "(cells / count / value_sum / huge\u4ef6\u6570 / \u7ea2\u4ef7\u503c\u533a\u95f4) \u8fc7\u6ee4 MC\u3002"
        "**\u5747\u683c\u3001\u5747\u4ef7\u4e0d\u8fdb MC**\uff0c\u53ea\u6536\u7d27\u8bfb\u6570\u533a\u7684\u5019\u9009\u679a\u4e3e\u3002"
        "\u6837\u672c\u4e0d\u8db3 30 \u65f6\u4f1a\u81ea\u52a8\u653e\u5bbd\u5bb9\u5dee\u5e76\u6807 \u26a0\ufe0f \u4f4e\u7f6e\u4fe1\u3002"
        "\u300c\u7ea2 bucket \u300d\u52fe\u9009\u300c\u5df2\u786e\u8ba4\u65e0\u7ea2\u54c1\u300d\u540e\uff0cMC \u4f1a\u5f3a\u5236 q=6 cells=0\u3002"
        "\u5de6\u4fa7\u680f\u300c\u603b\u85cf\u54c1\u4ef6\u6570\u300d>0 \u65f6\u4f1a\u8fdb MC\uff08\u7ea6\u675f sum(\u4ef6\u6570)\uff09\uff0c"
        "\u7ed3\u679c\u91cc\u6fc0\u6d3b\u7ea6\u675f\u53ef\u80fd\u663e\u793a items\u2248N\u3002"
    )
    _ready_wh = _wh_live
    _ready_tic = int(st.session_state.get("obs_total_item_count") or 0)
    _ready_mid = state.get("map_id")
    _ready_map_label = (
        _map_names.get(int(_ready_mid), str(_ready_mid)) if _ready_mid else "\u672a\u9009"
    )
    _tic_part = (
        f"\u603b\u4ef6\u6570 **{_ready_tic}** \u2713"
        if _ready_tic > 0
        else "\u603b\u4ef6\u6570\uff08\u672a\u586b\uff0c\u4e0d\u8fdb MC\uff09"
    )
    _warehouse_status = " \u2713" if warehouse_ready else " \u2717"
    _map_status = " \u2713" if map_ready else " \u2717"
    st.caption(
        f"\u5c31\u7eea\uff1a\u4ed3\u5e93 **{_ready_wh}** \u683c"
        f"{_warehouse_status}"
        f"\u00a0\u00b7\u00a0\u5730\u56fe **{_ready_map_label}**"
        f"{_map_status}"
        f"\u00a0\u00b7\u00a0{_tic_part}"
    )
    if _hint_budget_err:
        st.error(_hint_budget_err)
    if not warehouse_ready:
        st.error(
            "\u8bf7\u5728\u5de6\u4fa7\u680f\u300c\u4ed3\u5e93\u603b\u683c\u6570\u300d\u586b\u5199\u5927\u4e8e 0 \u7684\u6570\u5b57"
            "\uff08\u9762\u677f\u9876\u90e8\u300c\u6240\u6709\u85cf\u54c1\u603b\u5360\u7528\u2026N \u683c\u300d\uff0c"
            "\u4e0d\u662f\u626b\u63cf\u5de5\u5177\u7684\u84dd/\u767d\u7eff\u683c\u6570\uff09\u3002"
            "\u586b\u5199\u540e\u8bf7\u70b9\u4e00\u4e0b\u8f93\u5165\u6846\u5916\u533a\u57df\u4ee5\u786e\u4fdd\u751f\u6548\u3002"
        )
    elif not map_ready:
        st.error(
            "\u8bf7\u5728\u5de6\u4fa7\u680f\u9009\u62e9 **\u5177\u4f53\u5730\u56fe**"
            "\uff08\u5148\u9009\u522b\u5885/\u6c89\u8239\uff0c\u518d\u70b9\u5177\u4f53\u5730\u56fe\u4e0b\u62c9\u6846\uff09\u3002"
        )
    _cached_bundle = st.session_state.get("_hint_bundle")
    from bg_inference import hint_bundle_stale_report

    _stale_report = hint_bundle_stale_report(
        _cached_bundle, state, inference_ready=inference_ready,
    )
    if _stale_report["stale"]:
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:hint_bundle_stale",
            message="cached hint bundle invalidated",
            data=_stale_report,
            hypothesis_id="H20",
            run_id="post-fix",
        )
        # #endregion
        st.warning(
            "\u8bfb\u6570\u6216\u4ed3\u5e93/\u5730\u56fe\u5df2\u6539\u52a8\uff0c\u4e0a\u6b21\u63a8\u65ad\u7ed3\u679c\u5df2\u4f5c\u5e9f\u3002"
            "\u8bf7\u70b9\u300c\u8fd0\u884c\u51fa\u4ef7 hint\u300d\u91cd\u65b0\u63a8\u65ad\u3002"
        )
        st.session_state.pop("_hint_bundle", None)
        st.session_state.pop("_hint_tab_done_flash", None)
        _cached_bundle = None
    elif _cached_bundle is not None:
        st.caption("\u4e0b\u65b9\u4e3a\u5df2\u7f13\u5b58\u7684\u63a8\u65ad\u7ed3\u679c\uff08\u540e\u53f0\u6216\u624b\u52a8\u8fd0\u884c\uff09\u3002")
    _render_main_bid_advice_panel(_cached_bundle)
    _hint_btn_cols = st.columns([3, 1])
    with _hint_btn_cols[0]:
        _run_hint_clicked = st.button(
            "\u8fd0\u884c\u51fa\u4ef7 hint",
            key="run_hints",
            type="primary",
            disabled=not inference_ready or _mc_ui_running(),
        )
    with _hint_btn_cols[1]:
        if _mc_ui_running() and st.button(
            "\u53d6\u6d88\u63a8\u65ad",
            key="cancel_bg_hint",
            type="secondary",
        ):
            _cancel_background_hint()
            st.rerun()
    if _run_hint_clicked:
        if not inference_ready:
            st.warning(
                "\u5c1a\u672a\u6ee1\u8db3\u63a8\u65ad\u6761\u4ef6\uff08\u4ed3\u5e93 > 0 \u4e14\u5df2\u9009\u5730\u56fe\uff09\u3002"
                "\u8bf7\u67e5\u770b\u4e0a\u65b9\u5c31\u7eea\u72b6\u6001\u3002"
            )
        else:
            _box = st.session_state.get("_bg_infer_box")
            if _box and _box.get("cancel") is not None:
                _box["cancel"].set()
            st.session_state.pop("_bg_infer_box", None)
            st.session_state.pop("_hint_bundle", None)
            st.session_state["_request_bg_hint_manual"] = True
            st.session_state["_bg_infer_status"] = "idle"
            _clear_hint_done_flash()
            # #region agent log
            agent_debug_log(
                location="streamlit_app.py:run_hints_button",
                message="manual hint queued (background)",
                data={"n_trials": n_trials, "map_id": state.get("map_id")},
                hypothesis_id="perf",
                run_id="infer",
            )
            # #endregion
            st.rerun()

    if _cached_bundle is not None and not _hint_bundle_is_stale(
        _cached_bundle, state, inference_ready=inference_ready,
    ):
        from bidking_lab.inference.posterior import bucket_posterior_stats

        session = _cached_bundle["session"]
        filter_result = _cached_bundle["filter_result"]
        conditional_values = _cached_bundle["conditional_values"]
        _bucket_posteriors = _cached_bundle.get("bucket_posteriors")
        if _bucket_posteriors is None:
            _legacy_truths = _cached_bundle.get("conditional_truths")
            if _legacy_truths:
                _bucket_posteriors = {
                    q: bucket_posterior_stats(_legacy_truths, q)
                    for q in (1, 3, 4, 5, 6)
                }
        all_values = _cached_bundle["all_values"]
        analytical = _cached_bundle["analytical"]
        snipe = _cached_bundle["snipe"]
        pass_rec = _cached_bundle["pass_rec"]

        from experimental_tabs import render_joint_reasoning_summary

        render_joint_reasoning_summary(
            session=session,
            per_bucket_top=per_bucket_top,
            expanded=False,
        )

        # ---- Value range + distribution chart (top) ----
        st.markdown(
            "### \U0001F4CA \u4ed3\u5e93\u4ef7\u503c\u53ef\u80fd\u533a\u95f4"
        )

        constraint_summary = "\u00b7".join(filter_result.constraints_applied)
        tol_summary = (
            f"cells \u00b1{filter_result.cells_tol}\u30001count \u00b1{filter_result.count_tol}"
            f"\u3001value \u00b1{int(filter_result.value_rel_tol*100)}%"
            f"\u3001warehouse \u00b1{filter_result.warehouse_tol}"
        )
        if filter_result.low_confidence and filter_result.n_final > 0:
            st.warning(
                f"\u26a0\ufe0f \u4f4e\u7f6e\u4fe1\uff1a\u4e25\u683c\u5bb9\u5dee\u4e0b\u5339\u914d\u6837\u672c\u4e0d\u8db3 30\uff0c\u5df2\u81ea\u52a8\u653e\u5bbd\u5230 "
                f"**\u7b49\u7ea7 {filter_result.tol_level} ({tol_summary})**\u3002"
                f"\u6700\u7ec8 n={filter_result.n_final} / {filter_result.n_total} \u6837\u672c\u3002"
                f"\n\n\u6fc0\u6d3b\u7ea6\u675f\uff1a{constraint_summary}"
            )
        elif filter_result.n_final == 0:
            if analytical is not None:
                st.warning(
                    f"\u26a0\ufe0f MC \u8fc7\u6ee4\u6837\u672c\u4e0d\u8db3\uff08\u4f60\u7684\u4ed3\u5e93\u5927\u5c0f\u5728\u672c\u56fe\u5206\u5e03\u5c3e\u90e8\uff09\uff0c"
                    f"\u5df2\u5207\u6362\u5230\u201c\u5206\u6790\u4f30\u7b97\u201d\u6a21\u5f0f\uff08\u57fa\u4e8e\u4f60\u586b\u7684\u683c\u6570 \u00d7 \u6bcf\u683c\u5148\u9a8c\u4ef7\u503c\uff09\u3002"
                    f"\n\n\u6fc0\u6d3b\u7ea6\u675f\uff1a{constraint_summary}"
                )
            else:
                st.error(
                    f"\u26d4 \u8fc7\u6ee4\u540e\u96f6\u5339\u914d\uff08\u5373\u4f7f\u5728\u6700\u5bbd\u5bb9\u5dee\u4e0b\uff09\u3002\u8bf7\u68c0\u67e5\uff1a"
                    f"\u4f60\u586b\u7684\u67d0\u4e2a bucket cells / count / value \u662f\u5426\u8d85\u51fa\u672c\u56fe\u53ef\u80fd\u8303\u56f4\uff1f"
                    f"\n\n\u6fc0\u6d3b\u7ea6\u675f\uff1a{constraint_summary}"
                )
        else:
            st.info(
                f"\u2705 \u4e25\u683c\u5bb9\u5dee\u5339\u914d {filter_result.n_final} / {filter_result.n_total} \u6837\u672c"
                f"\uff08{tol_summary}\uff09\u3002\u7ea6\u675f\uff1a{constraint_summary}"
            )

        if not conditional_values and analytical is not None:
            st.markdown("#### \U0001F4CA \u5206\u6790\u4f30\u7b97\uff08\u57fa\u4e8e\u683c\u6570 \u00d7 \u5148\u9a8c\u4ef7\u503c\uff09")
            if analytical.red_auto_detected and analytical.red_cells_inferred > 0:
                st.info(
                    f"\U0001F534 \u81ea\u52a8\u63a8\u65ad\u7ea2\u54c1\u683c\u6570\uff1a"
                    f"\u4ed3\u5e93 {session.warehouse_capacity()} - "
                    f"\u5df2\u77e5\u683c\u6570\u603b\u548c {session.warehouse_capacity() - analytical.red_cells_inferred} "
                    f"= **{analytical.red_cells_inferred} \u683c\u7ea2\u54c1**"
                )
            elif analytical.red_auto_detected and analytical.red_cells_inferred == 0:
                st.success(
                    f"\u2705 \u81ea\u52a8\u68c0\u6d4b\uff1a\u5df2\u77e5 bucket \u683c\u6570\u603b\u548c "
                    f"= \u4ed3\u5e93\u5bb9\u91cf {session.warehouse_capacity()}\uff0c"
                    f"\u786e\u8ba4\u65e0\u7ea2\u54c1\u3002"
                )
            elif not analytical.red_auto_detected and analytical.red_cells_inferred == 0:
                wh = session.warehouse_capacity() or 0
                known_sum = sum(
                    b.total_cells for b in session.buckets.values()
                    if b.total_cells is not None and b.quality != 6
                )
                remaining = wh - known_sum
                if remaining > 0:
                    missing_names = []
                    for q in (5,):
                        if q not in session.buckets:
                            missing_names.append(
                                {5: "\u91d1\u54c1"}.get(q, f"q{q}")
                            )
                    if missing_names:
                        st.warning(
                            f"\u26a0\ufe0f \u5269\u4f59 **{remaining} \u683c**\u672a\u5206\u914d"
                            f"\uff08{'/'.join(missing_names)}\u672a\u586b\u5199\uff09\uff0c"
                            f"\u65e0\u6cd5\u5224\u65ad\u7ea2\u54c1\u5360\u6bd4\u3002"
                            f"\u4f30\u503c\u533a\u95f4\u5df2\u6309\u201c\u5168\u91d1\u201d\u5230\u201c\u5168\u7ea2\u201d\u8303\u56f4\u663e\u793a\u3002"
                            f"\u586b\u5199\u91d1\u54c1\u683c\u6570\u53ef\u5927\u5e45\u7f29\u5c0f\u533a\u95f4\u3002"
                        )
            col1, col2, col3 = st.columns(3)
            col1.metric(
                "\u4fdd\u5b88\u4f30\u503c (low)",
                f"{analytical.total_value_low:,}",
                help="\u6bcf\u683c\u4ef7\u503c\u53d6\u5148\u9a8c\u7684 60%\uff08\u8003\u8651\u4f4e\u4ef7\u7269\u54c1\u5360\u6bd4\u591a\uff09",
            )
            col2.metric(
                "\u4e2d\u4f4d\u4f30\u503c (mid)",
                f"{analytical.total_value_mid:,}",
                help="\u6bcf\u683c\u4ef7\u503c\u53d6\u5148\u9a8c\u4e2d\u4f4d\u6570",
            )
            col3.metric(
                "\u4e50\u89c2\u4f30\u503c (high)",
                f"{analytical.total_value_high:,}",
                help="\u6bcf\u683c\u4ef7\u503c\u53d6\u5148\u9a8c\u7684 150%\uff08\u8003\u8651\u9ad8\u4ef7\u7269\u54c1/\u5de8\u7269\u5360\u6bd4\u591a\uff09",
            )
            with st.expander("\U0001F4CB \u6309 bucket \u4ef7\u503c\u660e\u7ec6", expanded=True):
                st.text(analytical.breakdown_text)
                method_note = (
                    "\u8ba1\u7b97\u65b9\u6cd5\uff1a\u6bcf\u4e2a bucket \u7684\u683c\u6570 \u00d7 \u8be5\u54c1\u8d28\u6bcf\u683c\u5148\u9a8c\u4ef7\u503c\u3002"
                    "\u4fdd\u5b88/\u4e2d\u4f4d/\u4e50\u89c2 \u5206\u522b\u5bf9\u5e94\u5148\u9a8c\u7684 60%/100%/150%\u3002"
                )
                if analytical.red_auto_detected:
                    method_note += "\u7ea2\u54c1\u683c\u6570\u4e3a\u4ed3\u5e93\u603b\u91cf - \u5176\u4ed6 bucket \u683c\u6570\u4e4b\u548c\u3002"
                elif analytical.red_cells_inferred == 0 and not analytical.red_auto_detected:
                    method_note += (
                        "\u672a\u586b\u5199\u7684 bucket \u683c\u6570\u672a\u77e5\uff0c"
                        "\u4f30\u503c\u533a\u95f4\u5305\u542b\u201c\u5168\u91d1\u201d\u5230\u201c\u5168\u7ea2\u201d\u7684\u53ef\u80fd\u6027\u3002"
                    )
                st.caption(method_note)
        elif not conditional_values:
            pass  # already shown error above
        else:
            p25, p50, p75, p90 = np.percentile(
                conditional_values, [25, 50, 75, 90]
            )
            col1, col2, col3, col4 = st.columns(4)
            col1.metric(
                "\u60b2\u89c2\u4f30\u503c P25",
                f"{int(p25):,}",
                help="25% \u7684\u53ef\u80fd\u4ed3\u5e93\u4ef7\u503c\u4f4e\u4e8e\u6b64\u6570\u3002\u4f5c\u4e3a\u79d2\u4ed3\u4fdd\u5e95\u4ef7\u53c2\u8003\u3002",
            )
            col2.metric(
                "\u4e2d\u4f4d\u4f30\u503c P50",
                f"{int(p50):,}",
                help="50% \u4ed3\u5e93\u4ef7\u503c\u9ad8\u4e8e\u6b64\u30015 \u6210\u4ed3\u5e93\u4f4e\u4e8e\u6b64\u3002\u300c\u4e2d\u95f4\u9884\u671f\u300d\u3002",
            )
            col3.metric(
                "\u504f\u4e50\u89c2 P75",
                f"{int(p75):,}",
                help="75% \u4ed3\u5e93\u4ef7\u503c\u4f4e\u4e8e\u6b64\u3002\u900f\u652f\u51fa\u4ef7\u8fd1\u8fd9\u4e2a\u6570\u662f\u6709\u98ce\u9669\u7684\u3002",
            )
            col4.metric(
                "\u4e50\u89c2\u4e0a\u9650 P90",
                f"{int(p90):,}",
                help="90% \u4ed3\u5e93\u4ef7\u503c\u4f4e\u4e8e\u6b64\u3002\u4ec5\u5728\u5728\u4f60\u51e0\u4e4e\u80af\u5b9a\u662f\u5728\u62c5\u9ed1\u9a6c\u4ed3\u65f6\u4f7f\u7528\u3002",
            )

            x_max = int(np.percentile(all_values, 98))
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            bins = np.linspace(0, x_max, 48)
            ax.hist(
                np.clip(all_values, 0, x_max), bins=bins, alpha=0.28,
                color="#94a3b8", edgecolor="white", linewidth=0.4,
                label=f"All samples (n={len(all_values)})",
            )
            n_constraints = len(filter_result.constraints_applied)
            cond_legend = (
                f"All constraints (n={len(conditional_values)})"
                if n_constraints > 1
                else f"Warehouse {state.get('warehouse_cells', 0)}\u00b1{filter_result.warehouse_tol} "
                     f"cells (n={len(conditional_values)})"
            )
            ax.hist(
                np.clip(conditional_values, 0, x_max), bins=bins, alpha=0.72,
                color="#3b82f6", edgecolor="white", linewidth=0.4,
                label=cond_legend,
            )
            ax.axvline(p25, color="#16a34a", linestyle=":", linewidth=2,
                       label=f"Pessimistic P25 = {int(p25):,}")
            ax.axvline(p50, color="#0f172a", linewidth=1.6,
                       label=f"Median P50 = {int(p50):,}")
            ax.axvline(p75, color="#ea580c", linestyle="--", linewidth=2,
                       label=f"Optimistic P75 = {int(p75):,}")
            ax.axvline(p90, color="#9333ea", linestyle="--", linewidth=1.2,
                       alpha=0.75, label=f"Upside P90 = {int(p90):,}")
            ax.set_xlabel("Total session value (silver)")
            ax.set_ylabel("Number of MC sessions")
            style_value_hist(ax, x_max=x_max)
            ax.xaxis.set_major_formatter(
                plt.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M")
            )
            ax.legend(loc="upper right", fontsize=7)
            ax.tick_params(labelsize=8)
            ax.xaxis.label.set_size(8)
            ax.yaxis.label.set_size(8)
            plt.tight_layout()
            st.pyplot(
                fig,
                clear_figure=True,
                **layout_width_kwargs("pyplot", stretch=False),
            )

        # ---- Per-bucket posterior cards ----
        if _bucket_posteriors:
            st.divider()
            st.markdown("### \U0001F50D \u5404 bucket \u540e\u9a8c\u4f30\u8ba1\uff08\u8fc7\u6ee4\u540e\u6837\u672c\u4e0a\u7684\u5206\u4f4d\uff09")
            user_specified_cells: dict[int, int] = {}
            mc_estimated_qs: list[str] = []
            QUALITY_LABELS = {
                1: "\u767d/\u7eff (q=1,2)", 3: "\u84dd (q=3)", 4: "\u7d2b (q=4)",
                5: "\u91d1 (q=5)", 6: "\U0001F534 \u7ea2 (q=6)",
            }
            for q in (1, 3, 4, 5, 6):
                obs_b = session.buckets.get(q)
                if obs_b and obs_b.total_cells is not None:
                    user_specified_cells[q] = obs_b.total_cells
                else:
                    mc_estimated_qs.append(QUALITY_LABELS[q])
            user_cells_sum = sum(user_specified_cells.values())
            wh = session.warehouse_capacity()
            remaining_budget = wh - user_cells_sum if wh else None

            caption_parts = [
                "P50 = \u4e2d\u4f4d\u540e\u9a8c\u3002\u300c\u7a7a bucket %\u300d = \u5728\u8fc7\u6ee4\u540e\u6837\u672c\u4e2d\u6b64\u54c1\u8d28\u6ca1\u6709\u4efb\u4f55\u7269\u54c1\u7684\u6bd4\u4f8b\u3002"
                "\u7ea2\u54c1\u884c\u9ed8\u8ba4\u9ad8\u4eae\u2014\u2014\u73a9\u5bb6\u770b\u4e0d\u5230\u7ea2\u54c1\u8f6e\u5ed3 (Aisha) / \u9700\u8981\u9053\u5177\u624d\u80fd\u8bfb (Ethan)\uff0c"
                "\u662f\u63a8\u65ad\u5f15\u64ce\u7684\u4e3b\u8981\u4ef7\u503c\u8d21\u732e\u70b9\u3002",
            ]
            if mc_estimated_qs and remaining_budget is not None and remaining_budget > 0:
                caption_parts.append(
                    f"\n\n\u26a0\ufe0f **\u672a\u586b\u5199\u7684 bucket\uff08{'/'.join(mc_estimated_qs)}\uff09"
                    f"\u7684 cells \u6765\u81ea MC \u6837\u672c\u4f30\u8ba1**\uff0c"
                    f"\u5404\u884c P10/P90 \u6765\u81ea\u4e0d\u540c\u6837\u672c\uff0c\u4e0d\u80fd\u76f4\u63a5\u6c42\u548c\u3002"
                    f"\u4f60\u586b\u5199\u7684 bucket \u5df2\u5360 **{user_cells_sum} \u683c**\uff0c"
                    f"\u5269\u4f59 **{remaining_budget} \u683c**\u7531\u8fd9\u4e9b bucket \u5171\u4eab\u3002"
                    "\u586b\u5199\u66f4\u591a bucket \u683c\u6570\u53ef\u63d0\u9ad8\u7cbe\u5ea6\u3002"
                )
            st.caption("".join(caption_parts))

            posterior_rows: list[dict] = []
            red_stats = None
            for q in (1, 3, 4, 5, 6):
                stats = _bucket_posteriors[q]
                if q == 6:
                    red_stats = stats
                obs_bucket = session.buckets.get(q)
                user_cells = obs_bucket.total_cells if obs_bucket and obs_bucket.total_cells is not None else None
                user_count = obs_bucket.count if obs_bucket and obs_bucket.count is not None else None
                label = QUALITY_LABELS[q]
                if user_cells is None:
                    label += " \u2248"
                posterior_rows.append({
                    "\u54c1\u8d28": label,
                    "n": stats.n,
                    "cells P10": user_cells if user_cells is not None else stats.cells_p10,
                    "cells P50": user_cells if user_cells is not None else stats.cells_p50,
                    "cells P90": user_cells if user_cells is not None else stats.cells_p90,
                    "\u4ef6\u6570 P50": user_count if user_count is not None else stats.count_p50,
                    "\u4ef7\u503c P50": f"{stats.value_p50:,}",
                    "\u4ef7\u503c P90": f"{stats.value_p90:,}",
                    "\u5de8\u7269 P50": stats.huge_p50,
                    "\u7a7a bucket %": f"{stats.p_empty*100:.1f}%",
                })
            if red_stats is None:
                red_stats = _bucket_posteriors[6]
            with st.container():
                if red_stats.cells_p50 > 0 or red_stats.value_p50 > 0:
                    st.info(
                        f"\U0001F534 **\u7ea2\u54c1\u540e\u9a8c\u91cd\u70b9**\uff1a\u6a21\u578b\u8ba4\u4e3a\u7ea2\u54c1 \u7ea6 "
                        f"**{red_stats.cells_p50} cells** (P10={red_stats.cells_p10}, P90={red_stats.cells_p90})\u3001"
                        f"\u4ef7\u503c\u4e2d\u4f4d **{red_stats.value_p50:,} silver**\uff08P90={red_stats.value_p90:,}\uff09\u3002"
                        f"\u7a7a bucket \u6982\u7387 {red_stats.p_empty*100:.1f}%\u3002"
                        f"\u82e5\u4f60\u786e\u8ba4\u8fd9\u4ed3\u65e0\u7ea2\u54c1\uff0c\u8bf7\u5728\u300c\u7ea2\u54c1\u300d\u533a\u5757\u52fe\u9009\u300c\u2705 \u5df2\u786e\u8ba4\u65e0\u7ea2\u54c1\u300d\u540e\u91cd\u8dd1\u3002"
                    )
                else:
                    st.success(
                        f"\u2705 \u8fc7\u6ee4\u540e\u6a21\u578b\u8ba4\u4e3a\u672c\u4ed3\u51e0\u4e4e\u4e0d\u542b\u7ea2\u54c1"
                        f"\uff08cells P50={red_stats.cells_p50}\u3001\u7a7a bucket={red_stats.p_empty*100:.1f}%\uff09\u3002"
                    )
                _render_records_table(posterior_rows)

        # ---- Snipe / Pass recommendations (bottom) ----
        st.divider()
        st.markdown("### \U0001F3AF \u79d2\u4ed3 / \u653e\u4ed3 \u63a8\u8350")
        if not _ENABLE_SNIPE_PASS_HINTS:
            st.warning(
                "\U0001F9EA **\u5b9e\u9a8c\u6027\u529f\u80fd\uff0c\u6682\u672a\u63a5\u5165\u63a8\u65ad\u63a5\u53e3**\u3002"
                "\u540e\u7aef\u903b\u8f91\u5df2\u5b9e\u73b0\u4e8e inference/snipe.py\uff08\u5355\u6d4b\u4ecd\u53ef\u8dd1\uff09\uff0c"
                "\u4f46 UI \u6682\u505c\u5c55\u793a\uff1a\u5b9e\u6218\u4e2d\u5bb9\u6613\u8bef\u89e6\u53d1 / \u540c\u8f93\u5165\u7ed3\u679c\u4e0d\u7a33\u5b9a\u3002"
                "\u5f53\u524d\u8bf7\u4ee5\u300c\u4ed3\u5e93\u4ef7\u503c\u53ef\u80fd\u533a\u95f4\u300d\u4e0e bucket \u540e\u9a8c\u4e3a\u51b3\u7b56\u4e3b\u4f53\u3002"
                "\u5f00\u53d1\u8005\u53ef\u5728 streamlit_app.py \u5c06 _ENABLE_SNIPE_PASS_HINTS \u6539\u4e3a True \u6062\u590d\u5361\u7247\u3002"
            )
        else:
            col_snipe, col_pass = st.columns(2)
            with col_snipe:
                st.markdown("### \U0001F680 \u79d2\u4ed3\u63a8\u8350")
                if snipe is None:
                    st.warning(
                        "\u672a\u89e6\u53d1\u3002\u9700\u8981\u540c\u65f6\u6ee1\u8db3\uff1a"
                        "\u4ed3\u5e93 \u2265 120 \u683c\u3001\u4f4e\u54c1\u683c\u6570\u5df2\u7ed9\u51fa\u3001"
                        "MC \u5339\u914d\u6837\u672c\u8db3\u591f\uff08\u9ed8\u8ba4 \u2265 30\uff09\u3002"
                    )
                else:
                    cond_label = (
                        "\u542b\u7d2b\u54c1\u683c\u6570\u6761\u4ef6"
                        if snipe.purple_conditioned else "\u4ec5\u4ed3\u5e93\u683c\u6570\u6761\u4ef6"
                    )
                    if snipe.low_confidence:
                        st.warning(
                            f"\u26A0\ufe0f \u4f4e\u7f6e\u4fe1\u533a\u63a8\u8350\uff1a\u6837\u672c\u4ec5 "
                            f"{snipe.n_matching_samples} \u4e2a\uff0c\u4ec5\u4f9b\u53c2\u8003\u3002"
                            f"\u53ef\u63d0\u9ad8 MC \u91c7\u6837\u6570\u83b7\u5f97\u66f4\u7a33\u5b9a\u7ed3\u679c\u3002"
                        )
                    else:
                        st.success(snipe.as_ui_tooltip())
                    st.metric(
                        "\u63a8\u8350\u79d2\u4ed3\u9876\u4ef7\uff08silver\uff09",
                        f"{snipe.snipe_max_bid:,}",
                        delta=f"\u9884\u671f\u4ef7\u503c\u4e2d\u4f4d\u6570 P50 = {snipe.expected_value:,}",
                    )
                    st.caption(
                        f"\u4fdd\u5e95\u4ef7\uff08safe_floor\uff09= {snipe.safe_floor_bid:,}  \u00b7  "
                        f"\u504f\u4e50\u89c2 P75 = {snipe.p75_value:,}  \u00b7  "
                        f"\u4e50\u89c2\u4e0a\u9650 P90 = {snipe.p90_value:,}  \u00b7  "
                        f"\u5339\u914d\u6837\u672c = {snipe.n_matching_samples} \u00b7 {cond_label}"
                    )
                    with st.expander("\u8be6\u7ec6\u8bf4\u660e"):
                        st.write(snipe.rationale)
            with col_pass:
                st.markdown("### \U0001F6D1 \u653e\u4ed3\u63a8\u8350")
                if pass_rec is None:
                    st.warning(
                        "\u672a\u89e6\u53d1\u3002\u9700\u8981\u540c\u65f6\u6ee1\u8db3\uff1a"
                        "\u4ed3\u5e93 \u2264 80 \u683c\u3001\u4f4e\u54c1\u683c\u6570\u5360\u6bd4 \u2265 40%\u3001"
                        "MC \u5339\u914d\u6837\u672c\u8db3\u591f\uff08\u9ed8\u8ba4 \u2265 30\uff09\u3002"
                    )
                else:
                    cond_label = (
                        "\u542b\u7d2b\u54c1\u683c\u6570\u6761\u4ef6"
                        if pass_rec.purple_conditioned else "\u4ec5\u4ed3\u5e93\u683c\u6570\u6761\u4ef6"
                    )
                    if pass_rec.low_confidence:
                        st.warning(
                            f"\u26A0\ufe0f \u4f4e\u7f6e\u4fe1\u533a\u63a8\u8350\uff1a\u6837\u672c\u4ec5 "
                            f"{pass_rec.n_matching_samples} \u4e2a\uff0c\u4ec5\u4f9b\u53c2\u8003\u3002"
                        )
                    else:
                        st.error(pass_rec.as_ui_tooltip())
                    st.metric(
                        "\u653e\u4ed3\u9608\u503c\uff08silver\uff09",
                        f"{pass_rec.pass_max_bid:,}",
                        delta=f"\u4ec5\u662f\u5168\u56fe\u5747\u503c\u7684 "
                              f"{pass_rec.value_ratio:.0%}",
                        delta_color="inverse",
                    )
                    st.caption(
                        f"\u8fdb\u4ed3\u4ef7\uff08safe_entry\uff09= {pass_rec.safe_entry_bid:,}  \u00b7  "
                        f"\u5168\u56fe\u4e2d\u4f4d\u4f30\u503c P50 = {pass_rec.unconditional_p50:,}  \u00b7  "
                        f"\u5339\u914d\u6837\u672c = {pass_rec.n_matching_samples} \u00b7 {cond_label}"
                    )
                    with st.expander("\u8be6\u7ec6\u8bf4\u660e"):
                        st.write(pass_rec.rationale)
    elif _cached_bundle is None and not _mc_ui_running():
        st.info(
            "\u8bbe\u7f6e\u597d\u8bfb\u6570\u540e\u70b9\u51fb\u300c\u8fd0\u884c\u51fa\u4ef7 hint\u300d\uff0c"
            "\u6216\u5f00\u542f\u300cOCR \u540e\u540e\u53f0\u81ea\u52a8\u63a8\u65ad\u300d\u3002"
        )


if _main_tab == "hint":
    with st.container():
        # #region agent log
        agent_debug_log(
            location="streamlit_app.py:hint_tab",
            message="render_hint_tab",
            data={"main_tab": "hint"},
            hypothesis_id="H-ghost",
            run_id="post-fix",
        )
        # #endregion
        _render_hint_tab_impl()

# ===== Tab: 联合筛选 =====
if _main_tab == "joint":
    with _tab_pane.container():
        from experimental_tabs import render_joint_inference_tab

        render_joint_inference_tab(
            session_builder=lambda: _build_session_for_inference(state, maps),
            state=state,
            per_bucket_top=per_bucket_top,
        )


# ===== Tab 4: 道具 ROI =====
@st.cache_data(max_entries=24, show_spinner=False)
def _cached_tool_roi(map_id: int, *, tools: tuple, hero: str,
                     n_trials: int, seed: int, per_bucket_top: int,
                     include_aisha_outline: bool = False,
                     player_warehouse_noise_std: float = 10.0):
    """Cache ROI by (map_id, tools, hero, n_trials, seed, outline, noise).

    LOO ROI is purely offline: it doesn't read user readings, only the
    map + hero + tool kit + player-eyeball-noise model. So caching is
    safe and dramatic — a 30s run becomes instant on repeat clicks.
    """
    maps_, drops_, items_ = _load_tables()
    return compute_tool_roi(
        map_id, tool_kit=list(tools),
        maps=maps_, drops=drops_, items=items_,
        hero=hero, n_trials=n_trials, per_bucket_top=per_bucket_top,
        rng=np.random.default_rng(seed),
        include_aisha_outline=include_aisha_outline,
        player_warehouse_noise_std=player_warehouse_noise_std,
    )


if _main_tab == "roi":
    roi_hero = state["hero"]
    roi_hero_label = "\u4f0a\u68ee Ethan" if roi_hero == "ethan" else "\u827e\u838e Aisha"
    default_kit = ETHAN_KIT if roi_hero == "ethan" else AISHA_KIT
    st.subheader(
        f"\u9053\u5177\u6027\u4ef7\u6bd4 ROI \u2014 {roi_hero_label} \u6807\u914d "
        f"\u00b7 \u5730\u56fe {state.get('map_id') or '（未选）'}"
    )
    if roi_hero == "aisha":
        st.caption(
            "\u827e\u838e\u6a21\u5f0f\uff1aR1-R4 \u8f6e\u5ed3\u4f1a\u514d\u8d39\u63d0\u4f9b\u767d/\u7eff/\u84dd/\u7d2b \u7684 cells \u4e0e\u4ef6\u6570\uff0c"
            "\u4e0d\u8ba1\u9053\u5177\u8d39\u3002\u9009\u51fa\u6765\u7684\u9053\u5177\u662f\u7d2b/\u91d1\u7ea7\u4e0a\u7684\u989d\u5916\u8d2d\u4e70\u3002"
        )
    st.markdown(
        "**\u8fd9\u4e2a tab \u662f\u5e72\u4ec0\u4e48\u7684\uff1f** \u56de\u7b54\uff1a\n"
        "*\u300c\u5728\u8fd9\u5f20\u5730\u56fe\u4e0a\uff0c\u54ea\u4e2a\u9053\u5177\u5e26\u6765\u7684\u4ef7\u503c\u63a8\u65ad\u63d0\u5347\u6700\u7269\u6709\u6240\u503c\uff1f\u300d*\n\n"
        "**\u8ba1\u7b97\u65b9\u6cd5\uff08leave-one-out\uff09**\uff1a\u91c7\u6837 N \u6b21\u5730\u56fe\u53ef\u80fd\u7684\u771f\u5b9e\u4ed3\u5e93\uff0c"
        "\u6bcf\u6b21\u5206\u522b\u8dd1\u4e24\u6b21\u63a8\u65ad\uff1a"
        "\u4e00\u6b21 **\u5e26\u4e0a\u8be5\u9053\u5177** \u3001\u4e00\u6b21 **\u62cd\u6389\u8be5\u9053\u5177** \u3002"
        "\u4e24\u6b21\u4ef7\u503c\u8bef\u5dee\u7684\u5dee\u8ddd\u9664\u4ee5\u9053\u5177\u552e\u4ef7\uff0c\u5c31\u662f\u8fd9\u4e2a\u9053\u5177\u5728\u8fd9\u5f20\u5730\u56fe\u7684\u300c\u6bcf\u94f6\u5e01\u4ef7\u503c\u632b\u4f4e\u91cf\u300d\u3002"
    )
    st.info(
        "\U0001F4A1 ROI \u672c\u8eab\u4e0e\u4f60\u5728\u300c\u8bfb\u6570\u8f93\u5165\u300d\u91cc\u586b\u7684\u503c "
        "**\u5b8c\u5168\u65e0\u5173** \u2014 \u8fd9\u662f\u4e00\u4e2a\u79bb\u7ebf\u6307\u6807\uff0c"
        "\u53ea\u4f9d\u8d56\u5730\u56fe\u3001\u82f1\u96c4\u3001\u9053\u5177\u7ec4\u5408\u3001\u4ee5\u53ca\u4ed3\u5e93\u773c\u4f30\u8bef\u5dee\u3002"
        "\u5207\u6362\u5730\u56fe \u624d\u4f1a\u91cd\u65b0\u8ba1\u7b97\uff1b\u540c\u4e00\u5730\u56fe\u91cd\u590d\u70b9\u51fb\u662f\u77ac\u53d1\uff08\u5df2\u7f13\u5b58\uff09\u3002\n\n"
        "\u26A0\ufe0f **\u300c\u603b\u4ed3\u50a8\u300d\u9053\u5177\u7684 ROI \u4f9d\u8d56\u4e0b\u9762\u300c\u4ed3\u5e93\u773c\u4f30\u8bef\u5dee \u03c3\u300d\u6ed1\u5757**\uff1a"
        "\u8bbe\u4e3a 0 \u5219\u73a9\u5bb6\u88ab\u5047\u5b9a\u80fd\u7cbe\u51c6\u773c\u4f30\u603b\u683c\u6570 \u2192 \u603b\u4ed3\u50a8 ROI \u4f1a\u63a5\u8fd1 0\uff1b"
        "\u8bbe\u4e3a 10\uff08\u9ed8\u8ba4\uff09\u5219\u6a21\u62df\u73b0\u5b9e\u73a9\u5bb6\u773c\u4f30 \u00b110 \u683c\u7684\u4e0d\u786e\u5b9a\u6027 \u2192 \u603b\u4ed3\u50a8\u4f1a\u8868\u73b0\u51fa\u771f\u5b9e ROI\u3002"
    )

    # ---- Tool selection ----
    selected_tools = st.multiselect(
        "\u8981\u8bc4\u4f30\u7684\u9053\u5177\u7ec4\u5408",
        options=list(ALL_TOOLS),
        default=list(default_kit),
        format_func=lambda t: f"{t} \uff08\u9ed8\u8ba4 {TOOL_DEFAULT_PRICE[t]:,} silver\uff09",
        help="leave-one-out \u8bc4\u4f30\u3002\u9009\u4e0a\u7684\u9053\u5177\u4f1a\u53d8\u6210 kit\uff0c\u9010\u4e2a\u62cd\u6389\u770b\u5b83\u8d21\u732e\u591a\u5c11\u3002"
             + ("\u00a0\u00a0\u827e\u838e \u9ed8\u8ba4\u4e3a \u7d2b\u4f30\u4ef7 + \u603b\u4ed3\u50a8\uff1bR1-R4 \u8f6e\u5ed3\u4ee5\u514d\u8d39\u63a8\u65ad\u52a0\u6599\u3002"
                if roi_hero == "aisha" else ""),
        key=f"roi_kit_{roi_hero}",
    )
    include_aisha_outline = False
    if roi_hero == "aisha":
        include_aisha_outline = st.checkbox(
            "\u52a0\u6599 \u827e\u838e R1-R4 \u8f6e\u5ed3\u4fe1\u606f\uff080 silver\uff09",
            value=True,
            help="Aisha \u9760\u8f6e\u5ed3\u514d\u8d39\u770b\u5230 q=1\u22124 \u7684 cells + count\u3002"
                 "\u53d6\u6d88\u540e ROI \u4f1a\u53d8\u5dee \u2014 \u4ec5\u7528\u4e8e debug\uff0c\u5b9e\u6218\u7559\u52fe\u9009\u3002",
        )

    # ---- Price overrides (blue+ only) ----
    overridable_in_selection = [t for t in selected_tools if t in TOOL_PRICE_OVERRIDABLE]
    price_overrides: dict[str, int] = {}
    if overridable_in_selection:
        with st.expander(
            f"\U0001F4B0 \u4fee\u6b63\u9053\u5177\u552e\u4ef7\uff08\u84dd\u54c1\u53ca\u4ee5\u4e0a\u9053\u5177\u4ef7\u683c\u4f1a\u52a8\u6001\u53d8\u5316\uff09",
            expanded=False,
        ):
            st.caption(
                "\u84dd\u54c1\u53ca\u4ee5\u4e0a\u9053\u5177\u4ef7\u683c\u4f1a\u52a8\u6001\u53d8\u5316\u3002\u586b\u5165\u4f60\u5728\u6e38\u620f\u91cc"
                "\u770b\u5230\u7684\u4ef7\u683c\u540e ROI \u4f1a\u91cd\u65b0\u8ba1\u7b97\uff08\u53ea\u5728\u663e\u793a\u5c42\u9664\u4ef7\uff0c"
                "\u4e0d\u91cd\u8dd1 MC\uff09\u3002\u7559\u7a7a / 0 = \u4f7f\u7528\u9ed8\u8ba4\u4ef7\u3002"
            )
            cols = st.columns(min(3, len(overridable_in_selection)))
            for i, tool in enumerate(overridable_in_selection):
                with cols[i % len(cols)]:
                    v = st.number_input(
                        f"{tool}",
                        min_value=0, max_value=200_000,
                        value=TOOL_DEFAULT_PRICE[tool],
                        step=500,
                        key=f"price_override_{tool}",
                    )
                    if v > 0 and v != TOOL_DEFAULT_PRICE[tool]:
                        price_overrides[tool] = int(v)

    col_l, col_m, col_r = st.columns([1, 1, 1])
    roi_trials = col_l.slider(
        "ROI \u91c7\u6837\u8f6e\u6570", 30, 200, 60, step=10, key="roi_trials",
        help="60 \u8f6e\u5728\u672c\u673a \u2248 20-40s\uff0c\u8db3\u591f\u770b\u51fa\u8d8b\u52bf\u3002",
    )
    warehouse_noise_std = col_m.slider(
        "\u4ed3\u5e93\u683c\u6570\u773c\u4f30\u8bef\u5dee \u03c3 (cells)",
        min_value=0, max_value=20, value=10, step=1,
        key="roi_warehouse_noise",
        help="\u73a9\u5bb6\u4e0d\u5e26\u300c\u603b\u4ed3\u50a8\u300d\u9053\u5177\u65f6\uff0c"
             "\u9760\u773c\u4f30\u603b\u683c\u6570\u7684\u9ad8\u65af\u566a\u58f0\u6807\u51c6\u5dee\u3002"
             "\u8bbe\u4e3a 0 = \u5047\u8bbe\u73a9\u5bb6\u80fd\u7cbe\u51c6\u773c\u4f30\uff08\u6b64\u65f6\u603b\u4ed3\u50a8 ROI \u4f1a\u63a5\u8fd1 0\uff09\u3002"
             "10 \u662f\u4fdd\u5b88\u4f30\u8ba1\uff1b\u5b9e\u6218\u4e2d\u65b0\u624b\u53ef\u80fd\u66f4\u9ad8\u3002",
    )
    sort_by = col_r.selectbox(
        "\u6392\u5e8f\u65b9\u5f0f",
        options=["roi_value", "info_gain_value_mean", "silver_cost"],
        format_func={
            "roi_value": "ROI \u9ad8\u2192\u4f4e\uff08\u6027\u4ef7\u6bd4\u4f18\u5148\uff09",
            "info_gain_value_mean": "\u4ef7\u503c\u8d21\u732e \u9ad8\u2192\u4f4e",
            "silver_cost": "\u9053\u5177\u552e\u4ef7 \u4f4e\u2192\u9ad8",
        }.__getitem__,
    )

    run_disabled = len(selected_tools) < 2
    if run_disabled:
        st.warning(
            "\u8bf7\u81f3\u5c11\u9009\u62e9 2 \u4e2a\u9053\u5177\u624d\u80fd\u8dd1 LOO ROI\uff08\u62cd\u6389\u4e00\u4e2a\u540e\u53e6\u4e00\u4e2a\u4ecd\u9700\u53c2\u4e0e\u63a8\u65ad\uff09\u3002"
        )

    if st.button("\u8fd0\u884c ROI \u8ba1\u7b97", key="run_roi", type="primary",
                 disabled=run_disabled or state.get("map_id") is None):
        if state.get("map_id") is None:
            st.error("\u8bf7\u5148\u9009\u62e9\u5730\u56fe\u3002")
        with st.spinner(
            f"\u8ba1\u7b97\u4e2d\uff08leave-one-out, {roi_trials} trials\uff09..."
        ):
            rois_raw = _cached_tool_roi(
                state.get("map_id"), tools=tuple(selected_tools),
                hero=roi_hero, n_trials=roi_trials, seed=seed,
                per_bucket_top=per_bucket_top,
                include_aisha_outline=include_aisha_outline,
                player_warehouse_noise_std=float(warehouse_noise_std),
            )

        # Apply user's price overrides at display time. info_gain is a
        # property of (map, hero, kit, MC samples) so it's unchanged by
        # price; only the ROI ratio re-divides. This keeps the cache
        # warm even if the user tweaks prices.
        from dataclasses import replace as _replace
        def _override(r):
            cost = price_overrides.get(r.tool_name, r.silver_cost)
            if cost <= 0:
                cost = r.silver_cost
            new_roi = r.info_gain_value_mean / cost if cost > 0 else 0.0
            return _replace(r, silver_cost=cost, roi_value=new_roi)
        rois = [_override(r) for r in rois_raw]

        if sort_by == "silver_cost":
            sorted_rois = sorted(rois, key=lambda r: r.silver_cost)
        elif sort_by == "info_gain_value_mean":
            sorted_rois = sorted(rois, key=lambda r: -r.info_gain_value_mean)
        else:
            sorted_rois = sorted(rois, key=lambda r: -r.roi_value)

        # ---- Bar chart (English labels to avoid CJK font fallback) ----
        st.markdown(
            "##### \U0001F4CA \u6bcf\u94f6\u5e01\u4ef7\u503c\u632b\u4f4e\u91cf "
            "(ROI = mean info-gain / silver price)"
        )
        fig, ax = plt.subplots(figsize=(7.5, max(2.8, 0.38 * len(sorted_rois))))
        chart_rois = list(reversed(sorted_rois))
        names_en = [TOOL_EN_LABEL.get(r.tool_name, r.tool_name) for r in chart_rois]
        roi_vals = [r.roi_value for r in chart_rois]
        colors = ["#2563eb" if v > 0 else "#dc2626" for v in roi_vals]
        bars = ax.barh(
            names_en, roi_vals, color=colors, height=0.62,
            edgecolor="white", linewidth=0.5,
        )
        for bar, val in zip(bars, roi_vals):
            ax.text(
                val, bar.get_y() + bar.get_height() / 2,
                f" {val:+.3f}", va="center",
                fontsize=8, color="#333",
            )
        style_roi_barh(ax)
        ax.set_xlabel("ROI (silver-value recovered per silver spent)",
                       fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_title(
            "Each tool's marginal value-error reduction per silver spent",
            fontsize=9,
        )
        plt.tight_layout()
        st.pyplot(
            fig,
            clear_figure=True,
            **layout_width_kwargs("pyplot", stretch=False),
        )
        st.caption(
            "\u6b63\u503c = \u8be5\u9053\u5177\u8d21\u732e\u4e3a\u6b63\uff08\u4ef7\u503c\u63a8\u65ad\u66f4\u51c6\uff09\uff1b"
            "\u8d1f\u503c = \u5728\u73b0\u6709 kit \u4e2d\u88ab\u5176\u5b83\u9053\u5177\u8986\u76d6\uff0c"
            "\u300c\u6709\u4ed6\u4e0d\u591a\u3001\u6ca1\u4ed6\u4e0d\u5c11\u300d\u7684\u51b1\u4f59\u9009\u9879\u3002\u9053\u5177\u540d\u5728\u4e0b\u9762\u4e2d\u6587\u8868\u91cc\u3002"
        )

        # ---- Detail table (Chinese-friendly via st.table) ----
        st.markdown("##### \U0001F4CB \u8be6\u7ec6\u8868")
        rows = []
        for r in sorted_rois:
            overridden = r.tool_name in price_overrides
            price_str = f"{r.silver_cost:,}" + (" \u26A0\uFE0F" if overridden else "")
            rows.append({
                "\u9053\u5177": f"{r.tool_name} ({TOOL_EN_LABEL.get(r.tool_name, '')})",
                "\u552e\u4ef7 (silver)": price_str,
                "\u4ef7\u503c\u8d21\u732e mean \u00b1 std":
                    f"{r.info_gain_value_mean:,.0f} \u00b1 {r.info_gain_value_std:,.0f}",
                "cells \u8d21\u732e": f"{r.info_gain_cells_mean:,.2f}",
                "ROI (\u4ef7\u503c/silver)": f"{r.roi_value:+.3f}",
            })
        st.table(rows)
        if price_overrides:
            st.caption(
                "\u26A0\uFE0F = \u8be5\u9053\u5177\u4f7f\u7528\u4e86\u4f60\u8f93\u5165\u7684\u81ea\u5b9a\u4e49\u552e\u4ef7\u3002"
            )
    else:
        st.info(
            "\u70b9\u51fb\u4e0a\u9762\u6309\u94ae\u8fd0\u884c\u3002"
            "\u9996\u6b21 60 trials \u7ea6 20-40 \u79d2\uff1b\u4e4b\u540e\u540c\u5730\u56fe\u91cd\u590d\u70b9\u51fb\u662f\u77ac\u53d1\u3002"
        )

@st.fragment(run_every=2)
def _bg_infer_autopoll_fragment() -> None:
    """While MC runs, poll thread and rerun when done (no user click needed)."""
    _poll_bg_hint_and_maybe_rerun()


if (
    st.session_state.get("_bg_infer_box") is not None
    and st.session_state.get("_main_tab") == "hint"
):
    _bg_infer_autopoll_fragment()
