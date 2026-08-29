#!/usr/bin/env python3
"""Measure synthetic forecast calibration and evidence influence."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import (  # noqa: E402
    ApproximateObservation,
    BinaryForecast,
    Candidate,
    EvidenceTerm,
    assess_binary_calibration,
    rank_evidence_influence,
)


def main() -> int:
    calibration = assess_binary_calibration(
        (
            BinaryForecast(0.15, False),
            BinaryForecast(0.35, False),
            BinaryForecast(0.70, True),
            BinaryForecast(0.90, True),
        ),
        bins=4,
    )
    influence = rank_evidence_influence(
        (
            Candidate("compact", {"count": 8, "value": 900}, 0.4),
            Candidate("balanced", {"count": 12, "value": 1250}, 0.4),
            Candidate("dense", {"count": 16, "value": 1700}, 0.2),
        ),
        (
            EvidenceTerm("count", ApproximateObservation(11, 2), weight=1.5),
            EvidenceTerm("value", ApproximateObservation(1300, 250)),
        ),
    )
    print(
        json.dumps(
            {
                "synthetic": True,
                "calibration": {
                    "brier_score": calibration.brier_score,
                    "expected_calibration_error": (
                        calibration.expected_calibration_error
                    ),
                },
                "evidence_influence": [
                    {
                        "field": row.field,
                        "total_variation": row.shift.total_variation,
                        "top_changed": row.shift.top_changed,
                    }
                    for row in influence
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
