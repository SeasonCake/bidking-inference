from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
for _path in (
    ROOT / "scripts",
    ROOT / "src",
    AHMAD_SRC,
    ROOT / "hero_ref" / "tools",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402


REAL_4546_CAPTURE = (
    ROOT
    / "data"
    / "logs"
    / "live"
    / "raw"
    / "archive"
    / "reset"
    / "npcap_live_2026-07-20_212026_4546_1741283047653555_reset.json"
)


def _reduced_4546_snapshot(*, include_q5_value: bool = True) -> dict:
    structured = {
        "total_count": 32,
        "total_cells": 82,
        "counts": {"q1": 12, "q3": 7, "q4": 9},
        "quality_cells": {"q1": 20, "q3": 30, "q4": 16},
    }
    if include_q5_value:
        structured["quality_values"] = {"q5": 29_025}
    return {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 4546, "phase": "bidding", "round": 4},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": structured,
    }


@pytest.fixture
def q5_catalog_29025(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tiny deterministic catalog with the same three joint states as the real validated catalog:
    # one 1-cell item, one 4-cell item, or two 1-cell items whose values sum to 29,025.
    pairs = ((14_500, 1), (14_525, 1), (29_025, 1), (29_025, 4))
    rows = tuple((index, value, cells) for index, (value, cells) in enumerate(pairs, start=1))
    monkeypatch.setattr(engine, "_validated_q5_catalog", lambda: (pairs, rows, "ok"))
    original = engine._validated_map_quality_catalog

    def map_catalog(map_id: int, quality: int):
        if map_id == 4546 and quality == 5:
            return pairs, rows, "ok", "physical-category-v1:q5-29025-test"
        return original(map_id, quality)

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", map_catalog)
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {5: frozenset({14_500, 14_525, 29_025})},
    )


def _q5_joint_rows(result: dict) -> set[tuple[int, int]]:
    return {
        (int(row["count"]), int(row["cells"]))
        for row in result["quality_count_cells_candidates"]["q5"]
    }


def test_4546_q5_exact_value_preserves_legal_joint_states(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_29025: None,
) -> None:
    monkeypatch.delenv(engine.Q5_CATALOG_JOINT_STATE_DISABLE, raising=False)

    result = engine.run_reference_engine(
        _reduced_4546_snapshot(), max_combos=8_000
    ).as_dict()

    assert result["status"] == "ok"
    assert result["evidence"]["quality_count_cells_options"]["q5"] == [
        {"count": 1, "cells": 1},
        {"count": 1, "cells": 4},
        {"count": 2, "cells": 2},
    ]
    assert result["quality_count_ranges"]["q5"] == [1, 1, 2]
    assert _q5_joint_rows(result) == {(1, 1), (1, 4), (2, 2)}
    assert result["quality_cells_ranges"]["q5"] == [1, 2, 4]
    assert f"{engine.Q5_CATALOG_JOINT_STATE_NOTE}:3" in result["notes"]


def test_4546_q5_joint_constraint_named_rollback_restores_legacy_geometry(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_29025: None,
) -> None:
    monkeypatch.setenv(engine.Q5_CATALOG_JOINT_STATE_DISABLE, "1")

    result = engine.run_reference_engine(
        _reduced_4546_snapshot(), max_combos=8_000
    ).as_dict()

    assert result["evidence"]["quality_count_cells_options"] == {}
    assert _q5_joint_rows(result) != {(1, 1), (1, 4), (2, 2)}
    assert (2, 8) in _q5_joint_rows(result)
    assert not any(
        str(note).startswith(engine.Q5_CATALOG_JOINT_STATE_NOTE)
        for note in result["notes"]
    )


def test_q5_joint_constraint_is_noop_without_exact_value_signal(
    monkeypatch: pytest.MonkeyPatch,
    q5_catalog_29025: None,
) -> None:
    snapshot = _reduced_4546_snapshot(include_q5_value=False)
    monkeypatch.delenv(engine.Q5_CATALOG_JOINT_STATE_DISABLE, raising=False)
    default = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(engine.Q5_CATALOG_JOINT_STATE_DISABLE, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    for field in (
        "combo_count",
        "conservative",
        "balanced",
        "aggressive",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_cells_candidates",
    ):
        assert default[field] == rollback[field]


def test_q5_joint_constraint_fails_open_when_catalog_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(engine.Q5_CATALOG_JOINT_STATE_DISABLE, raising=False)
    monkeypatch.setattr(
        engine,
        "_validated_q5_catalog",
        lambda: ((), (), "catalog_unavailable"),
    )

    evidence = engine.extract_evidence(_reduced_4546_snapshot())

    assert evidence.quality_count_cells_options == {}
    assert not any(
        str(note).startswith(engine.Q5_CATALOG_JOINT_STATE_NOTE)
        for note in evidence.source_notes
    )


def test_real_4546_r4_scheduler_and_ui_keep_q5_joint_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-only raw replay: source -> actual Aisha scheduler -> API/UI joint states."""
    if not REAL_4546_CAPTURE.is_file():
        pytest.skip(
            f"missing gitignored real replay {REAL_4546_CAPTURE.name} — local-only gate"
        )

    # Focused replay must not inherit another terminal/window's experiment arm.
    for name in tuple(engine.os.environ):
        if name.startswith(("BIDKING_", "AHMAD_", "AISHA_", "HERO_")):
            monkeypatch.delenv(name, raising=False)

    from ahmad_live_panel_server import (  # noqa: E402
        _aisha_dual_pass_ref_result,
        _aisha_pass_max_combos,
        _purple_gold_quality_summary,
        _quality_probability_hover,
        _quality_probability_summary,
    )
    from audit_aisha_gap import (  # noqa: E402
        _build_snapshot,
        _events_through_sort,
        _map_id_from_events,
        _settlement_breakdown,
    )
    from bidking_lab.live.fatbeans import (  # noqa: E402
        live_batches_from_fatbeans_events,
        parse_fatbeans_capture,
    )

    events = parse_fatbeans_capture(REAL_4546_CAPTURE)
    truth = _settlement_breakdown(events)
    assert truth["counts"][5] == 1
    assert truth["cells"][5] == 1

    prefix_events = _events_through_sort(events, 25)
    prefix_batches = [
        batch
        for batch in live_batches_from_fatbeans_events(prefix_events)
        if batch.phase != "settled"
    ]
    snapshot = _build_snapshot(
        hero="aisha",
        events=prefix_events,
        prefix_batches=prefix_batches,
        map_id=_map_id_from_events(events),
    )
    context = snapshot["ui_contract"]["context"]
    assert context["round"] == 4
    assert _aisha_pass_max_combos(
        4,
        engine.prepare_reference_engine_snapshot(snapshot),
    ) == 8_000

    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    result, timing = _aisha_dual_pass_ref_result(
        snapshot,
        round_no=4,
        public_info=public_info,
    )

    assert "aisha_quote_pass:item" in result["notes"]
    assert timing["skill"] is not None
    assert timing["item"] is not None
    assert result["quality_count_ranges"]["q5"] == [1, 1, 2]
    assert result["quality_cells_ranges"]["q5"] == [1, 2, 4]
    assert _q5_joint_rows(result) == {(1, 1), (1, 4), (2, 2)}

    assert _purple_gold_quality_summary(result) == "紫9件/16格 · 金件 1/1/2 1/2/4格"
    assert _quality_probability_summary(result).startswith("金2件2格")
    hover = _quality_probability_hover(result)
    assert "金品：2件/2格" in hover
    assert "1件/1格" in hover
    assert "1件/4格" in hover
    assert "2件/8格" not in hover
