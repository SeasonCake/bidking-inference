from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import bidking_lab.inference.v2 as v2
from bidking_lab.inference.observation import (
    INDIANA_DEFAULT_LOADOUT,
    RAVEN_DEFAULT_LOADOUT,
    STANDARD_LOADOUTS,
)
from bidking_lab.live.fatbeans import (
    ETHAN_HERO_ID,
    ETHAN_SKILL_FULL_OUTLINE,
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansObservedItem,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    _complete_quality_reveal_scope_for_skill,
    _full_outline_updates,
    _skill_reveal_category,
    _state_grid_items,
    hero_mode_from_state,
    live_batches_from_fatbeans_events,
)
from bidking_lab.live.monitor import (
    _ahmad_ref_inputs_from_batches,
    _resolve_live_hero,
    _skill_reveal_label,
    _skill_reveal_round_projection,
    _skill_reveal_rows,
)
from bidking_lab.live.state import LiveSessionState, apply_observation_batch
from bidking_lab.live.types import GridItemObservation, LiveObservationBatch


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
import hero_ref_live_schedule as schedule  # noqa: E402


def _item(
    runtime_id: int,
    *,
    local_index: int | None = 0,
    quality: int | None = None,
    shape_code: int | None = None,
    cells: int | None = None,
) -> FatbeansObservedItem:
    return FatbeansObservedItem(
        local_index=local_index,
        runtime_id=runtime_id,
        item_id=None,
        quality=quality,
        value=None,
        shape_code=shape_code,
        cells=cells,
    )


def _state(
    sort_id: int,
    *,
    session_id: str = "5219:indiana",
    hero_id: int = 303,
    skill_id: int = 1003031,
    round_index: int | None = 1,
    items: tuple[FatbeansObservedItem, ...] = (),
    actions: tuple[FatbeansActionResult, ...] = (),
) -> FatbeansStateEvent:
    return FatbeansStateEvent(
        sort_id=sort_id,
        capture_time=f"2026-09-03 13:00:{sort_id:02d}",
        message_id=0x0025,
        session_id=session_id,
        map_id=5219,
        round_index=round_index,
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=skill_id,
                hero_id=hero_id,
                round_index=round_index,
                observed_items=items,
            ),
        ),
        action_results=actions,
    )


def _events(*states: FatbeansStateEvent) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=tuple(states),
    )


def _grid(
    runtime_id: int,
    *,
    cells: int,
    quality: int | None = None,
    shape_key: str | None = None,
    local_index: int | None = 0,
) -> GridItemObservation:
    return GridItemObservation(
        cells=cells,
        source="packet",
        confidence="exact",
        runtime_id=runtime_id,
        quality=quality,
        shape_key=shape_key,
        local_index=local_index,
    )


def _batch(
    sequence: int,
    *items: GridItemObservation,
    session_id: str = "5219:indiana",
) -> LiveObservationBatch:
    return LiveObservationBatch(
        source="packet",
        event_kind="tool_revealed",
        phase="bidding",
        sequence=sequence,
        session_id=session_id,
        grid_items=tuple(items),
    )


def test_indiana_identity_is_positive_and_neighbors_are_unchanged() -> None:
    assert hero_mode_from_state(_state(1, hero_id=303)) == "indiana"
    assert hero_mode_from_state(_state(1, hero_id=302)) == "liss"
    assert hero_mode_from_state(_state(1, hero_id=304)) is None

    assert engine.normalize_hero_key("印第安纳") == "indiana"
    assert engine.HERO_BY_ID[303] == "indiana"
    assert engine.HERO_BY_ID[302] == "liss"
    assert 304 not in engine.HERO_BY_ID
    assert engine.is_supported_ref_hero("indiana") is True
    assert engine.is_supported_ref_hero("unknown-304") is False
    assert STANDARD_LOADOUTS["indiana"] == INDIANA_DEFAULT_LOADOUT
    assert INDIANA_DEFAULT_LOADOUT != RAVEN_DEFAULT_LOADOUT


def test_indiana_opening_outline_is_lower_bound_not_complete_total() -> None:
    events = _events(
        _state(
            1,
            items=(
                _item(101, shape_code=23, cells=6),
                _item(102, local_index=9, shape_code=11, cells=1),
            ),
        )
    )

    batches = live_batches_from_fatbeans_events(events)
    assert len(batches) == 1
    assert any(
        update.path == ("session", "hero") and update.value == "indiana"
        for update in batches[0].field_updates
    )
    assert all(
        update.path
        not in {
            ("session", "total_item_count"),
            ("session", "warehouse_total_cells"),
        }
        for update in batches[0].field_updates
    )
    state = apply_observation_batch(LiveSessionState(), batches[0])
    assert [(item.runtime_id, item.cells, item.quality) for item in state.grid_items] == [
        (101, 6, None),
        (102, 1, None),
    ]
    assert _skill_reveal_category(1003031) is None
    assert _complete_quality_reveal_scope_for_skill(303, 1003031) == ()
    assert _skill_reveal_label(1003031, 303) == "印第安纳·R1 未知箱轮廓"


