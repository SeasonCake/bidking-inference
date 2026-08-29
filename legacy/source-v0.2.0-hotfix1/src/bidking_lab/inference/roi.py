"""Leave-one-out tool ROI.

For each tool ``t`` in a kit, we measure how much value-side error the
top-1 joint hypothesis loses when ``t`` is dropped from the kit, then
divide by ``t``'s silver cost. A ``ROI > 1`` means the tool pays for
itself in inference accuracy.

Math (value-side; cells-side is the diagnostic mirror)::

    For each trial:
      truth      = sample_session_truth(map_id)
      obs_full   = build_session_obs(truth, kit)
      top1_full  = joint_top_k_for_session(obs_full)[0]
      err_full   = abs(truth_value - inferred_value(top1_full, obs_full))

      For each tool t in kit:
        obs_loo  = build_session_obs(truth, kit_without_t)
        top1_loo = joint_top_k_for_session(obs_loo)[0]
        err_loo  = abs(truth_value - inferred_value(top1_loo, obs_loo))
        info_gain[t]  += err_loo - err_full

    ROI[t] = mean(info_gain[t]) / price(t)

A *negative* ROI is possible (and informative): it means the engine's
inferred value drifts further from truth when the tool is in the kit
than when it is left out. This can happen when (1) the kit is so thin
that the value-pinning effect of one tool is wiped out by free-running
cells estimates in the other buckets, or (2) the per-cell-value prior
systematically over-estimates the bucket the tool covers — in that
case the tool's "true" reading replaces a fortuitously-good prior.
Phase 2 ROI tables surface these cases as red flags rather than
filtering them away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import (
    SessionTruth,
    sample_session_truth,
)
from bidking_lab.inference.joint import (
    JointHypothesis,
    joint_top_k_for_session,
)
from bidking_lab.inference.observation import HeroMode, SessionObs
from bidking_lab.inference.quality_priors import (
    PER_CELL_VALUE_DEFAULT,
    PER_CELL_VALUE_HUGE,
)
from bidking_lab.inference.synth_readings import build_session_obs


@dataclass(frozen=True)
class ToolROI:
    """Aggregated ROI statistics for one tool over ``n_trials`` samples."""

    tool_name: str
    silver_cost: int
    n_trials: int
    info_gain_value_mean: float      # mean silver of value-error reduction added by tool
    info_gain_value_std: float       # std of the per-trial reduction
    info_gain_cells_mean: float      # mean cells of cells-error reduction (diagnostic)
    roi_value: float                 # info_gain_value_mean / silver_cost


def _inferred_total_value(top1: JointHypothesis, obs: SessionObs) -> int:
    """Estimate total warehouse value from a joint top-1 hypothesis + observations.

    For each observed quality bucket:

    * If the player has the value_sum reading → use it as-is.
    * Else if a value_range is provided → midpoint.
    * Else fall back to the cells-side estimate via the per-cell prior,
      crediting huge items at their lower per-cell rate when the player
      has flagged them.
    """
    total = 0
    for q, cand in top1.per_bucket.items():
        bucket = obs.buckets.get(q)
        if bucket is None:
            total += cand.total_cells * PER_CELL_VALUE_DEFAULT.get(q, 0)
            continue
        if bucket.value_sum is not None:
            total += bucket.value_sum
            continue
        if bucket.value_range is not None:
            lo, hi = bucket.value_range
            total += (lo + hi) // 2
            continue
        # Cells-only path: use the per-cell prior, with optional huge split.
        huge_lo, _ = bucket.huge_count_range()
        huge_per_item = bucket.huge_cells_per_item()
        huge_cells = min(huge_lo * huge_per_item, cand.total_cells)
        per_cell_huge = PER_CELL_VALUE_HUGE.get(q, PER_CELL_VALUE_DEFAULT[q])
        per_cell = PER_CELL_VALUE_DEFAULT[q]
        total += huge_cells * per_cell_huge
        total += (cand.total_cells - huge_cells) * per_cell
    return int(total)


def _inferred_total_cells(top1: JointHypothesis) -> int:
    return sum(cand.total_cells for cand in top1.per_bucket.values())


# Tool that pins exact warehouse cells. When this tool is NOT in the kit,
# the player only has a noisy eyeball estimate (±~10 cells), so the LOO
# run must reflect that — otherwise the engine silently "knows" the truth
# via the 159-cell fallback and 总仓储 looks like it has zero ROI.
_WAREHOUSE_TOOL = "\u603b\u4ed3\u50a8\u7a7a\u95f4"  # 总仓储空间


def _run_inference(
    truth: SessionTruth,
    *,
    hero: HeroMode,
    tools: Sequence[str],
    include_aisha_outline: bool,
    per_bucket_top: int,
    approx_capacity: int | None = None,
) -> tuple[int, int] | None:
    """Run the joint posterior for ``tools`` and return ``(inferred_value, inferred_cells)``.

    Returns None if the engine yields no hypothesis (over-constrained
    inputs); the caller falls back to a defined no-info baseline.

    When ``_WAREHOUSE_TOOL`` is absent from ``tools`` and ``approx_capacity``
    is provided, the obs is annotated with ``warehouse_total_cells_approx``
    so the engine's capacity constraint uses the player's noisy estimate
    instead of the 159-cell fallback. This is what makes 总仓储's ROI
    measurable.
    """
    obs, _ = build_session_obs(
        truth, hero=hero, tools=tools, include_aisha_outline=include_aisha_outline,
    )
    if _WAREHOUSE_TOOL not in tools and approx_capacity is not None:
        obs.warehouse_total_cells_approx = approx_capacity
    top_k = joint_top_k_for_session(obs, k=1, per_bucket_top=per_bucket_top)
    if not top_k:
        return None
    top1 = top_k[0]
    return _inferred_total_value(top1, obs), _inferred_total_cells(top1)


def _no_info_baseline_value(truth: SessionTruth) -> int:
    """Fallback estimate when the engine yields no hypothesis.

    Uses warehouse_total_cells × an aggregate per-cell prior (the gold
    rate, conservative — biased toward over-estimating). This is just a
    pinned reference so that LOO error remains a finite, comparable
    number across runs.
    """
    return truth.warehouse_total_cells * PER_CELL_VALUE_DEFAULT[5]   # 9400


def compute_tool_roi(
    map_id: int,
    tool_kit: Sequence[str],
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    hero: HeroMode = "ethan",
    n_trials: int = 200,
    rng: np.random.Generator | None = None,
    include_aisha_outline: bool = False,
    per_bucket_top: int = 8,
    player_warehouse_noise_std: float = 10.0,
) -> list[ToolROI]:
    """Leave-one-out ROI for each tool in ``tool_kit``.

    Each tool's ROI is the mean value-error reduction it contributes
    over ``n_trials`` sampled sessions, divided by its silver price.

    ``player_warehouse_noise_std`` models the player's eyeball estimate
    of warehouse cells when the kit does not include 总仓储空间. A real
    player can read total cells to within ±5\u201315 by counting visible
    slots; we draw a Gaussian noise with this std per trial. Setting
    this to ``0.0`` mimics the legacy behaviour (engine uses fixed 159
    fallback → 总仓储 looks like a 0-ROI tool because the LOO already
    "knows" the cells via the fallback).

    ``per_bucket_top`` controls the joint search width (passed through
    to :func:`joint_top_k_for_session`). Lower values trade some
    accuracy for substantially faster inference; defaults to 8 which
    typically converges within a few percent of per_bucket_top=20.
    """
    rng = rng or np.random.default_rng()

    # Per-tool running tallies of info_gain
    gain_value: dict[str, list[float]] = {t: [] for t in tool_kit}
    gain_cells: dict[str, list[float]] = {t: [] for t in tool_kit}

    for _ in range(n_trials):
        truth = sample_session_truth(
            map_id, maps=maps, drops=drops, items=items, rng=rng,
        )
        truth_value = truth.total_value()
        truth_cells = truth.warehouse_total_cells

        # Per-trial player eyeball estimate of warehouse cells (used by
        # every _run_inference call within this trial where 总仓储 is
        # absent from the kit). Single sample per trial keeps the LOO
        # comparison fair. std=0.0 → player has perfect eyeball estimate
        # → 总仓储 should be priced as 0 ROI (it tells you nothing new).
        if player_warehouse_noise_std > 0:
            noise = rng.normal(0.0, player_warehouse_noise_std)
            approx_capacity = max(40, int(round(truth_cells + noise)))
        else:
            approx_capacity = int(truth_cells)

        full_run = _run_inference(
            truth, hero=hero, tools=tool_kit,
            include_aisha_outline=include_aisha_outline,
            per_bucket_top=per_bucket_top,
            approx_capacity=approx_capacity,
        )
        if full_run is None:
            full_value_err = abs(truth_value - _no_info_baseline_value(truth))
            full_cells_err = truth_cells
        else:
            fv, fc = full_run
            full_value_err = abs(truth_value - fv)
            full_cells_err = abs(truth_cells - fc)

        for t in tool_kit:
            loo_tools = tuple(x for x in tool_kit if x != t)
            loo_run = _run_inference(
                truth, hero=hero, tools=loo_tools,
                include_aisha_outline=include_aisha_outline,
                per_bucket_top=per_bucket_top,
                approx_capacity=approx_capacity,
            )
            if loo_run is None:
                loo_value_err = abs(truth_value - _no_info_baseline_value(truth))
                loo_cells_err = truth_cells
            else:
                lv, lc = loo_run
                loo_value_err = abs(truth_value - lv)
                loo_cells_err = abs(truth_cells - lc)
            gain_value[t].append(loo_value_err - full_value_err)
            gain_cells[t].append(loo_cells_err - full_cells_err)

    # Aggregate
    from bidking_lab.inference.observation import tool_price
    from bidking_lab.inference.synth_readings import (
        SESSION_TOOL_SPECS,
        TOOL_SPECS,
    )

    out: list[ToolROI] = []
    for t in tool_kit:
        if t in TOOL_SPECS:
            cost = tool_price(t, TOOL_SPECS[t].rarity)
        elif t in SESSION_TOOL_SPECS:
            cost = tool_price(t, SESSION_TOOL_SPECS[t].rarity)
        else:
            raise KeyError(f"unknown tool {t!r}")
        v_arr = np.array(gain_value[t], dtype=np.float64)
        c_arr = np.array(gain_cells[t], dtype=np.float64)
        v_mean = float(v_arr.mean()) if len(v_arr) else 0.0
        v_std = float(v_arr.std(ddof=1)) if len(v_arr) > 1 else 0.0
        c_mean = float(c_arr.mean()) if len(c_arr) else 0.0
        out.append(
            ToolROI(
                tool_name=t,
                silver_cost=cost,
                n_trials=n_trials,
                info_gain_value_mean=v_mean,
                info_gain_value_std=v_std,
                info_gain_cells_mean=c_mean,
                roi_value=(v_mean / cost) if cost else 0.0,
            )
        )
    return out


__all__ = (
    "ToolROI",
    "compute_tool_roi",
)
