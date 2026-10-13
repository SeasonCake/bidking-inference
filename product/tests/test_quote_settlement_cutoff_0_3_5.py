"""QUOTE-TIME-01: local artifact creation cutoff, not a game-server event clock."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
from collections import Counter
from unittest.mock import MagicMock

import pytest

B = Path(__file__).resolve().parents[1]
for root in (B / "src", B / "scripts", B / "hero_ref/tools", B / "hero_ref/src", B / "tests"):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from bidking_lab.live import quote_preferences as q, monitor, sample_upload
from test_quote_preferences_basic_0_3_5 import record, policy
from test_sample_upload_privacy import _settled_artifact


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden")))
    assert Path(q.__file__).resolve() == B / "src/bidking_lab/live/quote_preferences.py"
    assert Path(monitor.__file__).resolve() == B / "src/bidking_lab/live/monitor.py"
    assert Path(sample_upload.__file__).resolve() == B / "src/bidking_lab/live/sample_upload.py"


def _quote_at(cutoff, offset, source="live"):
    value = record(applied=cutoff + offset, configured=policy(5000))
    value["snapshot_at"] = cutoff - 1
    value["source_mode"] = source
    return value


def _settlement(cutoff):
    value = _settled_artifact()
    value.update(session_id="synthetic-session", created_at=cutoff)
    return value


@pytest.mark.parametrize("offset", [-0.01, 0.01, 10.0])
def test_original_cutoff_counterexamples(tmp_path, monkeypatch, offset):
    """Reproduce formal R3's exact before/+10ms/+10s eligibility expectations."""
    cutoff = time.time() - 20
    value = _quote_at(cutoff, offset)
    assert q.write_applied(tmp_path / "quote_last_applied.json", value)["status"] == "recorded"
    artifact = _settlement(cutoff)
    original = copy.deepcopy(artifact)
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
    monitor.write_monitor_logs(artifact, log_dir=tmp_path)
    assert artifact == original and len(posted) == 1
    actual = posted[0]["quote_application"]
    print("CUTOFF", json.dumps({"cutoff": cutoff, "applied_at": value["applied_at"],
                               "offset": offset, "actual_status": actual["status"]}))
    assert actual["status"] == ("recorded" if offset < 0 else "unknown")


@pytest.mark.parametrize("source", ["live", "manual_overlay"])
@pytest.mark.parametrize("offset", [-0.01, 0.0, 0.01, 10.0])
def test_shared_cutoff_matrix_preserves_fractional_precision(source, offset):
    cutoff = 1_700_000_000.125
    value = _quote_at(cutoff, offset, source)
    before = copy.deepcopy(value)
    result = q.validate_settlement_record(value, settlement_cutoff=cutoff,
                                          session_hash=value["session_hash"], now=cutoff + 30)
    assert value == before
    if offset < 0:
        assert result == value
    else:
        assert result == q.unknown("not_before_settlement")
    # The same evidence remains a valid local pending record, even after the cutoff.
    assert q.validate_record(value, now=cutoff + 30) == value


@pytest.mark.parametrize("source", ["live", "manual_overlay"])
@pytest.mark.parametrize("offset,delay", [(-0.01, 0.05), (0, 0.05), (0.01, 0.05),
                                         (-0.01, 30), (0, 30), (0.01, 30), (10, 30)])
def test_normal_and_delayed_first_capture_use_object_time(tmp_path, monkeypatch, source, offset, delay):
    cutoff = 1_700_000_000.125
    monkeypatch.setattr(q.time, "time", lambda: cutoff + delay)
    value = _quote_at(cutoff, offset, source)
    bridge = tmp_path / "quote_last_applied.json"
    assert q.write_applied(bridge, value)["status"] == "recorded"
    before = bridge.read_bytes()
    assert q.read_applied(bridge, session_hash=value["session_hash"]) == value
    artifact = _settlement(cutoff)
    original = copy.deepcopy(artifact)
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
    monitor.write_monitor_logs(artifact, log_dir=tmp_path)
    expected = value if offset < 0 else q.unknown("not_before_settlement")
    assert posted[0]["quote_application"] == expected and len(posted) == 1
    assert artifact == original and bridge.read_bytes() == before
    local = json.loads((tmp_path / "sessions.jsonl").read_text(encoding="utf-8"))
    assert local["quote_application"] == expected


