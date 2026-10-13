"""Per-footprint (1/2/3/4/6 cell) average-value evidence for v2.

Interprets size-avg tool readings (100169-100173) with optional exact counts from
full outline (100100 / Ethan 1002085) or shaped runtime evidence. Low readings stay
soft-only; high readings with reliable counts can apply value-sum floors on samples.

See docs/size_avg_interpretation.zh-CN.md for human-readable tier tables.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import hashlib
from itertools import product
import json
import math
import os
import re
import struct
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.mean_multiset_support import (
    MEAN_SUPPORT_MAX_COUNT,
    MEAN_SUPPORT_MAX_INTERVAL_WIDTH,
    MEAN_SUPPORT_TRANSITION_BUDGET,
    MeanSupportCandidate,
    _normalized_candidate,
    solve_repeat_allowed_mean_support,
)

EvidenceStrength = Literal["soft", "hard_floor"]

# Signal floors (tool noise gate) — settlement p10 tuned; see analyze_size_avg_value_thresholds.py
SIZE_AVG_VALUE_SIGNAL_FLOORS: dict[int, float] = {
    1: 2_000.0,
    2: 2_000.0,
    3: 3_000.0,
    4: 5_000.0,
    6: 4_000.0,
}
SIZE_AVG_VALUE_SIGNAL_FLOOR = min(SIZE_AVG_VALUE_SIGNAL_FLOORS.values())

# Warehouse bands from 333 settlement sessions (total occupied cells).
WAREHOUSE_SMALL_MAX = 94
WAREHOUSE_MID_MAX = 127

# Anchor unit prices (silver) for dilution checks — 羊脂白玉籽 is 2-cell, not 羊脂玉璧.
SIZE_AVG_ANCHORS: tuple[tuple[str, int, int], ...] = (
    ("black_prince", 1, 3_000_000),
    ("yangzhi_jade", 2, 2_516_000),
    ("renshen", 2, 1_039_000),
    ("plane_box", 4, 1_688_400),
    ("yongle", 4, 1_500_000),
    ("black_box", 4, 7_402_320),
)

# Below these avg readings we only soft-score (typical filler / diluted pools).
FILLER_MAX_AVG_BY_CELLS: dict[int, float] = {
    1: 25_000.0,
    2: 30_000.0,
    3: 30_000.0,
    4: 30_000.0,
    6: 50_000.0,
}

# Minimum avg before we consider hard value-sum floor (needs count_exact too).
HIGH_VALUE_FLOOR_MIN_AVG: dict[int, float] = {
    1: 150_000.0,
    2: 80_000.0,
    4: 80_000.0,
    6: 100_000.0,
}

# Retained legacy configuration symbol for imported diagnostics. Complete
# mathematical support no longer uses a price threshold for hard common facts.
SHAPE_AVG_ACTIVE_NONUNIQUE_MIN_AVG: dict[int, float] = {
    4: 500_000.0,
}
SHAPE_AVG_ACTIVE_JOINT_DISABLE = "BIDKING_DISABLE_SHAPE_AVG_ACTIVE_JOINT"
SHAPE_AVG_FLOAT32_COUNT_CONSTRAINT_MODE = (
    "integer_total_roundtrips_to_float32_mean"
)

# Typical 4-cell item counts by warehouse band (settlement p50).
TYPICAL_SIZE_COUNT_BY_BAND: dict[str, dict[int, int]] = {
    "small": {1: 19, 2: 5, 4: 7, 6: 2},
    "mid": {1: 19, 2: 7, 4: 10, 6: 2},
    "large": {1: 19, 2: 9, 4: 13, 6: 2},
}

_FULL_OUTLINE_SOURCES = ("action:100100", "skill:1002085")

# Bounds every dense integer bitset/FFT axis.  Pivot q6 stays sparse and is
# governed by reachable_sum_budget instead, so a high item price cannot alone
# allocate a giant dense array.
MEAN_PARTITION_BITSET_WIDTH_BUDGET = 2_000_000
_QUALITY_COUNT = 6


@dataclass(frozen=True)
class MeanPartitionSupportState:
    item_count: int
    total_cells: int
    total_value: int
    pivot_quality: int
    pivot_count: int
    pivot_value: int
    other_quality_count_ranges: tuple[tuple[int, int], ...]
    other_quality_value_ranges: tuple[tuple[int, int], ...]

    def as_dict(self) -> dict[str, Any]:
        cells_per_item = self.total_cells // self.item_count
        count_ranges = {
            f"q{quality}": [int(lo), int(hi)]
            for quality, (lo, hi) in enumerate(
                self.other_quality_count_ranges, start=1
            )
            if lo or hi
        }
        value_ranges = {
            f"q{quality}": [int(lo), int(hi)]
            for quality, (lo, hi) in enumerate(
                self.other_quality_value_ranges, start=1
            )
            if lo or hi
        }
        pivot_key = f"q{self.pivot_quality}"
        count_ranges[pivot_key] = [self.pivot_count, self.pivot_count]
        value_ranges[pivot_key] = [self.pivot_value, self.pivot_value]
        return {
            "item_count": self.item_count,
            "total_cells": self.total_cells,
            "total_value": self.total_value,
            "quality_counts": {pivot_key: self.pivot_count},
            "quality_cells": {
                pivot_key: self.pivot_count * cells_per_item
            },
            "quality_count_ranges": count_ranges,
            "quality_cells_ranges": {
                key: [lo * cells_per_item, hi * cells_per_item]
                for key, (lo, hi) in (
                    (key, tuple(value)) for key, value in count_ranges.items()
                )
            },
            "quality_values": {pivot_key: self.pivot_value},
            "quality_value_ranges": value_ranges,
            "aggregate_unique": False,
            "support_envelope_kind": "complete_partition_envelope",
        }


@dataclass(frozen=True)
class MeanPartitionSupportResult:
    status: str
    states: tuple[MeanPartitionSupportState, ...] = ()
    count_one_candidate_item_ids: tuple[int, ...] = ()


_BIT_REVERSE_BYTES = bytes(
    int(f"{value:08b}"[::-1], 2) for value in range(256)
)


def _integer_bitset(values: set[int], *, width: int | None = None) -> int:
    if not values:
        return 0
    resolved_width = width or max(values) + 1
    if resolved_width <= 0 or any(
        value < 0 or value >= resolved_width for value in values
    ):
        return 0
    array = np.zeros(resolved_width, dtype=np.bool_)
    array[list(values)] = True
    return int.from_bytes(
        np.packbits(array, bitorder="little").tobytes(),
        "little",
    )


def _reverse_integer_bitset(value: int, width: int) -> int:
    byte_count = (width + 7) // 8
    padding = byte_count * 8 - width
    return int.from_bytes(
        value.to_bytes(byte_count, "little")
        .translate(_BIT_REVERSE_BYTES)[::-1],
        "little",
    ) >> padding


def _aligned_reversed_bitset(value: int, width: int, target: int) -> int:
    shift = width - 1 - target
    return value >> shift if shift >= 0 else value << (-shift)


def solve_repeat_allowed_mean_partition_support(
    candidates: Sequence[MeanSupportCandidate | Mapping[str, Any]],
    *,
    total_lower: int | None = None,
    total_upper: int | None = None,
    item_count: int | None = None,
    total_intervals_by_count: Mapping[int, Sequence[int]] | None = None,
    quality_count_options: Mapping[int, Sequence[int]],
    pivot_quality: int = 6,
    reachable_sum_budget: int = 250_000,
    bitset_width_budget: int = MEAN_PARTITION_BITSET_WIDTH_BUDGET,
    output_state_budget: int = 100_000,
) -> MeanPartitionSupportResult:
    """Complete high-tier/low-tier support with exact publication checks.

    FFT proposes q4+q5 sums only. Every state that can be published is
    rechecked using integer bitsets, and numerical drift returns no support.
    """

    normalized = tuple(_normalized_candidate(candidate) for candidate in candidates)
    if not normalized or any(candidate is None for candidate in normalized):
        return MeanPartitionSupportResult("invalid_candidates")
    canonical = tuple(candidate for candidate in normalized if candidate is not None)
    if len({candidate.item_id for candidate in canonical}) != len(canonical):
        return MeanPartitionSupportResult("duplicate_candidate_item_id")
    footprint_cells = canonical[0].cells
    if any(candidate.cells != footprint_cells for candidate in canonical):
        return MeanPartitionSupportResult("mixed_footprint_cells")
    intervals: dict[int, tuple[int, int]] = {}
    if total_intervals_by_count is None:
        if (
            type(item_count) is int  # noqa: E721
            and type(total_lower) is int  # noqa: E721
            and type(total_upper) is int  # noqa: E721
        ):
            intervals[item_count] = (total_lower, total_upper)
    elif isinstance(total_intervals_by_count, Mapping):
        for raw_count, raw_interval in total_intervals_by_count.items():
            if (
                type(raw_count) is not int  # noqa: E721
                or not isinstance(raw_interval, Sequence)
                or isinstance(raw_interval, (str, bytes))
                or len(raw_interval) != 2
                or type(raw_interval[0]) is not int  # noqa: E721
                or type(raw_interval[1]) is not int  # noqa: E721
            ):
                return MeanPartitionSupportResult("invalid_input")
            intervals[raw_count] = (raw_interval[0], raw_interval[1])
    if (
        not intervals
        or any(
            count <= 0
            or count > MEAN_SUPPORT_MAX_COUNT
            or lower <= 0
            or upper < lower
            or upper - lower + 1 > MEAN_SUPPORT_MAX_INTERVAL_WIDTH
            for count, (lower, upper) in intervals.items()
        )
        or pivot_quality != 6
        or type(reachable_sum_budget) is not int  # noqa: E721
        or reachable_sum_budget <= 0
        or type(bitset_width_budget) is not int  # noqa: E721
        or bitset_width_budget <= 0
        or type(output_state_budget) is not int  # noqa: E721
        or output_state_budget <= 0
    ):
        return MeanPartitionSupportResult("invalid_input")

    allowed: dict[int, tuple[int, ...]] = {}
    for quality in range(1, _QUALITY_COUNT + 1):
        raw = quality_count_options.get(quality)
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes))
            or not raw
            or any(
                type(value) is not int or value < 0  # noqa: E721
                for value in raw
            )
        ):
            return MeanPartitionSupportResult("invalid_quality_count_options")
        allowed[quality] = tuple(sorted(set(raw)))
    if allowed[2] != (0,):
        return MeanPartitionSupportResult("unsupported_quality_partition")

    qualities = (1, 3, 4, 5, 6)
    values_by_quality: dict[int, set[int]] = {
        quality: set() for quality in qualities
    }
    for candidate in canonical:
        if candidate.quality in values_by_quality:
            values_by_quality[candidate.quality].add(candidate.value)
    if any(
        max(allowed[quality]) > 0 and not values_by_quality[quality]
        for quality in qualities
    ):
        return MeanPartitionSupportResult("quality_count_support_empty")
    if any(
        max(allowed[quality]) * max(values_by_quality[quality], default=0) + 1
        > bitset_width_budget
        for quality in qualities
        if quality != pivot_quality
    ):
        return MeanPartitionSupportResult("partition_complexity_guard")

    reachable: dict[tuple[int, int], set[int]] = {}
    for quality, catalog_values in values_by_quality.items():
        sums = {0}
        reachable[quality, 0] = sums
        for count in range(1, max(allowed[quality]) + 1):
            sums = {
                prior + value
                for prior in sums
                for value in catalog_values
            }
            if len(sums) > reachable_sum_budget:
                return MeanPartitionSupportResult("partition_complexity_guard")
            reachable[quality, count] = sums

    exact_bits = {
        key: _integer_bitset(sums)
        for key, sums in reachable.items()
        if key[0] != pivot_quality
    }

    def pair_bitset(
        quality_a: int,
        count_a: int,
        quality_b: int,
        count_b: int,
    ) -> int:
        values_a = reachable[quality_a, count_a]
        values_b = reachable[quality_b, count_b]
        if len(values_a) > len(values_b):
            values_a, values_b = values_b, values_a
            quality_a, quality_b = quality_b, quality_a
            count_a, count_b = count_b, count_a
        base = exact_bits[quality_b, count_b]
        output = 0
        for value in values_a:
            output |= base << value
        return output

    low_left: dict[int, int] = {}
    for count_q1 in allowed[1]:
        for count_q3 in allowed[3]:
            count = count_q1 + count_q3
            low_left[count] = low_left.get(count, 0) | pair_bitset(
                1, count_q1, 3, count_q3
            )

    low_right_max = (
        max(allowed[4]) * max(values_by_quality[4], default=0)
        + max(allowed[5]) * max(values_by_quality[5], default=0)
    )
    if low_right_max + 1 > bitset_width_budget:
        return MeanPartitionSupportResult("partition_complexity_guard")
    fft_size = 1
    while fft_size <= low_right_max:
        fft_size *= 2
    spectra: dict[tuple[int, int], np.ndarray] = {}
    for quality in (4, 5):
        for count in allowed[quality]:
            array = np.zeros(fft_size, dtype=np.float64)
            array[list(reachable[quality, count])] = 1.0
            spectra[quality, count] = np.fft.rfft(array)
    low_right: dict[int, int] = {}
    for combined_count in range(max(allowed[4]) + max(allowed[5]) + 1):
        products = [
            spectra[4, count_q4] * spectra[5, count_q5]
            for count_q4 in allowed[4]
            for count_q5 in (combined_count - count_q4,)
            if count_q5 in allowed[5]
        ]
        if not products:
            continue
        spectrum = products[0]
        for row in products[1:]:
            spectrum = spectrum + row
        convolution = np.fft.irfft(spectrum, n=fft_size)
        rounded = np.rint(convolution)
        if float(np.max(np.abs(convolution - rounded))) > 0.01:
            return MeanPartitionSupportResult("partition_numerical_guard")
        low_right[combined_count] = int.from_bytes(
            np.packbits(rounded > 0.0, bitorder="little").tobytes(),
            "little",
        )

    right_width = low_right_max + 1
    reversed_low_right = {
        count: _reverse_integer_bitset(bitset, right_width)
        for count, bitset in low_right.items()
    }
    q5_width = max(allowed[5]) * max(values_by_quality[5], default=0) + 1
    reversed_q5 = {
        count: _reverse_integer_bitset(exact_bits[5, count], q5_width)
        for count in allowed[5]
    }
    low_total_max = low_right_max + max(
        (bitset.bit_length() - 1 for bitset in low_left.values()),
        default=0,
    )

    def exact_right_pair_exists(combined_count: int, target: int) -> bool:
        if target < 0 or target > low_right_max:
            return False
        for count_q4 in allowed[4]:
            count_q5 = combined_count - count_q4
            if count_q5 not in allowed[5]:
                continue
            if exact_bits[4, count_q4] & _aligned_reversed_bitset(
                reversed_q5[count_q5], q5_width, target
            ):
                return True
        return False

    def exact_low_support_exists(low_count: int, target: int) -> bool:
        if target < 0 or target > low_total_max:
            return False
        for left_count, left_bitset in low_left.items():
            right_count = low_count - left_count
            if right_count not in reversed_low_right:
                continue
            possible_left = left_bitset & _aligned_reversed_bitset(
                reversed_low_right[right_count], right_width, target
            )
            while possible_left:
                lowest = possible_left & -possible_left
                left_value = lowest.bit_length() - 1
                if exact_right_pair_exists(right_count, target - left_value):
                    return True
                possible_left -= lowest
        return False

    low_range_cache: dict[
        int,
        tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]],
    ] = {}

    def low_ranges(
        low_count: int,
    ) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
        cached = low_range_cache.get(low_count)
        if cached is not None:
            return cached
        vectors = tuple(
            vector
            for vector in product(
                allowed[1], allowed[3], allowed[4], allowed[5]
            )
            if sum(vector) == low_count
        )
        count_ranges = [(0, 0) for _ in range(_QUALITY_COUNT)]
        value_ranges = [(0, 0) for _ in range(_QUALITY_COUNT)]
        for index, quality in enumerate((1, 3, 4, 5)):
            counts = [vector[index] for vector in vectors]
            lo = min(counts, default=0)
            hi = max(counts, default=0)
            count_ranges[quality - 1] = (lo, hi)
            values = values_by_quality[quality]
            value_ranges[quality - 1] = (
                lo * min(values, default=0),
                hi * max(values, default=0),
            )
        cached = (tuple(count_ranges), tuple(value_ranges))
        low_range_cache[low_count] = cached
        return cached

    states: list[MeanPartitionSupportState] = []
    for resolved_item_count, (resolved_lower, resolved_upper) in sorted(
        intervals.items()
    ):
        for pivot_count in allowed[pivot_quality]:
            low_count = resolved_item_count - pivot_count
            if low_count < 0:
                continue
            count_ranges, value_ranges = low_ranges(low_count)
            for pivot_value in reachable[pivot_quality, pivot_count]:
                for total_value in range(resolved_lower, resolved_upper + 1):
                    if not exact_low_support_exists(
                        low_count, total_value - pivot_value
                    ):
                        continue
                    states.append(
                        MeanPartitionSupportState(
                            item_count=resolved_item_count,
                            total_cells=resolved_item_count * footprint_cells,
                            total_value=total_value,
                            pivot_quality=pivot_quality,
                            pivot_count=pivot_count,
                            pivot_value=pivot_value,
                            other_quality_count_ranges=count_ranges,
                            other_quality_value_ranges=value_ranges,
                        )
                    )
                    if len(states) > output_state_budget:
                        return MeanPartitionSupportResult(
                            "output_complexity_guard"
                        )

    count_one_ids: tuple[int, ...] = ()
    count_one_interval = intervals.get(1)
    if count_one_interval is not None:
        resolved_lower, resolved_upper = count_one_interval
        count_one_ids = tuple(
            sorted(
                candidate.item_id
                for candidate in canonical
                if resolved_lower <= candidate.value <= resolved_upper
                and 1 in allowed[candidate.quality]
            )
        )
    return MeanPartitionSupportResult("ok", tuple(states), count_one_ids)


# Tool action ids -> footprint cells (100169-100173).
SIZE_AVG_ACTION_FOOTPRINT: dict[int, int] = {
    100169: 1,
    100170: 2,
    100171: 3,
    100172: 4,
    100173: 6,
}
SIZE_AVG_ACTION_IDS = frozenset(SIZE_AVG_ACTION_FOOTPRINT)
SHAPE_AVG_EXACT_CONSTRAINT_DISABLE = (
    "BIDKING_DISABLE_SHAPE_AVG_EXACT_CONSTRAINT"
)


@dataclass(frozen=True)
class SizeBucketEvidence:
    """Combined count + avg evidence for one footprint size."""

    cells: int
    avg_value: float
    count_exact: int | None = None
    count_partial: int | None = None
    value_floor: int | None = None
    strength: EvidenceStrength = "soft"
    anchor_hint: str | None = None
    tier: str | None = None
    warehouse_band: str | None = None
    count_provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class SizeBucketCountPartition:
    counts: Mapping[int, int]
    exact: bool
    provenance: tuple[str, ...]


def warehouse_size_band(warehouse_total_cells: int | None) -> str | None:
    if warehouse_total_cells is None or warehouse_total_cells <= 0:
        return None
    cells = int(warehouse_total_cells)
    if cells <= WAREHOUSE_SMALL_MAX:
        return "small"
    if cells <= WAREHOUSE_MID_MAX:
        return "mid"
    return "large"


def actionable_size_avg_value_targets(
    values: Any,
    *,
    signal_floors: dict[int, float] | None = None,
) -> tuple[tuple[int, float], ...]:
    floors = signal_floors or SIZE_AVG_VALUE_SIGNAL_FLOORS
    return tuple(
        (int(cells), float(value))
        for cells, value in (values or ())
        if float(value) >= floors.get(int(cells), SIZE_AVG_VALUE_SIGNAL_FLOOR)
    )


def _revealed_counts_by_cells(store: Any) -> dict[int, int]:
    counts: Counter[int] = Counter()
    observations_by_identity = getattr(store, "footprint_observations", {})
    if isinstance(observations_by_identity, Mapping) and observations_by_identity:
        conflicts = getattr(store, "footprint_conflicts", {})
        alias_conflicts = getattr(store, "footprint_alias_conflicts", {})
        for identity_key, observations in observations_by_identity.items():
            if (
                isinstance(conflicts, Mapping)
                and identity_key in conflicts
                or isinstance(alias_conflicts, Mapping)
                and identity_key in alias_conflicts
            ):
                continue
            footprints = tuple(
                dict.fromkeys(
                    footprint
                    for observation in observations
                    if (footprint := _observation_footprint(observation)) is not None
                )
            )
            if len(footprints) == 1:
                counts[footprints[0][1]] += 1
        for evidence in store.items():
            if _physical_identity_key(evidence) is not None:
                continue
            cells = _strict_positive_count(getattr(evidence, "cells", None))
            if cells is not None:
                counts[cells] += 1
        return dict(counts)
    for evidence in store.items():
        if evidence.cells is None:
            continue
        counts[int(evidence.cells)] += 1
    return dict(counts)


def _strict_positive_count(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _physical_identity_key(evidence: Any) -> str | None:
    runtime_id = getattr(evidence, "runtime_id", None)
    if runtime_id is not None:
        return f"runtime:{runtime_id}"
    local_index = getattr(evidence, "local_index", None)
    if local_index is not None:
        return f"local:{local_index}"
    return None


def _observation_footprint(observation: Any) -> tuple[str, int] | None:
    shape_key = getattr(observation, "shape_key", None)
    cells = _strict_positive_count(getattr(observation, "cells", None))
    if not isinstance(shape_key, str) or not shape_key or cells is None:
        return None
    return shape_key, cells


def _full_outline_coverage_status(
    store: Any,
    total_item_count: int | None,
) -> tuple[bool, str | None]:
    items = tuple(store.items())
    if not items:
        return False, None
    conflicts = getattr(store, "footprint_conflicts", {})
    if isinstance(conflicts, Mapping) and conflicts:
        return False, "unstable_footprint_identity"
    alias_conflicts = getattr(store, "footprint_alias_conflicts", {})
    if isinstance(alias_conflicts, Mapping) and alias_conflicts:
        return False, "unstable_footprint_identity"
    observations_by_identity = getattr(store, "footprint_observations", {})
    if not isinstance(observations_by_identity, Mapping):
        observations_by_identity = {}
    if any(_physical_identity_key(evidence) is None for evidence in items):
        return False, "unstable_footprint_identity"
    if not observations_by_identity:
        return False, None
    for observations in observations_by_identity.values():
        observations = tuple(observations)
        observed_footprints = tuple(
            dict.fromkeys(
                footprint
                for observation in observations
                if (footprint := _observation_footprint(observation)) is not None
            )
        )
        if len(observed_footprints) != 1:
            return False, "unstable_footprint_identity"
        current_footprint = observed_footprints[0]
        for observation in observations:
            sources = getattr(observation, "sources", ())
            if any(source in _FULL_OUTLINE_SOURCES for source in sources) and (
                _observation_footprint(observation) is None
            ):
                return False, "full_outline_unpaired_identity"
        if not any(
            _observation_footprint(observation) == current_footprint
            and any(
                source in _FULL_OUTLINE_SOURCES
                for source in getattr(observation, "sources", ())
            )
            for observation in observations
        ):
            return False, None
    if total_item_count is not None:
        if len(observations_by_identity) != total_item_count:
            return False, None
    return True, None


def size_bucket_count_partition_from_context(
    store: Any,
    *,
    total_item_count: int | None,
    layout_footprint_count: int = 0,
    trusted_footprint_count: int = 0,
) -> SizeBucketCountPartition:
    """Return footprint counts with an explicit warehouse-coverage provenance."""
    revealed = _revealed_counts_by_cells(store)
    exact_total = _strict_positive_count(total_item_count)
    if total_item_count is not None and exact_total is None:
        return SizeBucketCountPartition(
            revealed,
            False,
            ("partial_revealed_footprints", "invalid_exact_total_item_count"),
        )
    full_outline, footprint_issue = _full_outline_coverage_status(store, exact_total)
    if footprint_issue is not None:
        return SizeBucketCountPartition(
            revealed,
            False,
            ("partial_revealed_footprints", footprint_issue),
        )
    if not revealed:
        return SizeBucketCountPartition({}, False, ("no_revealed_footprints",))
    if full_outline:
        return SizeBucketCountPartition(
            revealed,
            True,
            ("semantic_full_outline",),
        )
    # A self-consistent layout only proves the partition of what has been
    # revealed.  It cannot prove that the reveal covers the whole warehouse
    # unless an independent exact total count closes the identity set.
    if exact_total is None:
        return SizeBucketCountPartition(
            revealed,
            False,
            ("partial_revealed_footprints", "missing_exact_total_item_count"),
        )
    layout_count = _strict_positive_count(layout_footprint_count)
    trusted_count = _strict_positive_count(trusted_footprint_count)
    if layout_count is None or trusted_count is None:
        return SizeBucketCountPartition(
            revealed,
            False,
            ("partial_revealed_footprints", "invalid_coverage_count"),
        )
    if (
        layout_count == exact_total
        and trusted_count == exact_total
        and sum(revealed.values()) == exact_total
    ):
        return SizeBucketCountPartition(
            revealed,
            True,
            ("exact_total_item_count", "trusted_distinct_footprints"),
        )
    provenance = ["partial_revealed_footprints", "incomplete_identity_coverage"]
    if layout_count is not None:
        provenance.append("layout_observed")
    return SizeBucketCountPartition(revealed, False, tuple(provenance))


def size_bucket_counts_from_context(
    store: Any,
    *,
    total_item_count: int | None,
    layout_footprint_count: int = 0,
    trusted_footprint_count: int = 0,
) -> tuple[dict[int, int], bool]:
    """Compatibility tuple for existing v2 callers."""
    partition = size_bucket_count_partition_from_context(
        store,
        total_item_count=total_item_count,
        layout_footprint_count=layout_footprint_count,
        trusted_footprint_count=trusted_footprint_count,
    )
    return dict(partition.counts), partition.exact


def _classify_avg_tier(
    avg_value: float,
    cells: int,
    *,
    warehouse_band: str | None,
    count_exact: int | None,
) -> str:
    if avg_value < FILLER_MAX_AVG_BY_CELLS.get(cells, 30_000.0):
        return "filler"
    if cells == 4:
        if avg_value >= 650_000:
            return "black_box_likely"
        if avg_value >= 500_000:
            return "ultra_4cell"
        if avg_value >= 150_000:
            return "plane_yongle_singleton"
        if avg_value >= 80_000:
            return "rich_4cell"
        if avg_value >= 30_000 and warehouse_band == "small":
            return "possible_plane_diluted"
        return "elevated_4cell"
    if cells == 2:
        if avg_value >= 200_000:
            return "yangzhi_likely"
        if avg_value >= 80_000:
            return "renshen_or_yangzhi_diluted"
        return "elevated_2cell"
    if cells == 1:
        if avg_value >= 250_000:
            return "black_prince_likely"
        if avg_value >= 80_000:
            return "rich_1cell"
        return "elevated_1cell"
    if cells == 6 and avg_value >= 100_000:
        return "rich_6cell"
    return "elevated"


def _match_anchor_hint(
    avg_value: float,
    cells: int,
    count_exact: int | None,
) -> str | None:
    if count_exact is None or count_exact <= 0:
        return None
    implied_total = avg_value * count_exact
    best_hint: str | None = None
    best_err = 1.0
    for hint, anchor_cells, price in SIZE_AVG_ANCHORS:
        if anchor_cells != cells:
            continue
        err = abs(implied_total - price) / max(1.0, float(price))
        if err < best_err:
            best_err = err
            best_hint = hint
    if best_hint is not None and best_err <= 0.35:
        return best_hint
    return None


def _should_apply_value_floor(
    *,
    avg_value: float,
    cells: int,
    count_exact: int | None,
    anchor_hint: str | None,
    tier: str,
) -> bool:
    if count_exact is None or count_exact <= 0:
        return False
    if tier == "filler":
        return False
    min_avg = HIGH_VALUE_FLOOR_MIN_AVG.get(cells)
    if min_avg is not None and avg_value < min_avg:
        return False
    if anchor_hint is not None:
        return True
    if cells == 4:
        if avg_value >= 500_000:
            return count_exact <= 6
        if avg_value >= 150_000:
            return count_exact <= 4
    if cells == 2 and avg_value >= 200_000:
        return count_exact <= 4
    if cells == 1 and avg_value >= 250_000:
        return count_exact <= 3
    return False


def _value_floor_for_target(
    *,
    avg_value: float,
    count_exact: int,
    anchor_hint: str | None,
    cells: int,
) -> int:
    base = int(avg_value * count_exact * 0.85)
    if anchor_hint is not None:
        for hint, anchor_cells, price in SIZE_AVG_ANCHORS:
            if hint == anchor_hint and anchor_cells == cells:
                return max(base, int(price * 0.70))
    return base


def build_size_bucket_evidence(
    size_avg_value_targets: tuple[tuple[int, float], ...],
    *,
    store: Any,
    warehouse_total_cells: int | None = None,
    total_item_count: int | None = None,
    layout_footprint_count: int = 0,
    trusted_footprint_count: int = 0,
) -> tuple[SizeBucketEvidence, ...]:
    partition = size_bucket_count_partition_from_context(
        store,
        total_item_count=total_item_count,
        layout_footprint_count=layout_footprint_count,
        trusted_footprint_count=trusted_footprint_count,
    )
    counts = partition.counts
    counts_exact = partition.exact
    band = warehouse_size_band(warehouse_total_cells)
    targets: list[SizeBucketEvidence] = []
    for cells, avg_value in size_avg_value_targets:
        count_exact = counts.get(cells) if counts_exact else None
        count_partial = counts.get(cells) if not counts_exact else None
        anchor_hint = _match_anchor_hint(avg_value, cells, count_exact)
        tier = _classify_avg_tier(
            avg_value,
            cells,
            warehouse_band=band,
            count_exact=count_exact,
        )
        strength: EvidenceStrength = "soft"
        value_floor: int | None = None
        if _should_apply_value_floor(
            avg_value=avg_value,
            cells=cells,
            count_exact=count_exact,
            anchor_hint=anchor_hint,
            tier=tier,
        ):
            assert count_exact is not None
            value_floor = _value_floor_for_target(
                avg_value=avg_value,
                count_exact=count_exact,
                anchor_hint=anchor_hint,
                cells=cells,
            )
            strength = "hard_floor"
        targets.append(
            SizeBucketEvidence(
                cells=cells,
                avg_value=avg_value,
                count_exact=count_exact,
                count_partial=count_partial,
                value_floor=value_floor,
                strength=strength,
                anchor_hint=anchor_hint,
                tier=tier,
                warehouse_band=band,
                count_provenance=partition.provenance,
            )
        )
    return tuple(targets)


def size_bucket_count_targets_for_structured_evidence(
    store: Any,
    *,
    footprint_cells: Sequence[int],
    total_item_count: int | None,
    layout_footprint_count: int,
    trusted_footprint_count: int,
) -> tuple[dict[str, Any], ...]:
    """Project physical count provenance without any average-value threshold."""

    partition = size_bucket_count_partition_from_context(
        store,
        total_item_count=total_item_count,
        layout_footprint_count=layout_footprint_count,
        trusted_footprint_count=trusted_footprint_count,
    )
    rows: list[dict[str, Any]] = []
    for cells in sorted(set(footprint_cells)):
        if (
            type(cells) is not int
            or cells not in SIZE_AVG_ACTION_FOOTPRINT.values()
        ):
            continue
        row: dict[str, Any] = {
            "cells": int(cells),
            "count_provenance": list(partition.provenance),
        }
        observed_count = int(partition.counts.get(cells, 0))
        if partition.exact:
            # Zero is carried explicitly: a positive mean for a footprint that
            # complete physical coverage proves absent is a conflict, not an
            # invitation to guess a positive N.
            row["count_exact"] = observed_count
        elif observed_count > 0:
            row["count_partial"] = observed_count
        rows.append(row)
    return tuple(rows)


def footprint_count_in_buckets(buckets: Any, cells: int) -> int:
    total = 0
    for bucket in buckets.values():
        for item in bucket.items:
            if item.shape_w * item.shape_h == cells:
                total += 1
    return total


def residual_allowed_for_footprint(
    item: Any,
    buckets: Any,
    targets: tuple[SizeBucketEvidence, ...],
    *,
    add_count: int = 1,
) -> bool:
    area = item.shape_w * item.shape_h
    for target in targets:
        if target.cells != area or target.count_exact is None:
            continue
        current = footprint_count_in_buckets(buckets, area)
        if current + max(0, int(add_count)) > target.count_exact:
            return False
    return True


def prefill_size_bucket_targets(
    pool: Any,
    buckets: dict,
    targets: tuple[SizeBucketEvidence, ...],
    *,
    rng: Any,
    item_allowed: Any,
    add_item: Callable[[dict, Any, int], None],
    max_attempts: int = 400,
) -> None:
    """Place exact-count footprint items before residual sampling (experimental)."""

    for target in targets:
        if target.count_exact is None or target.count_exact <= 0:
            continue
        need = int(target.count_exact) - footprint_count_in_buckets(buckets, target.cells)
        if need <= 0:
            continue
        indexes = [
            idx
            for idx, item in enumerate(pool.items)
            if item.shape_w * item.shape_h == target.cells
            and item_allowed(item)
        ]
        if not indexes:
            continue
        min_sum = int(target.value_floor or target.avg_value * target.count_exact * 0.85)
        placed = False
        for _ in range(max_attempts):
            pick = rng.choice(indexes, size=min(need, len(indexes)), replace=len(indexes) < need)
            chosen = [int(i) for i in pick]
            trial_sum = sum(int(pool.items[i].value) for i in chosen)
            if trial_sum >= min_sum or min_sum <= 0:
                for pool_i in chosen:
                    item = pool.items[pool_i]
                    if residual_allowed_for_footprint(
                        item,
                        buckets,
                        targets,
                        add_count=1,
                    ):
                        add_item(buckets, item)
                placed = footprint_count_in_buckets(buckets, target.cells) >= target.count_exact
                if placed:
                    break
            probs = np.asarray(
                [float(pool.probabilities[i]) for i in indexes],
                dtype=np.float64,
            )
            if float(probs.sum()) <= 0:
                probs = np.ones(len(indexes), dtype=np.float64)
            probs = probs / probs.sum()
            chosen_idx = int(rng.choice(len(indexes), p=probs))
            item = pool.items[indexes[chosen_idx]]
            add_item(buckets, item)
            if footprint_count_in_buckets(buckets, target.cells) >= target.count_exact:
                placed = True
                break


def size_bucket_value_stats(truth: SessionTruth, cells: int) -> tuple[int, int]:
    count = 0
    value_sum = 0
    for bucket in truth.buckets.values():
        for item in bucket.items:
            area = item.shape_w * item.shape_h
            if area == cells:
                count += 1
                value_sum += int(item.value)
    return count, value_sum


def _avg_match_factor(actual: float, target: float) -> float:
    rel_err = abs(actual - target) / max(1.0, target)
    if rel_err <= 0.10:
        return 1.0
    if rel_err <= 0.50:
        return max(0.20, 1.0 - rel_err)
    return 0.10


def size_bucket_evidence_score(
    truth: SessionTruth,
    problem: Any,
) -> float:
    """Score per-footprint avg (+ optional exact count / value floor)."""
    targets = getattr(problem, "size_bucket_evidence", ()) or ()
    if not targets:
        return size_avg_value_evidence_score_legacy(truth, problem)
    score = 1.0
    for target in targets:
        count, value_sum = size_bucket_value_stats(truth, target.cells)
        if target.count_exact is not None:
            if count != target.count_exact:
                score *= 0.05
                continue
        elif target.count_partial is not None and count < target.count_partial:
            score *= 0.20
        if target.value_floor is not None and value_sum < target.value_floor:
            return 0.0
        if count <= 0:
            score *= 0.10
            continue
        score *= _avg_match_factor(value_sum / count, target.avg_value)
    return score


def size_avg_value_evidence_score_legacy(
    truth: SessionTruth,
    problem: Any,
) -> float:
    """Fallback when only size_avg_value_targets are populated."""
    values = getattr(problem, "size_avg_value_targets", ()) or ()
    if not values:
        return 1.0
    score = 1.0
    for cells, target_avg in values:
        count, value_sum = size_bucket_value_stats(truth, cells)
        if count <= 0:
            score *= 0.10
            continue
        score *= _avg_match_factor(value_sum / count, target_avg)
    return score


def size_avg_value_evidence_score(truth: SessionTruth, problem: Any) -> float:
    return size_bucket_evidence_score(truth, problem)


def parse_size_bucket_diagnostics(
    diagnostics: str,
) -> tuple[dict[str, Any], ...]:
    """Parse ``size_bucket:*`` tokens from a semicolon-joined diagnostics string."""
    if not diagnostics:
        return ()
    rows: list[dict[str, Any]] = []
    for chunk in diagnostics.split(";"):
        token = chunk.strip()
        if not token.startswith("size_bucket:"):
            continue
        parts = token.split(":")
        if len(parts) < 3:
            continue
        try:
            cells = int(parts[1])
        except ValueError:
            continue
        fields: dict[str, Any] = {"cells": cells}
        for segment in parts[2:]:
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            if key == "avg":
                try:
                    fields["avg_value"] = float(value)
                except ValueError:
                    fields["avg_value"] = value
            elif key == "count_exact":
                try:
                    fields["count_exact"] = int(value)
                except ValueError:
                    fields["count_exact_present_invalid"] = True
            elif key == "count_partial>":
                try:
                    fields["count_partial"] = int(value)
                except ValueError:
                    pass
            elif key == "count_source":
                fields["count_provenance"] = tuple(
                    part for part in value.split(",") if part
                )
            elif key == "value_floor":
                try:
                    fields["value_floor"] = int(float(value))
                except ValueError:
                    pass
            else:
                fields[key] = value
        if "avg_value" in fields:
            rows.append(fields)
    return tuple(rows)


def format_size_bucket_target_label(target: Mapping[str, Any]) -> str:
    """Human label for overlay / logs."""
    cells = int(target.get("cells") or 0)
    avg = target.get("avg_value")
    tier = str(target.get("tier") or "-")
    strength = str(target.get("strength") or "soft")
    parts = [f"{cells}格均价 {_fmt_silver(avg)}" if avg is not None else f"{cells}格均价"]
    parts.append(f"tier={tier}")
    parts.append(strength)
    if target.get("count_exact") is not None:
        parts.append(f"件数={target['count_exact']}")
    if target.get("anchor"):
        parts.append(f"anchor={target['anchor']}")
    return " · ".join(parts)


def _fmt_silver(value: Any) -> str:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:,}"


_ACTION_RESULT_SESSION_RE = re.compile(r"^\d{4}:\d+$")
_FLOAT32_RAW_HEX_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_FLOAT32_MIN_POSITIVE_NORMAL_BITS = 0x00800000
_FLOAT32_MAX_FINITE_BITS = 0x7F7FFFFF


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _float32_interval_from_wire(
    *,
    raw_hex: Any,
    parsed_value: Any,
) -> tuple[float, float] | None:
    if not isinstance(raw_hex, str) or _FLOAT32_RAW_HEX_RE.fullmatch(raw_hex) is None:
        return None
    try:
        raw = bytes.fromhex(raw_hex)
        bits = int.from_bytes(raw, "little", signed=False)
        value = struct.unpack("<f", raw)[0]
        parsed = float(parsed_value)
    except (TypeError, ValueError, OverflowError, struct.error):
        return None
    if (
        not math.isfinite(value)
        or value <= 0
        or not math.isfinite(parsed)
        or parsed <= 0
        or parsed != float(value)
        or bits < _FLOAT32_MIN_POSITIVE_NORMAL_BITS
        or bits >= _FLOAT32_MAX_FINITE_BITS
    ):
        return None
    previous = struct.unpack("<f", (bits - 1).to_bytes(4, "little"))[0]
    following = struct.unpack("<f", (bits + 1).to_bytes(4, "little"))[0]
    return ((float(previous) + value) / 2.0, (value + float(following)) / 2.0)


def _integer_total_interval_from_exact_count(
    avg_interval: tuple[float, float],
    count_as_float: float,
) -> tuple[list[int] | None, str | None]:
    lower_product = avg_interval[0] * count_as_float
    upper_product = avg_interval[1] * count_as_float
    if not math.isfinite(lower_product) or not math.isfinite(upper_product):
        return None, "unrepresentable_exact_count_interval"
    try:
        lower = math.ceil(lower_product)
        upper = math.floor(upper_product)
    except (OverflowError, ValueError):
        return None, "unrepresentable_exact_count_interval"
    if lower > upper:
        return None, "empty_integer_total_interval"
    return [int(lower), int(upper)], None


def _positive_float32_fraction(bits: int) -> Fraction | None:
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7FFFFF
    if bits <= 0 or exponent == 0xFF:
        return None
    if exponent == 0:
        significand = fraction
        exponent_two = -149
    else:
        significand = (1 << 23) | fraction
        exponent_two = exponent - 150
    if significand <= 0:
        return None
    if exponent_two >= 0:
        return Fraction(significand << exponent_two, 1)
    return Fraction(significand, 1 << -exponent_two)


@lru_cache(maxsize=1024)
def _float32_roundtrip_integer_bounds(
    raw_hex: str,
    item_count: int,
) -> tuple[int, int] | None:
    if (
        _FLOAT32_RAW_HEX_RE.fullmatch(raw_hex) is None
        or type(item_count) is not int
        or item_count <= 0
    ):
        return None
    try:
        bits = int.from_bytes(bytes.fromhex(raw_hex), "little", signed=False)
    except ValueError:
        return None
    if (
        bits < _FLOAT32_MIN_POSITIVE_NORMAL_BITS
        or bits >= _FLOAT32_MAX_FINITE_BITS
    ):
        return None
    previous = _positive_float32_fraction(bits - 1)
    current = _positive_float32_fraction(bits)
    following = _positive_float32_fraction(bits + 1)
    if previous is None or current is None or following is None:
        return None
    lower = ((previous + current) / 2) * item_count
    upper = ((current + following) / 2) * item_count
    if bits & 1:
        first = lower.numerator // lower.denominator + 1
        last = -(-upper.numerator // upper.denominator) - 1
    else:
        first = -(-lower.numerator // lower.denominator)
        last = upper.numerator // upper.denominator
    if first <= 0 or first > last:
        return None
    return int(first), int(last)


def _integer_total_roundtrips_to_float32_mean(
    *,
    raw_hex: Any,
    total_value: int,
    item_count: int,
) -> bool:
    """Compare an integer total with one exact Float32 ties-to-even bucket."""

    if (
        not isinstance(raw_hex, str)
        or type(total_value) is not int
        or total_value <= 0
        or type(item_count) is not int
        or item_count <= 0
    ):
        return False
    bounds = _float32_roundtrip_integer_bounds(raw_hex.lower(), item_count)
    return bounds is not None and bounds[0] <= total_value <= bounds[1]


def _float32_roundtrip_total_interval(
    *,
    raw_hex: Any,
    item_count: int,
) -> tuple[list[int] | None, str | None]:
    """Remove midpoint ties that encode to a neighboring Float32 value."""

    if not isinstance(raw_hex, str):
        return None, "float32_roundtrip_unavailable"
    bounds = _float32_roundtrip_integer_bounds(raw_hex.lower(), item_count)
    if bounds is None:
        return None, "empty_integer_total_interval"
    return [bounds[0], bounds[1]], None


def _domain_field(domain: Any, name: str, default: Any = None) -> Any:
    if isinstance(domain, Mapping):
        return domain.get(name, default)
    return getattr(domain, name, default)


def _canonical_mean_candidates(domain: Any) -> tuple[MeanSupportCandidate, ...] | None:
    raw_candidates = _domain_field(domain, "candidates", ())
    if not isinstance(raw_candidates, Sequence) or isinstance(
        raw_candidates,
        (str, bytes),
    ):
        return None
    candidates: list[MeanSupportCandidate] = []
    for raw in raw_candidates:
        if isinstance(raw, Mapping):
            item_id = raw.get("item_id")
            quality = raw.get("quality")
            value = raw.get("value")
            cells = raw.get("cells")
        else:
            item_id = getattr(raw, "item_id", None)
            quality = getattr(raw, "quality", None)
            value = getattr(raw, "value", None)
            cells = getattr(raw, "cells", None)
        if not all(type(field) is int for field in (item_id, quality, value, cells)):
            return None
        candidates.append(
            MeanSupportCandidate(
                item_id=int(item_id),
                quality=int(quality),
                value=int(value),
                cells=int(cells),
            )
        )
    return tuple(candidates)


def _positive_exact_limit(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def exact_quality_footprint_count_options(
    *,
    footprint_cells: int,
    exact_quality_counts: Mapping[str, Any],
    exact_quality_cells: Mapping[str, Any],
    candidate_domains_by_cells: Mapping[int, Any],
    exact_total_item_count: int | None = None,
    exact_warehouse_total_cells: int | None = None,
) -> dict[int, tuple[int, ...]] | None:
    """Return repeat-allowed target-footprint counts per engine quality.

    q1 is the engine's combined q1+q2 bucket.  Every other tier maps directly
    to its catalog quality.  All five tier counts and the non-red cell totals
    must be exact; q6 cells may remain unknown because a public ``red+empty``
    residual is not a red-cell measurement.  Every current-map footprint
    domain must be generation-consistent.  This makes the later mean solve a
    joint physical equation instead of treating the displayed average as a
    count lock.
    """

    if (
        type(footprint_cells) is not int  # noqa: E721
        or footprint_cells not in {1, 2, 3, 4, 6}
    ):
        return None
    engine_qualities = (
        ("q1", (1, 2)),
        ("q3", (3,)),
        ("q4", (4,)),
        ("q5", (5,)),
        ("q6", (6,)),
    )
    normalized_counts: dict[str, int] = {}
    normalized_cells: dict[str, int | None] = {}
    for key, _catalog_qualities in engine_qualities:
        raw_count = exact_quality_counts.get(key)
        raw_cells = exact_quality_cells.get(key)
        if type(raw_count) is not int or raw_count < 0:  # noqa: E721
            return None
        if raw_cells is None and key == "q6":
            normalized_counts[key] = raw_count
            normalized_cells[key] = None
            continue
        if isinstance(raw_cells, bool):
            return None
        try:
            parsed_cells = float(raw_cells)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            not math.isfinite(parsed_cells)
            or parsed_cells < 0
            or not parsed_cells.is_integer()
        ):
            return None
        normalized_counts[key] = raw_count
        normalized_cells[key] = int(parsed_cells)
        if (raw_count == 0) != (int(parsed_cells) == 0):
            return None

    count_sum = sum(normalized_counts.values())
    if count_sum > MEAN_SUPPORT_MAX_COUNT:
        return None
    known_cell_values = tuple(
        value for value in normalized_cells.values() if value is not None
    )
    cell_sum = sum(known_cell_values)
    if exact_total_item_count is not None and (
        type(exact_total_item_count) is not int  # noqa: E721
        or exact_total_item_count != count_sum
    ):
        return None
    if exact_warehouse_total_cells is not None and len(known_cell_values) == 5 and (
        type(exact_warehouse_total_cells) is not int  # noqa: E721
        or exact_warehouse_total_cells != cell_sum
    ):
        return None

    generation: str | None = None
    map_identity: int | None = None
    footprint_options_by_quality: dict[str, set[int]] = {
        key: set() for key, _qualities in engine_qualities
    }
    # Callers must supply the complete map area universe. Tool buckets only
    # name the target of the mean; larger physical items still occupy N/C.
    for cells, domain in candidate_domains_by_cells.items():
        if type(cells) is not int or cells <= 0:
            return None
        domain_map = _domain_field(domain, "map_id")
        if type(domain_map) is not int or domain_map <= 0:
            return None
        if map_identity is None:
            map_identity = domain_map
        elif map_identity != domain_map:
            return None
        if _domain_field(domain, "footprint_cells") != cells:
            return None
        if _domain_field(domain, "catalog_status", "catalog_unavailable") != "ok":
            return None
        domain_generation = _domain_field(domain, "catalog_generation", "")
        if not isinstance(domain_generation, str) or not domain_generation:
            return None
        if generation is None:
            generation = domain_generation
        elif generation != domain_generation:
            return None
        candidates = _canonical_mean_candidates(domain)
        if candidates is None:
            return None
        for candidate in candidates:
            if candidate.cells != cells or _normalized_candidate(candidate) is None:
                return None
            key = "q1" if candidate.quality in {1, 2} else f"q{candidate.quality}"
            if key in footprint_options_by_quality:
                footprint_options_by_quality[key].add(int(candidate.cells))

    result: dict[int, tuple[int, ...]] = {2: (0,)}
    transition_work = 0
    for key, catalog_qualities in engine_qualities:
        count = normalized_counts[key]
        total_cells = normalized_cells[key]
        allowed_cells = footprint_options_by_quality[key]
        if count == 0:
            target_counts = {0}
        elif total_cells is None:
            has_target = footprint_cells in allowed_cells
            has_other = any(cells != footprint_cells for cells in allowed_cells)
            target_counts = set()
            if has_target:
                target_counts.add(count)
            if has_other:
                target_counts.add(0)
            if has_target and has_other:
                target_counts.update(range(1, count))
        elif not allowed_cells:
            return None
        else:
            states: set[tuple[int, int]] = {(0, 0)}
            for _ in range(count):
                transition_work += len(states) * len(allowed_cells)
                if transition_work > MEAN_SUPPORT_TRANSITION_BUDGET:
                    return None
                next_states: set[tuple[int, int]] = set()
                for used_cells, used_target_count in states:
                    for item_cells in allowed_cells:
                        next_cells = used_cells + item_cells
                        if next_cells > total_cells:
                            continue
                        next_states.add(
                            (
                                next_cells,
                                used_target_count + (item_cells == footprint_cells),
                            )
                        )
                states = next_states
                if not states:
                    return None
            target_counts = {
                target_count
                for used_cells, target_count in states
                if used_cells == total_cells
            }
        if not target_counts:
            return None
        # q1 represents both catalog qualities 1 and 2 in the engine.  The
        # mean solver receives those candidates normalized to quality 1.
        result[catalog_qualities[0]] = tuple(sorted(target_counts))
    return result


@lru_cache(maxsize=32)
def _cached_partition_mean_support(
    candidates: tuple[MeanSupportCandidate, ...],
    total_intervals: tuple[tuple[int, tuple[int, int]], ...],
    quality_count_options: tuple[tuple[int, tuple[int, ...]], ...],
) -> MeanPartitionSupportResult:
    """Reuse an immutable complete solve across repeated live refreshes."""

    return solve_repeat_allowed_mean_partition_support(
        candidates,
        total_intervals_by_count=dict(total_intervals),
        quality_count_options=dict(quality_count_options),
    )


def _support_common_facts(
    joint_support: Sequence[Mapping[str, Any]],
    *,
    count_one: Mapping[str, Any],
    candidates_by_id: Mapping[int, MeanSupportCandidate],
) -> dict[str, Any]:
    item_counts = [int(row["item_count"]) for row in joint_support]
    total_cells = [int(row["total_cells"]) for row in joint_support]
    total_values = [int(row["total_value"]) for row in joint_support]
    common: dict[str, Any] = {
        "item_count_range": [min(item_counts), max(item_counts)],
        "item_count_floor": min(item_counts),
        "total_cells_range": [min(total_cells), max(total_cells)],
        "total_cells_floor": min(total_cells),
        "total_value_range": [min(total_values), max(total_values)],
        "total_value_floor": min(total_values),
        "quality_count_ranges": {},
        "quality_cells_ranges": {},
        "quality_value_ranges": {},
        "quality_count_floors": {},
        "quality_cells_floors": {},
        "quality_value_floors": {},
        "correlated_item_fact": None,
    }
    for quality in range(1, 7):
        key = f"q{quality}"
        count_ranges = [
            row.get("quality_count_ranges", {}).get(key)
            for row in joint_support
        ]
        counts = [
            int(row.get("quality_counts", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[0])
            for row, value_range in zip(joint_support, count_ranges)
        ]
        count_maxs = [
            int(row.get("quality_counts", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[1])
            for row, value_range in zip(joint_support, count_ranges)
        ]
        cell_ranges = [
            row.get("quality_cells_ranges", {}).get(key)
            for row in joint_support
        ]
        cells = [
            int(row.get("quality_cells", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[0])
            for row, value_range in zip(joint_support, cell_ranges)
        ]
        cell_maxs = [
            int(row.get("quality_cells", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[1])
            for row, value_range in zip(joint_support, cell_ranges)
        ]
        value_ranges = [
            row.get("quality_value_ranges", {}).get(key)
            for row in joint_support
        ]
        values = [
            int(row.get("quality_values", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[0])
            for row, value_range in zip(joint_support, value_ranges)
        ]
        value_maxs = [
            int(row.get("quality_values", {}).get(key, 0))
            if not isinstance(value_range, Sequence)
            or isinstance(value_range, (str, bytes))
            or len(value_range) != 2
            else int(value_range[1])
            for row, value_range in zip(joint_support, value_ranges)
        ]
        if max(count_maxs) <= 0:
            continue
        common["quality_count_ranges"][key] = [min(counts), max(count_maxs)]
        common["quality_cells_ranges"][key] = [min(cells), max(cell_maxs)]
        common["quality_value_ranges"][key] = [min(values), max(value_maxs)]
        if min(counts) > 0:
            common["quality_count_floors"][key] = min(counts)
        if min(cells) > 0:
            common["quality_cells_floors"][key] = min(cells)
        if min(values) > 0:
            common["quality_value_floors"][key] = min(values)

    unique_item_id = count_one.get("unique_item_id")
    if (
        min(item_counts) == max(item_counts) == 1
        and type(unique_item_id) is int  # noqa: E721 - reject bool
        and unique_item_id in candidates_by_id
    ):
        candidate = candidates_by_id[unique_item_id]
        common["correlated_item_fact"] = {
            "item_id": candidate.item_id,
            "quality": candidate.quality,
            "cells": candidate.cells,
            "value": candidate.value,
        }
    return common


def _reject_shape_average_count_conflict(
    evidence: dict[str, Any],
    status: str,
) -> None:
    evidence.update(
        {
            "support_status": status,
            "status": "conflict",
            "count_status": "conflict",
            "count_value": None,
            "total_value_interval": None,
            "conflicts": [*evidence.get("conflicts", []), status],
        }
    )


def _apply_shape_average_shadow_support(
    evidence: dict[str, Any],
    *,
    domain: Any,
    current_map_id: int | None,
    mandatory_units: Sequence[Mapping[str, Any]] | None,
    exact_total_item_count: int | None,
    exact_warehouse_total_cells: int | None,
    quality_count_options: Mapping[int, Sequence[int]] | None,
    active_joint: bool,
    exact_joint_signatures: bool = False,
) -> None:
    """Attach complete SAV2 support without changing any active inference."""

    evidence.update(
        {
            "support_status": "not_requested",
            "support_complete": False,
            "composition_signature_kind": "quality_count_cells_value",
            "reachable_item_multiset_count": None,
            "item_multiset_uniqueness": "not_computed",
            "count_arms": [],
            "joint_support": [],
            "joint_support_count": None,
            "count_one": {
                "candidate_item_ids": [],
                "candidate_count": 0,
                "unique_item_id": None,
            },
            "common_facts": {},
            "active_constraints": [],
            "hard_eligible": False,
            "active_status": "not_requested",
        }
    )
    if evidence.get("status") == "unavailable" or evidence.get("avg_interval") is None:
        evidence["support_status"] = "raw_evidence_unavailable"
        return
    if os.environ.get(SHAPE_AVG_EXACT_CONSTRAINT_DISABLE) == "1":
        evidence["support_status"] = "rollback_disabled"
        return
    if domain is None:
        evidence["support_status"] = "catalog_not_requested"
        return

    domain_status = _domain_field(domain, "catalog_status", "catalog_unavailable")
    generation = _domain_field(domain, "catalog_generation", "")
    domain_map_id = _domain_field(domain, "map_id")
    domain_cells = _domain_field(domain, "footprint_cells")
    evidence["catalog_context"] = str(generation or "") or None
    if domain_status != "ok":
        evidence["support_status"] = str(domain_status)
        return
    if not isinstance(generation, str) or not generation:
        evidence["support_status"] = "catalog_generation_missing"
        return
    if (
        type(current_map_id) is not int  # noqa: E721 - reject bool
        or type(domain_map_id) is not int  # noqa: E721
        or domain_map_id != current_map_id
    ):
        evidence["support_status"] = "catalog_map_mismatch"
        return
    if (
        type(domain_cells) is not int  # noqa: E721 - reject bool
        or domain_cells != evidence.get("footprint_cells")
    ):
        evidence["support_status"] = "catalog_footprint_mismatch"
        return
    candidates = _canonical_mean_candidates(domain)
    if not candidates:
        evidence["support_status"] = "catalog_candidates_malformed"
        return
    candidates_by_id = {candidate.item_id: candidate for candidate in candidates}
    if len(candidates_by_id) != len(candidates):
        evidence["support_status"] = "catalog_duplicate_item_id"
        return

    normalized_quality_count_options: dict[int, tuple[int, ...]] | None = None
    solve_candidates = candidates
    if quality_count_options is not None:
        normalized_quality_count_options = {}
        for quality in range(1, 7):
            raw_options = quality_count_options.get(quality)
            if (
                not isinstance(raw_options, Sequence)
                or isinstance(raw_options, (str, bytes))
                or not raw_options
                or any(
                    type(value) is not int or value < 0  # noqa: E721
                    for value in raw_options
                )
            ):
                evidence["support_status"] = "invalid_quality_count_options"
                return
            normalized_quality_count_options[quality] = tuple(
                sorted(set(raw_options))
            )
        # The reference engine merges catalog q1/q2 into its q1 bucket.
        solve_candidates = tuple(
            MeanSupportCandidate(
                item_id=candidate.item_id,
                quality=1 if candidate.quality == 2 else candidate.quality,
                value=candidate.value,
                cells=candidate.cells,
            )
            for candidate in candidates
        )

    raw_units: Sequence[Mapping[str, Any]] = mandatory_units or ()
    if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
        evidence["support_status"] = "mandatory_units_malformed"
        return
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            continue
        unit_generation = raw_unit.get("catalog_generation")
        if unit_generation is not None and (
            not isinstance(unit_generation, str)
            or not unit_generation
            or unit_generation != generation
        ):
            evidence["support_status"] = "catalog_generation_mismatch"
            return
        expected_fields = (
            raw_unit.get("expected_quality"),
            raw_unit.get("expected_cells"),
            raw_unit.get("expected_value"),
        )
        if all(field is None for field in expected_fields):
            continue
        raw_ids = raw_unit.get("candidate_item_ids")
        if (
            not all(type(field) is int for field in expected_fields)  # noqa: E721
            or not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes))
            or len(raw_ids) != 1
            or type(raw_ids[0]) is not int  # noqa: E721
        ):
            evidence["support_status"] = "mandatory_units_malformed"
            return
        candidate = candidates_by_id.get(int(raw_ids[0]))
        if candidate is None or (
            candidate.quality,
            candidate.cells,
            candidate.value,
        ) != tuple(int(field) for field in expected_fields):
            evidence["support_status"] = "mandatory_catalog_identity_mismatch"
            return
    mandatory_count = len(raw_units)
    count_status = evidence.get("count_status")
    count_value = evidence.get("count_value")
    exact_total = _positive_exact_limit(exact_total_item_count)
    warehouse_cells = _positive_exact_limit(exact_warehouse_total_cells)
    if count_status == "exact":
        exact_footprint_count = int(count_value)
        if exact_total is not None and exact_footprint_count > exact_total:
            _reject_shape_average_count_conflict(
                evidence,
                "count_exceeds_exact_total_item_count",
            )
            return
        if (
            warehouse_cells is not None
            and exact_footprint_count * int(evidence["footprint_cells"])
            > warehouse_cells
        ):
            _reject_shape_average_count_conflict(
                evidence,
                "footprint_cells_exceed_exact_warehouse_total_cells",
            )
            return
        counts = (exact_footprint_count,)
    else:
        lower = max(
            1,
            int(count_value)
            if count_status == "lower_bound"
            and type(count_value) is int  # noqa: E721
            else 1,
            mandatory_count,
        )
        ceilings: list[int] = []
        if exact_total is not None:
            ceilings.append(exact_total)
        if warehouse_cells is not None:
            ceilings.append(warehouse_cells // int(evidence["footprint_cells"]))
        if not ceilings:
            evidence["support_status"] = "count_unknown_unbounded"
            return
        ceiling = min(ceilings)
        if ceiling < lower:
            _reject_shape_average_count_conflict(
                evidence,
                "count_bounds_conflict",
            )
            return
        counts = tuple(range(lower, ceiling + 1))
    if normalized_quality_count_options is not None:
        possible_counts = {0}
        for options in normalized_quality_count_options.values():
            possible_counts = {
                prior + value
                for prior in possible_counts
                for value in options
            }
        counts = tuple(count for count in counts if count in possible_counts)
    if not counts or max(counts) > MEAN_SUPPORT_MAX_COUNT:
        evidence["support_status"] = (
            "quality_count_support_empty"
            if normalized_quality_count_options is not None
            else "count_complexity_guard"
        )
        return

    interval = evidence["avg_interval"]
    joint_rows: list[dict[str, Any]] = []
    count_arms: list[dict[str, Any]] = []
    count_one_ids: set[int] = set()
    partition_mode = (
        active_joint
        and normalized_quality_count_options is not None
        and not raw_units
        and not exact_joint_signatures
    )
    partition_states_by_count: dict[int, list[Any]] = {}
    partition_count_one_ids: tuple[int, ...] = ()
    if partition_mode:
        total_intervals_by_count: dict[int, tuple[int, int]] = {}
        for count in counts:
            total_interval, interval_conflict = (
                _integer_total_interval_from_exact_count(
                    (float(interval[0]), float(interval[1])),
                    float(count),
                )
            )
            if interval_conflict is None and total_interval is not None:
                total_interval, interval_conflict = (
                    _float32_roundtrip_total_interval(
                        raw_hex=evidence.get("raw_bits_hex"),
                        item_count=int(count),
                    )
                )
            if interval_conflict == "empty_integer_total_interval":
                continue
            if interval_conflict is not None or total_interval is None:
                evidence["support_status"] = str(
                    interval_conflict
                    or "integer_total_interval_unavailable"
                )
                return
            total_intervals_by_count[int(count)] = (
                int(total_interval[0]),
                int(total_interval[1]),
            )
        solved_partition = _cached_partition_mean_support(
            tuple(solve_candidates),
            tuple(sorted(total_intervals_by_count.items())),
            tuple(sorted(normalized_quality_count_options.items())),
        )
        if solved_partition.status != "ok":
            evidence["support_status"] = solved_partition.status
            return
        for state in solved_partition.states:
            partition_states_by_count.setdefault(
                int(state.item_count), []
            ).append(state)
        partition_count_one_ids = (
            solved_partition.count_one_candidate_item_ids
        )
    # Probe the most expensive arm first.  Any guard invalidates the entire
    # shadow evidence, so discovering it before accumulating smaller partial
    # arms avoids latency without changing the all-or-nothing contract.
    for count in reversed(counts):
        total_interval, interval_conflict = _integer_total_interval_from_exact_count(
            (float(interval[0]), float(interval[1])),
            float(count),
        )
        if interval_conflict is None and total_interval is not None:
            total_interval, interval_conflict = _float32_roundtrip_total_interval(
                raw_hex=evidence.get("raw_bits_hex"),
                item_count=int(count),
            )
        if interval_conflict == "empty_integer_total_interval":
            continue
        if interval_conflict is not None or total_interval is None:
            evidence["support_status"] = str(
                interval_conflict or "integer_total_interval_unavailable"
            )
            return
        if partition_mode:
            solved = None
            solved_signatures = tuple(
                partition_states_by_count.get(int(count), ())
            )
        else:
            solved = solve_repeat_allowed_mean_support(
                solve_candidates,
                total_lower=int(total_interval[0]),
                total_upper=int(total_interval[1]),
                item_count=int(count),
                mandatory_units=raw_units,
                quality_count_options=normalized_quality_count_options,
            )
            solved_signatures = solved.signatures
        if solved is not None and solved.status != "ok":
            evidence["support_status"] = solved.status
            return
        signatures = tuple(
            signature
            for signature in solved_signatures
            if _integer_total_roundtrips_to_float32_mean(
                raw_hex=evidence.get("raw_bits_hex"),
                total_value=signature.total_value,
                item_count=int(count),
            )
        )
        if not signatures:
            continue
        count_arms.append(
            {
                "item_count": int(count),
                "total_value_interval": [
                    int(total_interval[0]),
                    int(total_interval[1]),
                ],
                "composition_signature_count": len(signatures),
            }
        )
        count_one_ids.update(
            partition_count_one_ids
            if solved is None
            else solved.count_one_candidate_item_ids
        )
        joint_rows.extend(signature.as_dict() for signature in signatures)

    if not joint_rows:
        evidence["support_status"] = "catalog_support_empty"
        evidence["status"] = "conflict"
        evidence["conflicts"] = [
            *evidence.get("conflicts", []),
            "catalog_support_empty",
        ]
        return

    joint_rows.sort(
        key=lambda row: (
            int(row["item_count"]),
            int(row["total_value"]),
            tuple(sorted(row["quality_counts"].items())),
            tuple(sorted(row["quality_values"].items())),
        )
    )
    count_arms.sort(key=lambda row: int(row["item_count"]))
    count_one = {
        "candidate_item_ids": sorted(count_one_ids),
        "candidate_count": len(count_one_ids),
        "unique_item_id": next(iter(count_one_ids)) if len(count_one_ids) == 1 else None,
    }
    reachable_totals = sorted({int(row["total_value"]) for row in joint_rows})
    common_facts = _support_common_facts(
        joint_rows,
        count_one=count_one,
        candidates_by_id=candidates_by_id,
    )
    aggregate_unique = len(joint_rows) == 1 and all(
        not isinstance(value_range, Sequence)
        or isinstance(value_range, (str, bytes))
        or len(value_range) != 2
        or int(value_range[0]) == int(value_range[1])
        for range_key in (
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_value_ranges",
        )
        for value_range in joint_rows[0].get(range_key, {}).values()
    )
    active_allowed = (
        active_joint
        and os.environ.get(SHAPE_AVG_ACTIVE_JOINT_DISABLE) != "1"
        and int(evidence["footprint_cells"]) in SIZE_AVG_ACTION_FOOTPRINT.values()
    )
    active_constraints = []
    if active_allowed:
        active_constraints = [
            {
                "kind": "quality_value_floor",
                "quality": key,
                "value": int(value),
            }
            for key, value in sorted(
                common_facts.get("quality_value_floors", {}).items()
            )
            if int(value) > 0
        ]
        active_constraints.append({"kind": "footprint_value_floor", "footprint_cells": int(evidence["footprint_cells"]),
                                   "value": int(common_facts["total_value_floor"])})
    active_status = "active" if active_constraints else "shadow"
    if active_joint and not active_constraints:
        active_status = (
            "rollback_disabled"
            if os.environ.get(SHAPE_AVG_ACTIVE_JOINT_DISABLE) == "1"
            else "no_common_value_floor"
        )
    evidence.update(
        {
            "status": "unique_total" if len(reachable_totals) == 1 else "constrained",
            "support_status": "ok",
            "support_complete": True,
            "aggregate_unique": aggregate_unique,
            # Field-9 carries a rounded Float32 mean, not a fixed-point decimal
            # rational.  A clean-looking suffix such as .125 therefore cannot
            # impose a denominator lock by itself: each physical count must be
            # checked against the complete Float32 rounding bucket and an
            # integer aggregate total.  Publish the rule explicitly so later
            # engine/UI consumers cannot silently reinterpret the raw digits.
            "count_constraint_mode": SHAPE_AVG_FLOAT32_COUNT_CONSTRAINT_MODE,
            "decimal_denominator_authoritative": False,
            "count_arms": count_arms,
            "joint_support": joint_rows,
            "joint_support_count": len(joint_rows),
            "count_one": count_one,
            "common_facts": common_facts,
            "active_constraints": active_constraints,
            "hard_eligible": bool(active_constraints),
            "active_status": active_status,
            "reachable_total_values": reachable_totals,
            "reachable_total_value_count": len(reachable_totals),
            "reachable_composition_count": len(joint_rows),
            "support_truncated": False,
            "quote_effect": "active_joint" if active_constraints else "shadow",
            "consumed_by": [
                *evidence.get("consumed_by", []),
                "sav2_shadow",
                *(
                    ["reference_engine_shape_average_joint"]
                    if active_constraints
                    else []
                ),
            ],
        }
    )


def _join_shape_average_support(
    rows, *, domains, exact_counts, exact_cells, total_count, total_cells,
    mandatory_units_by_cells, transition_budget=250_000,
):
    """Intersect exact bucket signatures with one physical remainder equation.

    This never takes the Cartesian product of envelope bounds. A guarded or
    non-exact input returns no hard facts, including any accumulated prefix.
    """
    keys = ("q1", "q3", "q4", "q5", "q6")
    used_budget = 0
    def spend(amount=1):
        nonlocal used_budget
        used_budget += amount
        if used_budget > transition_budget:
            raise ValueError("joint_complexity_guard")
    def quality_index(q):
        return 0 if q in (1, 2) else q - 2
    zero = (0,) * 5
    def add(a, b):
        return tuple(x+y for x,y in zip(a,b))
    try:
        if type(total_count) is not int or not 0 < total_count <= MEAN_SUPPORT_MAX_COUNT:
            return {"status":"joint_total_count_unavailable"}
        target_cells = {int(row["footprint_cells"]) for row in rows}
        allowed_areas = [set() for _ in keys]
        by_id = {}
        for area, domain in domains.items():
            for candidate in _canonical_mean_candidates(domain) or ():
                by_id[candidate.item_id] = candidate
                if area not in target_cells:
                    allowed_areas[quality_index(candidate.quality)].add(area)
        outside = [unit for area, units in mandatory_units_by_cells.items()
                   if area not in target_cells for unit in units]
        outside_states = {(zero,zero)}
        outside_value_floor = 0
        for unit in outside:
            candidates = [by_id[item_id] for item_id in unit["candidate_item_ids"]]
            outside_value_floor += min(c.value for c in candidates)
            options = {(quality_index(c.quality),c.cells) for c in candidates}
            next_states = set()
            for counts,cells in outside_states:
                for index,area in options:
                    spend()
                    nc=list(counts);ns=list(cells);nc[index]+=1;ns[index]+=area
                    next_states.add((tuple(nc),tuple(ns)))
            outside_states=next_states
        area_cache = {}
        def sums(index,count):
            key=(index,count)
            if key in area_cache:
                return area_cache[key]
            if count == 0:
                return {0}
            values={v+area for v in sums(index,count-1) for area in allowed_areas[index]}
            spend(len(values));area_cache[key]=values
            return values
        def remainder_possible(counts,cells):
            remaining_total=total_count-sum(counts)
            if remaining_total < 0:
                return False
            remaining_cells=total_cells-sum(cells) if total_cells is not None else None
            if remaining_cells is not None and remaining_cells < 0:
                return False
            possibilities={(0,0)}
            for index,key in enumerate(keys):
                fixed=exact_counts.get(key)
                options=(fixed-counts[index],) if fixed is not None else range(remaining_total+1)
                required=exact_cells.get(key)
                if required is not None:
                    required-=cells[index]
                    if required < 0:
                        return False
                values=[]
                for n in options:
                    if n < 0 or n > remaining_total:
                        continue
                    attainable=sums(index,n)
                    if required is not None:
                        if required not in attainable:
                            continue
                        attainable={required}
                    elif remaining_cells is None:
                        attainable={0} if attainable else set()
                    values.extend((n,c) for c in attainable)
                next_states=set()
                for prior_n,prior_c in possibilities:
                    for n,c in values:
                        spend()
                        if prior_n+n <= remaining_total and (remaining_cells is None or prior_c+c <= remaining_cells):
                            next_states.add((prior_n+n,prior_c+c))
                possibilities=next_states
                if not possibilities:
                    return False
            return any(n==remaining_total and (remaining_cells is None or c==remaining_cells)
                       for n,c in possibilities)
        states={(zero,zero,zero,0)}
        for row in rows:
            bucket=[]
            area=int(row["footprint_cells"])
            for arm in row["joint_support"]:
                # General multiset signatures are exact. Partition envelopes
                # may participate only if every stored range is a point.
                if any(value[0] != value[1] for name in ("quality_count_ranges","quality_cells_ranges","quality_value_ranges")
                       for value in arm.get(name,{}).values()):
                    return {"status":"joint_envelope_not_exact"}
                count_vector=[0]*5;value_vector=[0]*5
                for quality in range(1,7):
                    key=f"q{quality}"
                    n=arm.get("quality_count_ranges",{}).get(key,[arm["quality_counts"].get(key,0)])[0]
                    value=arm.get("quality_value_ranges",{}).get(key,[arm["quality_values"].get(key,0)])[0]
                    count_vector[quality_index(quality)]+=int(n)
                    value_vector[quality_index(quality)]+=int(value)
                bucket.append((tuple(count_vector),tuple(n*area for n in count_vector),tuple(value_vector),int(arm["total_value"])))
            next_states=set()
            for prior in states:
                for arm in bucket:
                    spend()
                    counts=add(prior[0],arm[0]);cells=add(prior[1],arm[1])
                    if sum(counts)>total_count or any(exact_counts.get(k) is not None and counts[i]>exact_counts[k]
                                                     for i,k in enumerate(keys)):
                        continue
                    next_states.add((counts,cells,add(prior[2],arm[2]),prior[3]+arm[3]))
            states=next_states
        valid=[]
        for state in states:
            for counts,cells in outside_states:
                spend()
                if remainder_possible(add(state[0],counts),add(state[1],cells)):
                    valid.append(state);break
        if not valid:
            return {"status":"joint_support_empty"}
        return {"status":"ok","quality_value_floors":{key:min(state[2][i] for state in valid)
                                                       for i,key in enumerate(keys)},
                "total_value_floor":min(state[3] for state in valid)+outside_value_floor,
                "support_count":len(valid),"support_sha256":hashlib.sha256(
                    json.dumps(sorted(valid),separators=(",",":")).encode()).hexdigest()}
    except (KeyError,TypeError,ValueError,RecursionError) as exc:
        return {"status":"joint_complexity_guard" if str(exc)=="joint_complexity_guard" else "joint_input_conflict"}


def _shape_average_evidence_id(row: Mapping[str, Any]) -> str:
    identity = {
        "action_id": row.get("action_id"),
        "result_field": row.get("result_field"),
        "result_raw_hex": row.get("result_raw_hex"),
        "session_id": row.get("session_id"),
        "sort": row.get("sort"),
    }
    raw = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "shape-avg:" + hashlib.sha256(raw).hexdigest()


def shape_average_action_observation_fingerprint(
    row: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (type(value).__name__, repr(value))
        for value in (
            row.get("result"),
            row.get("result_field"),
            row.get("result_wire_type"),
            (
                row.get("result_raw_hex").lower()
                if isinstance(row.get("result_raw_hex"), str)
                else row.get("result_raw_hex")
            ),
        )
    )


def _conflicting_shape_average_action_keys(
    rows: Sequence[Mapping[str, Any]],
) -> frozenset[tuple[str, int]]:
    observations: dict[tuple[str, int], set[tuple[tuple[str, str], ...]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action_id = _strict_int(row.get("action_id"))
        session_id = row.get("session_id")
        if (
            action_id not in SIZE_AVG_ACTION_FOOTPRINT
            or not isinstance(session_id, str)
        ):
            continue
        observations.setdefault((session_id, action_id), set()).add(
            shape_average_action_observation_fingerprint(row)
        )
    return frozenset(
        key for key, fingerprints in observations.items() if len(fingerprints) > 1
    )


def shape_average_value_evidence_from_action_rows(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    current_session_id: str | None = None,
    posterior_targets: Sequence[Mapping[str, Any]] | None = None,
    candidate_domains_by_cells: Mapping[int, Any] | None = None,
    mandatory_units_by_cells: Mapping[
        int,
        Sequence[Mapping[str, Any]],
    ]
    | None = None,
    exact_total_item_count: int | None = None,
    exact_warehouse_total_cells: int | None = None,
    quality_count_options_by_cells: Mapping[
        int,
        Mapping[int, Sequence[int]],
    ]
    | None = None,
    active_joint: bool = False,
    exact_joint_signatures: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Build additive five-footprint evidence without changing inference."""
    if not rows:
        return ()
    targets_by_cells = {
        int(target.get("cells")): target
        for target in posterior_targets or ()
        if isinstance(target, Mapping)
        and _strict_int(target.get("cells")) in SIZE_AVG_ACTION_FOOTPRINT.values()
    }
    current_map_id = (
        int(current_session_id.split(":", 1)[0])
        if isinstance(current_session_id, str)
        and _ACTION_RESULT_SESSION_RE.fullmatch(current_session_id) is not None
        else None
    )
    duplicate_conflict_keys = _conflicting_shape_average_action_keys(rows)
    evidence_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action_id = _strict_int(row.get("action_id"))
        if action_id not in SIZE_AVG_ACTION_FOOTPRINT:
            continue
        footprint_cells = SIZE_AVG_ACTION_FOOTPRINT[action_id]
        target = targets_by_cells.get(footprint_cells, {})
        conflicts: list[str] = []
        if (
            not isinstance(current_session_id, str)
            or _ACTION_RESULT_SESSION_RE.fullmatch(current_session_id) is None
        ):
            conflicts.append("invalid_current_session")
        source_session = row.get("session_id")
        if (
            not isinstance(source_session, str)
            or _ACTION_RESULT_SESSION_RE.fullmatch(source_session) is None
        ):
            conflicts.append("invalid_source_session")
        if (
            isinstance(current_session_id, str)
            and _ACTION_RESULT_SESSION_RE.fullmatch(current_session_id) is not None
            and source_session != current_session_id
        ):
            conflicts.append("stale_or_cross_session")
        if (
            isinstance(source_session, str)
            and (source_session, action_id) in duplicate_conflict_keys
        ):
            conflicts.append("duplicate_action_conflict")
        source_field = _strict_int(row.get("result_field"))
        if source_field is None:
            conflicts.append("invalid_source_field")
        wire_type = row.get("result_wire_type")
        if wire_type not in {"float32", "varint", "varint_as_float"}:
            wire_type = "unknown"
        raw_value = row.get("result")
        if source_field == 9 and wire_type == "float32":
            # Field 9 is the direct fixed32 producer.  Its parser emits an
            # exact built-in float; accepting a coercible mirror here would
            # let a prepared snapshot turn an untrusted string/object into
            # active public pricing evidence before the raw-bits check below.
            numeric_value = raw_value if type(raw_value) is float else None  # noqa: E721
        elif isinstance(raw_value, bool):
            numeric_value = None
        else:
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                numeric_value = None
        if (
            numeric_value is None
            or not math.isfinite(numeric_value)
            or numeric_value <= 0
        ):
            conflicts.append("invalid_positive_numeric_value")
            numeric_value = None
        avg_interval = None
        if (
            numeric_value is not None
            and source_field == 9
            and wire_type == "float32"
        ):
            avg_interval = _float32_interval_from_wire(
                raw_hex=row.get("result_raw_hex"),
                parsed_value=numeric_value,
            )
            if avg_interval is None:
                conflicts.append("float32_wire_mismatch")

        count_exact_present_invalid = (
            target.get("count_exact_present_invalid") is True
        )
        count_exact_present = (
            "count_exact" in target or count_exact_present_invalid
        )
        raw_count_exact = target.get("count_exact")
        count_exact = (
            raw_count_exact
            if (
                not count_exact_present_invalid
                and type(raw_count_exact) is int  # noqa: E721
                and raw_count_exact > 0
            )
            else None
        )
        count_as_float: float | None = None
        count_conflict: str | None = None
        if count_exact_present:
            if count_exact is None:
                count_conflict = "invalid_exact_count"
            else:
                try:
                    count_as_float = float(count_exact)
                except (OverflowError, ValueError):
                    count_conflict = "unrepresentable_exact_count_interval"
                if count_as_float is not None and not math.isfinite(count_as_float):
                    count_conflict = "unrepresentable_exact_count_interval"
            if count_conflict is not None:
                conflicts.append(count_conflict)
                count_exact = None
                count_as_float = None
        count_partial = _strict_int(target.get("count_partial"))
        if count_conflict is not None:
            count_value = None
            count_status = "conflict"
        else:
            count_value = count_exact if count_exact is not None else count_partial
            count_status = (
                "exact"
                if count_exact is not None
                else "lower_bound"
                if count_partial is not None
                else "unknown"
            )
        count_provenance = target.get("count_provenance")
        if not isinstance(count_provenance, Sequence) or isinstance(
            count_provenance,
            (str, bytes),
        ):
            count_provenance = ()
        count_provenance = tuple(
            str(value) for value in count_provenance if str(value)
        )

        total_interval: list[int] | None = None
        if (
            avg_interval is not None
            and count_exact is not None
            and count_as_float is not None
        ):
            total_interval, interval_conflict = (
                _integer_total_interval_from_exact_count(
                    avg_interval,
                    count_as_float,
                )
            )
            if interval_conflict is None and total_interval is not None:
                total_interval, interval_conflict = (
                    _float32_roundtrip_total_interval(
                        raw_hex=row.get("result_raw_hex"),
                        item_count=count_exact,
                    )
                )
            if interval_conflict is not None:
                conflicts.append(interval_conflict)
                count_value = None
                count_status = "conflict"

        status = "unavailable"
        if not conflicts:
            status = "interval_only" if avg_interval is not None else "raw_only"
        consumed_by = ["v2_size_avg_legacy"] if status != "unavailable" else []
        evidence = {
            "schema_version": 1,
            "status": status,
            "action_id": action_id,
            "footprint_cells": footprint_cells,
            "source_session_id": (
                source_session if isinstance(source_session, str) else None
            ),
            "source_sort": _strict_int(row.get("sort")),
            "source_round": _strict_int(row.get("round")),
            "source_field": source_field,
            "wire_numeric_type": wire_type,
            "raw_value": numeric_value,
            "raw_bits_hex": (
                str(row.get("result_raw_hex")).lower()
                if isinstance(row.get("result_raw_hex"), str)
                else None
            ),
            "display_value": None,
            "display_decimals": None,
            "display_rounding": "unknown",
            "avg_interval": (
                [float(avg_interval[0]), float(avg_interval[1])]
                if avg_interval is not None
                else None
            ),
            "count_value": count_value,
            "count_status": count_status,
            "count_provenance": list(count_provenance),
            "total_value_interval": total_interval,
            "reachable_total_values": [],
            "reachable_total_value_count": None,
            "reachable_composition_count": None,
            "support_truncated": False,
            "catalog_context": None,
            "evidence_id": _shape_average_evidence_id(row),
            "consumed_by": consumed_by,
            "conflicts": conflicts,
            "quote_effect": "none_new_structured_evidence_only",
        }
        _apply_shape_average_shadow_support(
            evidence,
            domain=(candidate_domains_by_cells or {}).get(footprint_cells),
            current_map_id=current_map_id,
            mandatory_units=(mandatory_units_by_cells or {}).get(
                footprint_cells
            ),
            exact_total_item_count=exact_total_item_count,
            exact_warehouse_total_cells=exact_warehouse_total_cells,
            quality_count_options=(quality_count_options_by_cells or {}).get(
                footprint_cells
            ),
            active_joint=active_joint,
            exact_joint_signatures=exact_joint_signatures,
        )
        evidence_rows.append(evidence)
    return tuple(evidence_rows)


