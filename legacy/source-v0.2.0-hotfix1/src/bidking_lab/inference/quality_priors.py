"""Per-cell-value priors by quality, used to invert ``X品估价`` readings.

When the player uses a ``X品估价`` (X-quality total value) tool, the game
reveals ``Σ value`` for that quality bucket. Combined with ``X品均格``
(average cells per item) the inference engine wants to recover
``(total_cells, count)`` for the bucket. The bridge between **value sum**
and **total cells** is a per-cell value prior:

    estimated_total_cells ≈ value_sum / per_cell_value(quality)

These numbers are calibrated from drop-weighted medians on the high-tier
mansion / shipwreck pools (see ``scripts/probe_value_per_cell.py``):

* Purple (quality=4): drop-weighted p50 ≈ 2560, user heuristic 2500
* Gold   (quality=5): drop-weighted p50 ≈ 9800, user heuristic 9400
* Red    (quality=6): bimodal — small reds (1-2 cells, "rare-and-small")
                      have very high per-cell value (86k+) but are
                      indistinguishable long tail; medium reds (3-6 cells)
                      have p50 ≈ 45k; huge reds (≥7 cells, including 4×4
                      "巨物") have p50 ≈ 30k.

The huge-item split matters because the user can directly count "巨物
数" in the cabinet UI (4×4 屏风/车壳 etc.). Subtracting their cells from
the per-quality bucket before applying the per-cell-value prior reduces
inference error by a large margin (red items differ ~50% per cell
between small and huge bands).

The defaults below are the values the user picked during the 2026-05-15
design session; they are deliberately rounded to easy-to-remember numbers
for hand calculation.
"""

from __future__ import annotations

from typing import Literal

Quality = Literal[1, 2, 3, 4, 5, 6]
"""Item quality codes (1=白, 2=绿, 3=蓝, 4=紫, 5=金, 6=红)."""

# Per-cell value defaults (silver / cell). These are *medians* under the
# drop-weighted prior on a typical high-tier map. Use when no value-sum
# tool reading is available.
PER_CELL_VALUE_DEFAULT: dict[int, int] = {
    1: 130,     # 白: low and tight; rarely worth the inference effort
    2: 300,     # 绿
    3: 1100,    # 蓝
    4: 2500,    # 紫: user heuristic (≈ drop-weighted p50 of 2560)
    5: 9400,    # 金: user heuristic (≈ drop-weighted p50 of 9800)
    6: 50000,   # 红 default (non-huge bucket, drop-p50 in 3-6 cells)
}

# Huge-item subset for the rare quality buckets. Per-cell value differs
# from the default because huge items have different price/cell modes.
# Definition: "巨物 = 占 >= 12 格 的藏品"
#
# Purple (4): 可折叠高韧性防护盾 20082/12=1674/cell (only one purple huge).
#             Kept at 2500 (default) since it's the sole item.
# Gold (5):   3×4: 防弹衣 74745, 波斯毯 85800, 分析仪 86130, 无人作战车 93753
#             3×5: 服务器机柜 97382; 4×4: 锂电池 199900; 3×6: 游艇 106500
#             Drop-weighted median ~7000/cell across all gold huge.
# Red (6):    3×4: 单兵外骨骼 305920, 巡航摩托 357040, 相控阵雷达 1003000
#             3×5: 轿跑 293k, 金枪鱼 294k, GPU柜 375k, 飞行器 453k, 蓝鳍 1553k
#             4×4: 红木屏风 361k, 碳纤维车壳 444k, 翡翠屏风 844k
#             Drop-weighted median ~30k/cell.
PER_CELL_VALUE_HUGE: dict[int, int] = {
    4: 2500,    # 紫 巨物 (仅一件：防护盾; 沿用 default)
    5: 7000,    # 金 巨物 (12-18格全范围，加权中位 ~7000/cell)
    6: 30000,   # 红 巨物 (12-16格全范围，加权中位 ~30000/cell)
}

# Lower / upper safety bounds. The inference engine uses these to cap
# pathological inputs (e.g., player typoed a "100000" purple value sum).
PER_CELL_VALUE_BOUNDS: dict[int, tuple[int, int]] = {
    1: (60, 250),
    2: (150, 600),
    3: (500, 1800),
    4: (1500, 5000),
    5: (5000, 25000),
    6: (15000, 100000),
}

