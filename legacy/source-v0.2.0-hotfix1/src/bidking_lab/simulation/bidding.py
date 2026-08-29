"""Budget-aware session simulator.

Extends the basic MC (which reports "gross value if you got everything
for free") with a budget constraint:

- Player has ``starting_budget_silver`` to bid with.
- Each round, an item appears. The player decides to bid or skip.
- If the player bids and wins, they pay the bid price and gain the item
  value. If they lose, they keep their money.
- Session ends after ``rounds_total`` rounds (or earlier if all items
  presented).
- Net profit = items_won_value - total_bids_paid - entry_fee.

NPC bidding behavior
--------------------
We don't have NPC data tables. Instead we model the **reserve price**
(the price you must beat) as a fraction of the item's true value:

    reserve_ratio ~ Uniform[reserve_lo, reserve_hi]
    reserve_price = item_value × reserve_ratio

If the player's bid > reserve_price, they win.

The player's own bid strategy (``BidPolicy``) controls how they decide:

- **value_ratio policy** (default): bid ``bid_factor × item_value``
  for every item that's affordable. Simple but effective baseline.
- Future: greedy knapsack, threshold-based, info-aware (hero skills).

This lets us compare maps / tiers realistically:

- A map with budget=200万 vs 100万 (open vs sealed 别墅) → the richer
  player can win more items → higher net profit, all else equal.
- A map with higher entry fee → the break-even point shifts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from pydantic import BaseModel, Field

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.basic_mc import FlattenedPool, flatten_pool


@dataclass(frozen=True)
class BidPolicy:
    """How the player bids.

    ``bid_factor``: bid this fraction of the item's true value.
        0.3 = "pay 30% of value" → high ROI per item, but may lose to NPCs.
        0.6 = "pay 60% of value" → wins more often, lower margin per item.

    ``reserve_lo / reserve_hi``: NPC reserve price as a fraction of value.
        The NPC "accepts" if player_bid ≥ reserve_price.
        reserve_price ~ Uniform[reserve_lo, reserve_hi] per item.
    """

    bid_factor: float = 0.35
    reserve_lo: float = 0.15
    reserve_hi: float = 0.50


class SessionResult(BaseModel):
    """Single-trial session outcome (for aggregation)."""

    items_won_value: int = 0
    items_won_count: int = 0
    total_bids_paid: int = 0
    entry_fee: int = 0
    net_profit: int = 0
    budget_remaining: int = 0


class SessionSummary(BaseModel):
    """Aggregated statistics over N trials of a budgeted session."""

    map_id: int
    map_name: str
    auction_mode: str
    n_trials: int
    entry_fee: int
    starting_budget: int
    pool_size: int

    gross_mean: float = Field(description="Mean total value of ALL items in session (no budget limit)")
    net_mean: float = Field(description="Mean net profit (won value - bids paid - entry fee)")
    net_std: float = 0.0
    net_q05: int = 0
    net_q50: int = 0
    net_q95: int = 0
    net_min: int = 0
    net_max: int = 0

    items_won_mean: float = 0.0
    items_total_mean: float = 0.0
    win_rate_mean: float = Field(0.0, description="Fraction of items won out of items presented")
    budget_util_mean: float = Field(0.0, description="Fraction of budget spent")
    roi_mean: float = Field(0.0, description="net_profit / (entry_fee + bids_paid)")


def _build_flat_arrays(
    bid_map: BidMap,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> tuple[list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], np.ndarray]:
    """Build flattened pool arrays, handling anthology routing."""
    def to_arrays(fp: FlattenedPool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.array(fp.probabilities, dtype=np.float64),
            np.array(fp.n_min, dtype=np.int64),
            np.array(fp.n_max, dtype=np.int64),
            np.array(fp.values, dtype=np.int64),
        )

    if not bid_map.sub_pool_weights:
        fp = flatten_pool(bid_map.drop_pool_id, drops, items)
        return [to_arrays(fp)], np.array([1.0])

    arrays_list = []
    weight_list = []
    for sub_map_id, w in bid_map.sub_pool_weights:
        sub_map = maps.get(sub_map_id)
        if sub_map is None:
            continue
        fp = flatten_pool(sub_map.drop_pool_id, drops, items)
        if not fp.item_ids:
            continue
        arrays_list.append(to_arrays(fp))
        weight_list.append(w)
    if not arrays_list:
        empty = np.empty(0, dtype=np.float64)
        arrays_list = [(empty, empty.astype(np.int64), empty.astype(np.int64), empty.astype(np.int64))]
        weight_list = [1.0]
    sp = np.array(weight_list, dtype=np.float64)
    return arrays_list, sp / sp.sum()


def simulate_session(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    policy: BidPolicy | None = None,
    n_trials: int = 10_000,
    rng: np.random.Generator | None = None,
) -> SessionSummary:
    """Run budgeted auction sessions and return aggregated summary."""
    rng = rng or np.random.default_rng()
    policy = policy or BidPolicy()
    bid_map = maps[map_id]

    k_lo = bid_map.items_per_session_min
    k_hi = bid_map.items_per_session_max
    budget_start = bid_map.starting_budget_silver
    entry_fee = bid_map.entry_fee_silver

    sub_arrays, sub_p = _build_flat_arrays(bid_map, maps, drops, items)
    pool_size = max(len(a[0]) for a in sub_arrays)

    gross_totals = np.zeros(n_trials, dtype=np.float64)
    net_profits = np.zeros(n_trials, dtype=np.float64)
    items_won_counts = np.zeros(n_trials, dtype=np.float64)
    items_total_counts = np.zeros(n_trials, dtype=np.float64)
    bids_paid_totals = np.zeros(n_trials, dtype=np.float64)

    for t in range(n_trials):
        sub_idx = int(rng.choice(len(sub_arrays), p=sub_p)) if len(sub_arrays) > 1 else 0
        probs, n_mins, n_maxs, values = sub_arrays[sub_idx]
        n_pool = len(probs)
        if n_pool == 0:
            net_profits[t] = -entry_fee
            continue

        k = int(rng.integers(k_lo, k_hi + 1))
        idx = rng.choice(n_pool, size=k, replace=True, p=probs)
        counts = rng.integers(n_mins[idx], n_maxs[idx] + 1)
        item_values = values[idx] * counts

        gross_totals[t] = float(item_values.sum())
        items_total_counts[t] = k

        budget_left = budget_start
        won_value = 0
        won_count = 0
        paid_total = 0

        reserve_prices = rng.uniform(policy.reserve_lo, policy.reserve_hi, size=k) * item_values

        for i in range(k):
            v = int(item_values[i])
            my_bid = int(v * policy.bid_factor)
            if my_bid <= 0 or my_bid > budget_left:
                continue
            if my_bid >= reserve_prices[i]:
                won_value += v
                won_count += 1
                paid_total += my_bid
                budget_left -= my_bid

        net_profits[t] = won_value - paid_total - entry_fee
        items_won_counts[t] = won_count
        bids_paid_totals[t] = paid_total

    total_spent = bids_paid_totals + entry_fee
    with np.errstate(divide="ignore", invalid="ignore"):
        roi = np.where(total_spent > 0, net_profits / total_spent, 0.0)
    if budget_start > 0:
        budget_util = bids_paid_totals / budget_start
    else:
        budget_util = np.zeros(n_trials, dtype=np.float64)
    win_rate = np.where(
        items_total_counts > 0,
        items_won_counts / items_total_counts,
        0.0,
    )

    return SessionSummary(
        map_id=map_id,
        map_name=bid_map.name,
        auction_mode=bid_map.auction_mode,
        n_trials=n_trials,
        entry_fee=entry_fee,
        starting_budget=budget_start,
        pool_size=pool_size,
        gross_mean=float(gross_totals.mean()),
        net_mean=float(net_profits.mean()),
        net_std=float(net_profits.std()),
        net_q05=int(np.percentile(net_profits, 5)),
        net_q50=int(np.percentile(net_profits, 50)),
        net_q95=int(np.percentile(net_profits, 95)),
        net_min=int(net_profits.min()),
        net_max=int(net_profits.max()),
        items_won_mean=float(items_won_counts.mean()),
        items_total_mean=float(items_total_counts.mean()),
        win_rate_mean=float(win_rate.mean()),
        budget_util_mean=float(budget_util.mean()),
        roi_mean=float(roi.mean()),
    )


__all__ = ("BidPolicy", "SessionResult", "SessionSummary", "simulate_session")
