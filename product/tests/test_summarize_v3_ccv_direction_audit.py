import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccv_direction_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccv_direction_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    group: str,
    session_id: str,
    truth: int,
    baseline: int,
    ccv: int,
    profile: str = "shape",
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "map_id": group,
        "evidence_profile_key": profile,
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccv_ready": True,
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_summary_session_total_cells_exact": 80,
        "v3_summary_q6_cells_floor": 4,
        "v3_post_q6_count_p50": baseline,
        "v3_ccv_q6_count_p50": ccv,
        "v3_truth_q6_count": truth,
        "v3_post_q6_cells_p50": baseline,
        "v3_ccv_q6_cells_p50": ccv,
        "v3_truth_q6_cells": truth,
        "v3_post_q6_value_p50": baseline * 100_000,
        "v3_ccv_q6_value_p50": ccv * 100_000,
        "v3_truth_q6_raw_value": truth * 100_000,
        "v3_post_q6_formal_decision_value_p50": baseline * 100_000,
        "v3_ccv_q6_formal_decision_value_p50": ccv * 100_000,
        "v3_truth_q6_formal_decision_value": truth * 100_000,
    }


def test_ccv_direction_audit_blocks_wrong_direction_moves() -> None:
    module = _load_module()
    rows = [
        _row(group="2503", session_id=f"s{idx}", truth=2, baseline=1, ccv=0)
        for idx in range(6)
    ]

    result = module.summarize_direction(
        rows,
        group_field="map_id",
        components=("q6_count",),
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert len(result) == 1
    row = result[0]
    assert row["status"] == "blocked_directional_hurt"
    assert row["hurt_changed"] == 6
    assert row["directional_error_rows"] == 6
    assert row["mae_delta"] == 1


def test_ccv_direction_audit_marks_helpful_moves_as_candidate() -> None:
    module = _load_module()
    rows = [
        _row(group="2502", session_id=f"s{idx}", truth=4, baseline=2, ccv=3)
        for idx in range(6)
    ]

    result = module.summarize_direction(
        rows,
        group_field="map_id",
        components=("q6_count",),
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert len(result) == 1
    row = result[0]
    assert row["status"] == "watch_directional_candidate"
    assert row["helped_changed"] == 6
    assert row["directional_error_rows"] == 0
    assert row["mae_delta"] == -1


def test_ccv_direction_audit_can_filter_to_up_only_moves() -> None:
    module = _load_module()
    rows = [
        _row(group="2503", session_id=f"s{idx}", truth=2, baseline=1, ccv=0)
        for idx in range(6)
    ]

    result = module.summarize_direction(
        rows,
        group_field="map_id",
        components=("q6_count",),
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        movement_policy="up_only",
    )

    assert len(result) == 1
    row = result[0]
    assert row["movement_policy"] == "up_only"
    assert row["status"] == "blocked_low_movement"
    assert row["changed_rows"] == 0
    assert row["mae_delta"] == 0


def test_ccv_direction_audit_can_use_composite_group_field() -> None:
    module = _load_module()
    rows = [
        *[
            _row(
                group="2502",
                profile="shape",
                session_id=f"shape_{idx}",
                truth=4,
                baseline=2,
                ccv=3,
            )
            for idx in range(3)
        ],
        *[
            _row(
                group="2502",
                profile="public:total+shape",
                session_id=f"total_{idx}",
                truth=4,
                baseline=2,
                ccv=3,
            )
            for idx in range(3)
        ],
    ]

    result = module.summarize_direction(
        rows,
        group_field="map_id,evidence_profile_key",
        components=("q6_count",),
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert [row["group"] for row in result] == [
        "map_id=2502|evidence_profile_key=public:total+shape",
        "map_id=2502|evidence_profile_key=shape",
    ]