def test_indiana_user_quality_and_jianying_merge_by_exact_runtime_at_local_zero() -> None:
    outline = _item(201, local_index=0, shape_code=23, cells=6)
    quality_only = _item(201, local_index=5, quality=5)
    category_outline = _item(201, local_index=0, shape_code=23, cells=6)
    state = _state(
        1,
        items=(outline,),
        actions=(
            FatbeansActionResult(100134, None, None, (quality_only,)),
            FatbeansActionResult(100156, None, None, (category_outline,)),
        ),
    )

    rows = _state_grid_items(state)
    assert len(rows) == 1
    assert (
        rows[0].runtime_id,
        rows[0].quality,
        rows[0].shape_key,
        rows[0].cells,
        rows[0].local_index,
        rows[0].categories,
    ) == (201, 5, "23", 6, 0, (106,))

    store = v2.evidence_store_from_fatbeans_events(
        SimpleNamespace(states=(state,)),
        session_id="5219:indiana",
    )
    evidence = store.by_runtime[201]
    assert evidence.quality == 5
    assert evidence.shape_key == "23"
    assert evidence.cells == 6
    assert evidence.categories == (106,)
    assert evidence.excluded_qualities == ()


def test_quality_only_marker_enriches_without_shrinking_real_footprint() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        _batch(1, _grid(301, cells=6, shape_key="23", local_index=0)),
    )
    state = apply_observation_batch(
        state,
        _batch(2, _grid(301, cells=1, quality=5, local_index=7)),
    )

    assert len(state.grid_items) == 1
    item = state.grid_items[0]
    assert (item.quality, item.shape_key, item.cells, item.local_index) == (
        5,
        "23",
        6,
        0,
    )


def test_same_runtime_marker_is_enriched_by_later_real_shape() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        _batch(1, _grid(401, cells=1, quality=6, local_index=20)),
    )
    state = apply_observation_batch(
        state,
        _batch(2, _grid(401, cells=12, shape_key="34", local_index=20)),
    )

    item = state.grid_items[0]
    assert (item.quality, item.shape_key, item.cells, item.local_index) == (
        6,
        "34",
        12,
        20,
    )


def test_two_consistent_but_conflicting_real_shapes_keep_old_and_mark_low_confidence() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        _batch(1, _grid(501, cells=6, shape_key="23", local_index=0)),
    )
    state = apply_observation_batch(
        state,
        _batch(2, _grid(501, cells=12, quality=4, shape_key="34", local_index=30)),
    )

    item = state.grid_items[0]
    assert (item.quality, item.shape_key, item.cells, item.local_index) == (
        4,
        "23",
        6,
        0,
    )
    assert item.confidence == "low"


def test_internally_conflicting_real_shape_cells_are_not_published() -> None:
    invalid_only = _state_grid_items(
        _state(3, items=(_item(502, shape_code=34, cells=6),))
    )
    assert invalid_only == ()


def test_distinct_runtime_ids_remain_distinct() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        _batch(
            1,
            _grid(601, cells=6, shape_key="23"),
            _grid(602, cells=6, shape_key="23", local_index=20),
        ),
    )
    state = apply_observation_batch(
        state,
        _batch(2, _grid(601, cells=1, quality=5)),
    )

    assert [(row.runtime_id, row.quality, row.cells) for row in state.grid_items] == [
        (601, 5, 6),
        (602, None, 6),
    ]


def test_same_parent_skill_keeps_distinct_indiana_events_without_child_synthesis() -> None:
    events = _events(
        _state(10, round_index=None, items=(_item(701, shape_code=23, cells=6),)),
        _state(20, round_index=3, items=(_item(701, quality=5),)),
    )

    rows = _skill_reveal_rows(events, {}, session_id="5219:indiana")
    assert [row["sort"] for row in rows] == [20, 10]
    assert {row["skill_id"] for row in rows} == {1003031}
    assert all(row["hero_id"] == 303 for row in rows)
    assert rows[0]["round_index_state"] == "conflict"
    assert rows[0]["round_index_wire"] == 3
    assert rows[1]["round_index"] == 0
    assert rows[1]["round_index_state"] == "skill_canonical"


