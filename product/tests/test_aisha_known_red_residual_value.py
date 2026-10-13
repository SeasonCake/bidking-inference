"""Regression for HeroRefDiag 2550:1741282917733387 R5.

The two catalog-known reds occupy 14 cells and are worth 1,095,880.  Once all
four non-red tiers are fixed, the inferred 116-cell middle grid leaves 24 red
cells.  The old quote priced only the known pair and ignored the extra 10-cell
hidden red residual.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "hero_ref" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ahmad_ref_engine import (  # noqa: E402
    _correlated_q6_known_item_facts,
    run_reference_engine,
)


ITEMS = [
    (1, 2, 11, 4, 2, 1, None, None, 103, 1741282917733969),
    (1, 1, 2, 9, 1, 1, None, None, 18, 1741282917733954),
    (1, 1, 8, 2, 1, 1, None, None, 71, 1741282917733964),
    (6, 12, 4, 1, 3, 4, 1046008, 1003000, 30, 1741282917733947),
    (6, 2, 2, 10, 1, 2, 1096001, 92880, 19, 1741282917733955),
    (2, 1, 1, 6, 1, 1, 1032009, 295, 5, 1741282917733943),
    (2, 1, 6, 8, 1, 1, None, None, 57, 1741282917733959),
    (2, 1, 7, 9, 1, 1, None, None, 68, 1741282917733961),
    (2, 1, 10, 4, 1, 1, None, None, 93, 1741282917733968),
    (2, 1, 8, 1, 1, 1, None, None, 70, 1741282917733963),
    (2, 1, 7, 8, 1, 1, None, None, 67, 1741282917733960),
    (2, 1, 1, 10, 1, 1, None, None, 9, 1741282917733950),
    (2, 1, 12, 3, 1, 1, None, None, 112, 1741282917733972),
    (2, 1, 1, 9, 1, 1, None, None, 8, 1741282917733945),
    (3, 1, 8, 3, 1, 1, 1113002, 711, 72, 1741282917733965),
    (3, 4, 5, 4, 2, 2, None, None, 43, 1741282917733951),
    (3, 9, 13, 1, 3, 3, None, None, 120, 1741282917733975),
    (3, 9, 9, 1, 3, 3, None, None, 80, 1741282917733967),
    (3, 1, 12, 8, 1, 1, None, None, 117, 1741282917733974),
    (3, 4, 1, 7, 2, 2, None, None, 6, 1741282917733944),
    (4, 1, 1, 3, 1, 1, None, None, 2, 1741282917733941),
    (4, 1, 2, 6, 1, 1, None, None, 15, 1741282917733953),
    (4, 6, 4, 9, 2, 3, None, None, 38, 1741282917733949),
    (4, 1, 7, 10, 1, 1, None, None, 69, 1741282917733962),
    (4, 4, 3, 6, 4, 1, None, None, 25, 1741282917733946),
    (4, 8, 1, 4, 2, 4, None, None, 3, 1741282917733942),
    (4, 6, 1, 1, 2, 3, None, None, None, 1741282917733940),
    (4, 6, 7, 4, 2, 3, None, None, 63, 1741282917733958),
    (4, 8, 12, 4, 4, 2, None, None, 113, 1741282917733973),
]


def _snapshot(*, hero: str = "aisha") -> dict:
    minimap_items = [
        {
            "quality": quality,
            "cells": cells,
            "row": row,
            "col": col,
            "width": width,
            "height": height,
            "item_id": item_id,
            "value": value,
            "local_index": local_index,
            "runtime_id": runtime_id,
        }
        for (
            quality,
            cells,
            row,
            col,
            width,
            height,
            item_id,
            value,
            local_index,
            runtime_id,
        ) in ITEMS
    ]
    red_details = [
        {
            "local_index": item[8],
            "runtime_id": item[9],
            "item_id": item[6],
            "quality": item[0],
            "value": item[7],
            "shape_code": int(f"{item[4]}{item[5]}"),
            "cells": item[1],
        }
        for item in ITEMS
        if item[0] == 6
    ]
    return {
        "hero": hero,
        "map_id": 2550,
        "model_map_id": 2510,
        "phase": "bidding",
        "round": 5,
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            "total_count": None,
            "total_cells": None,
            "avg_cells": {},
            "counts": {"q3": 6, "q4": 9},
            "quality_cells": {"q3": 28, "q4": 41, "q5": 10},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "split_avg_cells": {},
            "split_counts": {"green": 9, "white": 3},
            "split_quality_cells": {"green": 9, "white": 4},
        },
        "minimap_grid_items": minimap_items,
        "public_info_rows": [
            {
                "info_id": 200021,
                "map_id": 2550,
                "value": 7.0,
                "revealed_items_detail": red_details,
            }
        ],
        "ui_contract": {
            "constraints": {
                "counts": {
                    "known_quality_counts": {
                        "q1": 3,
                        "q2": 9,
                        "q3": 6,
                        "q4": 9,
                        "q6": 2,
                    }
                }
            }
        },
    }


def _run(snapshot: dict) -> dict:
    return run_reference_engine(snapshot, max_combos=8_000).as_dict()


def _split_red_fact_rows(*, same_runtime: bool) -> list[dict]:
    value_runtime = 1001
    shape_runtime = value_runtime if same_runtime else 2002
    value_local = 11
    shape_local = value_local if same_runtime else 22
    return [
        {
            "info_id": 200021,
            "revealed_items_detail": [
                {
                    "runtime_id": value_runtime,
                    "local_index": value_local,
                    "item_id": 1096001,
                    "quality": 6,
                    "value": 92_880,
                }
            ],
        },
        {
            "info_id": 200021,
            "revealed_items_detail": [
                {
                    "runtime_id": shape_runtime,
                    "local_index": shape_local,
                    "item_id": 1096001,
                    "quality": 6,
                    "shape_code": 12,
                    "cells": 2,
                }
            ],
        },
    ]


def test_q6_known_fact_requires_same_physical_identity() -> None:
    correlated = {"public_info_rows": _split_red_fact_rows(same_runtime=True)}
    disjoint = {"public_info_rows": _split_red_fact_rows(same_runtime=False)}

    assert _correlated_q6_known_item_facts(correlated) == (("local:11", 2, 92_880),)
    assert _correlated_q6_known_item_facts(disjoint) == ()


def test_equal_floor_counts_from_different_runtimes_do_not_price_residual() -> None:
    snapshot = _snapshot()
    snapshot["public_info_rows"] = _split_red_fact_rows(same_runtime=False)
    result = _run(snapshot)

    assert result["evidence"]["quality_value_floor_item_counts"]["q6"] == 1
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 1
    assert result["evidence"]["quality_value_floors"]["q6"] == 92_880.0
    assert result["evidence"]["quality_catalog_min_value_floors"]["q6"] == 92_880.0
    assert result["evidence"]["q6_known_item_facts"] == []
    assert not any(
        note.startswith("aisha_known_red_residual_value:") for note in result["notes"]
    )


def test_known_red_residual_cells_lift_middle_and_high_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BIDKING_DISABLE_AISHA_KNOWN_RED_RESIDUAL_VALUE", raising=False)
    repaired = _run(_snapshot())
    monkeypatch.setenv("BIDKING_DISABLE_AISHA_KNOWN_RED_RESIDUAL_VALUE", "1")
    legacy = _run(_snapshot())

    # The later display-only catalog projection may widen the high arm to 35;
    # pricing remains anchored to the independently inferred 124-cell total (32 red).
    assert legacy["red_cells_range"] == [14, 24, 35]
    assert legacy["red_value_range"] == [529954, 1261796, 1374513]
    assert repaired["red_cells_range"] == legacy["red_cells_range"]
    assert repaired["total_grid_range"] == legacy["total_grid_range"] == [106, 116, 124]
    assert "hard_total_joint_red_cells_conditioned" not in repaired["notes"]
    assert "grid_identity_reconciled:116" in repaired["notes"]
    assert repaired["quality_count_ranges"] == legacy["quality_count_ranges"]
    assert repaired["quality_cells_ranges"] == legacy["quality_cells_ranges"]

    assert repaired["red_value_range"] == [1095880, 1531010, 1879114]
    assert repaired["conservative"] == legacy["conservative"] == 1095880
    assert repaired["balanced"] - legacy["balanced"] == 242293
    assert repaired["aggressive"] - legacy["aggressive"] == 454141
    assert any(
        note.startswith("aisha_known_red_residual_value:cells14/24/32")
        for note in repaired["notes"]
    )
    assert not any(
        note.startswith("aisha_known_red_residual_value:") for note in legacy["notes"]
    )


def test_known_red_residual_lift_is_aisha_only(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot(hero="gabriela")
    monkeypatch.delenv("BIDKING_DISABLE_AISHA_KNOWN_RED_RESIDUAL_VALUE", raising=False)
    unchanged = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv("BIDKING_DISABLE_AISHA_KNOWN_RED_RESIDUAL_VALUE", "1")
    rollback = _run(copy.deepcopy(snapshot))

    assert unchanged == rollback
