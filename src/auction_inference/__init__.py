"""Domain-neutral primitives for discrete hidden-inventory inference."""

from .adapters import CandidateAdapter, MappingCandidateAdapter, adapt_records
from .calibration import (
    BinaryForecast,
    CalibrationReport,
    ReliabilityBin,
    assess_binary_calibration,
)
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
from .sensitivity import (
    DistributionShift,
    EvidenceInfluence,
    ProbabilityDelta,
    compare_distributions,
    rank_evidence_influence,
)

__all__ = [
    "ApproximateObservation",
    "BinaryForecast",
    "CalibrationReport",
    "Candidate",
    "CandidateAdapter",
    "CandidateScore",
    "CategoricalObservation",
    "CategoryMean",
    "ConstraintResult",
    "DiscreteVariable",
    "DistributionShift",
    "Evidence",
    "EvidenceTerm",
    "EvidenceInfluence",
    "ExactObservation",
    "Hypothesis",
    "Interval",
    "IntervalObservation",
    "MappingCandidateAdapter",
    "MonteCarloSummary",
    "ObservationMatch",
    "PoolItem",
    "PoolSimulationSummary",
    "ProbabilityDelta",
    "PosteriorDiagnostics",
    "PosteriorRow",
    "PosteriorSummary",
    "ReliabilityBin",
    "VariableState",
    "WeightedPosteriorRow",
    "WeightedPosteriorSummary",
    "adapt_records",
    "assess_binary_calibration",
    "compare_distributions",
    "enumerate_joint_candidates",
    "evaluate_hypothesis",
    "filter_hypotheses",
    "infer_joint_posterior",
    "infer_posterior",
    "infer_weighted_posterior",
    "observation_from_mapping",
    "posterior_diagnostics",
    "rank_evidence_influence",
    "score_candidate",
    "simulate_pool",
    "simulate_totals",
    "smallest_credible_set",
]
