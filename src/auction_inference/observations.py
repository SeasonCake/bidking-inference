"""Composable, domain-neutral observations for discrete candidate scoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias


Scalar: TypeAlias = int | float | str


def _finite_numeric(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def validate_scalar(value: object, name: str) -> Scalar:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{name} must be an integer, finite float, or string")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class ObservationMatch:
    accepted: bool
    log_likelihood: float
    reason: str | None = None


class Observation(Protocol):
    def evaluate(self, value: object) -> ObservationMatch: ...


def _accepted(log_likelihood: float = 0.0) -> ObservationMatch:
    return ObservationMatch(accepted=True, log_likelihood=log_likelihood)


def _rejected(reason: str) -> ObservationMatch:
    return ObservationMatch(accepted=False, log_likelihood=float("-inf"), reason=reason)


@dataclass(frozen=True)
class ExactObservation:
    expected: Scalar

    def __post_init__(self) -> None:
        validate_scalar(self.expected, "expected")

    def evaluate(self, value: object) -> ObservationMatch:
        try:
            actual = validate_scalar(value, "value")
        except (TypeError, ValueError):
            return _rejected("type_mismatch")
        if isinstance(self.expected, str) != isinstance(actual, str):
            return _rejected("exact_mismatch")
        return _accepted() if actual == self.expected else _rejected("exact_mismatch")


@dataclass(frozen=True)
class IntervalObservation:
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        minimum = _finite_numeric(self.minimum, "minimum")
        maximum = _finite_numeric(self.maximum, "maximum")
        if minimum > maximum:
            raise ValueError("minimum cannot exceed maximum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def evaluate(self, value: object) -> ObservationMatch:
        try:
            actual = _finite_numeric(value, "value")
        except (TypeError, ValueError):
            return _rejected("numeric_type_mismatch")
        if self.minimum <= actual <= self.maximum:
            return _accepted()
        return _rejected("outside_interval")


@dataclass(frozen=True)
class ApproximateObservation:
    center: float
    scale: float
    cutoff: float | None = None

    def __post_init__(self) -> None:
        center = _finite_numeric(self.center, "center")
        scale = _finite_numeric(self.scale, "scale")
        if scale <= 0:
            raise ValueError("scale must be positive")
        cutoff = None if self.cutoff is None else _finite_numeric(self.cutoff, "cutoff")
        if cutoff is not None and cutoff <= 0:
            raise ValueError("cutoff must be positive")
        object.__setattr__(self, "center", center)
        object.__setattr__(self, "scale", scale)
        object.__setattr__(self, "cutoff", cutoff)

    def evaluate(self, value: object) -> ObservationMatch:
        try:
            actual = _finite_numeric(value, "value")
        except (TypeError, ValueError):
            return _rejected("numeric_type_mismatch")
        standardized = abs(actual - self.center) / self.scale
        if self.cutoff is not None and standardized > self.cutoff:
            return _rejected("outside_cutoff")
        return _accepted(-0.5 * standardized * standardized)


@dataclass(frozen=True)
class CategoricalObservation:
    likelihoods: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.likelihoods, Mapping) or not self.likelihoods:
            raise ValueError("likelihoods must be a non-empty mapping")
        normalized: dict[str, float] = {}
        for category, likelihood in self.likelihoods.items():
            if not isinstance(category, str) or not category.strip():
                raise ValueError("category names must be non-empty strings")
            weight = _finite_numeric(likelihood, f"likelihoods[{category!r}]")
            if weight <= 0:
                raise ValueError("categorical likelihoods must be positive")
            normalized[category] = weight
        object.__setattr__(self, "likelihoods", MappingProxyType(normalized))

    def evaluate(self, value: object) -> ObservationMatch:
        if not isinstance(value, str):
            return _rejected("categorical_type_mismatch")
        likelihood = self.likelihoods.get(value)
        if likelihood is None:
            return _rejected("category_not_supported")
        return _accepted(log(likelihood))


ObservationType: TypeAlias = (
    ExactObservation | IntervalObservation | ApproximateObservation | CategoricalObservation
)


def observation_from_mapping(value: object) -> ObservationType:
    if type(value) is not dict:
        raise TypeError("observation must be an exact JSON object")
    kind = value.get("kind")
    if kind == "exact" and set(value) == {"kind", "expected"}:
        return ExactObservation(validate_scalar(value["expected"], "expected"))
    if kind == "interval" and set(value) == {"kind", "minimum", "maximum"}:
        return IntervalObservation(value["minimum"], value["maximum"])
    if kind == "approximate" and set(value) in (
        {"kind", "center", "scale"},
        {"kind", "center", "scale", "cutoff"},
    ):
        return ApproximateObservation(
            value["center"], value["scale"], value.get("cutoff")
        )
    if kind == "categorical" and set(value) == {"kind", "likelihoods"}:
        likelihoods = value["likelihoods"]
        if type(likelihoods) is not dict:
            raise TypeError("categorical likelihoods must be an exact JSON object")
        return CategoricalObservation(likelihoods)
    raise ValueError("observation kind or fields are invalid")
