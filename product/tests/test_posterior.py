"""Tests for the per-bucket posterior filter + statistics."""

from __future__ import annotations

import numpy as np

from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.observation import QualityBucketObs, SessionObs, candidates_for_bucket, parse_reading
from bidking_lab.inference.posterior import (
    _describe_constraints,
    _fallback_hard_buckets,
    adaptive_filter,
    bucket_posterior_stats,
    compute_analytical_estimate,
    filter_truths_by_obs,
)


def _make_truth(
    *,
    warehouse: int,
    q1: tuple[int, int, int, int] | None = None,
    q4: tuple[int, int, int, int] | None = None,
    q6: tuple[int, int, int, int] | None = None,
    map_id: int = 9999,
) -> SessionTruth:
    """Tiny factory: each ``(cells, count, value, huge)`` tuple seeds one bucket."""
    buckets: dict[int, BucketTruth] = {}
    for q, t in [(1, q1), (4, q4), (6, q6)]:
        if t is None:
            continue
        cells, count, value, huge = t
        buckets[q] = BucketTruth(
            quality=q, count=count, total_cells=cells, value_sum=value, huge_count=huge,
        )
    return SessionTruth(
        map_id=map_id, map_name="test", warehouse_total_cells=warehouse, buckets=buckets,
    )


def _obs(warehouse: int, **bucket_kwargs) -> SessionObs:
    """Tiny factory: ``q4=QualityBucketObs(quality=4, total_cells=...)`` etc."""
    return SessionObs(
        map_id=9999, hero="ethan",
        warehouse_total_cells=warehouse,
        buckets={b.quality: b for b in bucket_kwargs.values()},
    )


# -------------------- filter_truths_by_obs --------------------

