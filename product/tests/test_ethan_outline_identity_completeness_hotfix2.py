from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansObservedItem,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    live_batches_from_fatbeans_events,
)


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import extract_evidence  # noqa: E402


def _engine_evidence(
    *,
    skill_reveals: list[dict],
    action_results: list[dict] | None = None,
):
    return extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "ethan",
                    "hero_id": 208,
                    "map_id": 2505,
                    "phase": "bidding",
                },
                "constraints": {"public_info": {}},
            },
            "structured_ref_inputs": {},
            "skill_reveals": skill_reveals,
            "action_result_rows": action_results or [],
        }
    )


def _engine_item(runtime_id: int | None, cells: int | None, *, quality: int = 4) -> dict:
    return {
        "runtime_id": runtime_id,
        "quality": quality,
        "cells": cells,
    }


def _observed_item(
    runtime_id: int | None,
    cells: int | None,
    *,
    quality: int | None = 4,
) -> FatbeansObservedItem:
    return FatbeansObservedItem(
        local_index=None,
        runtime_id=runtime_id,
        item_id=None,
        quality=quality,
        value=None,
        shape_code=None,
        cells=cells,
    )


def _live_updates(
    *,
    skill_reveals: tuple[FatbeansSkillReveal, ...] = (),
    action_results: tuple[FatbeansActionResult, ...] = (),
) -> dict[tuple[str, ...], object]:
    state = FatbeansStateEvent(
        sort_id=10,
        capture_time="2026-08-03 01:00:00.000",
        message_id=0x0021,
        session_id="2505:1",
        map_id=2505,
        round_index=5,
        skill_reveals=skill_reveals,
        action_results=action_results,
    )
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(state,),
        )
    )
    return {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }


def _assert_no_live_exact_totals(updates: dict[tuple[str, ...], object]) -> None:
    assert ("session", "warehouse_total_cells") not in updates
    assert ("session", "total_item_count") not in updates


def test_engine_complete_r5_outline_sets_atomic_exact_totals() -> None:
    evidence = _engine_evidence(
        skill_reveals=[
            {
                "skill_id": 1002085,
                "hero_id": 208,
                "observed_items": [
                    _engine_item(101, 4),
                    _engine_item(102, 9),
                ],
            }
        ]
    )

    assert evidence.total_count == 2
    assert evidence.total_grid_target == 13.0
    assert "ethan_skill_full_outline_count" in evidence.source_notes
    assert "ethan_skill_full_outline_cells" in evidence.source_notes


@pytest.mark.parametrize(
    "items",
    [
        [_engine_item(101, 4), _engine_item(102, None)],
        [_engine_item(101, 4), _engine_item(101, 9)],
        [_engine_item(101, 9), _engine_item(101, 4)],
    ],
)
def test_engine_incomplete_or_duplicate_r5_outline_never_sets_exact_totals(
    items: list[dict],
) -> None:
    evidence = _engine_evidence(
        skill_reveals=[
            {
                "skill_id": 1002085,
                "hero_id": 208,
                "observed_items": items,
            }
        ]
    )

    assert evidence.total_count is None
    assert "ethan_skill_full_outline_count" not in evidence.source_notes
    assert "ethan_skill_full_outline_cells" not in evidence.source_notes
    assert "ethan_full_outline_identity_conflict" in evidence.source_notes


def test_engine_conflicting_complete_r5_rows_are_order_invariant_fail_closed() -> None:
    rows = [
        {
            "skill_id": 1002085,
            "hero_id": 208,
            "observed_items": [_engine_item(101, 4), _engine_item(102, 9)],
        },
        {
            "skill_id": 1002085,
            "hero_id": 208,
            "observed_items": [_engine_item(101, 4), _engine_item(102, 10)],
        },
    ]

    for ordered_rows in (rows, list(reversed(rows))):
        evidence = _engine_evidence(skill_reveals=ordered_rows)
        assert evidence.total_count is None
        assert "ethan_skill_full_outline_cells" not in evidence.source_notes
        assert "ethan_full_outline_identity_conflict" in evidence.source_notes


def test_engine_complete_mirror_quality_and_outline_join_sets_exact_totals() -> None:
    evidence = _engine_evidence(
        action_results=[
            {
                "action_id": 100134,
                "observed_items": [
                    _engine_item(101, 4, quality=3),
                    _engine_item(102, 9, quality=5),
                ],
            }
        ],
        skill_reveals=[
            {
                "skill_id": 1002082,
                "hero_id": 208,
                "observed_items": [
                    _engine_item(101, 4),
                    _engine_item(102, 9),
                ],
            }
        ],
    )

    assert evidence.total_count == 2
    assert evidence.total_grid_target == 13.0


