"""BUG-M gate (#12): 至宝估价 (100163/100162) —— 用价格反查目录, 把"只给了个价"接成完整证据.

群友 (5609): "他不算至宝估价的". 实锤: the tool's observed item carries ONLY `value=458000`
(quality=None, no outline, just a rough anchor point), and the engine read that row solely to derive a
max-QUALITY ceiling — a path that needs `quality`, which this tool never sends. So the price was
dropped entirely.

作者给的机制: "这个价格在游戏中一般唯一对应一个藏品, 基本等同于抽检出了这个藏品". Catalog check:
585 distinct collectible values, 84% belong to exactly ONE item. 458,000 -> 1916003 = RED, 3x2 = 6 cells.
And the settled truth of that very game: 红 1件/6格/458,000 (top item 仿生机械宠物) — every field matches.

Wiring: min_count >= 1, cell floor >= its footprint, value floor >= its price, for the tier the price
identifies. Ambiguous prices (16% share a value) only contribute when EVERY candidate agrees on the
tier, and the cells floor takes the MINIMUM across candidates. Kill: BIDKING_DISABLE_TREASURE_APPRAISAL=1.
"""

from __future__ import annotations

import copy
from http.server import ThreadingHTTPServer
import json
import sys
from threading import Thread
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))
if str(ROOT / "hero_ref" / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "tools"))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_tk_overlay as tk_overlay  # noqa: E402

from ahmad_ref_engine import (  # noqa: E402
    TREASURE_APPRAISAL_NOTE,
    TREASURE_APPRAISAL_CONTEXT_STATUS_ABSENT,
    TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT,
    TREASURE_APPRAISAL_CONTEXT_STATUS_OK,
    _catalog_items_by_value,
    _strict_treasure_appraisal_evidence,
    _strip_untrusted_treasure_appraisal_markers,
    _treasure_appraisal_match,
    _TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS,
    _TREASURE_APPRAISAL_SANITIZE_MAX_CONTAINERS,
    _TREASURE_APPRAISAL_SANITIZE_MAX_DEPTH,
    _TREASURE_APPRAISAL_SANITIZE_MAX_TOTAL_ITEMS,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)

KILL = "BIDKING_DISABLE_TREASURE_APPRAISAL"
LEGACY_WIRE_MARKER = "_treasure_appraisal_wire_conflict"
FIXTURE = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag5609_treasure"
TRUTH_VALUE = 901_271  # settled: 红 1件/6格/458,000 (top item 仿生机械宠物)


class _DunderTrap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _raise(self, name: str):
        self.calls.append(name)
        raise RuntimeError(f"unexpected dunder call: {name}")

    def __eq__(self, _other):
        return self._raise("__eq__")

    def __str__(self) -> str:
        return self._raise("__str__")

    def __float__(self) -> float:
        return self._raise("__float__")

    def __int__(self) -> int:
        return self._raise("__int__")

    def __bool__(self) -> bool:
        return self._raise("__bool__")

    def __round__(self, _ndigits=None):
        return self._raise("__round__")


class _IntSubclassTrap(int):
    def __new__(cls, value: int):
        instance = int.__new__(cls, value)
        instance.calls = []
        return instance

    def _raise(self, name: str):
        self.calls.append(name)
        raise RuntimeError(f"unexpected int-subclass dunder call: {name}")

    def __eq__(self, _other):
        return self._raise("__eq__")

    def __str__(self) -> str:
        return self._raise("__str__")

    def __float__(self) -> float:
        return self._raise("__float__")

    def __int__(self) -> int:
        return self._raise("__int__")

    def __bool__(self) -> bool:
        return self._raise("__bool__")

    def __round__(self, _ndigits=None):
        return self._raise("__round__")


class _DictSubclassTrap(dict):
    def __init__(self, value: dict) -> None:
        dict.__init__(self, value)
        self.calls: list[str] = []

    def get(self, *_args, **_kwargs):
        self.calls.append("get")
        raise RuntimeError("unexpected dict-subclass get")


class _ListSubclassTrap(list):
    def __init__(self, value: list) -> None:
        list.__init__(self, value)
        self.calls: list[str] = []

    def __iter__(self):
        self.calls.append("iter")
        raise RuntimeError("unexpected list-subclass iter")

    def __len__(self):
        self.calls.append("len")
        raise RuntimeError("unexpected list-subclass len")

    def __getitem__(self, _index):
        self.calls.append("getitem")
        raise RuntimeError("unexpected list-subclass getitem")