def test_proven_indiana_child_rounds_enrich_exact_runtime_footprints() -> None:
    # 2026-09-03 live evidence: R3 may report a different local_index for the
    # same physical runtime.  Runtime identity must retain the R1 footprint.
    events = _events(
        _state(
            10,
            round_index=None,
            items=(
                _item(8549, local_index=84, shape_code=22, cells=4),
                _item(8543, local_index=73, shape_code=11, cells=1),
                _item(8366, local_index=44, shape_code=11, cells=1),
            ),
        ),
        _state(
            20,
            skill_id=1003033,
            round_index=2,
            items=(_item(8549, local_index=95, quality=2),),
        ),
        _state(
            30,
            skill_id=1003034,
            round_index=3,
            items=(_item(8543, local_index=73, quality=1),),
        ),
        _state(
            40,
            skill_id=1003035,
            round_index=4,
            items=(_item(8366, local_index=44, quality=3),),
        ),
    )

    merged = LiveSessionState()
    for batch in live_batches_from_fatbeans_events(events):
        merged = apply_observation_batch(merged, batch)
    by_runtime = {item.runtime_id: item for item in merged.grid_items}

    assert (
        by_runtime[8549].quality,
        by_runtime[8549].shape_key,
        by_runtime[8549].cells,
        by_runtime[8549].local_index,
    ) == (2, "22", 4, 84)
    assert (
        by_runtime[8543].quality,
        by_runtime[8543].shape_key,
        by_runtime[8543].cells,
    ) == (1, "11", 1)
    assert (
        by_runtime[8366].quality,
        by_runtime[8366].shape_key,
        by_runtime[8366].cells,
    ) == (3, "11", 1)

    rows = _skill_reveal_rows(events, {}, session_id="5219:indiana")
    assert [(row["skill_id"], row["round_index"]) for row in rows] == [
        (1003035, 4),
        (1003034, 3),
        (1003033, 2),
        (1003031, 0),
    ]
    assert [row["tool"] for row in rows] == [
        "印第安纳·R5 品质揭示",
        "印第安纳·R4 品质揭示",
        "印第安纳·R3 品质揭示",
        "印第安纳·R1 未知箱轮廓",
    ]
    assert [row.get("parent_skill_id") for row in rows] == [
        1003031,
        1003031,
        1003031,
        None,
    ]
    assert [row["round_index_state"] for row in rows] == [
        "wire",
        "wire",
        "wire",
        "skill_canonical",
    ]
    assert all(
        _skill_reveal_category(skill_id) is None
        and _complete_quality_reveal_scope_for_skill(303, skill_id) == ()
        for skill_id in (1003031, 1003033, 1003034, 1003035)
    )


def test_indiana_child_round_projection_rejects_missing_or_wrong_wire_round() -> None:
    assert _skill_reveal_round_projection(
        hero_id=303,
        skill_id=1003033,
        wire_round_index=2,
    ) == (2, "wire", None)
    assert _skill_reveal_round_projection(
        hero_id=303,
        skill_id=1003033,
        wire_round_index=None,
    ) == (None, "unavailable", None)
    assert _skill_reveal_round_projection(
        hero_id=303,
        skill_id=1003033,
        wire_round_index=3,
    ) == (None, "conflict", 3)


def test_wrong_session_and_late_foreign_hero_do_not_override_indiana() -> None:
    events = _events(
        _state(10, session_id="current", hero_id=303),
        _state(
            98,
            session_id="old",
            hero_id=303,
            skill_id=1003035,
            round_index=4,
            items=(_item(999, quality=6),),
        ),
        _state(99, session_id="old", hero_id=302),
    )

    resolved = _resolve_live_hero(
        events,
        SimpleNamespace(hero="?"),
        session_id="current",
    )
    assert resolved == "indiana"
    current_rows = _skill_reveal_rows(events, {}, session_id="current")
    assert [(row["hero_id"], row["skill_id"]) for row in current_rows] == [
        (303, 1003031)
    ]


def test_indiana_schedule_waits_for_each_proven_skill_round() -> None:
    spec = schedule.get_hero_schedule("indiana")
    assert spec is not None
    assert spec.hero_id == 303
    assert spec.skill_rounds == {
        1: 1003031,
        3: 1003033,
        4: 1003034,
        5: 1003035,
    }

    assert schedule.hero_quote_ready({}, "indiana", 1)[0] is False
    r1 = {
        "skill_reveal_rows": [
            {
                "hero_id": 303,
                "skill_id": 1003031,
                "revealed_items": 1,
                "observed_items": [
                    {"runtime_id": 1, "local_index": 0, "shape_key": "23", "cells": 6}
                ],
            }
        ]
    }
    assert schedule.hero_quote_ready(r1, "indiana", 1) == (True, "")
    assert schedule.hero_quote_ready(r1, "indiana", 2) == (True, "")
    assert schedule.hero_quote_ready(r1, "indiana", 3)[0] is False

    r3 = {
        "skill_reveal_rows": [
            *r1["skill_reveal_rows"],
            {
                "hero_id": 303,
                "skill_id": 1003033,
                "round_index": 2,
                "observed_items": [{"runtime_id": 1, "quality": 2}],
            },
        ]
    }
    assert schedule.hero_quote_ready(r3, "indiana", 3) == (True, "")
    assert schedule.hero_quote_ready(r3, "indiana", 4)[0] is False


