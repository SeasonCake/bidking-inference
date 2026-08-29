"""Generic multi-field likelihood scoring and stable posterior normalization."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, fsum, isfinite, log
from types import MappingProxyType
from typing import Iterable, Mapping

from .observations import (
    ObservationMatch,
    ObservationType,
    Scalar,
    observation_from_mapping,
    validate_scalar,
)


def _finite_non_negative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite non-negative number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True)
class Candidate:
    label: str
    attributes: Mapping[str, Scalar]
    prior: float

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label must be a non-empty string")
        if not isinstance(self.attributes, Mapping) or not self.attributes:
            raise ValueError("attributes must be a non-empty mapping")
        normalized: dict[str, Scalar] = {}
        for name, value in self.attributes.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("attribute names must be non-empty strings")
            normalized[name] = validate_scalar(value, f"attributes[{name!r}]")
        object.__setattr__(self, "attributes", MappingProxyType(normalized))
        object.__setattr__(self, "prior", _finite_non_negative(self.prior, "prior"))

    @classmethod
    def from_mapping(cls, value: object) -> "Candidate":
        if type(value) is not dict or set(value) != {"label", "attributes", "prior"}:
            raise ValueError("candidate must contain exactly label, attributes, and prior")
        attributes = value["attributes"]
        if type(attributes) is not dict:
            raise TypeError("candidate attributes must be an exact JSON object")
        return cls(label=value["label"], attributes=attributes, prior=value["prior"])


@dataclass(frozen=True)
class EvidenceTerm:
    field: str
    observation: ObservationType
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not self.field.strip():
            raise ValueError("field must be a non-empty string")
        if not hasattr(self.observation, "evaluate"):
            raise TypeError("observation must implement evaluate")
        weight = _finite_non_negative(self.weight, "weight")
        if weight <= 0:
            raise ValueError("weight must be positive")
        object.__setattr__(self, "weight", weight)

    @classmethod
    def from_mapping(cls, value: object) -> "EvidenceTerm":
        if type(value) is not dict or set(value) not in (
            {"field", "observation"},
            {"field", "observation", "weight"},
        ):
            raise ValueError("evidence term has invalid fields")
        return cls(
            field=value["field"],
            observation=observation_from_mapping(value["observation"]),
            weight=value.get("weight", 1.0),
        )


@dataclass(frozen=True)
class CandidateScore:
    candidate: Candidate
    accepted: bool
    log_weight: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WeightedPosteriorRow:
    label: str
    probability: float
    attributes: Mapping[str, Scalar]


@dataclass(frozen=True)
class WeightedPosteriorSummary:
    rows: tuple[WeightedPosteriorRow, ...]
    rejected: tuple[CandidateScore, ...]


def score_candidate(candidate: Candidate, terms: Iterable[EvidenceTerm]) -> CandidateScore:
    evidence_terms = tuple(terms)
    if not evidence_terms:
        raise ValueError("at least one evidence term is required")
    reasons: list[str] = []
    log_likelihood = 0.0
    if candidate.prior == 0:
        reasons.append("zero_prior")
    for term in evidence_terms:
        if term.field not in candidate.attributes:
            reasons.append(f"missing_field:{term.field}")
            continue
        match = term.observation.evaluate(candidate.attributes[term.field])
        if not isinstance(match, ObservationMatch):
            raise TypeError("observation evaluate() must return ObservationMatch")
        if not match.accepted:
            reasons.append(f"{term.field}:{match.reason}")
            continue
        if not isfinite(match.log_likelihood):
            raise ValueError("accepted observation log_likelihood must be finite")
        log_likelihood += term.weight * match.log_likelihood
    accepted = not reasons
    return CandidateScore(
        candidate=candidate,
        accepted=accepted,
        log_weight=(log(candidate.prior) + log_likelihood) if accepted else float("-inf"),
        reasons=tuple(reasons),
    )


def infer_weighted_posterior(
    candidates: Iterable[Candidate], terms: Iterable[EvidenceTerm]
) -> WeightedPosteriorSummary:
    candidate_values = tuple(candidates)
    evidence_terms = tuple(terms)
    if not candidate_values:
        raise ValueError("at least one candidate is required")
    if not evidence_terms:
        raise ValueError("at least one evidence term is required")
    labels = tuple(candidate.label for candidate in candidate_values)
    if len(set(labels)) != len(labels):
        raise ValueError("candidate labels must be unique")
    scores = tuple(score_candidate(candidate, evidence_terms) for candidate in candidate_values)
    accepted = tuple(score for score in scores if score.accepted)
    rejected = tuple(score for score in scores if not score.accepted)
    if not accepted:
        raise ValueError("evidence rejects every candidate")
    offset = max(score.log_weight for score in accepted)
    weights = tuple(exp(score.log_weight - offset) for score in accepted)
    total = fsum(weights)
    rows = tuple(
        WeightedPosteriorRow(
            label=score.candidate.label,
            probability=weight / total,
            attributes=score.candidate.attributes,
        )
        for score, weight in zip(accepted, weights, strict=True)
    )
    return WeightedPosteriorSummary(rows=rows, rejected=rejected)
