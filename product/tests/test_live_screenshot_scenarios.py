from __future__ import annotations

from pathlib import Path

import pytest

from bidking_lab.inference.display import parse_reading
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.live import (
    LiveSessionState,
    apply_observation_batch,
    compare_session_obs,
    live_batch_from_legacy_obs,
    live_state_to_session_obs,
)


def _session_from_obs(obs: dict) -> SessionObs | None:
    return live_state_to_session_obs(
        apply_observation_batch(
            LiveSessionState(),
            live_batch_from_legacy_obs(
                obs,
                source="ocr",
                event_kind="ocr_update",
            ),
        )
    )


def _bucket(q: int, **kwargs) -> QualityBucketObs:
    if "avg_cells" in kwargs and isinstance(kwargs["avg_cells"], str):
        kwargs["avg_cells"] = parse_reading(kwargs["avg_cells"])
    return QualityBucketObs(quality=q, **kwargs)


PICTURES = Path(r"C:\Users\example\Pictures")
SCENARIOS: tuple[dict, ...] = (
    {
        "label": "desktop_r3_ethan",
        "image": PICTURES / "Desktop Screenshot 2026.05.15 - 11.10.26.79.png",
        "obs": {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 22,
            "blue_cells": 15,
            "purple_avg_raw": "3.27",
        },
        "expected": SessionObs(
            map_id=2405,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=22),
                3: _bucket(3, total_cells=15),
                4: _bucket(4, avg_cells="3.27"),
            },
        ),
    },
    {
        "label": "wechat_r4_cells",
        "image": PICTURES / "微信图片_20260517163704.jpg",
        "obs": {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 12,
            "blue_cells": 35,
            "purple_cells": 34,
            "gold_cells": 28,
        },
        "expected": SessionObs(
            map_id=2405,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=12),
                3: _bucket(3, total_cells=35),
                4: _bucket(4, total_cells=34),
                5: _bucket(5, total_cells=28),
                6: _bucket(6, total_cells=14),
            },
        ),
    },
    {
        "label": "wechat_r3_warehouse",
        "image": PICTURES / "微信图片_20260517135143.jpg",
        "obs": {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 159,
            "wg_cells": 28,
            "blue_cells": 35,
            "purple_avg_raw": "3.43",
        },
        "expected": SessionObs(
            map_id=2405,
            hero="ethan",
            warehouse_total_cells=159,
            buckets={
                1: _bucket(1, total_cells=28),
                3: _bucket(3, total_cells=35),
                4: _bucket(4, avg_cells="3.43"),
            },
        ),
    },
    {
        "label": "wechat_r4_metrics",
        "image": PICTURES / "微信图片_20260517135136.jpg",
        "obs": {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 12,
            "blue_cells": 35,
            "purple_avg_raw": "2.90",
            "purple_value": 86_490,
        },
        "expected": SessionObs(
            map_id=2405,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=12),
                3: _bucket(3, total_cells=35),
                4: _bucket(4, avg_cells="2.90", value_sum=86_490),
            },
        ),
    },
    {
        "label": "wechat_r4_aisha",
        "image": PICTURES / "微信图片_20260517223852.jpg",
        "obs": {
            "map_id": 2510,
            "hero": "aisha",
            "warehouse_cells": 123,
            "gold_cells": 22,
        },
        "expected": SessionObs(
            map_id=2510,
            hero="aisha",
            warehouse_total_cells=123,
            buckets={5: _bucket(5, total_cells=22)},
        ),
    },
    {
        "label": "video_r1_public_info_20260526223715",
        "image": PICTURES / "微信图片_20260526223715.jpg",
        "obs": {
            "map_id": 2405,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 40,
            "blue_cells": 30,
            "purple_count": 14,
            "purple_value": 300_000,
        },
        "expected": SessionObs(
            map_id=2405,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=40),
                3: _bucket(3, total_cells=30),
                4: _bucket(4, count=14, value_sum=300_000),
            },
        ),
    },
    {
        "label": "video_r3_public_info_20260526223700",
        "image": PICTURES / "微信图片_20260526223700.jpg",
        "obs": {
            "map_id": 2510,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 43,
            "blue_cells": 32,
            "purple_cells": 42,
            "gold_count": 2,
        },
        "expected": SessionObs(
            map_id=2510,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=43),
                3: _bucket(3, total_cells=32),
                4: _bucket(4, total_cells=42),
                5: _bucket(5, count=2),
            },
        ),
    },
    {
        "label": "video_r4_public_info_20260526223711",
        "image": PICTURES / "微信图片_20260526223711.jpg",
        "obs": {
            "map_id": 2510,
            "hero": "ethan",
            "warehouse_cells": 123,
            "wg_cells": 44,
            "blue_cells": 43,
            "purple_cells": 12,
            "gold_cells": 24,
        },
        "expected": SessionObs(
            map_id=2510,
            hero="ethan",
            warehouse_total_cells=123,
            buckets={
                1: _bucket(1, total_cells=44),
                3: _bucket(3, total_cells=43),
                4: _bucket(4, total_cells=12),
                5: _bucket(5, total_cells=24),
                6: _bucket(6, total_cells=0, count=0),
            },
        ),
    },
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["label"])
def test_live_matches_screenshot_derived_legacy_session(scenario: dict) -> None:
    if not scenario["image"].is_file():
        pytest.skip(f"missing screenshot fixture: {scenario['image']}")

    live_session = _session_from_obs(scenario["obs"])
    diff = compare_session_obs(scenario["expected"], live_session)

    assert diff == ()
