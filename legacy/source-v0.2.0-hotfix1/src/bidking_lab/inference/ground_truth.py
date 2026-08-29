"""Sample one auction session with full ground-truth visibility.

The Phase 1A inference engine consumes *partial* observations (tool
readings + hero outlines) and produces top-K hypotheses for the unknown
warehouse contents. To evaluate that engine's accuracy we need
*ground-truth* sessions — sessions where we know exactly which items
landed in the cabinet, then synthetically apply only the readings the
player would have, then compare engine output against truth.

This module owns the ground-truth side of that loop. ``synth_readings``
owns the tool-reveal mapping, ``roi`` owns the leave-one-out ROI math.

Sampling reuses the same drop-pool flattening + per-item count
distribution as :mod:`bidking_lab.simulation.basic_mc` so that sampled
sessions are statistically identical to those in our existing MC.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.observation import HUGE_CELLS_PER_QUALITY
from bidking_lab.simulation.basic_mc import FlattenedPool, flatten_pool

_TEMPORARY_BLUE_ZODIAC_ITEM_IDS = frozenset(range(1306003, 1306015))
_TEMPORARY_BLUE_ZODIAC_POOL_MASS = 0.01


def is_huge_item(item: Item) -> bool:
    """True iff ``item`` occupies >= the huge threshold for its quality.

    Definition: "巨物 = 占 >= 12 格 的藏品". Only purple (q=4), gold
    (q=5), and red (q=6) have huge-band semantics; other qualities
    always return False (no in-game "huge" concept for white/green/blue).
    """
    threshold = HUGE_CELLS_PER_QUALITY.get(item.quality)
    if threshold is None:
        return False
    return item.shape_w * item.shape_h >= threshold


@dataclass
class BucketTruth:
    """Ground-truth contents of one quality bucket in a sampled session."""

    quality: int
    count: int = 0
    total_cells: int = 0
    value_sum: int = 0
    huge_count: int = 0
    items: list[Item] = field(default_factory=list)


@dataclass
class SessionTruth:
    """Ground-truth contents of one full sampled session."""

    map_id: int
    map_name: str
    warehouse_total_cells: int
    buckets: dict[int, BucketTruth] = field(default_factory=dict)

    def bucket(self, quality: int) -> BucketTruth | None:
        return self.buckets.get(quality)

    def total_value(self) -> int:
        """Sum of value across all buckets — the headline ground-truth number."""
        return sum(b.value_sum for b in self.buckets.values())


@dataclass
class _PreparedPool:
    """Flattened drop pool arrays reused across many session samples."""

    items: tuple[Item, ...]
    probabilities: np.ndarray
    n_min: np.ndarray
    n_max: np.ndarray
    areas: np.ndarray
    qualities: np.ndarray
    values: np.ndarray
    huge_flags: np.ndarray


def _prepare_pool(fp: FlattenedPool, items: Mapping[int, Item]) -> _PreparedPool:
    pool_items = tuple(items[item_id] for item_id in fp.item_ids)
    return _PreparedPool(
        items=pool_items,
        probabilities=np.array(fp.probabilities, dtype=np.float64),
        n_min=np.array(fp.n_min, dtype=np.int64),
        n_max=np.array(fp.n_max, dtype=np.int64),
        areas=np.array([it.shape_w * it.shape_h for it in pool_items], dtype=np.int64),
        qualities=np.array([it.quality for it in pool_items], dtype=np.int64),
        values=np.array([it.value for it in pool_items], dtype=np.int64),
        huge_flags=np.array([is_huge_item(it) for it in pool_items], dtype=np.bool_),
    )


def _with_temporary_blue_zodiac_pool_items(
    sampler: "SessionTruthSampler",
    items: Mapping[int, Item],
) -> "SessionTruthSampler":
    """Temporarily include active ordinary 2x2 q3 zodiac items in MC pools."""

    zodiac_items = tuple(
        item
        for item_id in sorted(_TEMPORARY_BLUE_ZODIAC_ITEM_IDS)
        if (item := items.get(item_id)) is not None
        and item.quality == 3
        and item.shape_w == 2
        and item.shape_h == 2
    )
    if not zodiac_items:
        return sampler

    pools = []
    for pool in sampler.pools:
        existing_ids = {item.item_id for item in pool.items}
        extras = tuple(item for item in zodiac_items if item.item_id not in existing_ids)
        if not extras:
            pools.append(pool)
            continue
        existing_total = float(pool.probabilities.sum())
        if existing_total <= 0:
            pools.append(pool)
            continue

        existing_probabilities = pool.probabilities.astype(np.float64) * (
            1.0 - _TEMPORARY_BLUE_ZODIAC_POOL_MASS
        )
        extra_probabilities = np.full(
            len(extras),
            _TEMPORARY_BLUE_ZODIAC_POOL_MASS / len(extras),
            dtype=np.float64,
        )
        pools.append(
            replace(
                pool,
                items=tuple((*pool.items, *extras)),
                probabilities=np.concatenate(
                    (existing_probabilities, extra_probabilities)
                ),
                n_min=np.concatenate(
                    (pool.n_min, np.ones(len(extras), dtype=np.int64))
                ),
                n_max=np.concatenate(
                    (pool.n_max, np.ones(len(extras), dtype=np.int64))
                ),
                areas=np.concatenate(
                    (pool.areas, np.full(len(extras), 4, dtype=np.int64))
                ),
                qualities=np.concatenate(
                    (pool.qualities, np.full(len(extras), 3, dtype=np.int64))
                ),
                values=np.concatenate(
                    (
                        pool.values,
                        np.asarray([item.value for item in extras], dtype=np.int64),
                    )
                ),
                huge_flags=np.concatenate(
                    (pool.huge_flags, np.zeros(len(extras), dtype=np.bool_))
                ),
            )
        )
    return replace(sampler, pools=tuple(pools))


@dataclass
class SessionTruthSampler:
    """Pre-flattened sampler for running many MC trials on the same map."""

    map_id: int
    map_name: str
    items_per_session_min: int
    items_per_session_max: int
    pools: tuple[_PreparedPool, ...]
    pool_weights: np.ndarray

    def sample(self, rng: np.random.Generator | None = None) -> SessionTruth:
        """Sample one session using precomputed pool arrays."""
        rng = rng or np.random.default_rng()
        if not self.pools:
            return SessionTruth(
                map_id=self.map_id,
                map_name=self.map_name,
                warehouse_total_cells=0,
                buckets={},
            )
        pool_idx = (
            int(rng.choice(len(self.pools), p=self.pool_weights))
            if len(self.pools) > 1
            else 0
        )
        pool = self.pools[pool_idx]
        if len(pool.probabilities) == 0:
            return SessionTruth(
                map_id=self.map_id,
                map_name=self.map_name,
                warehouse_total_cells=0,
                buckets={},
            )

        k = int(rng.integers(self.items_per_session_min, self.items_per_session_max + 1))
        sampled_idx = rng.choice(
            len(pool.probabilities), size=k, replace=True, p=pool.probabilities,
        )
        counts = rng.integers(pool.n_min[sampled_idx], pool.n_max[sampled_idx] + 1)

        buckets: dict[int, BucketTruth] = {}
        for pool_i, cnt in zip(sampled_idx, counts):
            idx = int(pool_i)
            quality = int(pool.qualities[idx])
            bucket = buckets.setdefault(quality, BucketTruth(quality=quality))
            item = pool.items[idx]
            item_count = int(cnt)
            bucket.count += item_count
            bucket.total_cells += int(pool.areas[idx]) * item_count
            bucket.value_sum += int(pool.values[idx]) * item_count
            if bool(pool.huge_flags[idx]):
                bucket.huge_count += item_count
            bucket.items.extend([item] * item_count)

        warehouse_total = sum(b.total_cells for b in buckets.values())
        return SessionTruth(
            map_id=self.map_id,
            map_name=self.map_name,
            warehouse_total_cells=warehouse_total,
            buckets=buckets,
        )


def prepare_session_sampler(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> SessionTruthSampler:
    """Build a reusable sampler for repeated MC trials on one map."""
    bid_map = maps[map_id]
    pools: list[_PreparedPool] = []
    weights: list[int] = []

    if not bid_map.sub_pool_weights:
        pools.append(_prepare_pool(flatten_pool(bid_map.drop_pool_id, drops, items), items))
        weights.append(1)
    else:
        for sub_map_id, weight in bid_map.sub_pool_weights:
            sub_map = maps.get(sub_map_id)
            if sub_map is None:
                continue
            fp = flatten_pool(sub_map.drop_pool_id, drops, items)
            if not fp.item_ids:
                continue
            pools.append(_prepare_pool(fp, items))
            weights.append(weight)
        if not pools:
            pools.append(_prepare_pool(flatten_pool(bid_map.drop_pool_id, drops, items), items))
            weights.append(1)

    weights_arr = np.array(weights, dtype=np.float64)
    weights_arr = weights_arr / weights_arr.sum()
    sampler = SessionTruthSampler(
        map_id=map_id,
        map_name=bid_map.name,
        items_per_session_min=bid_map.items_per_session_min,
        items_per_session_max=bid_map.items_per_session_max,
        pools=tuple(pools),
        pool_weights=weights_arr,
    )
    return _with_temporary_blue_zodiac_pool_items(sampler, items)


def _resolve_sub_pool(
    bid_map: BidMap,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    rng: np.random.Generator,
):
    """Pick one sub-pool (anthology routing) and return its flattened form."""
    if not bid_map.sub_pool_weights:
        return flatten_pool(bid_map.drop_pool_id, drops, items)
    sub_pools = []
    weights = []
    for sub_map_id, w in bid_map.sub_pool_weights:
        sub_map = maps.get(sub_map_id)
        if sub_map is None:
            continue
        fp = flatten_pool(sub_map.drop_pool_id, drops, items)
        if not fp.item_ids:
            continue
        sub_pools.append(fp)
        weights.append(w)
    if not sub_pools:
        return flatten_pool(bid_map.drop_pool_id, drops, items)
    weights_arr = np.array(weights, dtype=np.float64)
    weights_arr = weights_arr / weights_arr.sum()
    idx = int(rng.choice(len(sub_pools), p=weights_arr))
    return sub_pools[idx]


def sample_session_truth(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    rng: np.random.Generator | None = None,
) -> SessionTruth:
    """Sample one ground-truth session for ``map_id``.

    Statistically equivalent to one trial of
    :func:`bidking_lab.simulation.bidding.simulate_session`, but exposes
    the actual sampled :class:`Item` objects (grouped by quality) so that
    downstream tooling can compute exact per-bucket truth.
    """
    if rng is None:
        rng = np.random.default_rng()
    bid_map = maps[map_id]
    fp = _resolve_sub_pool(bid_map, maps, drops, items, rng)

    probs = np.array(fp.probabilities, dtype=np.float64)
    if len(probs) == 0:
        return SessionTruth(
            map_id=map_id,
            map_name=bid_map.name,
            warehouse_total_cells=0,
            buckets={},
        )

    k_lo = bid_map.items_per_session_min
    k_hi = bid_map.items_per_session_max
    k = int(rng.integers(k_lo, k_hi + 1))
    sampled_idx = rng.choice(len(probs), size=k, replace=True, p=probs)

    n_min = np.array(fp.n_min, dtype=np.int64)
    n_max = np.array(fp.n_max, dtype=np.int64)
    counts = rng.integers(n_min[sampled_idx], n_max[sampled_idx] + 1)

    buckets: dict[int, BucketTruth] = {}
    for pool_i, cnt in zip(sampled_idx, counts):
        item = items[fp.item_ids[int(pool_i)]]
        area = item.shape_w * item.shape_h
        bucket = buckets.setdefault(item.quality, BucketTruth(quality=item.quality))
        for _ in range(int(cnt)):
            bucket.count += 1
            bucket.total_cells += area
            bucket.value_sum += item.value
            if is_huge_item(item):
                bucket.huge_count += 1
            bucket.items.append(item)

    warehouse_total = sum(b.total_cells for b in buckets.values())
    return SessionTruth(
        map_id=map_id,
        map_name=bid_map.name,
        warehouse_total_cells=warehouse_total,
        buckets=buckets,
    )


__all__ = (
    "BucketTruth",
    "SessionTruthSampler",
    "SessionTruth",
    "is_huge_item",
    "prepare_session_sampler",
    "sample_session_truth",
)
