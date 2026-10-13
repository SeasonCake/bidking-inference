import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccvc_evidence_contribution.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccvc_evidence_contribution",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    truth_count: int = 3,
    baseline_count: int = 1,
    ccvc_count: int = 2,
    truth_cells: int = 4,
    baseline_cells: int = 2,
    ccvc_cells: int = 3,
    profile: str = "public:total+shape",
    diagnostics: str = (
        "ccvc_explicit_q6_anchor_count=1;"
        "ccvc_unassigned_anchor_count=2"
    ),
    session_id: str = "s1",
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "evidence_profile_key": profile,
        "item_anchors": 1,
        "shape_anchors": 1,
        "v3_summary_session_total_cells_exact": 20
        if "public:total" in profile
        else None,
        "v3_summary_q6_count_floor": 1,
        "v3_summary_q6_cells_floor": 4,
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccvc_ready": True,
        "v3_ccvc_match_scope": "ccv_component_likelihood",
        "v3_ccvc_diagnostics": diagnostics,
        "v3_truth_q6_count": truth_count,
        "v3_post_q6_count_p50": baseline_count,
        "v3_ccvc_q6_count_p50": ccvc_count,
        "v3_truth_q6_cells": truth_cells,
        "v3_post_q6_cells_p50": baseline_cells,
        "v3_ccvc_q6_cells_p50": ccvc_cells,
    }


def test_feature_flags_parse_profile_summary_and_diagnostics() -> None:
    module = _load_module()
    flags = module._feature_flags(
        _row(profile="public:random_avg+shape+layout")
    )

    assert flags["public_random_avg"] is True
    assert flags["shape_anchor"] is True
    assert flags["layout"] is True
    assert flags["q6_floor"] is True
    assert flags["explicit_q6_anchor"] is True
    assert flags["unassigned_anchor"] is True
    assert flags["public_random_avg+shape_anchor"] is True


def test_contribution_summary_separates_helpful_and_hurting_features() -> None:
    module = _load_module()
    rows = [
        _row(
            session_id=f"public_{idx}",
            truth_count=4,
            baseline_count=2,
            ccvc_count=3,
            truth_cells=4,
            baseline_cells=2,
            ccvc_cells=1,
            profile="public:total+shape",
        )
        for idx in range(6)
    ] + [
        _row(
            session_id=f"plain_{idx}",
            truth_count=1,
            baseline_count=2,
            ccvc_count=2,
            truth_cells=1,
            baseline_cells=2,
            ccvc_cells=2,
            profile="shape",
            diagnostics="ccvc_explicit_q6_anchor_count=0;"
            "ccvc_unassigned_anchor_count=0",
        )
        for idx in range(6)
    ]

    result = module.summarize_contributions(
        rows,
        components=("q6_count", "q6_cells"),
        features=("public_total",),
        min_changed=2,
    )
    by_key = {
        (row["component"], row["feature"]): row
        for row in result["feature_results"]
    }

    count_public = by_key[("q6_count", "public_total")]
    cells_public = by_key[("q6_cells", "public_total")]
    assert count_public["mae_delta"] == -1
    assert count_public["status"] == "positive_contribution"
    assert cells_public["mae_delta"] == 1
    assert cells_public["status"] == "blocked_directional_hurt"