def _malformed_scalar(kind: str, valid_int: int):
    if kind == "object":
        return object()
    if kind == "int_subclass":
        return _IntSubclassTrap(valid_int)
    if kind == "proxy":
        return _DunderTrap()
    raise AssertionError(kind)


def _single_dunder_trap(name: str):
    def _raise(instance, *_args):
        instance.calls.append(name)
        raise RuntimeError(f"unexpected isolated dunder call: {name}")

    cls = type(f"SingleDunderTrap_{name.strip('_')}", (), {name: _raise})
    instance = cls()
    instance.calls = []
    return instance


def _marker_value(kind: str):
    if kind == "true":
        return True
    if kind == "false":
        return False
    if kind == "one":
        return 1
    if kind == "string":
        return "true"
    if kind == "int_subclass":
        return _IntSubclassTrap(1)
    if kind == "proxy":
        return _DunderTrap()
    raise AssertionError(kind)


def _contains_legacy_wire_marker(value) -> bool:
    if type(value) is dict:  # noqa: E721 - inspect exact JSON containers only
        return any(
            (type(key) is str and key == LEGACY_WIRE_MARKER)  # noqa: E721
            or _contains_legacy_wire_marker(nested)
            for key, nested in dict.items(value)
        )
    if type(value) is list:  # noqa: E721 - inspect exact JSON containers only
        return any(_contains_legacy_wire_marker(item) for item in value)
    return False


