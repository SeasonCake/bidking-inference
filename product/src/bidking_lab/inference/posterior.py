"""Posterior filter + per-bucket statistics for the bid-hint MC pipeline.

This module centralises the "given a list of sampled
:class:`SessionTruth` and the player's :class:`SessionObs`, return the
subset that matches every observation the player provided" operation.

It replaces the previous single-line filter in ``app/streamlit_app.py``
that only conditioned on ``warehouse_total_cells``. Every per-bucket
field the player typed (``total_cells``, ``count``, ``value_sum``,
``value_range``, ``huge_band``) becomes a filter constraint.

Two flavours are exported:

* :func:`filter_truths_by_obs` — single-pass filter at a fixed
  tolerance level. Pure function, easy to test.
* :func:`adaptive_filter` — tries the strict tolerance first, widens
  in two more steps if the strict pass yields too few samples, and
  flags ``low_confidence`` once it had to widen. Returns a structured
  :class:`FilterResult` for the UI to render rationale.

A separate helper :func:`bucket_posterior_stats` computes per-bucket
P10/P50/P90 (cells, count, value_sum, huge_count) on the filtered set,
plus the empirical probability that the bucket is empty. This is what
feeds the new "红品后验估计" card in the Streamlit hint tab — even when
the player never typed any red field, the model can still say
*"based on what you did observe, red has P50 ≈ 6 cells, P(red=0) ≈ 18%"*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from bidking_lab.inference.ground_truth import SessionTruth
from bidking_lab.inference.display import best_count_for_avg_value_integer_leak
from bidking_lab.inference.observation import QualityBucketObs, SessionObs

QUALITIES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)


@dataclass
class FilterResult:
    """Outcome of :func:`adaptive_filter`."""

    truths: list[SessionTruth]
    tol_level: int
    """0 = strict, 1 = relaxed, 2 = very relaxed. The UI shows ⚠️ for level ≥ 1."""
    low_confidence: bool
    n_total: int
    n_final: int
    cells_tol: int
    count_tol: int
    value_rel_tol: float
    warehouse_tol: int
    constraints_applied: list[str] = field(default_factory=list)
    """Human-readable strings like ``"q=4 (cells, count, value)"`` for the rationale."""


def _bucket_truth_fields(truth: SessionTruth, quality: int) -> tuple[int, int, int, int]:
    """Return ``(total_cells, count, value_sum, huge_count)`` for a quality.

    Returns all zeros when the bucket is absent from the sampled truth
    (i.e., the session contained nothing of that quality). That is the
    correct semantics for filtering: if the player observed
    ``red.total_cells = 0``, we want truths with no red items to match.
    """
    b = truth.buckets.get(quality)
    if b is None:
        return 0, 0, 0, 0
    return b.total_cells, b.count, b.value_sum, b.huge_count


def _merged_bucket_truth_fields(truth: SessionTruth, qualities: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Return merged ``(total_cells, count, value_sum, huge_count)`` across multiple qualities."""
    total_cells = 0
    count = 0
    value_sum = 0
    huge_count = 0
    for q in qualities:
        c, k, v, h = _bucket_truth_fields(truth, q)
        total_cells += c
        count += k
        value_sum += v
        huge_count += h
    return total_cells, count, value_sum, huge_count


