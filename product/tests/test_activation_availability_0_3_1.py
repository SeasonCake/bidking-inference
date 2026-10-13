# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import importlib.util
import io
import json
import socket
import sys
import tkinter as tk
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce
from scripts import activation_transport_diagnostic as diag

ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    name = "ahmad_tk_overlay_activation_availability_0_3_1"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _new_tk_root(attempts: int = 3):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as exc:  # noqa: PERF203
            last = exc
    pytest.skip(f"真 Tk 不可用(环境层): {last}")


class _Response:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class _ExplodingReasonError(Exception):
    @property
    def reason(self):
        raise RuntimeError("PRIVATE_REASON_ATTRIBUTE")


class _ExplodingWinerror(OSError):
    @property
    def winerror(self):
        raise RuntimeError("PRIVATE_WINERROR_ATTRIBUTE")


class _ExplodingWinerrorEq:
    def __init__(self) -> None:
        self.magic_calls = 0

    def __eq__(self, _other):
        self.magic_calls += 1
        raise RuntimeError("PRIVATE_WINERROR_EQ_PROTOCOL")


class _ExplodingWinerrorInt(int):
    def __new__(cls):
        value = int.__new__(cls, 10054)
        value.magic_calls = 0
        return value

    def __eq__(self, _other):
        self.magic_calls += 1
        raise RuntimeError("PRIVATE_WINERROR_INT_SUBCLASS")


class _ProtocolWinerror(OSError):
    def __init__(self, winerror_value) -> None:
        super().__init__("private nested failure")
        self._winerror_value = winerror_value

    @property
    def reason(self):
        return self

    @property
    def winerror(self):
        return self._winerror_value


class _ClassImpostorWinerrorReason:
    def __init__(self) -> None:
        self.magic_calls = 0

    @property
    def __class__(self):
        self.magic_calls += 1
        return OSError

    @property
    def winerror(self):
        return 10054


class _ExplodingClassWinerrorReason:
    def __init__(self) -> None:
        self.magic_calls = 0

    @property
    def __class__(self):
        self.magic_calls += 1
        raise RuntimeError("PRIVATE_REASON_CLASS_PROTOCOL")

    @property
    def winerror(self):
        return 10054


@pytest.fixture(autouse=True)
def _isolated_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_pkg")
    monkeypatch.setattr(ca, "device_fingerprint", lambda: "DEVICE-AVAILABILITY")
    # This suite isolates the HTTPS availability classifier.  The production
    # HTTP-E2EE fallback is covered by its dedicated transport/packaging tests;
    # leaving it enabled here would turn synthetic "all HTTPS endpoints failed"
    # fixtures into real network calls.
    monkeypatch.setattr(ca, "_load_activation_http_e2ee_config", lambda: None)


def _stage_counts(result: ca.ActivationResult) -> dict[str, int]:
    return dict(result.transport_summary.stage_counts)


def test_existing_credential_cold_start_all_endpoints_failed_is_distinct_and_safe(
    monkeypatch,
):
    private = "PRIVATE_TOKEN C:\\Users\\example"
    calls = 0

    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def fail(_request, timeout):
        nonlocal calls
        del timeout
        calls += 1
        if calls == 1:
            raise urllib.error.URLError(ConnectionResetError(private))
        raise urllib.error.URLError(socket.timeout(private))

    monkeypatch.setattr(ca, "urlopen_verified", fail)

    result = ca.activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state(result)

    assert not result.activated
    assert result.reason == "offline"
    assert result.transport_summary.attempt_count == 2
    assert result.transport_summary.endpoint_count == 2
    assert result.transport_summary.all_endpoints_failed is True
    assert result.transport_failure_classes == (
        "tls_connection_reset",
        "connect_timeout",
    )
    assert _stage_counts(result) == {
        "dns": 0,
        # The endpoint summary records each endpoint's terminal direct result;
        # the fixed class tuple above retains the preceding proxy TLS failure.
        "tcp": 2,
        "tls": 0,
        "http": 0,
        "unknown": 0,
    }
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert "已有激活凭据" in state["text"]
    assert "在线复核失败" in state["text"]
    assert "无需更换 token" in state["text"]
    assert private not in repr(result)
    assert private not in repr(state)


