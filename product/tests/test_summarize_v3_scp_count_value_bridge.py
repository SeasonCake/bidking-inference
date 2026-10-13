import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_scp_count_value_bridge.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_scp_count_value_bridge",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _metric_row(
    *,
    session_id: str,
    scp_candidate: bool = True,
    target_count: int = 44,
    scp_p95: int = 56,
    prior_max: int = 44,
    truth_count: int = 60,
    truth_cells: int = 240,
    post_cells_p50: int = 180,
    post_cells_p90: int = 220,
    truth_formal: int = 1_000,
    post_formal_p50: int = 700,
    post_formal_p90: int = 900,
) -> dict[str, object]:
    return {
        "status": "ready",
        "session_id": session_id,
        "v3_scp_ready": True,
        "v3_scp_active": False,
        "v3_scp_affects_bid": False,
        "v3_scp_candidate": scp_candidate,
        "v3_scp_missing_table": False,
        "v3_scp_status": "observed_exceeds_table_caps_shadow_only",
        "v3_scp_group": "2601",
        "v3_scp_target_count": target_count,
        "v3_scp_prior_items_per_session_max": prior_max,
        "v3_scp_non_temp_inventory_count_p95": scp_p95,
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_truth_item_count": truth_count,
        "v3_truth_total_cells": truth_cells,
        "v3_truth_formal_decision_value": truth_formal,
        "v3_post_total_cells_p50": post_cells_p50,
        "v3_post_total_cells_p90": post_cells_p90,
        "v3_post_formal_decision_value_p50": post_formal_p50,
        "v3_post_formal_decision_value_p90": post_formal_p90,
        "v3_capacity_cases": "direct_prior_max_conflict",
        "v3_fv_stress_class": "capacity_cells_drift",
    }


def test_scp_count_value_bridge_quantifies_cells_and_value_undercoverage() -> None:
    module = _load_module()
    rows = [
        _metric_row(session_id="a"),
        _metric_row(
            session_id="b",
            scp_candidate=False,
            target_count=40,
            scp_p95=40,
            truth_count=40,
            truth_cells=120,
            post_cells_p90=140,
            truth_formal=500,
            post_formal_p90=600,
        ),
    ]

    result = module.summarize_bridge(rows, group_field="v3_scp_group")

    overall = result["overall"]
    assert overall["scp_rows"] == 2
    assert overall["metric_rows"] == 2
    assert overall["scp_candidate_metric_rows"] == 1
    assert overall["scp_p95_above_target_rows"] == 1
    assert overall["truth_above_prior_rows"] == 1
    assert overall["target_below_truth_rows"] == 1
    assert overall["cells_p90_under_rows"] == 1
    assert overall["formal_p90_under_rows"] == 1
    assert overall["count_cells_bridge_rows"] == 1
    assert overall["count_value_bridge_rows"] == 1
    assert overall["count_cells_value_bridge_rows"] == 1
    assert overall["truth_cells_per_item"]["max"] == 4
    assert overall["truth_formal_per_item"]["max"] == 16.667
    assert result["status_counts"] == {"watch_count_cells_value_bridge": 1}


def test_scp_count_value_bridge_keeps_missing_table_shadow_only() -> None:
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
        }
    ]

    result = module.summarize_bridge(rows, group_field="v3_scp_group")

    overall = result["overall"]
    assert overall["scp_rows"] == 1
    assert overall["metric_rows"] == 0
    assert overall["scp_missing_table_rows"] == 1
    assert overall["bridge_status"] == "missing_table_shadow_only"
    assert result["status_counts"] == {"missing_table_shadow_only": 1}
