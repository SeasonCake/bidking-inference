import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccv_layer_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccv_layer_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_fold(module, fold: int, *, prefix: str) -> str:
    ccv_holdout = module.sys.modules["summarize_v3_ccv_holdout"]
    for idx in range(1000):
        session_id = f"{prefix}_{idx}"
        if ccv_holdout._stable_fold(session_id, 2) == fold:
            return session_id
    raise AssertionError(f"no session for fold {fold}")


def _row(
    *,
    session_id: str,
    group: str = "ethan|2502",
    truth_count: int = 2,
    base_count: int = 1,
    ccv_count: int = 2,
    truth_cells: int = 8,
    base_cells: int = 4,
    ccv_cells: int = 8,
) -> dict[str, object]:
    map_id = int(group.split("|", 1)[1])
    return {
        "status": "ready",
        "session_id": session_id,
        "hero_map_id": group,
        "map_id": map_id,
        "map_family": "shipwreck",
        "hero_map_evidence_profile": f"{group}|public:total+item+shape",
        "v3_truth_decision_available": True,
        "v3_post_ready": True,
        "v3_ccv_ready": True,
        "v3_post_match_scope": "summary_likelihood",
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_post_formal_decision_value_p50": 500_000,
        "v3_post_formal_decision_value_p90": 700_000,
        "v3_truth_formal_decision_value": 700_000,
        "v3_truth_q6_count": truth_count,
        "v3_post_q6_count_p50": base_count,
        "v3_post_q6_count_p90": base_count + 1,
        "v3_ccv_q6_count_p50": ccv_count,
        "v3_ccv_q6_count_p90": ccv_count + 1,
        "v3_truth_q6_cells": truth_cells,
        "v3_post_q6_cells_p50": base_cells,
        "v3_post_q6_cells_p90": base_cells + 4,
        "v3_ccv_q6_cells_p50": ccv_cells,
        "v3_ccv_q6_cells_p90": ccv_cells + 4,
        "v3_truth_q6_raw_value": 300_000,
        "v3_post_q6_value_p50": 150_000,
        "v3_post_q6_value_p90": 250_000,
        "v3_ccv_q6_value_p50": 300_000,
        "v3_ccv_q6_value_p90": 350_000,
        "v3_truth_q6_formal_decision_value": 300_000,
        "v3_post_q6_formal_decision_value_p50": 150_000,
        "v3_post_q6_formal_decision_value_p90": 250_000,
        "v3_ccv_q6_formal_decision_value_p50": 300_000,
        "v3_ccv_q6_formal_decision_value_p90": 350_000,
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
    }


def test_layer_audit_reports_multiple_grouping_layers() -> None:
    module = _load_module()
    sessions0 = [
        _session_for_fold(module, 0, prefix=f"ccv0_{idx}") for idx in range(2)
    ]
    sessions1 = [
        _session_for_fold(module, 1, prefix=f"ccv1_{idx}") for idx in range(2)
    ]
    rows = [_row(session_id=session_id) for session_id in (*sessions0, *sessions1)]

    result = module.summarize_layers(
        rows,
        group_fields=("hero_map_id", "map_id"),
        folds=2,
        min_windows=1,
        min_sessions=1,
    )

    layers = {row["group_field"]: row for row in result["layers"]}
    assert set(layers) == {"hero_map_id", "map_id"}
    assert layers["hero_map_id"]["candidate_rows"] == 4
    assert layers["hero_map_id"]["status"] == "watch"
    assert layers["map_id"]["candidate_groups"] == ["2502"]
