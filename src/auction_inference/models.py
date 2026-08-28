"""Strict public data models with no product-specific schema."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


def strict_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    return value


def finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class Interval:
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        minimum = strict_int(self.minimum, "minimum")
        maximum = strict_int(self.maximum, "maximum")
        if minimum > maximum:
            raise ValueError("minimum cannot exceed maximum")

    def contains(self, value: int) -> bool:
        exact = strict_int(value, "value")
        return self.minimum <= exact <= self.maximum

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], name: str) -> "Interval":
        if set(value) != {"minimum", "maximum"}:
            raise ValueError(f"{name} must contain exactly minimum and maximum")
        return cls(
            minimum=strict_int(value["minimum"], f"{name}.minimum"),
            maximum=strict_int(value["maximum"], f"{name}.maximum"),
        )


@dataclass(frozen=True)
class Evidence:
    total_count: Interval
    total_value: Interval

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Evidence":
        if set(value) != {"total_count", "total_value"}:
            raise ValueError("evidence must contain exactly total_count and total_value")
        return cls(
            total_count=Interval.from_mapping(value["total_count"], "total_count"),
            total_value=Interval.from_mapping(value["total_value"], "total_value"),
        )


@dataclass(frozen=True)
class Hypothesis:
    label: str
    total_count: int
    total_value: int
    prior: float

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label must be a non-empty string")
        count = strict_int(self.total_count, "total_count")
        value = strict_int(self.total_value, "total_value")
        prior = finite_number(self.prior, "prior")
        if count < 0 or value < 0:
            raise ValueError("count and value must be non-negative")
        if prior < 0:
            raise ValueError("prior must be non-negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Hypothesis":
        if set(value) != {"label", "total_count", "total_value", "prior"}:
            raise ValueError("hypothesis has an unexpected or missing field")
        return cls(
            label=value["label"],
            total_count=strict_int(value["total_count"], "total_count"),
            total_value=strict_int(value["total_value"], "total_value"),
            prior=finite_number(value["prior"], "prior"),
        )
