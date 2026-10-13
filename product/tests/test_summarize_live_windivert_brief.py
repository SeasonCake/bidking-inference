from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str):
    # scripts/ 不是包（无 __init__.py），且 pyproject pythonpath 只含 src/，
    # 所以 `from scripts.x import` 在 pytest 下无法解析。按文件路径加载，与
    # test_fatbeans_webhook_monitor.py 一致；脚本自身的 sys.path 注入仍会执行。
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


module = _load_script_module("summarize_live_windivert_brief")
summarize = module.summarize


def test_summarize_live_windivert_brief_groups_by_round() -> None:
    rows = [
        {
            "ts": 1,
            "source": "windivert",
            "file": "windivert_live.json",
            "hero": "ethan",
            "map_id": 2401,
            "round": 1,
            "action_round": 2,
            "posterior_samples": 0,
            "posterior_total_samples": 20,
            "final_decision_value": 300_000,
            "decision_value_p50": 200_000,
            "decision_value_p90": 250_000,
            "decision_value_p50_error": -100_000,
            "final_q6_value": 180_000,
            "final_q6_count": 3,
            "final_q6_cells": 14,
            "final_q6_decision_value": 180_000,
            "final_q6_decision_value_with_tail_replacement": 230_000,
            "final_q6_tail_replacement_value": 50_000,
            "v2_q6_decision_value_p90": 120_000,
            "v2_q6_tail_replacement_estimate_p90": 150_000,
            "v2_q6_count_p90": 1,
            "v2_q6_cells_p90": 8,
            "v2_q6_count_p90_under_prior_by": 1.4,
            "v2_q6_cells_p90_under_prior_by": 2.2,
            "q6_plannable_p90_misses_truth": True,
            "v2_q6_space_pressure_p90": 0.2,
            "v2_q6_space_overflow_rate": 0,
            "q6_top_size_band": "q6_top_large",
            "random_sample_avg_signal_values": "n=3:avg=124892.00",
            "public_constraint_key": "max_quality",
            "evidence_profile_key": "public:random_avg+layout",
            "information_density_band": "low",
            "shape_target_count": 1,
            "category_target_count": 1,
            "category_exclusion_count": 0,
            "anchor_count": 1,
            "posterior_diagnostics": (
                "public_random_sample_value_floor:355942;"
                "public_random_sample_value_floor_mode:hard:pass=3/7:min=3"
            ),
            "q6_residual_boost_shadow_active": True,
            "q6_residual_boost_shadow_covered_after": True,
            "q6_residual_boost_shadow_label": "profile_b5",
            "q6_quality_only_deep_local_risk": True,
            "warehouse_p50_error": -45,
            "monitor_n_trials": 20,
            "formal_mode": "v3_practical",
            "formal_mode_reason": "v3_practical_ready_live_guarded",
            "v3_practical_live_guard": "是",
            "v3_practical_live_guard_reason": "low_support_baseline",
            "v3_practical_unguarded_decision_value": "100,000 / 280,000 / 500,000",
            "v3_practical_baseline_formal_decision_value_p50": 210_000,
            "v3_practical_baseline_formal_decision_value_p90": 260_000,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_formal_decision_value_p50": 280_000,
            "v3_practical_formal_decision_value_p90": 330_000,
        },
        {
            "ts": 2,
            "source": "windivert",
            "file": "windivert_live.json",
            "hero": "gabriela",
            "map_id": 2501,
            "round": 4,
            "action_round": 4,
            "posterior_samples": 10,
            "final_decision_value": 320_000,
            "decision_value_p90": 400_000,
            "decision_value_p50_error": 20_000,
            "final_q6_value": 0,
            "warehouse_p50_error": 8,
            "inference_profile": {"n_trials": 10},
            "formal_mode": "v2",
            "formal_mode_reason": "v2_mode_requested",
            "size_bucket_active": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_formal_decision_value_p50": 330_000,
            "v3_practical_formal_decision_value_p90": 450_000,
        },
    ]
    summary = summarize(rows)
    assert summary["total_rows"] == 2
    assert summary["source_counts"] == {"windivert": 2}
    assert summary["overall"]["formal_mode_counts"] == {
        "v2": 1,
        "v3_practical": 1,
    }
    assert summary["overall"]["formal_mode_reason_counts"] == {
        "v2_mode_requested": 1,
        "v3_practical_ready_live_guarded": 1,
    }
    assert summary["overall"]["v3_practical_formal_rows"] == 1
    assert summary["overall"]["v3_practical_live_guard_rows"] == 1
    assert summary["overall"]["v3_practical_live_guard_rate"] == 1.0
    assert summary["overall"]["v3_practical_live_guard_reason_counts"] == {
        "low_support_baseline": 1,
    }
    assert summary["overall"]["v3_practical_unguarded_rows"] == 1
    assert summary["overall"]["v3_practical_unguarded_mae"] == 20_000
    assert summary["overall"]["v3_practical_guard_comparison_rows"] == 1
    assert summary["overall"]["v3_practical_guarded_mae_on_comparison"] == 100_000
    assert summary["overall"]["v3_practical_unguarded_mae_on_comparison"] == 20_000
    assert (
        summary["overall"]["v3_practical_guarded_minus_unguarded_mae"]
        == 80_000
    )
    assert (
        summary["overall"]["v3_practical_guarded_minus_unguarded_median_p50"]
        == -80_000
    )
    assert (
        summary["overall"]["v3_practical_guarded_minus_unguarded_median_p90"]
        == -250_000
    )
    assert (
        summary["overall"]["v3_practical_guarded_minus_unguarded_p90_coverage"]
        == -1.0
    )
    guard_cases = summary["overall"]["v3_practical_guard_case_summary"]
    assert guard_cases["rows"] == 1
    assert guard_cases["p50_worsened_rows"] == 1
    assert guard_cases["p50_worsened_rate"] == 1.0
    assert guard_cases["p90_coverage_lost_rows"] == 1
    assert guard_cases["p90_coverage_lost_rate"] == 1.0
    assert guard_cases["p90_extreme_over_added_rows"] == 0
    loss_context = guard_cases["p90_coverage_loss_context"]
    assert loss_context["rows"] == 1
    assert loss_context["by_guard_kind"] == {"low_support_baseline": 1}
    assert loss_context["by_guard_flag"] == {"none": 1}
    assert loss_context["by_evidence_profile"] == {"public:random_avg+layout": 1}
    assert loss_context["median_guarded_p90_shortfall"] == 50_000
    assert loss_context["median_unguarded_p90_excess"] == 200_000
    assert loss_context["median_p90_guard_cut"] == 250_000
    assert guard_cases["top_p50_hurts"][0]["p50_abs_error_delta"] == 80_000
    assert guard_cases["top_p90_coverage_losses"][0]["guarded_p90_error"] == -50_000
    assert (
        summary["by_action_round"]["R2"]["v3_practical_guard_case_summary"]["rows"]
        == 1
    )
    matrix = summary["overall"]["formal_policy_comparison"]
    assert matrix["status"] == "watch"
    assert matrix["comparison_rows"] == 1
    assert matrix["policies"]["v2"]["p50_mae"] == 90_000
    assert matrix["policies"]["v3_guarded"]["p50_mae"] == 100_000
    assert matrix["policies"]["v3_unguarded"]["p50_mae"] == 20_000
    assert matrix["deltas_vs_v2"]["v3_guarded"]["p50_mae_delta"] == 10_000
    assert matrix["deltas_vs_v2"]["v3_unguarded"]["p90_coverage_delta"] == 1.0
    assert summary["by_observed_round"]["R1"]["rows"] == 1
    assert summary["by_action_round"]["R2"]["rows"] == 1
    assert summary["by_action_round"]["R2"]["v3_practical_formal_rows"] == 1
    assert summary["by_action_round"]["R2"]["v3_practical_live_guard_rate"] == 1.0
    assert summary["by_action_round"]["R2"]["p50_under_rate"] == 1.0
    assert summary["by_action_round"]["R2"]["p90_coverage"] == 0.0
    assert summary["by_action_round"]["R2"]["median_n_trials"] == 20
    assert summary["by_action_round"]["R2"]["v3_practical_raise_watch_evaluable_rows"] == 1
    assert summary["by_action_round"]["R2"]["v3_practical_p90_coverage"] == 1.0
    assert summary["by_action_round"]["R2"]["v3_practical_p90_extreme_over_rate"] == 0.0
    assert summary["by_action_round"]["R2"]["v3_practical_raise_watch_hit_rate"] == 1.0
    assert summary["by_action_round"]["R2"]["v3_practical_raise_watch_miss_rate"] == 0.0
    assert (
        summary["by_action_round"]["R2"]["v3_practical_raise_watch_false_alarm_rate"]
        == 0.0
    )
    assert summary["by_round"]["R4"]["v3_practical_raise_watch_false_alarm_rate"] == 1.0
    assert summary["by_round"]["R4"]["v3_practical_p90_coverage"] == 1.0
    assert summary["by_round"]["R4"]["v3_practical_p90_extreme_over_rate"] == 0.0
    assert summary["by_round"]["R4"]["median_matched"] == 10
    assert summary["by_q6_truth"]["q6>0"]["rows"] == 1
    assert summary["by_q6_truth"]["q6=0"]["rows"] == 1
    assert summary["by_random_avg"]["signal"]["rows"] == 1
    assert summary["by_random_avg"]["none"]["rows"] == 1
    assert summary["by_random_floor_mode"]["hard"]["rows"] == 1
    assert summary["by_random_floor_mode"]["none"]["rows"] == 1
    assert summary["by_q6_shadow"]["covered"]["rows"] == 1
    assert summary["by_q6_shadow"]["none"]["rows"] == 1
    assert summary["by_warehouse_error"]["under<-20"]["rows"] == 1
    assert summary["by_warehouse_error"]["within±20"]["rows"] == 1
    assert summary["by_primary_error"]["q6_tail_value"]["rows"] == 1
    assert summary["by_primary_error"]["p90_covered"]["rows"] == 1
    assert summary["by_diagnostic_tag"]["q6_truth"]["rows"] == 1
    assert summary["by_diagnostic_tag"]["random_avg_signal"]["rows"] == 1
    assert summary["by_diagnostic_tag"]["warehouse_under<-20"]["rows"] == 1
    assert summary["by_diagnostic_tag"]["quality_only_deep_local_risk"]["rows"] == 1
    assert summary["by_q6_component_tag"]["q6_value_under"]["rows"] == 1
    assert summary["by_q6_component_tag"]["q6_count_under_truth"]["rows"] == 1
    assert summary["by_q6_component_tag"]["q6_cells_under_truth"]["rows"] == 1
    assert summary["by_q6_component_tag"]["q6_tail_replacement_truth"]["rows"] == 1
    assert summary["by_q6_component_tag"]["q6_tail_estimate_under"]["rows"] == 1
    assert summary["by_q6_component_tag"]["no_q6_truth"]["rows"] == 1
    assert summary["by_hero"]["ethan"]["rows"] == 1
    assert summary["by_map_id"]["2401"]["rows"] == 1
    assert (
        summary["by_map_id"]["2401"]["v3_practical_guard_case_summary"][
            "p90_coverage_lost_rows"
        ]
        == 1
    )
    assert summary["by_map_family"]["villa"]["rows"] == 1
    assert (
        summary["by_map_family"]["villa"]["v3_practical_guard_case_summary"][
            "p90_coverage_lost_rows"
        ]
        == 1
    )
    assert summary["by_evidence_profile"]["public:random_avg+layout"]["rows"] == 1
    assert summary["by_public_constraint"]["max_quality"]["rows"] == 1
    assert summary["by_information_density"]["low"]["rows"] == 1
    assert summary["by_constraint_density"]["medium_3_5"]["rows"] == 1
    assert summary["by_sample_space"]["zero_match"]["rows"] == 1
    assert summary["by_space_pressure"]["low_space_pressure"]["rows"] == 1
    assert summary["by_tail"]["q6_tail_replacement"]["rows"] == 1
    assert summary["top_p90_misses"][0]["primary_error"] == "q6_tail_value"
    assert summary["top_p90_misses"][0]["p90"] == 250_000
    assert summary["top_p90_misses"][0]["v3_practical_p90"] == 330_000
    assert summary["top_p90_misses"][0]["v3_practical_under_by"] == 0
    assert summary["top_p90_misses"][0]["v3_practical_delta_p90"] == 80_000
    assert (
        summary["top_p90_misses"][0]["v3_practical_recommendation"]
        == "raise_watch"
    )
    assert (
        "q6_count_under_truth"
        in summary["top_p90_misses"][0]["q6_components"]
    )
    assert summary["top_p90_misses"][0]["q6_count_under_by"] == 2
    assert summary["top_p90_misses"][0]["q6_cells_under_by"] == 6
    assert summary["top_p90_misses"][0]["q6_replacement_under_by"] == 110_000
    assert summary["top_p90_misses"][0]["q6_tail_estimate_under_by"] == 80_000
    assert summary["top_p90_misses"][0]["q6_shadow"] == "covered"
    assert summary["top_p90_misses"][0]["q6_shadow_covered_labels"] == "profile_b5"
    assert summary["top_p90_misses"][0]["q6_shadow_active_labels"] == "profile_b5"
    assert summary["top_p90_misses"][0]["q6_shadow_active_miss_labels"] == ""


