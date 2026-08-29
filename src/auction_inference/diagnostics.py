"""Posterior diagnostics for normalized discrete probability rows."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isclose, isfinite, log2
from typing import Iterable


@dataclass(frozen=True)
class PosteriorDiagnostics:
    support_size: int
    entropy_bits: float
    effective_sample_size: float
    maximum_probability: float


def _normalized_probabilities(probabilities: Iterable[float]) -> tuple[float, ...]:
    raw_values = tuple(probabilities)
    if not raw_values:
        raise ValueError("at least one probability is required")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_values):
        raise TypeError("probabilities must be finite numbers")
    values = tuple(float(value) for value in raw_values)
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("probabilities must be finite and non-negative")
    total = fsum(values)
    if not isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("probabilities must sum to one")
    return tuple(value / total for value in values)


def posterior_diagnostics(probabilities: Iterable[float]) -> PosteriorDiagnostics:
    values = _normalized_probabilities(probabilities)
    entropy = -fsum(value * log2(value) for value in values if value > 0)
    return PosteriorDiagnostics(
        support_size=sum(value > 0 for value in values),
        entropy_bits=entropy,
        effective_sample_size=1.0 / fsum(value * value for value in values),
        maximum_probability=max(values),
    )


def smallest_credible_set(
    labeled_probabilities: Iterable[tuple[str, float]], *, mass: float = 0.95
) -> tuple[tuple[str, float], ...]:
    if isinstance(mass, bool) or not isinstance(mass, (int, float)) or not isfinite(mass):
        raise TypeError("mass must be a finite number")
    exact_mass = float(mass)
    if not 0 < exact_mass <= 1:
        raise ValueError("mass must be in (0, 1]")
    rows = tuple(labeled_probabilities)
    if not rows:
        raise ValueError("at least one labeled probability is required")
    labels = tuple(label for label, _ in rows)
    if any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("labels must be non-empty strings")
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")
    probabilities = _normalized_probabilities(probability for _, probability in rows)
    ordered = sorted(zip(labels, probabilities, strict=True), key=lambda row: (-row[1], row[0]))
    selected: list[tuple[str, float]] = []
    cumulative = 0.0
    for row in ordered:
        selected.append(row)
        cumulative += row[1]
        if cumulative + 1e-15 >= exact_mass:
            break
    return tuple(selected)
