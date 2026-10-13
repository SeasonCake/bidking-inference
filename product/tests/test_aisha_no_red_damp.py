"""Gate the aisha no-red count damp (LEVER1) + coupled masking-off + safety lift (2026-06-27).

Root cause: the multinomial count prior deposits ~probs.q6*total reds and ignores accumulated non-red
reveals; aisha pure-prior red was +100% over (§5-confirmed: 16 vaults with k=12-40 non-red seen yet 4-6
red bet / true 0-3). The fix is ONE switch (AISHA_ENABLE_NO_RED_DAMP=1):
  - extends the proven no-red count damp to aisha (strength 0.7) -> red count +100% -> ~0;
  - couples the now-redundant phantom masking OFF when the damp is actively correcting
    (_aisha_no_red_damp_active gates the pure-prior percentile clamp + D1 red value cap), but KEEPS it
    on at early rounds / low-k where the damp can't fire;
  - lifts aisha balanced safety 0.85 -> 0.90 to re-center balanced into the blessed -2~-5% band.
Measured (68 NEW samples): overall balanced -13% -> -2%, pure-prior -21% -> -6%, revealed -5% -> ~0,
red count honest. Default OFF (this gate pins the predicate + constants, not the live default).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as E  # noqa: E402


def _ev(hero="aisha", min_counts=None, fixed_counts=None, quality_cell_floors=None):
    return E.RefEvidence(
        hero=hero, map_id=2501, phase=None, total_count=None,
        fixed_counts=fixed_counts or {}, min_counts=min_counts or {},
        count_sums={}, avg_cells={}, quality_cells={}, quality_cell_floors=quality_cell_floors or {},
        avg_values={}, quality_values={}, quality_value_floors={}, quality_value_floor_item_counts={},
        split_counts={}, split_quality_cells={}, split_avg_cells={}, random_value_floors={},
        total_grid_target=None, soft_avg_cell_keys=frozenset(), soft_avg_value_keys=frozenset(),
        v3_conservative=None, v3_balanced=None, v3_aggressive=None, source_notes=(),
    )


def test_constants_pinned() -> None:
    assert E.AISHA_NO_RED_DAMP_STRENGTH == 0.7
    assert E.AISHA_NO_RED_DAMP_SAFETY == 0.90


def test_damp_active_when_enabled_pure_prior_high_k_late_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    ev = _ev(min_counts={"q1": 10, "q3": 8})  # k=18 non-red, no q6
    assert E._aisha_no_red_damp_active(ev, round_no=4) is True


def test_damp_active_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default is now ON (author flipped 2026-06-27): unset env -> the damp is active for aisha."""
    monkeypatch.delenv("AISHA_ENABLE_NO_RED_DAMP", raising=False)
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    assert E._aisha_no_red_damp_active(ev, round_no=4) is True


def test_damp_disabled_by_env_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "0")
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    assert E._aisha_no_red_damp_active(ev, round_no=4) is False


def test_damp_inactive_non_aisha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    assert E._aisha_no_red_damp_active(_ev(hero="ahmed", min_counts={"q1": 10, "q3": 8}), 4) is False


def test_damp_inactive_when_red_revealed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    # red count revealed -> not a no-red vault
    assert E._aisha_no_red_damp_active(_ev(min_counts={"q1": 10, "q3": 8, "q6": 1}), 4) is False
    # red CELLS revealed (cell floor) -> also off
    assert E._aisha_no_red_damp_active(
        _ev(min_counts={"q1": 10, "q3": 8}, quality_cell_floors={"q6": 4.0}), 4) is False


