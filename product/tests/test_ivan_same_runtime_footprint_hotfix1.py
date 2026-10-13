from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "scripts", "hero_ref/src", "hero_ref/tools"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

from ahmad_live_panel_server import _hero_single_pass_ref_result  # noqa: E402
from ahmad_ref_engine import extract_evidence  # noqa: E402


def _snapshot(
    hero: str,
    hero_id: int,
    skill_rows: list[dict],
    *,
    phase: str = "bidding",
) -> dict:
    return {
        "hero": hero,
        "phase": phase,
        "map_id": 2406,
        "skill_reveals": [
            {"hero_id": hero_id, **row}
            for row in skill_rows
        ],
        "ui_contract": {
            "context": {
                "hero": hero,
                "hero_id": hero_id,
                "phase": phase,
                "map_id": 2406,
            },
            "actions": {"results": []},
        },
    }


def _outline(runtime_id: int, shape_code: int) -> dict:
    return {
        "runtime_id": runtime_id,
        "local_index": runtime_id % 100,
        "shape_code": shape_code,
    }


def _quality(runtime_id: int, quality: int, **extra: int) -> dict:
    return {
        "runtime_id": runtime_id,
        "local_index": runtime_id % 100,
        "quality": quality,
        **extra,
    }


def test_ivan_same_runtime_reveal_recovers_exact_outline_cells_only() -> None:
    evidence = extract_evidence(
        _snapshot(
            "ivan",
            205,
            [
                {
                    "skill_id": 100205,
                    "round_index": 0,
                    "observed_items": [_outline(101, 42), _outline(102, 23)],
                },
                {
                    "skill_id": 1002051,
                    "round_index": 1,
                    "observed_items": [_quality(201, 5)],
                },
                {
                    "skill_id": 1002052,
                    "round_index": 2,
                    "observed_items": [_quality(101, 4)],
                },
            ],
        )
    )

    assert evidence.min_counts["q5"] == 1
    assert evidence.quality_cell_floors.get("q5") is None
    assert evidence.quality_cell_floors["q4"] == 8
    assert evidence.quality_cell_floor_item_counts["q4"] == 1
    assert (
        "hero_skill_footprint:skill_1002052_ivan_same_runtime_cells_8"
        in evidence.source_notes
    )
    assert evidence.quality_value_floors.get("q4") is None


def test_ivan_different_runtime_does_not_infer_max_unknown_floor() -> None:
    evidence = extract_evidence(
        _snapshot(
            "ivan",
            205,
            [
                {
                    "skill_id": 100205,
                    "round_index": 0,
                    "observed_items": [_outline(101, 42), _outline(102, 23)],
                },
                {
                    "skill_id": 1002051,
                    "round_index": 1,
                    "observed_items": [_quality(201, 5)],
                },
            ],
        )
    )

    assert evidence.min_counts["q5"] == 1
    assert evidence.quality_cell_floors.get("q5") is None
    assert not any(
        note.startswith("hero_skill_footprint:") for note in evidence.source_notes
    )


def test_ivan_direct_cells_are_stronger_and_not_rewritten() -> None:
    evidence = extract_evidence(
        _snapshot(
            "ivan",
            205,
            [
                {
                    "skill_id": 100205,
                    "round_index": 0,
                    "observed_items": [_outline(101, 42)],
                },
                {
                    "skill_id": 1002051,
                    "round_index": 1,
                    "observed_items": [_quality(201, 5, cells=9)],
                },
            ],
        )
    )

    assert evidence.quality_cell_floors["q5"] == 9
    assert not any(
        note.startswith("hero_skill_footprint:") for note in evidence.source_notes
    )


def test_ivan_conflicting_outline_for_same_runtime_fails_closed() -> None:
    evidence = extract_evidence(
        _snapshot(
            "ivan",
            205,
            [
                {
                    "skill_id": 100205,
                    "round_index": 0,
                    "observed_items": [_outline(101, 42), _outline(101, 23)],
                },
                {
                    "skill_id": 1002051,
                    "round_index": 1,
                    "observed_items": [_quality(101, 5)],
                },
            ],
        )
    )

    assert evidence.quality_cell_floors.get("q5") is None


def test_ivan_same_area_different_outline_shapes_fail_closed() -> None:
    evidence = extract_evidence(
        _snapshot(
            "ivan",
            205,
            [
                {
                    "skill_id": 100205,
                    "round_index": 0,
                    "observed_items": [_outline(101, 41), _outline(101, 22)],
                },
                {
                    "skill_id": 1002052,
                    "round_index": 2,
                    "observed_items": [_quality(101, 4)],
                },
            ],
        )
    )

    assert evidence.min_counts["q4"] == 1
    assert evidence.quality_cell_floors.get("q4") is None
    assert not any(
        note.startswith("hero_skill_footprint:") for note in evidence.source_notes
    )


