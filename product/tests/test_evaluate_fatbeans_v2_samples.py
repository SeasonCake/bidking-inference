from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _eval_module():
    path = ROOT / "scripts" / "evaluate_fatbeans_v2_samples.py"
    spec = importlib.util.spec_from_file_location("evaluate_fatbeans_v2_samples", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_zero_match_root_classifies_exact_and_public_constraints() -> None:
    module = _eval_module()

    root = module._zero_match_root(
        {
            "v2_matched": 0,
            "layout_conflict": True,
            "layout_conflict_root": "footprint_overlap;footprint_count_relaxed",
            "relaxed_exact_used": True,
            "public_max_item_cells_used": True,
            "bucket_targets": "q6:count=1,cells=16;q4:avg=12000.00",
        }
    )

    assert "layout_conflict" in root
    assert "footprint_overlap" in root
    assert "footprint_count_relaxed" in root
    assert "relaxed_exact_fallback" in root
    assert "public_max_item_cells" in root
    assert "q6_exact_count_cells" in root
    assert "q4_avg_value" in root


def test_q6_plannable_miss_root_splits_rate_and_value_causes() -> None:
    module = _eval_module()

    low_value_root = module._q6_plannable_miss_root(
        {
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_match_rate": 1.0,
            "q6_below_drop_prior": False,
            "v2_q6_count_p90_under_by": 1,
            "v2_q6_cells_p90_under_by": 4,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_cells_p90_under_prior_by": 3.0,
            "v2_q6_space_pressure_p90": 1.25,
            "v2_q6_space_overflow_rate": 0.5,
            "final_q6_count": 2,
            "final_top_item_quality": 6,
            "final_top_item_cells": 12,
            "layout_conflict": True,
            "layout_conflict_root": "footprint_overlap",
            "bucket_targets": "",
        }
    )
    low_rate_root = module._q6_plannable_miss_root(
        {
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_match_rate": 0.0,
            "q6_below_drop_prior": True,
            "final_q6_count": 1,
            "final_top_item_quality": 6,
            "final_top_item_cells": 1,
            "bucket_targets": "q6:count=1,cells=1",
        }
    )

    assert "low_q6_value_distribution" in low_value_root
    assert "q6_count_under" in low_value_root
    assert "q6_cells_under" in low_value_root
    assert "q6_count_below_prior" in low_value_root
    assert "q6_cells_below_prior" in low_value_root
    assert "q6_space_pressure_high" in low_value_root
    assert "q6_space_overflow" in low_value_root
    assert "layout_conflict" in low_value_root
    assert "q6_top_large" in low_value_root
    assert "low_q6_sample_rate" in low_rate_root
    assert "below_drop_prior" in low_rate_root
    assert "q6_exact_count_cells" in low_rate_root


def test_summary_reports_q6_priority_and_root_causes() -> None:
    module = _eval_module()

    rows = [
        {
            "file": "a.json",
            "status": "ok",
            "hero": "ethan",
            "map_id": 2501,
            "map_family": "shipwreck",
            "value_tier": ">=1.2m",
            "final_value": 1_500_000,
            "final_trimmed_tail_value": 600_000,
            "final_q6_count": 1,
            "final_q6_value": 700_000,
            "final_q6_decision_value": 250_000,
            "final_q6_trimmed_tail_value": 450_000,
            "final_top_item_quality": 6,
            "final_top_item_cells": 9,
            "v2_matched": 2,
            "v2_match_rate": 0.2,
            "v2_value_p50_error": -100_000,
            "v2_decision_value_p50_error": -80_000,
            "v2_value_p90_error": -20_000,
            "v2_decision_value_p90_error": -100_000,
            "v2_value_p90_covers_final": False,
            "v2_q6_value_p90": 300_000,
            "v2_q6_decision_value_p90": 300_000,
            "v2_q6_value_p90_error": -400_000,
            "v2_q6_value_p90_under_by": 400_000,
            "v2_q6_decision_value_p90_under_by": 0,
            "v2_q6_count_p90_under_by": 0,
            "v2_q6_cells_p90_under_by": 0,
            "v2_q6_count_p90_under_prior_by": 0,
            "v2_q6_cells_p90_under_prior_by": 0,
            "v2_q6_match_rate": 0.05,
            "v2_q6_prior_expected_value": 800_000,
            "q6_false_low_risk": True,
            "q6_below_drop_prior": True,
            "q6_p90_misses_truth": True,
            "q6_plannable_p90_misses_truth": False,
            "layout_conflict": False,
            "relaxed_exact_used": False,
            "category_target_count": 2,
            "category_exclusion_count": 1,
            "category_action_combo": "100153:时尚;100158:能源",
            "diagnostics": "",
            "presolve_unreachable_exact_buckets": "",
            "public_constraint_key": "none",
            "evidence_profile_key": "tool:category",
            "anchor_band": "3-5",
            "q6_top_size_band": "q6_top_large",
            "q6_miss_root": "low_q6_sample_rate;q6_top_large",
            "evidence_stage": "mid_3_4",
            "information_density_band": "medium",
            "density_value_tier": "medium|>=1.2m",
            "hero_information_density": "ethan|medium",
            "hero_evidence_stage": "ethan|mid_3_4",
            "trusted_footprint_count": 2,
            "footprint_occupied_cells": 10,
            "calibration_eligible": True,
        },
        {
            "file": "b.json",
            "status": "ok",
            "hero": "ethan",
            "map_id": 2501,
            "map_family": "shipwreck",
            "value_tier": "300k-800k",
            "final_value": 500_000,
            "final_trimmed_tail_value": 0,
            "final_q6_count": 0,
            "final_q6_value": 0,
            "final_q6_decision_value": 0,
            "final_q6_trimmed_tail_value": 0,
            "v2_matched": 0,
            "v2_match_rate": 0.0,
            "v2_value_p50_error": 50_000,
            "v2_decision_value_p50_error": 40_000,
            "v2_value_p90_error": 100_000,
            "v2_decision_value_p90_error": 60_000,
            "v2_value_p90_covers_final": True,
            "q6_false_low_risk": False,
            "v2_q6_decision_value_p90": None,
            "v2_q6_count_p90_under_by": 0,
            "v2_q6_cells_p90_under_by": 0,
            "v2_q6_count_p90_under_prior_by": 0,
            "v2_q6_cells_p90_under_prior_by": 0,
            "q6_p90_misses_truth": False,
            "q6_plannable_p90_misses_truth": False,
            "layout_conflict": True,
            "layout_conflict_root": "footprint_overlap;footprint_count_relaxed",
            "relaxed_exact_used": True,
            "bucket_targets": "q4:count=4,cells=12",
            "category_target_count": 1,
            "category_exclusion_count": 0,
            "category_action_combo": "100152:医疗",
            "diagnostics": "category_target_no_pool_match:108:6:33:9",
            "presolve_unreachable_exact_buckets": "q4:count=4,cells=12",
            "zero_match_root": (
                "layout_conflict;footprint_overlap;footprint_count_relaxed;"
                "relaxed_exact_fallback;presolve_unreachable_exact_bucket;"
                "q4_exact_count_cells"
            ),
            "public_constraint_key": "none",
            "evidence_profile_key": "tool:category",
            "anchor_band": "6+",
            "q6_top_size_band": "no_q6",
            "evidence_stage": "early_1_2",
            "information_density_band": "low",
            "density_value_tier": "low|300k-800k",
            "hero_information_density": "ethan|low",
            "hero_evidence_stage": "ethan|early_1_2",
            "trusted_footprint_count": 0,
            "footprint_occupied_cells": 0,
            "calibration_eligible": False,
        },
    ]

    summary = module._summary(rows)

    zero_causes = {row["cause"]: row["n"] for row in summary["zero_match_root_causes"]}
    assert zero_causes["layout_conflict"] == 1
    assert zero_causes["footprint_overlap"] == 1
    assert zero_causes["q4_exact_count_cells"] == 1
    layout_causes = {
        row["cause"]: row["n"]
        for row in summary["layout_conflict_root_causes"]
    }
    assert layout_causes["footprint_count_relaxed"] == 1

    q6_causes = {row["cause"]: row["n"] for row in summary["q6_miss_root_causes"]}
    assert q6_causes["low_q6_sample_rate"] == 1
    assert q6_causes["q6_top_large"] == 1

    priority = summary["q6_calibration_priority"]
    assert priority[0]["group"] == "hero=ethan|map_family=shipwreck"
    assert priority[0]["q6_p90_misses_truth"] == 1
    assert priority[0]["median_q6_under_by"] == 400_000
    assert summary["q6_truth_files"] == 1
    assert summary["q6_plannable_truth_files"] == 1
    assert summary["q6_no_plannable_truth_files"] == 0
    assert summary["q6_no_plannable_p90_positive"] == 0
    assert summary["q6_tail_event_files"] == 1
    assert summary["q6_p90_misses_truth"] == 1
    assert summary["q6_plannable_p90_misses_truth"] == 0
    assert summary["q6_value_p90_coverage"] == 0.0
    assert summary["q6_plannable_value_p90_coverage"] == 1.0
    assert summary["q6_tail_trimmed_value_median"] == 450_000
    assert (
        summary["q6_plannable_risk_groups"]["hero_map_family"][0][
            "q6_plannable_p90_misses_truth"
        ]
        == 0
    )
    assert summary["q6_plannable_calibration_priority"] == []
    assert summary["tail_event_count"] == 1
    assert summary["regular_decision_value_mae"] == 40_000
    assert summary["tail_event_decision_value_mae"] == 80_000
    assert summary["decision_value_accuracy"]["decision_value_mae"] == 60_000
    assert (
        summary["decision_value_accuracy"]["median_normalized_abs_p50_error"]
        == 0.067
    )
    assert summary["decision_value_accuracy"]["p50_under_rate"] == 0.5
    assert summary["decision_value_accuracy"]["p90_coverage"] == 0.5
    assert summary["decision_value_accuracy"]["p90_pinball_loss_mean"] == 48_000
    assert summary["category_evidence"]["target_rows"] == 2
    assert summary["category_evidence"]["exclusion_rows"] == 1
    assert summary["category_evidence"]["target_total"] == 3
    assert summary["category_evidence"]["exclusion_total"] == 1
    assert summary["category_evidence"]["no_pool_match_rows"] == 1
    assert summary["category_evidence"]["action_combo_top"][0] == {
        "combo": "100153:时尚;100158:能源",
        "n": 1,
    }
    assert summary["category_evidence"]["examples"][0]["file"] == "a.json"
    assert (
        summary["category_evidence"]["no_pool_match_examples"][0]["file"]
        == "b.json"
    )
    assert summary["presolve_unreachable_exact_rows"] == 1
    assert summary["presolve_exact_bucket"]["unreachable_rows"] == 1
    assert summary["presolve_exact_bucket"]["zero_match_rows"] == 1
    assert summary["presolve_exact_bucket"]["relaxed_exact_rows"] == 1
    assert (
        summary["presolve_exact_bucket"]["examples"][0][
            "presolve_unreachable_exact_buckets"
        ]
        == "q4:count=4,cells=12"
    )
    assert summary["sample_feasibility"]["calibration_eligible_rows"] == 1
    assert summary["sample_feasibility"]["early_rows"] == 1
    assert summary["sample_feasibility"]["calibration_decision_value_mae"] == 80_000
    assert summary["sample_feasibility"]["calibration_q6_p90_misses_truth"] == 1
    assert summary["sample_feasibility"]["by_evidence_stage"] == {
        "mid_3_4": 1,
        "early_1_2": 1,
    }
    assert summary["sample_feasibility"]["by_information_density"] == {
        "medium": 1,
        "low": 1,
    }
    assert {
        row["information_density_band"]: row["n"]
        for row in summary["groups"]["information_density"]
    } == {"low": 1, "medium": 1}
    assert {
        row["density_value_tier"]: row["n"]
        for row in summary["groups"]["density_value_tier"]
    } == {"low|300k-800k": 1, "medium|>=1.2m": 1}
    assert {
        row["hero_information_density"]: row["n"]
        for row in summary["groups"]["hero_information_density"]
    } == {"ethan|low": 1, "ethan|medium": 1}
    assert (
        summary["q6_plannable_risk_groups"]["information_density"][0][
            "q6_plannable_truth"
        ]
        == 1
    )
    assert (
        summary["q6_plannable_risk_groups"]["hero_map_family"][0][
            "trusted_footprint_median"
        ]
        == 1
    )
    assert (
        summary["q6_plannable_risk_groups"]["hero_map_family"][0][
            "footprint_occupied_cells_median"
        ]
        == 5
    )
    assert summary["groups"]["evidence_profile"][0]["evidence_profile_key"] == (
        "tool:category"
    )
    assert "q6_cell_gap_by_feature" in summary

    experiment = module._summary(rows, q6_residual_floor_ratio=0.75)[
        "q6_residual_floor_experiment"
    ]
    assert experiment["eligible_rows"] == 1
    assert experiment["floor_median"] == 600_000
    assert experiment["q6_p90_misses_truth"] == 1
    assert experiment["groups"]["hero_map_family"][0]["group"] == (
        "hero=ethan|map_family=shipwreck"
    )
    assert experiment["groups"]["hero_map_family"][0]["eligible_rows"] == 1
    assert (
        experiment["groups"]["hero_map_family"][0]["q6_p90_misses_before"]
        == 1
    )
    assert (
        experiment["groups"]["hero_map_family"][0]["q6_p90_misses_after"]
        == 1
    )

    experiment = module._summary(rows, q6_residual_floor_ratio=1.0)[
        "q6_residual_floor_experiment"
    ]
    assert experiment["q6_p90_misses_truth"] == 0
    assert experiment["q6_value_p90_coverage"] == 1.0
    assert (
        experiment["groups"]["hero_map_family"][0]["q6_p90_miss_improvement"]
        == 1
    )


def test_q6_cell_gap_by_feature_splits_count_cells_and_controls() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "aisha_r2.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "capture_round": 2,
            "evidence_stage": "early_1_2",
            "information_density_band": "medium",
            "evidence_profile_key": "shape+layout",
            "random_sample_avg_signal_band": "none",
            "public_avg_cells_solution_band": "none",
            "q6_top_size_band": "q6_top_huge",
            "footprint_bottom_row": 11,
            "final_q6_count": 6,
            "final_q6_cells": 57,
            "final_q6_decision_value": 1_000_000,
            "v2_q6_count_p90": 4,
            "v2_q6_cells_p90": 12,
            "v2_q6_decision_value_p90": 700_000,
            "v2_q6_prior_expected_count": 4.5,
            "v2_q6_prior_expected_cells": 25.0,
            "v2_q6_count_p90_under_by": 2,
            "v2_q6_cells_p90_under_by": 45,
            "v2_q6_count_p90_under_prior_by": 0.5,
            "v2_q6_cells_p90_under_prior_by": 13.0,
            "v2_q6_decision_value_p90_under_by": 300_000,
            "q6_plannable_p90_misses_truth": True,
        },
        {
            "status": "ok",
            "file": "aisha_no_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "capture_round": 2,
            "evidence_stage": "early_1_2",
            "information_density_band": "medium",
            "evidence_profile_key": "shape+layout",
            "random_sample_avg_signal_band": "none",
            "public_avg_cells_solution_band": "none",
            "q6_top_size_band": "no_q6",
            "footprint_bottom_row": 11,
            "final_q6_count": 0,
            "final_q6_cells": 0,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 180_000,
            "q6_plannable_p90_misses_truth": False,
        },
        {
            "status": "ok",
            "file": "aisha_r4.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "capture_round": 4,
            "evidence_stage": "mid_3_4",
            "information_density_band": "high",
            "evidence_profile_key": "shape+layout",
            "random_sample_avg_signal_band": "none",
            "public_avg_cells_solution_band": "all_unique",
            "q6_top_size_band": "q6_top_large",
            "footprint_bottom_row": 14,
            "final_q6_count": 4,
            "final_q6_cells": 20,
            "final_q6_decision_value": 400_000,
            "v2_q6_count_p90": 4,
            "v2_q6_cells_p90": 22,
            "v2_q6_decision_value_p90": 450_000,
            "v2_q6_prior_expected_count": 3.5,
            "v2_q6_prior_expected_cells": 18.0,
            "v2_q6_count_p90_under_by": 0,
            "v2_q6_cells_p90_under_by": 0,
            "v2_q6_count_p90_under_prior_by": 0,
            "v2_q6_cells_p90_under_prior_by": 0,
            "v2_q6_decision_value_p90_under_by": 0,
            "q6_plannable_p90_misses_truth": False,
        },
    ]

    summary = module._q6_cell_gap_by_feature_summary(rows)

    assert summary["audit_only"] is True
    assert summary["q6_plannable_truth_rows"] == 2
    assert summary["q6_plannable_miss_rows"] == 1
    assert summary["q6_count_and_cells_under_miss_rows"] == 1
    assert summary["no_q6_control_rows"] == 1
    assert summary["no_q6_p90_positive_rows"] == 1

    profile = summary["feature_groups"]["hero_map_profile"][0]
    assert profile["group"] == (
        "hero=aisha|map_family=shipwreck|evidence_profile_key=shape+layout"
    )
    assert profile["q6_plannable_truth_rows"] == 2
    assert profile["q6_plannable_miss_rows"] == 1
    assert profile["q6_plannable_coverage"] == 0.5
    assert profile["median_truth_count"] == 5
    assert profile["median_p90_cells"] == 17.0
    assert profile["no_q6_p90_positive_rate"] == 1.0

    bottom = summary["feature_groups"]["hero_map_bottom_row"][0]
    assert bottom["group"] == (
        "hero=aisha|map_family=shipwreck|q6_bottom_row_band=bottom_11_12"
    )
    assert bottom["median_cells_under_by"] == 45.0
    assert bottom["median_prior_cells_gap"] == 13.0
    assert bottom["examples"][0]["file"] == "aisha_r2.json"

    deep = summary["feature_groups"]["aisha_shipwreck_deep_band"][0]
    assert deep["group"] == (
        "aisha_shipwreck_deep_band=aisha_shipwreck_deep11_12_shadow|"
        "evidence_profile_key=shape+layout"
    )
    assert deep["q6_count_below_prior_rows"] == 1

    prior_gap = summary["feature_groups"]["hero_map_prior_gap"][0]
    assert prior_gap["group"] == (
        "hero=aisha|map_family=shipwreck|"
        "q6_prior_gap_kind=count_and_cells_below_prior"
    )


