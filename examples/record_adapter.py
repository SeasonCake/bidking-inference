#!/usr/bin/env python3
"""Adapt public mappings into generic candidates before inference."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import (  # noqa: E402
    EvidenceTerm,
    IntervalObservation,
    MappingCandidateAdapter,
    adapt_records,
    infer_weighted_posterior,
)


def main() -> int:
    records = (
        {"name": "alpha", "weight": 0.4, "count": 8, "score": 700},
        {"name": "beta", "weight": 0.6, "count": 12, "score": 1100},
    )
    adapter = MappingCandidateAdapter("name", "weight", ("count", "score"))
    candidates = adapt_records(records, adapter)
    summary = infer_weighted_posterior(
        candidates,
        (EvidenceTerm("count", IntervalObservation(10, 14)),),
    )
    print(
        json.dumps(
            {
                "synthetic": True,
                "accepted": [row.label for row in summary.rows],
                "rejected": [score.candidate.label for score in summary.rejected],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
