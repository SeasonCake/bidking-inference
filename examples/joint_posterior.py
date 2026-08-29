#!/usr/bin/env python3
"""Enumerate bounded joint states and score synthetic categorical evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import (  # noqa: E402
    CategoricalObservation,
    DiscreteVariable,
    EvidenceTerm,
    VariableState,
    infer_joint_posterior,
)


def main() -> int:
    variables = (
        DiscreteVariable("color", (VariableState("red", 0.6), VariableState("blue", 0.4))),
        DiscreteVariable("size", (VariableState("small", 0.7), VariableState("large", 0.3))),
    )
    terms = (
        EvidenceTerm("color", CategoricalObservation({"red": 1.0, "blue": 0.25})),
        EvidenceTerm("size", CategoricalObservation({"small": 0.4, "large": 1.0})),
    )
    summary = infer_joint_posterior(variables, terms)
    print(
        json.dumps(
            {
                "synthetic": True,
                "rows": [
                    {"label": row.label, "probability": row.probability}
                    for row in summary.rows
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