def test_damp_inactive_low_k(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    assert E._aisha_no_red_damp_active(_ev(min_counts={"q1": 2}), 4) is False  # k=2 < 3


def test_damp_inactive_early_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    assert E._aisha_no_red_damp_active(ev, round_no=1) is False  # ramp 0 at R1 -> masking stays on


def test_damp_inactive_when_q6_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    ev = _ev(min_counts={"q1": 10, "q3": 8}, fixed_counts={"q6": 2})
    assert E._aisha_no_red_damp_active(ev, round_no=4) is False


# --- LEVER1 map-aware ratescale ramp (2026-06-27): scale press strength by the map q6 rate ---
# factor = clamp((rate-LO)/(HI-LO), 0, 1). Real aisha NEW (settled): high-red 56xx (rate≈0.10≥HI) keeps
# the FULL legacy press (its prior over-deposits red -> press lands red median 0); low-red 25xx
# (rate≈0.074<LO) -> factor 0 -> no press (its prior ≈ truth, legacy over-pressed it to -25%).
# DEFAULT OFF (pre-release compromise — honest red count unmasks a downstream balanced over-estimate);
# opt-in AISHA_NO_RED_DAMP_MAP_AWARE=1 ramp / =excess press-above-mode / =0/unset legacy flat full-press.

def _damp(counts, ev, total, round_no=4, probs=None):
    return E._draw_hero_no_red_count_damp_log(counts, ev, total, round_no, probs)


def test_mapaware_default_off_is_legacy_full_press(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEFAULT (unset) -> legacy flat full-press for aisha too (ramp is opt-in, pre-release compromise).
    A low-rate map that the ramp would spare is still fully pressed when the flag is unset."""
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    monkeypatch.delenv("AISHA_NO_RED_DAMP_MAP_AWARE", raising=False)  # default
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    probs = {"q6": E.AISHA_NO_RED_DAMP_RATE_LO - 0.02}  # low rate (ramp would zero it)
    assert _damp({"q6": 2}, ev, 40, probs=probs) < 0.0  # default = full press regardless of rate


def test_ratescale_low_rate_no_press(monkeypatch: pytest.MonkeyPatch) -> None:
    """ramp opt-in, map q6 rate BELOW LO -> factor 0 -> NO press at any red (genuine low-map red spared)."""
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "1")  # opt into ramp
    ev = _ev(min_counts={"q1": 10, "q3": 8})  # k=18 non-red
    lo = E.AISHA_NO_RED_DAMP_RATE_LO
    probs = {"q6": lo - 0.005}  # below LO
    assert _damp({"q6": 2}, ev, 40, probs=probs) == 0.0
    assert _damp({"q6": 6}, ev, 40, probs=probs) == 0.0  # even high red -> no press


def test_ratescale_high_rate_full_press(monkeypatch: pytest.MonkeyPatch) -> None:
    """ramp opt-in, map q6 rate AT/ABOVE HI -> factor 1 -> identical to legacy full-press (56xx kept)."""
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    probs = {"q6": E.AISHA_NO_RED_DAMP_RATE_HI + 0.01}
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "1")
    ramp = _damp({"q6": 3}, ev, 40, probs=probs)
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "0")
    legacy = _damp({"q6": 3}, ev, 40, probs=probs)
    assert ramp < 0.0 and ramp == legacy  # high rate -> full legacy press


def test_ratescale_mid_rate_partial_press(monkeypatch: pytest.MonkeyPatch) -> None:
    """aisha, map q6 rate BETWEEN LO and HI -> partial press (between 0 and legacy full-press)."""
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    mid = (E.AISHA_NO_RED_DAMP_RATE_LO + E.AISHA_NO_RED_DAMP_RATE_HI) / 2
    probs = {"q6": mid}
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "1")
    ramp = _damp({"q6": 3}, ev, 40, probs=probs)
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "0")
    legacy = _damp({"q6": 3}, ev, 40, probs=probs)
    assert legacy < ramp < 0.0  # partial: stronger than 0, weaker than full press


def test_excess_mode_presses_only_above_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """AISHA_NO_RED_DAMP_MAP_AWARE=excess -> press only red above prior mode (rate*total)."""
    monkeypatch.setenv("AISHA_ENABLE_NO_RED_DAMP", "1")
    monkeypatch.setenv("AISHA_NO_RED_DAMP_MAP_AWARE", "excess")
    ev = _ev(min_counts={"q1": 10, "q3": 8})
    probs = {"q6": 0.05}  # expected_red = 0.05*40 = 2.0
    assert _damp({"q6": 2}, ev, 40, probs=probs) == 0.0  # red == mode -> no press
    assert _damp({"q6": 6}, ev, 40, probs=probs) < 0.0   # red >> mode -> press excess


def test_mapaware_draw_hero_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draw heroes are NOT map-aware by default (their design target is red->0 on no-red vaults,
    e.g. 2403): map-aware default for draw is OFF -> legacy full-press, identical with/without probs."""
    monkeypatch.delenv("AISHA_NO_RED_DAMP_MAP_AWARE", raising=False)
    ev = _ev(hero="gabriela", min_counts={"q1": 10, "q3": 8})
    probs = {"q6": 0.05}  # low rate: if draw were ratescale this would zero the press
    with_probs = _damp({"q6": 2}, ev, 40, probs=probs)
    no_probs = _damp({"q6": 2}, ev, 40, probs=None)
    assert with_probs < 0.0 and with_probs == no_probs  # full-press, probs ignored for draw
