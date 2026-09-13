"""Synthetic train/holdout regression metrics; distinct from binary calibration APIs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import fsum, isfinite, sqrt
from typing import Iterable


@dataclass(frozen=True)
class Sample:
    key: str
    split: str
    actual: float
    estimate: float
    phase: str = "prediction"


def checked(samples: Iterable[Sample]) -> tuple[Sample, ...]:
    rows = tuple(samples)
    if not rows or len({r.key for r in rows}) != len(rows):
        raise ValueError("Nonempty samples with unique identities are required")
    for r in rows:
        if r.split not in {"train", "holdout"} or r.phase not in {
            "prediction",
            "reconstruction",
        }:
            raise ValueError("Declare split and prediction/reconstruction phase")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
            for v in [r.actual, r.estimate]
        ):
            raise ValueError("Metrics require finite numeric values")
    return rows


def fit_offset(samples: Iterable[Sample]) -> float:
    rows = checked(samples)
    train = [r for r in rows if r.split == "train" and r.phase == "prediction"]
    if not train:
        raise ValueError(
            "No training predictions; holdout and reconstruction cannot fit the offset"
        )
    return fsum(r.actual - r.estimate for r in train) / len(train)


def metrics(samples: Iterable[Sample], offset: float = 0) -> dict:
    rows = checked(samples)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, (int, float))
        or not isfinite(offset)
    ):
        raise ValueError("Offset must be finite")
    if len({r.split for r in rows}) != 1 or len({r.phase for r in rows}) != 1:
        raise ValueError(
            "Report train/holdout and prediction/reconstruction separately"
        )
    errors = [r.estimate + offset - r.actual for r in rows]
    ape = [
        abs(error / r.actual)
        for r, error in zip(rows, errors, strict=True)
        if r.actual != 0
    ]
    total = fsum(r.actual for r in rows)
    return {
        "n": len(rows),
        "split": rows[0].split,
        "phase": rows[0].phase,
        "bias": fsum(errors) / len(rows),
        "mae": fsum(abs(e) for e in errors) / len(rows),
        "rmse": sqrt(fsum(e * e for e in errors) / len(rows)),
        "mape": fsum(ape) / len(ape) if ape else None,
        "mape_n": len(ape),
        "zero_actual_excluded_from_mape": len(rows) - len(ape),
        "aggregate_signed_relative_error": fsum(errors) / total if total else None,
    }


def example() -> dict:
    rows = (
        Sample("train-a", "train", 100, 120),
        Sample("train-b", "train", 100, 80),
        Sample("holdout-a", "holdout", 100, 140),
        Sample("holdout-b", "holdout", 100, 60),
    )
    offset = fit_offset(rows)
    return {
        "synthetic": True,
        "fitted_offset": offset,
        "train": metrics([r for r in rows if r.split == "train"], offset),
        "holdout": metrics([r for r in rows if r.split == "holdout"], offset),
    }


if __name__ == "__main__":
    print(json.dumps(example(), indent=2))
