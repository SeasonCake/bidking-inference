"""BUG-G gate: 均格倍数假锁 (群友加菲 aisha 5606 R4, 2026-07-11).

金均格 7.2 (=36/5) makes the gold count a multiple of 5; the multinomial prior window (probs*total
±1.6σ → [2,8]) intersected that hard constraint down to the SINGLE value 5 → zero-width "lock"
金 5件/36格 while the truth was 10件/72格. The 12 residual items were then all shoved into red
([7,7,7] vs truth 2) and 参考 read 1,746,636 vs the 836,304 truth (+109%).

Three parts, all needed (ablation in the commit):
  1. false-lock widen (_prior_count_values): a SOFT prior window must never delete a physically
     reachable arithmetic composition — when the intersection collapses to ≤1 candidate, restore
     every avg-feasible multiple that fits the physical ceiling. Kill:
     BIDKING_DISABLE_AVG_MULTIPLE_FALSE_LOCK_WIDEN=1
  2. grid-target multiple pick (_apply_total_grid_target_from_known_high_tier_cells): the derived
     HARD total-grid target must not be built on ONE assumed multiple (tg 133 pruned the true
     10-gold composition). With q5 value_sum present, pick the multiple by unit-value plausibility
     (Q5_SIZE_VALUE_POINTS); without it, fall back to a soft floor. Kill:
     AISHA_DISABLE_Q5_AVG_MULTIPLE_GUARD=1
  3. combo value-plausibility bias (_q5_value_sum_count_multiple_bias_log): 552,983/10 = 55.3k/件
     sits dead-center for cpi 7.2 while /5 = 110.6k is a 2× tail. Kill:
     BIDKING_DISABLE_Q5_VALUE_COUNT_BIAS=1
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    Q5_EXACT_VALUE_CELLS_DISABLE,
    Q5_VALUE_COUNT_BIAS_NOTE,
    _physical_count_ceiling,
    _q5_value_sum_count_multiple_bias_log,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)

FIXTURE = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag5606_aisha"
WIDEN_KILL = "BIDKING_DISABLE_AVG_MULTIPLE_FALSE_LOCK_WIDEN"
GRID_KILL = "AISHA_DISABLE_Q5_AVG_MULTIPLE_GUARD"
BIAS_KILL = "BIDKING_DISABLE_Q5_VALUE_COUNT_BIAS"
TRUTH_VALUE = 836_304  # r05_settled known_value_sum


def _evidence(**over):
    base = dict(
        hero="aisha",
        fixed_counts={"q1": 11, "q3": 8, "q4": 10},
        min_counts={"q5": 2},
        avg_cells={"q5": 7.2},
        quality_values={"q5": 552983.0},
        quality_cells={},
        quality_cell_floors={},
        quality_value_floors={},
        soft_avg_cell_keys=frozenset(),
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.skipif(
    not (FIXTURE / "r04_bidding.json").exists(),
    reason="5606 diag snapshot absent (gitignored) — local-only gate",
)
def test_physical_ceiling_is_arithmetic_not_belief() -> None:
    # 5606 R4: 41 items, 29 committed to q1/q3/q4 -> gold can physically hold up to 12 (truth 10),
    # while the multinomial prior window only reached 8 — that gap is what deleted the true multiple.
    from ahmad_ref_engine import extract_evidence

    snap = prepare_reference_engine_snapshot(
        json.loads((FIXTURE / "r04_bidding.json").read_text(encoding="utf-8"))
    )
    ev = extract_evidence(snap)
    assert _physical_count_ceiling(41, "q5", ev) == 12


def test_value_bias_prefers_the_plausible_multiple() -> None:
    ev = _evidence()
    # 552,983 over 10 golds = 55.3k/件 (dead center for cpi 7.2); over 5 = 110.6k (2x tail)
    assert _q5_value_sum_count_multiple_bias_log({"q5": 10}, ev) > _q5_value_sum_count_multiple_bias_log(
        {"q5": 5}, ev
    )
    assert math.isclose(_q5_value_sum_count_multiple_bias_log({"q5": 10}, ev), 0.0, abs_tol=0.35)


def test_value_bias_gates_are_noops() -> None:
    # count locked / no value evidence / soft (public) avg -> structurally silent
    assert _q5_value_sum_count_multiple_bias_log({"q5": 5}, _evidence(fixed_counts={"q5": 5})) == 0.0
    assert _q5_value_sum_count_multiple_bias_log({"q5": 5}, _evidence(quality_values={})) == 0.0
    assert (
        _q5_value_sum_count_multiple_bias_log({"q5": 5}, _evidence(soft_avg_cell_keys=frozenset({"q5"})))
        == 0.0
    )


def _run(monkeypatch, **kills) -> dict:
    for env in (WIDEN_KILL, GRID_KILL, BIAS_KILL, Q5_EXACT_VALUE_CELLS_DISABLE):
        monkeypatch.delenv(env, raising=False)
    # This file measures the historical BUG-G three-lever ablation. U07's later exact catalog
    # solver independently resolves 5606 and is covered in test_bug_n_unique_count_two_signals.py;
    # disable it here so it cannot mask whether each original BUG-G lever remains load-bearing.
    monkeypatch.setenv(Q5_EXACT_VALUE_CELLS_DISABLE, "1")
    for env, value in kills.items():
        monkeypatch.setenv(env, value)
    snap = json.loads((FIXTURE / "r04_bidding.json").read_text(encoding="utf-8"))
    return run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()


@pytest.mark.skipif(
    not (FIXTURE / "r04_bidding.json").exists(),
    reason="5606 diag snapshot absent (gitignored) — local-only gate",
)
def test_5606_replay_recovers_truth_composition(monkeypatch) -> None:
    off = _run(monkeypatch, **{WIDEN_KILL: "1", GRID_KILL: "1", BIAS_KILL: "1"})
    # OFF reproduces the shipped defect byte-for-byte (0.2.6-hotfix1 screenshot: 参考 1,746,636)
    assert off["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert off["quality_count_ranges"]["q6"] == [7, 7, 7]
    assert off["balanced"] == 1_746_636

    on = _run(monkeypatch)
    # truth: 金 10件/72格, 红 2件, 总格 148, 真值 836,304
    assert on["quality_count_ranges"]["q5"] == [10, 10, 10]
    assert on["quality_cells_ranges"]["q5"] == [72, 72, 72]
    assert on["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert on["evidence"]["total_grid_target"] == 151
    assert on["total_grid_range"] == [151, 151, 151]
    assert any(str(n).startswith("total_grid_q5_multiple_value_pick:10") for n in on["notes"])
    assert Q5_VALUE_COUNT_BIAS_NOTE in [str(n) for n in on["notes"]]
    # +109% over-estimate -> within ±20%
    assert abs(on["balanced"] - TRUTH_VALUE) / TRUTH_VALUE < 0.20
    assert abs(on["balanced"] - TRUTH_VALUE) < abs(off["balanced"] - TRUTH_VALUE)


@pytest.mark.skipif(
    not (FIXTURE / "r04_bidding.json").exists(),
    reason="5606 diag snapshot absent (gitignored) — local-only gate",
)
def test_5606_ablation_widen_and_grid_pick_are_load_bearing(monkeypatch) -> None:
    # Measured ablation: the widen alone cannot help (the hard tg 133, built on the assumed "5 golds",
    # still prunes the 10-gold composition) and the grid pick alone cannot help (10 was never a
    # candidate). Either kill switch restores the shipped false lock.
    for kills in ({WIDEN_KILL: "1"}, {GRID_KILL: "1"}):
        res = _run(monkeypatch, **kills)
        assert res["quality_count_ranges"]["q5"] == [5, 5, 5], kills

    # The combo value bias is corroborating on THIS (aisha) sample — the grid pick already steers it —
    # but it is the ONLY value-side disambiguator on heroes the aisha-scoped grid path never runs for,
    # so it stays. Killing it must not regress the recovered composition.
    res = _run(monkeypatch, **{BIAS_KILL: "1"})
    assert res["quality_count_ranges"]["q5"] == [10, 10, 10]
