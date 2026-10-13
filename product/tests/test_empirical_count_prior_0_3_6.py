"""Latest-generation count-center integration, without historical-round claims."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref/src"))
import ahmad_ref_engine as engine
from bidking_lab.extract.bid_map_table import parse_bid_map_table
from bidking_lab.live.warehouse_source import classify_from_map_table
from bidking_lab.runtime.snapshot import ui_contract_from_artifact

GEN = "76b7ca-2026-10-08-723"
TEXT = Path(os.environ.get("BIDKING_20261008_TEXT_ROOT", "C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/content/textassets"))


class MirrorInteger(int):
    pass


class MirrorString(str):
    pass


class SourceDict(dict):
    pass


def snapshot(hero="aisha", map_id=2501, total_count=None):
    rows = [line.split("\t") for line in (TEXT / "BidMap.txt").read_text(encoding="utf-8-sig").splitlines()]
    maps = parse_bid_map_table(rows)
    source = classify_from_map_table(map_id, maps, source_generation=GEN, profile_generation=GEN, game_version=GEN, owner_evidence_status="absent").as_dict()
    value = dict(map_id=map_id, model_map_id=map_id, hero=hero, session_id=f"{map_id}:123456789", phase="bidding", round=1,
                 action_round=1, observed_round=1, warehouse_source=source, warehouse_source_generation=GEN,
                 warehouse_source_qualification=source["qualification"], official_distribution_eligible=source["official_distribution_eligible"],
                 warehouse_game_version_session_stable=True, structured_ref_inputs={} if total_count is None else {"total_count": total_count})
    value["ui_contract"] = ui_contract_from_artifact(value)
    return value


def selected(value):
    return engine._empirical_count_prior_036(value, engine.extract_evidence(value))


@pytest.mark.parametrize("hero", ["aisha", "ahmed"])
def test_same_map_train_prior_available_to_both_heroes(hero):
    assert selected(snapshot(hero)) == (42, 396)


@pytest.mark.parametrize("path,value", [
    (("warehouse_game_version_session_stable",), False),
    (("warehouse_game_version_session_stable",), 1),
    (("session_id",), "2501:old"),
    (("model_map_id",), 2502),
    (("warehouse_source_generation",), "b049c3-2026-09-10-597"),
    (("warehouse_source", "game_version"), GEN + "-extra"),
    (("warehouse_source", "owner_evidence_status"), "valid"),
    (("warehouse_source", "qualification"), "player_custom_warehouse"),
    (("warehouse_source", "qualification"), []),
    (("warehouse_source", "qualification"), {}),
    (("warehouse_source", "qualification"), True),
    (("warehouse_source", "map_table_sha256"), "0" * 64),
    (("warehouse_source", "source_conflict"), True),
    (("warehouse_source", "config"), {"synthetic": True}),
    (("ui_contract", "context", "session_id"), "2501:987654321"),
    (("ui_contract", "context", "hero"), "ethan"),
    (("hero_id",), 208),
])
def test_source_or_runtime_conflict_never_promotes_prior(path, value):
    snap = snapshot()
    node = snap
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert selected(snap) is None


def test_other_hero_old_missing_generation_manual_and_hard_N_unchanged(monkeypatch):
    assert selected(snapshot("ethan")) is None
    assert selected(snapshot(total_count=19)) is None
    snap = snapshot()
    snap["phase"] = "manual"
    assert selected(snap) is None
    snap = snapshot()
    del snap["warehouse_source"]
    del snap["ui_contract"]
    assert selected(snap) is None
    monkeypatch.setenv("BIDKING_DISABLE_EMPIRICAL_COUNT_PRIOR_036", "1")
    assert selected(snapshot()) is None


def test_candidate_center_keeps_hard_count_and_original_window_width():
    evidence = engine.extract_evidence(snapshot(total_count=19))
    counts, _ = engine._total_count_candidates(evidence, [], empirical_count_center=42)
    assert counts == [19]
    evidence = engine.extract_evidence(snapshot())
    old, old_center = engine._total_count_candidates(evidence, [])
    new, new_center = engine._total_count_candidates(evidence, [], empirical_count_center=42)
    assert (old_center, new_center) == (33, 42)
    assert old == list(range(29, 38))
    assert new == list(range(38, 47))


def test_frozen_recipe_projection_and_no_quality_replacement():
    recipe = Path("C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/precision/FROZEN_COMPONENT_PRIOR.json")
    data = json.loads(recipe.read_text(encoding="utf-8"))
    assert engine.EMPIRICAL_COUNT_PRIOR_036_CENTERS == {int(k): (v["count_center"], v["train_sessions"]) for k, v in data["models"].items()}
    assert engine.EMPIRICAL_COUNT_PRIOR_036_RECIPE_SHA256 == "61b96da86b84815f01031a72b997ed17ab21652f35175de708c54f83b4944066"
    static = engine.load_reference_static_data()
    before = engine._quality_probabilities(2501, static)
    selected(snapshot())
    assert engine._quality_probabilities(2501, static) == before


def test_actual_engine_counterfactual_and_future_labels(monkeypatch):
    snap = snapshot()
    new = engine.run_reference_engine(snap, max_combos=3000).as_dict()
    assert any(n.startswith("empirical_count_prior_036:2501:42:396") for n in new["notes"]), new["notes"]
    assert new["balanced"] is not None
    future = copy.deepcopy(snap)
    future.update(inventory_count=99, inventory_cells=999, known_value_sum=999999999,
                  final_quality_counts="q6=99", final_quality_cells="q6=999", final_quality_values="q6=999999999")
    assert engine.run_reference_engine(future, max_combos=3000).as_dict() == new
    monkeypatch.setenv("BIDKING_DISABLE_EMPIRICAL_COUNT_PRIOR_036", "1")
    old = engine.run_reference_engine(snap, max_combos=3000).as_dict()
    assert not any(n.startswith("empirical_count_prior_036:") for n in old["notes"])
    assert old["total_count_estimate"] != new["total_count_estimate"]


def test_next_snapshot_does_not_inherit_prior_or_lose_exact_count():
    assert selected(snapshot()) == (42, 396)
    next_snap = snapshot(total_count=19)
    assert selected(next_snap) is None
    out = engine.run_reference_engine(next_snap, max_combos=3000).as_dict()
    assert out["total_count_estimate"] == 19
    assert not any(n.startswith("empirical_count_prior_036:") for n in out["notes"])


@pytest.mark.parametrize("path,value", [
    (("epoch",), 1),
    (("session_epoch",), "opaque"),
    (("session_epoch",), True),
    (("epoch",), "0123456789"),
    (("ui_contract", "context", "session_epoch"), 2),
    (("ui_contract", "source", "epoch"), 2),
])
def test_r1_optional_epoch_alias_is_bound_to_session(path, value):
    snap = snapshot()
    node = snap
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    assert selected(snap) is None


@pytest.mark.parametrize("tail", ["0", "0123456789", "18446744073709551616", "1" * 100])
def test_r1_session_epoch_is_positive_canonical_runtime_integer(tail):
    snap = snapshot()
    snap["session_id"] = f"2501:{tail}"
    snap["ui_contract"]["context"]["session_id"] = snap["session_id"]
    assert selected(snap) is None


def test_r1_matching_optional_aliases_or_no_aliases_remain_valid():
    snap = snapshot()
    assert selected(snap) == (42, 396)
    snap["epoch"] = 123456789
    snap["ui_contract"]["context"]["session_epoch"] = "123456789"
    snap["ui_contract"].setdefault("source", {})["epoch"] = 123456789
    assert selected(snap) == (42, 396)
    snap["ui_contract"]["context"]["session_epoch"] = 2
    assert selected(snap) is None


def test_r2_oversized_optional_epoch_is_ineligible_without_throwing():
    snap = snapshot()
    snap["epoch"] = "1" * 5000
    assert selected(snap) is None


@pytest.mark.parametrize("field,variant", [
    ("hero_id", MirrorInteger),
    ("warehouse_source_generation", MirrorString),
    ("warehouse_source", SourceDict),
])
def test_r2_rejected_type_only_change_invalidates_prior_cache_key(field, variant):
    from bidking_lab.live.live_refresh_signatures import ref_input_signature_from_snapshot
    good = snapshot("ahmed")
    good["hero_id"] = 204
    changed = copy.deepcopy(good)
    changed[field] = variant(good[field])
    assert selected(good) == (42, 396)
    assert selected(changed) is None
    assert ref_input_signature_from_snapshot(changed) != ref_input_signature_from_snapshot(good)


@pytest.mark.parametrize("kind", ["root_key_subclass", "ui_key_subclass", "long_key", "too_many_keys"])
def test_r2_rejected_owner_shape_is_part_of_cache_eligibility(kind):
    from bidking_lab.live.live_refresh_signatures import ref_input_signature_from_snapshot
    good = snapshot("ahmed")
    changed = copy.deepcopy(good)
    assert engine._WAREHOUSE_QUOTE_SOURCE_CONTAINER_MAX_KEYS == 512
    assert engine._WAREHOUSE_QUOTE_SOURCE_KEY_MAX_LENGTH == 128
    if kind == "root_key_subclass":
        changed[MirrorString("hero")] = changed.pop("hero")
    elif kind == "ui_key_subclass":
        ui = changed["ui_contract"]
        ui[MirrorString("context")] = ui.pop("context")
    elif kind == "long_key":
        changed["x" * 129] = None
    else:
        changed.update({f"synthetic_extra_{i}": None for i in range(512)})
    assert selected(changed) is None
    assert ref_input_signature_from_snapshot(changed) != ref_input_signature_from_snapshot(good)


def test_r1_signature_includes_every_prior_owner_and_preserves_bid_only():
    from bidking_lab.live.live_refresh_signatures import (
        EMPIRICAL_COUNT_PRIOR_036_MAP_IDS, ref_input_signature_from_snapshot,
    )
    assert set(EMPIRICAL_COUNT_PRIOR_036_MAP_IDS) == set(engine.EMPIRICAL_COUNT_PRIOR_036_CENTERS)
    good = snapshot()
    signature = ref_input_signature_from_snapshot(good)
    for path, value in [
        (("warehouse_source_generation",), "old-generation"),
        (("warehouse_game_version_session_stable",), False),
        (("warehouse_source", "source_conflict"), True),
        (("warehouse_source", "qualification"), "player_custom_warehouse"),
        (("epoch",), 2),
        (("hero_id",), 208),
        (("ui_contract", "context", "hero"), "ethan"),
        (("ui_contract", "source", "epoch"), 2),
        (("ui_contract", "source"), []),
        (("ui_contract", "context"), []),
        (("ui_contract",), []),
    ]:
        mutated = copy.deepcopy(good)
        node = mutated
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        assert selected(mutated) is None, path
        assert ref_input_signature_from_snapshot(mutated) != signature, path
    bids = copy.deepcopy(good)
    bids["latest_bids"] = {"synthetic": 50000}
    bids["ui_contract"].setdefault("baseline", {}).setdefault("decision", {})["current_highest"] = "50000"
    assert ref_input_signature_from_snapshot(bids) == signature


def test_r1_real_pending_cache_recomputes_on_source_change(tmp_path, monkeypatch):
    # Exercise the actual cache producer/consumer without constructing Tk or
    # starting a worker, helper, game, network request or real filesystem input.
    monkeypatch.syspath_prepend(str(ROOT / "hero_ref/tools"))
    import ahmad_tk_overlay as overlay
    import ahmad_live_panel_server as panel

    good = snapshot("ahmed")
    good["hero_id"] = 204
    good["structured_ref_inputs"] = {"min_counts": {"q6": 1}}
    good["inference_signature"] = ["synthetic_monitor", 2501, 1, "bidding", "ahmed"]
    obj = object.__new__(overlay.AhmadTkOverlay)
    obj.snapshot_path = tmp_path / "nonexistent-snapshot.json"
    obj._ref_cache_signature = None
    obj._ref_cache_summary = None
    summary = panel.summarize_snapshot(good, snapshot_path=obj.snapshot_path)
    obj._cache_live_summary_result(good, summary)
    assert obj._ref_cache_summary is summary
    assert any(n.startswith("empirical_count_prior_036:") for n in summary["ahmed_ref"]["notes"])

    for field, value, expected in [
        ("warehouse_source_generation", "old-generation", "worker"),
        ("warehouse_game_version_session_stable", False, "worker"),
        ("epoch", 2, "worker"),
        ("hero_id", MirrorInteger(204), "worker"),
        ("warehouse_source_generation", MirrorString(GEN), "worker"),
        ("warehouse_source", SourceDict(good["warehouse_source"]), "worker"),
        ("latest_bids", {"synthetic": 50000}, "cache"),
    ]:
        current = copy.deepcopy(good)
        current[field] = value
        observed = []
        obj._summary_worker_pending = {"snapshot": current, "signature": obj._live_inference_signature(current), "started_at": 0.0}
        obj._mark_summary_performance = lambda *a, **k: None
        obj._apply_live_summary = lambda *a, **k: observed.append("cache")
        obj._clear_compute_pending = lambda *a, **k: None
        obj._record_ui_runtime_status = lambda *a, **k: None
        obj._start_live_summary_worker = lambda **k: observed.append("worker")
        obj._maybe_start_pending_summary_worker()
        assert observed == [expected], field
