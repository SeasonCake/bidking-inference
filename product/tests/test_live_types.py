from __future__ import annotations

from bidking_lab.live import (
    FieldUpdate,
    GridItemObservation,
    LiveSessionState,
    LiveObservationBatch,
    apply_observation_batch,
    event_requests_recompute,
    legacy_obs_fields,
    live_inference_status,
    live_session_matches_context,
    live_state_to_session_obs,
    mark_ready,
    source_priority,
    summarize_blocked_field_updates,
    summarize_field_sources,
    summarize_selected_field_sources,
)


def test_source_priority_keeps_packet_above_manual_and_ocr() -> None:
    assert source_priority("packet") > source_priority("manual")
    assert source_priority("manual") > source_priority("ocr")
    assert source_priority("ocr") > source_priority("derived")


def test_only_semantic_events_request_recompute() -> None:
    for event_kind in (
        "manual_update",
        "ocr_update",
        "session_started",
        "round_changed",
        "tool_revealed",
        "public_info_changed",
        "session_settled",
    ):
        assert event_requests_recompute(event_kind) is True

    assert event_requests_recompute("heartbeat") is False


def test_live_observation_batch_can_hold_packet_grid_items() -> None:
    batch = LiveObservationBatch(
        source="packet",
        phase="reading",
        field_updates=(
            FieldUpdate(
                path=("session", "warehouse_total_cells"),
                value=123,
                source="packet",
                confidence="exact",
                sequence=7,
            ),
        ),
        grid_items=(
            GridItemObservation(
                cells=12,
                quality=None,
                source="packet",
                confidence="exact",
                shape_key="3x4",
            ),
        ),
        sequence=7,
    )

    assert batch.field_updates[0].path == ("session", "warehouse_total_cells")
    assert batch.grid_items[0].quality is None
    assert batch.grid_items[0].cells == 12


def _packet_grid_item(
    *,
    cells: int = 4,
    runtime_id: int | None = None,
    item_id: int | None = None,
    quality: int | None = None,
    shape_key: str | None = None,
    value: int | None = None,
    local_index: int | None = None,
) -> GridItemObservation:
    return GridItemObservation(
        cells=cells,
        source="packet",
        confidence="exact",
        runtime_id=runtime_id,
        item_id=item_id,
        quality=quality,
        shape_key=shape_key,
        value=value,
        local_index=local_index,
    )


def _apply_grid_items(
    state: LiveSessionState,
    *items: GridItemObservation,
    phase: str = "bidding",
    sequence: int | None = None,
) -> LiveSessionState:
    return apply_observation_batch(
        state,
        LiveObservationBatch(
            source="packet",
            event_kind="tool_revealed",
            phase=phase,
            sequence=sequence,
            grid_items=items,
        ),
    )


def test_grid_item_same_runtime_enriches_one_physical_observation() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(runtime_id=501, quality=5),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(
            runtime_id=501,
            item_id=100,
            quality=None,
            shape_key="22",
            value=12345,
            local_index=12,
        ),
        sequence=2,
    )

    assert len(state.grid_items) == 1
    item = state.grid_items[0]
    assert item.runtime_id == 501
    assert item.item_id == 100
    assert item.quality == 5
    assert item.value == 12345
    assert item.local_index == 12


def test_grid_items_with_distinct_runtime_keep_same_catalog_item_separate() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(runtime_id=501, item_id=100, shape_key="22"),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(runtime_id=502, item_id=100, shape_key="22"),
        sequence=2,
    )

    assert len(state.grid_items) == 2
    assert [item.runtime_id for item in state.grid_items] == [501, 502]


def test_same_shape_without_runtime_or_local_index_keeps_observations_separate() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(item_id=100, shape_key="22"),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(item_id=101, shape_key="22"),
        sequence=2,
    )

    assert len(state.grid_items) == 2
    assert [item.item_id for item in state.grid_items] == [100, 101]


def test_item_id_only_does_not_become_a_physical_merge_key() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(item_id=100),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(item_id=100),
        sequence=2,
    )

    assert len(state.grid_items) == 2


def test_local_index_and_valid_shape_can_enrich_without_runtime() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(quality=4, shape_key="22", local_index=12),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(
            item_id=100,
            quality=None,
            shape_key="22",
            value=54321,
            local_index=12,
        ),
        sequence=2,
    )

    assert len(state.grid_items) == 1
    item = state.grid_items[0]
    assert item.item_id == 100
    assert item.quality == 4
    assert item.value == 54321


def test_invalid_shape_does_not_enable_local_footprint_merge() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(item_id=100, shape_key="invalid", local_index=12),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(item_id=101, shape_key="invalid", local_index=12),
        sequence=2,
    )

    assert len(state.grid_items) == 2


