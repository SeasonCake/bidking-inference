"""Ahmed exact red-average must remain a hard aggregate footprint contract.

Real source: data (7), 5609:1770351226218593 R4. The game supplied total_count=39 and
q6 avg_cells=1.4; the posterior pinned q6=5, so q6 cells are exactly seven. H7's
display-only giant-red hedge used to publish a contradictory 7/7/31 red range even
though the joint candidate and probability summary were 5 items / 7 cells at 100%.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for sub in ("hero_ref/src", "hero_ref/tools"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

from ahmad_live_panel_server import _red_display_ranges  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    COMPLETE_GT5_JOINT_SUPPORT_DISABLE,
    run_reference_engine,
)
from ahmad_tk_overlay import _remaining_cells_range  # noqa: E402


ROLLBACK = "BIDKING_DISABLE_AHMED_Q6_AVG_RED_TAIL_DISPLAY_GUARD"
HEDGE_NOTE_PREFIX = "red_cells_tail_hedge_hi:"
GUARD_NOTE = "ahmed_q6_avg_red_tail_display_guard"


@pytest.fixture(autouse=True)
def _historical_red_tail_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this hotfix1 display test on its named pre-complete-support wire."""
    monkeypatch.setenv(COMPLETE_GT5_JOINT_SUPPORT_DISABLE, "1")


def _5609_r4_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 5609,
                "phase": "bidding",
                "round": 4,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 39,
            "avg_cells": {
                "q1": 2.222222328186035,
                "q3": 3.75,
                "q4": 4.125,
                "q5": 5.44444465637207,
                "q6": 1.399999976158142,
            },
            "counts": {"q3": 8},
        },
    }


def _5602_without_red_avg_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 5602,
                "phase": "bidding",
                "round": 4,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 36,
            "fixed_counts": {"q1": 12, "q3": 9, "q4": 8, "q5": 2, "q6": 5},
        },
    }


def _red_avg_with_unlocked_count_snapshot() -> dict:
    actions = [{"action_id": 100114, "result": 4.5, "tool": "珍品均格"}]
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2401,
                "phase": "bidding",
                "round": 4,
            },
            "actions": {"results": actions},
        },
        "action_result_rows": actions,
        "structured_ref_inputs": {
            "total_count": 40,
            "min_counts": {"q3": 1},
        },
    }


def _run(monkeypatch, *, rollback: bool) -> dict:
    if rollback:
        monkeypatch.setenv(ROLLBACK, "1")
    else:
        monkeypatch.delenv(ROLLBACK, raising=False)
    return run_reference_engine(_5609_r4_snapshot(), max_combos=8000).as_dict()


def test_5609_exact_red_avg_blocks_impossible_display_tail(monkeypatch) -> None:
    rollback = _run(monkeypatch, rollback=True)
    current = _run(monkeypatch, rollback=False)

    assert rollback["quality_count_ranges"]["q6"] == current["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert rollback["quality_cells_ranges"]["q6"] == current["quality_cells_ranges"]["q6"] == [7, 7, 7]
    assert rollback["red_cells_range"] == [7, 7, 31]
    assert rollback["total_grid_range"] == [139, 139, 163]
    assert current["red_cells_range"] == [7, 7, 7]
    assert current["total_grid_range"] == [139, 139, 139]

    panel_counts, panel_cells = _red_display_ranges(current)
    assert panel_counts == [5, 5, 5]
    assert panel_cells == [7, 7, 7]
    assert _remaining_cells_range(current) == (7, 7, 7)
    assert GUARD_NOTE in current["notes"]
    assert not any(str(note).startswith(HEDGE_NOTE_PREFIX) for note in current["notes"])
    assert any(str(note).startswith(HEDGE_NOTE_PREFIX) for note in rollback["notes"])


def test_5609_guard_is_quote_candidate_and_posterior_noop(monkeypatch) -> None:
    rollback = _run(monkeypatch, rollback=True)
    current = _run(monkeypatch, rollback=False)

    for field in (
        "status",
        "conservative",
        "balanced",
        "aggressive",
        "combo_count",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "red_count_range",
        "red_value_range",
    ):
        assert current[field] == rollback[field], field
    assert [
        current["conservative"],
        current["balanced"],
        current["aggressive"],
    ] == [1174000, 1251222, 1328443]


def test_no_red_average_keeps_existing_giant_red_display_hedge(monkeypatch) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = run_reference_engine(
        _5602_without_red_avg_snapshot(),
        max_combos=8000,
    ).as_dict()

    assert GUARD_NOTE not in result["notes"]
    assert any(str(note).startswith(HEDGE_NOTE_PREFIX) for note in result["notes"])
    assert result["red_cells_range"][2] > result["quality_cells_ranges"]["q6"][2]


def test_red_average_with_unlocked_count_keeps_existing_tail_coverage(monkeypatch) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = run_reference_engine(
        _red_avg_with_unlocked_count_snapshot(),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q6"] == [2, 2, 4]
    assert result["quality_cells_ranges"]["q6"] == [9, 9, 18]
    assert GUARD_NOTE not in result["notes"]
    assert any(str(note).startswith(HEDGE_NOTE_PREFIX) for note in result["notes"])
    assert result["red_cells_range"] == [9, 9, 32]


def test_other_heroes_are_byte_identical_across_rollback(monkeypatch) -> None:
    for hero in ("aisha", "ethan", "maria", "gabriela", "ivan", "carlos"):
        snapshot = copy.deepcopy(_5609_r4_snapshot())
        snapshot["ui_contract"]["context"]["hero"] = hero

        monkeypatch.delenv(ROLLBACK, raising=False)
        current = run_reference_engine(snapshot, max_combos=8000).as_dict()
        monkeypatch.setenv(ROLLBACK, "1")
        rollback = run_reference_engine(snapshot, max_combos=8000).as_dict()

        assert current == rollback, hero
