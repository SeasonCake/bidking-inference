"""Domain-neutral primitives for discrete hidden-inventory inference."""

from .adapters import CandidateAdapter, MappingCandidateAdapter, adapt_records
from .constraints import ConstraintResult, evaluate_hypothesis, filter_hypotheses
from .diagnostics import PosteriorDiagnostics, posterior_diagnostics, smallest_credible_set
from .joint import (
    DiscreteVariable,
    VariableState,
    enumerate_joint_candidates,
    infer_joint_posterior,
)
from .models import Evidence, Hypothesis, Interval
from .monte_carlo import (
    CategoryMean,
    MonteCarloSummary,
    PoolItem,
    PoolSimulationSummary,
    simulate_pool,
    simulate_totals,
)
from .observations import (
    ApproximateObservation,
    CategoricalObservation,
    ExactObservation,
    IntervalObservation,
    ObservationMatch,
    observation_from_mapping,
)
from .posterior import PosteriorRow, PosteriorSummary, infer_posterior
from .scoring import (
    Candidate,
    CandidateScore,
    EvidenceTerm,
    WeightedPosteriorRow,
    WeightedPosteriorSummary,
    infer_weighted_posterior,
    score_candidate,
)

__all__ = [
    "ApproximateObservation",
    "Candidate",
    "CandidateAdapter",
    "CandidateScore",
    "CategoricalObservation",
    "CategoryMean",
    "ConstraintResult",
    "DiscreteVariable",
    "Evidence",
    "EvidenceTerm",
    "ExactObservation",
    "Hypothesis",
    "Interval",
    "IntervalObservation",
    "MappingCandidateAdapter",
    "MonteCarloSummary",
    "ObservationMatch",
    "PoolItem",
    "PoolSimulationSummary",
    "PosteriorDiagnostics",
    "PosteriorRow",
    "PosteriorSummary",
    "VariableState",
    "WeightedPosteriorRow",
    "WeightedPosteriorSummary",
    "adapt_records",
    "enumerate_joint_candidates",
    "evaluate_hypothesis",
    "filter_hypotheses",
    "infer_joint_posterior",
    "infer_posterior",
    "infer_weighted_posterior",
    "observation_from_mapping",
    "posterior_diagnostics",
    "score_candidate",
    "simulate_pool",
    "simulate_totals",
    "smallest_credible_set",
]
