"""HTTPS-only 多地址 fallback 与本地开发例外设计门。

背景: 客户端只认一个地址时, 那个地址一挂 = 全员失活；但明文 IP 兜底会暴露 token/master key。
正式发布使用两个独立 HTTPS 入口，HTTP 仅允许 localhost/loopback 开发。

⭐两条正确性铁律(都容易写错):
  1. 【网络层失败才 fallback】: DNS/连不上/超时/TLS 失败 -> 试下一个地址。
  2. 【HTTP 4xx/5xx 绝不 fallback】: 服务器活着只是拒绝了(如 token 无效 401)。换地址还是 401,
     重试只会拖慢启动 + 触发限流 + 把同一个失败请求打到所有服务器。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time

import pytest

from bidking_lab.live import version_check as vc
from bidking_lab.secure import client_activate as ca


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_private_config(monkeypatch):
    """隔离作者机的私有上传配置/env，避免本机状态污染地址契约。

    真实群发配置必须由各测试显式传入；否则这层隔离会把加密包实际携带的
    production ingest 隐去，造成 fallback 测试假绿。"""
    for k in ("BIDKING_SERVER_BASE", "BIDKING_SAMPLE_UPLOAD_URL", "BIDKING_ACTIVATE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(vc, "resolve_ingest_url", lambda: None)
    monkeypatch.setattr(ca, "_resolve_ingest_url", lambda: None)


def _isolate_app_dir(monkeypatch, tmp_path, device: str):
    """app 目录指向 tmp(无缓存, 不污染本机) + 固定设备指纹。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ca, "device_fingerprint", lambda: device)
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_pkg")


def _valid_trial_response(device: str) -> dict:
    """Return a complete server-success fixture accepted by the strict client gate."""
    exp = int(time.time()) + 3600
    lease = {
        "uid": f"trial:{device}",
        "device": device,
        "exp": exp,
    }
    return {
        "ok": True,
        "mode": "trial",
        "lease": json.dumps(lease, separators=(",", ":")) + ".signature",
        "trial_exp": exp,
        "master_key": base64.b64encode(b"k" * 32).decode("ascii"),
    }


# --------------------------------------------------------------------------- #
# 地址列表本身
# --------------------------------------------------------------------------- #
def test_baked_bases_are_two_https_origins_without_plaintext_ip():
    bases = vc.iter_server_bases()
    assert len(bases) >= 2, "至少要有两个独立 HTTPS 入口(单地址=单点)"
    assert all(b.startswith("https://") for b in bases)
    assert bases[0] == "https://api.bidking.example.invalid"
    assert bases[1] == "https://backup.bidking.example.invalid"
    assert not any("203.0.113.10" in b for b in bases)


def test_resolve_server_base_returns_first():
    assert vc.resolve_server_base() == vc.iter_server_bases()[0]


def test_env_override_cannot_change_controlled_base_generation(monkeypatch):
    monkeypatch.setenv("BIDKING_SERVER_BASE", "http://127.0.0.1:9999/")
    assert vc.iter_server_bases() == [
        "https://api.bidking.example.invalid",
        "https://backup.bidking.example.invalid",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.bidking.example.invalid",
        "http://203.0.113.10:18900",
        "ftp://api.bidking.example.invalid",
    ],
)
def test_external_server_override_is_ignored(monkeypatch, url):
    monkeypatch.setenv("BIDKING_SERVER_BASE", url)
    assert vc.iter_server_bases()[0] == "https://api.bidking.example.invalid"


def test_loopback_override_does_not_replace_release_authority(monkeypatch):
    monkeypatch.setenv("BIDKING_SERVER_BASE", "http://[::1]:18900/")
    assert vc.iter_server_bases()[0] == "https://api.bidking.example.invalid"


def test_activate_urls_follow_base_list():
    urls = ca._activate_urls(None)
    assert urls == [b + "/activate" for b in vc.iter_server_bases()]


def test_production_ingest_keeps_baked_activation_fallbacks():
    assert ca._activate_urls("https://api.bidking.example.invalid/ingest") == [
        "https://api.bidking.example.invalid/activate",
        "https://backup.bidking.example.invalid/activate",
    ]


