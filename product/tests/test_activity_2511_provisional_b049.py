"""Selected real pre-settlement aggregate in an explicitly synthetic b049 envelope."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

from bidking_lab.live import warehouse_source
from bidking_lab.live.live_refresh_signatures import ref_input_signature_from_snapshot
from bidking_lab.runtime.snapshot import ui_contract_from_artifact
from test_pending_adaptation_copy_20260910 import panel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref/src"))
import ahmad_ref_engine as engine

GEN = "b049c3-2026-09-10-597"
SESSION = "2511:9000000000000059"
NOTICE = "参考可用"
AGGREGATE = Path(os.environ.get("BIDKING_2511_AGGREGATE", "C:/example/bidking-workspace/scratch/bidking_035_small_hotfix_20260910_01/SELECTED_2511_PRESETTLEMENT_AGGREGATE.json"))


def current_snapshot():
    raw = AGGREGATE.read_bytes()
    assert (len(raw), hashlib.sha256(raw).hexdigest()) == (8546, "944005cbee2d0f756a0c5993861f05390a514dfa5339cb30ee8e6090149bebb7")
    aggregate = json.loads(raw)
    maps = {row["map_id"]: row for row in json.loads((ROOT / "data/processed/maps.json").read_bytes())}
    source = warehouse_source.classify_from_map_table(2511, maps, source_generation=GEN,
        profile_generation=GEN, game_version=GEN, owner_evidence_status="absent").as_dict()
    snapshot = dict(map_id=2511, model_map_id=2511, hero="ahmed", hero_id=204,
        session_id=SESSION, phase="bidding", round=5, action_round=5, observed_round=5,
        created_at=time.time(), warehouse_source=source, warehouse_game_version_session_stable=True,
        warehouse_source_generation=GEN, warehouse_source_qualification=source["qualification"],
        official_distribution_eligible=False, formal_mode_reason="activity_2511_provisional_ref_only",
        structured_ref_inputs=aggregate)
    snapshot["ui_contract"] = ui_contract_from_artifact(snapshot)
    return snapshot


def test_model_exact_identity_and_selected_train_masses():
    model = engine._shipwreck_calibrated_model(2511)
    assert model is not None
    assert model['map_id']==2511 and model['train_sessions']==647
    assert model['official_distribution_authority'] is False
    assert model['candidate_sha256']=='1380de45f2f232de87cfb9f24674878fb769aa84a546749b1717cac905bc45d8'
    assert model['quality_probs']['q6']==0.12483339179235356
    assert sum(model['quality_probs'].values())==pytest.approx(1)
    assert not hasattr(engine,'_activity_2511_provisional_model')


def test_real_aggregate_current_envelope_reaches_nonempty_quote():
    snapshot = current_snapshot()
    assert engine._activity_2511_provisional_scope_state(snapshot) == "ok"
    assert engine._update606_changed_route_scope_state(snapshot) is None
    result = engine.run_reference_engine(engine.prepare_reference_engine_snapshot(snapshot)).as_dict()
    assert result["status"] == "ok", result["notes"]
    assert all(result[key] > 0 for key in ("conservative", "balanced", "aggressive"))
    assert result["conservative"] <= result["balanced"] <= result["aggressive"]
    assert result["evidence"]["total_count"] == 38
    assert result['total_count_estimate']==38
    assert result['total_count_error_band']==[38,38]
    assert result['empirical_calibration']['schema']=='shipwreck_calibrated_b049_v1'
    assert 'provisional_quote' not in result
    assert not any("activity_shipwreck_red_boost" in note for note in result["notes"])


@pytest.mark.parametrize("owner", ["root", "context", "source"])
@pytest.mark.parametrize("field,value", [
    ("owner_evidence_status", "valid"), ("owner_evidence_status", "malformed_conflict"),
    ("source_conflict", True), ("quarantine_required", True), ("qualification", "player_custom_warehouse"),
    ("game_version", "68171c-2026-09-03-907"), ("map_profile_generation", "68171c-2026-09-03-907"),
    ("source_generation", "unknown_warehouse_generation"), ("map_table_sha256", "0" * 64),
    ("map_source_contract_sha256", "0" * 64), ("table_status", "stale"), ("map_id", 4511),
])
def test_source_negative_cannot_borrow_other_valid_owner(owner, field, value):
    snapshot = copy.deepcopy(current_snapshot())
    # Actual artifact uses shared objects; independent JSON readback is the wire seam.
    snapshot = json.loads(json.dumps(snapshot))
    target = snapshot if owner == "root" else snapshot["ui_contract"][owner]
    target["warehouse_source"][field] = value
    assert engine._activity_2511_provisional_scope_state(snapshot) != "ok"
    for view in (snapshot, engine.prepare_reference_engine_snapshot(snapshot)):
        assert engine.run_reference_engine(view).balanced is None


@pytest.mark.parametrize("field,value", [("map_id", 4511), ("map_id", 2511.0), ("session_id", "2511:old"),
    ("warehouse_game_version_session_stable", False), ("phase", "settled"), ("model_map_id", 2510),
    ("warehouse_source_generation", "unknown_warehouse_generation"), ("formal_mode_reason", "game_generation_unverified")])
def test_root_context_binding_and_session_generation_negatives(field, value):
    snapshot = current_snapshot()
    snapshot[field] = value
    assert engine._activity_2511_provisional_scope_state(snapshot) != "ok"
    assert engine.run_reference_engine(snapshot).balanced is None


def test_missing_calibration_is_not_generic_prior_fallback(monkeypatch):
    snapshot = current_snapshot()
    monkeypatch.setattr(engine, "_shipwreck_calibrated_model", lambda _map: None)
    assert engine.run_reference_engine(snapshot).balanced is None


def test_unqualified_4511_pending_and_aisha_weight_exclusion_unchanged():
    assert engine.run_reference_engine({"map_id": 4511, "hero": "ahmed"}).balanced is None
    assert 2511 in engine._AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS
    assert 2511 not in engine._load_drop_weight_items()


def test_source_revocation_changes_real_ref_cache_signature():
    snapshot = current_snapshot()
    previous = ref_input_signature_from_snapshot(snapshot)
    for field, value in (("owner_evidence_status", "valid"), ("source_generation", "unknown_warehouse_generation"),
                         ("source_conflict", True)):
        newer = json.loads(json.dumps(snapshot))
        newer["warehouse_source"][field] = value
        assert ref_input_signature_from_snapshot(newer) != previous


def test_real_summary_persistent_notice_and_quote_labels(panel, tmp_path):
    summary = panel.summarize_snapshot(current_snapshot(), snapshot_path=tmp_path / "synthetic-envelope.json")
    assert summary["reference"]["action"] == NOTICE
    assert summary["reference"]["price_titles"] == {"conservative": "保守", "balanced": "参考", "aggressive": "激进"}
    assert '本周掉率未校准' not in summary['reference']['note']
    assert not any('暂估' in flag['label'] for flag in summary['flags'])
    assert summary["context"]["warehouse_source"]["official_distribution_eligible"] is False


@pytest.mark.parametrize("timestamp", [None, 1789012800.0])
@pytest.mark.parametrize("force", ["0", "1"])
def test_actual_consumed_quality_mass_ignores_old_boost_and_missing_time(monkeypatch, timestamp, force):
    snapshot = current_snapshot()
    if timestamp is None:
        snapshot.pop("created_at")
        snapshot["ui_contract"]["source"].pop("created_at", None)
    else:
        snapshot["created_at"] = snapshot["ui_contract"]["source"]["created_at"] = timestamp
    monkeypatch.setenv("AHMAD_FORCE_ACTIVITY_SHIPWRECK_RED_BOOST", force)
    monkeypatch.setenv("AHMAD_DISABLE_DROP_TABLE_QUALITY", "1")
    original = engine._total_count_candidates
    consumed = []
    def capture_mass(*args, **kwargs):
        consumed.append(dict(kwargs["probs"]))
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "_total_count_candidates", capture_mass)
    monkeypatch.setattr(engine, "_apply_activity_shipwreck_red_boost", lambda *a, **k: pytest.fail("old activity boost reached"))
    result = engine.run_reference_engine(engine.prepare_reference_engine_snapshot(snapshot))
    assert result.balanced is not None
    assert consumed
    assert all(row == engine._shipwreck_calibrated_model(2511)["quality_probs"] for row in consumed)


def test_actual_tk_cache_merge_revokes_previously_published_provisional(panel, tmp_path):
    spec = importlib.util.spec_from_file_location("ahmad_tk_overlay", ROOT / "hero_ref/tools/ahmad_tk_overlay.py")
    tk = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tk
    spec.loader.exec_module(tk)
    snapshot = current_snapshot()
    # Earlier successful summary shape, then a new same-session owner conflict.
    prior = {"ahmed_ref": {"balanced": 12345, "provisional_quote": {
        "schema": "activity_2511_provisional_b049_v1", "map_id": 2511, "empirically_calibrated": False}},
        "reference": {"balanced": "12,345", "action": NOTICE}}
    newer = json.loads(json.dumps(snapshot))
    newer["warehouse_source"]["owner_evidence_status"] = "valid"
    self = type("Receiver", (), {"snapshot_path": tmp_path / "synthetic.json"})()
    merged = tk.AhmadTkOverlay._merge_cached_summary_with_fresh_snapshot(self, prior, newer)
    assert merged["ahmed_ref"]["balanced"] is None
    assert merged["reference"]["action"] == "当前仓库待适配"
