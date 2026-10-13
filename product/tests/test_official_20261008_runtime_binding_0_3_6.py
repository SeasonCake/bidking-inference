"""Exact new profile selection at the real runtime binder, not only build data."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from bidking_lab.extract import official_20261008_model_profile as profile
from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.extract.item_table import load_item_table
from bidking_lab.live.current_rankmap_runtime import bind_current_rankmap_algorithm_prior
from bidking_lab.simulation.basic_mc import DropPoolResolutionError, flatten_pool

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(os.environ.get("BIDKING_20261008_MODEL_ROOT", "C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/content/src2a_candidate"))


@pytest.fixture
def packaged_inputs(tmp_path):
    manifest = {"baseline_id": profile.PACKAGE_BASELINE_ID, "files": {}}
    for relative, pin in profile.OUTPUT_IDENTITIES.items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODEL_ROOT / relative, destination)
        manifest["files"][relative] = {"size": pin["bytes"], "sha256": pin["sha256"]}
    relative = "data/raw/tables/RankMap.txt"
    raw = (ROOT / relative).read_bytes()
    (tmp_path / relative).write_bytes(raw)
    manifest["files"][relative] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/package_data_fingerprints.json").write_text(json.dumps(manifest), encoding="utf-8")
    maps = load_bid_map_table(tmp_path / "data/raw/tables/BidMap.txt")
    items = load_item_table(tmp_path / "data/raw/tables/Item.txt")
    return tmp_path, maps, items


def bind(inputs):
    root, maps, items = inputs
    return bind_current_rankmap_algorithm_prior(package_root=root, maps=maps, drops={}, items=items)


def test_new_profile_binds_all_physical_maps_without_claiming_official_rates(packaged_inputs):
    pools, status, receipt = bind(packaged_inputs)
    assert status == "ok"
    assert len(pools.bound_map_ids) == 112
    assert receipt["package_generation"] == profile.GENERATION
    assert receipt["model_distribution_authority"] is True
    assert receipt["official_distribution_authority"] is False
    assert receipt["runtime_open_claim"] is False
    assert receipt["scope_map_count"] == 112
    root, maps, _ = packaged_inputs
    weights = json.loads((root / "data/processed/drop_weights_all_maps_0627.json").read_text())
    weights.update(json.loads((root / "data/processed/rankmap_drop_weights_current.json").read_text()))
    grouped = defaultdict(list)
    for key, model in weights.items():
        mid = int(key)
        pid = maps[mid].drop_pool_id
        grouped[pid].append(mid)
        flat = flatten_pool(pid, pools, {}, map_id=mid)
        expected = {int(iid): row[0] for iid, row in model["items"].items() if row[0] > 0}
        total = sum(expected.values())
        assert dict(zip(flat.item_ids, flat.probabilities)) == pytest.approx({iid: p / total for iid, p in expected.items()})
    shared = [pid for pid, members in grouped.items() if len(members) > 1]
    assert len(shared) == 39
    with pytest.raises(DropPoolResolutionError, match="map_context_required"):
        flatten_pool(shared[0], pools, {})


@pytest.mark.parametrize("mutation", ["wrong_baseline", "wrong_manifest_hash", "changed_model", "missing_map", "wrong_pool", "wrong_value"])
def test_mixed_or_changed_inputs_do_not_select_new_profile(packaged_inputs, mutation):
    root, maps, items = packaged_inputs
    manifest_path = root / "scripts/package_data_fingerprints.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "wrong_baseline":
        manifest["baseline_id"] += "-mixed"
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "wrong_manifest_hash":
        manifest["files"]["data/processed/drop_weights_all_maps_0627.json"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "changed_model":
        path = root / "data/processed/drop_weights_all_maps_0627.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "missing_map":
        maps.pop(2501)
    elif mutation == "wrong_pool":
        maps[2501] = maps[2501].model_copy(update={"drop_pool_id": 999999})
    else:
        items = dict(items)
        item = next(value for value in items.values() if value.item_id == 1016015)
        items[item.item_id] = item.model_copy(update={"value": item.value + 1})
    pools, status, receipt = bind((root, maps, items))
    assert status != "ok"
    assert pools == {} and receipt == {}


def test_new_runtime_pins_match_builder_profile_without_retargeting_b049():
    from bidking_lab.extract import current_rankmap_package_contract as contract
    assert contract.CURRENT_PACKAGE_GENERATION == "b049c3-2026-09-10-597"
    descriptor = contract.OFFICIAL_20261008_RUNTIME_MODEL
    assert descriptor["generation"] == profile.GENERATION
    for relative, pin in descriptor["files"].items():
        if relative in profile.OUTPUT_IDENTITIES:
            assert {key: pin[key] for key in ("bytes", "sha256")} == dict(profile.OUTPUT_IDENTITIES[relative])


def test_actual_monitor_loader_selects_current_map_models(packaged_inputs, monkeypatch):
    from bidking_lab.live import monitor, version_check
    root, _, _ = packaged_inputs
    shutil.copyfile(ROOT / "data/raw/tables/Drop.txt", root / "data/raw/tables/Drop.txt")
    monkeypatch.setattr(version_check, "detect_game_version_strict_disk_snapshot",
                        lambda: SimpleNamespace(value=profile.GENERATION, path=Path("synthetic-game-version")))
    tables = monitor.load_monitor_tables(tables_dir=root / "data/raw/tables")
    assert tables.warehouse_package_generation == profile.GENERATION
    assert tables.rankmap_distribution_status == "ok"
    assert len(tables.drops.bound_map_ids) == 112
    weights = monitor._effective_item_weights_for_map(2501, maps=tables.maps, drops=tables.drops, items=tables.items)
    assert sum(weights.values()) == pytest.approx(1)
    assert 1016015 in weights and 1916001 not in weights
