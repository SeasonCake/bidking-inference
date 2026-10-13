"""Joint posterior over multiple quality buckets.

The per-bucket :func:`candidates_for_bucket` enumerator is locally correct
but globally myopic: when ``top_k_for_session`` solves quality 6 first and
greedily subtracts its top-1 cells from the warehouse budget, a slightly
wrong q=6 pick cascades into wrong q=5 and q=4 picks.

This module instead does a *joint* DFS over the cartesian product of
per-bucket top-N candidates, prunes on the running warehouse-cells sum,
and ranks the surviving global hypotheses by summed composite score plus
a soft penalty for exceeding the observed warehouse capacity.

Typical session size: 3-5 observed quality buckets × 8 top-N candidates
each = at most 8^5 = 32_768 combinations, with aggressive cells-sum
pruning the actual explored count is usually < 1_000 per call → sub-ms
runtime on a laptop. No need for fancy MCMC or LP relaxations.
"""

from __future__ import annotations

from dataclasses import dataclass

from bidking_lab.inference.observation import (
    BucketCandidate,
    SessionObs,
    candidates_for_bucket,
)


@dataclass(frozen=True)
class JointHypothesis:
    """One full assignment across all observed quality buckets.

    Attributes:
        per_bucket: ``{quality_int: BucketCandidate}`` for every quality
            present in the input session.
        total_cells: Sum of ``total_cells`` across the per-bucket picks.
        bucket_composite: Sum of the per-bucket composite scores (lower
            is better).
        warehouse_penalty: Soft penalty for going over the observed
            warehouse capacity (zero if under).
        composite: ``bucket_composite + warehouse_penalty``; the value the
            joint top-K is sorted on.
    """

    per_bucket: dict[int, BucketCandidate]
    total_cells: int
    bucket_composite: float
    warehouse_penalty: float
    composite: float


def joint_top_k_for_session(
    session: SessionObs,
    *,
    k: int = 5,
    per_bucket_top: int = 8,
    warehouse_slack: int = 10,
    warehouse_over_weight: float = 0.05,
) -> list[JointHypothesis]:
    """Joint top-K hypotheses across all observed buckets.

    Args:
        session: The player's observations.
        k: How many global top hypotheses to return.
        per_bucket_top: Truncate each quality's candidate list to its top-N
            before the cartesian search. Defaults to 8 (good enough — the
            true (total_cells, count) is almost always in the per-bucket
            top-5 if the player provided at least one reading or scan).
        warehouse_slack: Combinations whose summed cells exceed
            ``warehouse_capacity + warehouse_slack`` are pruned during
            DFS. Slack accounts for the player not necessarily having
            observed every quality bucket.
        warehouse_over_weight: Soft penalty per cell over capacity. Tiny by
            default — the slack window itself is the dominant filter.

    Returns:
        A list of at most ``k`` ``JointHypothesis`` sorted by composite
        score (lowest first).
    """
    capacity = session.warehouse_capacity()
    tolerance = (
        max(0, session.warehouse_total_cells_tolerance or 0)
        if session.warehouse_total_cells is None
        else 0
    )
    cap_max = capacity + max(warehouse_slack, tolerance)
    candidate_capacity = session.warehouse_capacity_upper_bound()
    total_item_cap = session.total_item_count   # None = unconstrained

    per_bucket_cands: dict[int, list[BucketCandidate]] = {}
    for q in (6, 5, 4, 3, 2, 1):
        bucket = session.buckets.get(q)
        if bucket is None:
            continue
        cands = candidates_for_bucket(
            bucket,
            warehouse_capacity=candidate_capacity,
        )
        if cands:
            per_bucket_cands[q] = cands[:per_bucket_top]

    if not per_bucket_cands:
        return []

    # Walk qualities high-to-low so the rare/expensive buckets pin down
    # large cell chunks first, giving aggressive sum-pruning early.
    qualities: list[int] = sorted(per_bucket_cands.keys(), reverse=True)
    candidate_lists: list[list[BucketCandidate]] = [
        per_bucket_cands[q] for q in qualities
    ]

    results: list[JointHypothesis] = []
    picks: list[BucketCandidate] = []

    def dfs(idx: int, running_cells: int, running_count: int,
            running_bucket_score: float) -> None:
        if running_cells > cap_max:
            return
        # Hard prune: if total_item_count is given, any hypothesis whose
        # observed buckets already overshoot it is impossible. We allow
        # equality (sum == total) and also undershoot (≤ total) since the
        # player may not have observed every bucket.
        if total_item_cap is not None and running_count > total_item_cap:
            return
        if idx == len(qualities):
            over = max(0, running_cells - capacity)
            penalty = warehouse_over_weight * over
            # Soft penalty if total_item_count given and our sum
            # significantly undershoots (we expect every bucket to have
            # ≥1 item, so undershoot is normal). Mild penalty per missing
            # item to slightly prefer hypotheses that account for more.
            if total_item_cap is not None:
                missing = max(0, total_item_cap - running_count)
                penalty += 0.02 * missing
            results.append(
                JointHypothesis(
                    per_bucket={qualities[i]: picks[i] for i in range(len(picks))},
                    total_cells=running_cells,
                    bucket_composite=running_bucket_score,
                    warehouse_penalty=penalty,
                    composite=running_bucket_score + penalty,
                )
            )
            return
        for cand in candidate_lists[idx]:
            picks.append(cand)
            dfs(
                idx + 1,
                running_cells + cand.total_cells,
                running_count + cand.count,
                running_bucket_score + cand.composite,
            )
            picks.pop()

    dfs(0, 0, 0, 0.0)
    results.sort(key=lambda h: h.composite)
    return results[:k]


__all__ = (
    "JointHypothesis",
    "joint_top_k_for_session",
)
