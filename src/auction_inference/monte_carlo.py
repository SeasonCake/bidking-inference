"""Deterministic Monte Carlo summaries for a synthetic discrete value pool."""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import fsum, isfinite
from typing import Sequence

from .models import strict_int


@dataclass(frozen=True)
class MonteCarloSummary:
    trials: int
    draws_per_trial: int
    seed: int
    mean: float
    minimum: int
    p05: int
    median: int
    p95: int
    maximum: int


@dataclass(frozen=True)
class PoolItem:
    label: str
    value: int
    weight: float
    category: str

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("pool item label must be a non-empty string")
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("pool item category must be a non-empty string")
        value = strict_int(self.value, "value")
        if value < 0:
            raise ValueError("pool item value must be non-negative")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise TypeError("pool item weight must be a finite positive number")
        weight = float(self.weight)
        if not isfinite(weight) or weight <= 0:
            raise ValueError("pool item weight must be finite and positive")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class CategoryMean:
    category: str
    mean_draws: float


@dataclass(frozen=True)
class PoolSimulationSummary:
    totals: MonteCarloSummary
    replacement: bool
    categories: tuple[CategoryMean, ...]


def _quantile(sorted_values: Sequence[int], probability: float) -> int:
    index = round((len(sorted_values) - 1) * probability)
    return sorted_values[index]


def simulate_totals(
    values: Sequence[int],
    probabilities: Sequence[float],
    *,
    draws_per_trial: int,
    trials: int,
    seed: int,
) -> MonteCarloSummary:
    exact_values = tuple(strict_int(value, "values[]") for value in values)
    if not exact_values or len(exact_values) != len(probabilities):
        raise ValueError("values and probabilities must be non-empty and have equal length")
    if any(value < 0 for value in exact_values):
        raise ValueError("values must be non-negative")
    if any(
        isinstance(weight, bool) or not isinstance(weight, (int, float))
        for weight in probabilities
    ):
        raise TypeError("probabilities must be finite numbers")
    weights = tuple(float(weight) for weight in probabilities)
    if any(not isfinite(weight) or weight < 0 for weight in weights) or fsum(weights) <= 0:
        raise ValueError("probabilities must be finite, non-negative, and have a positive sum")
    draws = strict_int(draws_per_trial, "draws_per_trial")
    trial_count = strict_int(trials, "trials")
    exact_seed = strict_int(seed, "seed")
    if draws <= 0 or trial_count <= 0:
        raise ValueError("draws_per_trial and trials must be positive")

    rng = random.Random(exact_seed)
    totals = sorted(
        sum(rng.choices(exact_values, weights=weights, k=draws)) for _ in range(trial_count)
    )
    return MonteCarloSummary(
        trials=trial_count,
        draws_per_trial=draws,
        seed=exact_seed,
        mean=fsum(totals) / trial_count,
        minimum=totals[0],
        p05=_quantile(totals, 0.05),
        median=_quantile(totals, 0.50),
        p95=_quantile(totals, 0.95),
        maximum=totals[-1],
    )


def _weighted_without_replacement(
    weights: Sequence[float], draws: int, rng: random.Random
) -> tuple[int, ...]:
    available_indices = list(range(len(weights)))
    available_weights = list(weights)
    selected: list[int] = []
    for _ in range(draws):
        target = rng.random() * fsum(available_weights)
        cumulative = 0.0
        selected_position = len(available_weights) - 1
        for position, weight in enumerate(available_weights):
            cumulative += weight
            if target < cumulative:
                selected_position = position
                break
        selected.append(available_indices.pop(selected_position))
        available_weights.pop(selected_position)
    return tuple(selected)


def simulate_pool(
    items: Sequence[PoolItem],
    *,
    draws_per_trial: int,
    trials: int,
    seed: int,
    replacement: bool = True,
) -> PoolSimulationSummary:
    pool = tuple(items)
    if not pool or any(not isinstance(item, PoolItem) for item in pool):
        raise ValueError("items must be a non-empty sequence of PoolItem values")
    labels = tuple(item.label for item in pool)
    if len(set(labels)) != len(labels):
        raise ValueError("pool item labels must be unique")
    draws = strict_int(draws_per_trial, "draws_per_trial")
    trial_count = strict_int(trials, "trials")
    exact_seed = strict_int(seed, "seed")
    if draws <= 0 or trial_count <= 0:
        raise ValueError("draws_per_trial and trials must be positive")
    if type(replacement) is not bool:
        raise TypeError("replacement must be an exact boolean")
    if not replacement and draws > len(pool):
        raise ValueError("draws_per_trial cannot exceed pool size without replacement")

    weights = tuple(item.weight for item in pool)
    rng = random.Random(exact_seed)
    totals: list[int] = []
    category_totals = {category: 0 for category in sorted({item.category for item in pool})}
    indices = tuple(range(len(pool)))
    for _ in range(trial_count):
        selected = (
            tuple(rng.choices(indices, weights=weights, k=draws))
            if replacement
            else _weighted_without_replacement(weights, draws, rng)
        )
        totals.append(sum(pool[index].value for index in selected))
        for index in selected:
            category_totals[pool[index].category] += 1

    ordered_totals = sorted(totals)
    total_summary = MonteCarloSummary(
        trials=trial_count,
        draws_per_trial=draws,
        seed=exact_seed,
        mean=fsum(ordered_totals) / trial_count,
        minimum=ordered_totals[0],
        p05=_quantile(ordered_totals, 0.05),
        median=_quantile(ordered_totals, 0.50),
        p95=_quantile(ordered_totals, 0.95),
        maximum=ordered_totals[-1],
    )
    categories = tuple(
        CategoryMean(category=category, mean_draws=count / trial_count)
        for category, count in category_totals.items()
    )
    return PoolSimulationSummary(
        totals=total_summary,
        replacement=replacement,
        categories=categories,
    )