def test_transport_exception_attribute_reads_are_no_throw_and_fallback(
    monkeypatch,
):
    failures = iter(
        (
            urllib.error.URLError(_ExplodingWinerror("private nested failure")),
            _ExplodingReasonError("private outer failure"),
        )
    )
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )

    def fail(_request, timeout):
        del timeout
        raise next(failures)

    monkeypatch.setattr(ca, "urlopen_verified", fail)

    result = ca.activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state(result)

    assert not result.activated
    assert result.reason == "offline"
    assert result.transport_failure_classes == ("transport_unknown",)
    assert result.transport_summary.attempt_count == 2
    assert result.transport_summary.all_endpoints_failed is True
    assert _stage_counts(result) == {
        "dns": 0,
        "tcp": 0,
        "tls": 0,
        "http": 0,
        "unknown": 2,
    }
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert "无需更换 token" in state["text"]
    assert "PRIVATE_" not in repr(result)
    assert "PRIVATE_" not in repr(state)


@pytest.mark.parametrize(
    "winerror_value",
    [_ExplodingWinerrorEq(), _ExplodingWinerrorInt()],
    ids=["protocol-object", "int-subclass"],
)
def test_winerror_protocol_values_are_unknown_without_magic_calls(
    monkeypatch,
    winerror_value,
):
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(_ProtocolWinerror(winerror_value))
        ),
    )

    result = ca.activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state(result)

    assert not result.activated
    assert result.reason == "offline"
    assert result.transport_failure_classes == ("transport_unknown",)
    assert result.transport_summary.attempt_count == 2
    assert _stage_counts(result)["unknown"] == 2
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert winerror_value.magic_calls == 0
    assert "PRIVATE_" not in repr(result)
    assert "PRIVATE_" not in repr(state)


@pytest.mark.parametrize(
    ("winerror_value", "expected"),
    [(10054, "tls_connection_reset"), (10061, "connect_refused")],
)
def test_exact_builtin_winerror_values_keep_fixed_transport_classification(
    winerror_value,
    expected,
):
    error = OSError("private nested failure")
    error.winerror = winerror_value
    assert type(error) is OSError
    assert ca._activation_transport_failure(error) == expected


def test_nested_real_base_exception_reason_cycle_keeps_fixed_classification():
    nested = _ProtocolWinerror(10054)
    assert nested.reason is nested
    assert ca._activation_transport_failure(
        urllib.error.URLError(nested)
    ) == "tls_connection_reset"


@pytest.mark.parametrize(
    "reason_factory",
    [_ClassImpostorWinerrorReason, _ExplodingClassWinerrorReason],
    ids=["class-impostor", "class-explodes"],
)
def test_reason_class_impostor_is_unknown_without_protocol_calls(
    monkeypatch,
    reason_factory,
):
    reason = reason_factory()
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(reason)
        ),
    )

    result = ca.activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state(result)

    assert not result.activated
    assert result.reason == "offline"
    assert result.transport_failure_classes == ("transport_unknown",)
    assert _stage_counts(result)["unknown"] == 2
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert reason.magic_calls == 0
    assert "PRIVATE_" not in repr(result)
    assert "PRIVATE_" not in repr(state)


def test_startup_and_real_tk_keep_credential_state_for_reason_class_impostor(
    monkeypatch,
):
    module = _overlay_module()
    reason = _ClassImpostorWinerrorReason()
    monkeypatch.setattr(enforce, "_LAST", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_GRANT", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_IDENTITY", None)
    monkeypatch.setattr(enforce, "_LAST_HEARTBEAT", None)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_COUNT", 0)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_STARTED_AT", None)
    monkeypatch.setattr(enforce, "_ACTIVATION_REQUEST_GENERATION", 0)
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(reason)
        ),
    )

    result = enforce.startup_activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state()

    assert result.reason == "offline"
    assert result.transport_failure_classes == ("transport_unknown",)
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert reason.magic_calls == 0

    root = _new_tk_root()
    try:
        root.tk.call("tk", "scaling", 1.25)
        label = tk.Label(root)
        label.pack()
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = label
        overlay._show_activation_label = lambda: None
        overlay._hide_activation_label = lambda: None
        overlay._set_activation_entry_available = lambda _available: None

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        root.update_idletasks()

        text = str(label.cget("text"))
        assert "已有激活凭据" in text
        assert "在线复核失败" in text
        assert "无需更换 token" in text
        assert not text.startswith("免费额度")
    finally:
        root.destroy()


