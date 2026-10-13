"""Hotfix4 regressions for Aisha opening public numeric evidence.

The packet parser, reference engine and R1 Panel gate must agree on the same
public-average/count transaction.  These tests deliberately keep RankMap and
package construction out of scope; package-shaped consumers are covered by the
existing runtime/Panel/Tk selectors in the source receipt.
"""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "src",
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    FatbeansPublicInfo,
    FatbeansStateEvent,
    _public_numeric_updates,
    live_batches_from_fatbeans_events,
)
from bidking_lab.live.monitor import _ahmad_ref_inputs_from_batches  # noqa: E402


SESSION_ID = "2531:opening-public-numeric"


class _ScopeProtocolTrap:
    calls: list[str] = []

    def _trip(self, name: str) -> None:
        type(self).calls.append(name)
        raise AssertionError(f"user protocol executed: {name}")

    @property
    def __class__(self) -> type[object]:
        self._trip("__class__")

    def __bool__(self) -> bool:
        self._trip("__bool__")

    def __str__(self) -> str:
        self._trip("__str__")

    def __int__(self) -> int:
        self._trip("__int__")

    def __float__(self) -> float:
        self._trip("__float__")

    def __eq__(self, other: object) -> bool:
        del other
        self._trip("__eq__")

    def __hash__(self) -> int:
        self._trip("__hash__")


def _public_info(info_id: int, value: int | float) -> FatbeansPublicInfo:
    return FatbeansPublicInfo(
        info_id=info_id,
        map_id=2531,
        value=value,
        value_field=11 if info_id in {200013, 200014, 200015, 200016} else 7,
    )


def _state(*infos: FatbeansPublicInfo) -> FatbeansStateEvent:
    return FatbeansStateEvent(
        sort_id=17,
        capture_time="2026-08-16T00:32:24",
        message_id=0x21,
        session_id=SESSION_ID,
        map_id=2531,
        round_index=1,
        public_infos=tuple(infos),
    )


def _fact(info_id: int, value: int | float) -> dict[str, Any]:
    metadata: dict[int, tuple[str, str, int | None]] = {
        200013: ("q4_avg_cells", "avg_cells", 4),
        200014: ("total_avg_cells", "avg_cells", None),
        200015: ("q5_avg_cells", "avg_cells", 5),
        200016: ("q6_avg_cells", "avg_cells", 6),
        200017: ("total_item_count", "count", None),
        200044: ("auction_category_total_value", "exact_subset_value", None),
    }
    semantic, kind, quality = metadata[info_id]
    row: dict[str, Any] = {
        "info_id": info_id,
        "semantic": semantic,
        "kind": kind,
        "value": value,
    }
    if quality is not None:
        row["quality"] = quality
    return row


def _r1_snapshot(
    *,
    rows: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    structured: dict[str, Any],
) -> dict[str, Any]:
    return {
        "created_at": time.time(),
        "hero": "aisha",
        "map_id": 2531,
        "phase": "bidding",
        "round": 1,
        "session_id": SESSION_ID,
        "public_info_rows": rows,
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "aisha",
            **structured,
        },
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "hero_id": 103,
                "map_id": 2531,
                "phase": "bidding",
                "round": 1,
                "session_id": SESSION_ID,
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": copy.deepcopy(facts),
                    "public_avg_cells": [
                        copy.deepcopy(fact)
                        for fact in facts
                        if fact.get("kind") == "avg_cells"
                    ],
                }
            },
            "source": {"created_at": time.time()},
        },
    }


def _raw_row(info_id: int, value: int | float, *, sort: int = 17) -> dict[str, Any]:
    return {
        "sort": sort,
        "info_id": info_id,
        "value": value,
        "map_id": 2531,
        "session_id": SESSION_ID,
        "round": 1,
    }


def test_packet_public_avg_cells_bridge_all_four_semantics() -> None:
    updates = _public_numeric_updates(
        _state(
            _public_info(200013, 3.25),
            _public_info(200014, 2.55),
            _public_info(200015, 7.75),
            _public_info(200016, 9.5),
        )
    )

    assert [(update.path, update.value) for update in updates] == [
        (("bucket", "4", "avg_cells"), 3.25),
        (("session", "total_avg_cells"), 2.55),
        (("bucket", "5", "avg_cells"), 7.75),
        (("bucket", "6", "avg_cells"), 9.5),
    ]
    assert all(update.source == "packet" for update in updates)
    assert all(update.confidence == "exact" for update in updates)


