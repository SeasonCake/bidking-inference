from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3 import BucketFeasibleSummary, FeasibleSummaryReport
from bidking_lab.inference.v3.formal_value_sampler import sample_formal_value_report
from bidking_lab.inference.v3.posterior import V3PosteriorReport


def _posterior() -> V3PosteriorReport:
    return V3PosteriorReport(
        map_id=2401,
        map_name="test",
        n_total=10,
        n_matched=10,
        n_strict_matched=2,
        match_scope="summary_likelihood",
        q6_present_rate=1.0,
        total_cells=QuantileSummary(40, 50, 60),
        total_value=QuantileSummary(600_000, 700_000, 900_000),
        formal_decision_value=QuantileSummary(600_000, 700_000, 900_000),
        tail_replacement_decision_value=QuantileSummary(600_000, 700_000, 900_000),
        q6_count=QuantileSummary(1, 1, 2),
        q6_cells=QuantileSummary(6, 8, 16),
        q6_value=QuantileSummary(300_000, 400_000, 600_000),
        q6_formal_decision_value=QuantileSummary(300_000, 400_000, 600_000),
        q6_tail_replacement_decision_value=QuantileSummary(300_000, 400_000, 600_000),
    )


def _summary(
    *,
    total_count_floor: int = 0,
    total_cells_floor: int = 0,
    total_value_floor: int = 0,
    q6_cells_floor: int = 0,
    q6_value_floor: int = 0,
) -> FeasibleSummaryReport:
    return FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=total_count_floor,
        known_cells_floor=total_cells_floor,
        known_value_floor=total_value_floor,
        buckets=(
            BucketFeasibleSummary(
                quality=6,
                cells_floor=q6_cells_floor,
                value_floor=q6_value_floor,
            ),
        ),
    )


def test_formal_value_sampler_value_floor_candidate_is_shadow_only() -> None:
    report = sample_formal_value_report(
        _posterior(),
        summary=_summary(total_value_floor=1_000_000, q6_value_floor=800_000),
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_expected_count": 20,
            "v3_prior_expected_cells": 80,
            "v3_prior_expected_value": 400_000,
            "v3_prior_q6_expected_cells": 6,
            "v3_prior_q6_expected_value": 300_000,
            "v3_prior_items_per_session_max": 40,
        },
    )
    flat = report.to_flat_dict()

    assert report.candidate is True
    assert report.active is False
    assert flat["v3_fv_affects_bid"] is False
    assert flat["v3_fv_status"] == "watch_only_value_floor_candidate"
    assert flat["v3_fv_stress_class"] == "value_floor_stress"
    assert flat["v3_fv_formal_decision_value_p50"] == 1_000_000
    assert flat["v3_fv_formal_decision_value_p90"] == 1_000_000
    assert flat["v3_fv_q6_formal_decision_value_p50"] == 800_000
    assert flat["v3_fv_total_value_target_prior_ratio"] == 2.5
    assert flat["v3_fv_q6_value_target_prior_ratio"] == 2.666667


def test_formal_value_sampler_capacity_drift_does_not_upshift_value() -> None:
    report = sample_formal_value_report(
        _posterior(),
        summary=_summary(total_count_floor=55, total_cells_floor=180, q6_cells_floor=30),
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_expected_count": 20,
            "v3_prior_expected_cells": 80,
            "v3_prior_expected_value": 700_000,
            "v3_prior_q6_expected_cells": 6,
            "v3_prior_items_per_session_max": 40,
        },
    )
    flat = report.to_flat_dict()

    assert report.candidate is False
    assert flat["v3_fv_status"] == "watch_capacity_cells_drift"
    assert flat["v3_fv_source"] == "baseline"
    assert flat["v3_fv_formal_decision_value_p50"] == 700_000
    assert flat["v3_fv_capacity_flags"] == "target_count_above_prior_max"
    assert "capacity_cells_drift" in flat["v3_fv_stress_class"]
    assert "q6_cells_floor_stress" in flat["v3_fv_stress_class"]


def test_formal_value_sampler_mixed_value_floor_is_guarded() -> None:
    report = sample_formal_value_report(
        _posterior(),
        summary=_summary(
            total_value_floor=1_000_000,
            q6_cells_floor=30,
            q6_value_floor=800_000,
        ),
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_expected_count": 20,
            "v3_prior_expected_cells": 80,
            "v3_prior_expected_value": 400_000,
            "v3_prior_q6_expected_cells": 6,
            "v3_prior_q6_expected_value": 300_000,
            "v3_prior_items_per_session_max": 40,
        },
    )
    flat = report.to_flat_dict()

    assert report.candidate is False
    assert report.mixed_value_floor_watch is True
    assert flat["v3_fv_candidate"] is False
    assert flat["v3_fv_mixed_value_floor_watch"] is True
    assert flat["v3_fv_status"] == "watch_mixed_value_floor_guarded"
    assert flat["v3_fv_source"] == "baseline"
    assert flat["v3_fv_formal_decision_value_p50"] == 700_000
    assert flat["v3_fv_q6_formal_decision_value_p50"] == 400_000
