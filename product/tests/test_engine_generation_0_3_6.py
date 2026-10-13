"""Actual 0.3.6 package-input consumers, not a historical quote replay."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref/src"))
import ahmad_ref_engine as engine
from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.live.warehouse_source import classify_from_map_table
from bidking_lab.runtime.snapshot import ui_contract_from_artifact

GENERATION = "76b7ca-2026-10-08-723"
BASELINE = "package-data-2026-10-08-76b7ca-0.3.6"
HALLOWEEN = {1016015, 1046014, 1046015, 1046016, 1046017, 1096016}


def snapshot(hero="aisha", map_id=2501, inputs=None):
    maps = load_bid_map_table(ROOT / "data/raw/tables/BidMap.txt")
    source = classify_from_map_table(
        map_id, maps, source_generation=GENERATION, profile_generation=GENERATION,
        game_version=GENERATION, owner_evidence_status="absent",
    ).as_dict()
    result = dict(
        map_id=map_id, model_map_id=map_id, hero=hero,
        session_id=f"{map_id}:123456789", phase="bidding", round=1,
        action_round=1, observed_round=1, warehouse_source=source,
        warehouse_source_generation=GENERATION,
        warehouse_source_qualification=source["qualification"],
        official_distribution_eligible=source["official_distribution_eligible"],
        warehouse_game_version_session_stable=True,
        structured_ref_inputs=dict(inputs or {}),
    )
    result["ui_contract"] = ui_contract_from_artifact(result)
    return result


def test_actual_package_common_gates_and_all_112_maps():
    assert engine.ROOT.resolve() == ROOT.resolve()
    assert engine._current_rankmap_distribution_generation_status() == ("ok", GENERATION)
    assert engine._aisha_q5_total_cells_catalog_generation_status() == ("ok", BASELINE)
    weights = engine._load_drop_weight_items()
    ordinary = json.loads((ROOT / "data/processed/drop_weights_all_maps_0627.json").read_bytes())
    ranked = json.loads((ROOT / "data/processed/rankmap_drop_weights_current.json").read_bytes())
    assert set(weights) == set(map(int, ordinary)) | set(map(int, ranked))
    assert len(weights) == 112
    assert set(weights) == engine.CURRENT_MODEL_AUTHORITY_MAP_IDS
    assert not {6001, 2511, 4511} & weights.keys()
    for map_id, payload in weights.items():
        selected = ranked.get(str(map_id), ordinary.get(str(map_id)))
        assert payload == selected
        probabilities = [row[0] for row in payload["items"].values()]
        assert all(math.isfinite(value) and value >= 0 for value in probabilities)
        assert math.isclose(sum(probabilities), 1.0, abs_tol=1e-10)


def test_actual_catalog_new_items_retired_support_and_model_mass():
    weights = engine._load_drop_weight_items()
    ordinary = set(map(int, weights[2501]["items"]))
    ranked = set(map(int, weights[5601]["items"]))
    assert (len(ordinary), len(ranked), len(ordinary | ranked)) == (675, 684, 690)
    assert HALLOWEEN <= ordinary
    assert not HALLOWEEN & ranked
    from bidking_lab.extract.item_table import load_item_table
    items = load_item_table(ROOT / "data/raw/tables/Item.txt")
    hall_only = {key for key, row in items.items() if json.loads(row.raw_row[39]) == [601]}
    assert len(hall_only) == 45
    assert not hall_only & (ordinary | ranked)
    static = engine.load_reference_static_data()
    for map_id in (2501, 2521, 2101, 5601, 5606):
        actual, note = engine._quality_probabilities(map_id, static)
        assert note == f"drop_table_prob:{map_id}"
        assert actual == {key: max(1e-9, value / sum(static["drop_table_priors"][map_id]["quality_probs"].values()))
                          for key, value in static["drop_table_priors"][map_id]["quality_probs"].items()}
    for item_id in HALLOWEEN:
        row = items[item_id]
        assert weights[2501]["items"][str(item_id)][1] == row.value
        assert engine._normalized_red_value({"item_id": item_id, "value": row.value}) == weights[2501]["items"][str(item_id)][2]


@pytest.mark.parametrize("hero", ["aisha", "ahmed"])
@pytest.mark.parametrize("map_id", [2501, 2521, 5601, 5606])
def test_both_hero_actual_quotes_and_explicit_count_remain_usable(hero, map_id):
    value = snapshot(hero, map_id, {"total_count": 19})
    before = copy.deepcopy(value)
    out = engine.run_reference_engine(value, max_combos=3000).as_dict()
    assert out["balanced"] is not None, out["notes"]
    assert 0 < out["conservative"] <= out["balanced"] <= out["aggressive"]
    assert out["total_count_estimate"] == 19
    assert out["evidence"]["total_count"] == 19
    assert not any(note.startswith("empirical_count_prior_036:") for note in out["notes"])
    assert value == before


@pytest.mark.parametrize("hero", ["aisha", "ahmed"])
def test_exact_quality_count_cells_and_value_are_not_replaced_by_new_priors(hero):
    value = snapshot(hero, 2501, {
        "total_count": 3, "fixed_counts": {"q1": 0, "q3": 0, "q4": 0, "q5": 1, "q6": 2},
        "quality_cells": {"q5": 4, "q6": 6}, "quality_values": {"q5": 50000, "q6": 300000},
    })
    out = engine.run_reference_engine(value, max_combos=3000).as_dict()
    assert out["balanced"] is not None, out["notes"]
    assert out["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert out["quality_cells_ranges"]["q6"] == [6, 6, 6]
    higher = copy.deepcopy(value)
    higher["structured_ref_inputs"]["quality_values"]["q6"] = 800000
    higher["ui_contract"] = ui_contract_from_artifact(higher)
    high = engine.run_reference_engine(higher, max_combos=3000).as_dict()
    assert high["value_p50"] > out["value_p50"]


@pytest.mark.parametrize("relative", list(engine._CURRENT_RANKMAP_GENERATION_FILES))
def test_common_gate_actually_rejects_each_changed_or_missing_input(relative, monkeypatch):
    original = engine._aisha_q5_total_cells_plaintext_identity
    engine._current_rankmap_distribution_generation_status.cache_clear()
    try:
        for bad, expected in ((None, "authority_input_unavailable"), ((1, "0" * 64), "authority_input_hash_mismatch")):
            monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity",
                                lambda path: bad if Path(path) == ROOT / relative else original(path))
            engine._current_rankmap_distribution_generation_status.cache_clear()
            assert engine._current_rankmap_distribution_generation_status() == (expected, "")
    finally:
        engine._current_rankmap_distribution_generation_status.cache_clear()


def test_new_epoch_has_exact_recorded_manifest_and_old_offline_leaf_retained():
    manifest = json.loads((ROOT / "scripts/package_data_fingerprints.json").read_bytes())
    assert manifest["baseline_id"] == BASELINE
    assert len(manifest["files"]) == 21
    for relative, record in manifest["files"].items():
        raw = (ROOT / relative).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (record["size"], record["sha256"])
    # This offline file is deliberately not a new live inference dependency.
    assert manifest["files"]["data/processed/quality_combo_presolve_q456.json"]["size"] == 15444339


@pytest.mark.parametrize("map_id", [2501, 5601])
@pytest.mark.parametrize("relative", ["data/processed/drop_weights_all_maps_0627.json",
                                    "data/processed/items_droppable.json"])
def test_warm_models_requalify_and_recover_without_manual_cache_clear(map_id, relative, monkeypatch):
    value = snapshot("ahmed", map_id, {"total_count": 19})
    original = engine._aisha_q5_total_cells_plaintext_identity
    assert len(engine._load_drop_weight_items()) == 112
    assert engine.run_reference_engine(value, max_combos=1000).balanced is not None
    for bad in (None, (1, "0" * 64)):
        monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity",
                            lambda path: bad if Path(path) == ROOT / relative else original(path))
        assert engine._load_drop_weight_items() == {}
        result = engine.run_reference_engine(value, max_combos=1000)
        assert result.balanced is None, result.notes
        assert engine._REFERENCE_INPUT_CHECKS.get() is None
        monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity", original)
        assert len(engine._load_drop_weight_items()) == 112
        assert engine.run_reference_engine(value, max_combos=1000).balanced is not None


def test_unavailable_catalog_does_not_stick_after_inputs_recover(monkeypatch):
    engine._aisha_q5_total_cells_catalog_generation_status.cache_clear()
    original = engine._aisha_q5_total_cells_plaintext_identity
    monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity",
                        lambda path: None if Path(path).name == "items_droppable.json" else original(path))
    assert engine._aisha_q5_total_cells_catalog_generation_status()[0] == "catalog_generation_unavailable"
    monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity", original)
    assert engine._aisha_q5_total_cells_catalog_generation_status() == ("ok", BASELINE)


def test_payload_read_failure_is_not_a_permanent_negative_cache(monkeypatch):
    engine._current_rankmap_distribution_generation_status.cache_clear()
    original = engine._moat_json
    def unavailable(path):
        if Path(path) == engine.CURRENT_RANKMAP_AUTHORITY_PATH:
            raise OSError("synthetic transient decode read")
        return original(path)
    monkeypatch.setattr(engine, "_moat_json", unavailable)
    assert engine._current_rankmap_distribution_generation_status()[0] == "authority_payload_unavailable"
    monkeypatch.setattr(engine, "_moat_json", original)
    assert engine._current_rankmap_distribution_generation_status() == ("ok", GENERATION)
