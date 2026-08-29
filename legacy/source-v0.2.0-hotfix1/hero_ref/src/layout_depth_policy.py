"""Warehouse layout / footroom depth policy shared across heroes.

Aisha uses one unified minimap depth + footroom path for all rounds (R1–R5).
Sparse lottery heroes (Raven, Sophie, Gabriela) keep early-band-only hints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

WAREHOUSE_ROWS = 18
GRID_COLUMNS = 10
WAREHOUSE_CELL_CAPACITY = WAREHOUSE_ROWS * GRID_COLUMNS
DEEPEST_ROW_THRESHOLD = 12
FOOTROOM_ACTIVATION_MIN_RAW_ROW = 7
# Raw minimap bottom at row 10+ with sparse known cells implies a deep warehouse band.
DEEP_VISIBILITY_MIN_RAW_ROW = 10
AISHA_DEEP_VISIBILITY_GRID_BAND_NOTE = "aisha_deep_visibility_grid_band"
AISHA_DEEP_VISIBILITY_MULTI_ANCHOR_NOTE = "aisha_deep_visibility_multi_anchor"
MULTI_DEEPEST_ROW_WEIGHT_EXP = 2.0
AISHA_MULTI_DEEPEST_FOOTROOM_NOTE = "aisha_multi_deepest_footroom_row"
# Minimap only reaches mid warehouse (e.g. 2410 R1–R3 row 7): count×2.85 overshoots vs ~70 格体感.
SHALLOW_WAREHOUSE_MAX_RAW_ROW = 8
# Round-aware shallow count-implied floor multiplier (was a flat 2.5). Tested 2026-06-16
# across 28 shallow-settled samples: bid-time minimap signals — visible fill, observed
# item size (cells/visible-item), visible item count — do NOT predict the warehouse's true
# cells/item (corr ~+0.1; bucket means flat ~2.3). What's revealed early (top rows, smaller
# items) doesn't expose the hidden depth below, so a *density-aware* multiplier has no signal
# to act on. The floor's ROLE does change by ROUND though: R1 is near-blind (combos stuck low)
# and needs a stronger prior lift; by R2 the rest of the engine has more info, so a flat 2.5
# over-lifts (live review + A/B: shallow R2 grid mid ran ~+11 over settled truth, low > truth
# in 17/25 samples). Population mean cells/item ~2.3 → decay from a conservative R1 prior toward
# the mean by R2. Dense warehouses (e.g. 2409, true ratio 2.68) are indistinguishable from sparse
# ones at bid time, so their mild under-estimate here is an irreducible info limit, not a tuning
# miss. Only mutates total_grid_range; never count/value.
#
# R1 stays at the aggressive 2.5 ON PURPOSE: a warehouse can look shallow early (minimap only
# reaches row ~7) yet settle deep (e.g. 2410 live: visible row 7 at R1 but truth 96 cells / 10
# rows). At R1 nothing distinguishes "looks shallow, IS shallow" from "looks shallow, IS deep",
# so the R1 floor must stay high enough to keep the wide R1 interval bracketing a possibly-deep
# truth (under-estimating a deep warehouse is a real miss, not the acceptable gold/red gap). By
# R2 the engine has more evidence and a flat 2.5 over-lifts the common genuinely-shallow case, so
# R2 drops to 2.2. (Lowering R1 too dropped 2410 R1 high 96->92 and stopped bracketing truth.)
AISHA_SHALLOW_COUNT_MULT_BY_ROUND = {1: 2.5, 2: 2.2}
AISHA_SHALLOW_COUNT_MULT_DEFAULT = 2.2  # R3+ fallback (the floor rarely fires past R2)
AISHA_SHALLOW_GRID_LOW_FLOOR = 68
AISHA_SHALLOW_GRID_LOW_NOTE = "aisha_shallow_visible_grid_low_floor"
AISHA_SHALLOW_FLAT_BOTTOM_GRID_TIGHTEN_NOTE = "aisha_shallow_flat_bottom_grid_tighten"
AISHA_LONELY_DEEPEST_BAND_NOTE = "aisha_lonely_deepest_band_tightened"
LAYOUT_MIN_ROUND = 1
WHITE_ONLY_MAX_ROUND = 0

# Typical reachable cells/item when warehouse is vertically deep but minimap omits bottom ghosts.
AISHA_COUNT_IMPLIED_CELLS_PER_ITEM = 2.85
AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE = "aisha_count_implied_grid_floor"
AISHA_FLOATING_GRID_MIN_SPREAD_NOTE = "aisha_floating_grid_min_spread_applied"
# --- Geometry-first grid-range floor/cap (strategy "D": band-max + taper + R1 wide) ---
# Floor ≈ deepest_row * GRID_COLUMNS * f_low. f_low is density-aware so an isolated/thin
# deep tail does not over-lift (regression downward is allowed as evidence sharpens), while
# a dense contiguous deep warehouse lifts strongly. Cap pulls the high tail down by the
# observed deep-band taper. R1 commits to a wide conservative interval (white-only sees little).
AISHA_GEOMETRY_GRID_NOTE = "aisha_geometry_grid_floor"
AISHA_GEOMETRY_GRID_CAP_NOTE = "aisha_geometry_grid_taper_cap"
AISHA_GEOMETRY_GRID_R1_WIDE_NOTE = "aisha_geometry_grid_r1_wide"
GEO_F_LOW_BASE = 0.55
GEO_F_LOW_CONTIGUITY_BONUS = 0.20   # most rows 1..deepest occupied -> filled top-down
GEO_F_LOW_DEEP_WIDTH_BONUS = 0.15   # deep band reaches a far column -> real wide deep row
GEO_CONTIGUITY_DENSE = 0.60
GEO_DEEP_WIDE_COLS = 6
GEO_DEEP_BAND_ROWS = 3
GEO_AVG_TAPER_STRONG = 5.0          # avg (env-col) per occupied deep row -> thin tail
GEO_AVG_TAPER_MILD = 3.0
GEO_F_LOW_TAPER_STRONG_PENALTY = 0.20
GEO_F_LOW_TAPER_MILD_PENALTY = 0.10
GEO_F_LOW_MIN, GEO_F_LOW_MAX = 0.45, 0.92
GEO_F_MID_BONUS = 0.06
GEO_TAPER_DAMP = 0.70               # trust only part of apparent narrowness as real taper
GEO_MIN_SPREAD_LOW = 3
GEO_MIN_SPREAD_HIGH = 4
GEO_DEEP_FLOOR_MIN_ROW = 6          # shallower than this: too shallow to geometrically bound
GEO_LOCK_SPREAD = 2                 # collapsed baseline (engine already knows total) -> skip
GEO_R1_WIDE_LOW_FRAC = 0.78
GEO_R1_WIDE_HIGH_FRAC = 1.30
GEO_R1_WIDE_HIGH_DEEP_FRAC = 1.00

AISHA_GRID_TOTAL_HARD_NOTES = frozenset(
    {
        "structured_ref_bridge_total_cells",
        "field_update_total_cells",
        "public_total_cells",
        "action_100103_total_cells",
        "settlement_review_total_grid",
        "settlement_review_total_grid_from_layout_replay",
        "total_grid_target_from_known_high_tier_cells",
    }
)


def normalize_grid_cell_target(value: float | int | None) -> float | None:
    """Warehouse grid counts are whole cells; layout footroom math may emit float noise."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return float(max(0, int(round(parsed))))

LAYOUT_MODE_OFF = "off"
LAYOUT_MODE_BAND = "band"
LAYOUT_MODE_TARGET = "target"
LAYOUT_MODE_SHADOW = "shadow"

# Notes — aisha-prefixed notes kept for backward-compatible UI/diagnostics.
AISHA_EARLY_VIEWPORT_GRID_HINT_NOTE = "aisha_early_viewport_grid_hint"
AISHA_LAYOUT_GRID_HINT_NOTE = "aisha_layout_grid_hint_shadow"
AISHA_LAYOUT_FOOTROOM_NOTE = "aisha_layout_grid_footroom_below_deepest"
AISHA_LAYOUT_FOOTROOM_MULT_NOTE = "aisha_layout_footroom_mult"
AISHA_LAYOUT_FOOTROOM_CAP_NOTE = "aisha_layout_footroom_capped"
AISHA_LAYOUT_FOOTROOM_SKIP_NOTE = "aisha_layout_footroom_skipped_not_undershoot"
AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE = "aisha_layout_footroom_sparse_viewport"
AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE = "aisha_layout_band_widen_delta"
AISHA_LAYOUT_BAND_WIDEN_APPLIED_NOTE = "aisha_layout_band_widen_applied"
AISHA_LAYOUT_FOOTROOM_HINT_NOTE = "aisha_layout_footroom_hint"
AISHA_LAYOUT_FOOTROOM_BAND_ANCHOR_NOTE = "aisha_layout_footroom_band_anchor_applied"
AISHA_LAYOUT_DENSE_VIEWPORT_CAP_NOTE = "aisha_layout_dense_viewport_cap"
AISHA_HIGH_FILL_FOOTROOM_DAMP_NOTE = "aisha_high_fill_footroom_damp"
# Near-full viewport => the warehouse depth is essentially confirmed at the deepest revealed row
# (items densely fill the page up to there; the bottom row may be partial, e.g. 5/10). There is
# little evidence of further rows below, so damp the below-deepest footroom toward 0 as fill -> 1.
# Rollback / A-B: env AISHA_DISABLE_HIGH_FILL_FOOTROOM_DAMP=1.
AISHA_HIGH_FILL_FOOTROOM_THRESHOLD = 0.85
AISHA_LATE_REVEAL_RESIDUAL_CAP_NOTE = "aisha_grid_late_reveal_residual_cap"
# Late-reveal residual cap on the FINAL grid range. The footroom/geometry path guesses cells for
# items not yet visible (hidden deep rows / invisible gold-red). By the late rounds most items are
# revealed, so the still-unseen cells are bounded by the HIDDEN ITEM count, not by empty geometric
# rows below the deepest occupied row. Without this, a partially-filled deepest row (e.g. row 7 with
# 5/10 cells) is treated as a full ×10 row and ~+7 phantom cells get added even though the engine
# already sees ~all of them (live R4 review: 2401 R4 known 63, truth 65, but grid mid ran 72).
# Cap the range at known_cells + hidden_items*per_item*slack. Self-protects genuinely deep warehouses:
# high count estimate -> many hidden items -> large residual -> cap stays high (2410 R4 truth 96 keeps
# ~90+). Floored at known_cells (a hard lower bound — those cells are seen). Only mutates total_grid_range.
AISHA_RESIDUAL_CAP_MIN_ROUND = 3       # count reliable enough only late; R1-R2 keep full footroom
AISHA_RESIDUAL_CAP_MIN_REVEAL_RATIO = 0.72  # known_cells/mid: only cap once most of the grid is seen
                                            # (shallow R4 ~0.8-0.9; deep 2410 ~0.2-0.3, R3 ~0.5 skip)
