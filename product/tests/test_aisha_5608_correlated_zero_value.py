"""Regression for 5608 R4 gold/red complement arms being flattened to zero value."""

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

import ahmad_ref_engine as _engine  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    _red_display_ranges,
    _red_display_value_range,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AISHA_Q5Q6_CORRELATED_COUNT_GUARD_NOTE,
    AISHA_Q5Q6_CORRELATED_GRID_FLOOR_NOTE,
    AISHA_Q5Q6_CORRELATED_PHYSICAL_GUARD_DISABLE,
    UPDATE606_RUNTIME_PACKAGE_GENERATION,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


ROUTE_STABLE_MAP_ID = 2508
CHANGED_ROUTE_MAP_ID = 5608

# The normalized route-2508 slice from the approved update606 integration inputs.  Keep this
# explicit: the test must exercise both the approved price/prior path and the package-input-absent
# fallback path regardless of which ignored files happen to exist beside the checkout.
_APPROVED_UPDATE606_2508_STATIC_CONTEXT = {
    "drop_weights": {},
    "nest_prices": {
        "2048": [203.48, 594.86, 2202.29, 6577.82, 33134.53, 195400.47],
    },
    "map_nests": {2508: ("2048", "生物实验室样本库")},
    "drop_table_priors": {
        2508: {
            "quality_probs": {
                "q1": 0.2947376,
                "q3": 0.2526319,
                "q4": 0.2526315,
                "q5": 0.1263156,
                "q6": 0.0736834,
            },
            "quality_values": {
                "q1": 549,
                "q3": 2255,
                "q4": 6712,
                "q5": 30906,
                "q6": 188333,
            },
        },
    },
}
_ABSENT_PACKAGE_INPUT_CONTEXT = {
    "drop_weights": {},
    "nest_prices": {},
    "map_nests": {},
    "drop_table_priors": {},
}
_PACKAGE_INPUT_CONTEXTS = (
    pytest.param(
        _APPROVED_UPDATE606_2508_STATIC_CONTEXT,
        ("nest_price:2048", "drop_table_prob:2508"),
        id=f"approved-{UPDATE606_RUNTIME_PACKAGE_GENERATION}",
    ),
    pytest.param(
        _ABSENT_PACKAGE_INPUT_CONTEXT,
        ("fallback_default_price", "fallback_uniform_prob"),
        id="package-input-absent",
    ),
)


def _item(quality: int, local_index: int, shape: int) -> dict:
    width, height = divmod(int(shape), 10)
    return {
        "quality": quality,
        "local_index": local_index,
        "row": local_index // 10 + 1,
        "col": local_index % 10 + 1,
        "width": width,
        "height": height,
        "cells": width * height,
        "shape_key": str(shape),
    }


def _snapshot(*, map_id: int = ROUTE_STABLE_MAP_ID) -> dict:
    # Sanitized sort=25 state from 5608:1741283120127876.  White/green/blue/purple are fully
    # visible (33 items, 62 cells); total=37 and gold avg=6.5 leave the joint count states
    # (q5=2,q6=2) and (q5=4,q6=0).
    item_specs = {
        1: [(73, 11), (59, 11), (3, 11), (31, 11), (32, 11), (8, 12), (68, 11)],
        2: [(6, 21), (4, 11), (42, 11), (52, 12), (40, 22), (77, 11),
            (9, 11), (65, 22), (48, 12)],
        3: [(30, 11), (15, 22), (5, 11), (34, 11), (36, 11), (53, 11),
            (72, 11), (17, 11), (27, 22), (63, 21), (60, 22)],
        4: [(44, 42), (19, 12), (67, 11), (69, 11), (74, 11), (39, 12)],
    }
    items = [
        _item(quality, local_index, shape)
        for quality, rows in item_specs.items()
        for local_index, shape in rows
    ]
    return {
        "session_id": f"{map_id}:1741283120127876",
        "hero": "aisha",
        "map_id": map_id,
        "phase": "bidding",
        "round": 4,
        "skill_reveal_rows": [
            {
                "sort": 25,
                "skill_id": 1001031,
                "hero_id": 103,
                "revealed_items": 6,
                "revealed_items_detail": [
                    {"quality": 4, "local_index": 44, "shape_code": 42},
                ],
            },
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": map_id,
                "phase": "bidding",
                "round": 4,
            },
        },
        "structured_ref_inputs": {
            "total_count": 37,
            "fixed_counts": {"q1": 16, "q3": 11, "q4": 6},
            "avg_cells": {
                "q1": 26 / 16,
                "q3": 21 / 11,
                "q4": 2.5,
                "q5": 6.5,
            },
            "quality_cells": {"q1": 26, "q3": 21, "q4": 15},
            "minimap_items": items,
        },
    }


def _install_static_context(
    monkeypatch: pytest.MonkeyPatch,
    static_context: dict,
) -> None:
    frozen_context = copy.deepcopy(static_context)
    monkeypatch.setattr(
        _engine,
        "load_reference_static_data",
        lambda *args, **kwargs: copy.deepcopy(frozen_context),
    )