def size_avg_readings_from_action_rows(
    rows: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    """Latest-first readings from live ``action_result_rows``."""
    if not rows:
        return ()
    duplicate_conflict_keys = _conflicting_shape_average_action_keys(rows)
    readings: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action_id = row.get("action_id")
        if action_id is None:
            continue
        try:
            aid = int(action_id)
        except (TypeError, ValueError):
            continue
        cells = SIZE_AVG_ACTION_FOOTPRINT.get(aid)
        if cells is None:
            continue
        session_id = row.get("session_id")
        if (
            isinstance(session_id, str)
            and (session_id, aid) in duplicate_conflict_keys
        ):
            continue
        raw = row.get("result")
        source_field = _strict_int(row.get("result_field"))
        wire_type = row.get("result_wire_type")
        if source_field == 9 and wire_type == "float32":
            # Keep the legacy diagnostics reader aligned with the evidence
            # builder: a direct fixed32 value is a parser-produced built-in
            # float, never a coercible UI/JSON mirror.
            avg_value = raw if type(raw) is float else None  # noqa: E721
        else:
            try:
                avg_value = float(raw)
            except (TypeError, ValueError, OverflowError):
                avg_value = None
        if avg_value is None:
            continue
        if not math.isfinite(avg_value) or avg_value <= 0:
            continue
        if (
            source_field == 9
            and wire_type == "float32"
            and _float32_interval_from_wire(
                raw_hex=row.get("result_raw_hex"),
                parsed_value=avg_value,
            )
            is None
        ):
            continue
        readings.append(
            {
                "action_id": aid,
                "tool": str(row.get("tool") or f"action:{aid}"),
                "footprint_cells": cells,
                "avg_value": avg_value,
                "avg_label": _fmt_silver(avg_value),
                "sort": row.get("sort"),
            }
        )
    return tuple(readings)


def size_bucket_eval_fields(
    *,
    posterior_diagnostics: str,
    action_result_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Flatten size-bucket diagnostics + tool usage for ``model_eval.jsonl``."""
    targets = parse_size_bucket_diagnostics(posterior_diagnostics)
    readings = size_avg_readings_from_action_rows(action_result_rows)
    action_ids = sorted({int(r["action_id"]) for r in readings})
    four_cell = next((t for t in targets if int(t.get("cells") or 0) == 4), None)
    latest_four = next(
        (r for r in readings if int(r.get("footprint_cells") or 0) == 4),
        None,
    )
    summary_parts: list[str] = []
    for target in targets[:3]:
        summary_parts.append(format_size_bucket_target_label(target))
    return {
        "size_bucket_active": bool(targets),
        "size_bucket_target_count": len(targets),
        "size_bucket_summary": "; ".join(summary_parts),
        "size_bucket_4cell_avg": (
            float(four_cell["avg_value"]) if four_cell and four_cell.get("avg_value") is not None else None
        ),
        "size_bucket_4cell_tier": (
            str(four_cell.get("tier") or "") if four_cell else ""
        ),
        "size_bucket_4cell_strength": (
            str(four_cell.get("strength") or "") if four_cell else ""
        ),
        "size_bucket_reading_4cell_avg": (
            float(latest_four["avg_value"])
            if latest_four and latest_four.get("avg_value") is not None
            else None
        ),
        "action_size_avg_tool_ids": ",".join(str(aid) for aid in action_ids),
        "action_100172_used": 100172 in action_ids,
        "action_size_avg_tool_count": len(action_ids),
    }


def size_bucket_evidence_diagnostics(
    evidence: tuple[SizeBucketEvidence, ...],
) -> tuple[str, ...]:
    lines: list[str] = []
    for target in evidence:
        parts = [
            f"size_bucket:{target.cells}",
            f"avg={target.avg_value:.0f}",
            f"tier={target.tier or '-'}",
            f"strength={target.strength}",
        ]
        if target.warehouse_band:
            parts.append(f"wh={target.warehouse_band}")
        if target.count_exact is not None:
            parts.append(f"count_exact={target.count_exact}")
        elif target.count_partial is not None:
            parts.append(f"count_partial>={target.count_partial}")
        if target.count_provenance:
            parts.append("count_source=" + ",".join(target.count_provenance))
        if target.anchor_hint:
            parts.append(f"anchor={target.anchor_hint}")
        if target.value_floor is not None:
            parts.append(f"value_floor={target.value_floor}")
        lines.append(":".join(parts))
    return tuple(lines)


__all__ = [
    "FILLER_MAX_AVG_BY_CELLS",
    "HIGH_VALUE_FLOOR_MIN_AVG",
    "SIZE_AVG_ANCHORS",
    "SIZE_AVG_VALUE_SIGNAL_FLOORS",
    "SIZE_AVG_ACTION_FOOTPRINT",
    "SIZE_AVG_ACTION_IDS",
    "SIZE_AVG_VALUE_SIGNAL_FLOOR",
    "SHAPE_AVG_ACTIVE_JOINT_DISABLE",
    "SHAPE_AVG_ACTIVE_NONUNIQUE_MIN_AVG",
    "SHAPE_AVG_EXACT_CONSTRAINT_DISABLE",
    "SizeBucketCountPartition",
    "SizeBucketEvidence",
    "TYPICAL_SIZE_COUNT_BY_BAND",
    "WAREHOUSE_MID_MAX",
    "WAREHOUSE_SMALL_MAX",
    "actionable_size_avg_value_targets",
    "build_size_bucket_evidence",
    "exact_quality_footprint_count_options",
    "format_size_bucket_target_label",
    "parse_size_bucket_diagnostics",
    "size_avg_readings_from_action_rows",
    "shape_average_action_observation_fingerprint",
    "shape_average_value_evidence_from_action_rows",
    "size_bucket_count_targets_for_structured_evidence",
    "size_bucket_eval_fields",
    "size_bucket_count_partition_from_context",
    "size_avg_value_evidence_score",
    "size_bucket_counts_from_context",
    "size_bucket_evidence_diagnostics",
    "size_bucket_evidence_score",
    "size_bucket_value_stats",
    "warehouse_size_band",
]
