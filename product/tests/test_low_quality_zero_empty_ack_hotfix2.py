from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansSendEvent,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import _action_result_rows


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import run_reference_engine  # noqa: E402


def _server_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location(
        "ahmad_live_panel_server_low_quality_zero",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _same_round_zero_rows() -> list[dict]:
    # 2601 real diagnostic: 100110 SEND at 21:40:22.216 and its structurally
    # empty direct 0x0027 ACK at 21:40:22.379. The parser preserves the ACK as
    # result=None; this existing monitor contract converts the matched pair to
    # explicit inferred zero without waiting for the next auction state.
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=10,
                capture_time="2026-07-27 21:40:22.216",
                message_id=0x0026,
                session_id="2601:9000000000000027",
                value=100110,
            ),
        ),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=11,
                capture_time="2026-07-27 21:40:22.379",
                message_id=0x0027,
                session_id="2601:9000000000000027",
                map_id=2601,
                round_index=2,
                action_results=(
                    FatbeansActionResult(
                        action_id=100110,
                        result=None,
                        result_field=None,
                    ),
                ),
            ),
        ),
    )
    return _action_result_rows(
        events,
        {100110: type("Tool", (), {"name": "普品均格"})()},
        session_id="2601:9000000000000027",
    )


@pytest.mark.parametrize("hero", ["ahmed", "aisha"])
def test_same_round_empty_ack_locks_white_green_zero_through_engine_and_ui(
    hero: str,
) -> None:
    action_rows = _same_round_zero_rows()
    assert action_rows == [
        {
            "sort": 10,
            "time": "2026-07-27 21:40:22.216",
            "action_id": 100110,
            "tool": "普品均格",
            "result": 0,
            "result_field": None,
            "inferred_zero": True,
            "result_inference": "sent_action_without_result_after_later_state",
            "revealed_items": 0,
            "revealed_items_detail": [],
            "revealed_summary": "",
        }
    ]
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2601,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": action_rows},
        },
        "structured_ref_inputs": {
            "total_count": 31,
            "fixed_counts": {"q3": 9},
            "avg_cells": {"q5": 3.3333332538604736},
        },
    }

    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert result["evidence"]["avg_cells"]["q1"] == 0
    assert result["evidence"]["fixed_counts"]["q1"] == 0
    assert result["quality_count_ranges"]["q1"] == [0, 0, 0]
    assert result["quality_cells_ranges"]["q1"] == [0, 0, 0]
    assert "action_100110_q1_avg_cells_inferred_zero" in result["notes"]
    assert "zero_avg_cells_q1_count_zero" in result["notes"]
    summary = _server_module()._quality_uncertainty_summary(result)
    assert "已锁" in summary
    assert "白绿0件/0格" in summary


def test_missing_empty_ack_keeps_prior_instead_of_inventing_zero() -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2601,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
        "structured_ref_inputs": {
            "total_count": 31,
            "fixed_counts": {"q3": 9},
            "avg_cells": {"q5": 3.3333332538604736},
        },
    }

    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert result["evidence"]["fixed_counts"].get("q1") is None
    assert result["quality_count_ranges"]["q1"] == [1, 3, 5]
    assert "zero_avg_cells_q1_count_zero" not in result["notes"]


def test_unsolicited_empty_ack_stays_resultless_and_does_not_invent_zero() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=11,
                capture_time="2026-07-27 21:40:22.379",
                message_id=0x0027,
                session_id="2601:9000000000000027",
                map_id=2601,
                round_index=2,
                action_results=(
                    FatbeansActionResult(
                        action_id=100110,
                        result=None,
                        result_field=None,
                    ),
                ),
            ),
        ),
    )

    rows = _action_result_rows(
        events,
        {100110: type("Tool", (), {"name": "普品均格"})()},
        session_id="2601:9000000000000027",
    )

    assert rows[0]["result"] is None
    assert "inferred_zero" not in rows[0]


def test_2601_product_summary_replaces_unlocked_white_green_with_exact_zero(
    tmp_path: Path,
) -> None:
    snapshot = {
        "created_at": time.time(),
        "session_id": "2601:9000000000000027",
        "hero": "ahmed",
        "map_id": 2601,
        "round": 2,
        "phase": "bidding",
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2601,
                "round": 2,
                "phase": "bidding",
                "session_id": "2601:9000000000000027",
            },
            "constraints": {"public_info": {}},
            "actions": {"results": _same_round_zero_rows()},
        },
        "structured_ref_inputs": {
            "total_count": 31,
            "fixed_counts": {"q3": 9},
            "avg_cells": {"q5": 3.3333332538604736},
        },
    }
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = _server_module().summarize_snapshot(
        snapshot,
        snapshot_path=snapshot_path,
    )

    assert summary["reference"]["conservative"] == "1,082,416"
    assert summary["reference"]["balanced"] == "1,289,256"
    assert summary["reference"]["aggressive"] == "1,494,307"
    assert summary["red"]["uncertainty_summary"] == "已锁 白绿0件/0格 蓝9件"
    assert (
        "白绿件候选：0件 100%" in summary["red"]["low_quality_count_probability_hover"]
    )
    assert summary["ahmed_ref"]["quality_count_ranges"]["q1"] == [0, 0, 0]
    assert summary["ahmed_ref"]["quality_cells_ranges"]["q1"] == [0, 0, 0]
