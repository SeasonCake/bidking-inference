"""Evidence-first inference v2 primitives.

The v1 live posterior samples a whole auction session and then rejects samples
that do not match observations. That works for low-information states, but it
breaks down once packet captures provide exact runtime/item facts. This module
keeps v2 separate so realtime code can compare both engines before switching.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
import math
from typing import Any, Literal, Mapping, Sequence

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import (
    BucketTruth,
    SessionTruth,
    is_huge_item,
    prepare_session_sampler,
)
from bidking_lab.inference.q6_residual import actionable_random_sample_avg_values
from bidking_lab.inference.map_likelihood import (
    QuantileSummary,
    category_observation_soft_score,
    truth_matches_obs,
)
from bidking_lab.inference.observation import CategoryItemObservation, SessionObs
from bidking_lab.inference.size_avg_evidence import (
    SIZE_AVG_VALUE_SIGNAL_FLOOR,
    SIZE_AVG_VALUE_SIGNAL_FLOORS,
    SizeBucketEvidence,
    actionable_size_avg_value_targets,
    build_size_bucket_evidence,
    footprint_count_in_buckets,
    prefill_size_bucket_targets,
    residual_allowed_for_footprint,
    size_avg_value_evidence_score,
    size_bucket_evidence_diagnostics,
)
from bidking_lab.simulation.robust_value import (
    DEFAULT_VALUE_FLOOR,
    is_confusable_long_tail,
)

EvidenceStrength = Literal["hard", "soft"]

_PUBLIC_AVG_VALUE_QUALITY: dict[int, int] = {
    200036: 4,  # 所有紫色品质藏品的平均价值
    200037: 5,  # 所有金色品质藏品的平均价值
    200038: 6,  # 所有红色品质藏品的平均价值
}
_PUBLIC_RANDOM_SAMPLE_AVG_VALUE_COUNT: dict[int, int] = {
    200031: 3,  # 随机 3 件藏品的平均价值
    200032: 6,  # 随机 6 件藏品的平均价值
    200033: 9,  # 随机 9 件藏品的平均价值
    200034: 12,  # 随机 12 件藏品的平均价值
}
_PUBLIC_AVG_CELLS_QUALITY: dict[int, int] = {
    200013: 4,  # 紫色品质藏品平均占用的格子数量
    200015: 5,  # 金色品质藏品平均占用的格子数量
    200016: 6,  # 红色品质藏品平均占用的格子数量
}
_PUBLIC_TOTAL_AVG_CELLS_IDS: set[int] = {
    200014,  # 每件藏品平均占用的格子数量
}
RANDOM_SAMPLE_VALUE_FLOOR_FACTOR = 0.95
RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY = 0.10
RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED = 3
RANDOM_SAMPLE_HARD_FLOOR_MIN_RATE = 0.20
RANDOM_SAMPLE_HARD_FLOOR_MAX_MIN_MATCHED = 20
RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS = 40
@dataclass(frozen=True)
class EvidenceFact:
    """One non-item evidence fact retained for diagnostics."""

    kind: str
    key: str
    value: Any
    source: str
    strength: EvidenceStrength = "hard"
    sequence: int | None = None


@dataclass(frozen=True)
class RuntimeEvidence:
    """Merged facts for one runtime/local warehouse object."""

    runtime_id: int | None = None
    local_index: int | None = None
    item_id: int | None = None
    quality: int | None = None
    value: int | None = None
    shape_key: str | None = None
    cells: int | None = None
    categories: tuple[int, ...] = ()
    excluded_categories: tuple[int, ...] = ()
    sources: tuple[str, ...] = ()

    @property
    def evidence_key(self) -> str:
        if self.runtime_id is not None:
            return f"runtime:{self.runtime_id}"
        if self.local_index is not None and self.shape_key is not None:
            return f"local:{self.local_index}:{self.shape_key}"
        return "anonymous"

    def merge(
        self,
        other: RuntimeEvidence,
    ) -> RuntimeEvidence:
        categories = tuple(dict.fromkeys((*self.categories, *other.categories)))
        excluded_categories = tuple(
            dict.fromkeys((*self.excluded_categories, *other.excluded_categories))
        )
        sources = tuple(dict.fromkeys((*self.sources, *other.sources)))
        if other.shape_key is not None:
            shape_key = other.shape_key
            cells = other.cells if other.cells is not None else self.cells
            local_index = other.local_index
        elif self.shape_key is not None:
            shape_key = self.shape_key
            cells = self.cells if self.cells is not None else other.cells
            local_index = self.local_index
        else:
            shape_key = None
            cells = self.cells if self.cells is not None else other.cells
            local_index = (
                other.local_index if other.local_index is not None else self.local_index
            )
        return RuntimeEvidence(
            runtime_id=self.runtime_id if self.runtime_id is not None else other.runtime_id,
            local_index=local_index,
            item_id=self.item_id if self.item_id is not None else other.item_id,
            quality=self.quality if self.quality is not None else other.quality,
            value=self.value if self.value is not None else other.value,
            shape_key=shape_key,
            cells=cells,
            categories=categories,
            excluded_categories=excluded_categories,
            sources=sources,
        )


@dataclass(frozen=True)
class EvidenceStore:
    """Runtime-indexed evidence gathered from packet/public/tool sources."""

    by_runtime: Mapping[int, RuntimeEvidence]
    anonymous: tuple[RuntimeEvidence, ...] = ()
    facts: tuple[EvidenceFact, ...] = ()

    def runtime_items(self) -> tuple[RuntimeEvidence, ...]:
        return tuple(self.by_runtime.values())

    def items(self) -> tuple[RuntimeEvidence, ...]:
        return (*self.runtime_items(), *self.anonymous)


class EvidenceStoreBuilder:
    """Mutable builder that deduplicates item evidence by runtime/local key."""

    def __init__(self) -> None:
        self._by_runtime: dict[int, RuntimeEvidence] = {}
        self._by_local_shape: dict[tuple[int, str], RuntimeEvidence] = {}
        self._anonymous: list[RuntimeEvidence] = []
        self._facts: list[EvidenceFact] = []

    def add_fact(self, fact: EvidenceFact) -> None:
        self._facts.append(fact)

    def add_item(self, item: RuntimeEvidence) -> None:
        if item.runtime_id is not None:
            current = self._by_runtime.get(item.runtime_id)
            self._by_runtime[item.runtime_id] = (
                item if current is None else current.merge(item)
            )
            return
        if item.local_index is not None and item.shape_key is not None:
            key = (item.local_index, item.shape_key)
            current = self._by_local_shape.get(key)
            self._by_local_shape[key] = item if current is None else current.merge(item)
            return
        self._anonymous.append(item)

    def build(self) -> EvidenceStore:
        return EvidenceStore(
            by_runtime=dict(self._by_runtime),
            anonymous=(*self._by_local_shape.values(), *self._anonymous),
            facts=tuple(self._facts),
        )


@dataclass(frozen=True)
class KnownItemAnchor:
    """Exact item evidence that every v2 sample must contain."""

    key: str
    item_id: int
    quality: int
    cells: int
    value: int
    local_index: int | None = None
    runtime_id: int | None = None
    categories: tuple[int, ...] = ()
    excluded_categories: tuple[int, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnownFootprint:
    """One trusted shape-bearing layout footprint."""

    key: str
    local_index: int
    shape_key: str
    cells: int
    row: int
    col: int
    width: int
    height: int
    bottom_row: int
    right_col: int
    item_id: int | None = None
    quality: int | None = None


@dataclass(frozen=True)
class ShapeTarget:
    """One non-unique quality+shape item that should exist in samples."""

    key: str
    quality: int
    shape_key: str
    cells: int
    categories: tuple[int, ...] = ()
    excluded_categories: tuple[int, ...] = ()


@dataclass(frozen=True)
class LayoutFeasibility:
    """Lightweight layout feasibility diagnostics for v2 samples."""

    footprint_count: int
    trusted_footprint_count: int
    occupied_cells: int
    item_cells: int
    overlap_cells: int
    overflow_count: int
    bottom_row: int | None
    bounding_cells: int
    score: float
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidualProblem:
    """Map problem after exact known items have been anchored."""

    map_id: int
    map_name: str
    anchors: tuple[KnownItemAnchor, ...]
    known_item_count: int
    known_cells: int
    known_value: int
    anchor_item_counts: Mapping[int, int]
    bucket_targets: Mapping[int, "ResidualBucketTarget"]
    category_targets: tuple[CategoryItemObservation, ...]
    shape_targets: tuple[ShapeTarget, ...]
    layout: LayoutFeasibility
    total_item_count: int | None = None
    warehouse_total_cells: int | None = None
    total_avg_cells: float | None = None
    random_sample_avg_values: tuple[tuple[int, float], ...] = ()
    random_sample_value_floor: int | None = None
    size_avg_value_targets: tuple[tuple[int, float], ...] = ()
    size_bucket_evidence: tuple[SizeBucketEvidence, ...] = ()
    size_bucket_prefill: bool = False
    size_bucket_mask_residual_pool: bool = False
    max_quality: int | None = None
    max_item_cells: int | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResidualBucketTarget:
    """Per-quality target after known anchors are accounted for."""

    quality: int
    total_cells_exact: int | None = None
    count_exact: int | None = None
    total_cells_floor: int | None = None
    count_floor: int | None = None
    value_floor: int | None = None
    value_exact: int | None = None
    avg_value: float | None = None
    avg_cells: float | None = None


@dataclass(frozen=True)
class QualityDropPrior:
    """Drop-table prior for one quality before runtime evidence filters it."""

    quality: int
    draw_probability: float
    session_probability: float
    expected_session_count: float
    expected_session_cells: float
    expected_session_value: float


@dataclass(frozen=True)
class SessionDropPrior:
    """Drop-table prior for the whole session before runtime evidence filters it."""

    expected_session_count: float
    expected_session_cells: float
    expected_session_value: float
    expected_session_decision_value: float
    expected_session_tail_replacement_decision_value: float


@dataclass(frozen=True)
class PosteriorReport:
    """Unified posterior summary produced by the v2 conditional sampler."""

    map_id: int
    map_name: str
    n_total: int
    n_matched: int
    total_cells: QuantileSummary | None
    total_value: QuantileSummary | None
    anchor_count: int
    known_cells: int
    known_value: int
    layout_score: float
    decision_value: QuantileSummary | None = None
    tail_replacement_decision_value: QuantileSummary | None = None
    q6_match_rate: float | None = None
    q6_value: QuantileSummary | None = None
    q6_decision_value: QuantileSummary | None = None
    q6_tail_replacement_decision_value: QuantileSummary | None = None
    q6_count: QuantileSummary | None = None
    q6_cells: QuantileSummary | None = None
    remaining_cells_after_layout: QuantileSummary | None = None
    q6_space_pressure: QuantileSummary | None = None
    q6_space_overflow_rate: float | None = None
    q6_prior_match_rate: float | None = None
    q6_prior_expected_count: float | None = None
    q6_prior_expected_cells: float | None = None
    q6_prior_expected_value: float | None = None
    prior_expected_count: float | None = None
    prior_expected_cells: float | None = None
    prior_expected_value: float | None = None
    prior_expected_decision_value: float | None = None
    prior_expected_tail_replacement_decision_value: float | None = None
    shape_target_count: int = 0
    category_target_count: int = 0
    category_exclusion_count: int = 0
    random_sample_avg_values: tuple[tuple[int, float], ...] = ()
    size_avg_value_targets: tuple[tuple[int, float], ...] = ()
    layout_diagnostics: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


def _shape_cells(shape_key: str | None) -> int | None:
    if shape_key is None:
        return None
    try:
        code = int(shape_key)
    except (TypeError, ValueError):
        return None
    w = code // 10
    h = code % 10
    if w <= 0 or h <= 0:
        return None
    return w * h


def _shape_dimensions(shape_key: str | None) -> tuple[int, int] | None:
    if shape_key is None:
        return None
    try:
        code = int(shape_key)
    except (TypeError, ValueError):
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width, height


def _observed_item_to_runtime_evidence(
    item: Any,
    *,
    source: str,
    category: int | None = None,
) -> RuntimeEvidence | None:
    shape_code = getattr(item, "shape_code", None)
    shape_key = str(shape_code) if shape_code else None
    cells = getattr(item, "cells", None)
    if cells is None:
        cells = _shape_cells(shape_key)
    categories = (category,) if category is not None else ()
    return RuntimeEvidence(
        runtime_id=getattr(item, "runtime_id", None),
        local_index=getattr(item, "local_index", None),
        item_id=getattr(item, "item_id", None),
        quality=getattr(item, "quality", None),
        value=getattr(item, "value", None),
        shape_key=shape_key,
        cells=cells,
        categories=categories,
        sources=(source,),
    )


def _evidence_keys_for_observed_item(item: Any) -> tuple[str, ...]:
    keys: list[str] = []
    runtime_id = getattr(item, "runtime_id", None)
    if runtime_id is not None:
        keys.append(f"runtime:{runtime_id}")
    local_index = getattr(item, "local_index", None)
    shape_code = getattr(item, "shape_code", None)
    if local_index is not None and shape_code:
        keys.append(f"local:{local_index}:{shape_code}")
    return tuple(keys)


def _evidence_keys_for_runtime_evidence(evidence: RuntimeEvidence) -> tuple[str, ...]:
    keys: list[str] = []
    if evidence.runtime_id is not None:
        keys.append(f"runtime:{evidence.runtime_id}")
    if evidence.local_index is not None and evidence.shape_key is not None:
        keys.append(f"local:{evidence.local_index}:{evidence.shape_key}")
    return tuple(keys)


def evidence_store_from_fatbeans_events(
    events: Any,
    *,
    include_inventory: bool = False,
) -> EvidenceStore:
    """Build an EvidenceStore from parsed Fatbeans events.

    Inventory facts are excluded by default because realtime inference should
    not use settlement truth as pre-settlement evidence.
    """

    from bidking_lab.live.fatbeans import (
        _ACTION_SIZE_AVG_VALUE,
        _CATEGORY_OUTLINE_ACTIONS,
        _skill_reveal_category,
    )

    builder = EvidenceStoreBuilder()
    for state in getattr(events, "states", ()) or ():
        sequence = getattr(state, "sort_id", None)
        known_shape_items: dict[str, RuntimeEvidence] = {}

        def remember_known_shape_item(item: Any, *, source: str) -> None:
            ev = _observed_item_to_runtime_evidence(item, source=source)
            if ev is None or ev.evidence_key == "anonymous":
                return
            if ev.shape_key is None and ev.cells is None:
                return
            for key in _evidence_keys_for_runtime_evidence(ev):
                current = known_shape_items.get(key)
                known_shape_items[key] = ev if current is None else current.merge(ev)

        for info in getattr(state, "public_infos", ()) or ():
            builder.add_fact(
                EvidenceFact(
                    kind="public_info",
                    key=str(getattr(info, "info_id", "")),
                    value=getattr(info, "value", None),
                    source="public",
                    sequence=sequence,
                )
            )
            for item in getattr(info, "observed_items", ()) or ():
                remember_known_shape_item(
                    item,
                    source=f"public:{getattr(info, 'info_id', '')}",
                )
                ev = _observed_item_to_runtime_evidence(
                    item,
                    source=f"public:{getattr(info, 'info_id', '')}",
                )
                if ev is not None:
                    builder.add_item(ev)
        for reveal in getattr(state, "skill_reveals", ()) or ():
            for item in getattr(reveal, "observed_items", ()) or ():
                remember_known_shape_item(
                    item,
                    source=f"skill:{getattr(reveal, 'skill_id', '')}",
                )
        for result in getattr(state, "action_results", ()) or ():
            action_id = getattr(result, "action_id", None)
            if action_id in _ACTION_SIZE_AVG_VALUE:
                result_value = getattr(result, "result", None)
                if result_value is not None:
                    try:
                        numeric = float(result_value)
                    except (TypeError, ValueError):
                        numeric = None
                    if numeric is not None and numeric > 0:
                        builder.add_fact(
                            EvidenceFact(
                                kind="action",
                                key=str(action_id),
                                value=numeric,
                                source=f"action:{action_id}",
                                strength="soft",
                                sequence=sequence,
                            )
                        )
            category = _CATEGORY_OUTLINE_ACTIONS.get(action_id)
            positive_keys = {
                key
                for item in getattr(result, "observed_items", ()) or ()
                for key in _evidence_keys_for_observed_item(item)
            }
            if category is not None:
                emitted_negative: set[str] = set()
                for known in known_shape_items.values():
                    known_keys = _evidence_keys_for_runtime_evidence(known)
                    if any(known_key in positive_keys for known_key in known_keys):
                        continue
                    if known.evidence_key in emitted_negative:
                        continue
                    emitted_negative.add(known.evidence_key)
                    builder.add_item(
                        RuntimeEvidence(
                            runtime_id=known.runtime_id,
                            local_index=known.local_index,
                            quality=known.quality,
                            shape_key=known.shape_key,
                            cells=known.cells,
                            excluded_categories=(category,),
                            sources=(f"action_negative:{action_id}",),
                        )
                    )
            for item in getattr(result, "observed_items", ()) or ():
                remember_known_shape_item(
                    item,
                    source=f"action:{action_id}",
                )
                ev = _observed_item_to_runtime_evidence(
                    item,
                    source=f"action:{action_id}",
                    category=category,
                )
                if ev is not None:
                    builder.add_item(ev)
        for reveal in getattr(state, "skill_reveals", ()) or ():
            skill_category = _skill_reveal_category(getattr(reveal, "skill_id", None))
            for item in getattr(reveal, "observed_items", ()) or ():
                ev = _observed_item_to_runtime_evidence(
                    item,
                    source=f"skill:{getattr(reveal, 'skill_id', '')}",
                    category=skill_category,
                )
                if ev is not None:
                    builder.add_item(ev)
        if include_inventory:
            for item in getattr(state, "inventory_items", ()) or ():
                item_id = getattr(item, "item_id", None)
                ev = RuntimeEvidence(
                    runtime_id=getattr(item, "runtime_id", None),
                    item_id=item_id,
                    quality=getattr(item, "quality", None),
                    cells=getattr(item, "cells", None),
                    sources=("inventory",),
                )
                builder.add_item(ev)
    return builder.build()


def known_item_anchors(
    store: EvidenceStore,
    *,
    items: Mapping[int, Item],
) -> tuple[KnownItemAnchor, ...]:
    """Return exact item anchors from merged runtime evidence."""

    anchors: list[KnownItemAnchor] = []
    seen_keys: set[str] = set()
    for evidence in store.items():
        if evidence.item_id is None or evidence.item_id not in items:
            continue
        item = items[evidence.item_id]
        key = evidence.evidence_key
        if key in seen_keys:
            continue
        seen_keys.add(key)
        anchors.append(
            KnownItemAnchor(
                key=key,
                runtime_id=evidence.runtime_id,
                local_index=evidence.local_index,
                item_id=item.item_id,
                quality=evidence.quality or item.quality,
                cells=evidence.cells or item.shape_w * item.shape_h,
                value=evidence.value or item.value,
                categories=evidence.categories,
                excluded_categories=evidence.excluded_categories,
                sources=evidence.sources,
            )
        )
    return tuple(anchors)


def _unique_shape_item_anchors(
    map_id: int,
    store: EvidenceStore,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    existing_keys: set[str],
) -> tuple[KnownItemAnchor, ...]:
    sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
    candidates_by_key: dict[
        tuple[int, str, tuple[int, ...]],
        dict[int, Item],
    ] = {}
    for pool in sampler.pools:
        for item in pool.items:
            shape_key = f"{item.shape_w}{item.shape_h}"
            category_keys = [()]
            category_keys.extend((tag,) for tag in item.tags)
            for category_key in category_keys:
                key = (item.quality, shape_key, category_key)
                candidates_by_key.setdefault(key, {})[item.item_id] = item

    anchors: list[KnownItemAnchor] = []
    seen_keys = set(existing_keys)
    for evidence in store.items():
        if evidence.item_id is not None:
            continue
        if evidence.quality is None or evidence.shape_key is None:
            continue
        lookup_keys = [
            (int(evidence.quality), evidence.shape_key, (category,))
            for category in evidence.categories
        ]
        lookup_keys.append((int(evidence.quality), evidence.shape_key, ()))
        matched_item: Item | None = None
        for lookup_key in lookup_keys:
            candidates = candidates_by_key.get(lookup_key, {})
            if evidence.excluded_categories:
                candidates = {
                    item_id: item
                    for item_id, item in candidates.items()
                    if not any(
                        category in item.tags
                        for category in evidence.excluded_categories
                    )
                }
            if len(candidates) == 1:
                matched_item = next(iter(candidates.values()))
                break
        if matched_item is None:
            continue
        key = evidence.evidence_key
        if key in seen_keys:
            continue
        seen_keys.add(key)
        anchors.append(
            KnownItemAnchor(
                key=key,
                runtime_id=evidence.runtime_id,
                local_index=evidence.local_index,
                item_id=matched_item.item_id,
                quality=matched_item.quality,
                cells=evidence.cells or matched_item.shape_w * matched_item.shape_h,
                value=matched_item.value,
                categories=evidence.categories,
                excluded_categories=evidence.excluded_categories,
                sources=(*evidence.sources, "inferred:unique_shape"),
            )
        )
    return tuple(anchors)


def shape_targets_from_store(
    store: EvidenceStore,
    *,
    anchored_keys: set[str],
) -> tuple[ShapeTarget, ...]:
    """Return non-unique quality+shape targets from outline evidence."""

    targets: list[ShapeTarget] = []
    seen: set[str] = set()
    for evidence in store.items():
        if evidence.item_id is not None:
            continue
        if evidence.categories:
            continue
        if evidence.quality is None or evidence.shape_key is None:
            continue
        cells = evidence.cells
        if cells is None:
            cells = _shape_cells(evidence.shape_key)
        if cells is None:
            continue
        key = evidence.evidence_key
        if key in anchored_keys or key in seen:
            continue
        seen.add(key)
        targets.append(
            ShapeTarget(
                key=key,
                quality=int(evidence.quality),
                shape_key=evidence.shape_key,
                cells=int(cells),
                categories=evidence.categories,
                excluded_categories=evidence.excluded_categories,
            )
        )
    return tuple(targets)


def _category_target_merge_key(
    target: CategoryItemObservation,
) -> tuple[
    int,
    int | None,
    str | None,
    int | None,
    int | None,
    tuple[int, ...],
]:
    return (
        target.category,
        target.cells,
        target.shape_key,
        target.quality,
        target.item_id,
        target.required_categories,
    )


def _merge_category_targets(
    targets: Sequence[CategoryItemObservation],
) -> tuple[CategoryItemObservation, ...]:
    merged: dict[
        tuple[
            int,
            int | None,
            str | None,
            int | None,
            int | None,
            tuple[int, ...],
        ],
        CategoryItemObservation,
    ] = {}
    for target in targets:
        key = _category_target_merge_key(target)
        current = merged.get(key)
        if current is None:
            merged[key] = target
            continue
        excluded_categories = tuple(
            dict.fromkeys((
                *current.excluded_categories,
                *target.excluded_categories,
            ))
        )
        merged[key] = replace(
            current,
            excluded_categories=excluded_categories,
            count=max(current.count, target.count),
        )
    return tuple(merged.values())


def category_targets_from_store(
    store: EvidenceStore,
    *,
    anchored_keys: set[str],
) -> tuple[CategoryItemObservation, ...]:
    """Return non-unique category+shape targets from item evidence."""

    targets: list[CategoryItemObservation] = []
    for evidence in store.items():
        if evidence.item_id is not None:
            continue
        if evidence.evidence_key in anchored_keys:
            continue
        if not evidence.categories:
            continue
        cells = evidence.cells
        if cells is None and evidence.shape_key is not None:
            cells = _shape_cells(evidence.shape_key)
        targets.append(
            CategoryItemObservation(
                category=evidence.categories[0],
                cells=int(cells) if cells is not None else None,
                shape_key=evidence.shape_key,
                quality=evidence.quality,
                required_categories=(
                    evidence.categories
                    if len(evidence.categories) > 1
                    else ()
                ),
                excluded_categories=evidence.excluded_categories,
            )
        )
    return _merge_category_targets(targets)


def known_footprints(
    store: EvidenceStore,
    *,
    columns: int = 10,
) -> tuple[KnownFootprint, ...]:
    footprints: list[KnownFootprint] = []
    seen: set[str] = set()
    for evidence in store.items():
        if evidence.shape_key is None:
            continue
        local_index = 0 if evidence.local_index is None else evidence.local_index
        if local_index < 0:
            continue
        dims = _shape_dimensions(evidence.shape_key)
        if dims is None:
            continue
        key = evidence.evidence_key
        if key in seen:
            continue
        seen.add(key)
        width, height = dims
        row = local_index // columns + 1
        col = local_index % columns + 1
        footprints.append(
            KnownFootprint(
                key=key,
                local_index=local_index,
                shape_key=evidence.shape_key,
                cells=evidence.cells or width * height,
                row=row,
                col=col,
                width=width,
                height=height,
                bottom_row=row + height - 1,
                right_col=col + width - 1,
                item_id=evidence.item_id,
                quality=evidence.quality,
            )
        )
    return tuple(footprints)


def layout_feasibility_from_store(
    store: EvidenceStore,
    *,
    columns: int = 10,
) -> LayoutFeasibility:
    footprints = known_footprints(store, columns=columns)
    if not footprints:
        return LayoutFeasibility(
            footprint_count=0,
            trusted_footprint_count=0,
            occupied_cells=0,
            item_cells=0,
            overlap_cells=0,
            overflow_count=0,
            bottom_row=None,
            bounding_cells=0,
            score=1.0,
        )
    occupied: set[tuple[int, int]] = set()
    overflow_count = 0
    item_cells = 0
    item_cells_in_grid = 0
    for footprint in footprints:
        item_cells += footprint.cells
        if footprint.right_col > columns:
            overflow_count += 1
        for row in range(footprint.row, footprint.bottom_row + 1):
            for col in range(footprint.col, footprint.right_col + 1):
                if 1 <= col <= columns:
                    item_cells_in_grid += 1
                    occupied.add((row, col))
    occupied_cells = len(occupied)
    overlap_cells = max(0, item_cells_in_grid - occupied_cells)
    bottom_row = max(footprint.bottom_row for footprint in footprints)
    diagnostics: list[str] = []
    score = 1.0
    if overflow_count:
        diagnostics.append(f"footprint_overflow:{overflow_count}")
        score *= max(0.25, 1.0 - 0.15 * overflow_count)
    if overlap_cells:
        diagnostics.append(f"footprint_overlap_cells:{overlap_cells}")
        score *= max(0.25, 1.0 - overlap_cells / max(1, item_cells))
    trusted_footprint_count = len(footprints)
    if overflow_count or overlap_cells:
        trusted_footprint_count = max(
            0,
            len(footprints) - overflow_count - min(overlap_cells, len(footprints)),
        )
        if trusted_footprint_count < len(footprints):
            diagnostics.append(
                f"footprint_count_relaxed:{len(footprints)}->{trusted_footprint_count}"
            )
    return LayoutFeasibility(
        footprint_count=len(footprints),
        trusted_footprint_count=trusted_footprint_count,
        occupied_cells=occupied_cells,
        item_cells=item_cells,
        overlap_cells=overlap_cells,
        overflow_count=overflow_count,
        bottom_row=bottom_row,
        bounding_cells=bottom_row * columns,
        score=score,
        diagnostics=tuple(diagnostics),
    )


def layout_feasibility_score(
    truth: SessionTruth,
    layout: LayoutFeasibility,
) -> float:
    if layout.footprint_count <= 0:
        return 1.0
    truth_count = sum(bucket.count for bucket in truth.buckets.values())
    if truth_count < layout.footprint_count:
        return 0.0
    if truth.warehouse_total_cells < layout.occupied_cells:
        return 0.0
    if layout.bottom_row is None or layout.bounding_cells <= 0:
        return layout.score

    minimum_dense_cells = max(0, (layout.bottom_row - 1) * 10)
    if minimum_dense_cells <= layout.occupied_cells:
        return layout.score
    if truth.warehouse_total_cells >= minimum_dense_cells:
        return layout.score
    gap = minimum_dense_cells - truth.warehouse_total_cells
    return layout.score * max(0.25, 1.0 - gap / max(1, minimum_dense_cells))


def _quality_evidence_floors(
    store: EvidenceStore,
) -> dict[int, tuple[int, int, int]]:
    count_by_quality: Counter[int] = Counter()
    cells_by_quality: Counter[int] = Counter()
    value_by_quality: Counter[int] = Counter()
    seen: set[str] = set()
    for evidence in store.items():
        if evidence.quality is None:
            continue
        key = evidence.evidence_key
        if key in seen:
            continue
        seen.add(key)
        quality = int(evidence.quality)
        count_by_quality[quality] += 1
        if evidence.cells is not None:
            cells_by_quality[quality] += int(evidence.cells)
        if evidence.value is not None:
            value_by_quality[quality] += int(evidence.value)
    return {
        quality: (count, cells_by_quality[quality], value_by_quality[quality])
        for quality, count in count_by_quality.items()
    }


def _public_avg_value_targets(store: EvidenceStore) -> dict[int, float]:
    targets: dict[int, float] = {}
    for fact in store.facts:
        if fact.kind != "public_info":
            continue
        try:
            info_id = int(fact.key)
            value = float(fact.value)
        except (TypeError, ValueError):
            continue
        quality = _PUBLIC_AVG_VALUE_QUALITY.get(info_id)
        if quality is None or value <= 0:
            continue
        targets[quality] = value
    return targets


def _public_avg_cells_targets(store: EvidenceStore) -> dict[int, float]:
    targets: dict[int, float] = {}
    for fact in store.facts:
        if fact.kind != "public_info":
            continue
        try:
            info_id = int(fact.key)
            value = float(fact.value)
        except (TypeError, ValueError):
            continue
        quality = _PUBLIC_AVG_CELLS_QUALITY.get(info_id)
        if quality is None or value <= 0:
            continue
        targets[quality] = value
    return targets


def _public_total_avg_cells_target(store: EvidenceStore) -> float | None:
    target: float | None = None
    for fact in store.facts:
        if fact.kind != "public_info":
            continue
        try:
            info_id = int(fact.key)
            value = float(fact.value)
        except (TypeError, ValueError):
            continue
        if info_id not in _PUBLIC_TOTAL_AVG_CELLS_IDS or value <= 0:
            continue
        target = value
    return target


def _action_size_avg_value_targets(
    store: EvidenceStore,
) -> tuple[tuple[int, float], ...]:
    """Per-footprint average item values from size-bucket action tools (100169-100173)."""
    from bidking_lab.live.fatbeans import _ACTION_SIZE_AVG_VALUE

    values: list[tuple[int, float]] = []
    seen: set[tuple[int, float]] = set()
    for fact in store.facts:
        if fact.kind != "action":
            continue
        try:
            action_id = int(fact.key)
            value = float(fact.value)
        except (TypeError, ValueError):
            continue
        cells = _ACTION_SIZE_AVG_VALUE.get(action_id)
        if cells is None or value <= 0:
            continue
        key = (cells, value)
        if key in seen:
            continue
        seen.add(key)
        values.append(key)
    return tuple(values)


def _public_random_sample_avg_values(
    store: EvidenceStore,
) -> tuple[tuple[int, float], ...]:
    values: list[tuple[int, float]] = []
    seen: set[tuple[int, float]] = set()
    for fact in store.facts:
        if fact.kind != "public_info":
            continue
        try:
            info_id = int(fact.key)
            value = float(fact.value)
        except (TypeError, ValueError):
            continue
        sample_count = _PUBLIC_RANDOM_SAMPLE_AVG_VALUE_COUNT.get(info_id)
        if sample_count is None or value <= 0:
            continue
        key = (sample_count, value)
        if key in seen:
            continue
        seen.add(key)
        values.append(key)
    return tuple(values)


def _random_sample_value_floor(
    values: tuple[tuple[int, float], ...],
    *,
    floor_factor: float = RANDOM_SAMPLE_VALUE_FLOOR_FACTOR,
) -> int | None:
    floors = [
        int(float(sample_count) * float(avg_value) * floor_factor)
        for sample_count, avg_value in actionable_random_sample_avg_values(values)
    ]
    return max(floors) if floors else None


def _public_global_constraints(
    store: EvidenceStore,
) -> tuple[int | None, int | None, tuple[str, ...]]:
    """Return global upper-bound constraints from item-level public info."""

    max_quality: int | None = None
    max_item_cells: int | None = None
    diagnostics: list[str] = []
    for evidence in store.items():
        sources = set(evidence.sources)
        if (
            {"public:200048", "skill:100110"} & sources
            and evidence.quality is not None
        ):
            quality = int(evidence.quality)
            max_quality = quality if max_quality is None else min(max_quality, quality)
        if "public:200050" in sources and evidence.cells is not None:
            cells = int(evidence.cells)
            max_item_cells = cells if max_item_cells is None else min(max_item_cells, cells)
    if max_quality is not None:
        diagnostics.append(f"public_max_quality:{max_quality}")
    if max_item_cells is not None:
        diagnostics.append(f"public_max_item_cells:{max_item_cells}")
    return max_quality, max_item_cells, tuple(diagnostics)


def _item_matches_category_target(
    item: Item,
    target: CategoryItemObservation,
) -> bool:
    if target.category not in item.tags:
        return False
    if target.required_categories and not all(
        category in item.tags for category in target.required_categories
    ):
        return False
    if target.excluded_categories and any(
        category in item.tags for category in target.excluded_categories
    ):
        return False
    if target.quality is not None and item.quality != target.quality:
        return False
    if target.cells is not None and item.shape_w * item.shape_h != target.cells:
        return False
    dims = _shape_dimensions(target.shape_key)
    if dims is not None and (item.shape_w, item.shape_h) != dims:
        return False
    return True


def _item_matches_shape_target(
    item: Item,
    target: ShapeTarget,
) -> bool:
    if item.quality != target.quality:
        return False
    dims = _shape_dimensions(target.shape_key)
    if dims is not None and (item.shape_w, item.shape_h) != dims:
        return False
    if target.cells is not None and item.shape_w * item.shape_h != target.cells:
        return False
    if target.categories and not any(
        category in item.tags for category in target.categories
    ):
        return False
    if target.excluded_categories and any(
        category in item.tags for category in target.excluded_categories
    ):
        return False
    return True


def build_residual_problem(
    map_id: int,
    store: EvidenceStore,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    obs: SessionObs | None = None,
    size_bucket_prefill: bool = False,
    size_bucket_mask_residual_pool: bool = False,
) -> ResidualProblem:
    """Create a residual problem by forcing exact known items into every sample."""

    bid_map = maps[map_id]
    anchors = known_item_anchors(store, items=items)
    anchors = (
        *anchors,
        *_unique_shape_item_anchors(
            map_id,
            store,
            maps=maps,
            drops=drops,
            items=items,
            existing_keys={anchor.key for anchor in anchors},
        ),
    )
    sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
    pool_item_ids = {
        item.item_id
        for pool in sampler.pools
        for item in pool.items
    }
    missing = sorted({anchor.item_id for anchor in anchors} - pool_item_ids)
    diagnostics: list[str] = []
    if missing:
        diagnostics.append(
            "anchors_not_in_flattened_pool:" + ",".join(str(item_id) for item_id in missing)
        )
    counts = Counter(anchor.item_id for anchor in anchors)
    shape_targets = shape_targets_from_store(
        store,
        anchored_keys={anchor.key for anchor in anchors},
    )
    store_category_targets = category_targets_from_store(
        store,
        anchored_keys={anchor.key for anchor in anchors},
    )
    layout = layout_feasibility_from_store(store)
    bucket_targets: dict[int, ResidualBucketTarget] = {}
    category_targets: tuple[CategoryItemObservation, ...] = store_category_targets
    if obs is not None:
        category_targets = _merge_category_targets((
            *category_targets,
            *(
                target for target in obs.category_items
                if target.item_id is None
            ),
        ))
        for quality, bucket in obs.buckets.items():
            cells_exact = bucket.total_cells
            cells_floor = bucket.total_cells_min
            count_exact = bucket.count
            count_floor = bucket.count_min
            value_floor = (
                bucket.value_sum
                if bucket.value_sum is not None and bucket.value_sum > 0
                else None
            )
            avg_value = (
                bucket.avg_value
                if bucket.avg_value is not None and bucket.avg_value > 0
                else None
            )
            if avg_value is not None:
                count_floor = max(count_floor or 0, 1)
            if (
                cells_exact is None
                and cells_floor is None
                and count_exact is None
                and count_floor is None
                and value_floor is None
                and avg_value is None
            ):
                continue
            bucket_targets[quality] = ResidualBucketTarget(
                quality=quality,
                total_cells_exact=int(cells_exact) if cells_exact is not None else None,
                count_exact=int(count_exact) if count_exact is not None else None,
                total_cells_floor=int(cells_floor) if cells_floor is not None else None,
                count_floor=int(count_floor) if count_floor is not None else None,
                value_floor=int(value_floor) if value_floor is not None else None,
                value_exact=int(value_floor) if value_floor is not None else None,
                avg_value=float(avg_value) if avg_value is not None else None,
            )
    for quality, (count_floor, cells_floor, value_floor) in _quality_evidence_floors(store).items():
        current = bucket_targets.get(quality)
        target_cells = cells_floor if cells_floor > 0 else None
        target_count = count_floor
        target_value = value_floor if value_floor > 0 else None
        target_avg_value = None
        target_avg_cells = None
        target_cells_exact = None
        target_count_exact = None
        if current is not None:
            if current.total_cells_floor is not None or target_cells is not None:
                target_cells = max(current.total_cells_floor or 0, target_cells or 0)
            target_count = max(current.count_floor or 0, target_count)
            if current.value_floor is not None or target_value is not None:
                target_value = max(current.value_floor or 0, target_value or 0)
            target_avg_value = current.avg_value
            target_avg_cells = current.avg_cells
            target_value_exact = current.value_exact
            target_cells_exact = current.total_cells_exact
            target_count_exact = current.count_exact
        else:
            target_value_exact = None
        bucket_targets[quality] = ResidualBucketTarget(
            quality=quality,
            total_cells_exact=target_cells_exact,
            count_exact=target_count_exact,
            total_cells_floor=target_cells,
            count_floor=target_count,
            value_floor=target_value,
            value_exact=target_value_exact,
            avg_value=target_avg_value,
            avg_cells=target_avg_cells,
        )
    for quality, avg_value in _public_avg_value_targets(store).items():
        current = bucket_targets.get(quality)
        bucket_targets[quality] = ResidualBucketTarget(
            quality=quality,
            total_cells_exact=current.total_cells_exact if current else None,
            count_exact=current.count_exact if current else None,
            total_cells_floor=current.total_cells_floor if current else None,
            count_floor=max(current.count_floor or 0, 1) if current else 1,
            value_floor=current.value_floor if current else None,
            value_exact=current.value_exact if current else None,
            avg_value=avg_value,
            avg_cells=current.avg_cells if current else None,
        )
    for quality, avg_cells in _public_avg_cells_targets(store).items():
        current = bucket_targets.get(quality)
        bucket_targets[quality] = ResidualBucketTarget(
            quality=quality,
            total_cells_exact=current.total_cells_exact if current else None,
            count_exact=current.count_exact if current else None,
            total_cells_floor=current.total_cells_floor if current else None,
            count_floor=max(current.count_floor or 0, 1) if current else 1,
            value_floor=current.value_floor if current else None,
            value_exact=current.value_exact if current else None,
            avg_value=current.avg_value if current else None,
            avg_cells=avg_cells,
        )
    if category_targets:
        for target in category_targets:
            if not any(
                _item_matches_category_target(item, target)
                for pool in sampler.pools
                for item in pool.items
            ):
                diagnostics.append(
                    "category_target_no_pool_match:"
                    f"{target.category}:{target.quality}:{target.shape_key}:{target.cells}"
                )
    if shape_targets:
        for target in shape_targets:
            if not any(
                _item_matches_shape_target(item, target)
                for pool in sampler.pools
                for item in pool.items
            ):
                diagnostics.append(
                    "shape_target_no_pool_match:"
                    f"q{target.quality}:{target.shape_key}:{target.cells}"
                )
    max_quality, max_item_cells, global_diagnostics = _public_global_constraints(store)
    diagnostics.extend(global_diagnostics)
    random_sample_avg_values = _public_random_sample_avg_values(store)
    random_sample_value_floor = _random_sample_value_floor(
        random_sample_avg_values
    )
    if random_sample_value_floor is not None:
        diagnostics.append(
            f"public_random_sample_value_floor:{random_sample_value_floor}"
        )
    size_avg_value_targets = actionable_size_avg_value_targets(
        _action_size_avg_value_targets(store)
    )
    size_bucket_evidence = build_size_bucket_evidence(
        size_avg_value_targets,
        store=store,
        warehouse_total_cells=obs.warehouse_total_cells if obs is not None else None,
        total_item_count=obs.total_item_count if obs is not None else None,
        layout_footprint_count=layout.footprint_count,
        trusted_footprint_count=layout.trusted_footprint_count,
    )
    diagnostics.extend(size_bucket_evidence_diagnostics(size_bucket_evidence))
    total_avg_cells = _public_total_avg_cells_target(store)
    if total_avg_cells is not None:
        diagnostics.append(f"public_total_avg_cells:{total_avg_cells:.4f}")
    return ResidualProblem(
        map_id=map_id,
        map_name=bid_map.name,
        anchors=anchors,
        known_item_count=len(anchors),
        known_cells=sum(anchor.cells for anchor in anchors),
        known_value=sum(anchor.value for anchor in anchors),
        anchor_item_counts=dict(counts),
        bucket_targets=bucket_targets,
        category_targets=category_targets,
        shape_targets=shape_targets,
        layout=layout,
        total_item_count=obs.total_item_count if obs is not None else None,
        warehouse_total_cells=obs.warehouse_total_cells if obs is not None else None,
        total_avg_cells=total_avg_cells,
        random_sample_avg_values=random_sample_avg_values,
        random_sample_value_floor=random_sample_value_floor,
        size_avg_value_targets=size_avg_value_targets,
        size_bucket_evidence=size_bucket_evidence,
        size_bucket_prefill=size_bucket_prefill,
        size_bucket_mask_residual_pool=size_bucket_mask_residual_pool,
        max_quality=max_quality,
        max_item_cells=max_item_cells,
        diagnostics=tuple((*diagnostics, *layout.diagnostics)),
    )


def _add_item_to_buckets(
    buckets: dict[int, BucketTruth],
    item: Item,
    *,
    count: int = 1,
) -> None:
    area = item.shape_w * item.shape_h
    bucket = buckets.setdefault(item.quality, BucketTruth(quality=item.quality))
    bucket.count += count
    bucket.total_cells += area * count
    bucket.value_sum += item.value * count
    if is_huge_item(item):
        bucket.huge_count += count
    bucket.items.extend([item] * count)


class ConditionalSampler:
    """Sample sessions while forcing KnownItemAnchor items to exist."""

    def __init__(
        self,
        problem: ResidualProblem,
        *,
        maps: Mapping[int, BidMap],
        drops: Mapping[int, DropPool],
        items: Mapping[int, Item],
        q6_residual_boost: float = 1.0,
        q6_residual_prior_floor_ratio: float = 0.0,
        q6_residual_prior_cell_floor_ratio: float = 0.0,
        q6_residual_value_power: float = 0.0,
        q6_conditional_target_count: float = 0.0,
        q6_conditional_target_cells: float = 0.0,
        q6_conditional_value_power: float = 0.0,
    ) -> None:
        self.problem = problem
        self.items = items
        self.q6_residual_boost = max(1.0, float(q6_residual_boost))
        self.q6_residual_prior_floor_ratio = max(
            0.0,
            float(q6_residual_prior_floor_ratio),
        )
        self.q6_residual_prior_cell_floor_ratio = max(
            0.0,
            float(q6_residual_prior_cell_floor_ratio),
        )
        self.q6_residual_value_power = max(0.0, float(q6_residual_value_power))
        self.q6_conditional_target_count = max(
            0.0,
            float(q6_conditional_target_count),
        )
        self.q6_conditional_target_cells = max(
            0.0,
            float(q6_conditional_target_cells),
        )
        self.q6_conditional_value_power = max(
            0.0,
            float(q6_conditional_value_power),
        )
        self._sampler = prepare_session_sampler(
            problem.map_id,
            maps=maps,
            drops=drops,
            items=items,
        )

    def quality_drop_prior(self, quality: int) -> QualityDropPrior | None:
        if not self._sampler.pools:
            return None
        draw_probability = 0.0
        session_probability = 0.0
        expected_session_count = 0.0
        expected_session_cells = 0.0
        expected_session_value = 0.0
        mean_draws = (
            self._sampler.items_per_session_min + self._sampler.items_per_session_max
        ) / 2
        for pool, pool_weight in zip(self._sampler.pools, self._sampler.pool_weights):
            if len(pool.probabilities) == 0:
                continue
            mask = pool.qualities == quality
            if not np.any(mask):
                continue
            pool_draw_p = float(pool.probabilities[mask].sum())
            pool_session_p = _session_probability_for_draw(
                pool_draw_p,
                self._sampler.items_per_session_min,
                self._sampler.items_per_session_max,
            )
            mean_counts = (pool.n_min[mask] + pool.n_max[mask]) / 2
            pool_expected_count = float(
                (pool.probabilities[mask] * mean_counts).sum()
            ) * mean_draws
            pool_expected_cells = float(
                (pool.probabilities[mask] * pool.areas[mask] * mean_counts).sum()
            ) * mean_draws
            pool_expected_value = float(
                (pool.probabilities[mask] * pool.values[mask] * mean_counts).sum()
            ) * mean_draws
            draw_probability += float(pool_weight) * pool_draw_p
            session_probability += float(pool_weight) * pool_session_p
            expected_session_count += float(pool_weight) * pool_expected_count
            expected_session_cells += float(pool_weight) * pool_expected_cells
            expected_session_value += float(pool_weight) * pool_expected_value
        return QualityDropPrior(
            quality=quality,
            draw_probability=draw_probability,
            session_probability=session_probability,
            expected_session_count=expected_session_count,
            expected_session_cells=expected_session_cells,
            expected_session_value=expected_session_value,
        )

    def session_drop_prior(self) -> SessionDropPrior | None:
        if not self._sampler.pools:
            return None
        expected_session_count = 0.0
        expected_session_cells = 0.0
        expected_session_value = 0.0
        expected_session_decision_value = 0.0
        expected_session_tail_replacement_decision_value = 0.0
        mean_draws = (
            self._sampler.items_per_session_min + self._sampler.items_per_session_max
        ) / 2
        exact_anchor_ids = set(self.problem.anchor_item_counts)
        replacement_values = self.tail_replacement_values()
        for pool, pool_weight in zip(self._sampler.pools, self._sampler.pool_weights):
            if len(pool.probabilities) == 0:
                continue
            mean_counts = (pool.n_min + pool.n_max) / 2
            for item, probability, mean_count in zip(
                pool.items,
                pool.probabilities,
                mean_counts,
            ):
                expected_item_count = (
                    float(pool_weight)
                    * float(probability)
                    * float(mean_count)
                    * mean_draws
                )
                if expected_item_count <= 0:
                    continue
                expected_session_count += expected_item_count
                expected_session_cells += (
                    expected_item_count * int(item.shape_w * item.shape_h)
                )
                expected_value = expected_item_count * int(item.value)
                expected_session_value += expected_value
                if _is_plannable_item(item, self.problem, exact_anchor_ids):
                    expected_session_decision_value += expected_value
                    expected_session_tail_replacement_decision_value += expected_value
                else:
                    expected_session_tail_replacement_decision_value += (
                        expected_item_count
                        * _tail_replacement_value(item, replacement_values)
                    )
        return SessionDropPrior(
            expected_session_count=expected_session_count,
            expected_session_cells=expected_session_cells,
            expected_session_value=expected_session_value,
            expected_session_decision_value=expected_session_decision_value,
            expected_session_tail_replacement_decision_value=(
                expected_session_tail_replacement_decision_value
            ),
        )

    def tail_replacement_values(self) -> dict[tuple[int, int, int], int]:
        """Map ``(quality, width, height)`` to map-weighted ordinary item P50."""

        values_by_shape: dict[tuple[int, int, int], list[int]] = {}
        weights_by_shape: dict[tuple[int, int, int], list[float]] = {}
        for pool, pool_weight in zip(self._sampler.pools, self._sampler.pool_weights):
            if len(pool.probabilities) == 0:
                continue
            mean_counts = (pool.n_min + pool.n_max) / 2
            for item, probability, mean_count in zip(
                pool.items,
                pool.probabilities,
                mean_counts,
            ):
                if item.shape_w <= 0 or item.shape_h <= 0:
                    continue
                if item.value <= 0 or item.value >= DEFAULT_VALUE_FLOOR:
                    continue
                key = (item.quality, item.shape_w, item.shape_h)
                values_by_shape.setdefault(key, []).append(int(item.value))
                weights_by_shape.setdefault(key, []).append(
                    float(pool_weight) * float(probability) * float(mean_count)
                )
        out: dict[tuple[int, int, int], int] = {}
        for key, values in values_by_shape.items():
            summary = _quantiles(values, weights_by_shape.get(key, ()))
            if summary is not None:
                out[key] = int(round(summary.p50))
        return out

    def _expected_items_per_draw(self, pool: Any) -> float:
        mean_counts = (pool.n_min + pool.n_max) / 2
        expected = float((pool.probabilities * mean_counts).sum())
        return max(1.0, expected)

    def _estimated_draws_for_item_count(
        self,
        pool: Any,
        *,
        item_count: int,
    ) -> int:
        if item_count <= 0:
            return 0
        expected_items_per_draw = self._expected_items_per_draw(pool)
        return max(1, int(math.ceil(int(item_count) / expected_items_per_draw)))

    def sample(self, rng: np.random.Generator | None = None) -> SessionTruth:
        rng = rng or np.random.default_rng()
        buckets: dict[int, BucketTruth] = {}
        for anchor in self.problem.anchors:
            item = self.items.get(anchor.item_id)
            if item is not None:
                _add_item_to_buckets(buckets, item)

        if not self._sampler.pools:
            return self._truth_from_buckets(buckets)

        pool_idx = (
            int(rng.choice(len(self._sampler.pools), p=self._sampler.pool_weights))
            if len(self._sampler.pools) > 1
            else 0
        )
        pool = self._sampler.pools[pool_idx]
        if len(pool.probabilities) == 0:
            return self._truth_from_buckets(buckets)

        if self.problem.size_bucket_prefill:
            prefill_size_bucket_targets(
                pool,
                buckets,
                self.problem.size_bucket_evidence,
                rng=rng,
                item_allowed=self._item_allowed_by_global_constraints,
                add_item=_add_item_to_buckets,
            )
        self._sample_shape_targets(pool, buckets, rng)
        self._sample_category_targets(pool, buckets, rng)
        self._sample_bucket_targets(pool, buckets, rng)
        self._sample_q6_prior_floor(pool, buckets, rng)
        self._sample_q6_conditional_target(pool, buckets, rng)

        current_count = sum(bucket.count for bucket in buckets.values())
        if (
            self.problem.total_item_count is not None
            and self.problem.total_item_count >= current_count
        ):
            total_draws = int(self.problem.total_item_count)
            residual_draws = max(0, total_draws - current_count)
        else:
            used_draws = self._estimated_draws_for_item_count(
                pool,
                item_count=current_count,
            )
            observed_min_draws = self._estimated_draws_for_item_count(
                pool,
                item_count=max(current_count, self.problem.layout.footprint_count),
            )
            draw_min = max(
                self._sampler.items_per_session_min,
                observed_min_draws,
            )
            draw_max = max(self._sampler.items_per_session_max, draw_min)
            total_draws = int(rng.integers(draw_min, draw_max + 1))
            residual_draws = max(0, total_draws - used_draws)
        if residual_draws and self.problem.total_item_count is not None:
            filled_exact_cells = False
            if self.problem.warehouse_total_cells is not None:
                filled_exact_cells = self._sample_exact_count_cells_residual(
                    pool,
                    buckets,
                    residual_draws,
                    rng,
                )
            if not filled_exact_cells:
                self._sample_exact_count_residual(pool, buckets, residual_draws, rng)
        elif residual_draws:
            filled_exact_cells = False
            if self.problem.warehouse_total_cells is not None:
                filled_exact_cells = self._sample_exact_cells_residual(
                    pool,
                    buckets,
                    rng,
                )
            if not filled_exact_cells:
                residual_probs = self._residual_probabilities(pool, buckets=buckets)
                sampled_idx = rng.choice(
                    len(residual_probs),
                    size=residual_draws,
                    replace=True,
                    p=residual_probs,
                )
                counts = rng.integers(
                    pool.n_min[sampled_idx],
                    pool.n_max[sampled_idx] + 1,
                )
                for pool_i, count in zip(sampled_idx, counts):
                    if not self._residual_bucket_delta_allowed(
                        pool.items[int(pool_i)],
                        buckets,
                        int(count),
                    ):
                        continue
                    _add_item_to_buckets(
                        buckets,
                        pool.items[int(pool_i)],
                        count=int(count),
                    )
        return self._truth_from_buckets(buckets)

    @staticmethod
    def _clone_buckets(
        buckets: Mapping[int, BucketTruth],
    ) -> dict[int, BucketTruth]:
        return {
            quality: replace(bucket, items=list(bucket.items))
            for quality, bucket in buckets.items()
        }

    def _sample_exact_cells_residual(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> bool:
        target_cells = self.problem.warehouse_total_cells
        if target_cells is None:
            return False
        current_cells = sum(bucket.total_cells for bucket in buckets.values())
        remaining_cells = int(target_cells) - int(current_cells)
        if remaining_cells == 0:
            return True
        if remaining_cells < 0 or remaining_cells > 320:
            return False

        options: list[tuple[int, int, int, float]] = []
        for pool_i, item in enumerate(pool.items):
            if not self._item_allowed_by_global_constraints(item):
                continue
            area = int(item.shape_w * item.shape_h)
            if area <= 0:
                continue
            min_count = int(pool.n_min[pool_i])
            max_count = int(pool.n_max[pool_i])
            weight = self._residual_candidate_weight(pool, int(pool_i))
            if min_count <= 0 or max_count < min_count or weight <= 0:
                continue
            for count in range(min_count, max_count + 1):
                added_cells = area * count
                if added_cells <= remaining_cells:
                    options.append((int(pool_i), count, added_cells, weight))
        if not options:
            return False

        cells_deltas = tuple(sorted({added_cells for _, _, added_cells, _ in options}))

        @lru_cache(maxsize=None)
        def can_fill(cells_left: int) -> bool:
            if cells_left == 0:
                return True
            if cells_left < 0:
                return False
            return any(
                cells <= cells_left and can_fill(cells_left - cells)
                for cells in cells_deltas
            )

        if not can_fill(remaining_cells):
            return False

        for _attempt in range(20):
            trial_buckets = self._clone_buckets(buckets)
            cells_left = remaining_cells
            while cells_left:
                feasible = [
                    option
                    for option in options
                    if option[2] <= cells_left
                    and can_fill(cells_left - option[2])
                    and self._residual_bucket_delta_allowed(
                        pool.items[option[0]],
                        trial_buckets,
                        option[1],
                    )
                ]
                if not feasible:
                    break
                weights = np.asarray(
                    [option[3] for option in feasible],
                    dtype=np.float64,
                )
                total = float(weights.sum())
                if total <= 0:
                    break
                pool_i, count, added_cells, _weight = feasible[
                    int(rng.choice(len(feasible), p=weights / total))
                ]
                _add_item_to_buckets(
                    trial_buckets,
                    pool.items[pool_i],
                    count=count,
                )
                cells_left -= added_cells
            if cells_left == 0:
                buckets.clear()
                buckets.update(trial_buckets)
                return True
        return False

    def _sample_exact_count_cells_residual(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        residual_count: int,
        rng: np.random.Generator,
    ) -> bool:
        target_cells = self.problem.warehouse_total_cells
        if target_cells is None:
            return False
        remaining_count = max(0, int(residual_count))
        current_cells = sum(bucket.total_cells for bucket in buckets.values())
        remaining_cells = int(target_cells) - int(current_cells)
        if remaining_count == 0 and remaining_cells == 0:
            return True
        if remaining_count <= 0 or remaining_cells <= 0:
            return False

        options_by_delta: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
        for pool_i, item in enumerate(pool.items):
            if not self._item_allowed_by_global_constraints(item):
                continue
            area = int(item.shape_w * item.shape_h)
            if area <= 0:
                continue
            min_count = int(pool.n_min[pool_i])
            max_count = min(int(pool.n_max[pool_i]), remaining_count)
            if min_count <= 0 or max_count < min_count:
                continue
            weight = self._residual_candidate_weight(pool, int(pool_i))
            if weight <= 0:
                continue
            for count in range(min_count, max_count + 1):
                if not self._residual_bucket_delta_allowed(item, buckets, count):
                    continue
                added_cells = area * count
                if added_cells <= remaining_cells:
                    options_by_delta.setdefault((count, added_cells), []).append(
                        (int(pool_i), count, weight)
                    )
        if not options_by_delta:
            return False

        deltas = tuple(sorted(options_by_delta))

        @lru_cache(maxsize=None)
        def can_fill(count_left: int, cells_left: int) -> bool:
            if count_left == 0 and cells_left == 0:
                return True
            if count_left < 0 or cells_left < 0:
                return False
            for count, cells in deltas:
                if count <= count_left and cells <= cells_left:
                    if can_fill(count_left - count, cells_left - cells):
                        return True
            return False

        if not can_fill(remaining_count, remaining_cells):
            return False

        count_left = remaining_count
        cells_left = remaining_cells
        while count_left or cells_left:
            feasible = [
                (count, cells)
                for count, cells in deltas
                if count <= count_left
                and cells <= cells_left
                and can_fill(count_left - count, cells_left - cells)
            ]
            if not feasible:
                return False
            weights = np.asarray(
                [
                    sum(weight for _pool_i, _count, weight in options_by_delta[key])
                    for key in feasible
                ],
                dtype=np.float64,
            )
            total = float(weights.sum())
            if total <= 0:
                return False
            key = feasible[int(rng.choice(len(feasible), p=weights / total))]
            choices = options_by_delta[key]
            choice_weights = np.asarray(
                [weight for _pool_i, _count, weight in choices],
                dtype=np.float64,
            )
            choice_total = float(choice_weights.sum())
            if choice_total <= 0:
                return False
            pool_i, count, _weight = choices[
                int(rng.choice(len(choices), p=choice_weights / choice_total))
            ]
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)
            count_left -= key[0]
            cells_left -= key[1]
        return True

    def _residual_probabilities(
        self,
        pool: Any,
        *,
        buckets: Mapping[int, BucketTruth] | None = None,
    ) -> np.ndarray:
        probs = pool.probabilities.astype(np.float64)
        if self.problem.size_bucket_mask_residual_pool:
            areas = np.asarray(
                [item.shape_w * item.shape_h for item in pool.items],
                dtype=np.int64,
            )
            masked_footprints = {
                target.cells
                for target in self.problem.size_bucket_evidence
                if target.count_exact is not None
            }
            for footprint in masked_footprints:
                probs[areas == footprint] = 0.0
        probs = self._apply_global_candidate_constraints(pool, probs)
        probs = self._apply_q6_value_tilt(pool, probs)
        qualities = getattr(pool, "qualities", None)
        if self.q6_residual_boost <= 1.0 or qualities is None:
            return probs
        boosted = probs.copy()
        boosted[np.asarray(qualities) == 6] *= self.q6_residual_boost
        total = float(boosted.sum())
        if total <= 0:
            return probs
        return boosted / total

    def _q6_value_tilt_for_item(self, pool: Any, pool_i: int) -> float:
        if self.q6_residual_value_power <= 0:
            return 1.0
        item = pool.items[int(pool_i)]
        if int(getattr(item, "quality", 0)) != 6:
            return 1.0
        q6_values = [
            max(1, int(candidate.value))
            for candidate in pool.items
            if int(getattr(candidate, "quality", 0)) == 6
        ]
        if not q6_values:
            return 1.0
        median_value = float(np.median(np.asarray(q6_values, dtype=np.float64)))
        if median_value <= 0:
            return 1.0
        raw = (max(1.0, float(item.value)) / median_value) ** self.q6_residual_value_power
        return float(np.clip(raw, 0.35, 4.00))

    def _apply_q6_value_tilt(self, pool: Any, probs: np.ndarray) -> np.ndarray:
        if self.q6_residual_value_power <= 0:
            return probs
        qualities = getattr(pool, "qualities", None)
        if qualities is None:
            return probs
        q6_mask = np.asarray(qualities) == 6
        q6_total = float(probs[q6_mask].sum())
        if q6_total <= 0:
            return probs
        tilted = probs.copy()
        q6_indexes = np.flatnonzero(q6_mask)
        factors = np.asarray(
            [self._q6_value_tilt_for_item(pool, int(index)) for index in q6_indexes],
            dtype=np.float64,
        )
        tilted_q6 = tilted[q6_indexes] * factors
        tilted_total = float(tilted_q6.sum())
        if tilted_total <= 0:
            return probs
        tilted[q6_indexes] = tilted_q6 / tilted_total * q6_total
        total = float(tilted.sum())
        if total <= 0:
            return probs
        return tilted / total

    def _q6_conditional_value_tilt_for_item(self, pool: Any, pool_i: int) -> float:
        if self.q6_conditional_value_power <= 0:
            return 1.0
        item = pool.items[int(pool_i)]
        if int(getattr(item, "quality", 0)) != 6:
            return 1.0
        q6_values = [
            max(1, int(candidate.value))
            for candidate in pool.items
            if int(getattr(candidate, "quality", 0)) == 6
        ]
        if not q6_values:
            return 1.0
        median_value = float(np.median(np.asarray(q6_values, dtype=np.float64)))
        if median_value <= 0:
            return 1.0
        raw = (max(1.0, float(item.value)) / median_value) ** (
            self.q6_conditional_value_power
        )
        return float(np.clip(raw, 0.50, 3.00))

    def _residual_candidate_weight(self, pool: Any, pool_i: int) -> float:
        if not self._item_allowed_by_global_constraints(pool.items[int(pool_i)]):
            return 0.0
        weight = float(pool.probabilities[pool_i])
        weight *= self._q6_value_tilt_for_item(pool, pool_i)
        if (
            self.q6_residual_boost > 1.0
            and int(getattr(pool.items[int(pool_i)], "quality", 0)) == 6
        ):
            weight *= self.q6_residual_boost
        return weight

    def _q6_conditional_candidate_weight(self, pool: Any, pool_i: int) -> float:
        weight = self._residual_candidate_weight(pool, pool_i)
        if weight <= 0:
            return 0.0
        return weight * self._q6_conditional_value_tilt_for_item(pool, pool_i)

    def _residual_bucket_delta_allowed(
        self,
        item: Item,
        buckets: Mapping[int, BucketTruth],
        count: int,
    ) -> bool:
        if not residual_allowed_for_footprint(
            item,
            buckets,
            self.problem.size_bucket_evidence,
            add_count=count,
        ):
            return False
        target = self.problem.bucket_targets.get(int(item.quality))
        if target is None:
            return True
        bucket = buckets.get(int(item.quality))
        current_count = bucket.count if bucket is not None else 0
        current_cells = bucket.total_cells if bucket is not None else 0
        current_value = bucket.value_sum if bucket is not None else 0
        added_count = max(0, int(count))
        added_cells = int(item.shape_w * item.shape_h) * added_count
        added_value = int(item.value) * added_count
        if target.count_exact is not None:
            if current_count + added_count > target.count_exact:
                return False
        if target.total_cells_exact is not None:
            if current_cells + added_cells > target.total_cells_exact:
                return False
        if target.value_exact is not None:
            value_limit = target.value_exact * 1.10
            if current_value + added_value > value_limit:
                return False
        return True

    def _item_allowed_by_global_constraints(self, item: Item) -> bool:
        if self.problem.max_quality is not None and item.quality > self.problem.max_quality:
            return False
        if (
            self.problem.max_item_cells is not None
            and item.shape_w * item.shape_h > self.problem.max_item_cells
        ):
            return False
        return True

    def _apply_global_candidate_constraints(
        self,
        pool: Any,
        probs: np.ndarray,
    ) -> np.ndarray:
        if self.problem.max_quality is None and self.problem.max_item_cells is None:
            return probs
        masked = probs.copy()
        for index, item in enumerate(pool.items):
            if not self._item_allowed_by_global_constraints(item):
                masked[index] = 0.0
        total = float(masked.sum())
        if total <= 0:
            return probs
        return masked / total

    def _sample_exact_count_residual(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        residual_count: int,
        rng: np.random.Generator,
    ) -> None:
        remaining = max(0, int(residual_count))
        areas = np.asarray(
            [item.shape_w * item.shape_h for item in pool.items],
            dtype=np.int64,
        )
        min_area = int(areas.min()) if len(areas) else 1
        max_area = int(areas.max()) if len(areas) else 16
        target_cells = self.problem.warehouse_total_cells
        cell_tol = 8
        attempts = 0
        while remaining > 0:
            attempts += 1
            if attempts > 500:
                break
            current_cells = sum(bucket.total_cells for bucket in buckets.values())
            feasible_pairs: list[tuple[int, int, float]] = []
            for pool_i in np.flatnonzero(pool.n_min <= remaining):
                if not self._item_allowed_by_global_constraints(pool.items[int(pool_i)]):
                    continue
                max_count = min(int(pool.n_max[pool_i]), remaining)
                min_count = min(int(pool.n_min[pool_i]), max_count)
                if min_count <= 0 or max_count <= 0:
                    continue
                for count in range(min_count, max_count + 1):
                    if not self._residual_bucket_delta_allowed(
                        pool.items[int(pool_i)],
                        buckets,
                        count,
                    ):
                        continue
                    add_cells = int(areas[pool_i]) * count
                    future_count = remaining - count
                    if target_cells is not None:
                        future_min = current_cells + add_cells + future_count * min_area
                        future_max = current_cells + add_cells + future_count * max_area
                        if future_min > target_cells + cell_tol:
                            continue
                        if future_max < target_cells - cell_tol:
                            continue
                    feasible_pairs.append(
                        (
                            int(pool_i),
                            count,
                            self._residual_candidate_weight(pool, int(pool_i)),
                        )
                    )
            if not feasible_pairs:
                feasible_pairs = [
                    (
                        int(pool_i),
                        int(pool.n_min[pool_i]),
                        self._residual_candidate_weight(pool, int(pool_i)),
                    )
                    for pool_i in np.flatnonzero(pool.n_min <= remaining)
                    if int(pool.n_min[pool_i]) > 0
                    and self._item_allowed_by_global_constraints(pool.items[int(pool_i)])
                    and self._residual_bucket_delta_allowed(
                        pool.items[int(pool_i)],
                        buckets,
                        int(pool.n_min[pool_i]),
                    )
                ]
            if not feasible_pairs:
                break
            probs = np.asarray([pair[2] for pair in feasible_pairs], dtype=np.float64)
            total = float(probs.sum())
            if total <= 0:
                break
            probs = probs / total
            pool_i, count, _weight = feasible_pairs[
                int(rng.choice(len(feasible_pairs), p=probs))
            ]
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)
            remaining -= count

    def _sample_q6_prior_floor(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> None:
        count_floor_ratio = self.q6_residual_prior_floor_ratio
        cell_floor_ratio = (
            self.q6_residual_prior_cell_floor_ratio
            if self.q6_residual_prior_cell_floor_ratio > 0
            else count_floor_ratio
        )
        if count_floor_ratio <= 0 and cell_floor_ratio <= 0:
            return
        if self.problem.max_quality is not None and self.problem.max_quality < 6:
            return
        q6_target = self.problem.bucket_targets.get(6)
        if q6_target is not None and (
            q6_target.count_exact is not None
            or q6_target.total_cells_exact is not None
        ):
            return
        prior = self.quality_drop_prior(6)
        if prior is None:
            return
        target_count = int(
            np.ceil(prior.expected_session_count * count_floor_ratio)
        )
        target_cells = int(
            np.ceil(prior.expected_session_cells * cell_floor_ratio)
        )
        if target_count <= 0 and target_cells <= 0:
            return

        attempts = 0
        while attempts < 50:
            attempts += 1
            q6_bucket = buckets.get(6)
            current_q6_count = q6_bucket.count if q6_bucket is not None else 0
            current_q6_cells = q6_bucket.total_cells if q6_bucket is not None else 0
            if current_q6_count >= target_count and current_q6_cells >= target_cells:
                return

            total_count = sum(bucket.count for bucket in buckets.values())
            if self.problem.total_item_count is not None:
                remaining_count_capacity = int(self.problem.total_item_count) - total_count
                if remaining_count_capacity <= 0:
                    return
            else:
                remaining_count_capacity = None

            total_cells = sum(bucket.total_cells for bucket in buckets.values())
            if self.problem.warehouse_total_cells is not None:
                remaining_cell_capacity = (
                    int(self.problem.warehouse_total_cells) - total_cells
                )
                if remaining_cell_capacity <= 0:
                    return
            else:
                remaining_cell_capacity = None

            candidates: list[tuple[int, int, float]] = []
            for pool_i, item in enumerate(pool.items):
                if int(item.quality) != 6:
                    continue
                if not self._item_allowed_by_global_constraints(item):
                    continue
                area = int(item.shape_w * item.shape_h)
                if area <= 0:
                    continue
                min_count = int(pool.n_min[pool_i])
                max_count = int(pool.n_max[pool_i])
                if remaining_count_capacity is not None:
                    max_count = min(max_count, remaining_count_capacity)
                if remaining_cell_capacity is not None:
                    max_count = min(max_count, remaining_cell_capacity // area)
                if max_count < min_count or min_count <= 0:
                    continue
                needed_count = max(0, target_count - current_q6_count)
                if needed_count > 0:
                    max_count = min(max_count, max(min_count, needed_count))
                weight = self._residual_candidate_weight(pool, pool_i)
                if weight > 0:
                    candidates.append((int(pool_i), max_count, weight))
            if not candidates:
                return

            weights = np.asarray([candidate[2] for candidate in candidates], dtype=np.float64)
            total = float(weights.sum())
            if total <= 0:
                return
            pool_i, max_count, _weight = candidates[
                int(rng.choice(len(candidates), p=weights / total))
            ]
            min_count = int(pool.n_min[pool_i])
            count = int(rng.integers(min_count, max_count + 1))
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)

    def _sample_q6_conditional_target(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> None:
        target_count = int(np.ceil(self.q6_conditional_target_count))
        target_cells = int(np.ceil(self.q6_conditional_target_cells))
        if target_count <= 0 and target_cells <= 0:
            return
        if self.problem.max_quality is not None and self.problem.max_quality < 6:
            return
        q6_target = self.problem.bucket_targets.get(6)
        if q6_target is not None and (
            q6_target.count_exact is not None
            or q6_target.total_cells_exact is not None
        ):
            return

        attempts = 0
        while attempts < 50:
            attempts += 1
            q6_bucket = buckets.get(6)
            current_q6_count = q6_bucket.count if q6_bucket is not None else 0
            current_q6_cells = q6_bucket.total_cells if q6_bucket is not None else 0
            if current_q6_count >= target_count and current_q6_cells >= target_cells:
                return

            total_count = sum(bucket.count for bucket in buckets.values())
            if self.problem.total_item_count is not None:
                remaining_count_capacity = int(self.problem.total_item_count) - total_count
                if remaining_count_capacity <= 0:
                    return
            else:
                remaining_count_capacity = None

            total_cells = sum(bucket.total_cells for bucket in buckets.values())
            if self.problem.warehouse_total_cells is not None:
                remaining_cell_capacity = (
                    int(self.problem.warehouse_total_cells) - total_cells
                )
                if remaining_cell_capacity <= 0:
                    return
            else:
                remaining_cell_capacity = None

            candidates: list[tuple[int, int, float]] = []
            for pool_i, item in enumerate(pool.items):
                if int(item.quality) != 6:
                    continue
                if not self._item_allowed_by_global_constraints(item):
                    continue
                area = int(item.shape_w * item.shape_h)
                if area <= 0:
                    continue
                min_count = int(pool.n_min[pool_i])
                max_count = int(pool.n_max[pool_i])
                if remaining_count_capacity is not None:
                    max_count = min(max_count, remaining_count_capacity)
                if remaining_cell_capacity is not None:
                    max_count = min(max_count, remaining_cell_capacity // area)
                if max_count < min_count or min_count <= 0:
                    continue
                needed_count = max(0, target_count - current_q6_count)
                if needed_count > 0:
                    max_count = min(max_count, max(min_count, needed_count))
                if not self._residual_bucket_delta_allowed(item, buckets, min_count):
                    continue
                weight = self._q6_conditional_candidate_weight(pool, pool_i)
                if weight > 0:
                    candidates.append((int(pool_i), max_count, weight))
            if not candidates:
                return

            weights = np.asarray([candidate[2] for candidate in candidates], dtype=np.float64)
            total = float(weights.sum())
            if total <= 0:
                return
            pool_i, max_count, _weight = candidates[
                int(rng.choice(len(candidates), p=weights / total))
            ]
            min_count = int(pool.n_min[pool_i])
            count = int(rng.integers(min_count, max_count + 1))
            if not self._residual_bucket_delta_allowed(
                pool.items[pool_i],
                buckets,
                count,
            ):
                continue
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)

    def _sample_shape_targets(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> None:
        if not self.problem.shape_targets:
            return
        for target in self.problem.shape_targets:
            indexes = [
                idx for idx, item in enumerate(pool.items)
                if self._item_allowed_by_global_constraints(item)
                and _item_matches_shape_target(item, target)
            ]
            if not indexes:
                continue
            probs = _target_sampling_probabilities(
                pool,
                indexes,
                quality=target.quality,
            )
            if probs is None:
                continue
            local_i = int(rng.choice(len(indexes), p=probs))
            pool_i = int(indexes[local_i])
            _add_item_to_buckets(buckets, pool.items[pool_i])

    def _sample_category_targets(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> None:
        if not self.problem.category_targets:
            return
        for target in self.problem.category_targets:
            indexes = [
                idx for idx, item in enumerate(pool.items)
                if self._item_allowed_by_global_constraints(item)
                and _item_matches_category_target(item, target)
            ]
            if not indexes:
                continue
            probs = _target_sampling_probabilities(
                pool,
                indexes,
                quality=target.quality,
            )
            if probs is None:
                continue
            repeats = max(1, int(target.count))
            for _ in range(repeats):
                local_i = int(rng.choice(len(indexes), p=probs))
                pool_i = int(indexes[local_i])
                _add_item_to_buckets(buckets, pool.items[pool_i])

    def _sample_bucket_targets(
        self,
        pool: Any,
        buckets: dict[int, BucketTruth],
        rng: np.random.Generator,
    ) -> None:
        if not self.problem.bucket_targets:
            return
        qualities = np.asarray([item.quality for item in pool.items], dtype=np.int64)
        for target in self.problem.bucket_targets.values():
            indexes = np.asarray(
                [
                    int(index)
                    for index in np.flatnonzero(qualities == target.quality)
                    if self._item_allowed_by_global_constraints(pool.items[int(index)])
                ],
                dtype=np.int64,
            )
            if len(indexes) == 0:
                continue
            if self._sample_exact_bucket_combo(pool, indexes, buckets, target, rng):
                if self._bucket_target_met(buckets.get(target.quality), target):
                    continue
            if self._sample_exact_cells_bucket_combo(pool, indexes, buckets, target, rng):
                if self._bucket_target_met(buckets.get(target.quality), target):
                    continue
            attempts = 0
            while not self._bucket_target_met(buckets.get(target.quality), target):
                attempts += 1
                if attempts > 200:
                    break
                feasible = self._feasible_target_indexes(
                    pool,
                    indexes,
                    buckets.get(target.quality),
                    target,
                )
                if len(feasible) == 0:
                    break
                probs = pool.probabilities[feasible].astype(np.float64)
                total = float(probs.sum())
                if total <= 0:
                    break
                probs = probs / total
                local_i = int(rng.choice(len(feasible), p=probs))
                pool_i = int(feasible[local_i])
                item = pool.items[pool_i]
                max_count = self._max_add_count_for_target(
                    buckets.get(target.quality),
                    target,
                    item,
                    int(pool.n_max[pool_i]),
                )
                if max_count < int(pool.n_min[pool_i]):
                    continue
                count = int(rng.integers(int(pool.n_min[pool_i]), max_count + 1))
                _add_item_to_buckets(buckets, item, count=count)

    def _sample_exact_bucket_combo(
        self,
        pool: Any,
        indexes: np.ndarray,
        buckets: dict[int, BucketTruth],
        target: ResidualBucketTarget,
        rng: np.random.Generator,
    ) -> bool:
        if target.count_exact is None or target.total_cells_exact is None:
            return False
        bucket = buckets.get(target.quality)
        current_count = bucket.count if bucket is not None else 0
        current_cells = bucket.total_cells if bucket is not None else 0
        remaining_count = int(target.count_exact - current_count)
        remaining_cells = int(target.total_cells_exact - current_cells)
        if remaining_count == 0 and remaining_cells == 0:
            return True
        if remaining_count < 0 or remaining_cells < 0:
            return False
        if remaining_count > 80 or remaining_cells > 320:
            return False

        options_by_delta: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
        for pool_i in indexes:
            pool_i = int(pool_i)
            item = pool.items[pool_i]
            area = int(item.shape_w * item.shape_h)
            if area <= 0:
                continue
            min_count = int(pool.n_min[pool_i])
            max_count = int(pool.n_max[pool_i])
            for count in range(min_count, max_count + 1):
                added_cells = area * count
                if count <= remaining_count and added_cells <= remaining_cells:
                    key = (count, added_cells)
                    weight = float(pool.probabilities[pool_i])
                    options_by_delta.setdefault(key, []).append((pool_i, count, weight))
        if not options_by_delta:
            return False

        deltas = tuple(sorted(options_by_delta))

        @lru_cache(maxsize=None)
        def can_fill(count_left: int, cells_left: int) -> bool:
            if count_left == 0 and cells_left == 0:
                return True
            if count_left < 0 or cells_left < 0:
                return False
            for count, cells in deltas:
                if count <= count_left and cells <= cells_left:
                    if can_fill(count_left - count, cells_left - cells):
                        return True
            return False

        if not can_fill(remaining_count, remaining_cells):
            return False

        count_left = remaining_count
        cells_left = remaining_cells
        while count_left or cells_left:
            feasible = [
                (count, cells)
                for count, cells in deltas
                if count <= count_left
                and cells <= cells_left
                and can_fill(count_left - count, cells_left - cells)
            ]
            if not feasible:
                return False
            weights = np.asarray(
                [
                    sum(weight for _pool_i, _count, weight in options_by_delta[key])
                    for key in feasible
                ],
                dtype=np.float64,
            )
            total = float(weights.sum())
            if total <= 0:
                return False
            key = feasible[int(rng.choice(len(feasible), p=weights / total))]
            choices = options_by_delta[key]
            choice_weights = np.asarray(
                [weight for _pool_i, _count, weight in choices],
                dtype=np.float64,
            )
            choice_total = float(choice_weights.sum())
            if choice_total <= 0:
                return False
            pool_i, count, _weight = choices[
                int(rng.choice(len(choices), p=choice_weights / choice_total))
            ]
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)
            count_left -= count
            cells_left -= key[1]
        return True

    def _sample_exact_cells_bucket_combo(
        self,
        pool: Any,
        indexes: np.ndarray,
        buckets: dict[int, BucketTruth],
        target: ResidualBucketTarget,
        rng: np.random.Generator,
    ) -> bool:
        if target.total_cells_exact is None or target.count_exact is not None:
            return False
        bucket = buckets.get(target.quality)
        current_count = bucket.count if bucket is not None else 0
        current_cells = bucket.total_cells if bucket is not None else 0
        remaining_cells = int(target.total_cells_exact - current_cells)
        min_count_left = max(0, int(target.count_floor or 0) - current_count)
        if remaining_cells == 0 and min_count_left == 0:
            return True
        if remaining_cells < 0 or remaining_cells > 320:
            return False

        options_by_delta: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
        for pool_i in indexes:
            pool_i = int(pool_i)
            item = pool.items[pool_i]
            area = int(item.shape_w * item.shape_h)
            if area <= 0:
                continue
            min_count = int(pool.n_min[pool_i])
            max_count = int(pool.n_max[pool_i])
            for count in range(min_count, max_count + 1):
                added_cells = area * count
                if added_cells <= remaining_cells:
                    key = (count, added_cells)
                    weight = float(pool.probabilities[pool_i])
                    options_by_delta.setdefault(key, []).append((pool_i, count, weight))
        if not options_by_delta:
            return False

        deltas = tuple(sorted(options_by_delta))

        @lru_cache(maxsize=None)
        def can_fill(cells_left: int, count_floor_left: int) -> bool:
            if cells_left == 0:
                return count_floor_left <= 0
            if cells_left < 0:
                return False
            for count, cells in deltas:
                if cells <= cells_left:
                    if can_fill(cells_left - cells, max(0, count_floor_left - count)):
                        return True
            return False

        if not can_fill(remaining_cells, min_count_left):
            return False

        cells_left = remaining_cells
        count_floor_left = min_count_left
        while cells_left:
            feasible = [
                (count, cells)
                for count, cells in deltas
                if cells <= cells_left
                and can_fill(cells_left - cells, max(0, count_floor_left - count))
            ]
            if not feasible:
                return False
            weights = np.asarray(
                [
                    sum(weight for _pool_i, _count, weight in options_by_delta[key])
                    for key in feasible
                ],
                dtype=np.float64,
            )
            total = float(weights.sum())
            if total <= 0:
                return False
            key = feasible[int(rng.choice(len(feasible), p=weights / total))]
            choices = options_by_delta[key]
            choice_weights = np.asarray(
                [weight for _pool_i, _count, weight in choices],
                dtype=np.float64,
            )
            choice_total = float(choice_weights.sum())
            if choice_total <= 0:
                return False
            pool_i, count, _weight = choices[
                int(rng.choice(len(choices), p=choice_weights / choice_total))
            ]
            _add_item_to_buckets(buckets, pool.items[pool_i], count=count)
            cells_left -= key[1]
            count_floor_left = max(0, count_floor_left - key[0])
        return count_floor_left <= 0

    def _feasible_target_indexes(
        self,
        pool: Any,
        indexes: np.ndarray,
        bucket: BucketTruth | None,
        target: ResidualBucketTarget,
    ) -> np.ndarray:
        if target.count_exact is None and target.total_cells_exact is None:
            return indexes
        feasible: list[int] = []
        for pool_i in indexes:
            max_count = self._max_add_count_for_target(
                bucket,
                target,
                pool.items[int(pool_i)],
                int(pool.n_max[int(pool_i)]),
            )
            if max_count >= int(pool.n_min[int(pool_i)]):
                feasible.append(int(pool_i))
        return np.asarray(feasible, dtype=np.int64)

    @staticmethod
    def _max_add_count_for_target(
        bucket: BucketTruth | None,
        target: ResidualBucketTarget,
        item: Item,
        default_max: int,
    ) -> int:
        max_count = max(0, int(default_max))
        current_count = bucket.count if bucket is not None else 0
        current_cells = bucket.total_cells if bucket is not None else 0
        if target.count_exact is not None:
            max_count = min(max_count, max(0, target.count_exact - current_count))
        if target.total_cells_exact is not None:
            area = item.shape_w * item.shape_h
            if area <= 0:
                return 0
            max_count = min(
                max_count,
                max(0, (target.total_cells_exact - current_cells) // area),
            )
        return max_count

    @staticmethod
    def _bucket_target_met(
        bucket: BucketTruth | None,
        target: ResidualBucketTarget,
    ) -> bool:
        cells = bucket.total_cells if bucket is not None else 0
        count = bucket.count if bucket is not None else 0
        if target.total_cells_exact is not None:
            if cells != target.total_cells_exact:
                return False
        elif target.total_cells_floor is not None and cells < target.total_cells_floor:
            return False
        if target.count_exact is not None:
            if count != target.count_exact:
                return False
        elif target.count_floor is not None and count < target.count_floor:
            return False
        value = bucket.value_sum if bucket is not None else 0
        if target.value_floor is not None and value < target.value_floor:
            return False
        return True

    def _truth_from_buckets(self, buckets: dict[int, BucketTruth]) -> SessionTruth:
        return SessionTruth(
            map_id=self.problem.map_id,
            map_name=self.problem.map_name,
            warehouse_total_cells=sum(bucket.total_cells for bucket in buckets.values()),
            buckets=buckets,
        )


def _session_probability_for_draw(
    draw_probability: float,
    min_draws: int,
    max_draws: int,
) -> float:
    if draw_probability <= 0:
        return 0.0
    if draw_probability >= 1:
        return 1.0
    lo = min(int(min_draws), int(max_draws))
    hi = max(int(min_draws), int(max_draws))
    probabilities = [
        1.0 - (1.0 - draw_probability) ** draws
        for draws in range(lo, hi + 1)
    ]
    return float(sum(probabilities) / len(probabilities))


def _target_sampling_probabilities(
    pool: Any,
    indexes: Sequence[int],
    *,
    quality: int | None,
) -> np.ndarray | None:
    """Return target-candidate probabilities with a conservative q6 value tilt."""

    if not indexes:
        return None
    index_array = np.asarray(indexes, dtype=np.int64)
    probs = pool.probabilities[index_array].astype(np.float64)
    if quality == 6 and len(index_array) > 1:
        values = np.asarray(
            [max(1, int(pool.items[int(idx)].value)) for idx in index_array],
            dtype=np.float64,
        )
        median_value = float(np.median(values))
        if median_value > 0:
            # The target already proves a q6 item with this shape/category exists.
            # Tilt only within that candidate set so low-probability high-value
            # matches are not starved, without raising global red-item odds.
            tilt = np.sqrt(values / median_value)
            probs *= np.clip(tilt, 0.50, 3.00)
    total = float(probs.sum())
    if total <= 0:
        return None
    return probs / total


def _quantiles(values: Sequence[int], weights: Sequence[float]) -> QuantileSummary | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    if not weights or len(weights) != len(values):
        p10, p50, p90 = np.percentile(arr, [10, 50, 90])
        return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))
    w = np.asarray(weights, dtype=np.float64)
    valid = w > 0
    if not np.any(valid):
        return None
    arr = arr[valid]
    w = w[valid]
    order = np.argsort(arr)
    arr = arr[order]
    w = w[order]
    cumulative = np.cumsum(w)
    total = float(cumulative[-1])
    p10, p50, p90 = np.interp(
        [0.10 * total, 0.50 * total, 0.90 * total],
        cumulative,
        arr,
    )
    return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))


def _weighted_positive_rate(
    values: Sequence[int],
    weights: Sequence[float],
) -> float | None:
    if not values:
        return None
    if not weights or len(weights) != len(values):
        return sum(1 for value in values if value > 0) / len(values)
    arr = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    valid = w > 0
    if not np.any(valid):
        return None
    total = float(w[valid].sum())
    if total <= 0:
        return None
    return float(w[valid & (arr > 0)].sum() / total)


def value_evidence_score(
    truth: SessionTruth,
    problem: ResidualProblem,
) -> float:
    """Score value floor and average-value evidence for one sampled truth."""

    score = 1.0
    for target in problem.bucket_targets.values():
        bucket = truth.buckets.get(target.quality)
        if target.value_floor is not None:
            value = bucket.value_sum if bucket is not None else 0
            if value < target.value_floor:
                return 0.0
        if target.value_exact is not None:
            value = bucket.value_sum if bucket is not None else 0
            rel_err = abs(value - target.value_exact) / max(1.0, target.value_exact)
            if rel_err <= 0.05:
                factor = 1.0
            elif rel_err <= 0.25:
                factor = max(0.35, 1.0 - rel_err * 2)
            elif rel_err <= 0.75:
                factor = max(0.10, 1.0 - rel_err)
            else:
                factor = 0.05
            score *= factor
        if target.avg_value is None:
            continue
        if bucket is None or bucket.count <= 0:
            return 0.0
        actual = bucket.value_sum / bucket.count
        rel_err = abs(actual - target.avg_value) / max(1.0, target.avg_value)
        if rel_err <= 0.10:
            factor = 1.0
        elif rel_err <= 0.50:
            factor = max(0.20, 1.0 - rel_err)
        else:
            factor = 0.10
        score *= factor
    return score


def _avg_cells_evidence_factor(actual: float, target: float) -> float:
    abs_err = abs(actual - target)
    if abs_err <= 0.025:
        return 1.0
    if abs_err <= 0.25:
        return max(0.35, 1.0 - abs_err * 2.0)
    if abs_err <= 1.00:
        return max(0.10, 1.0 - abs_err * 0.60)
    return 0.05


def cell_evidence_score(
    truth: SessionTruth,
    problem: ResidualProblem,
) -> float:
    """Score public average-cell evidence without hard-filtering samples."""

    score = 1.0
    for target in problem.bucket_targets.values():
        if target.avg_cells is None:
            continue
        bucket = truth.buckets.get(target.quality)
        if bucket is None or bucket.count <= 0:
            return 0.0
        actual = bucket.total_cells / bucket.count
        score *= _avg_cells_evidence_factor(actual, target.avg_cells)

    if problem.total_avg_cells is not None:
        total_count = sum(bucket.count for bucket in truth.buckets.values())
        if total_count <= 0:
            return 0.0
        actual = truth.warehouse_total_cells / total_count
        score *= _avg_cells_evidence_factor(actual, problem.total_avg_cells)
    return score


def global_evidence_score(
    truth: SessionTruth,
    problem: ResidualProblem,
    *,
    random_sample_mode: Literal["soft", "hard", "ignore"] = "soft",
) -> float:
    """Apply global public-info upper bounds to one sampled truth."""

    if (
        problem.max_quality is None
        and problem.max_item_cells is None
        and problem.random_sample_value_floor is None
    ):
        return 1.0
    score = 1.0
    if (
        random_sample_mode != "ignore"
        and
        problem.random_sample_value_floor is not None
        and truth.total_value() < problem.random_sample_value_floor
    ):
        if random_sample_mode == "hard":
            return 0.0
        score *= RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY
    for bucket in truth.buckets.values():
        for item in bucket.items:
            if problem.max_quality is not None and item.quality > problem.max_quality:
                return 0.0
            if (
                problem.max_item_cells is not None
                and item.shape_w * item.shape_h > problem.max_item_cells
            ):
                return 0.0
    return score


def _random_sample_floor_passes(
    truth: SessionTruth,
    problem: ResidualProblem,
) -> bool:
    if problem.random_sample_value_floor is None:
        return True
    return truth.total_value() >= problem.random_sample_value_floor


def _random_sample_hard_floor_min_matched(candidate_count: int) -> int:
    if candidate_count <= 0:
        return RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED
    return max(
        RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED,
        min(
            RANDOM_SAMPLE_HARD_FLOOR_MAX_MIN_MATCHED,
            int(math.ceil(candidate_count * RANDOM_SAMPLE_HARD_FLOOR_MIN_RATE)),
        ),
    )


def is_tail_supported_by_evidence(item: Item, problem: ResidualProblem) -> bool:
    """Whether explicit evidence supports planning around an extreme tail item."""

    if item.item_id in problem.anchor_item_counts:
        return True
    return any(
        _item_matches_category_target(item, target)
        for target in problem.category_targets
    )


def decision_value_for_truth(truth: SessionTruth, problem: ResidualProblem) -> int:
    """Return plannable value after trimming unconfirmed extreme tails."""

    exact_anchor_ids = set(problem.anchor_item_counts)
    total = 0
    for bucket in truth.buckets.values():
        for item in bucket.items:
            if not _is_plannable_item(item, problem, exact_anchor_ids):
                continue
            total += item.value
    return total


def q6_decision_value_for_truth(truth: SessionTruth, problem: ResidualProblem) -> int:
    """Return the q6 component of plannable value."""

    exact_anchor_ids = set(problem.anchor_item_counts)
    bucket = truth.buckets.get(6)
    if bucket is None:
        return 0
    return sum(
        item.value
        for item in bucket.items
        if _is_plannable_item(item, problem, exact_anchor_ids)
    )


def tail_replacement_decision_value_for_truth(
    truth: SessionTruth,
    problem: ResidualProblem,
    replacement_values: Mapping[tuple[int, int, int], int],
) -> int:
    """Return decision value plus same-shape ordinary replacement for trimmed tails."""

    exact_anchor_ids = set(problem.anchor_item_counts)
    total = 0
    for bucket in truth.buckets.values():
        for item in bucket.items:
            if _is_plannable_item(item, problem, exact_anchor_ids):
                total += item.value
                continue
            total += _tail_replacement_value(item, replacement_values)
    return total


def q6_tail_replacement_decision_value_for_truth(
    truth: SessionTruth,
    problem: ResidualProblem,
    replacement_values: Mapping[tuple[int, int, int], int],
) -> int:
    """Return q6 decision value plus same-shape ordinary replacement."""

    exact_anchor_ids = set(problem.anchor_item_counts)
    bucket = truth.buckets.get(6)
    if bucket is None:
        return 0
    total = 0
    for item in bucket.items:
        if _is_plannable_item(item, problem, exact_anchor_ids):
            total += item.value
            continue
        total += _tail_replacement_value(item, replacement_values)
    return total


def _tail_replacement_value(
    item: Item,
    replacement_values: Mapping[tuple[int, int, int], int],
) -> int:
    if item.shape_w <= 0 or item.shape_h <= 0:
        return 0
    return int(replacement_values.get((item.quality, item.shape_w, item.shape_h), 0))


def _is_plannable_item(
    item: Item,
    problem: ResidualProblem,
    exact_anchor_ids: set[int],
) -> bool:
    if item.item_id not in exact_anchor_ids and is_confusable_long_tail(item):
        return False
    if item.value >= DEFAULT_VALUE_FLOOR and not is_tail_supported_by_evidence(
        item,
        problem,
    ):
        return False
    return True


def _relax_exact_bucket_obs(obs: SessionObs) -> tuple[SessionObs, tuple[str, ...]]:
    relaxed_buckets = {}
    relaxed: list[str] = []
    for quality, bucket in obs.buckets.items():
        cells_min = bucket.total_cells_min
        count_min = bucket.count_min
        if bucket.total_cells is not None:
            cells_min = max(cells_min or 0, bucket.total_cells)
        if bucket.count is not None:
            count_min = max(count_min or 0, bucket.count)
        if bucket.total_cells is not None or bucket.count is not None:
            relaxed.append(
                f"q{quality}:count={bucket.count}:cells={bucket.total_cells}"
            )
        relaxed_buckets[quality] = replace(
            bucket,
            total_cells=None,
            count=None,
            total_cells_min=cells_min,
            count_min=count_min,
        )
    if not relaxed:
        return obs, ()
    return replace(obs, buckets=relaxed_buckets), (
        "relaxed_exact_bucket_targets:" + ";".join(relaxed),
    )


def _estimate_posterior_for_problem(
    problem: ResidualProblem,
    obs: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 3000,
    seed: int = 0,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    warehouse_tol: int = 8,
    total_item_count_tol: int = 0,
    q6_residual_boost: float = 1.0,
    q6_residual_prior_floor_ratio: float = 0.0,
    q6_residual_prior_cell_floor_ratio: float = 0.0,
    q6_residual_value_power: float = 0.0,
    q6_conditional_target_count: float = 0.0,
    q6_conditional_target_cells: float = 0.0,
    q6_conditional_value_power: float = 0.0,
    extra_diagnostics: tuple[str, ...] = (),
) -> PosteriorReport:
    sampler = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_residual_boost=q6_residual_boost,
        q6_residual_prior_floor_ratio=q6_residual_prior_floor_ratio,
        q6_residual_prior_cell_floor_ratio=q6_residual_prior_cell_floor_ratio,
        q6_residual_value_power=q6_residual_value_power,
        q6_conditional_target_count=q6_conditional_target_count,
        q6_conditional_target_cells=q6_conditional_target_cells,
        q6_conditional_value_power=q6_conditional_value_power,
    )
    session_prior = sampler.session_drop_prior()
    q6_prior = sampler.quality_drop_prior(6)
    tail_replacement_values = sampler.tail_replacement_values()
    rng = np.random.default_rng(seed)
    values: list[int] = []
    decision_values: list[int] = []
    tail_replacement_decision_values: list[int] = []
    cells: list[int] = []
    q6_values: list[int] = []
    q6_decision_values: list[int] = []
    q6_tail_replacement_decision_values: list[int] = []
    q6_counts: list[int] = []
    q6_cells: list[int] = []
    remaining_cells_after_layout: list[int] = []
    q6_space_pressures: list[float] = []
    q6_space_overflows: list[int] = []
    weights: list[float] = []
    random_sample_floor_passes: list[bool] = []
    known_q6_anchor_cells = sum(
        anchor.cells for anchor in problem.anchors if anchor.quality == 6
    )
    base_trials = max(0, int(n_trials))
    trial_limit = base_trials + (
        RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS
        if problem.random_sample_value_floor is not None
        else 0
    )
    attempts = 0
    while attempts < trial_limit:
        if (
            attempts >= base_trials
            and problem.random_sample_value_floor is not None
            and sum(1 for passed in random_sample_floor_passes if passed)
            >= _random_sample_hard_floor_min_matched(len(values))
        ):
            break
        attempts += 1
        truth = sampler.sample(rng=rng)
        if not truth_matches_obs(
            truth,
            obs,
            cells_tol=cells_tol,
            count_tol=count_tol,
            value_rel_tol=value_rel_tol,
            warehouse_tol=warehouse_tol,
            total_item_count_tol=total_item_count_tol,
        ):
            continue
        layout_score = layout_feasibility_score(truth, problem.layout)
        if layout_score <= 0:
            continue
        value_score = value_evidence_score(truth, problem)
        if value_score <= 0:
            continue
        size_avg_score = size_avg_value_evidence_score(truth, problem)
        if size_avg_score <= 0:
            continue
        cell_score = cell_evidence_score(truth, problem)
        if cell_score <= 0:
            continue
        global_score = global_evidence_score(
            truth,
            problem,
            random_sample_mode="ignore",
        )
        if global_score <= 0:
            continue
        weight = (
            category_observation_soft_score(truth, obs)
            * layout_score
            * value_score
            * size_avg_score
            * cell_score
            * global_score
        )
        values.append(truth.total_value())
        decision_values.append(decision_value_for_truth(truth, problem))
        tail_replacement_decision_values.append(
            tail_replacement_decision_value_for_truth(
                truth,
                problem,
                tail_replacement_values,
            )
        )
        cells.append(truth.warehouse_total_cells)
        q6_bucket = truth.buckets.get(6)
        q6_values.append(q6_bucket.value_sum if q6_bucket is not None else 0)
        q6_counts.append(q6_bucket.count if q6_bucket is not None else 0)
        q6_cell_count = q6_bucket.total_cells if q6_bucket is not None else 0
        q6_cells.append(q6_cell_count)
        q6_decision_values.append(q6_decision_value_for_truth(truth, problem))
        q6_tail_replacement_decision_values.append(
            q6_tail_replacement_decision_value_for_truth(
                truth,
                problem,
                tail_replacement_values,
            )
        )
        remaining_cells = max(
            0,
            int(truth.warehouse_total_cells) - int(problem.layout.occupied_cells),
        )
        remaining_cells_after_layout.append(remaining_cells)
        q6_residual_cell_count = max(0, q6_cell_count - known_q6_anchor_cells)
        if problem.layout.occupied_cells > 0:
            q6_space_pressures.append(
                q6_residual_cell_count / max(1, remaining_cells)
            )
            q6_space_overflows.append(
                1 if q6_residual_cell_count > remaining_cells else 0
            )
        else:
            q6_space_pressures.append(0.0)
            q6_space_overflows.append(0)
        weights.append(weight)
        random_sample_floor_passes.append(
            _random_sample_floor_passes(truth, problem)
        )
    diagnostics = [*extra_diagnostics, *problem.diagnostics]
    random_sample_floor_mode = "none"
    random_sample_floor_hard_matches = 0
    random_sample_floor_min_hard_matches = 0
    if problem.random_sample_value_floor is not None and values:
        random_sample_floor_hard_matches = sum(
            1 for passed in random_sample_floor_passes if passed
        )
        random_sample_floor_min_hard_matches = _random_sample_hard_floor_min_matched(
            len(values)
        )
        if random_sample_floor_hard_matches >= random_sample_floor_min_hard_matches:
            keep = [
                idx
                for idx, passed in enumerate(random_sample_floor_passes)
                if passed
            ]
            values = [values[idx] for idx in keep]
            decision_values = [decision_values[idx] for idx in keep]
            tail_replacement_decision_values = [
                tail_replacement_decision_values[idx] for idx in keep
            ]
            cells = [cells[idx] for idx in keep]
            q6_values = [q6_values[idx] for idx in keep]
            q6_decision_values = [q6_decision_values[idx] for idx in keep]
            q6_tail_replacement_decision_values = [
                q6_tail_replacement_decision_values[idx] for idx in keep
            ]
            q6_counts = [q6_counts[idx] for idx in keep]
            q6_cells = [q6_cells[idx] for idx in keep]
            remaining_cells_after_layout = [
                remaining_cells_after_layout[idx] for idx in keep
            ]
            q6_space_pressures = [q6_space_pressures[idx] for idx in keep]
            q6_space_overflows = [q6_space_overflows[idx] for idx in keep]
            weights = [weights[idx] for idx in keep]
            random_sample_floor_mode = "hard"
        else:
            weights = [
                weight
                if passed
                else weight * RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY
                for weight, passed in zip(weights, random_sample_floor_passes)
            ]
            random_sample_floor_mode = "soft"
        diagnostics.append(
            "public_random_sample_value_floor_mode:"
            f"{random_sample_floor_mode}:"
            f"pass={random_sample_floor_hard_matches}/"
            f"{len(random_sample_floor_passes)}:"
            f"min={random_sample_floor_min_hard_matches}:"
            f"attempts={attempts}"
        )
    if q6_residual_boost > 1.0:
        diagnostics.append(f"q6_residual_boost:{q6_residual_boost:.2f}")
    if q6_residual_prior_floor_ratio > 0:
        diagnostics.append(
            f"q6_residual_prior_floor_ratio:{q6_residual_prior_floor_ratio:.2f}"
        )
    if q6_residual_prior_cell_floor_ratio > 0:
        diagnostics.append(
            "q6_residual_prior_cell_floor_ratio:"
            f"{q6_residual_prior_cell_floor_ratio:.2f}"
        )
    if q6_residual_value_power > 0:
        diagnostics.append(f"q6_residual_value_power:{q6_residual_value_power:.2f}")
    if q6_conditional_target_count > 0 or q6_conditional_target_cells > 0:
        diagnostics.append(
            "q6_conditional_target:"
            f"count={q6_conditional_target_count:.2f}:"
            f"cells={q6_conditional_target_cells:.1f}:"
            f"value_power={q6_conditional_value_power:.2f}"
        )
    q6_match_rate = _weighted_positive_rate(q6_values, weights)
    q6_drop_prior_allowed = not (
        problem.max_quality is not None and problem.max_quality < 6
    )
    if (
        q6_drop_prior_allowed
        and
        6 not in problem.bucket_targets
        and q6_match_rate is not None
        and q6_match_rate < 0.10
    ):
        diagnostics.append(f"q6_unconstrained_low_sample_rate:{q6_match_rate:.3f}")
    if (
        q6_drop_prior_allowed
        and
        6 not in problem.bucket_targets
        and q6_prior is not None
        and q6_prior.session_probability >= 0.50
        and q6_match_rate is not None
        and q6_match_rate < q6_prior.session_probability * 0.35
    ):
        diagnostics.append(
            "q6_below_drop_prior:"
            f"{q6_match_rate:.3f}<prior:{q6_prior.session_probability:.3f}"
        )
    return PosteriorReport(
        map_id=problem.map_id,
        map_name=problem.map_name,
        n_total=attempts,
        n_matched=len(values),
        total_cells=_quantiles(cells, weights),
        total_value=_quantiles(values, weights),
        anchor_count=problem.known_item_count,
        known_cells=problem.known_cells,
        known_value=problem.known_value,
        layout_score=problem.layout.score,
        decision_value=_quantiles(decision_values, weights),
        tail_replacement_decision_value=_quantiles(
            tail_replacement_decision_values,
            weights,
        ),
        q6_match_rate=q6_match_rate,
        q6_value=_quantiles(q6_values, weights),
        q6_decision_value=_quantiles(q6_decision_values, weights),
        q6_tail_replacement_decision_value=_quantiles(
            q6_tail_replacement_decision_values,
            weights,
        ),
        q6_count=_quantiles(q6_counts, weights),
        q6_cells=_quantiles(q6_cells, weights),
        remaining_cells_after_layout=_quantiles(remaining_cells_after_layout, weights),
        q6_space_pressure=_quantiles(q6_space_pressures, weights),
        q6_space_overflow_rate=_weighted_positive_rate(q6_space_overflows, weights),
        q6_prior_match_rate=(
            q6_prior.session_probability if q6_prior is not None else None
        ),
        q6_prior_expected_count=(
            q6_prior.expected_session_count if q6_prior is not None else None
        ),
        q6_prior_expected_cells=(
            q6_prior.expected_session_cells if q6_prior is not None else None
        ),
        q6_prior_expected_value=(
            q6_prior.expected_session_value if q6_prior is not None else None
        ),
        prior_expected_count=(
            session_prior.expected_session_count if session_prior is not None else None
        ),
        prior_expected_cells=(
            session_prior.expected_session_cells if session_prior is not None else None
        ),
        prior_expected_value=(
            session_prior.expected_session_value if session_prior is not None else None
        ),
        prior_expected_decision_value=(
            session_prior.expected_session_decision_value
            if session_prior is not None
            else None
        ),
        prior_expected_tail_replacement_decision_value=(
            session_prior.expected_session_tail_replacement_decision_value
            if session_prior is not None
            else None
        ),
        shape_target_count=len(problem.shape_targets),
        category_target_count=len(problem.category_targets),
        category_exclusion_count=sum(
            len(target.excluded_categories)
            for target in problem.category_targets
        ),
        random_sample_avg_values=problem.random_sample_avg_values,
        size_avg_value_targets=problem.size_avg_value_targets,
        layout_diagnostics=problem.layout.diagnostics,
        diagnostics=tuple(diagnostics),
    )


def estimate_posterior_v2(
    map_id: int,
    obs: SessionObs,
    store: EvidenceStore,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 3000,
    seed: int = 0,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    warehouse_tol: int = 8,
    total_item_count_tol: int = 0,
    q6_residual_boost: float = 1.0,
    q6_residual_prior_floor_ratio: float = 0.0,
    q6_residual_prior_cell_floor_ratio: float = 0.0,
    q6_residual_value_power: float = 0.0,
    q6_conditional_target_count: float = 0.0,
    q6_conditional_target_cells: float = 0.0,
    q6_conditional_value_power: float = 0.0,
    size_bucket_prefill: bool = False,
    size_bucket_mask_residual_pool: bool = False,
) -> PosteriorReport:
    """Estimate a posterior with exact item anchors forced into every trial."""

    problem = build_residual_problem(
        map_id,
        store,
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
        size_bucket_prefill=size_bucket_prefill,
        size_bucket_mask_residual_pool=size_bucket_mask_residual_pool,
    )
    strict = _estimate_posterior_for_problem(
        problem,
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=n_trials,
        seed=seed,
        cells_tol=cells_tol,
        count_tol=count_tol,
        value_rel_tol=value_rel_tol,
        warehouse_tol=warehouse_tol,
        total_item_count_tol=total_item_count_tol,
        q6_residual_boost=q6_residual_boost,
        q6_residual_prior_floor_ratio=q6_residual_prior_floor_ratio,
        q6_residual_prior_cell_floor_ratio=q6_residual_prior_cell_floor_ratio,
        q6_residual_value_power=q6_residual_value_power,
        q6_conditional_target_count=q6_conditional_target_count,
        q6_conditional_target_cells=q6_conditional_target_cells,
        q6_conditional_value_power=q6_conditional_value_power,
    )
    if strict.n_matched > 0:
        return strict

    relaxed_obs, diagnostics = _relax_exact_bucket_obs(obs)
    if not diagnostics:
        return strict
    relaxed_problem = build_residual_problem(
        map_id,
        store,
        maps=maps,
        drops=drops,
        items=items,
        obs=relaxed_obs,
        size_bucket_prefill=size_bucket_prefill,
        size_bucket_mask_residual_pool=size_bucket_mask_residual_pool,
    )
    return _estimate_posterior_for_problem(
        relaxed_problem,
        relaxed_obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=n_trials,
        seed=seed,
        cells_tol=cells_tol,
        count_tol=count_tol,
        value_rel_tol=value_rel_tol,
        warehouse_tol=warehouse_tol,
        total_item_count_tol=total_item_count_tol,
        q6_residual_boost=q6_residual_boost,
        q6_residual_prior_floor_ratio=q6_residual_prior_floor_ratio,
        q6_residual_prior_cell_floor_ratio=q6_residual_prior_cell_floor_ratio,
        q6_residual_value_power=q6_residual_value_power,
        q6_conditional_target_count=q6_conditional_target_count,
        q6_conditional_target_cells=q6_conditional_target_cells,
        q6_conditional_value_power=q6_conditional_value_power,
        extra_diagnostics=diagnostics,
    )


__all__ = (
    "ConditionalSampler",
    "EvidenceFact",
    "EvidenceStore",
    "EvidenceStoreBuilder",
    "KnownItemAnchor",
    "KnownFootprint",
    "LayoutFeasibility",
    "PosteriorReport",
    "QualityDropPrior",
    "SessionDropPrior",
    "RANDOM_SAMPLE_HARD_FLOOR_MAX_MIN_MATCHED",
    "RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS",
    "RANDOM_SAMPLE_HARD_FLOOR_MIN_MATCHED",
    "RANDOM_SAMPLE_HARD_FLOOR_MIN_RATE",
    "RANDOM_SAMPLE_VALUE_FLOOR_SOFT_PENALTY",
    "RANDOM_SAMPLE_VALUE_FLOOR_FACTOR",
    "ResidualBucketTarget",
    "ResidualProblem",
    "RuntimeEvidence",
    "ShapeTarget",
    "build_residual_problem",
    "category_targets_from_store",
    "cell_evidence_score",
    "decision_value_for_truth",
    "estimate_posterior_v2",
    "evidence_store_from_fatbeans_events",
    "global_evidence_score",
    "is_tail_supported_by_evidence",
    "known_footprints",
    "known_item_anchors",
    "layout_feasibility_from_store",
    "layout_feasibility_score",
    "q6_decision_value_for_truth",
    "shape_targets_from_store",
    "SizeBucketEvidence",
    "size_avg_value_evidence_score",
    "SIZE_AVG_VALUE_SIGNAL_FLOOR",
    "SIZE_AVG_VALUE_SIGNAL_FLOORS",
    "actionable_size_avg_value_targets",
    "value_evidence_score",
)
