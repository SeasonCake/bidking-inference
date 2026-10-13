# -*- coding: utf-8 -*-
"""客户端激活流程(secure/client_activate)单测: trial/paid/缓存诊断/强制在线授权/撤销清缓存/
设备绑定+篡改拒绝/provider 装载/(有内核时)Python 兜底与 Rust 对齐。

不触真网络: monkeypatch _post_activate 模拟服务端。app 目录指向 tmp(LOCALAPPDATA), 不污染本机。
"""
from __future__ import annotations

import base64
import json

import pytest

from bidking_lab.secure import activation, client_activate as ca

_MK = bytes(range(32))                       # 假出厂主密钥(32B)
_MK_B64 = base64.b64encode(_MK).decode()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """app 目录 -> tmp; 清激活相关 env; 测试后恢复 dev provider。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for k in ("BIDKING_ACTIVATION_TOKEN", "BIDKING_ACTIVATION_FILE",
              "BIDKING_ACTIVATE_URL", "BIDKING_INTEGRITY_DIGEST"):
        monkeypatch.delenv(k, raising=False)
    # 设备指纹固定, 避免读真注册表的不确定性
    monkeypatch.setattr(ca, "device_fingerprint", lambda: "DEVICE_AAA")
    # 包目录候选指向空 tmp(防仓库根偶有 activation.txt 干扰)
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_pkg")
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    yield
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    activation.set_provider(activation._DevStubProvider())


def _fake_server(responses):
    """生成 _post_activate 替身: 按调用次序返回 responses 里的 (code, body); 用尽则报错。"""
    calls = {"n": 0}

    def fake(url, device, token, timeout):
        i = calls["n"]
        calls["n"] += 1
        assert i < len(responses), f"_post_activate 被多调用 (第{i+1}次)"
        return responses[i]

    fake.calls = calls
    return fake


_INGEST = "https://srv.example/ingest"


@pytest.mark.parametrize(
    "token",
    (
        "v1|buyer-1|1785500000." + "a" * 64,
        "v1|订单.2026-08|1785500000|1788092000." + "b" * 64,
        "v1|buyer-2|1785500000|1788092000|r." + "c" * 64,
    ),
)
def test_single_ui_activation_token_accepts_current_issuer_shapes(token: str) -> None:
    assert ca.is_single_activation_token_input(token) is True


@pytest.mark.parametrize(
    "token",
    (
        "",
        "   ",
        " v1|buyer|1785500000." + "a" * 64,
        "v1|buyer|1785500000." + "a" * 64 + "\n",
        "群友 08-01: v1|buyer|1785500000." + "a" * 64,
        "v1|buyer|1785500000." + "a" * 64 + " 请激活",
        "v1|buyer name|1785500000." + "a" * 64,
        "v1|buyer|time." + "a" * 64,
        "v1|buyer|1785500000.signature",
        "v1|buyer|1785500000." + "A" * 64,
        "v1|buyer|1785500000|1788092000|x." + "a" * 64,
        "v1|buyer|1785500000|1788092000|r|extra." + "a" * 64,
    ),
)
def test_single_ui_activation_token_rejects_mixed_or_partial_input(token: str) -> None:
    assert ca.is_single_activation_token_input(token) is False


def test_single_ui_activation_token_rejects_str_subclass_without_protocol_calls() -> None:
    class ExplodingToken(str):
        def __str__(self):
            raise AssertionError("token __str__ must not run")

        def strip(self, *_args, **_kwargs):
            raise AssertionError("token strip must not run")

        def __len__(self):
            raise AssertionError("token len must not run")

    token = ExplodingToken("v1|buyer|1785500000." + "a" * 64)
    assert ca.is_single_activation_token_input(token) is False


def _ok(mode, exp, *, trial_exp=None):
    lease = {"uid": ("trial:DEVICE_AAA" if mode == "trial" else "u1"),
             "device": "DEVICE_AAA", "exp": exp}
    body = {"ok": True, "mode": mode, "lease": json.dumps(lease) + ".sig",
            "master_key": _MK_B64}
    if trial_exp is not None:
        body["trial_exp"] = trial_exp
    return (200, body)


# --------------------------------------------------------------------------- #
def test_missing_cache_is_a_valid_first_activation_state():
    """首次激活没有缓存是正常状态；Cython 成品也必须接受 None。"""
    assert ca._result_from_cache(
        None,
        device="DEVICE_AAA",
        ingest_url=_INGEST,
        now=1000,
    ) is None


def test_local_activation_diagnostic_distinguishes_cache_states(tmp_path):
    now = 2_000
    missing = ca.inspect_local_activation_state(now=now)
    assert missing["cache_state"] == "missing"
    assert missing["credential_available"] is False
    assert missing["cache_file_present"] is False

    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    (app / "activation.txt").write_text("right", encoding="utf-8")
    (app / "activation_cache.bin").write_bytes(b"not-a-cache")
    unreadable = ca.inspect_local_activation_state(now=now)
    assert unreadable["cache_state"] == "unreadable_for_current_identity"
    assert unreadable["credential_available"] is True
    assert unreadable["cache_bytes"] > 0

    ca._save_cache(
        lease={"uid": "u1", "device": "DEVICE_AAA", "exp": now - 1},
        master_key=_MK,
        device="DEVICE_AAA",
        token="right",
        mode="paid",
        now=1_000,
    )
    expired = ca.inspect_local_activation_state(now=now)
    assert expired["cache_state"] == "expired"

    ca._save_cache(
        lease={"device": "DEVICE_AAA", "exp": now + 600},
        master_key=_MK,
        device="DEVICE_AAA",
        token="right",
        mode="paid",
        now=1_000,
    )
    malformed = ca.inspect_local_activation_state(now=now)
    assert malformed["cache_state"] == "malformed"

    ca._cache_invalid_file().write_text("invalid\n", encoding="utf-8")
    invalidated = ca.inspect_local_activation_state(now=now)
    assert invalidated["cache_state"] == "invalidated"

    ca._save_cache(
        lease={"uid": "u1", "device": "DEVICE_AAA", "exp": now + 600},
        master_key=_MK,
        device="DEVICE_AAA",
        token="right",
        mode="paid",
        now=1_000,
    )
    valid = ca.inspect_local_activation_state(now=now)
    assert valid["cache_state"] == "readable_current_identity"
    assert valid["lease_seconds_left"] == 600
    assert valid["authorization_reusable"] is False
    assert valid["online_refresh_required"] is True


@pytest.mark.parametrize(
    "cached",
    [
        [],
        {"lease": None},
        {"lease": {"device": "DEVICE_AAA", "exp": None}, "mk": _MK_B64},
        {"lease": {"device": "DEVICE_AAA", "exp": []}, "mk": _MK_B64},
        {"lease": {"device": "DEVICE_AAA", "exp": 9_000_000_000}, "mk": None},
    ],
)
def test_malformed_cache_is_ignored_without_typeerror(cached):
    assert ca._result_from_cache(
        cached,
        device="DEVICE_AAA",
        ingest_url=_INGEST,
        now=1000,
    ) is None


def test_decryptable_cache_with_null_exp_recontacts_server(monkeypatch):
    """特定设备上的旧/损坏缓存不能抛 TypeError；应忽略缓存并重新联网。"""
    ca._save_cache(
        lease={"uid": "u1", "device": "DEVICE_AAA", "exp": None},
        master_key=_MK,
        device="DEVICE_AAA",
        token="",
        mode="trial",
        now=1000,
    )
    fake = _fake_server([_ok("trial", 10_000_000_000, trial_exp=10_000_000_000)])
    monkeypatch.setattr(ca, "_post_activate", fake)
    result = ca.activate(ingest_url=_INGEST, now=2000)
    assert fake.calls["n"] == 1
    assert result.activated and result.source == "server"


def test_malformed_success_response_degrades_without_typeerror(monkeypatch):
    """服务端若意外返回 ok=true 但 lease 时间损坏，客户端不能把它写缓存或报 token 错。"""
    body = {
        "ok": True,
        "mode": "paid",
        "lease": json.dumps({"uid": "u1", "device": "DEVICE_AAA", "exp": None}) + ".sig",
        "master_key": _MK_B64,
    }
    monkeypatch.setattr(ca, "_post_activate", _fake_server([(200, body)]))
    result = ca.activate(ui_token="v1|u1|123.sig", ingest_url=_INGEST, now=2000, force_server=True)
    assert not result.activated and result.reason == "offline"
    assert not ca._cache_file().exists()


def test_trial_grant_no_token(monkeypatch):
    """无 token -> 试用: activated, mode=trial, trial_exp, provider 装好(data_key==mk)。"""
    exp = 10_000_000_000
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("trial", exp, trial_exp=exp)]))
    r = ca.activate(ingest_url=_INGEST, now=1000)
    assert r.activated and r.mode == "trial" and r.source == "server"
    assert r.trial_exp == exp
    assert activation.is_activated() is True
    assert activation.data_key() == _MK


def test_paid_with_token(tmp_path, monkeypatch):
    """有 token(激活文件)-> 付费永久。"""
    (tmp_path / "bidking").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bidking" / "activation.txt").write_text("v1|u1|123.deadbeef", encoding="utf-8")
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("paid", 9_000_000_000)]))
    r = ca.activate(ingest_url=_INGEST, now=1000)
    assert r.activated and r.mode == "paid"
    assert activation.data_key() == _MK


def test_ui_token_replaces_persisted_and_clears_cache(tmp_path, monkeypatch):
    """UI 粘贴新 token 时: 不用磁盘旧 token 激活; 清旧 lease 缓存; 持久化新 token。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_tok = "v1|old-30d|100|9999999999.oldsig"
    new_tok = "v1|new-perm|200.newsig"
    (app / "activation.txt").write_text(old_tok, encoding="utf-8")
    ca._save_cache(
        lease={"uid": "old", "device": "DEVICE_AAA", "exp": 9_000_000_000, "paid_exp": 9999999999},
        master_key=_MK,
        device="DEVICE_AAA",
        token=old_tok,
        mode="paid",
        now=1000,
    )
    seen: list[str] = []

    def fake(url, device, token, timeout):
        seen.append(token)
        lease = {"uid": "new-perm", "device": "DEVICE_AAA", "exp": 9_000_000_000}
        return _ok("paid", lease["exp"])

    monkeypatch.setattr(ca, "_post_activate", fake)
    r = ca.activate(ui_token=new_tok, ingest_url=_INGEST, now=1000, force_server=True)
    assert seen == [new_tok]
    assert r.activated and r.mode == "paid" and r.paid_exp is None
    assert ca._read_persisted_token() == new_tok
    assert ca._load_cache(device="DEVICE_AAA", token=old_tok) is None
    assert ca._load_cache(device="DEVICE_AAA", token=new_tok) is not None


