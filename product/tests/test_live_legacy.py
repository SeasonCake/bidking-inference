from __future__ import annotations

from bidking_lab.live import (
    LiveSessionState,
    apply_observation_batch,
    live_batch_from_legacy_obs,
    live_session_matches_context,
    live_state_to_session_obs,
)


def _session_from_legacy_obs(obs: dict):
    return live_state_to_session_obs(
        apply_observation_batch(
            LiveSessionState(),
            live_batch_from_legacy_obs(
                obs,
                source="manual",
                event_kind="manual_update",
            ),
        )
    )


def test_legacy_ocr_snapshot_maps_into_session_observation() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 22,
            "blue_cells": 15,
            "purple_avg_raw": "3.27",
            "purple_value": 86_490,
        },
        source="ocr",
        event_kind="ocr_update",
    )

    state = apply_observation_batch(LiveSessionState(), batch)
    session = live_state_to_session_obs(state)

    assert batch.event_kind == "ocr_update"
    assert session is not None
    assert session.warehouse_capacity() == 123
    assert session.buckets[1].total_cells == 22
    assert session.buckets[3].total_cells == 15
    assert session.buckets[4].avg_cells is not None
    assert session.buckets[4].avg_cells.raw == "3.27"
    assert session.buckets[4].value_sum == 86_490


def test_legacy_manual_delta_emits_field_clear() -> None:
    previous = {
        "map_id": 2405,
        "hero": "ethan",
        "warehouse_cells": 123,
        "gold_avg_raw": "4.00",
    }
    current = {
        "map_id": 2405,
        "hero": "ethan",
        "warehouse_cells": 123,
    }

    initial = apply_observation_batch(
        LiveSessionState(),
        live_batch_from_legacy_obs(
            previous,
            source="manual",
            event_kind="manual_update",
        ),
    )
    updated = apply_observation_batch(
        initial,
        live_batch_from_legacy_obs(
            current,
            previous=previous,
            source="manual",
            event_kind="manual_update",
        ),
    )
    session = live_state_to_session_obs(updated)

    assert updated.fields[("bucket", "5", "avg_cells")].value is None
    assert session is not None
    assert 5 not in session.buckets


def test_legacy_aisha_split_keeps_white_and_green_as_distinct_buckets() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2407,
            "hero": "aisha",
            "warehouse_cells": 128,
            "aisha_split": True,
            "white_cells": 24,
            "white_count": 12,
            "green_cells": 18,
            "green_count": 7,
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.buckets[1].total_cells == 24
    assert session.buckets[1].count == 12
    assert session.buckets[2].total_cells == 18
    assert session.buckets[2].count == 7


def test_legacy_effective_huge_item_preserves_cells_override() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "gold_huge_band": "1",
            "gold_huge_cells_override": 18,
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.buckets[5].huge_band == "1"
    assert session.buckets[5].huge_cells_override == 18
    assert session.buckets[5].min_huge_cells() == 18


def test_legacy_small_warehouse_matches_existing_red_cap_rule() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "small_warehouse_confirmed": True,
            "red_cells_total": 40,
            "red_huge_band": "1",
            "red_huge_cells_override": 16,
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.buckets[6].total_cells == 6
    assert session.buckets[6].huge_band == "none"


def test_legacy_aisha_does_not_observe_gold_or_red_huge_items() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2407,
            "hero": "aisha",
            "warehouse_cells": 128,
            "gold_value": 80_000,
            "gold_huge_band": "1",
            "gold_huge_cells_override": 18,
            "red_cells_total": 12,
            "red_huge_band": "1",
            "red_huge_cells_override": 16,
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.buckets[5].huge_band == "none"
    assert session.buckets[5].huge_cells_override == 0
    assert session.buckets[6].huge_band == "none"
    assert session.buckets[6].huge_cells_override == 0


def test_legacy_zero_defaults_do_not_create_low_quality_buckets() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2407,
            "hero": "aisha",
            "warehouse_cells": 128,
            "total_item_count": 0,
            "aisha_split": True,
            "white_cells": 0,
            "white_count": 0,
            "green_cells": 0,
            "green_count": 0,
            "blue_cells": 0,
            "blue_count": 0,
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.total_item_count is None
    assert session.buckets == {}


