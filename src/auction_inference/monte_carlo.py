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