def test_ui_token_persist_failure_preserves_old_activation(tmp_path, monkeypatch):
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_tok = "v1|old-good|100|9999999999.oldsig"
    new_tok = "v1|new-good|200.newsig"
    (app / "activation.txt").write_text(old_tok, encoding="utf-8")
    assert ca._save_cache(
        lease={"uid": "old", "device": "DEVICE_AAA", "exp": 9_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=old_tok,
        mode="paid",
        now=1000,
    )
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: _ok("paid", 9_000_000_000))
    monkeypatch.setattr(ca, "save_token", lambda token: False)

    result = ca.activate(ui_token=new_tok, ingest_url=_INGEST, now=2000, force_server=True)

    assert not result.activated and result.reason == "token_persist_failed"
    assert ca._read_persisted_token() == old_tok
    assert ca._load_cache(device="DEVICE_AAA", token=old_tok) is not None
    assert ca._load_cache(device="DEVICE_AAA", token=new_tok) is None


def test_ui_token_cache_failure_rolls_back_token_and_cache_together(tmp_path, monkeypatch):
    """The cache can fail after the new token is already durable; both old files must recover."""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_tok = "v1|old-transaction|100|9999999999.oldsig"
    new_tok = "v1|new-transaction|200.newsig"
    (app / "activation.txt").write_text(old_tok, encoding="utf-8")
    assert ca._save_cache(
        lease={"uid": "old", "device": "DEVICE_AAA", "exp": 9_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=old_tok,
        mode="paid",
        now=1000,
    )
    old_cache_bytes = ca._cache_file().read_bytes()
    real_save_cache = ca._save_cache

    def save_new_cache_then_report_failure(**kwargs):
        assert real_save_cache(**kwargs)
        assert ca._read_persisted_token() == new_tok
        assert ca._cache_file().read_bytes() != old_cache_bytes
        return False

    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: _ok("paid", 9_000_000_000))
    monkeypatch.setattr(ca, "_save_cache", save_new_cache_then_report_failure)

    result = ca.activate(ui_token=new_tok, ingest_url=_INGEST, now=2000, force_server=True)

    assert not result.activated and result.reason == "token_persist_failed"
    assert ca._read_persisted_token() == old_tok
    assert ca._cache_file().read_bytes() == old_cache_bytes
    assert ca._load_cache(device="DEVICE_AAA", token=old_tok) is not None
    assert ca._load_cache(device="DEVICE_AAA", token=new_tok) is None


