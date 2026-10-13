"""Cross-lane contracts for the final 0.3.2 engine Phase 2 composition."""

from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from test_aisha_5606_q5_total_cells_conditional_value_0_3_2 import (  # noqa: E402
    SESSION_ID,
    _target_snapshot,
)


def _snapshot_with_phase1_authorities(*, nonfinite_q5_wire: bool) -> dict:
    snapshot = _target_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    public_info["public_avg_values"] = [
        {
            "info_id": 200037,
            "semantic": "q5_avg_value",
            "kind": "avg_value",
            "quality": 5,
            "value": 50_000,
        }
    ]
    public_info["public_numeric_facts"] = [
        {
            "info_id": 200017,
            "semantic": "total_item_count",
            "kind": "count",
            "value": 32,
        },
        {
            "info_id": 200014,
            "semantic": "total_avg_cells",
            "kind": "avg_cells",
            "value": 79 / 32,
        },
    ]
    snapshot["public_info_rows"] = [
        {"info_id": 200037, "value": 50_000, "session_id": SESSION_ID},
        {"info_id": 200017, "value": 32, "session_id": SESSION_ID},
        {"info_id": 200014, "value": 79 / 32, "session_id": SESSION_ID},
    ]
    if nonfinite_q5_wire:
        root_row = next(
            row
            for row in snapshot["action_result_rows"]
            if row["action_id"] == 100107
        )
        ui_row = next(
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] == "100107"
        )
        root_row["result"] = float("nan")
        ui_row["result"] = "nan"
    return snapshot


def _assert_phase1_authorities(evidence: engine.RefEvidence) -> None:
    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (79,)
    assert evidence.total_grid_target == 79.0


def test_lane_b_nonfinite_wire_is_direct_safe_and_keeps_phase1_authorities() -> None:
    snapshot = _snapshot_with_phase1_authorities(nonfinite_q5_wire=True)

    direct_evidence = engine.extract_evidence(copy.deepcopy(snapshot))
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    prepared_evidence = engine.extract_evidence(prepared)
    direct_result = engine.run_reference_engine(
        copy.deepcopy(snapshot), max_combos=8_000
    ).as_dict()
    prepared_result = engine.run_reference_engine(
        prepared, max_combos=8_000
    ).as_dict()

    _assert_phase1_authorities(direct_evidence)
    _assert_phase1_authorities(prepared_evidence)
    assert direct_result == prepared_result
    assert direct_result["total_grid_range"] == [79, 79, 79]
    assert direct_result["q5_total_cells_value_summary"] == {}
    assert any(
        note == f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:79"
        for note in direct_result["notes"]
    )


def test_lane_b_valid_wire_still_reaches_its_conditional_summary() -> None:
    result = engine.run_reference_engine(
        _target_snapshot(), max_combos=8_000
    ).as_dict()

    assert result["q5_total_cells_value_summary"]["status"] == (
        "conditional_complete"
    )
    assert result["q5_total_cells_value_summary"]["total_count"] == 32
    assert result["q5_total_cells_value_summary"]["residual_count"] == 9
