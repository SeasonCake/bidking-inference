"""Hotfix2 regression for Aisha soft total-grid/q6 joint conditioning.

Sanitized from 5609:1862572800040593 at bidding R5.  Settlement truth is
deliberately absent from every bidding fixture; it appears only in the settled
no-injection control.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import ServerConfig, make_handler, summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AISHA_SOFT_TOTAL_Q6_JOINT_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


ROLLBACK = "BIDKING_DISABLE_AISHA_SOFT_TOTAL_Q6_JOINT"
CORE_A_ROLLBACK = "BIDKING_DISABLE_B6_COHERENT_JOINT_FIT"
ROUTE_STABLE_MAP_ID = 5611
CHANGED_ROUTE_MAP_ID = 5609


@pytest.fixture(autouse=True)
def _exact_current_rankmap_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    """This engine regression uses an explicitly approved current authority arm."""

    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("ok", engine.CURRENT_RANKMAP_PACKAGE_GENERATION),
    )

_MINIMAP_ROWS = (
    (1, 1, "11", 9, 1, 10, 1, 1),
    (1, 1, "11", 10, 2, 1, 1, 1),
    (1, 1, "11", 18, 2, 9, 1, 1),
    (2, 2, "21", 116, 12, 7, 2, 1),
    (2, 4, "22", 1, 1, 2, 2, 2),
    (2, 2, "21", 106, 11, 7, 2, 1),
    (2, 2, "12", 119, 12, 10, 1, 2),
    (2, 1, "11", 8, 1, 9, 1, 1),
    (3, 4, "22", 112, 12, 3, 2, 2),
    (3, 1, "11", 46, 5, 7, 1, 1),
    (3, 2, "12", 7, 1, 8, 1, 2),
    (3, 1, "11", 23, 3, 4, 1, 1),
    (3, 9, "33", 27, 3, 8, 3, 3),
    (3, 2, "21", 75, 8, 6, 2, 1),
    (3, 4, "22", 110, 12, 1, 2, 2),
    (3, 4, "22", 33, 4, 4, 2, 2),
    (3, 4, "22", 104, 11, 5, 2, 2),
    (3, 6, "23", 88, 9, 9, 2, 3),
    (3, 6, "32", 85, 9, 6, 3, 2),
    (3, 1, "11", 19, 2, 10, 1, 1),
    (3, 1, "11", 94, 10, 5, 1, 1),
    (3, 2, "21", 35, 4, 6, 2, 1),
    (3, 1, "11", 13, 2, 4, 1, 1),
    (4, 9, "33", 57, 6, 8, 3, 3),
    (4, 4, "22", 73, 8, 4, 2, 2),
    (4, 1, "11", 118, 12, 9, 1, 1),
    (4, 1, "11", 3, 1, 4, 1, 1),
    (4, 9, "33", 4, 1, 5, 3, 3),
    (4, 9, "33", 124, 13, 5, 3, 3),
    (4, 8, "42", 53, 6, 4, 4, 2),
    (5, 1, None, 41, None, None, None, None),
    (5, 1, None, 45, None, None, None, None),
)


def _minimap_items() -> list[dict]:
    keys = ("quality", "cells", "shape", "local_index", "row", "col", "width", "height")
    return [
        {key: value for key, value in zip(keys, row, strict=True) if value is not None}
        for row in _MINIMAP_ROWS
    ]


def _warehouse_source(source: str) -> dict:
    if source == "official_random_warehouse":
        return {
            "qualification": source,
            "official_distribution_eligible": True,
            "quote_policy": "advisory_only_no_clamp",
            "quarantine_required": False,
        }
    if source == "player_custom_warehouse":
        return {
            "qualification": source,
            "official_distribution_eligible": False,
            "quote_policy": "advisory_only_no_clamp",
            "quarantine_required": False,
        }
    return {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _r5_snapshot(
    *,
    source: str = "official_random_warehouse",
    map_id: int = ROUTE_STABLE_MAP_ID,
) -> dict:
    session_id = f"{map_id}:truth-free-r5"
    warehouse_source = _warehouse_source(source)
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": 35,
        "total_cells": None,
        "avg_cells": {"q4": 5.857142925262451, "q5": 3.6666667461395264},
        "counts": {"q3": 15, "q4": 7},
        "quality_cells": {"q3": 48, "q4": 41},
        "avg_values": {},
        "quality_values": {},
        "quality_value_floors": {},
        "count_sums": {},
        "scoped_count_sums": [],
        "split_avg_cells": {},
        "split_counts": {"white": 3, "green": 5},
        "split_quality_cells": {"white": 3, "green": 11},
        "field_updates": [],
        "minimap_items": _minimap_items(),
    }
    return {
        "session_id": session_id,
        "created_at": time.time(),
        "hero": "aisha",
        "map_id": map_id,
        "phase": "bidding",
        "round": 5,
        "action_round": 5,
        "warehouse_source": copy.deepcopy(warehouse_source),
        "warehouse_source_qualification": warehouse_source["qualification"],
        "structured_ref_inputs": structured,
        "minimap_grid_items": _minimap_items(),
        "ui_contract": {
            "context": {
                "session_id": session_id,
                "hero": "aisha",
                "map_id": map_id,
                "phase": "bidding",
                "round": 5,
                "warehouse_source": copy.deepcopy(warehouse_source),
                "warehouse_source_qualification": warehouse_source["qualification"],
            },
            "source": {
                "created_at": time.time(),
                "warehouse_source": copy.deepcopy(warehouse_source),
                "warehouse_source_qualification": warehouse_source["qualification"],
            },
            "constraints": {},
            "minimap": {"items": _minimap_items()},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
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
        f"{engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:"
        f"{CHANGED_ROUTE_MAP_ID}:authority_input_unavailable"
    ]


def _remove_q4_evidence(snapshot: dict) -> None:
    structured = snapshot["structured_ref_inputs"]
    structured["counts"].pop("q4", None)
    structured["quality_cells"].pop("q4", None)
    structured["avg_cells"].pop("q4", None)
    structured["minimap_items"] = [
        item for item in structured["minimap_items"] if item.get("quality") != 4
    ]
    snapshot["minimap_grid_items"] = [
        item for item in snapshot["minimap_grid_items"] if item.get("quality") != 4
    ]
    snapshot["ui_contract"]["minimap"]["items"] = [
        item
        for item in snapshot["ui_contract"]["minimap"]["items"]
        if item.get("quality") != 4
    ]


def _change_scope(snapshot: dict, *, hero: str | None = None, map_id: int | None = None) -> None:
    if hero is not None:
        snapshot["hero"] = hero
        snapshot["structured_ref_inputs"]["hero"] = hero
        snapshot["ui_contract"]["context"]["hero"] = hero
    if map_id is not None:
        snapshot["map_id"] = map_id
        snapshot["ui_contract"]["context"]["map_id"] = map_id


def _add_known_q6_item(snapshot: dict) -> None:
    item = {
        "quality": 6,
        "cells": 4,
        "shape": "22",
        "local_index": 200,
        "row": 14,
        "col": 1,
        "width": 2,
        "height": 2,
    }
    for items in (
        snapshot["structured_ref_inputs"]["minimap_items"],
        snapshot["minimap_grid_items"],
        snapshot["ui_contract"]["minimap"]["items"],
    ):
        items.append(copy.deepcopy(item))


def _add_public_exact_cells_row(snapshot: dict, *, info_id: int, cells: int) -> None:
    snapshot.setdefault("public_info_rows", []).append(
        {"info_id": info_id, "value": cells, "value_field": 14}
    )


def test_route_stable_5611_joint_conditions_cells_value_and_quotes_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r5_snapshot()
    original = copy.deepcopy(snapshot)

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)

    assert [legacy[key] for key in ("conservative", "balanced", "aggressive")] == [
        251_917,
        557_376,
        603_834,
    ]
    assert legacy["red_count_range"] == [2, 2, 2]
    assert legacy["red_cells_range"] == [4, 6, 20]
    assert legacy["red_value_range"] == [143_124, 314_880, 415_074]
    assert legacy["total_grid_range"] == [118, 131, 134]

    assert fixed["red_count_range"] == [2, 2, 2]
    assert fixed["red_cells_range"] == [4, 17, 20]
    assert [fixed[key] for key in ("conservative", "balanced", "aggressive")] == [
        553_463,
        679_190,
        742_053,
    ]
    assert fixed["red_value_range"] == [294_538, 432_000, 569_462]
    assert fixed["quality_cells_ranges"]["q6"] == [4, 17, 20]
    assert fixed["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert fixed["total_grid_range"] == legacy["total_grid_range"]
    assert fixed["red_value_range"][1] > legacy["red_value_range"][1]
    assert fixed["balanced"] > legacy["balanced"]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE in fixed["notes"]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in legacy["notes"]
    assert snapshot == original

    for key in (
        "combo_count",
        "quality_count_ranges",
        "quality_count_candidates",
        "risk_floor",
        "risk_ceiling",
    ):
        assert fixed[key] == legacy[key], key
    for key in ("q1", "q3", "q4", "q5"):
        assert fixed["quality_cells_ranges"][key] == legacy["quality_cells_ranges"][key]
        assert fixed["quality_lock"][key] == legacy["quality_lock"][key]
        assert fixed["quality_count_cells_candidates"][key] == legacy[
            "quality_count_cells_candidates"
        ][key]
    assert fixed["evidence"] == legacy["evidence"]


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_route_stable_5611_budget_is_stable(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = _run(_r5_snapshot(), max_combos=max_combos)
    assert result["combo_count"] == 1
    assert result["red_cells_range"] == [4, 17, 20]
    assert result["total_grid_range"] == [118, 131, 134]


def test_core_a_rollback_does_not_change_core_b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    monkeypatch.delenv(CORE_A_ROLLBACK, raising=False)
    default = _run(_r5_snapshot())
    monkeypatch.setenv(CORE_A_ROLLBACK, "1")
    core_a_rollback = _run(_r5_snapshot())

    assert default == core_a_rollback
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE in default["notes"]


@pytest.mark.parametrize(
    "mutator",
    (
        lambda s: s["structured_ref_inputs"]["quality_cells"].pop("q3"),
        _remove_q4_evidence,
        lambda s: s["structured_ref_inputs"].update(total_count=None),
        lambda s: s["structured_ref_inputs"].update(total_cells=134),
        lambda s: s["structured_ref_inputs"]["counts"].update(q6=2),
        lambda s: s["structured_ref_inputs"]["quality_cells"].update(q6=20),
        lambda s: s["structured_ref_inputs"]["avg_cells"].update(q6=10.0),
        lambda s: s["structured_ref_inputs"]["quality_values"].update(q6=587_520),
        lambda s: s["structured_ref_inputs"].update(
            quality_value_floors={"q6": 587_520}
        ),
        lambda s: s["structured_ref_inputs"]["avg_cells"].pop("q5"),
        lambda s: s["structured_ref_inputs"].update(total_count=33),
        lambda s: s["structured_ref_inputs"]["avg_cells"].update(q5="bad"),
        _add_known_q6_item,
        lambda s: (
            s.update(round=4, action_round=4),
            s["ui_contract"]["context"].update(round=4),
        ),
        lambda s: _change_scope(s, hero="ahmed"),
        lambda s: _change_scope(s, map_id=2509),
    ),
    ids=(
        "missing-q3-cells",
        "missing-q4-count",
        "missing-total-count",
        "hard-total",
        "direct-q6-count",
        "direct-q6-cells",
        "direct-q6-average",
        "direct-q6-value",
        "direct-q6-floor",
        "multiple-q6-support",
        "q6-zero",
        "malformed-q5-average",
        "known-q6-item",
        "earlier-round",
        "other-hero",
        "other-map",
    ),
)
def test_joint_gate_fail_closed_and_matches_rollback(mutator, monkeypatch) -> None:
    snapshot = _r5_snapshot()
    mutator(snapshot)

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)

    assert fixed == legacy
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in fixed.get("notes", [])


@pytest.mark.parametrize(
    "quality,conflict_mutator,expected_note",
    (
        (
            "q1",
            lambda s: s["structured_ref_inputs"]["quality_cells"].update(q1=15),
            "split_low_quality_q1_cells_conflict",
        ),
        (
            "q3",
            lambda s: s["structured_ref_inputs"]["avg_cells"].update(q3=4.0),
            "quality_cells_q3_avg_count_conflict",
        ),
        (
            "q4",
            lambda s: _add_public_exact_cells_row(s, info_id=200010, cells=42),
            "public_info_200010_q4_cells_conflict",
        ),
        (
            "q5",
            lambda s: (
                s["structured_ref_inputs"]["quality_cells"].update(q5=11),
                _add_public_exact_cells_row(s, info_id=200011, cells=12),
            ),
            "public_info_200011_q5_cells_conflict",
        ),
    ),
)
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_q1_q5_exact_cells_conflicts_fail_closed(
    quality: str,
    conflict_mutator,
    expected_note: str,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflict = _r5_snapshot()
    conflict_mutator(conflict)

    monkeypatch.delenv(ROLLBACK, raising=False)
    rejected = _run(conflict, max_combos=max_combos)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(conflict, max_combos=max_combos)

    assert expected_note in rejected["notes"]
    assert rejected == rollback, (quality, max_combos)
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in rejected["notes"]


def test_matching_q4_public_exact_cells_still_enables_joint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r5_snapshot()
    _add_public_exact_cells_row(snapshot, info_id=200010, cells=41)

    monkeypatch.delenv(ROLLBACK, raising=False)
    accepted = _run(snapshot)

    assert "public_info_200010_q4_cells" in accepted["notes"]
    assert "public_info_200010_q4_cells_conflict" not in accepted["notes"]
    assert accepted["red_cells_range"] == [4, 17, 20]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE in accepted["notes"]


def test_q4_public_cells_conflict_is_fail_closed_through_loopback_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _r5_snapshot()
    _add_public_exact_cells_row(snapshot, info_id=200010, cells=42)
    monkeypatch.delenv(ROLLBACK, raising=False)
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot)
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    result = payload["ahmed_ref"]
    assert status == 200
    assert payload["status"] == "ok"
    assert result["red_cells_range"] == [4, 6, 20]
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        251_917,
        557_376,
        603_834,
    ]
    assert "public_info_200010_q4_cells_conflict" in result["notes"]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in result["notes"]


@pytest.mark.parametrize(
    "source",
    (
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ),
)
def test_source_pool_is_not_reclassified(source: str, monkeypatch, tmp_path: Path) -> None:
    snapshot = _r5_snapshot(source=source)
    original = copy.deepcopy(snapshot)
    monkeypatch.delenv(ROLLBACK, raising=False)

    result = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"{source}.json",
    )

    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE in result["notes"]
    assert snapshot == original
    assert summary["context"]["warehouse_source_qualification"] == source
    assert summary["context"]["warehouse_source"] == original["warehouse_source"]


def test_settlement_truth_is_not_a_bidding_input(monkeypatch) -> None:
    snapshot = _r5_snapshot()
    snapshot["phase"] = "settled"
    snapshot["round"] = 5
    snapshot["structured_ref_inputs"].update(
        total_cells=134,
        counts={"q3": 15, "q4": 7, "q5": 3, "q6": 2},
        quality_cells={"q3": 48, "q4": 41, "q5": 11, "q6": 20},
        split_counts={"white": 3, "green": 5},
        split_quality_cells={"white": 3, "green": 11},
    )
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["final_quality_counts"] = "q1=8;q3=15;q4=7;q5=3;q6=2"
    snapshot["final_quality_cells"] = "q1=14;q3=48;q4=41;q5=11;q6=20"
    snapshot["final_quality_values"] = "q1=0;q3=0;q4=0;q5=229353;q6=587520"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 35,
        "total_cells": 134,
        "total_value": 816_873,
    }

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)

    assert fixed == legacy
    assert fixed["red_count_range"] == [2, 2, 2]
    assert fixed["red_cells_range"] == [20, 20, 20]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in fixed["notes"]


_FRESH_PROCESS_RUNNER = r"""
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
import ahmad_ref_engine as engine

