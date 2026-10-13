from __future__ import annotations

from typing import Any

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansObservedItem,
    FatbeansPlayerBid,
    FatbeansPublicInfo,
    FatbeansSendEvent,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    FatbeansStatusEvent,
)
from bidking_lab.live.monitor import (
    MonitorTables,
    _ahmad_ref_inputs_from_batches,
    _latest_confirmed_session_id,
    _minimap_grid_items,
    _public_info_rows,
    build_monitor_artifact_from_events,
)
from bidking_lab.live.types import (
    FieldUpdate,
    GridItemObservation,
    LiveObservationBatch,
)


SESSION_A = "4501:1770351298803000"
SESSION_B = "4501:9000000000000035"


def _state(
    *,
    sort_id: int,
    session_id: Any,
    message_id: int = 0x0025,
    map_id: int = 4501,
    round_index: int = 3,
    player_id: int | None = None,
    capture_time: str = "2026-07-28 19:42:43.000",
    public_avg: float | None = None,
    include_hero: bool = True,
    skill_id: int = 1001032,
    observed_cells: int | None = None,
    bids: tuple[FatbeansPlayerBid, ...] = (),
    inventory_items: tuple[FatbeansInventoryItem, ...] = (),
) -> FatbeansStateEvent:
    return FatbeansStateEvent(
        sort_id=sort_id,
        capture_time=capture_time,
        message_id=message_id,
        session_id=session_id,
        map_id=map_id,
        round_index=round_index,
        player_id=player_id,
        bids=bids,
        skill_reveals=(
            (
                FatbeansSkillReveal(
                    skill_id=skill_id,
                    hero_id=103,
                    round_index=3,
                    observed_items=(
                        (
                            FatbeansObservedItem(
                                local_index=sort_id,
                                runtime_id=sort_id,
                                item_id=None,
                                quality=4 if skill_id == 1001031 else 3,
                                value=None,
                                shape_code=1,
                                cells=observed_cells,
                            ),
                        )
                        if observed_cells is not None
                        else ()
                    ),
                ),
            )
            if include_hero
            else ()
        ),
        public_infos=(
            (
                FatbeansPublicInfo(
                    info_id=200013,
                    map_id=4501,
                    value=public_avg,
                    value_field=3,
                ),
            )
            if public_avg is not None
            else ()
        ),
        inventory_items=inventory_items,
    )


def _events(
    states: tuple[FatbeansStateEvent, ...],
    *,
    statuses: tuple[FatbeansStatusEvent, ...] = (),
    sends: tuple[FatbeansSendEvent, ...] = (),
) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=sends,
        statuses=statuses,
        states=states,
    )


def _batch(
    *,
    session_id: str,
    sequence: int,
    quality: int,
    avg_cells: float | None = None,
    phase: str = "bidding",
) -> LiveObservationBatch:
    return LiveObservationBatch(
        source="packet",
        event_kind="public_info",
        phase=phase,  # type: ignore[arg-type]
        sequence=sequence,
        session_id=session_id,
        field_updates=(
            (
                FieldUpdate(
                    path=("bucket", str(quality), "avg_cells"),
                    value=avg_cells,
                    source="packet",
                    confidence="exact",
                ),
            )
            if avg_cells is not None
            else ()
        ),
        grid_items=(
            GridItemObservation(
                cells=1,
                source="packet",
                confidence="exact",
                runtime_id=sequence,
                quality=quality,
                shape_key="1",
                local_index=sequence,
            ),
        ),
    )


def test_latest_confirmed_session_ignores_late_older_state() -> None:
    events = _events(
        (
            _state(sort_id=1, session_id=SESSION_B),
            _state(sort_id=2, session_id=SESSION_A, public_avg=2.75),
        )
    )

    assert _latest_confirmed_session_id(events) == SESSION_B
    assert _public_info_rows(
        events,
        {},
        session_id=_latest_confirmed_session_id(events),
    ) == []


def test_latest_confirmed_session_prefers_bidding_after_old_settlement() -> None:
    events = _events(
        (
            _state(
                sort_id=1,
                session_id=SESSION_A,
                message_id=0x002D,
            ),
            _state(sort_id=2, session_id=SESSION_B),
        )
    )

    assert _latest_confirmed_session_id(events) == SESSION_B


