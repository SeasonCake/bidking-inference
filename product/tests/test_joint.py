"""Tests for joint multi-bucket posterior."""

from __future__ import annotations

from bidking_lab.inference.display import parse_reading
from bidking_lab.inference.joint import (
    JointHypothesis,
    joint_top_k_for_session,
)
from bidking_lab.inference.observation import (
    QualityBucketObs,
    SessionObs,
)


def test_joint_returns_empty_when_no_buckets() -> None:
    session = SessionObs(map_id=2510, hero="ethan", warehouse_total_cells=100)
    assert joint_top_k_for_session(session) == []


def test_joint_single_bucket_matches_per_bucket_top1() -> None:
    """With only one observed bucket, joint top-1 = per-bucket top-1."""
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=159,
        buckets={
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=86_490,
            ),
        },
    )
    out = joint_top_k_for_session(session, k=3)
    assert out
    top = out[0]
    assert isinstance(top, JointHypothesis)
    purple = top.per_bucket[4]
    assert (purple.total_cells, purple.count) == (35, 14)
    assert top.total_cells == 35


def test_joint_warehouse_slack_filters_overbudget_combos() -> None:
    """Combos whose summed cells exceed capacity+slack are pruned."""
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=50,            # very tight
        buckets={
            4: QualityBucketObs(quality=4, avg_cells=parse_reading("2.5")),
            3: QualityBucketObs(quality=3, avg_cells=parse_reading("2")),
        },
    )
    out = joint_top_k_for_session(session, k=10, warehouse_slack=5)
    assert out
    for hyp in out:
        # Every surviving hypothesis must fit within capacity + slack.
        assert hyp.total_cells <= 50 + 5


def test_joint_allows_tolerance_for_approximate_ethan_warehouse() -> None:
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells_approx=50,
        warehouse_total_cells_tolerance=15,
        buckets={
            3: QualityBucketObs(quality=3, total_cells=60),
        },
    )

    out = joint_top_k_for_session(session, k=3, warehouse_slack=5)

    assert out
    assert out[0].per_bucket[3].total_cells == 60
    assert out[0].total_cells <= 65


def test_exact_warehouse_does_not_use_approximate_tolerance() -> None:
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=50,
        warehouse_total_cells_tolerance=15,
        buckets={
            3: QualityBucketObs(quality=3, total_cells=60),
        },
    )

    assert joint_top_k_for_session(session, k=3, warehouse_slack=5) == []


def test_joint_prefers_combos_under_capacity_no_penalty() -> None:
    """If two hypotheses score equal on bucket composite, the one *under*
    capacity should win on warehouse_penalty (it stays 0)."""
    # Build a session where two purple candidates have similar bucket
    # composite but one fits the capacity exactly and another overshoots.
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=40,
        buckets={
            4: QualityBucketObs(quality=4, avg_cells=parse_reading("2.5")),
        },
    )
    out = joint_top_k_for_session(session, k=5, warehouse_slack=20)
    # Top-1's warehouse_penalty must be 0 if any under-capacity
    # candidate exists at all.
    assert any(h.warehouse_penalty == 0.0 for h in out)
    # And out is sorted by composite (lowest first).
    composites = [h.composite for h in out]
    assert composites == sorted(composites)


def test_joint_beats_greedy_when_top1_purple_misleads() -> None:
    """Construct a scenario where the per-bucket top-1 for purple is
    cells-locally optimal but doesn't fit the warehouse with the
    observed blue scan. The joint search must demote that top-1.

    Setup:
      * warehouse exactly 50
      * blue scan locked at 30 cells
      * purple avg=2.5 → candidates include (10, 4), (15, 6), (20, 8),
        (25, 10), ...
      * Only (10, 4), (15, 6), (20, 8) can coexist with blue=30 under
        warehouse_slack=0 → joint top-1 must be one of these, not (35, 14)
    """
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=50,
        buckets={
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=37_500,    # ~ 15 cells per quality_priors
            ),
            3: QualityBucketObs(quality=3, total_cells=30),
        },
    )
    out = joint_top_k_for_session(session, k=5, warehouse_slack=2)
    assert out
    top = out[0]
    # Top hypothesis must keep blue=30 (only candidate) and purple total
    # cells must be <= 50 - 30 + slack = 22.
    assert top.per_bucket[3].total_cells == 30
    assert top.per_bucket[4].total_cells <= 22


def test_joint_three_buckets_runs_fast() -> None:
    """Smoke: 3 observed buckets × default top-8 finishes well under 1s."""
    import time

    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=159,
        buckets={
            6: QualityBucketObs(
                quality=6,
                avg_cells=parse_reading("4"),
                value_range=(1_500_000, 4_000_000),
                huge_band="1",
            ),
            5: QualityBucketObs(
                quality=5,
                avg_cells=parse_reading("3"),
                value_sum=180_000,
            ),
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=86_490,
            ),
        },
    )
    t0 = time.perf_counter()
    out = joint_top_k_for_session(session, k=5)
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"joint search took {dt:.3f}s (expected < 1s)"
    assert out
    # All hypotheses cover all three buckets.
    for hyp in out:
        assert set(hyp.per_bucket.keys()) == {4, 5, 6}


def test_joint_results_sorted_by_composite() -> None:
    session = SessionObs(
        map_id=2510,
        hero="ethan",
        warehouse_total_cells=159,
        buckets={
            4: QualityBucketObs(
                quality=4,
                avg_cells=parse_reading("2.5"),
                value_sum=86_490,
            ),
            3: QualityBucketObs(quality=3, total_cells=18),
        },
    )
    out = joint_top_k_for_session(session, k=10)
    assert out
    composites = [h.composite for h in out]
    assert composites == sorted(composites)