AISHA_RESIDUAL_CAP_SLACK = 1.25        # headroom: hidden items may run a bit larger than seen ones
AISHA_RESIDUAL_CAP_PER_ITEM_MIN = 2.0  # floor on per-hidden-item cells so the cap can't crush
# The count prior typically runs ~1 item above the visible-item count even when the warehouse is
# fully revealed (clean R4: count 28 vs 26-27 visible -> a phantom hidden item × ~2.5 cells inflated
# the cap ~+4-6 over truth, robustness eval). Discount that count-noise item from the residual: it
# erases the clean-R4 over while barely touching gold/red-heavy warehouses (hidden 10+ -> -1 is minor).
# The cap stays floored at known_cells (= visible cells), so this can never cause a visible-under.
AISHA_RESIDUAL_CAP_COUNT_NOISE = 1
# The residual cap originally required a SHALLOW viewport (raw_deepest<=8), but a scattered layout
# can show a deep raw_deepest (e.g. an outlier item at row 10) while the vault is actually ~8 rows
# and near-fully revealed (live 2402 R4: raw_deepest 10, known 75/78, but footroom inflated grid to
# 95). The reveal-completeness gate (known/mid>=0.72) is the real discriminator -- deep-and-hidden
# vaults (2410 ~0.2-0.3) fall below it -- so allow a deeper raw_deepest here and let completeness
# decide. Kept below full depth (12) as a safety margin for genuinely deep warehouses.
AISHA_RESIDUAL_CAP_MAX_DEEPEST = 11
AISHA_LAYOUT_FOOTROOM_RAW_BOTTOM_ANCHOR_NOTE = "aisha_layout_footroom_raw_bottom_anchor"
AISHA_LAYOUT_APPLICATION_MODE_NOTE = "aisha_layout_application_mode"

LAYOUT_SPARSE_EARLY_HINT_NOTE = "layout_sparse_early_viewport_hint"
LAYOUT_SPARSE_BAND_WIDEN_DELTA_NOTE = "layout_sparse_band_widen_delta"
LAYOUT_SPARSE_PROFILE_NOTE = "layout_sparse_profile"
# Soft target for sparse-early (draw) heroes: a CAP (known_cells + early_soft_target_cap) to curb
# early over-estimation, NOT a settled total. Consumers must treat it as soft — enforcing it as a
# hard grid equality blanks deep vaults (no count combo's grid equals the cap -> no_reachable_combo).
LAYOUT_SPARSE_EARLY_SOFT_TARGET_NOTE_PREFIX = "layout_sparse_early_soft_target:"

# 统一 0.85(原 0.90/0.85/0.80 递减系数会在窄价值区间把激进压到≤参考→max()钳平→"三档一样";
# 统一系数保留 p25/p50/p75 的天然区间宽度、参考(p50×0.85)不变,保<参<激不再塌。见 memory
# tier-collapse-descending-multipliers。)
REF_QUOTE_SAFETY_TIER = (0.85, 0.85, 0.85)
REF_QUOTE_SAFETY_BASE = 0.85
REF_QUOTE_SAFETY_TIER_NOTE = "ref_quote_safety_tier_uniform_v2"


@dataclass(frozen=True)
class LayoutDepthSpec:
    profile_id: str
    early_max_round: int
    footroom_min_round: int
    footroom_max_round: int
    footroom_mult_r1: float
    footroom_mult_r2: float
    early_soft_target_cap: float
    footroom_raise_cap_r3: int
    footroom_raise_cap_r4: int
    footroom_raise_cap_r5: int
    use_quality_blended_deepest: bool
    skip_white_only_through_round: int
    sparse_footroom_boost_floor: float


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_note_once(notes: list[str], marker: str) -> None:
    if marker not in notes:
        notes.append(marker)


