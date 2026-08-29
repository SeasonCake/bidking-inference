#!/usr/bin/env python3
"""Filter synthetic hypotheses with inclusive interval evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import Evidence, Hypothesis, Interval, filter_hypotheses  # noqa: E402


def main() -> int:
    evidence = Evidence(total_count=Interval(8, 12), total_value=Interval(700, 1300))
    hypotheses = (
        Hypothesis("too-small", 7, 650, 0.2),
        Hypothesis("balanced", 10, 1000, 0.5),
        Hypothesis("upper-edge", 12, 1300, 0.3),
    )
    accepted, rejected = filter_hypotheses(hypotheses, evidence)
    print(
        json.dumps(
            {
                "synthetic": True,
                "accepted": [row.hypothesis.label for row in accepted],
                "rejected": {
                    row.hypothesis.label: list(row.reasons) for row in rejected
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
