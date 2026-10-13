import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_tail_under_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_tail_under_holdout",
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
    formal_pred: int,
    formal_p90: int,
    tail_truth: int,
    tail_pred: int,
    tail_p90: int,
    q6_formal_truth: int,
    q6_formal_pred: int,
    q6_formal_p90: int,
    q6_tail_truth: int,
    q6_tail_pred: int,
    q6_tail_p90: int,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_id": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_formal_decision_value": formal_truth,
        "v3_post_formal_decision_value_p50": formal_pred,
        "v3_post_formal_decision_value_p90": formal_p90,
        "v3_truth_q6_formal_decision_value": q6_formal_truth,
        "v3_post_q6_formal_decision_value_p50": q6_formal_pred,
        "v3_post_q6_formal_decision_value_p90": q6_formal_p90,
        "v3_truth_tail_replacement_decision_value": tail_truth,
        "v3_truth_tail_replacement_value": max(0, tail_truth - formal_truth),
        "v3_post_tail_replacement_decision_value_p50": tail_pred,
        "v3_post_tail_replacement_decision_value_p90": tail_p90,
        "v3_truth_q6_tail_replacement_decision_value": q6_tail_truth,
        "v3_truth_q6_tail_replacement_value": max(
            0,
            q6_tail_truth - q6_formal_truth,
        ),
        "v3_post_q6_tail_replacement_decision_value_p50": q6_tail_pred,
        "v3_post_q6_tail_replacement_decision_value_p90": q6_tail_p90,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_tail_under_holdout_applies_guarded_candidates() -> None:
    module = _load_module()
    fold0 = [module._stable_fold("dummy", 2)]  # import-time smoke for helper presence
    assert fold0[0] in (0, 1)
    sessions0 = [
        _session_for_fold(module, 0, prefix=f"aisha0_{idx}") for idx in range(2)
    ]
    sessions1 = [
        _session_for_fold(module, 1, prefix=f"aisha1_{idx}") for idx in range(2)
    ]
    rows = [
        _row(
            "aisha|2506",
            session_id=session_id,
            formal_truth=600_000,
            formal_pred=300_000,
            formal_p90=420_000,
            tail_truth=900_000,
            tail_pred=870_000,
            tail_p90=880_000,
            q6_formal_truth=300_000,
            q6_formal_pred=150_000,
            q6_formal_p90=210_000,
            q6_tail_truth=500_000,
            q6_tail_pred=480_000,
            q6_tail_p90=490_000,
        )
        for session_id in (*sessions0, *sessions1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["under_candidate_rows"] == 4
    assert result["overall"]["tail_candidate_rows"] == 4
    assert result["overall"]["delta_formal_p50_mae"] < 0
    assert result["overall"]["delta_tail_p50_mae"] < 0
    assert result["overall"]["delta_q6_tail_p50_mae"] < 0
    assert result["candidate_only"]["under_candidate_groups"] == ["aisha|2506"]
    assert result["candidate_only"]["tail_candidate_groups"] == ["aisha|2506"]


def test_tail_under_holdout_keeps_tail_hurt_guard_inactive() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="hurt0")
    fold1 = _session_for_fold(module, 1, prefix="hurt1")
    rows = [
        _row(
            "ethan|2601",
            session_id=fold0,
            formal_truth=600_000,
            formal_pred=620_000,
            formal_p90=700_000,
            tail_truth=600_000,
            tail_pred=950_000,
            tail_p90=980_000,
            q6_formal_truth=250_000,
            q6_formal_pred=260_000,
            q6_formal_p90=300_000,
            q6_tail_truth=250_000,
            q6_tail_pred=650_000,
            q6_tail_p90=700_000,
        ),
        _row(
            "ethan|2601",
            session_id=fold1,
            formal_truth=650_000,
            formal_pred=660_000,
            formal_p90=720_000,
            tail_truth=650_000,
            tail_pred=990_000,
            tail_p90=1_020_000,
            q6_formal_truth=300_000,
            q6_formal_pred=310_000,
            q6_formal_p90=350_000,
            q6_tail_truth=300_000,
            q6_tail_pred=680_000,
            q6_tail_p90=720_000,
        ),
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["tail_hurt_guard_rows"] == 2
    assert result["overall"]["tail_candidate_rows"] == 0
    assert result["overall"]["delta_tail_p50_mae"] == 0
    assert result["tail_hurt_guard_only"]["tail_hurt_guard_groups"] == ["ethan|2601"]