def filter_truths_by_obs(
    truths: Sequence[SessionTruth],
    obs: SessionObs,
    *,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    warehouse_tol: int = 8,
) -> list[SessionTruth]:
    """Return truths compatible with every observation at the given tolerances.

    A truth is kept iff:

    * ``|truth.warehouse_total_cells - obs.warehouse_capacity()| <= warehouse_tol``
    * For every quality ``q`` the player filled into ``obs.buckets``:

      - If ``total_cells`` is given: ``|truth_cells - obs_cells| <= cells_tol``
      - If ``count`` is given: ``|truth_count - obs_count| <= count_tol``
      - If ``value_sum`` is given and > 0:
        ``|truth_value - obs_value| / obs_value <= value_rel_tol``
      - If ``value_range = (lo, hi)`` is given: ``lo <= truth_value <= hi``
      - If ``huge_band != "none"``: ``truth_huge_count`` falls inside the band

    A bucket the player did not fill (``q`` absent from ``obs.buckets``)
    imposes no constraint on that quality. A bucket whose `total_cells`
    is set to ``0`` is treated as the player asserting *no items of this
    quality* — truths with non-zero ``q`` cells get filtered out.

    Note: bucket key ``1`` is treated as merged white+green (quality 1+2)
    when the observation has a q=1 bucket but NO separate q=2 bucket,
    matching the "普品扫描" tool which reports combined cells. When both
    q=1 and q=2 are present (Aisha split mode), each is compared individually.
    """
    wh_target = obs.warehouse_capacity()
    warehouse_tol = _effective_warehouse_tol(obs, warehouse_tol)
    # Merge q=1+q=2 when obs has key 1 but NOT key 2 (Ethan / Aisha non-split).
    _merge_q1_q2 = (1 in obs.buckets and 2 not in obs.buckets)
    total_item_cap = obs.total_item_count
    out: list[SessionTruth] = []
    for t in truths:
        if abs(t.warehouse_total_cells - wh_target) > warehouse_tol:
            continue
        if total_item_cap is not None:
            truth_items = sum(b.count for b in t.buckets.values())
            # Wide tolerance for total items: ±30% or at least ±5
            item_tol = max(5, int(total_item_cap * 0.3))
            if abs(truth_items - total_item_cap) > item_tol:
                continue
        ok = True
        for q, b_obs in obs.buckets.items():
            if q == 1 and _merge_q1_q2:
                t_cells, t_count, t_value, t_huge = _merged_bucket_truth_fields(t, (1, 2))
            else:
                t_cells, t_count, t_value, t_huge = _bucket_truth_fields(t, q)

            if b_obs.total_cells is not None:
                effective_cells_tol = 0 if b_obs.total_cells == 0 else cells_tol
                if abs(t_cells - b_obs.total_cells) > effective_cells_tol:
                    ok = False
                    break
            if b_obs.count is not None:
                effective_count_tol = 0 if b_obs.count == 0 else count_tol
                if abs(t_count - b_obs.count) > effective_count_tol:
                    ok = False
                    break
            if b_obs.value_sum is not None and b_obs.value_sum > 0:
                rel = abs(t_value - b_obs.value_sum) / b_obs.value_sum
                if rel > value_rel_tol:
                    ok = False
                    break
            if b_obs.value_range is not None:
                lo, hi = b_obs.value_range
                if not (lo <= t_value <= hi):
                    ok = False
                    break
            if b_obs.huge_band != "none":
                lo, hi = b_obs.huge_count_range()
                if not (lo <= t_huge <= hi):
                    ok = False
                    break
        if ok:
            out.append(t)
    return out


def _effective_warehouse_tol(obs: SessionObs, warehouse_tol: int) -> int:
    if obs.warehouse_total_cells is not None:
        return warehouse_tol
    return max(warehouse_tol, max(0, obs.warehouse_total_cells_tolerance or 0))


def _describe_constraints(obs: SessionObs) -> list[str]:
    """Build a human-readable list of "what filters are active" for rationale."""
    if (
        obs.warehouse_total_cells is None
        and obs.warehouse_total_cells_approx is not None
        and obs.warehouse_total_cells_tolerance is not None
    ):
        parts: list[str] = [
            "warehouse≈%s±%s"
            % (obs.warehouse_capacity(), obs.warehouse_total_cells_tolerance)
        ]
    else:
        parts = [f"warehouse={obs.warehouse_capacity()}"]
    if obs.total_item_count is not None:
        cap = int(obs.total_item_count)
        item_tol = max(5, int(cap * 0.3))
        parts.append(f"items≈{cap}±{item_tol}")
    for q in sorted(obs.buckets.keys()):
        b = obs.buckets[q]
        items: list[str] = []
        if b.total_cells is not None:
            items.append(f"cells={b.total_cells}")
        if b.count is not None:
            items.append(f"count={b.count}")
        if b.value_sum is not None and b.value_sum > 0:
            items.append(f"value≈{b.value_sum:,}")
        if b.value_range is not None:
            items.append(f"value∈[{b.value_range[0]:,},{b.value_range[1]:,}]")
        if b.huge_band != "none":
            items.append(f"huge={b.huge_band}")
        if items:
            parts.append(f"q={q}({', '.join(items)})")
    return parts