def test_startup_and_real_tk_keep_credential_state_for_protocol_winerror(
    monkeypatch,
):
    module = _overlay_module()
    protocol_value = _ExplodingWinerrorEq()
    monkeypatch.setattr(enforce, "_LAST", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_GRANT", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_IDENTITY", None)
    monkeypatch.setattr(enforce, "_LAST_HEARTBEAT", None)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_COUNT", 0)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_STARTED_AT", None)
    monkeypatch.setattr(enforce, "_ACTIVATION_REQUEST_GENERATION", 0)
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(_ProtocolWinerror(protocol_value))
        ),
    )

    result = enforce.startup_activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state()

    assert result.reason == "offline"
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert protocol_value.magic_calls == 0
    assert "PRIVATE_" not in repr(result)
    assert "PRIVATE_" not in repr(state)

    root = _new_tk_root()
    try:
        root.tk.call("tk", "scaling", 1.25)
        label = tk.Label(root)
        label.pack()
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = label
        overlay._show_activation_label = lambda: None
        overlay._hide_activation_label = lambda: None
        overlay._set_activation_entry_available = lambda _available: None

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        root.update_idletasks()

        text = str(label.cget("text"))
        assert "已有激活凭据" in text
        assert "在线复核失败" in text
        assert "无需更换 token" in text
        assert not text.startswith("免费额度")
        assert label.winfo_reqwidth() > 0
        assert label.winfo_reqheight() > 0
    finally:
        root.destroy()


def test_startup_and_real_tk_keep_credential_state_for_nested_unknown_failure(
    monkeypatch,
):
    module = _overlay_module()
    monkeypatch.setattr(enforce, "_LAST", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_GRANT", None)
    monkeypatch.setattr(enforce, "_LAST_ONLINE_IDENTITY", None)
    monkeypatch.setattr(enforce, "_LAST_HEARTBEAT", None)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_COUNT", 0)
    monkeypatch.setattr(enforce, "_HEARTBEAT_FAILURE_STARTED_AT", None)
    monkeypatch.setattr(enforce, "_ACTIVATION_REQUEST_GENERATION", 0)
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(_ExplodingWinerror("private nested failure"))
        ),
    )

    result = enforce.startup_activate(
        ui_token="synthetic-credential",
        force_server=True,
    )
    state = enforce.activation_state()

    assert result.reason == "offline"
    assert state["activation_availability"] == "credential_online_recheck_failed"
    assert state["credential_present"] is True
    assert state["needs_token"] is False
    assert not state["text"].startswith("免费额度")
    assert "无需更换 token" in state["text"]

    root = _new_tk_root()
    try:
        root.tk.call("tk", "scaling", 1.25)
        label = tk.Label(root)
        label.pack()
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = label
        overlay._show_activation_label = lambda: None
        overlay._hide_activation_label = lambda: None
        overlay._set_activation_entry_available = lambda _available: None

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        root.update_idletasks()

        text = str(label.cget("text"))
        assert "已有激活凭据" in text
        assert "无需更换 token" in text
        assert not text.startswith("免费额度")
        assert label.winfo_reqwidth() > 0
        assert label.winfo_reqheight() > 0
    finally:
        root.destroy()


def test_no_credential_cold_start_all_endpoints_failed_is_distinct(monkeypatch):
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(socket.gaierror("private"))
        ),
    )

    result = ca.activate(ui_token="", force_server=True)
    state = enforce.activation_state(result)

    assert not result.activated
    assert result.transport_summary.all_endpoints_failed is True
    assert state["activation_availability"] == "cold_start_all_endpoints_failed"
    assert state["credential_present"] is False
    assert "冷启动" in state["text"]
    assert "已有激活凭据" not in state["text"]


