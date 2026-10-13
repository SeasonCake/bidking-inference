"""Tests for the decimal-display model used by the X品均格 inference.

Display rule **confirmed by 2026-05-15 playtest screenshots**: game uses
truncation (floor) at 2 decimals, with trailing zeros stripped iff the
floored value equals the exact ratio.

Calibration data:

* ``17 / 7 = 2.4285…`` → game shows ``"2.42"``
  (mansion R3, 优品均格 + map-given 紫品总格=17)
* ``35 / 14 = 2.5`` exactly → game shows ``"2.5"``
  (shipwreck R4, 优品均格)
* ``32 / 11 = 2.909…`` → game shows ``"2.90"`` (user's original example)
* ``55 / 16 = 3.4375`` → game shows ``"3.43"``
  (shipwreck R3, 优品均格 = 3.43, one of the consistent candidates)

All numeric work uses :class:`fractions.Fraction` to avoid float drift.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from bidking_lab.inference.display import (
    Reading,
    enumerate_candidates,
    filter_by_warehouse_size,
    format_value,
    is_compatible,
    parse_reading,
    reading_info_bits,
)


def test_parse_integer_reading() -> None:
    r = parse_reading("3")
    assert r.is_integer
    assert r.n_decimals == 0
    assert r.value == Fraction(3, 1)
    assert not r.trailing_zero


def test_parse_one_decimal_no_trailing_zero() -> None:
    r = parse_reading("2.9")
    assert r.value == Fraction(29, 10)
    assert r.n_decimals == 1
    assert not r.trailing_zero


def test_parse_two_decimal_trailing_zero() -> None:
    r = parse_reading("2.90")
    assert r.value == Fraction(29, 10)  # 29/10 == 290/100
    assert r.n_decimals == 2
    assert r.trailing_zero


def test_parse_two_decimal_no_trailing_zero() -> None:
    r = parse_reading("2.34")
    assert r.value == Fraction(234, 100)
    assert r.n_decimals == 2
    assert not r.trailing_zero


def test_parse_three_decimal_defensive() -> None:
    """Parser stays lenient even though the game uses at most 2 decimals.

    The 2026-05-15 screenshots confirm the game caps display at 2 dp;
    if we ever encounter a 3-decimal string it's likely a typo, but
    the parser doesn't reject — caller decides what to do.
    """
    r = parse_reading("2.345")
    assert r.value == Fraction(2345, 1000)
    assert r.n_decimals == 3
    assert not r.trailing_zero


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_reading("two point nine")


# --- format_value: screenshot-calibrated ---

def test_format_exact_integer() -> None:
    assert format_value(6, 2) == "3"   # 6/2 = 3 exactly


def test_format_exact_one_decimal_trim() -> None:
    assert format_value(29, 10) == "2.9"
    assert format_value(58, 20) == "2.9"  # multiple (m,n) → same display


def test_format_exact_two_decimals_trim_all_but_one() -> None:
    assert format_value(7, 4) == "1.75"


def test_format_screenshot_mansion_17_over_7() -> None:
    # Mansion R3 screenshot: 紫品总格=17, 件数=7 → "约2.42格"
    assert format_value(17, 7) == "2.42"


def test_format_screenshot_shipwreck_35_over_14() -> None:
    # Shipwreck R4 screenshot: candidate (35, 14) → 2.5 exact, trimmed
    assert format_value(35, 14) == "2.5"


def test_format_user_example_32_over_11() -> None:
    # The user's original example: 32/11 → "2.90" under truncation
    assert format_value(32, 11) == "2.90"


def test_format_screenshot_shipwreck_55_over_16() -> None:
    # Shipwreck R3 screenshot: candidate (55, 16) for "3.43"
    assert format_value(55, 16) == "3.43"


# --- is_compatible ---

def test_compatible_exact_one_decimal() -> None:
    r = parse_reading("2.9")
    assert is_compatible(r, 29, 10)
    assert is_compatible(r, 58, 20)
    assert is_compatible(r, 87, 30)
    # 32/11 ≈ 2.909 → truncates to 2.90 → display would be "2.90", not "2.9"
    assert not is_compatible(r, 32, 11)


def test_compatible_trailing_zero_under_truncation() -> None:
    r = parse_reading("2.90")
    # 32/11 = 2.909… → floor at d=2 = 2.90 ≠ exact → keep trailing 0 → "2.90"
    assert is_compatible(r, 32, 11)
    # 29/10 = 2.9 exactly → would have displayed "2.9", not "2.90"
    assert not is_compatible(r, 29, 10)


def test_compatible_screenshot_mansion_2_42() -> None:
    """Mansion R3: 紫品 17 总格, 优品均格 = "约2.42格" → 7 紫品件数."""
    r = parse_reading("2.42")
    assert is_compatible(r, 17, 7)         # confirmed by screenshot
    assert not is_compatible(r, 17, 6)     # 17/6 = 2.833 → "2.83"
    assert not is_compatible(r, 17, 8)     # 17/8 = 2.125 → "2.12"


def test_compatible_screenshot_shipwreck_2_5() -> None:
    """Shipwreck R4: 优品均格 = "约2.5格" → exact 2.5 multiples."""
    r = parse_reading("2.5")
    assert is_compatible(r, 5, 2)
    assert is_compatible(r, 35, 14)        # the value-sum-disambiguated answer
    assert not is_compatible(r, 32, 11)    # 32/11 = 2.909 → "2.90"


def test_compatible_screenshot_shipwreck_3_43() -> None:
    """Shipwreck R3: 优品均格 = "约3.43格", warehouse total = 159, blue+wg = 63.

    Without the warehouse prior, multiple (m, n) match 3.43 → triage by
    the prior in :func:`filter_by_warehouse_size`.
    """
    r = parse_reading("3.43")
    assert is_compatible(r, 55, 16)        # 55/16 = 3.4375 → floor 3.43
    assert is_compatible(r, 79, 23)        # 79/23 = 3.4347 → floor 3.43
    assert is_compatible(r, 103, 30)       # 103/30 = 3.4333 → floor 3.43
    # Confirm a few negatives
    assert not is_compatible(r, 24, 7)     # 24/7 = 3.4285 → floor 3.42


def test_compatible_two_decimals() -> None:
    r = parse_reading("2.34")
    # 117/50 = 2.34 exactly → trim would have made it "2.34" not "2.34"
    # Actually "2.34" has no trailing zero AND not integer → require exact
    assert is_compatible(r, 117, 50)
    # 7/3 = 2.333… → floor 2.33 → display "2.33" not "2.34"
    assert not is_compatible(r, 7, 3)


def test_compatible_integer_requires_exact_ratio() -> None:
    """Bare integer "3" can only mean ratio is EXACTLY 3.

    If ratio were e.g. 10/3 = 3.333…, game shows "3.33" not "3".
    """
    r = parse_reading("3")
    assert is_compatible(r, 3, 1)
    assert is_compatible(r, 6, 2)
    assert is_compatible(r, 9, 3)
    assert is_compatible(r, 30, 10)
    # 10/3 = 3.333... → game shows "3.33" not "3" → reject
    assert not is_compatible(r, 10, 3)


# --- enumerate_candidates & info bits ---

def test_enumerate_exact_one_decimal_gives_all_multiples() -> None:
    r = parse_reading("2.9")
    cands = enumerate_candidates(r, max_count=30, max_total_cells=90)
    # All (29k, 10k) pairs within bounds
    assert (29, 10) in cands
    assert (58, 20) in cands
    assert (87, 30) in cands


def test_integer_reading_has_high_info_bits() -> None:
    r_int = parse_reading("3")
    r_dec = parse_reading("2.5")  # 5/2 → 10/4, 15/6, 20/8, 25/10, 30/12
    bits_int = reading_info_bits(r_int, max_count=20, max_total_cells=100)
    bits_dec = reading_info_bits(r_dec, max_count=20, max_total_cells=100)
    # Integer reading admits more (m, n) pairs than a 1-decimal reading.
    assert bits_int > bits_dec


def test_warehouse_prior_disambiguates_3_43() -> None:
    """Reproduces the shipwreck R3 inference: warehouse=159, blue=35,
    white+green=28 → purple budget ≤ 96. Only 2 candidates survive."""
    r = parse_reading("3.43")
    cands = enumerate_candidates(r, max_count=40, max_total_cells=120)
    # Filter by purple budget: total cells - (blue + wg) - gold/red estimate
    purple_budget = 159 - 35 - 28  # 96
    pruned = filter_by_warehouse_size(cands, warehouse_size=purple_budget)
    purple_counts = {n for _, n in pruned}
    # Expected survivors: (55, 16), (79, 23). Reject (103, 30) > 96.
    assert 16 in purple_counts
    assert 23 in purple_counts
    assert 30 not in purple_counts


def test_trailing_zero_reading_is_more_informative_than_bare() -> None:
    r_bare = parse_reading("2.9")
    r_tail = parse_reading("2.90")
    # Both narrow down a lot, but the trailing-zero version excludes the
    # exact-2.9 multiples — strictly different candidate sets.
    cands_bare = set(enumerate_candidates(r_bare, max_count=20, max_total_cells=60))
    cands_tail = set(enumerate_candidates(r_tail, max_count=20, max_total_cells=60))
    assert cands_bare.isdisjoint(cands_tail)


# --- warehouse-size prior ---

def test_warehouse_prior_prunes_large_total_cells() -> None:
    r = parse_reading("2.9")
    cands = enumerate_candidates(r, max_count=20, max_total_cells=60)
    pruned = filter_by_warehouse_size(cands, warehouse_size=40)
    assert all(tc <= 40 for tc, _ in pruned)
    assert len(pruned) < len(cands)


def test_warehouse_prior_subtracts_known_shape_cells() -> None:
    r = parse_reading("2.9")
    cands = enumerate_candidates(r, max_count=20, max_total_cells=60)
    # Player has identified a 5x4 = 20-cell blue item; remaining capacity
    # for the rest of the blue bucket is warehouse - 20.
    pruned = filter_by_warehouse_size(cands, warehouse_size=60, shape_known_cells=20)
    assert all(tc <= 40 for tc, _ in pruned)


# --- avg_value fractional-cents → integer total silver ---


def test_avg_value_integer_leak_distance() -> None:
    from bidking_lab.inference.display import (
        integer_total_leak_distance,
    )

    assert integer_total_leak_distance(39_539.17, 6) < 0.05
    assert integer_total_leak_distance(39_539.17, 2) > 0.3


def test_best_count_for_gold_avg_hint() -> None:
    from bidking_lab.inference.display import (
        best_count_for_avg_value_integer_leak,
    )

    assert best_count_for_avg_value_integer_leak(39_539.17) == 6
    assert best_count_for_avg_value_integer_leak(32_507.6) == 5
    assert best_count_for_avg_value_integer_leak(32_507) is None