def _item_top_row(item: dict[str, Any], *, columns: int = GRID_COLUMNS) -> int | None:
    """Minimap row is 1-based; baoguang quality-only items may only have local_index."""
    row = _safe_int(item.get("row"))
    if row is not None:
        return int(row)
    local_index = _safe_int(item.get("local_index"))
    if local_index is not None and columns > 0:
        return int(local_index) // int(columns) + 1
    y = _safe_int(item.get("y"))
    if y is not None:
        cell_h = _safe_int(item.get("cell_h")) or 1
        return int(y // max(1, cell_h)) + 1
    return None


def _item_bottom_row(item: dict[str, Any], *, columns: int = GRID_COLUMNS) -> int | None:
    row = _item_top_row(item, columns=columns)
    if row is None:
        return None
    height = _safe_int(item.get("height")) or 1
    return int(row) + max(1, int(height)) - 1


def _footroom_viewport_deepest_row(*, depth_row: int, raw_deepest: int, round_no: int) -> int:
    """Anchor footroom viewport on raw bottom when it sits well below weighted cluster."""
    raw_gap = int(raw_deepest) - int(depth_row)
    if int(round_no) >= 2 and raw_gap > 0:
        return int(raw_deepest)
    # R1 baoguang/deep hints can sit 2+ rows below the weighted cluster (e.g. 2403 raw13 vs depth10).
    if int(round_no) == 1 and raw_gap >= 2:
        return int(raw_deepest)
    return int(depth_row)


def _deepest_minimap_bottom_row(items: list[dict[str, Any]], *, columns: int = GRID_COLUMNS) -> int | None:
    bottoms = [value for item in items if (value := _item_bottom_row(item, columns=columns)) is not None]
    return max(bottoms) if bottoms else None


def _known_minimap_cells(items: list[dict[str, Any]]) -> int:
    total = 0
    for item in items:
        cells = _safe_int(item.get("cells"))
        if cells is not None and cells > 0:
            total += int(cells)
            continue
        width = _safe_int(item.get("width")) or 1
        height = _safe_int(item.get("height")) or 1
        total += max(1, int(width) * int(height))
    return total


def _max_minimap_quality(items: list[dict[str, Any]]) -> int | None:
    qualities = [
        int(value)
        for item in items
        if (value := _safe_int(item.get("quality"))) is not None and int(value) > 0
    ]
    return max(qualities) if qualities else None


def _viewport_fill_ratio(*, known_cells: int, deepest: int, columns: int = GRID_COLUMNS) -> float:
    viewport_cells = max(1, int(deepest) * int(columns))
    return min(1.0, max(0.0, float(known_cells) / float(viewport_cells)))


def _sparsity_boost(fill_ratio: float, *, floor: float) -> float:
    return max(floor, min(0.85, 1.0 - float(fill_ratio)))


def _dense_viewport_footroom_cap(
    *,
    known_cells: int,
    raw_deepest: int,
    fill_ratio: float,
    round_no: int,
    balanced_mult: float,
    raw_bottom_anchored: bool = False,
) -> float | None:
    """Mid-game dense viewport: cap overshoot when weighted depth sits above raw bottom."""
    if int(round_no) < 2 or int(known_cells) < 20:
        return None
    if float(fill_ratio) < 0.28:
        return None
    slack = {2: 50, 3: 34, 4: 26, 5: 18}.get(int(round_no), 22)
    if raw_bottom_anchored:
        return float(known_cells) + float(slack)
    rows_below_raw = max(0, WAREHOUSE_ROWS - int(raw_deepest))
    occupancy_damp = max(0.30, 1.0 - float(fill_ratio) * 0.90)
    ghost = (
        float(rows_below_raw)
        * float(GRID_COLUMNS)
        * float(balanced_mult)
        * occupancy_damp
        * 0.75
    )
    return float(known_cells) + min(float(slack), ghost)


def _footroom_known_high_slack(*, round_no: int, known_cells: int) -> int | None:
    if int(known_cells) < 20:
        return None
    return {2: 50, 3: 34, 4: 26, 5: 22}.get(int(round_no), 24)


def _quality_depth_weight(quality: int) -> float:
    if quality <= 1:
        return 0.35
    if quality == 2:
        return 0.55
    if quality == 3:
        return 0.75
    if quality == 4:
        return 0.85
    return 1.0


def _item_cell_count(item: dict[str, Any]) -> int:
    cells = _safe_int(item.get("cells"))
    if cells is not None and cells > 0:
        return int(cells)
    width = _safe_int(item.get("width")) or 1
    height = _safe_int(item.get("height")) or 1
    return max(1, int(width) * int(height))


def _multi_deepest_bottom_weighted_row(
    items: list[dict[str, Any]], *, columns: int = GRID_COLUMNS
) -> int | None:
    """Cell-weighted average bottom row; deeper rows dominate via (row/capacity)^exp."""
    weighted_sum = 0.0
    weight_total = 0.0
    for item in items:
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None:
            continue
        quality = _safe_int(item.get("quality")) or 1
        row_weight = (float(bottom) / float(WAREHOUSE_ROWS)) ** MULTI_DEEPEST_ROW_WEIGHT_EXP
        item_weight = float(_item_cell_count(item)) * row_weight * _quality_depth_weight(int(quality))
        weighted_sum += float(bottom) * item_weight
        weight_total += item_weight
    if weight_total <= 0:
        return None
    return max(1, int(round(weighted_sum / weight_total)))


def _quality_blended_deepest_row(
    items: list[dict[str, Any]], *, columns: int = GRID_COLUMNS
) -> int | None:
    deepest = _deepest_minimap_bottom_row(items, columns=columns)
    if deepest is None:
        return None
    low_bottoms: list[int] = []
    high_bottoms: list[int] = []
    for item in items:
        quality = _safe_int(item.get("quality"))
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None or quality is None:
            continue
        if int(quality) <= 1:
            low_bottoms.append(int(bottom))
        elif int(quality) >= 3:
            high_bottoms.append(int(bottom))
    if not low_bottoms or not high_bottoms:
        return deepest
    low_ref = max(low_bottoms)
    high_ref = max(high_bottoms)
    return int(round(0.35 * low_ref + 0.65 * high_ref))


def _footroom_depth_row(
    items: list[dict[str, Any]],
    *,
    round_no: int,
    spec: LayoutDepthSpec,
    columns: int = GRID_COLUMNS,
) -> int | None:
    """Phase C: bottom-weighted multi-deepest row for footroom (shallower => more rows below)."""
    raw = _deepest_minimap_bottom_row(items, columns=columns)
    if raw is None:
        return None
    if not spec.use_quality_blended_deepest or int(round_no) >= 4:
        return raw
    candidates = [int(raw)]
    weighted = _multi_deepest_bottom_weighted_row(items, columns=columns)
    if weighted is not None:
        candidates.append(int(weighted))
    blended = _quality_blended_deepest_row(items, columns=columns)
    if blended is not None:
        candidates.append(min(int(raw), int(blended)))
    return max(1, min(candidates))


def _effective_footroom_raise_cap(
    spec: LayoutDepthSpec,
    round_no: int,
    *,
    depth_row: int,
    raw_deepest: int,
    baseline: float,
    raw_hinted: float,
) -> int:
    base = _footroom_raise_cap(spec, int(round_no))
    slack_rows = max(0, int(raw_deepest) - int(depth_row))
    cap = int(base) + slack_rows * (7 if int(round_no) <= 2 else 3)
    if int(round_no) <= 2:
        needed = int(max(0.0, round(float(raw_hinted) - float(baseline))))
        cap = max(cap, min(needed, 80))
    return cap


def _effective_deepest_row(
    items: list[dict[str, Any]],
    *,
    round_no: int,
    spec: LayoutDepthSpec,
    columns: int = GRID_COLUMNS,
) -> int | None:
    deepest = _deepest_minimap_bottom_row(items, columns=columns)
    if deepest is None or not spec.use_quality_blended_deepest:
        return deepest
    if int(round_no) >= 4:
        return deepest
    low_bottoms: list[int] = []
    high_bottoms: list[int] = []
    for item in items:
        quality = _safe_int(item.get("quality"))
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None or quality is None:
            continue
        if int(quality) <= 1:
            low_bottoms.append(int(bottom))
        elif int(quality) >= 3:
            high_bottoms.append(int(bottom))
    if not low_bottoms or not high_bottoms:
        return deepest
    low_ref = max(low_bottoms)
    high_ref = max(high_bottoms)
    blended = int(round(0.35 * low_ref + 0.65 * high_ref))
    return max(DEEPEST_ROW_THRESHOLD, min(deepest, blended))


def _footroom_raise_cap(spec: LayoutDepthSpec, round_no: int) -> int:
    if int(round_no) <= 3:
        return spec.footroom_raise_cap_r3
    if int(round_no) == 4:
        return spec.footroom_raise_cap_r4
    return spec.footroom_raise_cap_r5


def _footroom_balanced_mult(round_no: int, *, full_profile: bool) -> float:
    if not full_profile:
        return 0.65
    if int(round_no) <= 2:
        return 0.95
    if int(round_no) == 4:
        return 1.05
    return 1.15


def _footroom_multipliers(round_no: int, *, full_profile: bool) -> tuple[float, float, float]:
    if not full_profile:
        return (0.35, 0.65, 0.95)
    if int(round_no) <= 2:
        return (0.75, 0.95, 1.15)
    if int(round_no) == 4:
        return (0.85, 1.05, 1.30)
    return (0.95, 1.15, 1.40)


def _target_looks_undershot(
    *,
    total_grid_target: float | None,
    known_cells: int,
    rows_below: int,
    round_no: int,
    full_profile: bool,
) -> bool:
    if total_grid_target is None:
        return True
    baseline = float(total_grid_target)
    if baseline <= float(known_cells) + 0.5:
        return True
    round_scale = 0.45 if int(round_no) <= 3 else (0.35 if int(round_no) == 4 else 0.28)
    if not full_profile:
        round_scale *= 0.85
    implied_ceiling = float(known_cells) + float(rows_below) * float(GRID_COLUMNS) * round_scale
    return baseline + 0.5 < implied_ceiling


LAYOUT_DEPTH_SPECS: dict[str, LayoutDepthSpec] = {
    "aisha": LayoutDepthSpec(
        profile_id="full",
        early_max_round=0,
        footroom_min_round=LAYOUT_MIN_ROUND,
        footroom_max_round=5,
        footroom_mult_r1=1.35,
        footroom_mult_r2=1.25,
        early_soft_target_cap=float(WAREHOUSE_CELL_CAPACITY),
        footroom_raise_cap_r3=45,
        footroom_raise_cap_r4=55,
        footroom_raise_cap_r5=65,
        use_quality_blended_deepest=True,
        skip_white_only_through_round=WHITE_ONLY_MAX_ROUND,
        sparse_footroom_boost_floor=0.2,
    ),
    "raven": LayoutDepthSpec(
        profile_id="sparse_early",
        early_max_round=4,
        footroom_min_round=99,
        footroom_max_round=0,
        footroom_mult_r1=1.10,
        footroom_mult_r2=1.05,
        early_soft_target_cap=24.0,
        footroom_raise_cap_r3=8,
        footroom_raise_cap_r4=10,
        footroom_raise_cap_r5=12,
        use_quality_blended_deepest=False,
        skip_white_only_through_round=4,
        sparse_footroom_boost_floor=0.35,
    ),
    "sophie": LayoutDepthSpec(
        profile_id="sparse_early",
        early_max_round=4,
        footroom_min_round=99,
        footroom_max_round=0,
        footroom_mult_r1=1.12,
        footroom_mult_r2=1.06,
        early_soft_target_cap=26.0,
        footroom_raise_cap_r3=8,
        footroom_raise_cap_r4=10,
        footroom_raise_cap_r5=12,
        use_quality_blended_deepest=False,
        skip_white_only_through_round=3,
        sparse_footroom_boost_floor=0.35,
    ),
    "gabriela": LayoutDepthSpec(
        profile_id="sparse_early",
        early_max_round=4,
        footroom_min_round=99,
        footroom_max_round=0,
        footroom_mult_r1=1.12,
        footroom_mult_r2=1.06,
        early_soft_target_cap=26.0,
        footroom_raise_cap_r3=8,
        footroom_raise_cap_r4=10,
        footroom_raise_cap_r5=12,
        use_quality_blended_deepest=False,
        skip_white_only_through_round=3,
        sparse_footroom_boost_floor=0.35,
    ),
}


def layout_depth_spec_for_hero(hero_key: str) -> LayoutDepthSpec | None:
    return LAYOUT_DEPTH_SPECS.get(str(hero_key or "").strip().lower())


def quote_safety_multipliers(safety_factor: float = REF_QUOTE_SAFETY_BASE) -> tuple[float, float, float]:
    scale = float(safety_factor) / REF_QUOTE_SAFETY_BASE if safety_factor else 1.0
    return tuple(min(1.0, tier * scale) for tier in REF_QUOTE_SAFETY_TIER)


def _apply_early_viewport_hint(
    *,
    spec: LayoutDepthSpec,
    round_no: int,
    total_grid_target: float | None,
    source_notes: list[str],
    items: list[dict[str, Any]],
) -> float | None:
    if int(round_no) > spec.early_max_round:
        return total_grid_target
    if not items:
        return total_grid_target
    known_cells = _known_minimap_cells(items)
    if known_cells <= 0:
        return total_grid_target
    raw_deepest = _deepest_minimap_bottom_row(items)
    deepest = _footroom_depth_row(items, round_no=int(round_no), spec=spec)
    if (
        deepest is None
        or raw_deepest is None
        or int(raw_deepest) < FOOTROOM_ACTIVATION_MIN_RAW_ROW
    ):
        return total_grid_target

    rows_below = max(0, WAREHOUSE_ROWS - int(deepest))
    fill_ratio = _viewport_fill_ratio(known_cells=known_cells, deepest=int(deepest))
    sparsity_boost = _sparsity_boost(fill_ratio, floor=spec.sparse_footroom_boost_floor)
    footroom_mult = spec.footroom_mult_r1 if int(round_no) <= 1 else spec.footroom_mult_r2
    footroom = rows_below * GRID_COLUMNS * footroom_mult * sparsity_boost
    hinted = float(known_cells + footroom)
    max_quality = _max_minimap_quality(items)

    if spec.profile_id == "sparse_early":
        _append_note_once(source_notes, LAYOUT_SPARSE_PROFILE_NOTE)
        _append_note_once(source_notes, LAYOUT_SPARSE_EARLY_HINT_NOTE)
        baseline = float(total_grid_target if total_grid_target is not None else known_cells)
        delta = max(0, int(round(hinted - baseline)))
        if delta > 0:
            _append_note_once(source_notes, f"{LAYOUT_SPARSE_BAND_WIDEN_DELTA_NOTE}:{delta}")
        if total_grid_target is None:
            capped = min(hinted, float(known_cells) + spec.early_soft_target_cap)
            rounded = float(int(round(capped)))
            _append_note_once(source_notes, f"{LAYOUT_SPARSE_EARLY_SOFT_TARGET_NOTE_PREFIX}{int(round(rounded))}")
            return rounded
        return total_grid_target

    _append_note_once(source_notes, AISHA_EARLY_VIEWPORT_GRID_HINT_NOTE)
    if total_grid_target is not None and hinted <= float(total_grid_target) + 0.5:
        return total_grid_target
    capped = min(hinted, float(WAREHOUSE_CELL_CAPACITY))
    rounded = float(int(round(capped)))
    _append_note_once(source_notes, f"aisha_early_viewport_soft_target:{int(round(rounded))}")
    return rounded


def _apply_footroom_hint(
    *,
    spec: LayoutDepthSpec,
    round_no: int,
    total_grid_target: float | None,
    source_notes: list[str],
    items: list[dict[str, Any]],
    layout_mode: str,
    total_count: int | None = None,
) -> float | None:
    if spec.profile_id != "full":
        return total_grid_target
    if layout_mode == LAYOUT_MODE_OFF:
        return total_grid_target
    if int(round_no) < spec.footroom_min_round:
        return total_grid_target

    max_quality = _max_minimap_quality(items)
    if (
        max_quality is not None
        and max_quality <= 1
        and int(round_no) <= spec.skip_white_only_through_round
    ):
        return total_grid_target

    raw_deepest = _deepest_minimap_bottom_row(items)
    deepest = _footroom_depth_row(items, round_no=int(round_no), spec=spec)
    if (
        deepest is None
        or raw_deepest is None
        or int(raw_deepest) < FOOTROOM_ACTIVATION_MIN_RAW_ROW
    ):
        return total_grid_target

    known_cells = _known_minimap_cells(items)
    viewport_deepest = _footroom_viewport_deepest_row(
        depth_row=int(deepest),
        raw_deepest=int(raw_deepest),
        round_no=int(round_no),
    )
    raw_bottom_anchored = int(viewport_deepest) > int(deepest)
    if raw_bottom_anchored:
        _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_RAW_BOTTOM_ANCHOR_NOTE)
    rows_below = max(0, WAREHOUSE_ROWS - int(viewport_deepest))
    if not _target_looks_undershot(
        total_grid_target=total_grid_target,
        known_cells=known_cells,
        rows_below=rows_below,
        round_no=int(round_no),
        full_profile=True,
    ):
        _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_SKIP_NOTE)
        return total_grid_target

    fill_ratio = _viewport_fill_ratio(known_cells=known_cells, deepest=int(viewport_deepest))
    sparsity_boost = _sparsity_boost(fill_ratio, floor=spec.sparse_footroom_boost_floor)
    base_footroom = rows_below * GRID_COLUMNS
    conservative_mult, balanced_mult, aggressive_mult = _footroom_multipliers(
        int(round_no),
        full_profile=True,
    )
    footroom = base_footroom * balanced_mult * sparsity_boost
    _, _, lonely_scale = _lonely_deepest_band_scale(items, raw_deepest=raw_deepest)
    if _warehouse_is_shallow(raw_deepest=raw_deepest):
        footroom *= lonely_scale
    elif lonely_scale < 0.99 and os.environ.get("AISHA_DISABLE_DEEP_LONELY_FOOTROOM") != "1":
        # Part A (grid depth-decay): a DEEP warehouse whose deepest row is reached by only a lone /
        # weak-support item over-fills the full-width ghost rows below it (lib calibration: below-dense
        # rows settle to ~3-6 cells, support-scaled, NOT 10). Apply the same lonely scale that shallow
        # warehouses get (lone=0.35 / weak-2=0.55-0.70; bilateral >=3-item ~1.0 untouched). BUT floor by
        # the count-implied grid so a high-count vault whose deepest happens to be a lone item isn't
        # under-cut (the "件数协同": support AND item count, not support alone). count_implied uses the
        # engine's total_count; on real R3 it tracks known, so the scale still trims genuine over-fill.
        scaled = footroom * lonely_scale
        if total_count is not None and int(total_count) > 0:
            implied = float(int(total_count)) * _count_implied_cells_per_item(
                raw_deepest=raw_deepest, round_no=round_no
            )
            min_footroom = max(0.0, float(implied) - float(known_cells))
            scaled = max(scaled, min(footroom, min_footroom))
        if scaled < footroom:
            footroom = scaled
            _append_note_once(source_notes, f"aisha_deep_lonely_footroom:{lonely_scale:g}")
    if int(round_no) == 2 and not _warehouse_is_shallow(raw_deepest=raw_deepest):
        footroom *= spec.footroom_mult_r2
    if (
        os.environ.get("AISHA_DISABLE_HIGH_FILL_FOOTROOM_DAMP") != "1"
        and float(fill_ratio) >= AISHA_HIGH_FILL_FOOTROOM_THRESHOLD
    ):
        # Near-full page: depth is essentially confirmed at the deepest revealed row -> collapse the
        # below-deepest footroom toward 0 as fill -> 1 (linear from threshold). Sparse pages untouched.
        high_fill_damp = max(
            0.0,
            (1.0 - float(fill_ratio)) / (1.0 - AISHA_HIGH_FILL_FOOTROOM_THRESHOLD),
        )
        footroom *= high_fill_damp
        _append_note_once(source_notes, AISHA_HIGH_FILL_FOOTROOM_DAMP_NOTE)
    raw_hinted = float(known_cells + footroom)
    baseline = float(total_grid_target if total_grid_target is not None else known_cells)
    raise_cap = _effective_footroom_raise_cap(
        spec,
        int(round_no),
        depth_row=int(deepest),
        raw_deepest=int(raw_deepest),
        baseline=baseline,
        raw_hinted=raw_hinted,
    )
    capped_hinted = min(raw_hinted, baseline + float(raise_cap))
    dense_cap = _dense_viewport_footroom_cap(
        known_cells=int(known_cells),
        raw_deepest=int(raw_deepest),
        fill_ratio=float(fill_ratio),
        round_no=int(round_no),
        balanced_mult=float(balanced_mult),
        raw_bottom_anchored=raw_bottom_anchored,
    )
    if dense_cap is not None and dense_cap + 0.5 < capped_hinted:
        capped_hinted = min(capped_hinted, dense_cap)
        _append_note_once(source_notes, AISHA_LAYOUT_DENSE_VIEWPORT_CAP_NOTE)
    # R3+: dense cap only for shallow visible warehouse; deep row-9+ still has ghost rows below.
    if (
        int(round_no) >= 3
        and known_cells >= 40
        and _warehouse_is_shallow(raw_deepest=raw_deepest)
    ):
        raw_deepest = _deepest_minimap_bottom_row(items)
        depth_row = int(raw_deepest if raw_deepest is not None else deepest)
        rows_below_raw = max(0, WAREHOUSE_ROWS - depth_row)
        dense_budget = (
            float(rows_below_raw)
            * float(GRID_COLUMNS)
            * balanced_mult
            * sparsity_boost
            * 0.55
        )
        dense_cap = float(known_cells) + min(float(raise_cap), dense_budget)
        capped_hinted = min(capped_hinted, dense_cap)
    if _warehouse_is_shallow(raw_deepest=raw_deepest):
        shallow_cap = _shallow_visible_grid_cap(
            round_no=int(round_no),
            known_cells=known_cells,
        )
        capped_hinted = min(capped_hinted, shallow_cap)
    hinted = capped_hinted
    if hinted <= baseline + 0.5:
        return total_grid_target

    if raw_hinted > capped_hinted + 0.5:
        _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_CAP_NOTE)
    if sparsity_boost >= 0.55:
        _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE)
    _append_note_once(source_notes, AISHA_LAYOUT_GRID_HINT_NOTE)
    _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_NOTE)
    _append_note_once(
        source_notes,
        f"{AISHA_MULTI_DEEPEST_FOOTROOM_NOTE}:{int(deepest)}@raw{int(raw_deepest)}",
    )
    _append_note_once(
        source_notes,
        f"{AISHA_LAYOUT_FOOTROOM_MULT_NOTE}:"
        f"{conservative_mult:g}/{balanced_mult:g}/{aggressive_mult:g}@r{int(round_no)}",
    )
    _append_note_once(source_notes, f"{AISHA_LAYOUT_APPLICATION_MODE_NOTE}:{layout_mode}")
    delta = int(round(hinted - baseline))
    if layout_mode == LAYOUT_MODE_SHADOW:
        return total_grid_target
    if layout_mode == LAYOUT_MODE_BAND:
        band_delta = _effective_band_widen_delta(
            delta=int(delta),
            round_no=int(round_no),
            raw_deepest=raw_deepest,
            lonely_scale=lonely_scale,
        )
        _append_note_once(source_notes, f"{AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE}:{band_delta}")
        _append_note_once(source_notes, f"{AISHA_LAYOUT_FOOTROOM_HINT_NOTE}:{int(round(hinted))}")
        # Band mode widens combo range only; Aisha keeps floating grid unless hard total is known.
        return total_grid_target
    if total_grid_target is not None:
        _append_note_once(
            source_notes,
            f"total_grid_target_raised:{int(round(float(total_grid_target)))}->{int(round(hinted))}",
        )
    return hinted