def test_private_ingest_cannot_replace_activation_authority():
    assert ca._activate_urls("https://private.example/api/ingest") == [
        "https://api.bidking.example.invalid/activate",
        "https://backup.bidking.example.invalid/activate",
    ]


def test_server_base_override_cannot_replace_activation_authority(monkeypatch):
    monkeypatch.setenv("BIDKING_SERVER_BASE", "http://127.0.0.1:9999")
    assert ca._activate_urls("https://api.bidking.example.invalid/ingest") == [
        "https://api.bidking.example.invalid/activate",
        "https://backup.bidking.example.invalid/activate",
    ]


def test_activate_url_env_override_cannot_replace_authority(monkeypatch):
    monkeypatch.setenv("BIDKING_ACTIVATE_URL", "https://activate.example/activate")
    assert ca._activate_urls("https://api.bidking.example.invalid/ingest") == [
        "https://api.bidking.example.invalid/activate",
        "https://backup.bidking.example.invalid/activate",
    ]


def test_activate_url_external_http_override_is_ignored(monkeypatch):
    monkeypatch.setenv("BIDKING_ACTIVATE_URL", "http://203.0.113.10:18900/activate")
    assert ca._activate_urls(None)[0] == "https://api.bidking.example.invalid/activate"


def test_pyd_verifier_uses_test_seam_instead_of_runtime_endpoint_override():
    verifier = (ROOT / "scripts" / "verify_pyd_compile.py").read_text(
        encoding="utf-8"
    )
    assert "os.environ['BIDKING_ACTIVATE_URL']" not in verifier
    assert "mod._activate_urls = lambda" in verifier


def test_upload_url_external_http_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        vc.validate_service_url(
            "http://203.0.113.10:18900/ingest",
            purpose="sample upload",
        )


# --------------------------------------------------------------------------- #
# ⭐铁律 1: 网络层失败 -> fallback 到下一个地址
# --------------------------------------------------------------------------- #
def test_activate_falls_back_to_second_https_when_primary_unreachable(monkeypatch, tmp_path):
    """主入口连不上 -> 自动退到独立 HTTPS Worker，且不出现明文链路。"""
    _isolate_app_dir(monkeypatch, tmp_path, "DEV-FB")
    tried: list[str] = []
    installed_from: list[str | None] = []

    def fake_post(url, device, token, timeout):
        tried.append(url)
        if "api.bidking.example.invalid" in url:
            return None
        return 200, _valid_trial_response(device)

    def fake_install(
        master_key,
        exp,
        device,
        ingest_url,
        activation_url=None,
        telemetry_url=None,
    ):
        del master_key, exp, device, ingest_url, telemetry_url
        installed_from.append(activation_url)

    monkeypatch.setattr(ca, "_post_activate", fake_post)
    monkeypatch.setattr(ca, "_install_provider", fake_install)
    ca.activate(
        ui_token="",
        timeout=1.0,
        force_server=True,
        ingest_url="https://api.bidking.example.invalid/ingest",
    )

    assert len(tried) == 2, f"域名失败后必须试兜底地址, 实际只试了 {tried}"
    assert tried[0] == "https://api.bidking.example.invalid/activate"
    assert tried[1] == "https://backup.bidking.example.invalid/activate"
    assert installed_from == [tried[1]]
    assert ca._telemetry_url_from_activation_url(installed_from[0]) == (
        "https://backup.bidking.example.invalid/telemetry"
    )


def test_all_addresses_down_reports_offline_not_crash(monkeypatch, tmp_path):
    _isolate_app_dir(monkeypatch, tmp_path, "DEV-DOWN")
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)   # 全挂

    res = ca.activate(ui_token="tok", timeout=1.0, force_server=True)
    assert res.activated is False
    assert res.reason == "offline"        # 优雅降级(有缓存则走缓存), 不是崩


