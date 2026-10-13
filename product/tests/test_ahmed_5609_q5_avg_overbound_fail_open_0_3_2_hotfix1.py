"""Fail open when Ahmed 5609's hard gold mean still permits >6 items.

The qualified live export settled at q5=12/q6=0.  Before settlement, the
4.1666665 cells/item wire permitted q5 counts 6/12/18/24, while the posterior
published q5=6 as an exact lock and later manufactured q6=6 from the residual.
Hotfix1 suppresses that incomplete derived result; full >5 composition support
remains a later release item.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402


ROLLBACK = engine.AHMED_Q5_AVG_OVERBOUND_FAIL_OPEN_DISABLE
NOTE = engine.AHMED_Q5_AVG_OVERBOUND_FAIL_OPEN_NOTE
Q5_AVG = 4.166666507720947
CHANGED_ROUTE_MAP_ID = 5609


def _assert_changed_route_no_quote(result: dict) -> None:
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert result["quality_count_ranges"] == {}
    assert result["quality_cells_ranges"] == {}
    assert result["notes"] == [
        f"{engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:"
        f"{CHANGED_ROUTE_MAP_ID}:authority_input_unavailable"
    ]


def _snapshot(stage: str = "seq9") -> dict:
    avg_cells = {"q5": Q5_AVG}
    quality_cells: dict[str, int] = {}
    if stage in {"seq14", "seq17", "seq19"}:
        avg_cells["q1"] = 1.5555555820465088
    if stage in {"seq17", "seq19"}:
        avg_cells["q4"] = 2.200000047683716
    if stage == "seq19":
        quality_cells["q4"] = 11
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 5609,
                "phase": "bidding",
                "round": 3,
            },
        },
        "structured_ref_inputs": {
            "total_count": 38,
            "counts": {"q3": 12},
            "avg_cells": avg_cells,
            "quality_cells": quality_cells,
        },
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 1002041, "result": Q5_AVG},
        ],
    }


def _run(snapshot: dict, *, max_combos: int) -> dict:
    return engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()


@pytest.mark.parametrize(
    ("stage", "expected_support"),
    (("seq9", {6, 12, 18, 24}), ("seq19", {6, 12, 18})),
)
def test_hard_wire_retains_overbound_physical_count_arms(
    stage: str,
    expected_support: set[int],
) -> None:
    evidence = engine.extract_evidence(_snapshot(stage))
    ceiling = engine._physical_count_ceiling(38, "q5", evidence)
    support = {
        count
        for count in range(1, ceiling + 1)
        if engine._avg_grid_options_for_evidence(count, Q5_AVG, "q5", evidence)
    }

    assert support == expected_support
    assert 12 in support
    assert max(support) > 6


@pytest.mark.parametrize("stage", ("seq9", "seq14", "seq17", "seq19"))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_changed_route_real_prefix_is_no_quote_before_gt5_fallback(
    stage: str,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.COMPLETE_GT5_JOINT_SUPPORT_DISABLE, "1")
    result = _run(_snapshot(stage), max_combos=max_combos)

    _assert_changed_route_no_quote(result)
    assert NOTE not in result["notes"]


GT5_Q5_DOMAINS = {
    "seq9": (6, 12, 18, 24),
    "seq14": (6, 12, 18, 24),
    "seq17": (6, 12, 18, 24),
    "seq19": (6, 12, 18),
}


@pytest.mark.parametrize("stage", tuple(GT5_Q5_DOMAINS))
def test_route_specific_overbound_helper_and_named_rollback_keep_physical_domain(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = engine.extract_evidence(_snapshot(stage))
    monkeypatch.delenv(ROLLBACK, raising=False)

    assert engine._ahmed_q5_avg_overbound_fail_open_enabled(  # noqa: SLF001
        evidence,
        published_count_range=(6, 6, 6),
    ) is True
    assert engine._complete_gt5_physical_count_domains(evidence)["q5"] == (  # noqa: SLF001
        GT5_Q5_DOMAINS[stage]
    )

    monkeypatch.setenv(ROLLBACK, "1")
    assert engine._ahmed_q5_avg_overbound_fail_open_enabled(  # noqa: SLF001
        evidence,
        published_count_range=(6, 6, 6),
    ) is False
    assert engine._complete_gt5_physical_count_domains(evidence) == {}  # noqa: SLF001


@pytest.mark.parametrize("stage", tuple(GT5_Q5_DOMAINS))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_changed_route_named_rollback_cannot_restore_legacy_quote(
    stage: str,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROLLBACK, "1")
    result = _run(_snapshot(stage), max_combos=max_combos)

    _assert_changed_route_no_quote(result)
    assert NOTE not in result["notes"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("hero", "aisha"),
        ("map_id", 5611),
        ("phase", "manual"),
        ("q5_count", 12),
        ("q5_cells", 50),
        ("q5_value", 443_931),
        ("q6_count", 0),
        ("q6_cells", 0),
    ),
)
def test_non_target_or_direct_sibling_evidence_is_a_rollback_noop(
    mutation: str,
    value: str | int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot("seq19")
    context = snapshot["ui_contract"]["context"]
    structured = snapshot["structured_ref_inputs"]
    if mutation in {"hero", "map_id", "phase"}:
        context[mutation] = value
    elif mutation == "q5_count":
        structured["counts"]["q5"] = value
    elif mutation == "q5_cells":
        structured["quality_cells"]["q5"] = value
    elif mutation == "q5_value":
        structured.setdefault("quality_values", {})["q5"] = value
    elif mutation == "q6_count":
        structured["counts"]["q6"] = value
    else:
        structured["quality_cells"]["q6"] = value

    current = _run(snapshot, max_combos=50_000)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot, max_combos=50_000)

    assert current == rollback
    assert NOTE not in current["notes"]


def test_changed_route_panel_api_and_tk_projection_share_no_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.COMPLETE_GT5_JOINT_SUPPORT_DISABLE, "1")
    monkeypatch.setattr(panel, "hero_max_combos_for_round", lambda *args, **kwargs: 50_000)
    snapshot = _snapshot("seq19")
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["ahmed_ref"]["status"] == "no_reachable_combo"
    assert summary["ahmed_ref"]["balanced"] is None
    assert summary["reference"]["readiness"] == "no_reachable_combo"
    assert summary["reference"]["source"] == "ref_waiting"
    assert summary["reference"]["decision_range"] == "-"
    assert summary["red"]["count_range"] == "-"
    assert summary["red"]["cells_range"] == "-"
    assert summary["quality_composition"]["status"] == "unavailable"

    tk_row = overlay._summary_diagnostic_row(
        summary,
        snapshot_path=tmp_path / "latest_snapshot.json",
        render_mode="test",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert tk_row["reference"]["readiness"] == "no_reachable_combo"
    assert tk_row["reference"]["decision_range"] == "-"
    assert tk_row["red"]["count_range"] == "-"
    assert tk_row["red"]["cells_range"] == "-"


def test_optional_live_export_qualifies_the_wire_and_settlement_truth() -> None:
    export = (
        Path.home()
        / "Downloads"
        / "HeroRefDiag-20260809-153834-5609_1902361416120617"
    )
    if not export.is_dir():
        pytest.skip("author live export is not present on this machine")

    expected = {
        "BUILD_EXPORT_MANIFEST.json": (
            8_090,
            "92c8273ad0c407f7e34524ef6d987cbb345bf14e33ca54ace430587ab54ffdce",
        ),
        "hero_ref_current_summary.json": (
            42_109,
            "3613ad0f380ac00c9edf18aee897503c855ca578c8be83a7130e7d3bb6d0f3ca",
        ),
        "latest_snapshot.json": (
            187_362,
            "b037a5a7acdff4cd7639e730044e55cd1846ee7d7de623f8780d58364641b20f",
        ),
    }
    for name, (size, sha256) in expected.items():
        payload = (export / name).read_bytes()
        assert len(payload) == size
        assert hashlib.sha256(payload).hexdigest() == sha256

    manifest = json.loads((export / "BUILD_EXPORT_MANIFEST.json").read_text("utf-8"))
    summary = json.loads((export / "hero_ref_current_summary.json").read_text("utf-8"))
    snapshot = json.loads((export / "latest_snapshot.json").read_text("utf-8"))
    assert manifest["package"]["privacy_mode"] == "support-safe"
    assert manifest["package"]["contains_unredacted_raw"] is False
    assert manifest["context"]["warehouse_source"]["parser_replay_eligible"] is True
    assert manifest["context"]["hero"] == "ahmed"
    assert manifest["context"]["map_id"] == 5609

    truth = summary["truth"]
    assert truth["complete"] is True
    assert [truth[key] for key in ("total_items", "total_cells", "total_value")] == [
        38,
        114,
        509_722,
    ]
    qualities = summary["quality_composition"]["qualities"]
    assert qualities["q5"]["candidates"] == [
        {"count": 12, "cells": 50, "probability": 1.0},
    ]
    assert qualities["q6"]["candidates"] == [
        {"count": 0, "cells": 0, "probability": 1.0},
    ]
    q5_updates = [
        row
        for row in snapshot["structured_ref_inputs"]["field_updates"]
        if row.get("sequence") == 9
        and "['bucket', '5', 'avg_cells']" in str(row.get("path"))
    ]
    assert len(q5_updates) == 1
    assert q5_updates[0]["value"] == Q5_AVG
    assert q5_updates[0]["confidence"] == "exact"
