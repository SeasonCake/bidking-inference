"""Design-case hard gate for B3: star-map cell_floor mis-read as a count floor (gabriela 2408 latch).

gabriela map 2408 (session 1683352649522511, 0.2.5-diag): the star-map item《星空》reveals a 6-cell red
footprint -> q6 cell_floor 6. The sparse_early soft grid-target (47) is far below the cell-floor-respecting
minimum, so _fit_grids_to_total_target's exact-DP can't reach it and the proportional fallback folds each
grid toward its count. WITHOUT the cell_floor term the folded q6 grid ≈ count, then the post-fit floor
check (fitted[q6] < cell_floor) rejects every combo with red count < 6 -> the red count LATCHES to 6 (a
6-cell footprint mis-read as a 6-item count) and the quote latches at ~1.3M through R2-R4. Truth: 2 red.

Fix: floor the fold at cell_floor so the known footprint survives and the count is free to be the true low
value (env DRAW_HERO_DISABLE_FIT_CELLFLOOR_FLOOR). This uses the FAITHFUL live round_snapshots — offline
_build_snapshot reconstruction drops the star-map reveal and does NOT reproduce the latch (draw heroes,
memory draw-hero-replay-not-faithful-snapshot-at-batch). Snapshots are gitignored (PII); SKIP if absent.
See memory b3456-diag-investigation-0708 (B3).
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
            / "diag_exports" / "b3_gabriela_2408")
DISABLE = "DRAW_HERO_DISABLE_FIT_CELLFLOOR_FLOOR"


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


@pytest.mark.parametrize("round_name", ["r02_bidding", "r03_bidding", "r04_bidding"])
def test_b3_star_map_cellfloor_no_red_count_latch(round_name: str) -> None:
    if not (SNAP_DIR / f"{round_name}.json").exists():
        pytest.skip(f"faithful snapshot {round_name} absent (gitignored) — local-only gate")

    off = _run(round_name, disable=True)
    on = _run(round_name, disable=False)

    off_red = list(off["red_count_range"])
    on_red = list(on["red_count_range"])

    # OFF: the defect is real — the red count is latched to 6 (a 6-cell footprint mis-read as a count).
    assert off_red == [6, 6, 6], f"{round_name} OFF expected latched red [6,6,6], got {off_red}"
    assert off["balanced"] > 1_200_000, f"{round_name} OFF expected latched ~1.3M quote, got {off['balanced']}"

    # ON: the latch is broken — the red count is no longer pinned to 6 and brackets the truth (2 red).
    assert not (on_red[0] == on_red[1] == on_red[2] == 6), f"{round_name} ON still latched to 6: {on_red}"
    assert on_red[0] <= 2 <= on_red[2], f"{round_name} ON red range {on_red} should bracket truth 2"
    assert on["balanced"] < off["balanced"], f"{round_name} ON quote {on['balanced']} should drop below OFF"
