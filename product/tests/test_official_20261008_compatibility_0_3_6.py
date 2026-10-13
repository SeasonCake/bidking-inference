"""Exact 76b7ca static compatibility; missing fixtures are input failures."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from bidking_lab.extract import bid_map_table, current_rankmap_package_contract as package, item_table
from bidking_lab.live import warehouse_source as warehouse

NEW_ROOT = Path(os.environ.get("BIDKING_20261008_TEXT_ROOT", "C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/content/textassets"))
OLD_ROOT = Path(os.environ.get("BIDKING_B049_STATIC_ROOT", "C:/example/bidking-workspace/scratch/bidking_035_small_hotfix_20260910_01/official_b049_static01"))
GENERATION = "76b7ca-2026-10-08-723"
NEW_PINS = {
    "Item": (561370, "125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372"),
    "BidMap": (103972, "67918be0098eb8f65772c34e0ef14bded7929878a832e64e448d2d9b724ba211"),
}
PACKAGE_PINS = {
    "Item": "380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40",
    "BidMap": "5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020",
}


@pytest.fixture(scope="module")
def raw_tables():
    result = {name: (NEW_ROOT / f"{name}.txt").read_bytes() for name in NEW_PINS}
    for name, raw in result.items():
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == NEW_PINS[name], f"input identity failure: {name}"
    return result


def _rows(raw):
    return [line.split("\t") for line in raw.decode("utf-8-sig").splitlines()]


@pytest.mark.parametrize("name,parser,count,width", [("Item", item_table.parse_item_table, 1506, 45), ("BidMap", bid_map_table.parse_bid_map_table, 246, 28)])
def test_exact_new_and_b049_complete_tables(raw_tables, name, parser, count, width):
    parsed = parser(_rows(raw_tables[name]))
    assert len(parsed) == count
    assert {len(v.raw_row) for v in parsed.values()} == {width}
    old_raw = (OLD_ROOT / "textassets" / f"{name}.txt").read_bytes()
    assert len(parser(_rows(old_raw))) == (1432 if name == "Item" else 246)


@pytest.mark.parametrize("name,parser", [("Item", item_table.parse_item_table), ("BidMap", bid_map_table.parse_bid_map_table)])
@pytest.mark.parametrize("mutation", ["cell", "remove", "duplicate", "wrong-width", "new-tail"])
def test_exact_table_gates_reject_damage(raw_tables, name, parser, mutation):
    rows = _rows(raw_tables[name])
    if mutation == "cell":
        rows[0][1] += "!"
    elif mutation == "remove":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = list(rows[0])
    elif mutation == "wrong-width":
        rows[0].pop()
    else:
        rows[-1][-1] += "!"
    with pytest.raises(ValueError):
        parser(rows)


def test_item_semantics_and_new_tail_are_separate(raw_tables):
    items = item_table.parse_item_table(_rows(raw_tables["Item"]))
    expected = {
        1016015: (987000, (3, 2)), 1046014: (6660000, (2, 2)),
        1046015: (1282048, (2, 3)), 1046016: (2558800, (4, 2)),
        1046017: (422000, (2, 2)), 1096016: (285000, (2, 2)),
    }
    for key, (value, shape) in expected.items():
        item = items[key]
        assert (item.quality, item.value, (item.shape_w, item.shape_h)) == (6, value, shape)
        assert {501, 601}.issubset(json.loads(item.raw_row[39]))
        assert len(item.raw_row[42:]) == 3
    assert items[1046013].raw_row[39] == "[601]"
    assert items[1306019].raw_row[39] == "[]"
    assert items[1401001].raw_row[39] == "[]"
    assert (items[1401001].quality, items[1401001].value) == (3, 8888)
    # No speculative semantic field is added for the new tail.
    assert "guild_points" not in items[1016015].model_dump()


def test_hall_only_association_does_not_weaken_old_validator(raw_tables):
    old_rows = _rows((OLD_ROOT / "textassets/Item.txt").read_bytes())
    next(row for row in old_rows if row[0] == "1046013")[39] = "[601]"
    with pytest.raises(ValueError, match="mixed 501/601"):
        item_table._validate_official_20260910_table(old_rows)
    new_rows = _rows(raw_tables["Item"])
    next(row for row in new_rows if row[0] == "1046013")[39] = "[501]"
    with pytest.raises(ValueError, match="mixed 501/601"):
        item_table._validate_official_20261008_table(new_rows)
    mixed = _rows(raw_tables["Item"])
    mixed[0] = mixed[0][:42]
    with pytest.raises(ValueError, match="mixed"):
        item_table.parse_item_table(mixed)


def test_new_complete_load_never_supplements_old_catalog(raw_tables, tmp_path, monkeypatch):
    path = tmp_path / "data/raw/tables/Item.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(base64.b64encode("\n".join("\t".join(row) for row in _rows(raw_tables["Item"])).encode()))
    def bad_supplement(*args):
        pytest.fail("new complete table entered legacy catalog supplementation")
    monkeypatch.setattr(item_table, "_supplement_from_catalog", bad_supplement)
    assert len(item_table.load_item_table(path)) == 1506


def test_6001_structure_and_cost_domain(raw_tables):
    maps = bid_map_table.parse_bid_map_table(_rows(raw_tables["BidMap"]))
    hall = maps[6001]
    assert (hall.auction_mode, hall.mode_flag, hall.drop_pool_id) == ("auction_hall", 20, 6010)
    assert (hall.items_per_session_min, hall.items_per_session_max) == (1, 1)
    assert (hall.rounds_total, hall.col11_value_tier_scale_basis_points) == (100, 10000)
    wrong = list(maps[2501].raw_row)
    wrong[11] = "100"
    with pytest.raises(ValueError, match="restricted"):
        bid_map_table.parse_bid_map_row(wrong, rounds_profile=bid_map_table.BID_MAP_OFFICIAL_20261008_PROFILE)
    with pytest.raises(ValueError):
        bid_map_table.parse_bid_map_row(hall.raw_row, rounds_profile=bid_map_table.BID_MAP_OFFICIAL_20260910_PROFILE)
    assert warehouse.warehouse_map_name_table_sha256(maps) == "7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc"
    assert warehouse.warehouse_map_source_contract_sha256(maps) == "b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3"


@pytest.mark.parametrize("name", ["Item", "BidMap"])
def test_new_package_builder_is_exact_and_static_only(raw_tables, name):
    packaged, receipt = package.build_official_20261008_package_table(name, raw_tables[name])
    assert hashlib.sha256(packaged).hexdigest() == PACKAGE_PINS[name]
    assert receipt["package_generation"] == GENERATION
    assert receipt["distribution_authority"] is False
    assert receipt["quote_authority"] is False
    with pytest.raises(package.Update606PackageInputError):
        package.build_official_20261008_package_table(name, raw_tables[name] + b"\n")
    with pytest.raises(package.Update606PackageInputError):
        package.build_current_package_table(name, raw_tables[name])
    assert package.CURRENT_PACKAGE_GENERATION == "b049c3-2026-09-10-597"


def test_package_pair_generation_and_mixed_negative_controls(raw_tables):
    new = {n: package.build_official_20261008_package_table(n, v)[0] for n,v in raw_tables.items()}
    old = {n: package.build_current_package_table(n, (OLD_ROOT / "textassets" / f"{n}.txt").read_bytes())[0] for n in new}
    assert warehouse.warehouse_package_generation_from_exact_inputs(new["BidMap"], new["Item"]) == GENERATION
    assert warehouse.warehouse_package_generation_from_exact_inputs(old["BidMap"], old["Item"]) == "b049c3-2026-09-10-597"
    for left,right in ((new["BidMap"],old["Item"]),(old["BidMap"],new["Item"]),(new["BidMap"]+b"!",new["Item"]),(None,new["Item"])):
        assert warehouse.warehouse_package_generation_from_exact_inputs(left,right) == warehouse.UNKNOWN_WAREHOUSE_GENERATION


@pytest.mark.parametrize("game,profile,expected", [
    (GENERATION,GENERATION,GENERATION), (GENERATION,"b049c3-2026-09-10-597","unknown_warehouse_generation"),
    ("b049c3-2026-09-10-597",GENERATION,"unknown_warehouse_generation"), (GENERATION+"-suffix",GENERATION,"unknown_warehouse_generation"),
    (None,GENERATION,"unknown_warehouse_generation"), (GENERATION,None,"unknown_warehouse_generation"),
])
def test_live_generation_requires_exact_agreement(game, profile, expected):
    assert warehouse.warehouse_generation_from_live_evidence("2026-10-11T02:02:35-07:00",game_version=game,package_generation=profile)==expected


def test_new_hall_recognition_does_not_claim_distribution(raw_tables):
    maps = bid_map_table.parse_bid_map_table(_rows(raw_tables["BidMap"]))
    result = warehouse.classify_from_map_table(6001,maps,source_generation=GENERATION,profile_generation=GENERATION,
                                               game_version=GENERATION,owner_evidence_status="absent")
    assert result.map_table_build == "25795126"
    assert result.qualification == warehouse.POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    assert result.official_distribution_eligible is False