def _fallback_hard_buckets(obs: SessionObs) -> dict[int, QualityBucketObs]:
    """Per-bucket constraints kept when warehouse-only fallback drops soft filters."""
    hard_buckets: dict[int, QualityBucketObs] = {}
    for q, b in obs.buckets.items():
        if b.total_cells is not None and b.total_cells == 0:
            hard_buckets[q] = b
        elif q == 6 and b.total_cells is not None:
            hard_buckets[q] = b
        elif b.value_sum is not None and b.value_sum > 0:
            hard_buckets[q] = QualityBucketObs(
                quality=q,
                value_sum=b.value_sum,
                value_range=b.value_range,
                huge_band=b.huge_band,
                huge_cells_override=b.huge_cells_override,
            )
        elif b.huge_band != "none":
            hard_buckets[q] = QualityBucketObs(
                quality=q,
                huge_band=b.huge_band,
                huge_cells_override=b.huge_cells_override,
            )
    return hard_buckets


def adaptive_filter(
    truths: Sequence[SessionTruth],
    obs: SessionObs,
    *,
    min_samples: int = 30,
    cells_tol_levels: Sequence[int] = (2, 4, 8),
    count_tol_levels: Sequence[int] = (1, 2, 3),
    value_rel_tol_levels: Sequence[float] = (0.10, 0.20, 0.40),
    warehouse_tol_levels: Sequence[int] = (8, 8, 12),
) -> FilterResult:
    """Try strict→loose tolerances until at least ``min_samples`` truths match.

    Returns the result at the strictest level that reaches ``min_samples``.
    If even the loosest level doesn't reach it, attempts a warehouse-only
    fallback (ignoring per-bucket constraints) with progressively wider
    tolerance. This handles the common case where the user's warehouse
    sits at the tail of the map distribution but the cells breakdown
    they provided is internally consistent.

    The four ``*_levels`` tuples must have the same length; level i uses
    each tuple's i-th entry.
    """
    n_levels = len(cells_tol_levels)
    assert (
        len(count_tol_levels) == n_levels
        and len(value_rel_tol_levels) == n_levels
        and len(warehouse_tol_levels) == n_levels
    ), "tolerance level tuples must have equal length"

    constraints = _describe_constraints(obs)
    last_result: list[SessionTruth] = []
    last_level = n_levels - 1
    for level in range(n_levels):
        filtered = filter_truths_by_obs(
            truths,
            obs,
            cells_tol=cells_tol_levels[level],
            count_tol=count_tol_levels[level],
            value_rel_tol=value_rel_tol_levels[level],
            warehouse_tol=warehouse_tol_levels[level],
        )
        last_result = filtered
        last_level = level
        if len(filtered) >= min_samples:
            effective_wh_tol = _effective_warehouse_tol(
                obs,
                warehouse_tol_levels[level],
            )
            return FilterResult(
                truths=filtered,
                tol_level=level,
                low_confidence=(level > 0),
                n_total=len(truths),
                n_final=len(filtered),
                cells_tol=cells_tol_levels[level],
                count_tol=count_tol_levels[level],
                value_rel_tol=value_rel_tol_levels[level],
                warehouse_tol=effective_wh_tol,
                constraints_applied=constraints,
            )

    # --- Warehouse-only fallback ---
    # When per-bucket constraints kill all samples (common when the user's
    # warehouse is at the distribution tail), try warehouse-only with
    # progressively wider tolerance to get SOME conditioning.
    # PRESERVE hard assertions: red=0 (total_cells==0 buckets) and total_item_count.
    if len(last_result) < min_samples and obs.buckets:
        hard_buckets = _fallback_hard_buckets(obs)
        fallback_obs = SessionObs(
            map_id=obs.map_id, hero=obs.hero,
            warehouse_total_cells=obs.warehouse_total_cells,
            warehouse_total_cells_approx=obs.warehouse_total_cells_approx,
            warehouse_total_cells_tolerance=obs.warehouse_total_cells_tolerance,
            buckets=hard_buckets,
        )
        for wh_tol in (12, 20, 30, 50):
            effective_wh_tol = _effective_warehouse_tol(fallback_obs, wh_tol)
            wh_only = filter_truths_by_obs(
                truths, fallback_obs, warehouse_tol=wh_tol,
                value_rel_tol=value_rel_tol_levels[-1],
            )
            if len(wh_only) >= min_samples:
                kept_parts = ["warehouse"]
                if hard_buckets:
                    hard_desc = []
                    for hq, hb in hard_buckets.items():
                        parts_h = []
                        if hb.total_cells is not None:
                            parts_h.append(f"{hb.total_cells}cells")
                        if hb.value_sum is not None:
                            parts_h.append(f"≈{hb.value_sum:,}val")
                        if hb.huge_band != "none":
                            parts_h.append(f"huge={hb.huge_band}")
                        hard_desc.append(f"q{hq}({','.join(parts_h)})")
                    kept_parts.append("hard(" + ",".join(hard_desc) + ")")
                if obs.total_item_count:
                    kept_parts.append(f"items≈{obs.total_item_count}")
                constraints_wh = [
                    f"warehouse={obs.warehouse_capacity()}±{effective_wh_tol}"
                    f"({'+'.join(kept_parts)},soft bucket约束已放弃)"
                ]
                return FilterResult(
                    truths=wh_only,
                    tol_level=n_levels,
                    low_confidence=True,
                    n_total=len(truths),
                    n_final=len(wh_only),
                    cells_tol=wh_tol,
                    count_tol=count_tol_levels[-1],
                    value_rel_tol=1.0,
                    warehouse_tol=effective_wh_tol,
                    constraints_applied=constraints_wh,
                )

    return FilterResult(
        truths=last_result,
        tol_level=last_level,
        low_confidence=True,
        n_total=len(truths),
        n_final=len(last_result),
        cells_tol=cells_tol_levels[last_level],
        count_tol=count_tol_levels[last_level],
        value_rel_tol=value_rel_tol_levels[last_level],
        warehouse_tol=_effective_warehouse_tol(obs, warehouse_tol_levels[last_level]),
        constraints_applied=constraints,
    )


