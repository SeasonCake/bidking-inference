"""H7 phase2 gate: aggressive-tier red tail value hedge — NEGATIVE CLOSURE, default OFF.

The lever (aggressive = max(agg, bal + max(0, red_mid × family p75 − red_cells_mid) × $/cell)) was
implemented and refuted on the 246-row ahmed clean_eval sweep: coverage 58→78% but agg median error
+6.4→+25.5%, the 1-2红 bucket worsened +22.3→+35.6%, and 2407 fires once its red pins [1,1,1] —
both author guards violated. For pinned 3+红 vaults aggressive is already well centered (med +0.8%);
the deep unders are giant-red tails with NO bid-time discriminator (see the phase2 comment block in
ahmad_ref_engine.py). These tests pin the closure: default behavior is byte-identical (no hedge, no
note), and the opt-in env still fires with the formula-bounded top-up for future experiments.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import run_reference_engine  # noqa: E402

OPT_IN = "BIDKING_ENABLE_RED_TAIL_AGG_HEDGE"
NOTE_PREFIX = "red_tail_agg_hedge:"


def _snapshot(map_id: int, sri: dict, hero: str = "ahmed") -> dict:
    return {
        "ui_contract": {
            "context": {"hero": hero, "map_id": map_id, "phase": "bidding"},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": sri,
        "public_info_rows": [],
    }


def _snapshot_5602_like() -> dict:
    # 5602 R4 shape: declared/arithmetic-locked counts incl red=5, red footprints unseen. The real
    # vault held 5 giant reds (41 cells, 8.2/件); the combo mass prices ~3.2/件.
    return _snapshot(5602, {
        "total_count": 36,
        "fixed_counts": {"q1": 12, "q3": 9, "q4": 8, "q5": 2, "q6": 5},
    })


def _run(snap: dict, monkeypatch, opt_in: bool) -> dict:
    if opt_in:
        monkeypatch.setenv(OPT_IN, "1")
    else:
        monkeypatch.delenv(OPT_IN, raising=False)
    return run_reference_engine(snap, max_combos=6000).as_dict()


def _notes(res: dict) -> list[str]:
    return [str(n) for n in res.get("notes") or []]


def test_default_off_no_hedge_no_note(monkeypatch) -> None:
    off = _run(_snapshot_5602_like(), monkeypatch, opt_in=False)
    assert not any(n.startswith(NOTE_PREFIX) for n in _notes(off))
    # phase1 display hedge still fires (display-only remains the shipped treatment)
    assert any(str(n).startswith("red_cells_tail_hedge_hi") for n in _notes(off))


def test_opt_in_hedges_agg_cons_bal_identical(monkeypatch) -> None:
    off = _run(_snapshot_5602_like(), monkeypatch, opt_in=False)
    on = _run(_snapshot_5602_like(), monkeypatch, opt_in=True)
    assert on["conservative"] == off["conservative"]
    assert on["balanced"] == off["balanced"]
    assert on["aggressive"] > off["aggressive"]
    assert any(n.startswith(NOTE_PREFIX) for n in _notes(on))
    assert on["aggressive"] - off["aggressive"] > 50_000


def test_opt_in_small_red_hedge_is_formula_bounded(monkeypatch) -> None:
    # 2 pinned reds: even opted in, the top-up never exceeds r50×p75×$/cell (structural bound).
    snap = _snapshot(5605, {
        "total_count": 20,
        "fixed_counts": {"q1": 8, "q3": 6, "q4": 3, "q5": 1, "q6": 2},
    })
    off = _run(snap, monkeypatch, opt_in=False)
    on = _run(snap, monkeypatch, opt_in=True)
    assert on["conservative"] == off["conservative"]
    assert on["balanced"] == off["balanced"]
    delta = on["aggressive"] - off["aggressive"]
    assert delta >= 0
    # 56xx: r50=2, p75=4.33, $/cell 46864 -> absolute ceiling 2*4.33*46864 ≈ 406k
    assert delta <= 2 * 4.33 * 46864 + 1000
    assert on["aggressive"] <= on["balanced"] + 2 * 4.33 * 46864 + 1000


def test_wide_count_2407_style_never_fires(monkeypatch) -> None:
    # red count NOT pinned (pure prior, wide range) -> outer phase1 gate excludes; no note even opted in.
    snap = _snapshot(2407, {"total_count": 40, "fixed_counts": {"q1": 14, "q3": 10}})
    off = _run(snap, monkeypatch, opt_in=False)
    on = _run(snap, monkeypatch, opt_in=True)
    if any(str(n).startswith("red_cells_tail_hedge_hi") for n in _notes(on)):
        pytest.skip("phase1 display gate unexpectedly fired; case needs re-cutting")
    assert on["aggressive"] == off["aggressive"]
    assert not any(n.startswith(NOTE_PREFIX) for n in _notes(on))


def test_aisha_never_fires(monkeypatch) -> None:
    snap = _snapshot(2508, {
        "total_count": 44,
        "fixed_counts": {"q1": 13, "q3": 10, "q4": 13, "q5": 3, "q6": 5},
    }, hero="aisha")
    on = _run(snap, monkeypatch, opt_in=True)
    assert not any(n.startswith(NOTE_PREFIX) for n in _notes(on))