def test_summarize_live_windivert_brief_json_roundtrip() -> None:
    summary = summarize([])
    payload = json.loads(json.dumps(summary))
    assert payload["total_rows"] == 0
    assert payload["prebid_window_audit"]["windows"] == 0


def test_summarize_live_windivert_brief_counts_guarded_reason_without_bid_field() -> None:
    summary = summarize(
        [
            {
                "formal_mode": "v3_practical",
                "formal_mode_reason": "v3_practical_ready_live_guarded",
                "decision_value_p50": 100_000,
                "decision_value_p90": 200_000,
                "final_decision_value": 120_000,
            }
        ]
    )

    assert summary["overall"]["v3_practical_formal_rows"] == 1
    assert summary["overall"]["v3_practical_live_guard_rows"] == 1
    assert summary["overall"]["v3_practical_live_guard_reason_counts"] == {
        "v3_practical_ready_live_guarded": 1,
    }


def test_summarize_live_windivert_brief_preserves_five_prebid_rounds() -> None:
    rows = [
        {
            "eval_window": "pre_bid",
            "eval_window_round": round_no,
            "round": round_no,
            "action_round": round_no,
            "decision_value_p50": 500_000,
            "decision_value_p90": 800_000,
            "final_value": 1_000_000,
            "final_decision_value": 600_000,
            "decision_value_p50_error": -500_000,
            "window_has_prebid_state": True,
            "window_has_estimate": True,
            "window_action_result_ready": True,
            "window_round_matches_artifact": True,
            "window_ready_for_accuracy": True,
            "session_id": "session",
        }
        for round_no in range(1, 6)
    ]

    summary = summarize(rows)

    assert list(summary["prebid_by_round"]) == ["R1", "R2", "R3", "R4", "R5"]
    assert summary["prebid_by_round_q6_truth"]["R5"]["unknown"]["rows"] == 1
    assert summary["prebid_window_audit"]["five_window_sessions"] == 1
    assert summary["prebid_window_audit"]["ready_windows"] == 5
    assert summary["prebid_session_progression"]["transitions"] == 4
    assert (
        summary["prebid_session_progression"]["p50_abs_error_improved_or_equal_rate"]
        == 1.0
    )
    assert summary["prebid_session_progression"]["p90_coverage_lost_transitions"] == 0
    assert summary["prebid_overall"]["decision_value_mae"] == 100_000
    assert summary["prebid_overall"]["median_normalized_abs_p50_error"] == 0.167
    assert summary["prebid_overall"]["p90_coverage"] == 1.0
    assert summary["prebid_overall"]["median_p90_covered_excess_ratio"] == 0.333