engine._current_rankmap_distribution_generation_status = lambda: (
    "ok",
    engine.CURRENT_RANKMAP_PACKAGE_GENERATION,
)

snapshot = json.load(sys.stdin)
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot),
    max_combos=int(sys.argv[1]),
).as_dict()
print(json.dumps({
    "combo_count": result["combo_count"],
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
    "red_count_range": result["red_count_range"],
    "red_cells_range": result["red_cells_range"],
    "red_value_range": result["red_value_range"],
    "quality_cells_ranges": result["quality_cells_ranges"],
    "quality_lock": result["quality_lock"],
    "total_grid_range": result["total_grid_range"],
    "notes": result["notes"],
}))
"""


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    env.pop(CORE_A_ROLLBACK, None)
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
        timeout=180,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_fresh_process_default_and_rollback(max_combos: int) -> None:
    fixed = _fresh_process(_r5_snapshot(), max_combos=max_combos, rollback=False)
    legacy = _fresh_process(_r5_snapshot(), max_combos=max_combos, rollback=True)

    assert fixed["combo_count"] == legacy["combo_count"] == 1
    assert legacy["quotes"] == [251_917, 557_376, 603_834]
    assert legacy["red_cells_range"] == [4, 6, 20]
    assert fixed["red_cells_range"] == [4, 17, 20]
    assert fixed["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert fixed["total_grid_range"] == legacy["total_grid_range"] == [118, 131, 134]


def test_summary_api_and_tk_consume_the_same_joint(monkeypatch, tmp_path: Path) -> None:
    snapshot = _r5_snapshot()
    snapshot_path = tmp_path / "latest_snapshot.json"

    monkeypatch.delenv(ROLLBACK, raising=False)
    api_fixed = _run(snapshot)
    summary_fixed = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    monkeypatch.setenv(ROLLBACK, "1")
    api_legacy = _run(snapshot)
    summary_legacy = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert api_fixed["red_cells_range"] == [4, 17, 20]
    assert summary_fixed["red"]["cells_range"] == "4 / 17 / 20"
    assert summary_legacy["red"]["cells_range"] == "4 / 6 / 20"
    assert summary_fixed["reference"]["balanced"] == f"{api_fixed['balanced']:,}"
    assert summary_legacy["reference"]["balanced"] == f"{api_legacy['balanced']:,}"

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
    assert tk_fixed["red"]["cells_range"] == "4 / 17 / 20"
    assert tk_legacy["red"]["cells_range"] == "4 / 6 / 20"


def test_changed_route_5609_engine_summary_and_tk_publish_no_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("authority_input_unavailable", ""),
    )
    snapshot = _r5_snapshot(map_id=CHANGED_ROUTE_MAP_ID)
    snapshot_path = tmp_path / "changed_route_5609.json"

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    summary_fixed = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)
    summary_legacy = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert summary_fixed["reference"]["readiness"] == "no_reachable_combo"
    assert summary_fixed["reference"]["source"] == "ref_waiting"
    assert summary_fixed["reference"]["decision_range"] == "-"
    assert summary_fixed["ahmed_ref"]["status"] == "no_reachable_combo"
    assert summary_legacy["reference"] == summary_fixed["reference"]

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
