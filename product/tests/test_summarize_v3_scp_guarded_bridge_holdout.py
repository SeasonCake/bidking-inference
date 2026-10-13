import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_scp_guarded_bridge_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_scp_guarded_bridge_holdout",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_folds(module, outer_fold: int, inner_fold: int, *, prefix: str) -> str:
    for idx in range(10_000):
        session_id = f"{prefix}_{idx}"
        if (
            module._stable_fold(session_id, 2) == outer_fold
            and module._stable_fold(f"inner:{session_id}", 2) == inner_fold
        ):
            return session_id
    raise AssertionError(f"no session for folds {outer_fold}/{inner_fold}")


def _row(
    *,
    session_id: str,
    group: str,
    truth_formal: int,
    post_formal_p50: int,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "v3_scp_ready": True,
        "v3_scp_active": False,
        "v3_scp_affects_bid": False,
        "v3_scp_candidate": True,
        "v3_scp_missing_table": False,
        "v3_scp_status": "observed_exceeds_table_caps_shadow_only",
        "v3_scp_group": group,
        "v3_scp_target_count": 5,
        "v3_scp_prior_items_per_session_max": 5,
        "v3_scp_non_temp_inventory_count_p95": 10,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_item_count": 10,
        "v3_truth_total_cells": 30,
        "v3_truth_formal_decision_value": truth_formal,
        "v3_post_total_cells_p50": 20,
        "v3_post_total_cells_p90": 25,
        "v3_post_formal_decision_value_p50": post_formal_p50,
        "v3_post_formal_decision_value_p90": post_formal_p50,
        "v3_capacity_cases": "direct_prior_max_conflict",
        "v3_fv_stress_class": "none",
    }


def test_guarded_bridge_selects_only_inner_crossfit_watch_group() -> None:
    module = _load_module()
    rows = []
    for outer_fold in range(2):
        for inner_fold in range(2):
            rows.append(
                _row(
                    session_id=_session_for_folds(
                        module,
                        outer_fold,
                        inner_fold,
                        prefix="good",
                    ),
                    group="good",
                    truth_formal=200,
                    post_formal_p50=100,
                )
            )
            rows.append(
                _row(
                    session_id=_session_for_folds(
                        module,
                        outer_fold,
                        inner_fold,
                        prefix="no_lift",
                    ),
                    group="no_lift",
                    truth_formal=80,
                    post_formal_p50=100,
                )
            )

    result = module.summarize_guarded_holdout(
        rows,
        folds=2,
        inner_folds=2,
        min_train_sessions=1,
        min_guard_sessions=1,
        min_guard_fold_sessions=1,
        formal_lift_cap=30,
    )

    assert result["overall_status"] == "watch"
    assert result["selected_group_fold_counts"] == {"good": 2}
    assert result["candidate_only"]["applied_rows"] == 4
    assert result["candidate_only"]["delta_formal_p50_mae"] == -30
    assert result["candidate_only"]["bridge_formal_p50_over_rate"] == 0
    assert result["selected_group_fold_support"] == [
        {
            "fold": 0,
            "group": "good",
            "metric_rows": 2,
            "sessions": 2,
            "candidate_rows": 2,
            "applied_rows": 2,
            "sample_limited_rows": 0,
        },
        {
            "fold": 1,
            "group": "good",
            "metric_rows": 2,
            "sessions": 2,
            "candidate_rows": 2,
            "applied_rows": 2,
            "sample_limited_rows": 0,
        },
    ]
    assert len(result["selected_group_guard_summary"]) == 2
    assert result["selected_group_guard_summary"][0]["group"] == "good"
    assert result["selected_group_guard_summary"][0]["guard_status"] == (
        "watch_train_guard"
    )
    assert result["selected_group_guard_summary"][0]["guard_applied_sessions"] >= 1
    assert result["selected_group_guard_summary"][0]["guard_inner_status_counts"] == {
        "watch_count_value_bridge_holdout": 2
    }
    assert result["selected_group_support"] == [
        {
            "group": "good",
            "selected_folds": 2,
            "metric_rows": 4,
            "sessions": 4,
            "candidate_rows": 4,
            "applied_rows": 4,
            "sample_limited_rows": 0,
        }
    ]
    assert [row["group"] for row in result["group_results"]] == ["good"]
    assert result["applied_hurts"] == []


def test_guarded_bridge_is_sample_limited_without_metric_rows() -> None:
    module = _load_module()

    result = module.summarize_guarded_holdout(
        [
            {
                "status": "no_state",
                "session_id": "activity",
                "v3_scp_candidate": False,
            }
        ],
        folds=2,
        inner_folds=2,
        min_train_sessions=1,
        min_guard_sessions=1,
        min_guard_fold_sessions=1,
    )

    assert result["overall_status"] == "sample_limited"
    assert result["overall"]["rows"] == 0
    assert result["candidate_only"]["applied_rows"] == 0