def test_packet_public_numeric_updates_reach_structured_monitor_inputs() -> None:
    state = _state(
        _public_info(200013, 3.25),
        _public_info(200014, 2.55),
        _public_info(200015, 7.75),
        _public_info(200016, 9.5),
        _public_info(200017, 41),
    )
    events = FatbeansCaptureEvents((), (), (), (state,), ())
    batches = live_batches_from_fatbeans_events(events)

    structured = _ahmad_ref_inputs_from_batches(
        batches,
        hero="aisha",
        session_id=SESSION_ID,
    )

    assert structured["total_count"] == 41
    assert structured["total_avg_cells"] == 2.55
    assert structured["avg_cells"] == {"q4": 3.25, "q5": 7.75, "q6": 9.5}
    assert all(
        row["source"] == "packet" and row["confidence"] == "exact"
        for row in structured["field_updates"]
    )


@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_q5_public_avg_cells_is_not_claimed_as_q5_avg_value(prepared: bool) -> None:
    facts = [_fact(200015, 7.75), _fact(200017, 41)]
    snapshot = _r1_snapshot(
        rows=[_raw_row(200015, 7.75), _raw_row(200017, 41)],
        facts=facts,
        structured={"total_count": 41, "avg_cells": {"q5": 7.75}},
    )
    if prepared:
        snapshot = engine.prepare_reference_engine_snapshot(snapshot)

    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.avg_cells["q5"] == 7.75
    assert evidence.avg_values.get("q5") is None
    assert "public_q5_avg_cells" in evidence.source_notes
    assert result["balanced"] not in (None, 0)


@pytest.mark.parametrize(
    "siblings",
    (
        (),
        ((200044, 1_530_896),),
        ((200021, 7),),
        ((200044, 1_530_896), (200021, 7)),
    ),
    ids=("count-only", "category-sibling", "unrelated-sibling", "both-siblings"),
)
def test_r1_total_count_survives_legal_or_unrelated_siblings(
    siblings: tuple[tuple[int, int], ...],
) -> None:
    rows = [_raw_row(200017, 41)]
    facts = [_fact(200017, 41)]
    for info_id, value in siblings:
        rows.append(_raw_row(info_id, value, sort=18 + len(rows)))
        if info_id in {200044}:
            facts.append(_fact(info_id, value))
    snapshot = _r1_snapshot(
        rows=rows,
        facts=facts,
        structured={"total_count": 41},
    )

    assert panel._aisha_r1_current_public_row_ready(snapshot) is True
    summary = panel.summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "data" / "logs" / "live" / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]
    assert summary["reference"]["balanced"] != "-"
    assert ref["balanced"] not in (None, 0)
    assert "aisha_quote_pass:public" in ref["notes"]


@pytest.mark.parametrize(
    "mutate",
    (
        "target-string",
        "target-float",
        "conflicting-duplicate",
        "sort-alias-conflict",
        "cross-session",
        "cross-round",
    ),
)
def test_r1_targeted_malformed_conflict_or_cross_scope_fails_closed(
    mutate: str,
) -> None:
    rows = [_raw_row(200017, 41)]
    if mutate == "target-string":
        rows[0]["info_id"] = "200017"
    elif mutate == "target-float":
        rows[0]["info_id"] = 200017.0
    elif mutate == "conflicting-duplicate":
        rows.append(_raw_row(200017, 42, sort=18))
    elif mutate == "sort-alias-conflict":
        rows[0]["sort_id"] = 18
    elif mutate == "cross-session":
        rows[0]["session_id"] = "2531:stale"
    elif mutate == "cross-round":
        rows[0]["round"] = 2
    snapshot = _r1_snapshot(
        rows=rows,
        facts=[_fact(200017, 41)],
        structured={"total_count": 41},
    )

    assert panel._aisha_r1_current_public_row_ready(snapshot) is False
    assert panel._aisha_r1_public_provisional_ready(snapshot, 1, {}) is False


def test_r1_same_value_duplicate_and_unrelated_row_remain_ready() -> None:
    rows = [
        _raw_row(200017, 41, sort=17),
        _raw_row(200017, 41, sort=18),
        _raw_row(200021, 7, sort=19),
    ]
    snapshot = _r1_snapshot(
        rows=rows,
        facts=[_fact(200017, 41)],
        structured={"total_count": 41},
    )

    assert panel._aisha_r1_current_public_row_ready(snapshot) is True


def _scope_snapshot() -> dict[str, Any]:
    return _r1_snapshot(
        rows=[_raw_row(200017, 41)],
        facts=[_fact(200017, 41)],
        structured={"total_count": 41},
    )