@dataclass
class BucketPosterior:
    """Per-bucket posterior summary from the filtered truth set."""

    quality: int
    n: int
    cells_p10: int
    cells_p50: int
    cells_p90: int
    count_p10: int
    count_p50: int
    count_p90: int
    value_p10: int
    value_p50: int
    value_p90: int
    huge_p50: int
    huge_p90: int
    p_empty: float
    """Empirical probability that this quality bucket is empty in the filtered set."""


def bucket_posterior_stats(
    filtered: Sequence[SessionTruth],
    quality: int,
) -> BucketPosterior:
    """Compute P10/P50/P90 for cells / count / value of one quality bucket.

    Defined on the FILTERED set, not the raw map prior — so the numbers
    reflect "what is left possible after the observations".
    """
    n = len(filtered)
    if n == 0:
        return BucketPosterior(
            quality=quality, n=0,
            cells_p10=0, cells_p50=0, cells_p90=0,
            count_p10=0, count_p50=0, count_p90=0,
            value_p10=0, value_p50=0, value_p90=0,
            huge_p50=0, huge_p90=0,
            p_empty=0.0,
        )
    cells = np.empty(n, dtype=np.int64)
    counts = np.empty(n, dtype=np.int64)
    values = np.empty(n, dtype=np.int64)
    huges = np.empty(n, dtype=np.int64)
    for i, t in enumerate(filtered):
        c, k, v, h = _bucket_truth_fields(t, quality)
        cells[i] = c
        counts[i] = k
        values[i] = v
        huges[i] = h
    cells_p = np.percentile(cells, [10, 50, 90]).astype(int)
    count_p = np.percentile(counts, [10, 50, 90]).astype(int)
    value_p = np.percentile(values, [10, 50, 90]).astype(int)
    huge_p = np.percentile(huges, [50, 90]).astype(int)
    return BucketPosterior(
        quality=quality, n=n,
        cells_p10=int(cells_p[0]), cells_p50=int(cells_p[1]), cells_p90=int(cells_p[2]),
        count_p10=int(count_p[0]), count_p50=int(count_p[1]), count_p90=int(count_p[2]),
        value_p10=int(value_p[0]), value_p50=int(value_p[1]), value_p90=int(value_p[2]),
        huge_p50=int(huge_p[0]), huge_p90=int(huge_p[1]),
        p_empty=float(np.mean(cells == 0)),
    )


