"""Cross-source contracts for the 0.3.2 final core-engine composition."""

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
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402
from test_aisha_2509_exact_total_grid_support_0_3_2 import (  # noqa: E402
    _target_snapshot as _aisha_2509_snapshot,
)


HARD_FLOOR_NOTE = engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE
TOTAL_GRID_NOTE = engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE
SOFT_LAYOUT_NOTE = engine.AISHA_EXACT_TOTAL_GRID_SUPPORT_NOTE


def _warehouse_source() -> dict:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "normal",
        "quarantine_required": False,
    }


def _both_authorities_snapshot() -> dict:
    source = _warehouse_source()
    public_info = {
        "public_avg_values": [
            {
                "info_id": 200037,
                "semantic": "q5_avg_value",
                "kind": "avg_value",
                "quality": 5,
                "value": 50_000,
            }
        ],
        "public_numeric_facts": [
            {
                "info_id": 200017,
                "semantic": "total_item_count",
                "kind": "count",
                "value": 4,
            },
            {
                "info_id": 200014,
                "semantic": "total_avg_cells",
                "kind": "avg_cells",
                "value": 2.25,
            },
        ],
    }
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "session-a",
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {"public_info": public_info},
            "source": {
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
        },
        "structured_ref_inputs": {
            "total_count": 4,
            "fixed_counts": {
                "q1": 0,
                "q3": 0,
                "q4": 0,
                "q5": 4,
                "q6": 0,
            },
            "quality_cells": {"q5": 9},
        },
        "public_info_rows": [
            {"info_id": 200037, "value": 50_000, "session_id": "session-a"},
            {"info_id": 200017, "value": 4, "session_id": "session-a"},
            {"info_id": 200014, "value": 2.25, "session_id": "session-a"},
        ],
        "warehouse_source_qualification": source["qualification"],
        "warehouse_source": copy.deepcopy(source),
    }


def _prepared_evidence(snapshot: dict) -> engine.RefEvidence:
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return engine.extract_evidence(prepared)


@pytest.mark.parametrize("max_combos", [1, 2, 3, 4, 8_000, 50_000])
def test_both_authorities_survive_prepare_extract_and_every_cap(
    max_combos: int,
) -> None:
    snapshot = _both_authorities_snapshot()
    evidence = _prepared_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == (9,)
    assert evidence.total_grid_target == 9.0
    assert result["combo_count"] == 1
    assert result["balanced"] == 200_000
    assert result["total_grid_range"] == [9, 9, 9]
    assert f"{HARD_FLOOR_NOTE}:q5=200000" in result["notes"]
    assert f"{TOTAL_GRID_NOTE}:unique:9" in result["notes"]


def test_bad_issue25_row_does_not_erase_valid_issue26_authority() -> None:
    snapshot = _both_authorities_snapshot()
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_avg_values"
    ].append(
        {"kind": "avg_value", "quality": float("inf"), "value": 50_001}
    )

    evidence = _prepared_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.hard_public_avg_values == {}
    assert evidence.total_grid_options == (9,)
    assert evidence.total_grid_target == 9.0
    assert result["balanced"] == 184_000
    assert result["total_grid_range"] == [9, 9, 9]
    assert not any(note.startswith(HARD_FLOOR_NOTE) for note in result["notes"])
    assert f"{TOTAL_GRID_NOTE}:unique:9" in result["notes"]


def test_bad_issue26_row_does_not_erase_valid_issue25_authority() -> None:
    snapshot = _both_authorities_snapshot()
    snapshot["public_info_rows"].append(
        {"info_id": 200014, "value": float("nan"), "session_id": "session-a"}
    )

    evidence = _prepared_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert evidence.total_grid_options == ()
    assert evidence.total_grid_target is None
    assert result["balanced"] == 200_000
    assert f"{HARD_FLOOR_NOTE}:q5=200000" in result["notes"]
    assert not any(note.startswith(TOTAL_GRID_NOTE) for note in result["notes"])


def test_issue25_and_issue26_feature_kills_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _both_authorities_snapshot()

    monkeypatch.setenv(engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_DISABLE, "1")
    hard_floor_off = _prepared_evidence(snapshot)
    assert hard_floor_off.hard_public_avg_values == {}
    assert hard_floor_off.total_grid_options == (9,)

    monkeypatch.delenv(engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_DISABLE)
    monkeypatch.setenv(engine.TOTAL_AVG_CELLS_GRID_OPTIONS_DISABLE, "1")
    total_grid_off = _prepared_evidence(snapshot)
    assert total_grid_off.hard_public_avg_values == {"q5": 50_000.0}
    assert total_grid_off.total_grid_options == ()
    assert not any(
        note.startswith(TOTAL_GRID_NOTE)
        for note in total_grid_off.source_notes
    )


