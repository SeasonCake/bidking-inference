"""Design-case hard gate for B6: pure-prior red cells clamped to the geometric room.

aisha 2401 R3 (session 1683352651649900, 0.2.5-diag): the phantom (pure-prior) red showed 12-14 cells
while the confirmed non-red tiers already fill/overflow the reported grid (total_grid 52 < non-red 55) —
the red estimate had decoupled from the geometric remainder (author: the "red/empty" room is the anchor).

Fix (author-approved 2026-07-08, env BIDKING_DISABLE_RED_CELLS_GEOMETRY_CLAMP):
  - 保守/参考 red cells clamp to the geometric room; 激进 KEEPS the phantom-red hedge (the "no room"
    signal can come from an under-cut grid, so 激进 stays the upper bound and the value band keeps
    bracketing truth instead of under-bidding).
  - 参考 bid strips the phantom red by the same fraction; 激进 bid keeps it.
Only PURE-PRIOR red (zero evidence red exists) is touched — revealed/constrained red is never cut.

Sample is gitignored; SKIP when absent. See memory b3456-diag-investigation-0708 (B6).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from audit_aisha_gap import _build_snapshot, _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)

FATBEANS = ROOT / "data" / "samples" / "fatbeans"
B6_SAMPLE = "fatbeans_valid_aisha_2401_3rounds_2401_1683352651649900_9900.json"
DISABLE = "BIDKING_DISABLE_RED_CELLS_GEOMETRY_CLAMP"
DISABLE_COUNT_KEPT_COUPLING = "BIDKING_DISABLE_B6_COUNT_KEPT_BID_COUPLING"


def _run(sample: Path, hero: str, disable: bool) -> dict:
    events = parse_fatbeans_capture(str(sample))
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    dr = next((r for r in range(len(pre), 0, -1)
               if not all(b.phase == "reading" for b in pre[:r])), None)
    snap = prepare_reference_engine_snapshot(
        _build_snapshot(hero=hero, events=events, prefix_batches=pre[:dr], map_id=_map_id_from_events(events))
    )
    prev = os.environ.pop(DISABLE, None)
    try:
        if disable:
            os.environ[DISABLE] = "1"
        return run_reference_engine(snap, max_combos=200_000).as_dict()
    finally:
        os.environ.pop(DISABLE, None)
        if prev is not None:
            os.environ[DISABLE] = prev


def test_b6_pure_prior_red_clamped_to_geometry_aggressive_hedge_kept() -> None:
    sample = FATBEANS / B6_SAMPLE
    if not sample.exists():
        pytest.skip(f"sample {B6_SAMPLE} absent (gitignored) — local-only gate")

    events = parse_fatbeans_capture(str(sample))
    st = _settlement_breakdown(events) or {}
    truth_val = int(st.get("total_value") or 0)
    assert truth_val > 0

    off = _run(sample, "aisha", disable=True)
    on = _run(sample, "aisha", disable=False)

    off_rc = list(off["red_cells_range"])
    on_rc = list(on["red_cells_range"])

    # OFF: the defect is real — phantom red overflows the grid (sum non-red cells > total_grid) and the
    # red cells are large despite no geometric room.
    _nonred = sum(int((off["quality_cells_ranges"].get(q) or [0, 0, 0])[1] or 0) for q in ("q1", "q3", "q4", "q5"))
    assert _nonred > int(off["total_grid_range"][1]), "expected non-red cells to overflow total_grid (defect)"
    assert off_rc[1] >= 10, f"expected large phantom red mid OFF, got {off_rc}"

    # ON: 保守/参考 red clamped to ~room (0 here); 激进 keeps the hedge (unchanged, > 0).
    assert on_rc[0] <= off_rc[0] and on_rc[1] < off_rc[1], f"保守/参考 red not clamped: {on_rc} vs {off_rc}"
    assert on_rc[1] <= 1, f"参考 red should clamp to the geometric room (~0), got {on_rc}"
    assert on_rc[2] == off_rc[2], f"激进 red hedge must be preserved: {on_rc[2]} vs {off_rc[2]}"

    # 参考 bid drops (phantom red stripped) but 激进 keeps the hedge, so the band still brackets truth.
    assert on["balanced"] < off["balanced"], "参考 bid should drop after stripping phantom red"
    assert on["aggressive"] == off["aggressive"], "激进 bid hedge must be preserved"
    assert on["conservative"] <= truth_val <= on["aggressive"], (
        f"value band [{on['conservative']},{on['aggressive']}] must bracket truth {truth_val}"
    )


def test_b6_genuine_phantom_red_is_unchanged_by_count_kept_coupling(monkeypatch) -> None:
    """The new coupling is narrow: a genuine forced_red=0 phantom keeps B6's original behavior."""
    sample = FATBEANS / B6_SAMPLE
    if not sample.exists():
        pytest.skip(f"sample {B6_SAMPLE} absent (gitignored) — local-only gate")

    monkeypatch.setenv(DISABLE_COUNT_KEPT_COUPLING, "1")
    old = _run(sample, "aisha", disable=False)
    monkeypatch.delenv(DISABLE_COUNT_KEPT_COUPLING, raising=False)
    new = _run(sample, "aisha", disable=False)
    assert new == old


def test_b6_nonzero_quote_is_unchanged_by_zero_underflow_floor(monkeypatch) -> None:
    """The 5610 repair is zero-only; the original nonzero B6 design case stays byte-identical."""
    sample = FATBEANS / B6_SAMPLE
    if not sample.exists():
        pytest.skip(f"sample {B6_SAMPLE} absent (gitignored) — local-only gate")

    monkeypatch.delenv("BIDKING_DISABLE_B6_NONRED_VALUE_FLOOR", raising=False)
    default = _run(sample, "aisha", disable=False)
    monkeypatch.setenv("BIDKING_DISABLE_B6_NONRED_VALUE_FLOOR", "1")
    rollback = _run(sample, "aisha", disable=False)

    assert not any("aisha_no_room_nonred_value_floor" in str(note) for note in default["notes"])
    assert default == rollback
