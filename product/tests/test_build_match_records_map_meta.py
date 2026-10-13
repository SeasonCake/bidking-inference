from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_match_records as records  # noqa: E402


def test_red_crate_shipwreck_maps_are_named_and_classified() -> None:
    meta = records._load_map_meta()

    assert meta[2545]["name"] == "殖民商船宝库(红箱活动)"
    assert meta[2545]["family"] == "沉船"
    assert meta[4545]["name"] == "殖民商船宝库(红箱活动)"
    assert meta[4545]["family"] == "沉船"


def test_activity_map_blocks_keep_distinct_labels_and_bounds() -> None:
    meta = records._load_map_meta()

    assert meta[2535]["name"] == "殖民商船宝库(活动沉船)"
    assert meta[2535]["activity_status"] == "historical"
    assert meta[4537]["name"] == "皇家御用货舱(活动沉船)"
    assert meta[4537]["activity_status"] == "historical"
    assert meta[2523]["activity_status"] == "historical"
    assert meta[2550]["name"] == "现代货轮娱乐库(红箱活动)"
    assert meta[2550]["activity_status"] == "current"
    assert meta[2551]["name"] == "未知残骸(活动沉船·待确认)"
    assert meta[2551]["activity_status"] == "pending"
    assert records._family_from_id(2545) == "沉船"


def test_auction_hall_is_separate_from_regular_open_or_sealed_maps() -> None:
    meta = records._load_map_meta()

    assert meta[6001]["family"] == "竞拍大厅"
    assert meta[6001]["map_kind"] == "auction_hall"
    assert meta[6001]["auction_mode"] == "auction_hall"
    assert meta[6001]["warehouse_policy"] == "unknown"
