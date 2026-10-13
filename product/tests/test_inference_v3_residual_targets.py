from bidking_lab.inference.v3 import (
    BucketFeasibleSummary,
    FeasibleSummaryReport,
    assess_q6_residual_targets,
    empty_residual_target_candidate_flat_dict,
)


def _summary(
    *,
    count_total: int | None = 7,
    cells_total: int | None = 16,
    buckets: tuple[BucketFeasibleSummary, ...] | None = None,
    conflicts: tuple[str, ...] = (),
) -> FeasibleSummaryReport:
    return FeasibleSummaryReport(
        session_total_count_exact=count_total,
        session_total_cells_exact=cells_total,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=buckets
        if buckets is not None
        else (
            BucketFeasibleSummary(quality=1, count_exact=1, cells_exact=2),
            BucketFeasibleSummary(quality=2, count_exact=1, cells_exact=2),
            BucketFeasibleSummary(quality=3, count_exact=1, cells_exact=2),
            BucketFeasibleSummary(quality=4, count_exact=1, cells_exact=2),
            BucketFeasibleSummary(quality=5, count_exact=1, cells_exact=2),
            BucketFeasibleSummary(quality=6, count_floor=1, cells_floor=4),
        ),
        conflicts=conflicts,
    )


def test_residual_targets_derive_q6_count_and_cells_shadow_only() -> None:
    report = assess_q6_residual_targets(_summary())
    flat = report.to_flat_dict()

    assert report.candidate is True
    assert report.active is False
    assert report.derived_fields == ("count", "cells")
    assert flat["v3_rtc_affects_bid"] is False
    assert flat["v3_rtc_active"] is False
    assert flat["v3_rtc_q6_count_status"] == "derived"
    assert flat["v3_rtc_q6_count_value"] == 2
    assert flat["v3_rtc_q6_count_q6_floor"] == 1
    assert flat["v3_rtc_q6_cells_status"] == "derived"
    assert flat["v3_rtc_q6_cells_value"] == 6
    assert flat["v3_rtc_q6_cells_q6_floor"] == 4
    assert flat["v3_rtc_q6_value_status"] == "missing_total_exact"


def test_residual_targets_require_complete_non_q6_exact_partition() -> None:
    report = assess_q6_residual_targets(
        _summary(
            buckets=(
                BucketFeasibleSummary(quality=1, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=2, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=3, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=4, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=6, count_floor=1, cells_floor=4),
            )
        )
    )

    assert report.count.status == "missing_non_q6_exact"
    assert report.count.missing_non_q6_qualities == (5,)
    assert report.cells.status == "missing_non_q6_exact"
    assert report.cells.missing_non_q6_qualities == (5,)
    assert report.candidate is False


def test_residual_targets_reject_residual_below_q6_floor() -> None:
    report = assess_q6_residual_targets(
        _summary(
            buckets=(
                BucketFeasibleSummary(quality=1, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=2, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=3, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=4, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=5, count_exact=1, cells_exact=2),
                BucketFeasibleSummary(quality=6, count_floor=3, cells_floor=8),
            )
        )
    )

    assert report.count.status == "residual_below_q6_floor"
    assert report.count.value == 2
    assert report.cells.status == "residual_below_q6_floor"
    assert report.cells.value == 6
    assert report.candidate is False


def test_empty_residual_target_fields_are_unavailable() -> None:
    flat = empty_residual_target_candidate_flat_dict()

    assert flat["v3_rtc_available"] is False
    assert flat["v3_rtc_ready"] is False
    assert flat["v3_rtc_affects_bid"] is False
    assert flat["v3_rtc_active"] is False
    assert flat["v3_rtc_q6_count_status"] == "unavailable"