def _collector(tmp_path, monkeypatch):
    import sample_collector_server as collector
    import cloud_ref_replay
    assert Path(collector.__file__).resolve() == B / "scripts/sample_collector_server.py"
    assert Path(cloud_ref_replay.__file__).resolve() == B / "scripts/cloud_ref_replay.py"
    assert collector._quote_preferences is q
    monkeypatch.setattr(collector, "_OUT", tmp_path / "collector.jsonl")
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter())
    return collector, cloud_ref_replay


@pytest.mark.parametrize("boundary", ["trim", "collector", "replay"])
@pytest.mark.parametrize("offset", [-0.01, 0, 0.01, 10])
def test_direct_consumers_cannot_bypass_cutoff(tmp_path, monkeypatch, boundary, offset):
    cutoff = 1_700_000_000.125
    monkeypatch.setattr(q.time, "time", lambda: cutoff + 30)
    value = _quote_at(cutoff, offset)
    artifact = _settlement(cutoff)
    artifact["quote_application"] = value
    before = copy.deepcopy(artifact)
    expected = value if offset < 0 else q.unknown("not_before_settlement")
    if boundary == "trim":
        result = sample_upload.trim_artifact(artifact)["quote_application"]
        assert artifact == before
    else:
        collector, replay = _collector(tmp_path, monkeypatch)
        row = sample_upload.trim_artifact(_settlement(cutoff))
        row["quote_application"] = value  # Deliberately bypass both monitor and trim validation.
        if boundary == "replay":
            result = replay.recorded_quote_evidence(row)
        else:
            row["received_ts"] = cutoff + 9999  # This is not a substitute cutoff or server authority.
            assert collector._accept_one(row) == "accepted"
            persisted = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8"))
            assert persisted["received_ts"] == cutoff + 30
            result = persisted["quote_application"]
            assert collector._accept_one(row) == "duplicate"
    assert result == expected


@pytest.mark.parametrize("invalid", [None, True, False, "1700000000", 0, -1, float("nan"),
                                     float("inf"), float("-inf"), 10**400, "missing", "future"])
@pytest.mark.parametrize("boundary", ["monitor", "trim", "collector", "replay"])
def test_missing_invalid_or_future_outer_cutoff_is_unknown(tmp_path, monkeypatch, invalid, boundary):
    cutoff = 1_700_000_000.125
    monkeypatch.setattr(q.time, "time", lambda: cutoff + 30)
    value = _quote_at(cutoff, -0.01)
    invalid_time = cutoff + 30.001 if invalid == "future" else invalid
    artifact = _settlement(invalid_time)
    if invalid == "missing":
        artifact.pop("created_at")
    artifact["quote_application"] = value
    if boundary == "monitor":
        q.write_applied(tmp_path / "quote_last_applied.json", value)
        posted = []
        monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
        monitor.write_monitor_logs(artifact, log_dir=tmp_path)
        result = posted[0]["quote_application"]
    elif boundary == "trim":
        result = sample_upload.trim_artifact(artifact)["quote_application"]
    else:
        collector, replay = _collector(tmp_path, monkeypatch)
        row = sample_upload.trim_artifact(_settlement(cutoff))
        row["quote_application"] = value
        row["client_ts"] = invalid_time
        row["received_ts"] = cutoff + 30
        if invalid == "missing":
            row.pop("client_ts")
        if boundary == "collector":
            assert collector._accept_one(row) == "accepted"
            row = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8"))
            result = row["quote_application"]
        else:
            result = replay.recorded_quote_evidence(row)
    assert result == q.unknown("settlement_cutoff_invalid")
    assert not {"raw", "adjusted", "is_default"}.intersection(result)


def test_shared_entry_requires_outer_cutoff_and_session():
    cutoff = time.time() - 1
    value = _quote_at(cutoff, -0.01)
    with pytest.raises(TypeError):
        q.validate_settlement_record(value, session_hash=value["session_hash"])
    assert q.validate_settlement_record(value, settlement_cutoff=cutoff, session_hash=None)["status"] == "unknown"


