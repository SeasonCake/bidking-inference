"""Regression for shapeless random-red reveals collapsing draw-hero quotes.

宝光四鉴/双鉴 and matching public rows reveal the quality of random items but not their shape.
The draw-red count anchor must not interpret one such red as an almost-complete enumeration of the
vault's red population.  A real red footprint keeps the original tight anchor.
"""

from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "hero_ref" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ahmad_ref_engine import (  # noqa: E402
    DRAW_HERO_DISABLE_QUALITY_ONLY_RED_RELAX,
    DRAW_HERO_QUALITY_ONLY_RED_RELAX_NOTE,
    _draw_hero_quality_only_red_relax_note,
    _draw_hero_revealed_red_count_anchor_log,
    _draw_hero_revealed_red_count_sigma,
    run_reference_engine,
)

DIAG_4542 = Path(
    r"C:\Users\example\Desktop\new_recordings\HeroRefDiag-20260717-182941-4542_9000000000000021"
)
DIAG_2401_SHAPED_RED = Path(
    r"C:\Users\example\Desktop\new_recordings\HeroRefDiag-20260701-191530-2401_1654627627537602"
)
DIAG_2546 = Path(
    r"C:\Users\example\Desktop\bidking-live-v0.2.8-hotfix1-encrypted"
    r"\data\logs\live\exports\HeroRefDiag-20260718-131342-2546_1741282938755618.zip"
)


def _evidence(
    *,
    hero: str = "gabriela",
    q6_floor: float = 0.0,
    fixed_q6: bool = False,
    revealed: int = 1,
):
    return SimpleNamespace(
        hero=hero,
        fixed_counts={"q6": 1} if fixed_q6 else {},
        min_counts={"q6": revealed} if revealed else {},
        quality_cell_floors={"q6": q6_floor} if q6_floor else {},
    )


def test_quality_only_random_red_uses_loose_anchor_and_has_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence()

    sigma, relaxed = _draw_hero_revealed_red_count_sigma(evidence)
    assert (sigma, relaxed) == (4.0, True)
    assert _draw_hero_quality_only_red_relax_note(evidence) == (
        f"{DRAW_HERO_QUALITY_ONLY_RED_RELAX_NOTE}:4"
    )
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, evidence) == pytest.approx(
        -0.5 * (3.0 / 4.0) ** 2
    )

    monkeypatch.setenv(DRAW_HERO_DISABLE_QUALITY_ONLY_RED_RELAX, "1")
    sigma, relaxed = _draw_hero_revealed_red_count_sigma(evidence)
    assert (sigma, relaxed) == (1.0, False)
    assert _draw_hero_quality_only_red_relax_note(evidence) is None
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, evidence) == pytest.approx(-4.5)


def test_relax_note_uses_the_same_active_gate_as_the_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        _evidence(revealed=0),
        _evidence(fixed_q6=True),
    ]
    for evidence in cases:
        assert _draw_hero_quality_only_red_relax_note(evidence) is None
        assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, evidence) == 0.0

    globally_disabled = _evidence()
    monkeypatch.setenv("DRAW_HERO_DISABLE_REVEALED_RED_COUNT", "1")
    assert _draw_hero_quality_only_red_relax_note(globally_disabled) is None
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, globally_disabled) == 0.0
    monkeypatch.delenv("DRAW_HERO_DISABLE_REVEALED_RED_COUNT", raising=False)

    invalid_sigma = _evidence()
    monkeypatch.setenv("DRAW_HERO_REVEALED_RED_COUNT_SIGMA", "0")
    monkeypatch.setenv("DRAW_HERO_QUALITY_ONLY_RED_COUNT_SIGMA", "0")
    assert _draw_hero_quality_only_red_relax_note(invalid_sigma) is None
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, invalid_sigma) == 0.0


