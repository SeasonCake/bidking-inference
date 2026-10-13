"""Map-level likelihood from partial live observations.

This module is intentionally separate from ``posterior.filter_truths_by_obs``:
that filter always treats ``SessionObs.warehouse_capacity()`` as a target,
which falls back to 159 when the player has no warehouse reading. Map
likelihood must not do that, because early live packet sessions often have
quality/tool evidence but no total warehouse cells yet.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import SessionTruth, prepare_session_sampler
from bidking_lab.inference.observation import SessionObs

QUALITIES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class QuantileSummary:
    """P10/P50/P90 summary for one scalar posterior."""

    p10: float
    p50: float
    p90: float


@dataclass(frozen=True)
class MapLikelihoodResult:
    """Likelihood summary for one candidate map."""

    map_id: int
    map_name: str
    n_total: int
    n_matched: int
    likelihood: float
    posterior_probability: float = 0.0
    total_cells: QuantileSummary | None = None
    total_value: QuantileSummary | None = None


def _bucket_truth_fields(truth: SessionTruth, quality: int) -> tuple[int, int, int, int]:
    bucket = truth.buckets.get(quality)
    if bucket is None:
        return 0, 0, 0, 0
    return bucket.total_cells, bucket.count, bucket.value_sum, bucket.huge_count


def _merged_bucket_truth_fields(
    truth: SessionTruth,
    qualities: tuple[int, ...],
) -> tuple[int, int, int, int]:
    cells = 0
    count = 0
    value = 0
    huge = 0
    for quality in qualities:
        c, n, v, h = _bucket_truth_fields(truth, quality)
        cells += c
        count += n
        value += v
        huge += h
    return cells, count, value, huge


def _warehouse_target(obs: SessionObs) -> tuple[int, int] | None:
    if obs.warehouse_total_cells is not None:
        return obs.warehouse_total_cells, max(0, obs.warehouse_total_cells_tolerance or 0)
    if obs.warehouse_total_cells_approx is not None:
        return obs.warehouse_total_cells_approx, max(0, obs.warehouse_total_cells_tolerance or 0)
    return None


def _item_categories(item: Item) -> tuple[int, ...]:
    return tuple(int(tag) for tag in item.tags)


def _shape_dimensions(shape_key: str | int | None) -> tuple[int, int] | None:
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


def _category_item_key(item: Item) -> tuple[tuple[int, ...], int, int, int, int, int]:
    return (
        _item_categories(item),
        item.quality,
        item.shape_w * item.shape_h,
        item.shape_w,
        item.shape_h,
        item.item_id,
    )


def category_observation_soft_score(
    truth: SessionTruth,
    obs: SessionObs,
    *,
    miss_penalty: float = 0.35,
) -> float:
    """Return a soft [0, 1] match score for category-aware item reveals.

    分类鉴影 is useful but still being calibrated. A miss lowers posterior
    weight instead of rejecting the sample, so incomplete action-id mappings
    or item tags cannot wipe out the posterior.
    """

    if not obs.category_items:
        return 1.0

    available = Counter()
    for bucket in truth.buckets.values():
        for item in bucket.items:
            available[_category_item_key(item)] += 1

    matched = 0
    total = 0
    for observed in obs.category_items:
        repeats = max(1, int(observed.count))
        for _ in range(repeats):
            total += 1
            match_key = None
            for key, count in available.items():
                if count <= 0:
                    continue
                categories, quality, cells, width, height, item_id = key
                if observed.category not in categories:
                    continue
                if observed.required_categories and not all(
                    category in categories
                    for category in observed.required_categories
                ):
                    continue
                if observed.excluded_categories and any(
                    category in categories
                    for category in observed.excluded_categories
                ):
                    continue
                if observed.quality is not None and quality != observed.quality:
                    continue
                if observed.cells is not None and cells != observed.cells:
                    continue
                observed_dims = _shape_dimensions(observed.shape_key)
                if observed_dims is not None and observed_dims != (width, height):
                    continue
                if observed.item_id is not None and item_id != observed.item_id:
                    continue
                match_key = key
                break
            if match_key is None:
                continue
            available[match_key] -= 1
            matched += 1

    if total <= 0:
        return 1.0
    misses = total - matched
    return (matched + miss_penalty * misses) / total


def truth_matches_obs(
    truth: SessionTruth,
    obs: SessionObs,
    *,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    warehouse_tol: int = 8,
    total_item_count_tol: int = 0,
) -> bool:
    """Return whether a sampled truth is compatible with observations.

    Unlike ``posterior.filter_truths_by_obs``, warehouse cells are only
    checked when the observation contains an exact or approximate warehouse
    reading. Missing warehouse evidence imposes no warehouse constraint.
    """

    wh = _warehouse_target(obs)
    if wh is not None:
        target, declared_tol = wh
        effective_tol = max(warehouse_tol, declared_tol)
        if abs(truth.warehouse_total_cells - target) > effective_tol:
            return False

    if obs.total_item_count is not None:
        truth_items = sum(bucket.count for bucket in truth.buckets.values())
        if abs(truth_items - obs.total_item_count) > max(0, total_item_count_tol):
            return False
    else:
        truth_items = sum(bucket.count for bucket in truth.buckets.values())
        if (
            obs.visible_outline_item_count_min is not None
            and truth_items < obs.visible_outline_item_count_min
        ):
            return False

    if (
        obs.visible_outline_total_cells_min is not None
        and truth.warehouse_total_cells < obs.visible_outline_total_cells_min
    ):
        return False

    merge_q1_q2 = 1 in obs.buckets and 2 not in obs.buckets
    for quality, bucket_obs in obs.buckets.items():
        if quality == 1 and merge_q1_q2:
            cells, count, value, huge = _merged_bucket_truth_fields(truth, (1, 2))
        else:
            cells, count, value, huge = _bucket_truth_fields(truth, quality)

        if bucket_obs.total_cells is not None:
            effective_cells_tol = 0 if bucket_obs.total_cells == 0 else cells_tol
            if abs(cells - bucket_obs.total_cells) > effective_cells_tol:
                return False
        if bucket_obs.total_cells_min is not None:
            if cells < bucket_obs.total_cells_min:
                return False
        if bucket_obs.count is not None:
            effective_count_tol = 0 if bucket_obs.count == 0 else count_tol
            if abs(count - bucket_obs.count) > effective_count_tol:
                return False
        if bucket_obs.count_min is not None:
            if count < bucket_obs.count_min:
                return False
        if bucket_obs.value_sum is not None and bucket_obs.value_sum > 0:
            if abs(value - bucket_obs.value_sum) / bucket_obs.value_sum > value_rel_tol:
                return False
        if bucket_obs.value_range is not None:
            lo, hi = bucket_obs.value_range
            if not (lo <= value <= hi):
                return False
        if bucket_obs.huge_band != "none":
            lo, hi = bucket_obs.huge_count_range()
            if not (lo <= huge <= hi):
                return False
    return True


def _quantiles(values: Sequence[int]) -> QuantileSummary | None:
    if not values:
        return None
    p10, p50, p90 = np.percentile(np.array(values, dtype=np.float64), [10, 50, 90])
    return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))


def _weighted_quantiles(
    values: Sequence[int],
    weights: Sequence[float],
) -> QuantileSummary | None:
    if not values:
        return None
    if not weights or len(weights) != len(values):
        return _quantiles(values)
    if len(set(round(float(weight), 12) for weight in weights)) <= 1:
        return _quantiles(values)

    arr = np.asarray(values, dtype=np.float64)
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


def summarize_map_truths(
    map_id: int,
    map_name: str,
    truths: Sequence[SessionTruth],
    obs: SessionObs,
    *,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    warehouse_tol: int = 8,
    total_item_count_tol: int = 0,
) -> MapLikelihoodResult:
    """Summarize how often sampled truths for one map match the observation."""

    matched: list[SessionTruth] = []
    weights: list[float] = []
    for truth in truths:
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
        matched.append(truth)
        weights.append(category_observation_soft_score(truth, obs))

    n_total = len(truths)
    likelihood = (sum(weights) / n_total) if n_total else 0.0
    return MapLikelihoodResult(
        map_id=map_id,
        map_name=map_name,
        n_total=n_total,
        n_matched=len(matched),
        likelihood=likelihood,
        total_cells=_weighted_quantiles(
            [truth.warehouse_total_cells for truth in matched],
            weights,
        ),
        total_value=_weighted_quantiles(
            [truth.total_value() for truth in matched],
            weights,
        ),
    )


def estimate_map_likelihood(
    candidate_map_ids: Sequence[int],
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
) -> list[MapLikelihoodResult]:
    """Estimate normalized map posterior probabilities by Monte Carlo."""

    rng = np.random.default_rng(seed)
    results: list[MapLikelihoodResult] = []
    for map_id in candidate_map_ids:
        if map_id not in maps:
            continue
        sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
        truths = [sampler.sample(rng=rng) for _ in range(max(0, int(n_trials)))]
        results.append(
            summarize_map_truths(
                map_id,
                maps[map_id].name,
                truths,
                obs,
                cells_tol=cells_tol,
                count_tol=count_tol,
                value_rel_tol=value_rel_tol,
                warehouse_tol=warehouse_tol,
                total_item_count_tol=total_item_count_tol,
            )
        )

    total = sum(result.likelihood for result in results)
    if total <= 0:
        return sorted(results, key=lambda result: result.likelihood, reverse=True)

    normalized = [
        MapLikelihoodResult(
            map_id=result.map_id,
            map_name=result.map_name,
            n_total=result.n_total,
            n_matched=result.n_matched,
            likelihood=result.likelihood,
            posterior_probability=result.likelihood / total,
            total_cells=result.total_cells,
            total_value=result.total_value,
        )
        for result in results
    ]
    return sorted(
        normalized,
        key=lambda result: (result.posterior_probability, result.likelihood),
        reverse=True,
    )


__all__ = (
    "MapLikelihoodResult",
    "QuantileSummary",
    "category_observation_soft_score",
    "estimate_map_likelihood",
    "summarize_map_truths",
    "truth_matches_obs",
)
