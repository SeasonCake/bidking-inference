import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_tail_value_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_tail_value_holdout",
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
    group: str,
    *,
    session_id: str,
    formal_truth: int,
    tail_truth: int,
    formal_pred: int,
    tail_pred: int,
    tail_p90: int,
    q6_tail_truth: int = 0,
    q6_formal_pred: int = 0,
    q6_tail_pred: int = 0,
    q6_tail_p90: int = 0,
    public_total: bool = True,
    q6_floor: bool = True,
) -> dict[str, object]:
    q6_formal_truth = max(0, q6_tail_truth - 100_000) if q6_tail_truth else 0
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_id": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_formal_decision_value": formal_truth,
        "v3_truth_tail_replacement_decision_value": tail_truth,
        "v3_truth_tail_replacement_value": max(0, tail_truth - formal_truth),
        "v3_post_formal_decision_value_p50": formal_pred,
        "v3_post_formal_decision_value_p90": formal_pred + 50_000,
        "v3_post_tail_replacement_decision_value_p50": tail_pred,
        "v3_post_tail_replacement_decision_value_p90": tail_p90,
        "v3_truth_q6_formal_decision_value": q6_formal_truth,
        "v3_truth_q6_tail_replacement_decision_value": q6_tail_truth,
        "v3_truth_q6_tail_replacement_value": (
            q6_tail_truth - q6_formal_truth if q6_tail_truth else 0
        ),
        "v3_post_q6_formal_decision_value_p50": q6_formal_pred,
        "v3_post_q6_formal_decision_value_p90": q6_formal_pred + 50_000,
        "v3_post_q6_tail_replacement_decision_value_p50": q6_tail_pred,
        "v3_post_q6_tail_replacement_decision_value_p90": q6_tail_p90,
        "v3_summary_session_total_cells_exact": 80 if public_total else None,
        "v3_summary_q6_cells_floor": 4 if q6_floor else 0,
    }


def test_tail_holdout_applies_train_candidate_to_holdout_rows() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"tail0_{idx}") for idx in range(2)]
    fold1 = [_session_for_fold(module, 1, prefix=f"tail1_{idx}") for idx in range(2)]
    rows = [
        _row(
            "aisha|2506",
            session_id=session_id,
            formal_truth=600_000,
            tail_truth=900_000,
            formal_pred=610_000,
            tail_pred=870_000,
            tail_p90=880_000,
            q6_tail_truth=500_000,
            q6_formal_pred=220_000,
            q6_tail_pred=480_000,
            q6_tail_p90=490_000,
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
    assert result["overall"]["delta_tail_p50_mae"] < 0
    assert result["overall"]["delta_q6_tail_p50_mae"] < 0
    assert result["candidate_only"]["candidate_groups"] == ["aisha|2506"]
    assert result["group_results"][0]["group"] == "aisha|2506"


def test_tail_holdout_does_not_apply_no_tail_signal_group() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="notail0")
    fold1 = _session_for_fold(module, 1, prefix="notail1")
    rows = [
        _row(
            "ethan|2502",
            session_id=fold0,
            formal_truth=500_000,
            tail_truth=500_000,
            formal_pred=490_000,
            tail_pred=490_000,
            tail_p90=520_000,
        ),
        _row(
            "ethan|2502",
            session_id=fold1,
            formal_truth=600_000,
            tail_truth=600_000,
            formal_pred=610_000,
            tail_pred=610_000,
            tail_p90=650_000,
        ),
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["candidate_rows"] == 0
    assert result["overall"]["delta_tail_p50_mae"] == 0
    assert result["candidate_only"]["n"] == 0