def test_q6_conditional_target_experiment_reports_help_and_control_risk() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "miss.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_count": 4,
            "final_q6_cells": 12,
            "final_q6_decision_value": 500,
            "v2_q6_count_p90": 1,
            "v2_q6_cells_p90": 2,
            "v2_q6_decision_value_p90": 200,
            "v2_q6_count_p90_under_by": 3,
            "v2_q6_cells_p90_under_by": 10,
            "v2_q6_decision_value_p90_under_by": 300,
            "q6_plannable_p90_misses_truth": True,
        },
        {
            "status": "ok",
            "file": "covered.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_count": 2,
            "final_q6_cells": 6,
            "final_q6_decision_value": 300,
            "v2_q6_count_p90": 2,
            "v2_q6_cells_p90": 6,
            "v2_q6_decision_value_p90": 350,
            "v2_q6_count_p90_under_by": 0,
            "v2_q6_cells_p90_under_by": 0,
            "v2_q6_decision_value_p90_under_by": 0,
            "q6_plannable_p90_misses_truth": False,
        },
        {
            "status": "ok",
            "file": "control.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_count": 0,
            "final_q6_cells": 0,
            "final_q6_decision_value": 0,
            "v2_q6_count_p90": 0,
            "v2_q6_cells_p90": 0,
            "v2_q6_decision_value_p90": 0,
            "q6_plannable_p90_misses_truth": False,
        },
    ]

    experiment = module._q6_conditional_target_experiment(
        rows,
        name="test",
        keys=("hero", "map_family", "evidence_profile_key"),
        target_quantile=1.0,
        shrinkage_k=0.0,
        min_q6_truth=2,
        min_miss_rows=1,
    )

    assert experiment["candidate_groups"] == 1
    assert experiment["q6_misses_before"] == 1
    assert experiment["q6_misses_after"] == 0
    assert experiment["q6_helped_rows"] == 1
    assert experiment["active_no_q6_rows"] == 1
    assert experiment["no_q6_increased_rows"] == 1
    assert experiment["no_q6_new_positive_rows"] == 1
    assert experiment["groups"][0]["target_count"] == 4.0
    assert experiment["groups"][0]["target_cells"] == 12.0
    assert experiment["groups"][0]["target_value"] == 500
    assert experiment["helped_examples"][0]["file"] == "miss.json"
    assert experiment["no_q6_increased_examples"][0]["file"] == "control.json"

    summary = module._q6_conditional_target_experiment_summary(rows)
    assert summary["audit_only"] is True
    assert summary["in_sample_upper_bound"] is True
    assert "configs" in summary


