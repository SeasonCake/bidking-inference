"""Regression for HeroRefDiag 2548:1741282945703810 R5.

The action measured all red collectibles at 47 cells, but q6 count remained probabilistic.  The old
count-based value path displayed 47/47/47 cells while pricing the red center at only 936k, leaving the
headline quote at 1.318M versus the 2.019M settlement.  Exact q6 total cells now use a settlement-truth
total-value curve; direct red value evidence remains authoritative and the switch is reversible.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "hero_ref" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ahmad_ref_engine import (  # noqa: E402
    AISHA_EXACT_RED_CELLS_VALUE_NOTE_PREFIX,
    BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE,
    ETHAN_RESID_RED_PERCELL_BY_FAMILY,
    ETHAN_RESID_RED_PERCELL_GLOBAL,
    _aisha_exact_red_cells_value_curve,
    _aisha_exact_red_cells_value_target,
    run_reference_engine,
)

DIAG = Path(r"C:\Users\example\Desktop\HeroRefDiag-20260719-082052-2548_1741282945703810")
TRUTH_TOTAL = 2_019_042


def _evidence(*, hero: str = "aisha", phase: str = "bidding", cells: int = 47):
    from types import SimpleNamespace

    return SimpleNamespace(
        hero=hero,
        phase=phase,
        map_id=2548,
        quality_cells={"q6": cells},
        quality_values={},
        avg_values={},
        quality_value_floors={},
        quality_value_floor_item_counts={},
    )


def test_curve_and_shipwreck_family_scale() -> None:
    assert _aisha_exact_red_cells_value_curve(47) == pytest.approx(2_126_800.0)
    target = _aisha_exact_red_cells_value_target(_evidence())
    expected = (
        2_126_800.0
        * ETHAN_RESID_RED_PERCELL_BY_FAMILY["shipwreck"]
        / ETHAN_RESID_RED_PERCELL_GLOBAL
    )
    assert target == pytest.approx(expected)
    assert target == pytest.approx(2_011_814.0, abs=1.0)


def test_narrow_gate_and_value_evidence_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _aisha_exact_red_cells_value_target(_evidence(hero="ahmed")) is None
    assert _aisha_exact_red_cells_value_target(_evidence(phase="settled")) is None
    assert _aisha_exact_red_cells_value_target(_evidence(cells=19)) is None

    known = _evidence()
    known.quality_values = {"q6": 1_675_292}
    assert _aisha_exact_red_cells_value_target(known) is None

    averaged = _evidence()
    averaged.avg_values = {"q6": 420_000}
    assert _aisha_exact_red_cells_value_target(averaged) is None

    partial = _evidence()
    partial.quality_value_floors = {"q6": 519_800}
    partial.quality_value_floor_item_counts = {"q6": 1}
    assert _aisha_exact_red_cells_value_target(partial) == pytest.approx(
        _aisha_exact_red_cells_value_target(_evidence())
    )

    monkeypatch.setenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, "1")
    assert _aisha_exact_red_cells_value_target(_evidence()) is None


def _diag_snapshot() -> dict:
    latest = json.loads((DIAG / "latest_snapshot.json").read_text(encoding="utf-8-sig"))
    structured = copy.deepcopy(latest["structured_ref_inputs"])
    return {
        "hero": latest["hero"],
        "map_id": latest["map_id"],
        "model_map_id": latest["model_map_id"],
        "phase": "bidding",
        "round": 5,
        "structured_ref_inputs": structured,
        "minimap_grid_items": copy.deepcopy(structured.get("minimap_items") or []),
        "public_info_rows": copy.deepcopy(latest.get("public_info_rows") or []),
        "action_send_rows": copy.deepcopy(latest.get("action_send_rows") or []),
        "action_result_rows": copy.deepcopy(latest.get("action_result_rows") or []),
        "skill_reveals": copy.deepcopy(latest.get("skill_reveals") or []),
    }


def _with_known_red(snapshot: dict) -> dict:
    enriched = copy.deepcopy(snapshot)
    enriched["public_info_rows"].append(
        {
            "info_id": 200021,
            "map_id": 2548,
            "revealed_items_detail": [
                {
                    "runtime_id": 999001,
                    "local_index": 999,
                    "item_id": 1096001,
                    "quality": 6,
                    "value": 92_880,
                    "shape_code": 12,
                    "cells": 2,
                }
            ],
        }
    )
    return enriched


def test_2548_replay_lifts_price_without_changing_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (DIAG / "latest_snapshot.json").is_file():
        pytest.skip("faithful 2548 diagnostic package is local-only")
    snapshot = _diag_snapshot()

    monkeypatch.setenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, "1")
    legacy = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.delenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, raising=False)
    repaired = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert legacy["balanced"] == 1_318_321
    assert legacy["red_cells_range"] == [47, 47, 47]
    assert legacy["red_value_range"] == [393_312, 936_457, 1_318_953]

    assert repaired["red_cells_range"] == legacy["red_cells_range"]
    assert repaired["red_count_range"] == legacy["red_count_range"]
    assert repaired["total_grid_range"] == legacy["total_grid_range"]
    assert repaired["red_value_range"] == [844_962, 2_011_814, 2_464_472]
    assert repaired["balanced"] == 2_071_986
    assert abs(repaired["balanced"] - TRUTH_TOTAL) < abs(legacy["balanced"] - TRUTH_TOTAL)
    assert repaired["conservative"] < TRUTH_TOTAL < repaired["aggressive"]
    assert any(
        note.startswith(f"{AISHA_EXACT_RED_CELLS_VALUE_NOTE_PREFIX}47cells:")
        for note in repaired["notes"]
    )
    assert "aisha_dynamic_balanced_pct:0.50" in repaired["notes"]


def test_2548_known_red_is_monotonic_and_keeps_exact_total_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not (DIAG / "latest_snapshot.json").is_file():
        pytest.skip("faithful 2548 diagnostic package is local-only")
    snapshot = _diag_snapshot()
    exact_only = run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000).as_dict()
    with_known = run_reference_engine(_with_known_red(snapshot), max_combos=8_000).as_dict()

    assert exact_only["red_cells_range"] == with_known["red_cells_range"] == [47, 47, 47]
    assert all(
        enriched >= baseline
        for baseline, enriched in zip(
            (exact_only["conservative"], exact_only["balanced"], exact_only["aggressive"]),
            (with_known["conservative"], with_known["balanced"], with_known["aggressive"]),
        )
    )
    assert all(
        enriched >= baseline
        for baseline, enriched in zip(
            exact_only["red_value_range"], with_known["red_value_range"]
        )
    )
    assert with_known["evidence"]["quality_value_floors"]["q6"] == 92_880.0

    monkeypatch.setenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, "1")
    rollback = run_reference_engine(_with_known_red(snapshot), max_combos=8_000).as_dict()
    assert rollback["red_cells_range"] == [47, 47, 47]
    assert rollback["red_value_range"] == [482_178, 1_148_043, 1_668_589]
    assert (rollback["conservative"], rollback["balanced"], rollback["aggressive"]) == (
        865_258,
        1_501_705,
        1_672_190,
    )
    assert rollback["balanced"] < with_known["balanced"]


def test_non_aisha_full_engine_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (DIAG / "latest_snapshot.json").is_file():
        pytest.skip("faithful 2548 diagnostic package is local-only")
    snapshot = _diag_snapshot()
    snapshot["hero"] = "ahmed"
    snapshot["structured_ref_inputs"]["hero"] = "ahmed"

    monkeypatch.setenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, "1")
    disabled = run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000).as_dict()
    monkeypatch.delenv(BIDKING_DISABLE_AISHA_EXACT_RED_CELLS_VALUE, raising=False)
    enabled = run_reference_engine(copy.deepcopy(snapshot), max_combos=8_000).as_dict()
    assert enabled == disabled
