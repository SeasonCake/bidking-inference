"""Deterministic drop-prior summaries for v3 shadow reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import prepare_session_sampler
from bidking_lab.simulation.robust_value import DEFAULT_VALUE_FLOOR


@dataclass(frozen=True)
class QualityPriorReport:
    quality: int
    draw_probability: float
    session_probability: float
    expected_session_count: float
    expected_session_cells: float
    expected_session_value: float


@dataclass(frozen=True)
class SessionPriorReport:
    map_id: int
    map_name: str
    items_per_session_min: int
    items_per_session_max: int
    pool_count: int
    expected_draws: float
    expected_session_count: float
    expected_session_cells: float
    expected_session_value: float
    quality_priors: tuple[QualityPriorReport, ...]

    def quality(self, quality: int) -> QualityPriorReport | None:
        for prior in self.quality_priors:
            if prior.quality == quality:
                return prior
        return None

    def to_flat_dict(self, *, prefix: str = "v3_prior_") -> dict[str, Any]:
        q6 = self.quality(6)
        return {
            f"{prefix}map_id": self.map_id,
            f"{prefix}map_name": self.map_name,
            f"{prefix}items_per_session_min": self.items_per_session_min,
            f"{prefix}items_per_session_max": self.items_per_session_max,
            f"{prefix}pool_count": self.pool_count,
            f"{prefix}expected_draws": round(self.expected_draws, 6),
            f"{prefix}expected_count": round(self.expected_session_count, 6),
            f"{prefix}expected_cells": round(self.expected_session_cells, 6),
            f"{prefix}expected_value": round(self.expected_session_value, 6),
            f"{prefix}q6_draw_probability": (
                round(q6.draw_probability, 6) if q6 is not None else 0.0
            ),
            f"{prefix}q6_session_probability": (
                round(q6.session_probability, 6) if q6 is not None else 0.0
            ),
            f"{prefix}q6_expected_count": (
                round(q6.expected_session_count, 6) if q6 is not None else 0.0
            ),
            f"{prefix}q6_expected_cells": (
                round(q6.expected_session_cells, 6) if q6 is not None else 0.0
            ),
            f"{prefix}q6_expected_value": (
                round(q6.expected_session_value, 6) if q6 is not None else 0.0
            ),
        }


def empty_prior_flat_dict(*, prefix: str = "v3_prior_") -> dict[str, Any]:
    return {
        f"{prefix}available": False,
        f"{prefix}error": None,
        f"{prefix}map_id": None,
        f"{prefix}map_name": None,
        f"{prefix}items_per_session_min": None,
        f"{prefix}items_per_session_max": None,
        f"{prefix}pool_count": None,
        f"{prefix}expected_draws": None,
        f"{prefix}expected_count": None,
        f"{prefix}expected_cells": None,
        f"{prefix}expected_value": None,
        f"{prefix}q6_draw_probability": None,
        f"{prefix}q6_session_probability": None,
        f"{prefix}q6_expected_count": None,
        f"{prefix}q6_expected_cells": None,
        f"{prefix}q6_expected_value": None,
    }


def summarize_drop_prior_flat_dict(
    map_id: int | None,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    prefix: str = "v3_prior_",
) -> dict[str, Any]:
    if map_id is None:
        return empty_prior_flat_dict(prefix=prefix)
    try:
        prior = summarize_drop_prior(
            int(map_id),
            maps=maps,
            drops=drops,
            items=items,
        )
    except Exception as exc:
        row = empty_prior_flat_dict(prefix=prefix)
        row[f"{prefix}error"] = type(exc).__name__
        return row
    row = {f"{prefix}available": True, f"{prefix}error": None}
    row.update(prior.to_flat_dict(prefix=prefix))
    return row


def _session_probability_for_draw(
    draw_probability: float,
    items_per_session_min: int,
    items_per_session_max: int,
) -> float:
    draw_probability = min(1.0, max(0.0, float(draw_probability)))
    if draw_probability <= 0.0:
        return 0.0
    if draw_probability >= 1.0:
        return 1.0
    k_min = int(items_per_session_min)
    k_max = int(items_per_session_max)
    if k_max < k_min or k_max <= 0:
        return 0.0
    probabilities = [
        1.0 - ((1.0 - draw_probability) ** draw_count)
        for draw_count in range(max(0, k_min), k_max + 1)
    ]
    return float(sum(probabilities) / len(probabilities)) if probabilities else 0.0


def summarize_drop_prior(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> SessionPriorReport:
    """Return a deterministic map/drop-table prior before runtime evidence.

    This is intentionally not a posterior estimator. It is the baseline prior
    that later v3 likelihood/count-cell samplers must move away from when
    evidence supports doing so.
    """

    sampler = prepare_session_sampler(
        int(map_id),
        maps=maps,
        drops=drops,
        items=items,
    )
    expected_draws = (
        int(sampler.items_per_session_min) + int(sampler.items_per_session_max)
    ) / 2
    by_quality: dict[int, dict[str, float]] = defaultdict(
        lambda: {
            "draw_probability": 0.0,
            "session_probability": 0.0,
            "expected_session_count": 0.0,
            "expected_session_cells": 0.0,
            "expected_session_value": 0.0,
        }
    )
    expected_session_count = 0.0
    expected_session_cells = 0.0
    expected_session_value = 0.0
    for pool, pool_weight_raw in zip(sampler.pools, sampler.pool_weights):
        pool_weight = float(pool_weight_raw)
        if len(pool.probabilities) == 0:
            continue
        mean_counts = (pool.n_min + pool.n_max) / 2
        expected_counts = pool.probabilities * mean_counts * expected_draws
        expected_cells = expected_counts * pool.areas
        expected_values = expected_counts * pool.values
        expected_session_count += pool_weight * float(expected_counts.sum())
        expected_session_cells += pool_weight * float(expected_cells.sum())
        expected_session_value += pool_weight * float(expected_values.sum())
        qualities = tuple(int(value) for value in np.unique(pool.qualities))
        for quality in qualities:
            mask = pool.qualities == quality
            draw_probability = float(pool.probabilities[mask].sum())
            quality_prior = by_quality[quality]
            quality_prior["draw_probability"] += pool_weight * draw_probability
            quality_prior["session_probability"] += pool_weight * _session_probability_for_draw(
                draw_probability,
                sampler.items_per_session_min,
                sampler.items_per_session_max,
            )
            quality_prior["expected_session_count"] += pool_weight * float(
                expected_counts[mask].sum()
            )
            quality_prior["expected_session_cells"] += pool_weight * float(
                expected_cells[mask].sum()
            )
            quality_prior["expected_session_value"] += pool_weight * float(
                expected_values[mask].sum()
            )
    quality_priors = tuple(
        QualityPriorReport(
            quality=quality,
            draw_probability=values["draw_probability"],
            session_probability=values["session_probability"],
            expected_session_count=values["expected_session_count"],
            expected_session_cells=values["expected_session_cells"],
            expected_session_value=values["expected_session_value"],
        )
        for quality, values in sorted(by_quality.items())
    )
    return SessionPriorReport(
        map_id=sampler.map_id,
        map_name=sampler.map_name,
        items_per_session_min=int(sampler.items_per_session_min),
        items_per_session_max=int(sampler.items_per_session_max),
        pool_count=len(sampler.pools),
        expected_draws=expected_draws,
        expected_session_count=expected_session_count,
        expected_session_cells=expected_session_cells,
        expected_session_value=expected_session_value,
        quality_priors=quality_priors,
    )


def _weighted_p50(values: list[int], weights: list[float]) -> int:
    if not values:
        return 0
    pairs = sorted(zip(values, weights), key=lambda pair: pair[0])
    total_weight = sum(max(0.0, weight) for _value, weight in pairs)
    if total_weight <= 0:
        return int(pairs[len(pairs) // 2][0])
    cutoff = total_weight * 0.5
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += max(0.0, weight)
        if cumulative >= cutoff:
            return int(value)
    return int(pairs[-1][0])


def ordinary_shape_replacement_values(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> dict[tuple[int, int, int], int]:
    """Return map-weighted ordinary-value P50 by ``(quality, width, height)``.

    This mirrors the v2 tail-replacement audit idea without importing the v2
    sampler. Only positive values below ``DEFAULT_VALUE_FLOOR`` are eligible,
    so extreme tails are never used as their own replacement.
    """

    sampler = prepare_session_sampler(
        int(map_id),
        maps=maps,
        drops=drops,
        items=items,
    )
    values_by_shape: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    weights_by_shape: dict[tuple[int, int, int], list[float]] = defaultdict(list)
    for pool, pool_weight_raw in zip(sampler.pools, sampler.pool_weights):
        pool_weight = float(pool_weight_raw)
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
            key = (int(item.quality), int(item.shape_w), int(item.shape_h))
            values_by_shape[key].append(int(item.value))
            weights_by_shape[key].append(
                pool_weight * float(probability) * float(mean_count)
            )
    return {
        key: _weighted_p50(values, weights_by_shape[key])
        for key, values in values_by_shape.items()
    }


__all__ = (
    "empty_prior_flat_dict",
    "ordinary_shape_replacement_values",
    "QualityPriorReport",
    "SessionPriorReport",
    "summarize_drop_prior",
    "summarize_drop_prior_flat_dict",
)
