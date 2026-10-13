"""Hotfix2 evidence regression plus the update606 5602 no-quote boundary.

Sanitized from HeroRefDiag-20260727-194557-5602_9000000000000026.  At bidding R4
the product had exact q1/q3/q4 cells, exact q5 count=4, and hard q5 avg=6.0.
The legacy count/layout path nevertheless excluded q6=0 and priced a 7/8-red
warehouse.  Settlement later proved q6=0, q5=4/24, and total=34/117.  The
update606 route later changed without approved distribution authority, so the
runtime still extracts this diagnostic evidence but must no longer quote 5602.
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
    UPDATE606_CHANGED_ROUTE_NOTE_PREFIX,
    UPDATE606_RUNTIME_PACKAGE_GENERATION,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


ROLLBACK = "BIDKING_DISABLE_AISHA_DERIVED_Q5_PINNED_NONRED"
DERIVED_NOTE = "aisha_derived_q5_count_avg_cells:4x6=24"
RICHNESS_SKIP_NOTE = "aisha_derived_q5_pinned_nonred_richness_skip"

# quality, cells, row, col, width, height, item_id, value, local_index
VISIBLE_ITEMS = (
    (1, 1, 1, 10, 1, 1, None, None, 9),
    (1, 1, 2, 4, 1, 1, None, None, 13),
    (1, 2, 1, 1, 1, 2, None, None, None),
    (1, 1, 7, 5, 1, 1, None, None, 64),
    (1, 2, 11, 1, 2, 1, None, None, 100),
    (1, 2, 1, 4, 2, 1, None, None, 3),
    (1, 6, 5, 1, 2, 3, None, None, 40),
    (1, 1, 6, 5, 1, 1, None, None, 54),
    (2, 1, 10, 10, 1, 1, None, None, 99),
    (2, 1, 7, 6, 1, 1, None, None, 65),
    (2, 1, 5, 10, 1, 1, None, None, 49),
    (2, 1, 5, 6, 1, 1, None, None, 45),
    (2, 2, 11, 3, 2, 1, None, None, 102),
    (2, 4, 1, 2, 2, 2, 1012004, 1481, 1),
    (2, 1, 9, 10, 1, 1, None, None, 89),
    (2, 1, 6, 6, 1, 1, None, None, 55),
    (3, 9, 8, 4, 3, 3, None, None, 73),
    (3, 2, 3, 10, 1, 2, 1033008, 1661, 29),
    (3, 1, 6, 10, 1, 1, None, None, 59),
    (3, 4, 11, 5, 2, 2, None, None, 104),
    (3, 4, 3, 5, 2, 2, None, None, 24),
    (3, 1, 5, 5, 1, 1, None, None, 44),
    (3, 4, 3, 1, 2, 2, None, None, 20),
    (3, 6, 3, 3, 2, 3, None, None, 22),
    (4, 1, 2, 5, 1, 1, None, None, 14),
    (4, 9, 11, 7, 3, 3, None, None, 106),
    (4, 9, 8, 1, 3, 3, None, None, 70),
    (4, 2, 7, 10, 1, 2, None, None, 69),
    (4, 4, 6, 3, 2, 2, None, None, 52),
    (4, 9, 8, 7, 3, 3, None, None, 76),
    (5, 12, 1, 7, 3, 4, 1065010, 85800, 6),
)


def _visible_items() -> list[dict]:
    return [
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
        ) in VISIBLE_ITEMS
    ]


def _action_rows() -> list[dict]:
    return [
        {
            "action_id": 100113,
            "result": 6.0,
            "result_field": 11,
            "revealed_items": 0,
            "revealed_items_detail": [],
        },
        {
            "action_id": 100128,
            "result": 12,
            "result_field": 14,
            "revealed_items": 1,
            "revealed_items_detail": [
                {
                    "local_index": 6,
                    "runtime_id": 1770351258017213,
                    "item_id": 1065010,
                    "quality": 5,
                    "value": 85800,
                    "shape_code": 34,
                    "cells": 12,
                }
            ],
        },
        {
            "action_id": 100129,
            "result": 6,
            "result_field": 14,
            "revealed_items": 2,
            "revealed_items_detail": [
                {
                    "local_index": 29,
                    "runtime_id": 1770351258017221,
                    "item_id": 1033008,
                    "quality": 3,
                    "value": 1661,
                    "shape_code": 12,
                    "cells": 2,
                },
                {
                    "local_index": 1,
                    "runtime_id": 1770351258017210,
                    "item_id": 1012004,
                    "quality": 2,
                    "value": 1481,
                    "shape_code": 22,
                    "cells": 4,
                },
            ],
        },
    ]


def _skill_rows(items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for quality, skill_id in ((1, 1001034), (2, 1001033), (3, 1001032), (4, 1001031)):
        count = sum(1 for item in items if item["quality"] == quality)
        rows.append(
            {
                "sort": 25,
                "skill_id": skill_id,
                "hero_id": 103,
                # The exact shape/count payload is already represented by the
                # sanitized structured bridge and minimap below.  Keeping only
                # the skill-frame presence avoids duplicating physical items.
                "revealed_items": count,
                "revealed_items_detail": [],
            }
        )
    return rows


def _snapshot(*, hero: str = "aisha") -> dict:
    items = _visible_items()
    actions = _action_rows()
    skill_rows = _skill_rows(items) if hero == "aisha" else []
    return {
        "session_id": "5602:9000000000000026",
        "hero": hero,
        "map_id": 5602,
        "phase": "bidding",
        "round": 4,
        "minimap_grid_items": items,
        "action_result_rows": actions,
        "skill_reveal_rows": skill_rows,
        "public_info_rows": [{"info_id": 200019, "value": 4}],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            "total_count": None,
            "total_cells": None,
            "avg_cells": {"q5": 6.0},
            "counts": {"q3": 8, "q4": 6, "q5": 4},
            "quality_cells": {"q3": 31, "q4": 34},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "scoped_count_sums": [],
            "split_avg_cells": {},
            "split_counts": {"green": 8, "white": 8},
            "split_quality_cells": {"green": 12, "white": 16},
            "minimap_items": items,
        },
        "ui_contract": {
            "context": {
                "session_id": "5602:9000000000000026",
                "hero": hero,
                "map_id": 5602,
                "phase": "bidding",
                "round": 4,
            },
            "actions": {"results": actions},
            "constraints": {
                "counts": {
                    "known_quality_counts": {
                        "q1": 8,
                        "q2": 8,
                        "q3": 8,
                        "q4": 6,
                        "q5": 4,
                    }
                }
            },
            "minimap": {"items": items},
        },
    }


def _run(snapshot: dict) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=8_000,
    ).as_dict()


def _assert_update606_5602_no_quote(result: dict) -> None:
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert result["quality_count_ranges"] == {}
    assert result["quality_cells_ranges"] == {}
    assert result["total_grid_range"] == [None, None, None]
    assert result["notes"] == [
        f"{UPDATE606_CHANGED_ROUTE_NOTE_PREFIX}"
        f":{UPDATE606_RUNTIME_PACKAGE_GENERATION}"
        ":5602:auction_rounds_rate_changed:no_approved_distribution"
    ]


def test_5602_exact_gold_count_and_hard_avg_restore_zero_red_arm() -> None:
    snapshot = _snapshot()
    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    result = _run(snapshot)

    assert evidence.fixed_counts["q5"] == 4
    assert evidence.avg_cells["q5"] == 6.0
    assert evidence.quality_cells["q5"] == 24.0
    assert DERIVED_NOTE in evidence.source_notes
    _assert_update606_5602_no_quote(result)


def test_5602_rollback_restores_legacy_phantom_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROLLBACK, "1")
    snapshot = _snapshot()
    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    legacy = _run(snapshot)

    assert evidence.quality_cells.get("q5") is None
    assert DERIVED_NOTE not in evidence.source_notes
    assert RICHNESS_SKIP_NOTE not in evidence.source_notes
    _assert_update606_5602_no_quote(legacy)


def test_exact_total_count_keeps_existing_anchor_path() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["total_count"] = 41

    result = _run(snapshot)

    assert DERIVED_NOTE not in result["notes"]
    assert "q5" not in result["evidence"]["quality_cells"]


def test_older_map_family_keeps_existing_anchor_path() -> None:
    snapshot = _snapshot()
    snapshot["map_id"] = 2503
    snapshot["ui_contract"]["context"]["map_id"] = 2503

    result = _run(snapshot)

    assert DERIVED_NOTE not in result["notes"]
    assert "q5" not in result["evidence"]["quality_cells"]
    assert result["status"] != "no_reachable_combo"
    assert result["balanced"] is not None


@pytest.mark.parametrize(
    "hero",
    ["ahmed", "gabriela", "ethan", "maria", "raven", "ivan", "carlos"],
)
def test_5602_same_inputs_are_noop_for_other_heroes(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(_snapshot(hero=hero))
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(_snapshot(hero=hero))

    assert default == rollback
    assert DERIVED_NOTE not in default["notes"]
    _assert_update606_5602_no_quote(default)


def test_5602_direct_red_evidence_keeps_successor_dormant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["public_info_rows"].extend(
        [
            {"info_id": 200020, "value": 2},
            {"info_id": 200012, "value": 8},
        ]
    )
    default = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    _assert_update606_5602_no_quote(default)


def test_5602_dual_pass_api_and_tk_summary_no_longer_present_locked_seven_red(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot["created_at"] = time.time()
    snapshot["ui_contract"].setdefault("source", {})["created_at"] = time.time()

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "synthetic_aisha_5602.json",
    )

    assert summary["reference"]["readiness"] == "no_reachable_combo"
    assert summary["reference"]["source"] == "ref_waiting"
    assert summary["reference"]["decision_range"] == "-"
    assert summary["red"]["count_range"] == "-"
    assert "ahmad_ref" not in summary


def test_5602_settlement_truth_remains_exact_zero_red() -> None:
    snapshot = _snapshot()
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "total_items": 34,
        "total_cells": 117,
        "total_value": 268_515,
    }
    snapshot["final_quality_counts"] = "q1=8;q2=8;q3=8;q4=6;q5=4;q6=0"
    snapshot["final_quality_cells"] = "q1=16;q2=12;q3=31;q4=34;q5=24;q6=0"

    result = _run(snapshot)

    _assert_update606_5602_no_quote(result)


def test_derived_q5_cells_are_not_injected_when_the_average_is_not_unique() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q5"] = 6.125
    snapshot["action_result_rows"][0]["result"] = 6.125
    snapshot["ui_contract"]["actions"]["results"][0]["result"] = 6.125

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert evidence.quality_cells.get("q5") is None
    assert not any(
        str(note).startswith("aisha_derived_q5_count_avg_cells:")
        for note in evidence.source_notes
    )


def test_derived_q5_cells_wait_for_the_complete_nonred_body() -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["quality_cells"].pop("q4")

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert evidence.quality_cells.get("q5") is None
    assert not any(
        str(note).startswith("aisha_derived_q5_count_avg_cells:")
        for note in evidence.source_notes
    )