def test_real_red_footprint_and_non_draw_hero_keep_legacy_anchor() -> None:
    shaped = _evidence(q6_floor=1.0)
    assert _draw_hero_revealed_red_count_sigma(shaped) == (1.0, False)
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, shaped) == pytest.approx(-4.5)

    non_draw = _evidence(hero="aisha")
    assert _draw_hero_revealed_red_count_sigma(non_draw) == (1.0, False)
    assert _draw_hero_revealed_red_count_anchor_log({"q6": 4}, non_draw) == 0.0


def _run(snapshot: dict, *, rollback: bool, monkeypatch: pytest.MonkeyPatch) -> dict:
    if rollback:
        monkeypatch.setenv(DRAW_HERO_DISABLE_QUALITY_ONLY_RED_RELAX, "1")
    else:
        monkeypatch.delenv(DRAW_HERO_DISABLE_QUALITY_ONLY_RED_RELAX, raising=False)
    return run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000).as_dict()


def test_2546_baoguang_red_no_longer_crashes_the_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not DIAG_2546.is_file():
        pytest.skip("faithful 2546 diagnostic ZIP is local-only")
    with zipfile.ZipFile(DIAG_2546) as archive:
        reading = json.loads(
            archive.read("round_snapshots/r01_reading.json").decode("utf-8-sig")
        )
        bidding = json.loads(
            archive.read("round_snapshots/r01_bidding.json").decode("utf-8-sig")
        )

    before = _run(reading, rollback=False, monkeypatch=monkeypatch)
    fixed = _run(bidding, rollback=False, monkeypatch=monkeypatch)
    legacy = _run(bidding, rollback=True, monkeypatch=monkeypatch)

    assert before["balanced"] == 837_119
    assert legacy["balanced"] == 547_081
    assert legacy["red_count_range"] == [1, 2, 3]
    assert fixed["balanced"] == 834_589
    assert fixed["red_count_range"] == [2, 4, 6]
    assert abs(fixed["balanced"] - before["balanced"]) / before["balanced"] < 0.01
    assert f"{DRAW_HERO_QUALITY_ONLY_RED_RELAX_NOTE}:4" in fixed["notes"]
    assert not any(
        str(note).startswith(DRAW_HERO_QUALITY_ONLY_RED_RELAX_NOTE)
        for note in legacy["notes"]
    )


def test_4542_relaxation_stays_near_prior_and_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rounds = DIAG_4542 / "round_snapshots"
    if not (rounds / "r04_settled.json").is_file():
        pytest.skip("faithful 4542 diagnostic directory is local-only")
    before = json.loads((rounds / "r01_bidding.json").read_text(encoding="utf-8-sig"))
    after = json.loads((rounds / "r02_bidding.json").read_text(encoding="utf-8-sig"))
    settled = json.loads((rounds / "r04_settled.json").read_text(encoding="utf-8-sig"))
    truth = int(settled["ui_contract"]["truth"]["total_value"])

    prior = _run(before, rollback=False, monkeypatch=monkeypatch)
    fixed = _run(after, rollback=False, monkeypatch=monkeypatch)
    legacy = _run(after, rollback=True, monkeypatch=monkeypatch)

    assert prior["balanced"] == 688_202
    assert legacy["balanced"] == 486_425
    assert fixed["balanced"] == 690_887
    assert abs(fixed["balanced"] - prior["balanced"]) / prior["balanced"] < 0.01
    assert abs(fixed["balanced"] - truth) < abs(legacy["balanced"] - truth)


def test_shaped_red_full_engine_is_identical_across_new_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = DIAG_2401_SHAPED_RED / "round_snapshots" / "r02_bidding.json"
    if not path.is_file():
        pytest.skip("faithful 2401 shaped-red diagnostic is local-only")
    snapshot = json.loads(path.read_text(encoding="utf-8-sig"))

    fixed = _run(snapshot, rollback=False, monkeypatch=monkeypatch)
    rollback = _run(snapshot, rollback=True, monkeypatch=monkeypatch)

    assert fixed["evidence"]["quality_cell_floors"]["q6"] == 6.0
    assert fixed == rollback
