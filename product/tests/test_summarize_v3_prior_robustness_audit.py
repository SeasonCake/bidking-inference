import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_prior_robustness_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_prior_robustness_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ready_row(
    *,
    map_id: int,
    pred: int,
    truth: int,
    p90: int,
    stress_score: int,
    reasons: str,
    status: str = "prior_stressed",
    trusted: bool = False,
    activity: bool = False,
    post_ready: bool = True,
    scope: str = "strict",
) -> dict[str, object]:
    return {
        "status": "ready",
        "map_id": map_id,
        "hero": "aisha",
        "round": 2,
        "evidence_profile_key": "shape+layout",
        "hero_map_evidence_profile": f"aisha|{map_id}|shape+layout",
        "v3_truth_decision_available": True,
        "v3_post_ready": post_ready,
        "v3_post_match_scope": scope,
        "v3_truth_formal_decision_value": truth,
        "v3_post_formal_decision_value_p50": pred,
        "v3_post_formal_decision_value_p90": p90,
        "v3_truth_q6_count": 3,
        "v3_post_q6_count_p50": 1,
        "v3_truth_q6_cells": 10,
        "v3_post_q6_cells_p50": 4,
        "v3_truth_q6_raw_value": 300,
        "v3_truth_total_cells": 40,
        "v3_truth_item_count": 6,
        "v3_post_q6_value_p50": 100,
        "v3_post_total_cells_p50": 32,
        "v3_post_total_cells_p90": 42,
        "v3_prior_q6_expected_count": 1,
        "v3_prior_q6_expected_cells": 4,
        "v3_prior_q6_expected_value": 100,
        "v3_prior_expected_count": 3,
        "v3_prior_expected_cells": 20,
        "v3_prior_expected_value": 500,
        "v3_prior_items_per_session_min": 2,
        "v3_prior_items_per_session_max": 5,
        "v3_summary_session_total_cells_exact": None,
        "v3_summary_known_cells_floor": 30,
        "v3_summary_session_total_count_exact": None,
        "v3_summary_known_count_floor": 6,
        "v3_summary_known_value_floor": 300,
        "v3_summary_q6_count_floor": 3,
        "v3_summary_q6_cells_floor": 10,
        "v3_summary_q6_value_floor": 300,
        "numeric_constraints": 3,
        "item_anchors": 1,
        "shape_anchors": 4,
        "quality_floor_anchors": 1,
        "v3_robust_status": status,
        "v3_robust_prior_usable": not activity,
        "v3_robust_prior_trusted": trusted,
        "v3_robust_fallback_mode": (
            "missing_prior_truth_only" if activity else "strict_with_prior_stress"
        ),
        "v3_robust_activity_candidate": activity,
        "v3_robust_prior_stress_score": stress_score,
        "v3_robust_reasons": reasons,
    }