def test_public_rows_do_not_leak_from_previous_session() -> None:
    events = _events(
        (
            _state(
                sort_id=1,
                session_id=SESSION_A,
                public_avg=2.75,
                skill_id=1001031,
                observed_cells=3,
            ),
            _state(
                sort_id=2,
                session_id=SESSION_B,
                observed_cells=2,
            ),
        )
    )

    assert _public_info_rows(events, {}, session_id=SESSION_B) == []
    assert _public_info_rows(events, {})[0]["value"] == 2.75


def test_structured_and_minimap_bridges_use_only_current_session() -> None:
    batches = (
        _batch(
            session_id=SESSION_A,
            sequence=11,
            quality=4,
            avg_cells=2.75,
        ),
        _batch(
            session_id=SESSION_B,
            sequence=22,
            quality=3,
            avg_cells=2.0,
        ),
        _batch(
            session_id=SESSION_A,
            sequence=33,
            quality=6,
            phase="settled",
        ),
    )

    bridge = _ahmad_ref_inputs_from_batches(
        batches,
        hero="aisha",
        items={},
        session_id=SESSION_B,
    )
    minimap = _minimap_grid_items(batches, {}, session_id=SESSION_B)

    assert bridge["avg_cells"] == {"q3": 2.0}
    assert {row["sequence"] for row in bridge["field_updates"]} == {22}
    assert bridge["minimap_items"][0]["quality"] == 3
    assert [row["quality"] for row in minimap] == [3]


def test_single_session_filter_is_strict_noop() -> None:
    events = _events(
        (_state(sort_id=1, session_id=SESSION_B, public_avg=2.75),)
    )
    batches = (
        _batch(
            session_id=SESSION_B,
            sequence=22,
            quality=4,
            avg_cells=2.75,
        ),
    )

    assert _public_info_rows(events, {}, session_id=SESSION_B) == (
        _public_info_rows(events, {})
    )
    assert _ahmad_ref_inputs_from_batches(
        batches,
        hero="aisha",
        items={},
        session_id=SESSION_B,
    ) == _ahmad_ref_inputs_from_batches(batches, hero="aisha", items={})
    assert _minimap_grid_items(
        batches,
        {},
        session_id=SESSION_B,
    ) == _minimap_grid_items(batches, {})