def test_summarize_live_windivert_brief_uses_replacement_truth_when_available() -> None:
    summary = summarize(
        [
            {
                "eval_window": "pre_bid",
                "eval_window_round": 2,
                "round": 2,
                "action_round": 2,
                "decision_value_p50": 550_000,
                "decision_value_p90": 800_000,
                "final_value": 1_000_000,
                "final_decision_value": 600_000,
                "final_decision_value_with_tail_replacement": 650_000,
                "decision_value_p50_error": -450_000,
            },
        ]
    )

    assert summary["prebid_overall"]["decision_value_mae"] == 100_000
    assert summary["prebid_overall"]["p90_coverage"] == 1.0


def test_q6_shadow_bucket_uses_ethan_villa_random_floor_shadow() -> None:
    row = {
        "final_q6_value": 500_000,
        "final_q6_decision_value": 500_000,
        "q6_residual_ethan_villa_random_floor_shadow_active": True,
        "q6_residual_ethan_villa_random_floor_shadow_covered_after": True,
    }

    assert module._q6_shadow_bucket(row) == "covered"


def test_q6_shadow_labels_keep_active_miss_label() -> None:
    row = {
        "final_q6_value": 500_000,
        "final_q6_decision_value": 500_000,
        "q6_residual_deep11_floor_shadow_label": "aisha_deep11_floor1",
        "q6_residual_deep11_floor_shadow_active": True,
        "q6_residual_deep11_floor_shadow_covered_after": False,
    }

    assert module._q6_shadow_bucket(row) == "active_miss"
    assert module._q6_shadow_labels(row, "active") == "aisha_deep11_floor1"
    assert (
        module._q6_shadow_labels(row, "active_miss")
        == "aisha_deep11_floor1"
    )
    assert module._q6_shadow_labels(row, "covered") == ""