def test_prior_robustness_audit_groups_stress_and_activity() -> None:
    module = _load_module()
    rows = [
        _ready_row(
            map_id=2506,
            pred=80,
            truth=100,
            p90=110,
            stress_score=2,
            reasons="q6_count_above_prior;q6_cells_above_prior",
        ),
        {
            **_ready_row(
                map_id=2506,
                pred=160,
                truth=200,
                p90=210,
                stress_score=2,
                reasons="q6_count_above_prior;q6_cells_above_prior",
            ),
            "v3_prior_q6_expected_count": 2,
            "v3_prior_q6_expected_cells": 6,
            "v3_summary_q6_count_floor": 4,
            "v3_summary_q6_cells_floor": 12,
        },
        _ready_row(
            map_id=2401,
            pred=100,
            truth=100,
            p90=120,
            stress_score=0,
            reasons="summary_likelihood_fallback",
            status="weak_prior_fallback",
            scope="summary_likelihood",
        ),
        _ready_row(
            map_id=2526,
            pred=0,
            truth=0,
            p90=0,
            stress_score=0,
            reasons="prior_error=KeyError;activity_map_id_candidate",
            status="prior_unavailable",
            activity=True,
            post_ready=False,
            scope="none",
        ),
    ]

    by_map = module.summarize_prior_robustness(rows, "map_id")
    by_reason = module.summarize_prior_robustness(rows, "v3_robust_reason")

    stressed = next(row for row in by_map if row["value"] == "2506")
    assert stressed["ready"] == 2
    assert stressed["posterior_ready"] == 2
    assert stressed["metric_rows"] == 2
    assert stressed["prior_stressed"] == 2
    assert stressed["prior_trusted"] == 0
    assert stressed["reason_counts"] == {
        "q6_cells_above_prior": 2,
        "q6_count_above_prior": 2,
    }
    assert stressed["formal_p50_mae"] == 30
    assert stressed["formal_p50_bias"] == -30
    assert stressed["formal_p50_below_rate"] == 1.0
    assert stressed["formal_p90_coverage"] == 1.0
    assert stressed["q6_count_target_prior_ratio_avg"] == 2.5
    assert stressed["q6_count_target_prior_ratio_max"] == 3.0
    assert stressed["q6_cells_target_prior_ratio_avg"] == 2.25
    assert "prior_stressed" in stressed["flags"]
    assert "high_below" in stressed["flags"]

    activity = next(row for row in by_map if row["value"] == "2526")
    assert activity["metric_rows"] == 0
    assert activity["posterior_ready"] == 0
    assert activity["activity_candidate"] == 1
    assert activity["prior_usable"] == 0
    assert activity["fallback_counts"] == {"missing_prior_truth_only": 1}
    assert activity["posterior_scope_counts"] == {}
    assert "activity_or_new_table" in activity["flags"]
    assert "no_metric_rows" in activity["flags"]

    q6_count_reason = next(
        row for row in by_reason if row["value"] == "q6_count_above_prior"
    )
    assert q6_count_reason["ready"] == 2
    assert q6_count_reason["prior_stressed"] == 2
    assert q6_count_reason["maps"] == {"2506": 2}

    details = module.summarize_prior_stress_details(rows)
    assert len(details) == 2
    first = next(row for row in details if row["q6_cells"]["target"] == 10)
    assert first["map_id"] == 2506
    assert first["total_cells"]["source"] == "floor"
    assert first["total_cells"]["target"] == 30
    assert first["total_cells"]["target_prior_ratio"] == 1.5
    assert first["total_cells"]["target_truth_delta"] == -10
    assert first["total_cells"]["post_p50_target_delta"] == 2
    assert first["total_cells"]["post_p50_truth_delta"] == -8
    assert first["q6_cells"]["source"] == "floor"
    assert first["q6_cells"]["target"] == 10
    assert first["q6_cells"]["target_prior_ratio"] == 2.5
    assert first["q6_cells"]["post_p50_target_delta"] == -6
    assert first["item_count_capacity"]["total_count_source"] == "floor"
    assert first["item_count_capacity"]["total_count_target"] == 6
    assert first["item_count_capacity"]["truth_item_count"] == 6
    assert first["item_count_capacity"]["prior_items_per_session_max"] == 5
    assert first["item_count_capacity"]["target_prior_max_delta"] == 1
    assert first["item_count_capacity"]["truth_prior_max_delta"] == 1
    assert first["item_count_capacity"]["target_truth_delta"] == 0
    assert first["item_count_capacity"]["target_prior_max_ratio"] == 1.2
    assert first["item_count_capacity"]["truth_prior_max_ratio"] == 1.2
    assert first["item_count_capacity"]["cases"] == ["direct_prior_max_conflict"]
    assert "target_count_above_prior_max" in first["item_count_capacity"]["flags"]
    assert "truth_count_above_prior_max" in first["item_count_capacity"]["flags"]
    assert "capacity_direct_prior_max_conflict" in first["consistency_classes"]
    assert "total_cells_floor_below_truth" in first["consistency_classes"]
    assert "q6_cells_floor_matches_truth" in first["consistency_classes"]
    assert "q6_value_floor_matches_truth" in first["consistency_classes"]
    assert first["consistency_bucket"] == "hard_capacity_conflict"
    assert "posterior_total_cells_under_truth" in first["flags"]
    assert "posterior_q6_cells_under_truth" in first["flags"]
    assert "posterior_q6_cells_below_target" in first["flags"]

    q6_only_details = module.summarize_prior_stress_details(
        rows,
        reason="q6_cells_above_prior",
    )
    assert len(q6_only_details) == 2

    detail_summary = module.summarize_prior_stress_detail_summary(
        details,
        group_fields=("map_id", "hero_map_evidence_profile"),
    )
    overall = detail_summary["overall"]
    assert overall["rows"] == 2
    assert overall["reason_counts"] == {
        "q6_cells_above_prior": 2,
        "q6_count_above_prior": 2,
    }
    assert overall["source_counts"]["total_cells"] == {"floor": 2}
    assert overall["source_counts"]["q6_cells"] == {"floor": 2}
    assert overall["capacity_flag_counts"] == {
        "target_count_above_prior_max": 2,
        "truth_count_above_prior_max": 2,
    }
    assert overall["capacity_count_summary"]["total_count_source_counts"] == {
        "floor": 2
    }
    assert overall["capacity_count_summary"]["case_counts"] == {
        "direct_prior_max_conflict": 2
    }
    assert overall["capacity_count_summary"]["prior_items_per_session_max"]["avg"] == 5
    assert overall["capacity_count_summary"]["target_prior_max_delta"]["avg"] == 1
    assert overall["capacity_count_summary"]["truth_prior_max_delta"]["avg"] == 1
    assert overall["capacity_count_summary"]["target_truth_delta"]["avg"] == 0
    assert overall["capacity_count_summary"]["target_prior_max_ratio"]["avg"] == 1.2
    assert overall["capacity_count_summary"]["truth_prior_max_ratio"]["avg"] == 1.2
    assert overall["capacity_count_summary"]["target_prior_max_delta_counts"] == {
        "below_prior_max": 0,
        "matches_prior_max": 0,
        "above_prior_max": 2,
    }
    assert overall["capacity_count_summary"]["truth_prior_max_delta_counts"] == {
        "below_prior_max": 0,
        "matches_prior_max": 0,
        "above_prior_max": 2,
    }
    assert overall["capacity_count_summary"]["target_truth_delta_counts"] == {
        "below_truth": 0,
        "matches_truth": 2,
        "above_truth": 0,
    }
    assert overall["consistency_class_counts"] == {
        "capacity_direct_prior_max_conflict": 2,
        "q6_value_floor_matches_truth": 2,
        "total_cells_floor_below_truth": 2,
        "total_value_floor_above_truth": 2,
        "q6_cells_floor_above_truth": 1,
        "q6_cells_floor_matches_truth": 1,
    }
    assert overall["consistency_bucket_counts"] == {"hard_capacity_conflict": 2}
    assert overall["detail_flag_counts"]["posterior_total_cells_under_truth"] == 2
    assert overall["detail_flag_counts"]["posterior_q6_cells_under_truth"] == 2
    assert overall["target_truth_match_counts"]["q6_cells"] == 1
    assert overall["target_truth_delta_counts"]["total_cells"] == {
        "below_truth": 2,
        "matches_truth": 0,
        "above_truth": 0,
    }
    assert overall["target_truth_delta_counts"]["q6_cells"] == {
        "below_truth": 0,
        "matches_truth": 1,
        "above_truth": 1,
    }
    assert overall["target_truth_delta_summary"]["q6_cells"]["max"] == 2
    assert overall["post_p50_truth_delta_summary"]["total_cells"]["avg"] == -8
    assert overall["post_p50_target_delta_counts"]["total_cells"] == {
        "below_target": 0,
        "matches_target": 0,
        "above_target": 2,
    }
    assert overall["post_p50_target_delta_counts"]["q6_cells"] == {
        "below_target": 2,
        "matches_target": 0,
        "above_target": 0,
    }
    assert overall["post_p50_target_delta_summary"]["q6_cells"]["avg"] == -7
    assert overall["post_p90_target_delta_summary"]["total_cells"]["avg"] == 12
    assert overall["ratio_summary"]["q6_cells"]["max"] == 2.5
    assert overall["evidence_count_summary"]["shape_anchors"]["max"] == 4
    by_reason = {row["reason"]: row for row in detail_summary["by_reason"]}
    assert by_reason["q6_cells_above_prior"]["rows"] == 2
    assert by_reason["q6_count_above_prior"]["ratio_summary"]["q6_cells"]["n"] == 2
    assert by_reason["q6_cells_above_prior"]["consistency_class_counts"][
        "capacity_direct_prior_max_conflict"
    ] == 2
    assert by_reason["q6_cells_above_prior"]["consistency_bucket_counts"] == {
        "hard_capacity_conflict": 2
    }
    by_group = {
        (row["field"], row["value"]): row for row in detail_summary["by_group"]
    }
    map_group = by_group[("map_id", "2506")]
    assert map_group["rows"] == 2
    assert map_group["capacity_flag_hits"] == 4
    assert map_group["max_cells_ratio"] == 2.5
    assert map_group["capacity_count_summary"]["target_prior_max_delta"]["max"] == 1
    assert map_group["capacity_count_summary"]["case_counts"] == {
        "direct_prior_max_conflict": 2
    }
    assert map_group["source_counts"]["q6_cells"] == {"floor": 2}
    assert map_group["target_truth_delta_counts"]["q6_cells"]["above_truth"] == 1
    assert map_group["post_p50_target_delta_counts"]["q6_cells"]["below_target"] == 2
    profile_group = by_group[("hero_map_evidence_profile", "aisha|2506|shape+layout")]
    assert profile_group["rows"] == 2
    assert profile_group["reason_counts"] == {
        "q6_cells_above_prior": 2,
        "q6_count_above_prior": 2,
    }

    activity_details = module.summarize_prior_stress_details(
        rows,
        reason="activity_map_id_candidate",
    )
    assert activity_details == []


