"""Fail-closed quarantine for the locked Chenmei map discovered on 2026-08-13.

The installed live table identifies map 5506 as 珍海拾遗, but no opened-map
bidding raw exists yet.  These tests intentionally prove only the negative
publication contract; they do not model the future activity.
"""

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = ROOT / "hero_ref" / "src"
TOOLS = ROOT / "hero_ref" / "tools"
for _path in (ENGINE_SRC, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import (  # noqa: E402
    AUCTION_HALL_6001_PROVISIONAL_NOTE_PREFIX,
    CHENMEI_ZHENHAI_PENDING_NOTE_PREFIX,
    UPDATE606_LARGE_AUCTION_PENDING_NOTE_PREFIX,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402


def _snapshot(
    *,
    root_map=5506,
    context_map=5506,
    root_hero="chenmei",
    context_hero="chenmei",
):
    root = {
        "hero": root_hero,
        "map_id": root_map,
        "phase": "bidding",
        "structured_ref_inputs": {"total_count": 34},
        "public_info_rows": [],
    }
    if context_map is not None or context_hero is not None:
        context = {}
        if context_map is not None:
            context["map_id"] = context_map
        if context_hero is not None:
            context["hero"] = context_hero
        root["ui_contract"] = {"context": context}
    return root


def _assert_no_quote(result, state, *, prefix=CHENMEI_ZHENHAI_PENDING_NOTE_PREFIX):
    assert result.status == "no_reachable_combo"
    assert result.combo_count == 0
    assert (result.conservative, result.balanced, result.aggressive) == (
        None,
        None,
        None,
    )
    assert result.quality_count_ranges == {}
    assert result.quality_cells_ranges == {}
    assert result.quality_count_candidates == {}
    assert result.quality_cells_candidates == {}
    assert result.quality_count_cells_candidates == {}
    assert result.quality_lock == {}
    assert result.total_grid_range == (None, None, None)
    assert result.notes == (f"{prefix}:{state}",)
    wire = result.as_dict()
    assert wire["conservative"] is wire["balanced"] is wire["aggressive"] is None
    assert wire["combo_count"] == 0
    assert wire["quality_count_candidates"] == {}
    assert wire["quality_lock"] == {}


@pytest.mark.parametrize("max_combos", (8000, 50000))
def test_locked_5506_direct_and_prepared_publish_no_quote(max_combos):
    snapshot = _snapshot()
    _assert_no_quote(run_reference_engine(snapshot, max_combos=max_combos), "preopen")
    _assert_no_quote(
        run_reference_engine(
            prepare_reference_engine_snapshot(snapshot), max_combos=max_combos
        ),
        "preopen",
    )


@pytest.mark.parametrize(
    ("root_map", "context_map", "state"),
    (
        ("5506", 5506, "preopen"),
        (5506, 5507, "map_conflict"),
        (5507, 5506, "map_conflict"),
        (5506, True, "map_conflict"),
    ),
)
def test_5506_raw_map_owner_conflict_stays_quarantined(
    root_map, context_map, state
):
    snapshot = _snapshot(root_map=root_map, context_map=context_map)
    _assert_no_quote(run_reference_engine(snapshot), state)
    _assert_no_quote(run_reference_engine(prepare_reference_engine_snapshot(snapshot)), state)


@pytest.mark.parametrize(
    ("root_hero", "context_hero", "state"),
    (
        ("陈美", "chenmei", "preopen"),
        ("ahmed", "chenmei", "hero_conflict"),
        ("chenmei", "aisha", "hero_conflict"),
        (None, "chenmei", "hero_conflict"),
    ),
)
def test_5506_missing_or_wrong_hero_cannot_publish(root_hero, context_hero, state):
    snapshot = _snapshot(root_hero=root_hero, context_hero=context_hero)
    _assert_no_quote(run_reference_engine(snapshot), state)
    _assert_no_quote(run_reference_engine(prepare_reference_engine_snapshot(snapshot)), state)


def test_only_5506_is_quarantined():
    ordinary = run_reference_engine(_snapshot(root_map=5507, context_map=5507))
    assert ordinary.status != "no_reachable_combo"
    assert ordinary.balanced is not None
    assert not any(
        note.startswith(CHENMEI_ZHENHAI_PENDING_NOTE_PREFIX)
        for note in ordinary.notes
    )


def test_5506_summary_consumer_holds_all_three_quotes() -> None:
    summary = summarize_snapshot(
        _snapshot(), snapshot_path=Path("chenmei_5506_pending_probe.json")
    )
    reference = summary["reference"]
    assert reference["readiness"] == "no_reachable_combo"
    assert reference["source"] == "ref_waiting"
    assert (
        reference["conservative"],
        reference["balanced"],
        reference["aggressive"],
    ) == ("-", "-", "-")
    assert "ahmad_ref" not in summary


@pytest.mark.parametrize("max_combos", (8000, 50000))
def test_update606_6001_direct_and_prepared_publish_no_quote(max_combos):
    snapshot = _snapshot(
        root_map=6001,
        context_map=6001,
        root_hero="ahmed",
        context_hero="ahmed",
    )
    for candidate in (snapshot, prepare_reference_engine_snapshot(snapshot)):
        _assert_no_quote(
            run_reference_engine(candidate, max_combos=max_combos),
            "receipt_missing_or_invalid",
            prefix=AUCTION_HALL_6001_PROVISIONAL_NOTE_PREFIX,
        )


@pytest.mark.parametrize(
    ("root_map", "context_map", "state", "prefix"),
    (
        (
            "6001",
            6001,
            "receipt_missing_or_invalid",
            AUCTION_HALL_6001_PROVISIONAL_NOTE_PREFIX,
        ),
        (6001, 6002, "map_conflict", UPDATE606_LARGE_AUCTION_PENDING_NOTE_PREFIX),
        (6002, 6001, "map_conflict", UPDATE606_LARGE_AUCTION_PENDING_NOTE_PREFIX),
        (6001, True, "map_conflict", UPDATE606_LARGE_AUCTION_PENDING_NOTE_PREFIX),
    ),
)
def test_update606_6001_raw_owner_conflict_stays_no_quote(
    root_map, context_map, state, prefix
):
    snapshot = _snapshot(
        root_map=root_map,
        context_map=context_map,
        root_hero="ahmed",
        context_hero="ahmed",
    )
    _assert_no_quote(
        run_reference_engine(snapshot),
        state,
        prefix=prefix,
    )


def test_update606_6001_summary_consumer_holds_all_three_quotes() -> None:
    snapshot = _snapshot(
        root_map=6001,
        context_map=6001,
        root_hero="ahmed",
        context_hero="ahmed",
    )
    summary = summarize_snapshot(
        snapshot, snapshot_path=Path("update606_6001_pending_probe.json")
    )
    reference = summary["reference"]
    assert reference["readiness"] == "no_reachable_combo"
    assert reference["source"] == "ref_waiting"
    assert (
        reference["conservative"],
        reference["balanced"],
        reference["aggressive"],
    ) == ("-", "-", "-")
    assert "ahmad_ref" not in summary


def test_update606_6002_remains_outside_large_auction_pending_gate():
    ordinary = run_reference_engine(
        _snapshot(
            root_map=6002,
            context_map=6002,
            root_hero="ahmed",
            context_hero="ahmed",
        )
    )
    assert not any(
        note.startswith(UPDATE606_LARGE_AUCTION_PENDING_NOTE_PREFIX)
        for note in ordinary.notes
    )
