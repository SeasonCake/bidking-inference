from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as engine  # noqa: E402


def _aisha_snapshot(*, avg_q5_cells: float | None = None) -> dict:
    structured: dict = {
        "hero": "aisha",
        "total_count": 23,
        "counts": {"q3": 3},
        "quality_cells": {"q3": 12},
        "split_counts": {"green": 9, "white": 4},
        "split_quality_cells": {"green": 24, "white": 6},
    }
    if avg_q5_cells is not None:
        structured["avg_cells"] = {"q5": avg_q5_cells}
    return {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4542,
                "phase": "bidding",
                "round": 3,
            }
        },
        "structured_ref_inputs": structured,
    }


@pytest.mark.parametrize(
    ("key", "avg_cells", "floor", "max_cells", "identity_count"),
    (
        ("q4", 12.0, 20_082, 12, 1),
        ("q5", 18.0, 106_500, 18, 1),
        ("q6", 20.0, 519_800, 20, 3),
    ),
)
def test_max_footprint_catalog_floor_is_value_evidence_not_count_lock(
    key: str,
    avg_cells: float,
    floor: int,
    max_cells: int,
    identity_count: int,
) -> None:
    resolved = engine._max_footprint_avg_catalog_floor(key, avg_cells)

    assert resolved is not None
    assert resolved[0:2] == (floor, max_cells)
    assert len(resolved[2]) == identity_count
    evidence = engine.extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "aisha",
                    "map_id": 4542,
                    "phase": "bidding",
                    "round": 3,
                }
            },
            "structured_ref_inputs": {"avg_cells": {key: avg_cells}},
        }
    )
    assert evidence.quality_min_total_value_floors == {key: float(floor)}


def test_max_footprint_floor_fails_open_for_nonmax_or_catalog_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine._max_footprint_avg_catalog_floor("q5", 16.0) is None

    monkeypatch.setattr(
        engine,
        "_validated_quality_catalog",
        lambda _quality: ((), (), "catalog_mismatch"),
    )
    engine._max_footprint_avg_catalog_floor.cache_clear()
    assert engine._max_footprint_avg_catalog_floor("q5", 18.0) is None
    engine._max_footprint_avg_catalog_floor.cache_clear()


def test_public_soft_max_average_does_not_create_hard_value_floor() -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4542,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q5_avg_cells", "value": 18.0},
                    ]
                }
            },
        }
    }

    evidence = engine.extract_evidence(snapshot)

    assert evidence.soft_avg_cell_keys == frozenset({"q5"})
    assert evidence.quality_min_total_value_floors == {}


def test_4542_hard_gold_18_floor_preserves_repeat_count_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _aisha_snapshot(avg_q5_cells=18.0)

    monkeypatch.delenv("BIDKING_DISABLE_MAX_FOOTPRINT_AVG_VALUE_FLOOR", raising=False)
    fixed = engine.run_reference_engine(snapshot).as_dict()
    monkeypatch.setenv("BIDKING_DISABLE_MAX_FOOTPRINT_AVG_VALUE_FLOOR", "1")
    legacy = engine.run_reference_engine(snapshot).as_dict()

    assert fixed["evidence"]["quality_min_total_value_floors"] == {"q5": 106_500.0}
    assert fixed["conservative"] >= 106_500
    assert fixed["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert fixed["quality_count_ranges"]["q5"] == legacy["quality_count_ranges"]["q5"]
    assert fixed["quality_count_candidates"]["q5"] == legacy["quality_count_candidates"]["q5"]
    assert any(
        candidate["value"] > 1
        for candidate in fixed["quality_count_candidates"]["q5"]
    )


def test_aisha_conservative_repairs_only_marginal_zero_underflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4542,
                "phase": "bidding",
                "round": 2,
            }
        },
        "structured_ref_inputs": {
            "hero": "aisha",
            "total_count": 23,
            "split_counts": {"green": 9, "white": 4},
            "split_quality_cells": {"green": 24, "white": 6},
        },
    }

    monkeypatch.delenv(
        "BIDKING_DISABLE_AISHA_CONS_MARGINAL_UNDERFLOW_REPAIR",
        raising=False,
    )
    fixed = engine.run_reference_engine(snapshot, max_combos=20_000).as_dict()
    monkeypatch.setenv("BIDKING_DISABLE_AISHA_CONS_MARGINAL_UNDERFLOW_REPAIR", "1")
    legacy = engine.run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert legacy["conservative"] == 0
    assert fixed["conservative"] > legacy["conservative"]
    assert fixed["balanced"] == legacy["balanced"]
    assert fixed["aggressive"] == legacy["aggressive"]
    assert any(
        str(note).startswith("aisha_cons_pure_prior_red_strip_underflow_nonred:")
        for note in fixed["notes"]
    )
    assert any(
        str(note).startswith("aisha_cons_pure_prior_red_strip:")
        for note in legacy["notes"]
    )


def test_aisha_underflow_repair_never_exceeds_unstripped_p25(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4542,
                "phase": "bidding",
                "round": 2,
            }
        },
        "structured_ref_inputs": {
            "hero": "aisha",
            "total_count": 23,
            "split_counts": {"green": 9, "white": 4},
            "split_quality_cells": {"green": 24, "white": 6},
        },
    }

    # Positive control for the safety cap: an over-wide non-red reconstruction
    # must not turn a "strip" into a value lift above the original total P25.
    monkeypatch.setattr(
        engine,
        "_combo_value_distribution_points_with_quality_scales",
        lambda *_args, **_kwargs: ((1_000_000_000.0, 1.0),),
    )
    monkeypatch.delenv(
        "BIDKING_DISABLE_AISHA_CONS_MARGINAL_UNDERFLOW_REPAIR",
        raising=False,
    )
    monkeypatch.delenv("AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP", raising=False)
    repaired = engine.run_reference_engine(snapshot, max_combos=20_000).as_dict()

    monkeypatch.setenv("AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP", "1")
    unstripped = engine.run_reference_engine(snapshot, max_combos=20_000).as_dict()

    assert repaired["conservative"] == unstripped["conservative"]
