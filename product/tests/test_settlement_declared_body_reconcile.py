"""Regression for the 5607 declared-count settlement boundary desync.

The 2026-07-13 live frame conservatively parsed 33/35 items. The complete
淀粉肠 wrapper sits outside f2.f4, while the 碳纤维单体壳车身 wrapper crosses
the declared f2 boundary. A whole-body scan finds both, but is only safe when
the independent 库存清点 result says that its 35 candidates are the exact set.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
import sys

from bidking_lab.live.fatbeans import (
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)
from bidking_lab.live.monitor import (
    build_monitor_artifact_from_events,
    load_monitor_tables,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live"
CAPTURE = FIXTURES / "settlement_5607_declared_boundary_npcap_live.jsonl"
KILL = "BIDKING_DISABLE_SETTLEMENT_BODY_UNION"


def _rows() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in CAPTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _settled(rows: list[dict[str, object]]):
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    return next(state for state in events.states if state.message_id == 0x002D)


def _live_panel_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location(
        "settlement_boundary_live_panel",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_declared_count_recovers_cross_boundary_items() -> None:
    settled = _settled(_rows())
    by_id = {item.item_id: item for item in settled.inventory_items}

    assert len(settled.inventory_items) == 35
    assert sum(item.cells for item in settled.inventory_items) == 92
    assert (by_id[1091010].quality, by_id[1091010].cells) == (1, 1)
    assert (by_id[1086001].quality, by_id[1086001].cells) == (6, 16)
    assert Counter(item.quality for item in settled.inventory_items)[6] == 5
    # The raw walk still ends on a dangling byte. Recovery changes the item
    # truth, not this byte-level diagnostic.
    assert settled.settlement_parse_residual == 1


def test_without_independent_count_whole_body_candidates_are_not_admitted() -> None:
    rows = [row for row in _rows() if row.get("MessageID") != "0x0027"]
    settled = _settled(rows)

    assert len(settled.inventory_items) == 33
    assert {item.item_id for item in settled.inventory_items}.isdisjoint(
        {1086001, 1091010}
    )


def test_body_union_kill_switch_restores_conservative_parse(monkeypatch) -> None:
    monkeypatch.setenv(KILL, "1")
    settled = _settled(_rows())

    assert len(settled.inventory_items) == 33
    assert {item.item_id for item in settled.inventory_items}.isdisjoint(
        {1086001, 1091010}
    )


def test_residual_one_complete_5607_reaches_runtime_engine_and_ui() -> None:
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(_rows()))
    artifact = build_monitor_artifact_from_events(
        events,
        file=CAPTURE.name,
        tables=load_monitor_tables(),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["inventory_count"] == 35
    assert artifact["inventory_cells"] == 92
    assert artifact["known_value_sum"] == 938_354
    assert artifact["settlement_incomplete"] is False
    assert artifact["settlement_parse_residual"] == 1
    assert artifact["final_q6_count"] == 5
    assert artifact["final_q6_cells"] == 21
    assert artifact["final_q6_value"] == 749_775

    runtime_truth = artifact["ui_contract"]["truth"]
    assert runtime_truth["complete"] is True
    assert runtime_truth["partial"] is False
    assert runtime_truth["total_items"] == 35
    assert runtime_truth["total_cells"] == 92
    assert runtime_truth["total_value"] == 938_354
    assert runtime_truth["q6"] == {
        "count": 5,
        "cells": 21,
        "value": 749_775,
        "decision_value": 749_775,
        "trimmed_tail_value": 0,
        "tail_replacement_value": 0,
        "decision_value_with_tail_replacement": 749_775,
    }

    summary = _live_panel_module().summarize_snapshot(
        artifact,
        snapshot_path=CAPTURE,
    )
    assert summary["reference"]["source"] == "settlement"
    assert summary["reference"]["action"] == "结算完成"
    assert summary["truth"]["complete"] is True
    assert summary["truth"]["partial"] is False
    assert summary["truth"]["total_items"] == 35
    assert summary["truth"]["total_cells"] == 92
    assert summary["truth"]["total_value"] == 938_354
    assert summary["truth"]["q6"]["count"] == 5
    assert summary["truth"]["q6"]["cells"] == 21
    assert summary["truth"]["q6"]["value"] == 749_775
