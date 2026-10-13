"""Regression gate for the aisha fully-pinned red residual (2026-07-05, live 5608 R4/R5).

When 普/良/优/极 scans pin q1+q3+q4+q5 cells EXACTLY, red is a pure grid residual = total − non_red.
The combo count-prior over-counts invisible red (5608 R4: 16-23 cells vs residual ~5, truth 6) because
a weak min_q6>=1 floor + a slightly-high total leak items into red and the per-item cell prior inflates
the cells; the reported total grid (a post-combo layout override) never feeds back into red so the
per-quality cells sum silently exceeds the grid. The fix reprices red as the residual (cut-only).
Fixture: HeroRefDiag-20260705-231932-5608 R4/R5 (truth 32件/85格/595835, red 3件/6格/271400).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "HeroRefDiag-20260705-231932-5608_1683352518960938" / "round_snapshots"


def _run(rf, R, env=None):
    import os
    saved = {}
    if env:
        for k, v in env.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        snap = json.load(open(FIX / rf, encoding="utf-8"))
        return run_reference_engine(prepare_reference_engine_snapshot(snap),
                                    max_combos=hero_max_combos_for_round("aisha", R)).as_dict()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_5608_red_residual_caps_phantom_red():
    res = _run("r04_bidding.json", 4)
    rc = res.get("red_cells_range")
    # truth red = 6 cells; residual caps the phantom 16-23 down to <=8
    assert rc[2] <= 9, rc
    assert res["quality_cells_ranges"]["q6"] == rc
    assert "q6" not in res["quality_cells_candidates"]
    assert "q6" not in res["quality_count_cells_candidates"]
    assert any("pinned_red_residual" in n for n in res.get("notes") or [])
    bal = res.get("balanced")
    # truth total 595835; was 1.16M before the fix -> now within ~25% (not 2x over)
    assert bal < 800000, bal
    assert (
        res["conservative"],
        res["balanced"],
        res["aggressive"],
    ) == (437463, 691961, 803134)


def test_5608_off_switch_restores_phantom():
    res = _run("r04_bidding.json", 4, env={"AISHA_DISABLE_PINNED_RED_RESIDUAL": "1"})
    assert not any("pinned_red_residual" in n for n in res.get("notes") or [])
    assert res.get("red_cells_range")[2] >= 15  # phantom red back
    assert res["quality_cells_ranges"]["q6"] == res["red_cells_range"]
    assert "q6" in res["quality_cells_candidates"]
    assert "q6" in res["quality_count_cells_candidates"]


def test_5608_cut_only_never_lifts():
    on = _run("r04_bidding.json", 4).get("balanced")
    off = _run("r04_bidding.json", 4, env={"AISHA_DISABLE_PINNED_RED_RESIDUAL": "1"}).get("balanced")
    assert on <= off  # residual is cut-only
