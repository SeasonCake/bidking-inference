from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import (
    build_monitor_artifact_from_events,
    load_monitor_tables,
)
from bidking_lab.live.sample_upload import trim_artifact
from bidking_lab.runtime import ui_contract_from_artifact


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_match_records  # noqa: E402
import build_unified_sample_catalog  # noqa: E402
import public_stats  # noqa: E402
import refresh_sample_pool  # noqa: E402
import run_live_overlay  # noqa: E402
import sample_collector_server  # noqa: E402


CURRENT_CANARIES = (
    1_916_039,
    1_916_040,
    1_916_045,
    1_916_048,
    1_916_051,
    1_916_054,
    1_916_055,
    1_916_059,
)
CURRENT_CANARY_VALUE = 3_177_600
UNKNOWN_ITEM_ID = 9_999_999


def _events(*, include_unknown: bool) -> FatbeansCaptureEvents:
    tables = load_monitor_tables()
    inventory = [
        FatbeansInventoryItem(
            runtime_id=90_000 + index,
            item_id=item_id,
            quality=6,
            cells=tables.items[item_id].shape_w * tables.items[item_id].shape_h,
        )
        for index, item_id in enumerate(CURRENT_CANARIES)
    ]
    if include_unknown:
        inventory.append(
            FatbeansInventoryItem(
                runtime_id=99_999,
                item_id=UNKNOWN_ITEM_ID,
                quality=6,
                cells=1,
            )
        )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="2026-09-03 13:00:00",
                message_id=0x002D,
                session_id=(
                    "5609:catalog-partial"
                    if include_unknown
                    else "5609:catalog-complete"
                ),
                map_id=5609,
                round_index=5,
                inventory_items=tuple(inventory),
            ),
        ),
    )


def _artifact(*, include_unknown: bool) -> dict[str, object]:
    return build_monitor_artifact_from_events(
        _events(include_unknown=include_unknown),
        file="catalog-value.json",
        tables=load_monitor_tables(),
        n_trials=2,
        roi_trials=0,
    )


def test_monitor_and_snapshot_publish_complete_current_canaries() -> None:
    artifact = _artifact(include_unknown=False)

    assert artifact["known_value_sum"] == CURRENT_CANARY_VALUE
    assert artifact["catalog_value_complete"] is True
    assert artifact["catalog_unknown_item_ids"] == []
    assert artifact["catalog_valued_item_count"] == 8
    assert artifact["catalog_settled_item_count"] == 8
    assert artifact["settlement_incomplete"] is False
    assert artifact.get("model_eval_skipped_reason") is None
    truth = artifact["ui_contract"]["truth"]
    assert truth["complete"] is True
    assert truth["partial"] is False
    assert truth["total_value"] == CURRENT_CANARY_VALUE
    assert truth["q6"]["count"] == 8


def test_monitor_unknown_item_keeps_lower_bound_and_blocks_full_truth() -> None:
    artifact = _artifact(include_unknown=True)

    assert artifact["known_value_sum"] == CURRENT_CANARY_VALUE
    assert artifact["catalog_value_complete"] is False
    assert artifact["catalog_unknown_item_ids"] == [UNKNOWN_ITEM_ID]
    assert artifact["catalog_valued_item_count"] == 8
    assert artifact["catalog_settled_item_count"] == 9
    assert artifact["settlement_incomplete"] is False
    assert artifact["model_eval_skipped_reason"] == "catalog_value_incomplete"
    assert "model_eval" not in artifact
    truth = artifact["ui_contract"]["truth"]
    assert truth["complete"] is False
    assert truth["partial"] is True
    assert truth["source"] == "partial_catalog_value"
    assert truth["total_value"] == CURRENT_CANARY_VALUE
    assert truth["catalog_unknown_item_ids"] == [UNKNOWN_ITEM_ID]

    title, headline, detail = run_live_overlay._ui_contract_truth_section(  # noqa: SLF001
        artifact["ui_contract"]
    )
    assert title == "结算/Truth"
    assert headline.startswith("已知价值 3,177,600")
    assert "藏品尚未全部识别，当前显示已知部分" in detail
    assert "partial_catalog_value" not in detail


def _strict_legacy_artifact(**changes: object) -> dict[str, object]:
    artifact: dict[str, object] = {
        "phase": "settled",
        "known_value_sum": 100,
        "inventory_count": 1,
        "inventory_cells": 1,
        "settlement_incomplete": False,
        "final_quality_counts": "q1=1;q2=0;q3=0;q4=0;q5=0;q6=0",
        "final_quality_cells": "q1=1;q2=0;q3=0;q4=0;q5=0;q6=0",
    }
    artifact.update(changes)
    return artifact


def test_runtime_truth_keeps_missing_field_legacy_compatibility() -> None:
    legacy = ui_contract_from_artifact(_strict_legacy_artifact())["truth"]
    explicit_false = ui_contract_from_artifact(
        _strict_legacy_artifact(
            catalog_value_complete=False,
            catalog_unknown_item_ids=[UNKNOWN_ITEM_ID],
            catalog_valued_item_count=1,
            catalog_settled_item_count=2,
        )
    )["truth"]

    assert legacy["complete"] is True
    assert legacy["partial"] is False
    assert legacy["catalog_value_complete"] is None
    assert explicit_false["complete"] is False
    assert explicit_false["partial"] is True
    assert explicit_false["total_value"] == 100


def _upload_artifact(**changes: object) -> dict[str, object]:
    artifact: dict[str, object] = {
        "phase": "settled",
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "final_quality_cells": "q1=1",
        "final_quality_values": "q1=100",
        "inventory_count": 1,
        "inventory_cells": 1,
        "known_value_sum": 100,
        "settlement_incomplete": False,
    }
    artifact.update(changes)
    return artifact


