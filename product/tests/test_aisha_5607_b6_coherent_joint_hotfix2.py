"""Hotfix2 regression for Aisha B6 pricing from a coherent warehouse joint.

Sanitized from 5607:1862572798740647 at bidding R1.  The live evidence had a
103-cell warehouse, a white outline of 5 items / 11 cells, and q4 average cells
6.0.  The legacy B6 transform added independently projected q1/q3/q4/q5 P50
cells, manufacturing a no-room state that did not exist in the combo posterior.
Settlement values are deliberately absent from every bidding fixture below.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
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
    RefCombo,
    TOTAL_GRID_SOFT_FORCED_RED_TAIL_NOTE,
    UPDATE606_CHANGED_ROUTE_NOTE_PREFIX,
    UPDATE606_RUNTIME_PACKAGE_GENERATION,
    _aisha_b6_coherent_joint_fit,
    _hard_total_grid_target_from_notes,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


ROLLBACK = "BIDKING_DISABLE_B6_COHERENT_JOINT_FIT"
JOINT_NOTE = "aisha_b6_coherent_joint_fit:legacy0.80->joint1.00"
LEGACY_NOTE = "aisha_no_room_red_bid_strip:bal0.80"
CHANGED_ROUTE_MAP_ID = 5607


def _r1_snapshot(
    *,
    source: str = "postupdate_unknown_warehouse_source",
    map_id: int = CHANGED_ROUTE_MAP_ID,
) -> dict:
    action = {
        "sort": 3,
        "action_id": 100112,
        "tool": "优品均格",
        "result": 6.0,
        "result_field": 11,
        "revealed_items": 0,
        "revealed_items_detail": [],
    }
    warehouse_source = {
        "kind": source,
        "qualification": (
            "official_random_warehouse"
            if source == "official_random_warehouse"
            else "player_custom_warehouse"
            if source == "player_custom_warehouse"
            else "advisory_only"
        ),
        "eligible": source == "official_random_warehouse",
    }
    return {
        "session_id": f"{map_id}:truth-free-r1",
        "hero": "aisha",
        "map_id": map_id,
        "phase": "bidding",
        "round": 1,
        "warehouse_source": warehouse_source,
        "warehouse_source_qualification": warehouse_source["qualification"],
        "action_result_rows": [action],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "aisha",
            "total_count": None,
            "total_cells": 103,
            "avg_cells": {"q4": 6.0},
            "counts": {},
            "quality_cells": {},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "scoped_count_sums": [],
            "split_avg_cells": {},
            "split_counts": {"white": 5},
            "split_quality_cells": {"white": 11},
            "minimap_items": [],
        },
        "ui_contract": {
            "context": {
                "session_id": f"{map_id}:truth-free-r1",
                "hero": "aisha",
                "map_id": map_id,
                "phase": "bidding",
                "round": 1,
                "warehouse_source": warehouse_source,
                "warehouse_source_qualification": warehouse_source["qualification"],
            },
            "source": {
                "warehouse_source": warehouse_source,
                "warehouse_source_qualification": warehouse_source["qualification"],
            },
            "actions": {"results": [action]},
            "constraints": {},
            "minimap": {"items": []},
        },
    }


def _consumer_snapshot(*, map_id: int = CHANGED_ROUTE_MAP_ID) -> dict:
    """Small R3 posterior that makes the panel/API/Tk consumption chain observable."""
    snapshot = _r1_snapshot(map_id=map_id)
    snapshot["created_at"] = time.time()
    snapshot["session_id"] = f"{map_id}:consumer-chain"
    snapshot["round"] = 3
    snapshot["structured_ref_inputs"]["total_cells"] = 90
    snapshot["skill_reveal_rows"] = [
        {
            "sort": 8,
            "skill_id": 1001032,
            "hero_id": 103,
            "revealed_items": 1,
            "revealed_items_detail": [],
        }
    ]
    snapshot["ui_contract"]["context"].update(
        {"session_id": f"{map_id}:consumer-chain", "round": 3}
    )
    snapshot["ui_contract"]["source"]["created_at"] = snapshot["created_at"]
    snapshot["ui_contract"]["constraints"] = {
        "public_info": {"avg_cells": {"q4": 6.0}}
    }
    return snapshot


def _settled_snapshot() -> dict:
    """Settlement-only exact evidence; never reused by a bidding test."""
    snapshot = _r1_snapshot()
    snapshot["phase"] = "settled"
    snapshot["round"] = 5
    snapshot["structured_ref_inputs"].update(
        {
            "total_count": 36,
            "counts": {"q3": 10, "q4": 6, "q5": 2, "q6": 4},
            "quality_cells": {"q3": 21, "q4": 36, "q5": 4, "q6": 15},
            "split_counts": {"white": 5, "green": 9},
            "split_quality_cells": {"white": 11, "green": 16},
        }
    )
    snapshot["ui_contract"]["context"].update({"phase": "settled", "round": 5})
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 2_500) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()


def _assert_changed_route_no_quote(result: dict) -> None:
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert result["quality_count_ranges"] == {}
    assert result["quality_cells_ranges"] == {}
    assert result["notes"] == [
        f"{UPDATE606_CHANGED_ROUTE_NOTE_PREFIX}"
        f":{UPDATE606_RUNTIME_PACKAGE_GENERATION}"
        f":{CHANGED_ROUTE_MAP_ID}:auction_rounds_rate_changed"
        ":no_approved_distribution"
    ]


def test_joint_fit_keeps_each_nonred_and_red_row_against_one_total_arm() -> None:
    combos = [
        RefCombo(
            counts={"q1": 8, "q3": 0, "q4": 0, "q5": 0, "q6": 2},
            grids={"q1": 80, "q3": 0, "q4": 0, "q5": 0, "q6": 20},
            value=0,
            weight=0.0,
            total_grid=100,
        ),
        RefCombo(
            counts={"q1": 6, "q3": 0, "q4": 0, "q5": 0, "q6": 4},
            grids={"q1": 60, "q3": 0, "q4": 0, "q5": 0, "q6": 40},
            value=0,
            weight=-0.1,
            total_grid=100,
        ),
    ]

    assert _aisha_b6_coherent_joint_fit(
        combos,
        max_log_weight=0.0,
        total_grid_mid=100,
    ) == 1.0

    physical_undercut = [
        RefCombo(
            counts={"q1": 9, "q3": 0, "q4": 0, "q5": 0, "q6": 2},
            grids={"q1": 90, "q3": 0, "q4": 0, "q5": 0, "q6": 20},
            value=0,
            weight=0.0,
            total_grid=95,
        )
    ]
    assert _aisha_b6_coherent_joint_fit(
        physical_undercut,
        max_log_weight=0.0,
        total_grid_mid=95,
    ) == pytest.approx(0.25)


def test_joint_fit_preserves_the_legacy_point_eight_to_joint_one_contract() -> None:
    coherent_rows = [
        RefCombo(
            counts={"q1": 8, "q3": 0, "q4": 0, "q5": 0, "q6": 2},
            grids={"q1": 90, "q3": 0, "q4": 0, "q5": 0, "q6": 13},
            value=0,
            weight=0.0,
            total_grid=103,
        ),
        RefCombo(
            counts={"q1": 8, "q3": 0, "q4": 0, "q5": 0, "q6": 2},
            grids={"q1": 95, "q3": 0, "q4": 0, "q5": 0, "q6": 10},
            value=0,
            weight=-0.1,
            total_grid=105,
        ),
    ]

    assert (103 - 95) / 10 == pytest.approx(0.8)
    assert _aisha_b6_coherent_joint_fit(
        coherent_rows,
        max_log_weight=0.0,
        total_grid_mid=103,
    ) == 1.0


def test_joint_admission_requires_hard_total_grid_evidence() -> None:
    assert _hard_total_grid_target_from_notes(
        103,
        ("structured_ref_bridge_total_cells",),
    ) == 103
    assert (
        _hard_total_grid_target_from_notes(
            103,
            (TOTAL_GRID_SOFT_FORCED_RED_TAIL_NOTE,),
        )
        is None
    )


def test_changed_route_5607_r1_entry_is_no_quote_in_both_rollback_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot()
    original = copy.deepcopy(snapshot)

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert JOINT_NOTE not in fixed["notes"]
    assert LEGACY_NOTE not in fixed["notes"]
    assert snapshot == original


@pytest.mark.parametrize(
    "source",
    (
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ),
)
def test_source_qualification_is_not_reclassified(
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r1_snapshot(source=source)
    original_source = copy.deepcopy(snapshot["warehouse_source"])
    monkeypatch.delenv(ROLLBACK, raising=False)

    result = _run(snapshot)

    _assert_changed_route_no_quote(result)
    assert snapshot["warehouse_source"] == original_source
    assert (
        snapshot["ui_contract"]["context"]["warehouse_source"]
        == original_source
    )


def test_settlement_exact_red_evidence_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _settled_snapshot()
    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot, max_combos=8_000)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot, max_combos=8_000)

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert not any("coherent_joint" in str(note) for note in fixed["notes"])
    assert not any("no_room_red_bid_strip" in str(note) for note in fixed["notes"])


_FRESH_PROCESS_RUNNER = r"""
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

