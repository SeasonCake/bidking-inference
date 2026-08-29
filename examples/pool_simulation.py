#!/usr/bin/env python3
"""Compare synthetic weighted pool draws with and without replacement."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import PoolItem, simulate_pool  # noqa: E402


def main() -> int:
    items = (
        PoolItem("a", 40, 4.0, "common"),
        PoolItem("b", 80, 2.0, "common"),
        PoolItem("c", 200, 1.0, "rare"),
        PoolItem("d", 320, 0.5, "rare"),
    )
    with_replacement = simulate_pool(
        items, draws_per_trial=3, trials=1000, seed=23, replacement=True
    )
    without_replacement = simulate_pool(
        items, draws_per_trial=3, trials=1000, seed=23, replacement=False
    )
    print(
        json.dumps(
            {
                "synthetic": True,
                "with_replacement": asdict(with_replacement),
                "without_replacement": asdict(without_replacement),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
