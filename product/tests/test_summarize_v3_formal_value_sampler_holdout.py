import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_formal_value_sampler_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_formal_value_sampler_holdout",
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
    truth: int = 1_000,
    pred: int = 600,
    fv_pred: int = 900,
    q6_truth: int = 500,
    q6_pred: int = 200,
    q6_fv_pred: int = 450,
    candidate: bool = True,
    stress_class: str = "value_floor_stress",
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_id": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_fv_ready": True,
        "v3_fv_affects_bid": False,
        "v3_fv_active": False,
        "v3_fv_candidate": candidate,
        "v3_fv_stress_class": stress_class,
        "v3_truth_formal_decision_value": truth,
        "v3_post_formal_decision_value_p50": pred,
        "v3_post_formal_decision_value_p90": pred + 200,
        "v3_fv_formal_decision_value_p50": fv_pred,
        "v3_fv_formal_decision_value_p90": fv_pred + 200,
        "v3_truth_q6_formal_decision_value": q6_truth,
        "v3_post_q6_formal_decision_value_p50": q6_pred,
        "v3_post_q6_formal_decision_value_p90": q6_pred + 150,
        "v3_fv_q6_formal_decision_value_p50": q6_fv_pred,
        "v3_fv_q6_formal_decision_value_p90": q6_fv_pred + 150,
    }


def test_formal_value_sampler_holdout_applies_value_floor_candidates() -> None:
    module = _load_module()
    fold0 = [
        _session_for_fold(module, 0, prefix=f"fv0_{idx}")
        for idx in range(2)
    ]
    fold1 = [
        _session_for_fold(module, 1, prefix=f"fv1_{idx}")
        for idx in range(2)
    ]
    rows = [
        _row("aisha|2506", session_id=session_id)
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["candidate_rows"] == 4
    assert result["candidate_only"]["candidate_groups"] == ["aisha|2506"]
    assert result["candidate_only"]["delta_formal_p50_mae"] < 0
    assert result["candidate_only"]["delta_q6_formal_p50_mae"] < 0
    assert result["applied_hurts"] == []


def test_formal_value_sampler_holdout_ignores_capacity_only_watch() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="capacity0")
    fold1 = _session_for_fold(module, 1, prefix="capacity1")
    rows = [
        _row(
            "ethan|2506",
            session_id=session_id,
            candidate=False,
            stress_class="capacity_cells_drift+q6_cells_floor_stress",
        )
        for session_id in (fold0, fold1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall_status"] == "sample_limited"
    assert result["candidate_only"]["candidate_rows"] == 0
    assert result["overall"]["capacity_watch_rows"] == 2


def test_formal_value_sampler_holdout_ignores_mixed_value_floor_watch() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="mixed0")
    fold1 = _session_for_fold(module, 1, prefix="mixed1")
    rows = [
        _row(
            "ethan|2401",
            session_id=session_id,
            candidate=True,
            stress_class="q6_cells_floor_stress+value_floor_stress",
        )
        for session_id in (fold0, fold1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall_status"] == "sample_limited"
    assert result["candidate_only"]["candidate_rows"] == 0
    assert result["overall"]["mixed_value_floor_watch_rows"] == 2
