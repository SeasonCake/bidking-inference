from __future__ import annotations

import copy
import importlib.util
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    AISHA_LOW_TIER_RESIDUAL_CENTER_CAP_DISABLE,
    AISHA_SPLIT_PROVENANCE_ANTILOCK_DISABLE,
    AISHA_SPLIT_PROVENANCE_ANTILOCK_NOTE,
    _finalize_total_grid_range,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from layout_depth_policy import apply_shallow_visible_grid_low_floor  # noqa: E402


def _items(quality: int, count: int, *, start: int) -> list[dict[str, int]]:
    return [
        {"local_index": start + offset, "quality": quality, "shape_code": 11}
        for offset in range(count)
    ]


def _aisha_4548_coarse_green_snapshot() -> dict:
    """Desensitized 4548 R1 contract: exact white plus two distinct green samples.

    The normalized q1 floor of nine represents seven exact white items plus two
    physically distinct sampled greens.  The split map itself retains only the
    per-source coarse maximum (one green), matching the live provenance bug.
    """
    return {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "hero_id": 103,
                "map_id": 4548,
                "round": 1,
                "phase": "bidding",
            }
        },
        "structured_ref_inputs": {
            "hero": "aisha",
            "min_counts": {"q1": 9, "q3": 2, "q5": 1},
            "split_counts": {"white": 7},
            "split_quality_cells": {"white": 10},
        },
        "skill_reveals": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "observed_items": _items(1, 7, start=10),
            }
        ],
        "public_info_rows": [
            {
                "info_id": 200026,
                "revealed_items_detail": [
                    {"local_index": 57, "quality": 2},
                ],
            }
        ],
    }


def _aisha_4550_residual_center_snapshot(*, total_count: int | None = None) -> dict:
    structured = {
        "hero": "aisha",
        "total_cells": 144,
        "counts": {"q3": 5, "q4": 15},
        "min_counts": {"q6": 1},
        "quality_cells": {"q3": 10},
        "split_counts": {"white": 5, "green": 10},
        "split_quality_cells": {"white": 5, "green": 19},
    }
    if total_count is not None:
        structured["total_count"] = total_count
    return {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "hero_id": 103,
                "map_id": 4550,
                "round": 3,
                "phase": "bidding",
            }
        },
        "structured_ref_inputs": structured,
    }


