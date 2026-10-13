import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_formal_value_delta_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_formal_value_delta_holdout",
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
    group: str = "public:total+item+shape",
    formal_pred: int = 100_000,
    formal_truth: int = 200_000,
    q6_formal_pred: int = 50_000,
    q6_formal_candidate: int = 100_000,
    q6_formal_truth: int = 120_000,
    public_total: bool = True,
    q6_floor: bool = True,
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "evidence_profile_key": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_ccv_ready": True,
        "v3_resid_ready": True,
        "v3_post_formal_decision_value_p50": formal_pred,
        "v3_post_formal_decision_value_p90": formal_pred,
        "v3_post_q6_formal_decision_value_p50": q6_formal_pred,
        "v3_post_q6_formal_decision_value_p90": q6_formal_pred,
        "v3_ccv_q6_formal_decision_value_p50": q6_formal_candidate,
        "v3_ccv_q6_formal_decision_value_p90": q6_formal_candidate,
        "v3_resid_q6_formal_decision_value_p50": q6_formal_pred,
        "v3_resid_q6_formal_decision_value_p90": q6_formal_pred,
        "v3_truth_formal_decision_value": formal_truth,
        "v3_truth_q6_formal_decision_value": q6_formal_truth,
        "v3_summary_session_total_cells_exact": 80 if public_total else None,
        "v3_summary_session_total_count_exact": None,
        "v3_summary_q6_count_floor": 1 if q6_floor else 0,
        "v3_summary_q6_cells_floor": 4 if q6_floor else 0,
        "v3_summary_q6_value_floor": 80_000 if q6_floor else 0,
    }


def test_formal_value_delta_candidates_map_q6_formal_delta_to_formal() -> None:
    module = _load_module()
    rows = [_row(session_id=f"s{idx}") for idx in range(6)]

    result = module.summarize_candidates(
        rows,
        candidate_prefix="v3_ccv_",
        min_windows=2,
        min_sessions=2,
    )

    assert len(result) == 1
    row = result[0]
    assert row["candidate_status"] == "watch_formal_value_candidate"
    assert row["delta_formal_p50_mae"] == -50_000
    assert row["delta_q6_formal_p50_mae"] == -50_000
    assert row["candidate_q6_formal_prediction_delta_mean"] == 50_000


def test_formal_value_delta_candidates_block_sources_without_q6_formal_delta() -> None:
    module = _load_module()
    rows = [_row(session_id=f"s{idx}") for idx in range(6)]

    result = module.summarize_candidates(
        rows,
        candidate_prefix="v3_resid_",
        min_windows=2,
        min_sessions=2,
    )

    assert result[0]["candidate_status"] == "blocked_no_q6_formal_upshift"
    assert "no_q6_formal_upshift" in result[0]["flags"]


def test_formal_value_delta_holdout_applies_training_candidates() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"f0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"f1_{idx}") for idx in range(4)]
    rows = [_row(session_id=session_id) for session_id in (*fold0, *fold1)]

    result = module.summarize_holdout(
        rows,
        candidate_prefix="v3_ccv_",
        folds=2,
        min_windows=2,
        min_sessions=2,
    )

    assert result["overall_status"] == "watch"
    assert result["candidate_formula"] == (
        "formal + (candidate_q6_formal - baseline_q6_formal)"
    )
    assert result["candidate_only"]["candidate_rows"] == 8
    assert result["candidate_only"]["delta_formal_p50_mae"] == -50_000
    assert result["candidate_only"]["delta_q6_formal_p50_mae"] == -50_000


def test_formal_value_delta_holdout_blocks_high_over_holdout() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"over_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"under_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(
                session_id=session_id,
                formal_pred=250_000,
                formal_truth=200_000,
                q6_formal_pred=50_000,
                q6_formal_candidate=100_000,
                q6_formal_truth=80_000,
            )
            for session_id in fold0
        ],
        *[_row(session_id=session_id) for session_id in fold1],
    ]

    result = module.summarize_holdout(
        rows,
        candidate_prefix="v3_ccv_",
        folds=2,
        min_windows=2,
        min_sessions=2,
    )

    assert result["overall_status"] == "blocked_holdout_hurt"
    assert result["applied_hurts"] == ["public:total+item+shape"]
    assert result["candidate_only"]["candidate_formal_p50_over_rate"] == 1.0
