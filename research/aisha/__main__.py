"""Run with python -m research.aisha; emit deterministic teaching JSON."""

from __future__ import annotations

import json
from pathlib import Path

from . import (
    early_window_example, evaluate_grid_example, live_teaching_snapshot,
    offline_teaching_snapshot, simulate_visibility, toy_warehouse,
)


def demo() -> dict:
    rows = [{"semantic": "total_avg_cells", "value": 4.0, "unit": "cells_per_item"}]
    counts = {"all_items": (20, 25, 30)}
    live = live_teaching_snapshot(rows)
    offline = offline_teaching_snapshot(rows)
    legacy = offline_teaching_snapshot(rows, legacy_omission=True)
    fixture = toy_warehouse()
    summary_path = Path(__file__).with_name("fixtures") / "historical_summary.json"
    return {
        "kind": "research_demo",
        "window_examples": {
            "normal": early_window_example(42),
            "inverted_legacy": early_window_example(58, rollback=True),
            "rescued": early_window_example(58),
            "band_already_rescued": early_window_example(58, fixed_band=(45, 48)),
            "still_unreachable": early_window_example(105),
            "no_grid_target_branch": early_window_example(58, branch="without_grid_target"),
        },
        "replay_contract": {
            "synthetic": True,
            "live": live,
            "offline_fixed": offline,
            "offline_legacy": legacy,
            "legacy_result": evaluate_grid_example(legacy, (60, 100, 140), counts),
            "fixed_result": evaluate_grid_example(offline, (60, 100, 140), counts),
            "already_feasible": evaluate_grid_example(live, (90, 100, 110), counts),
        },
        "visibility_rounds": [simulate_visibility(fixture, r, seed=0) for r in range(1, 6)],
        "compensation_illustration": {
            "synthetic": True,
            "kind": "fictional_error_cancellation",
            "unit": "arbitrary_teaching_money",
            "non_red_value": 300,
            "true_red_count": 2,
            "fictional_red_unit_value": 100,
            "truth": 500,
            "fictional_compensation_multiplier": 1.25,
            "old_count_estimate": 1,
            "old_total_with_compensation": (300 + 1 * 100) * 1.25,
            "corrected_count_with_old_compensation": (300 + 2 * 100) * 1.25,
            "limitation": "Illustrates cancellation only; not the historical quote formula or calibrated constants.",
        },
        "historical_aggregate": json.loads(summary_path.read_text(encoding="utf-8")),
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
