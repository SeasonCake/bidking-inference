"""Aisha F1 and B6 compose one balanced red-value arm exactly once."""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AISHA_RED_VALUE_OWNER_COMPOSITION_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


OWNER_ROLLBACK = "BIDKING_DISABLE_AISHA_RED_VALUE_OWNER_COMPOSITION"
CAP_ROLLBACK = "BIDKING_DISABLE_AISHA_RED_PRIOR_CAP"
B6_ROLLBACK = "BIDKING_DISABLE_RED_CELLS_GEOMETRY_CLAMP"
JOINT_ROLLBACK = "BIDKING_DISABLE_B6_COHERENT_JOINT_FIT"


def _item(
    quality: int,
    cells: int,
    shape_key: str,
    local_index: int,
    row: int,
    col: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    return {
        "quality": quality,
        "item_id": None,
        "cells": cells,
        "shape_key": shape_key,
        "local_index": local_index,
        "row": row,
        "col": col,
        "width": width,
        "height": height,
        "category": None,
        "value": None,
    }


WHITE_ITEMS = [
    _item(1, 1, "11", 2, 1, 3, 1, 1),
    _item(1, 1, "11", 24, 3, 5, 1, 1),
    _item(1, 2, "21", 6, 1, 7, 2, 1),
]
GREEN_ITEMS = [
    _item(2, 1, "11", 15, 2, 6, 1, 1),
    _item(2, 2, "21", 57, 6, 8, 2, 1),
    _item(2, 2, "12", 19, 2, 10, 1, 2),
    _item(2, 1, "11", 39, 4, 10, 1, 1),
    _item(2, 2, "12", 22, 3, 3, 1, 2),
]


def _skill_row(
    *,
    sort: int,
    skill_id: int,
    quality: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "sort": sort,
        "skill_id": skill_id,
        "hero_id": 103,
        "revealed_items": len(items),
        "revealed_items_detail": [
            {
                "local_index": item["local_index"],
                "quality": quality,
                "shape_code": int(item["shape_key"]),
            }
            for item in items
        ],
    }


def _r1_snapshot(*, total_cells: int | None = None) -> dict[str, Any]:
    return {
        "session_id": "2409:r1-red-owner",
        "hero": "aisha",
        "map_id": 2409,
        "phase": "bidding",
        "round": 1,
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "public_info_rows": [],
        "skill_reveal_rows": [
            _skill_row(
                sort=1,
                skill_id=1001034,
                quality=1,
                items=WHITE_ITEMS,
            )
        ],
        "action_result_rows": [
            {
                "sort": 3,
                "action_id": 100112,
                "result": 3.75,
                "result_field": 11,
                "revealed_items": 0,
                "revealed_items_detail": [],
            }
        ],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "aisha",
            "total_count": None,
            "total_cells": total_cells,
            "avg_cells": {"q4": 3.75},
            "counts": {},
            "quality_cells": {},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "scoped_count_sums": [],
            "split_avg_cells": {},
            "split_counts": {"white": 3},
            "split_quality_cells": {"white": 4},
            "minimap_items": copy.deepcopy(WHITE_ITEMS),
        },
    }


def _r2_snapshot() -> dict[str, Any]:
    snapshot = _r1_snapshot()
    snapshot.update(
        session_id="2409:r2-green-owner",
        round=2,
        skill_reveal_rows=[
            _skill_row(
                sort=10,
                skill_id=1001034,
                quality=1,
                items=WHITE_ITEMS,
            ),
            _skill_row(
                sort=10,
                skill_id=1001033,
                quality=2,
                items=GREEN_ITEMS,
            ),
        ],
    )
    structured = snapshot["structured_ref_inputs"]
    structured["split_counts"] = {"white": 3, "green": 5}
    structured["split_quality_cells"] = {"white": 4, "green": 8}
    structured["minimap_items"] = copy.deepcopy(WHITE_ITEMS + GREEN_ITEMS)
    return snapshot


def _run(snapshot: dict[str, Any], *, max_combos: int = 8_000) -> dict[str, Any]:
    return run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()


