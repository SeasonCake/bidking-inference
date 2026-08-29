"""Calibration diagnostics for weighted binary probability forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite, log
from typing import Iterable


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class BinaryForecast:
    """One probability forecast, observed outcome, and optional sample weight."""

    probability: float
    outcome: bool
    weight: float = 1.0

    def __post_init__(self) -> None:
        probability = _finite_number(self.probability, "probability")
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        if type(self.outcome) is not bool:
            raise TypeError("outcome must be an exact boolean")
        weight = _finite_number(self.weight, "weight")
        if weight <= 0.0:
            raise ValueError("weight must be positive")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class ReliabilityBin:
    index: int
    lower: float
    upper: float
    forecast_count: int
    total_weight: float
    mean_probability: float
    observed_rate: float
    calibration_gap: float


@dataclass(frozen=True)
class CalibrationReport:
    forecast_count: int
    total_weight: float
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    maximum_calibration_error: float
    bins: tuple[ReliabilityBin, ...]


def assess_binary_calibration(
    forecasts: Iterable[BinaryForecast], *, bins: int = 10
) -> CalibrationReport:
    """Summarize reliability without treating calibration as a product decision.

    Empty bins are omitted. Log loss clips only the logarithm input to keep reports
    finite for exact 0/1 forecasts; Brier score and reliability bins use the original
    probabilities.
    """

    if type(bins) is not int:
        raise TypeError("bins must be an exact integer")
    if not 1 <= bins <= 100:
        raise ValueError("bins must be in [1, 100]")
    values = tuple(forecasts)
    if not values:
        raise ValueError("at least one forecast is required")
    if any(not isinstance(value, BinaryForecast) for value in values):
        raise TypeError("forecasts must contain BinaryForecast values")

    total_weight = fsum(value.weight for value in values)
    brier = fsum(
        value.weight * (value.probability - float(value.outcome)) ** 2
        for value in values
    ) / total_weight
    epsilon = 1e-15
    log_loss = -fsum(
        value.weight
        * (
            log(min(max(value.probability, epsilon), 1.0 - epsilon))
            if value.outcome
            else log(min(max(1.0 - value.probability, epsilon), 1.0 - epsilon))
        )
        for value in values
    ) / total_weight

    grouped: list[list[BinaryForecast]] = [[] for _ in range(bins)]
    for value in values:
        index = min(int(value.probability * bins), bins - 1)
        grouped[index].append(value)

    rows: list[ReliabilityBin] = []
    for index, group in enumerate(grouped):
        if not group:
            continue
        group_weight = fsum(value.weight for value in group)
        mean_probability = fsum(
            value.weight * value.probability for value in group
        ) / group_weight
        observed_rate = fsum(
            value.weight * float(value.outcome) for value in group
        ) / group_weight
        rows.append(
            ReliabilityBin(
                index=index,
                lower=index / bins,
                upper=(index + 1) / bins,
                forecast_count=len(group),
                total_weight=group_weight,
                mean_probability=mean_probability,
                observed_rate=observed_rate,
                calibration_gap=abs(mean_probability - observed_rate),
            )
        )

    expected_error = fsum(
        row.total_weight / total_weight * row.calibration_gap for row in rows
    )
    return CalibrationReport(
        forecast_count=len(values),
        total_weight=total_weight,
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=expected_error,
        maximum_calibration_error=max(row.calibration_gap for row in rows),
        bins=tuple(rows),
    )