def test_q6_count_cell_prior_floor_experiment_is_separate_from_raw_floor() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "q6_top_size_band": "q6_top_large",
            "final_q6_value": 500,
            "final_q6_decision_value": 500,
            "v2_q6_value_p90": 100,
            "v2_q6_decision_value_p90": 100,
            "v2_match_rate": 1.0,
            "q6_p90_misses_truth": True,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_cells_p90_under_prior_by": 4.0,
        },
        {
            "status": "ok",
            "file": "no_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "q6_top_size_band": "no_q6",
            "final_q6_value": 0,
            "final_q6_decision_value": 0,
            "v2_q6_value_p90": 0,
            "v2_q6_decision_value_p90": 0,
            "v2_match_rate": 1.0,
            "q6_p90_misses_truth": False,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
    ]

    experiment = module._summary(rows, q6_residual_floor_ratio=1.0)[
        "q6_count_cell_prior_floor_experiment"
    ]

    assert experiment["eligible_rows"] == 1
    assert experiment["eligible_no_q6_rows"] == 1
    assert experiment["q6_plannable_p90_misses_truth"] == 0
    assert experiment["q6_plannable_value_p90_coverage"] == 1.0
    assert (
        experiment["groups"]["hero_map_family"][0][
            "q6_plannable_miss_improvement"
        ]
        == 1
    )


