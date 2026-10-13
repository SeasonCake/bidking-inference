from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _summary_module():
    path = ROOT / "scripts" / "summarize_live_model_eval.py"
    spec = importlib.util.spec_from_file_location("summarize_live_model_eval", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_dedupes_latest_row_by_file() -> None:
    module = _summary_module()

    summary = module.summarize(
        [
            {
                "ts": 1,
                "file": "a.json",
                "final_value": 100,
                "decision_value_p50_error": -90,
            },
            {
                "ts": 2,
                "file": "a.json",
                "final_value": 100,
                "decision_value_p50_error": -10,
            },
        ]
    )

    assert summary["raw_rows"] == 2
    assert summary["rows"] == 1
    assert summary["deduped_rows"] == 1
    assert summary["decision_value_mae"] == 10


def test_filter_rows_since_uses_selected_time_window() -> None:
    module = _summary_module()

    rows, cutoff = module._filter_rows_since(
        [
            {"ts": 10, "file": "old.json"},
            {"ts": 100, "file": "edge.json"},
            {"ts": 101, "file": "new.json"},
            {"file": "missing-ts.json"},
        ],
        since_hours=1,
        now=3700,
    )

    assert cutoff == 100
    assert [row["file"] for row in rows] == ["edge.json", "new.json"]


def test_main_brief_since_hours_filters_model_eval_and_error_logs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _summary_module()
    model_eval = tmp_path / "model_eval.jsonl"
    error_log = tmp_path / "monitor_errors.jsonl"
    rows = [
        {
            "ts": 10,
            "file": "old.json",
            "final_value": 1,
            "v3_practical_available": True,
        },
        {
            "ts": 100,
            "file": "edge.json",
            "final_value": 500_000,
            "final_decision_value": 500_000,
        },
        {
            "ts": 101,
            "file": "new.json",
            "final_value": 700_000,
            "final_decision_value": 700_000,
            "final_q6_decision_value": 200_000,
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_affects_bid": False,
            "v3_practical_active": False,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_confidence": "medium",
            "v3_practical_source_lanes": "formal_value+prior_q6_floor",
            "v3_practical_risk_flags": "q6_prior_floor_watch",
            "v3_practical_baseline_formal_decision_value_p90": 500_000,
            "v3_practical_formal_decision_value_p90": 800_000,
            "v3_practical_baseline_q6_formal_decision_value_p90": 100_000,
            "v3_practical_q6_formal_decision_value_p90": 250_000,
        },
    ]
    errors = [
        {"ts": 50, "name": "old_error.json", "error_type": "ValueError"},
        {"ts": 100, "name": "edge_error.json", "error_type": "RuntimeError"},
    ]
    model_eval.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    error_log.write_text(
        "\n".join(json.dumps(row) for row in errors) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module.time, "time", lambda: 3700.0)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "summarize_live_model_eval.py",
            str(model_eval),
            "--brief",
            "--since-hours",
            "1",
        ],
    )

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)

    assert out["window"]["input_rows"] == 3
    assert out["window"]["selected_rows"] == 2
    assert out["window"]["input_monitor_error_rows"] == 2
    assert out["window"]["selected_monitor_error_rows"] == 1
    assert out["rows"] == 2
    assert out["v3_practical"]["status"] == "fields_present"
    assert out["v3_practical"]["rows"] == 1
    assert out["v3_practical"]["latest_rows"][0]["file"] == "new.json"
    assert out["v3_practical"]["latest_rows"][0]["delta_p90"] == 300_000


def test_summarize_recomputes_replacement_decision_error_for_old_rows() -> None:
    module = _summary_module()

    summary = module.summarize(
        [
            {
                "ts": 1,
                "file": "tail.json",
                "final_value": 1_000_000,
                "final_decision_value": 600_000,
                "final_decision_value_with_tail_replacement": 650_000,
                "decision_value_p50": 550_000,
                "decision_value_p90": 800_000,
                "decision_value_p50_error": -450_000,
            },
        ]
    )

    assert summary["decision_value_mae"] == 100_000


def test_summarize_recomputes_raw_decision_error_when_truth_fields_are_absent() -> None:
    module = _summary_module()

    row = {
        "ts": 1,
        "file": "raw-only.json",
        "final_value": 1_000_000,
        "decision_value_p50": 700_000,
        "decision_value_p90": 1_100_000,
        "decision_value_p50_error": 0,
    }
    derived = module._with_derived_decision_errors(row)
    summary = module.summarize([row])

    assert derived["decision_value_truth"] == 1_000_000
    assert derived["decision_value_truth_source"] == "raw"
    assert "decision_value_p50_error_vs_formal" not in derived
    assert derived["decision_value_p50_error_vs_raw"] == -300_000
    assert summary["decision_value_mae"] == 300_000


def test_summarize_includes_monitor_error_log_summary() -> None:
    module = _summary_module()

    summary = module.summarize(
        [],
        monitor_error_rows=[
            {
                "ts": 1,
                "path": "C:/captures/a.json",
                "name": "a.json",
                "fingerprint": {"size": 10, "mtime_ns": 100},
                "error_type": "ValueError",
                "error": "invalid frame length 123",
            },
            {
                "ts": 2,
                "path": "C:/captures/a.json",
                "name": "a.json",
                "fingerprint": {"size": 10, "mtime_ns": 100},
                "error_type": "ValueError",
                "error": "invalid frame length 123",
            },
            {
                "ts": 3,
                "path": "C:/captures/b.json",
                "name": "b.json",
                "fingerprint": {"size": 20, "mtime_ns": 200},
                "error_type": "RuntimeError",
                "error": "bad packet",
            },
        ],
    )

    errors = summary["monitor_errors"]
    assert errors["rows"] == 3
    assert errors["unique_file_fingerprints"] == 2
    assert errors["error_type_counts"] == {"RuntimeError": 1, "ValueError": 2}
    assert errors["latest"][0]["name"] == "b.json"
    assert errors["latest"][0]["error_type"] == "RuntimeError"


