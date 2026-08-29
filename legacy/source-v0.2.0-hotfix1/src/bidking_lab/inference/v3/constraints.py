"""Hard constraint compiler for v3 diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from bidking_lab.inference.v3.events import EvidenceEvent


@dataclass(frozen=True)
class HardNumericConstraint:
    target: str
    value: int | float
    event_id: str
    source_kind: str
    source_id: str
    sort_id: int | None


@dataclass(frozen=True)
class SoftNumericConstraint:
    key: str
    targets: tuple[str, ...]
    value: int | float
    event_id: str
    source_kind: str
    source_id: str
    semantic: str
    sort_id: int | None


@dataclass(frozen=True)
class ConstraintConflict:
    target: str
    first: HardNumericConstraint
    second: HardNumericConstraint


@dataclass(frozen=True)
class ItemAnchor:
    key: str
    event_id: str
    source_kind: str
    source_id: str
    sort_id: int | None
    runtime_id: int | None = None
    local_index: int | None = None
    item_id: int | None = None
    quality: int | None = None
    value: int | None = None
    shape_key: str | None = None
    cells: int | None = None
    categories: tuple[int, ...] = ()


@dataclass(frozen=True)
class ShapeAnchor:
    key: str
    event_id: str
    source_kind: str
    source_id: str
    sort_id: int | None
    shape_key: str
    cells: int
    runtime_id: int | None = None
    local_index: int | None = None
    item_id: int | None = None
    quality: int | None = None


@dataclass(frozen=True)
class QualityFloorAnchor:
    key: str
    event_id: str
    source_kind: str
    source_id: str
    sort_id: int | None
    quality: int
    runtime_id: int | None = None
    local_index: int | None = None


@dataclass
class ConstraintSet:
    numeric: dict[str, HardNumericConstraint] = field(default_factory=dict)
    item_anchor_events: list[EvidenceEvent] = field(default_factory=list)
    shape_anchor_events: list[EvidenceEvent] = field(default_factory=list)
    quality_floor_events: list[EvidenceEvent] = field(default_factory=list)
    item_anchors: dict[str, ItemAnchor] = field(default_factory=dict)
    shape_anchors: dict[str, ShapeAnchor] = field(default_factory=dict)
    quality_floor_anchors: dict[str, QualityFloorAnchor] = field(default_factory=dict)
    soft_numeric: dict[str, SoftNumericConstraint] = field(default_factory=dict)
    conflicts: list[ConstraintConflict] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not self.conflicts


def _numeric_payload_value(event: EvidenceEvent) -> int | float | None:
    payload = event.payload or {}
    value: Any
    if "value" in payload:
        value = payload["value"]
    elif "result" in payload:
        value = payload["result"]
    else:
        return None
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not numeric.is_integer():
        return numeric
    return int(numeric)


def _is_numeric_target(target: str) -> bool:
    return target.startswith("session.") or target.startswith("bucket.")


def _is_count_or_cells_target(target: str) -> bool:
    return target.endswith(".count") or target.endswith(".cells")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _shape_cells(shape_key: str | None) -> int | None:
    if shape_key in (None, ""):
        return None
    try:
        code = int(shape_key)
    except (TypeError, ValueError):
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width * height


def _anchor_key(item: dict[str, Any], *, fallback: str) -> str:
    runtime_id = _int_or_none(item.get("runtime_id"))
    if runtime_id is not None:
        return f"runtime:{runtime_id}"
    local_index = _int_or_none(item.get("local_index"))
    shape_key = item.get("shape_key")
    if local_index is not None and shape_key:
        return f"local:{local_index}:{shape_key}"
    return fallback


def _iter_payload_items(event: EvidenceEvent) -> Iterable[dict[str, Any]]:
    payload = event.payload or {}
    for raw_item in payload.get("items", ()) or ():
        if isinstance(raw_item, dict):
            yield raw_item


def _derived_shape_numeric_values(event: EvidenceEvent) -> dict[str, int]:
    target_set = set(event.targets)
    if "shape_anchors" not in target_set:
        return {}
    items = tuple(_iter_payload_items(event))
    if not items:
        return {}
    count_by_quality: dict[int, int] = {}
    cells_by_quality: dict[int, int] = {}
    total_cells = 0
    all_cells_known = True
    for item in items:
        cells = _int_or_none(item.get("cells"))
        if cells is None:
            cells = _shape_cells(item.get("shape_key"))
        if cells is None:
            all_cells_known = False
            cells = 0
        total_cells += cells
        quality = _int_or_none(item.get("quality"))
        if quality is None:
            continue
        count_by_quality[quality] = count_by_quality.get(quality, 0) + 1
        cells_by_quality[quality] = cells_by_quality.get(quality, 0) + cells
    out: dict[str, int] = {}
    for target in event.targets:
        if target == "session.total_count":
            out[target] = len(items)
        elif target == "session.total_cells" and all_cells_known:
            out[target] = total_cells
        elif target.startswith("bucket.q") and target.endswith(".count"):
            raw_quality = target.removeprefix("bucket.q").removesuffix(".count")
            quality = _int_or_none(raw_quality)
            if quality is not None:
                out[target] = count_by_quality.get(quality, 0)
        elif target.startswith("bucket.q") and target.endswith(".cells") and all_cells_known:
            raw_quality = target.removeprefix("bucket.q").removesuffix(".cells")
            quality = _int_or_none(raw_quality)
            if quality is not None:
                out[target] = cells_by_quality.get(quality, 0)
    return out


def _event_categories(event: EvidenceEvent) -> tuple[int, ...]:
    categories: list[int] = []
    for target in event.targets:
        if target.startswith("category."):
            category = _int_or_none(target.removeprefix("category."))
            if category is not None:
                categories.append(category)
    semantic = event.semantic
    if semantic.startswith("category_outline_"):
        category = _int_or_none(semantic.removeprefix("category_outline_"))
        if category is not None:
            categories.append(category)
    elif semantic.startswith("category_"):
        rest = semantic.removeprefix("category_")
        category = _int_or_none(rest.split("_", 1)[0])
        if category is not None:
            categories.append(category)
    return tuple(dict.fromkeys(categories))


def _merge_item_anchor(current: ItemAnchor, new: ItemAnchor) -> ItemAnchor:
    return ItemAnchor(
        key=current.key,
        event_id=current.event_id,
        source_kind=current.source_kind,
        source_id=current.source_id,
        sort_id=current.sort_id,
        runtime_id=current.runtime_id if current.runtime_id is not None else new.runtime_id,
        local_index=new.local_index if new.local_index is not None else current.local_index,
        item_id=current.item_id if current.item_id is not None else new.item_id,
        quality=current.quality if current.quality is not None else new.quality,
        value=current.value if current.value is not None else new.value,
        shape_key=new.shape_key if new.shape_key is not None else current.shape_key,
        cells=new.cells if new.cells is not None else current.cells,
        categories=tuple(dict.fromkeys((*current.categories, *new.categories))),
    )


def _record_item_and_shape_anchors(event: EvidenceEvent, out: ConstraintSet) -> None:
    target_set = set(event.targets)
    has_item_target = "item_anchors" in target_set or "category_anchors" in target_set
    has_shape_target = "shape_anchors" in target_set
    category_targets = [
        target.removeprefix("category.")
        for target in event.targets
        if target.startswith("category.")
    ]
    categories = tuple(
        value
        for raw in category_targets
        for value in (_int_or_none(raw),)
        if value is not None
    )
    categories = tuple(dict.fromkeys((*categories, *_event_categories(event))))
    for index, item in enumerate(_iter_payload_items(event)):
        fallback_key = f"{event.event_id}:item:{index}"
        key = _anchor_key(item, fallback=fallback_key)
        runtime_id = _int_or_none(item.get("runtime_id"))
        local_index = _int_or_none(item.get("local_index"))
        item_id = _int_or_none(item.get("item_id"))
        quality = _int_or_none(item.get("quality"))
        value = _int_or_none(item.get("value"))
        shape_key = item.get("shape_key")
        cells = _int_or_none(item.get("cells"))
        if cells is None:
            cells = _shape_cells(shape_key)
        if has_item_target and (item_id is not None or quality is not None or shape_key is not None):
            anchor = ItemAnchor(
                key=key,
                event_id=event.event_id,
                source_kind=event.source_kind,
                source_id=event.source_id,
                sort_id=event.sort_id,
                runtime_id=runtime_id,
                local_index=local_index,
                item_id=item_id,
                quality=quality,
                value=value,
                shape_key=shape_key,
                cells=cells,
                categories=categories,
            )
            current = out.item_anchors.get(key)
            out.item_anchors[key] = anchor if current is None else _merge_item_anchor(current, anchor)
        if has_shape_target and shape_key is not None and cells is not None:
            out.shape_anchors[key] = ShapeAnchor(
                key=key,
                event_id=event.event_id,
                source_kind=event.source_kind,
                source_id=event.source_id,
                sort_id=event.sort_id,
                runtime_id=runtime_id,
                local_index=local_index,
                item_id=item_id,
                quality=quality,
                shape_key=str(shape_key),
                cells=int(cells),
            )


def _record_quality_floor_anchors(event: EvidenceEvent, out: ConstraintSet) -> None:
    for index, item in enumerate(_iter_payload_items(event)):
        quality = _int_or_none(item.get("quality"))
        if quality is None:
            continue
        fallback_key = f"{event.event_id}:quality:{index}"
        key = _anchor_key(item, fallback=fallback_key)
        out.quality_floor_anchors[key] = QualityFloorAnchor(
            key=key,
            event_id=event.event_id,
            source_kind=event.source_kind,
            source_id=event.source_id,
            sort_id=event.sort_id,
            runtime_id=_int_or_none(item.get("runtime_id")),
            local_index=_int_or_none(item.get("local_index")),
            quality=quality,
        )


def _soft_numeric_key(event: EvidenceEvent) -> str:
    targets = ",".join(sorted(event.targets))
    return f"{event.source_kind}:{event.source_id}:{event.semantic}:{targets}"


def _record_soft_numeric(event: EvidenceEvent, out: ConstraintSet) -> None:
    value = _numeric_payload_value(event)
    if value is None:
        return
    if not any(_is_numeric_target(target) for target in event.targets):
        return
    key = _soft_numeric_key(event)
    current = out.soft_numeric.get(key)
    if current is not None:
        current_sort = current.sort_id if current.sort_id is not None else -1
        event_sort = event.sort_id if event.sort_id is not None else -1
        if event_sort < current_sort:
            return
    out.soft_numeric[key] = SoftNumericConstraint(
        key=key,
        targets=tuple(event.targets),
        value=value,
        event_id=event.event_id,
        source_kind=event.source_kind,
        source_id=event.source_id,
        semantic=event.semantic,
        sort_id=event.sort_id,
    )


def compile_hard_constraints(events: Iterable[EvidenceEvent]) -> ConstraintSet:
    """Compile hard v3 events into a first-pass constraint set.

    This is deliberately conservative: it compiles exact numeric constraints and
    records anchor-bearing events, but it does not yet allocate grid shapes or
    items. That later compiler must consume the anchor events directly.
    """

    out = ConstraintSet()
    for event in events:
        target_set = set(event.targets)
        if "item_anchors" in target_set or "category_anchors" in target_set:
            out.item_anchor_events.append(event)
        if "shape_anchors" in target_set:
            out.shape_anchor_events.append(event)
        if "quality_floors" in target_set:
            out.quality_floor_events.append(event)
        if "item_anchors" in target_set or "category_anchors" in target_set or "shape_anchors" in target_set:
            _record_item_and_shape_anchors(event, out)
        if "quality_floors" in target_set:
            _record_quality_floor_anchors(event, out)
        if event.strength == "soft":
            _record_soft_numeric(event, out)
        if event.strength != "hard":
            continue
        derived_values = _derived_shape_numeric_values(event)
        for target, value in derived_values.items():
            constraint = HardNumericConstraint(
                target=target,
                value=value,
                event_id=event.event_id,
                source_kind=event.source_kind,
                source_id=event.source_id,
                sort_id=event.sort_id,
            )
            current = out.numeric.get(target)
            if current is not None and current.value != value:
                out.conflicts.append(
                    ConstraintConflict(
                        target=target,
                        first=current,
                        second=constraint,
                    )
                )
                continue
            out.numeric[target] = constraint
        value = _numeric_payload_value(event)
        if value is None:
            continue
        for target in event.targets:
            if not _is_numeric_target(target):
                continue
            if target in derived_values or (
                "shape_anchors" in target_set
                and _is_count_or_cells_target(target)
            ):
                continue
            constraint = HardNumericConstraint(
                target=target,
                value=value,
                event_id=event.event_id,
                source_kind=event.source_kind,
                source_id=event.source_id,
                sort_id=event.sort_id,
            )
            current = out.numeric.get(target)
            if current is not None and current.value != value:
                out.conflicts.append(
                    ConstraintConflict(
                        target=target,
                        first=current,
                        second=constraint,
                    )
                )
                continue
            out.numeric[target] = constraint
    return out


__all__ = (
    "ConstraintConflict",
    "ConstraintSet",
    "HardNumericConstraint",
    "ItemAnchor",
    "QualityFloorAnchor",
    "ShapeAnchor",
    "SoftNumericConstraint",
    "compile_hard_constraints",
)