# --- Analytical value estimation (bypass MC when cells are fully specified) ---

from bidking_lab.inference.joint import joint_top_k_for_session
from bidking_lab.inference.observation import BucketCandidate, candidates_for_bucket
from bidking_lab.inference.quality_priors import PER_CELL_VALUE_DEFAULT, PER_CELL_VALUE_HUGE, estimate_total_cells


@dataclass
class AnalyticalEstimate:
    """Direct value estimate from cells × per-cell priors, no MC needed."""

    total_value_low: int
    total_value_mid: int
    total_value_high: int
    per_bucket: dict[int, tuple[int, int, int]]
    """quality → (low, mid, high) value estimates."""
    red_cells_inferred: int
    red_auto_detected: bool
    breakdown_text: str


# Per-cell value with uncertainty bands (low=0.6×, mid=1.0×, high=1.5×).
_VALUE_BAND_FACTORS = (0.6, 1.0, 1.5)


def _has_bucket_inference_info(bucket: QualityBucketObs) -> bool:
    """Return whether a bucket has enough non-cell data to infer cells."""
    return (
        (bucket.value_sum is not None and bucket.value_sum > 0)
        or bucket.huge_band != "none"
        or bucket.avg_cells is not None
        or bucket.count is not None
        or (bucket.avg_value is not None and bucket.avg_value > 0)
    )


def _candidate_count_for_value(bucket: QualityBucketObs, candidate: BucketCandidate) -> int:
    """Prefer the game's integer-leak count when avg_value exposes it."""
    if (
        bucket.avg_value is not None
        and bucket.avg_value > 0
        and (bucket.value_sum is None or bucket.value_sum <= 0)
    ):
        leak_cnt = best_count_for_avg_value_integer_leak(float(bucket.avg_value))
        if leak_cnt is not None:
            return leak_cnt
    return candidate.count


