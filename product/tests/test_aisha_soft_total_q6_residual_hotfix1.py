from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import (  # noqa: E402
    AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_DISABLE,
    AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


def _minimap_items() -> list[dict]:
    """Reduced geometry from 2601:1758737621663884; no account/session data."""
    rows = (
        (6, 4, 3, 7, 2, 2, 26, 1086005, 362_400),
        (6, 1, 9, 8, 1, 1, 87, 1086006, 68_800),
        (2, 2, 14, 3, 1, 2, 132, None, None),
        (2, 1, 7, 6, 1, 1, 65, None, None),
        (3, 1, 16, 6, 1, 1, 155, None, None),
        (3, 1, 16, 7, 1, 1, 156, None, None),
        (3, 2, 11, 6, 2, 1, 105, None, None),
        (3, 6, 12, 4, 2, 3, 113, None, None),
        (3, 1, 3, 10, 1, 1, 29, None, None),
        (3, 1, 7, 10, 1, 1, 69, None, None),
        (3, 1, 10, 8, 1, 1, 97, None, None),
        (3, 1, 18, 5, 1, 1, 174, None, None),
        (3, 1, 1, 10, 1, 1, 9, None, None),
        (3, 4, 9, 6, 2, 2, 85, None, None),
        (3, 1, 3, 9, 1, 1, 28, None, None),
        (3, 4, 1, 7, 2, 2, 6, None, None),
        (3, 9, 1, 4, 3, 3, 3, None, None),
        (3, 9, 4, 1, 3, 3, 30, None, None),
        (3, 2, 4, 9, 2, 1, 38, None, None),
        (3, 4, 17, 8, 2, 2, 167, None, None),
        (3, 5, 17, 3, 5, 1, 162, None, None),
        (4, 4, 18, 3, 2, 2, 172, None, None),
        (4, 2, 16, 10, 1, 2, 159, None, None),
        (4, 4, 16, 1, 2, 2, 150, None, None),
        (4, 8, 19, 5, 4, 2, 184, None, None),
        (4, 4, 15, 8, 2, 2, 147, None, None),
        (4, 4, 5, 9, 2, 2, 48, None, None),
        (4, 1, 10, 10, 1, 1, 99, None, None),
    )
    return [
        {
            "quality": quality,
            "cells": cells,
            "row": row,
            "col": col,
            "width": width,
            "height": height,
            "local_index": local_index,
            "item_id": item_id,
            "value": value,
        }
        for quality, cells, row, col, width, height, local_index, item_id, value in rows
    ]


def _snapshot(*, hero: str = "aisha", include_red_facts: bool = True) -> dict:
    items = _minimap_items()
    red_facts = [
        {**item, "runtime_id": index + 1}
        for index, item in enumerate(items)
        if item["quality"] == 6
    ]
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2601,
                "phase": "bidding",
                "round": 4,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_cells": 189,
            "fixed_counts": {"q1": 2, "q3": 17, "q4": 7, "q5": 10},
            "quality_cells": {"q3": 53, "q4": 27, "q5": 66},
            "avg_cells": {"q5": 6.599999904632568},
        },
        "minimap_grid_items": items,
        "public_info_rows": (
            [{"info_id": 200001, "revealed_items_detail": red_facts}]
            if include_red_facts
            else []
        ),
    }


def test_2601_four_hard_siblings_do_not_let_q6_prior_blank_soft_total_arm() -> None:
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_snapshot()),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 1
    assert result["quality_count_ranges"] == {
        "q1": [2, 2, 2],
        "q3": [17, 17, 17],
        "q4": [7, 7, 7],
        "q5": [10, 10, 10],
        "q6": [18, 18, 18],
    }
    assert result["total_grid_range"] == [189, 189, 189]
    assert all(result[key] is not None for key in ("conservative", "balanced", "aggressive"))
    assert AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_NOTE in result["notes"]


def test_2601_soft_total_q6_residual_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_DISABLE, "1")
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_snapshot()),
        max_combos=50_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_NOTE not in result["notes"]


@pytest.mark.parametrize(
    ("hero", "include_red_facts"),
    (("ahmed", True), ("aisha", False)),
)
def test_soft_total_q6_residual_retry_requires_aisha_and_observed_red(
    hero: str,
    include_red_facts: bool,
) -> None:
    result = run_reference_engine(
        prepare_reference_engine_snapshot(
            _snapshot(hero=hero, include_red_facts=include_red_facts)
        ),
        max_combos=50_000,
    ).as_dict()

    assert AISHA_Q6_SOFT_TOTAL_RESIDUAL_ANTILOCK_NOTE not in result["notes"]