def test_proxy_tls_direct_http_ready_reaches_real_product_ui(monkeypatch):
    module = _overlay_module()
    calls = 0
    monkeypatch.delenv(ca._ENV_DISABLE_ACTIVATION_DIRECT_RETRY, raising=False)
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: ("https://primary.invalid/activate",),
    )

    def proxy_tls_then_direct_http(request, timeout):
        nonlocal calls
        del timeout
        calls += 1
        if type(request) is ca._ActivationProxiedHTTPSRequest:
            raise urllib.error.URLError(
                ca.ssl.SSLError("PRIVATE_PROXY_TLS_DETAIL")
            )
        return _Response({"ok": "invalid"})

    monkeypatch.setattr(ca, "urlopen_verified", proxy_tls_then_direct_http)
    result = ca.activate(ui_token="existing-credential", force_server=True)
    state = enforce.activation_state(result)

    assert calls == 2
    assert result.reason == "offline"
    assert result.transport_failure_classes == (
        "tls_handshake_failed",
        "http_reachable_invalid_payload",
    )
    assert result.transport_summary.availability == (
        "system_proxy_tls_path_incompatible"
    )
    assert state["activation_availability"] == (
        "system_proxy_tls_path_incompatible"
    )
    assert state["text"] == (
        "系统代理/TLS路径不兼容 · 可关闭非必要代理或由客户端直连重试；"
        "无需更换 token"
    )
    assert "PRIVATE" not in repr(result)
    assert "PRIVATE" not in repr(state)

    monkeypatch.setattr(enforce, "activation_state", lambda: state)
    root = _new_tk_root()
    try:
        root.tk.call("tk", "scaling", 1.25)
        root.geometry("440x80")
        label = tk.Label(
            root,
            font=(module.FONT_UI, 8, "bold"),
            anchor="w",
        )
        label.pack(fill="x")
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = label
        overlay._show_activation_label = lambda: None
        overlay._hide_activation_label = lambda: None
        overlay._set_activation_entry_available = lambda _available: None

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        root.update_idletasks()

        assert str(label.cget("text")) == f'🔒 {state["text"]}'
        assert label.winfo_reqwidth() <= module.MINI_BASE_WIDTH
        assert label.winfo_reqheight() > 0
    finally:
        root.destroy()


@pytest.mark.parametrize(
    ("reason", "failures", "availability", "expected_text"),
    [
        (
            "offline",
            ("tls_handshake_failed",),
            "",
            "当前网络出口或过滤组件阻断激活服务",
        ),
        (
            "offline",
            ("dns_lookup_failed",),
            "system_proxy_tls_path_incompatible",
            "当前网络无法解析激活服务",
        ),
        (
            "server_error",
            ("tls_handshake_failed",),
            "system_proxy_tls_path_incompatible",
            "激活服务器暂时异常",
        ),
        (
            "bad_token",
            ("tls_handshake_failed",),
            "system_proxy_tls_path_incompatible",
            "token 无效",
        ),
    ],
    ids=["generic-tls", "dns", "server", "token"],
)
def test_proxy_tls_guidance_requires_exact_combined_product_state(
    reason,
    failures,
    availability,
    expected_text,
):
    result = ca.ActivationResult(
        activated=False,
        mode="none",
        reason=reason,
        transport_failure_classes=failures,
        transport_summary=ca.ActivationTransportSummary(
            attempt_count=1,
            endpoint_count=2,
            stage_counts=(
                ("dns", 0),
                ("tcp", 0),
                ("tls", 1),
                ("http", 0),
                ("unknown", 0),
            ),
            all_endpoints_failed=False,
            availability=availability,
        ),
    )

    state = enforce.activation_state(result)

    assert expected_text in state["text"]
    assert state["activation_availability"] != (
        "system_proxy_tls_path_incompatible"
    )
    assert "由客户端直连重试" not in state["text"]


def test_proxy_tls_availability_protocol_value_is_zero_magic_and_private_safe():
    class AvailabilitySubclass(str):
        def __new__(cls):
            value = str.__new__(cls, "system_proxy_tls_path_incompatible")
            value.magic_calls = 0
            return value

        def __eq__(self, _other):
            self.magic_calls += 1
            raise RuntimeError("PRIVATE_AVAILABILITY_MAGIC")

    availability = AvailabilitySubclass()
    result = ca.ActivationResult(
        activated=False,
        mode="none",
        reason="offline",
        transport_failure_classes=("tls_handshake_failed",),
        transport_summary=ca.ActivationTransportSummary(
            attempt_count=1,
            endpoint_count=1,
            stage_counts=(
                ("dns", 0),
                ("tcp", 0),
                ("tls", 1),
                ("http", 0),
                ("unknown", 0),
            ),
            all_endpoints_failed=True,
            availability=availability,
        ),
    )

    state = enforce.activation_state(result)

    assert availability.magic_calls == 0
    assert state["activation_availability"] == "cold_start_all_endpoints_failed"
    assert "由客户端直连重试" not in state["text"]
    assert "PRIVATE" not in repr(state)


