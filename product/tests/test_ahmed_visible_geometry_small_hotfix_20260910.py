"""Synthetic producer-shaped geometry tests; not a replay of the 4510 screenshot."""
from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "src"))
import ahmad_ref_engine as engine
from layout_depth_policy import ahmed_visible_geometry_grid_range

DISABLE = "BIDKING_DISABLE_AHMED_VISIBLE_GEOMETRY"


def _snapshot(**inputs):
    return {
        "hero": "ahmed", "map_id": 4510, "round": 1, "phase": "reading",
        "structured_ref_inputs": {"total_count": 46, **inputs},
        "minimap_grid_items": [{
            "local_index": 125, "cells": 1, "quality": 3,
            "row": None, "col": None, "width": None, "height": None,
            "shape_key": None, "source": "packet", "layout_source": "live_grid",
        }],
    }


def test_real_engine_geometry_reaches_soft_130(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setenv(DISABLE, "1")
    before = engine.run_reference_engine(snapshot)
    monkeypatch.delenv(DISABLE)
    after = engine.run_reference_engine(snapshot)
    assert before.combo_count > 0
    assert before.total_grid_range[1] < 130
    assert after.total_grid_range[1] == 130
    assert after.total_grid_range[0] == before.total_grid_range[0] < 130
    assert after.evidence.total_grid_target is None
    assert engine._hard_total_grid_target_int(after.evidence) is None
    assert after.red_cells_range != before.red_cells_range
    assert after.quality_cells_ranges != before.quality_cells_ranges
    allowed = {"total_grid_range", "quality_cells_ranges", "quality_cells_candidates",
               "red_cells_range", "notes"}
    left, right = before.as_dict(), after.as_dict()
    assert {k: v for k, v in left.items() if k not in allowed} == {
        k: v for k, v in right.items() if k not in allowed}
    for quality, cells in after.quality_cells_ranges.items():
        assert list(cells) == sorted(cells)
        assert all(c >= n for c, n in zip(cells, after.quality_count_ranges[quality]))
    sys.path.insert(0, str(ROOT / "hero_ref" / "tools"))
    from ahmad_live_panel_server import (
        _grid_summary_parts, _purple_gold_quality_summary,
        _quality_composition_payload, _red_display_ranges,
    )
    assert _quality_composition_payload(right)["status"] != "conflict"
    locked, estimated = _grid_summary_parts(right, right["evidence"])
    assert locked is None and "130" in estimated and "估" in estimated
    assert _red_display_ranges(right)[1] == right["red_cells_range"]
    assert _purple_gold_quality_summary(left) != _purple_gold_quality_summary(right)


@pytest.mark.parametrize("mutation", [
    {"local_index": None}, {"local_index": -1}, {"local_index": 250},
    {"local_index": True}, {"local_index": 125.5}, {"local_index": "125"},
    {"source": "derived"}, {"source": None}, {"layout_source": "settlement_inventory"},
    {"row": 1, "col": 1}, {"local_index": None, "y": 500, "cell_h": 24},
    {"width": 2, "height": 2},
    {"row": 13, "col": 10, "width": 2, "height": 2, "local_index": 129},
    {"row": 25, "col": 1, "width": 1, "height": 2, "local_index": 240},
    {"row": 13, "col": 6, "width": 1, "height": 2, "shape_key": "11"},
    {"row": 13, "col": 6, "width": 1, "height": 2, "cells": 1},
])
def test_invalid_or_nonlive_rows_do_not_move_center(mutation):
    row = {**_snapshot()["minimap_grid_items"][0], **mutation}
    assert ahmed_visible_geometry_grid_range((100, 110, 120), [row]) == (100, 110, 120)


@pytest.mark.parametrize("local,expected", [(0, 110), (105, 110), (125, 130), (195, 200), (249, 250)])
def test_marker_depth_is_soft_and_does_not_sum_placeholder_cells(local, expected):
    row = {**_snapshot()["minimap_grid_items"][0], "local_index": local, "cells": 99}
    result = ahmed_visible_geometry_grid_range((100, 110, 120), [row])
    assert result[0] == 100
    assert result[1] == expected
    assert result[0] <= result[1] <= result[2] <= 250


def test_live_producer_preserves_marker_and_shaped_depth():
    from bidking_lab.live.monitor import _minimap_grid_items
    from bidking_lab.live.types import GridItemObservation, LiveObservationBatch

    batch = LiveObservationBatch(source="packet", phase="reading", session_id="synthetic:1", grid_items=(
        GridItemObservation(cells=1, source="packet", confidence="exact", local_index=125, quality=3),
        GridItemObservation(cells=6, source="packet", confidence="exact", local_index=104, shape_key="23", quality=4),
    ))
    rows = _minimap_grid_items((batch,), {}, session_id="synthetic:1")
    assert rows[0]["width"] is None and rows[0]["row"] is None
    assert (rows[1]["row"], rows[1]["col"], rows[1]["width"], rows[1]["height"]) == (11, 5, 2, 3)
    assert ahmed_visible_geometry_grid_range((100, 110, 120), rows) == (100, 130, 140)
    assert ahmed_visible_geometry_grid_range((100, 110, 120), rows[1:]) == (100, 130, 140)
    assert _minimap_grid_items((batch,), {}, session_id="synthetic:2") == []


def test_capacity_rollback_never_publishes_an_inverted_band(monkeypatch):
    monkeypatch.setenv("BIDKING_DISABLE_DYNAMIC_WAREHOUSE_CAPACITY", "1")
    row = {**_snapshot()["minimap_grid_items"][0], "local_index": 249}
    assert ahmed_visible_geometry_grid_range((100, 110, 120), [row]) == (100, 110, 120)


def test_known_quality_cells_stay_exact_while_unknown_tail_grows(monkeypatch):
    snapshot = _snapshot(counts={"q1": 14, "q3": 10, "q4": 12},
                         quality_cells={"q1": 30, "q3": 20, "q4": 30})
    monkeypatch.setenv(DISABLE, "1")
    before = engine.run_reference_engine(snapshot)
    monkeypatch.delenv(DISABLE)
    after = engine.run_reference_engine(snapshot)
    assert after.total_grid_range[1] == 130
    for key, cells in {"q1": 30, "q3": 20, "q4": 30}.items():
        assert after.quality_cells_ranges[key] == before.quality_cells_ranges[key] == (cells, cells, cells)
    assert after.red_cells_range[1] > before.red_cells_range[1]
    assert after.quality_count_cells_candidates == before.quality_count_cells_candidates
    assert (after.conservative, after.balanced, after.aggressive) == (
        before.conservative, before.balanced, before.aggressive)


@pytest.mark.parametrize("failure", ["cell-below-count", "zero-count-positive-cells", "changed-exact-tier"])
def test_incompatible_second_pass_is_rejected_atomically(monkeypatch, failure):
    snapshot = _snapshot(counts={"q1": 14, "q3": 10, "q4": 12},
                         quality_cells={"q1": 30, "q3": 20, "q4": 30})
    if failure == "zero-count-positive-cells":
        snapshot["structured_ref_inputs"]["counts"]["q5"] = 0
    monkeypatch.setenv(DISABLE, "1")
    before = engine.run_reference_engine(snapshot)
    monkeypatch.delenv(DISABLE)
    original = engine._run_reference_engine_impl
    targets = []

    def invalid_second_pass(snapshot, **kwargs):
        target = kwargs.get("_grid_identity_target")
        if target is None:
            return original(snapshot, **kwargs)
        targets.append(target)
        cells = dict(before.quality_cells_ranges)
        if failure == "cell-below-count":
            cells["q1"] = (1, 1, 1)
        elif failure == "changed-exact-tier":
            cells["q1"] = (31, 31, 31)
        else:
            # The retained first-pass q5 low arm has zero items in this fixture.
            assert before.quality_count_ranges["q5"][0] == 0
            cells["q5"] = (1, 2, 3)
        return replace(before, quality_cells_ranges=cells)

    monkeypatch.setattr(engine, "_run_reference_engine_impl", invalid_second_pass)
    after = engine.run_reference_engine(snapshot)
    assert targets == [130]
    assert after.as_dict() == before.as_dict()


@pytest.mark.parametrize("kind", ["no-position", "other-hero", "settled", "hard-total", "exhausted", "zero-red"])
def test_real_engine_preserved_states(monkeypatch, kind):
    snapshot = _snapshot()
    if kind == "no-position":
        snapshot["minimap_grid_items"] = []
    elif kind == "other-hero":
        snapshot["hero"] = "ethan"
    elif kind == "settled":
        snapshot["phase"] = "settled"
    elif kind == "hard-total":
        snapshot["structured_ref_inputs"] = {"total_count": 10, "total_cells": 24}
    elif kind == "exhausted":
        snapshot["structured_ref_inputs"] = {
            "total_count": 10, "counts": {"q1": 5, "q3": 4, "q6": 1},
            "quality_cells": {"q1": 10, "q3": 8, "q6": 6},
        }
    else:
        snapshot["structured_ref_inputs"]["counts"] = {"q6": 0}
    monkeypatch.setenv(DISABLE, "1")
    before = engine.run_reference_engine(snapshot)
    monkeypatch.delenv(DISABLE)
    after = engine.run_reference_engine(snapshot)
    assert before.as_dict() == after.as_dict()
    if kind == "hard-total":
        assert engine._hard_total_grid_target_int(after.evidence) == 24
        assert after.total_grid_range == (24, 24, 24)
    if kind == "exhausted":
        assert engine._fixed_quality_counts_exhaust_total_grid(after.evidence)


def test_failed_second_pass_does_not_publish_geometry(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setenv(DISABLE, "1")
    before = engine.run_reference_engine(snapshot)
    monkeypatch.delenv(DISABLE)
    original = engine._run_reference_engine_impl
    targets = []

    def fail_second_pass(snapshot, **kwargs):
        target = kwargs.get("_grid_identity_target")
        if target is not None:
            targets.append(target)
            return replace(before, combo_count=0, quality_cells_ranges={})
        return original(snapshot, **kwargs)

    monkeypatch.setattr(engine, "_run_reference_engine_impl", fail_second_pass)
    after = engine.run_reference_engine(snapshot)
    assert targets == [130]
    assert after.as_dict() == before.as_dict()


@pytest.mark.parametrize("depth", [5, 13])
def test_instrumented_engine_call_budget_and_quote_identity(monkeypatch, depth):
    snapshot = _snapshot()
    snapshot["minimap_grid_items"][0]["local_index"] = (depth - 1) * 10 + 5
    original = engine._run_reference_engine_impl
    calls = []

    def measured(snapshot, **kwargs):
        calls.append(kwargs.get("_grid_identity_target"))
        return original(snapshot, **kwargs)

    monkeypatch.setattr(engine, "_run_reference_engine_impl", measured)
    reports = []
    for disabled in (True, False):
        monkeypatch.setenv(DISABLE, "1" if disabled else "0")
        calls.clear()
        started = time.perf_counter()
        result = engine.run_reference_engine(snapshot)
        reports.append((result, tuple(calls), time.perf_counter() - started))
    before, after = reports
    assert len(after[1]) <= 2
    assert (before[0].conservative, before[0].balanced, before[0].aggressive) == (
        after[0].conservative, after[0].balanced, after[0].aggressive)
    if depth == 13:
        assert after[1][-1] == 130
        assert after[0].total_grid_range[1] == 130
        assert any(n == "ahmed_visible_geometry_soft_center:130" for n in after[0].notes)
    else:
        assert before[0].as_dict() == after[0].as_dict()
    print({"depth": depth, "old_calls": before[1], "new_calls": after[1],
           "old_seconds": round(before[2], 3), "new_seconds": round(after[2], 3)})
