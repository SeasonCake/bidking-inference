"""update606 current shipwreck activity names stay human-readable end to end."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_match_records as records  # noqa: E402
import public_stats  # noqa: E402
import sample_dashboard  # noqa: E402


FINAL_INPUT_ROOT_ENV = "BIDKING_UPDATE606_FINAL_INPUT_ROOT"
CURRENT_SUFFIX = "(活动沉船)"
RED_CRATE_CURRENT_SUFFIX = "(红箱活动)"
FINAL_CURRENT_NAMES = {
    2531: "未知残骸(活动沉船)",
    2533: "军用舰艇保险库(活动沉船)",
    4533: "军用舰艇保险库(活动沉船)",
    4537: "皇家御用货舱(活动沉船)",
}


def _final_data_root() -> Path:
    configured = os.environ.get(FINAL_INPUT_ROOT_ENV)
    if not configured:
        pytest.skip(f"{FINAL_INPUT_ROOT_ENV} is required for update606 final-input controls")
    root = Path(configured).resolve()
    data_root = root / "final" if (root / "final").is_dir() else root
    maps_path = data_root / "data" / "processed" / "maps.json"
    if not maps_path.is_file():
        pytest.fail(f"update606 final maps input is missing: {maps_path}")
    return data_root


def _sample(map_id: int, ordinal: int) -> dict:
    return {
        "session_id": f"update606-activity-meta-{map_id}-{ordinal}",
        "map_id": map_id,
        "final_quality_counts": "q1=2;q6=1",
        "final_quality_values": "q1=100;q6=90000",
        "final_quality_cells": "q1=2;q6=4",
        "inventory_count": 3,
        "inventory_cells": 6,
        "known_value_sum": 90100,
        "client_ts": 1_786_586_400.0 + ordinal,
    }


@pytest.mark.parametrize(("map_id", "expected_name"), FINAL_CURRENT_NAMES.items())
def test_update606_final_existing_current_shipwreck_rows_get_human_name(
    monkeypatch: pytest.MonkeyPatch,
    map_id: int,
    expected_name: str,
) -> None:
    monkeypatch.setattr(records, "ROOT", _final_data_root())

    row = records._load_map_meta()[map_id]

    assert row["name"] == expected_name
    assert row["name"].count(CURRENT_SUFFIX) == 1
    assert row["map_kind"] == "activity"
    assert row["activity_status"] == "current"
    assert row["activity_cohort"] == "活动沉船"


def test_current_suffix_is_idempotent_and_other_cohorts_stay_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = [
        {"map_id": 2501, "name": "普通沉船", "category": 105, "auction_mode": "open"},
        {"map_id": 2511, "name": "旧活动", "category": 105, "auction_mode": "open"},
        {"map_id": 2521, "name": "近期批次", "category": 105, "auction_mode": "open"},
        {"map_id": 2531, "name": "当前裸名", "category": 105, "auction_mode": "open"},
        {
            "map_id": 2532,
            "name": f"当前已有后缀{CURRENT_SUFFIX}",
            "category": 105,
            "auction_mode": "open",
        },
        {"map_id": 2541, "name": "红箱当前批次", "category": 105, "auction_mode": "open"},
        {"map_id": 2551, "name": "待确认批次", "category": 105, "auction_mode": "open"},
        {"map_id": 2599, "name": "未知批次", "category": 105, "auction_mode": "open"},
        {"map_id": 5601, "name": "不可信排位名", "category": 402, "auction_mode": "sealed"},
    ]
    maps_path = tmp_path / "data" / "processed" / "maps.json"
    maps_path.parent.mkdir(parents=True)
    maps_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(records, "ROOT", tmp_path)

    first = records._load_map_meta()
    second = records._load_map_meta()

    assert first[2501]["name"] == "普通沉船"
    assert first[2511]["name"] == "旧活动(活动)"
    assert first[2521]["name"] == "近期批次(近期活动)"
    assert first[2531]["name"] == f"当前裸名{CURRENT_SUFFIX}"
    assert first[2532]["name"] == f"当前已有后缀{CURRENT_SUFFIX}"
    assert first[2532]["name"].count(CURRENT_SUFFIX) == 1
    assert second[2531]["name"] == first[2531]["name"]
    assert second[2532]["name"] == first[2532]["name"]
    assert first[2541]["name"] == f"红箱当前批次{RED_CRATE_CURRENT_SUFFIX}"
    assert second[2541]["name"] == first[2541]["name"]
    assert first[2541]["name"].count(RED_CRATE_CURRENT_SUFFIX) == 1
    assert first[2551]["name"] == "待确认批次"
    assert first[2599]["name"] == "未知批次"
    assert first[5601]["name"] == "百物杂拍"


def test_final_current_names_match_public_cube_html_and_private_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(records, "ROOT", _final_data_root())
    meta = records._load_map_meta()
    samples = [
        _sample(map_id, ordinal)
        for map_id in FINAL_CURRENT_NAMES
        for ordinal in range(public_stats.MIN_SAMPLES_TO_SHOW)
    ]

    aggregate = public_stats.aggregate_public(samples)
    public_by_id = {row["map_id"]: row for row in aggregate["maps"]}
    cube_names = dict(zip(aggregate["cube"]["maps"], aggregate["cube"]["map_names"], strict=True))
    html = public_stats.build_public_html(aggregate, generated_at=0)

    for map_id, expected_name in FINAL_CURRENT_NAMES.items():
        assert public_by_id[map_id]["name"] == expected_name
        assert cube_names[map_id] == expected_name
        assert expected_name in html
        private_row = sample_dashboard._row_for_client(
            _sample(map_id, 0),
            map_meta=meta,
            include_details=False,
        )
        assert private_row is not None
        assert private_row["name"] == expected_name
