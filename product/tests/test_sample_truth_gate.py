from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "src", "hero_ref/src"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from sample_truth_gate import (  # noqa: E402
    _revealed_lower_bound_mismatches,
    compare_claim,
    extract_exact_claims,
    validate_events,
)


TRUTH = {
    "raw_item_count": 10,
    "collectible_count": 8,
    "total_cells": 23,
    "total_value": 137_155,
    "counts": {1: 1, 2: 2, 3: 1, 4: 1, 5: 3},
    "cells": {1: 1, 2: 3, 3: 2, 4: 4, 5: 13},
    "values": {1: 100, 2: 600, 3: 1_455, 4: 15_000, 5: 120_000},
    "cell_size_counts": {1: 1, 2: 1, 3: 1, 4: 1, 6: 4},
    "cell_size_values": {1: 100, 2: 1_455, 3: 600, 4: 15_000, 6: 120_000},
    "max_quality": 5,
    "max_item_value": 40_000,
    "max_item_cells": 6,
}


def _claim(metric: str, value: float, qualities: list[int] | None) -> dict:
    return {
        "source": "test",
        "metric": metric,
        "value": value,
        "qualities": qualities,
        "round": 1,
    }


def test_total_count_keeps_raw_title_tolerance_but_quality_count_does_not() -> None:
    assert compare_claim(_claim("count", 8, None), TRUTH)["matched"]
    assert compare_claim(_claim("count", 10, None), TRUTH)["matched"]
    assert not compare_claim(_claim("count", 10, [5]), TRUTH)["matched"]


def test_avg_cells_uses_game_display_semantics_and_rejects_neighbor_grid() -> None:
    assert compare_claim(_claim("avg_cells", 4.33, [5]), TRUTH)["matched"]
    assert not compare_claim(_claim("avg_cells", 4.0, [5]), TRUTH)["matched"]


def test_value_sum_and_average_value_are_checked_as_siblings() -> None:
    assert compare_claim(_claim("value_sum", 120_000, [5]), TRUTH)["matched"]
    assert compare_claim(_claim("avg_value", 40_000, [5]), TRUTH)["matched"]
    assert not compare_claim(_claim("avg_value", 40_001, [5]), TRUTH)["matched"]


def test_green_actions_and_white_green_average_keep_distinct_truth_scopes() -> None:
    actions = (
        SimpleNamespace(action_id=100104, result=3, observed_items=()),
        SimpleNamespace(action_id=100110, result=4 / 3, observed_items=()),
        SimpleNamespace(action_id=100116, result=2, observed_items=()),
        SimpleNamespace(action_id=100122, result=600, observed_items=()),
    )
    events = SimpleNamespace(
        states=(
            SimpleNamespace(
                round_index=3,
                public_infos=(),
                action_results=actions,
                skill_reveals=(),
            ),
        )
    )

    claims = {
        (claim["source"], claim["metric"]): claim["qualities"]
        for claim in extract_exact_claims(events, tables=SimpleNamespace(items={}))
    }
    assert claims[("action:100104", "cells")] == [1, 2]
    assert claims[("action:100116", "count")] == [2]
    assert claims[("action:100122", "value_sum")] == [2]
    assert claims[("action:100110", "avg_cells")] == [1, 2]


def test_partial_reveals_are_lower_bounds_not_exact_counts() -> None:
    lower = {
        "counts": {5: 2, 6: 2},
        "cells": {5: 7},
        "values": {5: 100_000},
    }
    truth = {
        "counts": {5: 3, 6: 1},
        "cells": {5: 13},
        "values": {5: 120_000},
    }
    mismatches = _revealed_lower_bound_mismatches(lower, truth)
    assert [row["reason"] for row in mismatches] == [
        "revealed_q6_count_lower_bound_exceeds_settlement"
    ]


def test_size_average_and_global_item_claims_are_checked() -> None:
    by_size = _claim("avg_value_by_cells", 15_000, None)
    by_size["cell_size"] = 4
    assert compare_claim(by_size, TRUTH)["matched"]
    assert compare_claim(_claim("max_quality", 5, None), TRUTH)["matched"]
    assert not compare_claim(_claim("max_item_cells", 5, None), TRUTH)["matched"]

    float32_wire = _claim("avg_value_by_cells", 68_192.3671875, None)
    float32_wire["cell_size"] = 1
    float32_truth = {**TRUTH, "cell_size_counts": {1: 19}, "cell_size_values": {1: 1_295_655}}
    assert compare_claim(float32_wire, float32_truth)["matched"]


BAD_CONTROL = (
    ROOT
    / "data/samples/fatbeans"
    / "fatbeans_valid_ahmed_2303_5rounds_2303_1469222822562941_0166.json"
)
GOOD_CONTROL = (
    ROOT
    / "data/samples/fatbeans"
    / "fatbeans_valid_ahmed_2101_1rounds_2101_1425860542745996_0003.json"
)


@pytest.mark.skipif(not BAD_CONTROL.is_file(), reason="gitignored local sample is unavailable")
def test_known_incomplete_settlement_is_a_positive_control() -> None:
    result = validate_events(parse_fatbeans_capture(BAD_CONTROL))
    assert result["verdict"] == "INVALID"
    assert "count_mismatch" in result["reasons"]


@pytest.mark.skipif(not GOOD_CONTROL.is_file(), reason="gitignored local sample is unavailable")
def test_known_complete_settlement_is_a_negative_control() -> None:
    result = validate_events(parse_fatbeans_capture(GOOD_CONTROL))
    assert result["verdict"] == "valid"
    assert result["claims_present"] >= 3
    assert result["claims_matched"] == result["claims_present"]
