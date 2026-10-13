"""BUG-H gate: the red-cells geometry clamp must never shrink a LOCKED red count.

群友 ahmed 2601 R5 (v0.2.6 截图: 红件 "5/8/8"). In one and the same run the engine reported two
contradictory red counts:
    quality_count_ranges["q6"] = [8, 8, 8]   (locked by the arithmetic identity 总件 − 其他档; truth 8)
    red_count_range            = [5, 8, 8]   <- the panel renders THIS one
Cause: the B6 red-cells geometry clamp scales the red COUNT (and value) by the CELLS shrink factor.
Scaling the count is coherent only while the count is a free prior; once it is locked, shrinking it
invents a composition the evidence forbids. The clamp must clamp CELLS (and the value that rides on
them), never a locked count. `_is_pure_prior_red` (the clamp's gate) only means "no red-SPECIFIC
evidence" — an identity lock is not red evidence, so it does not exclude this case.

Scope: count-only. The value scaling by the cells factor is kept (red value tracks red cells), so
quotes stay byte-identical on the design sample. Kill: BIDKING_DISABLE_RED_COUNT_CLAMP_GUARD=1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

KILL = "BIDKING_DISABLE_RED_COUNT_CLAMP_GUARD"
FIXTURE = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag2601_ahmed"


def _run(monkeypatch, off: bool) -> dict:
    if off:
        monkeypatch.setenv(KILL, "1")
    else:
        monkeypatch.delenv(KILL, raising=False)
    snap = json.loads((FIXTURE / "r05_bidding.json").read_text(encoding="utf-8"))
    return run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()


@pytest.mark.skipif(
    not (FIXTURE / "r05_bidding.json").exists(),
    reason="2601 diag snapshot absent (gitignored) — local-only gate",
)
def test_2601_locked_red_count_is_not_scaled_by_cells_clamp(monkeypatch) -> None:
    off = _run(monkeypatch, off=True)
    on = _run(monkeypatch, off=False)

    # both runs agree the count is locked at the truth (8)
    assert off["quality_count_ranges"]["q6"] == [8, 8, 8]
    assert on["quality_count_ranges"]["q6"] == [8, 8, 8]

    # OFF: the shipped defect — red_count_range disagrees with q6 (this is what the panel renders)
    assert off["red_count_range"] == [5, 8, 8]
    # ON: the two fields agree
    assert on["red_count_range"] == [8, 8, 8]
    assert on["red_count_range"] == on["quality_count_ranges"]["q6"]

    # the cells clamp itself still runs, now flagged as count-locked
    assert any(str(n).startswith("red_cells_geometry_clamp") for n in on["notes"])
    assert any(str(n).endswith(":count_kept") for n in on["notes"])

    # QUOTES ARE UNTOUCHED — this is a count-coherence repair, not a pricing change.
    for field in ("conservative", "balanced", "aggressive", "red_value_range"):
        assert on[field] == off[field], field

    # Second-order (display-only, by design): with the count no longer fake-spread (5..8 -> 8..8), the
    # H7p1 giant-red tail hedge can finally engage — it requires a PINNED count (hi−lo ≤ 3) and unseen
    # footprints. Red cells hi 27 -> 41 = 8 × hidden p90 5.12; truth 21 stays bracketed either way.
    assert any(str(n).startswith("red_cells_tail_hedge_hi") for n in on["notes"])
    assert not any(str(n).startswith("red_cells_tail_hedge_hi") for n in off["notes"])
    assert on["red_cells_range"][0] == off["red_cells_range"][0] == 15  # lo unchanged
    assert on["red_cells_range"][2] > off["red_cells_range"][2]  # hi honestly widened
    assert on["red_cells_range"][0] <= 21 <= on["red_cells_range"][2]  # truth bracketed


@pytest.mark.skipif(
    not (FIXTURE / "r05_bidding.json").exists(),
    reason="2601 diag snapshot absent (gitignored) — local-only gate",
)
def test_2601_engine_and_panel_red_counts_agree(monkeypatch) -> None:
    from ahmad_live_panel_server import _red_display_ranges

    on = _run(monkeypatch, off=False)
    panel_counts, _cells = _red_display_ranges(on)
    # the panel's BUG-D paired clamp was masking the engine defect; with BUG-H fixed they agree at source
    assert list(panel_counts) == on["red_count_range"] == [8, 8, 8]