class TestFilterByObs:
    def test_warehouse_only_passes_all_within_tolerance(self):
        truths = [
            _make_truth(warehouse=70),
            _make_truth(warehouse=72),
            _make_truth(warehouse=85),  # outside ±8
        ]
        obs = _obs(72)
        kept = filter_truths_by_obs(truths, obs, warehouse_tol=8)
        assert len(kept) == 2

    def test_approximate_warehouse_uses_declared_tolerance(self):
        truths = [
            _make_truth(warehouse=112),
            _make_truth(warehouse=128),
            _make_truth(warehouse=140),
        ]
        obs = SessionObs(
            map_id=9999,
            hero="ethan",
            warehouse_total_cells_approx=112,
            warehouse_total_cells_tolerance=18,
        )

        kept = filter_truths_by_obs(truths, obs, warehouse_tol=8)

        assert [truth.warehouse_total_cells for truth in kept] == [112, 128]

    def test_adaptive_filter_reports_declared_warehouse_tolerance(self):
        obs = SessionObs(
            map_id=9999,
            hero="ethan",
            warehouse_total_cells_approx=112,
            warehouse_total_cells_tolerance=18,
        )

        result = adaptive_filter([_make_truth(warehouse=128)], obs, min_samples=1)

        assert result.warehouse_tol == 18

    def test_purple_cells_filter_keeps_close_matches(self):
        truths = [
            _make_truth(warehouse=72, q4=(19, 6, 60_000, 0)),    # exact
            _make_truth(warehouse=72, q4=(21, 7, 62_000, 0)),    # +2 cells, OK at tol=2
            _make_truth(warehouse=72, q4=(30, 10, 90_000, 1)),   # too many cells
        ]
        purple_obs = QualityBucketObs(quality=4, total_cells=19)
        obs = _obs(72, p=purple_obs)
        kept = filter_truths_by_obs(truths, obs, cells_tol=2)
        assert len(kept) == 2

    def test_red_cells_zero_filters_out_any_red(self):
        """The headline bug: user types red.total_cells=0, we must drop red-bearing truths."""
        truths = [
            _make_truth(warehouse=72, q6=(0, 0, 0, 0)),          # no red, keep
            _make_truth(warehouse=72, q6=(16, 1, 300_000, 1)),   # red巨物, drop
            _make_truth(warehouse=72, q6=(4, 2, 80_000, 0)),     # some red, drop at tol=2
        ]
        red_obs = QualityBucketObs(quality=6, total_cells=0)
        obs = _obs(72, r=red_obs)
        kept = filter_truths_by_obs(truths, obs, cells_tol=2)
        assert len(kept) == 1
        assert kept[0].buckets[6].total_cells == 0

    def test_value_sum_relative_tolerance(self):
        truths = [
            _make_truth(warehouse=72, q4=(20, 6, 60_000, 0)),
            _make_truth(warehouse=72, q4=(20, 6, 66_000, 0)),    # +10% from 60k, edge
            _make_truth(warehouse=72, q4=(20, 6, 80_000, 0)),    # +33%, drop
        ]
        purple_obs = QualityBucketObs(quality=4, value_sum=60_000)
        obs = _obs(72, p=purple_obs)
        kept = filter_truths_by_obs(truths, obs, value_rel_tol=0.10)
        assert 1 <= len(kept) <= 2  # edge depends on float

    def test_huge_band_filter(self):
        truths = [
            _make_truth(warehouse=72, q6=(16, 1, 300_000, 1)),   # 1 huge — in band "1"
            _make_truth(warehouse=72, q6=(32, 2, 600_000, 2)),   # 2 huge — NOT in band "1"
            _make_truth(warehouse=72, q6=(0, 0, 0, 0)),          # 0 huge — NOT in band "1"
        ]
        red_obs = QualityBucketObs(quality=6, huge_band="1")
        obs = _obs(72, r=red_obs)
        kept = filter_truths_by_obs(truths, obs)
        assert len(kept) == 1
        assert kept[0].buckets[6].huge_count == 1

    def test_unfilled_bucket_imposes_no_constraint(self):
        """If user didn't fill obs for q=6, every truth should be considered."""
        truths = [
            _make_truth(warehouse=72, q6=(0, 0, 0, 0)),
            _make_truth(warehouse=72, q6=(16, 1, 300_000, 1)),
        ]
        obs = _obs(72)
        kept = filter_truths_by_obs(truths, obs)
        assert len(kept) == 2

    def test_total_cells_zero_is_exact_assertion_not_fuzzy(self):
        """Player asserting ``red.total_cells = 0`` means EXACTLY zero,
        never "approximately zero". The tolerance must not widen it.
        """
        truths = [
            _make_truth(warehouse=72, q6=(0, 0, 0, 0)),     # exact zero, keep
            _make_truth(warehouse=72, q6=(1, 1, 5_000, 0)), # 1 cell — must be dropped
            _make_truth(warehouse=72, q6=(5, 2, 30_000, 0)),
            _make_truth(warehouse=72, q6=(8, 3, 80_000, 0)),  # within +8 tol, still drop
        ]
        red_obs = QualityBucketObs(quality=6, total_cells=0)
        obs = _obs(72, r=red_obs)
        kept = filter_truths_by_obs(truths, obs, cells_tol=8)  # loosest level
        assert len(kept) == 1
        assert kept[0].buckets[6].total_cells == 0

    def test_count_zero_is_also_exact_assertion(self):
        truths = [
            _make_truth(warehouse=72, q6=(0, 0, 0, 0)),
            _make_truth(warehouse=72, q6=(0, 1, 0, 0)),    # count 1, drop
            _make_truth(warehouse=72, q6=(0, 3, 0, 0)),
        ]
        red_obs = QualityBucketObs(quality=6, count=0)
        obs = _obs(72, r=red_obs)
        kept = filter_truths_by_obs(truths, obs, count_tol=5)
        assert len(kept) == 1

    def test_avg_cells_only_does_not_filter_mc_truths(self):
        """MC filter ignores avg_cells; preview enumeration may still use it."""
        truths = [
            _make_truth(warehouse=72, q4=(20, 8, 60_000, 0)),
            _make_truth(warehouse=72, q4=(21, 7, 62_000, 0)),
        ]
        purple_obs = QualityBucketObs(quality=4, avg_cells=parse_reading("2.88"))
        obs = _obs(72, p=purple_obs)
        kept = filter_truths_by_obs(truths, obs)
        assert len(kept) == 2

    def test_value_sum_still_filters_when_preview_avg_inconsistent(self):
        """Hard value_sum reaches MC even if avg-only preview would warn."""
        truths = [
            _make_truth(warehouse=123, q4=(20, 8, 50_630, 0)),
            _make_truth(warehouse=123, q4=(20, 8, 80_000, 0)),
        ]
        purple_obs = QualityBucketObs(
            quality=4,
            value_sum=50_630,
            avg_cells=parse_reading("1.90"),
            count=8,
        )
        cands = candidates_for_bucket(
            purple_obs, warehouse_capacity=123, other_known_cells=63,
        )
        assert cands == [] or all(c.total_cells >= c.count for c in cands)

        obs = _obs(123, p=QualityBucketObs(quality=4, value_sum=50_630))
        kept = filter_truths_by_obs(truths, obs, warehouse_tol=8, value_rel_tol=0.02)
        assert len(kept) == 1
        assert kept[0].buckets[4].value_sum == 50_630


