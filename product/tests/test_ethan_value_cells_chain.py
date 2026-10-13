"""Regression gates for the 2026-07-05 ethan value-cells chain (author live games 2401/2407).

Live failure being fixed: two villa games quoted -51%/-61% vs settled truth. Root causes:
  (a) occupancy lone-deep discount cut rect 110->86 (wrong for ethan's UNIFORM ~50-60% reveal;
      a lone revealed deep item implies hidden neighbours, not absence) -> grid anchor 80 vs 101;
  (b) 优品估价 (exact q4 value) never fed cells: per-cell settled price is near-constant (~2330
      +/-10% across families, 1522-game calibration) so value/percell is a strong cells proof —
      truth 47 cells vs count*mean display 17/23/29;
  (c) combos cannot compose big hidden reds -> red p50 far below the residual-implied red value;
  (d) R5 full outline proves total cells exactly but the published range kept a +/-4 spread.
Fixes: no-lone-deep occupancy (alpha 1.0), q4/q5 value->cell FLOOR, red-residual LIFT-only quote
factor (gated on the full author item chain: low scans + purple appraisal + gold info), R5 range
collapse. All ethan-gated; kill envs per block.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ETHAN_QUALITY_PERCELL_BY_FAMILY,
    ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP_DEFAULT,
    ETHAN_RESID_RED_PERCELL_BY_FAMILY,
    _ethan_occupancy_grid_estimate,
    _ethan_quality_percell,
    _ethan_red_resid_lift_cap,
)


def _mk_items(rows, cols=10, per_row=10, lone_deep_rows=0):
    items = [
        {"row": r, "col": c, "width": 1, "height": 1, "cells": 1}
        for r in range(1, rows + 1) for c in range(1, per_row + 1)
    ]
    if lone_deep_rows:
        items.append({"row": rows + 1, "col": 1, "width": 1, "height": lone_deep_rows, "cells": lone_deep_rows})
    return items


def test_occupancy_no_lonedeep_discount_by_default(monkeypatch):
    """2401 pattern: one lone item reaching the deepest rows must NOT collapse the rectangle.
    8 full rows + a lone 3-tall item to row 11 -> rect 110; default = 110 - 1.0*10 = 100 (truth 101),
    old lone-deep behaviour (env restore) discounts to support*cols + tail."""
    monkeypatch.delenv("ETHAN_OCCUPANCY_LONEDEEP", raising=False)
    monkeypatch.delenv("ETHAN_OCCUPANCY_GRID_ALPHA", raising=False)
    est, parts = _ethan_occupancy_grid_estimate(_mk_items(8, lone_deep_rows=3))
    assert parts["raw_deepest"] == 11
    assert est == 100  # rect 110 - alpha(1.0)*10; NOT the lone-deep-discounted 86-ish
    monkeypatch.setenv("ETHAN_OCCUPANCY_LONEDEEP", "1")
    est_old, _ = _ethan_occupancy_grid_estimate(_mk_items(8, lone_deep_rows=3))
    assert est_old < est  # env restores the old (discounting) semantics


def test_percell_tables_flat_and_family_lookup():
    # Purple per-cell price is validated near-constant; a change here must re-run calib_percell_q4q5
    for fam, (p25, mid, p75) in ETHAN_QUALITY_PERCELL_BY_FAMILY["q4"].items():
        assert 1900 < p25 < mid < p75 < 2800, fam
    assert _ethan_quality_percell("q4", 2401) == ETHAN_QUALITY_PERCELL_BY_FAMILY["q4"]["villa"]
    assert _ethan_quality_percell("q5", 5606) == ETHAN_QUALITY_PERCELL_BY_FAMILY["q5"]["map_56xx"]
    assert _ethan_quality_percell("q6", 2401) is None
    assert ETHAN_RESID_RED_PERCELL_BY_FAMILY["villa"] == 46627.0


def _engine_src():
    import inspect
    import ahmad_ref_engine as E
    target = getattr(E, "_run_reference_engine_impl", E.run_reference_engine)
    return inspect.getsource(target)


def test_lift_is_lift_only_and_default_on():
    """The quote block must never multiply by <1 (the v2 cut reversed the author's live games) and
    must gate on the full chain incl. gold; kill env documented."""
    src = _engine_src()
    assert "ETHAN_DISABLE_RED_RESID_LIFT" in src
    assert "_lift = min(max(1.0, _lift), _lift_cap)" in src
    assert "_q5_resolved is not None" in src  # gold gate (2401 R3 overshoot guard)
    assert '"action_100107_q5_cells" in evidence.source_notes' in src
    assert "ethan_red_resid_lift" in src


def test_q5_scan_lift_cap_default_and_rollback_are_scoped(monkeypatch):
    monkeypatch.delenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", raising=False)
    monkeypatch.delenv("ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP", raising=False)
    assert ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP_DEFAULT == 1.10
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 1.10
    assert _ethan_red_resid_lift_cap(q5_scan=False) == 2.0

    monkeypatch.setenv("ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP", "1")
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 2.0
    monkeypatch.delenv("ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP")

    monkeypatch.setenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", "1.15")
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 1.15
    monkeypatch.setenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", "not-a-number")
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 1.10
    monkeypatch.setenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", "0.5")
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 1.0
    monkeypatch.setenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", "3.0")
    assert _ethan_red_resid_lift_cap(q5_scan=True) == 2.0


def test_value_cell_floor_and_grid_lock_wired():
    src = _engine_src()
    assert "ETHAN_DISABLE_VALUE_CELL_FLOOR" in src
    assert "ethan_q4_value_cell_floor" in src or "ethan_{_vq}_value_cell_floor" in src
    assert "ETHAN_DISABLE_FULL_OUTLINE_GRID_LOCK" in src
    assert "ethan_full_outline_grid_lock" in src


def test_display_bands_value_path_and_residual_red():
    """Phase-2 display honesty (2026-07-05): 估价-only q4 publishes the percell p10/p90 band
    (narrow combo band had 27-48% truth coverage); fully-determined R5 games publish the
    residual red band (author 2407 R5: combo showed 2/7/14 vs residual-provable 15)."""
    import inspect
    import ahmad_ref_engine as E
    target = getattr(E, "_run_reference_engine_impl", E.run_reference_engine)
    src = inspect.getsource(target)
    assert "ETHAN_DISABLE_DISPLAY_CELLS_CONSISTENCY" in src
    assert "ethan_q6_residual_display_band" in src
    b4 = E.ETHAN_QUALITY_PERCELL_DISPLAY_BAND["q4"]
    assert b4[0] < b4[1] < b4[2]
    # 2407-style arithmetic: value 113857 -> band [floor(v/p90), round(v/p50), ceil(v/p10)]
    lo, mid, hi = (int(113857 // b4[2]), round(113857 / b4[1]), -(-113857 // int(b4[0])))
    assert lo <= 48 <= hi and hi >= 56  # truth 57 within ceil'd upper
