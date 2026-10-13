from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402


def _snapshot(
    actions: list[dict],
    *,
    hero: str = "ahmed",
    public_info: dict | None = None,
    public_rows: list[dict] | None = None,
) -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2404,
                "phase": "bidding",
                "round": 4,
            },
            "actions": {"results": actions},
            "constraints": {"public_info": public_info or {}},
        },
        "action_result_rows": actions,
        "public_info_rows": public_rows or [],
        "structured_ref_inputs": {"total_count": 8},
    }


def _q4_options(snapshot: dict) -> tuple[tuple[int, int], ...]:
    evidence = engine.extract_evidence(snapshot)
    return (evidence.quality_count_cells_options or {}).get("q4", ())


@pytest.mark.parametrize(
    ("hero", "avg_action"),
    [("ahmed", 1002042), ("aisha", 100112)],
)
def test_q4_hard_avg_and_total_value_preserve_joint_states_for_any_hero(
    hero: str,
    avg_action: int,
) -> None:
    snapshot = _snapshot(
        [
            {"action_id": avg_action, "result": 2.0},
            {"action_id": 100124, "result": 10_800},
        ],
        hero=hero,
    )

    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q4") is None
    assert evidence.quality_cells.get("q4") is None
    assert _q4_options(snapshot) == ((2, 4), (3, 6))
    assert f"{engine.Q4_EXACT_VALUE_CELLS_NOTE}:ok:2" in evidence.source_notes


def test_q4_count_and_total_value_keep_reachable_cell_states_together() -> None:
    evidence = engine.extract_evidence(
        _snapshot(
            [
                {"action_id": 100118, "result": 2},
                {"action_id": 100124, "result": 10_800},
            ],
            hero="aisha",
        )
    )

    assert evidence.fixed_counts["q4"] == 2
    assert evidence.quality_cells.get("q4") is None
    assert evidence.quality_count_cells_options["q4"] == (
        (2, 3),
        (2, 4),
        (2, 5),
    )


def test_q4_scan_and_total_value_keep_reachable_count_states_together() -> None:
    evidence = engine.extract_evidence(
        _snapshot(
            [
                {"action_id": 100106, "result": 4},
                {"action_id": 100124, "result": 10_800},
            ],
            hero="aisha",
        )
    )

    assert evidence.fixed_counts.get("q4") is None
    assert evidence.quality_cells["q4"] == 4
    assert evidence.quality_count_cells_options["q4"] == (
        (2, 4),
        (3, 4),
        (4, 4),
    )


def test_q4_count_scan_and_total_value_intersect_to_one_joint_state() -> None:
    evidence = engine.extract_evidence(
        _snapshot(
            [
                {"action_id": 100118, "result": 2},
                {"action_id": 100106, "result": 4},
                {"action_id": 100124, "result": 10_800},
            ],
            hero="aisha",
        )
    )

    assert evidence.fixed_counts["q4"] == 2
    assert evidence.quality_cells["q4"] == 4
    assert evidence.quality_count_cells_options["q4"] == ((2, 4),)


def test_public_q4_avg_value_and_exact_count_derive_total_and_joint_cells() -> None:
    evidence = engine.extract_evidence(
        _snapshot(
            [{"action_id": 100118, "result": 2}],
            hero="aisha",
            public_info={
                "public_avg_values": [
                    {
                        "semantic": "q4_avg_value",
                        "kind": "avg_value",
                        "quality": 4,
                        "value": 5_400.0,
                    }
                ]
            },
        )
    )

    assert evidence.fixed_counts["q4"] == 2
    assert evidence.quality_values["q4"] == 10_800
    assert evidence.quality_count_cells_options["q4"] == (
        (2, 3),
        (2, 4),
        (2, 5),
    )