def test_prior_stress_consistency_bucket_splits_main_risk_modes() -> None:
    module = _load_module()

    def row(
        cases: list[str],
        *,
        total_cells: dict[str, object] | None = None,
        q6_cells: dict[str, object] | None = None,
        total_value: dict[str, object] | None = None,
        q6_value: dict[str, object] | None = None,
    ) -> dict[str, object]:
        matched = {"source": "exact", "target_truth_delta": 0}
        value_matched = {"source": "floor", "target_truth_delta": 0}
        return {
            "item_count_capacity": {"cases": cases},
            "total_cells": total_cells or matched,
            "q6_cells": q6_cells or matched,
            "total_value": total_value or value_matched,
            "q6_value": q6_value or value_matched,
        }

    assert (
        module._consistency_bucket(
            row(["direct_prior_max_conflict"])
        )
        == "hard_capacity_conflict"
    )
    assert (
        module._consistency_bucket(
            row(["target_lower_bound_truth_above_prior"])
        )
        == "lower_bound_under_truth"
    )
    assert (
        module._consistency_bucket(
            row(
                ["no_capacity_prior_max_case"],
                total_cells={"source": "floor", "target_truth_delta": -4},
            )
        )
        == "evidence_floor_only"
    )
    assert (
        module._consistency_bucket(
            row(
                ["no_capacity_prior_max_case"],
                q6_cells={"source": "floor", "target_truth_delta": 2},
            )
        )
        == "target_over_truth_risk"
    )
    assert (
        module._consistency_bucket(
            row(["no_capacity_prior_max_case"])
        )
        == "no_capacity_prior_conflict"
    )


