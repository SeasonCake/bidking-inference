import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_scp_formal_value_link.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_scp_formal_value_link",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _formal_row(
    *,
    session_id: str,
    scp_candidate: bool,
    scp_status: str,
    truth: int,
    post_p50: int,
    post_p90: int,
    fv_p50: int,
    fv_p90: int,
    stress: str = "none",
    capacity_cases: str = "no_capacity_prior_max_case",
    truth_prior_delta: int = 0,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "v3_scp_ready": True,
        "v3_scp_active": False,
        "v3_scp_affects_bid": False,
        "v3_scp_candidate": scp_candidate,
        "v3_scp_missing_table": False,
        "v3_scp_status": scp_status,
        "v3_scp_group": "2501",
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_fv_ready": True,
        "v3_fv_active": False,
        "v3_fv_affects_bid": False,
        "v3_fv_stress_class": stress,
        "v3_truth_formal_decision_value": truth,
        "v3_post_formal_decision_value_p50": post_p50,
        "v3_post_formal_decision_value_p90": post_p90,
        "v3_fv_formal_decision_value_p50": fv_p50,
        "v3_fv_formal_decision_value_p90": fv_p90,
        "v3_capacity_cases": capacity_cases,
        "v3_capacity_truth_prior_max_delta": truth_prior_delta,
        "v3_capacity_target_prior_max_delta": truth_prior_delta,
    }


def test_scp_formal_value_link_quantifies_value_and_capacity_overlap() -> None:
    module = _load_module()
    rows = [
        _formal_row(
            session_id="a",
            scp_candidate=True,
            scp_status="observed_exceeds_table_caps_shadow_only",
            truth=100,
            post_p50=80,
            post_p90=90,
            fv_p50=95,
            fv_p90=105,
            stress="value_floor_stress",
            capacity_cases="direct_prior_max_conflict",
            truth_prior_delta=2,
        ),
        _formal_row(
            session_id="b",
            scp_candidate=True,
            scp_status="observed_exceeds_table_caps_shadow_only",
            truth=200,
            post_p50=210,
            post_p90=230,
            fv_p50=210,
            fv_p90=230,
            stress="capacity_cells_drift",
            capacity_cases="direct_prior_max_conflict",
            truth_prior_delta=3,
        ),
        _formal_row(
            session_id="c",
            scp_candidate=False,
            scp_status="table_caps_cover_observed_shadow_only",
            truth=50,
            post_p50=55,
            post_p90=60,
            fv_p50=55,
            fv_p90=60,
        ),
    ]

    result = module.summarize_link(rows, group_field="v3_scp_status")

    overall = result["overall"]
    assert overall["scp_rows"] == 3
    assert overall["formal_rows"] == 3
    assert overall["scp_candidate_rows"] == 2
    assert overall["scp_candidate_formal_rows"] == 2
    assert overall["scp_candidate_value_floor_rows"] == 1
    assert overall["scp_candidate_capacity_watch_rows"] == 1
    assert overall["capacity_prior_max_conflict_rows"] == 2
    assert overall["truth_above_prior_max_rows"] == 2
    assert overall["formal_baseline_p50_below_rate"] == 0.333333
    assert overall["formal_fv_delta_p50_mae"] == -5.0
    assert result["status_counts"] == {
        "no_scp_candidate_formal_rows": 1,
        "watch_scp_value_floor_overlap": 1,
    }


def test_scp_formal_value_link_keeps_missing_table_out_of_formal_metrics() -> None:
    module = _load_module()
    rows = [
        {
            "status": "ready",
            "session_id": "activity",
            "v3_scp_ready": True,
            "v3_scp_active": False,
            "v3_scp_affects_bid": False,
            "v3_scp_candidate": False,
            "v3_scp_missing_table": True,
            "v3_scp_status": "missing_table_shadow_only",
            "v3_scp_group": "252",
            "v3_truth_decision_available": True,
            "v3_post_ready": False,
            "v3_fv_ready": False,
        }
    ]

    result = module.summarize_link(rows, group_field="v3_scp_status")

    assert result["overall"]["scp_rows"] == 1
    assert result["overall"]["formal_rows"] == 0
    assert result["overall"]["scp_missing_table_rows"] == 1
    assert result["overall"]["link_status"] == "missing_table_shadow_only"
    assert result["status_counts"] == {"missing_table_shadow_only": 1}
