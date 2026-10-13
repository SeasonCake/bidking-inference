"""Geometry-first grid-range adjustment prototype for Aisha (post-process only).

NOT wired into the engine yet. This module takes the engine's baseline
total_grid_range plus the minimap footprint geometry and proposes a revised
range driven primarily by spatial layout:

  - Floor (low/mid lift): the deepest occupied row is hard, monotone-ish
    evidence that the warehouse spans at least that many rows. Floor scales as
    deepest_row * GRID_COLUMNS * f_low, where f_low is density-aware:
    dense/contiguous depth -> high factor (big warehouse); an isolated single
    deep item -> low factor (could be one item placed alone at the bottom, so
    we do NOT over-lift). This intentionally allows downward revision as info
    clarifies (per user: regression is acceptable when evidence sharpens).

  - Cap (high tighten): when the deep band is narrow (items only occupy a few
    columns while upper rows are wider), the warehouse tapers; the high tail is
    pulled down toward deepest_row * observed_width instead of deepest_row * 10.

All coefficients are v0 guesses for eyeball calibration. They are grouped and
commented so the basis of each magic number is explicit.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from layout_depth_policy import (  # noqa: E402
    GRID_COLUMNS,
    WAREHOUSE_ROWS,
    _item_bottom_row,
    _item_top_row,
)
from minimap_audit_matrix import decode_item_col  # noqa: E402

# --- v0 calibration constants (eyeball-tunable) -----------------------------
# Floor density factor band: fraction of deepest_row*10 used as the low bound.
F_LOW_BASE = 0.55           # bare "depth exists" floor when depth looks isolated/sparse
F_LOW_CONTIGUITY_BONUS = 0.20  # most rows 1..deepest occupied -> warehouse is filled top-down
F_LOW_DEEP_WIDTH_BONUS = 0.15  # deep band itself is wide (>=DEEP_WIDE_COLS) -> not a lone item
F_LOW_MIN, F_LOW_MAX = 0.45, 0.92
F_MID_BONUS = 0.06          # mid floor sits a touch above low floor
CONTIGUITY_DENSE = 0.60     # >= this share of rows occupied counts as "filled top-down"
DEEP_WIDE_COLS = 6          # deep band max column >= this counts as a real wide deep row
DEEP_BAND_ROWS = 3          # how many rows up from deepest define the "deep band"
# Taper penalty: a deep band that is narrow on average (tall narrow column) is likely
# an isolated/single deep item, not a full deep warehouse -> pull the floor factor down.
AVG_TAPER_STRONG = 5.0      # avg (env-col) gap per occupied deep row -> strong narrowness
AVG_TAPER_MILD = 3.0
F_LOW_TAPER_STRONG_PENALTY = 0.20
F_LOW_TAPER_MILD_PENALTY = 0.10
# Per-row deep-fill penalty (mode="perrow"): instead of band-max width, judge the deep
# band by the AVERAGE occupied-cell fraction of its rows, so a sparse narrow tail (low
# fill) pulls the factor down even when one row in the band happens to reach a far column.
DEEP_FILL_DENSE = 0.55      # avg occupied fraction >= this -> deep rows look full
DEEP_FILL_SPARSE = 0.35     # avg occupied fraction <= this -> deep rows look like a thin tail
F_LOW_FILL_DENSE_BONUS = 0.15
F_LOW_FILL_SPARSE_PENALTY = 0.20
# Cap (taper) constants.
TAPER_DAMP = 0.70           # only trust part of the apparent narrowness as real taper
MIN_SPREAD_LOW = 3          # keep a floating spread (Aisha UI contract: not a single value)
MIN_SPREAD_HIGH = 4
DEEP_FLOOR_MIN_ROW = 6      # below this deepest row, skip geometric floor (too shallow to bound)
# A near-collapsed baseline (low≈mid≈high) means the engine already knows the total
# (bridge/public/settlement/high-tier lock). Geometry must not override a hard total.
LOCK_SPREAD = 2
# R1 conservative wide-interval (r1_wide=True): R1 only sees the white skill, so commit to
# a wide band around the count-prior mid instead of a confident narrow guess.
R1_WIDE_LOW_FRAC = 0.78
R1_WIDE_HIGH_FRAC = 1.30
R1_WIDE_HIGH_DEEP_FRAC = 1.00  # high also at least deepest_row*10 when depth is visible


@dataclass
class GeometryFeatures:
    deepest: int | None
    occupied_rows: int
    contiguity: float           # occupied_rows / deepest
    width_env: int              # max occupied column across all rows (apparent width)
    deep_band_rows: tuple[int, ...]
    deep_max_col: int           # max occupied column within the deep band
    deep_occ_rows: int          # occupied rows within the deep band
    taper_loss: int             # Σ(width_env - row_right) over occupied deep-band rows
    deep_rowfill: float         # avg occupied-cell fraction over deep-band occupied rows
    known_cells: int
    f_low: float = 0.0
    notes: list[str] = field(default_factory=list)


def _row_rightmost_cols(items: list[dict[str, Any]]) -> dict[int, int]:
    """Map row -> rightmost occupied column (1-based), expanding multi-cell footprints."""
    right: dict[int, int] = {}
    for item in items:
        top = _item_top_row(item)
        col = decode_item_col(item)
        if top is None or col is None:
            continue
        height = max(1, int(item.get("height") or 1))
        width = max(1, int(item.get("width") or 1))
        right_col = int(col) + width - 1
        for dr in range(height):
            row = int(top) + dr
            if 1 <= row <= WAREHOUSE_ROWS:
                right[row] = max(right.get(row, 0), min(GRID_COLUMNS, right_col))
    return right


def _row_cell_counts(items: list[dict[str, Any]]) -> dict[int, int]:
    """Map row -> number of distinct occupied columns (1-based), expanding footprints."""
    cells: dict[int, set[int]] = {}
    for item in items:
        top = _item_top_row(item)
        col = decode_item_col(item)
        if top is None or col is None:
            continue
        height = max(1, int(item.get("height") or 1))
        width = max(1, int(item.get("width") or 1))
        for dr in range(height):
            row = int(top) + dr
            if not (1 <= row <= WAREHOUSE_ROWS):
                continue
            bucket = cells.setdefault(row, set())
            for dc in range(width):
                c = int(col) + dc
                if 1 <= c <= GRID_COLUMNS:
                    bucket.add(c)
    return {row: len(cols) for row, cols in cells.items()}


def compute_features(items: list[dict[str, Any]], *, known_cells: int) -> GeometryFeatures:
    deepest = None
    bottoms = [b for it in items if (b := _item_bottom_row(it)) is not None]
    if bottoms:
        deepest = max(bottoms)
    right = _row_rightmost_cols(items)
    occupied_rows = len(right)
    width_env = max(right.values()) if right else 0
    counts = _row_cell_counts(items)
    if deepest is None:
        return GeometryFeatures(
            deepest=None, occupied_rows=occupied_rows, contiguity=0.0,
            width_env=width_env, deep_band_rows=(), deep_max_col=0,
            deep_occ_rows=0, taper_loss=0, deep_rowfill=0.0, known_cells=known_cells,
        )
    contiguity = occupied_rows / float(deepest) if deepest else 0.0
    band_rows = tuple(r for r in range(max(1, deepest - DEEP_BAND_ROWS + 1), deepest + 1))
    band_occ = {r: right[r] for r in band_rows if r in right}
    deep_max_col = max(band_occ.values()) if band_occ else 0
    taper_loss = sum(max(0, width_env - col) for col in band_occ.values())
    band_counts = [counts[r] for r in band_rows if r in counts]
    deep_rowfill = (sum(band_counts) / len(band_counts) / GRID_COLUMNS) if band_counts else 0.0
    return GeometryFeatures(
        deepest=deepest,
        occupied_rows=occupied_rows,
        contiguity=contiguity,
        width_env=width_env,
        deep_band_rows=band_rows,
        deep_max_col=deep_max_col,
        deep_occ_rows=len(band_occ),
        taper_loss=taper_loss,
        deep_rowfill=deep_rowfill,
        known_cells=known_cells,
    )


def _f_low(features: GeometryFeatures, mode: str) -> float:
    f = F_LOW_BASE
    if features.contiguity >= CONTIGUITY_DENSE:
        f += F_LOW_CONTIGUITY_BONUS
    if mode == "perrow":
        # Judge depth by avg occupied fraction of the deep rows themselves.
        if features.deep_rowfill >= DEEP_FILL_DENSE:
            f += F_LOW_FILL_DENSE_BONUS
        elif features.deep_rowfill <= DEEP_FILL_SPARSE:
            f -= F_LOW_FILL_SPARSE_PENALTY
    else:  # "band": deep width by furthest column + average taper narrowness
        if features.deep_max_col >= DEEP_WIDE_COLS:
            f += F_LOW_DEEP_WIDTH_BONUS
        avg_taper = features.taper_loss / max(1, features.deep_occ_rows)
        if avg_taper >= AVG_TAPER_STRONG:
            f -= F_LOW_TAPER_STRONG_PENALTY
        elif avg_taper >= AVG_TAPER_MILD:
            f -= F_LOW_TAPER_MILD_PENALTY
    return max(F_LOW_MIN, min(F_LOW_MAX, f))


def _r1_wide(
    baseline: tuple[int | None, int | None, int | None],
    features: GeometryFeatures,
) -> tuple[tuple[int | None, int | None, int | None], list[str]]:
    low, mid, high = baseline
    if mid is None:
        return baseline, []
    mid_i = int(mid)
    deep_high = int(round((features.deepest or 0) * GRID_COLUMNS * R1_WIDE_HIGH_DEEP_FRAC))
    new_low = min(int(low if low is not None else mid_i), int(round(mid_i * R1_WIDE_LOW_FRAC)))
    new_high = max(int(high if high is not None else mid_i), int(round(mid_i * R1_WIDE_HIGH_FRAC)), deep_high)
    new_high = min(new_high, WAREHOUSE_ROWS * GRID_COLUMNS)
    return (new_low, mid_i, new_high), ["r1_wide"]


def adjust_range(
    baseline: tuple[int | None, int | None, int | None],
    features: GeometryFeatures,
    *,
    round_no: int | None = None,
    mode: str = "band",
    r1_wide: bool = False,
) -> tuple[tuple[int | None, int | None, int | None], list[str]]:
    """Return (proposed_range, applied_notes). Pure geometry post-process.

    mode: "band" (deep width by furthest column) or "perrow" (deep avg occupied fill).
    r1_wide: when round_no==1, replace with a conservative wide interval.
    """
    low, mid, high = baseline
    if mid is None or features.deepest is None:
        return baseline, []
    if low is not None and high is not None and (int(high) - int(low)) <= LOCK_SPREAD:
        return baseline, ["geo_skip_locked"]
    if r1_wide and round_no == 1:
        return _r1_wide(baseline, features)
    deepest = int(features.deepest)
    if deepest < DEEP_FLOOR_MIN_ROW:
        return baseline, ["geo_skip_shallow"]

    notes: list[str] = []
    viewport = deepest * GRID_COLUMNS
    f_low = _f_low(features, mode)
    features.f_low = f_low

    geo_floor_low = int(round(viewport * f_low))
    geo_floor_mid = int(round(viewport * min(F_LOW_MAX, f_low + F_MID_BONUS)))

    new_low = max(int(low or 0), geo_floor_low)
    new_mid = max(int(mid), geo_floor_mid, new_low + MIN_SPREAD_LOW)
    if new_low > int(low or 0) or new_mid > int(mid):
        notes.append(f"geo_floor:f_low={f_low:.2f}->low{geo_floor_low}/mid{geo_floor_mid}")

    # Cap: geometric hard max is deepest*10; tighten by observed deep taper.
    geo_cap = int(round(viewport - features.taper_loss * TAPER_DAMP))
    geo_cap = max(geo_cap, new_mid)  # never cap below the mid we just set
    new_high = min(int(high if high is not None else new_mid), geo_cap)
    new_high = max(new_high, new_mid + MIN_SPREAD_HIGH)
    if new_high < int(high if high is not None else new_high):
        notes.append(f"geo_cap:viewport{viewport}-taper{features.taper_loss}->{geo_cap}")

    new_high = min(new_high, WAREHOUSE_ROWS * GRID_COLUMNS)
    return (new_low, new_mid, new_high), notes
