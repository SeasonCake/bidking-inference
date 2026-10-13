"""Design-case hard gate for B5: aisha coarse-value red VALUE band widened on the low side.

aisha map 2410 R3 (0.2.5-diag, faithful round_snapshot): a live quality reveal locks the red COUNT (4)
but the red items are coarse (item_id/value/shape absent) so the per-item value comes from the villa q6
prior. The modeled red-value spread is too tight (rv [613k, 791k, 968k]) while the truth is 336k — a
genuine low-value (family p10) vault whose truth sits BELOW the conservative 613k. The fix widens the
DISPLAY red-value low bound to rv50*ratio and drops the conservative bid by that delta so 保守 is an honest
low-value floor; 参考/激进 (center + upside hedge) are untouched — the villa q6 prior center is correct
(cloud n=103 truth unit median 180k ≈ engine ~194k), so this must NOT lower the center.

Env BIDKING_DISABLE_RED_VALUE_LOW_BAND. Faithful live snapshots (gitignored, PII); SKIP if absent.
Cloud ref_inputs replay is NOT faithful for red value/bid — this MUST use the diag package. See memory
b3456-diag-investigation-0708 / cloud-ref-inputs-replay-fidelity.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

SNAP_DIR = (ROOT / "data" / "samples" / "recordings_inbox_20260708_desktop_diag025"
            / "diag_exports" / "b5_aisha_2410")
DISABLE = "BIDKING_DISABLE_RED_VALUE_LOW_BAND"
TRUTH_RED_VALUE = 336250
TRUTH_TOTAL = 495541


def _run(round_name: str, disable: bool) -> dict:
    snap = json.loads((SNAP_DIR / f"{round_name}.json").read_text(encoding="utf-8-sig"))
    prep = prepare_reference_engine_snapshot(snap)
    prev = os.environ.pop(DISABLE, None)
    try:
        if disable:
            os.environ[DISABLE] = "1"
        return run_reference_engine(prep).as_dict()
    finally:
        os.environ.pop(DISABLE, None)
        if prev is not None:
            os.environ[DISABLE] = prev


def test_b5_coarse_red_value_low_band() -> None:
    if not (SNAP_DIR / "r04_bidding.json").exists():
        pytest.skip("faithful snapshot absent (gitignored) — local-only gate")

    off = _run("r04_bidding", disable=True)
    on = _run("r04_bidding", disable=False)

    off_rv = list(off["red_value_range"])
    on_rv = list(on["red_value_range"])

    # OFF: the defect is real — the conservative red value overestimates truth (336k < 613k).
    assert off_rv[0] > TRUTH_RED_VALUE, f"expected OFF cons red {off_rv[0]} to overestimate truth {TRUTH_RED_VALUE}"

    # ON: red-value low bound widened DOWN to bracket the low-value truth; center + high untouched.
    assert on_rv[0] < off_rv[0], f"ON low bound {on_rv[0]} not lower than OFF {off_rv[0]}"
    assert on_rv[0] <= TRUTH_RED_VALUE, f"ON low bound {on_rv[0]} should reach the truth {TRUTH_RED_VALUE}"
    assert on_rv[1] == off_rv[1], "参考 (rv50 center) must be untouched — the prior center is correct"
    assert on_rv[2] == off_rv[2], "激进 (rv90 upside hedge) must be untouched"

    # The conservative BID drops toward an honest floor (below the settled total), 参考/激进 unchanged.
    assert on["conservative"] < off["conservative"], "conservative bid should drop to an honest low floor"
    assert on["conservative"] <= TRUTH_TOTAL, f"conservative {on['conservative']} should sit at/below truth total"
    assert on["balanced"] == off["balanced"], "参考 bid must be untouched"
    assert on["aggressive"] == off["aggressive"], "激进 bid must be untouched"
    assert on["red_count_range"] == off["red_count_range"], "red COUNT must be untouched (value-band only)"
    assert any(n.startswith("red_value_low_band:") for n in on["notes"]), "expected the B5 note"
