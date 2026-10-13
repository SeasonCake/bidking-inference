from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    TOTAL_GRID_SOFT_FORCED_RED_TAIL_NOTE,
    UPDATE606_CHANGED_ROUTE_NOTE_PREFIX,
    UPDATE606_RUNTIME_PACKAGE_GENERATION,
    _hard_total_grid_target_int,
    extract_evidence,
    run_reference_engine,
)


SOFT_GRID_KILL = "AISHA_DISABLE_SOFT_Q5_FORCED_RED_GRID"


def _snapshot(*, total_cells: int | None = None) -> dict:
    structured = {
        "total_count": 43,
        "fixed_counts": {"q1": 14, "q3": 12, "q4": 9},
        "quality_cells": {"q1": 24, "q3": 28, "q4": 22},
        "avg_cells": {
            "q1": 24 / 14,
            "q3": 28 / 12,
            "q4": 2.444444417953491,
            "q5": 4.400000095367432,
        },
    }
    if total_cells is not None:
        structured["total_cells"] = total_cells
    return {
        "hero": "aisha",
        "map_id": 5604,
        "phase": "bidding",
        "round": 4,
        "structured_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 5604,
                "phase": "bidding",
                "round": 4,
            }
        },
    }


def _run(*, total_cells: int | None = None) -> dict:
    return run_reference_engine(_snapshot(total_cells=total_cells), max_combos=60_000).as_dict()


def _assert_update606_5604_no_quote(result: dict) -> None:
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert result["quality_count_ranges"] == {}
    assert result["quality_cells_ranges"] == {}
    assert result["total_grid_range"] == [None, None, None]
    assert result["notes"] == [
        f"{UPDATE606_CHANGED_ROUTE_NOTE_PREFIX}"
        f":{UPDATE606_RUNTIME_PACKAGE_GENERATION}"
        ":5604:auction_rounds_rate_changed:no_approved_distribution"
    ]


def test_5604_unique_gold_multiple_keeps_unmeasured_red_tail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """q5 22/5 fixes five golds, but it does not measure the forced three reds' cells."""
    monkeypatch.delenv(SOFT_GRID_KILL, raising=False)
    evidence = extract_evidence(_snapshot())
    result = _run()

    assert evidence.total_grid_target == 104
    assert TOTAL_GRID_SOFT_FORCED_RED_TAIL_NOTE in evidence.source_notes
    assert _hard_total_grid_target_int(evidence) is None
    _assert_update606_5604_no_quote(result)

    # The fix changes confidence/display only; the pricing posterior remains byte-identical.
    monkeypatch.setenv(SOFT_GRID_KILL, "1")
    rollback = _run()
    assert rollback == result


def test_5604_ui_withholds_false_red_100_until_total_cells_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SOFT_GRID_KILL, raising=False)
    soft = _run()

    _assert_update606_5604_no_quote(soft)
    assert panel._quality_probability_summary(soft) == "-"
    assert "红品" not in panel._quality_probability_hover(soft)

    hard = _run(total_cells=106)
    _assert_update606_5604_no_quote(hard)
    assert panel._quality_probability_summary(hard) == "-"
    assert "红品" not in panel._quality_probability_hover(hard)


def test_route_stable_2504_keeps_existing_quote_path() -> None:
    snapshot = _snapshot()
    snapshot["map_id"] = 2504
    snapshot["ui_contract"]["context"]["map_id"] = 2504
    result = run_reference_engine(snapshot, max_combos=60_000).as_dict()

    assert result["status"] != "no_reachable_combo"
    assert result["balanced"] is not None
    assert not any(
        note.startswith(UPDATE606_CHANGED_ROUTE_NOTE_PREFIX)
        for note in result["notes"]
    )