@pytest.mark.parametrize("status", [401, 500])
def test_http_error_with_ok_true_never_activates_or_persists(status, monkeypatch):
    body = _ok("paid", 9_000_000_000)[1]
    monkeypatch.setattr(ca, "_post_activate", _fake_server([(status, body)]))

    result = ca.activate(
        ui_token="v1|must-not-persist|300.sig",
        ingest_url=_INGEST,
        now=2000,
        force_server=True,
    )

    assert not result.activated and result.reason == "offline"
    assert not ca._token_file().exists()
    assert not ca._cache_file().exists()


def test_bad_ui_token_denied_preserves_old_activation(tmp_path, monkeypatch):
    """#1: 粘【错】新 token 被服务端权威拒绝时, 不毁旧的有效激活(旧 activation.txt + 旧缓存都在)。
    旧实现在 resolve_token 里先删旧文件+缓存【再】校验 -> 粘错就把用户锁死。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_tok = "v1|good-30d|100|9999999999.oldsig"
    bad_tok = "v1|typo|200.badsig"
    (app / "activation.txt").write_text(old_tok, encoding="utf-8")
    ca._save_cache(lease={"uid": "good", "device": "DEVICE_AAA", "exp": 9_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token=old_tok, mode="paid", now=1000)
    monkeypatch.setattr(ca, "_post_activate",
                        lambda *a, **k: (401, {"ok": False, "mode": "paid", "reason": "bad_token"}))
    r = ca.activate(ui_token=bad_tok, ingest_url=_INGEST, now=2000, force_server=True)
    assert not r.activated and r.reason == "bad_token"
    # 旧持久态完好: 旧 token 文件仍在 + 旧缓存仍能解 -> 下次冷启动/联网旧 token 照常激活。
    assert ca._read_persisted_token() == old_tok
    assert ca._load_cache(device="DEVICE_AAA", token=old_tok) is not None


def test_new_ui_token_offline_preserves_old_activation(tmp_path, monkeypatch):
    """粘新 token 但断网时，不毁旧 token 和诊断缓存；是否保留进程授权由 enforce 决定。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_tok = "v1|good-30d|100|9999999999.oldsig"
    new_tok = "v1|new-perm|200.newsig"
    (app / "activation.txt").write_text(old_tok, encoding="utf-8")
    ca._save_cache(lease={"uid": "good", "device": "DEVICE_AAA", "exp": 9_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token=old_tok, mode="paid", now=1000)
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)  # 断网
    r = ca.activate(ui_token=new_tok, ingest_url=_INGEST, now=2000, force_server=True)
    assert not r.activated  # 新 token 未经校验, 本次不激活
    # 旧持久态完好，网络恢复后仍可用旧 token 重新在线确认；缓存本身不授权。
    assert ca._read_persisted_token() == old_tok
    assert ca._load_cache(device="DEVICE_AAA", token=old_tok) is not None