def _aisha_4550_shallow_dual_pass_snapshot() -> dict:
    specs = [
        (1, "11", 25, None, None),
        (1, "11", 18, None, None),
        (1, "11", 99, None, None),
        (1, "11", 119, None, None),
        (1, "11", 73, None, None),
        (4, None, 5, None, None),
        (6, None, 94, None, None),
        (3, "21", 7, None, None),
        (2, "11", 9, None, None),
        (2, "31", 142, None, None),
        (2, "12", 53, None, None),
        (2, "11", 70, None, None),
        (2, "11", 125, None, None),
        (2, "11", 43, None, None),
        (2, "11", 3, None, None),
        (2, "11", 83, None, None),
        (2, "22", 28, None, None),
        (2, "22", 1, 1022001, 770),
        (4, "11", 109, 1034004, 2460),
        (3, "12", 128, None, None),
        (3, "22", 97, None, None),
        (3, "11", 82, None, None),
        (3, "11", 37, None, None),
    ]
    shape_size = {
        "11": (1, 1),
        "12": (1, 2),
        "21": (2, 1),
        "22": (2, 2),
        "31": (3, 1),
        None: (1, 1),
    }

    def detail(
        quality: int,
        shape_key: str | None,
        local_index: int,
        item_id: int | None = None,
        value: int | None = None,
    ) -> dict:
        width, height = shape_size[shape_key]
        return {
            "local_index": local_index,
            "runtime_id": 1000 + local_index,
            "item_id": item_id,
            "item_name": "",
            "quality": quality,
            "value": value,
            "shape_code": int(shape_key) if shape_key else None,
            "cells": width * height if item_id else None,
        }

    items = []
    for quality, shape_key, local_index, item_id, value in specs:
        width, height = shape_size[shape_key]
        items.append(
            {
                "quality": quality,
                "runtime_id": 1000 + local_index,
                "item_id": item_id,
                "item_name": "",
                "local_index": local_index,
                "cells": width * height,
                "shape_key": shape_key,
                "row": None,
                "col": None,
                "width": width if shape_key else None,
                "height": height if shape_key else None,
                "value": value,
                "category": (
                    102
                    if item_id == 1022001
                    else 103 if item_id == 1034004 else None
                ),
            }
        )

    occupied: set[tuple[int, int]] = set()
    packed = []
    for item in sorted(copy.deepcopy(items), key=lambda candidate: -candidate["cells"]):
        width = int(item.get("width") or 1)
        height = int(item.get("height") or 1)
        for row in range(1, 26):
            for col in range(1, 12 - width):
                cells = {
                    (row + dy, col + dx)
                    for dy in range(height)
                    for dx in range(width)
                }
                if not cells & occupied:
                    break
            else:
                continue
            break
        occupied.update(cells)
        item.update(
            row=row,
            col=col,
            width=width,
            height=height,
            local_index=(row - 1) * 10 + col - 1,
        )
        packed.append(item)

    assert len(packed) == 23
    assert len(occupied) == 37
    assert max(row for row, _ in occupied) == 4

    skills = []
    for quality, skill_id in ((1, 1001034), (2, 1001033), (3, 1001032)):
        observed = [
            detail(item_quality, shape_key, local_index)
            for item_quality, shape_key, local_index, _, _ in specs
            if item_quality == quality
        ]
        skills.append(
            {"skill_id": skill_id, "hero_id": 103, "observed_items": observed}
        )
    skill_rows = [
        {
            "sort": 17,
            "skill_id": skill["skill_id"],
            "hero_id": 103,
            "revealed_items_detail": copy.deepcopy(skill["observed_items"]),
            "observed_items": copy.deepcopy(skill["observed_items"]),
        }
        for skill in skills
    ]

    minimap_keys = {
        "quality",
        "item_id",
        "cells",
        "shape_key",
        "local_index",
        "row",
        "col",
        "width",
        "height",
        "category",
        "value",
    }
    structured = _aisha_4550_residual_center_snapshot()["structured_ref_inputs"]
    structured.update(
        source="monitor_pre_settlement_field_updates",
        min_counts={},
        minimap_items=[
            {key: value for key, value in item.items() if key in minimap_keys}
            for item in packed
        ],
    )
    context = {
        "hero": "aisha",
        "hero_id": 103,
        "map_id": 4550,
        "round": 3,
        "action_round": 3,
        "observed_round": 2,
        "phase": "bidding",
    }
    return {
        **context,
        "created_at": time.time(),
        "ui_contract": {"context": context},
        "structured_ref_inputs": structured,
        "minimap_grid_items": packed,
        "skill_reveals": skills,
        "skill_reveal_rows": skill_rows,
        "action_send_rows": [
            {"sort": 19, "action_id": 100103},
            {"sort": 11, "action_id": 100129},
            {"sort": 2, "action_id": 100136},
        ],
        "action_result_rows": [
            {
                "sort": 20,
                "action_id": 100103,
                "result": 144,
                "result_field": 14,
                "revealed_items": 0,
                "revealed_items_detail": [],
            },
            {
                "sort": 12,
                "action_id": 100129,
                "result": 5,
                "result_field": 14,
                "revealed_items": 2,
                "revealed_items_detail": [
                    detail(4, "11", 109, 1034004, 2460),
                    detail(2, "22", 1, 1022001, 770),
                ],
            },
            {
                "sort": 3,
                "action_id": 100136,
                "result": None,
                "result_field": None,
                "revealed_items": 4,
                "revealed_items_detail": [
                    detail(4, None, 5),
                    detail(6, None, 94),
                    detail(1, None, 25),
                    detail(3, None, 8),
                ],
            },
        ],
        "public_info_rows": [
            {
                "sort": 17,
                "info_id": 200018,
                "map_id": 4550,
                "value": 15,
                "value_field": 7,
                "revealed_items": 0,
                "revealed_items_detail": [],
            }
        ],
    }


def _live_panel_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location(
        "ahmad_live_panel_server_aisha_no_reachable_hotfix2_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_4548_coarse_green_floor_does_not_complete_exact_q1_lock() -> None:
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_aisha_4548_coarse_green_snapshot()),
        max_combos=2500,
    ).as_dict()

    evidence = result["evidence"]
    assert evidence["fixed_counts"].get("q1") is None
    assert evidence["min_counts"]["q1"] >= 9
    assert result["status"] == "count_prior"
    assert result["combo_count"] == 2500
    assert AISHA_SPLIT_PROVENANCE_ANTILOCK_NOTE in result["notes"]
    assert "split_low_quality_q1_coarse_floor_only" in result["notes"]


def test_4548_provenance_rollback_reproduces_fixed_min_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_SPLIT_PROVENANCE_ANTILOCK_DISABLE, "1")
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_aisha_4548_coarse_green_snapshot()),
        max_combos=2500,
    ).as_dict()

    assert result["evidence"]["fixed_counts"]["q1"] == 8
    assert result["evidence"]["min_counts"]["q1"] == 9
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert AISHA_SPLIT_PROVENANCE_ANTILOCK_NOTE not in result["notes"]


