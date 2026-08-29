"""Distribution-shift and leave-one-evidence-out sensitivity diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isclose, isfinite, log2
from typing import Iterable, Mapping

from .scoring import Candidate, EvidenceTerm, infer_weighted_posterior


@dataclass(frozen=True)
class ProbabilityDelta:
    label: str
    baseline: float
    alternative: float
    delta: float


@dataclass(frozen=True)
class DistributionShift:
    total_variation: float
    jensen_shannon_bits: float
    baseline_top: str
    alternative_top: str
    top_changed: bool
    deltas: tuple[ProbabilityDelta, ...]


@dataclass(frozen=True)
class EvidenceInfluence:
    term_index: int
    field: str
    weight: float
    shift: DistributionShift


def _normalized_distribution(
    probabilities: Mapping[str, float], name: str
) -> dict[str, float]:
    if not isinstance(probabilities, Mapping) or not probabilities:
        raise ValueError(f"{name} must be a non-empty mapping")
    normalized: dict[str, float] = {}
    for label, probability in probabilities.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"{name} labels must be non-empty strings")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise TypeError(f"{name} probabilities must be finite numbers")
        value = float(probability)
        if not isfinite(value) or value < 0.0:
            raise ValueError(f"{name} probabilities must be finite and non-negative")
        normalized[label] = value
    total = fsum(normalized.values())
    if not isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"{name} probabilities must sum to one")
    return {label: value / total for label, value in normalized.items()}


def _top_label(probabilities: Mapping[str, float]) -> str:
    return sorted(probabilities, key=lambda label: (-probabilities[label], label))[0]


def compare_distributions(
    baseline: Mapping[str, float], alternative: Mapping[str, float]
) -> DistributionShift:
    """Compare two normalized labeled distributions over their union of labels."""

    left = _normalized_distribution(baseline, "baseline")
    right = _normalized_distribution(alternative, "alternative")
    labels = sorted(set(left) | set(right))
    deltas = tuple(
        ProbabilityDelta(
            label=label,
            baseline=left.get(label, 0.0),
            alternative=right.get(label, 0.0),
            delta=right.get(label, 0.0) - left.get(label, 0.0),
        )
        for label in labels
    )
    total_variation = 0.5 * fsum(abs(row.delta) for row in deltas)
    jensen_shannon = 0.0
    for row in deltas:
        midpoint = 0.5 * (row.baseline + row.alternative)
        if row.baseline > 0.0:
            jensen_shannon += 0.5 * row.baseline * log2(row.baseline / midpoint)
        if row.alternative > 0.0:
            jensen_shannon += 0.5 * row.alternative * log2(row.alternative / midpoint)
    baseline_top = _top_label(left)
    alternative_top = _top_label(right)
    ordered_deltas = tuple(
        sorted(deltas, key=lambda row: (-abs(row.delta), row.label))
    )
    return DistributionShift(
        total_variation=total_variation,
        jensen_shannon_bits=jensen_shannon,
        baseline_top=baseline_top,
        alternative_top=alternative_top,
        top_changed=baseline_top != alternative_top,
        deltas=ordered_deltas,
    )


def rank_evidence_influence(
    candidates: Iterable[Candidate], terms: Iterable[EvidenceTerm]
) -> tuple[EvidenceInfluence, ...]:
    """Rank evidence terms by leave-one-out posterior total variation.

    At least two terms are required so every leave-one-out arm still has a defined
    evidence contract. The result is diagnostic only and does not choose a model or
    modify priors.
    """

    candidate_values = tuple(candidates)
    term_values = tuple(terms)
    if len(term_values) < 2:
        raise ValueError("at least two evidence terms are required")
    baseline_summary = infer_weighted_posterior(candidate_values, term_values)
    baseline = {row.label: row.probability for row in baseline_summary.rows}
    influences: list[EvidenceInfluence] = []
    for index, term in enumerate(term_values):
        remaining = term_values[:index] + term_values[index + 1 :]
        alternative_summary = infer_weighted_posterior(candidate_values, remaining)
        alternative = {
            row.label: row.probability for row in alternative_summary.rows
        }
        influences.append(
            EvidenceInfluence(
                term_index=index,
                field=term.field,
                weight=term.weight,
                shift=compare_distributions(baseline, alternative),
            )
        )
    return tuple(
        sorted(
            influences,
            key=lambda row: (-row.shift.total_variation, row.term_index),
        )
    )
