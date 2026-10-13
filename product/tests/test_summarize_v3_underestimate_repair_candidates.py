import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_underestimate_repair_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_underestimate_repair_candidates",
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
    truth: int,
    pred: int,
    p90: int,
    q6_truth: int,
    q6_pred: int,
    public_total: bool = True,
    q6_floor: bool = True,
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
        "v3_truth_q6_formal_decision_value": q6_truth,
        "v3_post_q6_formal_decision_value_p50": q6_pred,
        "v3_summary_session_total_cells_exact": 80 if public_total else None,
        "v3_summary_q6_cells_floor": 4 if q6_floor else 0,
    }


def test_underestimate_repair_candidate_scales_systemic_under_group() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2506",
            session_id="s1",
            truth=1_000,
            pred=500,
            p90=700,
            q6_truth=600,
            q6_pred=300,
        ),
        _row(
            "aisha|2506",
            session_id="s2",
            truth=1_200,
            pred=600,
            p90=800,
            q6_truth=700,
            q6_pred=350,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_upshift_candidate"
    assert result[0]["proposed_scale"] > 1.0
    assert result[0]["scaled_delta_formal_p50_mae"] < 0
    assert result[0]["scaled_formal_p50_below_rate"] == 1.0


def test_underestimate_repair_candidate_blocks_high_over_group() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2508",
            session_id="s1",
            truth=1_000,
            pred=1_300,
            p90=1_500,
            q6_truth=600,
            q6_pred=800,
        ),
        _row(
            "ethan|2508",
            session_id="s2",
            truth=1_200,
            pred=1_500,
            p90=1_700,
            q6_truth=700,
            q6_pred=900,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "blocked_not_systemic_under"
    assert "not_systemic_under" in result[0]["flags"]
    assert "high_over_rate" in result[0]["flags"]


def test_underestimate_repair_candidate_keeps_hidden_needs_evidence() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2601",
            session_id="s1",
            truth=1_000,
            pred=500,
            p90=700,
            q6_truth=600,
            q6_pred=300,
        ),
        _row(
            "aisha|2601",
            session_id="s2",
            truth=1_200,
            pred=600,
            p90=800,
            q6_truth=700,
            q6_pred=350,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_needs_evidence"
    assert "hidden_requires_separate_validation" in result[0]["flags"]