snapshot = json.load(sys.stdin)
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot),
    max_combos=int(sys.argv[1]),
).as_dict()
print(json.dumps({
    "status": result["status"],
    "combo_count": result["combo_count"],
    "conservative": result["conservative"],
    "balanced": result["balanced"],
    "aggressive": result["aggressive"],
    "red_count_range": result["red_count_range"],
    "red_cells_range": result["red_cells_range"],
    "red_value_range": result["red_value_range"],
    "notes": result["notes"],
}))
"""


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    env.pop("AISHA_ENABLE_B6_VISIBLE_DEPTH_GUARD", None)
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
        timeout=180 if max_combos >= 50_000 else 60,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("snapshot_factory", "max_combos"),
    (
        (_r1_snapshot, 2_500),
        (_r1_snapshot, 8_000),
        (_r1_snapshot, 50_000),
        (_consumer_snapshot, 8_000),
        (_consumer_snapshot, 50_000),
    ),
    ids=("early-cap", "raw-8k", "raw-50k", "consumer-8k", "consumer-50k"),
)
def test_fresh_process_default_and_rollback_budget_matrix(
    snapshot_factory,
    max_combos: int,
) -> None:
    snapshot = snapshot_factory()
    fixed = _fresh_process(snapshot, max_combos=max_combos, rollback=False)
    legacy = _fresh_process(snapshot, max_combos=max_combos, rollback=True)

    assert fixed == legacy
    assert fixed["status"] == "no_reachable_combo"
    assert fixed["combo_count"] == 0
    assert [fixed[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]


def test_changed_route_5607_summary_api_and_tk_publish_no_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _consumer_snapshot(map_id=CHANGED_ROUTE_MAP_ID)
    snapshot_path = tmp_path / "latest_snapshot.json"

    monkeypatch.delenv(ROLLBACK, raising=False)
    api_fixed = _run(snapshot, max_combos=8_000)
    summary_fixed = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    monkeypatch.setenv(ROLLBACK, "1")
    api_legacy = _run(snapshot, max_combos=8_000)
    summary_legacy = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert api_fixed == api_legacy
    _assert_changed_route_no_quote(api_fixed)
    assert summary_fixed["reference"]["readiness"] == "no_reachable_combo"
    assert summary_fixed["reference"]["source"] == "ref_waiting"
    assert summary_fixed["reference"]["decision_range"] == "-"
    assert summary_legacy["reference"] == summary_fixed["reference"]
    assert summary_fixed["ahmed_ref"]["status"] == "no_reachable_combo"
    assert [
        summary_fixed["ahmed_ref"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [None, None, None]
    assert summary_fixed["red"] == summary_legacy["red"]

    tk_fixed = _summary_diagnostic_row(
        summary_fixed,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    tk_legacy = _summary_diagnostic_row(
        summary_legacy,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert tk_fixed["reference"]["readiness"] == "no_reachable_combo"
    assert tk_fixed["reference"]["balanced"] == "-"
    assert tk_legacy["reference"] == tk_fixed["reference"]
    assert tk_fixed["red"] == tk_legacy["red"]
