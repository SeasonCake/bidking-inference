from __future__ import annotations

from bidking_lab.live import (
    FieldUpdate,
    LiveSessionState,
    LiveObservationBatch,
    apply_observation_batch,
    live_batch_from_packet_fixture,
    live_state_to_session_obs,
)


def test_packet_fixture_maps_session_buckets_and_grid_items() -> None:
    batch = live_batch_from_packet_fixture(
        {
            "event_kind": "public_info_changed",
            "phase": "bidding",
            "sequence": 42,
            "session": {
                "map_id": 2401,
                "hero": "ethan",
                "warehouse_total_cells": 123,
                "round": 3,
            },
            "buckets": {
                "4": {"cells": 18, "count": 6, "avg_cells": "3"},
                "5": {"total_cells": 10, "value_sum": 120000},
            },
            "grid_items": [
                {
                    "item_id": 10001,
                    "quality": 4,
                    "shape_key": "2x3",
                    "value": 8888,
                },
                {"quality": 5, "cells": 4, "shape": "L4"},
            ],
        }
    )

    assert batch.source == "packet"
    assert batch.event_kind == "public_info_changed"
    assert batch.phase == "bidding"
    assert batch.sequence == 42
    assert len(batch.grid_items) == 2
    assert batch.grid_items[0].cells == 6
    assert batch.grid_items[0].item_id == 10001
    assert batch.grid_items[1].shape_key == "L4"

    state = apply_observation_batch(LiveSessionState(), batch)
    session = live_state_to_session_obs(state)

    assert state.dirty is True
    assert session is not None
    assert session.map_id == 2401
    assert session.hero == "ethan"
    assert session.warehouse_capacity() == 123
    assert session.buckets[4].total_cells == 18
    assert session.buckets[4].count == 6
    assert session.buckets[5].value_sum == 120000


def test_packet_fixture_infers_round_event_and_heartbeat() -> None:
    round_batch = live_batch_from_packet_fixture({"round_index": 2})
    heartbeat = live_batch_from_packet_fixture({"heartbeat": True})

    assert round_batch.event_kind == "round_changed"
    assert heartbeat.event_kind == "heartbeat"

    ready = apply_observation_batch(LiveSessionState(), round_batch)
    after_heartbeat = apply_observation_batch(ready, heartbeat)

    assert ready.dirty is True
    assert after_heartbeat is ready


def test_packet_fixture_packet_overrides_manual_state() -> None:
    manual = LiveObservationBatch(
        source="manual",
        event_kind="manual_update",
        field_updates=(
            FieldUpdate(
                path=("session", "warehouse_total_cells"),
                value=90,
                source="manual",
                confidence="exact",
            ),
        ),
    )
    state = apply_observation_batch(LiveSessionState(), manual)

    packet = live_batch_from_packet_fixture(
        {
            "event": "state",
            "warehouse_cells": 123,
            "buckets": {"6": {"value_range": [3000000, 3200000]}},
        }
    )
    state = apply_observation_batch(state, packet)

    assert state.fields[("session", "warehouse_total_cells")].value == 123
    assert state.fields[("bucket", "6", "value_range")].value == (
        3000000,
        3200000,
    )


def test_packet_fixture_keeps_ethan_warehouse_estimate_without_item_count() -> None:
    batch = live_batch_from_packet_fixture(
        {
            "event": "public",
            "session": {
                "map_id": 2510,
                "hero": "ethan",
                "warehouse_estimated_cells": 112,
                "warehouse_estimate_tolerance": 18,
            },
            "visible_items": [{"shape": "3x4", "quality": None}],
        }
    )
    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.warehouse_total_cells is None
    assert session.warehouse_total_cells_approx == 112
    assert session.warehouse_total_cells_tolerance == 18
    assert session.warehouse_capacity() == 112
    assert session.warehouse_capacity_upper_bound() == 130
    assert session.total_item_count is None


def test_packet_fixture_r5_exact_warehouse_and_item_count_override_estimate() -> None:
    batch = live_batch_from_packet_fixture(
        {
            "event": "public",
            "session": {
                "map_id": 2510,
                "hero": "ethan",
                "warehouse_estimated_cells": 112,
                "warehouse_estimate_tolerance": 18,
                "warehouse_total_cells": 123,
                "total_item_count": 37,
            },
        }
    )
    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.warehouse_total_cells == 123
    assert session.warehouse_total_cells_approx == 112
    assert session.warehouse_capacity() == 123
    assert session.warehouse_capacity_upper_bound() == 123
    assert session.total_item_count == 37
