"""Domain-neutral primitives for discrete hidden-inventory inference."""

from .constraints import ConstraintResult, evaluate_hypothesis, filter_hypotheses
from .models import Evidence, Hypothesis, Interval
from .monte_carlo import MonteCarloSummary, simulate_totals
from .posterior import PosteriorRow, PosteriorSummary, infer_posterior

__all__ = [
    "ConstraintResult",
    "Evidence",
    "Hypothesis",
    "Interval",
    "MonteCarloSummary",
    "PosteriorRow",
    "PosteriorSummary",
    "evaluate_hypothesis",
    "filter_hypotheses",
    "infer_posterior",
    "simulate_totals",
]
