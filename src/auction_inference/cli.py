"""Fixture-driven command-line demonstration."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .diagnostics import posterior_diagnostics, smallest_credible_set
from .models import Evidence, Hypothesis, strict_int
from .monte_carlo import simulate_totals
from .posterior import infer_posterior
from .scoring import Candidate, EvidenceTerm, infer_weighted_posterior


def evaluate_weighted_document(document: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {"synthetic", "mode", "candidates", "evidence", "credible_mass"}
    required_fields = {"synthetic", "mode", "candidates", "evidence"}
    if not required_fields.issubset(document) or not set(document).issubset(allowed_fields):
        raise ValueError("weighted document has unexpected or missing fields")
    candidates_value = document["candidates"]
    evidence_value = document["evidence"]
    if type(candidates_value) is not list or type(evidence_value) is not list:
        raise TypeError("candidates and evidence must be JSON arrays")
    candidates = tuple(Candidate.from_mapping(value) for value in candidates_value)
    terms = tuple(EvidenceTerm.from_mapping(value) for value in evidence_value)
    posterior = infer_weighted_posterior(candidates, terms)
    probabilities = tuple(row.probability for row in posterior.rows)
    diagnostics = posterior_diagnostics(probabilities)
    credible_mass = document.get("credible_mass", 0.95)
    credible = smallest_credible_set(
        ((row.label, row.probability) for row in posterior.rows), mass=credible_mass
    )
    return {
        "synthetic": True,
        "mode": "weighted",
        "posterior": {
            "rows": [
                {
                    "label": row.label,
                    "probability": row.probability,
                    "attributes": dict(row.attributes),
                }
                for row in posterior.rows
            ],
            "rejected": [
                {"label": score.candidate.label, "reasons": list(score.reasons)}
                for score in posterior.rejected
            ],
            "diagnostics": asdict(diagnostics),
            "credible_mass": credible_mass,
            "credible_set": [
                {"label": label, "probability": probability}
                for label, probability in credible
            ],
        },
    }


def evaluate_document(document: dict[str, Any]) -> dict[str, Any]:
    if type(document) is not dict:
        raise TypeError("input must be a JSON object")
    if document.get("synthetic") is not True:
        raise ValueError("input must explicitly declare synthetic=true")
    mode = document.get("mode")
    if mode == "weighted":
        return evaluate_weighted_document(document)
    if mode is not None:
        raise ValueError("unsupported mode")
    evidence = Evidence.from_mapping(document["evidence"])
    hypotheses = tuple(Hypothesis.from_mapping(value) for value in document["hypotheses"])
    posterior = infer_posterior(hypotheses, evidence)
    simulation = document["monte_carlo"]
    monte_carlo = simulate_totals(
        simulation["values"],
        simulation["probabilities"],
        draws_per_trial=strict_int(simulation["draws_per_trial"], "draws_per_trial"),
        trials=strict_int(simulation["trials"], "trials"),
        seed=strict_int(simulation["seed"], "seed"),
    )
    return {
        "synthetic": True,
        "posterior": {
            "rows": [asdict(row) for row in posterior.rows],
            "expected_count": posterior.expected_count,
            "expected_value": posterior.expected_value,
            "rejected": [
                {"label": result.hypothesis.label, "reasons": list(result.reasons)}
                for result in posterior.rejected
            ],
        },
        "monte_carlo": asdict(monte_carlo),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a synthetic discrete auction fixture.")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.fixture.read_text(encoding="utf-8"))
    print(json.dumps(evaluate_document(document), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
