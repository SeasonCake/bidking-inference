"""Design gate for the ethan q4-mid promote-only residual fix (2026-07-09, workflow wf_da4e681e).

The red-residual LIFT reads purple(q4) cells from the appraisal VALUE as value/p25 (LOWEST per-cell
price => MAX plausible purple, ahmad_ref_engine.py ~line 10763). Measured on 99 clean_eval ethan
(corrected-round, 15 gate-passing 3+red rows), that reading OVER-claims purple by ~+4.4 cells median
and starves resid_red = max(0, tg - known_nonred - q4_generous) 1:1 -> 3+red value UNDER-estimate.
q4=EXACT rows over-claim 0 (clean causal contrast). Fix: re-read purple at the MID per-cell price and
PROMOTE the residual, behind FOUR hard gates so it cannot worsen the 1-2red band (already +34.7% OVER;
needs the OPPOSITE correction) or regress 2401/2407 villa phantom-red:
  (1) map_56xx only (only family with a measured over-claim + settlement data; villa/hidden = 0 samples),
  (2) q4 came from the VALUE path (the only over-claiming reading),
  (3) promote-only: the p25 residual did NOT already fire the lift (never enlarge an already-firing row),
  (4) revealed red count (min_counts['q6']) >= 3 -- Ethan reveals red outlines; the band separator.
Default OFF (env ETHAN_ENABLE_Q4_MID_RESID=1). Quantitative behavioral proof = scripts/_tmp_ethan_residual_ab.py
sweep (real samples, off vs on, scope audit) -- not committed here because it needs gitignored clean_eval data,
matching the repo's other ethan sweep gates. This file locks the STRUCTURE + the trim direction.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ahmad_ref_engine as E  # noqa: E402
from ahmad_ref_engine import ETHAN_QUALITY_PERCELL_BY_FAMILY  # noqa: E402


def _src():
    target = getattr(E, "_run_reference_engine_impl", E.run_reference_engine)
    return inspect.getsource(target)


def test_q4_mid_all_four_gates_present():
    src = _src()
    # enable env (default OFF) + the four hard gates, all in one guarded block
    assert 'ETHAN_ENABLE_Q4_MID_RESID") == "1"' in src, "must be enable-not-disable, default OFF"
    assert '_q4_from_value' in src, "gate 2: only the value-path over-claims"
    assert '_fam_l == "map_56xx"' in src, "gate 1: family-restricted (villa/hidden byte-identical)"
    assert "_red_lift_target <= _rv_scaled_l" in src, "gate 3: promote-only (p25 not already firing)"
    assert '.get("q6", 0) or 0) >= 3' in src, "gate 4: revealed red>=3 band separator"


def test_q4_mid_is_promote_only_never_shrinks():
    src = _src()
    # the recompute must only replace resid when the mid residual is LARGER (never shrink -> never
    # cut an existing lift), and it must use the MID divisor (_pc4m[1]), not p25 (_pc4m[0]).
    assert "_resid_red_mid > _resid_red" in src, "promote-only: never shrink the residual"
    assert "_q4_mid = float(_q4_value) / _pc4m[1]" in src, "must divide by MID price, not p25"
    assert "ethan_q4_mid_resid" in src, "note for the sweep changed-row audit"


def test_q4_from_value_flag_only_on_value_path():
    src = _src()
    # the flag is set True ONLY inside the appraisal-value elif (not the exact-cells or avg-cells paths)
    assert "_q4_from_value = False" in src
    assert "_q4_from_value = True" in src
    # exactly one True assignment (guards against accidentally flagging the exact path too)
    assert src.count("_q4_from_value = True") == 1


def test_mid_trim_direction_enlarges_residual():
    """value/mid < value/p25 (mid price > p25 price) => fewer purple cells => LARGER red residual.
    This is the whole point; a table edit that broke mid>p25 would silently invert the fix."""
    p25, mid, p75 = ETHAN_QUALITY_PERCELL_BY_FAMILY["q4"]["map_56xx"]
    assert p25 < mid < p75, "per-cell price band must be ordered"
    v = 80000.0  # an appraisal value
    cells_p25 = v / p25   # current generous reading (MAX purple)
    cells_mid = v / mid   # new central reading
    assert cells_mid < cells_p25, "mid reading must claim FEWER purple cells than p25"
    # ~10% proportional trim (mid/p25 ~ 2330/2092 ~ 1.114)
    assert 0.88 < (cells_mid / cells_p25) < 0.92


def test_master_kill_still_dominates():
    src = _src()
    # the whole residual block (incl. this recompute) is under ETHAN_DISABLE_RED_RESID_LIFT
    assert 'ETHAN_DISABLE_RED_RESID_LIFT") != "1"' in src