def test_both_context_tokens_reset_after_engine_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("phase1-composition-positive-control")

    monkeypatch.setattr(engine, "_run_reference_engine_impl", explode)
    with pytest.raises(RuntimeError, match="phase1-composition-positive-control"):
        engine.run_reference_engine(_both_authorities_snapshot(), max_combos=8_000)

    assert engine._RUN_CALL_DEPTH.get() == 0
    assert engine._HARD_PUBLIC_AVG_VALUE_RUN_DECISION.get() is None
    assert engine._TOTAL_AVG_CELLS_RAW_AUTHORITY.get() is None


def test_recursive_run_reuses_outer_authorities_and_resets_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = engine._run_reference_engine_impl
    observed: list[tuple[object, object]] = []
    nested_started = False

    def audited(snapshot: dict, **kwargs: object) -> engine.RefResult:
        nonlocal nested_started
        observed.append(
            (
                engine._HARD_PUBLIC_AVG_VALUE_RUN_DECISION.get(),
                engine._TOTAL_AVG_CELLS_RAW_AUTHORITY.get(),
            )
        )
        if not nested_started:
            nested_started = True
            engine.run_reference_engine(
                copy.deepcopy(snapshot),
                max_combos=int(kwargs["max_combos"]),
            )
        return original(snapshot, **kwargs)

    monkeypatch.setattr(engine, "_run_reference_engine_impl", audited)
    engine.run_reference_engine(_both_authorities_snapshot(), max_combos=8_000)

    assert len(observed) >= 2
    assert observed[0][0] is not None
    assert observed[0][1] is not None
    assert observed[1] == observed[0]
    assert engine._RUN_CALL_DEPTH.get() == 0
    assert engine._HARD_PUBLIC_AVG_VALUE_RUN_DECISION.get() is None
    assert engine._TOTAL_AVG_CELLS_RAW_AUTHORITY.get() is None


def test_issue26_hard_grid_preempts_aisha2509_soft_layout_expansion() -> None:
    soft_snapshot = _aisha_2509_snapshot()
    soft = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(soft_snapshot)),
        max_combos=8_000,
    ).as_dict()
    assert any(note.startswith(SOFT_LAYOUT_NOTE) for note in soft["notes"])

    hard_snapshot = _aisha_2509_snapshot()
    session_id = hard_snapshot["session_id"]
    facts = [
        {
            "info_id": 200017,
            "semantic": "total_item_count",
            "kind": "count",
            "value": 61,
        },
        {
            "info_id": 200014,
            "semantic": "total_avg_cells",
            "kind": "avg_cells",
            "value": 3.0,
        },
    ]
    hard_snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = copy.deepcopy(facts)
    hard_snapshot["public_info_rows"].extend(
        {"info_id": row["info_id"], "value": row["value"], "session_id": session_id}
        for row in facts
    )
    hard = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(hard_snapshot)),
        max_combos=8_000,
    ).as_dict()

    assert hard["total_grid_range"] == [183, 183, 183]
    assert f"{TOTAL_GRID_NOTE}:unique:183" in hard["notes"]
    assert not any(note.startswith(SOFT_LAYOUT_NOTE) for note in hard["notes"])


def test_summary_api_panel_and_tk_keep_both_authorities(tmp_path: Path) -> None:
    snapshot = _both_authorities_snapshot()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
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

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    for product_row in (summary["ahmed_ref"], payload["ahmed_ref"]):
        assert product_row["balanced"] == 200_000
        assert product_row["total_grid_range"] == [9, 9, 9]
        assert product_row["evidence"]["total_grid_options"] == [9]
        assert f"{HARD_FLOOR_NOTE}:q5=200000" in product_row["notes"]
        assert f"{TOTAL_GRID_NOTE}:unique:9" in product_row["notes"]
    assert tk_row["reference"]["balanced"] == "200,000"
    assert tk_row["ref_v0"]["total_grid_range"] == [9, 9, 9]
    assert f"{HARD_FLOOR_NOTE}:q5=200000" in tk_row["ref_v0"]["notes"]
    assert f"{TOTAL_GRID_NOTE}:unique:9" in tk_row["ref_v0"]["notes"]
