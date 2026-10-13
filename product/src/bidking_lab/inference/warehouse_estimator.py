"""Estimate total warehouse cells from partial live observations.

The normal posterior engine requires a warehouse reading. This module answers
the earlier question: when that reading is missing, what total-cell range is
still likely given the map, Aisha/Ethan reveals, scans, and public hints?
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import prepare_session_sampler
from bidking_lab.inference.map_likelihood import (
    QuantileSummary,
    category_observation_soft_score,
    truth_matches_obs,
)
from bidking_lab.inference.observation import SessionObs


@dataclass(frozen=True)
class WarehouseMapContribution:
    """How much one candidate map contributed to the warehouse estimate."""

    map_id: int
    map_name: str
    n_total: int
    n_matched: int
    likelihood: float
    posterior_probability: float = 0.0
    total_cells: QuantileSummary | None = None


@dataclass(frozen=True)
class WarehouseEstimate:
    """Aggregate warehouse-cell estimate across candidate maps."""

    n_total: int
    n_matched: int
    total_cells: QuantileSummary | None
    total_value: QuantileSummary | None
    confidence: str
    reason: str
    map_contributions: tuple[WarehouseMapContribution, ...]


def _quantiles(values: Sequence[int]) -> QuantileSummary | None:
    if not values:
        return None
    p10, p50, p90 = np.percentile(np.asarray(values, dtype=np.float64), [10, 50, 90])
    return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))


def _obs_without_warehouse(obs: SessionObs) -> SessionObs:
    return replace(
        obs,
        warehouse_total_cells=None,
        warehouse_total_cells_approx=None,
        warehouse_total_cells_tolerance=None,
    )


def _confidence(n_matched: int, total_cells: QuantileSummary | None) -> tuple[str, str]:
    if n_matched <= 0 or total_cells is None:
        return "无匹配", "当前证据在采样中没有匹配样本"
    width = total_cells.p90 - total_cells.p10
    if n_matched < 30:
        return "低", "匹配样本少，仓储区间只作方向参考"
    if n_matched < 100 or width > 40:
        return "中", "匹配样本或区间宽度仍有不确定性"
    return "高", "匹配样本充足且仓储区间较集中"


def estimate_warehouse_cells(
    candidate_map_ids: Sequence[int],
    obs: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 3000,
    seed: int = 0,
    cells_tol: int = 2,
    count_tol: int = 1,
    value_rel_tol: float = 0.10,
    total_item_count_tol: int = 0,
) -> WarehouseEstimate:
    """Estimate total warehouse cells without using warehouse observations.

    Any exact or approximate warehouse field in ``obs`` is intentionally
    removed before matching. All other evidence is kept.
    """

    obs_no_warehouse = _obs_without_warehouse(obs)
    rng = np.random.default_rng(seed)
    contributions: list[WarehouseMapContribution] = []
    matched_cells: list[int] = []
    matched_values: list[int] = []
    total_trials = 0

    for map_id in candidate_map_ids:
        if map_id not in maps:
            continue
        sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
        map_cells: list[int] = []
        map_weight = 0.0
        for _ in range(max(0, int(n_trials))):
            truth = sampler.sample(rng=rng)
            total_trials += 1
            if truth_matches_obs(
                truth,
                obs_no_warehouse,
                cells_tol=cells_tol,
                count_tol=count_tol,
                value_rel_tol=value_rel_tol,
                total_item_count_tol=total_item_count_tol,
            ):
                weight = category_observation_soft_score(truth, obs_no_warehouse)
                map_weight += weight
                map_cells.append(truth.warehouse_total_cells)
                matched_cells.append(truth.warehouse_total_cells)
                matched_values.append(truth.total_value())
        likelihood = map_weight / max(1, int(n_trials))
        contributions.append(
            WarehouseMapContribution(
                map_id=map_id,
                map_name=maps[map_id].name,
                n_total=max(0, int(n_trials)),
                n_matched=len(map_cells),
                likelihood=likelihood,
                total_cells=_quantiles(map_cells),
            )
        )

    total_likelihood = sum(row.likelihood for row in contributions)
    if total_likelihood > 0:
        contributions = [
            WarehouseMapContribution(
                map_id=row.map_id,
                map_name=row.map_name,
                n_total=row.n_total,
                n_matched=row.n_matched,
                likelihood=row.likelihood,
                posterior_probability=row.likelihood / total_likelihood,
                total_cells=row.total_cells,
            )
            for row in contributions
        ]
    contributions.sort(
        key=lambda row: (row.posterior_probability, row.likelihood),
        reverse=True,
    )

    total_cells = _quantiles(matched_cells)
    confidence, reason = _confidence(len(matched_cells), total_cells)
    return WarehouseEstimate(
        n_total=total_trials,
        n_matched=len(matched_cells),
        total_cells=total_cells,
        total_value=_quantiles(matched_values),
        confidence=confidence,
        reason=reason,
        map_contributions=tuple(contributions),
    )


__all__ = (
    "WarehouseEstimate",
    "WarehouseMapContribution",
    "estimate_warehouse_cells",
)
