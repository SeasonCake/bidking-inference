import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_underestimate_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_underestimate_holdout",
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
    truth: int,
    pred: int,
    p90: int,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_id": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_formal_decision_value": truth,
        "v3_post_formal_decision_value_p50": pred,
        "v3_post_formal_decision_value_p90": p90,
        "v3_truth_q6_formal_decision_value": truth // 2,
        "v3_post_q6_formal_decision_value_p50": pred // 2,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_holdout_applies_train_candidate_to_holdout_rows() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"f0_{idx}") for idx in range(2)]
    fold1 = [_session_for_fold(module, 1, prefix=f"f1_{idx}") for idx in range(2)]
    rows = [
        _row("aisha|2506", session_id=session_id, truth=1_000, pred=500, p90=700)
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["candidate_rows"] == 4
    assert result["overall"]["delta_formal_p50_mae"] < 0
    assert result["candidate_only"]["candidate_groups"] == ["aisha|2506"]
    assert result["group_results"][0]["group"] == "aisha|2506"
    assert result["group_results"][0]["delta_formal_p50_mae"] < 0


def test_holdout_blocks_non_systemic_under_group() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="over0")
    fold1 = _session_for_fold(module, 1, prefix="over1")
    rows = [
        _row("ethan|2508", session_id=fold0, truth=1_000, pred=1_300, p90=1_500),
        _row("ethan|2508", session_id=fold1, truth=1_200, pred=1_500, p90=1_700),
    ]

    result = module.summarize_holdout(
        rows,
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    assert result["overall"]["candidate_rows"] == 0
    assert result["overall"]["delta_formal_p50_mae"] == 0
    assert result["candidate_only"]["n"] == 0
