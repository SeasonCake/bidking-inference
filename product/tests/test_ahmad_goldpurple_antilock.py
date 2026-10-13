"""Design-case hard gates for the gold/purple high-tier anti-false-lock
(`AHMAD_ENABLE_GOLDPURPLE_ANTILOCK`, default OFF).

The engine manufactures a UNIQUE (single-value) purple/gold count out of a count-prior
window that excludes the truth, then a soft-feasibility filter collapses the survivors to
one value — a false PRECISION lock (min==mid==max) that is also wrong. Two collapse sites,
two interventions (both env-gated, fire only on single-value collapse):

  * Intervention B (avg_value divisor path): ahmed 2501 purple, wire avg_value 7281.7 admits
    only divisor-multiples {7,14,21,28}; the window [8..19] intersects to {14}; truth 7 sits
    one feasibility-step below the floor. Fix re-admits 7 -> range brackets the truth.
  * Intervention A (avg_cells integer-grid path): ahmed 2505 purple, avg_cells 2.167 admits
    {6,12,18,24}; the sparse-exact tightened window [12..17] intersects to {12}; truth 18 sits
    one step above the tight upper. Fix re-admits 18 on the HIGH side.

These replay real capture samples (gitignored, PII) so the test SKIPS when the sample is
absent (CI) and GATES locally. Each case asserts BOTH: OFF still collapses to the wrong single
value (the test is meaningful / the defect is real) and ON breaks the lock toward truth.

See docs/handoffs/PLAN_red_count_tuning_next_2026-07-02 §十 and memory
ahmed-2506-false-count-lock-twolayer.
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
from audit_aisha_gap import (  # noqa: E402
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402

FATBEANS = ROOT / "data" / "samples" / "fatbeans"

# (file, round, tier, truth_count, off_lock_value) — real values verified 2026-07-02.
CASES = [
    # Intervention B (avg_value): purple truth 7, OFF locks [14,14,14].
    ("fatbeans_valid_ahmed_2501_1rounds_2501_1425860811684177_0576.json", 3, "q4", 7, 14),
    # Intervention A (avg_cells): purple truth 18, OFF locks [12,12,12].
    ("fatbeans_valid_ahmed_2505_3rounds_2505_1425860813206158_0110.json", 1, "q4", 18, 12),
]


def _range(fname: str, rnd: int, tier: str, antilock: bool) -> tuple[int | None, int | None, int | None]:
    events = parse_fatbeans_capture(str(FATBEANS / fname))
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    rnd = min(rnd, len(pre))
    prefix = pre[:rnd]
    sid = _prefix_sort_id(prefix)
    pev = _events_through_sort(events, sid) if sid else events
    snap = _build_snapshot(hero="ahmed", events=pev, prefix_batches=prefix, map_id=_map_id_from_events(events))
    snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = rnd
    prepared = prepare_reference_engine_snapshot(snap)
    budget = hero_max_combos_for_round("ahmed", rnd)
    prev = os.environ.pop("AHMAD_ENABLE_GOLDPURPLE_ANTILOCK", None)
    narrow_kill = "BIDKING_DISABLE_AHMED_PUBLIC_Q4_AVG_ANTILOCK"
    prev_narrow_kill = os.environ.get(narrow_kill)
    # This file isolates the older global experiment.  The 0.2.9 default-on
    # Ahmed/public-q4 repair has its own positive/rollback gates in
    # test_ahmed_skill_avg_persistence_hotfix1.py and must not pre-fix the OFF arm here.
    os.environ[narrow_kill] = "1"
    # NOTE: the B4 soft-avg-value full-domain widen is scoped to DRAW heroes (SOFT_AVG_VALUE_WIDEN_HEROES,
    # excludes ahmed), so it has ZERO effect on these ahmed cases — this antilock test measures the antilock
    # feature independently of B4. (An earlier unscoped B4 draft did break ahmed 2501's [14] lock; the
    # shipped scoped B4 does not — see the correction in memory audit-0708-b3b4b6-fixes-verified.)
    try:
        if antilock:
            os.environ["AHMAD_ENABLE_GOLDPURPLE_ANTILOCK"] = "1"
        res = run_reference_engine(prepared, max_combos=budget).as_dict()
    finally:
        os.environ.pop("AHMAD_ENABLE_GOLDPURPLE_ANTILOCK", None)
        if prev is not None:
            os.environ["AHMAD_ENABLE_GOLDPURPLE_ANTILOCK"] = prev
        if prev_narrow_kill is None:
            os.environ.pop(narrow_kill, None)
        else:
            os.environ[narrow_kill] = prev_narrow_kill
    trip = (res.get("quality_count_ranges") or {}).get(tier) or [None, None, None]
    return trip[0], trip[1], trip[2]


@pytest.mark.parametrize("fname,rnd,tier,truth,off_lock", CASES)
def test_goldpurple_antilock_breaks_false_lock(
    fname: str, rnd: int, tier: str, truth: int, off_lock: int
) -> None:
    sample = FATBEANS / fname
    if not sample.exists():
        pytest.skip(f"sample {fname} absent (gitignored) — local-only gate")

    # settlement truth sanity: the tier's true count is what we claim.
    events = parse_fatbeans_capture(str(sample))
    counts = (_settlement_breakdown(events) or {}).get("counts", {})
    tq = counts.get(int(tier[1:]), counts.get(tier[1:], 0))
    assert tq == truth, f"{fname} {tier} settlement truth {tq} != expected {truth}"

    off_lo, off_mid, off_hi = _range(fname, rnd, tier, antilock=False)
    on_lo, on_mid, on_hi = _range(fname, rnd, tier, antilock=True)

    # OFF: the defect is real — a wrong single-value lock.
    assert off_lo == off_mid == off_hi == off_lock, (
        f"{fname} {tier} R{rnd} OFF expected false-lock [{off_lock}]*3, got [{off_lo},{off_mid},{off_hi}]"
    )

    # ON: lock broken (spread) AND the truth is now bracketed by the range.
    assert not (on_lo == on_mid == on_hi), (
        f"{fname} {tier} R{rnd} ON still collapsed to single value {on_lo}"
    )
    assert on_lo <= truth <= on_hi, (
        f"{fname} {tier} R{rnd} ON range [{on_lo},{on_hi}] does not bracket truth {truth}"
    )