def test_summarize_reports_collection_readiness_gaps() -> None:
    module = _summary_module()

    summary = module.summarize(
        [
            {
                "ts": 1,
                "file": "a.json",
                "hero": "aisha",
                "map_id": 2401,
                "round": 1,
                "monitor_processing_seconds": 1.25,
                "monitor_n_trials": 80,
                "monitor_shadow_trials": 80,
                "monitor_roi_trials": 0,
                "final_value": 100,
                "final_cells": 10,
                "final_q6_value": 0,
                "decision_value_p50": 120,
                "raw_minus_decision_p90": 0,
                "category_target_count": 1,
                "category_exclusion_count": 0,
                "layout_conflict": False,
                "layout_conflict_root": "",
            },
            {
                "ts": 2,
                "file": "b.json",
                "hero": "ethan",
                "map_id": 2501,
                "round": 4,
                "monitor_processing_seconds": 3.75,
                "monitor_n_trials": 500,
                "monitor_shadow_trials": 80,
                "monitor_roi_trials": 250,
                "final_value": 200,
                "final_cells": 12,
                "final_q6_value": 80,
                "final_q6_decision_value": 80,
                "final_q6_tail_replacement_value": 93_000,
                "final_q6_tail_replacement_count": 1,
                "final_q6_tail_replacement_items": "tail:1039000->93000",
                "final_q6_tail_replacement_source": "map_weighted_p50",
                "final_q6_decision_value_with_tail_replacement": 93_080,
                "q6_tail_replacement_p90_misses_truth": True,
                "v2_q6_tail_replacement_decision_value_p90_under_by": 93_050,
                "v2_q6_tail_replacement_estimate_p90": 93_000,
                "q6_tail_replacement_estimate_p90_misses_truth": True,
                "v2_q6_tail_replacement_estimate_p90_under_by": 80,
                "final_top_item_quality": 6,
                "final_top_item_cells": 9,
                "decision_value_p50": 180,
                "q6_below_drop_prior": True,
                "q6_count_cell_prior_risk": True,
                "q6_count_cell_prior_floor_value": 486_510,
                "q6_practical_gate": "shipwreck_positive_net",
                "q6_practical_p90": 486_510,
                "q6_practical_gate_hit": True,
                "q6_practical_gate_under_before": True,
                "q6_practical_gate_covered_after": True,
                "q6_practical_gate_helped": True,
                "q6_practical_gate_false_positive_proxy": False,
                "q6_practical_p90_under_by": 0,
                "q6_residual_boost_shadow_active": True,
                "q6_residual_boost_shadow_under_before": True,
                "q6_residual_boost_shadow_covered_after": True,
                "q6_residual_boost_shadow_helped": True,
                "q6_residual_boost_shadow_false_positive_proxy": False,
                "q6_residual_boost_shadow_q6_p90_delta": 120,
                "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
                "q6_residual_deep_floor_shadow_active": True,
                "q6_residual_deep_floor_shadow_under_before": True,
                "q6_residual_deep_floor_shadow_covered_after": True,
                "q6_residual_deep_floor_shadow_helped": True,
                "q6_residual_deep_floor_shadow_false_positive_proxy": False,
                "q6_residual_deep_floor_shadow_q6_p90_delta": 180,
                "q6_aisha_bottom_row_risk": True,
                "q6_quality_only_local_count": 1,
                "q6_quality_only_deep_local_risk": True,
                "q6_p90_misses_truth": True,
                "v2_q6_value_p90": 30,
                "raw_minus_decision_p90": 300_000,
                "anchor_count": 3,
                "category_target_count": 2,
                "category_exclusion_count": 1,
                "random_sample_avg_values": "n=6:avg=96897.66",
                "layout_conflict": True,
                "posterior_diagnostics": (
                    "footprint_overlap_cells:2;footprint_count_relaxed:3->1"
                ),
            },
            {
                "ts": 3,
                "file": "c.json",
                "map_id": 2402,
                "monitor_processing_seconds": 9.0,
                "monitor_n_trials": 500,
                "monitor_shadow_trials": 1,
                "monitor_roi_trials": 0,
                "final_value": 300,
                "final_cells": 14,
                "raw_minus_decision_p90": 900_000,
                "layout_conflict": False,
                "layout_conflict_root": "",
            },
        ],
        target_per_hero_family=2,
        hidden_target_per_hero=1,
    )

    readiness = summary["collection_readiness"]
    assert readiness["ready"] is False
    assert readiness["total_needed"] == 8
    assert readiness["hidden_target_per_hero"] == 1
    assert summary["monitor_processing_seconds_median"] == 3.75
    assert summary["monitor_processing_seconds_p75"] is None
    assert summary["monitor_n_trials_values"] == {"80": 1, "500": 2}
    assert summary["monitor_shadow_trials_values"] == {"1": 1, "80": 2}
    assert summary["monitor_roi_trials_values"] == {"0": 2, "250": 1}
    assert summary["next_sampling_targets"][0] == {
        "hero": "aisha",
        "map_family": "hidden",
        "needed": 1,
        "reason": "hidden_cold_start",
    }
    assert summary["log_quality"]["missing_hero"] == 1
    assert summary["log_quality"]["missing_q6_truth_fields"] == 1
    assert summary["q6_below_drop_prior_count"] == 1
    assert summary["q6_count_cell_prior_risk_count"] == 1
    assert summary["q6_count_cell_prior_floor_median"] == 486_510
    assert summary["q6_practical_gate_count"] == 1
    assert summary["q6_practical_p90_median"] == 486_510
    assert summary["q6_practical_gate_under_before_count"] == 1
    assert summary["q6_practical_gate_helped_count"] == 1
    assert summary["q6_practical_gate_false_positive_proxy_count"] == 0
    assert summary["q6_practical_p90_under_by_median"] == 0
    assert summary["q6_residual_boost_shadow_active_count"] == 1
    assert summary["q6_residual_boost_shadow_under_before_count"] == 1
    assert summary["q6_residual_boost_shadow_helped_count"] == 1
    assert summary["q6_residual_boost_shadow_false_positive_proxy_count"] == 0
    assert summary["q6_residual_boost_shadow_q6_p90_delta_median"] == 120
    assert summary["q6_residual_deep_floor_shadow_active_count"] == 1
    assert summary["q6_residual_deep_floor_shadow_under_before_count"] == 1
    assert summary["q6_residual_deep_floor_shadow_helped_count"] == 1
    assert summary["q6_residual_deep_floor_shadow_false_positive_proxy_count"] == 0
    assert summary["q6_residual_deep_floor_shadow_q6_p90_delta_median"] == 180
    assert summary["q6_residual_hidden_floor_shadow_active_count"] == 0
    assert summary["q6_residual_hidden_floor_shadow_helped_count"] == 0
    assert summary["q6_residual_villa_floor_shadow_active_count"] == 0
    assert summary["q6_residual_villa_floor_shadow_helped_count"] == 0
    readiness_summary = summary["q6_shadow_candidate_readiness"]
    assert readiness_summary["profile_b5"]["status"] == "needs_live_samples"
    assert readiness_summary["profile_b5"]["tracked_rows"] == 0
    assert readiness_summary["aisha_deep_floor1"]["status"] == "needs_live_samples"
    assert readiness_summary["aisha_deep_floor1"]["tracked_rows"] == 1
    assert readiness_summary["aisha_deep_floor1"]["active_rows"] == 1
    assert readiness_summary["aisha_deep_floor1"]["under_before_rows"] == 1
    assert readiness_summary["aisha_deep_floor1"]["helped_rows"] == 1
    assert readiness_summary["aisha_deep_floor1"]["still_missed_rows"] == 0
    assert readiness_summary["aisha_deep_floor1"]["still_missed_rate"] == 0.0
    assert (
        readiness_summary["aisha_deep_floor1"]["false_positive_proxy_rows"] == 0
    )
    assert readiness_summary["aisha_deep_floor1"]["q6_p90_delta_median"] == 180
    assert readiness_summary["aisha_hidden_floor15"]["status"] == "needs_live_samples"
    assert readiness_summary["aisha_hidden_floor15"]["tracked_rows"] == 0
    assert readiness_summary["aisha_villa_floor05"]["status"] == "needs_live_samples"
    assert readiness_summary["aisha_villa_floor05"]["tracked_rows"] == 0
    assert summary["q6_aisha_bottom_row_risk_count"] == 1
    assert summary["q6_quality_only_local_count"] == 1
    assert summary["q6_quality_only_deep_local_risk_count"] == 1
    assert summary["q6_tail_replacement_value_count"] == 1
    assert summary["q6_tail_replacement_value_median"] == 93_000
    assert summary["q6_tail_replacement_p90_miss_count"] == 1
    assert summary["q6_tail_replacement_p90_under_by_median"] == 93_050
    assert summary["q6_tail_replacement_estimate_p90_miss_count"] == 1
    assert summary["q6_tail_replacement_estimate_p90_under_by_median"] == 80
    assert summary["q6_p90_miss_count"] == 1
    assert summary["q6_p90_under_by_median"] == 50
    assert summary["category_target_rows"] == 2
    assert summary["category_exclusion_rows"] == 1
    assert summary["category_target_total"] == 3
    assert summary["category_exclusion_total"] == 1
    q6_causes = {
        row["cause"]: row["n"]
        for row in summary["q6_miss_root_causes"]
    }
    assert q6_causes["below_drop_prior"] == 1
    assert q6_causes["q6_top_medium"] == 1
    assert summary["layout_conflict_count"] == 1
    assert summary["layout_conflict_root_causes"][0]["cause"] == "footprint_overlap"
    assert summary["raw_ceiling_gap_median"] == 300_000
    assert summary["raw_ceiling_gap_250k_count"] == 2
    assert summary["raw_ceiling_gap_700k_count"] == 1
    assert any(
        row["hero"] == "aisha"
        and row["map_family"] == "villa"
        and row["n"] == 1
        and row["needed"] == 1
        for row in readiness["groups"]
    )
    assert {
        row["evidence_stage"]: row["n"]
        for row in summary["groups"]["evidence_stage"]
    }["mid_3_4"] == 1
    assert any(
        row["information_density_band"] == "medium"
        and row["n"] == 1
        for row in summary["groups"]["information_density"]
    )
    assert any(
        row["hero_information_density"] == "ethan|medium"
        and row["n"] == 1
        for row in summary["groups"]["hero_information_density"]
    )
    assert any(
        row["evidence_profile_key"] == "public:random_avg+tool:category"
        and row["n"] == 1
        for row in summary["groups"]["evidence_profile"]
    )
    assert any(
        row["map_family"] == "villa"
        and row["raw_ceiling_gap_median"] == 450_000
        and row["layout_overlap_rate"] == 0.0
        for row in summary["groups"]["map_family"]
    )
    assert any(
        row["hero"] == "ethan"
        and row["layout_overlap_rate"] == 1.0
        and row["q6_prior_risk_rate"] == 1.0
        and row["q6_practical_gate_rate"] == 1.0
        and row["q6_residual_boost_shadow_active_rate"] == 1.0
        and row["q6_residual_deep_floor_shadow_active_rate"] == 1.0
        for row in summary["groups"]["hero"]
    )
    assert summary["q6_practical_gate"]["map_family"][0]["gated_rows"] == 1
    assert summary["q6_practical_gate"]["map_family"][0]["helped_rows"] == 1
    assert any(
        row["hero_map_family"] == "hero=ethan|map_family=shipwreck"
        and row["gated_rows"] == 1
        for row in summary["q6_practical_gate"]["hero_map_family"]
    )
    assert summary["q6_residual_boost_shadow"]["map_family"][0]["active_rows"] == 1
    assert summary["q6_residual_boost_shadow"]["map_family"][0]["helped_rows"] == 1
    assert summary["q6_residual_boost_shadow"]["map_family"][0][
        "q6_p90_delta_median"
    ] == 120
    assert summary["q6_residual_deep_floor_shadow"]["map_family"][0][
        "active_rows"
    ] == 1
    assert summary["q6_residual_deep_floor_shadow"]["map_family"][0][
        "q6_p90_delta_median"
    ] == 180
    assert summary["q6_residual_villa_floor_shadow"]["map_family"][0][
        "active_rows"
    ] == 0
    assert any(
        row["hero"] == "ethan"
        and row["map_family"] == "hidden"
        and row["n"] == 0
        and row["needed"] == 1
        for row in readiness["priority_needs"]
    )


