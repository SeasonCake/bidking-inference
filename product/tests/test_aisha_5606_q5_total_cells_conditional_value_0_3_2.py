"""Aisha exact-q5-cells conditional value source for the 5606 family.

The bidding fixture contains no settlement fields.  The real 5606 settlement
is an evaluation label only; the source gate and every expected value below
are derived from pre-settlement actions, Aisha reveals, physical footprints,
and the approved item/drop-weight catalogs.
"""

from __future__ import annotations

import copy
from collections import defaultdict
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402
from bidking_lab.live.monitor import _action_result_rows  # noqa: E402
from bidking_lab.runtime.snapshot import _ui_actions_contract  # noqa: E402


ROLLBACK = engine.AISHA_Q5_TOTAL_CELLS_VALUE_DISABLE
NOTE = engine.AISHA_Q5_TOTAL_CELLS_VALUE_NOTE
SESSION_ID = "5606:9000000000000048"
CHANGED_ROUTE_MAP_ID = 5606


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
    assert result["q5_total_cells_value_summary"] == {}
    assert result["notes"] == [
        f"{engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:"
        f"{CHANGED_ROUTE_MAP_ID}:authority_input_unavailable"
    ]


def _warehouse_source(
    qualification: str = "postupdate_unknown_warehouse_source",
) -> dict:
    official = qualification == "official_random_warehouse"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "normal" if official else "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _item_plan() -> dict[int, list[int]]:
    return {
        1: [1, 1],
        2: [2, 2, 2, 2, 2],
        3: [3, 3, 3, 3, 3, 3, 3, 3, 2, 2],
        4: [3, 3, 3, 3, 4, 4],
    }


def _target_snapshot(
    *,
    qualification: str = "postupdate_unknown_warehouse_source",
    round_no: int = 4,
) -> dict:
    source = _warehouse_source(qualification)
    shape_code = {1: 11, 2: 12, 3: 13, 4: 22}
    dimensions = {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (2, 2)}
    skill_rows: list[dict] = []
    minimap: list[dict] = []
    runtime_id = 10_000
    local_index = 0
    for skill_id, quality in engine._AISHA_Q5_TOTAL_CELLS_SKILLS.items():
        items: list[dict] = []
        for cells in _item_plan()[quality]:
            item = {
                "runtime_id": runtime_id,
                "local_index": local_index,
                "quality": quality,
                "shape_code": shape_code[cells],
                "cells": cells,
            }
            items.append(item)
            width, height = dimensions[cells]
            minimap.append(
                {
                    "runtime_id": runtime_id,
                    "local_index": local_index,
                    "quality": quality,
                    "width": width,
                    "height": height,
                    "cells": cells,
                    "source": "packet",
                    "layout_source": "live_grid",
                }
            )
            runtime_id += 1
            local_index += 1
        skill_rows.append(
            {
                "sort": 20 + quality,
                "skill_id": skill_id,
                "hero_id": 103,
                "revealed_items": len(items),
                "revealed_items_detail": copy.deepcopy(items),
                "observed_items": copy.deepcopy(items),
                "session_id": SESSION_ID,
                "round": quality,
            }
        )
    actions = [
        {
            "sort": 30,
            "action_id": 100107,
            "result": 19.0,
            "result_field": 9,
            "result_wire_type": "float32",
            "result_raw_hex": "00009841",
            "session_id": SESSION_ID,
            "round": 4,
        },
        {
            "sort": 31,
            "action_id": 100115,
            "result": 32,
            "result_field": 7,
            "result_wire_type": "varint",
            "result_raw_hex": None,
            "session_id": SESSION_ID,
            "round": 4,
        },
    ]
    ui_actions = [
        {
            key: "" if value is None else str(value)
            for key, value in row.items()
        }
        for row in actions
    ]
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": 32,
        "counts": {"q3": 10, "q4": 6},
        "split_counts": {"white": 2, "green": 5},
        "quality_cells": {"q3": 28, "q4": 20, "q5": 19},
        "split_quality_cells": {"white": 2, "green": 10},
        "avg_cells": {},
        "quality_values": {},
        "quality_value_floors": {},
    }
    context = {
        "hero": "aisha",
        "map_id": 5606,
        "round": round_no,
        "phase": "bidding",
        "session_id": SESSION_ID,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
    }
    return {
        "hero": "aisha",
        "map_id": 5606,
        "round": round_no,
        "action_round": round_no,
        "phase": "bidding",
        "session_id": SESSION_ID,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "structured_ref_inputs": structured,
        "action_result_rows": actions,
        "skill_reveal_rows": skill_rows,
        "skill_reveals": copy.deepcopy(skill_rows),
        "minimap_grid_items": minimap,
        "ui_contract": {
            "context": context,
            "constraints": {"public_info": {}},
            "actions": {"results": ui_actions},
            "source": {"created_at": 1.0},
        },
    }


def _exact_q6_zero_snapshot(*, producer_bridge: bool) -> dict:
    snapshot = _target_snapshot()
    if producer_bridge:
        snapshot["structured_ref_inputs"]["counts"]["q6"] = 0
    else:
        snapshot["structured_ref_inputs"]["fixed_counts"] = {"q6": 0}
    snapshot["structured_ref_inputs"]["quality_cells"]["q6"] = 0
    return snapshot


