"""Current-input activity consumers and explicit historical parameter transfers."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys

import pytest

from test_engine_generation_0_3_6 import GENERATION, ROOT, engine, snapshot

sys.path.insert(0, str(ROOT / "hero_ref/tools"))
import ahmad_live_panel_server as panel
import wuqilin_5215_quote_model as model

BASELINE = Path("C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/baseline_package_data")


@pytest.mark.parametrize("map_id", [2511, 4511])
@pytest.mark.parametrize("hero", ["aisha", "ahmed"])
def test_current_shipwreck_actual_engine_uses_transfer_not_legacy_fallback(map_id, hero):
    value = snapshot(hero, map_id, {"total_count": 38})
    result = engine.run_reference_engine(value, max_combos=3000).as_dict()
    assert result["balanced"] is not None, result["notes"]
    metadata = result["empirical_calibration"]
    assert metadata["schema"] == "shipwreck_calibrated_transfer_036_v1"
    assert metadata["generation"] == GENERATION
    assert metadata["parameter_generation"] == model.GENERATION
    assert metadata["replay_eligible"] is False
    assert metadata["official_distribution_authority"] is False
    assert result["total_count_estimate"] == 38
    assert not panel.activity_2511_provisional_quote_revoked(value, {"ahmed_ref": result})
    stale = copy.deepcopy(result)
    stale["empirical_calibration"]["generation"] = model.GENERATION
    assert panel.activity_2511_provisional_quote_revoked(value, {"ahmed_ref": stale})


def test_shipwreck_transfer_is_raw_catalog_ratio_with_historical_parameters_intact():
    old_weights = json.loads((BASELINE / "data/processed/drop_weights_all_maps_0627.json").read_bytes())
    new_weights = json.loads((ROOT / "data/processed/drop_weights_all_maps_0627.json").read_bytes())
    old_items = {r["item_id"]: r for r in json.loads((BASELINE / "data/processed/items_droppable.json").read_bytes())}
    new_items = {r["item_id"]: r for r in json.loads((ROOT / "data/processed/items_droppable.json").read_bytes())}
    def mean(weights, catalog, map_id, column):
        rows = [row for key, row in weights[str(map_id)]["items"].items() if catalog[int(key)]["quality"] == 6]
        return sum(row[0] * row[column] for row in rows) / sum(row[0] for row in rows)
    for map_id in range(2501, 2511):
        ratio = mean(new_weights, new_items, map_id, 1) / mean(old_weights, old_items, map_id, 1)
        assert math.isclose(ratio, engine.SHIPWRECK_036_RAW_Q6_CATALOG_SCALE, abs_tol=1e-12)
        fitted = mean(new_weights, new_items, map_id, 2) / mean(old_weights, old_items, map_id, 2)
        assert abs(fitted - ratio) > .01  # Wrong-unit positive control.
    for map_id in (2511, 4511):
        old = engine._shipwreck_calibrated_model(map_id)
        current = engine._shipwreck_calibrated_model(map_id, generation=GENERATION)
        assert old["generation"] == model.GENERATION
        for key in ("count_mean", "count_sd", "quality_probs", "quality_cells", "train_sessions"):
            assert current[key] == old[key]
        assert math.isclose(current["quality_values"]["q6"], old["quality_values"]["q6"] * ratio,
                            rel_tol=1e-12)  # Independently summed JSON rows differ at float rounding precision.
        assert all(current["quality_values"][key] == old["quality_values"][key] for key in ("q1", "q3", "q4", "q5"))
        assert engine._shipwreck_calibrated_model(map_id) == old


@pytest.mark.parametrize("map_id", [2511, 4511])
def test_shipwreck_real_count_cells_and_value_override_transfer(map_id):
    value = snapshot("ahmed", map_id, {
        "total_count": 3, "fixed_counts": {"q1": 0, "q3": 0, "q4": 0, "q5": 1, "q6": 2},
        "quality_cells": {"q5": 4, "q6": 6}, "quality_values": {"q5": 50000, "q6": 300000},
    })
    low = engine.run_reference_engine(value, max_combos=3000).as_dict()
    assert low["balanced"] is not None, low["notes"]
    assert low["quality_count_ranges"]["q6"] == [2, 2, 2]
    assert low["quality_cells_ranges"]["q6"] == [6, 6, 6]
    value["structured_ref_inputs"]["quality_values"]["q6"] = 800000
    from bidking_lab.runtime.snapshot import ui_contract_from_artifact
    value["ui_contract"] = ui_contract_from_artifact(value)
    assert engine.run_reference_engine(value, max_combos=3000).value_p50 > low["value_p50"]


def test_5215_current_inputs_guarantees_and_count_parameter_provenance():
    value = snapshot("wuqilin", 5215, {"total_count": 38})
    assert model.scope_state(value, expected_generation=GENERATION) == "ok"
    result = engine.run_reference_engine(value).as_dict()
    assert result["balanced"] is not None, result["notes"]
    meta = result["provisional_quote"]
    assert meta["generation"] == GENERATION
    assert meta["parameter_generation"] == model.GENERATION
    assert meta["count_prior"]["generation"] == model.GENERATION
    assert meta["count_prior"]["used"] is False
    assert meta["guarantee_violations"] == 0
    assert meta["total_count_range"] == [38, 38, 38]
    assert not panel.activity_2511_provisional_quote_revoked(value, {"ahmed_ref": result})
    for key, bad in (("generation", model.GENERATION), ("input_sha256", {path: pin[1] for path, pin in model.INPUTS.items()}),
                     ("schema", "old"), ("model_map_id", 2501), ("map_id", 2501)):
        stale = copy.deepcopy(result)
        stale["provisional_quote"][key] = bad
        assert panel.activity_2511_provisional_quote_revoked(value, {"ahmed_ref": stale})


def test_5215_old_and_new_loaders_remain_separate_and_reject_crossed_inputs():
    assert len(model.load_model(lambda path: (BASELINE / path).read_bytes())) == 10
    peers = model.load_model(lambda path: (ROOT / path).read_bytes(), generation=GENERATION)
    assert len(peers) == 10 and all(len(peer["ids"]) == 675 for peer in peers)
    for root, generation in ((ROOT, model.GENERATION), (BASELINE, GENERATION)):
        with pytest.raises(model.ModelInputError, match="model_input_identity"):
            model.load_model(lambda path: (root / path).read_bytes(), generation=generation)


@pytest.mark.parametrize("map_id,hero", [(2511, "ahmed"), (4511, "aisha"), (5215, "wuqilin")])
@pytest.mark.parametrize("field,bad", [("epoch", 2), ("session_epoch", "1" * 5000),
                                     ("warehouse_game_version_session_stable", False)])
def test_current_activity_identity_changes_revoke_without_numeric_coercion(map_id, hero, field, bad):
    value = snapshot(hero, map_id, {"total_count": 38})
    value[field] = bad
    assert engine.run_reference_engine(value, max_combos=1000).balanced is None


@pytest.mark.parametrize("map_id,hero", [(2511, "ahmed"), (5215, "wuqilin")])
def test_current_activity_warm_missing_or_changed_input_is_not_a_quote(map_id, hero, monkeypatch):
    value = snapshot(hero, map_id, {"total_count": 38})
    original = engine._aisha_q5_total_cells_plaintext_bytes
    assert engine.run_reference_engine(value, max_combos=3000).balanced is not None
    for bad in (b"{}", None):
        def reader(path):
            if Path(path).name == "items_droppable.json":
                if bad is None:
                    raise FileNotFoundError("synthetic missing current input")
                return bad
            return original(path)
        monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_bytes", reader)
        assert engine.run_reference_engine(value, max_combos=3000).balanced is None


@pytest.mark.parametrize("map_id", [2511, 5601])
def test_warmed_common_authority_cannot_hide_later_rankmap_identity_failure(map_id, monkeypatch):
    value = snapshot("ahmed", map_id, {"total_count": 38})
    original = engine._aisha_q5_total_cells_plaintext_identity
    assert engine.run_reference_engine(value, max_combos=3000).balanced is not None
    monkeypatch.setattr(engine, "_aisha_q5_total_cells_plaintext_identity",
                        lambda path: None if Path(path).name == "RankMap.txt" else original(path))
    assert engine.run_reference_engine(value, max_combos=3000).balanced is None


@pytest.mark.parametrize("map_id", [2511, 5601])
def test_fresh_authority_is_shared_only_inside_one_call_and_resets(map_id, monkeypatch):
    value = snapshot("ahmed", map_id, {"total_count": 19})
    original = engine._current_rankmap_fresh_generation_status
    calls = []
    def counted():
        calls.append(1)
        return original()
    monkeypatch.setattr(engine, "_current_rankmap_fresh_generation_status", counted)
    for _ in range(2):
        before = len(calls)
        assert engine.run_reference_engine(value, max_combos=1000).balanced is not None
        assert len(calls) == before + 1
        assert engine._REFERENCE_INPUT_CHECKS.get() is None


@pytest.mark.parametrize("map_id,hero_id,skill_id", [(2511, 204, 100204), (4511, 204, 100204), (5215, 207, 100207)])
def test_actual_current_monitor_producer_reaches_activity_engine(map_id, hero_id, skill_id, monkeypatch):
    from bidking_lab.extract.bid_map_table import load_bid_map_table
    from bidking_lab.live import fatbeans as fb, monitor
    tables = monitor.MonitorTables(
        maps=load_bid_map_table(ROOT / "data/raw/tables/BidMap.txt"), drops={}, items={},
        warehouse_package_generation=GENERATION, warehouse_game_version=GENERATION,
        warehouse_live_identity_check=True,
    )
    state = fb.FatbeansStateEvent(
        sort_id=1, capture_time="2026-10-11T14:00:00+08:00", message_id=0x21,
        session_id=f"{map_id}:123456789", map_id=map_id, round_index=1,
        skill_reveals=(fb.FatbeansSkillReveal(skill_id=skill_id, hero_id=hero_id, round_index=1),),
        public_infos=(fb.FatbeansPublicInfo(info_id=200017, map_id=map_id, value=38, value_field=2),),
    )
    monkeypatch.setattr(monitor, "_fresh_session_game_version", lambda *_: (GENERATION, True))
    artifact = monitor.build_monitor_artifact_from_events(
        fb.FatbeansCaptureEvents(packets=(), frames=(), sends=(), states=(state,), statuses=()),
        tables=tables, n_trials=1, roi_trials=1, run_debug_shadows=False,
    )
    assert artifact["model_map_id"] == map_id
    result = engine.run_reference_engine(artifact, max_combos=3000).as_dict()
    assert result["balanced"] is not None, result["notes"]
    assert result["evidence"]["total_count"] == 38


def test_current_category_authority_reaches_settlement_and_rejects_wrong_profile(monkeypatch):
    from bidking_lab.live import monitor
    from bidking_lab.data.item_category_authority import ItemCategoryAuthorityError
    profile = monitor._current_category_authority_profile(GENERATION)
    assert (profile["item_rows"], profile["item_columns"]) == (1506, 45)
    authority = monitor._settlement_category_authority(GENERATION)
    assert authority is not None
    assert authority.identity == "36600b5116701a8152af992e51e1c7d6a0492fed0e841d36c79e0de285f89dde"
    assert authority.require_random_drop_category(1016015) in range(101, 111)
    with pytest.raises(ItemCategoryAuthorityError):
        authority.require_random_drop_category(1401001)  # Guild item is visible, not random loot.
    monkeypatch.setattr(monitor, "OFFICIAL_20261008_CATEGORY_AUTHORITY_IDENTITY", "0" * 64)
    assert monitor._settlement_category_authority(GENERATION) is None


@pytest.mark.parametrize("generation,expected", [(GENERATION, 106), (model.GENERATION, 106),
                                              (GENERATION + "-mixed", None), (None, None), ([], None)])
def test_5215_r4_current_generation_classifies_only_observed_items(generation, expected):
    from bidking_lab.live import fatbeans as fb
    item = fb.FatbeansObservedItem(local_index=20, runtime_id=9001, item_id=50101,
                                  quality=4, value=12000, shape_code=12, cells=2)
    state = fb.FatbeansStateEvent(
        sort_id=1, capture_time="2026-10-11T14:00:00+08:00", message_id=0x21,
        session_id="5215:123456789", map_id=5215, round_index=4,
        skill_reveals=(fb.FatbeansSkillReveal(skill_id=4022, hero_id=207, round_index=4,
                                            observed_items=(item,)),),
    )
    rows = fb._state_grid_items(state, current_generation=generation, current_session_id=state.session_id)
    assert len(rows) == 1
    assert rows[0].category == expected
    assert (rows[0].value, rows[0].cells, rows[0].runtime_id) == (12000, 2, 9001)