def test_map_family_groups_hidden_and_late_map_prefixes() -> None:
    module = _summary_module()

    summary = module.summarize(
        [
            {"file": "a.json", "hero": "aisha", "map_id": 2601, "final_value": 1},
            {"file": "b.json", "hero": "aisha", "map_id": 3401, "final_value": 1},
            {"file": "c.json", "hero": "aisha", "map_id": 4510, "final_value": 1},
        ]
    )

    groups = {
        row["map_family"]: row["n"]
        for row in summary["groups"]["map_family"]
    }
    assert groups["hidden"] == 1
    assert groups["villa"] == 1
    assert groups["shipwreck"] == 1


def test_brief_summary_keeps_live_review_signals_compact() -> None:
    module = _summary_module()

    summary = {
        "window": {
            "since_hours": 24.0,
            "selected_rows": 303,
        },
        "rows": 303,
        "raw_rows": 422,
        "deduped_rows": 119,
        "valid": 303,
        "decision_value_mae": 470_430,
        "decision_value_median_abs_error": 286_826,
        "raw_value_mae": 348_541,
        "warehouse_mae": 20,
        "layout_fit_mae": 7,
        "monitor_processing_seconds_median": 6.982,
        "monitor_processing_seconds_p75": 13.803,
        "monitor_n_trials_values": {"500": 163},
        "monitor_shadow_trials_values": {"80": 163},
        "monitor_roi_trials_values": {"250": 163},
        "log_quality": {"missing_hero": 2},
        "monitor_errors": {"rows": 1},
        "collection_readiness": {
            "ready": True,
            "total_needed": 0,
            "priority_needs": [],
            "groups": [
                {
                    "hero": "aisha",
                    "map_family": "shipwreck",
                    "n": 60,
                    "target": 30,
                    "needed": 0,
                }
            ],
        },
        "q6_shadow_sampling_progress": {
            "ready": True,
            "total_needed": 0,
            "priority_needs": [],
            "targets": [
                {
                    "hero": "aisha",
                    "map_family": "shipwreck",
                    "n": 60,
                    "target": 20,
                    "needed": 0,
                }
            ],
        },
        "q6_shadow_candidate_readiness": {
            "aisha_deep_floor1": {
                "status": "candidate_for_review",
                "target_ready": True,
                "target_total_needed": 0,
                "tracked_rows": 163,
                "active_rows": 30,
                "active_no_q6_rows": 0,
                "under_before_rows": 21,
                "helped_rows": 15,
                "still_missed_rows": 6,
                "helped_rate": 0.7143,
                "false_positive_proxy_rows": 0,
                "false_positive_proxy_rate_active": 0.0,
                "q6_p90_delta_median": 674_579,
                "priority_needs": [],
            }
        },
        "next_sampling_targets": [],
        "q6_p90_miss_count": 101,
        "q6_p90_under_by_median": 346_051,
        "q6_tail_replacement_value_count": 3,
        "q6_tail_replacement_value_median": 93_000,
        "q6_tail_replacement_p90_miss_count": 1,
        "q6_tail_replacement_p90_under_by_median": 93_050,
        "q6_tail_replacement_estimate_p90_miss_count": 1,
        "q6_tail_replacement_estimate_p90_under_by_median": 80,
        "q6_false_low_count": 16,
        "q6_below_drop_prior_count": 16,
        "q6_practical_gate_count": 46,
        "q6_practical_gate_helped_count": 9,
        "q6_practical_gate_false_positive_proxy_count": 5,
        "q6_miss_root_causes": [{"cause": "q6_top_unknown_cells", "n": 43}],
        "layout_conflict_count": 65,
        "relaxed_exact_count": 21,
        "layout_conflict_root_causes": [{"cause": "footprint_overlap", "n": 62}],
        "groups": {"hero": [{"hero": "aisha", "n": 161}]},
    }

    brief = module.brief_summary(summary)

    assert brief["rows"] == 303
    assert brief["window"] == {
        "since_hours": 24.0,
        "selected_rows": 303,
    }
    assert brief["performance"] == {
        "processing_seconds_median": 6.982,
        "processing_seconds_p75": 13.803,
        "n_trials": {"500": 163},
        "shadow_trials": {"80": 163},
        "roi_trials": {"250": 163},
    }
    assert brief["collection_readiness"]["ready"] is True
    assert brief["collection_readiness"]["groups"][0]["hero"] == "aisha"
    assert brief["q6_shadow_sampling_progress"]["groups"][0]["target"] == 20
    assert brief["q6_shadow_candidate_readiness"]["aisha_deep_floor1"][
        "status"
    ] == "candidate_for_review"
    assert brief["q6"]["top_miss_root_causes"] == [
        {"cause": "q6_top_unknown_cells", "n": 43}
    ]
    assert brief["q6"]["q6_tail_replacement_value_count"] == 3
    assert brief["q6"]["q6_tail_replacement_p90_under_by_median"] == 93_050
    assert (
        brief["q6"]["q6_tail_replacement_estimate_p90_under_by_median"] == 80
    )
    assert brief["layout"]["top_conflict_root_causes"] == [
        {"cause": "footprint_overlap", "n": 62}
    ]
    assert brief["v3_practical"] == {}
    assert "groups" not in brief