def _physical_unique_q6_snapshot() -> dict:
    snapshot = _target_snapshot()
    structured = snapshot["structured_ref_inputs"]
    structured["counts"]["q6"] = 1
    structured["quality_cells"].update(q5=21, q6=20)
    root_q5 = next(
        row for row in snapshot["action_result_rows"] if row["action_id"] == 100107
    )
    ui_q5 = next(
        row
        for row in snapshot["ui_contract"]["actions"]["results"]
        if row["action_id"] == "100107"
    )
    root_q5.update(result=21.0, result_raw_hex="0000a841")
    ui_q5.update(result="21.0", result_raw_hex="0000a841")

    outline = [
        {
            "runtime_id": row["runtime_id"],
            "local_index": row["local_index"],
            "shape_code": row["width"] * 10 + row["height"],
            "cells": row["cells"],
        }
        for row in snapshot["minimap_grid_items"]
    ]
    runtime_id = 20_000
    local_index = len(outline)
    for width, height in ([(1, 3)] * 5 + [(1, 2)] * 3 + [(4, 5)]):
        outline.append(
            {
                "runtime_id": runtime_id,
                "local_index": local_index,
                "shape_code": width * 10 + height,
                "cells": width * height,
            }
        )
        runtime_id += 1
        local_index += 1
    outline_action = {
        "sort": 29,
        "action_id": 100100,
        "revealed_items": 32,
        "revealed_items_detail": copy.deepcopy(outline),
        "observed_items": copy.deepcopy(outline),
        "session_id": SESSION_ID,
        "round": 4,
    }
    snapshot["action_result_rows"].insert(0, copy.deepcopy(outline_action))
    snapshot["ui_contract"]["actions"]["results"].insert(
        0,
        {
            key: (
                copy.deepcopy(value)
                if key in {"revealed_items_detail", "observed_items"}
                else str(value)
            )
            for key, value in outline_action.items()
        },
    )
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return engine.run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _consumer_snapshot() -> dict:
    """Same lane on the existing 2524 joint-positive control.

    The isolated d96 parent still has the known q6 joint/lock atomicity defect
    for the 5606 target shape.  2524 keeps a canonical q6 joint and therefore
    exercises the real Panel/API/Tk consumers without absorbing that separate
    engine source.
    """
    snapshot = _target_snapshot()
    session_id = "2524:9000000000000048"
    for owner in (snapshot, snapshot["ui_contract"]["context"]):
        owner["map_id"] = 2524
        owner["session_id"] = session_id
    for rows in (
        snapshot["action_result_rows"],
        snapshot["skill_reveal_rows"],
        snapshot["skill_reveals"],
        snapshot["ui_contract"]["actions"]["results"],
    ):
        for row in rows:
            row["session_id"] = session_id
    snapshot["action_result_rows"][0]["result"] = 3.0
    snapshot["action_result_rows"][0]["result_raw_hex"] = "00004040"
    snapshot["ui_contract"]["actions"]["results"][0]["result"] = "3.0"
    snapshot["ui_contract"]["actions"]["results"][0][
        "result_raw_hex"
    ] = "00004040"
    snapshot["structured_ref_inputs"]["quality_cells"]["q5"] = 3
    return snapshot


def _default_and_rollback(
    snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_combos: int = 8_000,
) -> tuple[dict, dict]:
    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot, max_combos=max_combos)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot, max_combos=max_combos)
    return fixed, legacy


def _product_action_rows() -> tuple[list[dict], dict]:
    """Exercise the monitor and runtime producers, not a hand-shaped mirror."""
    results = (
        SimpleNamespace(
            action_id=100107,
            result=19.0,
            result_field=9,
            result_wire_type="float32",
            result_raw_hex="00009841",
            observed_items=(),
        ),
        SimpleNamespace(
            action_id=100115,
            result=32,
            result_field=7,
            result_wire_type="varint",
            result_raw_hex=None,
            observed_items=(),
        ),
    )
    state = SimpleNamespace(
        session_id=SESSION_ID,
        message_id=0x0027,
        inventory_items=(),
        sort_id=31,
        capture_time=1.0,
        round_no=4,
        action_results=results,
    )
    rows = _action_result_rows(
        SimpleNamespace(states=(state,), sends=()),
        {},
        session_id=SESSION_ID,
    )
    ui_actions = _ui_actions_contract({"action_result_rows": rows})
    return rows, json.loads(json.dumps(ui_actions))