def test_ui_token_same_as_persisted_recontacts_server_and_keeps_cache(tmp_path, monkeypatch):
    """同一 token 也必须在线验证；网络失败不把诊断缓存误删。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    tok = "v1|same|123.deadbeef"
    (app / "activation.txt").write_text(tok, encoding="utf-8")
    ca._save_cache(
        lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 9_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=tok,
        mode="paid",
        now=1000,
    )
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)
    r = ca.activate(ui_token=tok, ingest_url=_INGEST, now=2000)
    assert not r.activated and r.source == "server" and r.reason == "offline"
    assert (app / "activation.txt").read_text(encoding="utf-8").strip() == tok
    assert ca._load_cache(device="DEVICE_AAA", token=tok) is not None


def test_cold_start_reads_persisted_file_without_ui_token(tmp_path, monkeypatch):
    """无 UI 输入时仍只读 activation.txt(现有群友冷启动路径不变)。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    tok = "v1|cold|456.cafe"
    (app / "activation.txt").write_text(tok, encoding="utf-8")
    seen: list[str] = []

    def fake(url, device, token, timeout):
        seen.append(token)
        return _ok("paid", 9_000_000_000)

    monkeypatch.setattr(ca, "_post_activate", fake)
    r = ca.activate(ingest_url=_INGEST, now=1000)
    assert seen == [tok]
    assert r.activated and r.mode == "paid"