def test_brief_summary_keeps_empty_v3_practical_compact() -> None:
    module = _summary_module()

    brief = module.brief_summary(
        {
            "v3_practical": {
                "status": "no_v3_practical_fields",
                "note": "当前选中的 model_eval 行没有 v3_practical_* 字段",
                "rows": 0,
                "available_rows": 0,
                "ready_rows": 0,
                "candidate_rows": 0,
                "active_rows": 0,
                "affects_bid_rows": 0,
                "formal_p90": {"practical_p90_median": None},
                "raise_watch_review": {"rows": 0},
            }
        }
    )

    assert brief["v3_practical"] == {
        "status": "no_v3_practical_fields",
        "note": "当前选中的 model_eval 行没有 v3_practical_* 字段",
        "rows": 0,
        "available_rows": 0,
        "ready_rows": 0,
        "candidate_rows": 0,
        "active_rows": 0,
        "affects_bid_rows": 0,
    }


def test_summarize_v3_practical_distinguishes_empty_window_from_old_fields() -> None:
    module = _summary_module()

    empty = module.summarize([])
    old_fields = module.summarize([{"ts": 1, "file": "old.json", "final_value": 1}])

    assert empty["v3_practical"]["status"] == "no_evaluable_rows"
    assert "没有可评估" in empty["v3_practical"]["note"]
    assert old_fields["v3_practical"]["status"] == "no_v3_practical_fields"
    assert "没有 v3_practical_* 字段" in old_fields["v3_practical"]["note"]


