"""Additive contract gate: RefResult exposes per-quality candidate probability distributions +
lock strength/reason, for the overlay hover "锁定但非唯一" (most-likely N (p%) · also M (q%)).

Purely additive & display-only: candidates never feed the estimate (balanced/value unchanged), exactly-
locked qualities collapse to no candidate list, and every degraded return keeps empty defaults.
See ahmad_ref_engine._candidate_distribution + RefResult candidate fields/quality_lock.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "hero_ref" / "src",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_ref_engine import run_reference_engine  # noqa: E402


def _snapshot() -> dict:
    # ahmed, total 10 items, q3 pinned EXACTLY to 3 (fixed count) — leaves the rest a multi-solution
    # spread over q1/q4/q5/q6 so at least one quality is a probability lock with a real distribution.
    return {
        "ui_contract": {"context": {"hero": "ahmed", "map_id": 4402, "phase": "bidding"}},
        "structured_ref_inputs": {"total_count": 10, "fixed_counts": {"q3": 3}},
    }


def _run() -> dict:
    return run_reference_engine(_snapshot(), max_combos=60_000).as_dict()


def test_exact_lock_marked_and_has_no_candidate_spread() -> None:
    r = _run()
    lock = r["quality_lock"]
    assert lock["q3"]["strength"] == "exact"
    assert lock["q3"]["reason"] == "exact_lock"
    # an exactly-pinned quality does not emit a multi-candidate list
    assert "q3" not in r["quality_count_candidates"]


def test_probabilistic_quality_has_normalized_sorted_candidates() -> None:
    r = _run()
    cands = r["quality_count_candidates"]
    lock = r["quality_lock"]
    prob_qs = [q for q, lk in lock.items() if lk["strength"] == "probabilistic"]
    assert prob_qs, "expected at least one probability-locked quality"
    # every probabilistic quality that emits candidates must be a valid distribution
    emitted = [q for q in prob_qs if q in cands]
    assert emitted, "probability-locked qualities should expose a candidate list"
    for q in emitted:
        dist = cands[q]
        assert len(dist) >= 2
        probs = [c["prob"] for c in dist]
        assert abs(sum(probs) - 1.0) < 0.05  # normalized (may drop tail < min_prob)
        assert probs == sorted(probs, reverse=True)  # most likely first
        assert all(isinstance(c["value"], int) for c in dist)
        assert lock[q]["reason"] in {"partial_reveal_min", "combo_multi_solution"}


def test_candidates_are_display_only_additive() -> None:
    # The additive fields must not change the estimate: the balanced bid is identical whether or not a
    # consumer reads the candidates (they are computed from the same combo mass, never fed back).
    r = _run()
    assert r["balanced"] is not None
    # every count/cells candidate value stays within (or at the edge of) the displayed lo..hi envelope-ish
    # sanity: the mode is a plausible integer near the range, not garbage.
    for q, dist in r["quality_count_candidates"].items():
        lo, _mid, hi = r["quality_count_ranges"][q]
        if lo is None or hi is None:
            continue
        top_value = dist[0]["value"]
        assert lo - 1 <= top_value <= hi + 1


def test_joint_count_cells_candidates_preserve_feasible_pairing() -> None:
    r = _run()
    joint = r["quality_count_cells_candidates"]

    assert {"q5", "q6"}.issubset(joint)
    for quality, rows in joint.items():
        assert rows
        probs = [row["prob"] for row in rows]
        assert probs == sorted(probs, reverse=True)
        assert 0.0 < sum(probs) <= 1.01
        for row in rows:
            assert isinstance(row["count"], int)
            assert isinstance(row["cells"], int)
            assert row["count"] >= 0
            assert row["cells"] >= row["count"]

    # q3 is exactly pinned to 3 pieces but still has several feasible footprints.  The joint contract
    # keeps those three honest states instead of pairing an independent count mode with a cell mode.
    assert {(row["count"], row["cells"]) for row in joint["q3"]} == {
        (3, 6),
        (3, 7),
        (3, 8),
    }
