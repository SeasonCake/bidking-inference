from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    _apply_aisha_pinned_nonred_room_hi,
    extract_evidence,
    run_reference_engine,
)
from hero_ref.tools.ahmad_live_panel_server import (  # noqa: E402
    _quality_composition_payload,
)


def _snapshot(
    *,
    hero: str = "aisha",
    round_no: int = 4,
    phase: str = "bidding",
) -> dict:
    return {
        "hero": hero,
        "map_id": 5609,
        "round": round_no,
        "phase": phase,
        "structured_ref_inputs": {
            "fixed_counts": {"q1": 8, "q3": 15, "q4": 7},
            "quality_cells": {"q1": 14, "q3": 48, "q4": 41, "q5": 11},
        },
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 5609,
                "round": round_no,
                "phase": phase,
            },
            "constraints": {"public_info": {}},
        },
    }


def _seeded_result(*, clipped: bool = False, max_combos: int = 8_000):
    snapshot = _snapshot()
    evidence = extract_evidence(snapshot)
    base = run_reference_engine(snapshot, max_combos=max_combos)
    notes = tuple(base.notes)
    if clipped:
        notes += ("aisha_pinned_red_residual:6->17",)
    seeded = replace(
        base,
        total_grid_range=(118, 131, 134),
        red_count_range=(2, 2, 2),
        red_cells_range=(4, 6, 20),
        quality_cells_ranges={**base.quality_cells_ranges, "q6": (4, 6, 20)},
        quality_cells_candidates={
            **base.quality_cells_candidates,
            "q6": ((4, 0.4), (6, 0.3), (20, 0.3)),
        },
        quality_count_cells_candidates={
            **base.quality_count_cells_candidates,
            "q6": ((2, 4, 0.4), (2, 6, 0.3), (2, 20, 0.3)),
        },
        quality_lock={
            **base.quality_lock,
            "q6": {"strength": "probabilistic", "reason": "combo_multi_solution"},
        },
        notes=notes,
    )
    return seeded, evidence


def _strict_composition_wire(result) -> dict:
    """Keep deliberately malformed native q6 scalars visible to the strict producer."""

    raw = result.as_dict()
    if "q6" in result.quality_count_cells_candidates:
        raw["quality_count_cells_candidates"]["q6"] = [
            {"count": count, "cells": cells, "prob": probability}
            for count, cells, probability in result.quality_count_cells_candidates["q6"]
        ]
    for field in ("quality_count_candidates", "quality_cells_candidates"):
        native = getattr(result, field)
        if "q6" in native:
            raw[field]["q6"] = [
                {"value": value, "prob": probability}
                for value, probability in native["q6"]
            ]
    return raw


def _project(result, evidence):
    return _apply_aisha_pinned_nonred_room_hi(result, evidence, round_no=4)


@pytest.mark.parametrize("clipped", [False, True], ids=["normal", "clipped"])
def test_valid_soft_q6_bundle_is_omitted_atomically(clipped: bool) -> None:
    seeded, evidence = _seeded_result(clipped=clipped)

    repaired = _project(seeded, evidence)

    assert "q6" not in repaired.quality_cells_candidates
    assert "q6" not in repaired.quality_count_cells_candidates
    assert "q6" not in repaired.quality_lock
    payload = _quality_composition_payload(repaired.as_dict())
    assert payload["status"] == "ready"
    assert payload["qualities"]["q6"]["status"] == "unavailable"
    assert (
        repaired.conservative,
        repaired.balanced,
        repaired.aggressive,
    ) == (seeded.conservative, seeded.balanced, seeded.aggressive)


@pytest.mark.parametrize("clipped", [False, True], ids=["normal", "clipped"])
def test_malformed_boolean_joint_survives_to_producer_conflict(clipped: bool) -> None:
    seeded, evidence = _seeded_result(clipped=clipped)
    malformed = replace(
        seeded,
        quality_count_cells_candidates={
            **seeded.quality_count_cells_candidates,
            "q6": ((True, 7, 1.0),),
        },
        quality_lock={
            **seeded.quality_lock,
            "q6": {"strength": "exact", "reason": "exact_lock"},
        },
    )
    assert _quality_composition_payload(_strict_composition_wire(malformed))["status"] == (
        "conflict"
    )

    repaired = _project(malformed, evidence)

    assert repaired.quality_count_cells_candidates["q6"] == ((True, 7, 1.0),)
    assert repaired.quality_lock["q6"] == malformed.quality_lock["q6"]
    assert _quality_composition_payload(_strict_composition_wire(repaired))["status"] == (
        "conflict"
    )


