"""Posterior normalization over hypotheses that passed explicit constraints."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite
from typing import Iterable

from .constraints import ConstraintResult, filter_hypotheses
from .models import Evidence, Hypothesis


@dataclass(frozen=True)
class PosteriorRow:
    label: str
    probability: float
    total_count: int
    total_value: int


@dataclass(frozen=True)
class PosteriorSummary:
    rows: tuple[PosteriorRow, ...]
    expected_count: float
    expected_value: float
    rejected: tuple[ConstraintResult, ...]


def normalize_weights(weights: Iterable[float]) -> tuple[float, ...]:
    raw_values = tuple(weights)
    if not raw_values:
        raise ValueError("at least one weight is required")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_values):
        raise TypeError("weights must be finite numbers")
    values = tuple(float(weight) for weight in raw_values)
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("weights must be finite and non-negative")
    total = fsum(values)
    if total <= 0:
        raise ValueError("weights must have a positive sum")
    return tuple(value / total for value in values)


def infer_posterior(hypotheses: Iterable[Hypothesis], evidence: Evidence) -> PosteriorSummary:
    accepted, rejected = filter_hypotheses(tuple(hypotheses), evidence)
    if not accepted:
        raise ValueError("evidence rejects every hypothesis")
    probabilities = normalize_weights(result.hypothesis.prior for result in accepted)
    rows = tuple(
        PosteriorRow(
            label=result.hypothesis.label,
            probability=probability,
            total_count=result.hypothesis.total_count,
            total_value=result.hypothesis.total_value,
        )
        for result, probability in zip(accepted, probabilities, strict=True)
    )
    return PosteriorSummary(
        rows=rows,
        expected_count=fsum(row.probability * row.total_count for row in rows),
        expected_value=fsum(row.probability * row.total_value for row in rows),
        rejected=rejected,
    )
