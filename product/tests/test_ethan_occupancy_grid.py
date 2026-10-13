"""Regression gate for the ethan R1-R4 occupancy grid estimator (env-gated, default OFF).

Design cases (author 2026-07-03, real 2601 games): truth 135 -> R1-R4 fell to prior 95-121 (under);
truth 186 -> 235-243 (over). With the estimator ON both converge (128 / 168) and R5 stays exact.
The estimator anchors the partial-reveal grid to revealed footprints:
    est = max(adjusted_rect - ALPHA*cols, full_row_floor, distinct_revealed)
See memory ethan-grid-primary-数格子 / ethan-synthesis-b-reveal-mechanism.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ETHAN_HERO_ID,
    _apply_ethan_skill_evidence,
    _ethan_occupancy_grid_estimate,
)


def _mk_items(rows: int, cols: int = 10, *, skip_bottom: int = 0, per_row: int = 10):
    """Synthetic revealed footprints: 1x1 items packing `per_row` cells of each row."""
    items = []
    for r in range(1, rows + 1 - skip_bottom):
        for c in range(1, per_row + 1):
            items.append({"row": r, "col": c, "width": 1, "height": 1, "cells": 1})
    return items


def test_estimate_full_pack_discounts_bottom_deficit(monkeypatch):
    monkeypatch.setenv("ETHAN_OCCUPANCY_GRID_ALPHA", "1.2")
    est, parts = _ethan_occupancy_grid_estimate(_mk_items(15))
    # full 15x10 revealed: full-row floor proves 150; estimate never sinks below a proof
    assert parts["raw_deepest"] == 15
    assert est == 150


def test_estimate_partial_reveal_uses_depth_minus_alpha(monkeypatch):
    monkeypatch.setenv("ETHAN_OCCUPANCY_GRID_ALPHA", "1.2")
    # 15 rows deep but only 6 cells of each row revealed (60% uniform coverage, no full row)
    est, parts = _ethan_occupancy_grid_estimate(_mk_items(15, per_row=6))
    assert parts["raw_deepest"] == 15
    assert parts["full_row_floor"] == 0
    assert est == 150 - 12  # adjusted_rect(150) - 1.2*10

def test_estimate_none_on_thin_view():
    # 4 positioned items < 8 guard
    assert _ethan_occupancy_grid_estimate(_mk_items(1, per_row=4)) is None
    assert _ethan_occupancy_grid_estimate([]) is None


def _partial_snapshot():
    outline = [
        {"runtime_id": 100 + i, "local_index": i, "cells": 1, "shape_code": 11}
        for i in range(30)
    ]
    minimap = [
        {"row": r, "col": c, "width": 1, "height": 1, "cells": 1}
        for r in range(1, 13) for c in range(1, 7)  # 12 rows deep, 6/10 per row
    ]
    return {
        "skill_reveal_rows": [
            {"hero_id": ETHAN_HERO_ID, "skill_id": 1002081, "observed_items": outline}
        ],
        "minimap_grid_items": minimap,
    }


def _run_apply(snapshot):
    notes: list[str] = []
    captured: dict[str, float] = {}

    def set_count(v, note):
        captured["count"] = v
        notes.append(note)

    def set_grid(v, note):
        captured["grid"] = v
        notes.append(note)

    _apply_ethan_skill_evidence(
        snapshot, set_total_count=set_count, set_total_grid_target=set_grid,
        source_notes=notes,
    )
    return captured, notes


def test_partial_outline_on_by_default(monkeypatch):
    # DEFAULT ON since 2026-07-04 (author: net-gain verified); kill-switch must fully disable.
    monkeypatch.delenv("ETHAN_DISABLE_OCCUPANCY_GRID", raising=False)
    monkeypatch.setenv("ETHAN_OCCUPANCY_GRID_ALPHA", "1.2")
    captured, notes = _run_apply(_partial_snapshot())
    assert captured.get("grid") == 120 - 12
    assert "ethan_occupancy_grid_estimate" in notes


def test_partial_outline_kill_switch(monkeypatch):
    monkeypatch.setenv("ETHAN_DISABLE_OCCUPANCY_GRID", "1")
    captured, notes = _run_apply(_partial_snapshot())
    assert "grid" not in captured  # disabled: R1 outline stays note-only (prior path)
    assert any(n.startswith("ethan_skill_r1_outline") for n in notes)


def test_full_outline_still_exact_when_on(monkeypatch):
    monkeypatch.delenv("ETHAN_DISABLE_OCCUPANCY_GRID", raising=False)
    snap = _partial_snapshot()
    full = [
        {"runtime_id": 200 + i, "local_index": i, "cells": 2, "shape_code": 21}
        for i in range(40)
    ]
    snap["skill_reveal_rows"].append(
        {"hero_id": ETHAN_HERO_ID, "skill_id": 1002085, "observed_items": full}
    )
    captured, notes = _run_apply(snap)
    assert captured.get("grid") == 80  # full outline exact total wins; occupancy path not taken
    assert "ethan_skill_full_outline_cells" in notes
    assert "ethan_occupancy_grid_estimate" not in notes


def test_non_ethan_snapshot_untouched_even_when_on(monkeypatch):
    monkeypatch.delenv("ETHAN_DISABLE_OCCUPANCY_GRID", raising=False)
    snap = _partial_snapshot()
    snap["skill_reveal_rows"][0]["hero_id"] = 999  # not ethan
    captured, notes = _run_apply(snap)
    assert captured == {}
    assert notes == []