def layout_band_widen_delta(source_notes: Iterable[str], *, sparse: bool = False) -> int | None:
    prefixes = (
        LAYOUT_SPARSE_BAND_WIDEN_DELTA_NOTE,
        AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE,
    )
    if sparse:
        prefixes = (LAYOUT_SPARSE_BAND_WIDEN_DELTA_NOTE, AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE)
    for note in source_notes:
        text = str(note)
        for prefix in prefixes:
            if text.startswith(f"{prefix}:"):
                try:
                    return max(0, int(text.split(":", 1)[1]))
                except ValueError:
                    return None
    return None


def apply_layout_band_widen_to_range(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
) -> tuple[int | None, int | None, int | None]:
    delta = layout_band_widen_delta(source_notes)
    if delta is None or delta <= 0:
        return grid_range
    low, mid, high = grid_range
    if mid is None and high is None:
        return grid_range
    anchor = int(mid if mid is not None else high or 0)
    new_high = max(int(high or 0), anchor + int(delta))
    new_mid = max(int(mid or 0), anchor + max(1, int(delta) // 2))
    if low is not None:
        new_high = max(int(low), new_high)
        new_mid = max(int(low), new_mid)
    _append_note_once(source_notes, AISHA_LAYOUT_BAND_WIDEN_APPLIED_NOTE)
    return (low, new_mid, new_high)


def layout_footroom_hint(source_notes: Iterable[str]) -> int | None:
    for note in source_notes:
        text = str(note)
        if text.startswith(f"{AISHA_LAYOUT_FOOTROOM_HINT_NOTE}:"):
            try:
                return max(0, int(text.split(":", 1)[1]))
            except ValueError:
                return None
    return None


def apply_layout_footroom_band_range_anchor(
    grid_range: tuple[int | None, int | None, int | None],
    *,
    hero_key: str,
    round_no: int | None,
    known_cells: int,
    raw_deepest: int | None,
    source_notes: list[str],
) -> tuple[int | None, int | None, int | None]:
    """Anchor floating grid range on layout footroom when combo prior sits too low."""
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if round_no is None or int(round_no) < 1:
        return grid_range
    if _warehouse_is_shallow(raw_deepest=raw_deepest):
        return grid_range
    if int(known_cells) < _footroom_band_known_threshold(int(round_no)):
        return grid_range
    if AISHA_LAYOUT_BAND_WIDEN_APPLIED_NOTE not in source_notes:
        return grid_range
    hint = layout_footroom_hint(source_notes)
    if hint is None:
        return grid_range
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    new_mid = max(int(mid), int(hint))
    low_spread = _footroom_band_low_spread(int(round_no))
    physical_low = max(int(known_cells) + 6, new_mid - low_spread)
    new_low = max(int(low or 0), physical_low)
    new_low = min(new_low, new_mid)
    high_cap = _footroom_band_high_cap(hint=int(hint), round_no=int(round_no))
    known_slack = _footroom_known_high_slack(
        round_no=int(round_no),
        known_cells=int(known_cells),
    )
    if known_slack is not None:
        high_cap = min(int(high_cap), int(known_cells) + int(known_slack))
    new_high = max(int(high if high is not None else new_mid), new_mid + low_spread)
    if high_cap >= new_mid:
        new_high = min(new_high, int(high_cap))
    new_high = max(new_high, new_mid)
    if new_low == int(low or 0) and new_mid == int(mid) and new_high == int(high or 0):
        return grid_range
    _append_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_BAND_ANCHOR_NOTE)
    return (new_low, new_mid, new_high)


def _count_implied_cells_per_item(*, raw_deepest: int | None, round_no: int | None = None) -> float:
    if raw_deepest is not None and int(raw_deepest) <= SHALLOW_WAREHOUSE_MAX_RAW_ROW:
        if round_no is not None:
            return AISHA_SHALLOW_COUNT_MULT_BY_ROUND.get(
                int(round_no), AISHA_SHALLOW_COUNT_MULT_DEFAULT
            )
        return AISHA_SHALLOW_COUNT_MULT_DEFAULT
    return AISHA_COUNT_IMPLIED_CELLS_PER_ITEM


def _shallow_visible_grid_cap(*, round_no: int, known_cells: int) -> float:
    """2410-style mid-warehouse visibility (deepest row ~7): keep totals near ~70–76."""
    round_cap = {1: 72.0, 2: 75.0, 3: 76.0}.get(int(round_no), 85.0)
    return max(float(known_cells), round_cap)


def _item_left_col(item: dict[str, Any], *, columns: int = GRID_COLUMNS) -> int | None:
    col = _safe_int(item.get("col"))
    if col is not None:
        return int(col)
    local_index = _safe_int(item.get("local_index"))
    if local_index is not None and columns > 0:
        return int(local_index) % int(columns) + 1
    return None


def _bottom_band_occupied_cells(
    items: list[dict[str, Any]], *, raw_deepest: int, columns: int = GRID_COLUMNS
) -> int:
    """Count occupied cells in the bottom two visible rows (flat-bottom architecture signal)."""
    raw = int(raw_deepest)
    band_rows = {raw}
    if raw > 1:
        band_rows.add(raw - 1)
    occupied: set[tuple[int, int]] = set()
    for item in items:
        top = _item_top_row(item, columns=columns)
        left = _item_left_col(item, columns=columns)
        if top is None or left is None:
            continue
        width = _safe_int(item.get("width")) or 1
        height = _safe_int(item.get("height")) or 1
        for dr in range(int(height)):
            for dc in range(int(width)):
                row = int(top) + dr
                col = int(left) + dc
                if row in band_rows and 1 <= col <= int(columns):
                    occupied.add((row, col))
    return len(occupied)


def _shallow_flat_bottom_ready(
    *,
    round_no: int,
    raw_deepest: int | None,
    known_cells: int,
    items: list[dict[str, Any]],
) -> bool:
    if not _warehouse_is_shallow(raw_deepest=raw_deepest):
        return False
    if int(round_no) < 2:
        return False
    raw = int(raw_deepest)
    viewport_cells = raw * GRID_COLUMNS
    fill_ratio = float(known_cells) / max(1.0, float(viewport_cells))
    min_fill = {2: 0.38, 3: 0.42, 4: 0.48, 5: 0.52}.get(int(round_no), 0.55)
    if fill_ratio < min_fill:
        return False
    bottom_cells = _bottom_band_occupied_cells(items, raw_deepest=raw)
    bottom_band_cells = max(1, min(2, raw) * GRID_COLUMNS)
    return float(bottom_cells) / float(bottom_band_cells) >= 0.30


def _shallow_flat_bottom_grid_caps(*, round_no: int, anchor_cells: int) -> tuple[int, int]:
    """Return (mid_cap, high_cap) anchored on raw_row×10 for late shallow rounds."""
    mid_tail = {2: 5, 3: 4, 4: 3, 5: 2}.get(int(round_no), 2)
    high_tail = {2: 8, 3: 6, 4: 4, 5: 2}.get(int(round_no), 2)
    anchor = int(anchor_cells)
    return anchor + int(mid_tail), anchor + int(high_tail)


def apply_shallow_flat_bottom_grid_range_tighten(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
    known_cells: int,
    items: list[dict[str, Any]] | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Late shallow rounds: once bottom rows fill in, cap float band near raw_row×10."""
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if _aisha_grid_total_hard_locked(source_notes):
        return grid_range
    if round_no is None or int(round_no) < 2:
        return grid_range
    if not _shallow_flat_bottom_ready(
        round_no=int(round_no),
        raw_deepest=raw_deepest,
        known_cells=int(known_cells),
        items=list(items or []),
    ):
        return grid_range
    low, mid, high = grid_range
    if mid is None or high is None:
        return grid_range
    anchor_cells = int(raw_deepest) * GRID_COLUMNS
    mid_cap, high_cap = _shallow_flat_bottom_grid_caps(
        round_no=int(round_no),
        anchor_cells=anchor_cells,
    )
    new_high = min(int(high), int(high_cap))
    new_mid = min(int(mid), int(mid_cap), new_high - 2)
    new_low = int(low if low is not None else new_mid)
    if new_mid >= new_high:
        new_mid = max(new_low + 2, new_high - 2)
    if new_low >= new_mid:
        new_low = max(0, new_mid - 2)
    if new_low == int(low or new_low) and new_mid == int(mid) and new_high == int(high):
        return grid_range
    _append_note_once(source_notes, AISHA_SHALLOW_FLAT_BOTTOM_GRID_TIGHTEN_NOTE)
    return (new_low if low is not None else None, new_mid, new_high)


def _warehouse_is_shallow(*, raw_deepest: int | None) -> bool:
    return raw_deepest is not None and int(raw_deepest) <= SHALLOW_WAREHOUSE_MAX_RAW_ROW


def _lonely_deepest_band_scale(
    items: list[dict[str, Any]], *, raw_deepest: int | None
) -> tuple[int, int, float]:
    """Bottom row with one isolated item is weak evidence for full-width ghost rows below."""
    if raw_deepest is None:
        return (0, 0, 1.0)
    at_deepest = [
        item
        for item in items
        if (bottom := _item_bottom_row(item)) is not None and int(bottom) == int(raw_deepest)
    ]
    item_count = len(at_deepest)
    cells = sum(_item_cell_count(item) for item in at_deepest)
    if item_count == 1:
        return (1, cells, 0.35)
    if item_count == 2 and cells <= 4:
        return (2, cells, 0.55)
    if item_count == 2:
        return (2, cells, 0.70)
    return (item_count, cells, 1.0)


def _effective_band_widen_delta(
    *,
    delta: int,
    round_no: int,
    raw_deepest: int | None,
    lonely_scale: float,
) -> int:
    scaled = int(round(max(0, int(delta)) * float(lonely_scale)))
    if _warehouse_is_shallow(raw_deepest=raw_deepest):
        return min(scaled, 6)
    if float(lonely_scale) < 0.99:
        return min(scaled, max(5, int(round(18 * float(lonely_scale)))))
    if int(round_no) <= 2:
        return min(scaled, 10)
    return min(scaled, 45)


def apply_grid_range_spread_cap(
    grid_range: tuple[int | None, int | None, int | None],
    *,
    hero_key: str,
    items: list[dict[str, Any]],
    raw_deepest: int | None,
    source_notes: list[str],
) -> tuple[int | None, int | None, int | None]:
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if raw_deepest is None:
        return grid_range
    _, _, lonely_scale = _lonely_deepest_band_scale(items, raw_deepest=raw_deepest)
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    new_mid = int(mid)
    new_high = int(high if high is not None else mid)
    changed = False
    if _warehouse_is_shallow(raw_deepest=raw_deepest):
        anchor = int(low if low is not None else mid)
        capped_mid = min(new_mid, anchor + 4)
        capped_high = min(new_high, capped_mid + 6)
        if capped_mid != new_mid or capped_high != new_high:
            new_mid, new_high = capped_mid, capped_high
            changed = True
    elif float(lonely_scale) < 0.99:
        # Deep raw bottom (row 10+) is strong depth evidence; do not crush high on lonely cap.
        if int(raw_deepest) < DEEP_VISIBILITY_MIN_RAW_ROW:
            capped_high = min(new_high, new_mid + max(5, int(round(8 * float(lonely_scale) + 3))))
            if capped_high != new_high:
                new_high = capped_high
                changed = True
    if not changed:
        return grid_range
    _append_note_once(
        source_notes,
        f"{AISHA_LONELY_DEEPEST_BAND_NOTE}:{int(raw_deepest)}@{lonely_scale:g}",
    )
    return (low, new_mid, new_high)


def apply_shallow_visible_grid_low_floor(
    grid_range: tuple[int | None, int | None, int | None],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
    known_cells: int,
    source_notes: list[str],
) -> tuple[int | None, int | None, int | None]:
    """R3 shallow visible warehouse: combo g10 can dip below plausible small-warehouse totals."""
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if round_no is None or int(round_no) != 3:
        return grid_range
    if raw_deepest is None or int(raw_deepest) > SHALLOW_WAREHOUSE_MAX_RAW_ROW:
        return grid_range
    if int(known_cells) < 25:
        return grid_range
    low, mid, high = grid_range
    if low is None and mid is None and high is None:
        return grid_range
    floor = max(AISHA_SHALLOW_GRID_LOW_FLOOR, int(known_cells))
    new_low = max(int(low or 0), floor)
    round_cap = int(
        round(
            _shallow_visible_grid_cap(
                round_no=int(round_no),
                known_cells=int(known_cells),
            )
        )
    )
    new_mid = max(int(mid or 0), new_low)
    new_high = max(int(high or 0), new_mid)
    if new_mid > round_cap:
        new_mid = round_cap
    if new_high > round_cap + 6:
        new_high = round_cap + 6
    if new_low == int(low or 0) and new_mid == int(mid or 0) and new_high == int(high or 0):
        return grid_range
    _append_note_once(source_notes, f"{AISHA_SHALLOW_GRID_LOW_NOTE}:{new_low}")
    return (new_low, new_mid, new_high)


def count_implied_grid_floor(source_notes: Iterable[str]) -> int | None:
    for note in source_notes:
        text = str(note)
        if text.startswith(f"{AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE}:"):
            try:
                return max(0, int(text.split(":", 1)[1]))
            except ValueError:
                return None
    return None


def _aisha_grid_total_hard_locked(source_notes: Iterable[str]) -> bool:
    notes = {str(note) for note in source_notes}
    return bool(notes & AISHA_GRID_TOTAL_HARD_NOTES)


def _aisha_floating_grid_spread_targets(*, round_no: int | None, raw_deepest: int | None) -> tuple[int, int, int | None]:
    """Return (mid_delta, high_delta, optional high_cap) for unlocked Aisha grid ranges."""
    round_key = int(round_no or 1)
    mid_delta = {1: 4, 2: 6, 3: 6, 4: 8, 5: 10}.get(round_key, 6)
    high_delta = {1: 10, 2: 12, 3: 12, 4: 15, 5: 18}.get(round_key, 12)
    high_cap: int | None = None
    if round_key <= 1 and raw_deepest is not None and int(raw_deepest) <= SHALLOW_WAREHOUSE_MAX_RAW_ROW:
        high_cap = 85
    return int(mid_delta), int(high_delta), high_cap


def _deep_visibility_anchor_floor_row(raw_deepest: int) -> int:
    """Bottom band for counting deep anchors; include raw-2..raw (2408: row8/9/10 cluster)."""
    raw = int(raw_deepest)
    if raw <= 11:
        return max(DEEP_VISIBILITY_MIN_RAW_ROW - 2, raw - 2)
    return max(DEEP_VISIBILITY_MIN_RAW_ROW, raw - 3)


def _deep_visibility_anchor_items(
    items: list[dict[str, Any]], *, raw_deepest: int
) -> list[dict[str, Any]]:
    """Footprints sitting in the deep band near raw bottom confirm warehouse depth."""
    floor_row = _deep_visibility_anchor_floor_row(int(raw_deepest))
    anchored: list[dict[str, Any]] = []
    for item in items:
        bottom = _item_bottom_row(item)
        if bottom is not None and int(bottom) >= floor_row:
            anchored.append(item)
    return anchored


def _deep_visibility_band_targets(
    *,
    viewport_cells: int,
    anchor_count: int,
    known_cells: int,
) -> tuple[int, int, int]:
    if int(anchor_count) >= 2:
        # Two+ deep items (e.g. row-11 red + row-13 baoguang) → total tracks ~row×10.
        depth_low = max(int(known_cells) + 20, int(round(float(viewport_cells) * 0.85)))
        depth_mid = max(int(round(float(viewport_cells) * 0.92)), depth_low + 6)
        depth_high = min(
            WAREHOUSE_CELL_CAPACITY,
            max(int(round(float(viewport_cells) * 0.99)), depth_mid + 6),
        )
        return depth_low, depth_mid, depth_high
    depth_low = max(int(known_cells) + 12, int(round(float(viewport_cells) * 0.62)))
    depth_mid = max(int(round(float(viewport_cells) * 0.82)), depth_low + 8)
    depth_high = min(
        WAREHOUSE_CELL_CAPACITY,
        max(int(round(float(viewport_cells) * 0.95)), depth_mid + 10),
    )
    return depth_low, depth_mid, depth_high


def apply_deep_visibility_grid_range_anchor(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
    known_cells: int,
    items: list[dict[str, Any]] | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Early R1–R2 deep warehouse: raw row 10+ with sparse known cells implies ~row×10 total band."""
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if _aisha_grid_total_hard_locked(source_notes):
        return grid_range
    # Early-round deep band only. The geometry pass (apply_aisha_geometry_grid_range) now owns
    # deep R3+ lifting with a density-aware floor; letting this raw×10 band fire on sparse late
    # rounds over-lifts shallow-but-raw10 layouts (e.g. 2410 live r03 -> 92 vs ~70 truth).
    if round_no is None or int(round_no) > 2:
        return grid_range
    if raw_deepest is None or int(raw_deepest) < DEEP_VISIBILITY_MIN_RAW_ROW:
        return grid_range
    if int(known_cells) >= 40:
        return grid_range
    viewport_cells = int(raw_deepest) * GRID_COLUMNS
    fill_ratio = float(known_cells) / max(1.0, float(viewport_cells))
    if fill_ratio >= 0.35:
        return grid_range
    minimap_items = list(items or [])
    anchor_count = len(_deep_visibility_anchor_items(minimap_items, raw_deepest=int(raw_deepest)))
    depth_low, depth_mid, depth_high = _deep_visibility_band_targets(
        viewport_cells=viewport_cells,
        anchor_count=anchor_count,
        known_cells=int(known_cells),
    )
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    new_low = max(int(low or 0), depth_low)
    new_mid = max(int(mid), depth_mid, new_low + 6)
    new_high = max(int(high if high is not None else new_mid), depth_high, new_mid + 8)
    new_high = min(new_high, WAREHOUSE_CELL_CAPACITY)
    new_high = max(new_high, new_mid)
    if new_low == int(low or 0) and new_mid == int(mid) and new_high == int(high or new_high):
        return grid_range
    _append_note_once(source_notes, AISHA_DEEP_VISIBILITY_GRID_BAND_NOTE)
    if anchor_count >= 2:
        _append_note_once(source_notes, AISHA_DEEP_VISIBILITY_MULTI_ANCHOR_NOTE)
    return (new_low if low is not None else None, new_mid, new_high)


def _geo_row_rightmost_cols(items: list[dict[str, Any]]) -> dict[int, int]:
    right: dict[int, int] = {}
    for item in items:
        top = _item_top_row(item)
        col = _item_left_col(item)
        if top is None or col is None:
            continue
        height = max(1, _safe_int(item.get("height")) or 1)
        width = max(1, _safe_int(item.get("width")) or 1)
        right_col = min(GRID_COLUMNS, int(col) + width - 1)
        for dr in range(height):
            row = int(top) + dr
            if 1 <= row <= WAREHOUSE_ROWS:
                right[row] = max(right.get(row, 0), right_col)
    return right


def _geo_band_floor_cap(
    grid_range: tuple[int | None, int | None, int | None],
    *,
    deepest: int,
    known_cells: int,
    items: list[dict[str, Any]],
) -> tuple[tuple[int | None, int | None, int | None], list[str]]:
    """Strategy D core (band mode): density-aware floor lift + deep-taper high cap."""
    low, mid, high = grid_range
    if mid is None:
        return grid_range, []
    right = _geo_row_rightmost_cols(items)
    occupied_rows = len(right)
    width_env = max(right.values()) if right else 0
    contiguity = occupied_rows / float(deepest) if deepest else 0.0
    band_rows = range(max(1, deepest - GEO_DEEP_BAND_ROWS + 1), deepest + 1)
    band_cols = [right[r] for r in band_rows if r in right]
    deep_max_col = max(band_cols) if band_cols else 0
    taper_loss = sum(max(0, width_env - c) for c in band_cols)

    f_low = GEO_F_LOW_BASE
    if contiguity >= GEO_CONTIGUITY_DENSE:
        f_low += GEO_F_LOW_CONTIGUITY_BONUS
    if deep_max_col >= GEO_DEEP_WIDE_COLS:
        f_low += GEO_F_LOW_DEEP_WIDTH_BONUS
    avg_taper = taper_loss / max(1, len(band_cols))
    if avg_taper >= GEO_AVG_TAPER_STRONG:
        f_low -= GEO_F_LOW_TAPER_STRONG_PENALTY
    elif avg_taper >= GEO_AVG_TAPER_MILD:
        f_low -= GEO_F_LOW_TAPER_MILD_PENALTY
    f_low = max(GEO_F_LOW_MIN, min(GEO_F_LOW_MAX, f_low))

    viewport = deepest * GRID_COLUMNS
    notes: list[str] = []
    geo_floor_low = int(round(viewport * f_low))
    geo_floor_mid = int(round(viewport * min(GEO_F_LOW_MAX, f_low + GEO_F_MID_BONUS)))
    new_low = max(int(low or 0), geo_floor_low)
    new_mid = max(int(mid), geo_floor_mid, new_low + GEO_MIN_SPREAD_LOW)
    if new_low > int(low or 0) or new_mid > int(mid):
        notes.append(AISHA_GEOMETRY_GRID_NOTE)

    geo_cap = int(round(viewport - taper_loss * GEO_TAPER_DAMP))
    # Mid cap: the count prior can push the CENTRAL grid above what the upper density + a tapering
    # (lone / narrow) bottom supports. User (2406 R3 est 109 vs upper-density ~95; 2401 R3 89 vs ~75):
    # "the model is too anchored on the deepest position, pulled off by it, without recognising the
    # upper density only supports ~N cells and below is a low-confidence zone." The geo_cap above (=
    # viewport - taper_loss*damp) already computes that supported value (2406->97, 2401->77 ~ targets),
    # it was just applied to HIGH only. When the deep band genuinely tapers (narrow lone tail), also cap
    # the MID so a lone deep object can't let the count prior inflate the central estimate. Dense-deep
    # bottoms (low taper) keep their count mid. Reversible: AISHA_DISABLE_GEO_MID_CAP=1.
    if (
        os.environ.get("AISHA_DISABLE_GEO_MID_CAP") != "1"
        and avg_taper >= GEO_AVG_TAPER_MILD
        and geo_cap < new_mid
    ):
        new_mid = geo_cap
        if new_low > new_mid:
            # the count prior inflated the whole band above the geometry support; pull the low down
            # too so 保守 doesn't invert above 参考 (downstream min-spread re-opens the band).
            new_low = new_mid
        _append_note_once(notes, AISHA_GEOMETRY_GRID_CAP_NOTE)
    geo_cap = max(geo_cap, new_mid)
    new_high = min(int(high if high is not None else new_mid), geo_cap)
    new_high = max(new_high, new_mid + GEO_MIN_SPREAD_HIGH)
    if new_high < int(high if high is not None else new_high):
        notes.append(AISHA_GEOMETRY_GRID_CAP_NOTE)
    new_high = min(new_high, WAREHOUSE_CELL_CAPACITY)
    return (new_low if low is not None else None, new_mid, new_high), notes


DRAW_VISIBLE_FILL = 0.95  # deepest VISIBLE row is a lower bound on true depth for draw heroes; 0.85 under-shot R4
DRAW_VISIBLE_ANCHOR_NOTE = "draw_visible_depth_anchor"


def apply_draw_visible_anchor_grid_range(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    raw_deepest: int | None,
    known_cells: int,
) -> tuple[int | None, int | None, int | None]:
    """Draw heroes (sparse_early): anchor grid to the VISIBLE deepest row, not footroom-to-expectation.

    Draw heroes reveal by random sampling, so the deepest revealed row is the actionable depth signal
    the player can see. Per-round usefulness (esp. the R4 decision round, where info is most complete)
    requires the estimate to TRACK that visible depth — lifting deep vaults the sparse cap under-rates
    (2403: row13 -> ~110, was 55) and trimming shallow vaults that footroom over-inflates (2402: row6
    -> ~55, was 86). Center = deepest_visible_row * cols * fill; no footroom below the deepest visible
    row. Rollback / A-B: env DISABLE_DRAW_VISIBLE_ANCHOR=1.
    """
    hk = str(hero_key or "").strip().lower()
    spec = LAYOUT_DEPTH_SPECS.get(hk)
    if spec is None or spec.profile_id != "sparse_early":
        return grid_range
    if os.environ.get("DISABLE_DRAW_VISIBLE_ANCHOR") == "1":
        return grid_range
    if raw_deepest is None or int(raw_deepest) < 1:
        return grid_range
    low, _mid, high = grid_range
    # Collapsed band => the engine already knows the total (public total / hard lock). Don't override it.
    if low is not None and high is not None and (int(high) - int(low)) <= 2:
        return grid_range
    anchor = int(round(int(raw_deepest) * GRID_COLUMNS * DRAW_VISIBLE_FILL))
    if anchor <= 0:
        return grid_range
    new_mid = max(anchor, int(known_cells or 0))  # seen cells are a hard floor
    new_low = min(new_mid, max(int(known_cells or 0), int(round(new_mid * 0.85))))
    new_high = min(WAREHOUSE_CELL_CAPACITY, max(int(round(new_mid * 1.15)), new_mid + 3))
    _append_note_once(source_notes, f"{DRAW_VISIBLE_ANCHOR_NOTE}:{anchor}")
    return (new_low if low is not None else None, new_mid, new_high)


def apply_aisha_geometry_grid_range(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
    known_cells: int,
    items: list[dict[str, Any]] | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Geometry-first grid-range pass (strategy D): floor/cap from minimap shape + R1 wide.

    Applied last in the grid chain. Skips when the engine already has a hard total
    (note-locked) or the baseline is collapsed (low≈mid≈high). Does not touch value.
    """
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if os.environ.get("BIDKING_DISABLE_AISHA_GEOMETRY"):  # rollback / A-B verification switch
        return grid_range
    if _aisha_grid_total_hard_locked(source_notes):
        return grid_range
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    if low is not None and high is not None and (int(high) - int(low)) <= GEO_LOCK_SPREAD:
        return grid_range

    minimap_items = list(items or [])
    # R1: white skill only sees a sliver -> conservative wide interval around the count mid.
    if round_no is not None and int(round_no) == 1:
        mid_i = int(mid)
        deep_high = int(round(int(raw_deepest or 0) * GRID_COLUMNS * GEO_R1_WIDE_HIGH_DEEP_FRAC))
        new_low = min(int(low if low is not None else mid_i), int(round(mid_i * GEO_R1_WIDE_LOW_FRAC)))
        new_high = max(
            int(high if high is not None else mid_i),
            int(round(mid_i * GEO_R1_WIDE_HIGH_FRAC)),
            deep_high,
        )
        new_high = min(new_high, WAREHOUSE_CELL_CAPACITY)
        if new_low == int(low if low is not None else mid_i) and new_high == int(high if high is not None else mid_i):
            return grid_range
        _append_note_once(source_notes, AISHA_GEOMETRY_GRID_R1_WIDE_NOTE)
        return (new_low if low is not None else None, mid_i, new_high)

    if raw_deepest is None or int(raw_deepest) < GEO_DEEP_FLOOR_MIN_ROW:
        return grid_range

    (new_low, new_mid, new_high), notes = _geo_band_floor_cap(
        grid_range,
        deepest=int(raw_deepest),
        known_cells=int(known_cells),
        items=minimap_items,
    )
    if not notes:
        return grid_range
    for note in notes:
        _append_note_once(source_notes, note)
    return (new_low, new_mid, new_high)


def apply_late_reveal_grid_residual_cap(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
    known_cells: int,
    count_estimate: int | None,
    items: list[dict[str, Any]] | None = None,
) -> tuple[int | None, int | None, int | None]:
    """Near-complete shallow reveal: bound grid by visible cells + hidden-item residual.

    Root cause (live R4): the footroom/geometry path treats a partially-filled deepest row as a
    full ×10 row and adds phantom cells below, even though the engine already sees ~all of them.
    Once most of the estimated grid is visible, the still-unseen cells are limited by how many
    ITEMS are still hidden (unrevealed / invisible gold-red), not by empty geometric rows.

    Gates (data-driven, 2026-06-16): a partial deepest row and a low count prior do NOT separate
    "shallow ends here" from "deep continues" (deep 2410 also shows a sparse deepest row + low
    count). What separates them is REVEAL COMPLETENESS — known_cells/mid: shallow R4 ~0.8-0.9 vs
    deep 2410 ~0.2-0.3 and shallow R3 ~0.5. So fire only when (a) shallow viewport (raw_deepest<=8),
    (b) round >= 3, and (c) most of the estimated grid is already seen. Deep warehouses (more rows
    still hidden) and mid-game R3 keep their footroom. Shift the band down preserving spread (no
    collapse); floor at known_cells (a hard lower bound — those cells are seen).
    """
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if _aisha_grid_total_hard_locked(source_notes):
        return grid_range
    if round_no is None or int(round_no) < AISHA_RESIDUAL_CAP_MIN_ROUND:
        return grid_range
    # Allow a deeper viewport than the shallow threshold: a scattered deepest row can read deep while
    # the vault is near-fully revealed. The reveal-completeness gate below is the real guard.
    if raw_deepest is None or int(raw_deepest) > AISHA_RESIDUAL_CAP_MAX_DEEPEST:
        return grid_range
    if count_estimate is None or int(count_estimate) <= 0 or int(known_cells) <= 0:
        return grid_range
    low, mid, high = grid_range
    if mid is None or int(mid) <= 0:
        return grid_range
    # Reveal-completeness gate: only trust "known + small residual" once most of the estimated
    # grid is already visible. Deep/early views (lots still hidden) fall below this and keep footroom.
    if float(known_cells) / float(mid) < AISHA_RESIDUAL_CAP_MIN_REVEAL_RATIO:
        return grid_range
    visible_items = sum(1 for it in (items or []) if it.get("row") is not None)
    if visible_items <= 0:
        return grid_range
    per_item = max(float(known_cells) / float(visible_items), AISHA_RESIDUAL_CAP_PER_ITEM_MIN)
    hidden_items = max(0, int(count_estimate) - visible_items - AISHA_RESIDUAL_CAP_COUNT_NOISE)
    cap = max(float(known_cells), float(known_cells) + hidden_items * per_item * AISHA_RESIDUAL_CAP_SLACK)
    cap_i = int(round(cap))
    if cap_i >= int(mid):  # range already within the residual ceiling -> nothing to trim
        return grid_range
    # Shift the band down to center mid on the cap, preserving the original spread (don't collapse).
    delta = int(mid) - cap_i
    lo0 = int(low if low is not None else mid)
    hi0 = int(high if high is not None else mid)
    new_mid = max(cap_i, int(known_cells))
    new_low = max(int(known_cells), lo0 - delta)
    new_low = min(new_low, new_mid)
    new_high = max(new_mid, hi0 - delta)
    if (new_low, new_mid, new_high) == (lo0, int(mid), hi0):
        return grid_range
    _append_note_once(source_notes, f"{AISHA_LATE_REVEAL_RESIDUAL_CAP_NOTE}:{cap_i}")
    return (new_low if low is not None else None, new_mid, new_high)


def ensure_aisha_floating_grid_minimum_spread(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    hero_key: str,
    round_no: int | None,
    raw_deepest: int | None,
) -> tuple[int | None, int | None, int | None]:
    """Aisha UI contract: floating 估总格 must not collapse to a single value without hard total."""
    if str(hero_key or "").strip().lower() != "aisha":
        return grid_range
    if _aisha_grid_total_hard_locked(source_notes):
        return grid_range
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    new_low = int(low if low is not None else mid)
    new_mid = int(mid)
    new_high = int(high if high is not None else mid)
    mid_delta, high_delta, high_cap = _aisha_floating_grid_spread_targets(
        round_no=round_no,
        raw_deepest=raw_deepest,
    )
    new_mid = max(new_mid, new_low + int(mid_delta))
    new_high = max(new_high, new_mid + max(4, int(mid_delta) // 2), new_low + int(high_delta))
    if high_cap is not None:
        new_high = min(new_high, int(high_cap))
    new_high = max(new_high, new_mid)
    if new_low == int(low or new_low) and new_mid == int(mid) and new_high == int(high or new_high):
        return grid_range
    _append_note_once(source_notes, AISHA_FLOATING_GRID_MIN_SPREAD_NOTE)
    return (new_low if low is not None else None, new_mid, new_high)


def apply_count_implied_grid_range_floor(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
    *,
    raw_deepest: int | None = None,
    round_no: int | None = None,
    known_cells: int = 0,
) -> tuple[int | None, int | None, int | None]:
    """Shallow warehouse early rounds still need count floor; sparse R1 reading keeps combo float."""
    floor = count_implied_grid_floor(source_notes)
    if floor is None or floor <= 0 or not _warehouse_is_shallow(raw_deepest=raw_deepest):
        return grid_range
    if (
        round_no is not None
        and int(round_no) <= 1
        and int(known_cells) < 12
        and raw_deepest is not None
        and int(raw_deepest) <= 6
    ):
        return grid_range
    low, mid, high = grid_range
    if mid is None:
        return grid_range
    new_low = max(int(low or 0), int(floor))
    new_mid = max(int(mid), new_low)
    new_high = max(int(high if high is not None else new_mid), new_mid)
    mid_delta, high_delta, high_cap = _aisha_floating_grid_spread_targets(
        round_no=round_no,
        raw_deepest=raw_deepest,
    )
    if new_mid <= new_low:
        new_mid = new_low + max(2, int(mid_delta) // 2)
    if new_high <= new_mid:
        new_high = max(new_mid + 4, new_low + int(high_delta))
    if high_cap is not None:
        new_high = min(new_high, int(high_cap))
    new_high = max(new_high, new_mid)
    if new_low == int(low or 0) and new_mid == int(mid) and new_high == int(high or 0):
        return grid_range
    return (new_low if low is not None else None, new_mid, new_high)


def _footroom_band_known_threshold(round_no: int) -> int:
    if int(round_no) <= 1:
        return 4
    if int(round_no) == 2:
        return 15
    return 42


def _footroom_band_low_spread(round_no: int) -> int:
    return {1: 8, 2: 8, 3: 8, 4: 10, 5: 12}.get(int(round_no), 10)


def _footroom_band_high_cap(*, hint: int, round_no: int) -> int:
    tail = {1: 12, 2: 15, 3: 12, 4: 15, 5: 18}.get(int(round_no), 15)
    return int(hint) + int(tail)


def apply_count_implied_grid_floor(
    *,
    hero_key: str,
    total_count: int | None,
    total_grid_target: float | None,
    source_notes: list[str],
    max_round: int = 2,
    round_no: int | None = None,
    raw_deepest: int | None = None,
) -> float | None:
    """When item count is known-ish but minimap footroom is shallow, floor grid by physical avg cells.

    v0.1.9: default R1–R2 only; R3+ samples use layout band without this floor.
    """
    if round_no is not None and int(round_no) > int(max_round):
        return total_grid_target
    if str(hero_key or "").strip().lower() != "aisha":
        return total_grid_target
    if total_count is None or int(total_count) <= 0:
        return total_grid_target
    implied = float(int(total_count)) * _count_implied_cells_per_item(
        raw_deepest=raw_deepest, round_no=round_no
    )
    baseline = float(total_grid_target if total_grid_target is not None else 0.0)
    capped = min(implied, float(WAREHOUSE_CELL_CAPACITY))
    if _warehouse_is_shallow(raw_deepest=raw_deepest) and round_no is not None:
        capped = min(
            capped,
            _shallow_visible_grid_cap(
                round_no=int(round_no),
                known_cells=0,
            ),
        )
    if _warehouse_is_shallow(raw_deepest=raw_deepest) and total_grid_target is not None:
        if capped + 0.5 < float(total_grid_target):
            _append_note_once(
                source_notes,
                f"{AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE}:{int(round(capped))}",
            )
            return capped
    if implied <= baseline + 0.5:
        return total_grid_target
    _append_note_once(
        source_notes,
        f"{AISHA_COUNT_IMPLIED_GRID_FLOOR_NOTE}:{int(round(capped))}",
    )
    return capped


def apply_layout_depth_hints(
    *,
    hero_key: str,
    round_no: int | None,
    total_grid_target: float | None,
    source_notes: list[str],
    items: list[dict[str, Any]],
    layout_mode: str,
    hard_total_locked: bool,
    total_count: int | None = None,
) -> float | None:
    spec = layout_depth_spec_for_hero(hero_key)
    if spec is None or round_no is None or hard_total_locked:
        return total_grid_target
    if not items:
        return total_grid_target

    raw_deepest = _deepest_minimap_bottom_row(items)
    updated = _apply_early_viewport_hint(
        spec=spec,
        round_no=int(round_no),
        total_grid_target=total_grid_target,
        source_notes=source_notes,
        items=items,
    )
    updated = _apply_footroom_hint(
        spec=spec,
        round_no=int(round_no),
        total_grid_target=updated,
        source_notes=source_notes,
        items=items,
        layout_mode=layout_mode,
        total_count=total_count,
    )
    floored = apply_count_implied_grid_floor(
        hero_key=hero_key,
        total_count=total_count,
        total_grid_target=updated,
        source_notes=source_notes,
        round_no=int(round_no),
        raw_deepest=raw_deepest,
    )
    if layout_mode == LAYOUT_MODE_BAND:
        return normalize_grid_cell_target(updated)
    return normalize_grid_cell_target(floored)