def test_prior_stress_detail_summary_exposes_lower_bound_target_completeness() -> None:
    module = _load_module()
    rows = [
        {
            **_ready_row(
                map_id=2506,
                pred=800,
                truth=1000,
                p90=1100,
                stress_score=1,
                reasons="total_cells_above_prior",
                scope="summary_likelihood",
            ),
            "v3_truth_item_count": 8,
            "v3_summary_known_count_floor": 4,
            "v3_robust_fallback_mode": "summary_likelihood_conservative",
        },
        {
            **_ready_row(
                map_id=2601,
                pred=800,
                truth=1000,
                p90=1100,
                stress_score=1,
                reasons="total_cells_above_prior",
                scope="summary_likelihood",
            ),
            "v3_truth_item_count": 8,
            "v3_summary_known_count_floor": None,
            "v3_robust_fallback_mode": "summary_likelihood_conservative",
        },
        {
            **_ready_row(
                map_id=2501,
                pred=800,
                truth=1000,
                p90=1100,
                stress_score=1,
                reasons="total_count_above_prior",
                scope="summary_likelihood",
            ),
            "v3_truth_item_count": 8,
            "v3_summary_known_count_floor": 6,
            "v3_robust_fallback_mode": "summary_likelihood_conservative",
        },
    ]

    details = module.summarize_prior_stress_details(rows)

    assert [row["consistency_bucket"] for row in details] == [
        "lower_bound_under_truth",
        "lower_bound_under_truth",
        "lower_bound_under_truth",
    ]
    summary = module.summarize_prior_stress_detail_summary(details)
    lower_summary = summary["overall"]["lower_bound_target_completeness_summary"]
    assert lower_summary["rows"] == 3
    assert lower_summary["reason_counts"] == {
        "total_cells_above_prior": 2,
        "total_count_above_prior": 1,
    }
    assert lower_summary["target_completeness_class_counts"] == {
        "count_target_above_prior_but_below_truth": 1,
        "floor_count_target_below_prior_and_truth": 1,
        "missing_count_target_truth_above_prior": 1,
    }
    assert lower_summary["capacity_count_summary"]["case_counts"] == {
        "target_lower_bound_truth_above_prior": 2,
        "target_above_prior_but_below_truth": 1,
        "truth_above_prior_without_count_target": 1,
        "truth_above_prior_without_target_prior_hit": 1,
    }
    assert lower_summary["capacity_count_summary"]["total_count_source_counts"] == {
        "floor": 2,
        "none": 1,
    }
    assert lower_summary["capacity_count_summary"]["target_prior_max_delta_counts"] == {
        "below_prior_max": 1,
        "matches_prior_max": 0,
        "above_prior_max": 1,
    }
    assert lower_summary["capacity_count_summary"]["truth_prior_max_delta_counts"] == {
        "below_prior_max": 0,
        "matches_prior_max": 0,
        "above_prior_max": 3,
    }
    assert lower_summary["capacity_count_summary"]["target_truth_delta_counts"] == {
        "below_truth": 2,
        "matches_truth": 0,
        "above_truth": 0,
    }
    assert lower_summary["capacity_count_summary"]["target_truth_delta"]["avg"] == -3
    assert lower_summary["target_missing_pattern_counts"] == {"none": 3}
    assert lower_summary["floor_below_truth_pattern_counts"] == {
        "total_cells+total_value": 3,
    }
    assert lower_summary["component_issue_counts"]["total_cells"] == {
        "floor_below_truth": 3,
    }