def test_readable_cache_does_not_skip_server_or_authorize_offline(monkeypatch):
    """第一次联网写缓存；第二次仍须回连，断网时缓存不得授权。"""
    exp = 10_000_000_000
    fake = _fake_server([_ok("trial", exp, trial_exp=exp)])
    monkeypatch.setattr(ca, "_post_activate", fake)
    ca.activate(ingest_url=_INGEST, now=1000)
    assert fake.calls["n"] == 1
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)
    r2 = ca.activate(ingest_url=_INGEST, now=2000)
    assert not r2.activated and r2.source == "server" and r2.reason == "offline"
    assert ca._load_cache(device="DEVICE_AAA", token="") is not None


def test_expired_cache_recontacts(monkeypatch):
    """缓存 lease 过期 -> 跳过缓存, 回连服务端。"""
    # 先写一份过期缓存(exp < now)
    ca._save_cache(lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 500},
                   master_key=_MK, device="DEVICE_AAA", token="", mode="trial", now=400)
    fake = _fake_server([_ok("trial", 10_000_000_000, trial_exp=10_000_000_000)])
    monkeypatch.setattr(ca, "_post_activate", fake)
    r = ca.activate(ingest_url=_INGEST, now=1000)   # now=1000 > exp=500
    assert fake.calls["n"] == 1 and r.source == "server" and r.activated


def test_revoked_clears_cache(monkeypatch):
    """撤销(403 revoked)-> 未激活 + 清缓存(防离线残留)。"""
    # 预置一份有效缓存
    ca._save_cache(lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 10_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token="tok", mode="paid", now=1000)
    (ca._app_dir() / "activation.txt").write_text("tok", encoding="utf-8")  # 让 resolve_token=tok
    # 缓存对应 token="tok"; 但撤销路径需先让缓存"无效"才会回连。这里 lease 仍有效会走缓存,
    # 故本用例直接验证回连分支: 删缓存再测 403。
    ca._clear_cache()
    monkeypatch.setattr(ca, "_post_activate",
                        _fake_server([(403, {"ok": False, "mode": "paid", "reason": "revoked"})]))
    r = ca.activate(ingest_url=_INGEST, now=2000)
    assert not r.activated and r.reason == "revoked"
    assert not ca._cache_file().exists()           # 缓存被清