# -------------------- adaptive_filter --------------------

class TestFallbackHardBuckets:
    def test_preserves_huge_cells_override(self):
        gold = QualityBucketObs(quality=5, huge_band="1", huge_cells_override=18)
        obs = _obs(72, g=gold)
        hard = _fallback_hard_buckets(obs)
        assert hard[5].huge_cells_override == 18
        assert hard[5].huge_cells_per_item() == 18


class TestAdaptiveFilter:
    def test_strict_when_enough_samples(self):
        truths = [_make_truth(warehouse=72, q4=(19, 6, 60_000, 0)) for _ in range(50)]
        purple_obs = QualityBucketObs(quality=4, total_cells=19)
        obs = _obs(72, p=purple_obs)
        result = adaptive_filter(truths, obs, min_samples=30)
        assert result.tol_level == 0
        assert result.low_confidence is False
        assert result.n_final == 50

    def test_widens_when_strict_too_tight(self):
        """Make truths sit at q4 cells = 25; strict ±2 fails, ±4 fails, ±8 succeeds."""
        truths = [_make_truth(warehouse=72, q4=(25, 7, 70_000, 0)) for _ in range(50)]
        purple_obs = QualityBucketObs(quality=4, total_cells=18)  # 7 away
        obs = _obs(72, p=purple_obs)
        result = adaptive_filter(truths, obs, min_samples=30)
        assert result.tol_level == 2
        assert result.low_confidence is True
        assert result.n_final == 50

    def test_returns_loosest_result_when_even_loose_fails(self):
        """Truths are 20 cells away — even ±8 won't help. Return what we have, flag low_confidence."""
        truths = [_make_truth(warehouse=72, q4=(40, 10, 200_000, 1)) for _ in range(10)]
        purple_obs = QualityBucketObs(quality=4, total_cells=18)
        obs = _obs(72, p=purple_obs)
        result = adaptive_filter(truths, obs, min_samples=30)
        assert result.low_confidence is True
        assert result.n_final == 0  # nothing matches at any tolerance


# -------------------- bucket_posterior_stats --------------------

