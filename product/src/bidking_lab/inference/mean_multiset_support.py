"""Pure repeat-allowed support for a Float32 mean equation.

The DP is centered on the wire mean so absolute catalog prices do not control
complexity.  It retains cross-quality count/value signatures, but deliberately
does not interpret those signatures as active engine locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


MEAN_SUPPORT_MAX_COUNT = 128
MEAN_SUPPORT_MAX_INTERVAL_WIDTH = 4096
MEAN_SUPPORT_LAYER_SIGNATURE_BUDGET = 250_000
MEAN_SUPPORT_TRANSITION_BUDGET = 250_000
MEAN_SUPPORT_OUTPUT_SIGNATURE_BUDGET = 100_000
_QUALITY_COUNT = 6


@dataclass(frozen=True)
class MeanSupportCandidate:
    item_id: int
    quality: int
    value: int
    cells: int


@dataclass(frozen=True)
class MeanSupportMandatoryUnit:
    """One already-observed physical item with one or more catalog options."""

    identity: str
    candidate_item_ids: tuple[int, ...]


@dataclass(frozen=True)
class MeanSupportCompositionSignature:
    item_count: int
    total_cells: int
    total_value: int
    quality_counts: tuple[int, ...]
    quality_values: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        cells_per_item = self.total_cells // self.item_count
        return {
            "item_count": self.item_count,
            "total_cells": self.total_cells,
            "total_value": self.total_value,
            "quality_counts": {
                f"q{quality}": int(count)
                for quality, count in enumerate(self.quality_counts, start=1)
                if count
            },
            "quality_cells": {
                f"q{quality}": int(count) * cells_per_item
                for quality, count in enumerate(self.quality_counts, start=1)
                if count
            },
            "quality_values": {
                f"q{quality}": int(value)
                for quality, value in enumerate(self.quality_values, start=1)
                if value
            },
        }


@dataclass(frozen=True)
class MeanMultisetSupportResult:
    status: str
    signatures: tuple[MeanSupportCompositionSignature, ...] = ()
    count_one_candidate_item_ids: tuple[int, ...] = ()


_InternalSignature = tuple[tuple[int, ...], tuple[int, ...]]


def _zero_signature() -> _InternalSignature:
    return ((0,) * _QUALITY_COUNT, (0,) * _QUALITY_COUNT)


def _add_candidate_to_signature(
    signature: _InternalSignature,
    *,
    quality: int,
    value: int,
) -> _InternalSignature:
    counts, values = signature
    index = quality - 1
    next_counts = list(counts)
    next_values = list(values)
    next_counts[index] += 1
    next_values[index] += value
    return tuple(next_counts), tuple(next_values)


def _normalized_candidate(value: Any) -> MeanSupportCandidate | None:
    if isinstance(value, MeanSupportCandidate):
        candidate = value
    elif isinstance(value, Mapping):
        try:
            candidate = MeanSupportCandidate(
                item_id=value["item_id"],
                quality=value["quality"],
                value=value["value"],
                cells=value["cells"],
            )
        except (KeyError, TypeError):
            return None
    else:
        return None
    if (
        type(candidate.item_id) is not int  # noqa: E721 - reject bool
        or candidate.item_id <= 0
        or type(candidate.quality) is not int  # noqa: E721
        or not 1 <= candidate.quality <= _QUALITY_COUNT
        or type(candidate.value) is not int  # noqa: E721
        or candidate.value <= 0
        or type(candidate.cells) is not int  # noqa: E721
        or candidate.cells <= 0
    ):
        return None
    return candidate


def _normalized_mandatory_unit(value: Any) -> MeanSupportMandatoryUnit | None:
    if isinstance(value, MeanSupportMandatoryUnit):
        unit = value
    elif isinstance(value, Mapping):
        identity = value.get("identity")
        raw_ids = value.get("candidate_item_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            return None
        unit = MeanSupportMandatoryUnit(
            identity=identity,
            candidate_item_ids=tuple(raw_ids),
        )
    else:
        return None
    if not isinstance(unit.identity, str) or not unit.identity:
        return None
    if not unit.candidate_item_ids or any(
        type(item_id) is not int or item_id <= 0  # noqa: E721
        for item_id in unit.candidate_item_ids
    ):
        return None
    return MeanSupportMandatoryUnit(
        identity=unit.identity,
        candidate_item_ids=tuple(sorted(set(unit.candidate_item_ids))),
    )


def solve_repeat_allowed_mean_support(
    candidates: Sequence[MeanSupportCandidate | Mapping[str, Any]],
    *,
    total_lower: int,
    total_upper: int,
    item_count: int,
    mandatory_units: Sequence[MeanSupportMandatoryUnit | Mapping[str, Any]] = (),
    quality_count_options: Mapping[int, Sequence[int]] | None = None,
    layer_signature_budget: int = MEAN_SUPPORT_LAYER_SIGNATURE_BUDGET,
    transition_budget: int = MEAN_SUPPORT_TRANSITION_BUDGET,
    output_signature_budget: int = MEAN_SUPPORT_OUTPUT_SIGNATURE_BUDGET,
) -> MeanMultisetSupportResult:
    """Return complete quality signatures for one count-fixed mean window.

    Residual catalog selection is repeat-allowed.  Mandatory units model known
    physical identities and select exactly one canonical candidate each.  A
    budget failure returns no partial signatures, so callers cannot promote a
    uniqueness result from an incomplete search.
    """

    normalized = tuple(_normalized_candidate(candidate) for candidate in candidates)
    if not normalized or any(candidate is None for candidate in normalized):
        return MeanMultisetSupportResult("invalid_candidates")
    canonical = tuple(candidate for candidate in normalized if candidate is not None)
    if len({candidate.item_id for candidate in canonical}) != len(canonical):
        return MeanMultisetSupportResult("duplicate_candidate_item_id")
    footprint_cells = canonical[0].cells
    if any(candidate.cells != footprint_cells for candidate in canonical):
        return MeanMultisetSupportResult("mixed_footprint_cells")
    if (
        type(item_count) is not int  # noqa: E721 - reject bool
        or item_count <= 0
        or item_count > MEAN_SUPPORT_MAX_COUNT
        or type(total_lower) is not int  # noqa: E721
        or type(total_upper) is not int  # noqa: E721
        or total_lower <= 0
        or total_upper < total_lower
        or total_upper - total_lower + 1 > MEAN_SUPPORT_MAX_INTERVAL_WIDTH
        or type(layer_signature_budget) is not int  # noqa: E721
        or layer_signature_budget <= 0
        or type(transition_budget) is not int  # noqa: E721
        or transition_budget <= 0
        or type(output_signature_budget) is not int  # noqa: E721
        or output_signature_budget <= 0
    ):
        return MeanMultisetSupportResult("invalid_input")

    normalized_units = tuple(
        _normalized_mandatory_unit(unit) for unit in mandatory_units
    )
    if any(unit is None for unit in normalized_units):
        return MeanMultisetSupportResult("invalid_mandatory_unit")
    units = tuple(unit for unit in normalized_units if unit is not None)
    allowed_quality_counts = None
    if quality_count_options is not None:
        allowed_quality_counts = {}
        for quality in range(1, _QUALITY_COUNT + 1):
            options = quality_count_options.get(quality)
            if (not isinstance(options, Sequence) or isinstance(options, (str, bytes))
                    or not options or any(type(n) is not int or n < 0 for n in options)):
                return MeanMultisetSupportResult("invalid_quality_count_options")
            allowed_quality_counts[quality] = frozenset(options)
    if len({unit.identity for unit in units}) != len(units):
        return MeanMultisetSupportResult("duplicate_mandatory_identity")
    if len(units) > item_count:
        return MeanMultisetSupportResult("mandatory_count_exceeds_item_count")

    by_id = {candidate.item_id: candidate for candidate in canonical}
    if any(
        item_id not in by_id
        for unit in units
        for item_id in unit.candidate_item_ids
    ):
        return MeanMultisetSupportResult("mandatory_candidate_outside_domain")

    base_value = ((total_lower + total_upper) // 2) // item_count
    target_lower = total_lower - base_value * item_count
    target_upper = total_upper - base_value * item_count
    value_quality_options = tuple(
        sorted(
            {
                (
                    candidate.value - base_value,
                    candidate.quality,
                    candidate.value,
                )
                for candidate in canonical
            }
        )
    )
    minimum_delta = min(option[0] for option in value_quality_options)
    maximum_delta = max(option[0] for option in value_quality_options)

    states: dict[int, set[_InternalSignature]] = {0: {_zero_signature()}}
    for unit in units:
        unit_options = {
            (
                by_id[item_id].value - base_value,
                by_id[item_id].quality,
                by_id[item_id].value,
            )
            for item_id in unit.candidate_item_ids
        }
        if (
            sum(len(signatures) for signatures in states.values())
            * len(unit_options)
            > transition_budget
        ):
            return MeanMultisetSupportResult("mandatory_complexity_guard")
        next_states: dict[int, set[_InternalSignature]] = {}
        for prior_delta, prior_signatures in states.items():
            for delta, quality, value in unit_options:
                bucket = next_states.setdefault(prior_delta + delta, set())
                for signature in prior_signatures:
                    bucket.add(
                        _add_candidate_to_signature(
                            signature,
                            quality=quality,
                            value=value,
                        )
                    )
        if sum(len(signatures) for signatures in next_states.values()) > (
            layer_signature_budget
        ):
            return MeanMultisetSupportResult("mandatory_complexity_guard")
        states = next_states

    residual_count = item_count - len(units)
    for used_count in range(1, residual_count + 1):
        remaining = residual_count - used_count
        transition_work = 0
        for prior_delta, prior_signatures in states.items():
            for delta, _quality, _value in value_quality_options:
                next_delta = prior_delta + delta
                if (
                    next_delta + remaining * minimum_delta > target_upper
                    or next_delta + remaining * maximum_delta < target_lower
                ):
                    continue
                transition_work += len(prior_signatures)
                if transition_work > transition_budget:
                    return MeanMultisetSupportResult(
                        "deviation_complexity_guard"
                    )
        next_states: dict[int, set[_InternalSignature]] = {}
        for prior_delta, prior_signatures in states.items():
            for delta, quality, value in value_quality_options:
                next_delta = prior_delta + delta
                if (
                    next_delta + remaining * minimum_delta > target_upper
                    or next_delta + remaining * maximum_delta < target_lower
                ):
                    continue
                bucket = next_states.setdefault(next_delta, set())
                for signature in prior_signatures:
                    bucket.add(
                        _add_candidate_to_signature(
                            signature,
                            quality=quality,
                            value=value,
                        )
                    )
        if sum(len(signatures) for signatures in next_states.values()) > (
            layer_signature_budget
        ):
            return MeanMultisetSupportResult("deviation_complexity_guard")
        states = next_states
        if not states:
            break

    output: list[MeanSupportCompositionSignature] = []
    for delta in sorted(states):
        if not target_lower <= delta <= target_upper:
            continue
        total_value = base_value * item_count + delta
        for counts, values in sorted(states[delta]):
            if allowed_quality_counts is not None and any(
                counts[q - 1] not in allowed_quality_counts[q] for q in range(1, _QUALITY_COUNT + 1)
            ):
                continue
            output.append(
                MeanSupportCompositionSignature(
                    item_count=item_count,
                    total_cells=item_count * footprint_cells,
                    total_value=total_value,
                    quality_counts=counts,
                    quality_values=values,
                )
            )
            if len(output) > output_signature_budget:
                return MeanMultisetSupportResult("output_complexity_guard")

    count_one_ids: tuple[int, ...] = ()
    if item_count == 1:
        allowed_ids = (
            units[0].candidate_item_ids if units else tuple(by_id)
        )
        count_one_ids = tuple(
            sorted(
                item_id
                for item_id in allowed_ids
                if total_lower <= by_id[item_id].value <= total_upper
            )
        )
    return MeanMultisetSupportResult(
        "ok",
        tuple(output),
        count_one_ids,
    )


__all__ = (
    "MEAN_SUPPORT_LAYER_SIGNATURE_BUDGET",
    "MEAN_SUPPORT_MAX_COUNT",
    "MEAN_SUPPORT_MAX_INTERVAL_WIDTH",
    "MEAN_SUPPORT_OUTPUT_SIGNATURE_BUDGET",
    "MEAN_SUPPORT_TRANSITION_BUDGET",
    "MeanMultisetSupportResult",
    "MeanSupportCandidate",
    "MeanSupportCompositionSignature",
    "MeanSupportMandatoryUnit",
    "solve_repeat_allowed_mean_support",
)
