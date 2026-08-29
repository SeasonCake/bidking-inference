"""Settlement truth extraction for v3 archive diagnostics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import SessionTruth
from bidking_lab.inference.v3.constraints import ConstraintSet
from bidking_lab.simulation.robust_value import (
    DEFAULT_VALUE_FLOOR,
    is_confusable_long_tail,
)


@dataclass(frozen=True)
class QualityTruthReport:
    quality: int
    count: int
    cells: int
    raw_value: int


@dataclass(frozen=True)
class SettlementTruthReport:
    session_id: str | None
    map_id: int | None
    sort_id: int | None
    item_count: int
    total_cells: int
    raw_total_value: int
    quality_truths: tuple[QualityTruthReport, ...]

    def quality(self, quality: int) -> QualityTruthReport | None:
        for truth in self.quality_truths:
            if truth.quality == quality:
                return truth
        return None

    def to_flat_dict(self, *, prefix: str = "v3_truth_") -> dict[str, Any]:
        q6 = self.quality(6)
        return {
            f"{prefix}available": True,
            f"{prefix}session_id": self.session_id,
            f"{prefix}map_id": self.map_id,
            f"{prefix}sort_id": self.sort_id,
            f"{prefix}item_count": self.item_count,
            f"{prefix}total_cells": self.total_cells,
            f"{prefix}raw_total_value": self.raw_total_value,
            f"{prefix}q6_count": q6.count if q6 is not None else 0,
            f"{prefix}q6_cells": q6.cells if q6 is not None else 0,
            f"{prefix}q6_raw_value": q6.raw_value if q6 is not None else 0,
        }


@dataclass(frozen=True)
class DecisionTruthReport:
    formal_decision_value: int
    tail_replacement_decision_value: int
    trimmed_tail_value: int
    trimmed_tail_count: int
    q6_formal_decision_value: int
    q6_tail_replacement_decision_value: int
    q6_trimmed_tail_value: int
    q6_trimmed_tail_count: int

    @property
    def tail_replacement_value(self) -> int:
        return self.tail_replacement_decision_value - self.formal_decision_value

    @property
    def q6_tail_replacement_value(self) -> int:
        return (
            self.q6_tail_replacement_decision_value
            - self.q6_formal_decision_value
        )

    def to_flat_dict(self, *, prefix: str = "v3_truth_") -> dict[str, Any]:
        return {
            f"{prefix}decision_available": True,
            f"{prefix}formal_decision_value": self.formal_decision_value,
            f"{prefix}tail_replacement_decision_value": (
                self.tail_replacement_decision_value
            ),
            f"{prefix}tail_replacement_value": self.tail_replacement_value,
            f"{prefix}trimmed_tail_value": self.trimmed_tail_value,
            f"{prefix}trimmed_tail_count": self.trimmed_tail_count,
            f"{prefix}q6_formal_decision_value": (
                self.q6_formal_decision_value
            ),
            f"{prefix}q6_tail_replacement_decision_value": (
                self.q6_tail_replacement_decision_value
            ),
            f"{prefix}q6_tail_replacement_value": (
                self.q6_tail_replacement_value
            ),
            f"{prefix}q6_trimmed_tail_value": self.q6_trimmed_tail_value,
            f"{prefix}q6_trimmed_tail_count": self.q6_trimmed_tail_count,
        }


def _latest_inventory_state(events: Any) -> Any | None:
    for state in reversed(tuple(getattr(events, "states", ()) or ())):
        if tuple(getattr(state, "inventory_items", ()) or ()):
            return state
    return None


def _exact_anchor_ids(constraints: ConstraintSet) -> set[int]:
    return {
        int(anchor.item_id)
        for anchor in constraints.item_anchors.values()
        if anchor.item_id is not None
    }


def _category_supports_item(item: Item, constraints: ConstraintSet) -> bool:
    if not item.tags:
        return False
    item_categories = set(int(category) for category in item.tags)
    for anchor in constraints.item_anchors.values():
        if any(int(category) in item_categories for category in anchor.categories):
            return True
    return False


def _item_plannable(
    item: Item,
    *,
    constraints: ConstraintSet,
    exact_anchor_ids: set[int],
) -> bool:
    if item.item_id not in exact_anchor_ids and is_confusable_long_tail(item):
        return False
    if (
        item.value >= DEFAULT_VALUE_FLOOR
        and item.item_id not in exact_anchor_ids
        and not _category_supports_item(item, constraints)
    ):
        return False
    return True


def _replacement_value(
    item: Item,
    replacement_values: Mapping[tuple[int, int, int], int],
) -> int:
    if item.shape_w <= 0 or item.shape_h <= 0:
        return 0
    return int(
        replacement_values.get(
            (int(item.quality), int(item.shape_w), int(item.shape_h)),
            0,
        )
    )


def _decision_truth_from_items(
    truth_items: tuple[Item, ...],
    *,
    constraints: ConstraintSet,
    replacement_values: Mapping[tuple[int, int, int], int],
) -> DecisionTruthReport:
    exact_ids = _exact_anchor_ids(constraints)
    formal_decision_value = 0
    replacement_decision_value = 0
    trimmed_tail_value = 0
    trimmed_tail_count = 0
    q6_formal_decision_value = 0
    q6_replacement_decision_value = 0
    q6_trimmed_tail_value = 0
    q6_trimmed_tail_count = 0
    for item in truth_items:
        value = int(item.value)
        is_q6 = int(item.quality) == 6
        if _item_plannable(item, constraints=constraints, exact_anchor_ids=exact_ids):
            formal_decision_value += value
            replacement_decision_value += value
            if is_q6:
                q6_formal_decision_value += value
                q6_replacement_decision_value += value
            continue
        trimmed_tail_value += value
        trimmed_tail_count += 1
        replacement = _replacement_value(item, replacement_values)
        replacement_decision_value += replacement
        if is_q6:
            q6_trimmed_tail_value += value
            q6_trimmed_tail_count += 1
            q6_replacement_decision_value += replacement
    return DecisionTruthReport(
        formal_decision_value=formal_decision_value,
        tail_replacement_decision_value=replacement_decision_value,
        trimmed_tail_value=trimmed_tail_value,
        trimmed_tail_count=trimmed_tail_count,
        q6_formal_decision_value=q6_formal_decision_value,
        q6_tail_replacement_decision_value=q6_replacement_decision_value,
        q6_trimmed_tail_value=q6_trimmed_tail_value,
        q6_trimmed_tail_count=q6_trimmed_tail_count,
    )


def decision_truth_from_session_truth(
    truth: SessionTruth,
    *,
    constraints: ConstraintSet,
    replacement_values: Mapping[tuple[int, int, int], int] | None = None,
) -> DecisionTruthReport:
    """Return formal/replacement decision truth for a sampled SessionTruth."""

    truth_items = tuple(
        item
        for bucket in truth.buckets.values()
        for item in bucket.items
    )
    return _decision_truth_from_items(
        truth_items,
        constraints=constraints,
        replacement_values=replacement_values or {},
    )


def decision_truth_from_fatbeans(
    events: Any,
    *,
    items: Mapping[int, Item],
    constraints: ConstraintSet,
    replacement_values: Mapping[tuple[int, int, int], int] | None = None,
) -> DecisionTruthReport | None:
    """Return formal and replacement decision truth for one pre-bid window."""

    state = _latest_inventory_state(events)
    if state is None:
        return None
    replacement_values = replacement_values or {}
    truth_items: list[Item] = []
    for inv_item in tuple(getattr(state, "inventory_items", ()) or ()):
        item_id = getattr(inv_item, "item_id", None)
        item = items.get(int(item_id)) if item_id is not None else None
        if item is None:
            continue
        truth_items.append(item)
    return _decision_truth_from_items(
        tuple(truth_items),
        constraints=constraints,
        replacement_values=replacement_values,
    )


def settlement_truth_from_fatbeans(
    events: Any,
    *,
    items: Mapping[int, Item],
) -> SettlementTruthReport | None:
    """Return raw settlement inventory truth from the latest inventory state."""

    for state in reversed(tuple(getattr(events, "states", ()) or ())):
        inventory_items = tuple(getattr(state, "inventory_items", ()) or ())
        if not inventory_items:
            continue
        counts: defaultdict[int, int] = defaultdict(int)
        cells: defaultdict[int, int] = defaultdict(int)
        values: defaultdict[int, int] = defaultdict(int)
        total_cells = 0
        raw_total_value = 0
        for inv_item in inventory_items:
            item_id = getattr(inv_item, "item_id", None)
            item = items.get(int(item_id)) if item_id is not None else None
            quality = getattr(inv_item, "quality", None)
            if quality is None and item is not None:
                quality = item.quality
            item_cells = getattr(inv_item, "cells", None)
            if item_cells is None and item is not None:
                item_cells = item.shape_w * item.shape_h
            item_value = item.value if item is not None else 0
            total_cells += int(item_cells or 0)
            raw_total_value += int(item_value)
            if quality is None:
                continue
            q = int(quality)
            counts[q] += 1
            cells[q] += int(item_cells or 0)
            values[q] += int(item_value)
        return SettlementTruthReport(
            session_id=getattr(state, "session_id", None),
            map_id=getattr(state, "map_id", None),
            sort_id=getattr(state, "sort_id", None),
            item_count=len(inventory_items),
            total_cells=total_cells,
            raw_total_value=raw_total_value,
            quality_truths=tuple(
                QualityTruthReport(
                    quality=quality,
                    count=counts[quality],
                    cells=cells[quality],
                    raw_value=values[quality],
                )
                for quality in sorted(counts)
            ),
        )
    return None


def empty_truth_flat_dict(*, prefix: str = "v3_truth_") -> dict[str, Any]:
    return {
        f"{prefix}available": False,
        f"{prefix}session_id": None,
        f"{prefix}map_id": None,
        f"{prefix}sort_id": None,
        f"{prefix}item_count": None,
        f"{prefix}total_cells": None,
        f"{prefix}raw_total_value": None,
        f"{prefix}q6_count": None,
        f"{prefix}q6_cells": None,
        f"{prefix}q6_raw_value": None,
    }


def empty_decision_truth_flat_dict(*, prefix: str = "v3_truth_") -> dict[str, Any]:
    return {
        f"{prefix}decision_available": False,
        f"{prefix}formal_decision_value": None,
        f"{prefix}tail_replacement_decision_value": None,
        f"{prefix}tail_replacement_value": None,
        f"{prefix}trimmed_tail_value": None,
        f"{prefix}trimmed_tail_count": None,
        f"{prefix}q6_formal_decision_value": None,
        f"{prefix}q6_tail_replacement_decision_value": None,
        f"{prefix}q6_tail_replacement_value": None,
        f"{prefix}q6_trimmed_tail_value": None,
        f"{prefix}q6_trimmed_tail_count": None,
    }


__all__ = (
    "DecisionTruthReport",
    "QualityTruthReport",
    "SettlementTruthReport",
    "decision_truth_from_fatbeans",
    "decision_truth_from_session_truth",
    "empty_decision_truth_flat_dict",
    "empty_truth_flat_dict",
    "settlement_truth_from_fatbeans",
)