def _snapshot(
    value: int = 266050,
    *,
    hero: str = "gabriela",
    map_id: int = 5611,
    round_no: int = 5,
    total_count: int = 36,
) -> dict:
    now = time.time()
    session_id = f"{map_id}:1"
    action = {
        "sort": 30,
        "time": "2026-08-17 20:00:00.000",
        "action_id": 100163,
        "tool": "至宝估价",
        "result": None,
        "result_field": None,
        "result_wire_type": None,
        "result_raw_hex": None,
        "session_id": session_id,
        "round": round_no,
        "source_message_id": 0x0027,
        "source_kind": "direct_action_response",
        "revealed_items": 1,
        "revealed_items_detail": [
            {
                "local_index": 30,
                "runtime_id": 9003,
                "item_id": None,
                "item_name": "",
                "quality": None,
                "value": value,
                "shape_code": None,
                "cells": None,
            }
        ],
    }
    action_mirror = {
        key: "" if item is None else str(item)
        for key, item in action.items()
        if key != "revealed_items_detail"
    }
    return {
        "schema_version": 1,
        "source": "support_safe_regression",
        "created_at": now,
        "session_id": session_id,
        "hero": hero,
        "map_id": map_id,
        "model_map_id": map_id,
        "round": round_no,
        "action_round": round_no,
        "observed_round": round_no,
        "phase": "bidding",
        "structured_ref_inputs": {"total_count": total_count},
        "action_result_rows": [action],
        "ui_contract": {
            "context": {
                "session_id": session_id,
                "hero": hero,
                "map_id": map_id,
                "model_map_id": map_id,
                "round": round_no,
                "action_round": round_no,
                "observed_round": round_no,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": [action_mirror]},
            "source": {"created_at": now},
            "truth": {"available": False},
            "baseline": {},
            "diagnostics": {},
        },
    }


def _strict_status(snapshot: dict) -> str:
    context = snapshot["ui_contract"]["context"]
    status, _match = _strict_treasure_appraisal_evidence(
        snapshot,
        hero=str(context.get("hero") or ""),
        phase=str(context.get("phase") or ""),
        map_id=context.get("map_id"),
        current_session=context.get("session_id"),
        current_round=context.get("round"),
    )
    return status


def test_catalog_price_identifies_the_item() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    match = _treasure_appraisal_match(458000)
    assert match is not None
    key, cells, value = match
    assert key == "q6" and cells == 6 and value == 458000.0  # 1916003 仿生机械宠物, 3x2

    # a price nobody carries -> no match (never invent a reveal)
    assert _treasure_appraisal_match(12345) is None
    assert _treasure_appraisal_match(0) is None
    assert _treasure_appraisal_match(None) is None


def test_gabriela_5611_appraisal_266050_counts_the_revealed_item() -> None:
    """群友 5611/R5：266050 不只参与价格，也必须计入红品件数与格数下限。"""
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")

    assert _treasure_appraisal_match(266050) == ("q6", 4, 266050.0)
    snapshot = _snapshot()

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_OK
    assert evidence.min_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert evidence.quality_value_floors.get("q6") == 266050.0
    assert any(
        str(note).startswith(TREASURE_APPRAISAL_NOTE)
        for note in evidence.source_notes
    )


def test_malformed_treasure_value_dunders_are_never_invoked() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    trap = _DunderTrap()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = trap

    prepared = prepare_reference_engine_snapshot(snapshot)
    evidence = extract_evidence(prepared)
    result = run_reference_engine(prepared, max_combos=8_000)

    assert prepared is not snapshot
    assert len(snapshot["action_result_rows"]) == 1
    assert prepared["action_result_rows"] == []
    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT
    assert _strict_status(prepared) == TREASURE_APPRAISAL_CONTEXT_STATUS_ABSENT
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert result.evidence == evidence
    assert trap.calls == []


def test_untrusted_root_marker_cannot_bypass_target_row_quarantine() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    snapshot[LEGACY_WIRE_MARKER] = True
    trap = _DunderTrap()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["runtime_id"] = trap

    evidence = extract_evidence(snapshot)

    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert trap.calls == []


def _attach_treasure_container_cycle(owner: dict, cycle_kind: str) -> None:
    if cycle_kind == "self":
        owner["untrusted_cycle"] = owner
        return
    if cycle_kind == "pair":
        other: dict = {}
        owner["untrusted_cycle"] = other
        other["back"] = owner
        return
    if cycle_kind == "list":
        cycle: list = []
        cycle.append(cycle)
        owner["untrusted_cycle"] = cycle
        return
    raise AssertionError(cycle_kind)


@pytest.mark.parametrize(
    "owner_name",
    ["root", "ui_contract", "context", "action", "item"],
)
@pytest.mark.parametrize("cycle_kind", ["self", "pair", "list"])
@pytest.mark.parametrize("with_marker", [False, True])
def test_treasure_container_cycles_fail_closed_without_mutating_snapshot(
    owner_name: str,
    cycle_kind: str,
    with_marker: bool,
) -> None:
    """Every raw local-container cycle must stop before target evidence consumers."""
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    owners = {
        "root": snapshot,
        "ui_contract": snapshot["ui_contract"],
        "context": snapshot["ui_contract"]["context"],
        "action": snapshot["action_result_rows"][0],
        "item": snapshot["action_result_rows"][0]["revealed_items_detail"][0],
    }
    owner = owners[owner_name]
    if with_marker:
        owner[LEGACY_WIRE_MARKER] = "forged"
    _attach_treasure_container_cycle(owner, cycle_kind)

    started = time.monotonic()
    direct = extract_evidence(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    prepared_evidence = extract_evidence(prepared)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert direct == prepared_evidence
    assert direct.quality_cell_floors.get("q6") is None
    assert direct.quality_value_floors.get("q6") is None
    assert type(prepared) is dict  # noqa: E721 - exact safe output contract
    assert "action_result_rows" not in prepared
    assert not _contains_legacy_wire_marker(prepared)
    assert owner["untrusted_cycle"] is owner or cycle_kind != "self"


@pytest.mark.parametrize(
    "trap",
    [_DictSubclassTrap({"nested": 1}), _ListSubclassTrap([{"nested": 1}])],
)
def test_treasure_container_subclasses_fail_closed_without_protocol_calls(trap) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    snapshot["untrusted_container"] = trap

    started = time.monotonic()
    direct = extract_evidence(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert direct.quality_cell_floors.get("q6") is None
    assert direct.quality_value_floors.get("q6") is None
    assert type(prepared) is dict  # noqa: E721 - exact safe output contract
    assert "action_result_rows" not in prepared
    assert trap.calls == []


def test_treasure_shared_acyclic_marker_branch_remains_usable_and_shared() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    shared = {LEGACY_WIRE_MARKER: "forged", "nested": ["safe"]}
    snapshot["shared"] = shared
    snapshot["ui_contract"]["shared"] = shared

    prepared = prepare_reference_engine_snapshot(snapshot)
    evidence = extract_evidence(prepared)

    assert evidence.min_counts.get("q6") == 1
    assert prepared["shared"] == prepared["ui_contract"]["shared"] == {"nested": ["safe"]}
    assert LEGACY_WIRE_MARKER not in prepared["shared"]
    assert snapshot["shared"] is snapshot["ui_contract"]["shared"] is shared
    assert shared[LEGACY_WIRE_MARKER] == "forged"


def test_prepared_snapshot_rejects_a_reinserted_container_cycle() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    prepared = prepare_reference_engine_snapshot(_snapshot())
    action = prepared["action_result_rows"][0]
    _attach_treasure_container_cycle(action, "pair")

    started = time.monotonic()
    evidence = extract_evidence(prepared)
    replayed = prepare_reference_engine_snapshot(prepared)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert type(replayed) is dict  # noqa: E721 - exact safe output contract
    assert "action_result_rows" not in replayed
    assert action["untrusted_cycle"]["back"] is action


def _snapshot_with_treasure_depth(depth: int) -> dict:
    snapshot = _snapshot()
    current = snapshot
    for index in range(depth):
        nested: dict = {}
        current[f"nested_{index}"] = nested
        current = nested
    return snapshot


@pytest.mark.parametrize(
    "depth, accepted",
    [
        (_TREASURE_APPRAISAL_SANITIZE_MAX_DEPTH - 1, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_DEPTH, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_DEPTH + 1, False),
    ],
)
def test_treasure_container_depth_boundary_is_bounded(
    depth: int, accepted: bool
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot_with_treasure_depth(depth)

    started = time.monotonic()
    evidence = extract_evidence(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    if accepted:
        assert evidence.min_counts.get("q6") == 1
        assert prepared["action_result_rows"]
    else:
        assert evidence.quality_cell_floors.get("q6") is None
        assert "action_result_rows" not in prepared


@pytest.mark.parametrize(
    "size, accepted",
    [
        (_TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS - 1, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS + 1, False),
    ],
)
def test_treasure_collection_size_boundary_is_bounded(
    size: int, accepted: bool
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    snapshot["untrusted_wide"] = list(range(size))

    started = time.monotonic()
    evidence = extract_evidence(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    if accepted:
        assert evidence.min_counts.get("q6") == 1
        assert prepared["action_result_rows"]
    else:
        assert evidence.quality_cell_floors.get("q6") is None
        assert "action_result_rows" not in prepared


@pytest.mark.parametrize(
    "total_nodes, accepted",
    [
        (_TREASURE_APPRAISAL_SANITIZE_MAX_CONTAINERS - 1, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_CONTAINERS, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_CONTAINERS + 1, False),
    ],
)
def test_treasure_container_node_boundary_is_bounded(
    total_nodes: int, accepted: bool
) -> None:
    snapshot = {"nodes": [{} for _ in range(total_nodes - 2)]}

    started = time.monotonic()
    sanitized, rejected = _strip_untrusted_treasure_appraisal_markers(snapshot)
    evidence = extract_evidence(snapshot)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert (sanitized is snapshot) is accepted
    assert rejected is (not accepted)
    assert evidence.quality_cell_floors.get("q6") is None


@pytest.mark.parametrize(
    "total_items, accepted",
    [
        (_TREASURE_APPRAISAL_SANITIZE_MAX_TOTAL_ITEMS - 1, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_TOTAL_ITEMS, True),
        (_TREASURE_APPRAISAL_SANITIZE_MAX_TOTAL_ITEMS + 1, False),
    ],
)
def test_treasure_container_total_item_boundary_is_bounded(
    total_items: int, accepted: bool
) -> None:
    nested_items = total_items - 4  # root key + owner list of three child lists
    lengths = [
        _TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS,
        _TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS,
        nested_items - 2 * _TREASURE_APPRAISAL_SANITIZE_MAX_COLLECTION_ITEMS,
    ]
    snapshot = {"wide": [list(range(length)) for length in lengths]}

    started = time.monotonic()
    sanitized, rejected = _strip_untrusted_treasure_appraisal_markers(snapshot)
    evidence = extract_evidence(snapshot)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert (sanitized is snapshot) is accepted
    assert rejected is (not accepted)
    assert evidence.quality_cell_floors.get("q6") is None


@pytest.mark.parametrize(
    "marker_kind",
    ["true", "false", "one", "string", "int_subclass", "proxy"],
)
@pytest.mark.parametrize(
    ("owner", "field", "valid_int"),
    [
        ("row", "revealed_items", 1),
        ("item", "runtime_id", 9003),
        ("item", "quality", 6),
        ("item", "cells", 4),
        ("item", "value", 266050),
    ],
)
def test_untrusted_marker_variants_never_bypass_malformed_target_quarantine(
    monkeypatch,
    marker_kind: str,
    owner: str,
    field: str,
    valid_int: int,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    marker = _marker_value(marker_kind)
    malformed = _DunderTrap()
    snapshot[LEGACY_WIRE_MARKER] = marker
    target = (
        snapshot["action_result_rows"][0]
        if owner == "row"
        else snapshot["action_result_rows"][0]["revealed_items_detail"][0]
    )
    target[field] = malformed

    monkeypatch.delenv(KILL, raising=False)
    direct = extract_evidence(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    prepared_evidence = extract_evidence(prepared)
    monkeypatch.setenv(KILL, "1")
    rollback = extract_evidence(snapshot)

    assert direct == prepared_evidence == rollback
    assert direct.quality_cell_floors.get("q6") is None
    assert direct.quality_value_floors.get("q6") is None
    assert snapshot[LEGACY_WIRE_MARKER] is marker
    assert target[field] is malformed
    assert not _contains_legacy_wire_marker(prepared)
    assert LEGACY_WIRE_MARKER not in json.dumps(prepared)
    assert malformed.calls == []
    if hasattr(marker, "calls"):
        assert marker.calls == []


@pytest.mark.parametrize(
    "marker_kind",
    ["true", "false", "one", "string", "int_subclass", "proxy"],
)
def test_clean_untrusted_marker_is_stripped_without_suppressing_valid_appraisal(
    marker_kind: str,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    marker = _marker_value(marker_kind)
    snapshot[LEGACY_WIRE_MARKER] = marker
    snapshot["ui_contract"][LEGACY_WIRE_MARKER] = marker
    snapshot["ui_contract"]["context"][LEGACY_WIRE_MARKER] = marker
    snapshot["action_result_rows"][0][LEGACY_WIRE_MARKER] = marker
    snapshot["action_result_rows"][0]["revealed_items_detail"][0][
        LEGACY_WIRE_MARKER
    ] = marker

    prepared = prepare_reference_engine_snapshot(snapshot)
    evidence = extract_evidence(prepared)

    assert _strict_status(prepared) == TREASURE_APPRAISAL_CONTEXT_STATUS_OK
    assert evidence.min_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert evidence.quality_value_floors.get("q6") == 266050.0
    assert _contains_legacy_wire_marker(snapshot)
    assert not _contains_legacy_wire_marker(prepared)
    assert LEGACY_WIRE_MARKER not in json.dumps(prepared)
    if hasattr(marker, "calls"):
        assert marker.calls == []


def test_prepared_malformed_snapshot_replay_stays_sanitized_without_marker() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    snapshot[LEGACY_WIRE_MARKER] = "forged"
    trap = _DunderTrap()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = trap

    prepared = prepare_reference_engine_snapshot(snapshot)
    replayed = prepare_reference_engine_snapshot(prepared)
    direct = extract_evidence(snapshot)
    prepared_evidence = extract_evidence(prepared)
    replayed_evidence = extract_evidence(replayed)

    assert prepared is not snapshot and replayed is not prepared
    assert snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] is trap
    assert prepared["action_result_rows"] == replayed["action_result_rows"] == []
    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT
    assert _strict_status(prepared) == TREASURE_APPRAISAL_CONTEXT_STATUS_ABSENT
    assert _strict_status(replayed) == TREASURE_APPRAISAL_CONTEXT_STATUS_ABSENT
    assert direct == prepared_evidence == replayed_evidence
    assert not _contains_legacy_wire_marker(prepared)
    assert not _contains_legacy_wire_marker(replayed)
    assert LEGACY_WIRE_MARKER not in json.dumps(prepared)
    assert LEGACY_WIRE_MARKER not in json.dumps(replayed)
    assert trap.calls == []


def test_prepared_snapshot_revalidates_reinserted_target_rows() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    original = _snapshot()
    original["action_result_rows"][0]["revealed_items_detail"][0]["value"] = object()
    prepared = prepare_reference_engine_snapshot(original)
    reinserted = copy.deepcopy(_snapshot()["action_result_rows"][0])
    trap = _DunderTrap()
    reinserted["revealed_items_detail"][0]["runtime_id"] = trap
    prepared["action_result_rows"] = [reinserted]

    evidence = extract_evidence(prepared)

    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert trap.calls == []


@pytest.mark.parametrize(
    "dunder",
    ["__eq__", "__str__", "__float__", "__int__", "__bool__", "__round__"],
)
def test_each_malformed_treasure_value_dunder_is_never_invoked(dunder: str) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    trap = _single_dunder_trap(dunder)
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = trap

    prepared = prepare_reference_engine_snapshot(snapshot)
    evidence = extract_evidence(prepared)

    assert _strict_status(prepared) == TREASURE_APPRAISAL_CONTEXT_STATUS_ABSENT
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert trap.calls == []


@pytest.mark.parametrize("kind", ["object", "int_subclass", "proxy"])
@pytest.mark.parametrize(
    ("owner", "field", "valid_int"),
    [
        ("row", "revealed_items", 1),
        ("item", "runtime_id", 9003),
        ("item", "item_id", 1916003),
        ("item", "quality", 6),
        ("item", "cells", 4),
        ("item", "value", 266050),
    ],
)
def test_malformed_treasure_scalars_are_atomically_quarantined(
    monkeypatch,
    owner: str,
    field: str,
    valid_int: int,
    kind: str,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    malformed = _malformed_scalar(kind, valid_int)
    target = (
        snapshot["action_result_rows"][0]
        if owner == "row"
        else snapshot["action_result_rows"][0]["revealed_items_detail"][0]
    )
    target[field] = malformed

    monkeypatch.delenv(KILL, raising=False)
    default = extract_evidence(snapshot)
    prepared_default = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    monkeypatch.setenv(KILL, "1")
    rollback = extract_evidence(snapshot)

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT
    assert default == rollback
    assert prepared_default == default
    assert default.quality_cell_floors.get("q6") is None
    assert default.quality_value_floors.get("q6") is None
    assert not any(
        str(note).startswith(TREASURE_APPRAISAL_NOTE)
        for note in default.source_notes
    )
    if hasattr(malformed, "calls"):
        assert malformed.calls == []


def test_malformed_treasure_does_not_quarantine_a_legal_other_item() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    trap = _DunderTrap()
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = trap
    snapshot["public_info_rows"] = [
        {
            "info_id": 200028,
            "revealed_items_detail": [
                {
                    "runtime_id": 7,
                    "local_index": 34,
                    "quality": 4,
                    "cells": 2,
                    "value": 4_788,
                }
            ],
        }
    ]

    evidence = extract_evidence(snapshot)
    prepared_evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert evidence.q4_known_item_facts == (("local:34", 2, 4_788),)
    assert prepared_evidence.q4_known_item_facts == evidence.q4_known_item_facts
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert trap.calls == []


@pytest.mark.parametrize("owner", ["row", "item"])
def test_target_mapping_subclasses_are_quarantined_without_method_dispatch(
    owner: str,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    if owner == "row":
        malformed = _DictSubclassTrap(snapshot["action_result_rows"][0])
        snapshot["action_result_rows"][0] = malformed
    else:
        malformed = _DictSubclassTrap(
            snapshot["action_result_rows"][0]["revealed_items_detail"][0]
        )
        snapshot["action_result_rows"][0]["revealed_items_detail"][0] = malformed

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert malformed.calls == []


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_gabriela_5611_appraisal_changes_joint_posterior_and_quote(
    monkeypatch,
    max_combos: int,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()

    monkeypatch.delenv(KILL, raising=False)
    default = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()
    monkeypatch.setenv(KILL, "1")
    rollback = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()

    assert default["quality_count_ranges"]["q6"] == [1, 2, 3]
    assert default["quality_cells_ranges"]["q6"][:2] == [4, 6]
    assert [default[key] for key in ("conservative", "balanced", "aggressive")] == [
        555349,
        638203,
        721076,
    ]
    assert default["quality_count_ranges"]["q6"] != rollback["quality_count_ranges"]["q6"]
    assert [default[key] for key in ("conservative", "balanced", "aggressive")] != [
        rollback[key] for key in ("conservative", "balanced", "aggressive")
    ]


def test_value_only_appraisal_remains_hero_agnostic() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot(hero="aisha")
    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_OK
    assert evidence.min_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert evidence.quality_value_floors.get("q6") == 266050.0


def test_cells_only_100162_sibling_does_not_conflict_with_100163_value() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    footprint = copy.deepcopy(snapshot["action_result_rows"][0])
    footprint["action_id"] = 100162
    footprint["tool"] = "至宝体积"
    footprint["sort"] = 29
    footprint["revealed_items_detail"][0]["value"] = None
    footprint["revealed_items_detail"][0]["cells"] = 4
    snapshot["action_result_rows"].insert(0, footprint)

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_OK
    assert evidence.min_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert evidence.quality_value_floors.get("q6") == 266050.0


def test_state_replay_appraisal_row_remains_eligible() -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    row = snapshot["action_result_rows"][0]
    row["source_message_id"] = 0x0025
    row["source_kind"] = "state_replay"

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_OK
    assert evidence.min_counts.get("q6") == 1
    assert evidence.quality_cell_floors.get("q6") == 4.0
    assert evidence.quality_value_floors.get("q6") == 266050.0


def test_5611_appraisal_count_reaches_summary_api_panel_and_tk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    monkeypatch.delenv(KILL, raising=False)
    snapshot = _snapshot()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = panel.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    config = panel.ServerConfig(project_root=tmp_path, snapshot=snapshot_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(config))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only package-shaped API probe
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            api_summary = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        worker.join(timeout=30)
        server.server_close()

    diagnostic_row = tk_overlay._summary_diagnostic_row(  # noqa: SLF001
        summary,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert summary["red"]["count_range"] == "1 / 2 / 3"
    assert summary["red"]["cells_range"].startswith("4 / 6 /")
    assert summary["ahmed_ref"]["evidence"]["min_counts"]["q6"] == 1
    assert api_summary["red"] == summary["red"]
    assert api_summary["ahmed_ref"]["evidence"]["min_counts"]["q6"] == 1
    assert tk_overlay.summarize_snapshot is panel.summarize_snapshot
    assert diagnostic_row["red"]["count_range"] == "1 / 2 / 3"
    assert diagnostic_row["ref_v0"]["evidence"]["min_counts"]["q6"] == 1


def test_ambiguous_price_only_contributes_when_the_tier_agrees() -> None:
    by_value = _catalog_items_by_value()
    if not by_value:
        pytest.skip("Item.txt catalog absent")
    shared = {v: rows for v, rows in by_value.items() if len(rows) > 1}
    assert shared, "expected some prices to be shared by several items"
    for value, rows in shared.items():
        match = _treasure_appraisal_match(value)
        qualities = {row[0] for row in rows}
        if len(qualities) > 1:
            assert match is None, (value, rows)  # ambiguous tier -> stay silent
        elif match is not None:
            # tier agrees -> the cells floor is the SMALLEST candidate (a floor, never over-stated)
            assert match[1] == min(row[1] for row in rows)


def test_appraised_unique_gold_price_locks_zero_red(monkeypatch) -> None:
    """至宝估价 targets the highest-quality item, so a catalog-certain gold proves no red."""
    by_value = _catalog_items_by_value()
    if not by_value:
        pytest.skip("Item.txt catalog absent")
    gold_value = next(
        value
        for value, rows in sorted(by_value.items())
        if len(rows) == 1 and rows[0][0] == 5
    )
    snapshot = _snapshot(gold_value, map_id=2402, total_count=34)

    monkeypatch.delenv(KILL, raising=False)
    monkeypatch.delenv("BIDKING_DISABLE_TREASURE_APPRAISAL_MAX_QUALITY", raising=False)
    fixed_evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    fixed = run_reference_engine(
        prepare_reference_engine_snapshot(snapshot), max_combos=8_000
    ).as_dict()
    assert fixed_evidence.min_counts.get("q5") == 1
    assert fixed_evidence.fixed_counts.get("q6") == 0
    assert "public_max_quality_ceiling:5" in fixed_evidence.source_notes
    assert fixed["red_count_range"] == [0, 0, 0]

    monkeypatch.setenv("BIDKING_DISABLE_TREASURE_APPRAISAL_MAX_QUALITY", "1")
    legacy_evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    assert legacy_evidence.min_counts.get("q5") == 1
    assert legacy_evidence.fixed_counts.get("q6") is None


def test_appraised_cross_tier_price_does_not_lock_max_quality() -> None:
    """A price shared by gold and another tier is not enough to claim gold is the maximum."""
    by_value = _catalog_items_by_value()
    if not by_value:
        pytest.skip("Item.txt catalog absent")
    ambiguous_value = next(
        value
        for value, rows in sorted(by_value.items())
        if len({row[0] for row in rows}) > 1
    )
    snapshot = _snapshot(ambiguous_value, map_id=2402, total_count=34)

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))
    assert evidence.fixed_counts.get("q6") is None
    assert not any(note.startswith("public_max_quality_ceiling:") for note in evidence.source_notes)


def _foreign_session(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["session_id"] = "5611:2"


def _future_round(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["round"] = 6


def _root_context_map_conflict(snapshot: dict) -> None:
    snapshot["ui_contract"]["context"]["map_id"] = 5610


def _missing_root_session(snapshot: dict) -> None:
    snapshot.pop("session_id")


def _malformed_string_value(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = "266050"


def _malformed_bool_value(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = True


def _malformed_nan_value(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = float("nan")


def _malformed_inf_value(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = float("inf")


def _oversized_integer_value(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["revealed_items_detail"][0]["value"] = 1 << 60


def _duplicate_equivalent(snapshot: dict) -> None:
    snapshot["action_result_rows"].append(
        copy.deepcopy(snapshot["action_result_rows"][0])
    )


def _duplicate_conflict(snapshot: dict) -> None:
    duplicate = copy.deepcopy(snapshot["action_result_rows"][0])
    duplicate["revealed_items_detail"][0]["value"] = 458000
    snapshot["action_result_rows"].append(duplicate)


def _multiple_revealed_items(snapshot: dict) -> None:
    row = snapshot["action_result_rows"][0]
    row["revealed_items"] = 2
    row["revealed_items_detail"].append(copy.deepcopy(row["revealed_items_detail"][0]))


def _settled(snapshot: dict) -> None:
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"


@pytest.mark.parametrize(
    "mutate",
    [
        _foreign_session,
        _future_round,
        _root_context_map_conflict,
        _missing_root_session,
        _malformed_string_value,
        _malformed_bool_value,
        _malformed_nan_value,
        _malformed_inf_value,
        _oversized_integer_value,
        _duplicate_equivalent,
        _duplicate_conflict,
        _multiple_revealed_items,
        _settled,
    ],
    ids=lambda mutate: mutate.__name__,
)
def test_value_only_appraisal_requires_one_atomic_current_bidding_row(mutate) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    mutate(snapshot)

    evidence = extract_evidence(prepare_reference_engine_snapshot(snapshot))

    assert _strict_status(snapshot) == TREASURE_APPRAISAL_CONTEXT_STATUS_CONFLICT
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert not any(
        str(note).startswith(TREASURE_APPRAISAL_NOTE)
        for note in evidence.source_notes
    )


@pytest.mark.parametrize(
    "mutate",
    [_foreign_session, _duplicate_conflict],
    ids=lambda mutate: mutate.__name__,
)
def test_rejected_appraisal_is_quote_identical_to_kill(monkeypatch, mutate) -> None:
    if not _catalog_items_by_value():
        pytest.skip("Item.txt catalog absent")
    snapshot = _snapshot()
    mutate(snapshot)

    monkeypatch.delenv(KILL, raising=False)
    default = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=8_000,
    ).as_dict()
    monkeypatch.setenv(KILL, "1")
    rollback = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=8_000,
    ).as_dict()

    assert default == rollback


@pytest.mark.skipif(
    not (FIXTURE / "r03_bidding.json").exists(),
    reason="5609 treasure snapshot absent (gitignored) — local-only gate",
)
def test_5609_replay_wires_the_appraised_red(monkeypatch) -> None:
    def _run(off: bool):
        if off:
            monkeypatch.setenv(KILL, "1")
        else:
            monkeypatch.delenv(KILL, raising=False)
        snap = prepare_reference_engine_snapshot(
            json.loads((FIXTURE / "r03_bidding.json").read_text(encoding="utf-8"))
        )
        return extract_evidence(snap), run_reference_engine(snap, max_combos=12000).as_dict()

    ev_off, off = _run(True)
    ev_on, on = _run(False)

    # OFF: the 458k price is dropped — the engine does not even know a red exists
    assert ev_off.min_counts.get("q6") in (None, 0)
    assert off["quality_count_ranges"]["q6"][0] == 0

    # ON: one red of 6 cells worth 458,000 is known (settled truth: exactly that)
    assert ev_on.min_counts.get("q6") == 1
    assert float(ev_on.quality_cell_floors.get("q6")) == 6.0
    assert float(ev_on.quality_value_floors.get("q6")) == 458000.0
    assert any(str(n).startswith(TREASURE_APPRAISAL_NOTE) for n in ev_on.source_notes)
    assert on["quality_count_ranges"]["q6"][0] >= 1
    assert on["quality_cells_ranges"]["q6"][0] >= 6

    # and the quote moves toward the truth (−23% -> +1.4% on this round)
    assert abs(on["balanced"] - TRUTH_VALUE) < abs(off["balanced"] - TRUTH_VALUE)