def test_summary_reports_no_q6_positive_p90_proxy() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "no_q6.json",
            "final_value": 100,
            "final_q6_count": 0,
            "final_q6_decision_value": 0,
            "final_q6_trimmed_tail_value": 0,
            "v2_matched": 10,
            "v2_match_rate": 1.0,
            "v2_value_p50_error": 0,
            "v2_decision_value_p50_error": 0,
            "v2_value_p90_error": 50,
            "v2_value_p90_covers_final": True,
            "v2_q6_decision_value_p90": 200,
            "q6_false_low_risk": False,
            "q6_below_drop_prior": False,
            "q6_p90_misses_truth": False,
            "q6_plannable_p90_misses_truth": False,
            "layout_conflict": False,
            "relaxed_exact_used": False,
            "category_target_count": 0,
            "category_exclusion_count": 0,
            "presolve_unreachable_exact_buckets": "",
        }
    ]

    summary = module._summary(rows)

    assert summary["q6_no_plannable_truth_files"] == 1
    assert summary["q6_no_plannable_p90_positive"] == 1
    assert summary["q6_no_plannable_p90_positive_rate"] == 1.0
    assert summary["q6_no_plannable_p90_positive_median"] == 200


def test_q6_residual_boost_profile_gate_is_narrow() -> None:
    module = _eval_module()

    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="villa",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 1.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="public:random_avg+shape+layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="ethan",
        map_family="shipwreck",
        evidence_profile_key="public:random_avg+layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="ethan",
        map_family="villa",
        evidence_profile_key="public:random_avg+layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="ethan",
        map_family="villa",
        evidence_profile_key="layout",
        requested_boost=3.0,
        gate="shipwreck_profile_v1",
    ) == 1.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=13,
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=12,
    ) == 1.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="aisha_shipwreck_bottom_v1",
        bottom_row=16,
    ) == 3.0
    assert module._q6_residual_boost_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_boost=3.0,
        gate="aisha_shipwreck_bottom_v1",
        bottom_row=12,
    ) == 1.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_profile_v1",
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="public:random_avg+shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_profile_v1",
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=13,
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=12,
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep12_v1",
        bottom_row=12,
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep12_v1",
        bottom_row=11,
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep11_v1",
        bottom_row=11,
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="public:random_avg+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_deep11_v1",
        bottom_row=11,
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_bottom_v1",
        bottom_row=16,
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_bottom_v1",
        bottom_row=12,
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="ethan",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_shipwreck_profile_v1",
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="hidden",
        evidence_profile_key="shape+layout",
        requested_ratio=1.0,
        gate="aisha_hidden_v1",
    ) == 1.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="ethan",
        map_family="hidden",
        evidence_profile_key="shape+layout",
        requested_ratio=1.0,
        gate="aisha_hidden_v1",
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=1.0,
        gate="aisha_deep_or_hidden_v1",
        bottom_row=13,
    ) == 1.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=1.0,
        gate="aisha_deep_or_hidden_v1",
        bottom_row=12,
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="hidden",
        evidence_profile_key="shape+layout",
        requested_ratio=1.0,
        gate="aisha_deep_or_hidden_v1",
    ) == 1.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="hidden",
        evidence_profile_key="public:random_avg+shape+layout",
        requested_ratio=1.0,
        gate="aisha_hidden_v1",
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="villa",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_villa_shape_layout_v1",
    ) == 0.75
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_villa_shape_layout_v1",
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="ethan",
        map_family="villa",
        evidence_profile_key="shape+layout",
        requested_ratio=0.75,
        gate="aisha_villa_shape_layout_v1",
    ) == 0.0
    assert module.q6_residual_prior_floor_ratio_for_profile(
        hero="aisha",
        map_family="villa",
        evidence_profile_key="public:random_avg+shape+layout",
        requested_ratio=0.75,
        gate="aisha_villa_shape_layout_v1",
    ) == 0.0
    assert module.q6_residual_value_power_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_power=0.5,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=13,
    ) == 0.5
    assert module.q6_residual_value_power_for_profile(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        requested_power=0.5,
        gate="aisha_shipwreck_deep_v1",
        bottom_row=12,
    ) == 0.0
    assert module.q6_residual_value_power_for_profile(
        hero="ethan",
        map_family="villa",
        evidence_profile_key="public:random_avg+layout",
        requested_power=0.5,
        gate="ethan_villa_random_avg_v1",
    ) == 0.5
    assert module.q6_conditional_target_active_for_profile(
        hero="ethan",
        map_family="shipwreck",
        evidence_profile_key="layout",
        gate="ethan_shipwreck_layout_v1",
    )
    assert module.q6_conditional_target_active_for_profile(
        hero="ethan",
        map_family="shipwreck",
        evidence_profile_key="public:random_avg+layout",
        gate="ethan_shipwreck_layout_v1",
    )
    assert not module.q6_conditional_target_active_for_profile(
        hero="ethan",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        gate="ethan_shipwreck_layout_v1",
    )


def test_q6_residual_boost_summary_reports_activation_and_no_q6_proxy() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "v2_decision_value_p50_error": 0,
            "q6_residual_boost": 3.0,
            "q6_residual_boost_gate": "shipwreck_profile_v1",
        },
        {
            "status": "ok",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 200,
            "q6_plannable_p90_misses_truth": False,
            "v2_decision_value_p50_error": 0,
            "q6_residual_boost": 3.0,
            "q6_residual_boost_gate": "shipwreck_profile_v1",
        },
        {
            "status": "ok",
            "hero": "aisha",
            "map_family": "villa",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_decision_value_p50_error": 0,
            "q6_residual_boost": 1.0,
            "q6_residual_boost_gate": "shipwreck_profile_v1",
        },
    ]

    summary = module._q6_residual_boost_summary(rows)

    assert summary["boost_values"] == [1.0, 3.0]
    assert summary["active_rows"] == 2
    assert summary["active_no_q6_rows"] == 1
    assert summary["active_no_q6_p90_positive_rate"] == 1.0
    assert summary["q6_plannable_value_p90_coverage"] == 0.5
    assert summary["groups"]["hero_map_profile"][0]["group"] == (
        "hero=aisha|map_family=shipwreck|evidence_profile_key=shape+layout"
    )


def test_q6_residual_prior_floor_sampler_summary_reports_activation() -> None:
    module = _eval_module()
    rows = [
        {
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "q6_residual_prior_floor_ratio": 0.75,
            "q6_residual_prior_cell_floor_ratio": 3.0,
            "q6_residual_prior_floor_gate": "aisha_shipwreck_profile_v1",
        },
        {
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 200,
            "q6_residual_prior_floor_ratio": 0.75,
            "q6_residual_prior_floor_gate": "aisha_shipwreck_profile_v1",
        },
        {
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "q6_residual_prior_floor_ratio": 0.0,
            "q6_residual_prior_floor_gate": "aisha_shipwreck_profile_v1",
        },
    ]

    summary = module._q6_residual_prior_floor_sampler_summary(rows)

    assert summary["floor_ratios"] == [0.0, 0.75]
    assert summary["cell_floor_ratios"] == [0.0, 3.0]
    assert summary["active_rows"] == 2
    assert summary["active_no_q6_rows"] == 1
    assert summary["active_no_q6_p90_positive_rate"] == 1.0
    assert summary["q6_plannable_value_p90_coverage"] == 0.5


def test_q6_residual_value_sampler_summary_reports_activation() -> None:
    module = _eval_module()
    rows = [
        {
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "q6_residual_value_power": 0.5,
            "q6_residual_value_gate": "aisha_shipwreck_deep_v1",
        },
        {
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 200,
            "q6_residual_value_power": 0.5,
            "q6_residual_value_gate": "aisha_shipwreck_deep_v1",
        },
        {
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "q6_residual_value_power": 0.0,
            "q6_residual_value_gate": "aisha_shipwreck_deep_v1",
        },
    ]

    summary = module._q6_residual_value_sampler_summary(rows)

    assert summary["value_powers"] == [0.0, 0.5]
    assert summary["active_rows"] == 2
    assert summary["active_no_q6_rows"] == 1
    assert summary["active_no_q6_p90_positive_rate"] == 1.0
    assert summary["q6_plannable_value_p90_coverage"] == 0.5


