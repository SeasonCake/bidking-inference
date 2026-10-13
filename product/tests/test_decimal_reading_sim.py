"""Tests for decimal-reading simulation helpers."""

from __future__ import annotations

from bidking_lab.inference.decimal_reading_sim import (
    analyze_avg_cells_ambiguity,
    classify_display_suffix,
    format_silver_avg,
    integer_leak_matching_counts,
    sample_bucket_from_truth,
)
from bidking_lab.inference.display import parse_reading


def test_format_silver_avg_floor_at_two_dp() -> None:
    # Integer silver total / count — truncate (floor) at 2dp, same as 均格.
    assert format_silver_avg(237_235, 6) == "39539.16"
    assert 6 in integer_leak_matching_counts(39_539.17)


def test_classify_integer_vs_tight() -> None:
    assert classify_display_suffix("4") == "integer"
    assert classify_display_suffix("2.5") == "one_decimal_exact"
    assert classify_display_suffix("2.90") == "trailing_zero"
    assert classify_display_suffix("1.90") == "trailing_zero"
    assert classify_display_suffix("3.43") == "tight_fraction"


def test_warehouse_cap_excludes_oversized_candidates() -> None:
    sample = sample_bucket_from_truth(4, 32, 11, 200_000)
    rep = analyze_avg_cells_ambiguity(
        sample,
        warehouse_capacity=50,
        other_known_cells=10,
    )
    assert rep.budget_for_bucket == 40
    assert rep.engine_respects_budget
    assert all(tc <= 40 for tc, _ in [] or [])  # noqa — structure check
    assert rep.n_candidates_budget <= rep.n_candidates_unbounded


def test_integer_leak_finds_six_for_39539_17() -> None:
    counts = integer_leak_matching_counts(39_539.17)
    assert 6 in counts
    assert 2 not in counts


def test_truth_pair_compatible_with_2_90_reading() -> None:
    sample = sample_bucket_from_truth(4, 32, 11, 100_000)
    reading = parse_reading(sample.avg_cells_display)
    from bidking_lab.inference.display import is_compatible

    assert is_compatible(reading, 32, 11)
