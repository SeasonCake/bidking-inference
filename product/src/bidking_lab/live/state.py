"""Reducer and adapter for live observation batches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from typing import Literal

from bidking_lab.inference.display import Reading, parse_reading
from bidking_lab.inference.observation import (
    CategoryItemObservation,
    HUGE_BAND_RANGE,
    QualityBucketObs,
    SessionObs,
)
from bidking_lab.live.types import (
    AuctionPhase,
    FieldUpdate,
    GridItemObservation,
    LiveObservationBatch,
    ObservationSource,
    SourceConfidence,
    event_requests_recompute,
    source_priority,
)


_CONFIDENCE_PRIORITY: dict[SourceConfidence, int] = {
    "low": 10,
    "medium": 20,
    "high": 30,
    "exact": 40,
}

LiveInferenceStatus = Literal["idle", "dirty", "running", "ready", "error"]


@dataclass(frozen=True)
class ObservedField:
    """Current winning value for one logical field path."""

    value: Any
    source: ObservationSource
    confidence: SourceConfidence
    observed_at_ms: int | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class LiveSessionState:
    """Merged realtime observations before conversion to ``SessionObs``."""

    phase: AuctionPhase = "unknown"
    fields: dict[tuple[str, ...], ObservedField] = field(default_factory=dict)
    grid_items: tuple[GridItemObservation, ...] = ()
    version: int = 0
    dirty: bool = False


def _observed_from_update(update: FieldUpdate) -> ObservedField:
    return ObservedField(
        value=update.value,
        source=update.source,
        confidence=update.confidence,
        observed_at_ms=update.observed_at_ms,
        sequence=update.sequence,
    )


def _is_newer(new: ObservedField, old: ObservedField) -> bool:
    if new.sequence is not None and old.sequence is not None:
        return new.sequence >= old.sequence
    if new.observed_at_ms is not None and old.observed_at_ms is not None:
        return new.observed_at_ms >= old.observed_at_ms
    return True


def should_replace_field(old: ObservedField | None, new: ObservedField) -> bool:
    """Return whether ``new`` should replace ``old`` for one field path."""
    if old is None:
        return True

    new_source = source_priority(new.source)
    old_source = source_priority(old.source)
    if new_source != old_source:
        return new_source > old_source

    if _is_newer(new, old):
        return True

    new_conf = _CONFIDENCE_PRIORITY[new.confidence]
    old_conf = _CONFIDENCE_PRIORITY[old.confidence]
    return new_conf > old_conf


def _first_not_none(primary: Any, fallback: Any) -> Any:
    return primary if primary is not None else fallback


def _grid_item_key(item: GridItemObservation) -> tuple[Any, ...] | None:
    if item.runtime_id is not None:
        return ("runtime", item.runtime_id)
    if item.local_index is not None and item.footprint() is not None:
        return ("footprint", item.local_index, item.shape_key)
    return None


def _merge_grid_item(
    old: GridItemObservation,
    new: GridItemObservation,
) -> GridItemObservation:
    categories = tuple(
        sorted(
            {
                category
                for category in (
                    *old.categories,
                    old.category,
                    *new.categories,
                    new.category,
                )
                if category is not None
            }
        )
    )
    excluded_categories = tuple(
        sorted(set((*old.excluded_categories, *new.excluded_categories)))
    )
    excluded_qualities = tuple(
        sorted(set((*old.excluded_qualities, *new.excluded_qualities)))
    )
    old_footprint = old.footprint()
    new_footprint = new.footprint()
    old_shape_cells = (
        old_footprint.width * old_footprint.height
        if old_footprint is not None
        else None
    )
    new_shape_cells = (
        new_footprint.width * new_footprint.height
        if new_footprint is not None
        else None
    )
    new_shape_is_consistent = (
        new_shape_cells is not None and new.cells == new_shape_cells
    )
    footprint_conflict = False
    if (
        old_footprint is not None
        and new_footprint is not None
        and new_shape_is_consistent
        and old_footprint != new_footprint
    ):
        # Two self-consistent physical observations for one runtime disagree.
        # Keep the earlier exact footprint and surface reduced confidence rather
        # than silently moving/resizing the item.  A marker without a footprint
        # is still allowed to be enriched by a later real shape below.
        footprint_conflict = True
        shape_key = old.shape_key
        local_index = old.local_index
        cells = old_shape_cells
    elif new.shape_key is not None and new_shape_is_consistent:
        shape_key = new.shape_key
        local_index = new.local_index
        cells = new_shape_cells
    elif old.shape_key is not None and old_shape_cells is not None:
        footprint_conflict = new.shape_key is not None
        shape_key = old.shape_key
        local_index = old.local_index
        cells = old_shape_cells
    else:
        shape_key = None
        local_index = _first_not_none(new.local_index, old.local_index)
        cells = new.cells
    return GridItemObservation(
        cells=cells,
        source=new.source,
        confidence="low" if footprint_conflict else new.confidence,
        runtime_id=_first_not_none(new.runtime_id, old.runtime_id),
        item_id=_first_not_none(new.item_id, old.item_id),
        quality=_first_not_none(new.quality, old.quality),
        shape_key=shape_key,
        value=_first_not_none(new.value, old.value),
        local_index=local_index,
        category=categories[0] if categories else None,
        categories=categories,
        excluded_categories=excluded_categories,
        excluded_qualities=excluded_qualities,
        observed_at_ms=_first_not_none(new.observed_at_ms, old.observed_at_ms),
    )


def merge_grid_items(
    current: tuple[GridItemObservation, ...],
    incoming: tuple[GridItemObservation, ...],
) -> tuple[GridItemObservation, ...]:
    """Merge incremental reveal items without discarding earlier knowledge."""
    if not current:
        return tuple(incoming)
    merged = list(current)
    index_by_key = {
        key: index
        for index, item in enumerate(merged)
        if (key := _grid_item_key(item)) is not None
    }
    for item in incoming:
        key = _grid_item_key(item)
        index = index_by_key.get(key) if key is not None else None
        if index is None:
            merged.append(item)
            if key is not None:
                index_by_key[key] = len(merged) - 1
            continue
        merged[index] = _merge_grid_item(merged[index], item)
    return tuple(merged)


def apply_observation_batch(
    state: LiveSessionState,
    batch: LiveObservationBatch,
) -> LiveSessionState:
    """Merge one observation batch into live state."""
    fields = dict(state.fields)
    changed = False

    for update in batch.field_updates:
        new_field = _observed_from_update(update)
        old_field = fields.get(update.path)
        if should_replace_field(old_field, new_field):
            if old_field != new_field:
                changed = True
            fields[update.path] = new_field

    grid_items = state.grid_items
    if batch.grid_items:
        grid_items = (
            tuple(batch.grid_items)
            if batch.phase == "settled"
            else merge_grid_items(grid_items, tuple(batch.grid_items))
        )
        if grid_items != state.grid_items:
            changed = True

    phase = state.phase
    if batch.phase != "unknown" and batch.phase != state.phase:
        phase = batch.phase
        changed = True

    if not changed:
        return state
    return LiveSessionState(
        phase=phase,
        fields=fields,
        grid_items=grid_items,
        version=state.version + 1,
        dirty=state.dirty or event_requests_recompute(batch.event_kind),
    )


def mark_ready(state: LiveSessionState) -> LiveSessionState:
    """Mark current state as having a fresh inference result."""
    if not state.dirty:
        return state
    return replace(state, dirty=False)


def live_inference_status(
    state: LiveSessionState | None,
    *,
    worker_running: bool,
    has_result: bool,
    has_error: bool = False,
) -> LiveInferenceStatus:
    """Summarize live inference freshness for UI state displays."""
    if has_error:
        return "error"
    if worker_running:
        return "running"
    if state is not None and state.dirty:
        return "dirty"
    if has_result:
        return "ready"
    return "idle"


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def summarize_field_sources(
    state: LiveSessionState,
    *,
    limit: int = 20,
) -> tuple[dict[str, Any], ...]:
    """Return compact rows for UI/debug display of field provenance."""
    rows: list[dict[str, Any]] = []
    for path, observed in sorted(state.fields.items()):
        rows.append(
            {
                "field": ".".join(path),
                "value": _display_value(observed.value),
                "source": observed.source,
                "confidence": observed.confidence,
            }
        )
        if len(rows) >= limit:
            break
    return tuple(rows)


def summarize_selected_field_sources(
    state: LiveSessionState,
    path_labels: Mapping[str, tuple[str, ...]],
    *,
    limit: int = 20,
) -> tuple[dict[str, Any], ...]:
    """Return source rows for a selected set of logical paths."""
    rows: list[dict[str, Any]] = []
    for label, path in path_labels.items():
        observed = state.fields.get(path)
        if observed is None:
            continue
        rows.append(
            {
                "field": label,
                "value": _display_value(observed.value),
                "source": observed.source,
                "confidence": observed.confidence,
            }
        )
        if len(rows) >= limit:
            break
    return tuple(rows)


def summarize_blocked_field_updates(
    state: LiveSessionState,
    batch: LiveObservationBatch,
    *,
    limit: int = 20,
) -> tuple[dict[str, Any], ...]:
    """Return attempted updates that would not replace current fields."""
    rows: list[dict[str, Any]] = []
    for update in batch.field_updates:
        old_field = state.fields.get(update.path)
        if old_field is None:
            continue
        new_field = _observed_from_update(update)
        if should_replace_field(old_field, new_field):
            continue
        if source_priority(new_field.source) < source_priority(old_field.source):
            reason = "lower_priority_source"
        else:
            reason = "older_or_lower_confidence"
        rows.append(
            {
                "field": ".".join(update.path),
                "attempted_value": _display_value(new_field.value),
                "attempted_source": new_field.source,
                "kept_value": _display_value(old_field.value),
                "kept_source": old_field.source,
                "reason": reason,
            }
        )
        if len(rows) >= limit:
            break
    return tuple(rows)


def _field_value(state: LiveSessionState, path: tuple[str, ...]) -> Any:
    field_value = state.fields.get(path)
    if field_value is None:
        return None
    return field_value.value


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_reading(value: Any) -> Reading | None:
    if value is None or value == "":
        return None
    if isinstance(value, Reading):
        return value
    try:
        return parse_reading(str(value))
    except ValueError:
        return None


def _bucket_from_fields(
    state: LiveSessionState,
    quality: int,
) -> QualityBucketObs | None:
    root = ("bucket", str(quality))
    total_cells = _as_int(_field_value(state, root + ("total_cells",)))
    count = _as_int(_field_value(state, root + ("count",)))
    value_sum = _as_int(_field_value(state, root + ("value_sum",)))
    avg_value = _as_float(_field_value(state, root + ("avg_value",)))
    avg_cells = _as_reading(_field_value(state, root + ("avg_cells",)))
    huge_band_raw = _field_value(state, root + ("huge_band",)) or "none"
    huge_band = huge_band_raw if huge_band_raw in HUGE_BAND_RANGE else "none"
    huge_cells_override = _as_int(
        _field_value(state, root + ("huge_cells_override",))
    ) or 0
    value_range = _field_value(state, root + ("value_range",))

    has_any = (
        total_cells is not None
        or count is not None
        or (value_sum is not None and value_sum > 0)
        or avg_value is not None
        or avg_cells is not None
        or huge_band != "none"
        or value_range is not None
    )
    if not has_any:
        return None

    return QualityBucketObs(
        quality=quality,
        avg_cells=avg_cells,
        total_cells=total_cells,
        count=count,
        value_sum=value_sum if value_sum is not None and value_sum > 0 else None,
        avg_value=avg_value,
        value_range=value_range,
        huge_band=huge_band,
        huge_cells_override=huge_cells_override,
    )


def _merge_grid_item_bucket_mins(
    buckets: dict[int, QualityBucketObs],
    grid_items: tuple[GridItemObservation, ...],
) -> dict[int, QualityBucketObs]:
    """Add per-quality lower bounds from known-quality visible outlines."""
    if not grid_items:
        return buckets

    cells_by_quality: dict[int, int] = {}
    count_by_quality: dict[int, int] = {}
    for item in grid_items:
        if item.quality is None:
            continue
        cells_by_quality[item.quality] = (
            cells_by_quality.get(item.quality, 0) + item.cells
        )
        count_by_quality[item.quality] = count_by_quality.get(item.quality, 0) + 1

    if not cells_by_quality:
        return buckets

    merged = dict(buckets)
    for quality, cells_min in cells_by_quality.items():
        count_min = count_by_quality[quality]
        bucket = merged.get(quality)
        if bucket is None:
            merged[quality] = QualityBucketObs(
                quality=quality,
                total_cells_min=cells_min,
                count_min=count_min,
            )
            continue
        merged[quality] = replace(
            bucket,
            total_cells_min=max(bucket.total_cells_min or 0, cells_min),
            count_min=max(bucket.count_min or 0, count_min),
        )
    return merged


def live_state_to_session_obs(state: LiveSessionState) -> SessionObs | None:
    """Convert live state into inference ``SessionObs`` when possible."""
    map_id = _as_int(_field_value(state, ("session", "map_id")))
    hero = _field_value(state, ("session", "hero"))
    if map_id is None or not hero:
        return None

    buckets: dict[int, QualityBucketObs] = {}
    for quality in (1, 2, 3, 4, 5, 6):
        bucket = _bucket_from_fields(state, quality)
        if bucket is not None:
            buckets[quality] = bucket
    visible_outline_items = tuple(state.grid_items)
    buckets = _merge_grid_item_bucket_mins(buckets, visible_outline_items)
    warehouse_total_cells = _as_int(
        _field_value(state, ("session", "warehouse_total_cells")),
    )
    _fill_residual_red_bucket(buckets, warehouse_total_cells)
    unknown_outline_items = tuple(
        item for item in visible_outline_items
        if item.quality is None
    )
    category_items = tuple(
        CategoryItemObservation(
            category=item.category,
            cells=item.cells,
            shape_key=item.shape_key,
            quality=item.quality,
            item_id=item.item_id,
        )
        for item in visible_outline_items
        if item.category is not None
    )
    footprints = tuple(
        footprint for item in visible_outline_items
        if (footprint := item.footprint()) is not None
    )

    return SessionObs(
        map_id=map_id,
        hero=hero,
        warehouse_total_cells=warehouse_total_cells,
        warehouse_total_cells_approx=_as_int(
            _field_value(state, ("session", "warehouse_total_cells_approx")),
        ),
        warehouse_total_cells_tolerance=_as_int(
            _field_value(state, ("session", "warehouse_total_cells_tolerance")),
        ),
        total_item_count=_as_int(
            _field_value(state, ("session", "total_item_count")),
        ),
        visible_outline_item_count_min=(
            len(visible_outline_items) if visible_outline_items else None
        ),
        visible_outline_total_cells_min=(
            sum(item.cells for item in visible_outline_items)
            if visible_outline_items
            else None
        ),
        visible_outline_bottom_row_min=(
            max(footprint.bottom_row for footprint in footprints)
            if footprints
            else None
        ),
        unknown_outline_item_count=(
            len(unknown_outline_items) if unknown_outline_items else None
        ),
        unknown_outline_total_cells=(
            sum(item.cells for item in unknown_outline_items)
            if unknown_outline_items
            else None
        ),
        category_items=category_items,
        buckets=buckets,
    )


def _fill_residual_red_bucket(
    buckets: dict[int, QualityBucketObs],
    warehouse_total_cells: int | None,
) -> None:
    if 6 in buckets or warehouse_total_cells is None or warehouse_total_cells <= 0:
        return
    required_qs = {1, 3, 4, 5}
    if 2 in buckets:
        required_qs.add(2)
    if not required_qs.issubset(buckets):
        return

    if any(buckets[q].total_cells is None for q in required_qs):
        return

    explicit_sum = sum(
        b.total_cells for b in buckets.values()
        if b.total_cells is not None and b.total_cells > 0
    )
    known_sum = explicit_sum
    if known_sum <= 0:
        return
    red_residual = warehouse_total_cells - known_sum
    if red_residual == 0:
        buckets[6] = QualityBucketObs(
            quality=6,
            total_cells=0,
            count=0,
            huge_band="none",
        )
    elif red_residual > 0:
        buckets[6] = QualityBucketObs(quality=6, total_cells=red_residual)


def live_session_matches_context(
    session: SessionObs | None,
    *,
    map_id: Any,
    warehouse_total_cells: Any,
) -> bool:
    """Return whether a live-derived session matches current UI context."""
    if session is None:
        return False
    if _as_int(map_id) != session.map_id:
        return False
    warehouse = _as_int(warehouse_total_cells)
    if warehouse is not None and warehouse > 0:
        if session.warehouse_total_cells is not None:
            return session.warehouse_total_cells == warehouse
        return session.warehouse_total_cells_approx == warehouse
    return True


__all__ = (
    "LiveSessionState",
    "ObservedField",
    "apply_observation_batch",
    "live_inference_status",
    "live_state_to_session_obs",
    "live_session_matches_context",
    "mark_ready",
    "should_replace_field",
    "summarize_blocked_field_updates",
    "summarize_field_sources",
    "summarize_selected_field_sources",
)