def compute_analytical_estimate(obs: SessionObs) -> AnalyticalEstimate | None:
    """Compute value directly from user-provided cells using per-cell priors.

    Returns None if the user hasn't provided enough bucket cells to make
    a meaningful estimate (< 50% of warehouse covered).
    """
    warehouse = obs.warehouse_capacity()
    if warehouse <= 0:
        return None

    known_cells: dict[int, int] = {}
    inferred_count: dict[int, int] = {}  # count from enumeration top-1
    value_derived: set[int] = set()  # track which buckets had cells estimated

    # First pass: collect explicitly known cells
    for q, b in obs.buckets.items():
        if b.total_cells is not None and b.total_cells > 0:
            known_cells[q if q != 2 else 1] = b.total_cells

    # Second pass: prefer session-level joint enumeration for buckets without
    # explicit cells. This lets purple/gold/etc. compete for the same warehouse
    # capacity instead of each taking its local top-1 independently.
    joint = joint_top_k_for_session(
        obs,
        k=1,
        per_bucket_top=16,
        warehouse_slack=5,
    )
    if joint:
        for q, cand in joint[0].per_bucket.items():
            if q in (1, 2):
                continue
            b = obs.buckets[q]
            if b.total_cells is not None or not _has_bucket_inference_info(b):
                continue
            known_cells[q] = cand.total_cells
            inferred_count[q] = _candidate_count_for_value(b, cand)
            value_derived.add(q)

    # Fallback for buckets not solved by the joint pass: use the per-bucket
    # brute-force enumerator, constrained by cells already known or inferred.
    for q, b in obs.buckets.items():
        if b.total_cells is not None or q in (1, 2) or q in known_cells:
            continue
        if not _has_bucket_inference_info(b):
            continue
        other_known_cells = sum(known_cells.values())
        cands = candidates_for_bucket(
            b, warehouse_capacity=warehouse, other_known_cells=other_known_cells,
        )
        if cands:
            known_cells[q] = cands[0].total_cells
            inferred_count[q] = _candidate_count_for_value(b, cands[0])
            value_derived.add(q)
        else:
            huge_cells = b.min_huge_cells() if b.huge_band != "none" else 0
            if b.value_sum is not None and b.value_sum > 0:
                est = estimate_total_cells(q, b.value_sum, huge_cells=huge_cells)
                if est > 0:
                    known_cells[q] = est
                    value_derived.add(q)
            elif huge_cells > 0:
                known_cells[q] = huge_cells

    total_known = sum(known_cells.values())
    if total_known < warehouse * 0.4:
        return None

    # Only auto-infer red when ALL non-red buckets are accounted for.
    # If gold (or another bucket) is missing, remaining cells could be
    # gold OR red — we can't assign them all to red.
    required_non_red = {1, 3, 4, 5}
    provided_qs = set(obs.buckets.keys())
    if 2 in provided_qs:
        required_non_red.add(2)
    all_non_red_filled = all(known_cells.get(q, 0) > 0 for q in required_non_red)

    red_cells = max(0, warehouse - total_known)
    red_auto = (6 not in known_cells and red_cells > 0 and all_non_red_filled)
    if red_auto:
        known_cells[6] = red_cells

    per_bucket: dict[int, tuple[int, int, int]] = {}
    total_low, total_mid, total_high = 0, 0, 0
    lines: list[str] = []

    # Use merged q1+q2 per-cell value for bucket key 1
    _merged_pcv = (PER_CELL_VALUE_DEFAULT[1] + PER_CELL_VALUE_DEFAULT[2]) / 2

    for q in sorted(known_cells.keys()):
        cells = known_cells[q]
        b_obs = obs.buckets.get(q)
        huge_cells_in_bucket = 0
        if b_obs is not None and b_obs.huge_band != "none":
            h_lo, _ = b_obs.huge_count_range()
            if h_lo > 0:
                huge_cells_in_bucket = h_lo * b_obs.huge_cells_per_item()

        # If user provided value_sum directly, use it as mid-point
        has_value_sum = (b_obs is not None and b_obs.value_sum is not None
                         and b_obs.value_sum > 0)

        if has_value_sum:
            mid_val = b_obs.value_sum
            pcv = mid_val / max(1, cells)
            avg_value_tag = ""
        elif (
            b_obs is not None
            and b_obs.avg_value is not None
            and b_obs.avg_value > 0
        ):
            avg_per_item = float(b_obs.avg_value)
            cnt = inferred_count.get(q)
            if cnt is None and b_obs.count is not None and b_obs.count > 0:
                cnt = int(b_obs.count)
            if cnt is None:
                cnt = best_count_for_avg_value_integer_leak(avg_per_item)
            if cnt is not None and cnt > 0:
                mid_val = int(avg_per_item * cnt)
                pcv = mid_val / max(1, cells)
                avg_value_tag = f"·{cnt}件×{avg_per_item:.0f}/件"
            else:
                mid_val = int(cells * avg_per_item)
                pcv = avg_per_item
                avg_value_tag = "（均价/件，件数未锁定）"
        elif q == 1:
            pcv = _merged_pcv
            mid_val = int(cells * pcv)
            avg_value_tag = ""
        elif huge_cells_in_bucket > 0 and q in PER_CELL_VALUE_HUGE:
            non_huge_cells = max(0, cells - huge_cells_in_bucket)
            mid_val = (
                huge_cells_in_bucket * PER_CELL_VALUE_HUGE[q]
                + non_huge_cells * PER_CELL_VALUE_DEFAULT.get(q, 1000)
            )
            pcv = mid_val / max(1, cells)
            avg_value_tag = ""
        else:
            pcv = PER_CELL_VALUE_DEFAULT.get(q, 1000)
            mid_val = int(cells * pcv)
            avg_value_tag = ""

        lo = int(mid_val * _VALUE_BAND_FACTORS[0])
        mid = mid_val
        hi = int(mid_val * _VALUE_BAND_FACTORS[2])
        per_bucket[q] = (lo, mid, hi)
        total_low += lo
        total_mid += mid
        total_high += hi
        q_name = {1: "白绿", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}.get(q, f"q{q}")
        inferred_tag = "（自动推断）" if (q == 6 and red_auto) else ""
        huge_tag = f"(含{huge_cells_in_bucket}格巨物)" if huge_cells_in_bucket > 0 else ""
        value_tag = "（用户估价）" if has_value_sum else ""
        if q in value_derived:
            cnt = inferred_count.get(q)
            est_tag = f"（由枚举推算→{cnt}件）" if cnt else "（由估价推算）"
        else:
            est_tag = ""
        lines.append(
            f"{q_name} {cells}格{avg_value_tag}×{pcv:.0f}/格 → {lo:,}~{hi:,}"
            f"{inferred_tag}{huge_tag}{value_tag}{est_tag}"
        )

    if not all_non_red_filled and red_cells > 0:
        missing_qs = required_non_red - provided_qs
        missing_names = [
            {1: "白绿", 3: "蓝", 4: "紫", 5: "金"}.get(q, f"q{q}")
            for q in sorted(missing_qs) if q != 6
        ]
        # Estimate range: all remaining could be cheapest missing quality or red
        missing_q_pcvs = [
            PER_CELL_VALUE_DEFAULT.get(q, 1000)
            for q in sorted(missing_qs) if q != 6
        ]
        lo_pcv = min(missing_q_pcvs) if missing_q_pcvs else PER_CELL_VALUE_DEFAULT.get(5, 9400)
        hi_pcv = PER_CELL_VALUE_DEFAULT[6]  # red is the most expensive
        remaining_lo = int(red_cells * lo_pcv * _VALUE_BAND_FACTORS[0])
        remaining_hi = int(red_cells * hi_pcv * _VALUE_BAND_FACTORS[2])
        total_low += remaining_lo
        total_high += remaining_hi
        total_mid += int(red_cells * (lo_pcv + hi_pcv) / 2)
        lines.append(
            f"未分配 {red_cells}格（{'/'.join(missing_names)}未填）"
            f" → {remaining_lo:,}~{remaining_hi:,}"
        )

    breakdown = "\n".join(lines)

    return AnalyticalEstimate(
        total_value_low=total_low,
        total_value_mid=total_mid,
        total_value_high=total_high,
        per_bucket=per_bucket,
        red_cells_inferred=red_cells if red_auto else 0,
        red_auto_detected=red_auto,
        breakdown_text=breakdown,
    )