def test_public_sampled_q4_item_constrains_only_its_catalog_residual() -> None:
    snapshot = _snapshot(
        [
            {"action_id": 1002042, "result": 2.0},
            {"action_id": 100124, "result": 10_800},
        ],
        public_rows=[
            {
                "info_id": 200028,
                "revealed_items_detail": [
                    {
                        "runtime_id": 7,
                        "local_index": 34,
                        "quality": 4,
                        "cells": 2,
                        "value": 4_788,
                    }
                ],
            }
        ],
    )

    evidence = engine.extract_evidence(snapshot)

    assert evidence.q4_known_item_facts == (("local:34", 2, 4_788),)
    assert evidence.fixed_counts["q4"] == 2
    assert evidence.quality_cells["q4"] == 4
    assert evidence.quality_count_cells_options["q4"] == ((2, 4),)


def test_disjoint_q4_cell_and_value_rows_never_form_a_physical_item() -> None:
    actions = [
        {"action_id": 1002042, "result": 2.0},
        {"action_id": 100124, "result": 10_800},
        {
            "action_id": 999001,
            "revealed_items_detail": [
                {"runtime_id": 1, "quality": 4, "cells": 2},
                {"runtime_id": 2, "quality": 4, "value": 4_788},
            ],
        },
    ]

    evidence = engine.extract_evidence(_snapshot(actions))

    assert evidence.q4_known_item_facts == ()
    assert evidence.quality_count_cells_options["q4"] == ((2, 4), (3, 6))


def test_lone_q4_total_and_public_soft_avg_cells_do_not_create_hard_joint_lane() -> None:
    total_only = engine.extract_evidence(
        _snapshot([{"action_id": 100124, "result": 10_800}], hero="raven")
    )
    soft_public_avg = engine.extract_evidence(
        _snapshot(
            [{"action_id": 100124, "result": 10_800}],
            hero="aisha",
            public_info={
                "public_numeric_facts": [
                    {"semantic": "q4_avg_cells", "value": 2.0}
                ]
            },
        )
    )

    for evidence in (total_only, soft_public_avg):
        assert "q4" not in (evidence.quality_count_cells_options or {})
        assert not any(
            str(note).startswith(engine.Q4_EXACT_VALUE_CELLS_NOTE)
            for note in evidence.source_notes
        )


def test_q4_named_rollback_restores_pre_projection_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.Q4_EXACT_VALUE_CELLS_DISABLE, "1")
    snapshot = _snapshot(
        [
            {"action_id": 1002042, "result": 2.0},
            {"action_id": 100124, "result": 10_800},
        ]
    )

    evidence = engine.extract_evidence(snapshot)

    assert "q4" not in (evidence.quality_count_cells_options or {})
    assert not any(
        str(note).startswith(engine.Q4_EXACT_VALUE_CELLS_NOTE)
        for note in evidence.source_notes
    )


def test_q4_catalog_mismatch_fails_open_instead_of_using_coarse_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_validated_q4_catalog",
        lambda: ((), (), "catalog_mismatch"),
    )
    snapshot = _snapshot(
        [
            {"action_id": 1002042, "result": 2.0},
            {"action_id": 100124, "result": 10_800},
        ]
    )

    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q4") is None
    assert evidence.quality_cells.get("q4") is None
    assert "q4" not in (evidence.quality_count_cells_options or {})
    assert f"{engine.Q4_EXACT_VALUE_CELLS_NOTE}:catalog_mismatch:0" in evidence.source_notes


def test_product_result_exposes_only_q4_catalog_joint_candidates() -> None:
    from ahmad_live_panel_server import _purple_gold_quality_summary

    result = engine.run_reference_engine(
        _snapshot(
            [
                {"action_id": 1002042, "result": 2.0},
                {"action_id": 100124, "result": 10_800},
            ]
        ),
        max_combos=5_000,
    ).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["evidence"]["quality_count_cells_options"]["q4"] == [
        {"count": 2, "cells": 4},
        {"count": 3, "cells": 6},
    ]
    assert result["quality_count_ranges"]["q4"] == [2, 2, 3]
    assert result["quality_cells_ranges"]["q4"] == [4, 4, 6]
    assert {
        (row["count"], row["cells"])
        for row in result["quality_count_cells_candidates"]["q4"]
    } == {(2, 4), (3, 6)}
    assert _purple_gold_quality_summary(result).startswith("紫件 2/2/3 约4/4/6格")
