from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402


SESSION_ID = "2501:9000000000000034"


def _warehouse_source() -> dict:
    return {
        "qualification": "postupdate_unknown_warehouse_source",
        "reason": "postupdate_non_ranked_source_unresolved",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }


def _structured_inputs() -> dict:
    """Pre-settlement wire facts from HeroRefDiag-20260728-053151-2501.

    Settlement later showed q4=6/20/39149, but it is intentionally absent here:
    the bidding decision may consume only the R3 evidence already on the wire.
    """
    return {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "ahmed",
        "total_count": 41,
        "avg_cells": {
            "q1": 1.7692307233810425,
            "q4": 3.3333332538604736,
            "q5": 3.0,
        },
        "counts": {"q3": 12},
        "quality_values": {"q4": 39_149},
    }


def _snapshot() -> dict:
    warehouse_source = _warehouse_source()
    structured = _structured_inputs()
    return {
        "hero": "ahmed",
        "map_id": 2501,
        "model_map_id": 2501,
        "round": 3,
        "action_round": 3,
        "phase": "bidding",
        "session_id": SESSION_ID,
        "warehouse_source": warehouse_source,
        "warehouse_source_qualification": warehouse_source["qualification"],
        "official_distribution_eligible": False,
        "structured_ref_inputs": structured,
        "ahmad_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2501,
                "phase": "bidding",
                "round": 3,
                "warehouse_source": warehouse_source,
                "warehouse_source_qualification": warehouse_source[
                    "qualification"
                ],
                "official_distribution_eligible": False,
            },
            "constraints": {
                "structured_ref_inputs": structured,
                "public_info": {},
            },
        },
    }


def test_real_2501_q4_total_and_avg_preserve_both_physical_states() -> None:
    result = engine.run_reference_engine(_snapshot(), max_combos=8_000).as_dict()

    assert result["quality_count_ranges"]["q4"] == [6, 6, 9]
    assert result["quality_cells_ranges"]["q4"] == [20, 20, 30]
    assert result["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert {
        (row["count"], row["cells"])
        for row in result["evidence"]["quality_count_cells_options"]["q4"]
    } == {(6, 20), (9, 30)}
    assert {
        (row["count"], row["cells"])
        for row in result["quality_count_cells_candidates"]["q4"]
    } == {(6, 20), (9, 30)}
    assert "q4_exact_value_cells_multiset:ok:2" in result["notes"]


def test_real_2501_product_summary_does_not_upgrade_unknown_source_to_exact() -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=Path("reconstructed_2501_bidding.json"),
    )
    result = summary["ahmed_ref"]

    assert summary["reference"]["readiness"] == "live_ready"
    assert result["quality_count_ranges"]["q4"] == [6, 6, 9]
    assert result["quality_cells_ranges"]["q4"] == [20, 20, 30]
    assert result["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert any(flag["label"] == "仓来源待确认" for flag in summary["flags"])


def test_real_2501_exact_solver_rollback_still_never_false_locks_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.Q4_EXACT_VALUE_CELLS_DISABLE, "1")
    result = engine.run_reference_engine(_snapshot(), max_combos=8_000).as_dict()

    assert result["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert result["quality_count_ranges"]["q4"] != [6, 6, 6]
    assert not any(
        str(note).startswith(engine.Q4_EXACT_VALUE_CELLS_NOTE)
        for note in result["notes"]
    )