def test_q6_conditional_target_sampler_summary_reports_activation() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "hero": "ethan",
            "map_family": "shipwreck",
            "evidence_profile_key": "layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "q6_conditional_target_count": 4.0,
            "q6_conditional_target_cells": 15.0,
            "q6_conditional_target_gate": "ethan_shipwreck_layout_v1",
            "q6_conditional_value_power": 0.25,
        },
        {
            "status": "ok",
            "hero": "ethan",
            "map_family": "shipwreck",
            "evidence_profile_key": "layout",
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 200,
            "q6_conditional_target_count": 4.0,
            "q6_conditional_target_cells": 15.0,
            "q6_conditional_target_gate": "ethan_shipwreck_layout_v1",
            "q6_conditional_value_power": 0.25,
        },
        {
            "status": "ok",
            "hero": "ethan",
            "map_family": "villa",
            "evidence_profile_key": "layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "q6_conditional_target_count": 0.0,
            "q6_conditional_target_cells": 0.0,
            "q6_conditional_target_gate": "ethan_shipwreck_layout_v1",
        },
    ]

    summary = module._q6_conditional_target_sampler_summary(rows)

    assert summary["target_counts"] == [0.0, 4.0]
    assert summary["target_cells"] == [0.0, 15.0]
    assert summary["value_powers"] == [0.0, 0.25]
    assert summary["active_rows"] == 2
    assert summary["active_no_q6_rows"] == 1
    assert summary["active_no_q6_p90_positive_rate"] == 1.0
    assert summary["q6_plannable_value_p90_coverage"] == 0.5


def test_q6_low_space_residual_floor_experiment_is_narrower_than_prior_floor() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "low_space_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.2,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "status": "ok",
            "file": "high_space_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 1.2,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "status": "ok",
            "file": "low_space_no_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.1,
            "v2_q6_space_overflow_rate": 0.0,
        },
    ]

    experiment = module._q6_low_space_residual_floor_experiment(
        rows,
        floor_ratio=1.0,
    )

    assert experiment["eligible_rows"] == 1
    assert experiment["eligible_no_q6_rows"] == 1
    assert experiment["q6_plannable_p90_misses_truth"] == 1
    assert experiment["groups"]["hero_map_profile"][0]["net_improvement"] == 0


def test_q6_low_space_residual_gated_floor_keeps_positive_net_profiles() -> None:
    module = _eval_module()
    rows = [
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.2,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.3,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "hero": "aisha",
            "map_family": "villa",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.2,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "hero": "aisha",
            "map_family": "villa",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.2,
            "v2_q6_space_overflow_rate": 0.0,
        },
    ]

    experiment = module._q6_low_space_residual_gated_floor_experiment(
        rows,
        floor_ratio=1.0,
        min_q6_truth=1,
    )

    assert [row["group"] for row in experiment["gates"]] == [
        "hero=aisha|map_family=shipwreck|evidence_profile_key=shape+layout"
    ]
    assert experiment["eligible_rows"] == 2
    assert experiment["eligible_no_q6_rows"] == 0
    assert experiment["q6_plannable_p90_misses_truth"] == 1


def test_q6_tail_value_sampler_experiment_scans_aisha_shipwreck_thresholds() -> None:
    module = _eval_module()
    rows = [
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 11,
            "final_q6_decision_value": 1_000,
            "final_q6_decision_value_with_tail_replacement": 1_200,
            "v2_q6_decision_value_p90": 500,
            "v2_q6_tail_replacement_decision_value_p90": 1_200,
        },
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 10,
            "final_q6_decision_value": 1_000,
            "final_q6_decision_value_with_tail_replacement": 1_000,
            "v2_q6_decision_value_p90": 500,
            "v2_q6_tail_replacement_decision_value_p90": 1_200,
        },
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 11,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "v2_q6_tail_replacement_decision_value_p90": 200,
        },
        {
            "hero": "ethan",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 11,
            "final_q6_decision_value": 1_000,
            "v2_q6_decision_value_p90": 500,
            "v2_q6_tail_replacement_decision_value_p90": 1_200,
        },
    ]

    experiment = module._q6_tail_value_sampler_experiment(rows)
    threshold_11 = next(
        row for row in experiment["thresholds"]
        if row["bottom_row_threshold"] == 11
    )

    assert experiment["formal_decision_value_unchanged"] is True
    assert threshold_11["active_q6_truth_rows"] == 1
    assert threshold_11["q6_helped_rows"] == 1
    assert threshold_11["tail_replacement_helped_rows"] == 1
    assert threshold_11["active_no_q6_rows"] == 1
    assert threshold_11["active_no_q6_new_positive_rows"] == 1
    assert threshold_11["net_improvement"] == 0


def test_q6_count_cell_prior_gated_floor_prefers_positive_net_groups() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "good_gate_q6.json",
            "hero": "ethan",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "bad_gate_q6.json",
            "hero": "aisha",
            "map_family": "villa",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "bad_gate_no_q6.json",
            "hero": "aisha",
            "map_family": "villa",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
    ]

    experiment = module._q6_count_cell_prior_gated_floor_experiment(
        rows,
        floor_ratio=1.0,
    )

    assert [row["group"] for row in experiment["gates"]] == [
        "hero=ethan|map_family=shipwreck"
    ]
    assert experiment["eligible_rows"] == 1
    assert experiment["eligible_no_q6_rows"] == 0
    assert experiment["q6_plannable_p90_misses_truth"] == 1

    profile_experiment = module._q6_count_cell_prior_gated_floor_experiment(
        rows,
        floor_ratio=1.0,
        gate_keys=("hero", "map_family", "evidence_profile_key"),
        gate_name="hero_map_family_profile_positive_net",
    )
    assert profile_experiment["gates"][0]["group"] == (
        "hero=ethan|map_family=shipwreck|evidence_profile_key=shape+layout"
    )
    strict_profile_experiment = module._q6_count_cell_prior_gated_floor_experiment(
        rows,
        floor_ratio=1.0,
        gate_keys=("hero", "map_family", "evidence_profile_key"),
        gate_name="hero_map_family_profile_positive_net",
        min_q6_truth=2,
    )
    assert strict_profile_experiment["gates"] == []


def test_q6_count_cell_prior_narrow_gate_p90_sweep_keeps_p50_separate() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "aisha_deep_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "aisha_deep_no_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_cells_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "aisha_shallow_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 12,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "ethan_villa_q6.json",
            "hero": "ethan",
            "map_family": "villa",
            "evidence_profile_key": "public:random_avg+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
    ]

    sweep = module._q6_count_cell_prior_narrow_gate_p90_sweep(rows)
    aisha = next(
        row for row in sweep["candidates"]
        if row["gate"] == "aisha_shipwreck_deep_v1"
        and row["floor_ratio"] == 1.0
    )
    ethan = next(
        row for row in sweep["candidates"]
        if row["gate"] == "ethan_villa_random_avg_v1"
        and row["floor_ratio"] == 1.0
    )

    assert sweep["offline_shadow_only"] is True
    assert sweep["formal_decision_value_unchanged"] is True
    assert sweep["p50_unchanged"] is True
    assert aisha["active_rows"] == 2
    assert aisha["q6_helped_rows"] == 1
    assert aisha["no_q6_p90_increased_rows"] == 1
    assert aisha["no_q6_new_positive_rows"] == 1
    assert aisha["q6_plannable_misses_after"] == 2
    assert ethan["active_rows"] == 1
    assert ethan["q6_helped_rows"] == 1
    assert ethan["eligible_no_q6_rows"] == 0


