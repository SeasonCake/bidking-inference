from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansPlayerBid,
    FatbeansSendEvent,
    FatbeansStateEvent,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str):
    # scripts/ 不是包（无 __init__.py），且 pyproject pythonpath 只含 src/，
    # 所以 `from scripts.x import` 在 pytest 下无法解析。按文件路径加载，与
    # test_fatbeans_webhook_monitor.py 一致；脚本自身的 sys.path 注入仍会执行。
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_module = _load_script_module("summarize_live_sample_usage")
SessionUsage = _module.SessionUsage
session_usage_from_events = _module.session_usage_from_events
summarize_usage = _module.summarize_usage


def test_session_usage_from_events_extracts_local_hero_actions_and_outcome() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=1,
                capture_time="2026-06-04 10:00:00",
                message_id=0x0026,
                session_id="2510:abc",
                value=100129,
            ),
        ),
        states=(
            FatbeansStateEvent(
                sort_id=2,
                capture_time="2026-06-04 10:00:01",
                message_id=0x0025,
                session_id="2510:abc",
                map_id=2510,
                round_index=1,
                player_id=7,
                bids=(
                    FatbeansPlayerBid(
                        player_id=7,
                        name="me",
                        hero_id=103,
                        values=(100_000, 200_000),
                    ),
                    FatbeansPlayerBid(
                        player_id=8,
                        name="opponent",
                        hero_id=104,
                        values=(180_000,),
                    ),
                ),
            ),
            FatbeansStateEvent(
                sort_id=3,
                capture_time="2026-06-04 10:00:02",
                message_id=0x002D,
                session_id="2510:abc",
                map_id=2510,
                round_index=2,
                player_id=7,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=1001,
                        quality=5,
                        cells=4,
                    ),
                    FatbeansInventoryItem(
                        runtime_id=2,
                        item_id=1002,
                        quality=6,
                        cells=6,
                    ),
                ),
            ),
        ),
        statuses=(),
    )

    usage = session_usage_from_events(
        Path("sample.json"),
        events,
        items={
            1001: SimpleNamespace(value=300_000),
            1002: SimpleNamespace(value=700_000),
        },
    )

    assert usage.session_id == "2510:abc"
    assert usage.map_id == 2510
    assert usage.local_player_id == 7
    assert usage.local_hero == "aisha"
    assert usage.opponent_heroes == ("gabriela",)
    assert usage.local_action_ids == (100129,)
    assert usage.settled is True
    assert usage.final_value == 1_000_000
    assert usage.final_cells == 10
    assert usage.local_final_bid == 200_000
    assert usage.highest_bid == 200_000


def test_summarize_usage_counts_frequency_and_candidate_loadouts() -> None:
    sessions = [
        SessionUsage(
            path="a.json",
            session_id="s1",
            map_id=2510,
            local_player_id=1,
            local_hero="aisha",
            opponent_heroes=("gabriela", "ethan"),
            local_action_ids=(100129, 100136),
            settled=True,
            final_value=1_000_000,
            final_cells=100,
            final_item_count=40,
            local_final_bid=800_000,
            highest_bid=900_000,
        ),
        SessionUsage(
            path="b.json",
            session_id="s2",
            map_id=2510,
            local_player_id=1,
            local_hero="aisha",
            opponent_heroes=("gabriela",),
            local_action_ids=(100129, 100104),
            settled=True,
            final_value=2_000_000,
            final_cells=120,
            final_item_count=42,
            local_final_bid=2_200_000,
            highest_bid=2_200_000,
        ),
        SessionUsage(
            path="c.json",
            session_id="s3",
            map_id=2401,
            local_player_id=2,
            local_hero="ethan",
            opponent_heroes=("aisha",),
            local_action_ids=(100104,),
            settled=False,
            final_value=None,
            final_cells=None,
            final_item_count=None,
            local_final_bid=300_000,
            highest_bid=350_000,
        ),
    ]

    summary = summarize_usage(
        sessions,
        action_names={
            100129: "随机抽检(2)",
            100136: "宝光四鉴",
            100104: "普品扫描",
        },
        top=10,
        min_group_sessions=1,
    )

    assert summary["sessions"] == 3
    assert summary["settled_sessions"] == 2
    assert summary["local_heroes"][0] == {
        "key": "aisha",
        "count": 2,
        "share": 0.6667,
    }
    assert summary["opponent_heroes"][0]["key"] == "gabriela"
    assert summary["opponent_heroes"][0]["count"] == 2
    action_sessions = {
        row["key"]: row
        for row in summary["local_action_sessions"]
    }
    assert action_sessions[100129]["count"] == 2
    assert action_sessions[100129]["label"] == "随机抽检(2)"
    action_outcomes = {
        row["key"]: row
        for row in summary["outcome_by_local_action"]
    }
    assert action_outcomes[100129]["sessions"] == 2
    assert action_outcomes[100129]["settled_sessions"] == 2
    assert action_outcomes[100129]["median_final_value"] == 1_500_000
    assert action_outcomes[100129]["median_local_bid_to_value"] == 0.95
    assert action_outcomes[100129]["local_over_value_rate"] == 0.5
    assert summary["recommended_loadout_candidates"]["aisha"][0]["action"] == "随机抽检(2)"
    assert len(summary["recommended_loadout_candidates"]["aisha"]) == 3
