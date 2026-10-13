from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import extract_evidence, run_reference_engine  # noqa: E402


def _snapshot(*, structured_ref_inputs: dict, action_results: list[dict]) -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 5610,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {"public_info": {}},
            "actions": {"results": action_results},
        },
        "structured_ref_inputs": structured_ref_inputs,
        "public_info_rows": [],
    }


def test_low_quality_actions_keep_green_and_white_green_scopes_distinct() -> None:
    evidence = extract_evidence(
        _snapshot(
            structured_ref_inputs={"total_count": 38},
            action_results=[
                {"action_id": 100104, "result": 8},
                {"action_id": 100110, "result": 2.0},
                {"action_id": 100116, "result": 4},
                {"action_id": 100122, "result": 1200},
            ],
        )
    )

    assert evidence.split_counts == {"green": 4, "white": 0}
    assert evidence.split_quality_cells == {}
    assert evidence.avg_cells["q1"] == 2.0
    assert evidence.fixed_counts["q1"] == 4
    assert evidence.quality_cells["q1"] == 8.0
    assert "q1" not in evidence.quality_values
    assert evidence.quality_value_floors["q1"] == 1200.0


def test_5610_legacy_monitor_green_count_with_merged_average_is_reachable() -> None:
    result = run_reference_engine(
        _snapshot(
            structured_ref_inputs={
                "source": "monitor_pre_settlement_field_updates",
                "total_count": 38,
                "counts": {"q1": 13, "q3": 8, "q5": 3},
                "avg_cells": {
                    "q1": 1.75,
                    "q4": 3.125,
                    "q5": 3.6666667461395264,
                },
                "field_updates": [
                    {"path": ["bucket", "1", "count"], "value": 13},
                    {"path": ["bucket", "1", "avg_cells"], "value": 1.75},
                ],
            },
            action_results=[
                {"action_id": 100110, "result": 1.75},
                {"action_id": 100112, "result": 3.125},
                {"action_id": 100113, "result": 3.6666667461395264},
                {"action_id": 100115, "result": 38},
                {"action_id": 100116, "result": 13},
                {"action_id": 100117, "result": 8},
                {"action_id": 100119, "result": 3},
            ],
        ),
        max_combos=60_000,
    ).as_dict()

    evidence = result["evidence"]
    assert result["status"] == "ok"
    assert result["combo_count"] == 1
    assert evidence["split_counts"] == {"green": 13}
    assert "q1" not in evidence["fixed_counts"]
    assert result["quality_count_ranges"] == {
        "q1": [16, 16, 16],
        "q3": [8, 8, 8],
        "q4": [8, 8, 8],
        "q5": [3, 3, 3],
        "q6": [3, 3, 3],
    }
    assert result["quality_cells_ranges"]["q1"] == [28, 28, 28]
    assert result["quality_cells_ranges"]["q4"] == [25, 25, 25]
    assert result["quality_cells_ranges"]["q5"] == [11, 11, 11]
    assert [result["conservative"], result["balanced"], result["aggressive"]] == [
        511229,
        563884,
        616539,
    ]
    assert "action_100116_green_count" in result["notes"]
    assert "action_100116_q1_count" not in result["notes"]
