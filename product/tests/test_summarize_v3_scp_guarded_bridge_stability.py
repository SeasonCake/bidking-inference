import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_scp_guarded_bridge_stability.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_scp_guarded_bridge_stability",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(
    *,
    trials: int,
    seed: int,
    status: str = "watch",
    selected: dict[str, int] | None = None,
    applied_rows: int = 25,
    applied_hurts: list[str] | None = None,
) -> dict[str, object]:
    selected_counts = selected or {"2506": 2}
    return {
        "posterior_trials": trials,
        "posterior_seed": seed,
        "overall_status": status,
        "selected_group_fold_counts": selected_counts,
        "selected_group_support": [
            {
                "group": group,
                "selected_folds": selected_folds,
                "sessions": max(1, applied_rows // max(1, selected_folds)),
                "metric_rows": applied_rows,
                "candidate_rows": applied_rows,
                "applied_rows": applied_rows,
                "sample_limited_rows": 0,
            }
            for group, selected_folds in selected_counts.items()
        ],
        "candidate_only": {
            "candidate_rows": applied_rows,
            "applied_rows": applied_rows,
            "delta_formal_p50_mae": -1000,
            "delta_formal_p90_coverage": 0,
            "bridge_formal_p50_over_rate": 0.25,
        },
        "applied_hurts": applied_hurts or [],
    }


def test_guarded_bridge_stability_passes_exact_group_across_runs() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            _run(trials=256, seed=0),
            _run(trials=256, seed=1),
            _run(trials=256, seed=7),
        ],
        required_selected_groups=("2506",),
        min_applied_rows=20,
    )

    assert result["overall_status"] == "watch"
    assert result["status_reasons"] == ["all_runs_stable"]
    assert result["watch_runs"] == 3
    assert result["stable_selected_groups"] == ["2506"]
    assert result["union_selected_groups"] == ["2506"]
    assert result["min_applied_rows"] == 25
    assert result["selected_group_support_summary"][0]["group"] == "2506"
    assert result["selected_group_support_summary"][0]["missing_support_runs"] == 0
    assert result["selected_group_guard_summary"] == []


def test_guarded_bridge_stability_blocks_hurt_run() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            _run(trials=64, seed=0),
            _run(
                trials=64,
                seed=1,
                status="blocked_holdout_hurt",
                selected={"2501": 1, "2506": 2},
                applied_rows=62,
                applied_hurts=["2501"],
            ),
        ],
        required_selected_groups=("2506",),
        min_applied_rows=20,
    )

    assert result["overall_status"] == "blocked_applied_hurt"
    assert "applied_hurts_present" in result["status_reasons"]
    assert "selected_group_drift" in result["status_reasons"]
    assert result["hurt_group_counts"] == {"2501": 1}
    assert result["union_selected_groups"] == ["2501", "2506"]
    support = {
        row["group"]: row for row in result["selected_group_support_summary"]
    }
    assert support["2501"]["hurt_run_count"] == 1
    assert support["2501"]["min_applied_rows"] == 62
    assert support["2506"]["run_count"] == 2


def test_guarded_bridge_stability_blocks_low_support() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            _run(trials=256, seed=0, applied_rows=9),
            _run(trials=256, seed=1, applied_rows=9),
            _run(trials=256, seed=7, applied_rows=9),
        ],
        required_selected_groups=("2506",),
        min_applied_rows=20,
    )

    assert result["overall_status"] == "blocked_low_support"
    assert result["status_reasons"] == ["low_applied_rows"]
    assert result["min_applied_rows"] == 9
    assert result["max_applied_rows"] == 9
    assert len(result["selected_group_support_gap"]) == 1
    gap = result["selected_group_support_gap"][0]
    assert gap["group"] == "2506"
    assert gap["run_count"] == 3
    assert gap["required_applied_rows"] == 20
    assert gap["min_applied_rows"] == 9
    assert gap["max_applied_rows"] == 9
    assert gap["min_applied_gap"] == 11
    assert gap["min_candidate_rows"] == 9
    assert gap["min_metric_rows"] == 9
    assert gap["min_sessions"] == 4
    assert gap["missing_support_runs"] == 0
    assert [row["posterior_seed"] for row in gap["runs"]] == [0, 1, 7]
    instability = result["selected_group_instability_summary"][0]
    assert instability["group"] == "2506"
    assert instability["status"] == "blocked_support_depth_gap"
    assert instability["reasons"] == ["min_applied_rows_below_required"]