def test_transient_server_error_keeps_diagnostic_cache_but_fails_closed(tmp_path, monkeypatch):
    """瞬时错误不清诊断缓存，但不可把本机可重封装缓存升级为授权。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    tok = "v1|u1|123.deadbeef"
    (app / "activation.txt").write_text(tok, encoding="utf-8")
    ca._save_cache(lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 10_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token=tok, mode="paid", now=1000)
    original = ca._cache_file().read_bytes()
    for code, body, expected in [
        (503, {"ok": False, "reason": "state_error"}, "state_error"),
        (429, {"ok": False, "reason": "rate_limited"}, "rate_limited"),
        (500, {"ok": False}, "server_error"),
    ]:
        monkeypatch.setattr(ca, "_post_activate", lambda *a, _r=(code, body), **k: _r)
        r = ca.activate(ui_token=None, ingest_url=_INGEST, now=2000, force_server=True)
        assert not r.activated and r.source == "server" and r.reason == expected
        assert ca._cache_file().read_bytes() == original


@pytest.mark.parametrize("deny_reason", ["device_mismatch", "device_revoked"])
def test_authoritative_deny_still_clears_cache(tmp_path, monkeypatch, deny_reason):
    """权威拒绝(403 device_mismatch / 401 bad_token 等)仍清缓存+硬降级(不被 #1 修复放过)。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    tok = "v1|u1|123.deadbeef"
    (app / "activation.txt").write_text(tok, encoding="utf-8")
    ca._save_cache(lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 10_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token=tok, mode="paid", now=1000)
    monkeypatch.setattr(ca, "_post_activate",
                        lambda *a, **k: (
                            403,
                            {"ok": False, "mode": "paid", "reason": deny_reason},
                        ))
    r = ca.activate(ui_token=None, ingest_url=_INGEST, now=2000, force_server=True)
    assert not r.activated and r.reason == deny_reason
    assert not ca._cache_file().exists()           # 权威拒绝: 清缓存


@pytest.mark.parametrize("force_server", [False, True])
@pytest.mark.parametrize("use_new_ui_token", [False, True])
def test_device_revoked_always_clears_old_cache_for_same_or_new_token(
    tmp_path,
    monkeypatch,
    force_server,
    use_new_ui_token,
):
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_token = "v1|old-authorized|123.deadbeef"
    new_token = "v1|new-pasted|456.deadbeef"
    (app / "activation.txt").write_text(old_token, encoding="utf-8")
    assert ca._save_cache(
        lease={"uid": "old", "device": "DEVICE_AAA", "exp": 10_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=old_token,
        mode="paid",
        now=1000,
    )
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda *a, **k: (
            403,
            {"ok": False, "mode": "paid", "reason": "device_revoked"},
        ),
    )

    result = ca.activate(
        ui_token=(new_token if use_new_ui_token else old_token),
        ingest_url=_INGEST,
        now=2000,
        force_server=force_server,
    )

    assert not result.activated and result.reason == "device_revoked"
    assert not ca._cache_file().exists()
    assert ca._load_cache(device="DEVICE_AAA", token=old_token) is None
    # A rejected pasted token is never persisted; the old token file remains an
    # operator-visible credential, but it no longer carries any cached grant.
    assert ca._read_persisted_token() == old_token


def test_upgrade_required_is_authoritative_and_clears_cache_even_for_new_paste(tmp_path, monkeypatch):
    """安全代际 426 与 token 对错无关；即使正在粘新 token 也不能回落旧离线 lease。"""
    app = tmp_path / "bidking"
    app.mkdir(parents=True, exist_ok=True)
    old_token = "v1|old|123.deadbeef"
    (app / "activation.txt").write_text(old_token, encoding="utf-8")
    ca._save_cache(
        lease={"uid": "old", "device": "DEVICE_AAA", "exp": 10_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=old_token,
        mode="paid",
        now=1000,
    )
    monkeypatch.setattr(
        ca,
        "_post_activate",
        lambda *a, **k: (
            426,
            {
                "ok": False,
                "mode": "paid",
                "reason": "upgrade_required",
                "min_client_security_epoch": 1,
            },
        ),
    )

    r = ca.activate(
        ui_token="v1|new|456.deadbeef",
        ingest_url=_INGEST,
        now=2000,
        force_server=True,
    )
    assert not r.activated and r.reason == "upgrade_required"
    assert "当前版本已停用" in r.status_line()
    assert not ca._cache_file().exists()


def test_tampered_cache_rejected(monkeypatch):
    """手改缓存文件 -> GCM tag 失败 -> 当无缓存 -> 回连。"""
    ca._save_cache(lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 10_000_000_000},
                   master_key=_MK, device="DEVICE_AAA", token="", mode="trial", now=1000)
    p = ca._cache_file()
    raw = bytearray(p.read_bytes())
    raw[-1] ^= 0xFF                                  # 翻转最后一字节(破坏认证 tag)
    p.write_bytes(bytes(raw))
    assert ca._load_cache(device="DEVICE_AAA", token="") is None
    fake = _fake_server([_ok("trial", 10_000_000_000, trial_exp=10_000_000_000)])
    monkeypatch.setattr(ca, "_post_activate", fake)
    r = ca.activate(ingest_url=_INGEST, now=2000)
    assert fake.calls["n"] == 1 and r.activated      # 篡改后强制回连


def test_wrong_device_cache_ignored():
    """缓存设备指纹 != 本机 -> 不复用(拷别机解不开/或设备字段不符)。"""
    ca._save_cache(lease={"uid": "u1", "device": "OTHER_DEV", "exp": 10_000_000_000},
                   master_key=_MK, device="OTHER_DEV", token="", mode="paid", now=1000)
    # 本机 device=DEVICE_AAA, wrap key 不同 -> 解密直接失败
    assert ca._load_cache(device="DEVICE_AAA", token="") is None


def test_no_server_degrades(monkeypatch):
    """完全无激活地址可用(候选列表为空)且无缓存 -> 降级(no_server)。
    注: 0.2.2 起客户端【烤了默认服务器地址】, 0.2.7 起更是【多地址 fallback 列表】(域名+IP兜底),
    故仅靠 _resolve_ingest_url=None 不再触发 no_server(会回落烤死地址)。这里直接令候选列表为空,
    测真正"无地址可用"分支。"""
    monkeypatch.setattr(ca, "_resolve_ingest_url", lambda: None)
    monkeypatch.setattr(ca, "_activate_urls", lambda *a, **k: [])
    r = ca.activate(now=1000)
    assert not r.activated and r.reason == "no_server"


def test_normal_and_force_server_both_require_online_success(monkeypatch):
    """普通启动和周期心跳都不能从落盘缓存恢复授权。"""
    exp = 10_000_000_000
    # 1) 首次激活(server) -> 写缓存
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("trial", exp, trial_exp=exp)]))
    r1 = ca.activate(ingest_url=_INGEST, now=1000)
    assert r1.activated and r1.source == "server"
    # 2) 普通调用也必须重新得到在线成功
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("trial", exp, trial_exp=exp)]))
    r2 = ca.activate(ingest_url=_INGEST, now=1001)
    assert r2.activated and r2.source == "server"
    # 3) force_server: 跳过缓存, 真连服务端(心跳)
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("trial", exp, trial_exp=exp)]))
    r3 = ca.activate(ingest_url=_INGEST, now=1002, force_server=True)
    assert r3.activated and r3.source == "server"
    # 4) 服务端连不上：即使缓存可读也 fail-closed
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)
    r4 = ca.activate(ingest_url=_INGEST, now=1003, force_server=True)
    assert not r4.activated and r4.source == "server" and r4.reason == "offline"