def test_summarize_and_brief_include_v3_practical_shadow_review() -> None:
    module = _summary_module()

    rows = [
        {
            "ts": 1,
            "file": "hit.json",
            "final_value": 500_000,
            "final_decision_value": 500_000,
            "final_q6_decision_value": 200_000,
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_active": False,
            "v3_practical_affects_bid": False,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_confidence": "medium",
            "v3_practical_source": "q6_prior_floor",
            "v3_practical_source_lanes": "formal_value+prior_q6_floor",
            "v3_practical_risk_flags": "q6_prior_floor_watch,value_floor_candidate",
            "v3_practical_baseline_formal_decision_value_p50": 300_000,
            "v3_practical_formal_decision_value_p50": 300_000,
            "v3_practical_delta_formal_decision_value_p50": 0,
            "v3_practical_baseline_formal_decision_value_p90": 300_000,
            "v3_practical_formal_decision_value_p90": 600_000,
            "v3_practical_baseline_q6_formal_decision_value_p50": 100_000,
            "v3_practical_q6_formal_decision_value_p50": 100_000,
            "v3_practical_delta_q6_formal_decision_value_p50": 0,
            "v3_practical_baseline_q6_formal_decision_value_p90": 100_000,
            "v3_practical_q6_formal_decision_value_p90": 250_000,
        },
        {
            "ts": 2,
            "file": "false-alarm.json",
            "final_value": 600_000,
            "final_decision_value": 600_000,
            "final_q6_decision_value": 0,
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_active": False,
            "v3_practical_affects_bid": False,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_confidence": "low",
            "v3_practical_source": "formal_value",
            "v3_practical_source_lanes": "formal_value,underestimate_repair",
            "v3_practical_risk_flags": "underestimate_repair_candidate",
            "v3_practical_baseline_formal_decision_value_p50": 600_000,
            "v3_practical_formal_decision_value_p50": 620_000,
            "v3_practical_delta_formal_decision_value_p50": 20_000,
            "v3_practical_baseline_formal_decision_value_p90": 800_000,
            "v3_practical_formal_decision_value_p90": 1_300_000,
            "v3_practical_baseline_q6_formal_decision_value_p50": 0,
            "v3_practical_q6_formal_decision_value_p50": 0,
            "v3_practical_delta_q6_formal_decision_value_p50": 0,
            "v3_practical_baseline_q6_formal_decision_value_p90": 0,
            "v3_practical_q6_formal_decision_value_p90": 0,
        },
        {
            "ts": 3,
            "file": "still-missed.json",
            "final_value": 900_000,
            "final_decision_value": 900_000,
            "final_q6_decision_value": 300_000,
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_active": False,
            "v3_practical_affects_bid": False,
            "v3_practical_recommendation": "ceiling_watch",
            "v3_practical_confidence": "medium",
            "v3_practical_source": "q6_value_residual",
            "v3_practical_source_lanes": "formal_value+q6_value_residual",
            "v3_practical_risk_flags": "q6_value_ceiling_watch",
            "v3_practical_baseline_formal_decision_value_p50": 700_000,
            "v3_practical_formal_decision_value_p50": 700_000,
            "v3_practical_delta_formal_decision_value_p50": 0,
            "v3_practical_baseline_formal_decision_value_p90": 700_000,
            "v3_practical_formal_decision_value_p90": 800_000,
            "v3_practical_baseline_q6_formal_decision_value_p50": 200_000,
            "v3_practical_q6_formal_decision_value_p50": 220_000,
            "v3_practical_delta_q6_formal_decision_value_p50": 20_000,
            "v3_practical_baseline_q6_formal_decision_value_p90": 200_000,
            "v3_practical_q6_formal_decision_value_p90": 250_000,
        },
    ]

    summary = module.summarize(rows)
    practical = summary["v3_practical"]

    assert practical["rows"] == 3
    assert practical["status"] == "fields_present"
    assert practical["note"] is None
    assert practical["available_rows"] == 3
    assert practical["ready_rows"] == 3
    assert practical["candidate_rows"] == 3
    assert practical["active_rows"] == 0
    assert practical["affects_bid_rows"] == 0
    assert practical["recommendation_counts"] == {
        "raise_watch": 2,
        "ceiling_watch": 1,
    }
    assert practical["source_lane_counts"]["formal_value"] == 3
    assert practical["risk_flag_counts"]["q6_prior_floor_watch"] == 1
    assert practical["risk_flag_counts"]["underestimate_repair_candidate"] == 1
    assert [row["file"] for row in practical["latest_rows"]] == [
        "still-missed.json",
        "false-alarm.json",
        "hit.json",
    ]
    assert practical["latest_rows"][0]["recommendation"] == "ceiling_watch"
    assert practical["latest_rows"][0]["baseline_p90"] == 700_000
    assert practical["latest_rows"][0]["practical_p90"] == 800_000
    assert practical["latest_rows"][0]["practical_under_by"] == 100_000
    assert practical["latest_rows"][0]["q6_under_by"] == 50_000
    assert practical["top_under_rows"] == [
        {
            "ts": 3,
            "file": "still-missed.json",
            "hero": None,
            "map_id": None,
            "round": None,
            "action_round": None,
            "recommendation": "ceiling_watch",
            "confidence": "medium",
            "source_lanes": "formal_value+q6_value_residual",
            "risk_flags": "q6_value_ceiling_watch",
            "active": False,
            "affects_bid": False,
            "truth": 900_000,
            "baseline_p90": 700_000,
            "practical_p90": 800_000,
            "delta_p90": 100_000,
            "baseline_under_by": 200_000,
            "practical_under_by": 100_000,
            "q6_truth": 300_000,
            "baseline_q6_p90": 200_000,
            "practical_q6_p90": 250_000,
            "delta_q6_p90": 50_000,
            "q6_under_by": 50_000,
        }
    ]

    formal_p90 = practical["formal_p90"]
    assert formal_p90["baseline_p90_median"] == 700_000
    assert formal_p90["practical_p90_median"] == 800_000
    assert formal_p90["delta_p90_median"] == 300_000
    assert formal_p90["baseline_under_rows"] == 2
    assert formal_p90["baseline_coverage_rate"] == 0.3333
    assert formal_p90["baseline_under_by_median"] == 200_000
    assert formal_p90["practical_under_rows"] == 1
    assert formal_p90["practical_coverage_rate"] == 0.6667
    assert formal_p90["practical_under_by_median"] == 100_000
    assert formal_p90["helped_rows"] == 1
    assert formal_p90["still_missed_rows"] == 1
    assert formal_p90["worsened_rows"] == 0
    assert formal_p90["under_by_reduction_median"] == 100_000
    assert formal_p90["practical_extreme_over_rate"] == 0.3333

    q6_p90 = practical["q6_formal_p90"]
    assert q6_p90["baseline_under_rows"] == 2
    assert q6_p90["practical_under_rows"] == 1
    assert q6_p90["helped_rows"] == 1
    assert q6_p90["still_missed_rows"] == 1

    raise_watch = practical["raise_watch_review"]
    assert raise_watch["rows"] == 2
    assert raise_watch["evaluated_rows"] == 2
    assert raise_watch["hit_rows"] == 1
    assert raise_watch["false_alarm_rows"] == 1
    assert raise_watch["extreme_over_rows"] == 1
    assert raise_watch["misleading_rows"] == 1
    assert raise_watch["misleading_rate"] == 0.5

    brief = module.brief_summary(summary)
    assert brief["v3_practical"]["status"] == "fields_present"
    assert brief["v3_practical"]["formal_p90"]["helped_rows"] == 1
    assert brief["v3_practical"]["affects_bid_rows"] == 0
    assert brief["v3_practical"]["latest_rows"][0]["file"] == "still-missed.json"
    assert brief["v3_practical"]["top_under_rows"][0]["practical_under_by"] == 100_000


