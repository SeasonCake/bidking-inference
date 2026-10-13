"""Aisha 4507 hard-total red-cell regression.

Sanitized from HeroRefDiag-20260727-221941-4507_9000000000000030.  At R4 the
game had disclosed total count/cells, every non-red tier count/cells, and the
gold value/average pair.  The joint engine support was uniquely q6=2/19, but
the later room band reopened the visible range to 2/19/19.
"""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE,
    HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE,
    run_reference_engine,
)


def _snapshot_4507() -> dict:
    source = {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }
    return {
        "created_at": time.time(),
        "hero": "aisha",
        "map_id": 4507,
        "round": 4,
        "phase": "bidding",
        "session_id": "4507:9000000000000030",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": False,
        "skill_reveals": [
            {
                "skill_id": 1001031,
                "hero_id": 103,
                "sort": 25,
                "observed_items": [
                    {
                        "runtime_id": 1,
                        "local_index": 1,
                        "quality": 4,
                        "shape_code": 11,
                    }
                ],
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4507,
                "round": 4,
                "phase": "bidding",
                "session_id": "4507:9000000000000030",
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": source["qualification"],
                "official_distribution_eligible": False,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "info_id": 200015,
                            "semantic": "q5_avg_cells",
                            "kind": "avg_cells",
                            "quality": 5,
                            "value": 5.800000190734863,
                        }
                    ]
                }
            },
            "baseline": {"decision": {}, "posterior": {}},
            "source": {},
        },
        "structured_ref_inputs": {
            "total_count": 48,
            "total_cells": 140,
            "avg_cells": {"q4": 2.3333332538604736},
            "counts": {"q3": 9, "q4": 15},
            "quality_cells": {"q3": 25, "q4": 35},
            "quality_values": {"q5": 218_880},
            "split_counts": {"green": 11, "white": 6},
            "split_quality_cells": {"green": 20, "white": 12},
        },
    }


def _quotes(result: dict) -> list[int]:
    return [result[key] for key in ("conservative", "balanced", "aggressive")]


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4507_hard_total_single_support_stays_exact(max_combos: int) -> None:
    result = run_reference_engine(_snapshot_4507(), max_combos=max_combos).as_dict()

    assert _quotes(result) == [400_710, 750_053, 806_213]
    assert result["red_count_range"] == [2, 2, 2]
    assert result["red_cells_range"] == [19, 19, 19]
    assert result["quality_cells_ranges"]["q6"] == [19, 19, 19]
    assert result["quality_count_cells_candidates"]["q6"] == [
        {"count": 2, "cells": 19, "prob": 1.0}
    ]
    assert f"{HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE}:19cells" in result["notes"]
    assert "red_cells_room_band:2-19" not in result["notes"]


def test_4507_known_quality_residual_quote_transform_has_fresh_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(_snapshot_4507(), max_combos=8_000).as_dict()
    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM", "1")
    rollback = run_reference_engine(_snapshot_4507(), max_combos=8_000).as_dict()

    assert _quotes(default) == [400_710, 750_053, 806_213]
    assert _quotes(rollback) == [267_347, 728_165, 784_325]
    for key in (
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "settlement",
        "warehouse_source",
        "warehouse_source_qualification",
        "official_distribution_eligible",
    ):
        assert default.get(key) == rollback.get(key), key


def test_truncated_singleton_never_activates_complete_support_guard() -> None:
    result = run_reference_engine(_snapshot_4507(), max_combos=1).as_dict()

    assert "combo_cap_hit" in result["notes"]
    assert not any(
        note.startswith(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE)
        for note in result["notes"]
    )
    assert result["red_cells_range"] == [2, 19, 19]
    assert result["quality_cells_ranges"]["q6"] == [2, 19, 19]
    assert "red_cells_room_band:2-19" in result["notes"]
    assert _quotes(result) == [400_710, 750_053, 806_213]


def test_direct_q6_cells_bypasses_single_support_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4507()
    snapshot["structured_ref_inputs"]["quality_cells"]["q6"] = 19
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert default["red_cells_range"] == [19, 19, 19]
    assert not any(
        note.startswith(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE)
        for note in default["notes"]
    )


def test_multiple_red_support_bypasses_single_support_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4507()
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = []
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert len(default["quality_count_cells_candidates"]["q6"]) > 1
    assert not any(
        note.startswith(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE)
        for note in default["notes"]
    )


def test_4507_rollback_restores_only_false_red_cells_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4507()
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert rollback["red_cells_range"] == [2, 19, 19]
    assert rollback["quality_cells_ranges"]["q6"] == [2, 19, 19]
    assert rollback["quality_count_cells_candidates"]["q6"] == [
        {"count": 2, "cells": 19, "prob": 1.0}
    ]
    assert "red_cells_room_band:2-19" in rollback["notes"]
    assert not any(
        note.startswith(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE)
        for note in rollback["notes"]
    )
    assert _quotes(default) == _quotes(rollback)
    for key in (
        "red_count_range",
        "red_value_range",
        "quality_count_ranges",
        "quality_count_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "total_grid_range",
    ):
        assert default[key] == rollback[key]


def test_4507_product_summary_uses_exact_red_cells_without_repricing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4507()
    default = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    monkeypatch.setenv(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE, "1")
    rollback = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert default["red"]["count_range"] == "2 / 2 / 2"
    assert default["red"]["cells_range"] == "19 / 19 / 19"
    assert rollback["red"]["cells_range"] == "2 / 19 / 19"
    assert default["reference"]["conservative"] == "400,710"
    assert default["reference"]["balanced"] == "750,053"
    assert default["reference"]["aggressive"] == "806,213"
    assert default["reference"] == rollback["reference"]


def test_single_support_guard_fails_closed_without_hard_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_4507()
    snapshot["structured_ref_inputs"].pop("total_cells")
    default = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert default == rollback
    assert not any(
        note.startswith(HARD_TOTAL_SINGLE_RED_CELLS_SUPPORT_NOTE)
        for note in default["notes"]
    )
