"""Bidding-hint recommendations for Ethan (R2) and Aisha (R3) sessions.

Two symmetric hints share the conditional-MC infrastructure:

* :func:`compute_snipe_recommendation` — *upside* hint for **big**
  warehouses (\u2265 120 cells) when the low-tier scan is in. Surfaces
  an aggressive bid ceiling above the typical opponent bid region.
* :func:`compute_pass_recommendation` — *downside* hint for **small,
  junk-heavy** warehouses (\u2264 80 cells with \u2265 40% low-tier
  cells). Surfaces a "let it go above X" anchor when the conditional
  expected value is materially below the map's overall median.

Both gates branch on hero:

* **Ethan @ R2** — has spent silver on 普品扫描 + 良品扫描 to reveal
  ``q=1`` (white+green combined) and ``q=3`` (blue) cell totals. The R2
  decision happens before R3 opponents pad their bids.
* **Aisha @ R3** — her R1\u2013R3 outline reveal automatically exposes
  q=1, q=2, q=3 shapes; the player manually counts cells from the
  outlines. *No scan silver spent.* The R3 timing is later than Ethan's
  R2 window, but the information was free.

Hard gating (returns ``None`` if any fail):

* ``session.hero`` in ``{"ethan", "aisha"}``
* ``warehouse_total_cells >= 120`` — only big warehouses justify the snipe
  premium (small warehouses have too little upside to overpay)
* **Ethan**: ``q=1`` and ``q=3`` total_cells both known
* **Aisha**: ``q=1`` *and* ``q=2`` *and* ``q=3`` total_cells all known

Model:

1. Sample ``n_trials`` ground-truth sessions for the map.
2. Filter to sessions whose warehouse_total_cells matches the player's
   observation within ``warehouse_tolerance`` cells (default ±8).
3. From the filtered distribution, compute the 50th/75th/90th percentile
   of *total* session value.
4. Recommended bid range:

   * ``safe_floor`` = 50th percentile × ``safe_floor_ratio`` (default 0.70)
   * ``expected``   = 50th percentile (median session value, conditional)
   * ``snipe_max``  = 75th percentile × ``snipe_premium`` (default 1.15)

The "high-risk premium" is baked into the snipe_max being a quantile
*plus* a multiplier — opponents at R2 typically bid near the median, so
exceeding the 75th-percentile point gives a real edge while staying
below ``snipe_max`` keeps the worst-case loss bounded.

The returned ``rationale`` string is human-readable and can be dropped
verbatim into a Streamlit UI tooltip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import SessionTruth, sample_session_truth
from bidking_lab.inference.observation import SessionObs


@dataclass(frozen=True)
class SnipeRecommendation:
    """Snipe-bid suggestion bundle (Ethan R2 or Aisha R3)."""

    map_id: int
    map_name: str
    hero: str                         # "ethan" | "aisha"
    round_window: str                 # "R2" for Ethan, "R3" for Aisha
    warehouse_total_cells: int
    low_tier_cells_observed: int      # sum of all observed low-tier (q\u22643) cells
    purple_conditioned: bool          # True when MC also filtered on purple cells
    low_confidence: bool              # True when only the relaxed-threshold fallback fired

    n_matching_samples: int           # how many MC sessions matched the warehouse filter
    expected_value: int               # median total session value (conditional MC)
    p25_value: int                    # 25th percentile (downside reference)
    p75_value: int                    # 75th percentile (snipe upside)
    p90_value: int                    # 90th percentile (extreme upside, not used in recommendation)

    safe_floor_bid: int               # below this is a no-brainer keep bid
    snipe_max_bid: int                # above this is overpaying for the upside
    rationale: str                    # human-readable explanation

    def as_ui_tooltip(self) -> str:
        """One-line summary suitable for a UI badge."""
        return (
            f"\u9ad8\u98ce\u9669\u64cd\u4f5c ({self.round_window}): "
            f"\u53ef\u79d2\u4ed3, "
            f"\u63a8\u8350\u4ef7\u683c {self.snipe_max_bid:,} \u4ee5\u5185 "
            f"(\u9884\u671f\u4ed3\u4ef7 \u4e2d\u4f4d\u6570 {self.expected_value:,})"
        )


def compute_snipe_recommendation(
    session: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 2000,
    warehouse_tolerance: int = 8,
    purple_tolerance: int = 4,
    min_matching_samples: int = 30,
    min_matching_samples_relaxed: int = 10,
    safe_floor_ratio: float = 0.70,
    snipe_premium: float = 1.15,
    min_warehouse_cells: int = 120,
    rng: np.random.Generator | None = None,
    truths: list[SessionTruth] | None = None,
) -> SnipeRecommendation | None:
    """Conditional Monte Carlo over the map's drop pool, gated on R2-Ethan
    information state. See module docstring for the model.

    Returns ``None`` only if the hard preconditions fail or the relaxed
    fallback also fails. When the strict ``min_matching_samples``
    threshold is missed but at least ``min_matching_samples_relaxed``
    samples are available, returns a recommendation with
    ``low_confidence=True`` and a warning baked into ``rationale`` so
    the UI can surface it with caveats instead of silently dropping it.
    """
    if session.hero == "ethan":
        round_window = "R2"
        required_qs: tuple[int, ...] = (1, 3)        # 普品扫描 (q=1 wg combined) + 良品扫描 (q=3)
    elif session.hero == "aisha":
        round_window = "R3"
        required_qs = (1, 2, 3)                      # R1\u2013R3 outline-revealed white/green/blue
    else:
        return None
    wh = session.warehouse_total_cells
    if wh is None or wh < min_warehouse_cells:
        return None

    observed_cells: list[tuple[int, int]] = []
    for q in required_qs:
        bucket = session.buckets.get(q)
        if bucket is None or bucket.total_cells is None:
            return None
        observed_cells.append((q, bucket.total_cells))
    low_cells = sum(c for _, c in observed_cells)
    if session.map_id not in maps:
        return None

    purple_obs = session.buckets.get(4)
    purple_cells_obs = purple_obs.total_cells if purple_obs is not None else None

    # Red constraints — same rationale as in compute_pass_recommendation.
    red_obs = session.buckets.get(6)
    red_huge_active = red_obs is not None and red_obs.huge_band != "none"
    red_value_active = red_obs is not None and red_obs.value_range is not None
    red_active = red_huge_active or red_value_active

    rng = rng or np.random.default_rng()
    if truths is None:
        truths = [
            sample_session_truth(
                session.map_id, maps=maps, drops=drops, items=items, rng=rng,
            )
            for _ in range(n_trials)
        ]
    values_warehouse: list[int] = []
    values_purple: list[int] = []      # subset that also matches purple cells
    values_red: list[int] = []         # subset that also matches red constraints
    huge_lo, huge_hi = red_obs.huge_count_range() if red_huge_active else (0, 99)
    val_lo, val_hi = red_obs.value_range if red_value_active else (0, 10**12)
    for truth in truths:
        if abs(truth.warehouse_total_cells - wh) > warehouse_tolerance:
            continue
        v = truth.total_value()
        values_warehouse.append(v)
        purple_ok = True
        if purple_cells_obs is not None:
            tp = truth.buckets.get(4)
            tp_cells = tp.total_cells if tp is not None else 0
            if abs(tp_cells - purple_cells_obs) <= purple_tolerance:
                values_purple.append(v)
            else:
                purple_ok = False
        if red_active and purple_ok:
            tr = truth.buckets.get(6)
            tr_huge = tr.huge_count if tr is not None else 0
            tr_value = tr.value_sum if tr is not None else 0
            if huge_lo <= tr_huge <= huge_hi and val_lo <= tr_value <= val_hi:
                values_red.append(v)

    # Pick the tighter conditional set if it has enough samples; else fall back
    # through tiers of confidence:
    #   1. red + purple + warehouse, ≥ min_matching_samples           (tightest)
    #   2. red + purple + warehouse, ≥ min_matching_samples_relaxed   (low conf)
    #   3. purple + warehouse,       ≥ min_matching_samples           (normal)
    #   4. warehouse only,           ≥ min_matching_samples
    #   5. warehouse only,           ≥ min_matching_samples_relaxed   (low conf)
    low_confidence = False
    if red_active and len(values_red) >= min_matching_samples:
        values = values_red
        purple_conditioned = True
    elif red_active and len(values_red) >= min_matching_samples_relaxed:
        values = values_red
        purple_conditioned = True
        low_confidence = True
    elif purple_cells_obs is not None and len(values_purple) >= min_matching_samples:
        values = values_purple
        purple_conditioned = True
    elif len(values_warehouse) >= min_matching_samples:
        values = values_warehouse
        purple_conditioned = False
    elif len(values_warehouse) >= min_matching_samples_relaxed:
        values = values_warehouse
        purple_conditioned = False
        low_confidence = True
    else:
        # Truly noisy — bail rather than mislead the UI.
        return None

    arr = np.asarray(values, dtype=np.int64)
    p25 = int(np.percentile(arr, 25))
    p50 = int(np.percentile(arr, 50))
    p75 = int(np.percentile(arr, 75))
    p90 = int(np.percentile(arr, 90))

    safe_floor = int(p50 * safe_floor_ratio)
    snipe_max = int(p75 * snipe_premium)

    if session.hero == "ethan":
        info_breakdown = (
            f"\u767d\u7eff(\u666e\u54c1\u626b\u63cf) {observed_cells[0][1]} + "
            f"\u84dd(\u826f\u54c1\u626b\u63cf) {observed_cells[1][1]}"
        )
        timing_note = (
            "R2 \u662f\u79d2\u4ed3\u9ec4\u91d1\u7a97\u53e3"
            "\uff08\u5bf9\u624b\u8fd8\u672a\u9501\u4ef7\uff09"
        )
    else:   # aisha
        info_breakdown = (
            f"\u767d(\u8f6e\u5ed3 R1) {observed_cells[0][1]} + "
            f"\u7eff(\u8f6e\u5ed3 R2) {observed_cells[1][1]} + "
            f"\u84dd(\u8f6e\u5ed3 R3) {observed_cells[2][1]}"
        )
        timing_note = (
            "R3 \u8f6e\u5ed3\u53e0\u52a0\u540e\u4f4e\u54c1\u4fe1\u606f\u5168\u9f50"
            "\uff080 \u94f6\u5e01\u626b\u63cf\u6210\u672c\uff09\uff0c"
            "\u5bf9\u624b\u53ef\u80fd\u5df2\u5f00\u59cb\u62ac\u4ef7\u4f46\u4f60\u4ee5\u96f6\u6210\u672c\u62ff\u5230\u540c\u7b49\u4fe1\u606f"
        )

    if purple_conditioned:
        conditioning_note = (
            f"MC \u540c\u65f6\u8fc7\u6ee4\u4ed3\u5e93\u5927\u5c0f \u00b1{warehouse_tolerance} "
            f"+ \u7d2b\u54c1\u683c\u6570 {purple_cells_obs} \u00b1{purple_tolerance}"
        )
    else:
        conditioning_note = (
            f"MC \u8fc7\u6ee4\u4ed3\u5e93\u5927\u5c0f \u00b1{warehouse_tolerance} \u683c"
        )
        if purple_cells_obs is not None:
            conditioning_note += "\uff08\u7d2b\u54c1\u683c\u6570\u6837\u672c\u4e0d\u8db3\uff0cfallback\uff09"

    low_conf_warning = (
        f"\u26A0\ufe0f \u6837\u672c\u4ec5 {len(values)} \u4e2a "
        f"(\u4f4e\u4e8e\u63a8\u8350\u9608\u503c {min_matching_samples})\uff0c"
        f"\u4ef7\u683c\u533a\u95f4\u566a\u58f0\u8f83\u5927\u3001\u4ec5\u4f9b\u53c2\u8003\u3002"
        f"\u53ef\u63d0\u9ad8 n_trials \u6216\u653e\u5bbd warehouse_tolerance \u83b7\u5f97\u66f4\u7a33\u5b9a\u7ed3\u679c\u3002\n"
        if low_confidence else ""
    )
    rationale = (
        f"\u4ed3\u5e93 {wh} \u683c\uff0c\u4f4e\u54c1 {low_cells} \u683c "
        f"({info_breakdown})\u3002\n"
        f"{conditioning_note}\uff0c\u547d\u4e2d {len(values)} \u4e2a\u6837\u672c\uff1a"
        f"\u603b\u4ed3\u4ef7 \u4e2d\u4f4d\u6570 = {p50:,}\u3001"
        f"P75 = {p75:,}\u3001P90 = {p90:,} \u94f6\u5e01\u3002\n"
        f"{low_conf_warning}"
        f"{timing_note}\uff0c\u63a8\u8350\u51fa\u4ef7\u533a\u95f4\uff1a"
        f"\u8d77\u7801 {safe_floor:,} \u2192 \u79d2\u4ed3\u9876 {snipe_max:,} \u94f6\u5e01\u3002\n"
        f"\u9876\u4ef7\u91c7\u7528 P75 \u00d7 {snipe_premium:.2f} \u7684\u9ad8\u98ce\u9669\u6e22\u4ef7\uff1a"
        f"\u4f4e\u4e8e P75 \u662f\u591a\u6570\u5bf9\u624b\u7684\u51fa\u4ef7\u9ed8\u8ba4\u533a\uff0c\u8d85\u8fc7 = \u62d3\u5bbd\u80dc\u9762\u3002"
    )

    return SnipeRecommendation(
        map_id=session.map_id,
        map_name=maps[session.map_id].name,
        hero=session.hero,
        round_window=round_window,
        warehouse_total_cells=wh,
        low_tier_cells_observed=low_cells,
        purple_conditioned=purple_conditioned,
        low_confidence=low_confidence,
        n_matching_samples=len(values),
        expected_value=p50,
        p25_value=p25,
        p75_value=p75,
        p90_value=p90,
        safe_floor_bid=safe_floor,
        snipe_max_bid=snipe_max,
        rationale=rationale,
    )


@dataclass(frozen=True)
class PassRecommendation:
    """\u653e\u4ed3 bid-ceiling suggestion for small junk-heavy cabinets.

    Symmetric to :class:`SnipeRecommendation` but for the *downside*: when
    the warehouse is small (\u2264 ``max_warehouse_cells``) and the
    observed low-tier cells make up a large fraction (\u2265
    ``min_low_tier_fraction``) of the cabinet, the conditional value
    distribution sits *below* the map's overall median. The UI surfaces
    a "pass above X" hint so the player doesn't get baited into bidding
    above the conditional expected value just because the map's general
    reputation is decent.
    """

    map_id: int
    map_name: str
    hero: str
    round_window: str
    warehouse_total_cells: int
    low_tier_cells_observed: int
    low_tier_fraction: float          # low_cells / warehouse_cells
    purple_conditioned: bool          # True when MC also filtered on purple cells
    low_confidence: bool              # True when only the relaxed-threshold fallback fired

    n_matching_samples: int
    expected_value: int               # conditional P50
    p25_value: int                    # conditional P25 (safe-entry reference)
    p75_value: int                    # conditional P75 (rarely worth chasing)

    unconditional_p50: int            # overall map median (no warehouse filter)
    value_ratio: float                # expected / unconditional_p50 — how depressed this cabinet is

    pass_max_bid: int                 # let opponents have it above this (= conditional P50)
    safe_entry_bid: int               # bid up to this for a margin of safety (= P25)
    rationale: str

    def as_ui_tooltip(self) -> str:
        return (
            f"\u4f4e\u4ef7\u4ed3 ({self.round_window}): \u8d85\u8fc7 "
            f"{self.pass_max_bid:,} \u5c31\u653e, "
            f"\u9884\u671f\u4ed3\u4ef7\u4ec5\u662f\u5168\u56fe\u5747\u503c\u7684 "
            f"{self.value_ratio:.0%}"
        )


def compute_pass_recommendation(
    session: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 2000,
    warehouse_tolerance: int = 6,
    purple_tolerance: int = 4,
    min_matching_samples: int = 30,
    min_matching_samples_relaxed: int = 10,
    max_warehouse_cells: int = 80,
    min_low_tier_fraction: float = 0.40,
    safe_entry_ratio: float = 1.0,    # bid up to P25 × this for a margin of safety
    suppress_above_ratio: float = 1.0,
    rng: np.random.Generator | None = None,
    truths: list[SessionTruth] | None = None,
) -> PassRecommendation | None:
    """Conditional MC for the *small junk-heavy* scenario; returns ``None``
    if any precondition fails.

    Hard gating mirrors :func:`compute_snipe_recommendation` but inverts
    the warehouse-size bound and adds a low-tier fraction requirement:

    * ``session.hero`` in ``{"ethan", "aisha"}``
    * ``warehouse_total_cells <= max_warehouse_cells`` (default 80)
    * Low-tier buckets fully observed (Ethan: q=1+q=3; Aisha: q=1+q=2+q=3)
    * ``low_cells / warehouse_cells >= min_low_tier_fraction`` (default 0.40)
    """
    if session.hero == "ethan":
        round_window = "R2"
        required_qs: tuple[int, ...] = (1, 3)
    elif session.hero == "aisha":
        round_window = "R3"
        required_qs = (1, 2, 3)
    else:
        return None

    wh = session.warehouse_total_cells
    if wh is None or wh <= 0 or wh > max_warehouse_cells:
        return None

    observed_cells: list[tuple[int, int]] = []
    for q in required_qs:
        bucket = session.buckets.get(q)
        if bucket is None or bucket.total_cells is None:
            return None
        observed_cells.append((q, bucket.total_cells))
    low_cells = sum(c for _, c in observed_cells)
    low_fraction = low_cells / wh
    if low_fraction < min_low_tier_fraction:
        return None

    if session.map_id not in maps:
        return None

    purple_obs = session.buckets.get(4)
    purple_cells_obs = purple_obs.total_cells if purple_obs is not None else None

    # Red constraints — when player gives red huge_band or value_range, the
    # MC filter should honor them. Otherwise the threshold is computed on
    # average warehouses (no red huge), which understates value for high-red
    # warehouses and falsely fires the pass recommendation.
    red_obs = session.buckets.get(6)
    red_huge_active = red_obs is not None and red_obs.huge_band != "none"
    red_value_active = red_obs is not None and red_obs.value_range is not None
    red_active = red_huge_active or red_value_active

    rng = rng or np.random.default_rng()
    if truths is None:
        truths = [
            sample_session_truth(
                session.map_id, maps=maps, drops=drops, items=items, rng=rng,
            )
            for _ in range(n_trials)
        ]
    all_values: list[int] = []
    cond_warehouse: list[int] = []
    cond_purple: list[int] = []        # subset that also matches purple cells
    cond_red: list[int] = []           # subset that also matches red constraints
    huge_lo, huge_hi = red_obs.huge_count_range() if red_huge_active else (0, 99)
    val_lo, val_hi = red_obs.value_range if red_value_active else (0, 10**12)
    for truth in truths:
        v = truth.total_value()
        all_values.append(v)
        if abs(truth.warehouse_total_cells - wh) > warehouse_tolerance:
            continue
        cond_warehouse.append(v)
        purple_ok = True
        if purple_cells_obs is not None:
            tp = truth.buckets.get(4)
            tp_cells = tp.total_cells if tp is not None else 0
            if abs(tp_cells - purple_cells_obs) <= purple_tolerance:
                cond_purple.append(v)
            else:
                purple_ok = False
        if red_active and purple_ok:
            tr = truth.buckets.get(6)
            tr_huge = tr.huge_count if tr is not None else 0
            tr_value = tr.value_sum if tr is not None else 0
            if huge_lo <= tr_huge <= huge_hi and val_lo <= tr_value <= val_hi:
                cond_red.append(v)

    low_confidence = False
    # Tier preference: cond_red (tightest) → cond_purple → cond_warehouse → relaxed.
    # Red-conditioned samples can be sparse on small maps; accept down to the
    # relaxed threshold and surface low_confidence so the UI warns the player.
    if red_active and len(cond_red) >= min_matching_samples:
        conditional_values = cond_red
        purple_conditioned = True
    elif red_active and len(cond_red) >= min_matching_samples_relaxed:
        conditional_values = cond_red
        purple_conditioned = True
        low_confidence = True
    elif purple_cells_obs is not None and len(cond_purple) >= min_matching_samples:
        conditional_values = cond_purple
        purple_conditioned = True
    elif len(cond_warehouse) >= min_matching_samples:
        conditional_values = cond_warehouse
        purple_conditioned = False
    elif len(cond_warehouse) >= min_matching_samples_relaxed:
        conditional_values = cond_warehouse
        purple_conditioned = False
        low_confidence = True
    else:
        return None

    cond = np.asarray(conditional_values, dtype=np.int64)
    uncond = np.asarray(all_values, dtype=np.int64)
    p25 = int(np.percentile(cond, 25))
    p50 = int(np.percentile(cond, 50))
    p75 = int(np.percentile(cond, 75))
    unc_p50 = int(np.percentile(uncond, 50))
    ratio = (p50 / unc_p50) if unc_p50 > 0 else 0.0

    # When the player's input narrows down to an *above-average* warehouse
    # (e.g. red huge=1 → conditional median way above the map's overall
    # median), the "drop above X" hint is misleading: it'd suggest dropping
    # at a threshold that's actually below typical opponent bid floor.
    # Suppress the recommendation entirely in that case.
    if ratio > suppress_above_ratio:
        return None

    pass_max = p50
    safe_entry = int(p25 * safe_entry_ratio)

    if session.hero == "ethan":
        info_breakdown = (
            f"\u767d\u7eff(\u666e\u54c1\u626b\u63cf) {observed_cells[0][1]} + "
            f"\u84dd(\u826f\u54c1\u626b\u63cf) {observed_cells[1][1]}"
        )
        timing_note = "R2 \u626b\u63cf\u540e\u53d1\u73b0\u5c0f\u4ed3 + \u4f4e\u54c1\u5360\u6bd4\u9ad8"
    else:
        info_breakdown = (
            f"\u767d(\u8f6e\u5ed3 R1) {observed_cells[0][1]} + "
            f"\u7eff(\u8f6e\u5ed3 R2) {observed_cells[1][1]} + "
            f"\u84dd(\u8f6e\u5ed3 R3) {observed_cells[2][1]}"
        )
        timing_note = "R3 \u8f6e\u5ed3\u53e0\u52a0\u540e\u53d1\u73b0\u5c0f\u4ed3 + \u4f4e\u54c1\u5360\u6bd4\u9ad8"

    if purple_conditioned:
        conditioning_note = (
            f"MC \u540c\u65f6\u8fc7\u6ee4 \u4ed3\u5e93\u00b1{warehouse_tolerance} "
            f"+ \u7d2b\u54c1\u683c\u6570 {purple_cells_obs}\u00b1{purple_tolerance}"
        )
    else:
        conditioning_note = (
            f"MC \u8fc7\u6ee4 \u4ed3\u5e93\u00b1{warehouse_tolerance} \u683c"
        )
        if purple_cells_obs is not None:
            conditioning_note += "\uff08\u7d2b\u54c1\u6837\u672c\u4e0d\u8db3\uff0cfallback\uff09"

    low_conf_warning = (
        f"\u26A0\ufe0f \u6837\u672c\u4ec5 {len(conditional_values)} \u4e2a "
        f"(\u4f4e\u4e8e\u63a8\u8350\u9608\u503c {min_matching_samples})\uff0c"
        f"\u4ec5\u4f9b\u53c2\u8003\u3002\u53ef\u63d0\u9ad8 n_trials \u83b7\u5f97\u66f4\u7a33\u5b9a\u7ed3\u679c\u3002\n"
        if low_confidence else ""
    )
    rationale = (
        f"{timing_note}\uff1a\u4ed3\u5e93\u4ec5 {wh} \u683c\uff0c"
        f"\u4f4e\u54c1\u5df2\u5360 {low_cells} \u683c "
        f"({low_fraction:.0%}, {info_breakdown})\u3002\n"
        f"{conditioning_note}\uff0c\u547d\u4e2d {len(conditional_values)} \u4e2a\u6837\u672c\uff1a"
        f"\u603b\u4ed3\u4ef7 \u4e2d\u4f4d\u6570 = {p50:,}\u3001P25 = {p25:,} \u94f6\u5e01\uff0c"
        f"\u53ea\u662f\u672c\u56fe\u5168\u56fe\u5747\u503c {unc_p50:,} \u7684 {ratio:.0%}\u3002\n"
        f"{low_conf_warning}"
        f"\u8d85\u8fc7 {pass_max:,} \u5c31\u653e\u4ed3\uff1b\u82e5\u4e0d\u5f97\u5df2\u8981\u51fa\u4ef7\uff0c"
        f"\u5efa\u8bae\u4e0d\u9ad8\u4e8e P25 = {safe_entry:,} \u94f6\u5e01\uff08\u9001\u8d77\u624b\u4f59\u88d5\uff09\u3002"
    )

    return PassRecommendation(
        map_id=session.map_id,
        map_name=maps[session.map_id].name,
        hero=session.hero,
        round_window=round_window,
        warehouse_total_cells=wh,
        low_tier_cells_observed=low_cells,
        low_tier_fraction=low_fraction,
        purple_conditioned=purple_conditioned,
        low_confidence=low_confidence,
        n_matching_samples=len(conditional_values),
        expected_value=p50,
        p25_value=p25,
        p75_value=p75,
        unconditional_p50=unc_p50,
        value_ratio=ratio,
        pass_max_bid=pass_max,
        safe_entry_bid=safe_entry,
        rationale=rationale,
    )


__all__ = (
    "SnipeRecommendation",
    "compute_snipe_recommendation",
    "PassRecommendation",
    "compute_pass_recommendation",
)