def test_overlay_does_not_prefix_existing_credential_failure_as_free_quota(
    monkeypatch,
):
    module = _overlay_module()
    state = {
        "run_full": False,
        "mode": "none",
        "reason": "offline",
        "needs_token": False,
        "warn": True,
        "credential_present": True,
        "activation_availability": "credential_online_recheck_failed",
        "text": "已有激活凭据，但所有激活线路在线复核失败 · 无需更换 token",
    }
    monkeypatch.setattr(enforce, "activation_state", lambda: state)

    class Widget:
        def __init__(self) -> None:
            self.text = ""

        def configure(self, **kwargs) -> None:
            self.text = str(kwargs.get("text", self.text))

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = False
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None
    overlay._hide_activation_label = lambda: None
    overlay._set_activation_entry_available = lambda _available: None

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
    assert "已有激活凭据" in overlay.activation_label.text
    assert not overlay.activation_label.text.startswith("免费额度")


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75])
def test_real_tk_existing_credential_failure_remains_visible_at_supported_dpi(
    monkeypatch,
    scale,
):
    module = _overlay_module()
    state = {
        "run_full": False,
        "mode": "none",
        "reason": "offline",
        "needs_token": False,
        "warn": True,
        "credential_present": True,
        "activation_availability": "credential_online_recheck_failed",
        "text": "已有激活凭据，但所有激活线路在线复核失败 · 无需更换 token",
    }
    monkeypatch.setattr(enforce, "activation_state", lambda: state)
    root = _new_tk_root()
    try:
        root.tk.call("tk", "scaling", scale)
        label = tk.Label(root)
        label.pack()
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = label
        overlay._show_activation_label = lambda: None
        overlay._hide_activation_label = lambda: None
        overlay._set_activation_entry_available = lambda _available: None

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
        root.update_idletasks()

        text = str(label.cget("text"))
        assert "已有激活凭据" in text
        assert "无需更换 token" in text
        assert not text.startswith("免费额度")
        assert label.winfo_reqwidth() > 0
        assert label.winfo_reqheight() > 0
    finally:
        root.destroy()


def test_runtime_short_fluctuation_remains_separate_from_cold_start(monkeypatch):
    now = 2_000_000
    grant = ca.ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        lease_exp=now + 300,
        paid_exp=now + 300,
        plan_kind="30d",
    )
    failure = ca.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        transport_failure_classes=("tls_connection_reset",),
        transport_summary=ca.ActivationTransportSummary(
            attempt_count=2,
            endpoint_count=2,
            stage_counts=(
                ("dns", 0),
                ("tcp", 0),
                ("tls", 2),
                ("http", 0),
                ("unknown", 0),
            ),
            all_endpoints_failed=True,
        ),
    )
    monkeypatch.setattr(
        enforce,
        "_effective_activation_snapshot",
        lambda *, now: (grant, failure),
    )

    state = enforce.activation_state(now=now)

    assert state["run_full"] is True
    assert state["activation_availability"] == "runtime_short_fluctuation"
    assert state["transport_grace"] is True
    assert state["text"] == "激活连接波动，正在重试"


def test_malformed_transport_summary_cannot_spoof_cold_start_state_or_leak():
    result = ca.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        transport_failure_classes=("tls_connection_reset",),
        transport_summary=SimpleNamespace(
            attempt_count=1,
            endpoint_count=1,
            stage_counts=(("PRIVATE_TOKEN C:\\Users\\example", 1),),
            all_endpoints_failed=True,
        ),
    )

    state = enforce.activation_state(result)

    assert state["activation_availability"] == "entitlement_unavailable"
    assert state["transport_summary"]["all_endpoints_failed"] is False
    assert "PRIVATE_TOKEN" not in repr(state)
    assert "private" not in repr(state).lower()