def _owner_note(result: dict[str, Any]) -> str | None:
    return next(
        (
            str(note)
            for note in result["notes"]
            if str(note).startswith(AISHA_RED_VALUE_OWNER_COMPOSITION_NOTE)
        ),
        None,
    )


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_r1_cap_and_no_room_strip_compose_without_double_removal(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    snapshot = _r1_snapshot()
    original = copy.deepcopy(snapshot)
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    monkeypatch.delenv(CAP_ROLLBACK, raising=False)
    monkeypatch.delenv(B6_ROLLBACK, raising=False)

    composed = _run(snapshot, max_combos=max_combos)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    legacy = _run(snapshot, max_combos=max_combos)

    assert [composed[key] for key in ("value_p25", "value_p50", "value_p75")] == [
        266_912,
        429_674,
        622_175,
    ]
    assert [composed[key] for key in ("conservative", "balanced", "aggressive")] == [
        64_598,
        177_736,
        720_599,
    ]
    assert [legacy[key] for key in ("conservative", "balanced", "aggressive")] == [
        13_821,
        13_821,
        720_599,
    ]
    assert _owner_note(composed) == (
        "aisha_b6_red_value_owner_compose:prior0.30*room0.00"
    )
    assert _owner_note(legacy) is None
    assert "aisha_red_prior_value_cap=0.38@r1" in composed["notes"]
    assert "aisha_no_room_red_bid_strip:bal0.00" in composed["notes"]
    for key in (
        "combo_count",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "quality_count_ranges",
        "quality_cells_ranges",
        "total_grid_range",
    ):
        assert composed[key] == legacy[key], key
    assert snapshot == original


def test_cap_rollback_leaves_owner_composition_byte_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot()
    monkeypatch.setenv(CAP_ROLLBACK, "1")
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    enabled = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    disabled = _run(snapshot)

    assert enabled == disabled
    assert enabled["balanced"] == 177_736
    assert _owner_note(enabled) is None


def test_b6_rollback_leaves_owner_composition_byte_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot()
    monkeypatch.setenv(B6_ROLLBACK, "1")
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    enabled = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    disabled = _run(snapshot)

    assert enabled == disabled
    assert enabled["balanced"] == 247_986
    assert _owner_note(enabled) is None


def test_partial_room_multiplies_retained_red_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot()
    # Keep the input on the soft/prior geometry path while producing a partial
    # B6 room ratio at the bounded 2.5k live budget.
    snapshot["structured_ref_inputs"]["minimap_items"][-1]["row"] = 7
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    monkeypatch.delenv(CAP_ROLLBACK, raising=False)
    composed = _run(snapshot, max_combos=2_500)

    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    legacy_double_remove = _run(snapshot, max_combos=2_500)
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    monkeypatch.setenv(CAP_ROLLBACK, "1")
    full_red_b6 = _run(snapshot, max_combos=2_500)

    assert legacy_double_remove["balanced"] == 130_625
    assert composed["balanced"] == 205_919
    assert full_red_b6["balanced"] == 281_213
    assert legacy_double_remove["balanced"] < composed["balanced"] < full_red_b6["balanced"]
    assert _owner_note(composed) == (
        "aisha_b6_red_value_owner_compose:prior0.30*room0.50"
    )
    assert "aisha_no_room_red_bid_strip:bal0.50" in composed["notes"]


def test_coherent_joint_owner_stays_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot(total_cells=45)
    monkeypatch.delenv(JOINT_ROLLBACK, raising=False)
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    enabled = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    disabled = _run(snapshot)

    assert enabled == disabled
    assert enabled["balanced"] == 217_874
    assert "aisha_b6_coherent_joint_fit:legacy0.67->joint1.00" in enabled["notes"]
    assert _owner_note(enabled) is None

    # The hard-total coherent-joint rollback is an independent historical
    # contract and must not be silently replaced by this soft-owner fix.
    monkeypatch.setenv(JOINT_ROLLBACK, "1")
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    joint_legacy_owner_on = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    joint_legacy_owner_off = _run(snapshot)
    assert joint_legacy_owner_on == joint_legacy_owner_off
    assert joint_legacy_owner_on["balanced"] == 148_975


def test_r2_green_evidence_does_not_reuse_r1_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r2_snapshot()
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    enabled = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    disabled = _run(snapshot)

    assert enabled == disabled
    assert [enabled[key] for key in ("value_p25", "value_p50", "value_p75")] == [
        234_523,
        402_481,
        582_653,
    ]
    assert [enabled[key] for key in ("conservative", "balanced", "aggressive")] == [
        56_049,
        178_810,
        678_823,
    ]
    assert _owner_note(enabled) is None


def _as_settlement(snapshot: dict[str, Any]) -> None:
    snapshot["phase"] = "settled"


def _as_ahmed(snapshot: dict[str, Any]) -> None:
    snapshot["hero"] = "ahmed"
    snapshot["structured_ref_inputs"]["hero"] = "ahmed"


def _with_red_evidence(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"]["counts"] = {"q6": 1}


@pytest.mark.parametrize(
    "mutate",
    [_as_settlement, _as_ahmed, _with_red_evidence],
    ids=["settlement", "other_hero", "direct_red"],
)
def test_non_target_paths_are_byte_noop(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    snapshot = _r1_snapshot()
    mutate(snapshot)
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)
    enabled = _run(snapshot)
    monkeypatch.setenv(OWNER_ROLLBACK, "1")
    disabled = _run(snapshot)

    assert enabled == disabled
    assert _owner_note(enabled) is None


def test_panel_summary_publishes_composed_r1_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot()
    snapshot["created_at"] = time.time()
    snapshot["ui_contract"] = {
        "context": {
            "session_id": snapshot["session_id"],
            "hero": "aisha",
            "map_id": 2409,
            "phase": "bidding",
            "round": 1,
        },
        "baseline": {"decision": {}, "posterior": {}},
        "constraints": {"public_info": {}},
        "source": {"created_at": snapshot["created_at"]},
    }
    monkeypatch.delenv(OWNER_ROLLBACK, raising=False)

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    # The panel's scheduled early-round pass owns a separate, already-shipped
    # inference flag, so its exact band is slightly different from the direct
    # replay above.  The composition contract is the same: keep the F1-owned
    # residual rather than letting B6 subtract the full red median again.
    assert [
        summary["ahmed_ref"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [65_816, 178_162, 720_232]
    assert summary["reference"]["balanced"] == "178,162"
    assert _owner_note(summary["ahmed_ref"]) == (
        "aisha_b6_red_value_owner_compose:prior0.30*room0.00"
    )
