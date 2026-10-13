"""Exact b049 source inputs. Missing frozen inputs are input failures, not skips."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from bidking_lab.extract import bid_map_table, current_rankmap_package_contract as package, item_table
from bidking_lab.live import warehouse_source

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(os.environ.get("BIDKING_B049_STATIC_ROOT", "C:/example/bidking-workspace/scratch/bidking_035_small_hotfix_20260910_01/official_b049_static01"))
CANDIDATE = Path(os.environ.get("BIDKING_B049_CANDIDATE_ROOT", "C:/example/bidking-workspace/scratch/bidking_035_b049_compat_source_20260910_01/candidate_inputs"))
GENERATION = "b049c3-2026-09-10-597"


@pytest.fixture(scope="module")
def raw_tables():
    intake_raw = (STATIC / "INTAKE.json").read_bytes()
    assert hashlib.sha256(intake_raw).hexdigest() == "ad879d35319a26da34dcd17712098e09c0e4da79791bddf0c67b6e99d1732675"
    intake = json.loads(intake_raw)
    result = {}
    for name, expected in intake["tables"].items():
        raw = (STATIC / "textassets" / (name + ".txt")).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (expected["bytes"], expected["sha256"])
        result[name] = raw
    return result


def _rows(raw):
    return [line.split("\t") for line in raw.decode("utf-8-sig").splitlines()]


@pytest.mark.parametrize("name,parser,count", [("Item", item_table.parse_item_table, 1432), ("BidMap", bid_map_table.parse_bid_map_table, 246)])
def test_exact_new_and_old_complete_tables(raw_tables, name, parser, count):
    assert len(parser(_rows(raw_tables[name]))) == count
    old = json.loads((STATIC / "baseline_rows" / (name + ".json")).read_bytes())
    assert len(parser(old)) == (1427 if name == "Item" else 246)


@pytest.mark.parametrize("name,parser", [("Item", item_table.parse_item_table), ("BidMap", bid_map_table.parse_bid_map_table)])
@pytest.mark.parametrize("mutation", ["cell", "remove", "duplicate", "wrong-width"])
def test_new_complete_table_identity_rejects_mutations(raw_tables, name, parser, mutation):
    rows = _rows(raw_tables[name])
    if mutation == "cell":
        rows[-1][-1] += "x"
    elif mutation == "remove":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = list(rows[0])
    else:
        rows[0].pop()
    with pytest.raises(ValueError):
        parser(rows)


def test_b049_new_zero_shape_items_do_not_change_collectibles(raw_tables):
    new = item_table.parse_item_table(_rows(raw_tables["Item"]))
    old = item_table.parse_item_table(json.loads((STATIC / "baseline_rows/Item.json").read_bytes()))
    assert set(new) - set(old) == {12, 13, 14, 15, 6201}
    assert all(new[key].model_dump() == value.model_dump() for key, value in old.items())
    for key in set(new) - set(old):
        assert (new[key].value, new[key].shape_w, new[key].shape_h) == (0, 0, 0)
    droppable = json.loads((ROOT / "data/processed/items_droppable.json").read_bytes())
    assert {row["item_id"] for row in droppable}.isdisjoint({12, 13, 14, 15, 6201})


def test_6001_is_hall_only_under_exact_new_profile(raw_tables):
    maps = bid_map_table.parse_bid_map_table(_rows(raw_tables["BidMap"]))
    assert maps[6001].mode_flag == 10
    assert maps[6001].auction_mode == "auction_hall"
    assert bid_map_table._infer_auction_mode(6001, 10) == "sealed"
    assert maps[2511].auction_mode == "open"
    assert maps[4511].auction_mode == "sealed"
    assert warehouse_source.warehouse_map_source_contract_sha256(maps) == "b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3"


@pytest.mark.parametrize("name,expected", [("Item", "cb15d92ba307831d3da42875a6c542b5675baa48e74d37290cfbcd1a86594949"), ("BidMap", "03fb51becf3793d197a5901140d3b0ebbb0f1fe8502782f0793eed735b5f4f16")])
def test_current_package_builder_is_exact(raw_tables, name, expected):
    packaged, receipt = package.build_current_package_table(name, raw_tables[name])
    assert hashlib.sha256(packaged).hexdigest() == expected
    assert receipt["package_generation"] == GENERATION
    assert base64.b64decode(packaged).decode("utf-8").splitlines() == ["\t".join(row) for row in _rows(raw_tables[name])]
    with pytest.raises(package.Update606PackageInputError):
        package.build_current_package_table(name, raw_tables[name] + b"\n")


def test_candidate_catalog_and_authority_preserve_old_numeric_inputs():
    new = json.loads((CANDIDATE / "items.json").read_bytes())
    assert len(new) == 1432
    authority = json.loads((CANDIDATE / "rankmap_distribution_authority_current.json").read_bytes())
    assert authority["package_generation"] == GENERATION
    assert authority["model_distribution_authority"] is True
    assert authority["official_distribution_authority"] is False
    assert authority["outputs"]["weights"]["sha256"] == "6adc5792bae22bba8ca972b1397e30f7d651145b5b306516e18725d3809beec2"
    assert authority["outputs"]["priors"]["sha256"] == "d543ce6e998c6bc8dffa4d27b7453ede04f713cbe75be288ab7dc10032a46c26"


def test_old_frozen_intake_profiles_remain_exact_and_new_is_latest():
    path = ROOT / "scripts/inspect_0_3_4_official_update.py"
    spec = importlib.util.spec_from_file_location("b049_inspector_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LATEST_FROZEN_PACKAGE_GENERATION == GENERATION
    old = module.FROZEN_GENERATION_PROFILES["68171c-2026-09-03-907"]
    assert old.text_assets["Item"].rows == 1427
    assert old.artifacts["Scripts.dll"]["sha256"] == "2c76aa7194e611a23a38e1931261f2f48d011bf803fc86e1b40b8f458f2d5aca"
    assert module.FROZEN_GENERATION_PROFILES[GENERATION].text_assets["Item"].rows == 1432


def test_real_source_runtime_loads_exact_current_package_without_reading_game(monkeypatch):
    import sys
    from types import SimpleNamespace
    from bidking_lab.live import monitor, version_check

    monkeypatch.setattr(version_check, "detect_game_version_strict_disk_snapshot",
                        lambda *args: SimpleNamespace(value=GENERATION, path=STATIC / "artifacts/DefaultPackage.version"))
    tables = monitor.load_monitor_tables(tables_dir=ROOT / "data/raw/tables")
    assert tables.warehouse_package_generation == GENERATION
    assert tables.warehouse_game_version == GENERATION
    assert tables.rankmap_distribution_status == "ok"
    assert len(tables.items) == 1432
    assert tables.maps[6001].auction_mode == "auction_hall"
    sys.path.insert(0, str(ROOT / "hero_ref/src"))
    import ahmad_ref_engine as engine
    engine._current_rankmap_distribution_generation_status.cache_clear()
    engine._aisha_q5_total_cells_catalog_generation_status.cache_clear()
    assert engine._current_rankmap_distribution_generation_status() == ("ok", GENERATION)
    assert engine._aisha_q5_total_cells_catalog_generation_status()[0] == "ok"
