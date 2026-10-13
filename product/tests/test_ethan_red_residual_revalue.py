"""Regression gate for the ethan red-cell-residual revalue (default OFF, 5ab8bb7).

v1 share-extrapolation was STRUCTURALLY wrong for ethan: his skill outlines carry no quality, so
invisible reds were counted as "non-red" and the residual collapsed to 0 on red-rich vaults (2601
truth-186 design case went +25% -> -60%). v2 = prior-fill: only CONFIRMED quality splits count;
gray cells stay in the unknown mass filled by the family red-share prior.
Acceptance (2026-07-04): real58 MAE 43.9->29.6, over10 31->20.7% (all §4.4 real gates pass);
synth 0-red gate fails due to synth evidence-channel starvation — default OFF until channels fixed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ETHAN_RED_CELL_FRAC_BY_FAMILY,
    ETHAN_RED_PERCELL_BY_FAMILY,
    _ethan_map_family,
    _ethan_nonred_cell_estimate,
)


def _mk(row, col, w, h, quality=None):
    return {"row": row, "col": col, "width": w, "height": h, "quality": quality}


def test_family_mapping_authoritative():
    # mirror of monitor._map_family_from_id: 24/34/44 villa, 25/35/45 shipwreck, 26 hidden
    assert _ethan_map_family(2401) == "villa"
    assert _ethan_map_family(4511) == "shipwreck"
    assert _ethan_map_family(2601) == "hidden"
    assert _ethan_map_family(5604) == "map_56xx"


def test_tables_are_truth_calibrated():
    # 58-pool truth calibration (2026-07-04); a change must re-run the pooled measurement
    assert ETHAN_RED_CELL_FRAC_BY_FAMILY["hidden"] == 0.161
    assert ETHAN_RED_PERCELL_BY_FAMILY["villa"] == 38800.0


def test_gray_outlines_stay_unknown_not_nonred(monkeypatch):
    monkeypatch.delenv("ETHAN_RED_RESIDUAL_MIN_REVEAL", raising=False)
    # 12 rows x 10, half revealed as GRAY (quality None) + one colored red 2x2:
    items = [_mk(r, c, 1, 1, None) for r in range(1, 13) for c in range(1, 6)]
    items.append(_mk(1, 7, 2, 2, 6))
    out = _ethan_nonred_cell_estimate(items, 120.0)
    assert out is not None
    colored_red, confirmed_nonred, revealed = out
    assert colored_red == 4
    # v1's fatal bug: gray cells counted as non-red. v2: gray != confirmed non-red.
    assert confirmed_nonred == 0
    assert revealed == 64


def test_colored_nonred_counts(monkeypatch):
    monkeypatch.delenv("ETHAN_RED_RESIDUAL_MIN_REVEAL", raising=False)
    items = [_mk(1, 1, 5, 2, 3), _mk(3, 1, 5, 2, None), _mk(5, 1, 2, 1, 6)]
    out = _ethan_nonred_cell_estimate(items, 30.0)
    assert out == (2, 10, 22)  # red 2, confirmed non-red 10 (blue), revealed 22


def test_thin_view_returns_none(monkeypatch):
    monkeypatch.delenv("ETHAN_RED_RESIDUAL_MIN_REVEAL", raising=False)
    items = [_mk(1, 1, 2, 2, None)]  # 4 cells of a 100-cell vault = 4% < 35%
    assert _ethan_nonred_cell_estimate(items, 100.0) is None


def test_default_off_with_optin_switch():
    # DEFAULT OFF since 2026-07-04 evening: author's two live villa games (2401/2407) were both CUT
    # by the v2 fill (f=0.887/0.735) while already under-estimating — see engine comment. Re-enable
    # only via the documented opt-in env after a v3 fill passes the live design cases.
    import inspect
    import ahmad_ref_engine as E
    target = getattr(E, "_run_reference_engine_impl", E.run_reference_engine)
    src = inspect.getsource(target)
    assert 'ETHAN_ENABLE_RED_CELL_RESIDUAL_REVALUE") == "1"' in src
    assert "ETHAN_DISABLE_RED_CELL_RESIDUAL_REVALUE" in src
