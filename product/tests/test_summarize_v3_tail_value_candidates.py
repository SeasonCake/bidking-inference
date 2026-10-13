import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_tail_value_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_tail_value_candidates",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
            q6_tail_truth - q6_formal_truth
            if q6_tail_truth
            else 0
        ),
        "v3_post_q6_formal_decision_value_p50": q6_formal_pred,
        "v3_post_q6_tail_replacement_decision_value_p50": q6_tail_pred,
        "v3_post_q6_tail_replacement_decision_value_p90": q6_tail_p90,
        "v3_summary_session_total_cells_exact": 80 if public_total else None,
        "v3_summary_q6_cells_floor": 4 if q6_floor else 0,
    }


def test_tail_value_candidates_mark_q6_tail_review() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2506",
            session_id="s1",
            formal_truth=600_000,
            tail_truth=900_000,
            formal_pred=550_000,
            tail_pred=850_000,
            tail_p90=880_000,
            q6_tail_truth=500_000,
            q6_formal_pred=250_000,
            q6_tail_pred=480_000,
            q6_tail_p90=490_000,
        ),
        _row(
            "aisha|2506",
            session_id="s2",
            formal_truth=700_000,
            tail_truth=1_000_000,
            formal_pred=650_000,
            tail_pred=930_000,
            tail_p90=960_000,
            q6_tail_truth=600_000,
            q6_formal_pred=300_000,
            q6_tail_pred=560_000,
            q6_tail_p90=580_000,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_q6_tail_value_candidate"
    assert result[0]["tail_replacement_delta_mae_vs_formal_to_tail"] < 0
    assert result[0]["q6_tail_replacement_delta_mae_vs_formal_to_tail"] < 0
    assert "q6_tail_p90_miss" in result[0]["flags"]


def test_tail_value_candidates_block_no_tail_signal() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2502",
            session_id="s1",
            formal_truth=500_000,
            tail_truth=500_000,
            formal_pred=490_000,
            tail_pred=490_000,
            tail_p90=520_000,
        ),
        _row(
            "ethan|2502",
            session_id="s2",
            formal_truth=600_000,
            tail_truth=600_000,
            formal_pred=610_000,
            tail_pred=610_000,
            tail_p90=650_000,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "blocked_no_tail_signal"
    assert "no_tail_signal" in result[0]["flags"]


def test_tail_value_candidates_block_tail_estimate_hurts() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2401",
            session_id="s1",
            formal_truth=500_000,
            tail_truth=700_000,
            formal_pred=680_000,
            tail_pred=1_000_000,
            tail_p90=1_100_000,
            q6_tail_truth=300_000,
            q6_formal_pred=280_000,
            q6_tail_pred=600_000,
            q6_tail_p90=650_000,
        ),
        _row(
            "aisha|2401",
            session_id="s2",
            formal_truth=600_000,
            tail_truth=800_000,
            formal_pred=760_000,
            tail_pred=1_100_000,
            tail_p90=1_200_000,
            q6_tail_truth=400_000,
            q6_formal_pred=390_000,
            q6_tail_pred=700_000,
            q6_tail_p90=750_000,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "blocked_tail_estimate_hurts"
    assert "tail_estimate_hurts_total" in result[0]["flags"]
    assert "tail_estimate_hurts_q6" in result[0]["flags"]


def test_tail_value_candidates_need_evidence_when_under_context_is_weak() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2601",
            session_id="s1",
            formal_truth=600_000,
            tail_truth=900_000,
            formal_pred=650_000,
            tail_pred=880_000,
            tail_p90=930_000,
            q6_tail_truth=500_000,
            q6_formal_pred=260_000,
            q6_tail_pred=480_000,
            q6_tail_p90=520_000,
        ),
        _row(
            "ethan|2601",
            session_id="s2",
            formal_truth=700_000,
            tail_truth=1_000_000,
            formal_pred=760_000,
            tail_pred=970_000,
            tail_p90=1_020_000,
            q6_tail_truth=600_000,
            q6_formal_pred=320_000,
            q6_tail_pred=580_000,
            q6_tail_p90=620_000,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_needs_evidence"
    assert "weak_tail_under_context" in result[0]["flags"]
