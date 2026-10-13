"""Regression gate for the partial-known cell-floor display center (2026-07-04, 群友 2401 R3 diag).

Bug: anchor-only reveals (position, no shape — 沉底大红 as 1-cell marker) contribute no cells to
quality_cell_floors, so a floor of 15 (one revealed car) with red count locked at 3 ranked the
display grid options around count*grid_mean(9.6) -> [15,16,17] against truth 21 (15+4+2). Fix:
when the floor covers only SOME items (floor_items < count), center = floor + unknown_n*grid_mean.
Rollback: AHMAD_DISABLE_PARTIAL_FLOOR_CENTER=1.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    _coarse_quality_reveal_floors,
    _display_grid_options_for_quality,
    RefEvidence,
)


def _evidence(cell_floors, floor_item_counts):
    return RefEvidence(
        hero="ahmed", map_id=2401, phase="bidding", total_count=None,
        fixed_counts={}, min_counts={}, count_sums={}, avg_cells={}, quality_cells={},
        quality_cell_floors=cell_floors, avg_values={}, quality_values={},
        quality_value_floors={}, quality_value_floor_item_counts={}, split_counts={},
        split_quality_cells={}, split_avg_cells={}, random_value_floors=(),
        total_grid_target=None, soft_avg_cell_keys=frozenset(), soft_avg_value_keys=frozenset(),
        v3_conservative="", v3_balanced="", v3_aggressive="", source_notes=(),
        quality_cell_floor_item_counts=floor_item_counts,
    )


COUNTS = {"q1": 5, "q2": 0, "q3": 3, "q4": 2, "q5": 1, "q6": 3}


def test_partial_known_floor_centers_on_floor_plus_prior(monkeypatch):
    monkeypatch.delenv("AHMAD_DISABLE_PARTIAL_FLOOR_CENTER", raising=False)
    # design case shape: floor 15 from ONE known red, count 3 -> 2 unknown-size reds
    ev = _evidence({"q6": 15.0}, {"q6": 1})
    options = _display_grid_options_for_quality(COUNTS, ev, "q6")
    assert options, "options must not be empty"
    # center = 15 + 2*grid_mean(q6~3.2+) ≈ 21+ -> nearest options must bracket ~21, NOT hug 15
    assert min(options) >= 15
    assert any(option >= 20 for option in options), options
    center = sorted(options, key=lambda o: abs(o - 21))[0]
    assert abs(center - 21) <= 2, options


def test_rollback_env_restores_old_ranking(monkeypatch):
    monkeypatch.setenv("AHMAD_DISABLE_PARTIAL_FLOOR_CENTER", "1")
    ev = _evidence({"q6": 15.0}, {"q6": 1})
    options = _display_grid_options_for_quality(COUNTS, ev, "q6")
    # Old behavior ranks around count*mean (9.6): the smallest legal candidates win. "Legal" now starts
    # at 17, not 15 — BUG-L (2026-07-11, 群友 2407 "2件12格"): the floor covers only the ONE red it was
    # revealed from, so the other 2 reds of this 3-red combo still own >= 1 cell each (15 + 2 = 17).
    # The old 15 expectation described a composition where two reds occupied zero cells.
    assert options and min(options) == 17, options
    assert max(options) <= 20, options


def test_fully_known_floor_unchanged(monkeypatch):
    monkeypatch.delenv("AHMAD_DISABLE_PARTIAL_FLOOR_CENTER", raising=False)
    # floor covers ALL 3 reds (floor_items == count) -> partial-known path must not fire;
    # behavior identical to legacy (floor acts as plain lower bound)
    ev_new = _evidence({"q6": 21.0}, {"q6": 3})
    ev_legacy = _evidence({"q6": 21.0}, None)  # old evidence without the field
    assert _display_grid_options_for_quality(COUNTS, ev_new, "q6") == \
        _display_grid_options_for_quality(COUNTS, ev_legacy, "q6")


def test_no_floor_unchanged():
    ev = _evidence({}, {})
    base = _display_grid_options_for_quality(COUNTS, ev, "q6")
    assert base  # normal prior-ranked options, path untouched


def test_partial_red_fp_gives_partial_credit():
    from ahmad_ref_engine import (
        _revealed_red_minimap_partial_footprint,
        _revealed_red_minimap_footprint,
    )
    known = {"quality": 6, "item_id": 1046004, "cells": 12, "shape_key": "34"}
    anchor = {"quality": 6, "cells": 1}
    # 拆红 case (5606 R2): known 12-cell red + anchor -> partial gives (1,12); all-or-nothing None
    assert _revealed_red_minimap_partial_footprint([known, anchor]) == (1, 12)
    assert _revealed_red_minimap_footprint([known, anchor]) is None
    # all anchors -> both None
    assert _revealed_red_minimap_partial_footprint([anchor, dict(anchor)]) is None
    # all known -> partial agrees with all-or-nothing
    k2 = {"quality": 6, "item_id": 1, "cells": 4, "shape_key": "22"}
    assert _revealed_red_minimap_partial_footprint([known, k2]) == (2, 16)
    assert _revealed_red_minimap_footprint([known, k2]) == (2, 16)


def test_coarse_floor_reports_contributing_item_count():
    snapshot = {
        "public_info_rows": [
            {
                "info_id": 200021,
                "revealed_items_detail": [
                    # known car: quality+cells -> contributes floor AND item count
                    {"runtime_id": 1, "quality": 6, "cells": 15, "item_id": 1086008, "shape_code": 53},
                    # anchor-only: quality + position, no cells/shape -> counted, NO floor contribution
                    {"runtime_id": 2, "quality": 6, "local_index": 30},
                    {"runtime_id": 3, "quality": 6, "local_index": 41},
                ],
            }
        ]
    }
    (
        counts,
        cell_floors,
        _vf,
        _catalog_vf,
        _vfic,
        _sc,
        _svf,
        floor_items,
    ) = _coarse_quality_reveal_floors(snapshot, source_notes=[])
    # identified car is excluded from coarse min-COUNTS (counted via minimap elsewhere) but its
    # cells DO feed the floor; the 2 anchors count but contribute no cells — mirrors the real diag.
    assert counts.get("q6") == 2
    assert cell_floors.get("q6") == 15.0
    assert floor_items.get("q6") == 1
