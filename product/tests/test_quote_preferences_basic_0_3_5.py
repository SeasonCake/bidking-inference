from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
from collections import Counter

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    sys.path.insert(0, str(entry))
from bidking_lab.live import quote_preferences as q
from test_roaming_client_state_0_3_5 import wire, headless
from test_activation_candidate_atomic_commit_0_3_1 import isolated_activation_runtime


def policy(a=10000, b=10000, c=10000, cap=None):
    return {"schema": q.POLICY_SCHEMA, "ratios_bps": dict(zip(q.TIERS, (a, b, c))), "cap": cap}


def record(*, sid="synthetic-session", writer="a" * 32, revision=1, applied=None, configured=None):
    now = time.time() if applied is None else applied
    raw = dict(zip(q.TIERS, (10.5, 20, 30)))
    configured = configured or policy()
    return {"schema": q.RECORD_SCHEMA, "status": "recorded", "coverage": q.COVERAGE,
            "session_hash": q.hash_session(sid), "writer_id": writer, "revision": revision,
            "source_mode": "live", "input_fingerprint": "b" * 64, "result_identity": "c" * 64,
            "generation_hash": None, "round": 1, "action_round": 1, "observed_round": 1,
            "phase": "bidding", "raw": raw, "policy": configured,
            "policy_fingerprint": q.fingerprint(configured), "is_default": q.is_default(configured),
            "adjusted": q.apply_policy(raw, configured), "applied_at": now, "snapshot_at": now - 1}


def test_actual_source_and_default_identity():
    assert Path(q.__file__).resolve() == ROOT / "src/bidking_lab/live/quote_preferences.py"
    raw = dict(zip(q.TIERS, (0, 2.5, 999.125)))
    assert q.apply_policy(raw, policy()) == raw
    assert type(q.apply_policy(raw, policy())["balanced"]) is float


@pytest.mark.parametrize("bps,expected", [(0, 0), (5000, 1), (10000, 1), (15000, 2), (20000, 2)])
def test_decimal_rounding_cap_and_order(bps, expected):
    assert q.apply_policy(dict.fromkeys(q.TIERS, 1), policy(bps, bps, bps))["balanced"] == expected
    assert q.apply_policy(dict.fromkeys(q.TIERS, 2.5), policy(20000, 0, 10000, 3)) == dict(zip(q.TIERS, (3, 0, 3)))


@pytest.mark.parametrize("bad", [True, -1, 20001, 10000.0, None, float("nan"), "10000"])
def test_invalid_ratios_known_red(bad):
    with pytest.raises(ValueError):
        q.validate_policy(policy(bad))


@pytest.mark.parametrize("bad", [True, 0, -1, 1000000000, 1.5, "10", float("inf")])
def test_invalid_cap_known_red(bad):
    with pytest.raises(ValueError):
        q.validate_policy(policy(cap=bad))


@pytest.mark.parametrize("text", ["100.001", "NaN", "Infinity", "-0", "201", "1e2", ""])
def test_text_parser_rejects_bad_precision(text):
    with pytest.raises(ValueError):
        q.policy_from_text(dict.fromkeys(q.TIERS, text), "")


def test_text_parser_exact_and_name_default_independent():
    assert q.policy_from_text(dict.fromkeys(q.TIERS, "0.01"), "1") == policy(1, 1, 1, 1)
    assert q.is_default(policy())


def test_preferences_atomic_revision_and_corrupt_preservation(tmp_path):
    path = tmp_path / "preferences.json"
    original, error = q.load_preferences(path)
    assert error is None and not path.exists()
    saved = q.save_preferences(path, expected_revision=0, name="游戏参考", policy=policy(8000))
    assert saved["revision"] == 1
    before = path.read_bytes()
    with pytest.raises(ValueError, match="另一窗口"):
        q.save_preferences(path, expected_revision=0, name="陈旧", policy=policy(9000))
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        q.save_preferences(path, expected_revision=1, name="默认", policy=policy(5000))
    path.write_text("broken fixture", encoding="utf-8")
    assert q.load_preferences(path)[1]
    with pytest.raises(ValueError):
        q.save_preferences(path, expected_revision=0, name="不覆盖", policy=policy())
    assert path.read_text() == "broken fixture"