# User-input convention for the "red value range" field:
# Users always provide a range [low, high] for red, even when no huge
# items are flagged, because the variance is too high. These defaults
# are sane fallbacks if the user leaves the field blank.
DEFAULT_RED_VALUE_RANGE_PER_CELL: tuple[int, int] = (15_000, 100_000)


def per_cell_value(
    quality: int,
    *,
    huge: bool = False,
) -> int:
    """Return the per-cell value prior for ``quality``.

    Parameters
    ----------
    quality
        Item quality (1=白 … 6=红).
    huge
        Whether the cells being valued belong to "huge" items (area ≥ 7
        cells, identified by the player via cabinet outline). Only
        meaningful for gold and red.
    """
    if huge and quality in PER_CELL_VALUE_HUGE:
        return PER_CELL_VALUE_HUGE[quality]
    if quality not in PER_CELL_VALUE_DEFAULT:
        raise ValueError(f"unknown quality {quality}; expected 1-6")
    return PER_CELL_VALUE_DEFAULT[quality]


def estimate_total_cells(
    quality: int,
    value_sum: int,
    *,
    huge_cells: int = 0,
    huge_value: int = 0,
) -> int:
    """Estimate ``total_cells`` for a quality bucket given ``value_sum``.

    Parameters
    ----------
    quality
        Item quality (1-6).
    value_sum
        ``Σ value`` for this quality bucket, e.g., from ``优品估价``.
    huge_cells
        Cells already attributed to huge items in this bucket (player
        counted them directly via outline).
    huge_value
        ``Σ value`` of the huge items the player has identified. If 0
        but ``huge_cells > 0``, we estimate it via the huge-band prior.

    Returns
    -------
    int
        Estimated total cells for the bucket, including the huge cells.

    Notes
    -----
    Algorithm:

    * If ``huge_cells > 0``: subtract them (and their estimated value)
      from the bucket, then estimate the non-huge cells using the
      default per-cell prior, then add ``huge_cells`` back.
    * Else: divide the bucket value by the default per-cell prior.

    The estimate is rounded to the nearest non-negative integer.
    """
    if value_sum < 0:
        raise ValueError(f"value_sum must be non-negative, got {value_sum}")
    if huge_cells < 0:
        raise ValueError(f"huge_cells must be non-negative, got {huge_cells}")
    if huge_value == 0 and huge_cells > 0 and quality in PER_CELL_VALUE_HUGE:
        huge_value = PER_CELL_VALUE_HUGE[quality] * huge_cells
    remaining_value = max(0, value_sum - huge_value)
    default_per_cell = PER_CELL_VALUE_DEFAULT[quality]
    non_huge_cells = round(remaining_value / default_per_cell)
    return non_huge_cells + huge_cells


def value_consistency_score(
    quality: int,
    candidate_total_cells: int,
    *,
    value_sum: int | None = None,
    value_range: tuple[int, int] | None = None,
    huge_cells: int = 0,
) -> float:
    """Score how well ``candidate_total_cells`` agrees with a value reading.

    Lower is better; 0 means perfect agreement at the prior.

    * If ``value_sum`` is given: ``|candidate_total_cells -
      estimate_from_value| / candidate_total_cells``.
    * If ``value_range`` is given: 0 if candidate value (using
      default per-cell) lies inside the range, else relative distance
      to the nearer bound.
    * If neither: 0 (no constraint, all candidates tie).

    Used by the inference engine to rank candidates returned by the
    cells-based enumeration.
    """
    if candidate_total_cells <= 0:
        return float("inf")
    per_cell = PER_CELL_VALUE_DEFAULT[quality]
    if value_sum is not None:
        expected = estimate_total_cells(
            quality,
            value_sum,
            huge_cells=huge_cells,
        )
        return abs(candidate_total_cells - expected) / max(1, candidate_total_cells)
    if value_range is not None:
        # Candidate cells → candidate value via per-cell prior
        candidate_value = candidate_total_cells * per_cell
        lo, hi = value_range
        if lo <= candidate_value <= hi:
            return 0.0
        if candidate_value < lo:
            return (lo - candidate_value) / lo
        return (candidate_value - hi) / hi
    return 0.0


__all__ = (
    "Quality",
    "PER_CELL_VALUE_DEFAULT",
    "PER_CELL_VALUE_HUGE",
    "PER_CELL_VALUE_BOUNDS",
    "DEFAULT_RED_VALUE_RANGE_PER_CELL",
    "per_cell_value",
    "estimate_total_cells",
    "value_consistency_score",
)