def test_legacy_formatted_average_value_is_numeric() -> None:
    batch = live_batch_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "gold_avg_value": "39,539.17",
        },
        source="manual",
        event_kind="manual_update",
    )

    session = live_state_to_session_obs(
        apply_observation_batch(LiveSessionState(), batch)
    )

    assert session is not None
    assert session.buckets[5].avg_value == 39_539.17


def test_live_adapter_matches_legacy_ethan_full_reading_contract() -> None:
    session = _session_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 132,
            "total_item_count": 42,
            "wg_cells": 31,
            "blue_cells": 15,
            "purple_cells": 24,
            "purple_count": 8,
            "purple_avg_raw": "3.00",
            "purple_value": 86_490,
            "purple_avg_value": "10811.25",
            "purple_huge_band": "1",
            "purple_huge_cells_override": 10,
            "gold_cells": 27,
            "gold_count": 3,
            "gold_avg_raw": "9.00",
            "gold_value": 244_350,
            "gold_avg_value": "81,450",
            "gold_huge_band": "1",
            "gold_huge_cells_override": 18,
            "red_cells_total": 12,
            "red_value_lo": 300_000,
            "red_value_hi": 420_000,
            "red_huge_band": "1",
            "red_huge_cells_override": 16,
        }
    )

    assert session is not None
    assert session.map_id == 2405
    assert session.hero == "ethan"
    assert session.warehouse_capacity() == 132
    assert session.total_item_count == 42
    assert session.buckets[1].total_cells == 31
    assert session.buckets[3].total_cells == 15
    purple = session.buckets[4]
    assert purple.total_cells == 24
    assert purple.count == 8
    assert purple.avg_cells is not None
    assert purple.avg_cells.raw == "3.00"
    assert purple.value_sum == 86_490
    assert purple.avg_value == 10811.25
    assert purple.huge_band == "1"
    assert purple.huge_cells_override == 10
    gold = session.buckets[5]
    assert gold.total_cells == 27
    assert gold.count == 3
    assert gold.avg_cells is not None
    assert gold.avg_cells.raw == "9.00"
    assert gold.value_sum == 244_350
    assert gold.avg_value == 81_450
    assert gold.huge_band == "1"
    assert gold.huge_cells_override == 18
    red = session.buckets[6]
    assert red.total_cells == 12
    assert red.value_range == (300_000, 420_000)
    assert red.huge_band == "1"
    assert red.huge_cells_override == 16


def test_live_adapter_matches_legacy_aisha_merged_low_quality_contract() -> None:
    session = _session_from_legacy_obs(
        {
            "map_id": 2407,
            "hero": "aisha",
            "warehouse_cells": 128,
            "aisha_split": False,
            "white_cells": 24,
            "white_count": 10,
            "green_cells": 18,
            "green_count": 7,
            "blue_cells": 14,
            "blue_count": 5,
            "purple_cells": 21,
            "purple_count": 6,
            "purple_huge_band": "1",
            "purple_huge_cells_override": 10,
            "gold_cells": 22,
            "gold_count": 2,
            "gold_huge_band": "1",
            "gold_huge_cells_override": 18,
            "red_cells_total": 9,
            "red_huge_band": "1",
            "red_huge_cells_override": 16,
        }
    )

    assert session is not None
    assert session.buckets[1].total_cells == 42
    assert session.buckets[1].count == 17
    assert 2 not in session.buckets
    assert session.buckets[3].total_cells == 14
    assert session.buckets[3].count == 5
    assert session.buckets[4].huge_band == "1"
    assert session.buckets[4].huge_cells_override == 10
    assert session.buckets[5].total_cells == 22
    assert session.buckets[5].count == 2
    assert session.buckets[5].huge_band == "none"
    assert session.buckets[5].huge_cells_override == 0
    assert session.buckets[6].total_cells == 9
    assert session.buckets[6].huge_band == "none"
    assert session.buckets[6].huge_cells_override == 0


def test_live_adapter_infers_residual_red_bucket_like_legacy_builder() -> None:
    session = _session_from_legacy_obs(
        {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 100,
            "wg_cells": 20,
            "blue_cells": 10,
            "purple_cells": 30,
            "gold_cells": 25,
        }
    )

    assert session is not None
    assert session.buckets[6].total_cells == 15
    assert live_session_matches_context(
        session,
        map_id=2405,
        warehouse_total_cells=100,
    )
    assert not live_session_matches_context(
        session,
        map_id=2407,
        warehouse_total_cells=100,
    )