def test_preferences_failed_write_keeps_bytes(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    q.save_preferences(path, expected_revision=0, name="正常", policy=policy())
    before = path.read_bytes()
    monkeypatch.setattr(q.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(OSError):
        q.save_preferences(path, expected_revision=1, name="失败", policy=policy(5000))
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"is_default": False}, {"writer_id": "private name"}, {"scheme_name": "private name"},
    {"generation_hash": "invented generation"}, {"revision": True}, {"phase": "settled"},
    {"source_mode": "other"}, {"applied_at": float("nan")}, {"raw": dict.fromkeys(q.TIERS, None)},
    {"adjusted": dict.fromkeys(q.TIERS, 99)}, {"policy_fingerprint": "d" * 64},
])
def test_record_tampering_is_unknown_not_default(change):
    value = record()
    value.update(change)
    invalid = q.validate_record(value)
    assert invalid == q.unknown("invalid")
    assert not {"raw", "adjusted", "is_default"}.intersection(invalid)


def test_record_default_custom_unknown_and_old_replay():
    value = record(applied=time.time() - 100000)
    assert q.validate_record(value)["is_default"] is True  # Historical replay is not expired bridge data.
    value = record(configured=policy(0, 20000, 12345, 20))
    assert q.validate_record(value)["adjusted"] == q.apply_policy(value["raw"], value["policy"])
    assert q.validate_record(q.unknown("missing")) == q.unknown("missing")
    assert q.validate_record(record(), session_hash="0" * 16) == q.unknown("different_session")


def test_bridge_conflicting_writer_restart_and_new_session(tmp_path):
    path = tmp_path / "quote_last_applied.json"
    first = record()
    assert q.write_applied(path, first)["status"] == "recorded"
    assert q.read_applied(path, session_hash=first["session_hash"])["raw"] == first["raw"]
    assert q.write_applied(path, record(writer="d" * 32)) == q.unknown("writer_conflict")
    assert q.write_applied(path, record(writer="e" * 32)) == q.unknown("writer_conflict")
    next_session = record(sid="next", writer="e" * 32)
    assert q.write_applied(path, next_session)["status"] == "recorded"
    assert q.read_applied(path, session_hash=first["session_hash"]) == q.unknown("different_session")


def test_bridge_stale_future_lock_failure(tmp_path, monkeypatch):
    path = tmp_path / "quote_last_applied.json"
    value = record(applied=time.time() - q.MAX_AGE - 2)
    assert q.write_applied(path, value)["status"] == "recorded"
    assert q.read_applied(path, session_hash=value["session_hash"]) == q.unknown("stale")
    assert q.write_applied(path, record(applied=time.time() + 20)) == q.unknown("future")
    with q._locked(path):
        assert q.read_applied(path, session_hash=value["session_hash"])["status"] == "unknown"
    assert q.read_applied(path, session_hash=value["session_hash"]) == q.unknown("stale")


def _overlay(tmp_path):
    import ahmad_tk_overlay as ui
    obj = ui.AhmadTkOverlay.__new__(ui.AhmadTkOverlay)
    obj._quote_policy = policy()
    obj._quote_writer_id = "a" * 32
    obj._quote_preferences = q.new_preferences()
    obj._ui_prefs_enabled = False
    obj._act_state = {"can_use": True}
    obj.snapshot_path = tmp_path / "latest_snapshot.json"
    return obj


def _summary():
    value = record()
    return {"context": {"session_id": "synthetic-session", "phase": "bidding", "round": 1,
                        "action_round": 1, "observed_round": 1},
            "updated_at": value["snapshot_at"], "quote_basis": q.quote_basis(value["raw"], eligible=True),
            "quote_identity": {"input_fingerprint": value["input_fingerprint"], "result_identity": value["result_identity"]},
            "quote_receipt": {"source": "live_ref_result"}, "reference": dict.fromkeys(q.TIERS, "original")}


def test_display_default_custom_no_compounding_and_no_summary_mutation(tmp_path):
    ui = _overlay(tmp_path)
    summary = _summary()
    before = copy.deepcopy(summary)
    assert ui._quote_display_values(summary) == {}
    ui._quote_policy = policy(20000, 5000, 0)
    a = ui._quote_display_values(summary)
    assert a == ui._quote_display_values(summary)
    assert a == dict(zip(q.TIERS, ("21", "10", "0")))
    assert summary == before
    assert not (tmp_path / "quote_last_applied.json").exists()  # Projection/cache is not application.
    summary["context"]["phase"] = "settled"
    assert ui._quote_display_values(summary) == {}


