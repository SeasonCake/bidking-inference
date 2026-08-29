"""Bounded enumeration of independent discrete variables into joint candidates."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import isfinite, prod
from typing import Iterable

from .models import strict_int
from .scoring import Candidate, EvidenceTerm, WeightedPosteriorSummary, infer_weighted_posterior


@dataclass(frozen=True)
class VariableState:
    label: str
    prior: float

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("state label must be a non-empty string")
        if isinstance(self.prior, bool) or not isinstance(self.prior, (int, float)):
            raise TypeError("state prior must be a finite non-negative number")
        prior = float(self.prior)
        if not isfinite(prior) or prior < 0:
            raise ValueError("state prior must be finite and non-negative")
        object.__setattr__(self, "prior", prior)


@dataclass(frozen=True)
class DiscreteVariable:
    name: str
    states: tuple[VariableState, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("variable name must be a non-empty string")
        states = tuple(self.states)
        if not states:
            raise ValueError("variable must contain at least one state")
        if any(not isinstance(state, VariableState) for state in states):
            raise TypeError("states must contain VariableState values")
        labels = tuple(state.label for state in states)
        if len(set(labels)) != len(labels):
            raise ValueError("state labels must be unique within a variable")
        if sum(state.prior for state in states) <= 0:
            raise ValueError("state priors must have a positive sum")
        object.__setattr__(self, "states", states)


def enumerate_joint_candidates(
    variables: Iterable[DiscreteVariable], *, max_combinations: int = 10_000
) -> tuple[Candidate, ...]:
    values = tuple(variables)
    if not values:
        raise ValueError("at least one variable is required")
    if any(not isinstance(variable, DiscreteVariable) for variable in values):
        raise TypeError("variables must contain DiscreteVariable values")
    names = tuple(variable.name for variable in values)
    if len(set(names)) != len(names):
        raise ValueError("variable names must be unique")
    limit = strict_int(max_combinations, "max_combinations")
    if limit <= 0:
        raise ValueError("max_combinations must be positive")
    combinations = prod(len(variable.states) for variable in values)
    if combinations > limit:
        raise ValueError(
            f"joint state count {combinations} exceeds max_combinations={limit}"
        )
    candidates: list[Candidate] = []
    for state_values in product(*(variable.states for variable in values)):
        attributes = {
            variable.name: state.label
            for variable, state in zip(values, state_values, strict=True)
        }
        label = "|".join(f"{name}={attributes[name]}" for name in names)
        prior = prod(state.prior for state in state_values)
        candidates.append(Candidate(label=label, attributes=attributes, prior=prior))
    return tuple(candidates)


def infer_joint_posterior(
    variables: Iterable[DiscreteVariable],
    terms: Iterable[EvidenceTerm],
    *,
    max_combinations: int = 10_000,
) -> WeightedPosteriorSummary:
    candidates = enumerate_joint_candidates(variables, max_combinations=max_combinations)
    return infer_weighted_posterior(candidates, terms)