def test_load_archive_rows_replays_recent_complete_archive(
    tmp_path,
    monkeypatch,
) -> None:
    archive = tmp_path / "archive" / "complete"
    archive.mkdir(parents=True)
    capture = archive / "windivert_2026-06-04_complete_ethan_2401_session.json"
    capture.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(module, "load_monitor_tables", lambda: "tables")

    def fake_build(path, **kwargs):
        assert path == capture
        assert kwargs["tables"] == "tables"
        assert kwargs["n_trials"] == 10
        assert kwargs["roi_trials"] == 0
        assert kwargs["shadow_trials"] == 1
        assert kwargs["run_debug_shadows"] is False
        assert kwargs["formal_mode"] == "v3_practical"
        return {
            "session_id": "2401:session",
            "formal_mode_requested": "v3_practical",
            "formal_mode": "v3_practical",
            "formal_mode_reason": "v3_practical_ready",
            "bid_rows": [
                {
                    "v3_practical_live_guard": "是",
                    "v3_practical_live_guard_reason": "guard_from_bid_row",
                    "v3_practical_unguarded_decision_value": "1 / 2 / 3",
                }
            ],
            "model_eval": {
                "file": str(path.name),
                "round": 3,
                "action_round": 3,
                "decision_value_p50_error": -42,
                "monitor_n_trials": 10,
            },
        }

    monkeypatch.setattr(module, "build_monitor_artifact_from_file", fake_build)

    rows = module._load_archive_rows(
        tmp_path / "archive",
        since_ts=0,
        n_trials=10,
        roi_trials=0,
        shadow_trials=1,
        run_debug_shadows=False,
        window="full",
        formal_mode="v3_practical",
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "windivert_archive"
    assert rows[0]["session_id"] == "2401:session"
    assert rows[0]["archive_path"] == str(capture)
    assert rows[0]["snapshot_mode"] == "archive_fast"
    assert rows[0]["formal_mode_requested"] == "v3_practical"
    assert rows[0]["formal_mode"] == "v3_practical"
    assert rows[0]["formal_mode_reason"] == "v3_practical_ready"
    assert rows[0]["replay_formal_mode"] == "v3_practical"
    assert rows[0]["v3_practical_live_guard"] == "是"
    assert rows[0]["v3_practical_live_guard_reason"] == "guard_from_bid_row"
    assert rows[0]["v3_practical_unguarded_decision_value"] == "1 / 2 / 3"


def test_load_archive_rows_can_emit_prebid_windows(
    tmp_path,
    monkeypatch,
) -> None:
    archive = tmp_path / "archive" / "complete"
    archive.mkdir(parents=True)
    capture = archive / "windivert_2026-06-04_complete_ethan_2401_session.json"
    capture.write_text("[]\n", encoding="utf-8")
    events = SimpleNamespace(
        packets=tuple(SimpleNamespace(sort_id=sort_id) for sort_id in range(1, 5)),
        frames=tuple(SimpleNamespace(sort_id=sort_id) for sort_id in range(1, 5)),
        sends=(
            SimpleNamespace(sort_id=2, kind="action", value=100136),
            SimpleNamespace(sort_id=4, kind="bid", value=580000),
        ),
        states=(SimpleNamespace(sort_id=1), SimpleNamespace(sort_id=3)),
        statuses=(),
    )

    monkeypatch.setattr(module, "load_monitor_tables", lambda: "tables")
    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: events)

    def fake_build_events(events_arg, **kwargs):
        assert kwargs["formal_mode"] == "v3_practical"
        if len(events_arg.packets) == 4:
            return {
                "session_id": "2401:session",
                "known_value_sum": 900000,
                "inventory_cells": 121,
                "final_q6_value": 600000,
                "formal_mode_requested": "v3_practical",
                "formal_mode": "v3_practical",
                "formal_mode_reason": "v3_practical_ready",
            }
        assert [send.kind for send in events_arg.sends] == ["action"]
        assert [state.sort_id for state in events_arg.states] == [1, 3]
        return {
            "session_id": "2401:session",
            "round": None,
            "action_round": None,
            "formal_mode_requested": "v3_practical",
            "formal_mode": "v3_practical",
            "formal_mode_reason": "v3_practical_ready_live_guarded",
            "bid_rows": [
                {
                    "v3_practical_live_guard": "是",
                    "v3_practical_live_guard_reason": "prebid_guard",
                    "v3_practical_unguarded_decision_value": "4 / 5 / 6",
                }
            ],
        }

    def fake_model_eval_row(**kwargs):
        assert kwargs["final_value"] == 900000
        assert kwargs["final_cells"] == 121
        assert kwargs["truth_breakdown"]["final_q6_value"] == 600000
        return {
            "file": kwargs["file"],
            "round": None,
            "action_round": None,
            "decision_value_p50": 800000,
            "decision_value_p90": 950000,
            "decision_value_p50_error": -100000,
        }

    monkeypatch.setattr(module, "build_monitor_artifact_from_events", fake_build_events)
    monkeypatch.setattr(module, "_model_eval_row", fake_model_eval_row)

    rows = module._load_archive_rows(
        tmp_path / "archive",
        since_ts=0,
        n_trials=10,
        roi_trials=0,
        shadow_trials=1,
        run_debug_shadows=False,
        window="prebid",
        formal_mode="v3_practical",
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "windivert_archive_prebid"
    assert rows[0]["eval_window"] == "pre_bid"
    assert rows[0]["eval_window_round"] == 1
    assert rows[0]["window_bid_value"] == 580000
    assert rows[0]["window_has_prebid_state"] is True
    assert rows[0]["window_has_estimate"] is True
    assert rows[0]["window_round_action_send_count"] == 1
    assert rows[0]["window_round_last_action_sort_id"] == 2
    assert rows[0]["window_round_last_state_sort_id"] == 3
    assert rows[0]["window_action_result_ready"] is True
    assert rows[0]["window_ready_for_accuracy"] is True
    assert rows[0]["round"] == 1
    assert rows[0]["action_round"] == 1
    assert rows[0]["formal_mode_requested"] == "v3_practical"
    assert rows[0]["formal_mode"] == "v3_practical"
    assert rows[0]["formal_mode_reason"] == "v3_practical_ready_live_guarded"
    assert rows[0]["replay_formal_mode"] == "v3_practical"
    assert rows[0]["v3_practical_live_guard"] == "是"
    assert rows[0]["v3_practical_live_guard_reason"] == "prebid_guard"
    assert rows[0]["v3_practical_unguarded_decision_value"] == "4 / 5 / 6"


def test_load_archive_rows_scans_reset_complete_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    complete = tmp_path / "archive" / "complete"
    reset = tmp_path / "archive" / "reset"
    complete.mkdir(parents=True)
    reset.mkdir(parents=True)
    complete_capture = (
        complete / "windivert_2026-06-04_complete_ethan_2401_session-a.json"
    )
    reset_capture = reset / "windivert_live_2026-06-04_190618_2501_session-b_reset.json"
    complete_capture.write_text("[]\n", encoding="utf-8")
    reset_capture.write_text("[]\n", encoding="utf-8")

    monkeypatch.setattr(module, "load_monitor_tables", lambda: "tables")

    def fake_build(path, **kwargs):
        return {
            "session_id": f"session:{path.stem}",
            "model_eval": {
                "file": path.name,
                "round": 3,
                "action_round": 3,
                "decision_value_p50_error": -42,
            },
        }

    monkeypatch.setattr(module, "build_monitor_artifact_from_file", fake_build)

    rows = module._load_archive_rows(
        tmp_path / "archive",
        since_ts=0,
        n_trials=10,
        roi_trials=0,
        shadow_trials=1,
        run_debug_shadows=False,
        window="full",
    )

    assert {Path(row["archive_path"]).parent.name for row in rows} == {
        "complete",
        "reset",
    }


def test_summarize_live_windivert_brief_dedupes_archive_by_session() -> None:
    rows = module._dedupe_rows(
        [
            {"source": "windivert", "session_id": "2401:a", "file": "live.json"},
            {
                "source": "windivert_archive",
                "session_id": "2401:a",
                "archive_path": "archive/complete/windivert_a.json",
            },
            {
                "source": "windivert_archive",
                "session_id": "2401:b",
                "archive_path": "archive/complete/windivert_b.json",
            },
        ]
    )

    assert [row["session_id"] for row in rows] == ["2401:a", "2401:b"]
