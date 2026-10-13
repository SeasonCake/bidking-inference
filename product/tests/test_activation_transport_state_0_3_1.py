# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import importlib.util
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce

ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location(
        "ahmad_tk_overlay_activation_transport_0_3_1",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, raw: bytes, status: int = 200) -> None:
        self._raw = raw
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._raw


@pytest.fixture(autouse=True)
def _isolated_activation(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_pkg")
    monkeypatch.setattr(ca, "device_fingerprint", lambda: "DEVICE-TRANSPORT")


def test_real_transport_preserves_tls_reset_and_timeout_without_raw_error(monkeypatch):
    calls = {"value": 0}

    def fail(_request, timeout):
        del timeout
        calls["value"] += 1
        if calls["value"] == 1:
            raise urllib.error.URLError(
                ConnectionResetError("PRIVATE_TOKEN C:\\Users\\example")
            )
        raise urllib.error.URLError(socket.timeout("PRIVATE_TOKEN timeout"))

    monkeypatch.setattr(ca, "urlopen_verified", fail)

    result = ca.activate(ui_token="synthetic-token", force_server=True)

    assert not result.activated
    assert result.reason == "offline"
    assert result.transport_failure_classes == (
        "tls_connection_reset",
        "connect_timeout",
    )
    assert "PRIVATE_TOKEN" not in repr(result)
    assert "private" not in result.status_line().lower()


def test_http_reachable_invalid_json_stops_fallback_and_is_not_token_failure(monkeypatch):
    calls = {"value": 0}

    def invalid_json(_request, timeout):
        del timeout
        calls["value"] += 1
        return _Response(b"not-json")

    monkeypatch.setattr(ca, "urlopen_verified", invalid_json)

    result = ca.activate(ui_token="synthetic-token", force_server=True)
    state = enforce.activation_state(result)

    assert calls["value"] == 1
    assert result.transport_failure_classes == ("http_reachable_invalid_payload",)
    assert state["reason"] == "offline"
    assert state["mode"] == "none"
    assert state["needs_token"] is False
    assert "响应异常" in state["text"]


@pytest.mark.parametrize(
    ("failure_class", "message"),
    [
        ("tls_connection_reset", "网络出口或过滤组件"),
        ("tls_handshake_failed", "网络出口或过滤组件"),
        ("dns_lookup_failed", "无法解析"),
        ("connect_timeout", "超时或被拒绝"),
        ("connect_refused", "超时或被拒绝"),
    ],
)
def test_transport_failure_ui_is_actionable_and_never_requests_token(
    failure_class, message
):
    result = ca.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        transport_failure_classes=(failure_class,),
    )

    state = enforce.activation_state(result)

    assert state["run_full"] is False
    assert state["mode"] == "none"
    assert state["needs_token"] is False
    assert message in state["text"]
    assert "无需更换 token" in state["text"]
    assert state["transport_failure_classes"] == (failure_class,)


def test_unknown_transport_metadata_is_not_reflected_to_ui():
    result = ca.ActivationResult(
        activated=False,
        mode="paid",
        reason="offline",
        transport_failure_classes=("PRIVATE_TOKEN C:\\Users\\example",),
    )

    state = enforce.activation_state(result)

    assert state["transport_failure_classes"] == ()
    assert "PRIVATE_TOKEN" not in state["text"]
    assert "private" not in state["text"].lower()


def test_overlay_network_failure_hides_token_entry_and_does_not_latch_expiry(
    monkeypatch,
):
    module = _overlay_module()
    state = enforce.activation_state(
        ca.ActivationResult(
            activated=False,
            mode="paid",
            reason="offline",
            transport_failure_classes=("tls_connection_reset",),
        )
    )
    monkeypatch.setattr(enforce, "activation_state", lambda: state)

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = False
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None
    overlay._hide_activation_label = lambda: None
    entries: list[bool] = []
    overlay._set_activation_entry_available = entries.append

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
    assert overlay._expired_latched is False
    assert entries == [False]
    assert "无需更换 token" in overlay.activation_label.kwargs["text"]


def test_degraded_network_copy_never_points_to_hidden_token_entry():
    module = _overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.text = ""

        def configure(self, **kwargs) -> None:
            self.text = kwargs.get("text", self.text)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_state = {
        "needs_token": False,
        "text": "当前网络出口或过滤组件阻断激活服务 · 无需更换 token",
    }
    overlay.title = Widget()
    overlay.subtitle = Widget()
    overlay.status = Widget()
    overlay._hide_pixel_cat = lambda: None
    overlay._clear_values = lambda: None

    module.AhmadTkOverlay._render_degraded(overlay)

    assert overlay.title.text == "🔒 激活暂不可用"
    assert "点右侧 token 入口" not in overlay.subtitle.text
    assert "无需更换 token" in overlay.subtitle.text


def test_network_fallback_success_does_not_taint_success_result(monkeypatch):
    calls = {"value": 0}
    now = 2_000_000
    body = {
        "ok": True,
        "mode": "trial",
        "lease": (
            '{"uid":"trial:DEVICE-TRANSPORT","device":"DEVICE-TRANSPORT",'
            f'"exp":{now + 3600}'
            "}.signature"
        ),
        "trial_exp": now + 3600,
        "master_key": base64.b64encode(b"k" * 32).decode("ascii"),
    }

    # Keep the real _post_activate path while returning a valid second response.
    def fallback_valid(_request, timeout):
        del timeout
        calls["value"] += 1
        if calls["value"] == 1:
            raise urllib.error.URLError(ConnectionResetError("reset"))
        return _Response(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(ca, "urlopen_verified", fallback_valid)

    result = ca.activate(ui_token="", force_server=True, now=now)

    assert result.activated
    assert result.mode == "trial"
    assert result.transport_failure_classes == ()
    assert calls["value"] == 2