# --------------------------------------------------------------------------- #
# ⭐铁律 2: HTTP 4xx/5xx 绝不 fallback(服务器活着, 换地址还是同样的错)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_http_error_does_not_retry_other_addresses(monkeypatch, tmp_path, status):
    """token 无效 -> 401。绝不能换地址重试: 换了还是 401, 白白拖慢启动 + 把失败请求打到所有服务器。"""
    _isolate_app_dir(monkeypatch, tmp_path, "DEV-401")
    tried: list[str] = []

    def fake_post(url, device, token, timeout):
        tried.append(url)
        return status, {"ok": False, "mode": "paid", "reason": "rejected"}

    monkeypatch.setattr(ca, "_post_activate", fake_post)
    ca.activate(
        ui_token="bad-token",
        timeout=1.0,
        force_server=True,
        ingest_url="https://api.bidking.example.invalid/ingest",
    )

    assert len(tried) == 1, f"HTTP {status} 不该 fallback, 却试了 {tried}"


def test_payload_invalid_http_200_does_not_retry_other_addresses(
    monkeypatch,
    tmp_path,
):
    _isolate_app_dir(monkeypatch, tmp_path, "DEV-INVALID-200")
    tried: list[str] = []

    def fake_post(url, device, token, timeout):
        tried.append(url)
        return 200, {"ok": "true"}

    monkeypatch.setattr(ca, "_post_activate", fake_post)
    result = ca.activate(ui_token="", timeout=1.0, force_server=True)

    assert result.activated is False
    assert result.transport_failure_classes == (
        "http_reachable_invalid_payload",
    )
    assert tried == ["https://api.bidking.example.invalid/activate"]


def test_domain_success_never_touches_fallback(monkeypatch, tmp_path):
    """正常情况(域名活着)零额外开销: 只连域名, 不碰兜底地址。"""
    _isolate_app_dir(monkeypatch, tmp_path, "DEV-OK")
    tried: list[str] = []

    def fake_post(url, device, token, timeout):
        tried.append(url)
        return 200, _valid_trial_response(device)

    monkeypatch.setattr(ca, "_post_activate", fake_post)
    ca.activate(ui_token="", timeout=1.0, force_server=True)

    assert len(tried) == 1
    assert tried[0].startswith("https://api.")


# --------------------------------------------------------------------------- #
# 版本检查也要 fallback(否则域名一挂, 更新提示就瞎了)
# --------------------------------------------------------------------------- #
def test_version_check_falls_back_and_reports_used_base(monkeypatch):
    tried: list[str] = []

    def fake_check(ingest_url, timeout):
        tried.append(ingest_url)
        if "api.bidking.example.invalid" in ingest_url:
            return {"ok": False}
        return {"ok": True, "latest": "0.2.8-hotfix1"}

    monkeypatch.setattr(vc, "check_against_server", fake_check)
    monkeypatch.setattr(
        vc,
        "resolve_ingest_url",
        lambda: "https://api.bidking.example.invalid/ingest",
    )
    st = vc.check_from_local_config(timeout=1.0)

    assert st["ok"] is True
    assert st["latest"] == "0.2.8-hotfix1"
    assert st["server_base"] == "https://backup.bidking.example.invalid"
    assert len(tried) == 2


def test_version_check_http_error_does_not_fallback(monkeypatch):
    tried: list[str] = []

    def fake_check(ingest_url, timeout):
        tried.append(ingest_url)
        return {"ok": False, "reachable": True, "http_status": 503}

    monkeypatch.setattr(vc, "check_against_server", fake_check)
    monkeypatch.setattr(
        vc,
        "resolve_ingest_url",
        lambda: "https://api.bidking.example.invalid/ingest",
    )
    st = vc.check_from_local_config(timeout=1.0)

    assert st["ok"] is False
    assert st["http_status"] == 503
    assert st["server_base"] == "https://api.bidking.example.invalid"
    assert len(tried) == 1


def test_version_check_stops_at_first_working_base(monkeypatch):
    tried: list[str] = []

    def fake_check(ingest_url, timeout):
        tried.append(ingest_url)
        return {"ok": True, "latest": "0.2.7"}

    monkeypatch.setattr(vc, "check_against_server", fake_check)
    st = vc.check_from_local_config(timeout=1.0)

    assert st["ok"] is True
    assert len(tried) == 1, "首选地址成功就不该再试兜底"
