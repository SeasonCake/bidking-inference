import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_residual_profile_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_residual_profile_candidates",
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
    truth_formal: int,
    formal_p50: int,
    formal_p90: int,
    truth_q6_count: int,
    post_q6_count: int,
    resid_q6_count: int,
    truth_q6_cells: int,
    post_q6_cells: int,
    resid_q6_cells: int,
    truth_q6_value: int,
    post_q6_value: int,
    resid_q6_value: int,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_evidence_profile": group,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_resid_ready": True,
        "v3_post_match_scope": "summary_likelihood",
        "v3_resid_match_scope": "residual_likelihood",
        "v3_truth_formal_decision_value": truth_formal,
        "v3_post_formal_decision_value_p50": formal_p50,
        "v3_post_formal_decision_value_p90": formal_p90,
        "v3_truth_q6_count": truth_q6_count,
        "v3_post_q6_count_p50": post_q6_count,
        "v3_resid_q6_count_p50": resid_q6_count,
        "v3_truth_q6_cells": truth_q6_cells,
        "v3_post_q6_cells_p50": post_q6_cells,
        "v3_resid_q6_cells_p50": resid_q6_cells,
        "v3_truth_q6_raw_value": truth_q6_value,
        "v3_post_q6_value_p50": post_q6_value,
        "v3_resid_q6_value_p50": resid_q6_value,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_residual_profile_candidates_mark_promising_over_correction() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2506|shape",
            session_id="s1",
            truth_formal=100,
            formal_p50=130,
            formal_p90=150,
            truth_q6_count=1,
            post_q6_count=2,
            resid_q6_count=1,
            truth_q6_cells=6,
            post_q6_cells=10,
            resid_q6_cells=6,
            truth_q6_value=100,
            post_q6_value=220,
            resid_q6_value=105,
        ),
        _row(
            "ethan|2506|shape",
            session_id="s2",
            truth_formal=100,
            formal_p50=125,
            formal_p90=140,
            truth_q6_count=1,
            post_q6_count=2,
            resid_q6_count=1,
            truth_q6_cells=8,
            post_q6_cells=12,
            resid_q6_cells=8,
            truth_q6_value=120,
            post_q6_value=240,
            resid_q6_value=125,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_over_correction_candidate"
    assert result[0]["v3_resid_delta_q6_count_p50_mae"] == -1.0
    assert result[0]["v3_resid_delta_q6_cells_p50_mae"] == -4.0
    assert result[0]["v3_resid_delta_q6_value_p50_mae"] == -115.0


def test_residual_profile_candidates_block_under_value_downshift() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2506|item+shape",
            session_id="s1",
            truth_formal=400,
            formal_p50=200,
            formal_p90=300,
            truth_q6_count=4,
            post_q6_count=3,
            resid_q6_count=2,
            truth_q6_cells=20,
            post_q6_cells=16,
            resid_q6_cells=12,
            truth_q6_value=500_000,
            post_q6_value=420_000,
            resid_q6_value=250_000,
        ),
        _row(
            "aisha|2506|item+shape",
            session_id="s2",
            truth_formal=420,
            formal_p50=210,
            formal_p90=320,
            truth_q6_count=5,
            post_q6_count=4,
            resid_q6_count=3,
            truth_q6_cells=24,
            post_q6_cells=18,
            resid_q6_cells=14,
            truth_q6_value=600_000,
            post_q6_value=500_000,
            resid_q6_value=300_000,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "blocked_under_value_downshift"
    assert "systemic_under" in result[0]["flags"]
    assert "resid_lowers_under_value" in result[0]["flags"]