def test_baked_server_base_for_group_friend(monkeypatch):
    """群友包(无私有 .local.ps1 / env)也能解析出激活地址 = 烤死默认服务器(/activate 不需上传 key)。
    保证: resolve_ingest_url=None(不上传)时, _activate_url(None) 仍回落默认地址。"""
    from bidking_lab.live import version_check as vc
    monkeypatch.setattr(vc, "resolve_ingest_url", lambda: None)         # 群友: 无上传配置
    monkeypatch.delenv("BIDKING_SERVER_BASE", raising=False)
    monkeypatch.delenv("BIDKING_ACTIVATE_URL", raising=False)
    base = vc.resolve_server_base()
    assert base and base.startswith("http")                            # 总返回可用地址
    url = ca._activate_url(None)                                        # 无 ingest_url 也回落默认
    assert url and url.endswith("/activate")
    assert vc.resolve_ingest_url() is None                             # 上传仍关闭(无 key)


def test_offline_no_cache_degrades(monkeypatch):
    """连不上(_post_activate=None)且无缓存 -> 降级 offline。"""
    monkeypatch.setattr(ca, "_post_activate", lambda *a, **k: None)
    r = ca.activate(ingest_url=_INGEST, now=1000)
    assert not r.activated and r.reason == "offline"


@pytest.mark.parametrize(
    ("code", "body", "expected_reason", "visible_text"),
    [
        (503, {"ok": False, "mode": "paid", "reason": "state_error"},
         "state_error", "服务器激活服务暂时异常"),
        (429, {"ok": False, "reason": "rate_limited"},
         "rate_limited", "请求过于频繁"),
        (500, {"ok": False, "reason": "internal_error"},
         "server_error", "服务器暂时异常"),
        (401, {"ok": False, "reason": "unknown_rejection"},
         "server_rejected", "服务器拒绝了本次请求"),
        (426, {"ok": False, "reason": "unknown_upgrade"},
         "upgrade_required", "当前版本已停用"),
    ],
)
def test_http_failure_categories_remain_visible_without_cache(
    monkeypatch, code, body, expected_reason, visible_text
):
    monkeypatch.setattr(ca, "_post_activate", _fake_server([(code, body)]))

    result = ca.activate(
        ui_token="v1|paid|123.signature",
        ingest_url=_INGEST,
        now=1000,
        force_server=True,
    )

    assert not result.activated and result.reason == expected_reason
    assert visible_text in result.status_line(now=1000)


def test_state_error_preserves_cache_bytes_but_does_not_authorize(monkeypatch):
    token = "v1|paid|123.signature"
    ca.save_token(token)
    assert ca._save_cache(
        lease={"uid": "u1", "device": "DEVICE_AAA", "exp": 9_000_000_000},
        master_key=_MK,
        device="DEVICE_AAA",
        token=token,
        mode="paid",
        now=1000,
    )
    original = ca._cache_file().read_bytes()
    monkeypatch.setattr(
        ca,
        "_post_activate",
        _fake_server([(503, {"ok": False, "mode": "paid", "reason": "state_error"})]),
    )

    result = ca.activate(ingest_url=_INGEST, now=1001, force_server=True)

    assert not result.activated and result.source == "server"
    assert result.reason == "state_error"
    assert ca._cache_file().read_bytes() == original


