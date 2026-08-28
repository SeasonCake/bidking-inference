"""Fixture-driven command-line demonstration."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Evidence, Hypothesis, strict_int
from .monte_carlo import simulate_totals
from .posterior import infer_posterior


def evaluate_document(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("synthetic") is not True:
        raise ValueError("input must explicitly declare synthetic=true")
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