def test_duplicate_out_of_order_cumulative_items_preserve_identity_and_unknowns() -> None:
    unknown = _packet_grid_item(
        runtime_id=501,
        quality=None,
        shape_key="22",
        value=None,
        local_index=12,
    )
    other = _packet_grid_item(
        runtime_id=502,
        item_id=101,
        quality=4,
        shape_key="11",
        value=22222,
        local_index=20,
    )
    state = _apply_grid_items(LiveSessionState(), unknown, sequence=10)
    state = _apply_grid_items(state, other, sequence=30)
    state = _apply_grid_items(state, unknown, sequence=20)
    state = _apply_grid_items(state, unknown, other, sequence=40)

    assert len(state.grid_items) == 2
    by_runtime = {item.runtime_id: item for item in state.grid_items}
    assert by_runtime[501].quality is None
    assert by_runtime[501].value is None
    assert by_runtime[502].quality == 4
    assert by_runtime[502].value == 22222


def test_settlement_batch_preserves_distinct_same_shape_inventory_truth() -> None:
    state = _apply_grid_items(
        LiveSessionState(),
        _packet_grid_item(runtime_id=1, item_id=100, shape_key="22"),
        sequence=1,
    )
    state = _apply_grid_items(
        state,
        _packet_grid_item(runtime_id=601, item_id=100, shape_key="22"),
        _packet_grid_item(runtime_id=602, item_id=100, shape_key="22"),
        phase="settled",
        sequence=2,
    )

    assert state.phase == "settled"
    assert len(state.grid_items) == 2
    assert [item.runtime_id for item in state.grid_items] == [601, 602]


def test_reducer_prefers_packet_over_manual_and_ocr() -> None:
    state = LiveSessionState()
    state = apply_observation_batch(
        state,
        LiveObservationBatch(
            source="ocr",
            field_updates=(
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=120,
                    source="ocr",
                    confidence="medium",
                    sequence=1,
                ),
            ),
        ),
    )
    state = apply_observation_batch(
        state,
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=123,
                    source="manual",
                    confidence="high",
                    sequence=2,
                ),
            ),
        ),
    )
    state = apply_observation_batch(
        state,
        LiveObservationBatch(
            source="packet",
            field_updates=(
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=126,
                    source="packet",
                    confidence="exact",
                    sequence=3,
                ),
            ),
        ),
    )

    field = state.fields[("session", "warehouse_total_cells")]
    assert field.value == 126
    assert field.source == "packet"
    assert state.version == 3
    assert state.dirty is True


def test_reducer_same_source_newer_sequence_wins() -> None:
    state = LiveSessionState()
    for seq, value in ((1, 60), (2, 72)):
        state = apply_observation_batch(
            state,
            LiveObservationBatch(
                source="packet",
                field_updates=(
                    FieldUpdate(
                        path=("session", "warehouse_total_cells"),
                        value=value,
                        source="packet",
                        confidence="exact",
                        sequence=seq,
                    ),
                ),
            ),
        )

    assert state.fields[("session", "warehouse_total_cells")].value == 72


def test_reducer_lower_priority_cannot_overwrite_higher_priority() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="packet",
            field_updates=(
                FieldUpdate(
                    path=("session", "map_id"),
                    value=2401,
                    source="packet",
                    confidence="exact",
                    sequence=1,
                ),
            ),
        ),
    )
    state = apply_observation_batch(
        state,
        LiveObservationBatch(
            source="ocr",
            field_updates=(
                FieldUpdate(
                    path=("session", "map_id"),
                    value=2402,
                    source="ocr",
                    confidence="high",
                    sequence=99,
                ),
            ),
        ),
    )

    assert state.fields[("session", "map_id")].value == 2401


def test_live_state_to_session_obs_builds_inference_observation() -> None:
    state = LiveSessionState()
    state = apply_observation_batch(
        state,
        LiveObservationBatch(
            source="manual",
            phase="reading",
            field_updates=(
                FieldUpdate(
                    path=("session", "map_id"),
                    value=2401,
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("session", "hero"),
                    value="ethan",
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=60,
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("bucket", "4", "avg_cells"),
                    value="2.50",
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("bucket", "4", "value_sum"),
                    value=86_490,
                    source="manual",
                    confidence="high",
                ),
                FieldUpdate(
                    path=("bucket", "5", "total_cells"),
                    value=30,
                    source="packet",
                    confidence="exact",
                ),
            ),
        ),
    )

    obs = live_state_to_session_obs(state)

    assert obs is not None
    assert obs.map_id == 2401
    assert obs.hero == "ethan"
    assert obs.warehouse_capacity() == 60
    assert obs.buckets[4].avg_cells is not None
    assert obs.buckets[4].avg_cells.raw == "2.50"
    assert obs.buckets[4].value_sum == 86_490
    assert obs.buckets[5].total_cells == 30


