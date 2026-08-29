"""Robust session-value estimators that trim the 'small-and-rare' long tail.

Background
----------
Raw expected session value (``simulate_map.mean``) is dominated, on
mansion / shipwreck pools, by a handful of ultra-rare red items whose
expected contribution is meaningful in aggregate but whose *realised*
contribution in any single session is almost always zero. The shapes
of those items also matter:

* **Small-and-rare** (area ≤ 3, value ≥ 1M): 金陵折扇 (1×2),
  非洲之心 (1×1), 黑王子红宝石 (1×1), 羊脂玉 (1×2), 黑盒 (2×2 area=4
  → not small), 超级跑车钥匙 (1×1), 百年人参 (1×2).  Their 1×1 / 1×2 /
  2×1 / 1×3 / 3×1 footprints live in pools with hundreds of cheap
  whites/greens, so even shape-seeing heroes (Aisha, Ethan) cannot tell
  them apart from clutter. A player who sees one of these footprints
  systematically *under*-bids on it because the prior says "probably
  cheap". So treating the item's full E[value] as decision-relevant is
  wrong.

* **Large-and-rare** (area > 3, value ≥ 1M): 蓝鳍金枪鱼 (3×5),
  复苏呼吸机 (3×3), 相控阵雷达 (3×4), 翡翠屏风 (4×4), GPU 计算柜
  (3×5), … These have *distinctive* footprints (see
  ``scripts/probe_distinctive_shapes.py``) that any shape-seeing
  capability identifies with near-certainty. They are NOT trimmed.

This module's :func:`robust_session_value` zeros out the contribution
of small-and-rare items, leaving everything else as the raw model.
The result is a "decisional" expected value: what you can realistically
plan around given shape-only information.

This is a deliberate, conservative one-shot heuristic. More elaborate
versions (shape-conditioned scoring, posterior-after-tool-readings,
etc.) live in the Phase-1A inference engine and combine with this
module rather than replace it.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.basic_mc import FlattenedPool

DEFAULT_VALUE_FLOOR = 1_000_000
"""Items with value below this are never trimmed."""

DEFAULT_CONFUSABLE_MAX_AREA = 3
"""Max shape area (W×H) that is considered indistinguishable from cheap
clutter by shape alone. 1×1 / 1×2 / 2×1 / 1×3 / 3×1 all fall under this."""


def is_confusable_long_tail(
    item: Item,
    *,
    value_floor: int = DEFAULT_VALUE_FLOOR,
    confusable_max_area: int = DEFAULT_CONFUSABLE_MAX_AREA,
) -> bool:
    """Whether *item* should be trimmed by :func:`robust_session_value`.

    Returns ``True`` iff the item's value meets ``value_floor`` AND its
    footprint area is at most ``confusable_max_area``. Items with
    ``shape_w == 0`` or ``shape_h == 0`` (non-physical items: achievements,
    skins, currency) are never trimmed because they don't drop in
    auctions to begin with.
    """
    if item.shape_w == 0 or item.shape_h == 0:
        return False
    area = item.shape_w * item.shape_h
    return item.value >= value_floor and area <= confusable_max_area


def robust_session_value(
    fp: FlattenedPool,
    items: Mapping[int, Item],
    items_per_session: float,
    *,
    value_floor: int = DEFAULT_VALUE_FLOOR,
    confusable_max_area: int = DEFAULT_CONFUSABLE_MAX_AREA,
) -> float:
    """Approximate ``E[session value]`` with the small-rare long tail zeroed.

    This is the closed-form expectation of "sample items_per_session items
    with replacement from ``fp``, multiply each by value, sum" — except
    that items flagged by :func:`is_confusable_long_tail` contribute 0.

    The result matches the raw closed-form expectation
    (``items_per_session × Σ p·v``) when no items are trimmed.
    """
    total = 0.0
    for iid, p in zip(fp.item_ids, fp.probabilities):
        item = items[iid]
        if is_confusable_long_tail(
            item,
            value_floor=value_floor,
            confusable_max_area=confusable_max_area,
        ):
            continue
        total += p * item.value
    return items_per_session * total


def winsorize(samples: np.ndarray, *, upper_quantile: float = 0.99) -> np.ndarray:
    """Cap ``samples`` at the given upper quantile (in place safe copy).

    Useful when summarising an empirical MC distribution that has fat
    upper tails. Defaults to 99% (cap top 1% to the 99th percentile).
    """
    if samples.size == 0:
        return samples
    cap = np.quantile(samples, upper_quantile)
    out = samples.copy()
    out[out > cap] = cap
    return out


__all__ = (
    "DEFAULT_VALUE_FLOOR",
    "DEFAULT_CONFUSABLE_MAX_AREA",
    "is_confusable_long_tail",
    "robust_session_value",
    "winsorize",
)
