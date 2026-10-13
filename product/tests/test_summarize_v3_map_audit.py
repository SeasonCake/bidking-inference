import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_v3_map_audit.py"
    spec = importlib.util.spec_from_file_location("summarize_v3_map_audit", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_maps_separates_sample_and_model_risk_flags() -> None:
    module = _load_module()
    rows = [
        {
            "status": "ready",
            "map_id": 2506,
            "map_family": "shipwreck",
            "hero": "aisha",
            "evidence_stage": "early_1_2",
            "information_density_band": "low",
            "evidence_profile_key": "shape",
            "hero_map_evidence_profile": "aisha|2506|shape",
            "round": 1,
            "session_id": "s1",
            "prior_state_count": 1,
            "numeric_constraints": 1,
            "item_anchors": 1,
            "shape_anchors": 4,
            "quality_floor_anchors": 1,
            "v3_truth_decision_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "summary_likelihood",
            "v3_prior_map_name": "test",
            "v3_truth_formal_decision_value": 1_000,
            "v3_post_formal_decision_value_p50": 500,
            "v3_post_formal_decision_value_p90": 900,
            "v3_truth_q6_formal_decision_value": 400,
            "v3_post_q6_formal_decision_value_p50": 200,
            "v3_truth_q6_count": 1,
            "v3_truth_q6_cells": 4,
            "v3_truth_q6_raw_value": 100,
            "v3_post_q6_count_p50": 0,
            "v3_post_q6_cells_p50": 2,
            "v3_post_q6_value_p50": 80,
            "v3_ccv_match_scope": "ccv_likelihood",
            "v3_ccv_q6_count_p50": 1,
            "v3_ccv_q6_cells_p50": 4,
            "v3_resid_match_scope": "residual_likelihood",
            "v3_resid_q6_count_p50": 1,
            "v3_resid_q6_cells_p50": 4,
            "v3_resid_q6_value_p50": 100,
            "v3_resid_gate_active": True,
            "v3_resid_gate_q6_count_p50": 1,
            "v3_resid_gate_q6_cells_p50": 4,
            "v3_resid_gate_q6_value_p50": 100,
        },
        {
            "status": "ready",
            "map_id": 2506,
            "map_family": "shipwreck",
            "hero": "ethan",
            "evidence_stage": "mid_3_4",
            "information_density_band": "medium",
            "evidence_profile_key": "public:total+shape+layout",
            "hero_map_evidence_profile": "ethan|2506|public:total+shape+layout",
            "round": 2,
            "session_id": "s1",
            "prior_state_count": 2,
            "numeric_constraints": 1,
            "item_anchors": 1,
            "shape_anchors": 5,
            "quality_floor_anchors": 1,
            "v3_truth_decision_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "summary_likelihood",
            "v3_summary_session_total_cells_exact": 88,
            "v3_summary_q6_count_floor": 1,
            "v3_truth_formal_decision_value": 1_200,
            "v3_post_formal_decision_value_p50": 600,
            "v3_post_formal_decision_value_p90": 1_400,
            "v3_truth_q6_formal_decision_value": 500,
            "v3_post_q6_formal_decision_value_p50": 300,
            "v3_truth_q6_count": 2,
            "v3_truth_q6_cells": 8,
            "v3_truth_q6_raw_value": 300,
            "v3_post_q6_count_p50": 1,
            "v3_post_q6_cells_p50": 4,
            "v3_post_q6_value_p50": 200,
            "v3_ccv_match_scope": "ccv_likelihood",
            "v3_ccv_q6_count_p50": 2,
            "v3_ccv_q6_cells_p50": 7,
            "v3_resid_match_scope": "residual_likelihood",
            "v3_resid_q6_count_p50": 2,
            "v3_resid_q6_cells_p50": 8,
            "v3_resid_q6_value_p50": 260,
            "v3_resid_gate_active": False,
            "v3_resid_gate_q6_count_p50": 1,
            "v3_resid_gate_q6_cells_p50": 4,
            "v3_resid_gate_q6_value_p50": 200,
        },
        {
            "status": "no_state",
            "map_id": 2506,
            "map_family": "shipwreck",
            "round": 1,
            "session_id": "s2",
            "v3_truth_decision_available": False,
            "v3_post_ready": False,
        },
    ]

    result = module.summarize_maps(rows)

    assert len(result) == 1
    row = result[0]
    assert row["map_id"] == 2506
    assert row["sessions"] == 2
    assert row["ready_windows"] == 2
    assert row["no_state_windows"] == 1
    assert row["paired_windows"] == 2
    assert row["rounds"] == {"R1": 1, "R2": 1}
    assert row["heroes"] == {"aisha": 1, "ethan": 1}
    assert row["evidence_stages"] == {"early_1_2": 1, "mid_3_4": 1}
    assert row["information_density"] == {"low": 1, "medium": 1}
    assert row["evidence_profiles"] == {
        "public:total+shape+layout": 1,
        "shape": 1,
    }
    assert row["formal_p50_mae"] == 550
    assert row["formal_p50_bias"] == -550
    assert row["formal_p50_below_rate"] == 1.0
    assert row["formal_p90_coverage"] == 0.5
    assert row["q6_formal_p50_mae"] == 200
    assert row["q6_count_p50_mae"] == 1
    assert row["q6_cells_p50_mae"] == 3
    assert row["v3_ccv_likelihood_rate"] == 1.0
    assert row["v3_ccv_q6_count_p50_mae"] == 0
    assert row["v3_ccv_delta_q6_count_p50_mae"] == -1
    assert row["v3_ccv_q6_cells_p50_mae"] == 0.5
    assert row["v3_ccv_delta_q6_cells_p50_mae"] == -2.5
    assert row["q6_value_p50_mae"] == 60
    assert row["v3_resid_likelihood_rate"] == 1.0
    assert row["v3_resid_q6_count_p50_mae"] == 0
    assert row["v3_resid_delta_q6_count_p50_mae"] == -1
    assert row["v3_resid_q6_cells_p50_mae"] == 0
    assert row["v3_resid_delta_q6_cells_p50_mae"] == -3
    assert row["v3_resid_q6_value_p50_mae"] == 20
    assert row["v3_resid_delta_q6_value_p50_mae"] == -40
    assert row["v3_resid_gate_active_rate"] == 0.5
    assert row["v3_resid_gate_q6_count_p50_mae"] == 0.5
    assert row["v3_resid_gate_delta_q6_count_p50_mae"] == -0.5
    assert row["v3_resid_gate_q6_cells_p50_mae"] == 2
    assert row["v3_resid_gate_delta_q6_cells_p50_mae"] == -1
    assert row["v3_resid_gate_q6_value_p50_mae"] == 50
    assert row["v3_resid_gate_delta_q6_value_p50_mae"] == -10
    assert row["public_total_rate"] == 0.5
    assert row["q6_floor_rate"] == 0.5
    assert "few_sessions" in row["flags"]
    assert "few_windows" in row["flags"]
    assert "mostly_fallback" in row["flags"]
    assert "systemic_under" in row["flags"]
