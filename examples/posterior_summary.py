#!/usr/bin/env python3
"""Normalize priors for synthetic hypotheses that satisfy evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import Evidence, Hypothesis, Interval, infer_posterior  # noqa: E402


def main() -> int:
    evidence = Evidence(total_count=Interval(10, 14), total_value=Interval(900, 1600))
    summary = infer_posterior(
        (
            Hypothesis("outside", 9, 850, 0.2),
            Hypothesis("balanced", 12, 1200, 0.5),
            Hypothesis("dense", 14, 1540, 0.3),
        ),
        evidence,
    )
    print(
        json.dumps(
            {
                "synthetic": True,
                "posterior": {row.label: row.probability for row in summary.rows},
                "expected_count": summary.expected_count,
                "expected_value": summary.expected_value,
                "rejected": [row.hypothesis.label for row in summary.rejected],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
