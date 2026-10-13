"""Design-case hard gate for B4: soft public tier-mean (avg_value) full-domain divisor lock.

A fractional public tier-mean price is an EXACT integer-product constraint (total value = avg*count
must be integer -> count is a multiple of the reduced denominator). For draw-heroes without an exact
total item count (total_count=None), the count-prior probability window is centered on total*p and can
EXCLUDE the true multiple; the soft-key divisor lock in _filter_quality_counts_for_avg_value can only
KEEP counts already in the window, so the constraint silently fails and the tier stays wrongly wide.

Real case (gabriela map 2402 R1, session 1683352649635960, 0.2.5-diag): public purple avg 5497.615
-> denominator 13 -> purple count must be 13 (= settlement truth). Pre-fix the engine gave q4 [5,8,11]
(panel "紫件 4/6/9"); the fix unions the full-domain divisor-multiples so the lock reaches 13.

Sample is gitignored (PII), so SKIP when absent (CI) and GATE locally. Asserts BOTH: DISABLE still
leaves the tier wrongly wide (defect is real) and default-ON locks the count onto the truth.
See memory b3456-diag-investigation-0708 (B4).
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

# B4 capture (gabriela 2402 R1, public purple avg 5497.615, truth q4=13). Local-only (gitignored inbox).
SAMPLE = (ROOT / "data" / "samples" / "recordings_inbox_20260708_desktop_diag025" / "captures"
          / "fatbeans_valid_gabriela_2402_1rounds_2402_1683352649635960_0111.json")
TIER, TRUTH, DISABLE_ENV = "q4", 13, "AHMAD_DISABLE_SOFT_AVG_VALUE_FULL_DOMAIN"


def _q4_range(disable: bool) -> tuple[int | None, int | None, int | None]:
    events = parse_fatbeans_capture(str(SAMPLE))
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    snap = _build_snapshot(hero="gabriela", events=events, prefix_batches=pre,
                           map_id=_map_id_from_events(events))
    prepared = prepare_reference_engine_snapshot(snap)
    prev = os.environ.pop(DISABLE_ENV, None)
    try:
        if disable:
            os.environ[DISABLE_ENV] = "1"
        res = run_reference_engine(prepared, max_combos=200_000).as_dict()
    finally:
        os.environ.pop(DISABLE_ENV, None)
        if prev is not None:
            os.environ[DISABLE_ENV] = prev
    trip = (res.get("quality_count_ranges") or {}).get(TIER) or [None, None, None]
    return trip[0], trip[1], trip[2]


def test_b4_soft_avg_value_locks_purple_to_truth() -> None:
    if not SAMPLE.exists():
        pytest.skip(f"sample {SAMPLE.name} absent (gitignored) — local-only gate")

    # settlement truth sanity: purple really is 13.
    events = parse_fatbeans_capture(str(SAMPLE))
    tq = (_settlement_breakdown(events) or {}).get("counts", {}).get(4)
    assert tq == TRUTH, f"settlement purple {tq} != expected {TRUTH}"

    off_lo, off_mid, off_hi = _q4_range(disable=True)
    on_lo, on_mid, on_hi = _q4_range(disable=False)

    # DISABLE: the defect is real — the fractional-mean lock never reaches truth, tier stays wide.
    assert not (off_lo == off_mid == off_hi == TRUTH), (
        f"DISABLE expected wide/wrong q4 (defect real), got locked [{off_lo},{off_mid},{off_hi}]"
    )
    assert off_hi < TRUTH or off_lo > TRUTH or off_lo != off_hi, (
        f"DISABLE q4 [{off_lo},{off_mid},{off_hi}] unexpectedly already brackets-and-locks truth"
    )

    # Default-ON: the divisor lock reaches the truth and pins the count.
    assert on_lo == on_mid == on_hi == TRUTH, (
        f"fix expected q4 locked [{TRUTH}]*3, got [{on_lo},{on_mid},{on_hi}]"
    )
