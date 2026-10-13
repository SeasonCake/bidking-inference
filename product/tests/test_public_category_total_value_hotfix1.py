from __future__ import annotations

from copy import deepcopy
import json
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import (  # noqa: E402
    BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR,
    extract_evidence,
    run_reference_engine,
)
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402


def _snapshot(*, category_total: int | None = 1_430_456) -> dict:
    public_rows = []
    if category_total is not None:
        public_rows.append(
            {
                "info_id": 200044,
                "map_id": 5611,
                "value": category_total,
                "value_field": 12,
            }
        )
    return {
        "hero": "aisha",
        "map_id": 5611,
        "phase": "bidding",
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 5611,
                "phase": "bidding",
                "round": 4,
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "hero": "aisha",
            "total_count": 36,
            "total_cells": 104,
            "counts": {"q3": 7, "q4": 9},
            "quality_cells": {"q3": 21, "q4": 23},
            "avg_cells": {"q5": 3.25},
            "quality_values": {"q5": 133_860},
            "split_counts": {"white": 5, "green": 4},
            "split_quality_cells": {"white": 8, "green": 11},
        },
        "public_info_rows": public_rows,
    }


def test_public_category_total_is_exact_global_floor_not_additive_tier() -> None:
    evidence = extract_evidence(_snapshot())

    assert evidence.public_category_total_value_floor == 1_430_456
    assert "public_info_200044_category_total_value_floor:1430456" in evidence.source_notes

    result = run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    assert result["status"] in {"ok", "count_prior"}
    assert result["conservative"] == 1_430_456
    assert result["balanced"] >= 1_430_456
    assert result["aggressive"] >= result["balanced"]
    assert result["evidence"]["public_category_total_value_floor"] == 1_430_456
    assert any(
        str(note).startswith(
            "known_quality_value_residual_quote_transform:1430456:"
        )
        for note in result["notes"]
    )


def test_public_category_total_floor_has_named_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    default = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    monkeypatch.setenv(BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert default["conservative"] == 1_430_456
    assert rollback["conservative"] < 1_430_456
    assert rollback["evidence"]["public_category_total_value_floor"] == 0
    assert not any(
        str(note).startswith("public_info_200044_category_total_value_floor:")
        for note in rollback["notes"]
    )


def test_unknown_public_info_12_does_not_change_engine() -> None:
    baseline_snapshot = _snapshot(category_total=None)
    unknown_snapshot = deepcopy(baseline_snapshot)
    unknown_snapshot["public_info_rows"] = [
        {
            "info_id": 299999,
            "map_id": 5611,
            "value": 9_999_999,
            "value_field": 12,
        }
    ]

    baseline = run_reference_engine(baseline_snapshot, max_combos=50_000).as_dict()
    unknown = run_reference_engine(unknown_snapshot, max_combos=50_000).as_dict()

    for key in ("status", "conservative", "balanced", "aggressive", "combo_count"):
        assert unknown[key] == baseline[key]


def test_product_summary_uses_public_category_total_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["created_at"] = time.time()
    snapshot["round"] = 5
    snapshot["action_round"] = 5
    snapshot["ui_contract"]["context"].update(
        {
            "round": 5,
            "action_round": 5,
            "session_id": "5611:redacted",
        }
    )
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    monkeypatch.setenv("BIDKING_DISABLE_KNOWN_QUALITY_RESIDUAL_QUOTE_TRANSFORM", "1")
    rollback = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert summary["status"] == "ok"
    assert summary["reference"]["source"] == "ref_v0"
    assert summary["reference"]["readiness"] == "live_ready"
    assert summary["reference"]["conservative"] == "1,430,456"
    assert summary["reference"]["balanced"] == "1,521,460"
    assert summary["reference"]["aggressive"] == "1,612,719"
    assert [
        rollback["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == ["1,430,456", "1,430,456", "1,469,673"]
    assert [
        summary["ahmed_ref"][key]
        for key in ("value_p25", "value_p50", "value_p75")
    ] == [
        rollback["ahmed_ref"][key]
        for key in ("value_p25", "value_p50", "value_p75")
    ]
    assert summary["ahmed_ref"]["evidence"]["public_category_total_value_floor"] == (
        1_430_456
    )