@pytest.mark.parametrize("clipped", [False, True], ids=["normal", "clipped"])
def test_exact_joint_marginal_conflict_survives_projection(clipped: bool) -> None:
    seeded, evidence = _seeded_result(clipped=clipped)
    conflicting = replace(
        seeded,
        quality_count_candidates={
            **seeded.quality_count_candidates,
            "q6": ((4, 1.0),),
        },
        quality_cells_candidates={
            **seeded.quality_cells_candidates,
            "q6": ((7, 1.0),),
        },
        quality_count_cells_candidates={
            **seeded.quality_count_cells_candidates,
            "q6": ((3, 7, 1.0),),
        },
        quality_lock={
            **seeded.quality_lock,
            "q6": {"strength": "exact", "reason": "exact_lock"},
        },
    )
    assert _quality_composition_payload(conflicting.as_dict())["status"] == "conflict"

    repaired = _project(conflicting, evidence)

    assert repaired.quality_count_candidates["q6"] == ((4, 1.0),)
    assert repaired.quality_cells_candidates["q6"] == ((7, 1.0),)
    assert repaired.quality_count_cells_candidates["q6"] == ((3, 7, 1.0),)
    assert repaired.quality_lock["q6"] == conflicting.quality_lock["q6"]
    assert _quality_composition_payload(repaired.as_dict())["status"] == "conflict"


@pytest.mark.parametrize(
    ("joint", "lock"),
    [
        (((2, 7, 0.6), (2, 9, 0.4)), None),
        (None, {"strength": "probabilistic", "reason": "combo_multi_solution"}),
    ],
    ids=["joint_only", "lock_only"],
)
def test_q6_joint_lock_half_pair_is_preserved(joint, lock) -> None:
    seeded, evidence = _seeded_result()
    joints = dict(seeded.quality_count_cells_candidates)
    locks = dict(seeded.quality_lock)
    if joint is None:
        joints.pop("q6", None)
    else:
        joints["q6"] = joint
    if lock is None:
        locks.pop("q6", None)
    else:
        locks["q6"] = lock
    half_pair = replace(
        seeded,
        quality_count_cells_candidates=joints,
        quality_lock=locks,
    )
    assert _quality_composition_payload(half_pair.as_dict())["status"] == "conflict"

    repaired = _project(half_pair, evidence)

    assert ("q6" in repaired.quality_count_cells_candidates) == (joint is not None)
    assert ("q6" in repaired.quality_lock) == (lock is not None)
    assert _quality_composition_payload(repaired.as_dict())["status"] == "conflict"


@pytest.mark.parametrize(
    ("field", "q6_candidates"),
    [
        ("quality_count_candidates", ((2, 1.0),)),
        ("quality_cells_candidates", ((7, 1.0),)),
        (
            "quality_count_cells_candidates",
            ((2, 7, 0.5), (2, 7, 0.5)),
        ),
    ],
    ids=["exact_count_marginal", "exact_cells_marginal", "duplicate_joint"],
)
def test_authoritative_q6_candidate_sets_are_not_cleaned(field, q6_candidates) -> None:
    seeded, evidence = _seeded_result()
    values = dict(getattr(seeded, field))
    values["q6"] = q6_candidates
    authoritative = replace(seeded, **{field: values})

    repaired = _project(authoritative, evidence)

    assert getattr(repaired, field)["q6"] == q6_candidates
    assert repaired.quality_count_cells_candidates.get("q6") is not None
    assert repaired.quality_lock.get("q6") is not None


def test_settlement_other_hero_r1_and_rollback_remain_noop(monkeypatch) -> None:
    seeded, _ = _seeded_result()
    contexts = (
        (_snapshot(phase="settlement"), 4),
        (_snapshot(hero="ahmed"), 4),
        (_snapshot(round_no=1), 1),
    )
    for snapshot, round_no in contexts:
        assert _apply_aisha_pinned_nonred_room_hi(
            seeded,
            extract_evidence(snapshot),
            round_no=round_no,
        ) == seeded

    monkeypatch.setenv("BIDKING_DISABLE_AISHA_PINNED_NONRED_ROOM_HI", "1")
    assert _project(seeded, extract_evidence(_snapshot())) == seeded


@pytest.mark.parametrize("clipped", [False, True], ids=["normal", "clipped"])
def test_valid_soft_q6_bundle_remains_atomic_at_50k(clipped: bool) -> None:
    seeded, evidence = _seeded_result(clipped=clipped, max_combos=50_000)

    repaired = _project(seeded, evidence)

    assert "q6" not in repaired.quality_count_cells_candidates
    assert "q6" not in repaired.quality_lock
    assert _quality_composition_payload(repaired.as_dict())["status"] == "ready"
    assert (
        repaired.conservative,
        repaired.balanced,
        repaired.aggressive,
    ) == (seeded.conservative, seeded.balanced, seeded.aggressive)