def test_ivan_rollback_restores_legacy_quality_only_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(
        "ivan",
        205,
        [
            {
                "skill_id": 100205,
                "round_index": 0,
                "observed_items": [_outline(101, 42)],
            },
            {
                "skill_id": 1002052,
                "round_index": 2,
                "observed_items": [_quality(101, 4)],
            },
        ],
    )
    assert extract_evidence(snapshot).quality_cell_floors["q4"] == 8

    monkeypatch.setenv("BIDKING_DISABLE_IVAN_SAME_RUNTIME_FOOTPRINT", "1")
    rollback = extract_evidence(snapshot)
    assert rollback.min_counts["q4"] == 1
    assert rollback.quality_cell_floors.get("q4") is None
    assert not any(
        note.startswith("hero_skill_footprint:") for note in rollback.source_notes
    )


def test_ivan_product_single_pass_consumes_exact_fact_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(
        "ivan",
        205,
        [
            {
                "skill_id": 100205,
                "round_index": 0,
                "observed_items": [_outline(101, 42), _outline(102, 23)],
            },
            {
                "skill_id": 1002051,
                "round_index": 1,
                "observed_items": [_quality(201, 5)],
            },
            {
                "skill_id": 1002052,
                "round_index": 2,
                "observed_items": [_quality(101, 4)],
            },
        ],
    )
    snapshot["round"] = 3
    snapshot["ui_contract"]["context"]["round"] = 3

    default = _hero_single_pass_ref_result(
        snapshot,
        hero_key="ivan",
        round_no=3,
        public_info={},
    )
    assert default["status"] == "count_prior"
    assert default["evidence"]["quality_cell_floors"]["q4"] == 8
    assert (
        "hero_skill_footprint:skill_1002052_ivan_same_runtime_cells_8"
        in default["notes"]
    )

    monkeypatch.setenv("BIDKING_DISABLE_IVAN_SAME_RUNTIME_FOOTPRINT", "1")
    rollback = _hero_single_pass_ref_result(
        snapshot,
        hero_key="ivan",
        round_no=3,
        public_info={},
    )
    assert rollback["evidence"]["quality_cell_floors"].get("q4") is None
    assert not any(
        note.startswith("hero_skill_footprint:") for note in rollback["notes"]
    )
    assert all(
        isinstance(default[key], int) and default[key] >= 0
        for key in ("conservative", "balanced", "aggressive")
    )


@pytest.mark.parametrize(
    ("hero", "hero_id"),
    [
        ("aisha", 103),
        ("gabriela", 104),
        ("ethan", 208),
        ("maria", 108),
        ("carlos", 202),
    ],
)
def test_non_ivan_heroes_do_not_consume_injected_ivan_rows(
    hero: str,
    hero_id: int,
) -> None:
    snapshot = _snapshot(
        hero,
        hero_id,
        [
            {
                "skill_id": 100205,
                "hero_id": 205,
                "round_index": 0,
                "observed_items": [_outline(101, 42)],
            },
            {
                "skill_id": 1002052,
                "hero_id": 205,
                "round_index": 2,
                "observed_items": [_quality(101, 4)],
            },
        ],
    )
    evidence = extract_evidence(snapshot)

    assert evidence.hero == hero
    assert evidence.quality_cell_floors.get("q4") is None
    assert not any(
        note.startswith("hero_skill_footprint:") for note in evidence.source_notes
    )


def test_settlement_truth_does_not_apply_bidding_adapter() -> None:
    snapshot = _snapshot(
        "ivan",
        205,
        [
            {
                "skill_id": 100205,
                "round_index": 0,
                "observed_items": [_outline(101, 42)],
            },
            {
                "skill_id": 1002052,
                "round_index": 2,
                "observed_items": [_quality(101, 4)],
            },
        ],
        phase="settled",
    )
    snapshot.update(
        {
            "final_quality_counts": "q2=9;q3=11;q4=5;q5=3;q6=3",
            "final_quality_cells": "q2=14;q3=26;q4=19;q5=13;q6=10",
        }
    )
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "total_items": 31,
        "total_cells": 82,
        "total_value": 682_411,
    }

    evidence = extract_evidence(snapshot)
    assert evidence.fixed_counts == {
        "q1": 9,
        "q3": 11,
        "q4": 5,
        "q5": 3,
        "q6": 3,
    }
    assert evidence.quality_cell_floors == {}
    assert not any(
        note.startswith("hero_skill_footprint:") for note in evidence.source_notes
    )
