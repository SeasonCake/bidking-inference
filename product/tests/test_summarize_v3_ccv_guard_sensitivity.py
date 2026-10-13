import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccv_guard_sensitivity.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccv_guard_sensitivity",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    file: str,
    ccv_count: int,
    ccv_cells: int,
) -> dict[str, object]:
    return {
        "file": file,
        "status": "ready",
        "round": 2,
        "session_id": "session_a",
        "map_id": 2503,
        "map_family": "shipwreck",
        "hero": "ethan",
        "hero_map_id": "ethan|2503",
        "evidence_stage": "mid_2_3",
        "information_density_band": "medium",
        "evidence_profile_key": "public:total+q6",
        "hero_map_evidence_stage": "ethan|2503|mid_2_3",
        "hero_map_evidence_profile": "ethan|2503|public:total+q6",
        "v3_truth_available": True,
        "v3_truth_decision_available": True,
        "v3_truth_formal_decision_value": 100_000,
        "v3_truth_q6_formal_decision_value": 100_000,
        "v3_truth_q6_count": 1,
        "v3_truth_q6_cells": 4,
        "v3_truth_q6_raw_value": 100_000,
        "v3_post_ready": True,
        "v3_post_match_scope": "summary_likelihood",
        "v3_post_formal_decision_value_p50": 100_000,
        "v3_post_formal_decision_value_p90": 100_000,
        "v3_post_q6_formal_decision_value_p50": 100_000,
        "v3_post_q6_formal_decision_value_p90": 100_000,
        "v3_post_q6_count_p50": 1,
        "v3_post_q6_count_p90": 1,
        "v3_post_q6_cells_p50": 4,
        "v3_post_q6_cells_p90": 4,
        "v3_post_q6_value_p50": 100_000,
        "v3_post_q6_value_p90": 100_000,
        "v3_ccv_ready": True,
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_ccv_q6_count_p50": ccv_count,
        "v3_ccv_q6_count_p90": ccv_count,
        "v3_ccv_q6_cells_p50": ccv_cells,
        "v3_ccv_q6_cells_p90": ccv_cells,
        "v3_ccv_q6_value_p50": 100_000,
        "v3_ccv_q6_value_p90": 100_000,
        "v3_ccv_q6_formal_decision_value_p50": 100_000,
        "v3_ccv_q6_formal_decision_value_p90": 100_000,
    }


def test_guard_sensitivity_reports_paired_ccv_deltas() -> None:
    module = _load_module()
    default_rows = [_row(file="a#r2", ccv_count=3, ccv_cells=20)]
    alternative_rows = [_row(file="a#r2", ccv_count=1, ccv_cells=4)]

    result = module.summarize_sensitivity(
        default_rows,
        [],
        alternative_rows,
        [],
        alternative_options=module.V3CcvOptions(count_cell_tail_guard=False),
        group_fields=(),
    )

    assert result["default_summary"]["v3_ccv_delta_q6_count_p50_mae"] == 2
    assert result["alternative_summary"]["v3_ccv_delta_q6_count_p50_mae"] == 0
    assert result["paired_diff"]["paired_rows"] == 1
    assert result["paired_diff"]["q6_count_changed_rows"] == 1
    assert result["paired_diff"]["q6_count_prediction_delta_mean"] == -2
    assert result["paired_diff"]["q6_count_mae_delta"] == -2
    assert result["paired_diff"]["q6_cells_changed_rows"] == 1
    assert result["paired_diff"]["q6_cells_prediction_delta_mean"] == -16
    assert result["paired_diff"]["q6_cells_mae_delta"] == -16