def test_prior_stress_detail_summary_exposes_evidence_floor_only_modes() -> None:
    module = _load_module()
    rows = [
        {
            **_ready_row(
                map_id=2401,
                pred=800,
                truth=1000,
                p90=1100,
                stress_score=1,
                reasons="q6_cells_above_prior",
                scope="summary_likelihood",
            ),
            "v3_prior_items_per_session_max": 10,
            "v3_robust_fallback_mode": "summary_likelihood_conservative",
        },
        {
            **_ready_row(
                map_id=2502,
                pred=900,
                truth=1000,
                p90=1100,
                stress_score=1,
                reasons="total_cells_above_prior",
                scope="summary_likelihood",
            ),
            "v3_prior_items_per_session_max": 10,
            "v3_summary_session_total_cells_exact": 40,
            "v3_summary_known_cells_floor": 30,
            "v3_summary_q6_cells_floor": 0,
            "v3_summary_q6_value_floor": 0,
            "v3_summary_known_value_floor": 0,
            "v3_robust_fallback_mode": "summary_likelihood_conservative",
        },
    ]

    details = module.summarize_prior_stress_details(rows)

    assert [row["consistency_bucket"] for row in details] == [
        "evidence_floor_only",
        "evidence_floor_only",
    ]
    summary = module.summarize_prior_stress_detail_summary(details)
    floor_summary = summary["overall"]["evidence_floor_only_summary"]
    assert floor_summary["rows"] == 2
    assert floor_summary["reason_counts"] == {
        "q6_cells_above_prior": 1,
        "total_cells_above_prior": 1,
    }
    assert floor_summary["source_counts"]["total_cells"] == {
        "exact": 1,
        "floor": 1,
    }
    assert floor_summary["source_counts"]["q6_cells"] == {
        "floor": 1,
        "none": 1,
    }
    assert floor_summary["component_issue_counts"]["total_cells"] == {
        "exact_matches_truth": 1,
        "floor_below_truth": 1,
    }
    assert floor_summary["component_issue_counts"]["q6_cells"] == {
        "floor_matches_truth": 1,
        "target_missing": 1,
    }
    assert floor_summary["component_issue_counts"]["total_value"] == {
        "floor_below_truth": 1,
        "target_missing": 1,
    }
    assert floor_summary["target_missing_pattern_counts"] == {
        "q6_cells+total_value+q6_value": 1,
        "none": 1,
    }
    assert floor_summary["floor_below_truth_pattern_counts"] == {
        "total_cells+total_value": 1,
        "none": 1,
    }
    assert floor_summary["exact_with_target_missing_pattern_counts"] == {
        "total_cells+q6_cells+total_value+q6_value": 1,
        "none": 1,
    }
    missing_summary = floor_summary["target_missing_attribution_summary"]
    assert missing_summary["rows"] == 1
    assert missing_summary["map_counts"] == {"2502": 1}
    assert missing_summary["missing_component_pattern_counts"] == {
        "q6_cells+total_value+q6_value": 1,
    }
    assert missing_summary["source_counts"]["total_cells"] == {"exact": 1}
    assert missing_summary["source_counts"]["q6_cells"] == {"none": 1}
    assert missing_summary["attribution_counts"] == {
        "item_anchors_present": 1,
        "item_anchors_present_value_targets_missing": 1,
        "numeric_constraints_present": 1,
        "q6_and_value_targets_missing": 1,
        "q6_cells_target_missing": 1,
        "q6_value_target_missing": 1,
        "quality_floor_anchors_present": 1,
        "quality_floor_present_value_targets_missing": 1,
    }
    assert floor_summary["target_truth_delta_counts"]["total_cells"] == {
        "below_truth": 1,
        "matches_truth": 1,
        "above_truth": 0,
    }
    assert floor_summary["evidence_count_summary"]["shape_anchors"]["n"] == 2
