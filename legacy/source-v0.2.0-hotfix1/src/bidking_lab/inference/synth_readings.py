"""Synthetic tool readings: from ground truth to what the engine sees.

For Phase 2 tool-ROI evaluation we need to know what a tool *would*
reveal if the player ran it against a known ground-truth session.
This module owns the per-tool dispatch table.

Each modelled tool maps to a :class:`ToolEffect` containing:

* the silver price (used as the ROI denominator),
* a ``bucket_patches`` dict ``{quality: {field: value}}`` of what to
  write into the corresponding :class:`QualityBucketObs`, and
* an optional ``session_patch`` for session-level fields (currently
  only ``warehouse_total_cells``).

The hero-skill outline reveal (Aisha R1\u2013R3) is **not** modelled here;
it is a separate free information source. See
:func:`apply_aisha_outline_pins` below for the optional helper that
folds her outline into a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from bidking_lab.inference.display import format_value, parse_reading
from bidking_lab.inference.ground_truth import SessionTruth
from bidking_lab.inference.observation import (
    HeroMode,
    QualityBucketObs,
    SessionObs,
    tool_price,
)


@dataclass(frozen=True)
class ToolSpec:
    """Static metadata for one battle-item tool."""

    name: str
    rarity: str          # "white" | "green" | "blue" | "purple" | "gold"
    target_qualities: tuple[int, ...]  # which quality buckets the tool writes
    field_name: str      # which QualityBucketObs field the tool fills


# Tool naming follows BattleItem.txt (verified 2026-05-15):
#   普品 → white+green (q=1+2)
#   良品 → blue        (q=3)
#   优品 → purple      (q=4)
#   极品 → gold        (q=5)
#   珍品 → red         (q=6)
# Tool's `rarity` field is the silver-cost tier (NOT the target quality):
# e.g. 优品估价 reads purple items but is a "blue"-tier 20K-silver tool.
TOOL_SPECS: dict[str, ToolSpec] = {
    "\u666e\u54c1\u626b\u63cf":   ToolSpec("\u666e\u54c1\u626b\u63cf",   "white",  (1, 2), "total_cells"),  # 普品扫描   white+green combined
    "\u826f\u54c1\u626b\u63cf":   ToolSpec("\u826f\u54c1\u626b\u63cf",   "green",  (3,),   "total_cells"),  # 良品扫描   blue
    "\u4f18\u54c1\u626b\u63cf":   ToolSpec("\u4f18\u54c1\u626b\u63cf",   "blue",   (4,),   "total_cells"),  # 优品扫描   purple
    "\u4f18\u54c1\u4f30\u4ef7":   ToolSpec("\u4f18\u54c1\u4f30\u4ef7",   "blue",   (4,),   "value_sum"),    # 优品估价   purple
    "\u4f18\u54c1\u5747\u683c":   ToolSpec("\u4f18\u54c1\u5747\u683c",   "blue",   (4,),   "avg_cells"),    # 优品均格   purple
    "\u6781\u54c1\u626b\u63cf":   ToolSpec("\u6781\u54c1\u626b\u63cf",   "purple", (5,),   "total_cells"),  # 极品扫描   gold
    "\u6781\u54c1\u4f30\u4ef7":   ToolSpec("\u6781\u54c1\u4f30\u4ef7",   "purple", (5,),   "value_sum"),    # 极品估价   gold
    "\u6781\u54c1\u5747\u683c":   ToolSpec("\u6781\u54c1\u5747\u683c",   "purple", (5,),   "avg_cells"),    # 极品均格   gold
    "\u73cd\u54c1\u626b\u63cf":   ToolSpec("\u73cd\u54c1\u626b\u63cf",   "gold",   (6,),   "total_cells"),  # 珍品扫描   red
    "\u73cd\u54c1\u4f30\u4ef7":   ToolSpec("\u73cd\u54c1\u4f30\u4ef7",   "gold",   (6,),   "value_sum"),    # 珍品估价   red
    "\u73cd\u54c1\u5747\u683c":   ToolSpec("\u73cd\u54c1\u5747\u683c",   "gold",   (6,),   "avg_cells"),    # 珍品均格   red
}


# Session-level "tools" that write into SessionObs directly.
SESSION_TOOL_SPECS: dict[str, ToolSpec] = {
    "\u603b\u4ed3\u50a8\u7a7a\u95f4": ToolSpec(
        name="\u603b\u4ed3\u50a8\u7a7a\u95f4",
        rarity="gold",        # nominal; actual price is in TOOL_PRICE_OVERRIDES
        target_qualities=(),
        field_name="warehouse_total_cells",
    ),
}


@dataclass
class ToolEffect:
    """The reading one tool produces against one ground-truth session."""

    tool_name: str
    rarity: str
    silver_cost: int
    bucket_patches: dict[int, dict[str, object]] = field(default_factory=dict)
    session_patch: dict[str, object] = field(default_factory=dict)


def apply_tool(truth: SessionTruth, tool_name: str) -> ToolEffect:
    """Compute the reading ``tool_name`` would produce against ``truth``.

    Raises
    ------
    KeyError
        If ``tool_name`` is not registered in either ``TOOL_SPECS`` or
        ``SESSION_TOOL_SPECS``.
    """
    if tool_name in SESSION_TOOL_SPECS:
        spec = SESSION_TOOL_SPECS[tool_name]
        eff = ToolEffect(
            tool_name=spec.name,
            rarity=spec.rarity,
            silver_cost=tool_price(spec.name, spec.rarity),
        )
        if spec.field_name == "warehouse_total_cells":
            eff.session_patch["warehouse_total_cells"] = truth.warehouse_total_cells
        return eff

    spec = TOOL_SPECS[tool_name]
    eff = ToolEffect(
        tool_name=spec.name,
        rarity=spec.rarity,
        silver_cost=tool_price(spec.name, spec.rarity),
    )

    # Aggregate the targeted quality buckets (white+green tools combine).
    agg_count = sum(
        truth.buckets[q].count for q in spec.target_qualities if q in truth.buckets
    )
    agg_cells = sum(
        truth.buckets[q].total_cells for q in spec.target_qualities if q in truth.buckets
    )
    agg_value = sum(
        truth.buckets[q].value_sum for q in spec.target_qualities if q in truth.buckets
    )

    # The combined "low" tool (\u666e\u54c1\u626b\u63cf) writes the
    # aggregated number into the first listed quality (q=1) so it lines
    # up with how the existing inference engine demos consume it.
    write_q = spec.target_qualities[0]

    if spec.field_name == "total_cells":
        eff.bucket_patches[write_q] = {"total_cells": int(agg_cells)}
    elif spec.field_name == "value_sum":
        eff.bucket_patches[write_q] = {"value_sum": int(agg_value)}
    elif spec.field_name == "avg_cells":
        if agg_count == 0:
            # Tool can't be applied to an empty bucket; no patch.
            return eff
        reading_str = format_value(int(agg_cells), int(agg_count))
        eff.bucket_patches[write_q] = {"avg_cells": parse_reading(reading_str)}
    else:
        raise ValueError(f"unknown field_name {spec.field_name!r} in TOOL_SPECS")
    return eff


def build_session_obs(
    truth: SessionTruth,
    *,
    hero: HeroMode,
    tools: Iterable[str],
    include_aisha_outline: bool = False,
    huge_band_inputs: dict[int, str] | None = None,
) -> tuple[SessionObs, int]:
    """Build a :class:`SessionObs` from ground truth by applying ``tools``.

    Returns
    -------
    (session, total_silver)
        ``session`` is the SessionObs the inference engine should
        consume; ``total_silver`` is the summed silver cost of the
        applied tools (the ROI denominator).

    Parameters
    ----------
    truth:
        Ground-truth session to read from.
    hero:
        ``"aisha"`` or ``"ethan"``. Currently affects only the optional
        outline pinning.
    tools:
        Iterable of tool names registered in either ``TOOL_SPECS`` or
        ``SESSION_TOOL_SPECS``.
    include_aisha_outline:
        If True and ``hero == "aisha"``, additionally pin q=1..4 buckets
        with the exact ground-truth count/total_cells/value_range (free
        info from Aisha's R1\u2013R3 reveals).
    huge_band_inputs:
        Optional override of the player-reported huge band per quality;
        if not provided, derived from the ground-truth huge_count.
    """
    bucket_patches: dict[int, dict[str, object]] = {}
    session_patch: dict[str, object] = {}
    total_silver = 0

    for name in tools:
        eff = apply_tool(truth, name)
        total_silver += eff.silver_cost
        for q, patch in eff.bucket_patches.items():
            bucket_patches.setdefault(q, {}).update(patch)
        session_patch.update(eff.session_patch)

    if include_aisha_outline and hero == "aisha":
        for q in (1, 2, 3, 4):
            if q not in truth.buckets:
                continue
            b = truth.buckets[q]
            patch = bucket_patches.setdefault(q, {})
            patch.setdefault("count", b.count)
            patch.setdefault("total_cells", b.total_cells)

    # Huge bands: derived from ground truth so the engine consumes the
    # same info the player would have eyeballed off the cabinet.
    huge_band_inputs = huge_band_inputs or {}

    def _band_from_count(c: int) -> str:
        if c == 0:
            return "none"
        if c == 1:
            return "1"
        if c <= 3:
            return "2-3"
        return "4+"

    # Per the hero-visibility rule: Ethan sees all huge bands, Aisha
    # only the purple one.
    visible_huge_qs = (4, 5, 6) if hero == "ethan" else (4,)

    buckets: dict[int, QualityBucketObs] = {}
    for q in sorted(set(bucket_patches.keys()) | set(visible_huge_qs)):
        patch = bucket_patches.get(q, {})
        huge_band = huge_band_inputs.get(q)
        if huge_band is None and q in visible_huge_qs and q in truth.buckets:
            huge_band = _band_from_count(truth.buckets[q].huge_count)
        if huge_band is None:
            huge_band = "none"
        buckets[q] = QualityBucketObs(quality=q, huge_band=huge_band, **patch)

    obs = SessionObs(
        map_id=truth.map_id,
        hero=hero,
        warehouse_total_cells=session_patch.get("warehouse_total_cells"),  # type: ignore[arg-type]
        buckets=buckets,
    )
    return obs, total_silver


__all__ = (
    "ToolSpec",
    "ToolEffect",
    "TOOL_SPECS",
    "SESSION_TOOL_SPECS",
    "apply_tool",
    "build_session_obs",
)
