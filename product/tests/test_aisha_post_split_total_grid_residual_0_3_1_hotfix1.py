"""Aisha low-tier split must close before the hard total-grid residual pass.

Truth-free fixtures are reduced from sessions 2521:9000000000000042 and
2530:9000000000000043.  Settlement fields are deliberately absent from the
bidding fixtures: the regression is about evidence ordering, not truth replay.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_DISABLE,
    AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE,
    AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_DISABLE,
    AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_NOTE,
    AISHA_SOFT_TOTAL_Q6_JOINT_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _remaining_cells_range  # noqa: E402


QUALIFICATION = "postupdate_unknown_warehouse_source"


def _warehouse_source(qualification: str = QUALIFICATION) -> dict:
    official = qualification == "official_random_warehouse"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "normal" if official else "advisory_only_no_clamp",
    }


def _skill_rows(*skill_counts: tuple[int, int]) -> list[dict]:
    return [
        {
            "sort": index + 1,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for index, (skill_id, count) in enumerate(skill_counts)
    ]


def _snapshot(
    *,
    session_id: str,
    map_id: int,
    structured: dict,
    skill_rows: list[dict] | None = None,
    hero: str = "aisha",
    phase: str = "bidding",
    qualification: str = QUALIFICATION,
) -> dict:
    source = _warehouse_source(qualification)
    return {
        "hero": hero,
        "map_id": map_id,
        "round": 4,
        "action_round": 4,
        "phase": phase,
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            **copy.deepcopy(structured),
        },
        "skill_reveal_rows": copy.deepcopy(skill_rows or []),
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "round": 4,
                "phase": phase,
                "session_id": session_id,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": 1.0},
        },
    }


def _snapshot_2521() -> dict:
    return _snapshot(
        session_id="2521:9000000000000042",
        map_id=2521,
        structured={
            "total_cells": 111,
            "counts": {"q3": 8, "q4": 15},
            "quality_cells": {"q3": 24, "q4": 49, "q5": 4},
            "split_counts": {"green": 12},
            "split_quality_cells": {"green": 21},
        },
        # Green/blue/purple arrived, so the missing earlier white reveal is an
        # explicit zero rather than an unobserved future frame.
        skill_rows=_skill_rows((1001033, 12), (1001032, 8), (1001031, 15)),
    )


def _snapshot_2530() -> dict:
    snapshot = _snapshot(
        session_id="2530:9000000000000043",
        map_id=2530,
        structured={
            "total_cells": 133,
            "counts": {"q3": 10, "q4": 8},
            "quality_cells": {"q3": 20, "q4": 18, "q5": 23},
            "split_counts": {"white": 1, "green": 11},
            "split_quality_cells": {"white": 1, "green": 19},
        },
        skill_rows=_skill_rows(
            (1001034, 1),
            (1001033, 11),
            (1001032, 10),
            (1001031, 8),
        ),
    )
    # One observed red item is enough to reproduce the historical blank once
    # q1 cells were merged after the old residual pass.
    snapshot["minimap_grid_items"] = [
        {
            "quality": 6,
            "item_id": 1046002,
            "cells": 6,
            "shape_key": "23",
            "local_index": 122,
            "row": 13,
            "col": 3,
            "value": 199080,
            "category": 104,
        }
    ]
    return snapshot


def _snapshot_prior_2521_absent_white() -> dict:
    """Previously protected absent-white sample 2521:9000000000000041."""
    return _snapshot(
        session_id="2521:9000000000000041",
        map_id=2521,
        structured={
            "total_cells": 214,
            "counts": {"q3": 13, "q4": 16},
            "quality_cells": {"q3": 43, "q4": 61, "q5": 26},
            "split_counts": {"green": 12},
            "split_quality_cells": {"green": 30},
        },
        skill_rows=_skill_rows((1001033, 12), (1001032, 13), (1001031, 16)),
    )


def _run(snapshot: dict, *, max_combos: int = 8000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_DISABLE, "1")


@pytest.mark.parametrize("max_combos", [8000, 50000])
def test_2521_absent_white_closes_q6_cells_without_high_count_tail(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2521()
    current = _run(snapshot, max_combos=max_combos)
    _rollback(monkeypatch)
    rollback = _run(snapshot, max_combos=max_combos)

    assert current["status"] == rollback["status"] == "count_prior"
    assert current["evidence"]["quality_cells"]["q1"] == 21
    assert current["evidence"]["quality_cells"]["q6"] == 13
    assert current["quality_cells_ranges"]["q6"] == [13, 13, 13]
    assert current["quality_count_ranges"]["q6"][2] < 10
    assert current["aggressive"] < 2_226_647
    assert rollback["evidence"]["quality_cells"].get("q1") is None
    assert rollback["quality_cells_ranges"]["q6"] != [13, 13, 13]
    assert current["notes"].count(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE) == 1
    assert "aisha_absent_white_cells_inferred_zero" in current["notes"]


@pytest.mark.parametrize("max_combos", [8000, 50000])
def test_2530_merged_low_tier_cells_prevent_blank_and_close_q6_cells(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_DISABLE, raising=False)
    monkeypatch.delenv(AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_DISABLE, raising=False)
    snapshot = _snapshot_2530()
    current = _run(snapshot, max_combos=max_combos)
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=Path("missing-2530-post-split-snapshot.json"),
    )
    _rollback(monkeypatch)
    rollback = _run(snapshot, max_combos=max_combos)

    assert current["status"] == "count_prior"
    assert current["conservative"] is not None
    assert current["evidence"]["quality_cells"]["q1"] == 20
    assert current["evidence"]["quality_cells"]["q6"] == 52
    assert current["quality_cells_ranges"]["q6"] == [52, 52, 52]
    assert rollback["status"] == "count_prior"
    assert rollback["evidence"]["quality_cells"].get("q6") is None
    assert current["quality_count_ranges"]["q6"] == [3, 5, 7]
    assert rollback["quality_count_ranges"]["q6"] == [5, 7, 7]
    current_q6_counts = {
        int(row["count"])
        for row in current["quality_count_cells_candidates"]["q6"]
    }
    rollback_q6_counts = {
        int(row["count"])
        for row in rollback["quality_count_cells_candidates"]["q6"]
    }
    assert 8 in current_q6_counts
    assert 8 not in rollback_q6_counts
    assert current["quality_lock"] == rollback["quality_lock"]
    assert current["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert [current[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_144_416,
        2_228_454,
        2_573_460,
    ]
    assert [rollback[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_501_321,
        1_950_639,
        2_089_773,
    ]
    assert current["notes"].count(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE) == 1
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in rollback["notes"]
    assert current["notes"].count(AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_NOTE) == 1
    assert AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_NOTE not in rollback["notes"]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in current["notes"]
    assert AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in rollback["notes"]
    assert current["evidence"]["phase"] == rollback["evidence"]["phase"] == "bidding"
    assert summary["context"]["warehouse_source_qualification"] == QUALIFICATION
    assert summary["context"]["warehouse_source"] == _warehouse_source()
    assert [
        summary["ahmed_ref"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [1_144_416, 2_228_454, 2_573_460]


def test_absent_green_uses_zero_cells_after_later_skill() -> None:
    snapshot = _snapshot(
        session_id="4501:post-split-green-zero",
        map_id=4501,
        structured={
            "total_cells": 92,
            "counts": {"q3": 11, "q4": 8},
            "quality_cells": {"q3": 21, "q4": 22, "q5": 10},
            "split_counts": {"white": 15},
            "split_quality_cells": {"white": 28},
        },
        skill_rows=_skill_rows((1001034, 15), (1001032, 11)),
    )

    result = _run(snapshot)

    assert result["evidence"]["split_counts"] == {"white": 15, "green": 0}
    assert result["evidence"]["quality_cells"]["q1"] == 28
    assert result["evidence"]["quality_cells"]["q6"] == 11
    assert "aisha_absent_green_cells_inferred_zero" in result["notes"]
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE in result["notes"]


def test_prior_2521_absent_white_sample_keeps_exact_residual_cells() -> None:
    result = _run(_snapshot_prior_2521_absent_white())

    assert result["evidence"]["quality_cells"]["q1"] == 30
    assert result["evidence"]["quality_cells"]["q6"] == 54
    assert result["quality_cells_ranges"]["q6"] == [54, 54, 54]
    assert "aisha_absent_white_cells_inferred_zero" in result["notes"]
    assert result["notes"].count(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE) == 1


@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ],
)
def test_source_pool_label_is_not_changed_by_physical_closure(
    qualification: str,
) -> None:
    snapshot = _snapshot_2530()
    source = _warehouse_source(qualification)
    snapshot["warehouse_source"] = copy.deepcopy(source)
    snapshot["warehouse_source_qualification"] = qualification
    snapshot["official_distribution_eligible"] = source[
        "official_distribution_eligible"
    ]
    snapshot["ui_contract"]["context"]["warehouse_source"] = copy.deepcopy(source)
    snapshot["ui_contract"]["context"]["warehouse_source_qualification"] = (
        qualification
    )
    snapshot["ui_contract"]["context"]["official_distribution_eligible"] = source[
        "official_distribution_eligible"
    ]

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=Path("missing-source-pool-snapshot.json"),
    )

    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] is source[
        "official_distribution_eligible"
    ]
    assert summary["ahmed_ref"]["quality_cells_ranges"]["q6"] == [52, 52, 52]


def test_no_split_no_hard_total_and_other_hero_are_rollback_noops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = []
    no_split = _snapshot_2530()
    no_split["structured_ref_inputs"].pop("split_counts")
    no_split["structured_ref_inputs"].pop("split_quality_cells")
    cases.append(no_split)
    no_total = _snapshot_2530()
    no_total["structured_ref_inputs"].pop("total_cells")
    cases.append(no_total)
    other_hero = _snapshot_2530()
    other_hero["hero"] = "ahmed"
    other_hero["structured_ref_inputs"]["hero"] = "ahmed"
    other_hero["ui_contract"]["context"]["hero"] = "ahmed"
    cases.append(other_hero)

    default = [_run(case) for case in cases]
    _rollback(monkeypatch)
    rollback = [_run(case) for case in cases]

    assert default == rollback
    assert all(
        AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in result["notes"]
        for result in default
    )


def test_partial_and_conflicting_split_fail_closed_without_post_closure() -> None:
    partial = _snapshot_2521()
    partial["skill_reveal_rows"] = []
    partial_result = _run(partial)
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in partial_result["notes"]
    assert partial_result["evidence"]["quality_cells"].get("q6") is None

    conflict = _snapshot_2521()
    conflict["structured_ref_inputs"]["split_quality_cells"]["white"] = 1
    conflict_result = _run(conflict)
    assert conflict_result["status"] == "no_reachable_combo"
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in conflict_result["notes"]
    assert any(
        str(note).startswith("hard_conflict:split_low_quality")
        for note in conflict_result["notes"]
    )


def test_conflicting_public_exact_total_skips_post_split_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2530()
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = [
        {
            "semantic": "total_cells",
            "kind": "total_cells",
            "value": 999,
            "display_value": "999",
        }
    ]

    current = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert current == rollback
    assert current["evidence"]["quality_cells"].get("q6") is None
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in current["notes"]
    assert any("conflict" in str(note) for note in current["notes"])


def test_existing_direct_q6_and_zero_q6_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = _snapshot_2530()
    direct["structured_ref_inputs"]["quality_cells"]["q6"] = 52
    current_direct = _run(direct)
    _rollback(monkeypatch)
    rollback_direct = _run(direct)
    assert current_direct == rollback_direct
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in current_direct["notes"]

    monkeypatch.delenv(AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_DISABLE, raising=False)
    zero = _snapshot_2530()
    zero["structured_ref_inputs"]["total_cells"] = 81
    zero["structured_ref_inputs"]["counts"]["q6"] = 0
    zero["structured_ref_inputs"]["quality_cells"]["q6"] = 0
    zero.pop("minimap_grid_items")
    current_zero = _run(zero)
    _rollback(monkeypatch)
    rollback_zero = _run(zero)
    assert current_zero == rollback_zero
    assert current_zero["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert current_zero["quality_cells_ranges"]["q6"] == [0, 0, 0]


def test_settlement_truth_remains_exact_and_default_equals_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2521()
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 41,
        "total_cells": 111,
        "total_value": 985153,
    }
    snapshot["final_quality_counts"] = "q1=0;q2=12;q3=8;q4=15;q5=1;q6=5"
    snapshot["final_quality_cells"] = "q1=0;q2=21;q3=24;q4=49;q5=4;q6=13"
    snapshot["final_quality_values"] = "q1=0;q2=0;q3=0;q4=0;q5=39600;q6=827808"

    current = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert current == rollback
    assert current["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert current["quality_cells_ranges"]["q6"] == [13, 13, 13]
    assert AISHA_POST_SPLIT_TOTAL_GRID_RESIDUAL_NOTE not in current["notes"]


def test_summary_api_and_tk_consume_closed_q6_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2530()
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]
    assert ref["quality_cells_ranges"]["q6"] == [52, 52, 52]
    assert _remaining_cells_range(ref) == (52, 52, 52)

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ahmed_ref"]["quality_cells_ranges"]["q6"] == [52, 52, 52]
    assert payload["context"]["warehouse_source_qualification"] == QUALIFICATION