def test_build_monitor_artifact_routes_public_bridge_to_current_session() -> None:
    events = _events(
        (
            _state(
                sort_id=1,
                session_id=SESSION_A,
                public_avg=2.75,
                skill_id=1001031,
                observed_cells=3,
            ),
            _state(
                sort_id=2,
                session_id=SESSION_B,
                observed_cells=2,
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=2,
                capture_time="2026-07-28 19:42:44.000",
                player_id=None,
                session_id=SESSION_B,
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="current_session_isolation.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == SESSION_B
    assert artifact["public_info_rows"] == []
    assert artifact["structured_ref_inputs"]["counts"] == {"q3": 1}
    assert artifact["structured_ref_inputs"]["quality_cells"] == {"q3": 2}
    assert artifact["structured_ref_inputs"]["avg_cells"] == {}
    assert [row["quality"] for row in artifact["minimap_grid_items"]] == [3]


def test_previous_session_hero_does_not_label_current_structured_bridge() -> None:
    events = _events(
        (
            _state(sort_id=1, session_id=SESSION_A, public_avg=2.75),
            _state(
                sort_id=2,
                session_id=SESSION_B,
                include_hero=False,
            ),
        )
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="current_session_hero_isolation.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == SESSION_B
    assert artifact["hero"] is None
    assert artifact["hero_id"] is None
    assert artifact["structured_ref_inputs"] == {}


def test_artifact_old_settlement_cannot_change_current_bidding_phase() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=SESSION_B,
                round_index=2,
            ),
            _state(
                sort_id=30,
                session_id=SESSION_A,
                message_id=0x002D,
                round_index=5,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=1001,
                        quality=6,
                        cells=4,
                    ),
                ),
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=9,
                capture_time="2026-07-28 19:42:42.000",
                player_id=None,
                session_id=SESSION_B,
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="late_old_settlement.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == SESSION_B
    assert artifact["phase"] == "bidding"
    assert artifact["round"] == 3
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None
    assert artifact["known_value_sum"] is None
    assert "final_quality_counts" not in artifact


def test_artifact_old_map_round_bid_and_capture_context_are_session_scoped() -> None:
    current_bid = FatbeansPlayerBid(
        player_id=1,
        name="current",
        hero_id=103,
        values=(123_000,),
    )
    old_bid = FatbeansPlayerBid(
        player_id=2,
        name="old",
        hero_id=101,
        values=(999_000,),
    )
    events = _events(
        (
            _state(
                sort_id=10,
                session_id="4501:200",
                map_id=4501,
                round_index=2,
                player_id=1,
                capture_time="2026-07-28 19:42:43.000",
                bids=(current_bid,),
            ),
            _state(
                sort_id=40,
                session_id="2510:100",
                map_id=2510,
                round_index=5,
                capture_time="2026-07-29 23:59:59.000",
                bids=(old_bid,),
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=9,
                capture_time="2026-07-28 19:42:42.000",
                player_id=1,
                session_id="4501:200",
            ),
        ),
        sends=(
            FatbeansSendEvent(
                sort_id=11,
                capture_time="2026-07-28 19:42:44.000",
                message_id=0x0022,
                session_id="4501:200",
                value=123_000,
            ),
            FatbeansSendEvent(
                sort_id=41,
                capture_time="2026-07-29 23:59:59.100",
                message_id=0x0022,
                session_id="2510:100",
                value=999_000,
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="late_old_map_round_bid_capture.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == "4501:200"
    assert artifact["map_id"] == 4501
    assert artifact["round"] == 3
    assert artifact["latest_bids"] == {"current": 123_000}
    assert artifact["self_bid"] == 123_000
    assert artifact["warehouse_source_generation"] != "postupdate"


def test_artifact_current_session_settlement_remains_authoritative() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=SESSION_A,
                message_id=0x002D,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=1001,
                        quality=4,
                        cells=3,
                    ),
                ),
            ),
            _state(
                sort_id=20,
                session_id=SESSION_B,
                round_index=4,
            ),
            _state(
                sort_id=30,
                session_id=SESSION_B,
                message_id=0x002D,
                round_index=4,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=2,
                        item_id=1002,
                        quality=6,
                        cells=5,
                    ),
                ),
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=19,
                capture_time="2026-07-28 19:42:42.000",
                player_id=None,
                session_id=SESSION_B,
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="current_session_settlement.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == SESSION_B
    assert artifact["phase"] == "settled"
    assert artifact["states"] == 2
    assert artifact["inventory_count"] == 1
    assert artifact["inventory_cells"] == 5


def test_artifact_confirmed_session_rejects_legacy_none_skill() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=SESSION_B,
                include_hero=False,
            ),
            _state(
                sort_id=30,
                session_id=None,
                skill_id=1001031,
                observed_cells=4,
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=9,
                capture_time="2026-07-28 19:42:42.000",
                player_id=None,
                session_id=SESSION_B,
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="confirmed_rejects_none_skill.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == SESSION_B
    assert artifact["skill_reveal_rows"] == []
    assert artifact["skill_reveals"] == []
    assert artifact["hero"] is None
    assert artifact["hero_id"] is None


def test_same_generation_cross_map_prefers_confirmed_status_session() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id="4501:200",
                map_id=4501,
            ),
            _state(
                sort_id=30,
                session_id="2510:200",
                map_id=2510,
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=9,
                capture_time="2026-07-28 19:42:42.000",
                player_id=None,
                session_id="4501:200",
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="same_generation_cross_map.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == "4501:200"
    assert artifact["map_id"] == 4501


def test_pure_legacy_none_session_retains_compatibility() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=None,
                public_avg=2.75,
                observed_cells=4,
            ),
        )
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="pure_legacy_none_session.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["public_info_rows"][0]["value"] == 2.75
    assert len(artifact["skill_reveal_rows"]) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_artifact_excludes_malformed_integer_session(
    reverse: bool,
) -> None:
    legacy = _state(
        sort_id=10,
        session_id=None,
        map_id=4501,
        round_index=2,
        public_avg=2.75,
    )
    malformed = _state(
        sort_id=30,
        session_id=123,
        map_id=2510,
        message_id=0x002D,
        round_index=5,
        inventory_items=(
            FatbeansInventoryItem(
                runtime_id=3,
                item_id=1003,
                quality=6,
                cells=9,
            ),
        ),
    )
    states = (malformed, legacy) if reverse else (legacy, malformed)

    artifact = build_monitor_artifact_from_events(
        _events(states),
        file="legacy_with_malformed_integer.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "bidding"
    assert artifact["round"] == 3
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None
    assert artifact["inventory_cells"] is None
    assert artifact["ui_contract"]["context"]["map_id"] == 4501
    assert artifact["ui_contract"]["context"]["phase"] == "bidding"


class _CustomSession:
    def __str__(self) -> str:
        return "2510:999"


class _CustomStringSession(str):
    pass


class _ExplosiveStringSession(str):
    def __str__(self) -> str:
        raise RuntimeError("must not stringify malformed session")

    def __eq__(self, _other: object) -> bool:
        raise RuntimeError("must not compare malformed session")

    __hash__ = str.__hash__


@pytest.mark.parametrize(
    "malformed_session",
    [
        True,
        1.5,
        [],
        {},
        _CustomSession(),
        _CustomStringSession("2510:999"),
        _ExplosiveStringSession("2510:999"),
        "",
        " ",
        "2510",
        "2510:",
        ":999",
        "2510::bad",
        "2510 bad",
    ],
)
def test_malformed_session_kinds_cannot_pollute_legacy_artifact(
    malformed_session: Any,
) -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=None,
                map_id=4501,
                round_index=2,
            ),
            _state(
                sort_id=30,
                session_id=malformed_session,
                map_id=2510,
                message_id=0x002D,
                round_index=5,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=3,
                        item_id=1003,
                        quality=6,
                        cells=9,
                    ),
                ),
            ),
        )
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="legacy_with_malformed_kind.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "bidding"
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None