def test_applied_record_real_method_dedup_manual_and_denied(tmp_path, monkeypatch):
    import ahmad_tk_overlay as module
    obj = _overlay(tmp_path)
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    summary = _summary()
    obj._quote_display_values(summary)
    obj._record_applied_quote(summary)
    assert obj._quote_record_status["status"] == "recorded"
    first = (tmp_path / "quote_last_applied.json").read_bytes()
    obj._record_applied_quote(summary)
    assert (tmp_path / "quote_last_applied.json").read_bytes() == first
    obj._quote_policy = policy(5000)
    obj._quote_display_values(summary)
    obj._record_applied_quote(summary)
    assert obj._quote_application_revision == 2
    summary["quote_basis"]["source_mode"] = "manual"
    obj._quote_display_values(summary)
    obj._record_applied_quote(summary)
    assert obj._quote_record_status == q.unknown("manual")
    before = (tmp_path / "quote_last_applied.json").read_bytes()
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: True)
    obj._quote_policy = policy(1000)
    obj._quote_display_values(summary)
    obj._record_applied_quote(summary)
    assert (tmp_path / "quote_last_applied.json").read_bytes() == before


def test_draft_save_error_does_not_publish(tmp_path, monkeypatch):
    obj = _overlay(tmp_path)
    obj._ui_prefs_enabled = True
    obj._quote_preferences_path = tmp_path / "p.json"
    before = copy.deepcopy(obj._quote_policy)
    monkeypatch.setattr(q, "save_preferences", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture")))
    with pytest.raises(OSError):
        obj._save_quote_draft("failed", policy(5000), 0)
    assert obj._quote_policy == before


def test_real_summary_typed_basis_and_settlement_guard(tmp_path, monkeypatch):
    import test_settlement_incomplete_ui as fixture
    module = fixture._server_module()
    fixture._configure_fake_engine(module, monkeypatch)
    snapshot = fixture._snapshot(partial=False)
    settled = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest.json")
    assert settled["quote_basis"]["status"] == "unavailable"
    snapshot["phase"] = "bidding"
    snapshot["ui_contract"]["context"]["phase"] = "bidding"
    snapshot["ui_contract"]["truth"] = {}
    live = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest.json")
    assert live["quote_basis"]["status"] == "available"
    assert live["quote_basis"]["raw"]["balanced"] == 664358
    assert live["reference"]["balanced"] == "664,358"


def test_monitor_trim_collector_readback_replay_and_public_projection(tmp_path, monkeypatch):
    from bidking_lab.live import monitor, sample_upload
    from test_sample_upload_privacy import _settled_artifact
    import sample_collector_server as collector
    import cloud_ref_replay
    import public_stats
    artifact = _settled_artifact()
    artifact["session_id"] = "synthetic-session"
    artifact["created_at"] = time.time()
    evidence = record(applied=artifact["created_at"] - 0.01, configured=policy(5000, 12500, 20000, 25))
    q.write_applied(tmp_path / "quote_last_applied.json", evidence)
    posted = []
    monkeypatch.setattr(sample_upload, "maybe_upload_settlement", lambda item: posted.append(copy.deepcopy(item)))
    original = copy.deepcopy(artifact)
    monitor.write_monitor_logs(artifact, log_dir=tmp_path)
    assert artifact == original
    assert posted[0]["quote_application"] == evidence
    local = json.loads((tmp_path / "sessions.jsonl").read_text().splitlines()[0])
    trimmed = sample_upload.trim_artifact(local)
    assert trimmed["quote_application"] == evidence
    monkeypatch.setattr(collector, "_OUT", tmp_path / "collector.jsonl")
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter())
    assert collector._accept_one(trimmed) == "accepted"
    collected = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert cloud_ref_replay.recorded_quote_evidence(collected) == evidence
    public = json.dumps(public_stats.aggregate_public([collected]), ensure_ascii=False)
    assert "quote_application" not in public and "policy_fingerprint" not in public
    assert evidence["writer_id"] not in public
    bad = copy.deepcopy(trimmed)
    bad["session_hash"] = q.hash_session("another")
    bad["quote_application"]["scheme_name"] = "private fixture"
    assert collector._accept_one(bad) == "accepted"
    bad_collected = json.loads((tmp_path / "collector.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert bad_collected["quote_application"] == q.unknown("invalid")
    assert "private fixture" not in json.dumps(bad_collected)
    no_append = tmp_path / "no_append"
    monitor.write_monitor_logs(artifact, log_dir=no_append, append_logs=False)
    assert len(posted) == 1 and not (no_append / "sessions.jsonl").exists()


@pytest.mark.parametrize("fail_third", [False, True])
def test_real_render_records_only_after_all_three_configurations(tmp_path, monkeypatch, fail_third):
    from unittest.mock import MagicMock
    import ahmad_tk_overlay as module
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    adapter = MagicMock()  # Explicit no-window adapter; unrelated display helpers are no-ops.
    adapter._quote_policy = policy(20000, 5000, 0)
    adapter._quote_writer_id = "a" * 32
    adapter._quote_application_revision = 0
    adapter._quote_applied_signature = None
    adapter._quote_preview_refresh = None
    adapter._last_live_summary = None
    adapter._manual_active = False
    adapter._compute_pending = False
    adapter._summary_worker_running = False
    adapter.snapshot_path = tmp_path / "latest_snapshot.json"
    adapter.price_labels = {key: MagicMock(name="price-" + key) for key in q.TIERS}
    adapter._quote_display_values.side_effect = lambda data: module.AhmadTkOverlay._quote_display_values(adapter, data)
    adapter._record_applied_quote.side_effect = lambda data: module.AhmadTkOverlay._record_applied_quote(adapter, data)
    adapter._settlement_display_value.side_effect = lambda data, value: value
    configured = []

    def configure(widget, text, **kwargs):
        if widget in adapter.price_labels.values():
            configured.append((widget, text))
            widget.cget.return_value = text
            assert not (tmp_path / "quote_last_applied.json").exists()
            if fail_third and len(configured) == 3:
                raise RuntimeError("synthetic third configure failure")

    adapter._set_autofit_label.side_effect = configure
    summary = _summary()
    if fail_third:
        with pytest.raises(RuntimeError, match="third configure"):
            module.AhmadTkOverlay.render(adapter, summary)
        assert not (tmp_path / "quote_last_applied.json").exists()
    else:
        module.AhmadTkOverlay.render(adapter, summary)
        assert [value for _, value in configured] == ["21", "10", "0"]
        saved = q.read_applied(tmp_path / "quote_last_applied.json", session_hash=q.hash_session("synthetic-session"))
        assert saved["status"] == "recorded"


def test_new_session_rejects_late_applied_summary(tmp_path, monkeypatch):
    import ahmad_tk_overlay as module
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    obj = _overlay(tmp_path)
    obj._last_live_summary = {"context": {"session_id": "new-session"}}
    old = _summary()
    obj._quote_display_values(old)
    obj._record_applied_quote(old)
    assert obj._quote_record_status == q.unknown("different_session")
    assert not (tmp_path / "quote_last_applied.json").exists()


def test_monitor_generation_is_actual_launch_identity(tmp_path):
    assert q.monitor_generation({"created_at": 1, "session_id": "ordinary"}) is None
    assert q.monitor_generation({"started_at": 1, "pid": True}) is None
    generation = q.monitor_generation({"started_at": 123, "pid": 42})
    value = record()
    value["generation_hash"] = generation
    path = tmp_path / "quote_last_applied.json"
    assert q.write_applied(path, value)["status"] == "recorded"
    assert q.read_applied(path, session_hash=value["session_hash"]) == q.unknown("unavailable")
    assert q.read_applied(path, session_hash=value["session_hash"], generation_hash=generation)["status"] == "recorded"
    assert q.read_applied(path, session_hash=value["session_hash"], generation_hash="e" * 64) == q.unknown("unavailable")


def test_real_manual_summary_stays_manual(tmp_path, monkeypatch):
    import ahmad_tk_overlay as module
    import test_settlement_incomplete_ui as fixture
    obj = _overlay(tmp_path)
    obj._manual_live_round_no = lambda snapshot: 1
    monkeypatch.setattr(module, "run_reference_engine", lambda *a, **k: fixture._FakeRefResult())
    data = obj._manual_result_summary({"hero": "victor", "round": 1, "map_id": 2541})
    assert data["quote_basis"]["status"] == "available"
    assert data["quote_basis"]["source_mode"] == "manual"
    assert data["context"]["session_id"] == "manual"
    assert data["quote_basis"]["raw"]["balanced"] == 664358
    obj._quote_policy = policy(5000)
    assert obj._quote_display_values(data)["conservative"] == "300,000"


@pytest.mark.parametrize("diagnostic", [False, True])
def test_shop_entry_only_standard_actual_dialog_branch(tmp_path, monkeypatch, diagnostic):
    from unittest.mock import MagicMock
    import ahmad_tk_overlay as module
    obj = _overlay(tmp_path)
    obj.root = MagicMock()
    obj.network_diagnostic_cohort = diagnostic
    obj._add_shop_entry = MagicMock()
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    monkeypatch.setattr(module.tk, "Toplevel", lambda *_: MagicMock())
    # Stop after the real standard/cohort branch, before unrelated activation widgets/state.
    monkeypatch.setattr(module.tk, "Label", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop after shop branch")))
    with pytest.raises(RuntimeError, match="after shop"):
        obj._open_token_dialog()
    assert obj._add_shop_entry.call_count == (0 if diagnostic else 1)


@pytest.mark.parametrize("missing", [False, True])
def test_shop_link_hover_padding_destroy_without_navigation(tmp_path, monkeypatch, missing):
    from unittest.mock import MagicMock
    import ahmad_tk_overlay as module
    obj = _overlay(tmp_path)
    button, dialog, window = MagicMock(), MagicMock(), MagicMock()
    button.winfo_rootx.return_value = 20
    button.winfo_rooty.return_value = 30
    button.winfo_height.return_value = 20
    obj._rounded_button = lambda *a, **k: button
    opened = []
    monkeypatch.setattr(module.webbrowser, "open", lambda url: opened.append(url))
    photo = MagicMock()
    photo.width.return_value = photo.height.return_value = 225
    def image(**kwargs):
        assert Path(kwargs["file"]) == ROOT / "hero_ref/assets/shop_qr.png"
        if missing:
            raise module.tk.TclError("synthetic missing QR")
        return photo
    monkeypatch.setattr(module.tk, "PhotoImage", image)
    monkeypatch.setattr(module.tk, "Toplevel", lambda *_: window)
    monkeypatch.setattr(module.tk, "Label", lambda *a, **k: MagicMock())
    obj._add_shop_entry(dialog)
    bindings = {call.args[0]: call.args[1] for call in button.bind.call_args_list}
    bindings["<Button-1>"](None)
    assert opened == ["https://www.kufaka.com/shop/GGTO42L9"]
    bindings["<Enter>"](None)
    if not missing:
        window.configure.assert_called_with(bg="white", padx=20, pady=20)
        bindings["<Leave>"](None)
        window.destroy.assert_called_once()
    assert "<Destroy>" in {call.args[0] for call in dialog.bind.call_args_list}


def _dialog_adapter(obj, monkeypatch):
    from unittest.mock import MagicMock
    import ahmad_tk_overlay as module
    variables, commands, labels = [], {}, []

    class Variable:
        def __init__(self, master, value=""):
            self.value, self.callbacks = value, []
            variables.append(self)
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
            for callback in self.callbacks:
                callback()
        def trace_add(self, mode, callback):
            self.callbacks.append(callback)

    def label(*args, **kwargs):
        widget = MagicMock()
        labels.append(widget)
        return widget

    def button(*args, **kwargs):
        commands[kwargs["text"]] = kwargs["command"]
        return MagicMock()

    obj.root = MagicMock()
    monkeypatch.setattr(module.tk, "Toplevel", lambda *_: MagicMock())
    monkeypatch.setattr(module.tk, "StringVar", Variable)
    monkeypatch.setattr(module.tk, "Label", label)
    monkeypatch.setattr(module.tk, "Entry", lambda *a, **k: MagicMock())
    monkeypatch.setattr(module.tk, "Frame", lambda *a, **k: MagicMock())
    monkeypatch.setattr(module.tk, "Button", button)
    monkeypatch.setattr(module.ttk, "Combobox", lambda *a, **k: MagicMock())
    monkeypatch.setattr(module.AhmadTkOverlay, "_activation_product_denied", lambda self: False)
    obj._open_quote_preferences()
    assert len(variables) == 5  # name, three independent percentages, cap
    return variables, commands, labels


def test_real_dialog_draft_cancel_save_reset_and_refresh(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    obj = _overlay(tmp_path)
    obj._last_summary = _summary()
    obj._quote_display_data = obj._last_summary
    obj.render = MagicMock()
    variables, commands, labels = _dialog_adapter(obj, monkeypatch)
    variables[0].set("一半")
    variables[1].set("50")
    assert q.is_default(obj._quote_policy)
    obj._quote_display_data = _summary()
    obj._quote_preview_refresh()
    assert variables[1].get() == "50"  # new result does not reset draft/focus widgets
    commands["取消"]()
    assert q.is_default(obj._quote_policy) and not (tmp_path / "quote_preferences.json").exists()
    variables, commands, labels = _dialog_adapter(obj, monkeypatch)
    variables[0].set("一半")
    variables[1].set("50")
    commands["保存并应用"]()
    assert obj._quote_policy == policy(5000)
    obj.render.assert_called_once_with(obj._last_summary)
    variables, commands, labels = _dialog_adapter(obj, monkeypatch)
    commands["恢复默认"]()
    assert obj._quote_policy == policy(5000)
    commands["保存并应用"]()
    assert q.is_default(obj._quote_policy)
    assert obj._quote_preferences["schemes"]["一半"] == policy(5000)


def test_dialog_uses_fresh_revision_and_draft_without_silent_apply(tmp_path, monkeypatch):
    obj = _overlay(tmp_path)
    obj._ui_prefs_enabled = True
    obj._quote_preferences_path = tmp_path / "p.json"
    q.save_preferences(obj._quote_preferences_path, expected_revision=0, name="另一窗", policy=policy(7500))
    variables, commands, labels = _dialog_adapter(obj, monkeypatch)
    assert variables[0].get() == "另一窗" and variables[1].get() == "75"
    assert q.is_default(obj._quote_policy)
    # Another save after opening is rejected and cannot replace the effective policy.
    q.save_preferences(obj._quote_preferences_path, expected_revision=1, name="随后修改", policy=policy(6000))
    commands["保存并应用"]()
    assert q.is_default(obj._quote_policy)
    assert obj._quote_dialog is not None
    assert "保存失败" in labels[-1].configure.call_args.kwargs["text"]


def test_bridge_old_revision_cannot_claim_new_application(tmp_path):
    path = tmp_path / "quote_last_applied.json"
    first = record(revision=2)
    assert q.write_applied(path, first)["status"] == "recorded"
    before = path.read_bytes()
    assert q.write_applied(path, record(revision=1)) == q.unknown("stale")
    assert path.read_bytes() == before


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("used", [0, 8])
def test_existing_roam_dialog_count_with_explicit_shop_adapter(wire, headless, profile, used):
    import test_roaming_client_state_0_3_5 as previous
    headless.overlay._add_shop_entry = lambda dialog: None
    previous.test_idle_dialog_and_success_consume_real_current_count(wire, headless, profile, used)


@pytest.mark.parametrize("profile", [False, True])
def test_existing_roam_cap_feedback_with_explicit_shop_adapter(wire, headless, profile):
    import test_roaming_client_state_0_3_5 as previous
    headless.overlay._add_shop_entry = lambda dialog: None
    previous.test_cap_dialog_and_late_old_view_callbacks_keep_feedback_owner(wire, headless, profile)


@pytest.mark.parametrize("kind", ["lower_bound", "unavailable"])
def test_actual_summary_nonquote_branches_never_apply_preferences(tmp_path, monkeypatch, kind):
    import test_settlement_incomplete_ui as fixture
    module = fixture._server_module()
    fixture._configure_fake_engine(module, monkeypatch)
    original_result = fixture._FakeRefResult().as_dict()
    if kind == "lower_bound":
        original_result["total_value_bound"] = {"mode": "lower_bound_only", "lower_bound": 1000000}
    else:
        original_result["status"] = "missing_total_count"
    class Result:
        def as_dict(self):
            return copy.deepcopy(original_result)
    monkeypatch.setattr(module, "run_reference_engine", lambda *a, **k: Result())
    snapshot = fixture._snapshot(partial=False)
    snapshot["phase"] = snapshot["ui_contract"]["context"]["phase"] = "bidding"
    snapshot["ui_contract"]["truth"] = {}
    summary = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest.json")
    assert summary["quote_basis"]["status"] == "unavailable"
    obj = _overlay(tmp_path)
    obj._quote_policy = policy(0, 0, 0)
    before = copy.deepcopy(summary)
    assert obj._quote_display_values(summary) == {}
    assert summary == before
    if kind == "lower_bound":
        assert summary["reference"]["conservative"].startswith("≥")


def test_public_aggregation_active_sample_is_unchanged_by_quote_metadata():
    import public_stats
    from test_public_stats import _sample
    raw = _sample()
    baseline = public_stats.aggregate_public([raw])
    with_quote = copy.deepcopy(raw)
    with_quote["quote_application"] = record()
    assert public_stats.aggregate_public([with_quote]) == baseline
    assert baseline["maps"]  # Do not accept empty aggregation as a privacy positive control.


def test_save_from_standby_does_not_promote_stale_cached_quote(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    obj = _overlay(tmp_path)
    obj._last_summary = {"status": "stale_snapshot", "context": {"phase": "bidding"}}
    obj._quote_display_data = obj._last_summary
    obj.render, obj.render_standby = MagicMock(), MagicMock()
    variables, commands, labels = _dialog_adapter(obj, monkeypatch)
    variables[0].set("暂停时设置")
    variables[1].set("50")
    commands["保存并应用"]()
    obj.render.assert_not_called()
    obj.render_standby.assert_called_once_with(obj._last_summary)


def test_record_numeric_upper_boundary_and_subsecond_future_are_distinct():
    value = record(applied=100, configured=policy(20000, 20000, 20000))
    value["raw"] = dict.fromkeys(q.TIERS, 1e12)
    value["adjusted"] = q.apply_policy(value["raw"], value["policy"])
    assert q.validate_record(value, now=100)["adjusted"]["balanced"] == 2000000000000
    assert q.validate_record(value, now=99.999) == q.unknown("future")
    with pytest.raises(ValueError):
        q.validate_raw(dict.fromkeys(q.TIERS, 10**400))


def _assert_current_store_rows(rows):
    assert rows == (
        ("绑定设备：", ("30天 ¥15.88", "90天 ¥35.88", "365天 ¥88.88")),
        ("可换绑：", ("30天 ¥24.88", "90天 ¥48.88", "365天 ¥118.88")),
    )


def test_profile_store_prices_exact_and_swapped_or_old_price_control():
    import product_profile
    _assert_current_store_rows(product_profile.STANDARD_SUBSCRIPTION_PRICE_ROWS)
    wrong = (("绑定设备：", ("90天 ¥35.88", "30天 ¥15.88", "365天 ¥88.88")),
             product_profile.STANDARD_SUBSCRIPTION_PRICE_ROWS[1])
    with pytest.raises(AssertionError):
        _assert_current_store_rows(wrong)
    old = (("绑定设备：", ("30天 ¥12.88", "90天 ¥35.88", "365天 ¥88.88")),
           product_profile.STANDARD_SUBSCRIPTION_PRICE_ROWS[1])
    with pytest.raises(AssertionError):
        _assert_current_store_rows(old)


@pytest.mark.parametrize("diagnostic", [False, True])
def test_actual_dialog_consumes_current_prices_and_contact_copy(wire, headless, diagnostic):
    from bidking_lab.secure import enforce
    import product_profile
    wire.seed(0)
    enforce.refresh_activation()
    headless.open(diagnostic)
    shown = [w.options.get("text", "") for w in headless.widgets]
    prices = [price for _, values in product_profile.STANDARD_SUBSCRIPTION_PRICE_ROWS for price in values]
    if diagnostic:
        assert not set(prices).intersection(shown)
        assert "续费、换档或衔接剩余时间，请先联系作者核对" not in shown
    else:
        assert all(price in shown for price in prices)
        assert "一次性购买，不自动扣款" in shown
        assert "续费、换档或衔接剩余时间，请先联系作者核对" in shown
        assert not any("再次购买会保留并延长" in text for text in shown)
        assert any("30天12小时" in text for text in shown)
        assert any("365天另含" in text for text in shown)


@pytest.mark.parametrize("state", ["ready", "settled", "unavailable"])
def test_display_tip_does_not_claim_application_when_basis_is_unavailable(tmp_path, state):
    from unittest.mock import MagicMock
    obj = _overlay(tmp_path)
    obj.quote_tip = MagicMock()
    obj._quote_policy = policy(5000)
    summary = _summary()
    if state == "settled":
        summary["context"]["phase"] = "settled"
    elif state == "unavailable":
        summary["quote_basis"]["status"] = "unavailable"
    obj._quote_display_values(summary)
    text = obj.quote_tip.set_text.call_args.args[0]
    assert ("未应用" in text) == (state != "ready")