def test_guarded_bridge_stability_blocks_missing_multi_group_support() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            {
                "posterior_trials": 64,
                "posterior_seed": 1,
                "overall_status": "watch",
                "selected_group_fold_counts": {"2501": 1, "2506": 2},
                "candidate_only": {
                    "candidate_rows": 62,
                    "applied_rows": 62,
                    "delta_formal_p50_mae": -100,
                    "delta_formal_p90_coverage": 0,
                    "bridge_formal_p50_over_rate": 0.25,
                },
                "applied_hurts": [],
            }
        ],
        required_selected_groups=(),
        min_applied_rows=20,
    )

    assert result["overall_status"] == "blocked_missing_support"
    assert result["status_reasons"] == ["selected_group_support_missing"]
    support = {
        row["group"]: row for row in result["selected_group_support_summary"]
    }
    assert support["2501"]["missing_support_runs"] == 1
    assert support["2506"]["missing_support_runs"] == 1


def test_guarded_bridge_stability_summarizes_train_guard_metrics() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            {
                **_run(trials=64, seed=1, selected={"2501": 1}),
                "selected_group_guard_summary": [
                    {
                        "fold": 4,
                        "group": "2501",
                        "guard_status": "watch_train_guard",
                        "guard_applied_sessions": 19,
                        "guard_delta_formal_p50_mae": -110.256,
                        "guard_delta_formal_p90_coverage": 0.016949,
                        "guard_bridge_formal_p50_over_rate": 0.559322,
                        "guard_inner_status_counts": {
                            "watch_count_value_bridge_holdout": 4
                        },
                        "applied_rows": 53,
                        "sessions": 17,
                    }
                ],
            }
        ],
        required_selected_groups=("2501",),
        min_applied_rows=20,
    )

    guard = result["selected_group_guard_summary"][0]
    assert guard["group"] == "2501"
    assert guard["run_count"] == 1
    assert guard["selected_folds"] == 1
    assert guard["guard_status_counts"] == {"watch_train_guard": 1}
    assert guard["min_guard_applied_sessions"] == 19
    assert guard["max_guard_delta_formal_p50_mae"] == -110.256
    assert guard["max_guard_bridge_formal_p50_over_rate"] == 0.559322
    instability = result["selected_group_instability_summary"][0]
    assert instability["group"] == "2501"
    assert instability["status"] == "watch_train_guard_stable"


def test_guarded_bridge_stability_classifies_train_holdout_instability() -> None:
    module = _load_module()

    result = module.summarize_stability(
        [
            {
                **_run(
                    trials=64,
                    seed=1,
                    status="blocked_holdout_hurt",
                    selected={"2501": 1},
                    applied_rows=53,
                    applied_hurts=["2501"],
                ),
                "selected_group_guard_summary": [
                    {
                        "fold": 4,
                        "group": "2501",
                        "guard_status": "watch_train_guard",
                        "guard_applied_sessions": 59,
                        "guard_delta_formal_p50_mae": -1707.317,
                        "guard_delta_formal_p90_coverage": 0.0,
                        "guard_bridge_formal_p50_over_rate": 0.414634,
                        "guard_inner_status_counts": {
                            "watch_count_value_bridge_holdout": 4
                        },
                        "applied_rows": 53,
                        "sessions": 17,
                    }
                ],
            }
        ],
        required_selected_groups=("2501",),
        min_applied_rows=20,
    )

    instability = result["selected_group_instability_summary"][0]
    assert instability["group"] == "2501"
    assert instability["status"] == "blocked_train_holdout_instability"
    assert instability["reasons"] == ["train_guard_watch_but_holdout_hurt"]
    assert instability["support_hurt_run_count"] == 1
    assert instability["guard_status_counts"] == {"watch_train_guard": 1}
