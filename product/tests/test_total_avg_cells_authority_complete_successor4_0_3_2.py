"""Successor4 regressions for issue26 atomic authority and product scope."""

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
                "public_info": {
                    "public_numeric_facts": copy.deepcopy(facts),
                }
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


def _model_projection(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in (
            "status",
            "conservative",
            "balanced",
            "aggressive",
            "combo_count",
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_count_candidates",
            "quality_cells_candidates",
            "quality_count_cells_candidates",
            "quality_lock",
            "total_grid_range",
        )
    }


def _assert_total_average_noop(snapshot: dict) -> None:
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    control = run_reference_engine(
        _without_total_average(snapshot),
        max_combos=8_000,
    ).as_dict()
    assert extract_evidence(snapshot).total_grid_options == ()
    assert _model_projection(current) == _model_projection(control)


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _StrSubclass(str):
    pass


@pytest.mark.parametrize(
    ("scalar_type", "value"),
    (
        (_IntSubclass, 200014),
        (_FloatSubclass, 200014.0),
        (_StrSubclass, "200014"),
    ),
)
@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("reverse", (False, True))
def test_numeric_equal_scalar_subclass_alias_is_targeted_invalid(
    scalar_type: type,
    value: object,
    location: str,
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    bad = {"info_id": scalar_type(value), "value": 2.02}
    rows = (
        snapshot["public_info_rows"]
        if location == "raw"
        else snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    )
    if reverse:
        rows.insert(0, bad)
    else:
        rows.append(bad)

    _assert_total_average_noop(snapshot)


@pytest.mark.parametrize(
    ("field", "context_value", "root_value"),
    (
        ("session_id", "context-session", "root-session"),
        ("session_epoch", 8, 9),
        ("epoch", 11, 12),
    ),
)
@pytest.mark.parametrize("row_owner", ("context", "root"))
def test_conflicting_context_and_root_identity_anchors_fail_closed(
    field: str,
    context_value: str | int,
    root_value: str | int,
    row_owner: str,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"][field] = context_value
    snapshot[field] = root_value
    row_value = context_value if row_owner == "context" else root_value
    for row in snapshot["public_info_rows"]:
        row[field] = row_value

    _assert_total_average_noop(snapshot)


@pytest.mark.parametrize(
    ("field", "context_value", "root_value"),
    (
        ("session_id", "context-session", "root-session"),
        ("session_epoch", 8, 9),
        ("epoch", 11, 12),
    ),
)
def test_conflicting_scope_rejects_identity_free_target_rows(
    field: str,
    context_value: str | int,
    root_value: str | int,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"][field] = context_value
    snapshot[field] = root_value

    _assert_total_average_noop(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    (("session_id", "same"), ("session_epoch", 8), ("epoch", 11)),
)
def test_equal_root_and_context_scope_keeps_identity_free_monitor_rows(
    field: str,
    value: str | int,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"][field] = value
    snapshot[field] = value

    assert extract_evidence(snapshot).total_grid_options == (301, 302)


class _RowSubclass(dict):
    pass


class _ListSubclass(list):
    pass


class _TupleSubclass(tuple):
    pass


@pytest.mark.parametrize("location", ("raw", "aggregate"))
@pytest.mark.parametrize("container_type", (_ListSubclass, _TupleSubclass))
def test_targeted_sequence_subclass_is_fail_closed(
    location: str,
    container_type: type,
) -> None:
    snapshot = _snapshot()
    if location == "raw":
        snapshot["public_info_rows"] = container_type(
            snapshot["public_info_rows"]
        )
    else:
        public_info = snapshot["ui_contract"]["constraints"]["public_info"]
        public_info["public_numeric_facts"] = container_type(
            public_info["public_numeric_facts"]
        )

    _assert_total_average_noop(snapshot)


@pytest.mark.parametrize("location", ("raw", "aggregate"))
def test_unrelated_row_subclass_does_not_poison_valid_authority(
    location: str,
) -> None:
    snapshot = _snapshot()
    unrelated = _RowSubclass({"info_id": 200018, "value": 3})
    rows = (
        snapshot["public_info_rows"]
        if location == "raw"
        else snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
    )
    rows.append(unrelated)

    assert extract_evidence(snapshot).total_grid_options == (301, 302)


@pytest.mark.parametrize(
    "identity",
    (True, _IntSubclass(8), _StrSubclass("session")),
)
def test_identity_scalar_subclass_or_bool_is_fail_closed(identity: object) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"]["session_id"] = identity
    for row in snapshot["public_info_rows"]:
        row["session_id"] = identity

    _assert_total_average_noop(snapshot)


def _session_snapshot(session_id: str) -> dict:
    snapshot = _snapshot()
    snapshot["ui_contract"]["context"]["session_id"] = session_id
    for row in snapshot["public_info_rows"]:
        row["session_id"] = session_id
    return snapshot


def _transplant_prepared_authority(source: dict, target: dict) -> None:
    target[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = source[
        _TOTAL_AVG_CELLS_PREPARED_MARKER_KEY
    ]
    target[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY] = copy.deepcopy(
        source[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]
    )


def test_marker_accepts_only_order_change_of_same_raw_ledger() -> None:
    prepared = prepare_reference_engine_snapshot(_session_snapshot("same"))
    reordered = _session_snapshot("same")
    reordered["public_info_rows"].reverse()
    _transplant_prepared_authority(prepared, reordered)

    assert extract_evidence(reordered).total_grid_options == (301, 302)


def test_marker_ignores_unrelated_raw_fact() -> None:
    prepared = prepare_reference_engine_snapshot(_session_snapshot("same"))
    with_unrelated = _session_snapshot("same")
    with_unrelated["public_info_rows"].append(
        {"info_id": 200018, "value": 3}
    )
    _transplant_prepared_authority(prepared, with_unrelated)

    assert extract_evidence(with_unrelated).total_grid_options == (301, 302)


def test_marker_rejects_duplicate_targeted_ledger_transplant() -> None:
    prepared = prepare_reference_engine_snapshot(_session_snapshot("same"))
    duplicated = _session_snapshot("same")
    duplicated["public_info_rows"].append(
        copy.deepcopy(duplicated["public_info_rows"][-1])
    )
    _transplant_prepared_authority(prepared, duplicated)

    _assert_total_average_noop(duplicated)


@pytest.mark.parametrize("damage", ("extra-field", "bad-binding", "marker-subclass"))
def test_prepared_decision_shape_and_marker_type_damage_fail_closed(
    damage: str,
) -> None:
    prepared = prepare_reference_engine_snapshot(_session_snapshot("same"))
    if damage == "extra-field":
        prepared[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]["extra"] = "bad"
    elif damage == "bad-binding":
        prepared[_TOTAL_AVG_CELLS_PREPARED_DECISION_KEY]["raw_binding"] = (
            "0" * 64
        )
    else:
        prepared[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY] = _StrSubclass(
            prepared[_TOTAL_AVG_CELLS_PREPARED_MARKER_KEY]
        )

    _assert_total_average_noop(prepared)


def _malformed_aisha_case() -> tuple[dict, dict]:
    snapshot = _snapshot(hero="aisha", map_id=4501)
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].append(_fact("total_avg_cells", float("nan")))
    return snapshot, _without_total_average(snapshot)


def test_malformed_aggregate_prepare_preserves_the_nonaverage_model() -> None:
    snapshot, baseline = _malformed_aisha_case()

    _assert_total_average_noop(snapshot)
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    prepared_baseline = prepare_reference_engine_snapshot(copy.deepcopy(baseline))
    assert extract_evidence(prepared).total_grid_options == ()
    assert _model_projection(
        run_reference_engine(prepared, max_combos=8_000).as_dict()
    ) == _model_projection(
        run_reference_engine(prepared_baseline, max_combos=8_000).as_dict()
    )


def test_ahmed_malformed_aggregate_prepare_preserves_nonaverage_model() -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].append(_fact("total_avg_cells", float("nan")))
    baseline = _without_total_average(snapshot)

    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    prepared_baseline = prepare_reference_engine_snapshot(copy.deepcopy(baseline))
    assert _model_projection(
        run_reference_engine(prepared, max_combos=8_000).as_dict()
    ) == _model_projection(
        run_reference_engine(prepared_baseline, max_combos=8_000).as_dict()
    )


def test_malformed_aggregate_summary_api_and_diagnostic_preserve_baseline(
    tmp_path: Path,
) -> None:
    snapshot, baseline = _malformed_aisha_case()

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    summary = live_server.summarize_snapshot(
        snapshot,
        snapshot_path=snapshot_path,
    )
    baseline_summary = live_server.summarize_snapshot(
        baseline,
        snapshot_path=tmp_path / "baseline.json",
    )
    summary_equal = _model_projection(summary["ahmed_ref"]) == _model_projection(
        baseline_summary["ahmed_ref"]
    )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        live_server.make_handler(
            live_server.ServerConfig(
                project_root=tmp_path,
                snapshot=Path("snapshot.json"),
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
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    api_equal = _model_projection(payload["ahmed_ref"]) == _model_projection(
        baseline_summary["ahmed_ref"]
    )
    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    diagnostic_equal = tk_row["ref_v0"]["total_grid_range"] == baseline_summary[
        "ahmed_ref"
    ]["total_grid_range"]
    assert {
        "summary": summary_equal,
        "api": api_equal,
        "diagnostic": diagnostic_equal,
    } == {"summary": True, "api": True, "diagnostic": True}


def test_malformed_aggregate_real_tk_preserves_baseline_labels(
    tmp_path: Path,
) -> None:
    snapshot, baseline = _malformed_aisha_case()
    snapshot_path = tmp_path / "snapshot.json"
    summary = live_server.summarize_snapshot(
        snapshot,
        snapshot_path=snapshot_path,
    )
    baseline_summary = live_server.summarize_snapshot(
        baseline,
        snapshot_path=tmp_path / "baseline.json",
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
            snapshot_path=snapshot_path,
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        overlay.render(summary)
        root.update_idletasks()
        root.update()
        malformed_labels = {
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
        overlay.render(baseline_summary)
        root.update_idletasks()
        root.update()
        baseline_labels = {
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
        assert malformed_labels == baseline_labels
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