def test_upload_and_collector_preserve_optional_false_and_quarantine_it() -> None:
    artifact = _upload_artifact(
        catalog_value_complete=False,
        catalog_unknown_item_ids=[UNKNOWN_ITEM_ID],
        catalog_valued_item_count=1,
        catalog_settled_item_count=2,
    )
    record = trim_artifact(artifact)
    assert record is not None
    assert record["catalog_value_complete"] is False
    assert record["catalog_unknown_item_ids"] == [UNKNOWN_ITEM_ID]
    assert record["catalog_valued_item_count"] == 1
    assert record["catalog_settled_item_count"] == 2
    assert sample_collector_server._quarantine_reason(record) == (  # noqa: SLF001
        "catalog_value_incomplete"
    )
    assert sample_collector_server._stored_quarantine_reason(record) == (  # noqa: SLF001
        "catalog_value_incomplete"
    )
    assert {
        "catalog_value_complete",
        "catalog_unknown_item_ids",
        "catalog_valued_item_count",
        "catalog_settled_item_count",
    } <= sample_collector_server._ALLOWED  # noqa: SLF001

    legacy = trim_artifact(_upload_artifact())
    assert legacy is not None
    assert "catalog_value_complete" not in legacy
    assert sample_collector_server._quarantine_reason(legacy) is None  # noqa: SLF001


def test_full_truth_consumers_reject_only_explicit_false(tmp_path: Path) -> None:
    base = _upload_artifact(
        client_ts=1_783_000_000.0,
        session_id="2401:catalog-value",
    )
    assert build_unified_sample_catalog._truth_eligible(base) is True  # noqa: SLF001
    assert build_unified_sample_catalog._truth_eligible(  # noqa: SLF001
        {**base, "catalog_value_complete": False}
    ) is False

    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text(
        json.dumps({**base, "catalog_value_complete": False})
        + "\n"
        + json.dumps(base)
        + "\n",
        encoding="utf-8",
    )
    records = build_match_records.build_records(sessions, self_id=None)
    assert len(records) == 1
    assert records[0]["lot_value"] == 100

    legacy_public = {**base, "session_hash": "legacy"}
    partial_public = {
        **legacy_public,
        "session_hash": "partial",
        "catalog_value_complete": False,
    }
    aggregate = public_stats.aggregate_public([partial_public, legacy_public])
    assert sum(row["n"] for row in aggregate["maps"]) == 1
    assert public_stats._usable(legacy_public) is True  # noqa: SLF001
    assert public_stats._usable(partial_public) is False  # noqa: SLF001
    assert public_stats.hero_name("indiana") == "印第安纳"


def test_unified_local_registry_rejects_explicit_false_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "truth_registry.json"
    rows = [
        {
            "file": "fatbeans_truth_catalog_partial.json",
            "verdict": "valid",
            "truth_eligible": True,
            "replay_eligible": False,
            "catalog_value_complete": False,
            "session_id": "partial",
            "map_id": 2401,
        },
        {
            "file": "fatbeans_truth_legacy.json",
            "verdict": "valid",
            "truth_eligible": True,
            "replay_eligible": False,
            "session_id": "legacy",
            "map_id": 2401,
        },
    ]
    registry.write_text(json.dumps({"samples": rows}), encoding="utf-8")
    monkeypatch.setattr(build_unified_sample_catalog, "ROOT", tmp_path)
    monkeypatch.setattr(build_unified_sample_catalog, "TRUTH_REGISTRY", registry)
    monkeypatch.setattr(
        build_unified_sample_catalog,
        "_load_truth_registry",
        lambda: {row["file"]: row for row in rows},
    )
    monkeypatch.setattr(
        build_unified_sample_catalog,
        "_truth_gate_context_sha256",
        lambda: "test-context",
    )

    local, summary = build_unified_sample_catalog._load_local()  # noqa: SLF001

    assert len(local) == 1
    retained = next(iter(local.values()))
    assert [row["file"] for row in retained] == ["fatbeans_truth_legacy.json"]
    assert summary["truth_unique_sessions"] == 1


def test_refresh_screen_rejects_explicit_false_and_keeps_missing_compat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sample.json"
    path.write_text("{}", encoding="utf-8")
    events = object()
    artifact = {
        "settlement_incomplete": False,
        "catalog_value_complete": False,
        "inference_input_constraints": {},
    }
    monkeypatch.setattr(refresh_sample_pool, "parse_fatbeans_capture", lambda _p: events)
    monkeypatch.setattr(refresh_sample_pool, "_hero_of", lambda _e: "aisha")
    monkeypatch.setattr(refresh_sample_pool, "_map_id_from_events", lambda _e: 2401)
    monkeypatch.setattr(refresh_sample_pool, "_capture_date", lambda _p: "2026-09-03")
    monkeypatch.setattr(
        refresh_sample_pool,
        "_settlement_breakdown",
        lambda _e: {
            "counts": {1: 1},
            "cells": {1: 1},
            "total_value": 100,
        },
    )
    monkeypatch.setattr(
        refresh_sample_pool,
        "build_monitor_artifact_from_events",
        lambda _e, tables: dict(artifact),
    )
    monkeypatch.setattr(refresh_sample_pool, "_tables", lambda: object())

    rejected = refresh_sample_pool._screen(path)  # noqa: SLF001
    assert rejected["verdict"] == "INVALID"
    assert rejected["reason"] == "catalog_value_incomplete"

    artifact.pop("catalog_value_complete")
    compatible = refresh_sample_pool._screen(path)  # noqa: SLF001
    assert compatible["verdict"] == "valid"
    assert compatible["reason"] is None