@pytest.mark.parametrize(
    ("quality_items", "outline_items"),
    [
        (
            [_engine_item(101, 4, quality=3)],
            [_engine_item(101, 4), _engine_item(102, 9)],
        ),
        (
            [_engine_item(101, 4, quality=3), _engine_item(101, 4, quality=5)],
            [_engine_item(101, 4)],
        ),
        (
            [_engine_item(101, 4, quality=3), _engine_item(102, 9, quality=5)],
            [_engine_item(101, 4), _engine_item(102, None)],
        ),
    ],
)
def test_engine_partial_or_duplicate_mirror_join_is_diagnostic_only(
    quality_items: list[dict],
    outline_items: list[dict],
) -> None:
    evidence = _engine_evidence(
        action_results=[{"action_id": 100134, "observed_items": quality_items}],
        skill_reveals=[
            {
                "skill_id": 1002082,
                "hero_id": 208,
                "observed_items": outline_items,
            }
        ],
    )

    assert evidence.total_count is None
    assert "ethan_skill_full_outline_count" not in evidence.source_notes


def test_fatbeans_complete_r5_outline_projects_exact_totals() -> None:
    updates = _live_updates(
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002085,
                hero_id=208,
                round_index=5,
                observed_items=(
                    _observed_item(101, 4),
                    _observed_item(102, 9),
                ),
            ),
        )
    )

    assert updates[("session", "total_item_count")] == 2
    assert updates[("session", "warehouse_total_cells")] == 13


@pytest.mark.parametrize(
    "items",
    [
        (_observed_item(101, 4), _observed_item(102, None)),
        (_observed_item(101, 4), _observed_item(101, 9)),
        (_observed_item(101, 9), _observed_item(101, 4)),
    ],
)
def test_fatbeans_incomplete_or_duplicate_r5_outline_never_projects_exact_totals(
    items: tuple[FatbeansObservedItem, ...],
) -> None:
    updates = _live_updates(
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002085,
                hero_id=208,
                round_index=5,
                observed_items=items,
            ),
        )
    )

    _assert_no_live_exact_totals(updates)


def test_fatbeans_conflicting_complete_r5_rows_are_order_invariant_fail_closed() -> None:
    reveals = (
        FatbeansSkillReveal(
            skill_id=1002085,
            hero_id=208,
            round_index=5,
            observed_items=(_observed_item(101, 4), _observed_item(102, 9)),
        ),
        FatbeansSkillReveal(
            skill_id=1002085,
            hero_id=208,
            round_index=5,
            observed_items=(_observed_item(101, 4), _observed_item(102, 10)),
        ),
    )

    for ordered_reveals in (reveals, tuple(reversed(reveals))):
        _assert_no_live_exact_totals(_live_updates(skill_reveals=ordered_reveals))


def test_fatbeans_complete_mirror_quality_and_outline_join_projects_exact_totals() -> None:
    updates = _live_updates(
        action_results=(
            FatbeansActionResult(
                action_id=100134,
                result=None,
                result_field=None,
                observed_items=(
                    _observed_item(101, 4, quality=3),
                    _observed_item(102, 9, quality=5),
                ),
            ),
        ),
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002082,
                hero_id=208,
                round_index=2,
                observed_items=(
                    _observed_item(101, 4),
                    _observed_item(102, 9),
                ),
            ),
        ),
    )

    assert updates[("session", "total_item_count")] == 2
    assert updates[("session", "warehouse_total_cells")] == 13


@pytest.mark.parametrize(
    ("quality_items", "outline_items"),
    [
        (
            (_observed_item(101, 4, quality=3),),
            (_observed_item(101, 4), _observed_item(102, 9)),
        ),
        (
            (
                _observed_item(101, 4, quality=3),
                _observed_item(101, 4, quality=5),
            ),
            (_observed_item(101, 4),),
        ),
        (
            (
                _observed_item(101, 4, quality=3),
                _observed_item(102, 9, quality=5),
            ),
            (_observed_item(101, 4), _observed_item(102, None)),
        ),
    ],
)
def test_fatbeans_partial_or_duplicate_mirror_join_never_projects_exact_totals(
    quality_items: tuple[FatbeansObservedItem, ...],
    outline_items: tuple[FatbeansObservedItem, ...],
) -> None:
    updates = _live_updates(
        action_results=(
            FatbeansActionResult(
                action_id=100134,
                result=None,
                result_field=None,
                observed_items=quality_items,
            ),
        ),
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002082,
                hero_id=208,
                round_index=2,
                observed_items=outline_items,
            ),
        ),
    )

    _assert_no_live_exact_totals(updates)
