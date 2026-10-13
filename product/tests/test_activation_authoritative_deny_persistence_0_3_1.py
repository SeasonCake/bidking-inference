# -*- coding: utf-8 -*-
"""Final 0.3.1 candidate gate; internal hotfix1 commits are not a product version."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import queue
import sys

import pytest

from bidking_lab.live import sample_upload
from bidking_lab.live import monitor
from bidking_lab.secure import activation
from bidking_lab.secure import client_activate as client
from bidking_lab.secure import enforce


_DEVICE = "DEVICE-A"
_TOKEN = "v1|paid-user|synthetic"
_MASTER_KEY = bytes(range(32))
_NOW = 2_000_000
_INGEST_URL = "https://srv.example/ingest"
_ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = _ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("hotfix1_ahmad_tk_overlay", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _settled_artifact() -> dict:
    return {
        "phase": "settled",
        "session_id": "2523:synthetic",
        "map_id": 2523,
        "hero": "ahmed",
        "final_quality_counts": {"q5": 2},
        "final_quality_cells": {"q5": 19},
        "final_quality_values": {"q5": 100_000},
        "inventory_count": 24,
        "inventory_cells": 85,
        "known_value_sum": 194_604,
        "settlement_incomplete": False,
    }


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    old_provider = activation._provider
    old_runtime_denied = client._RUNTIME_ACTIVATION_DENIED.is_set()
    old_last = enforce._LAST
    old_grant = enforce._LAST_ONLINE_GRANT
    old_identity = enforce._LAST_ONLINE_IDENTITY
    old_heartbeat = enforce._LAST_HEARTBEAT
    old_generation = enforce._ACTIVATION_REQUEST_GENERATION
    old_cfg = sample_upload._LOCAL_CFG_CACHE
    old_upload_epoch = sample_upload._UPLOAD_AUTHORITY_EPOCH

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("BIDKING_ACTIVATE_URL", "https://srv.example/activate")
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_URL", raising=False)
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_KEY", raising=False)
    monkeypatch.setattr(client, "device_fingerprint", lambda: _DEVICE)
    monkeypatch.setattr(client, "_package_dir", lambda: tmp_path / "package")
    monkeypatch.setattr(client.time, "time", lambda: _NOW)
    monkeypatch.setattr(enforce.time, "time", lambda: _NOW)

    client._RUNTIME_ACTIVATION_DENIED.clear()
    client._CACHE_INVALIDATED_IN_PROCESS = False
    client._ACTIVATION_CLEAR_STATE = None
    enforce._LAST = None
    enforce._LAST_ONLINE_GRANT = None
    enforce._LAST_ONLINE_IDENTITY = None
    enforce._LAST_HEARTBEAT = None
    enforce._ACTIVATION_REQUEST_GENERATION = 0
    enforce._HEARTBEAT_FAILURE_COUNT = 0
    enforce._HEARTBEAT_FAILURE_STARTED_AT = None
    sample_upload._LOCAL_CFG_CACHE = None
    sample_upload._UPLOAD_AUTHORITY_EPOCH = 0

    yield

    activation.set_provider(old_provider)
    enforce._LAST = old_last
    enforce._LAST_ONLINE_GRANT = old_grant
    enforce._LAST_ONLINE_IDENTITY = old_identity
    enforce._LAST_HEARTBEAT = old_heartbeat
    enforce._ACTIVATION_REQUEST_GENERATION = old_generation
    sample_upload._LOCAL_CFG_CACHE = old_cfg
    sample_upload._UPLOAD_AUTHORITY_EPOCH = old_upload_epoch
    if old_runtime_denied:
        client._RUNTIME_ACTIVATION_DENIED.set()
    else:
        client._RUNTIME_ACTIVATION_DENIED.clear()


def _prime_online_grant() -> client.ActivationResult:
    client._app_dir().mkdir(parents=True, exist_ok=True)
    client._token_file().write_text(_TOKEN, encoding="utf-8")
    assert client._save_cache(
        lease={"uid": "paid-user", "device": _DEVICE, "exp": _NOW + 3600},
        master_key=_MASTER_KEY,
        device=_DEVICE,
        token=_TOKEN,
        mode="paid",
        now=_NOW,
    )
    assert client._install_provider(
        _MASTER_KEY,
        _NOW + 3600,
        _DEVICE,
        _INGEST_URL,
    )
    grant = client.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        source="server",
        lease_exp=_NOW + 3600,
        paid_exp=_NOW + 7200,
    )
    enforce._LAST = grant
    enforce._LAST_ONLINE_GRANT = grant
    enforce._LAST_ONLINE_IDENTITY = enforce._activation_identity(client, None)
    enforce._LAST_HEARTBEAT = grant
    sample_upload._LOCAL_CFG_CACHE = (_INGEST_URL, "synthetic-key")
    return grant


def _successful_grant_candidate(
    *,
    ui_token: str | None = None,
) -> client.ActivationCandidate:
    identity = client.activation_identity_snapshot(ui_token)
    assert identity is not None
    result = client.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        source="server",
        lease_exp=_NOW + 3600,
        paid_exp=_NOW + 7200,
    )
    return client.ActivationCandidate(
        result=result,
        identity_snapshot=identity,
        ui_token=ui_token,
        commit_plan=client._ActivationCommitPlan(
            kind="grant",
            lease={"uid": "paid-user", "device": _DEVICE, "exp": _NOW + 3600},
            master_key=_MASTER_KEY,
            device=_DEVICE,
            token=identity.token,
            mode="paid",
            now=_NOW,
            exp=_NOW + 3600,
            ingest_url=_INGEST_URL,
            activation_url="https://srv.example/activate",
            rotating_ui_token=ui_token is not None,
        ),
    )


def _install_authoritative_deny_case(reason: str) -> None:
    if reason == "corrupt":
        client._app_dir().mkdir(parents=True, exist_ok=True)
        client._authoritative_deny_file().write_text("{", encoding="utf-8")
        return
    assert client._persist_authoritative_deny(reason)


def _clear_authoritative_deny_case() -> None:
    client._authoritative_deny_file().unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (403, "revoked"),
        (403, "device_revoked"),
        (426, "upgrade_required"),
    ],
)
def test_authoritative_deny_atomically_stops_product_and_upload(
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    reason: str,
) -> None:
    _prime_online_grant()
    monkeypatch.setattr(
        client,
        "_post_activate",
        lambda *_args, **_kwargs: (
            code,
            {"ok": False, "mode": "paid", "reason": reason},
        ),
    )

    result = enforce.refresh_activation()

    assert result.reason == reason
    assert enforce.current().reason == reason
    assert enforce.should_run_full() is False
    assert activation.is_activated() is False
    assert client._cache_file().exists() is False
    assert client.persistent_authoritative_deny_reason() == reason
    assert sample_upload._LOCAL_CFG_CACHE is None
    assert sample_upload.maybe_upload_settlement(_settled_artifact()) is False
    state = enforce.activation_state()
    assert state["run_full"] is False
    assert state["reason"] == reason

    marker = json.loads(client._authoritative_deny_file().read_text(encoding="utf-8"))
    assert marker == {"reason": reason, "schema": 1}
    serialized = json.dumps(marker)
    assert _TOKEN not in serialized
    assert _DEVICE not in serialized


def test_authoritative_deny_cleanup_does_not_short_circuit_when_marker_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_online_grant()
    provider_revocations: list[str] = []

    def revoke_provider(device: str) -> bool:
        provider_revocations.append(device)
        return True

    monkeypatch.setattr(
        client,
        "_post_activate",
        lambda *_args, **_kwargs: (
            403,
            {"ok": False, "mode": "paid", "reason": "device_revoked"},
        ),
    )
    monkeypatch.setattr(client, "_persist_authoritative_deny", lambda _reason: False)
    monkeypatch.setattr(
        client,
        "_force_fail_closed_provider",
        revoke_provider,
    )

    result = enforce.refresh_activation()

    assert result.activated is False
    assert client._cache_file().exists() is False
    assert sample_upload._LOCAL_CFG_CACHE is None
    assert provider_revocations == [_DEVICE]
    assert client.activation_runtime_denied() is True


def test_restart_consumes_persistent_reason_and_cannot_restore_stale_provider() -> None:
    _prime_online_grant()
    assert client._persist_authoritative_deny("device_revoked")
    client._RUNTIME_ACTIVATION_DENIED.clear()
    assert client._install_provider(
        _MASTER_KEY,
        _NOW + 3600,
        _DEVICE,
        _INGEST_URL,
    )
    enforce._LAST = client.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        source="server",
    )

    assert client.activation_runtime_denied() is True
    assert activation.is_activated() is False
    assert enforce.current().reason == "device_revoked"
    assert enforce.activation_state()["reason"] == "device_revoked"
    assert enforce.should_run_full() is False
    assert client._result_from_cache(
        client._load_cache(device=_DEVICE, token=_TOKEN),
        device=_DEVICE,
        ingest_url=_INGEST_URL,
        now=_NOW,
    ) is None


def test_successful_current_identity_grant_clears_persistent_deny() -> None:
    client._token_file().write_text(_TOKEN, encoding="utf-8")
    assert client._persist_authoritative_deny("revoked")
    client._RUNTIME_ACTIVATION_DENIED.set()
    identity = client.activation_identity_snapshot()
    result = client.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        source="server",
        lease_exp=_NOW + 3600,
        paid_exp=_NOW + 7200,
    )
    candidate = client.ActivationCandidate(
        result=result,
        identity_snapshot=identity,
        ui_token=None,
        commit_plan=client._ActivationCommitPlan(
            kind="grant",
            lease={"uid": "paid-user", "device": _DEVICE, "exp": _NOW + 3600},
            master_key=_MASTER_KEY,
            device=_DEVICE,
            token=_TOKEN,
            mode="paid",
            now=_NOW,
            exp=_NOW + 3600,
            ingest_url=_INGEST_URL,
            activation_url="https://srv.example/activate",
            rotating_ui_token=False,
        ),
    )

    outcome = client.commit_activation_candidate(candidate)

    assert outcome.committed is True
    assert outcome.result is result
    assert client.persistent_authoritative_deny_reason() is None
    assert client.activation_runtime_denied() is False
    assert activation.is_activated() is True


def test_incomplete_network_failure_fails_closed_without_persisting_deny_or_clearing_upload_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_online_grant()
    failure = client.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        source="server",
        transport_failure_classes=("connect_timeout",),
    )
    monkeypatch.setattr(client, "activate", lambda **_kwargs: failure)

    assert enforce.refresh_activation() is failure
    assert enforce.current() is failure
    assert enforce.should_run_full() is False
    assert client.persistent_authoritative_deny_reason() is None
    assert sample_upload._LOCAL_CFG_CACHE == (_INGEST_URL, "synthetic-key")


def test_queued_upload_from_before_deny_cannot_run_after_regrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_online_grant()
    queued: dict = {}
    posted: list[dict] = []
    monkeypatch.setattr(
        sample_upload.threading,
        "Thread",
        lambda target, args, daemon: type(
            "Queued",
            (),
            {"start": lambda self: queued.update(target=target, args=args)},
        )(),
    )
    monkeypatch.setattr(
        sample_upload,
        "_post",
        lambda _url, _key, rec, _skip=None: posted.append(rec),
    )

    assert sample_upload.maybe_upload_settlement(_settled_artifact()) is True
    assert queued
    assert sample_upload.revoke_upload_authority() is True
    client._RUNTIME_ACTIVATION_DENIED.clear()
    queued["target"](*queued["args"])

    assert posted == []


def test_monitor_settlement_path_cannot_dispatch_after_persistent_deny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_online_grant()
    assert client._persist_authoritative_deny("device_revoked")
    client._RUNTIME_ACTIVATION_DENIED.clear()
    sample_upload._LOCAL_CFG_CACHE = (_INGEST_URL, "synthetic-key")
    dispatched: list[dict] = []
    monkeypatch.setattr(
        sample_upload,
        "_post",
        lambda _url, _key, rec, _skip=None: dispatched.append(rec),
    )

    monitor.write_monitor_logs(
        _settled_artifact(),
        log_dir=tmp_path / "monitor-logs",
        append_logs=True,
    )

    assert dispatched == []
    assert sample_upload._LOCAL_CFG_CACHE is None


@pytest.mark.parametrize(
    ("reason", "message", "act_ready"),
    [
        ("revoked", "当前凭证已停用", False),
        ("revoked", "当前凭证已停用", True),
        ("device_revoked", "当前设备已停用", False),
        ("device_revoked", "当前设备已停用", True),
    ],
)
def test_tk_authoritative_stop_does_not_offer_token_as_a_false_remedy(
    reason: str,
    message: str,
    act_ready: bool,
) -> None:
    module = _overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    _prime_online_grant()
    assert client._persist_authoritative_deny(reason)
    client._RUNTIME_ACTIVATION_DENIED.clear()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = act_ready
    overlay._expired_latched = True
    overlay.activation_label = Widget()
    visibility: list[str] = []
    entry_available: list[bool] = []
    overlay._show_activation_label = lambda: visibility.append("show")
    overlay._hide_activation_label = lambda: visibility.append("hide")
    overlay._set_activation_entry_available = entry_available.append

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
    assert overlay._expired_latched is False
    assert entry_available == [False]
    assert visibility == ["show"]
    assert message in overlay.activation_label.kwargs["text"]


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("revoked", "当前凭证已停用"),
        ("device_revoked", "当前设备已停用"),
        ("upgrade_required", "当前版本已停用"),
    ],
)
def test_pre_network_persistent_deny_closes_all_overlay_entrypoints(
    reason: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    assert client._persist_authoritative_deny(reason)
    client._RUNTIME_ACTIVATION_DENIED.clear()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = False
    overlay._act_state = {
        "run_full": True,
        "mode": "checking",
        "reason": "pending",
        "text": "检测中…",
    }
    overlay.manual_status = Widget()
    overlay._summary_result_queue = object()
    overlay._manual_result_queue = object()
    overlay._summary_worker_running = True
    overlay._summary_worker_mode = "live"
    render_calls: list[str] = []
    input_calls: list[str] = []
    overlay._render_degraded = lambda: render_calls.append("degraded")
    overlay._manual_inputs_snapshot = lambda: input_calls.append("called")
    monkeypatch.setattr(
        module.tk,
        "Toplevel",
        lambda *_args, **_kwargs: pytest.fail("token dialog must stay closed"),
    )

    assert module.AhmadTkOverlay._activation_product_denied(overlay) is True
    assert message in overlay._act_state["text"]
    module.AhmadTkOverlay.apply_manual_inputs(overlay)
    module.AhmadTkOverlay._apply_live_summary(overlay, {}, {})
    assert module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot={},
        signature=(),
        started_at=0.0,
    ) is False
    assert module.AhmadTkOverlay._start_manual_summary_worker(
        overlay,
        snapshot={},
        settlement_manual=False,
        live_session_id="",
        revision=0,
    ) is False
    assert module.AhmadTkOverlay._open_token_dialog(overlay) is None

    assert input_calls == []
    assert render_calls == ["degraded", "degraded"]
    assert message in overlay.manual_status.kwargs["text"]
    assert "token" not in overlay.manual_status.kwargs["text"].lower()


def test_pre_network_without_marker_keeps_normal_checking_state() -> None:
    module = _overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = False
    overlay._act_state = {
        "run_full": True,
        "mode": "checking",
        "reason": "pending",
        "text": "检测中…",
    }
    overlay.activation_label = Widget()
    entry_available: list[bool] = []
    visibility: list[str] = []
    overlay._set_activation_entry_available = entry_available.append
    overlay._show_activation_label = lambda: visibility.append("show")

    assert module.AhmadTkOverlay._activation_product_denied(overlay) is False
    assert module.AhmadTkOverlay._update_activation_ui(overlay) is False
    assert entry_available == [True]
    assert visibility == ["show"]
    assert "免费额度 · 检测中" in overlay.activation_label.kwargs["text"]


@pytest.mark.parametrize(
    ("reason", "message", "update_visible"),
    [
        ("revoked", "当前凭证已停用", False),
        ("device_revoked", "当前设备已停用", False),
        ("upgrade_required", "当前版本已停用", True),
    ],
)
def test_real_tk_pre_network_persistent_stop_is_visible_and_not_clickable(
    reason: str,
    message: str,
    update_visible: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    assert client._persist_authoritative_deny(reason)
    client._RUNTIME_ACTIVATION_DENIED.clear()
    root = None
    for _ in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError:
            continue
    if root is None:
        pytest.skip("real Tk unavailable")
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        overlay._act_ready = False
        module.AhmadTkOverlay._apply_startup_update_notice(
            overlay,
            {
                "app": module.HERO_REF_DISPLAY_VERSION,
                "latest": "0.3.2",
                "min": "0.3.2",
                "url": "https://downloads.example/bidking.zip",
                "notice": "synthetic update",
                "ok": True,
                "reachable": True,
                "up_to_date": False,
                "supported": False,
            },
        )

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        module.AhmadTkOverlay._render_degraded(overlay)
        root.update_idletasks()

        assert message in str(overlay.activation_label.cget("text"))
        assert str(overlay.activation_action.winfo_manager()) == ""
        assert (
            str(overlay.update_notice.winfo_manager()) == "pack"
        ) is update_visible
        assert bool(overlay._startup_update_url) is update_visible
        assert "激活暂不可用" in str(overlay.title.cget("text"))
        assert module.AhmadTkOverlay._open_token_dialog(overlay) is None
        assert overlay._token_dialog is None
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("revoked", "当前凭证已停用"),
        ("device_revoked", "当前设备已停用"),
        ("upgrade_required", "当前版本已停用"),
        ("corrupt", "服务器激活服务暂时异常"),
    ],
)
@pytest.mark.parametrize("result_timing", ["queued_before_marker", "result_after_marker"])
def test_manual_result_drain_discards_work_across_authoritative_deny(
    reason: str,
    message: str,
    result_timing: str,
) -> None:
    module = _overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._act_state = {
        "run_full": True,
        "mode": "paid",
        "reason": "ok",
        "text": "会员有效",
    }
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = True
    overlay._manual_worker_seq = 7
    overlay._manual_input_revision = 3
    overlay._manual_active = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    previous_summary = {"status": "ok", "source": "previous"}
    overlay._last_summary = previous_summary
    overlay.manual_status = Widget()
    degraded: list[str] = []
    rendered: list[dict] = []
    overlay._render_degraded = lambda: degraded.append("degraded")
    overlay._set_manual_edit_enabled = lambda _enabled: None
    overlay._set_manual_button_state = lambda: None
    overlay.render = rendered.append
    overlay.render_standby = rendered.append
    result = {
        "seq": 7,
        "revision": 3,
        "snapshot": {"phase": "manual"},
        "summary": {"status": "ok", "source": "queued"},
        "settlement_manual": False,
        "live_session_id": "2523:synthetic",
    }

    if result_timing == "queued_before_marker":
        overlay._manual_result_queue.put(result)
    _install_authoritative_deny_case(reason)
    module.AhmadTkOverlay._drain_manual_summary_results(overlay)

    assert overlay._manual_worker_running is False
    assert overlay._manual_worker_seq == 8
    assert overlay._manual_result_queue.empty()
    assert overlay._manual_active is False
    assert overlay._manual_snapshot == {}
    assert overlay._manual_summary == {}
    assert overlay._last_summary is previous_summary
    assert rendered == []
    assert degraded == ["degraded"]
    assert message in overlay.manual_status.kwargs["text"]

    if result_timing == "result_after_marker":
        _clear_authoritative_deny_case()
        overlay._act_state = {
            "run_full": True,
            "mode": "paid",
            "reason": "ok",
            "text": "会员有效",
        }
        overlay._manual_result_queue.put(result)
        module.AhmadTkOverlay._drain_manual_summary_results(overlay)
        assert overlay._manual_result_queue.empty()
        assert overlay._manual_active is False
        assert overlay._manual_summary == {}
        assert overlay._last_summary is previous_summary
        assert rendered == []
        assert degraded == ["degraded"]


def test_manual_result_drain_without_marker_commits_current_result() -> None:
    module = _overlay_module()
    authority_fence = enforce.product_authority_fence_snapshot()
    assert authority_fence is not None

    class Widget:
        def configure(self, **_kwargs) -> None:
            return

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._act_state = {
        "run_full": True,
        "mode": "paid",
        "reason": "ok",
        "text": "会员有效",
    }
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = True
    overlay._manual_worker_seq = 2
    overlay._manual_input_revision = 4
    overlay._manual_active = False
    overlay._manual_summary = {}
    overlay._last_summary = {}
    overlay.manual_status = Widget()
    overlay._set_manual_edit_enabled = lambda _enabled: None
    overlay._set_manual_button_state = lambda: None
    rendered: list[dict] = []
    overlay.render = rendered.append
    overlay.render_standby = rendered.append
    summary = {"status": "ok", "source": "current"}
    overlay._manual_result_queue.put(
        {
            "seq": 2,
            "revision": 4,
            "snapshot": {"phase": "manual"},
            "summary": summary,
            "settlement_manual": False,
            "live_session_id": "2523:synthetic",
            "authority_fence": authority_fence,
        }
    )

    module.AhmadTkOverlay._drain_manual_summary_results(overlay)

    assert overlay._manual_worker_running is False
    assert overlay._manual_active is True
    assert overlay._manual_summary is summary
    assert overlay._last_summary is summary
    assert rendered == [summary]


def test_product_authority_fence_rejects_protocol_tuple_without_magic() -> None:
    fence = enforce.product_authority_fence_snapshot()
    assert fence is not None
    assert enforce.product_authority_fence_is_current(fence) is True
    magic_calls: list[str] = []

    class BombTuple(tuple):
        def __len__(self) -> int:
            magic_calls.append("len")
            raise RuntimeError("private-canary-len")

        def __getitem__(self, key):
            magic_calls.append("getitem")
            raise RuntimeError("private-canary-getitem")

        def __eq__(self, other) -> bool:
            magic_calls.append("eq")
            raise RuntimeError("private-canary-eq")

    assert enforce.product_authority_fence_is_current(BombTuple(fence)) is False
    assert magic_calls == []


def test_product_authority_fence_projects_identity_snapshot_without_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_fence = enforce.product_authority_fence_snapshot()
    assert current_fence is not None
    magic_calls: list[str] = []

    class BombStr(str):
        def encode(self, *_args, **_kwargs):
            magic_calls.append("encode")
            raise RuntimeError("private-canary-encode")

        def __eq__(self, _other) -> bool:
            magic_calls.append("eq")
            raise RuntimeError("private-canary-eq")

        def __hash__(self) -> int:
            magic_calls.append("hash")
            raise RuntimeError("private-canary-hash")

    class BombInt(int):
        def __eq__(self, _other) -> bool:
            magic_calls.append("eq")
            raise RuntimeError("private-canary-eq")

        def __lt__(self, _other) -> bool:
            magic_calls.append("lt")
            raise RuntimeError("private-canary-lt")

        def __mod__(self, _other):
            magic_calls.append("mod")
            raise RuntimeError("private-canary-mod")

    def rejected(snapshot: object) -> None:
        magic_calls.clear()
        monkeypatch.setattr(
            client,
            "activation_identity_snapshot",
            lambda _ui_token=None, _snapshot=snapshot: _snapshot,
        )
        assert enforce.product_authority_fence_snapshot() is None
        assert enforce.product_authority_fence_is_current(current_fence) is False
        assert magic_calls == []

    valid = {
        "token": _TOKEN,
        "device": _DEVICE,
        "revision": 0,
        "clear_epoch": 0,
    }
    for field, value in (
        ("token", BombStr(_TOKEN)),
        ("device", BombStr(_DEVICE)),
        ("revision", BombInt(0)),
        ("clear_epoch", BombInt(0)),
        ("revision", True),
        ("clear_epoch", True),
        ("revision", -2),
        ("clear_epoch", -1),
        ("revision", 1 << 64),
        ("clear_epoch", 1 << 64),
        ("token", "t" * 4097),
        ("device", "d" * 513),
        ("token", "\ud800"),
    ):
        fields = dict(valid)
        fields[field] = value
        rejected(client.ActivationIdentitySnapshot(**fields))


def test_product_authority_fence_rejects_fake_and_collision_snapshot_no_throw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_fence = enforce.product_authority_fence_snapshot()
    assert current_fence is not None
    magic_calls: list[str] = []

    class FakeSnapshot:
        @property
        def __class__(self):
            magic_calls.append("class")
            raise RuntimeError("private-canary-class")

        @property
        def token(self):
            magic_calls.append("token")
            raise RuntimeError("private-canary-token")

    fake = FakeSnapshot()
    monkeypatch.setattr(
        client,
        "activation_identity_snapshot",
        lambda _ui_token=None: fake,
    )
    assert enforce.product_authority_fence_snapshot() is None
    assert enforce.product_authority_fence_is_current(current_fence) is False
    assert magic_calls == []

    class BombKey:
        explode = False

        def __hash__(self) -> int:
            magic_calls.append("hash")
            return hash("token")

        def __eq__(self, _other) -> bool:
            magic_calls.append("eq")
            if self.explode:
                raise RuntimeError("private-canary-eq")
            return False

    snapshot = client.ActivationIdentitySnapshot(
        token=_TOKEN,
        device=_DEVICE,
        revision=0,
        clear_epoch=0,
    )
    fields = object.__getattribute__(snapshot, "__dict__")
    collision_key = BombKey()
    dict.__setitem__(fields, collision_key, "private-canary-value")
    collision_key.explode = True
    magic_calls.clear()
    monkeypatch.setattr(
        client,
        "activation_identity_snapshot",
        lambda _ui_token=None: snapshot,
    )
    assert enforce.product_authority_fence_snapshot() is None
    assert enforce.product_authority_fence_is_current(current_fence) is False
    assert magic_calls == []

    for mutate in (
        lambda values: dict.__delitem__(values, "clear_epoch"),
        lambda values: dict.__setitem__(values, "extra", "unexpected"),
    ):
        malformed = client.ActivationIdentitySnapshot(
            token=_TOKEN,
            device=_DEVICE,
            revision=0,
            clear_epoch=0,
        )
        mutate(object.__getattribute__(malformed, "__dict__"))
        monkeypatch.setattr(
            client,
            "activation_identity_snapshot",
            lambda _ui_token=None, _snapshot=malformed: _snapshot,
        )
        assert enforce.product_authority_fence_snapshot() is None
        assert enforce.product_authority_fence_is_current(current_fence) is False


def test_product_authority_fence_accepts_bounded_builtin_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = client.ActivationIdentitySnapshot(
        token="v1|用户|" + ("t" * 4090),
        device="设备-" + ("d" * 509),
        revision=(1 << 64) - 2,
        clear_epoch=(1 << 64) - 1,
    )
    monkeypatch.setattr(
        client,
        "activation_identity_snapshot",
        lambda _ui_token=None: snapshot,
    )
    fence = enforce.product_authority_fence_snapshot()
    assert fence is not None
    assert type(fence) is tuple
    assert all(type(value) is bytes for value in fence[:2])  # noqa: E721
    assert fence[2:4] == ((1 << 64) - 2, (1 << 64) - 1)
    assert enforce.product_authority_fence_is_current(fence) is True


@pytest.mark.parametrize(
    "reason",
    ["revoked", "device_revoked", "upgrade_required", "corrupt"],
)
@pytest.mark.parametrize("identity_change", ["same", "new"])
def test_manual_result_fence_rejects_work_after_unobserved_deny_and_regrant(
    reason: str,
    identity_change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    _prime_online_grant()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    queued_thread: dict[str, object] = {}

    class QueuedThread:
        def __init__(self, *, target, daemon, name) -> None:
            assert daemon is True
            assert name == "hero-ref-manual"
            queued_thread["target"] = target

        def start(self) -> None:
            return

    monkeypatch.setattr(module.threading, "Thread", QueuedThread)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._act_state = enforce.activation_state()
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_worker_seq = 6
    overlay._manual_input_revision = 3
    overlay._manual_active = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    previous_summary = {"status": "ok", "source": "previous"}
    overlay._last_summary = previous_summary
    overlay.manual_status = Widget()
    overlay._manual_result_summary = lambda _snapshot: {
        "status": "ok",
        "source": "pre-deny",
    }
    overlay._set_manual_edit_enabled = lambda _enabled: None
    overlay._set_manual_button_state = lambda: None
    rendered: list[dict] = []
    overlay.render = rendered.append
    overlay.render_standby = rendered.append

    assert module.AhmadTkOverlay._start_manual_summary_worker(
        overlay,
        snapshot={"phase": "manual"},
        settlement_manual=False,
        live_session_id="2523:synthetic",
        revision=3,
    )
    assert overlay._manual_worker_seq == 7
    started_fence = enforce.product_authority_fence_snapshot()
    assert started_fence is not None

    _install_authoritative_deny_case(reason)
    assert client.activation_runtime_denied() is True
    worker_target = queued_thread.get("target")
    assert callable(worker_target)
    worker_target()
    assert overlay._manual_result_queue.qsize() == 1

    new_token = (
        "v1|paid-user-new|synthetic"
        if identity_change == "new"
        else None
    )
    monkeypatch.setattr(
        client,
        "activate",
        lambda **kwargs: _successful_grant_candidate(
            ui_token=kwargs.get("ui_token")
        ),
    )
    regrant = enforce.startup_activate(
        ui_token=new_token,
        force_server=True,
    )
    assert regrant.activated is True
    assert client.activation_runtime_denied() is False
    assert client.persistent_authoritative_deny_reason() is None
    assert enforce.product_authority_fence_is_current(started_fence) is False
    overlay._act_state = enforce.activation_state()

    module.AhmadTkOverlay._drain_manual_summary_results(overlay)

    assert overlay._manual_result_queue.empty()
    assert overlay._manual_worker_running is False
    assert overlay._manual_active is False
    assert overlay._manual_snapshot == {}
    assert overlay._manual_summary == {}
    assert overlay._last_summary is previous_summary
    assert rendered == []
    assert "激活状态已更新" in overlay.manual_status.kwargs["text"]


@pytest.mark.parametrize(
    ("reason", "message", "update_visible"),
    [
        ("revoked", "当前凭证已停用", False),
        ("device_revoked", "当前设备已停用", False),
        ("upgrade_required", "当前版本已停用", True),
        ("corrupt", "服务器激活服务暂时异常", True),
    ],
)
@pytest.mark.parametrize("result_timing", ["queued_before_marker", "result_after_marker"])
def test_real_tk_manual_result_drain_never_revives_pre_deny_work(
    reason: str,
    message: str,
    update_visible: bool,
    result_timing: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    root = None
    for _ in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError:
            continue
    if root is None:
        pytest.skip("real Tk unavailable")
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        overlay._act_ready = True
        overlay._act_state = {
            "run_full": True,
            "mode": "paid",
            "reason": "ok",
            "text": "会员有效",
        }
        module.AhmadTkOverlay._apply_startup_update_notice(
            overlay,
            {
                "app": module.HERO_REF_DISPLAY_VERSION,
                "latest": "0.3.2",
                "min": "0.3.2",
                "url": "https://downloads.example/bidking.zip",
                "notice": "synthetic update",
                "ok": True,
                "reachable": True,
                "up_to_date": False,
                "supported": False,
            },
        )
        overlay._manual_worker_running = True
        overlay._manual_worker_seq = 11
        overlay._manual_input_revision = 5
        overlay._manual_active = False
        overlay._manual_snapshot = {}
        overlay._manual_summary = {}
        previous_summary = {"status": "ok", "source": "previous"}
        overlay._last_summary = previous_summary
        overlay.render = lambda _summary: pytest.fail("denied manual result rendered")
        overlay.render_standby = lambda _summary: pytest.fail(
            "denied stale manual result rendered"
        )
        result = {
            "seq": 11,
            "revision": 5,
            "snapshot": {"phase": "manual"},
            "summary": {"status": "ok", "source": "queued"},
            "settlement_manual": False,
            "live_session_id": "2523:synthetic",
        }

        if result_timing == "queued_before_marker":
            overlay._manual_result_queue.put(result)
        _install_authoritative_deny_case(reason)
        module.AhmadTkOverlay._drain_manual_summary_results(overlay)
        root.update_idletasks()

        assert overlay._manual_worker_running is False
        assert overlay._manual_worker_seq == 12
        assert overlay._manual_result_queue.empty()
        assert overlay._manual_active is False
        assert overlay._manual_summary == {}
        assert overlay._last_summary is previous_summary
        assert message in str(overlay.manual_status.cget("text"))
        assert message in str(overlay.activation_label.cget("text"))
        assert "激活暂不可用" in str(overlay.title.cget("text"))
        assert (
            str(overlay.update_notice.winfo_manager()) == "pack"
        ) is update_visible
        assert bool(overlay._startup_update_url) is update_visible

        if result_timing == "result_after_marker":
            _clear_authoritative_deny_case()
            overlay._act_state = {
                "run_full": True,
                "mode": "paid",
                "reason": "ok",
                "text": "会员有效",
            }
            overlay._manual_result_queue.put(result)
            module.AhmadTkOverlay._drain_manual_summary_results(overlay)
            root.update_idletasks()
            assert overlay._manual_result_queue.empty()
            assert overlay._manual_active is False
            assert overlay._manual_summary == {}
            assert overlay._last_summary is previous_summary
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
