"""H5 gate: family-refit high-tier cells->count divisor — NEGATIVE CLOSURE, default OFF.

Full-pool calibration (4969 warehouses) beat the old global fit on marginal count error, but the
aisha clean_eval value sweep was net negative (changed=8, better=0, worse=8): the lever fires on
"gold cells known + red invisible" red-heavy vaults where the family marginal pushes gold UP and
squeezes the red residual (see the H5 comment block in ahmad_ref_engine.py for the full numbers).
These tests pin the closure: DEFAULT behavior is byte-identical to the global fit, the opt-in env
enables the archived tables, and the scope note only appears when opted in.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    HIGH_TIER_FAMILY_DIVISOR_NOTE_PREFIX,
    _high_tier_count_center_from_cells,
    _known_cells_count_bias_log,
    run_reference_engine,
)

OPT_IN = "AISHA_ENABLE_FAMILY_HIGH_TIER_DIVISOR"


def _snapshot_2508_like() -> dict:
    # aisha R4 loadout from the 2508 diag: 库存清点 total + q1/q3/q4 locked by skill + 极品扫描 gold cells.
    return {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2508, "phase": "bidding"},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 44,
            "fixed_counts": {"q1": 13, "q3": 10, "q4": 13},
            "quality_cells": {"q1": 21, "q3": 25, "q4": 43, "q5": 12},
        },
        "public_info_rows": [],
    }


def test_default_off_family_arg_is_inert() -> None:
    # Sweep-refuted lever: by default the family tables must NOT change the center (global fit).
    for key, cells, fam in (("q5", 12, "shipwreck"), ("q5", 18, "shipwreck"), ("q6", 24, "villa")):
        assert _high_tier_count_center_from_cells(key, cells, family=fam) == (
            _high_tier_count_center_from_cells(key, cells)
        )


def test_default_off_bias_matches_global_center() -> None:
    ev = SimpleNamespace(hero="aisha", map_id=2508, fixed_counts={}, quality_cells={"q5": 12.0})
    # global center 3.95: count 4 closer than count 5
    assert _known_cells_count_bias_log({"q5": 4}, ev) > _known_cells_count_bias_log({"q5": 5}, ev)


def test_default_off_no_scope_note() -> None:
    result = run_reference_engine(_snapshot_2508_like(), max_combos=4000).as_dict()
    notes = [str(n) for n in result.get("notes") or []]
    assert not any(n.startswith(HIGH_TIER_FAMILY_DIVISOR_NOTE_PREFIX) for n in notes)


def test_opt_in_family_divisor_centers_match_calibration(monkeypatch) -> None:
    monkeypatch.setenv(OPT_IN, "1")
    # shipwreck gold @12: divisor 1.75+0.075*12=2.65 -> 4.53 (global fit gave 3.95)
    assert _high_tier_count_center_from_cells("q5", 12, family="shipwreck") == pytest.approx(12 / 2.65)
    # villa red @24: divisor 1.40+0.20*24=6.2 (< clamp hi 6.33) -> 3.87
    assert _high_tier_count_center_from_cells("q6", 24, family="villa") == pytest.approx(24 / 6.2)
    # unknown family / missing table -> global
    assert _high_tier_count_center_from_cells("q5", 12, family="map_21xx") == (
        _high_tier_count_center_from_cells("q5", 12, family=None)
    )
    assert _high_tier_count_center_from_cells("q6", 16, family="map_23xx") == (
        _high_tier_count_center_from_cells("q6", 16)
    )


def test_opt_in_emits_family_divisor_scope_note(monkeypatch) -> None:
    monkeypatch.setenv(OPT_IN, "1")
    result = run_reference_engine(_snapshot_2508_like(), max_combos=4000).as_dict()
    notes = [str(n) for n in result.get("notes") or []]
    assert any(n == f"{HIGH_TIER_FAMILY_DIVISOR_NOTE_PREFIX}shipwreck:q5" for n in notes)