def test_aisha_threshold_sweep_shows_lower_threshold_control_risk() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "file": "aisha_bottom11_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 11,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_count_p90_under_prior_by": 1.0,
        },
        {
            "status": "ok",
            "file": "aisha_bottom11_no_q6.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 11,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 0,
            "v2_q6_prior_expected_value": 600,
            "v2_q6_cells_p90_under_prior_by": 1.0,
        },
    ]

    sweep = module._aisha_shipwreck_deep_threshold_p90_sweep(rows)
    by_threshold = {
        row["bottom_row_threshold"]: row for row in sweep["thresholds"]
    }

    assert sweep["audit_only"] is True
    assert sweep["formal_decision_value_unchanged"] is True
    assert sweep["p50_unchanged"] is True
    assert sweep["current_formal_threshold"] == 13
    assert by_threshold[13]["active_rows"] == 0
    assert by_threshold[13]["q6_helped_rows"] == 0
    assert by_threshold[11]["active_rows"] == 2
    assert by_threshold[11]["q6_helped_rows"] == 1
    assert by_threshold[11]["no_q6_new_positive_rows"] == 1
    assert by_threshold[11]["net_helped_minus_no_q6_new_positive"] == 0
    assert by_threshold[11]["net_helped_minus_no_q6_p90_increase"] == 0


def test_q6_condition_audit_counts_nonexclusive_miss_tags() -> None:
    module = _eval_module()
    rows = [
        {
            "file": "aisha_deep_miss.json",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "footprint_bottom_row": 13,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_decision_value_p90_under_by": 400,
            "v2_q6_count_p90_under_by": 1,
            "v2_q6_cells_p90_under_by": 2,
            "v2_q6_count_p90_under_prior_by": 0.5,
            "v2_q6_cells_p90_under_prior_by": 1.0,
            "v2_q6_space_pressure_p90": 0.1,
            "v2_q6_space_overflow_rate": 0.0,
            "random_sample_avg_signal_band": "none",
            "public_avg_cells_solution_band": "all_unique",
        },
        {
            "file": "ethan_random_covered.json",
            "hero": "ethan",
            "map_family": "villa",
            "evidence_profile_key": "public:random_avg+layout",
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_space_pressure_p90": 1.1,
            "random_sample_avg_signal_band": "signal",
            "public_avg_cells_solution_band": "ambiguous",
        },
    ]

    audit = module._q6_condition_audit_summary(rows)
    by_tag = {row["tag"]: row for row in audit["tags"]}

    assert audit["audit_only"] is True
    assert audit["q6_plannable_truth_rows"] == 2
    assert audit["q6_plannable_miss_rows"] == 1
    assert by_tag["q6_count_under_truth"]["q6_plannable_miss_rows"] == 1
    assert by_tag["q6_cells_below_prior"]["examples"] == [
        "aisha_deep_miss.json"
    ]
    assert by_tag["aisha_deep_gate_active"]["q6_plannable_miss_rows"] == 1
    assert by_tag["ethan_villa_random_avg_gate_active"][
        "q6_plannable_miss_rows"
    ] == 0
    assert by_tag["random_avg_signal"]["q6_plannable_coverage"] == 1.0


def test_q6_actionable_targets_prioritize_shipwreck_profiles() -> None:
    module = _eval_module()
    rows = [
        {
            "status": "ok",
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "information_density_band": "high",
            "final_q6_decision_value": 500_000,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_decision_value_p90_under_by": 300_000,
            "v2_q6_space_pressure_p90": 0.10,
            "v2_q6_space_overflow_rate": 0.0,
            "final_q6_trimmed_tail_value": 0,
            "v2_matched": 10,
            "layout_conflict": True,
        }
        for _ in range(10)
    ]

    targets = module._q6_actionable_targets(rows)

    assert targets[0]["q6_plannable_truth"] == 10
    assert targets[0]["q6_plannable_misses"] == 10
    assert any(
        target["scope"] == "hero_map_profile"
        and target["recommended_next"] == "shipwreck_shape_residual_sampler"
        for target in targets
    )


def test_q6_space_diagnostics_separates_residual_from_space_pressure() -> None:
    module = _eval_module()
    rows = [
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 500_000,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_decision_value_p90_under_by": 300_000,
            "v2_q6_space_pressure_p90": 0.25,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 400_000,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_decision_value_p90_under_by": 200_000,
            "v2_q6_space_pressure_p90": 0.40,
            "v2_q6_space_overflow_rate": 0.0,
        },
        {
            "hero": "ethan",
            "map_family": "villa",
            "evidence_profile_key": "layout",
            "final_q6_decision_value": 300_000,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_decision_value_p90_under_by": 100_000,
            "v2_q6_space_pressure_p90": 1.20,
            "v2_q6_space_overflow_rate": 0.5,
        },
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "evidence_profile_key": "shape+layout",
            "final_q6_decision_value": 300_000,
            "q6_plannable_p90_misses_truth": False,
            "v2_q6_space_pressure_p90": 0.30,
            "v2_q6_space_overflow_rate": 0.0,
        },
    ]

    diagnostics = module._q6_space_diagnostics(rows)

    assert diagnostics["q6_plannable_miss_rows"] == 3
    assert diagnostics["low_space_pressure_miss_rows"] == 2
    assert diagnostics["high_space_pressure_miss_rows"] == 1
    assert diagnostics["recommended_next"] == "residual_q6_count_cell_sampler"
    assert diagnostics["groups"]["hero_map_profile"][0]["group"] == (
        "hero=aisha|map_family=shipwreck|evidence_profile_key=shape+layout"
    )
    assert diagnostics["groups"]["hero_map_profile"][0]["recommended_next"] == (
        "residual_q6_count_cell_sampler"
    )


def test_expand_cli_paths_supports_globs(tmp_path: Path) -> None:
    module = _eval_module()
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")

    assert module._expand_cli_paths([str(tmp_path / "*.json")]) == [
        first,
        second,
    ]


def test_evidence_stage_marks_early_rounds_separately() -> None:
    module = _eval_module()

    assert module._evidence_stage(1) == "early_1_2"
    assert module._evidence_stage(2) == "early_1_2"
    assert module._evidence_stage(3) == "mid_3_4"
    assert module._evidence_stage(5) == "full_5"


def test_information_density_combines_round_and_evidence_counts() -> None:
    module = _eval_module()

    score = module._information_density_score(
        {
            "capture_round": 4,
            "anchor_count": 3,
            "shape_target_count": 1,
            "category_target_count": 2,
            "category_exclusion_count": 1,
            "trusted_footprint_count": 5,
            "public_constraint_key": "max_quality",
        }
    )

    assert score == 29
    assert module._information_density_band(score) == "medium"
    assert module._information_density_band(17) == "low"
    assert module._information_density_band(18) == "medium"
    assert module._information_density_band(34) == "high"


def test_evidence_profile_key_summarizes_public_tool_and_layout() -> None:
    module = _eval_module()

    assert module._evidence_profile_key(
        {
            "public_constraint_key": "max_quality",
            "random_sample_avg_values": "n=6:avg=96897.66",
            "category_action_count": 2,
            "shape_target_count": 1,
            "trusted_footprint_count": 3,
        }
    ) == "public:max_quality+public:random_avg+tool:category+shape+layout"
    assert module._evidence_profile_key({}) == "basic"
    assert module._evidence_profile_key(
        {
            "random_sample_avg_values": "n=6:avg=2980.17",
            "random_sample_avg_signal_values": "",
            "shape_target_count": 1,
        }
    ) == "shape"


