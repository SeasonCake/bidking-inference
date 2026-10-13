"""Current model support -> real count/cell presolve, with independent bit DP."""
from __future__ import annotations

import importlib.util
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(os.environ.get("BIDKING_20261008_MODEL_ROOT", "C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/content/src2a_candidate"))
spec = importlib.util.spec_from_file_location("presolve_builder_036", ROOT / "scripts/build_quality_combo_presolve.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture(scope="module")
def current_payload():
    return builder.modeled_presolve_payload(MODEL_ROOT)


def test_all_112_maps_and_missing_old_31_are_really_generated(current_payload):
    payload = current_payload
    ordinary = json.loads((MODEL_ROOT / "data/processed/drop_weights_all_maps_0627.json").read_text())
    ranked = json.loads((MODEL_ROOT / "data/processed/rankmap_drop_weights_current.json").read_text())
    assert set(payload["maps"]) == ordinary.keys() | ranked.keys()
    assert len(payload["maps"]) == 112
    assert payload["model_generation"] == "76b7ca-2026-10-08-723"
    assert payload["model_distribution_authority"] is True
    assert {str(mid) for mid in (*range(2521, 2531), *range(4521, 4531), *range(5601, 5612))} <= payload["maps"].keys()
    assert not {"6001", "2511", "4511"} & payload["maps"].keys()


def test_every_count_cell_set_matches_independent_model_area_bit_dp(current_payload):
    items = builder.load_item_table(MODEL_ROOT / "data/raw/tables/Item.txt")
    maps = builder.load_bid_map_table(MODEL_ROOT / "data/raw/tables/BidMap.txt")
    weights = json.loads((MODEL_ROOT / "data/processed/drop_weights_all_maps_0627.json").read_text())
    weights.update(json.loads((MODEL_ROOT / "data/processed/rankmap_drop_weights_current.json").read_text()))
    for key, modeled in weights.items():
        for quality in (4, 5, 6):
            areas = {items[int(iid)].shape_w * items[int(iid)].shape_h
                     for iid, row in modeled["items"].items()
                     if row[0] > 0 and items[int(iid)].quality == quality}
            expected = {}
            bits = 1
            for count in range(1, maps[int(key)].items_per_session_max + 1):
                following = 0
                for area in areas:
                    following |= bits << area
                bits = following
                if bits:
                    expected[str(count)] = [cell for cell in range(bits.bit_length()) if (bits >> cell) & 1]
            assert current_payload["maps"][key][str(quality)] == expected, (key, quality)


def test_independent_geometry_checker_detects_a_wrong_cell_set(current_payload):
    corrupted = copy.deepcopy(current_payload)
    corrupted["maps"]["2501"]["6"]["1"].append(99999)
    with pytest.raises(AssertionError):
        test_every_count_cell_set_matches_independent_model_area_bit_dp(corrupted)


def test_count_limit_and_unmodeled_scope_are_explicit():
    payload = builder.modeled_presolve_payload(MODEL_ROOT, map_ids=[2501], max_count=2)
    assert set(payload["maps"]) == {"2501"}
    assert all(set(rows) <= {"1", "2"} for rows in payload["maps"]["2501"].values())
    with pytest.raises(ValueError, match="outside"):
        builder.modeled_presolve_payload(MODEL_ROOT, map_ids=[6001])
    with pytest.raises(ValueError, match="positive"):
        builder.modeled_presolve_payload(MODEL_ROOT, max_count=0)


def test_wrong_generation_input_never_overwrites_destination(tmp_path):
    from bidking_lab.extract.official_20261008_model_profile import OUTPUT_IDENTITIES
    root = tmp_path / "model"
    for relative in OUTPUT_IDENTITIES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODEL_ROOT / relative, destination)
    source = root / "data/processed/items.json"
    source.write_bytes(source.read_bytes() + b" ")
    destination = tmp_path / "presolve.json"
    destination.write_bytes(b"preserve-existing-output")
    completed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_quality_combo_presolve.py"),
                                "--model-root", str(root), "--out", str(destination)],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode != 0
    assert destination.read_bytes() == b"preserve-existing-output"