def test_trial_status_line(monkeypatch):
    """试用状态行含剩余时间 + 联系方式。"""
    exp = 1000 + 2 * 3600
    monkeypatch.setattr(ca, "_post_activate", _fake_server([_ok("trial", exp, trial_exp=exp)]))
    r = ca.activate(ingest_url=_INGEST, now=1000)
    line = r.status_line(now=1000)
    assert "试用中" in line and "2 小时" in line and "synthetic-user-001" in line


def test_paid_status_line_uses_subscription_neutral_copy():
    r = ca.ActivationResult(activated=True, mode="paid", paid_exp=None)
    assert r.status_line(now=1000) == "激活: 已激活"
    assert "永久" not in r.status_line(now=1000)


def test_kernel_parity_if_present():
    """有 .pyd 时: Python 兜底设备指纹/派生 key 与 Rust 内核一致(同算法)。无内核则跳过。"""
    from bidking_lab.secure.rust_provider import load_kernel
    k = load_kernel()
    if k is None:
        pytest.skip("无 bidking_kernel.pyd(dev 源码)")
    import hashlib

    def _read_guid():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as kk:
                return winreg.QueryValueEx(kk, "MachineGuid")[0]
        except Exception:  # noqa: BLE001
            return "unknown-device"
    raw = _read_guid()
    py_fp = hashlib.sha256(b"bidking-dev-v1:" + raw.encode()).hexdigest()[:32]
    assert k.device_fingerprint() == py_fp
    # 派生 key 对齐(同 token/device)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ikm = f"tok|{py_fp}".encode()
    py_key = HKDF(algorithm=hashes.SHA256(), length=32,
                  salt=b"bidking-data-key-v1", info=b"aes-256-data").derive(ikm)
    assert bytes(k.derive_data_key("tok", py_fp)) == py_key


def test_clear_activation_wipes_token_cache_and_env(monkeypatch):
    """换绑/清除: 删 activation.txt + 加密缓存, 并从【本进程】env 移除 BIDKING_ACTIVATION_TOKEN
    —— 它优先级最高会盖过新贴 token('填了错 token 到 env、没清除、一直绑定失败'的根因)。
    清后 resolve_token 回空 = 干净态(可粘贴新 token / 走试用)。"""
    import os
    monkeypatch.setenv("BIDKING_ACTIVATION_TOKEN", "v1|baduid|1|9.deadbeef")
    ca.save_token("v1|olduid|1|9.oldsig")            # 写 activation.txt(app 目录)
    ca._cache_file().write_bytes(b"\x00fake-cache")   # 假加密缓存
    assert ca.resolve_token() == "v1|baduid|1|9.deadbeef"  # env 抢先(bug 场景)

    old_runtime_denied = ca._RUNTIME_ACTIVATION_DENIED.is_set()
    try:
        info = ca.clear_activation()

        assert info == {
            "cleared": True,
            "token_cleared": True,
            "cache_cleared": True,
            "cache_invalidated": False,
            "env_was_set": True,
        }
        assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
        assert "BIDKING_ACTIVATION_TOKEN" not in os.environ
        assert not ca._cache_file().exists()
        assert ca.resolve_token() == ""                    # 干净态
    finally:
        if old_runtime_denied:
            ca._RUNTIME_ACTIVATION_DENIED.set()
        else:
            ca._RUNTIME_ACTIVATION_DENIED.clear()


def test_clear_activation_no_env_reports_false(monkeypatch):
    """没有 env token 时清除: env_was_set=False, 仅删文件/缓存。"""
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    ca.save_token("v1|uid|1|9.sig")
    old_runtime_denied = ca._RUNTIME_ACTIVATION_DENIED.is_set()
    try:
        info = ca.clear_activation()
        assert info == {
            "cleared": True,
            "token_cleared": True,
            "cache_cleared": True,
            "cache_invalidated": False,
            "env_was_set": False,
        }
        assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
        assert ca.resolve_token() == ""
    finally:
        if old_runtime_denied:
            ca._RUNTIME_ACTIVATION_DENIED.set()
        else:
            ca._RUNTIME_ACTIVATION_DENIED.clear()
