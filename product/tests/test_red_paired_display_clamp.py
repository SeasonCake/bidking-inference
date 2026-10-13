"""Red triplets always follow conservative/balanced/aggressive policy order.

Gold/red joint correlation remains available in structured candidates and hover detail.  The main
card must not reorder a red arm to mirror the gold axis, even for historical payloads or stale
rollback environment variables.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "hero_ref" / "tools", ROOT / "hero_ref" / "src", ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_live_panel_server import _paired_complement_range, _red_display_ranges  # noqa: E402


def _ref(total_count, locked, gold_range, red_count, red_cells):
    ranges = {k: [v, v, v] for k, v in locked.items()}
    ranges["q5"] = list(gold_range)
    ranges["q6"] = list(red_count)
    return {
        "quality_count_ranges": ranges,
        "red_count_range": list(red_count),
        "red_cells_range": list(red_cells),
        "evidence": {"total_count": total_count, "count_sums": {}},
    }


def test_5610_transient_red_range_keeps_policy_order(monkeypatch):
    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CLAMP", raising=False)
    # 5610 live: total 39, q1=10 q3=10 q4=11 locked -> q5q6 total 8; gold [2,2,4]; engine red
    # transiently [3,6,6].  The policy view keeps the engine triplet unchanged; joint correlation
    # and any structural warning are separate from the price-card axis.
    ref = _ref(39, {"q1": 10, "q3": 10, "q4": 11}, [2, 2, 4], [3, 6, 6], [11, 18, 18])
    counts, _cells = _red_display_ranges(ref)
    assert counts == [3, 6, 6], counts


def test_coherent_range_also_keeps_policy_order(monkeypatch):
    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CLAMP", raising=False)
    # HEAD replay values: engine red [4,6,6] remains in policy order.
    ref = _ref(39, {"q1": 10, "q3": 10, "q4": 11}, [2, 2, 4], [4, 6, 6], [11, 18, 18])
    counts, _cells = _red_display_ranges(ref)
    assert counts == [4, 6, 6], counts


def test_5607_fully_locked_pair_stays_locked(monkeypatch):
    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CLAMP", raising=False)
    # 5607: gold locked 3/3/3, total q5q6 = 8 -> red 5/5/5 (no illegal member possible).
    ref = _ref(36, {"q1": 13, "q3": 9, "q4": 6}, [3, 3, 3], [5, 5, 5], [8, 14, 14])
    counts, _cells = _red_display_ranges(ref)
    assert counts == [5, 5, 5], counts


def test_no_total_no_pairing_unchanged(monkeypatch):
    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CLAMP", raising=False)
    # q4 NOT locked -> q5q6 total unknown -> raw engine range passes through untouched.
    ref = _ref(39, {"q1": 10, "q3": 10}, [2, 2, 4], [3, 6, 6], [11, 18, 18])
    ref["quality_count_ranges"]["q4"] = [9, 11, 12]
    counts, cells = _red_display_ranges(ref)
    assert counts == [3, 6, 6] and cells == [11, 18, 18]


def test_negative_pair_bails_out():
    # total smaller than a gold candidate -> impossible pair -> no pairing (never negative display)
    assert _paired_complement_range(2, [2, 2, 4], [0, 0, 0]) is None


def test_historical_pairing_env_cannot_change_policy_order(monkeypatch):
    monkeypatch.setenv("BIDKING_DISABLE_RED_PAIRED_CLAMP", "1")
    ref = _ref(39, {"q1": 10, "q3": 10, "q4": 11}, [2, 2, 4], [3, 6, 6], [11, 18, 18])
    counts, _cells = _red_display_ranges(ref)
    assert counts == [3, 6, 6], counts