def test_aisha_bottom_row_risk_summary_is_diagnostic_only() -> None:
    module = _eval_module()

    summary = module._aisha_bottom_row_risk_summary(
        [
            {
                "hero": "aisha",
                "map_family": "shipwreck",
                "footprint_bottom_row": 17,
                "aisha_bottom_row_risk": True,
                "final_q6_decision_value": 500,
                "v2_q6_decision_value_p90": 100,
                "q6_plannable_p90_misses_truth": True,
                "v2_cells_p50_error": -40,
            },
            {
                "hero": "aisha",
                "map_family": "shipwreck",
                "footprint_bottom_row": 10,
                "aisha_bottom_row_risk": False,
                "final_q6_decision_value": 500,
                "v2_q6_decision_value_p90": 600,
                "q6_plannable_p90_misses_truth": False,
                "v2_cells_p50_error": -10,
            },
        ]
    )

    assert summary["bottom_row_threshold"] == 16
    assert summary["risk"]["q6_plannable_miss_rate"] == 1.0
    assert summary["below_threshold"]["q6_plannable_miss_rate"] == 0.0


def test_q6_shadow_reference_coverage_is_labeled_as_batch_scope() -> None:
    module = _eval_module()

    coverage = module._q6_shadow_reference_coverage(
        [{"hero": "aisha", "map_family": "shipwreck"}]
    )

    assert coverage["sample_scope"] == "all_available_batch_samples"
    assert coverage["ready"] is False
    assert coverage["total_needed"] == 54


def test_case_breakdown_separates_normal_early_tail_and_no_q6() -> None:
    module = _eval_module()

    rows = [
        {
            "calibration_eligible": True,
            "capture_round": 3,
            "evidence_stage": "mid_3_4",
            "information_density_band": "high",
            "v2_matched": True,
            "v2_decision_value_p50_error": -100,
            "v2_value_p90_covers_final": True,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 600,
            "q6_plannable_p90_misses_truth": False,
            "final_trimmed_tail_value": 0,
            "public_avg_cells_solution_band": "all_unique",
            "v2_cells_p50_error": -10,
        },
        {
            "calibration_eligible": False,
            "capture_round": 1,
            "evidence_stage": "early_1_2",
            "information_density_band": "low",
            "v2_matched": True,
            "v2_decision_value_p50_error": -300,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "final_trimmed_tail_value": 0,
            "public_avg_cells_solution_band": "ambiguous",
            "v2_cells_p50_error": -50,
        },
        {
            "calibration_eligible": True,
            "capture_round": 3,
            "evidence_stage": "mid_3_4",
            "information_density_band": "medium",
            "v2_matched": True,
            "v2_decision_value_p50_error": -700,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 200,
            "final_trimmed_tail_value": 900,
            "v2_cells_p50_error": -20,
        },
        {
            "calibration_eligible": True,
            "capture_round": 5,
            "evidence_stage": "full_5",
            "information_density_band": "medium",
            "v2_matched": True,
            "v2_decision_value_p50_error": -120,
            "final_q6_decision_value": 0,
            "v2_q6_decision_value_p90": 80,
            "final_trimmed_tail_value": 0,
            "diagnostics": "public_max_quality:5",
            "v2_cells_p50_error": -5,
        },
        {
            "calibration_eligible": True,
            "capture_round": 5,
            "evidence_stage": "full_5",
            "information_density_band": "high",
            "v2_matched": False,
            "v2_decision_value_p50_error": -400_000,
            "v2_value_p90_covers_final": False,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 0,
            "q6_plannable_p90_misses_truth": True,
            "final_trimmed_tail_value": 0,
        },
        {
            "calibration_eligible": True,
            "capture_round": 4,
            "evidence_stage": "mid_3_4",
            "information_density_band": "medium",
            "map_family": "hidden",
            "v2_matched": True,
            "v2_decision_value_p50_error": -900,
            "v2_value_p90_covers_final": False,
            "final_q6_decision_value": 500,
            "v2_q6_decision_value_p90": 100,
            "q6_plannable_p90_misses_truth": True,
            "final_trimmed_tail_value": 0,
            "v2_cells_p50_error": -15,
        },
    ]

    summary = module._case_breakdown_summary(rows)

    assert summary["normal_case"]["rows"] == 2
    assert summary["normal_case"]["decision_value_mae"] == 110
    assert summary["normal_case"]["q6_plannable_coverage"] == 1.0
    assert summary["early_diagnostic"]["rows"] == 1
    assert summary["early_diagnostic"]["cells_p50_mae"] == 50
    assert summary["single_round"]["rows"] == 1
    assert summary["tail_event"]["rows"] == 1
    assert summary["hidden_case"]["rows"] == 1
    assert summary["hidden_case"]["decision_value_mae"] == 900
    assert summary["no_q6_control"]["rows"] == 2
    assert summary["no_q6_control"]["no_q6_positive_rate"] == 1.0
    assert summary["zero_q6_proven"]["rows"] == 1
    assert summary["high_info_value_miss"]["rows"] == 1
    assert summary["high_info_q6_miss"]["q6_plannable_miss_rows"] == 1
    assert summary["avg_cells_unique"]["rows"] == 1
    assert summary["avg_cells_ambiguous"]["rows"] == 1
    assert summary["case_tag_counts"]["single_round"] == 1
    assert summary["case_tag_counts"]["hidden_case"] == 1
    assert summary["case_tag_counts"]["zero_q6_proven"] == 1


def test_public_info_row_summary_marks_modeled_pending_and_unknown_ids() -> None:
    module = _eval_module()
    events = SimpleNamespace(
        states=(
            SimpleNamespace(
                public_infos=(
                    SimpleNamespace(
                        info_id=200048,
                        value=1,
                        observed_items=(
                            SimpleNamespace(
                                item_id=1055001,
                                shape_code=11,
                                local_index=3,
                                quality=5,
                            ),
                        ),
                    ),
                    SimpleNamespace(
                        info_id=200013,
                        value=2.5,
                        observed_items=(),
                    ),
                    SimpleNamespace(
                        info_id=999999,
                        value=7,
                        observed_items=(),
                    ),
                ),
            ),
        ),
    )

    summary = module._public_info_row_summary(events)

    assert summary["public_info_ids"] == "200013:1;200048:1;999999:1"
    assert "modeled_global_max_quality:1" in summary["public_info_model_uses"]
    assert "modeled_soft_avg_cells:1" in summary["public_info_model_uses"]
    assert "unknown_pending_reference:1" in summary["public_info_model_uses"]
    assert summary["public_info_pending_model_ids"] == "999999"
    assert summary["public_info_unknown_ids"] == "999999"
    assert summary["public_info_needs_screenshot_ids"] == "999999"
    assert summary["public_info_items_with_item_id"] == "200048:1"


def test_public_info_semantics_summary_keeps_reference_gaps_separate() -> None:
    module = _eval_module()

    summary = module._public_info_semantics_summary(
        [
            {
                "file": "known.json",
                "public_info_ids": "200048:1;200013:2;200037:1",
            },
            {
                "file": "unknown.json",
                "public_info_ids": "999999:1",
            },
        ]
    )

    assert summary["events"] == 5
    assert summary["unique_ids"] == 4
    assert summary["modeled_hard_ids"] == [200048]
    assert summary["modeled_soft_ids"] == [200013, 200037]
    assert summary["pending_model_ids"] == [999999]
    assert summary["unknown_ids"] == [999999]
    assert summary["needs_screenshot_ids"] == [999999]
    by_id = {row["info_id"]: row for row in summary["by_id"]}
    assert by_id[200013]["reference"] == "known"
    assert by_id[999999]["reference"] == "missing"


