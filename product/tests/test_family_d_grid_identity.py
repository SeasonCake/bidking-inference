"""族D gate: Σ(per-tier cells) == total grid is an IDENTITY, not an estimate.

Every cell of the vault belongs to exactly one quality tier. The engine broke that identity because the
two sides were computed on paths that never met — the total grid comes from the spatial anchor chain
(footroom band / visible depth / geometry / minimap), while the per-tier cells were re-derived from
count×prior and never saw it. Measured on the clean pools BEFORE the fix: Σcells != total grid on
aisha 109/142 (76.8%) and ahmed 352/1418 (24.8%), with up to 52 cells belonging to nobody.

Those orphan cells are exactly the invisible red the users report. aisha 2502 R5 is the cleanest proof:
q1/q3/q4/q5 cells land EXACTLY on settled truth, the total grid lands on truth (102 vs 103) — and the
18 cells left over are precisely the red the panel was showing as 2 (truth 21).

DISPLAY-ONLY by default, deliberately. The reconciled cells are better (aisha red band covers truth 87/142 vs 81;
truth-above-ceiling 55 -> 48) and at EQUAL calibration they even price better (MAE 31.3% vs 33.9% once
each arm gets its own optimal value scale). But at the CURRENT calibration they push the quote bias from
-3.5% to +8.5%, because the per-cell price was fitted against the under-claimed cells and absorbed the
missing footprint. Correcting the cells without re-fitting that price double-counts it, so the quote
stays on pass 1 until 族D-value re-calibrates it. Hence: cells move, quote must not.

Narrow U04 exception: when pass 1 says exact-zero red but pass 2 has an internally coherent positive
red count/cells/value allocation, display-only would splice the two passes into count=0/cells>0/value=0
and quote no red. That exact contradiction promotes pass 2's semantic family and quote, with every quote
tier monotonic against pass 1. Other 族D rows remain display-only.

Kill: BIDKING_DISABLE_GRID_IDENTITY_RECONCILE=1. Hero scope: BIDKING_GRID_IDENTITY_HEROES (aisha,ahmed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

from ahmad_ref_engine import (  # noqa: E402
    GRID_IDENTITY_ZERO_RED_RESCUE_NOTE,
    GRID_IDENTITY_MIN_GAP,
    RefEvidence,
    _fit_grids_to_total_target,
    _grid_identity_hero_in_scope,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)

QK = ("q1", "q3", "q4", "q5", "q6")


def test_hero_scope_defaults_to_the_two_measured_heroes() -> None:
    # ethan is grid-primary: he counts cells directly, so his TIERS are the trustworthy side and his
    # total is the estimated one. Reconciling him improves the identity (81->66) but makes every tier's
    # truth coverage worse (q4 76->73, q5 74->71, q6 63->59) — aligning the good side to the bad one.
    assert _grid_identity_hero_in_scope("aisha")
    assert _grid_identity_hero_in_scope("ahmed")
    assert not _grid_identity_hero_in_scope("ethan")
    assert not _grid_identity_hero_in_scope("sophie")


def test_hero_scope_env_allows_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_GRID_IDENTITY_HEROES", "aisha,ethan")
    assert _grid_identity_hero_in_scope("ethan")
    assert not _grid_identity_hero_in_scope("ahmed")


def test_orphan_cells_are_dealt_to_the_unpinned_tier() -> None:
    """The design gate, in miniature (aisha 2502 R5). The low tiers are pinned by tool readings, the
    total is 102, and the tiers only claim 84 — so all 18 orphan cells are physically red, and the fit
    must hand them to the one tier that is free to take them."""
    grids = {"q1": 15.0, "q3": 31.0, "q4": 35.0, "q5": 1.0, "q6": 2.0}
    counts = {"q1": 6, "q3": 12, "q4": 9, "q5": 1, "q6": 2}
    # q1/q3/q4/q5 carry a tool avg -> pinned; only q6's footprint is unknown
    avg_cells = {"q1": 2.5, "q3": 2.583, "q4": 3.889, "q5": 1.0}
    fitted = _fit_grids_to_total_target(
        grids, counts, avg_cells, 102.0, soft_avg_cell_keys=frozenset(), exact_dp=False
    )
    assert sum(fitted.values()) == pytest.approx(102.0, abs=0.01)
    for key in ("q1", "q3", "q4", "q5"):
        assert fitted[key] == pytest.approx(grids[key]), f"{key} was pinned and must not move"
    assert fitted["q6"] == pytest.approx(20.0, abs=0.01)  # 2 + the 18 orphans; settled truth was 21


def test_proportional_split_when_nothing_is_pinned() -> None:
    """Early rounds pin nothing, and then the orphans must NOT all land on red — 5609 R1 has 42 of them
    against a red whose truth is 24 cells. Every scalable tier grows by the same factor instead."""
    grids = {"q1": 15.0, "q3": 11.0, "q4": 14.0, "q5": 8.0, "q6": 6.0}  # sum 54
    counts = {"q1": 6, "q3": 4, "q4": 5, "q5": 3, "q6": 2}
    fitted = _fit_grids_to_total_target(
        grids, counts, {}, 98.0, soft_avg_cell_keys=frozenset(), exact_dp=False
    )
    assert sum(fitted.values()) == pytest.approx(98.0, abs=0.01)
    scale = 98.0 / 54.0
    for key in QK:
        assert fitted[key] == pytest.approx(grids[key] * scale, abs=0.01)
    # red takes its share, not the whole remainder (which would be 6 + 44 = 50 against a truth of 24)
    assert fitted["q6"] < 12.0


def test_fit_never_drops_a_tier_below_one_cell_per_item() -> None:
    grids = {"q1": 15.0, "q3": 11.0, "q4": 14.0, "q5": 8.0, "q6": 6.0}
    counts = {"q1": 6, "q3": 4, "q4": 5, "q5": 3, "q6": 4}
    fitted = _fit_grids_to_total_target(
        grids, counts, {}, 60.0, soft_avg_cell_keys=frozenset(),
        cell_floors={k: 0.0 for k in QK}, exact_dp=False,
    )
    for key in QK:
        assert fitted[key] >= float(counts[key]), (key, fitted[key], counts[key])


def _aisha_2501_r4_zero_red_repro() -> dict:
    """Reduced from HeroRefDiag-20260713-204315-2501_1700600949334061 R4.

    The real snapshot additionally carries the minimap layout and yields red cells 19/24/24. These
    are the mapping-sensitive inputs needed to reproduce the same pass split without personal/session
    metadata: total 53; exact q1/q3/q4 = 14/14/17; q5 minimum 3 and public avg 1.75.
    """
    return prepare_reference_engine_snapshot(
        {
            "hero": "aisha",
            "map_id": 2501,
            "round": 4,
            "phase": "bidding",
            "structured_ref_inputs": {
                "hero": "aisha",
                "total_count": 53,
                "counts": {"q3": 14, "q4": 17},
                "quality_cells": {"q3": 49, "q4": 43},
                "split_counts": {"green": 11, "white": 3},
                "split_quality_cells": {"green": 23, "white": 3},
            },
            "ui_contract": {
                "context": {"round": 4},
                "constraints": {
                    "counts": {
                        "known_quality_counts": {"q1": 3, "q2": 11, "q3": 14, "q4": 17, "q5": 3},
                        "input_total_item_count": 53,
                    },
                    "public_info": {
                        "public_numeric_facts": [
                            {
                                "info_id": 200015,
                                "semantic": "q5_avg_cells",
                                "kind": "avg_cells",
                                "quality": 5,
                                "value": 1.75,
                            }
                        ],
                        "public_avg_cells": [
                            {
                                "info_id": 200015,
                                "semantic": "q5_avg_cells",
                                "kind": "avg_cells",
                                "quality": 5,
                                "value": 1.75,
                            }
                        ],
                    },
                },
            },
        }
    )


def test_zero_red_count_is_rescued_with_the_reconciled_semantic_family(monkeypatch) -> None:
    snapshot = _aisha_2501_r4_zero_red_repro()

    monkeypatch.setenv("BIDKING_DISABLE_GRID_IDENTITY_ZERO_RED_RESCUE", "1")
    rollback = run_reference_engine(snapshot).as_dict()
    assert rollback["red_count_range"] == [0, 0, 0]
    assert rollback["red_cells_range"][1] > 0
    assert rollback["red_value_range"] == [0, 0, 0]

    monkeypatch.delenv("BIDKING_DISABLE_GRID_IDENTITY_ZERO_RED_RESCUE", raising=False)
    fixed = run_reference_engine(snapshot).as_dict()
    assert fixed["red_count_range"] == [4, 4, 4]
    assert fixed["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert fixed["red_cells_range"][1] > 0
    assert fixed["red_value_range"][0] > 0
    assert all(
        sum(int(fixed["quality_count_ranges"][key][index]) for key in QK) == 53
        for index in range(3)
    )
    assert fixed["conservative"] >= rollback["conservative"]
    assert fixed["balanced"] > rollback["balanced"]
    assert fixed["aggressive"] > rollback["aggressive"]
    assert GRID_IDENTITY_ZERO_RED_RESCUE_NOTE in fixed["notes"]


def test_zero_red_rescue_is_noop_when_red_count_is_already_positive(monkeypatch) -> None:
    snapshot = _aisha_2501_r4_zero_red_repro()
    snapshot["structured_ref_inputs"]["counts"]["q6"] = 1

    monkeypatch.setenv("BIDKING_DISABLE_GRID_IDENTITY_ZERO_RED_RESCUE", "1")
    rollback = run_reference_engine(snapshot).as_dict()
    monkeypatch.delenv("BIDKING_DISABLE_GRID_IDENTITY_ZERO_RED_RESCUE", raising=False)
    fixed = run_reference_engine(snapshot).as_dict()

    assert rollback["red_count_range"][1] > 0
    assert fixed == rollback
    assert GRID_IDENTITY_ZERO_RED_RESCUE_NOTE not in fixed["notes"]


def test_2501_recursive_reconcile_matches_with_run_cache_disabled(monkeypatch) -> None:
    snapshot = _aisha_2501_r4_zero_red_repro()

    monkeypatch.delenv("AHMAD_DISABLE_RUN_INVARIANT_CACHE", raising=False)
    cached = run_reference_engine(snapshot).as_dict()
    monkeypatch.setenv("AHMAD_DISABLE_RUN_INVARIANT_CACHE", "1")
    uncached = run_reference_engine(snapshot).as_dict()

    assert uncached == cached
    assert GRID_IDENTITY_ZERO_RED_RESCUE_NOTE in uncached["notes"]