def test_indiana_bridge_and_engine_run_generic_calculation_path() -> None:
    batch = _batch(1, _grid(801, cells=6, shape_key="23"))
    bridge = _ahmad_ref_inputs_from_batches(
        (batch,),
        hero="印第安纳",
        items={},
        session_id="5219:indiana",
    )
    assert bridge["hero"] == "indiana"
    assert bridge["minimap_items"][0]["quality"] is None
    assert bridge["minimap_items"][0]["shape_key"] == "23"

    result = engine.run_reference_engine(
        {
            "hero": "indiana",
            "map_id": 2401,
            "phase": "bidding",
            "structured_ref_inputs": {"hero": "indiana", "total_count": 28},
        },
        max_combos=200,
    )
    assert result.status != "not_structured_hero"
    assert result.evidence.hero == "indiana"
    assert "generic_ref_hero" in result.notes
    assert not any(str(note).startswith("raven_") for note in result.notes)


def test_aisha_and_ethan_complete_outline_semantics_are_unchanged() -> None:
    assert _complete_quality_reveal_scope_for_skill(103, 1001034) == (1,)
    ethan_state = _state(
        1,
        hero_id=ETHAN_HERO_ID,
        skill_id=ETHAN_SKILL_FULL_OUTLINE,
        items=(
            _item(901, shape_code=23, cells=6),
            _item(902, local_index=20, shape_code=11, cells=1),
        ),
    )
    updates = _full_outline_updates(ethan_state)
    by_path = {update.path: update.value for update in updates}
    assert by_path == {
        ("session", "warehouse_total_cells"): 7,
        ("session", "total_item_count"): 2,
    }


def test_current_rankmap_positive_membership_reaches_5608() -> None:
    v2._validated_physical_category_catalog.cache_clear()
    catalog, reachable, status, generation = v2._validated_physical_category_catalog()
    assert status == "ok"
    assert generation.startswith("physical-category-v1:")
    assert 5608 in reachable
    assert reachable[5608]
    assert reachable[5608] <= catalog.keys()


def test_current_sidecar_failure_isolated_from_legacy_210x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2._validated_physical_category_catalog.cache_clear()
    _catalog, baseline, status, _generation = v2._validated_physical_category_catalog()
    assert status == "ok"
    legacy_2101 = baseline[2101]

    monkeypatch.setattr(
        v2,
        "_validated_current_rankmap_reachable",
        lambda *_args: ({}, "current_map_catalog_hash_mismatch", "wrong"),
    )
    v2._validated_physical_category_catalog.cache_clear()
    _catalog, isolated, status, _generation = v2._validated_physical_category_catalog()
    assert status == "ok"
    assert isolated[2101] == legacy_2101
    assert 5608 not in isolated
    v2._validated_physical_category_catalog.cache_clear()


def _copy_current_sidecar_inputs(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "data" / "processed").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts" / "package_data_fingerprints.json",
        tmp_path / "scripts" / "package_data_fingerprints.json",
    )
    shutil.copyfile(
        ROOT / "data" / "processed" / "rankmap_drop_weights_current.json",
        tmp_path / "data" / "processed" / "rankmap_drop_weights_current.json",
    )
    return tmp_path


@pytest.mark.parametrize("failure", ("missing", "tamper", "wrong_generation"))
def test_current_sidecar_missing_tamper_and_generation_mismatch_fail_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    v2._validated_physical_category_catalog.cache_clear()
    catalog, legacy, status, _generation = v2._validated_physical_category_catalog()
    assert status == "ok"
    legacy_only = {map_id: ids for map_id, ids in legacy.items() if map_id != 5608}

    if failure != "missing":
        _copy_current_sidecar_inputs(tmp_path)
    if failure == "tamper":
        path = tmp_path / "data" / "processed" / "rankmap_drop_weights_current.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif failure == "wrong_generation":
        path = tmp_path / "scripts" / "package_data_fingerprints.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["baseline_id"] = "wrong-generation"
        path.write_text(json.dumps(document), encoding="utf-8")

    current, current_status, _current_generation = (
        v2._validated_current_rankmap_reachable(tmp_path, catalog, legacy_only)
    )
    assert current == {}
    assert current_status != "ok"