@pytest.mark.parametrize(
    "malformed_session",
    [
        123,
        True,
        1.5,
        [],
        {},
        _CustomSession(),
        _CustomStringSession("2510:999"),
        _ExplosiveStringSession("2510:999"),
        "",
        "2510:",
        "2510::bad",
        "٢٥١٠:999",
        "２５١０:999",
    ],
)
def test_only_malformed_session_is_stable_empty_artifact(
    malformed_session: Any,
) -> None:
    events = _events(
        (
            _state(
                sort_id=30,
                session_id=malformed_session,
                map_id=2510,
                message_id=0x002D,
                round_index=5,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=3,
                        item_id=1003,
                        quality=6,
                        cells=9,
                    ),
                ),
            ),
        )
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="only_malformed_session.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] is None
    assert artifact["phase"] == "unknown"
    assert artifact["round"] is None
    assert artifact["states"] == 0
    assert artifact["batches"] == 0
    assert artifact["inventory_count"] is None
    assert artifact["skill_reveal_rows"] == []
    assert artifact["public_info_rows"] == []
    assert artifact["structured_ref_inputs"] == {}
    assert artifact["ui_contract"]["context"]["map_id"] is None
    assert artifact["ui_contract"]["context"]["phase"] == "unknown"


def test_confirmed_session_ignores_malformed_and_cross_map_states() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id="4501:200",
                map_id=4501,
                round_index=2,
            ),
            _state(
                sort_id=30,
                session_id=123,
                map_id=2510,
                message_id=0x002D,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=3,
                        item_id=1003,
                        quality=6,
                        cells=9,
                    ),
                ),
            ),
            _state(
                sort_id=40,
                session_id="4501:200",
                map_id=2510,
                message_id=0x002D,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=4,
                        item_id=1004,
                        quality=6,
                        cells=8,
                    ),
                ),
            ),
        ),
        statuses=(
            FatbeansStatusEvent(
                sort_id=9,
                capture_time="2026-07-28 19:42:42.000",
                player_id=None,
                session_id="4501:200",
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="confirmed_with_malformed_and_cross_map.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] == "4501:200"
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "bidding"
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None


def test_multiple_legacy_none_states_keep_existing_latest_phase_contract() -> None:
    events = _events(
        (
            _state(
                sort_id=10,
                session_id=None,
                map_id=4501,
                round_index=4,
            ),
            _state(
                sort_id=20,
                session_id=None,
                map_id=4501,
                message_id=0x002D,
                round_index=4,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=2,
                        item_id=1002,
                        quality=6,
                        cells=5,
                    ),
                ),
            ),
        )
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="multiple_legacy_none_states.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "settled"
    assert artifact["states"] == 2
    assert artifact["inventory_count"] == 1
    assert artifact["inventory_cells"] == 5


@pytest.mark.parametrize(
    "malformed_session",
    [
        "٢٥١٠:999",
        "２５１０:999",
        "25١٠:999",
        "2510:٩٩٩",
        "2510:９９９",
        "2510:9٩9",
        "2510:+999",
        "2510:-999",
        "2510: 999",
        "2510:999 ",
        "2510:1e3",
        "2510:1_000",
        "2510:999:1",
        "25100:999",
        "251:999",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_non_ascii_and_native_like_invalid_session_cannot_pollute_legacy(
    malformed_session: str,
    reverse: bool,
) -> None:
    legacy = _state(
        sort_id=10,
        session_id=None,
        map_id=4501,
        round_index=2,
        public_avg=2.75,
    )
    malformed = _state(
        sort_id=30,
        session_id=malformed_session,
        map_id=2510,
        message_id=0x002D,
        round_index=5,
        inventory_items=(
            FatbeansInventoryItem(
                runtime_id=3,
                item_id=1003,
                quality=6,
                cells=8,
            ),
        ),
    )
    states = (malformed, legacy) if reverse else (legacy, malformed)

    artifact = build_monitor_artifact_from_events(
        _events(states),
        file="legacy_with_non_ascii_or_native_like_invalid.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "bidding"
    assert artifact["round"] == 3
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None
    assert artifact["ui_contract"]["context"]["map_id"] == 4501
    assert artifact["ui_contract"]["context"]["phase"] == "bidding"


@pytest.mark.parametrize("reverse", [False, True])
def test_overlong_native_token_cannot_pollute_legacy_artifact(
    reverse: bool,
) -> None:
    legacy = _state(
        sort_id=10,
        session_id=None,
        map_id=4501,
        round_index=2,
    )
    malformed = _state(
        sort_id=30,
        session_id="2510:" + ("9" * 5000),
        map_id=2510,
        message_id=0x002D,
        round_index=5,
        inventory_items=(
            FatbeansInventoryItem(
                runtime_id=3,
                item_id=1003,
                quality=6,
                cells=7,
            ),
        ),
    )
    states = (malformed, legacy) if reverse else (legacy, malformed)

    artifact = build_monitor_artifact_from_events(
        _events(states),
        file="legacy_with_overlong_native_token.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["session_id"] is None
    assert artifact["map_id"] == 4501
    assert artifact["phase"] == "bidding"
    assert artifact["states"] == 1
    assert artifact["inventory_count"] is None
    assert artifact["ui_contract"]["context"]["map_id"] == 4501
    assert artifact["ui_contract"]["context"]["phase"] == "bidding"

    only_malformed = build_monitor_artifact_from_events(
        _events((malformed,)),
        file="only_overlong_native_token.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )
    assert only_malformed["session_id"] is None
    assert only_malformed["map_id"] is None
    assert only_malformed["phase"] == "unknown"
    assert only_malformed["states"] == 0
    assert only_malformed["batches"] == 0
    assert only_malformed["inventory_count"] is None
    assert only_malformed["ui_contract"]["context"]["map_id"] is None
    assert only_malformed["ui_contract"]["context"]["phase"] == "unknown"


@pytest.mark.parametrize(
    ("session_id", "accepted"),
    [
        ("4501:18446744073709551615", True),
        ("4501:18446744073709551616", False),
        ("4501:" + ("9" * 21), False),
        ("4501:" + ("a" * 32), True),
        ("4501:" + ("a" * 33), False),
        ("s" * 32, True),
        ("s" * 33, False),
    ],
)
def test_session_id_numeric_and_synthetic_boundaries(
    session_id: str,
    accepted: bool,
) -> None:
    confirmed = _state(
        sort_id=20,
        session_id=session_id,
        map_id=4501,
        round_index=2,
    )
    events = _events((confirmed,))

    artifact = build_monitor_artifact_from_events(
        events,
        file="session_id_boundaries.json",
        tables=MonitorTables(maps={}, drops={}, items={}),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    if accepted:
        assert artifact["session_id"] == session_id
        assert artifact["map_id"] == 4501
        assert artifact["phase"] == "bidding"
        assert artifact["states"] == 1
        return
    assert artifact["session_id"] is None
    assert artifact["map_id"] is None
    assert artifact["phase"] == "unknown"
    assert artifact["states"] == 0
    assert artifact["batches"] == 0
    assert artifact["ui_contract"]["context"]["map_id"] is None
    assert artifact["ui_contract"]["context"]["phase"] == "unknown"
