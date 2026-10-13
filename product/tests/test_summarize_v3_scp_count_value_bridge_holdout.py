import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_scp_count_value_bridge_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_scp_count_value_bridge_holdout",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_fold(module, fold: int, *, prefix: str) -> str:
    for idx in range(1000):
        session_id = f"{prefix}_{idx}"
        if module._stable_fold(session_id, 2) == fold:
            return session_id
    raise AssertionError(f"no session for fold {fold}")


def _row(
    *,
    session_id: str,
    group: str = "2601",
    scp_candidate: bool = True,
    target_count: int = 5,
    scp_p95: int = 10,
    truth_count: int = 10,
    truth_cells: int = 30,
    truth_formal: int = 100,
    post_cells_p50: int = 20,
    post_cells_p90: int = 25,
    post_formal_p50: int = 70,
    post_formal_p90: int = 90,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "v3_scp_ready": True,
        "v3_scp_active": False,
        "v3_scp_affects_bid": False,
        "v3_scp_candidate": scp_candidate,
        "v3_scp_missing_table": False,
        "v3_scp_status": "observed_exceeds_table_caps_shadow_only",
        "v3_scp_group": group,
        "v3_scp_target_count": target_count,
        "v3_scp_prior_items_per_session_max": target_count,
        "v3_scp_non_temp_inventory_count_p95": scp_p95,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_item_count": truth_count,
        "v3_truth_total_cells": truth_cells,
        "v3_truth_formal_decision_value": truth_formal,
        "v3_post_total_cells_p50": post_cells_p50,
        "v3_post_total_cells_p90": post_cells_p90,
        "v3_post_formal_decision_value_p50": post_formal_p50,
        "v3_post_formal_decision_value_p90": post_formal_p90,
        "v3_capacity_cases": "direct_prior_max_conflict",
        "v3_fv_stress_class": "none",
    }


def test_scp_count_value_bridge_holdout_applies_train_only_floor() -> None:
    module = _load_module()
    fold0 = [
        _session_for_fold(module, 0, prefix=f"bridge0_{idx}")
        for idx in range(2)
    ]
    fold1 = [
        _session_for_fold(module, 1, prefix=f"bridge1_{idx}")
        for idx in range(2)
    ]
    rows = [
        _row(session_id=fold0[0]),
        _row(session_id=fold0[1]),
        _row(session_id=fold1[0]),
        _row(session_id=fold1[1]),
    ]

    result = module.summarize_holdout(
        rows,
        group_field="v3_scp_group",
        folds=2,
        min_train_sessions=1,
    )

    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["applied_rows"] == 4
    assert result["candidate_only"]["delta_formal_p50_mae"] == -30
    assert result["candidate_only"]["delta_formal_p90_coverage"] == 1.0
    assert result["candidate_only"]["bridge_formal_p50_over_rate"] == 0.0
    group = result["group_results"][0]
    assert group["group"] == "2601"
    assert group["candidate_status"] == "watch_count_value_bridge_holdout"
    assert group["train_formal_per_item_p90"]["p50"] == 10


def test_scp_count_value_bridge_holdout_extra_mode_adds_only_count_gap() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="extra0")
    fold1 = _session_for_fold(module, 1, prefix="extra1")
    rows = [
        _row(session_id=fold0, truth_formal=120, post_formal_p50=60),
        _row(session_id=fold1, truth_formal=120, post_formal_p50=60),
    ]

    result = module.summarize_holdout(
        rows,
        group_field="v3_scp_group",
        folds=2,
        min_train_sessions=1,
        floor_mode="extra",
    )

    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["applied_rows"] == 2
    assert result["candidate_only"]["bridge_formal_p50_bias"] == 0
    assert result["candidate_only"]["delta_formal_p50_mae"] == -60


def test_scp_count_value_bridge_holdout_caps_formal_lift() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="cap0")
    fold1 = _session_for_fold(module, 1, prefix="cap1")
    rows = [
        _row(session_id=fold0, truth_formal=200, post_formal_p50=100),
        _row(session_id=fold1, truth_formal=200, post_formal_p50=100),
    ]

    result = module.summarize_holdout(
        rows,
        group_field="v3_scp_group",
        folds=2,
        min_train_sessions=1,
        formal_lift_cap=30,
    )

    assert result["formal_lift_cap"] == 30.0
    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["applied_rows"] == 2
    assert result["candidate_only"]["bridge_formal_p50_mae"] == 70
    assert result["candidate_only"]["delta_formal_p50_mae"] == -30
    assert result["candidate_only"]["bridge_formal_p50_bias"] == -70


def test_scp_count_value_bridge_holdout_marks_low_sample_candidates() -> None:
    module = _load_module()
    rows = [
        _row(
            session_id="activity",
            group="2521",
            scp_candidate=False,
            target_count=0,
            scp_p95=0,
        )
    ]

    result = module.summarize_holdout(
        rows,
        group_field="v3_scp_group",
        folds=2,
        min_train_sessions=2,
    )

    assert result["overall_status"] == "sample_limited"
    assert result["overall"]["candidate_rows"] == 0
    assert result["status_counts"] == {"sample_limited": 1}
