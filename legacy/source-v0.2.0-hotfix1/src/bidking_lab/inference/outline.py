"""Outline observations: the bridge between hero "see-shape" skills and bucket constraints.

An OutlineObs is one cabinet outline the player has spotted via Aisha
or Ethan. It carries enough metadata for the inference engine to either
*pin* it to a quality bucket (when ``quality_hint`` is known) or *try*
each compatible item (when only shape is known).

Per the 2026-05-15 design decision, hero-specific skill modeling lives
here (not in the disputed ``hero_skills.py``); this module follows the
user's stated game behaviour:

* **Aisha (103)**: 4 rounds, low-to-high quality. R1=白 (1), R2=绿 (2),
  R3=蓝 (3), R4=紫 (4). Each round shows *all* items of that quality.
  The player accumulates knowledge across rounds, so by R4 Aisha knows
  outlines for every white / green / blue / purple item in the cabinet.
* **Ethan (208)**: R1 reveals all items in 5 random *categories* (not 5
  random items). R5 reveals all items. Middle rounds reveal only items
  whose quality has already been disclosed by some other source.

Both heroes are "outline-only" — quality is *not* attached to the
outline directly (except indirectly via Aisha's round → quality mapping
and Ethan's category filter).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping

from bidking_lab.extract.item_table import Item
from bidking_lab.inference.observation import (
    HeroMode,
    QualityBucketObs,
)


# --- Aisha round → quality mapping (user-confirmed, 2026-05-15) ---

AISHA_ROUND_QUALITY: dict[int, int] = {
    1: 1,   # 白
    2: 2,   # 绿
    3: 3,   # 蓝
    4: 4,   # 紫
}


def aisha_outline_quality(round_no: int) -> int | None:
    """Quality revealed by Aisha at ``round_no`` (None if she doesn't reveal that round)."""
    return AISHA_ROUND_QUALITY.get(round_no)


@dataclass(frozen=True)
class OutlineObs:
    """One outline the player has seen on the cabinet UI.

    Parameters
    ----------
    shape
        ``(width, height)`` from the cabinet grid (Item.txt col[7]).
    round_seen
        Round (1-based) in which this outline first appeared.
    quality_hint
        Quality bucket(s) the outline is known to belong to. For Aisha
        outlines this is set by ``aisha_outline_quality``; for Ethan
        outlines this is normally ``None`` (only shape, no quality).
    hero
        Which hero produced this outline (informational; the engine
        treats both heroes the same once ``quality_hint`` is set).
    """

    shape: tuple[int, int]
    round_seen: int
    quality_hint: tuple[int, ...] | None = None
    hero: HeroMode | None = None

    def area(self) -> int:
        return self.shape[0] * self.shape[1]


def make_aisha_outlines(
    *,
    round_seen: int,
    shapes: Iterable[tuple[int, int]],
) -> list[OutlineObs]:
    """Build the Aisha outline list at a given round.

    Each shape gets ``quality_hint=(q,)`` where ``q`` is the round's
    quality per :data:`AISHA_ROUND_QUALITY`. If the round is outside
    1..4 we still emit outlines but with ``quality_hint=None`` (no info).
    """
    q = aisha_outline_quality(round_seen)
    hint = (q,) if q is not None else None
    return [
        OutlineObs(shape=tuple(s), round_seen=round_seen, quality_hint=hint, hero="aisha")
        for s in shapes
    ]


def make_ethan_outlines(
    *,
    round_seen: int,
    shapes: Iterable[tuple[int, int]],
) -> list[OutlineObs]:
    """Build the Ethan outline list at a given round. Quality is unknown."""
    return [
        OutlineObs(shape=tuple(s), round_seen=round_seen, quality_hint=None, hero="ethan")
        for s in shapes
    ]


# --- Shape × quality reverse index over the drop pool ---


def build_shape_index(
    items: Mapping[int, Item],
    *,
    droppable_ids: Iterable[int] | None = None,
) -> dict[tuple[int, int, int], list[Item]]:
    """``{(quality, w, h): [Item, ...]}`` for fast outline → candidate lookup.

    Parameters
    ----------
    items
        ``{item_id: Item}`` mapping (typically from ``load_items``).
    droppable_ids
        Optional whitelist; when provided, restricts the index to items
        in the active drop pool (saves work when iterating over outlines
        that can't have come from disabled items).
    """
    out: dict[tuple[int, int, int], list[Item]] = defaultdict(list)
    eligible = set(droppable_ids) if droppable_ids is not None else None
    for item_id, item in items.items():
        if eligible is not None and item_id not in eligible:
            continue
        key = (item.quality, item.shape_w, item.shape_h)
        out[key].append(item)
    return dict(out)


def candidates_for_outline(
    outline: OutlineObs,
    shape_index: Mapping[tuple[int, int, int], list[Item]],
    *,
    quality_filter: int | None = None,
) -> list[Item]:
    """Items that could be behind ``outline`` per shape + quality constraints."""
    w, h = outline.shape
    qs: Iterable[int]
    if quality_filter is not None:
        qs = (quality_filter,)
    elif outline.quality_hint is not None:
        qs = outline.quality_hint
    else:
        qs = (1, 2, 3, 4, 5, 6)
    out: list[Item] = []
    for q in qs:
        out.extend(shape_index.get((q, w, h), []))
    return out


# --- Outline → bucket constraint derivation ---


def derive_bucket_from_outlines(
    quality: int,
    outlines: Iterable[OutlineObs],
    shape_index: Mapping[tuple[int, int, int], list[Item]],
) -> QualityBucketObs | None:
    """Build a tight ``QualityBucketObs`` from Aisha-style complete-bucket outlines.

    Use this when the player has *all* outlines for ``quality`` (Aisha's
    pattern: she reveals every item of a quality, so the count is exact).
    For Ethan-style partial sampling, do not call this.

    Returns
    -------
    QualityBucketObs | None
        ``None`` if no outlines match the quality.

    Notes
    -----
    Derived fields:

    * ``count`` = number of outlines for this quality (exact).
    * ``total_cells`` = sum of outline areas (exact).
    * ``value_range`` = ``(Σ min, Σ max)`` over each outline's matching
      candidate items (looked up via ``shape_index``). If a shape has no
      matching item at this quality, that outline contributes ``(0, 0)``
      and a warning could be raised — the engine treats this as a
      best-effort lower bound.
    """
    relevant = [
        o for o in outlines
        if o.quality_hint is not None and quality in o.quality_hint
    ]
    if not relevant:
        return None

    count = len(relevant)
    total_cells = sum(o.area() for o in relevant)

    value_lo = 0
    value_hi = 0
    for o in relevant:
        cands = candidates_for_outline(o, shape_index, quality_filter=quality)
        if cands:
            value_lo += min(it.value for it in cands)
            value_hi += max(it.value for it in cands)

    return QualityBucketObs(
        quality=quality,
        count=count,
        total_cells=total_cells,
        value_range=(value_lo, value_hi) if value_hi > 0 else None,
    )


__all__ = (
    "AISHA_ROUND_QUALITY",
    "aisha_outline_quality",
    "OutlineObs",
    "make_aisha_outlines",
    "make_ethan_outlines",
    "build_shape_index",
    "candidates_for_outline",
    "derive_bucket_from_outlines",
)
