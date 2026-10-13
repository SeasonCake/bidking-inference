import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccv_profile_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccv_profile_candidates",
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
    ccv_q6_count: int,
    truth_q6_cells: int,
    post_q6_cells: int,
    ccv_q6_cells: int,
    truth_q6_value: int = 100_000,
    post_q6_value: int = 100_000,
    ccv_q6_value: int = 100_000,
    truth_q6_formal: int = 100_000,
    post_q6_formal: int = 100_000,
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
        "v3_ccv_q6_count_p50": ccv_q6_count,
        "v3_truth_q6_cells": truth_q6_cells,
        "v3_post_q6_cells_p50": post_q6_cells,
        "v3_ccv_q6_cells_p50": ccv_q6_cells,
        "v3_truth_q6_raw_value": truth_q6_value,
        "v3_post_q6_value_p50": post_q6_value,
        "v3_ccv_q6_value_p50": ccv_q6_value,
        "v3_truth_q6_formal_decision_value": truth_q6_formal,
        "v3_post_q6_formal_decision_value_p50": post_q6_formal,
        "v3_ccv_q6_formal_decision_value_p50": ccv_q6_formal,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_ccv_candidates_mark_count_cell_improvement() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2506|shape",
            session_id="s1",
            truth_formal=100,
            formal_p50=105,
            formal_p90=130,
            truth_q6_count=2,
            post_q6_count=4,
            ccv_q6_count=2,
            truth_q6_cells=8,
            post_q6_cells=14,
            ccv_q6_cells=8,
        ),
        _row(
            "ethan|2506|shape",
            session_id="s2",
            truth_formal=120,
            formal_p50=130,
            formal_p90=150,
            truth_q6_count=1,
            post_q6_count=3,
            ccv_q6_count=1,
            truth_q6_cells=5,
            post_q6_cells=11,
            ccv_q6_cells=5,
        ),
    ]

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "watch_only_count_cell_candidate"
    assert result[0]["v3_ccv_delta_q6_count_p50_mae"] == -2.0
    assert result[0]["v3_ccv_delta_q6_cells_p50_mae"] == -6.0


def test_ccv_candidates_block_systemic_under_downshift() -> None:
    module = _load_module()
    rows = [
        _row(
            "aisha|2506|item+shape",
            session_id="s1",
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
            session_id="s2",
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

    result = module.summarize_candidates(rows, min_windows=2, min_sessions=2)

    assert result[0]["candidate_status"] == "blocked_under_count_cell_downshift"
    assert "systemic_under" in result[0]["flags"]
    assert "ccv_lowers_under_count" in result[0]["flags"]
    assert "ccv_lowers_under_cells" in result[0]["flags"]


def test_ccv_candidates_block_low_activity() -> None:
    module = _load_module()
    rows = [
        _row(
            "ethan|2401|shape",
            session_id="s1",
            truth_formal=100,
            formal_p50=110,
            formal_p90=130,
            truth_q6_count=1,
            post_q6_count=2,
            ccv_q6_count=1,
            truth_q6_cells=4,
            post_q6_cells=8,
            ccv_q6_cells=4,
        ),
        {
            **_row(
                "ethan|2401|shape",
                session_id="s2",
                truth_formal=100,
                formal_p50=110,
                formal_p90=130,
                truth_q6_count=1,
                post_q6_count=2,
                ccv_q6_count=1,
                truth_q6_cells=4,
                post_q6_cells=8,
                ccv_q6_cells=4,
            ),
            "v3_ccv_match_scope": "summary_likelihood",
        },
    ]

    result = module.summarize_candidates(
        rows,
        min_windows=2,
        min_sessions=2,
        min_ccv_likelihood_rate=0.75,
    )

    assert result[0]["candidate_status"] == "blocked_low_ccv_activity"