def test_export_shadow_candidate_reviews_writes_active_rows(tmp_path: Path) -> None:
    module = _summary_module()
    rows = [
        {
            "ts": 1,
            "file": "deep_helped.json",
            "hero": "aisha",
            "map_id": 2501,
            "final_q6_value": 600_000,
            "final_q6_decision_value": 600_000,
            "decision_value_p50": 300_000,
            "v2_q6_decision_value_p90": 200_000,
            "v2_q6_decision_value_p90_under_by": 400_000,
            "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            "q6_residual_deep_floor_shadow_active": True,
            "q6_residual_deep_floor_shadow_q6_decision_value_p90": 700_000,
            "q6_residual_deep_floor_shadow_q6_p90_delta": 500_000,
            "q6_residual_deep_floor_shadow_under_before": True,
            "q6_residual_deep_floor_shadow_covered_after": True,
            "q6_residual_deep_floor_shadow_helped": True,
            "q6_residual_deep_floor_shadow_false_positive_proxy": False,
        },
        {
            "ts": 2,
            "file": "deep_helped.json",
            "hero": "aisha",
            "map_id": 2501,
            "final_q6_value": 600_000,
            "final_q6_decision_value": 600_000,
            "decision_value_p50": 310_000,
            "v2_q6_decision_value_p90": 210_000,
            "v2_q6_decision_value_p90_under_by": 390_000,
            "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            "q6_residual_deep_floor_shadow_active": True,
            "q6_residual_deep_floor_shadow_q6_decision_value_p90": 710_000,
            "q6_residual_deep_floor_shadow_q6_p90_delta": 500_000,
            "q6_residual_deep_floor_shadow_under_before": True,
            "q6_residual_deep_floor_shadow_covered_after": True,
            "q6_residual_deep_floor_shadow_helped": True,
            "q6_residual_deep_floor_shadow_false_positive_proxy": False,
        },
        {
            "ts": 3,
            "file": "hidden_control.json",
            "hero": "aisha",
            "map_id": 2601,
            "final_q6_value": 1_039_000,
            "final_q6_decision_value": 0,
            "final_q6_trimmed_tail_value": 1_039_000,
            "final_q6_trimmed_tail_items": "tail:1039000",
            "final_q6_tail_replacement_value": 93_000,
            "final_q6_tail_replacement_count": 1,
            "final_q6_tail_replacement_items": "tail:1039000->93000",
            "final_q6_tail_replacement_source": "map_weighted_p50",
            "final_q6_decision_value_with_tail_replacement": 93_000,
            "q6_tail_replacement_p90_misses_truth": True,
            "v2_q6_tail_replacement_decision_value_p90_under_by": 93_000,
            "q6_residual_hidden_floor_shadow_label": "aisha_hidden_floor15",
            "q6_residual_hidden_floor_shadow_active": True,
            "q6_residual_hidden_floor_shadow_q6_decision_value_p90": 80_000,
            "q6_residual_hidden_floor_shadow_under_before": False,
            "q6_residual_hidden_floor_shadow_helped": False,
            "q6_residual_hidden_floor_shadow_false_positive_proxy": False,
        },
        {
            "ts": 4,
            "file": "hidden_inactive.json",
            "hero": "aisha",
            "map_id": 2601,
            "q6_residual_hidden_floor_shadow_label": "aisha_hidden_floor15",
            "q6_residual_hidden_floor_shadow_active": False,
        },
    ]

    summary = module.export_shadow_candidate_reviews(
        rows,
        out_dir=tmp_path,
        candidate_labels=("aisha_deep_floor1", "aisha_hidden_floor15"),
    )

    deep = summary["candidates"]["aisha_deep_floor1"]
    assert deep["tracked_rows"] == 1
    assert deep["active_rows"] == 1
    assert deep["review_class_counts"] == {"active_helped": 1}
    hidden = summary["candidates"]["aisha_hidden_floor15"]
    assert hidden["tracked_rows"] == 2
    assert hidden["active_rows"] == 1
    assert hidden["inactive_rows"] == 1
    assert hidden["review_class_counts"] == {"active_no_q6_control": 1}
    deep_rows = [
        json.loads(line)
        for line in (tmp_path / "aisha_deep_floor1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert deep_rows[0]["baseline_decision_value_p50"] == 310_000
    assert deep_rows[0]["baseline_q6_plannable_under_by"] == 390_000
    assert deep_rows[0]["baseline_q6_tail_replacement_under_by"] == 390_000
    assert deep_rows[0]["shadow_q6_plannable_under_by"] == 0
    assert deep_rows[0]["shadow_q6_plannable_gap_band"] == "covered"
    assert deep_rows[0]["shadow_q6_tail_replacement_gap_band"] == "covered"
    assert deep_rows[0]["tail_replacement_review_needed"] is False
    hidden_rows = [
        json.loads(line)
        for line in (tmp_path / "aisha_hidden_floor15.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert hidden_rows[0]["final_q6_value"] == 1_039_000
    assert hidden_rows[0]["final_q6_decision_value"] == 0
    assert hidden_rows[0]["final_q6_trimmed_tail_value"] == 1_039_000
    assert hidden_rows[0]["final_q6_tail_replacement_value"] == 93_000
    assert hidden_rows[0]["final_q6_decision_value_with_tail_replacement"] == 93_000
    assert hidden_rows[0]["shadow_q6_plannable_gap_band"] == "covered"
    assert hidden_rows[0]["shadow_q6_tail_replacement_under_by"] == 13_000
    assert hidden_rows[0]["shadow_q6_tail_replacement_gap_band"] == "small_<=100k"
    assert hidden_rows[0]["tail_trimmed_q6"] is True
    assert hidden_rows[0]["tail_replacement_review_needed"] is True
    assert (tmp_path / "aisha_hidden_floor15.csv").exists()
    assert (tmp_path / "q6_shadow_candidate_review_summary.json").exists()


def test_q6_miss_root_marks_missing_top_item_as_unknown() -> None:
    module = _summary_module()

    summary = module.summarize(
        [
            {
                "file": "a.json",
                "final_value": 100,
                "final_q6_value": 80,
                "q6_p90_misses_truth": True,
                "v2_q6_value_p90": 20,
            }
        ]
    )

    causes = {
        row["cause"]: row["n"]
        for row in summary["q6_miss_root_causes"]
    }
    assert causes["q6_top_unknown_cells"] == 1


def test_collection_readiness_supports_per_hero_hidden_targets() -> None:
    module = _summary_module()

    readiness = module._collection_readiness(
        [],
        target_per_hero_family=30,
        hidden_target_per_hero=10,
        hidden_target_by_hero={"aisha": 10, "ethan": 5},
    )

    hidden = {
        row["hero"]: row["target"]
        for row in readiness["groups"]
        if row["map_family"] == "hidden"
    }
    assert hidden == {"aisha": 10, "ethan": 5}


def test_q6_shadow_sampling_progress_uses_current_focus_targets() -> None:
    module = _summary_module()

    progress = module._q6_shadow_sampling_progress(
        [
            {
                "hero": "ethan",
                "map_id": 2601,
                "q6_residual_boost_shadow_label": "profile_b5",
            },
            {
                "hero": "ethan",
                "map_id": 2601,
                "q6_residual_boost_shadow_label": "profile_b5",
            },
            {
                "hero": "aisha",
                "map_id": 2501,
                "q6_residual_boost_shadow_label": "profile_b5",
            },
            {
                "hero": "aisha",
                "map_id": 2501,
                "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            },
            {
                "hero": "aisha",
                "map_id": 2601,
                "q6_residual_hidden_floor_shadow_label": "aisha_hidden_floor15",
            },
            {
                "hero": "aisha",
                "map_id": 2401,
                "q6_residual_villa_floor_shadow_label": "aisha_villa_floor05",
            },
        ]
    )

    assert progress["sample_scope"] == "live_profile_b5_logs"
    assert progress["tracked_rows"] == 3
    assert progress["ready"] is False
    assert {
        (row["hero"], row["map_family"]): row["target"]
        for row in progress["targets"]
    } == {
        ("aisha", "shipwreck"): 20,
        ("ethan", "shipwreck"): 20,
        ("aisha", "hidden"): 10,
        ("ethan", "hidden"): 5,
    }
    deep = progress["candidates"]["aisha_deep_floor1"]
    assert deep["sample_scope"] == "live_aisha_deep_floor1_logs"
    assert deep["tracked_rows"] == 1
    assert deep["targets"] == [
        {
            "hero": "aisha",
            "map_family": "shipwreck",
            "n": 1,
            "target": 20,
            "needed": 19,
            "ready": False,
        }
    ]
    hidden = progress["candidates"]["aisha_hidden_floor15"]
    assert hidden["sample_scope"] == "live_aisha_hidden_floor15_logs"
    assert hidden["tracked_rows"] == 1
    assert hidden["targets"] == [
        {
            "hero": "aisha",
            "map_family": "hidden",
            "n": 1,
            "target": 10,
            "needed": 9,
            "ready": False,
        }
    ]
    villa = progress["candidates"]["aisha_villa_floor05"]
    assert villa["sample_scope"] == "live_aisha_villa_floor05_logs"
    assert villa["tracked_rows"] == 1
    assert villa["targets"] == [
        {
            "hero": "aisha",
            "map_family": "villa",
            "n": 1,
            "target": 20,
            "needed": 19,
            "ready": False,
        }
    ]


def test_q6_shadow_sampling_progress_ignores_legacy_rows() -> None:
    module = _summary_module()

    progress = module._q6_shadow_sampling_progress(
        [{"hero": "aisha", "map_id": 2501}]
    )

    assert progress["tracked_rows"] == 0
    assert progress["total_needed"] == 55


def test_q6_shadow_candidate_readiness_blocks_false_positive() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2501,
            "final_value": 100,
            "final_q6_value": 0,
            "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            "q6_residual_deep_floor_shadow_active": True,
            "q6_residual_deep_floor_shadow_false_positive_proxy": True,
            "q6_residual_deep_floor_shadow_q6_p90_delta": 200,
        }
        for _ in range(20)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_deep_floor1"]

    assert readiness["status"] == "blocked_false_positive"
    assert readiness["target_ready"] is True
    assert readiness["tracked_rows"] == 20
    assert readiness["active_no_q6_rows"] == 20
    assert readiness["active_zero_q6_proven_rows"] == 0
    assert readiness["false_positive_proxy_rows"] == 20
    assert readiness["zero_q6_proven_false_positive_rows"] == 0
    assert readiness["false_positive_proxy_rate_active_no_q6"] == 1.0


def test_q6_shadow_candidate_readiness_splits_zero_q6_proven_controls() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2401,
            "final_value": 100,
            "final_q6_value": 0,
            "q6_no_plannable_control": True,
            "q6_zero_q6_proven_control": True,
            "q6_residual_villa_floor_shadow_label": "aisha_villa_floor05",
            "q6_residual_villa_floor_shadow_active": True,
            "q6_residual_villa_floor_shadow_false_positive_proxy": True,
            "q6_residual_villa_floor_shadow_zero_q6_proven_false_positive": True,
            "q6_residual_villa_floor_shadow_q6_p90_delta": 200,
        }
        for _ in range(20)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_villa_floor05"]

    assert readiness["status"] == "blocked_false_positive"
    assert readiness["active_no_q6_rows"] == 20
    assert readiness["active_zero_q6_proven_rows"] == 20
    assert readiness["false_positive_proxy_rows"] == 20
    assert readiness["zero_q6_proven_false_positive_rows"] == 20
    assert readiness["zero_q6_proven_false_positive_rate"] == 1.0


def test_q6_shadow_candidate_readiness_uses_plannable_no_q6_controls() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2501,
            "final_value": 1_039_000,
            "final_q6_value": 1_039_000,
            "final_q6_decision_value": 0,
            "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            "q6_residual_deep_floor_shadow_active": True,
            "q6_residual_deep_floor_shadow_false_positive_proxy": True,
            "q6_residual_deep_floor_shadow_q6_p90_delta": 200,
        }
        for _ in range(20)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_deep_floor1"]

    assert readiness["status"] == "blocked_false_positive"
    assert readiness["active_no_q6_rows"] == 20
    assert readiness["false_positive_proxy_rows"] == 20


def test_q6_shadow_candidate_readiness_marks_review_candidate() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2501,
            "final_value": 100,
            "final_q6_value": 100,
            "q6_residual_deep_floor_shadow_label": "aisha_deep_floor1",
            "q6_residual_deep_floor_shadow_active": True,
            "q6_residual_deep_floor_shadow_under_before": True,
            "q6_residual_deep_floor_shadow_helped": True,
            "q6_residual_deep_floor_shadow_false_positive_proxy": False,
            "q6_residual_deep_floor_shadow_q6_p90_delta": 300,
        }
        for _ in range(20)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_deep_floor1"]

    assert readiness["status"] == "candidate_for_review"
    assert readiness["target_ready"] is True
    assert readiness["under_before_rows"] == 20
    assert readiness["helped_rate"] == 1.0
    assert readiness["still_missed_rows"] == 0
    assert readiness["false_positive_proxy_rows"] == 0


def test_q6_hidden_shadow_candidate_readiness_marks_review_candidate() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2601,
            "final_value": 100,
            "final_q6_value": 100,
            "q6_residual_hidden_floor_shadow_label": "aisha_hidden_floor15",
            "q6_residual_hidden_floor_shadow_active": True,
            "q6_residual_hidden_floor_shadow_under_before": True,
            "q6_residual_hidden_floor_shadow_helped": True,
            "q6_residual_hidden_floor_shadow_false_positive_proxy": False,
            "q6_residual_hidden_floor_shadow_q6_p90_delta": 300,
        }
        for _ in range(10)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_hidden_floor15"]

    assert readiness["status"] == "candidate_for_review"
    assert readiness["target_ready"] is True
    assert readiness["under_before_rows"] == 10
    assert readiness["helped_rate"] == 1.0
    assert readiness["still_missed_rows"] == 0
    assert readiness["false_positive_proxy_rows"] == 0


def test_q6_villa_shadow_candidate_readiness_marks_review_candidate() -> None:
    module = _summary_module()

    rows = [
        {
            "hero": "aisha",
            "map_id": 2401,
            "final_value": 100,
            "final_q6_value": 100,
            "q6_residual_villa_floor_shadow_label": "aisha_villa_floor05",
            "q6_residual_villa_floor_shadow_active": True,
            "q6_residual_villa_floor_shadow_under_before": True,
            "q6_residual_villa_floor_shadow_helped": True,
            "q6_residual_villa_floor_shadow_false_positive_proxy": False,
            "q6_residual_villa_floor_shadow_q6_p90_delta": 240,
        }
        for _ in range(20)
    ]

    summary = module.summarize(rows)
    readiness = summary["q6_shadow_candidate_readiness"]["aisha_villa_floor05"]

    assert readiness["status"] == "candidate_for_review"
    assert readiness["target_ready"] is True
    assert readiness["under_before_rows"] == 20
    assert readiness["helped_rate"] == 1.0
    assert readiness["still_missed_rows"] == 0
    assert readiness["false_positive_proxy_rows"] == 0
    assert readiness["q6_p90_delta_median"] == 240