def test_4548_two_exact_skill_splits_still_lock_with_coarse_sample() -> None:
    snapshot = _aisha_4548_coarse_green_snapshot()
    snapshot["structured_ref_inputs"]["split_counts"]["green"] = 2
    snapshot["structured_ref_inputs"]["split_quality_cells"]["green"] = 5
    snapshot["skill_reveals"].append(
        {
            "skill_id": 1001033,
            "hero_id": 103,
            "observed_items": _items(2, 2, start=70),
        }
    )

    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=2500,
    ).as_dict()

    assert result["evidence"]["fixed_counts"]["q1"] == 9
    assert result["status"] == "count_prior"
    assert "aisha_absent_low_quality_q1_lock" in result["notes"]
    assert AISHA_SPLIT_PROVENANCE_ANTILOCK_NOTE not in result["notes"]


def test_4550_low_tier_upper_caps_later_residual_center() -> None:
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_aisha_4550_residual_center_snapshot()),
        max_combos=8000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] > 0
    assert result["total_grid_range"] == [144, 144, 144]
    assert result["quality_count_ranges"]["q1"] == [15, 15, 15]
    assert result["quality_count_ranges"]["q3"] == [5, 5, 5]
    assert result["quality_count_ranges"]["q4"] == [15, 15, 15]
    assert min(result["quality_count_ranges"]["q6"]) >= 1
    assert "aisha_low_tier_count_upper_center:63->51" in result["notes"]
    assert "total_count_prior_center:51" in result["notes"]
    assert not any(
        str(note).startswith("aisha_low_tier_spatial_grid_anchor:")
        for note in result["notes"]
    )


def test_4550_low_tier_upper_rollback_restores_no_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_LOW_TIER_RESIDUAL_CENTER_CAP_DISABLE, "1")
    result = run_reference_engine(
        prepare_reference_engine_snapshot(_aisha_4550_residual_center_snapshot()),
        max_combos=8000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert result["total_grid_range"] == [144, 144, 144]
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert "aisha_low_tier_count_upper_center:63->51" not in result["notes"]
    assert "total_count_prior_center:59" in result["notes"]


@pytest.mark.parametrize(
    ("total_count", "expected_status"),
    ((58, "count_prior"), (59, "no_reachable_combo")),
)
def test_4550_explicit_total_count_remains_hard(
    total_count: int,
    expected_status: str,
) -> None:
    result = run_reference_engine(
        prepare_reference_engine_snapshot(
            _aisha_4550_residual_center_snapshot(total_count=total_count)
        ),
        max_combos=8000,
    ).as_dict()

    assert result["status"] == expected_status
    assert (result["combo_count"] > 0) is (total_count == 58)
    if total_count == 59:
        assert [
            result[key] for key in ("conservative", "balanced", "aggressive")
        ] == [None, None, None]
    assert not any(
        str(note).startswith("aisha_low_tier_count_upper_center:")
        for note in result["notes"]
    )


def test_shallow_visible_floor_does_not_mutate_hard_total() -> None:
    notes = ["action_100103_total_cells"]

    result = apply_shallow_visible_grid_low_floor(
        (144, 144, 144),
        hero_key="aisha",
        round_no=3,
        raw_deepest=4,
        known_cells=37,
        source_notes=notes,
    )

    assert result == (144, 144, 144)
    assert notes == ["action_100103_total_cells"]


def test_public_grid_range_finalizer_orders_unlocked_range() -> None:
    evidence = extract_evidence(
        prepare_reference_engine_snapshot(_aisha_4548_coarse_green_snapshot())
    )

    assert _finalize_total_grid_range(evidence, (144, 76, 82)) == (76, 82, 144)


def test_4550_shallow_layout_preserves_hard_total_quotes_and_api_ui(tmp_path: Path) -> None:
    snapshot = _aisha_4550_shallow_dual_pass_snapshot()
    result = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=8000,
    ).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 43
    assert result["evidence"]["total_grid_target"] == 144
    assert result["total_grid_range"] == [144, 144, 144]
    assert result["total_grid_range"] == sorted(result["total_grid_range"])
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        1012577,
        1671904,
        1881167,
    ]
    assert not any(
        str(note).startswith(
            (
                "aisha_shallow_visible_grid_low_floor:",
                "aisha_low_tier_spatial_grid_anchor:",
            )
        )
        for note in result["notes"]
    )

    summary = _live_panel_module().summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    reference = summary["reference"]
    performance = summary["diagnostics"]["performance"]
    assert reference["total_grid_range"] == "144 / 144 / 144格"
    assert [reference[key] for key in ("conservative", "balanced", "aggressive")] == [
        "1,012,577",
        "1,671,904",
        "1,881,167",
    ]
    assert performance["ref_engine_skill_pass_ms"] is not None
    assert performance["ref_engine_item_pass_ms"] is not None
