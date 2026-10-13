"""0.3.1 quality-composition producer -> consumer product-path regressions.

The 2401 fixture is a privacy-safe reduction of
HeroRefDiag-20260728-193041-2401_1770351298265584.  It keeps only the
structured facts required by the native reference engine and drops packet
rows, player names, local paths, timestamps, and raw diagnostics.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hero_ref.tools.ahmad_live_panel_server import (
    _quality_composition_payload,
    summarize_snapshot,
)
from hero_ref.tools.detail_composition_view import detail_composition_view


EXPECTED_2401_PAIRS = {
    "q1": (4, 21),
    "q3": (6, 44),
    "q4": (12, 49),
    "q5": (1, 1),
    "q6": (3, 7),
}


def _sanitized_2401_snapshot(*, phase: str) -> dict:
    settled = phase == "settled"
    snapshot = {
        "created_at": time.time(),
        "hero": "ahmed",
        "map_id": 2401,
        "round": 4,
        "phase": phase,
        "session_id": f"2401:sanitized-{phase}",
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "settlement_incomplete": False,
        "inventory_count": 26,
        "inventory_cells": 122,
        "known_value_sum": 500_453,
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "ahmed",
            "total_count": 26,
            "total_cells": None,
            "avg_cells": {
                "q1": 5.25,
                "q3": 7.333333492279053,
                "q4": 4.083333492279053,
                "q5": 1.0,
            },
            "counts": {"q3": 6, "q5": 1},
            "quality_cells": {"q1": 21},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "scoped_count_sums": [],
            "split_avg_cells": {},
            "split_counts": {},
            "split_quality_cells": {},
            "field_updates": [],
            "minimap_items": [],
        },
        "ui_contract": {
            "context": {
                "session_id": f"2401:sanitized-{phase}",
                "hero": "ahmed",
                "map_id": 2401,
                "model_map_id": 2401,
                "round": 4,
                "phase": phase,
                "known_value_sum": 500_453,
                "inventory_count": 26,
                "inventory_cells": 122,
                "warehouse_source_qualification": (
                    "postupdate_unknown_warehouse_source"
                ),
            },
            "constraints": {"public_info": {}},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {},
            "actions": {},
            "diagnostics": {},
            "truth": {
                "available": settled,
                "complete": settled,
                "partial": False,
                "total_value": 500_453 if settled else None,
                "total_items": 26 if settled else None,
                "total_cells": 122 if settled else None,
                "q6": (
                    {"count": 3, "cells": 7, "value": 330_762}
                    if settled
                    else {}
                ),
                "top_item": {},
            },
        },
    }
    if settled:
        snapshot.update(
            {
                "final_quality_counts": "q2=4;q3=6;q4=12;q5=1;q6=3",
                "final_quality_cells": "q2=21;q3=44;q4=49;q5=1;q6=7",
            }
        )
    return snapshot


def _native_2401_summary(tmp_path: Path, *, phase: str) -> dict:
    return summarize_snapshot(
        _sanitized_2401_snapshot(phase=phase),
        snapshot_path=tmp_path / phase / "latest_snapshot.json",
    )


def _assert_expected_exact_composition(summary: dict) -> None:
    view = detail_composition_view(summary)
    assert view.status == "ready"
    for key, (count, cells) in EXPECTED_2401_PAIRS.items():
        quality = view.quality(key)
        assert quality.status == "exact"
        assert quality.lock_strength == "exact"
        assert quality.source == "pricing_posterior"
        assert quality.pricing_posterior is True
        assert quality.display_reconciled is False
        assert quality.shown_probability == 1.0
        assert quality.other_probability == 0.0
        assert quality.truncated is False
        assert quality.cap_hit is False
        assert len(quality.candidates) == 1
        assert (
            quality.candidates[0].count,
            quality.candidates[0].cells,
            quality.candidates[0].probability,
        ) == (count, cells, 1.0)


def test_real_2401_reduced_settled_snapshot_reaches_native_consumer(
    tmp_path: Path,
) -> None:
    summary = _native_2401_summary(tmp_path, phase="settled")
    assert summary["context"]["phase"] == "settled"
    _assert_expected_exact_composition(summary)


def test_bidding_snapshot_keeps_native_joint_composition_available(
    tmp_path: Path,
) -> None:
    summary = _native_2401_summary(tmp_path, phase="bidding")
    assert summary["context"]["phase"] == "bidding"
    view = detail_composition_view(summary)
    assert view.status == "ready"
    for key in ("q1", "q3", "q4", "q5"):
        assert view.quality(key).status == "exact"
    red = view.quality("q6")
    assert red.status == "ready"
    assert red.lock_strength == "probabilistic"
    assert red.lock_reason == "count_exact_joint_multi_solution"
    assert {
        (candidate.count, candidate.cells, candidate.probability)
        for candidate in red.candidates
    } == {
        (3, 9, 0.3333),
        (3, 10, 0.3333),
        (3, 11, 0.3333),
    }
    assert red.shown_probability == pytest.approx(0.9999)
    assert red.other_probability == pytest.approx(0.0001)
    assert red.truncated is True


def test_marginal_candidates_are_never_synthesized_into_joint_rows() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_candidates": {
                "q6": [{"value": 2, "prob": 1.0}],
            },
            "quality_cells_candidates": {
                "q6": [{"value": 7, "prob": 1.0}],
            },
            "quality_lock": {
                "q6": {"strength": "exact", "reason": "count_only"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    assert view.status == "unavailable"
    assert view.quality("q6").candidates == ()


def test_probabilistic_marginals_reconcile_to_visible_joint_rows() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [
                    {"count": 1, "cells": 2, "prob": 0.0673},
                    {"count": 1, "cells": 3, "prob": 0.0673},
                    {"count": 2, "cells": 5, "prob": 0.0615},
                ],
            },
            # Independent top-k marginals can omit a visible joint value and can
            # round a repeated value one ten-thousandth below the joint sum.
            "quality_count_candidates": {
                "q6": [
                    {"value": 3, "prob": 0.4},
                    {"value": 1, "prob": 0.1345},
                ],
            },
            "quality_cells_candidates": {
                "q6": [
                    {"value": 2, "prob": 0.0673},
                    {"value": 5, "prob": 0.0615},
                ],
            },
            "quality_lock": {
                "q6": {"strength": "probabilistic", "reason": "joint"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    red = view.quality("q6")

    assert view.status == "ready"
    assert red.status == "ready"
    assert red.display_reconciled is True
    assert [(row.value, row.probability) for row in red.count_candidates] == [
        (3, 0.4),
        (1, 0.1346),
        (2, 0.0615),
    ]
    assert [(row.value, row.probability) for row in red.cells_candidates] == [
        (2, 0.0673),
        (3, 0.0673),
        (5, 0.0615),
    ]


def test_exact_joint_still_rejects_a_contradictory_exact_marginal() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 3, "cells": 7, "prob": 1.0}],
            },
            "quality_count_candidates": {
                "q6": [{"value": 4, "prob": 1.0}],
            },
            "quality_lock": {
                "q6": {"strength": "exact", "reason": "exact_lock"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})

    assert view.status == "conflict"
    assert view.quality("q6").status == "conflict"


@pytest.mark.parametrize(
    "bad_row",
    [
        {"count": True, "cells": 7, "prob": 1.0},
        {"count": 3, "cells": False, "prob": 1.0},
        {"count": 3, "cells": 7, "prob": float("nan")},
        {"count": 3, "cells": 7, "prob": float("inf")},
        {"count": 3, "cells": 7, "prob": -0.1},
        {"count": 3, "cells": 7, "prob": "1.0"},
    ],
)
def test_malformed_authoritative_joint_rows_are_visible_conflicts(
    bad_row: dict,
) -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {"q6": [bad_row]},
            "quality_lock": {
                "q6": {"strength": "exact", "reason": "exact_lock"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    assert view.status == "conflict"
    assert view.quality("q6").status == "conflict"
    assert view.quality("q6").candidates == ()


@pytest.mark.parametrize(
    ("ref_status", "expected_status"),
    [
        ("no_reachable", "no_reachable"),
        ("stale", "stale"),
        ("error", "error"),
        ("unavailable", "unavailable"),
    ],
)
def test_upstream_non_ready_statuses_never_become_ready(
    ref_status: str,
    expected_status: str,
) -> None:
    payload = _quality_composition_payload({"status": ref_status})
    view = detail_composition_view({"quality_composition": payload})
    assert view.status == expected_status
    assert all(not quality.candidates for quality in view.qualities)


def test_zero_count_zero_cells_is_a_valid_exact_joint_candidate() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 0, "cells": 0, "prob": 1.0}],
            },
            "quality_lock": {
                "q6": {"strength": "exact", "reason": "exact_lock"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    red = view.quality("q6")
    assert view.status == "ready"
    assert red.status == "exact"
    assert [(row.count, row.cells) for row in red.candidates] == [(0, 0)]


def test_positive_other_mass_never_renders_as_exact_lock() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 3, "cells": 7, "prob": 0.9999995}],
            },
            "quality_lock": {
                "q6": {"strength": "exact", "reason": "exact_lock"},
            },
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    red = view.quality("q6")

    assert view.status == "ready"
    assert red.status == "ready"
    assert red.lock_strength == "probabilistic"
    assert red.lock_reason == "exact_lock_has_other_mass"
    assert red.shown_probability == pytest.approx(0.9999995)
    assert red.other_probability == pytest.approx(0.0000005)
    assert red.truncated is True
