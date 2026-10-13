import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1] / "scripts" / "summarize_v3_ccv_holdout.py"
    )
    spec = importlib.util.spec_from_file_location("summarize_v3_ccv_holdout", path)
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
    group: str,
    *,
    session_id: str,
    truth_formal: int,
    formal_p50: int,
    formal_p90: int,
    truth_q6_count: int,
    post_q6_count: int,
    ccv_q6_count: int,
    truth_q6_cells: int,
    post_q6_cells: int,
    ccv_q6_cells: int,
    truth_q6_value: int = 100_000,
    post_q6_value: int = 120_000,
    ccv_q6_value: int = 100_000,
    truth_q6_formal: int = 100_000,
    post_q6_formal: int = 120_000,
    ccv_q6_formal: int = 100_000,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_evidence_profile": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_ccv_ready": True,
        "v3_post_match_scope": "summary_likelihood",
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_truth_formal_decision_value": truth_formal,
        "v3_post_formal_decision_value_p50": formal_p50,
        "v3_post_formal_decision_value_p90": formal_p90,
        "v3_truth_q6_count": truth_q6_count,
        "v3_post_q6_count_p50": post_q6_count,
        "v3_post_q6_count_p90": post_q6_count + 1,
        "v3_ccv_q6_count_p50": ccv_q6_count,
        "v3_ccv_q6_count_p90": ccv_q6_count + 1,
        "v3_truth_q6_cells": truth_q6_cells,
        "v3_post_q6_cells_p50": post_q6_cells,
        "v3_post_q6_cells_p90": post_q6_cells + 2,
        "v3_ccv_q6_cells_p50": ccv_q6_cells,
        "v3_ccv_q6_cells_p90": ccv_q6_cells + 2,
        "v3_truth_q6_raw_value": truth_q6_value,
        "v3_post_q6_value_p50": post_q6_value,
        "v3_post_q6_value_p90": post_q6_value + 20_000,
        "v3_ccv_q6_value_p50": ccv_q6_value,
        "v3_ccv_q6_value_p90": ccv_q6_value + 20_000,
        "v3_truth_q6_formal_decision_value": truth_q6_formal,
        "v3_post_q6_formal_decision_value_p50": post_q6_formal,
        "v3_post_q6_formal_decision_value_p90": post_q6_formal + 20_000,
        "v3_ccv_q6_formal_decision_value_p50": ccv_q6_formal,
        "v3_ccv_q6_formal_decision_value_p90": ccv_q6_formal + 20_000,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_ccv_holdout_applies_train_candidate_to_holdout_rows() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"f0_{idx}") for idx in range(2)]
    fold1 = [_session_for_fold(module, 1, prefix=f"f1_{idx}") for idx in range(2)]
    rows = [
        _row(
            "ethan|2502|shape",
            session_id=session_id,
            truth_formal=100,
            formal_p50=105,
            formal_p90=130,
            truth_q6_count=2,
            post_q6_count=5,
            ccv_q6_count=2,
            truth_q6_cells=8,
            post_q6_cells=16,
            ccv_q6_cells=8,
        )
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["candidate_rows"] == 4
    assert result["overall"]["delta_q6_count_p50_mae"] < 0
    assert result["overall"]["delta_q6_cells_p50_mae"] < 0
    assert result["candidate_only"]["candidate_groups"] == ["ethan|2502|shape"]
    assert result["group_results"][0]["group"] == "ethan|2502|shape"
    assert result["group_results"][0]["delta_q6_formal_p50_mae"] < 0


def test_ccv_holdout_does_not_apply_blocked_under_downshift_group() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="under0")
    fold1 = _session_for_fold(module, 1, prefix="under1")
    rows = [
        _row(
            "aisha|2506|item+shape",
            session_id=fold0,
            truth_formal=1_000,
            formal_p50=500,
            formal_p90=700,
            truth_q6_count=4,
            post_q6_count=4,
            ccv_q6_count=2,
            truth_q6_cells=20,
            post_q6_cells=20,
            ccv_q6_cells=12,
        ),
        _row(
            "aisha|2506|item+shape",
            session_id=fold1,
            truth_formal=1_100,
            formal_p50=600,
            formal_p90=800,
            truth_q6_count=5,
            post_q6_count=5,
            ccv_q6_count=3,
            truth_q6_cells=22,
            post_q6_cells=22,
            ccv_q6_cells=14,
        ),
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["candidate_rows"] == 0
    assert result["overall"]["delta_q6_count_p50_mae"] == 0
    assert result["overall"]["delta_q6_cells_p50_mae"] == 0
    assert result["candidate_only"]["n"] == 0
