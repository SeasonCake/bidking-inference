"""Fail-closed filtering of discrete hypotheses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Evidence, Hypothesis


@dataclass(frozen=True)
class ConstraintResult:
    hypothesis: Hypothesis
    accepted: bool
    reasons: tuple[str, ...]


def evaluate_hypothesis(hypothesis: Hypothesis, evidence: Evidence) -> ConstraintResult:
    reasons: list[str] = []
    if not evidence.total_count.contains(hypothesis.total_count):
        reasons.append("total_count_outside_interval")
    if not evidence.total_value.contains(hypothesis.total_value):
        reasons.append("total_value_outside_interval")
    if hypothesis.prior == 0:
        reasons.append("zero_prior")
    return ConstraintResult(hypothesis=hypothesis, accepted=not reasons, reasons=tuple(reasons))


def filter_hypotheses(
    hypotheses: Iterable[Hypothesis], evidence: Evidence
) -> tuple[tuple[ConstraintResult, ...], tuple[ConstraintResult, ...]]:
    results = tuple(evaluate_hypothesis(hypothesis, evidence) for hypothesis in hypotheses)
    if not results:
        raise ValueError("at least one hypothesis is required")
    accepted = tuple(result for result in results if result.accepted)
    rejected = tuple(result for result in results if not result.accepted)
    return accepted, rejected