def _run(
    monkeypatch: pytest.MonkeyPatch | None = None,
    static_context: dict | None = None,
) -> dict:
    if static_context is not None:
        assert monkeypatch is not None
        _install_static_context(monkeypatch, static_context)
    snapshot = prepare_reference_engine_snapshot(_snapshot())
    return run_reference_engine(snapshot, max_combos=8_000).as_dict()


def _assert_correlated_physical_result(result: dict) -> None:
    assert result["combo_count"] == 2
    assert result["quality_count_ranges"]["q5"] == [2, 2, 4]
    assert result["quality_count_ranges"]["q6"] == [0, 2, 2]
    assert result["red_count_range"] == [0, 2, 2]
    assert result["red_cells_range"] == result["quality_cells_ranges"]["q6"]
    assert result["red_cells_range"][0] == 0
    assert result["red_cells_range"][1] >= result["red_count_range"][1] > 0
    assert result["red_cells_range"][2] >= result["red_count_range"][2] > 0
    assert result["red_value_range"][0] == 0
    assert result["red_value_range"][1] > 0
    assert result["red_value_range"][2] >= result["red_value_range"][1]
    assert result["total_grid_range"][0] >= 80
    assert result["total_grid_range"][1] >= 80
    assert result["total_grid_range"][2] >= 88
    assert any(
        str(note).startswith(AISHA_Q5Q6_CORRELATED_GRID_FLOOR_NOTE)
        for note in result["notes"]
    )
    assert AISHA_Q5Q6_CORRELATED_COUNT_GUARD_NOTE in result["notes"]

    display_counts, display_cells = _red_display_ranges(result)
    display_values = _red_display_value_range(result, display_counts)
    assert display_counts == result["red_count_range"] == [0, 2, 2]
    assert display_cells == result["red_cells_range"]
    assert display_values == result["red_value_range"]
    assert {
        (candidate["count"], candidate["cells"])
        for candidate in result["quality_count_cells_candidates"]["q5"]
    } == {(2, 13), (4, 26)}
    assert {
        (candidate["count"], candidate["cells"])
        for candidate in result["quality_count_cells_candidates"]["q6"]
    } == {(0, 0), (2, 5), (2, 6), (2, 7)}


@pytest.mark.parametrize(("static_context", "expected_context_notes"), _PACKAGE_INPUT_CONTEXTS)
def test_route_stable_2508_correlated_gold_red_arms_keep_physical_cells_and_value(
    monkeypatch: pytest.MonkeyPatch,
    static_context: dict,
    expected_context_notes: tuple[str, str],
) -> None:
    result = _run(monkeypatch, static_context)

    _assert_correlated_physical_result(result)
    assert all(note in result["notes"] for note in expected_context_notes)


@pytest.mark.parametrize(("static_context", "expected_context_notes"), _PACKAGE_INPUT_CONTEXTS)
def test_route_stable_2508_correlated_physical_guard_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
    static_context: dict,
    expected_context_notes: tuple[str, str],
) -> None:
    monkeypatch.setenv(AISHA_Q5Q6_CORRELATED_PHYSICAL_GUARD_DISABLE, "1")
    legacy = _run(monkeypatch, static_context)

    assert legacy["total_grid_range"] == [67, 70, 72]
    assert legacy["red_count_range"] == [0, 0, 2]
    assert legacy["red_cells_range"] == [0, 2, 7]
    assert legacy["red_value_range"][:2] == [0, 0]
    assert _red_display_ranges(legacy) == ([0, 0, 2], [0, 2, 7])
    assert AISHA_Q5Q6_CORRELATED_COUNT_GUARD_NOTE not in legacy["notes"]
    assert all(note in legacy["notes"] for note in expected_context_notes)


@pytest.mark.parametrize(
    "static_context",
    (
        pytest.param(
            _APPROVED_UPDATE606_2508_STATIC_CONTEXT,
            id=f"approved-{UPDATE606_RUNTIME_PACKAGE_GENERATION}",
        ),
        pytest.param(_ABSENT_PACKAGE_INPUT_CONTEXT, id="package-input-absent"),
    ),
)
def test_changed_route_5608_dual_pass_api_ui_publishes_no_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    static_context: dict,
) -> None:
    _install_static_context(monkeypatch, static_context)
    snapshot = _snapshot(map_id=CHANGED_ROUTE_MAP_ID)
    snapshot["created_at"] = time.time()
    snapshot["ui_contract"].setdefault("source", {})["created_at"] = time.time()

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "synthetic_5608.json",
    )

    assert summary["reference"]["readiness"] == "no_reachable_combo"
    assert summary["reference"]["source"] == "ref_waiting"
    assert summary["reference"]["decision_range"] == "-"
    assert "ahmad_ref" not in summary
    direct = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=8_000,
    ).as_dict()
    assert direct["notes"] == [
        f"{_engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{_engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:"
        f"{CHANGED_ROUTE_MAP_ID}:authority_input_unavailable"
    ]
