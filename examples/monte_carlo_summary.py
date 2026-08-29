#!/usr/bin/env python3
"""Produce a reproducible summary from a synthetic discrete value pool."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import simulate_totals  # noqa: E402


def main() -> int:
    summary = simulate_totals(
        (40, 100, 240),
        (0.55, 0.30, 0.15),
        draws_per_trial=10,
        trials=1000,
        seed=17,
    )
    print(json.dumps({"synthetic": True, "summary": asdict(summary)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
