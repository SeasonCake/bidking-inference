from bidking_lab.inference.v3.prior_robustness import assess_prior_robustness
from bidking_lab.inference.v3.summary import (
    BucketFeasibleSummary,
    FeasibleSummaryReport,
)


def _summary(*, q6_cells_floor: int = 0) -> FeasibleSummaryReport:
    buckets = (
        BucketFeasibleSummary(
            quality=6,
            cells_floor=q6_cells_floor,
        ),
    ) if q6_cells_floor else ()
    return FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=q6_cells_floor,
        known_value_floor=0,
        buckets=buckets,
    )


def _prior_fields() -> dict[str, object]:
    return {
        "v3_prior_available": True,
        "v3_prior_error": None,
        "v3_prior_expected_count": 10,
        "v3_prior_expected_cells": 36,
        "v3_prior_expected_value": 480_000,
        "v3_prior_q6_expected_count": 1,
        "v3_prior_q6_expected_cells": 8,
        "v3_prior_q6_expected_value": 220_000,
    }


def test_prior_robustness_ok_for_strict_supported_prior() -> None:
    report = assess_prior_robustness(
        map_id=2401,
        map_family="villa",
        summary=_summary(),
        prior_fields=_prior_fields(),
        posterior_fields={
            "v3_post_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "strict",
        },
    )

    assert report.status == "ok"
    assert report.prior_usable is True
    assert report.prior_trusted is True
    assert report.fallback_mode == "normal_prior"


def test_prior_robustness_blocks_missing_activity_prior() -> None:
    report = assess_prior_robustness(
        map_id=2526,
        map_family="shipwreck",
        summary=_summary(),
        prior_fields={
            "v3_prior_available": False,
            "v3_prior_error": "KeyError",
        },
        posterior_fields={
            "v3_post_available": True,
            "v3_post_ready": False,
        },
    )

    assert report.status == "prior_unavailable"
    assert report.prior_usable is False
    assert report.prior_trusted is False
    assert report.activity_candidate is True
    assert report.fallback_mode == "missing_prior_truth_only"
    assert "activity_map_id_candidate" in report.reasons


def test_prior_robustness_marks_evidence_above_prior_as_stressed() -> None:
    report = assess_prior_robustness(
        map_id=2506,
        map_family="shipwreck",
        summary=_summary(q6_cells_floor=32),
        prior_fields=_prior_fields(),
        posterior_fields={
            "v3_post_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "summary_likelihood",
        },
    )

    assert report.status == "prior_stressed"
    assert report.prior_usable is True
    assert report.prior_trusted is False
    assert report.fallback_mode == "summary_likelihood_conservative"
    assert report.prior_stress_score >= 1
    assert "q6_cells_above_prior" in report.reasons