def test_avg_cells_integer_candidates_can_distinguish_unique_and_ambiguous() -> None:
    module = _eval_module()

    unique = module._avg_cells_integer_candidates(
        86 / 29,
        min_count=1,
        max_count=50,
        min_cells=1,
        max_cells=240,
    )
    ambiguous = module._avg_cells_integer_candidates(
        86 / 29,
        min_count=1,
        max_count=80,
        min_cells=1,
        max_cells=240,
    )

    assert unique == [(29, 86)]
    assert ambiguous[:2] == [(29, 86), (58, 172)]


def test_public_avg_cells_uniqueness_summary_counts_status_bands() -> None:
    module = _eval_module()

    summary = module._public_avg_cells_uniqueness_summary(
        [
            {
                "file": "unique.json",
                "public_avg_cells_solution_band": "all_unique",
                "public_avg_cells_solution_statuses": "total_avg_cells:unique",
            },
            {
                "file": "ambiguous.json",
                "public_avg_cells_solution_band": "ambiguous",
                "public_avg_cells_solution_statuses": "q5_avg_cells:ambiguous",
            },
        ]
    )

    assert summary["band_counts"]["all_unique"] == 1
    assert summary["band_counts"]["ambiguous"] == 1
    assert summary["unique_rows"] == 1
    assert summary["ambiguous_rows"] == 1
    assert summary["examples"]["total_avg_cells:unique"] == ["unique.json"]


def test_evidence_parameter_audit_centralizes_thresholds_and_gate_activity() -> None:
    module = _eval_module()

    audit = module._evidence_parameter_audit_summary(
        [
            {
                "file": "aisha_deep_q6.json",
                "hero": "aisha",
                "map_family": "shipwreck",
                "evidence_profile_key": "shape+layout",
                "footprint_bottom_row": 13,
                "final_q6_decision_value": 500,
                "q6_plannable_p90_misses_truth": True,
                "random_sample_avg_profile_floor": 20_000.0,
                "random_sample_avg_signal_band": "signal",
                "public_avg_cells_solution_band": "all_unique",
                "public_avg_cells_solution_statuses": "total_avg_cells:unique",
            },
            {
                "file": "ethan_random_q6.json",
                "hero": "ethan",
                "map_family": "villa",
                "evidence_profile_key": "public:random_avg+layout",
                "final_q6_decision_value": 500,
                "q6_plannable_p90_misses_truth": False,
                "random_sample_avg_profile_floor": 20_000.0,
                "random_sample_avg_signal_band": "low_filtered",
                "public_avg_cells_solution_band": "ambiguous",
                "public_avg_cells_solution_statuses": "q5_avg_cells:ambiguous",
            },
        ]
    )

    assert audit["audit_only"] is True
    assert audit["random_sample_avg"]["profile_signal_floor"] == 20_000.0
    assert (
        audit["random_sample_avg"]["compiled_value_floor_signal_floor"]
        == 20_000.0
    )
    assert "evidence-profile routing" in audit["random_sample_avg"]["floor_scope_note"]
    assert audit["random_sample_avg"]["value_floor_factor"] == 0.95
    assert audit["random_sample_avg"]["soft_penalty"] == 0.10
    assert audit["random_sample_avg"]["signal_band_counts"] == {
        "low_filtered": 1,
        "signal": 1,
    }
    assert audit["public_avg_cells"]["unique_rows"] == 1
    assert audit["public_avg_cells"]["ambiguous_rows"] == 1
    assert audit["aisha_q6_position"]["shipwreck_deep_row_threshold"] == 13
    assert audit["aisha_q6_position"]["quality_only_deep_row_threshold"] == 13
    assert (
        audit["q6_gate_activity"]["aisha_shipwreck_deep_v1"]["active_rows"]
        == 1
    )
    assert (
        audit["q6_gate_activity"]["ethan_villa_random_avg_v1"]["active_rows"]
        == 1
    )
    assert any(
        row["parameter"] == "RANDOM_SAMPLE_VALUE_FLOOR_*"
        for row in audit["change_requires_confirmation"]
    )


def test_low_random_sample_avg_does_not_change_problem_profile() -> None:
    module = _eval_module()
    base = {
        "diagnostics": (),
        "category_targets": (),
        "shape_targets": (SimpleNamespace(),),
        "layout": SimpleNamespace(trusted_footprint_count=1),
    }

    low = module._evidence_profile_key_from_problem(
        SimpleNamespace(**base, random_sample_avg_values=((6, 2_980.17),))
    )
    high = module._evidence_profile_key_from_problem(
        SimpleNamespace(**base, random_sample_avg_values=((6, 73_208.22),))
    )

    assert low == "shape+layout"
    assert high == "public:random_avg+shape+layout"


def test_random_sample_avg_profile_floor_is_configurable() -> None:
    module = _eval_module()
    problem = SimpleNamespace(
        diagnostics=(),
        category_targets=(),
        shape_targets=(SimpleNamespace(),),
        layout=SimpleNamespace(trusted_footprint_count=1),
        random_sample_avg_values=((6, 12_000.0),),
    )

    assert module._evidence_profile_key_from_problem(
        problem,
        random_sample_avg_profile_floor=20_000.0,
    ) == "shape+layout"
    assert module._evidence_profile_key_from_problem(
        problem,
        random_sample_avg_profile_floor=10_000.0,
    ) == "public:random_avg+shape+layout"


def test_random_sample_avg_signal_band_marks_filtered_values() -> None:
    module = _eval_module()

    assert module._random_sample_avg_signal_band({}) == "none"
    assert module._random_sample_avg_signal_band(
        {
            "random_sample_avg_values": "n=6:avg=12000.00",
            "random_sample_avg_signal_values": "",
        }
    ) == "low_filtered"
    assert module._random_sample_avg_signal_band(
        {
            "random_sample_avg_values": "n=6:avg=72000.00",
            "random_sample_avg_signal_values": "n=6:avg=72000.00",
        }
    ) == "signal"


def test_capture_round_uses_cumulative_actions_at_settlement() -> None:
    module = _eval_module()
    events = SimpleNamespace(
        states=(
            SimpleNamespace(round_no=1, action_results=(1,)),
            SimpleNamespace(round_no=2, action_results=(1, 2)),
            SimpleNamespace(round_no=3, action_results=(1, 2, 3, 4)),
        ),
    )

    assert module._capture_round(events) == 4


def test_category_action_combo_uses_first_seen_order() -> None:
    module = _eval_module()
    events = SimpleNamespace(
        states=(
            SimpleNamespace(
                action_results=(
                    SimpleNamespace(action_id=100153),
                    SimpleNamespace(action_id=100158),
                ),
            ),
            SimpleNamespace(
                action_results=(
                    SimpleNamespace(action_id=100153),
                    SimpleNamespace(action_id=100151),
                    SimpleNamespace(action_id=100129),
                ),
            ),
        ),
    )

    assert (
        module._category_action_combo(events)
        == "100153:时尚;100158:能源;100151:家具"
    )


def test_presolve_unreachable_exact_bucket_formatter() -> None:
    module = _eval_module()
    problem = SimpleNamespace(
        map_id=2401,
        bucket_targets={
            6: SimpleNamespace(count_exact=1, total_cells_exact=16),
            5: SimpleNamespace(count_exact=2, total_cells_exact=None),
        },
    )
    payload = {
        "maps": {
            "2401": {
                "6": {
                    "1": [12],
                },
            },
        },
    }

    assert module._format_presolve_unreachable_exact_buckets(
        problem,
        payload,
    ) == "q6:count=1,cells=16"