def test_product_action_and_ui_producers_activate_exact_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, ui_actions = _product_action_rows()
    snapshot = _target_snapshot()
    snapshot["action_result_rows"] = rows
    snapshot["ui_contract"]["actions"] = ui_actions

    q5 = next(row for row in rows if row["action_id"] == 100107)
    total = next(row for row in rows if row["action_id"] == 100115)
    ui_q5 = next(
        row for row in ui_actions["results"] if row["action_id"] == "100107"
    )
    ui_total = next(
        row for row in ui_actions["results"] if row["action_id"] == "100115"
    )
    assert (q5["result"], q5["result_field"], q5["result_wire_type"]) == (
        19.0,
        9,
        "float32",
    )
    assert q5["result_raw_hex"] == "00009841"
    assert (ui_q5["result"], ui_q5["result_field"]) == ("19.0", "9")
    assert ui_q5["result_raw_hex"] == "00009841"
    assert (total["result"], total["result_field"], total["result_wire_type"]) == (
        32,
        7,
        "varint",
    )
    assert total["result_raw_hex"] is None
    assert (ui_total["result"], ui_total["result_field"]) == ("32", "7")
    assert ui_total["result_raw_hex"] == ""

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize("producer_bridge", [False, True])
def test_exact_q6_zero_forces_one_q5_complement_arm(
    producer_bridge: bool,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed, legacy = _default_and_rollback(
        _exact_q6_zero_snapshot(producer_bridge=producer_bridge),
        monkeypatch,
        max_combos=max_combos,
    )

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert NOTE not in ";".join(fixed.get("notes", []))


@pytest.mark.parametrize("missing", ["count", "cells"])
def test_partial_q6_zero_evidence_remains_atomic_noop(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    if missing != "count":
        snapshot["structured_ref_inputs"]["fixed_counts"] = {"q6": 0}
    if missing != "cells":
        snapshot["structured_ref_inputs"]["quality_cells"]["q6"] = 0

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}


@pytest.mark.parametrize(
    "case",
    [
        "count_bool",
        "count_float",
        "count_string",
        "count_negative",
        "cells_bool",
        "cells_string",
        "cells_nonfinite",
        "cells_fractional",
        "cells_negative",
        "zero_count_positive_cells",
        "positive_count_zero_cells",
        "count_alias_conflict",
        "cells_alias_conflict",
        "cross_input_partial_union",
        "input_mirror_conflict",
        "count_container_wrong_type",
    ],
)
def test_raw_q6_authority_invalid_conflict_and_cross_input_union_are_noop(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _exact_q6_zero_snapshot(producer_bridge=False)
    inputs = snapshot["structured_ref_inputs"]
    if case == "count_bool":
        inputs["fixed_counts"]["q6"] = False
    elif case == "count_float":
        inputs["fixed_counts"]["q6"] = 0.0
    elif case == "count_string":
        inputs["fixed_counts"]["q6"] = "0"
    elif case == "count_negative":
        inputs["fixed_counts"]["q6"] = -1
    elif case == "cells_bool":
        inputs["quality_cells"]["q6"] = False
    elif case == "cells_string":
        inputs["quality_cells"]["q6"] = "0"
    elif case == "cells_nonfinite":
        inputs["quality_cells"]["q6"] = math.nan
    elif case == "cells_fractional":
        inputs["quality_cells"]["q6"] = 0.5
    elif case == "cells_negative":
        inputs["quality_cells"]["q6"] = -1
    elif case == "zero_count_positive_cells":
        inputs["quality_cells"]["q6"] = 20
    elif case == "positive_count_zero_cells":
        inputs["fixed_counts"]["q6"] = 1
    elif case == "count_alias_conflict":
        inputs["counts"]["q6"] = 1
    elif case == "cells_alias_conflict":
        inputs["cells"] = {"q6": 1}
    elif case == "cross_input_partial_union":
        inputs["quality_cells"].pop("q6")
        snapshot["ui_contract"]["structured_ref_inputs"] = {
            "quality_cells": {"q6": 0}
        }
    elif case == "input_mirror_conflict":
        snapshot["ui_contract"]["structured_ref_inputs"] = {
            "fixed_counts": {"q6": 1},
            "quality_cells": {"q6": 20},
        }
    elif case == "count_container_wrong_type":
        inputs["fixed_counts"] = []
    else:  # pragma: no cover - table and mutation logic are one frozen contract
        raise AssertionError(case)

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_full_outline_catalog_unique_q6_forces_one_q5_arm(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed, legacy = _default_and_rollback(
        _physical_unique_q6_snapshot(),
        monkeypatch,
        max_combos=max_combos,
    )

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)


@pytest.mark.parametrize(
    "case",
    ["ui_outline_missing", "noncatalog_shape", "quality_conflict"],
)
def test_physical_unique_requires_complete_catalog_bound_outline(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _physical_unique_q6_snapshot()
    ui_rows = snapshot["ui_contract"]["actions"]["results"]
    if case == "ui_outline_missing":
        ui_rows[:] = [row for row in ui_rows if row["action_id"] != "100100"]
    elif case == "noncatalog_shape":
        for rows in (snapshot["action_result_rows"], ui_rows):
            outline = next(
                row
                for row in rows
                if str(row["action_id"]) == "100100"
            )
            for key in ("revealed_items_detail", "observed_items"):
                outline[key][-1].update(shape_code=55, cells=25)
    elif case == "quality_conflict":
        for rows in (snapshot["action_result_rows"], ui_rows):
            outline = next(
                row
                for row in rows
                if str(row["action_id"]) == "100100"
            )
            for key in ("revealed_items_detail", "observed_items"):
                outline[key][-1]["quality"] = 5
    else:  # pragma: no cover - table and mutation logic are one frozen contract
        raise AssertionError(case)

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}


STRICT_WIRE_FAIL_CLOSED_CASES = (
    "q5_root_missing_field",
    "q5_ui_missing_field",
    "q5_root_missing_wire",
    "q5_ui_missing_wire",
    "q5_root_missing_raw",
    "q5_ui_missing_raw",
    "q5_root_wrong_field",
    "q5_ui_wrong_field",
    "q5_root_wrong_wire",
    "q5_ui_wrong_wire",
    "q5_root_raw_conflict",
    "q5_ui_raw_conflict",
    "q5_root_nonfinite",
    "q5_ui_nonfinite",
    "q5_root_wrong_scalar",
    "q5_ui_wrong_scalar",
    "q5_duplicate_different",
    "q5_root_ui_value_conflict",
    "total_root_missing_field",
    "total_ui_missing_field",
    "total_root_missing_wire",
    "total_ui_missing_wire",
    "total_root_missing_raw",
    "total_ui_missing_raw",
    "total_root_wrong_field",
    "total_ui_wrong_field",
    "total_root_wrong_wire",
    "total_ui_wrong_wire",
    "total_root_raw_present",
    "total_ui_raw_present",
    "total_duplicate_different",
    "total_root_ui_value_conflict",
)


@pytest.mark.parametrize("case", STRICT_WIRE_FAIL_CLOSED_CASES)
def test_product_wire_missing_conflict_nonfinite_and_raw_fail_closed(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    root_rows = snapshot["action_result_rows"]
    ui_rows = snapshot["ui_contract"]["actions"]["results"]
    root_q5 = next(row for row in root_rows if row["action_id"] == 100107)
    ui_q5 = next(row for row in ui_rows if row["action_id"] == "100107")
    root_total = next(row for row in root_rows if row["action_id"] == 100115)
    ui_total = next(row for row in ui_rows if row["action_id"] == "100115")
    owners = {
        "q5_root": root_q5,
        "q5_ui": ui_q5,
        "total_root": root_total,
        "total_ui": ui_total,
    }

    if case.endswith("missing_field"):
        owners[case.removesuffix("_missing_field")].pop("result_field")
    elif case.endswith("missing_wire"):
        owners[case.removesuffix("_missing_wire")].pop("result_wire_type")
    elif case.endswith("missing_raw"):
        owners[case.removesuffix("_missing_raw")].pop("result_raw_hex")
    elif case == "q5_root_wrong_field":
        root_q5["result_field"] = 14
    elif case == "q5_ui_wrong_field":
        ui_q5["result_field"] = "14"
    elif case == "q5_root_wrong_wire":
        root_q5["result_wire_type"] = "varint"
    elif case == "q5_ui_wrong_wire":
        ui_q5["result_wire_type"] = "varint"
    elif case == "q5_root_raw_conflict":
        root_q5["result_raw_hex"] = "00000000"
    elif case == "q5_ui_raw_conflict":
        ui_q5["result_raw_hex"] = "00000000"
    elif case == "q5_root_nonfinite":
        root_q5["result"] = float("nan")
    elif case == "q5_ui_nonfinite":
        ui_q5["result"] = "nan"
    elif case == "q5_root_wrong_scalar":
        root_q5["result"] = 19
    elif case == "q5_ui_wrong_scalar":
        ui_q5["result"] = 19.0
    elif case == "q5_duplicate_different":
        duplicate = copy.deepcopy(root_q5)
        duplicate.update(result=18.0, result_raw_hex="00009041", sort=32)
        root_rows.append(duplicate)
        ui_duplicate = copy.deepcopy(ui_q5)
        ui_duplicate.update(result="18.0", result_raw_hex="00009041", sort="32")
        ui_rows.append(ui_duplicate)
    elif case == "q5_root_ui_value_conflict":
        ui_q5.update(result="18.0", result_raw_hex="00009041")
    elif case == "total_root_wrong_field":
        root_total["result_field"] = 14
    elif case == "total_ui_wrong_field":
        ui_total["result_field"] = "14"
    elif case == "total_root_wrong_wire":
        root_total["result_wire_type"] = "float32"
    elif case == "total_ui_wrong_wire":
        ui_total["result_wire_type"] = "float32"
    elif case == "total_root_raw_present":
        root_total["result_raw_hex"] = "20"
    elif case == "total_ui_raw_present":
        ui_total["result_raw_hex"] = "20"
    elif case == "total_duplicate_different":
        duplicate = copy.deepcopy(root_total)
        duplicate.update(result=31, sort=32)
        root_rows.append(duplicate)
        ui_duplicate = copy.deepcopy(ui_total)
        ui_duplicate.update(result="31", sort="32")
        ui_rows.append(ui_duplicate)
    elif case == "total_root_ui_value_conflict":
        ui_total["result"] = "31"
    else:  # pragma: no cover - table and mutation logic are one frozen contract
        raise AssertionError(case)

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}
    assert NOTE not in ";".join(fixed.get("notes", []))
    _assert_changed_route_no_quote(fixed)


@pytest.mark.parametrize(
    "case",
    (
        "q5_root_row_missing",
        "q5_ui_row_missing",
        "total_root_row_missing",
        "total_ui_row_missing",
        "root_container_missing",
        "root_container_none",
        "ui_actions_missing",
        "ui_results_missing",
    ),
)
def test_product_wire_requires_both_complete_mirror_containers(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    root_rows = snapshot["action_result_rows"]
    ui_actions = snapshot["ui_contract"]["actions"]
    ui_rows = ui_actions["results"]
    if case == "q5_root_row_missing":
        root_rows[:] = [row for row in root_rows if row["action_id"] != 100107]
    elif case == "q5_ui_row_missing":
        ui_rows[:] = [row for row in ui_rows if row["action_id"] != "100107"]
    elif case == "total_root_row_missing":
        root_rows[:] = [row for row in root_rows if row["action_id"] != 100115]
    elif case == "total_ui_row_missing":
        ui_rows[:] = [row for row in ui_rows if row["action_id"] != "100115"]
    elif case == "root_container_missing":
        snapshot.pop("action_result_rows")
    elif case == "root_container_none":
        snapshot["action_result_rows"] = None
    elif case == "ui_actions_missing":
        snapshot["ui_contract"].pop("actions")
    elif case == "ui_results_missing":
        ui_actions.pop("results")
    else:  # pragma: no cover - table and mutation logic are one frozen contract
        raise AssertionError(case)

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}
    assert NOTE not in ";".join(fixed.get("notes", []))


def test_finite_field14_varint_q5_stays_on_the_existing_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finite legacy/product variant is not a reason to erase generic evidence."""
    snapshot = _target_snapshot()
    root_q5 = snapshot["action_result_rows"][0]
    ui_q5 = snapshot["ui_contract"]["actions"]["results"][0]
    root_q5.update(
        result=19,
        result_field=14,
        result_wire_type="varint",
        result_raw_hex=None,
    )
    ui_q5.update(
        result="19",
        result_field="14",
        result_wire_type="varint",
        result_raw_hex="",
    )

    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)


def _synthetic_q5_convolution(q5_count: int) -> dict[int, float]:
    """Route-independent oracle for the bounded quantile compressor itself."""

    one_item = {
        30_000: 0.05,
        32_000: 0.15,
        34_000: 0.35,
        36_000: 0.30,
        38_000: 0.15,
    }
    distribution = {0: 1.0}
    for _ in range(q5_count):
        combined: dict[int, float] = defaultdict(float)
        for left_value, left_weight in distribution.items():
            for right_value, right_weight in one_item.items():
                combined[left_value + right_value] += left_weight * right_weight
        distribution = dict(combined)
    return distribution


@pytest.mark.parametrize("q5_count", [2, 3, 4, 5])
def test_route_independent_quantile_compression_stays_close_to_uncompressed_oracle(
    q5_count: int,
) -> None:
    exact = _synthetic_q5_convolution(q5_count)
    compressed = dict(
        engine._aisha_q5_total_cells_quantile_bins(  # noqa: SLF001
            exact,
            bins=engine.AISHA_Q5_TOTAL_CELLS_VALUE_CATALOG_BINS,
        )
    )
    exact_p50 = engine._raven_weighted_quantile(exact, 0.50)
    exact_p90 = engine._raven_weighted_quantile(exact, 0.90)
    compressed_p50 = engine._raven_weighted_quantile(compressed, 0.50)
    compressed_p90 = engine._raven_weighted_quantile(compressed, 0.90)

    assert len(compressed) == engine.AISHA_Q5_TOTAL_CELLS_VALUE_CATALOG_BINS
    assert sum(compressed.values()) == pytest.approx(1.0)
    assert exact_p50 is not None and compressed_p50 is not None
    assert exact_p90 is not None and compressed_p90 is not None
    assert abs(compressed_p50 - exact_p50) <= 2_000
    assert abs(compressed_p90 - exact_p90) <= 5_000


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_changed_route_5606_holds_all_q5_count_cells_value_arms(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed, legacy = _default_and_rollback(
        _target_snapshot(),
        monkeypatch,
        max_combos=max_combos,
    )

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert NOTE not in ";".join(fixed["notes"])


@pytest.mark.parametrize("round_no", [1, 2, 3, 4, 5])
def test_changed_route_5606_holds_every_round_before_feature_activation(
    round_no: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed, legacy = _default_and_rollback(
        _target_snapshot(round_no=round_no),
        monkeypatch,
    )

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)


def test_changed_route_5606_r4_r5_are_the_same_no_quote_contract() -> None:
    r4 = _run(_target_snapshot(round_no=4), max_combos=50_000)
    r5 = _run(_target_snapshot(round_no=5), max_combos=50_000)
    assert r4 == r5
    _assert_changed_route_no_quote(r4)


@pytest.mark.parametrize(
    "case",
    [
        "missing_total_count",
        "partial_q4",
        "duplicate_action_value",
        "cross_session_action",
        "cross_epoch_action",
        "duplicate_skill_footprint",
        "minimap_identity_mismatch",
        "q5_action_mismatch",
        "direct_total_cells",
        "direct_q6_cells",
        "q5_avg",
        "q5_value",
        "non_aisha",
        "settled",
        "catalog_hash",
        "catalog_incomplete",
        "state_cap",
    ],
)
def test_partial_conflict_catalog_and_scope_fail_closed(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    if case == "missing_total_count":
        snapshot["structured_ref_inputs"].pop("total_count")
        snapshot["action_result_rows"] = [
            row for row in snapshot["action_result_rows"] if row["action_id"] != 100115
        ]
        snapshot["ui_contract"]["actions"]["results"] = [
            row
            for row in snapshot["ui_contract"]["actions"]["results"]
            if row["action_id"] != "100115"
        ]
    elif case == "partial_q4":
        snapshot["skill_reveal_rows"] = snapshot["skill_reveal_rows"][:-1]
        snapshot["skill_reveals"] = snapshot["skill_reveals"][:-1]
    elif case == "duplicate_action_value":
        snapshot["action_result_rows"].append(
            {"action_id": 100107, "result": 18, "result_field": 14}
        )
    elif case == "cross_session_action":
        snapshot["action_result_rows"][0]["session_id"] = "5606:9000000000000049"
    elif case == "cross_epoch_action":
        snapshot["action_result_rows"][0]["epoch"] = 9000000000000049
    elif case == "duplicate_skill_footprint":
        snapshot["skill_reveals"][0]["observed_items"][0]["shape_code"] = 12
        snapshot["skill_reveals"][0]["observed_items"][0]["cells"] = 2
        snapshot["skill_reveals"][0]["revealed_items_detail"][0]["shape_code"] = 12
        snapshot["skill_reveals"][0]["revealed_items_detail"][0]["cells"] = 2
    elif case == "minimap_identity_mismatch":
        snapshot["minimap_grid_items"][0]["runtime_id"] += 99_000
    elif case == "q5_action_mismatch":
        snapshot["action_result_rows"][0]["result"] = 18
    elif case == "direct_total_cells":
        snapshot["structured_ref_inputs"]["total_cells"] = 86
    elif case == "direct_q6_cells":
        snapshot["structured_ref_inputs"]["quality_cells"]["q6"] = 5
    elif case == "q5_avg":
        snapshot["structured_ref_inputs"]["avg_cells"]["q5"] = 3.8
    elif case == "q5_value":
        snapshot["structured_ref_inputs"]["quality_values"]["q5"] = 180_000
    elif case == "non_aisha":
        snapshot["hero"] = "ahmed"
        snapshot["structured_ref_inputs"]["hero"] = "ahmed"
        snapshot["ui_contract"]["context"]["hero"] = "ahmed"
    elif case == "settled":
        snapshot["phase"] = "settled"
        snapshot["ui_contract"]["context"]["phase"] = "settled"
    elif case == "catalog_hash":
        monkeypatch.setattr(
            engine,
            "_aisha_q5_total_cells_catalog_generation_status",
            lambda: ("catalog_generation_hash_mismatch", ""),
        )
    elif case == "catalog_incomplete":
        monkeypatch.setattr(
            engine,
            "_validated_q5_catalog",
            lambda: ((), (), "catalog_mismatch"),
        )
    elif case == "state_cap":
        monkeypatch.setattr(engine, "AISHA_Q5_TOTAL_CELLS_VALUE_MAX_STATES", 1)

    engine._aisha_q5_total_cells_value_distribution.cache_clear()
    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)

    assert fixed == legacy
    assert fixed["q5_total_cells_value_summary"] == {}
    assert NOTE not in ";".join(fixed.get("notes", []))
    engine._aisha_q5_total_cells_value_distribution.cache_clear()


@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ],
)
def test_source_qualification_is_preserved(
    qualification: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _target_snapshot(qualification=qualification)
    fixed, legacy = _default_and_rollback(snapshot, monkeypatch)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "source.json",
    )

    assert fixed == legacy
    _assert_changed_route_no_quote(fixed)
    assert summary["reference"]["readiness"] == "no_reachable_combo"
    assert summary["reference"]["decision_range"] == "-"
    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] is (qualification == "official_random_warehouse")


def test_summary_api_panel_and_tk_share_one_engine_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _consumer_snapshot()
    monkeypatch.delenv(ROLLBACK, raising=False)
    direct = _run(snapshot, max_combos=50_000)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "summary.json",
    )
    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        panel.make_handler(
            panel.ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            api = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    tk_adapter = overlay.AhmadTkOverlay.__new__(overlay.AhmadTkOverlay)
    tk_payload = tk_adapter._manual_result_summary(copy.deepcopy(snapshot))

    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "value_p25",
        "value_p50",
        "value_p75",
        "quality_count_ranges",
        "quality_cells_ranges",
        "q5_total_cells_value_summary",
    ):
        assert summary["ahmed_ref"][key] == direct[key]
        assert api["ahmed_ref"][key] == direct[key]
    assert summary["q5_total_cells_value_summary"] == direct[
        "q5_total_cells_value_summary"
    ]
    assert api["q5_total_cells_value_summary"] == direct[
        "q5_total_cells_value_summary"
    ]
    assert summary["reference"]["balanced"] == tk_payload["reference"]["balanced"]
    assert summary["reference"]["decision_range"] == tk_payload["reference"][
        "decision_range"
    ]


def test_changed_route_5606_panel_publishes_no_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _target_snapshot()
    monkeypatch.delenv(ROLLBACK, raising=False)
    direct = _run(snapshot)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "5606.json",
    )

    _assert_changed_route_no_quote(direct)
    assert summary["reference"]["readiness"] == "no_reachable_combo"
    assert summary["reference"]["source"] == "ref_waiting"
    assert summary["reference"]["decision_range"] == "-"
    assert summary["quality_composition"]["status"] == "unavailable"
    assert summary["ahmed_ref"]["status"] == "no_reachable_combo"
    assert summary["q5_total_cells_value_summary"] == {}


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
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
    "q5": result["quality_count_ranges"].get("q5"),
    "q6": result["quality_count_ranges"].get("q6"),
    "summary": result["q5_total_cells_value_summary"],
    "notes": result["notes"],
}))
"""


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter/local source
        [sys.executable, "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    "case",
    [
        "multi_arm",
        "exact_q6_zero",
        "physical_unique",
    ],
)
@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_changed_route_fresh_process_default_and_rollback_are_no_quote(
    case: str,
    max_combos: int,
) -> None:
    if case == "multi_arm":
        snapshot = _target_snapshot()
    elif case == "exact_q6_zero":
        snapshot = _exact_q6_zero_snapshot(producer_bridge=True)
    elif case == "physical_unique":
        snapshot = _physical_unique_q6_snapshot()
    else:  # pragma: no cover - table and fixture choice are one frozen contract
        raise AssertionError(case)
    fixed = _fresh_process(
        snapshot,
        max_combos=max_combos,
        rollback=False,
    )
    legacy = _fresh_process(
        snapshot,
        max_combos=max_combos,
        rollback=True,
    )

    assert fixed == legacy
    assert fixed["status"] == "no_reachable_combo"
    assert fixed["quotes"] == [None, None, None]
    assert fixed["q5"] is None
    assert fixed["q6"] is None
    assert fixed["summary"] == {}
    assert fixed["notes"] == [
        f"{engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:"
        f"{CHANGED_ROUTE_MAP_ID}:authority_input_unavailable"
    ]


_UPDATE606_FINAL_INPUT_ROOT_ENV = "BIDKING_UPDATE606_FINAL_INPUT_ROOT"
_UPDATE606_WEIGHTS_RELATIVE = (
    "data/processed/drop_weights_all_maps_0627.json"
)
_UPDATE606_IDENTITY_RELATIVES = (
    "data/raw/tables/Item.txt",
    "data/processed/items_droppable.json",
    _UPDATE606_WEIGHTS_RELATIVE,
)
_UPDATE606_CONSUMER_RELATIVES = _UPDATE606_IDENTITY_RELATIVES + (
    "data/processed/items.json",
)


def _update606_final_input_root() -> tuple[Path, dict]:
    configured = os.environ.get(_UPDATE606_FINAL_INPUT_ROOT_ENV)
    if not configured:
        pytest.skip(f"{_UPDATE606_FINAL_INPUT_ROOT_ENV} is not assembled")
    input_root = Path(configured)
    final_root = input_root if (input_root / "receipts").is_dir() else input_root / "final"
    receipt_path = final_root / "receipts" / "generation.json"
    if not receipt_path.is_file():
        pytest.fail(f"missing update606 final generation receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    return final_root, receipt


def _update606_package_manifest(receipt: dict) -> dict:
    package = receipt["package"]
    return {
        "schema": "bidking_package_data_fingerprints_v1",
        "baseline_id": package["baseline_id"],
        "files": {
            relative: {
                "size": int(spec["bytes"]),
                "sha256": str(spec["sha256"]),
            }
            for relative, spec in package["identities"].items()
        },
    }


def _clear_update606_runtime_caches() -> None:
    from bidking_lab.inference import v2

    seen: set[int] = set()
    for module in (engine, v2):
        for cached in vars(module).values():
            cache_clear = getattr(cached, "cache_clear", None)
            if callable(cache_clear) and id(cached) not in seen:
                seen.add(id(cached))
                cache_clear()


def _bind_update606_runtime_root(
    root: Path,
    manifest: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    moat_json=engine._moat_json,
) -> None:
    manifest_path = root / "scripts" / "package_data_fingerprints.json"

    def bound_moat_json(path: Path):
        if Path(path) == manifest_path:
            return manifest
        return moat_json(path)

    monkeypatch.setenv("BIDKING_PROJECT_ROOT", str(root))
    monkeypatch.setattr(engine, "ROOT", root)
    monkeypatch.setattr(
        engine,
        "DROP_WEIGHT_ITEMS_PATH",
        root / _UPDATE606_WEIGHTS_RELATIVE,
    )
    monkeypatch.setattr(engine, "_moat_json", bound_moat_json)
    _clear_update606_runtime_caches()


@pytest.fixture
def update606_final_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict, dict]:
    final_root, receipt = _update606_final_input_root()
    manifest = _update606_package_manifest(receipt)
    _bind_update606_runtime_root(final_root, manifest, monkeypatch)
    yield final_root, receipt, manifest
    _clear_update606_runtime_caches()


def test_update606_filtered_weights_bind_exact_generation_and_inventory(
    update606_final_runtime: tuple[Path, dict, dict],
) -> None:
    _final_root, receipt, _manifest = update606_final_runtime
    package = receipt["package"]
    weight_receipt = receipt["table_generation"]["legacy_distribution"][
        "outputs"
    ]["drop_weights_all_maps_0627"]["output"]

    assert engine.CURRENT_RANKMAP_PACKAGE_GENERATION == receipt[
        "package_generation"
    ]
    assert engine.AISHA_Q5_TOTAL_CELLS_PACKAGE_GENERATION == receipt[
        "raw_drop_closure"
    ]["scope"]["package_generation"]
    assert engine.AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID == package[
        "baseline_id"
    ]
    assert engine.AISHA_Q5_TOTAL_CELLS_DROP_WEIGHTS_BYTES == weight_receipt[
        "bytes"
    ]
    assert engine.AISHA_Q5_TOTAL_CELLS_DROP_WEIGHTS_SHA256 == weight_receipt[
        "sha256"
    ]
    assert engine._aisha_q5_total_cells_catalog_generation_status() == (
        "ok",
        package["baseline_id"],
    )

    weights = engine._load_drop_weight_items()
    map_ids = sorted(weights)
    assert len(map_ids) == 101
    assert hashlib.sha256(
        json.dumps(map_ids, separators=(",", ":")).encode("ascii")
    ).hexdigest() == engine.AISHA_Q5_TOTAL_CELLS_DROP_WEIGHTS_MAP_IDS_SHA256
    route_scope = receipt["raw_drop_closure"]["scope"]
    excluded_route_ids = {
        *route_scope["changed_route_map_ids"],
        *route_scope["new_only_map_ids"],
        *route_scope["legacy_only_map_ids"],
    }
    assert {5506, 6001}.issubset(excluded_route_ids)
    assert excluded_route_ids.issubset(
        engine._AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS
    )
    assert excluded_route_ids.isdisjoint(map_ids)
    assert not engine._AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS.intersection(
        map_ids
    )

    pairs, rows, catalog_status, _generation = (
        engine._validated_map_quality_catalog(2524, 5)
    )
    assert catalog_status == "ok"
    assert pairs and rows
    target_cells = rows[0][2]
    assert engine._aisha_map_quality_value_distributions(
        2524,
        5,
        ((1, target_cells),),
    )[0] == "ok"
    assert engine._aisha_q5_total_cells_value_distribution(2524, 1, 2)[0] == "ok"


def test_update606_filtered_weights_restore_direct_prepared_and_panel_surface(
    update606_final_runtime: tuple[Path, dict, dict],
    tmp_path: Path,
) -> None:
    snapshot = _consumer_snapshot()
    direct = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=50_000,
    ).as_dict()
    prepared = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=50_000,
    ).as_dict()
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "update606-final.json",
    )

    for label, result in (
        ("direct", direct),
        ("prepared", prepared),
        ("panel", summary["ahmed_ref"]),
    ):
        assert result["status"] == "ok"
        assert any(
            note == NOTE or note.startswith(f"{NOTE}:")
            for note in result["notes"]
        ), (label, result["q5_total_cells_value_summary"], result["notes"])
        assert result["q5_total_cells_value_summary"]["status"] == (
            "conditional_complete"
        )
    assert direct["q5_total_cells_value_summary"] == prepared[
        "q5_total_cells_value_summary"
    ]
    assert summary["q5_total_cells_value_summary"] == prepared[
        "q5_total_cells_value_summary"
    ]


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("missing_weight_spec", "catalog_generation_incomplete"),
        ("wrong_weight_spec", "catalog_generation_mixed"),
        ("legacy_weight_spec", "catalog_generation_mixed"),
        ("wrong_baseline", "catalog_generation_malformed"),
    ],
)
def test_update606_mixed_or_wrong_manifest_fails_closed_for_both_consumers(
    case: str,
    expected_status: str,
    update606_final_runtime: tuple[Path, dict, dict],
) -> None:
    _final_root, receipt, manifest = update606_final_runtime
    files = manifest["files"]
    if case == "missing_weight_spec":
        files.pop(_UPDATE606_WEIGHTS_RELATIVE)
    elif case == "wrong_weight_spec":
        files[_UPDATE606_WEIGHTS_RELATIVE]["sha256"] = "0" * 64
    elif case == "legacy_weight_spec":
        legacy = receipt["table_generation"]["legacy_distribution"][
            "outputs"
        ]["drop_weights_all_maps_0627"]["source"]
        files[_UPDATE606_WEIGHTS_RELATIVE] = {
            "size": legacy["bytes"],
            "sha256": legacy["sha256"],
        }
    elif case == "wrong_baseline":
        manifest["baseline_id"] = "package-data-legacy"
    else:  # pragma: no cover - frozen parameter table
        raise AssertionError(case)
    _clear_update606_runtime_caches()

    assert engine._aisha_q5_total_cells_catalog_generation_status()[0] == (
        expected_status
    )
    assert engine._load_drop_weight_items() == {}
    assert engine._aisha_map_quality_value_distributions(2524, 5, ((1, 2),))[0] == (
        expected_status
    )
    assert engine._aisha_q5_total_cells_value_distribution(2524, 1, 2)[0] == (
        expected_status
    )


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("missing", "catalog_generation_unavailable"),
        ("wrong", "drop_weight_generation_hash_mismatch"),
        ("legacy_145", "drop_weight_generation_hash_mismatch"),
    ],
)
def test_update606_missing_wrong_or_legacy_weight_payload_fails_closed(
    case: str,
    expected_status: str,
    update606_final_runtime: tuple[Path, dict, dict],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    final_root, _receipt, manifest = update606_final_runtime
    for relative in _UPDATE606_IDENTITY_RELATIVES[:-1]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_root / relative, target)
    weight_target = tmp_path / _UPDATE606_WEIGHTS_RELATIVE
    weight_target.parent.mkdir(parents=True, exist_ok=True)
    if case == "wrong":
        payload = (final_root / _UPDATE606_WEIGHTS_RELATIVE).read_bytes()
        weight_target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    elif case == "legacy_145":
        input_root = final_root.parent
        shutil.copy2(
            input_root / "legacy" / "derived" / weight_target.name,
            weight_target,
        )
    elif case != "missing":  # pragma: no cover - frozen parameter table
        raise AssertionError(case)
    _bind_update606_runtime_root(tmp_path, manifest, monkeypatch)

    assert engine._aisha_q5_total_cells_catalog_generation_status()[0] == (
        expected_status
    )
    assert engine._load_drop_weight_items() == {}
    assert engine._aisha_map_quality_value_distributions(2524, 5, ((1, 2),))[0] == (
        expected_status
    )
    assert engine._aisha_q5_total_cells_value_distribution(2524, 1, 2)[0] == (
        expected_status
    )


def test_update606_encrypted_plaintext_identity_reactivates_runtime(
    update606_final_runtime: tuple[Path, dict, dict],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from bidking_lab.secure import data_crypt

    final_root, _receipt, manifest = update606_final_runtime
    key = bytes(range(32))
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: key)
    for relative in _UPDATE606_CONSUMER_RELATIVES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            data_crypt.encrypt_compressed_bytes(
                (final_root / relative).read_bytes(),
                key,
                member_path=relative,
            )
        )
    _bind_update606_runtime_root(tmp_path, manifest, monkeypatch)

    assert engine._aisha_q5_total_cells_catalog_generation_status() == (
        "ok",
        engine.AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID,
    )
    assert len(engine._load_drop_weight_items()) == 101
    assert engine._aisha_q5_total_cells_value_distribution(2524, 1, 2)[0] == "ok"