def test_three_candidate_profile_uses_only_network_fallback_and_can_recover(
    monkeypatch,
):
    now = 2_000_000
    calls = 0
    body = {
        "ok": True,
        "mode": "trial",
        "lease": (
            '{"uid":"trial:DEVICE-AVAILABILITY",'
            '"device":"DEVICE-AVAILABILITY",'
            f'"exp":{now + 600}'
            "}.signature"
        ),
        "trial_exp": now + 600,
        "master_key": base64.b64encode(b"k" * 32).decode("ascii"),
    }
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
            "https://contingency.invalid/activate",
        ),
    )

    def recover(_request, timeout):
        nonlocal calls
        del timeout
        calls += 1
        if calls == 1:
            raise urllib.error.URLError(ConnectionResetError("private"))
        if calls == 2:
            raise urllib.error.URLError(socket.timeout("private"))
        return _Response(body)

    monkeypatch.setattr(ca, "urlopen_verified", recover)

    result = ca.activate(ui_token="", force_server=True, now=now)

    assert result.activated
    assert calls == 3
    assert result.transport_failure_classes == ()
    assert result.transport_summary.endpoint_count == 3
    # Primary consumed proxy+direct, then fallback answered on its proxy arm:
    # two logical endpoint outcomes, three underlying Python requests.
    assert result.transport_summary.attempt_count == 2
    assert result.transport_summary.all_endpoints_failed is False
    assert _stage_counts(result) == {
        "dns": 0,
        "tcp": 1,
        "tls": 0,
        "http": 1,
        "unknown": 0,
    }


@pytest.mark.parametrize(
    "reason",
    ["bad_token", "device_mismatch", "revoked", "device_revoked"],
)
def test_http_authoritative_denial_never_falls_back(monkeypatch, reason):
    calls = 0
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
            "https://contingency.invalid/activate",
        ),
    )

    def reject(request, timeout):
        nonlocal calls
        del timeout
        calls += 1
        raw = json.dumps(
            {"ok": False, "mode": "paid", "reason": reason}
        ).encode("utf-8")
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "private",
            {},
            io.BytesIO(raw),
        )

    monkeypatch.setattr(ca, "urlopen_verified", reject)

    result = ca.activate(
        ui_token="synthetic-credential",
        force_server=True,
    )

    assert calls == 1
    assert not result.activated
    assert result.reason == reason
    assert result.transport_summary.attempt_count == 1
    assert _stage_counts(result)["http"] == 1


def test_unsigned_disk_cache_does_not_authorize_when_all_endpoints_fail(monkeypatch):
    now = 2_000_000
    token = "synthetic-credential"
    assert ca._save_cache(
        lease={
            "uid": "synthetic",
            "device": "DEVICE-AVAILABILITY",
            "exp": now + 3600,
            "paid_exp": now + 3600,
            "plan_kind": "30d",
        },
        master_key=b"k" * 32,
        device="DEVICE-AVAILABILITY",
        token=token,
        mode="paid",
        now=now,
    )
    monkeypatch.setattr(
        ca,
        "_activate_urls",
        lambda _ingest: (
            "https://primary.invalid/activate",
            "https://fallback.invalid/activate",
        ),
    )
    monkeypatch.setattr(
        ca,
        "urlopen_verified",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(socket.timeout("private"))
        ),
    )

    result = ca.activate(
        ui_token=token,
        force_server=True,
        now=now,
    )

    assert not result.activated
    assert result.source == "server"
    assert result.reason == "offline"
    assert ca._result_from_cache(
        ca._load_cache(device="DEVICE-AVAILABILITY", token=token),
        device="DEVICE-AVAILABILITY",
        ingest_url=None,
        now=now,
    ) is None


def test_diagnostic_reports_only_fixed_stage_counts_and_availability():
    private = "PRIVATE_TOKEN C:\\Users\\example"
    rows = [
        {
            "name": "primary",
            "transport": "packaged_python",
            "endpoint": "health",
            "ok": False,
            "stage": "tls",
            "failure_class": "tls_connection_reset",
            "url": private,
        },
        {
            "name": "fallback",
            "transport": "packaged_python",
            "endpoint": "activation_readiness",
            "ok": False,
            "stage": "tcp",
            "failure_class": "connect_timeout",
            "exception": private,
        },
    ]

    summary = diag.summarize_transport_results(rows)
    availability = diag.classify_activation_availability(
        rows,
        {"credential_available": True},
    )

    assert summary == {
        "probe_count": 2,
        "success_count": 0,
        "http_response_count": 0,
        "network_failure_count": 2,
        "stage_counts": {
            "dns": 0,
            "tcp": 1,
            "tls": 1,
            "http": 0,
            "unknown": 0,
        },
    }
    assert availability == "credential_online_recheck_failed"
    assert private not in repr(summary)
    assert private not in availability
