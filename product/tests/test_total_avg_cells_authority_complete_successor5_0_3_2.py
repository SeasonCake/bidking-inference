"""Owner regression matrix for issue26 successor5."""

from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as live_server  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    _TOTAL_AVG_CELLS_PREPARED_DECISION_KEY,
    _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import AhmadTkOverlay, _summary_diagnostic_row  # noqa: E402


def _source() -> dict:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _fact(semantic: str, value: object) -> dict:
    return {
        "info_id": {
            "total_item_count": 200017,
            "total_avg_cells": 200014,
        }[semantic],
        "semantic": semantic,
        "kind": "count" if semantic == "total_item_count" else "avg_cells",
        "value": value,
    }


def _snapshot(*, hero: str = "ahmed", map_id: int = 2401) -> dict:
    source = _source()
    facts = [
        _fact("total_item_count", 150),
        _fact("total_avg_cells", 2.01),
    ]
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "phase": "bidding",
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {
                "public_info": {"public_numeric_facts": copy.deepcopy(facts)}
            },
            "source": {
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
        },
        "structured_ref_inputs": {
            "total_count": 150,
            "fixed_counts": {
                "q1": 150,
                "q3": 0,
                "q4": 0,
                "q5": 0,
                "q6": 0,
            },
        },
        "public_info_rows": [
            {"info_id": 200017, "value": 150},
            {"info_id": 200014, "value": 2.01},
        ],
        "warehouse_source_qualification": source["qualification"],
        "warehouse_source": copy.deepcopy(source),
    }


def _without_total_average(snapshot: dict) -> dict:
    control = copy.deepcopy(snapshot)
    control["public_info_rows"] = [
        row
        for row in control["public_info_rows"]
        if dict.get(row, "info_id") != 200014
    ]
    public_info = control["ui_contract"]["constraints"]["public_info"]
    public_info["public_numeric_facts"] = [
        row
        for row in public_info["public_numeric_facts"]
        if dict.get(row, "info_id") != 200014
    ]
    return control


def _prepared_as_dict(snapshot: dict) -> dict:
    prepared = prepare_reference_engine_snapshot(snapshot)
    return run_reference_engine(prepared, max_combos=8_000).as_dict()