def test_mark_ready_clears_dirty_without_changing_version() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("session", "hero"),
                    value="ethan",
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    ready = mark_ready(state)

    assert ready.version == state.version
    assert ready.dirty is False


def test_live_inference_status_orders_running_dirty_ready() -> None:
    dirty = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            event_kind="manual_update",
            field_updates=(
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=123,
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    ready = mark_ready(dirty)

    assert live_inference_status(dirty, worker_running=True, has_result=False) == "running"
    assert live_inference_status(dirty, worker_running=False, has_result=True) == "dirty"
    assert live_inference_status(ready, worker_running=False, has_result=True) == "ready"
    assert live_inference_status(None, worker_running=False, has_result=False) == "idle"
    assert (
        live_inference_status(
            ready,
            worker_running=False,
            has_result=True,
            has_error=True,
        )
        == "error"
    )


def test_summarize_field_sources_returns_stable_debug_rows() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("session", "hero"),
                    value="ethan",
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("bucket", "4", "avg_cells"),
                    value="2.90",
                    source="ocr",
                    confidence="high",
                ),
            ),
        ),
    )

    rows = summarize_field_sources(state)

    assert rows == (
        {
            "field": "bucket.4.avg_cells",
            "value": "2.90",
            "source": "ocr",
            "confidence": "high",
        },
        {
            "field": "session.hero",
            "value": "ethan",
            "source": "manual",
            "confidence": "exact",
        },
    )


def test_summarize_selected_field_sources_uses_labels_and_skips_missing() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="packet",
            field_updates=(
                FieldUpdate(
                    path=("session", "warehouse_total_cells"),
                    value=90,
                    source="packet",
                    confidence="exact",
                ),
            ),
        ),
    )

    rows = summarize_selected_field_sources(
        state,
        {
            "仓库总格": ("session", "warehouse_total_cells"),
            "紫品总价": ("bucket", "4", "value_sum"),
        },
    )

    assert rows == (
        {
            "field": "仓库总格",
            "value": "90",
            "source": "packet",
            "confidence": "exact",
        },
    )


def test_summarize_blocked_field_updates_reports_lower_priority_attempts() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("bucket", "4", "value_sum"),
                    value=86_490,
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    batch = LiveObservationBatch(
        source="ocr",
        field_updates=(
            FieldUpdate(
                path=("bucket", "4", "value_sum"),
                value=80_000,
                source="ocr",
                confidence="high",
            ),
        ),
    )

    rows = summarize_blocked_field_updates(state, batch)
    updated = apply_observation_batch(state, batch)

    assert rows == (
        {
            "field": "bucket.4.value_sum",
            "attempted_value": "80000",
            "attempted_source": "ocr",
            "kept_value": "86490",
            "kept_source": "manual",
            "reason": "lower_priority_source",
        },
    )
    assert updated.fields[("bucket", "4", "value_sum")].value == 86_490


def test_legacy_obs_estimated_warehouse_maps_to_approx_fields() -> None:
    fields = legacy_obs_fields(
        {
            "map_id": 2510,
            "hero": "ethan",
            "warehouse_cells": 112,
            "warehouse_cells_mode": "estimate",
            "warehouse_cells_tolerance": 9,
        }
    )

    assert ("session", "warehouse_total_cells") not in fields
    assert fields[("session", "warehouse_total_cells_approx")] == 112
    assert fields[("session", "warehouse_total_cells_tolerance")] == 9


def test_live_context_matches_approximate_warehouse_value() -> None:
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            event_kind="manual_update",
            field_updates=(
                FieldUpdate(
                    path=("session", "map_id"),
                    value=2510,
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("session", "hero"),
                    value="ethan",
                    source="manual",
                    confidence="exact",
                ),
                FieldUpdate(
                    path=("session", "warehouse_total_cells_approx"),
                    value=112,
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    session = live_state_to_session_obs(state)

    assert live_session_matches_context(
        session,
        map_id=2510,
        warehouse_total_cells=112,
    )


def test_heartbeat_metadata_update_does_not_make_inference_stale() -> None:
    ready = mark_ready(
        apply_observation_batch(
            LiveSessionState(),
            LiveObservationBatch(
                source="manual",
                field_updates=(
                    FieldUpdate(
                        path=("session", "hero"),
                        value="ethan",
                        source="manual",
                        confidence="exact",
                    ),
                ),
            ),
        )
    )

    heartbeat = apply_observation_batch(
        ready,
        LiveObservationBatch(
            source="packet",
            event_kind="heartbeat",
            field_updates=(
                FieldUpdate(
                    path=("transport", "last_seen_ms"),
                    value=1234,
                    source="packet",
                    confidence="exact",
                ),
            ),
        ),
    )

    assert heartbeat.version == ready.version + 1
    assert heartbeat.dirty is False