@pytest.mark.parametrize("container", ("ui_contract", "context"))
@pytest.mark.parametrize(
    ("shape", "expected"),
    (
        ("missing", True),
        ("empty-dict", True),
        ("explicit-none", False),
        ("wrong-builtin", False),
        ("protocol-trap", False),
    ),
)
def test_r1_scope_container_missing_is_distinct_from_invalid_present(
    container: str,
    shape: str,
    expected: bool,
) -> None:
    snapshot = _scope_snapshot()
    parent = snapshot if container == "ui_contract" else snapshot["ui_contract"]
    key = "ui_contract" if container == "ui_contract" else "context"
    if shape == "missing":
        parent.pop(key)
    elif shape == "empty-dict":
        parent[key] = {}
    elif shape == "explicit-none":
        parent[key] = None
    elif shape == "wrong-builtin":
        parent[key] = []
    else:
        parent[key] = _ScopeProtocolTrap()

    _ScopeProtocolTrap.calls = []
    assert panel._aisha_r1_current_public_row_ready(snapshot) is expected
    assert _ScopeProtocolTrap.calls == []


@pytest.mark.parametrize("location", ("context", "snapshot"))
@pytest.mark.parametrize("field", ("session_id", "map_id", "round"))
def test_r1_scope_protocol_object_fails_closed_without_dunder(
    location: str,
    field: str,
) -> None:
    snapshot = _scope_snapshot()
    context = snapshot["ui_contract"]["context"]
    if location == "context":
        context[field] = _ScopeProtocolTrap()
    else:
        context.pop(field)
        snapshot[field] = _ScopeProtocolTrap()

    _ScopeProtocolTrap.calls = []
    assert panel._aisha_r1_current_public_row_ready(snapshot) is False
    assert _ScopeProtocolTrap.calls == []


@pytest.mark.parametrize("field", ("session_id", "map_id", "round"))
def test_r1_scope_snapshot_protocol_alias_cannot_hide_behind_valid_context(
    field: str,
) -> None:
    snapshot = _scope_snapshot()
    snapshot[field] = _ScopeProtocolTrap()

    _ScopeProtocolTrap.calls = []
    assert panel._aisha_r1_current_public_row_ready(snapshot) is False
    assert _ScopeProtocolTrap.calls == []


@pytest.mark.parametrize("owner", ("context", "snapshot"))
@pytest.mark.parametrize("field", ("session_id", "map_id", "round"))
def test_r1_scope_exact_missing_alias_uses_valid_fallback(
    owner: str,
    field: str,
) -> None:
    snapshot = _scope_snapshot()
    context = snapshot["ui_contract"]["context"]
    if owner == "context":
        snapshot.pop(field)
    else:
        context.pop(field)

    assert panel._aisha_r1_current_public_row_ready(snapshot) is True


@pytest.mark.parametrize("location", ("context", "snapshot"))
@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("session_id", ""),
        ("session_id", None),
        ("session_id", 0),
        ("session_id", True),
        ("map_id", "2531"),
        ("map_id", None),
        ("map_id", 2531.0),
        ("map_id", -1),
        ("map_id", True),
        ("round", "1"),
        ("round", None),
        ("round", 1.0),
        ("round", 0),
        ("round", True),
    ),
)
def test_r1_scope_explicit_invalid_builtin_never_falls_back(
    location: str,
    field: str,
    invalid: object,
) -> None:
    snapshot = _scope_snapshot()
    context = snapshot["ui_contract"]["context"]
    target = context if location == "context" else snapshot
    target[field] = invalid

    assert panel._aisha_r1_current_public_row_ready(snapshot) is False


@pytest.mark.parametrize("field", ("session_id", "map_id", "round"))
def test_r1_scope_missing_from_both_layers_fails_closed(field: str) -> None:
    snapshot = _scope_snapshot()
    snapshot.pop(field)
    snapshot["ui_contract"]["context"].pop(field)

    assert panel._aisha_r1_current_public_row_ready(snapshot) is False


@pytest.mark.parametrize(
    ("field", "other"),
    (
        ("session_id", "2531:other"),
        ("map_id", 2532),
        ("round", 2),
    ),
)
def test_r1_scope_mixed_alias_values_fail_closed(field: str, other: object) -> None:
    snapshot = _scope_snapshot()
    snapshot["ui_contract"]["context"][field] = other

    assert panel._aisha_r1_current_public_row_ready(snapshot) is False


def test_r1_scope_session_aliases_compare_canonical_builtin_text() -> None:
    snapshot = _scope_snapshot()
    snapshot["ui_contract"]["context"]["session_id"] = f"  {SESSION_ID}  "

    assert panel._aisha_r1_current_public_row_ready(snapshot) is True


def test_r1_protocol_object_is_rejected_without_conversion() -> None:
    row = _raw_row(200017, 41)
    row["info_id"] = _ScopeProtocolTrap()
    snapshot = _r1_snapshot(
        rows=[row],
        facts=[_fact(200017, 41)],
        structured={"total_count": 41},
    )

    _ScopeProtocolTrap.calls = []
    assert panel._aisha_r1_current_public_row_ready(snapshot) is False
    assert _ScopeProtocolTrap.calls == []
