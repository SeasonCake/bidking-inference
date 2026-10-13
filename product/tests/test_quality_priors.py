"""Tests for per-cell value priors and value-sum inference."""

from __future__ import annotations

import pytest

from bidking_lab.inference.quality_priors import (
    PER_CELL_VALUE_DEFAULT,
    PER_CELL_VALUE_HUGE,
    estimate_total_cells,
    per_cell_value,
    value_consistency_score,
)


def test_default_per_cell_values_match_design() -> None:
    """Pin the user-blessed numbers so they're not silently retuned."""
    assert PER_CELL_VALUE_DEFAULT[4] == 2500    # 紫
    assert PER_CELL_VALUE_DEFAULT[5] == 9400    # 金
    assert PER_CELL_VALUE_DEFAULT[6] == 50000   # 红 default


def test_huge_red_per_cell_lower_than_default() -> None:
    """Huge red items have systematically lower per-cell value."""
    assert PER_CELL_VALUE_HUGE[6] < PER_CELL_VALUE_DEFAULT[6]
    assert PER_CELL_VALUE_HUGE[6] == 30000


def test_per_cell_value_huge_flag() -> None:
    assert per_cell_value(6, huge=False) == 50000
    assert per_cell_value(6, huge=True) == 30000
    assert per_cell_value(4, huge=True) == 2500
    # Gold huge (12-18 cells range, weighted median ~7000/cell).
    assert per_cell_value(5, huge=True) == 7000


def test_estimate_total_cells_purple_with_huge_avoids_double_count() -> None:
    """Without the purple huge prior the function double-counted huge cells.

    value_sum=95k + 1 purple huge (16 cells, ~40k each via 2500/cell huge prior):
    - huge_value = 16 × 2500 = 40_000
    - remaining = 95_000 - 40_000 = 55_000
    - non_huge_cells = round(55_000 / 2500) = 22
    - total = 22 + 16 = 38
    (Old buggy result was 38 + 16 = 54.)
    """
    est = estimate_total_cells(quality=4, value_sum=95_000, huge_cells=16)
    assert est == 38


def test_per_cell_value_rejects_unknown_quality() -> None:
    with pytest.raises(ValueError):
        per_cell_value(99)


# --- estimate_total_cells: replays the shipwreck R4 screenshot inference ---

def test_estimate_total_cells_purple_shipwreck_r4() -> None:
    """Replay: 沉船 R4, 优品估价 = 86490 → expected ~35 cells (matches 35/14)."""
    est = estimate_total_cells(quality=4, value_sum=86490)
    # 86490 / 2500 = 34.596 → rounds to 35
    assert est == 35


def test_estimate_total_cells_gold_no_huge() -> None:
    est = estimate_total_cells(quality=5, value_sum=94000)
    # 94000 / 9400 = exactly 10
    assert est == 10


def test_estimate_total_cells_red_with_huge_items() -> None:
    """Red bucket: value_sum=2,000,000 with 1 巨物 (4×4=16 cells)."""
    # User identifies 1 huge red item (16 cells).
    # Huge value estimate = 30000 * 16 = 480,000
    # Remaining bucket value = 2,000,000 - 480,000 = 1,520,000
    # Remaining cells = 1,520,000 / 50,000 = 30.4 → 30
    # Total estimate = 30 + 16 = 46
    est = estimate_total_cells(
        quality=6, value_sum=2_000_000, huge_cells=16,
    )
    assert est == 46


def test_estimate_total_cells_with_user_supplied_huge_value() -> None:
    """User passes the actual huge-item value (overrides the prior)."""
    est = estimate_total_cells(
        quality=6, value_sum=2_000_000, huge_cells=16, huge_value=840_000,
    )
    # Remaining bucket = 2,000,000 - 840,000 = 1,160,000
    # Remaining cells = 1,160,000 / 50,000 = 23.2 → 23
    # Total estimate = 23 + 16 = 39
    assert est == 39


def test_estimate_total_cells_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        estimate_total_cells(quality=4, value_sum=-1)


# --- value_consistency_score ---

def test_score_zero_when_value_sum_matches_perfectly() -> None:
    """86490 / 2500 ≈ 35 cells. A candidate of 35 should score very low."""
    s = value_consistency_score(quality=4, candidate_total_cells=35, value_sum=86490)
    assert s == pytest.approx(0.0)


def test_score_higher_for_distant_candidate() -> None:
    s_close = value_consistency_score(quality=4, candidate_total_cells=35, value_sum=86490)
    s_far = value_consistency_score(quality=4, candidate_total_cells=20, value_sum=86490)
    assert s_far > s_close


def test_score_zero_when_in_value_range() -> None:
    """Range constraint: candidate value must lie inside [lo, hi]."""
    # quality=6, candidate 30 cells → value ≈ 1,500,000
    s = value_consistency_score(
        quality=6, candidate_total_cells=30, value_range=(1_000_000, 2_000_000)
    )
    assert s == 0.0


def test_score_nonzero_outside_value_range() -> None:
    s = value_consistency_score(
        quality=6, candidate_total_cells=10, value_range=(1_000_000, 2_000_000)
    )
    # candidate value = 500,000 → below the lower bound
    assert s > 0


def test_score_returns_zero_with_no_constraints() -> None:
    """No value info → all candidates tie at score=0; let the cells layer rank."""
    s = value_consistency_score(quality=4, candidate_total_cells=20)
    assert s == 0.0


def test_score_huge_flag_changes_estimate() -> None:
    """With huge_cells supplied, the estimate shifts toward the huge prior."""
    s_no_huge = value_consistency_score(
        quality=6, candidate_total_cells=46, value_sum=2_000_000
    )
    s_with_huge = value_consistency_score(
        quality=6,
        candidate_total_cells=46,
        value_sum=2_000_000,
        huge_cells=16,
    )
    # With 16 huge cells, the estimate becomes 46 → s_with_huge should be 0
    # (exact match). Without huge accounting, the estimate is 40 → s_no_huge > 0.
    assert s_with_huge == 0.0
    assert s_no_huge > 0