@pytest.mark.parametrize("source", ["live", "manual_overlay"])
@pytest.mark.parametrize("offset", [-0.01, 0, 0.01, 10])
def test_real_summary_render_first_post_to_collector_and_replay(tmp_path, monkeypatch, source, offset):
    import ahmad_tk_overlay as ui
    import test_settlement_incomplete_ui as fixture
    cutoff = 1_700_000_000.125
    clock = [cutoff + offset]
    monkeypatch.setattr(q.time, "time", lambda: clock[0])
    panel = fixture._server_module()
    fixture._configure_fake_engine(panel, monkeypatch)
    snapshot = fixture._snapshot(partial=False)
    snapshot.update(phase="bidding", created_at=cutoff - 1)
    context = snapshot["ui_contract"]["context"]
    context.update(phase="bidding", action_round=5, observed_round=5)
    snapshot["ui_contract"]["truth"] = {}
    snapshot["ui_contract"]["source"]["created_at"] = cutoff - 1
    if source == "manual_overlay":
        producer = ui.AhmadTkOverlay.__new__(ui.AhmadTkOverlay)
        producer.snapshot_path = tmp_path / "latest_snapshot.json"
        monkeypatch.setattr(ui, "summarize_snapshot", panel.summarize_snapshot)
        summary = producer._manual_overlay_summary(snapshot, {"structured_ref_inputs": {"hero": "victor", "total_count": 30}})
    else:
        summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    assert summary["quote_basis"]["source_mode"] == source
    assert summary["quote_basis"]["status"] == "available"
    original_summary = copy.deepcopy(summary)
    adapter = MagicMock()  # No window; raw production/render/application methods remain real.
    adapter._quote_policy = policy(5000)
    adapter._quote_writer_id = "a" * 32
    adapter._quote_application_revision = 0
    adapter._quote_applied_signature = None
    adapter._quote_preview_refresh = None
    adapter._last_live_summary = summary
    adapter._manual_active = source == "manual_overlay"
    adapter._compute_pending = adapter._summary_worker_running = False
    adapter.snapshot_path = tmp_path / "latest_snapshot.json"
    adapter.price_labels = {key: MagicMock() for key in q.TIERS}
    adapter._quote_display_values.side_effect = lambda data: ui.AhmadTkOverlay._quote_display_values(adapter, data)
    adapter._record_applied_quote.side_effect = lambda data: ui.AhmadTkOverlay._record_applied_quote(adapter, data)
    adapter._settlement_display_value.side_effect = lambda data, value: value
    configured = []
    def configure(widget, text, **kwargs):
        if widget in adapter.price_labels.values():
            configured.append(text)
            widget.cget.return_value = text
    adapter._set_autofit_label.side_effect = configure
    monkeypatch.setattr(ui.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    ui.AhmadTkOverlay.render(adapter, summary)
    assert configured == ["300,000", "664,358", "720,000"]
    assert summary == original_summary
    sid = summary["context"]["session_id"]
    bridge = tmp_path / "quote_last_applied.json"
    local_pending = q.read_applied(bridge, session_hash=q.hash_session(sid))
    assert local_pending["status"] == "recorded" and local_pending["applied_at"] == cutoff + offset
    bridge_bytes = bridge.read_bytes()
    clock[0] = cutoff + 30  # Actual first capture can be delayed after object creation.
    artifact = _settlement(cutoff)
    artifact["session_id"] = sid
    original_artifact = copy.deepcopy(artifact)
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
    monitor.write_monitor_logs(artifact, log_dir=tmp_path)
    assert len(posted) == 1 and artifact == original_artifact and bridge.read_bytes() == bridge_bytes
    local = json.loads((tmp_path / "sessions.jsonl").read_text(encoding="utf-8"))
    trimmed = sample_upload.trim_artifact(local)
    assert trimmed["client_ts"] == cutoff
    collector, replay = _collector(tmp_path, monkeypatch)
    assert collector._accept_one(trimmed) == "accepted"
    persisted = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8"))
    expected = local_pending if offset < 0 else q.unknown("not_before_settlement")
    for value in (posted[0]["quote_application"], local["quote_application"], trimmed["quote_application"],
                  persisted["quote_application"], replay.recorded_quote_evidence(persisted)):
        assert value == expected
    assert collector._accept_one(trimmed) == "duplicate"


@pytest.mark.parametrize("offset", [-0.01, 0, 0.01, 10])
def test_signed_collector_bypass_uses_client_cutoff_with_real_sqlite(tmp_path, monkeypatch, offset):
    import test_trusted_upload_identity_hotfix2 as signed
    cutoff = time.time() - 20
    row = sample_upload.trim_artifact(_settlement(cutoff))
    evidence = _quote_at(cutoff, offset)
    row["quote_application"] = evidence  # Bypass monitor and client trim deliberately.
    collector, replay = _collector(tmp_path, monkeypatch)
    store = signed.UploadIdentityStore(tmp_path / "identity.sqlite3", create=True)
    identity = collector.UploadIdentity(trust="server_signed", subject_kind="paid", subject_id="a" * 64,
                                        uid_hash="b" * 16, device_alias="c" * 64, security_epoch=7,
                                        lease_id="d" * 32, lease_exp=2_000_000_300, request_nonce="e" * 32)
    assert signed._accept_signed(store, row, identity, "f" * 64) == "accepted"
    persisted = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8"))
    expected = evidence if offset < 0 else q.unknown("not_before_settlement")
    assert persisted["quote_application"] == expected
    assert replay.recorded_quote_evidence(persisted) == expected
    assert store.redacted_session(row["session_hash"])[0]["disposition"] == "accepted"
    assert store.sessions_for_uid_hash("b" * 16) == frozenset({row["session_hash"]})
    assert "uid_hash" not in persisted and "subject_id" not in persisted


@pytest.mark.parametrize("kind", ["different_session", "generation", "conflict", "write_failure", "missing", "stale"])
def test_pending_bridge_guards_still_dominate_settlement_association(tmp_path, monkeypatch, kind):
    cutoff = time.time() - 20
    evidence = _quote_at(cutoff, -0.01)
    bridge = tmp_path / "quote_last_applied.json"
    if kind == "generation":
        evidence["generation_hash"] = "e" * 64
    if kind == "stale":
        evidence["applied_at"] -= q.MAX_AGE + 1
        evidence["snapshot_at"] -= q.MAX_AGE + 1
    if kind == "write_failure":
        monkeypatch.setattr(q, "_atomic", lambda *a: (_ for _ in ()).throw(OSError("synthetic write failure")))
        assert q.write_applied(bridge, evidence)["status"] == "unknown"
    elif kind != "missing":
        assert q.write_applied(bridge, evidence)["status"] == "recorded"
    if kind == "conflict":
        second = copy.deepcopy(evidence)
        second["writer_id"] = "b" * 32
        assert q.write_applied(bridge, second) == q.unknown("writer_conflict")
    artifact = _settlement(cutoff)
    if kind == "different_session":
        artifact["session_id"] = "another-session"
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
    monitor.write_monitor_logs(artifact, log_dir=tmp_path)
    assert posted[0]["quote_application"]["status"] == "unknown"
    assert sample_upload.trim_artifact(posted[0]) is not None


def test_snapshot_order_legacy_quote_and_nonsettled_append_contracts(tmp_path, monkeypatch):
    cutoff = time.time() - 20
    evidence = _quote_at(cutoff, -0.01)
    evidence["snapshot_at"] = evidence["applied_at"] + 0.001
    assert q.validate_settlement_record(evidence, settlement_cutoff=cutoff,
                                       session_hash=evidence["session_hash"]) == q.unknown("invalid")
    row = sample_upload.trim_artifact(_settlement(None))
    assert row["quote_application"] == q.unknown("missing")
    collector, replay = _collector(tmp_path, monkeypatch)
    assert collector._accept_one(row) == "accepted"
    assert replay.recorded_quote_evidence(row) == q.unknown("missing")
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(item))
    artifact = _settlement(cutoff)
    monitor.write_monitor_logs(artifact, log_dir=tmp_path / "no_append", append_logs=False)
    assert posted == [] and not (tmp_path / "no_append/sessions.jsonl").exists()
    artifact["phase"] = "bidding"
    monitor.write_monitor_logs(artifact, log_dir=tmp_path / "bidding")
    assert "quote_application" not in posted[0] and sample_upload.trim_artifact(posted[0]) is None
