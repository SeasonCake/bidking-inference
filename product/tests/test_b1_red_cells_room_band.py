"""Design-case hard gate for B1: unlocked red CELLS band widened to be room-informed (cross-hero).

aisha map 5605 R3 (session 1683352662890793, 0.2.5-diag): a total-count item pins the red COUNT (3) but
the per-item footprint is NOT revealed, so the red CELLS band collapses to a false ±1 (红格 9/10/11) around
count*grid_mean while the truth is 4. The honest range is the "red/empty" room (total_grid − known non-red
= red + uncertain cells): red can be as few as count (all 1x1) or fill the whole room. The fix widens the
DISPLAY band to be room-informed (low = min footprint, high = aggressive room) so the left 红格 column
tracks the right red/empty column instead of showing false precision.

Env BIDKING_DISABLE_RED_CELLS_ROOM_BAND. Faithful live snapshots (gitignored, PII); SKIP if absent.
See memory b1-ahmed-lockred-value-band-undercoverage.
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
            / "diag_exports" / "b1_aisha_5605")
DISABLE = "BIDKING_DISABLE_RED_CELLS_ROOM_BAND"
TRUTH_RED_CELLS = 4


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


def test_b1_unlocked_red_cells_band_is_room_informed() -> None:
    if not (SNAP_DIR / "r03_bidding.json").exists():
        pytest.skip("faithful snapshot absent (gitignored) — local-only gate")

    off = _run("r03_bidding", disable=True)
    on = _run("r03_bidding", disable=False)

    off_rc = list(off["red_cells_range"])
    on_rc = list(on["red_cells_range"])

    # OFF: the defect is real — a false ±1 band that does NOT bracket the truth (4).
    assert off_rc[2] - off_rc[0] <= 2, f"expected false ±1 band OFF, got {off_rc}"
    assert not (off_rc[0] <= TRUTH_RED_CELLS <= off_rc[2]), f"OFF band {off_rc} unexpectedly brackets truth"

    # ON: widened to the room-informed band that brackets the truth, red count unchanged (display-only).
    assert on_rc[2] - on_rc[0] > off_rc[2] - off_rc[0], f"ON band {on_rc} not wider than OFF {off_rc}"
    assert on_rc[0] <= TRUTH_RED_CELLS <= on_rc[2], f"ON band {on_rc} should bracket truth {TRUTH_RED_CELLS}"
    assert on["red_count_range"] == off["red_count_range"], "red COUNT must be untouched (display-only)"
    assert on["balanced"] == off["balanced"], "the bid must be untouched (display-only)"
