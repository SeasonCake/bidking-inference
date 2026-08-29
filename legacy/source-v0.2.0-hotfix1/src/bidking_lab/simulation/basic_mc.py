"""First-pass Monte Carlo for a single BidMap session.

Drop pools in BidKing are **multi-level**: a map's top pool typically
points to a "quality distribution" pool, which points to a "category
blind box", which finally points to a category-quality leaf pool of
actual Item.txt items. ``DropEntry.category == 9999`` marks an entry
as a pool-reference (recurse with ``item_id`` as the next pool id);
any other category means the entry is a leaf with a real ``item_id``.

Strategy:

1.  For each map, flatten the multi-level pool tree into a single
    distribution of ``leaf_item_id → effective_probability``. This is
    done once per simulation call (and once per sub-pool for anthology
    maps).

2.  For each trial:
    - Pick an effective leaf-distribution (anthology maps choose a
      sub-pool weighted by ``sub_pool_weights``; leaf maps always use
      their own).
    - Sample ``K`` items from that distribution where
      ``K ~ Uniform[items_per_session_min, items_per_session_max]``.
    - Count per item ``~ Uniform[n_min, n_max]`` from the leaf entry.
    - Total value = ``sum(value × count)``.

Known v1 simplifications (deliberate, documented):

- **Sampling is with replacement.** Real auctions show unique slots,
  so the variance we report is wider than the truth. For ranking maps
  by expected value the bias is tiny; for tight quantile claims it
  matters and is a Q1-followup.
- **No hero skills.** Hero effects (e.g. 艾哈迈德 revealing counts)
  change player *decisions*, not the underlying loot distribution.
  Project_vision Q4 will model their marginal value separately.
- **No bidding / budget.** We assume the player buys everything that
  drops; result is "gross take if you won everything".
- **No grid placement.** Q3 needs item footprints, deferred.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
from pydantic import BaseModel

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item

POOL_REFERENCE_CATEGORY = 9999
"""Sentinel in ``DropEntry.category``: this entry refers to another pool
rather than to a real Item.txt item."""

_MAX_POOL_DEPTH = 16


def is_physical_loot_item(item: Item) -> bool:
    """Return True for auctionable collectibles that can affect inference."""
    return item.quality > 0 and item.shape_w > 0 and item.shape_h > 0 and item.value > 0


class FlattenedPool(BaseModel):
    """Flat (item_id, prob, count-range, value) view of a multi-level pool.

    All arrays have the same length ``N``. ``probabilities`` sums to 1.0.
    """

    item_ids: list[int]
    probabilities: list[float]
    n_min: list[int]
    n_max: list[int]
    values: list[int]


class SimulationResult(BaseModel):
    """Summary statistics of an N-trial Monte Carlo run."""

    map_id: int
    map_name: str
    n_trials: int
    pool_size_after_flatten: int
    mean: float
    std: float
    min: int
    q05: int
    q50: int
    q95: int
    max: int


def flatten_pool(
    pool_id: int,
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> FlattenedPool:
    """Walk ``pool_id`` recursively, expanding cat-9999 references, until
    every node is a leaf entry with a known Item.txt item.

    Returns a single distribution over leaf items with their effective
    probabilities (path probability product) and count ranges. Same
    ``item_id`` reached via multiple paths gets its probabilities
    summed and count range unioned.
    """
    bucket: dict[int, dict[str, float | int]] = {}

    def walk(pid: int, path_prob: float, depth: int) -> None:
        if depth > _MAX_POOL_DEPTH:
            raise RuntimeError(
                f"pool resolution exceeded depth {_MAX_POOL_DEPTH} at pool_id={pid}"
            )
        pool = drops.get(pid)
        if pool is None or not pool.entries:
            return
        total = sum(e.weight for e in pool.entries)
        if total <= 0:
            return
        for entry in pool.entries:
            if entry.weight <= 0:
                continue
            edge_p = path_prob * (entry.weight / total)
            if entry.category == POOL_REFERENCE_CATEGORY:
                walk(entry.item_id, edge_p, depth + 1)
                continue
            # Leaf entry: must resolve to a real physical collectible to count.
            item = items.get(entry.item_id)
            if item is None or not is_physical_loot_item(item):
                continue
            slot = bucket.get(entry.item_id)
            if slot is None:
                bucket[entry.item_id] = {
                    "p": edge_p,
                    "n_min": entry.n_min,
                    "n_max": entry.n_max,
                }
            else:
                slot["p"] = float(slot["p"]) + edge_p
                slot["n_min"] = min(int(slot["n_min"]), entry.n_min)
                slot["n_max"] = max(int(slot["n_max"]), entry.n_max)

    walk(pool_id, 1.0, 0)

    item_ids = sorted(bucket.keys())
    probs = [float(bucket[i]["p"]) for i in item_ids]
    n_min = [int(bucket[i]["n_min"]) for i in item_ids]
    n_max = [int(bucket[i]["n_max"]) for i in item_ids]
    values = [int(items[i].value) for i in item_ids]
    total_p = sum(probs)
    if total_p > 0:
        probs = [p / total_p for p in probs]
    return FlattenedPool(
        item_ids=item_ids,
        probabilities=probs,
        n_min=n_min,
        n_max=n_max,
        values=values,
    )


def _flattened_arrays(
    fp: FlattenedPool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array(fp.item_ids, dtype=np.int64),
        np.array(fp.probabilities, dtype=np.float64),
        np.array(fp.n_min, dtype=np.int64),
        np.array(fp.n_max, dtype=np.int64),
        np.array(fp.values, dtype=np.int64),
    )


def simulate_map(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 10_000,
    rng: np.random.Generator | None = None,
) -> SimulationResult:
    """Run ``n_trials`` Monte Carlo sessions of the given map.

    See module docstring for the model. Returns a ``SimulationResult``
    with mean / std / quantile summary of session total value.
    """
    rng = rng or np.random.default_rng()
    bid_map = maps[map_id]

    k_lo = bid_map.items_per_session_min
    k_hi = bid_map.items_per_session_max
    if k_hi < k_lo:
        raise ValueError(f"map {map_id} has inverted item range [{k_lo},{k_hi}]")

    # Build the effective leaf distribution(s). For leaf maps it's one.
    # For anthology maps we keep one per sub-pool plus the anthology
    # weights so we can pick a sub-pool per trial.
    if not bid_map.sub_pool_weights:
        flat = flatten_pool(bid_map.drop_pool_id, drops, items)
        sub_pool_arrays = [_flattened_arrays(flat)]
        sub_pool_p = np.array([1.0])
    else:
        arrays_list = []
        weight_list = []
        for sub_map_id, sub_weight in bid_map.sub_pool_weights:
            sub_map = maps.get(sub_map_id)
            if sub_map is None:
                continue
            flat = flatten_pool(sub_map.drop_pool_id, drops, items)
            if not flat.item_ids:
                continue
            arrays_list.append(_flattened_arrays(flat))
            weight_list.append(sub_weight)
        if not arrays_list:
            arrays_list = [_flattened_arrays(FlattenedPool(item_ids=[], probabilities=[], n_min=[], n_max=[], values=[]))]
            weight_list = [1.0]
        sub_pool_arrays = arrays_list
        sub_pool_p = np.array(weight_list, dtype=np.float64)
        sub_pool_p = sub_pool_p / sub_pool_p.sum()

    pool_size_after_flatten = max(len(a[0]) for a in sub_pool_arrays)

    results = np.zeros(n_trials, dtype=np.float64)
    for t in range(n_trials):
        sub_idx = (
            int(rng.choice(len(sub_pool_arrays), p=sub_pool_p))
            if len(sub_pool_arrays) > 1
            else 0
        )
        item_ids, p, n_mins, n_maxs, values = sub_pool_arrays[sub_idx]
        pool_size = len(item_ids)
        if pool_size == 0:
            results[t] = 0.0
            continue
        k = int(rng.integers(k_lo, k_hi + 1))
        # Sampling with replacement — see module docstring for caveat.
        idx = rng.choice(pool_size, size=k, replace=True, p=p)
        counts = rng.integers(n_mins[idx], n_maxs[idx] + 1)
        results[t] = float((values[idx] * counts).sum())

    return SimulationResult(
        map_id=map_id,
        map_name=bid_map.name,
        n_trials=n_trials,
        pool_size_after_flatten=pool_size_after_flatten,
        mean=float(results.mean()),
        std=float(results.std()),
        min=int(results.min()),
        q05=int(np.percentile(results, 5)),
        q50=int(np.percentile(results, 50)),
        q95=int(np.percentile(results, 95)),
        max=int(results.max()),
    )


__all__ = (
    "FlattenedPool",
    "SimulationResult",
    "flatten_pool",
    "is_physical_loot_item",
    "simulate_map",
)