class TestBucketPosteriorStats:
    def test_basic_quantiles(self):
        rng = np.random.default_rng(42)
        truths = []
        for _ in range(200):
            cells = int(rng.integers(0, 30))
            count = int(rng.integers(0, 10))
            value = int(rng.integers(0, 500_000))
            truths.append(_make_truth(warehouse=72, q6=(cells, count, value, 0)))
        stats = bucket_posterior_stats(truths, quality=6)
        assert stats.n == 200
        assert 0 <= stats.cells_p10 <= stats.cells_p50 <= stats.cells_p90 <= 30
        assert 0.0 <= stats.p_empty <= 1.0

    def test_zero_truths_returns_zeros(self):
        stats = bucket_posterior_stats([], quality=6)
        assert stats.n == 0
        assert stats.cells_p50 == 0
        assert stats.p_empty == 0.0

    def test_p_empty_when_no_red(self):
        truths = [_make_truth(warehouse=72) for _ in range(20)]  # no q=6 buckets
        stats = bucket_posterior_stats(truths, quality=6)
        assert stats.n == 20
        assert stats.cells_p50 == 0
        assert stats.p_empty == 1.0


class TestDescribeConstraints:
    def test_includes_total_item_count_and_buckets(self):
        obs = SessionObs(
            map_id=2403,
            hero="ethan",
            warehouse_total_cells=159,
            total_item_count=32,
            buckets={
                1: QualityBucketObs(quality=1, total_cells=40),
                3: QualityBucketObs(quality=3, total_cells=12),
                4: QualityBucketObs(
                    quality=4,
                    total_cells=19,
                    count=6,
                    value_sum=60_000,
                    huge_band="1-2",
                ),
            },
        )
        parts = _describe_constraints(obs)
        assert parts[0] == "warehouse=159"
        assert "items≈32±" in parts[1]
        assert any("q=1(cells=40)" in p for p in parts)
        assert any("q=3(cells=12)" in p for p in parts)
        assert any(
            "q=4(cells=19, count=6, value≈60,000, huge=1-2)" in p for p in parts
        )

    def test_describes_approximate_warehouse_tolerance(self):
        obs = SessionObs(
            map_id=2403,
            hero="ethan",
            warehouse_total_cells_approx=112,
            warehouse_total_cells_tolerance=18,
        )

        assert _describe_constraints(obs)[0] == "warehouse≈112±18"


def test_analytical_uses_gold_avg_without_cells_not_all_red() -> None:
    """Gold avg_value alone should infer gold cells, not dump remainder into red."""
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        warehouse_total_cells=123,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=40),
            3: QualityBucketObs(quality=3, total_cells=26),
            4: QualityBucketObs(quality=4, total_cells=18),
            5: QualityBucketObs(quality=5, avg_value=32_507.6),
        },
    )
    est = compute_analytical_estimate(obs)
    assert est is not None
    assert 5 in est.per_bucket
    assert est.per_bucket[5][1] > 0
    assert "金" in est.breakdown_text
    assert est.red_cells_inferred < 123 - 84 or 6 not in est.per_bucket


def test_analytical_gold_avg_value_is_per_item_not_per_cell() -> None:
    """Map hint「金品均价」uses integer-leak count (6) × per-item price."""
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        warehouse_total_cells=120,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=40),
            3: QualityBucketObs(quality=3, total_cells=30),
            4: QualityBucketObs(quality=4, total_cells=30),
            5: QualityBucketObs(quality=5, avg_value=39_539.17),
        },
    )
    est = compute_analytical_estimate(obs)
    assert est is not None
    lo, mid, hi = est.per_bucket[5]
    assert mid == round(39_539.17 * 6)
    assert "·6件×39539/件" in est.breakdown_text


def test_analytical_uses_joint_bucket_capacity_constraint() -> None:
    """Purple/gold local top-1 candidates must not oversubscribe warehouse."""
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        warehouse_total_cells=60,
        buckets={
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=86_490,
            ),
            5: QualityBucketObs(
                quality=5,
                avg_cells=parse_reading("2.5"),
                value_sum=329_000,
            ),
        },
    )
    est = compute_analytical_estimate(obs)
    assert est is not None
    assert "紫 30格" in est.breakdown_text
    assert "金 30格" in est.breakdown_text