def _boom(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("overridden scalar protocol executed")


class _HostileInt(int):
    __eq__ = _boom
    __ne__ = _boom
    __int__ = _boom
    __float__ = _boom
    __index__ = _boom
    __str__ = _boom
    __repr__ = _boom
    __hash__ = _boom


class _HostileFloat(float):
    __eq__ = _boom
    __ne__ = _boom
    __int__ = _boom
    __float__ = _boom
    __str__ = _boom
    __repr__ = _boom
    __hash__ = _boom
    is_integer = _boom
    hex = _boom


class _HostileStr(str):
    __eq__ = _boom
    __ne__ = _boom
    __str__ = _boom
    __repr__ = _boom
    __hash__ = _boom
    strip = _boom


@pytest.mark.parametrize(
    ("scalar_type", "info_id", "bad_value"),
    (
        (_HostileInt, 200014, 2.02),
        (_HostileFloat, 200014.0, 2.02),
        (_HostileStr, "200014", 2.02),
        (_HostileInt, 200017, 151),
        (_HostileFloat, 200017.0, 151),
        (_HostileStr, "200017", 151),
    ),
)
@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("reverse", (False, True))
def test_protocol_overrides_cannot_hide_numeric_equal_info_alias(
    scalar_type: type,
    info_id: object,
    bad_value: int | float,
    location: str,
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    bad = {
        "info_id": scalar_type(info_id),
        "value": bad_value,
    }
    rows = (
        snapshot["public_info_rows"]
        if location == "raw"
        else snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    )
    rows.insert(0, bad) if reverse else rows.append(bad)

    assert extract_evidence(snapshot).total_grid_options == ()


@pytest.mark.parametrize(
    "semantic",
    ("total_item_count", "total_avg_cells"),
)
@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("reverse", (False, True))
def test_protocol_bearing_semantic_alias_is_targeted_invalid(
    semantic: str,
    location: str,
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    bad = {
        "semantic": _HostileStr(semantic),
        "value": 151 if semantic == "total_item_count" else 2.02,
    }
    rows = (
        snapshot["public_info_rows"]
        if location == "raw"
        else snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    )
    rows.insert(0, bad) if reverse else rows.append(bad)

    assert extract_evidence(snapshot).total_grid_options == ()


class _ContextSubclass(dict):
    pass


@pytest.mark.parametrize(
    ("field", "root_value", "context_value"),
    (
        ("session_id", "root-session", "context-session"),
        ("session_epoch", 7, 8),
        ("epoch", 11, 12),
    ),
)
@pytest.mark.parametrize("same_value", (False, True))
def test_context_container_subclass_cannot_disappear_from_current_scope(
    field: str,
    root_value: str | int,
    context_value: str | int,
    same_value: bool,
) -> None:
    snapshot = _snapshot()
    snapshot[field] = root_value
    context = dict(snapshot["ui_contract"]["context"])
    context[field] = root_value if same_value else context_value
    snapshot["ui_contract"]["context"] = _ContextSubclass(context)
    for row in snapshot["public_info_rows"]:
        row[field] = root_value

    assert extract_evidence(snapshot).total_grid_options == ()
    assert _prepared_as_dict(snapshot) == _prepared_as_dict(
        _without_total_average(snapshot)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("session_id", "same"), ("session_epoch", 8), ("epoch", 11)),
)
@pytest.mark.parametrize("location", ("root", "context"))
@pytest.mark.parametrize("rows_have_identity", (False, True))
def test_single_valid_current_anchor_remains_compatible(
    field: str,
    value: str | int,
    location: str,
    rows_have_identity: bool,
) -> None:
    snapshot = _snapshot()
    owner = snapshot if location == "root" else snapshot["ui_contract"]["context"]
    owner[field] = value
    if rows_have_identity:
        for row in snapshot["public_info_rows"]:
            row[field] = value

    assert extract_evidence(snapshot).total_grid_options == (301, 302)


@pytest.mark.parametrize(
    ("field", "value"),
    (("session_id", "same"), ("session_epoch", 8), ("epoch", 11)),
)
def test_row_identity_without_current_anchor_fails_closed(
    field: str,
    value: str | int,
) -> None:
    snapshot = _snapshot()
    for row in snapshot["public_info_rows"]:
        row[field] = value

    assert extract_evidence(snapshot).total_grid_options == ()


@pytest.mark.parametrize(
    ("field", "value", "stale_value"),
    (
        ("session_id", "current", "old"),
        ("session_epoch", 8, 7),
        ("epoch", 11, 10),
    ),
)
def test_complete_current_ledger_ignores_explicit_stale_sibling(
    field: str,
    value: str | int,
    stale_value: str | int,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"][field] = value
    for row in snapshot["public_info_rows"]:
        row[field] = value
    snapshot["public_info_rows"].append(
        {
            "info_id": 200014,
            "value": 9.99,
            field: stale_value,
            "stale": True,
        }
    )

    assert extract_evidence(snapshot).total_grid_options == (301, 302)


def _scoped_snapshot(field: str, value: str | int, location: str) -> dict:
    snapshot = _snapshot()
    owner = snapshot if location == "root" else snapshot["ui_contract"]["context"]
    owner[field] = value
    for row in snapshot["public_info_rows"]:
        row[field] = value
    return snapshot


def _transplant(source: dict, target: dict) -> None:
    target[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = source[
        _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY
    ]
    target[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY] = copy.deepcopy(
        source[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("session_id", "same"), ("session_epoch", 8), ("epoch", 11)),
)
@pytest.mark.parametrize(
    ("source_location", "target_location"),
    (("root", "context"), ("context", "root")),
)
def test_marker_rejects_same_identity_moved_between_scope_locations(
    field: str,
    value: str | int,
    source_location: str,
    target_location: str,
) -> None:
    prepared = prepare_reference_engine_snapshot(
        _scoped_snapshot(field, value, source_location)
    )
    target = _scoped_snapshot(field, value, target_location)
    _transplant(prepared, target)

    assert extract_evidence(target).total_grid_options == ()


@pytest.mark.parametrize(
    ("field", "source_value", "target_value"),
    (
        ("session_id", "source", "target"),
        ("session_epoch", 8, 9),
        ("epoch", 11, 12),
    ),
)
def test_marker_rejects_cross_scope_transplant(
    field: str,
    source_value: str | int,
    target_value: str | int,
) -> None:
    prepared = prepare_reference_engine_snapshot(
        _scoped_snapshot(field, source_value, "context")
    )
    target = _scoped_snapshot(field, target_value, "context")
    _transplant(prepared, target)

    assert extract_evidence(target).total_grid_options == ()


@pytest.mark.parametrize(
    "damage",
    ("missing-marker", "missing-decision", "missing-field", "forged-marker"),
)
def test_marker_missing_fields_and_forgery_fail_closed(damage: str) -> None:
    prepared = prepare_reference_engine_snapshot(
        _scoped_snapshot("session_id", "same", "context")
    )
    if damage == "missing-marker":
        prepared.pop(_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY)
    elif damage == "missing-decision":
        prepared.pop(_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY)
    elif damage == "missing-field":
        prepared[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY].pop("raw_binding")
    else:
        prepared[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = "f" * 64

    assert extract_evidence(prepared).total_grid_options == ()


def _visible_labels(overlay: AhmadTkOverlay) -> dict[str, dict[str, object]]:
    return {
        "detail": {
            key: widget.cget("text")
            for key, widget in overlay.detail_rows.items()
            if "text" in widget.keys()
        },
        "evidence": {
            key: widget.cget("text")
            for key, widget in overlay.evidence_rows.items()
            if "text" in widget.keys()
        },
    }


def _loopback_payload(snapshot: dict, snapshot_path: Path) -> dict:
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        live_server.make_handler(
            live_server.ServerConfig(
                project_root=snapshot_path.parent,
                snapshot=Path(snapshot_path.name),
            )
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            return json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.mark.parametrize(("hero", "map_id"), (("aisha", 4501), ("ahmed", 2401)))
def test_malformed_aggregate_is_full_product_fresh_baseline_noop(
    hero: str,
    map_id: int,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(hero=hero, map_id=map_id)
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].append(_fact("total_avg_cells", float("nan")))
    baseline = _without_total_average(snapshot)

    prepared_result = _prepared_as_dict(copy.deepcopy(snapshot))
    prepared_baseline = _prepared_as_dict(copy.deepcopy(baseline))
    summary = live_server.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "direct.json",
    )
    baseline_summary = live_server.summarize_snapshot(
        baseline,
        snapshot_path=tmp_path / "baseline.json",
    )
    api_payload = _loopback_payload(snapshot, tmp_path / "snapshot.json")
    diagnostic = _summary_diagnostic_row(
        api_payload,
        snapshot_path=tmp_path / "snapshot.json",
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    baseline_diagnostic = _summary_diagnostic_row(
        baseline_summary,
        snapshot_path=tmp_path / "baseline.json",
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    tk = pytest.importorskip("tkinter")
    root = None
    last_error = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
    if root is None:
        pytest.skip(f"real Tk unavailable: {last_error}")
    try:
        root.attributes("-alpha", 0.0)
        overlay = AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        overlay.render(summary)
        root.update_idletasks()
        root.update()
        labels = _visible_labels(overlay)
        overlay.render(baseline_summary)
        root.update_idletasks()
        root.update()
        baseline_labels = _visible_labels(overlay)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

    assert {
        "prepared_as_dict": prepared_result == prepared_baseline,
        "summary": summary["ahmed_ref"] == baseline_summary["ahmed_ref"],
        "api": api_payload["ahmed_ref"] == baseline_summary["ahmed_ref"],
        "diagnostic": diagnostic["ref_v0"] == baseline_diagnostic["ref_v0"],
        "real_tk": labels == baseline_labels,
    } == {
        "prepared_as_dict": True,
        "summary": True,
        "api": True,
        "diagnostic": True,
        "real_tk": True,
    }


class _RowSubclass(dict):
    pass


class _ListSubclass(list):
    pass


class _TupleSubclass(tuple):
    pass


@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("container_type", (_ListSubclass, _TupleSubclass))
def test_targeted_sequence_subclass_is_full_wire_baseline_noop(
    location: str,
    container_type: type,
) -> None:
    snapshot = _snapshot(hero="aisha", map_id=4501)
    baseline = _without_total_average(snapshot)
    if location == "raw":
        snapshot["public_info_rows"] = container_type(snapshot["public_info_rows"])
    else:
        public_info = snapshot["ui_contract"]["constraints"]["public_info"]
        public_info["public_numeric_facts"] = container_type(
            public_info["public_numeric_facts"]
        )

    assert _prepared_as_dict(snapshot) == _prepared_as_dict(baseline)


@pytest.mark.parametrize("location", ("raw", "aggregate"))
def test_targeted_row_subclass_is_full_wire_baseline_noop(location: str) -> None:
    snapshot = _snapshot(hero="aisha", map_id=4501)
    baseline = _without_total_average(snapshot)
    if location == "raw":
        snapshot["public_info_rows"][-1] = _RowSubclass(
            snapshot["public_info_rows"][-1]
        )
    else:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        rows[-1] = _RowSubclass(rows[-1])

    assert _prepared_as_dict(snapshot) == _prepared_as_dict(baseline)
