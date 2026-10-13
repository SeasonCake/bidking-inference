"""b049 5215 intake and quote scope; synthetic events, not first-live wire proof.

Original facts-only/noquote contract tests are preserved in the source before freeze.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from bidking_lab.live import fatbeans as fb, monitor
from test_pending_adaptation_copy_20260910 import panel, synthetic_snapshot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref/src"))
sys.path.insert(0, str(ROOT / "hero_ref/tools"))
import ahmad_ref_engine as engine
import hero_ref_live_schedule as schedule

GEN = "b049c3-2026-09-10-597"
SESSION = "5215:9000000000000060"
ITEM = fb.FatbeansObservedItem(local_index=20, runtime_id=9001, item_id=50101,
    quality=4, value=12000, shape_code=12, cells=2)


def event(**changes):
    result = fb.FatbeansStateEvent(sort_id=1, capture_time="2026-09-10T16:00:00+08:00",
        message_id=0x0021, session_id=SESSION, map_id=5215, round_index=4,
        skill_reveals=(fb.FatbeansSkillReveal(skill_id=4022, hero_id=207, round_index=4,
                                            observed_items=(ITEM,)),))
    return replace(result, **changes)


def capture(state):
    return fb.FatbeansCaptureEvents(packets=(), frames=(), sends=(), states=(state,), statuses=())


def test_actual_item_preserved_and_only_scoped_category_added():
    state = event()
    raw = fb._state_grid_items(state)
    scoped = fb._state_grid_items(state, current_generation=GEN, current_session_id=SESSION)
    assert len(raw) == len(scoped) == 1
    assert raw[0].category is None and scoped[0].category == 106
    assert scoped[0].categories == (106,)
    assert replace(scoped[0], category=None, categories=()) == raw[0]
    assert (scoped[0].runtime_id, scoped[0].item_id, scoped[0].quality, scoped[0].cells,
            scoped[0].local_index) == (9001, 50101, 4, 2, 20)
    assert fb._skill_reveal_category(4022) is None


@pytest.mark.parametrize("change", ["generation", "session", "missing_session", "map", "hero", "missing_hero", "state_round", "reveal_round", "settled", "conflict_hero"])
def test_scope_negatives_preserve_item_without_new_category(change):
    state, gen, sid = event(), GEN, SESSION
    reveal = state.skill_reveals[0]
    if change == "generation": gen = "68171c-2026-09-03-907"
    elif change == "session": sid = "5215:other"
    elif change == "missing_session": sid = None
    elif change == "map": state = replace(state, map_id=5214)
    elif change == "hero": state = replace(state, skill_reveals=(replace(reveal, hero_id=206),))
    elif change == "missing_hero": state = replace(state, skill_reveals=(replace(reveal, hero_id=None),))
    elif change == "state_round": state = replace(state, round_index=3)
    elif change == "reveal_round": state = replace(state, skill_reveals=(replace(reveal, round_index=3),))
    elif change == "settled": state = replace(state, message_id=0x002D)
    elif change == "conflict_hero": state = replace(state, skill_reveals=(reveal, replace(reveal, hero_id=206)))
    rows = fb._state_grid_items(state, current_generation=gen, current_session_id=sid)
    assert len(rows) == 1
    assert rows[0].category is None


def test_empty_partial_duplicate_never_manufactures_items_or_total():
    state = event()
    reveal = state.skill_reveals[0]
    assert not fb._state_grid_items(replace(state, skill_reveals=(replace(reveal, observed_items=()),)), current_generation=GEN, current_session_id=SESSION)
    duplicate = replace(state, skill_reveals=(replace(reveal, observed_items=(ITEM, ITEM)),))
    batches = fb.live_batches_from_fatbeans_events(capture(duplicate), current_generation=GEN, current_session_id=SESSION)
    assert len(batches[0].grid_items) == 1
    assert not any("total_item_count" in update.path for update in batches[0].field_updates)


@pytest.mark.parametrize("root,context", [(5215, 5215), ("5215", "5215"), (2511, 5215), (5215, 6001), (5215.5, 5215), (" 5215", None)])
def test_direct_prepared_and_impl_pending_precede_every_quote_fallback(monkeypatch, root, context):
    snapshot = {"map_id": root, "hero": "wuqilin", "total_item_count": 1,
                "ui_contract": {"context": {"map_id": context}} if context is not None else {}}
    monkeypatch.setattr(engine, "load_reference_static_data", lambda: pytest.fail("ordinary fallback reached"))
    monkeypatch.setattr(engine, "_auction_hall_6001_provisional_result", lambda *a, **k: pytest.fail("6001 alias reached"))
    original = copy.deepcopy(snapshot)
    for value in (snapshot, engine.prepare_reference_engine_snapshot(snapshot)):
        for run in (engine.run_reference_engine, engine._run_reference_engine_impl):
            result = run(value).as_dict()
            assert result["status"] == "no_reachable_combo"
            assert [result[k] for k in ("conservative", "balanced", "aggressive")] == [None] * 3
            assert any(note.startswith("wuqilin_5215_adaptation_pending:") for note in result["notes"])
            assert result["evidence"]["total_count"] is None
    assert snapshot == original


@pytest.mark.parametrize("map_id", [5214, 5216, 5208, 5211, 5219, 2511, 4511])
def test_negative_guard_does_not_capture_siblings(map_id):
    assert engine._wuqilin_5215_pending_scope_state({"map_id": map_id}) is None


def scope_snapshot():
    source = dict(map_id=5215, source_generation=GEN, map_profile_generation=GEN,
                  game_version=GEN, source_conflict=False, quarantine_required=False)
    return dict(map_id=5215, hero="wuqilin", session_id=SESSION, warehouse_source=source,
                skill_reveal_rows=[dict(hero_id=207, skill_id=4022, state_round_index=4,
                    session_id=SESSION, observed_items=[dict(runtime_id=9001, quality=4)])])


def test_current_schedule_ready_is_separate_from_quote_permission():
    snapshot = scope_snapshot()
    assert schedule.hero_quote_ready(snapshot, "wuqilin", 4) == (True, "")
    assert engine.run_reference_engine(snapshot).balanced is None
    snapshot["skill_reveal_rows"] = []
    assert schedule.hero_quote_ready(snapshot, "wuqilin", 4) == (True, "")
    assert schedule.get_hero_schedule("wuqilin").skill_round_labels[4] == "R4古董1/3完整信息"


@pytest.mark.parametrize("key,value", [("session_id", "5215:old"), ("state_round_index", 3), ("hero_id", 206), ("skill_id", 1002074)])
def test_stale_wrong_round_or_hero_skill_does_not_grant_quote(key, value):
    snapshot = scope_snapshot()
    snapshot["skill_reveal_rows"][0][key] = value
    assert engine.run_reference_engine(snapshot).balanced is None


def test_monitor_blocks_mc_and_preserves_actual_minimap(monkeypatch):
    # Exact producer generation is a synthetic envelope; all product parsers,
    # session selection, batches and artifact consumers below are real.
    monkeypatch.setattr(monitor, "_fresh_session_game_version", lambda *_: (GEN, True))
    tables = monitor.MonitorTables(maps={}, drops={}, items={},
        warehouse_package_generation=GEN, warehouse_game_version=GEN, warehouse_live_identity_check=True)
    artifact = monitor.build_monitor_artifact_from_events(capture(event()), tables=tables,
        n_trials=1, roi_trials=1, run_debug_shadows=False)
    assert artifact["formal_mode_reason"] == "wuqilin_5215_provisional_ref_only"
    assert artifact["balanced"] is None and artifact["model_map_id"] == 5215
    assert artifact["inference_input_constraints"]["quote_available"] is True
    # Missing map-table identity still blocks Hero Ref; generic MC never quotes.
    assert engine.run_reference_engine(artifact).balanced is None
    assert len(artifact["minimap_grid_items"]) == 1


def test_real_summary_pending_does_not_request_unavailable_skill(panel, tmp_path):
    snapshot = synthetic_snapshot(map_id=5215)
    snapshot["hero"] = snapshot["ui_contract"]["context"]["hero"] = "wuqilin"
    snapshot["formal_mode_reason"] = "wuqilin_5215_adaptation_pending"
    summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "synthetic.json")
    assert summary["reference"]["action"] == "当前仓库待适配"
    assert summary["evidence"]["next_info_hint"] == "本局暂不提供估值"
