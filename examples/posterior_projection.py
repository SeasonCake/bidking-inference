"""Full marginal versus truncated joint mass, using an entirely synthetic posterior."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import fsum, isclose, isfinite
from pathlib import Path
import sys
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from auction_inference.posterior import normalize_weights  # noqa: E402
from auction_inference.scoring import Candidate, EvidenceTerm, infer_weighted_posterior  # noqa: E402
from auction_inference.observations import ExactObservation  # noqa: E402


@dataclass(frozen=True)
class Projection:
    rows: tuple[tuple[str, float], ...]
    other: float


def projection(
    distribution: Mapping[str, float], limit: int | None = None
) -> Projection:
    if not distribution or any(type(k) is not str for k in distribution):
        raise ValueError("A labeled distribution is required")
    values = tuple(distribution.values())
    if any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not isfinite(v)
        or v < 0
        for v in values
    ):
        raise ValueError("Probabilities must be finite nonnegative numbers")
    if not isclose(fsum(values), 1.0, abs_tol=1e-12, rel_tol=0):
        raise ValueError(
            "Supply one complete probability distribution, not mixed tail masses"
        )
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Display limit must be a positive integer")
    ordered = tuple(sorted(distribution.items(), key=lambda row: (-row[1], row[0])))
    shown = ordered if limit is None else ordered[:limit]
    other = fsum(probability for _, probability in ordered[len(shown) :])
    return Projection(shown, other)


def marginal(
    candidates: tuple[Candidate, ...], probabilities: tuple[float, ...], field: str
) -> dict[str, float]:
    if len(candidates) != len(probabilities) or not candidates:
        raise ValueError("Candidates and complete probabilities must align")
    # Validate total mass before projecting; never renormalize truncated joint rows.
    projection({str(i): p for i, p in enumerate(probabilities)})
    bins: dict[str, list[float]] = {}
    label_types: dict[str, type] = {}
    for candidate, probability in zip(candidates, probabilities, strict=True):
        if field not in candidate.attributes:
            raise ValueError("Marginal field is missing")
        label = str(candidate.attributes[field])
        actual_type = type(candidate.attributes[field])
        if label in label_types and label_types[label] is not actual_type:
            raise ValueError("Display labels would merge distinct scalar types")
        label_types[label] = actual_type
        bins.setdefault(label, []).append(probability)
    return {label: fsum(values) for label, values in bins.items()}


def views(value: Projection) -> dict:
    """Main and detail consume the same ordered projection; neither sorts independently."""
    return {"main": value.rows[0][0], "details": list(value.rows), "other": value.other}


def count_distribution(
    candidates: tuple[Candidate, ...], observed: int | None
) -> dict[str, float]:
    if observed is None:
        return marginal(
            candidates, normalize_weights(c.prior for c in candidates), "count"
        )
    if type(observed) is not int or observed < 0:
        raise ValueError("Observed count must be a nonnegative integer or unknown")
    posterior = infer_weighted_posterior(
        candidates, [EvidenceTerm("count", ExactObservation(observed))]
    )
    selected = tuple(
        Candidate(row.label, row.attributes, row.probability) for row in posterior.rows
    )
    return marginal(selected, tuple(row.probability for row in posterior.rows), "count")


def example() -> dict:
    candidates = tuple(
        Candidate(f"cell-{i}", {"count": count, "shape": shape}, weight)
        for i, (count, shape, weight) in enumerate(
            [
                (0, "a", 0.35),
                (0, "b", 0.25),
                (1, "a", 0.20),
                (1, "b", 0.10),
                (2, "a", 0.06),
                (2, "b", 0.04),
            ]
        )
    )
    probabilities = normalize_weights(c.prior for c in candidates)
    joint = projection(
        {c.label: p for c, p in zip(candidates, probabilities, strict=True)}, 2
    )
    full = projection(marginal(candidates, probabilities, "count"))
    trimmed = projection(marginal(candidates, probabilities, "count"), 2)
    return {
        "synthetic": True,
        "joint_top_two": asdict(joint),
        "full_marginal": views(full),
        "truncated_marginal": views(trimmed),
        "invalid_mixed_mass": 1.0 + joint.other,
        "observed_zero": count_distribution(candidates, 0),
    }


if __name__ == "__main__":
    print(json.dumps(example(), indent=2))
