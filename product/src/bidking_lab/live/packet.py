"""Packet/protohub fixture adapter for live observations.

This module intentionally parses plain dictionaries, not a specific capture
transport. ProtoHub / pcap tooling can first dump safe JSON fixtures, then
feed them here before any live listener is introduced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bidking_lab.live.types import (
    AuctionPhase,
    FieldUpdate,
    GridItemObservation,
    LiveEventKind,
    LiveObservationBatch,
    SourceConfidence,
)


_EVENT_ALIASES: dict[str, LiveEventKind] = {
    "manual_update": "manual_update",
    "ocr_update": "ocr_update",
    "session_started": "session_started",
    "round_changed": "round_changed",
    "tool_revealed": "tool_revealed",
    "public_info_changed": "public_info_changed",
    "session_settled": "session_settled",
    "heartbeat": "heartbeat",
    "round": "round_changed",
    "tool": "tool_revealed",
    "public": "public_info_changed",
    "state": "public_info_changed",
}

_PHASE_ALIASES: dict[str, AuctionPhase] = {
    "unknown": "unknown",
    "map_select": "map_select",
    "reading": "reading",
    "bidding": "bidding",
    "settled": "settled",
    "round": "bidding",
}

_SESSION_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("map_id", ("session", "map_id")),
    ("hero", ("session", "hero")),
    ("warehouse_total_cells", ("session", "warehouse_total_cells")),
    ("warehouse_cells", ("session", "warehouse_total_cells")),
    ("warehouse_total_cells_approx", ("session", "warehouse_total_cells_approx")),
    ("warehouse_cells_approx", ("session", "warehouse_total_cells_approx")),
    ("warehouse_estimated_cells", ("session", "warehouse_total_cells_approx")),
    ("warehouse_total_cells_tolerance", ("session", "warehouse_total_cells_tolerance")),
    ("warehouse_cells_tolerance", ("session", "warehouse_total_cells_tolerance")),
    ("warehouse_estimate_tolerance", ("session", "warehouse_total_cells_tolerance")),
    ("total_item_count", ("session", "total_item_count")),
    ("round", ("session", "round")),
    ("round_index", ("session", "round")),
)

_BUCKET_KEYS: tuple[tuple[str, str], ...] = (
    ("total_cells", "total_cells"),
    ("cells", "total_cells"),
    ("count", "count"),
    ("item_count", "count"),
    ("value_sum", "value_sum"),
    ("total_value", "value_sum"),
    ("avg_cells", "avg_cells"),
    ("avg_value", "avg_value"),
    ("huge_band", "huge_band"),
    ("huge_cells_override", "huge_cells_override"),
    ("value_range", "value_range"),
)


def _first_mapping(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_kind(payload: Mapping[str, Any]) -> LiveEventKind:
    raw = str(
        payload.get("event_kind")
        or payload.get("event")
        or payload.get("type")
        or ""
    ).strip().lower()
    if raw in _EVENT_ALIASES:
        return _EVENT_ALIASES[raw]
    if "round" in payload or "round_index" in payload:
        return "round_changed"
    if payload.get("tool") or payload.get("tools"):
        return "tool_revealed"
    if payload.get("heartbeat"):
        return "heartbeat"
    return "public_info_changed"


def _phase(payload: Mapping[str, Any]) -> AuctionPhase:
    raw = str(payload.get("phase") or "").strip().lower()
    return _PHASE_ALIASES.get(raw, "unknown")


def _confidence(payload: Mapping[str, Any]) -> SourceConfidence:
    raw = str(payload.get("confidence") or "exact").strip().lower()
    if raw in ("low", "medium", "high", "exact"):
        return raw  # type: ignore[return-value]
    return "exact"


def _sequence(payload: Mapping[str, Any]) -> int | None:
    return _as_int(payload.get("sequence") or payload.get("seq"))


def _observed_at_ms(payload: Mapping[str, Any]) -> int | None:
    return _as_int(payload.get("observed_at_ms") or payload.get("timestamp_ms"))


def _add_update(
    updates: list[FieldUpdate],
    path: tuple[str, ...],
    value: Any,
    *,
    confidence: SourceConfidence,
    sequence: int | None,
    observed_at_ms: int | None,
) -> None:
    if value is None or value == "":
        return
    if path[-1] == "value_range":
        value = _value_range(value)
        if value is None:
            return
    updates.append(
        FieldUpdate(
            path=path,
            value=value,
            source="packet",
            confidence=confidence,
            sequence=sequence,
            observed_at_ms=observed_at_ms,
        )
    )


def _value_range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            return None
        lo = _as_int(value[0])
        hi = _as_int(value[1])
        if lo is not None and hi is not None and hi >= lo:
            return (lo, hi)
    if isinstance(value, Mapping):
        lo = _as_int(value.get("lo") or value.get("min"))
        hi = _as_int(value.get("hi") or value.get("max"))
        if lo is not None and hi is not None and hi >= lo:
            return (lo, hi)
    return None


def _packet_session_updates(
    payload: Mapping[str, Any],
    *,
    confidence: SourceConfidence,
    sequence: int | None,
    observed_at_ms: int | None,
) -> list[FieldUpdate]:
    session = _first_mapping(payload, "session", "game", "state")
    merged: dict[str, Any] = dict(session)
    for key in (
        "map_id",
        "hero",
        "warehouse_total_cells",
        "warehouse_cells",
        "warehouse_total_cells_approx",
        "warehouse_cells_approx",
        "warehouse_estimated_cells",
        "warehouse_total_cells_tolerance",
        "warehouse_cells_tolerance",
        "warehouse_estimate_tolerance",
        "total_item_count",
        "round",
        "round_index",
    ):
        if key in payload:
            merged[key] = payload[key]

    updates: list[FieldUpdate] = []
    for key, path in _SESSION_KEYS:
        if key in merged:
            _add_update(
                updates,
                path,
                merged[key],
                confidence=confidence,
                sequence=sequence,
                observed_at_ms=observed_at_ms,
            )
    return updates


def _bucket_quality(raw_key: Any, raw_bucket: Mapping[str, Any]) -> int | None:
    if "quality" in raw_bucket:
        parsed = _as_int(raw_bucket.get("quality"))
        if parsed is not None:
            return parsed
    return _as_int(raw_key)


def _packet_bucket_updates(
    payload: Mapping[str, Any],
    *,
    confidence: SourceConfidence,
    sequence: int | None,
    observed_at_ms: int | None,
) -> list[FieldUpdate]:
    buckets = _first_mapping(payload, "buckets", "qualities", "quality_buckets")
    updates: list[FieldUpdate] = []
    for raw_quality, raw_bucket in buckets.items():
        if not isinstance(raw_bucket, Mapping):
            continue
        quality = _bucket_quality(raw_quality, raw_bucket)
        if quality is None:
            continue
        for source_key, field_key in _BUCKET_KEYS:
            if source_key not in raw_bucket:
                continue
            _add_update(
                updates,
                ("bucket", str(quality), field_key),
                raw_bucket[source_key],
                confidence=confidence,
                sequence=sequence,
                observed_at_ms=observed_at_ms,
            )
    return updates


def _item_cells(raw: Mapping[str, Any]) -> int | None:
    for key in ("cells", "cell_count", "size"):
        parsed = _as_int(raw.get(key))
        if parsed is not None:
            return parsed
    shape = raw.get("shape_key") or raw.get("shape")
    if isinstance(shape, str) and "x" in shape.lower():
        parts = shape.lower().split("x", 1)
        left = _as_int(parts[0])
        right = _as_int(parts[1])
        if left is not None and right is not None:
            return left * right
    return None


def _iter_item_mappings(payload: Mapping[str, Any]) -> Sequence[Any]:
    for key in ("grid_items", "items", "public_items", "visible_items"):
        value = payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return value
    return ()


def _packet_grid_items(
    payload: Mapping[str, Any],
    *,
    confidence: SourceConfidence,
    observed_at_ms: int | None,
) -> list[GridItemObservation]:
    items: list[GridItemObservation] = []
    for raw in _iter_item_mappings(payload):
        if not isinstance(raw, Mapping):
            continue
        cells = _item_cells(raw)
        if cells is None or cells <= 0:
            continue
        items.append(
            GridItemObservation(
                cells=cells,
                source="packet",
                confidence=confidence,
                item_id=_as_int(raw.get("item_id") or raw.get("id")),
                quality=_as_int(raw.get("quality")),
                shape_key=raw.get("shape_key") or raw.get("shape"),
                value=_as_int(raw.get("value")),
                observed_at_ms=observed_at_ms,
            )
        )
    return items


def live_batch_from_packet_fixture(payload: Mapping[str, Any]) -> LiveObservationBatch:
    """Convert a protohub/packet JSON-like fixture into a live observation batch."""
    confidence = _confidence(payload)
    sequence = _sequence(payload)
    observed_at_ms = _observed_at_ms(payload)
    updates = [
        *_packet_session_updates(
            payload,
            confidence=confidence,
            sequence=sequence,
            observed_at_ms=observed_at_ms,
        ),
        *_packet_bucket_updates(
            payload,
            confidence=confidence,
            sequence=sequence,
            observed_at_ms=observed_at_ms,
        ),
    ]
    return LiveObservationBatch(
        source="packet",
        event_kind=_event_kind(payload),
        phase=_phase(payload),
        field_updates=tuple(updates),
        grid_items=tuple(
            _packet_grid_items(
                payload,
                confidence=confidence,
                observed_at_ms=observed_at_ms,
            )
        ),
        sequence=sequence,
        observed_at_ms=observed_at_ms,
    )


__all__ = ("live_batch_from_packet_fixture",)
