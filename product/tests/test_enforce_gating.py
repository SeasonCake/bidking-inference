# -*- coding: utf-8 -*-
"""secure/enforce 强制层单测: 时间感知 should_run_full(试用到期即降级) + activation_state(UI 状态)。
0.2.2 付费版的"真降级"判据。"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bidking_lab.secure.client_activate import ActivationResult  # noqa: E402
from bidking_lab.secure import enforce as E  # noqa: E402

NOW = 1_000_000


def test_paid_runs_full():
    r = ActivationResult(activated=True, mode="paid", reason="ok")
    assert E.should_run_full(r, now=NOW) is True
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and not st["needs_token"] and not st["warn"]


def test_trial_with_time_runs_full_no_warn():
    r = ActivationResult(activated=True, mode="trial", reason="ok", trial_exp=NOW + 5 * 3600)
    assert E.should_run_full(r, now=NOW) is True
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and not st["warn"] and st["seconds_left"] == 5 * 3600


def test_trial_under_15min_warns_but_still_runs():
    # WARN_SECONDS 为 15min，避免短试用全程红；剩 10min<15min → 变红。
    r = ActivationResult(activated=True, mode="trial", reason="ok", trial_exp=NOW + 10 * 60)
    assert E.should_run_full(r, now=NOW) is True       # 仍计算
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and st["warn"]               # 但 UI 变红(剩 <15min)


def test_trial_over_15min_runs_no_warn():
    # 剩 30min>15min → 正常显示倒计时不变红(前 45 分体验)。
    r = ActivationResult(activated=True, mode="trial", reason="ok", trial_exp=NOW + 30 * 60)
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and not st["warn"]


def test_trial_expired_degrades_even_if_activated_flag_true():
    # 启动时 activated=True 的试用, 到期(trial_exp 已过)也必须降级(时间感知)。
    r = ActivationResult(activated=True, mode="trial", reason="ok", trial_exp=NOW - 10)
    assert E.should_run_full(r, now=NOW) is False
    st = E.activation_state(r, now=NOW)
    assert not st["run_full"] and st["needs_token"] and st["warn"]


def test_permanent_paid_no_countdown():
    r = ActivationResult(activated=True, mode="paid", reason="ok")  # paid_exp=None
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and not st["warn"] and st["text"] == "已激活"


def test_membership_shows_days_and_warns_under_1day():
    r = ActivationResult(activated=True, mode="paid", reason="ok", paid_exp=NOW + 15 * 86400)
    st = E.activation_state(r, now=NOW)
    assert st["run_full"] and not st["warn"] and "会员" in st["text"] and "15 天" in st["text"]
    r2 = ActivationResult(activated=True, mode="paid", reason="ok", paid_exp=NOW + 10 * 3600)
    st2 = E.activation_state(r2, now=NOW)
    assert st2["run_full"] and st2["warn"]                       # 剩 <1 天 -> 红


def test_activation_state_exposes_only_validated_entitlement_pair():
    annual = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        paid_exp=NOW + 365 * 86400,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "full"},
    )
    annual_state = E.activation_state(annual, now=NOW)
    assert annual_state["plan_kind"] == "365d"
    assert annual_state["feature_entitlements"] == {"pet_companion": "full"}

    inconsistent = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        paid_exp=NOW + 365 * 86400,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "none"},
    )
    inconsistent_state = E.activation_state(inconsistent, now=NOW)
    assert "plan_kind" not in inconsistent_state
    assert "feature_entitlements" not in inconsistent_state

    denied = ActivationResult(
        activated=False,
        mode="paid",
        reason="revoked",
        paid_exp=NOW + 365 * 86400,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "full"},
    )
    denied_state = E.activation_state(denied, now=NOW)
    assert "plan_kind" not in denied_state
    assert "feature_entitlements" not in denied_state


def test_membership_expired_degrades():
    # 服务端 expired
    r = ActivationResult(activated=False, mode="paid", reason="expired")
    st = E.activation_state(r, now=NOW)
    assert not st["run_full"] and st["needs_token"] and "会员" in st["text"]
    # 本地 paid_exp 已过(reason 还 ok)也应降级
    r2 = ActivationResult(activated=True, mode="paid", reason="ok", paid_exp=NOW - 10)
    assert E.should_run_full(r2, now=NOW) is False


def test_none_and_offline_degrade():
    assert E.should_run_full(None, now=NOW) is False
    r = ActivationResult(activated=False, mode="none", reason="no_server")
    st = E.activation_state(r, now=NOW)
    assert not st["run_full"] and not st["needs_token"]
    assert "无需重复更换 token" in st["text"]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("offline", "激活网络诊断"),
        ("no_server", "无需重复更换 token"),
        ("bad_token", "token 无效"),
        ("token_persist_failed", "程序目录或当前身份的写入权限"),
        ("revoked", "当前凭证已停用"),
        ("device_revoked", "当前设备已停用"),
        ("device_mismatch", "当前设备与授权不匹配"),
        ("unexpected_reason", "激活未通过"),
    ],
)
def test_activation_state_maps_failure_reason_without_raw_unknown_reason(reason, expected):
    result = ActivationResult(activated=False, mode="none", reason=reason)

    state = E.activation_state(result, now=NOW)

    assert state["run_full"] is False
    assert expected in state["text"]
    if reason == "unexpected_reason":
        assert reason not in state["text"]


def test_should_run_full_uses_process_last_when_no_arg():
    # refresh_activation 别名设置 _LAST; should_run_full() 无参用它。
    import bidking_lab.secure.enforce as mod
    mod._LAST = ActivationResult(activated=True, mode="paid", reason="ok")
    try:
        assert E.should_run_full() is True
        assert E.current() is mod._LAST
    finally:
        mod._LAST = None


@pytest.mark.parametrize("reason", ["offline", "bad_token", "token_persist_failed"])
def test_distinct_ui_credential_failure_replaces_process_state(monkeypatch, reason):
    """A pasted different credential is never treated as a heartbeat for the old grant."""
    old = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        lease_exp=NOW + 3600,
        paid_exp=NOW + 7200,
    )
    failure = ActivationResult(activated=False, mode="paid", reason=reason)
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda ui_token=None: "new" if ui_token else "old",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: failure,
    )
    E._LAST = old
    monkeypatch.setattr(E.time, "time", lambda: NOW)
    try:
        returned = E.startup_activate(ui_token="new", force_server=True)
        assert returned is failure
        assert E.current() is failure
        assert not E.should_run_full()
    finally:
        E._LAST = None


@pytest.mark.parametrize(
    "reason",
    [
        "no_server",
        "upgrade_required",
        "device_mismatch",
        "device_revoked",
        "revoked",
        "server_rejected",
    ],
)
def test_distinct_ui_credential_failure_outside_allowlist_replaces_process_state(
    monkeypatch,
    reason,
):
    old = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        lease_exp=NOW + 3600,
        paid_exp=NOW + 7200,
    )
    failure = ActivationResult(activated=False, mode="paid", reason=reason)
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda ui_token=None: "new" if ui_token else "old",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: failure,
    )
    monkeypatch.setattr(E.time, "time", lambda: NOW)
    E._LAST = old
    try:
        assert E.startup_activate(ui_token="new", force_server=True) is failure
        assert E.current() is failure
    finally:
        E._LAST = None


@pytest.mark.parametrize(
    ("ui_token", "resolved_ui_token"),
    [
        (None, "old"),
        ("old", "old"),
        ("new", "new"),
    ],
)
def test_device_revoked_replaces_old_process_grant_for_heartbeat_same_and_new_token(
    monkeypatch,
    ui_token,
    resolved_ui_token,
):
    old = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        lease_exp=NOW + 3600,
        paid_exp=NOW + 7200,
    )
    revoked = ActivationResult(
        activated=False,
        mode="paid",
        reason="device_revoked",
    )
    seen_force_server: list[bool] = []

    def resolve(candidate=None):
        return resolved_ui_token if candidate else "old"

    def activate(**kwargs):
        seen_force_server.append(bool(kwargs.get("force_server")))
        return revoked

    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        resolve,
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        activate,
    )
    monkeypatch.setattr(E.time, "time", lambda: NOW)
    E._LAST = old
    try:
        assert E.startup_activate(ui_token=ui_token, force_server=True) is revoked
        assert E.current() is revoked
        assert not E.should_run_full()
        assert seen_force_server == [True]
    finally:
        E._LAST = None


@pytest.mark.parametrize("reason", ["offline", "bad_token", "token_persist_failed"])
def test_same_ui_credential_failure_never_preserves_process_state(monkeypatch, reason):
    old = ActivationResult(
        activated=True,
        mode="paid",
        reason="ok",
        lease_exp=NOW + 3600,
        paid_exp=NOW + 7200,
    )
    failure = ActivationResult(activated=False, mode="paid", reason=reason)
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda _ui_token=None: "same",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: failure,
    )
    monkeypatch.setattr(E.time, "time", lambda: NOW)
    E._LAST = old
    try:
        assert E.startup_activate(ui_token="same", force_server=True) is failure
        assert E.current() is failure
    finally:
        E._LAST = None


@pytest.mark.parametrize(
    "old",
    [
        ActivationResult(activated=True, mode="paid", reason="ok", lease_exp=NOW),
        ActivationResult(activated=True, mode="paid", reason="ok", lease_exp=str(NOW + 10)),
        ActivationResult(activated=True, mode="paid", reason="ok", lease_exp=True),
        ActivationResult(
            activated=True,
            mode="paid",
            reason="ok",
            lease_exp=NOW + 100,
            paid_exp=NOW,
        ),
        ActivationResult(
            activated=True,
            mode="paid",
            reason="ok",
            lease_exp=NOW + 100,
            paid_exp=str(NOW + 100),
        ),
        ActivationResult(
            activated=True,
            mode="trial",
            reason="ok",
            lease_exp=NOW + 100,
            trial_exp=NOW,
        ),
        ActivationResult(
            activated=True,
            mode="trial",
            reason="ok",
            lease_exp=NOW + 100,
            trial_exp=True,
        ),
    ],
)
def test_malformed_or_expired_previous_grant_is_never_preserved(monkeypatch, old):
    failure = ActivationResult(activated=False, mode="paid", reason="offline")
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda ui_token=None: "new" if ui_token else "old",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: failure,
    )
    monkeypatch.setattr(E.time, "time", lambda: NOW)
    E._LAST = old
    try:
        assert E.startup_activate(ui_token="new", force_server=True) is failure
        assert E.current() is failure
    finally:
        E._LAST = None


@pytest.mark.parametrize(
    "grant",
    [
        ActivationResult(
            activated=True,
            mode="paid",
            reason="ok",
            lease_exp=NOW + 100,
            paid_exp=NOW + 200,
        ),
        ActivationResult(
            activated=True,
            mode="paid",
            reason="ok",
            lease_exp=NOW + 100,
            paid_exp=None,
            plan_kind="legacy_permanent",
        ),
        ActivationResult(
            activated=True,
            mode="trial",
            reason="ok",
            lease_exp=NOW + 100,
            trial_exp=NOW + 100,
        ),
    ],
)
def test_strict_process_grant_accepts_only_live_finite_permanent_or_trial(grant):
    assert E._strictly_time_valid_process_grant(grant, now=NOW)


def test_same_credential_denial_and_upgrade_replace_process_state(monkeypatch):
    old = ActivationResult(activated=True, mode="paid", reason="ok")
    denied = ActivationResult(activated=False, mode="paid", reason="revoked")
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda _ui_token=None: "same",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: denied,
    )
    E._LAST = old
    try:
        assert E.startup_activate(ui_token="same", force_server=True) is denied
        assert E.current() is denied
    finally:
        E._LAST = None

    upgrade = ActivationResult(
        activated=False,
        mode="paid",
        reason="upgrade_required",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.resolve_token",
        lambda ui_token=None: "new" if ui_token else "old",
    )
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.activate",
        lambda **_kwargs: upgrade,
    )
    E._LAST = old
    try:
        assert E.startup_activate(ui_token="new", force_server=True) is upgrade
        assert E.current() is upgrade
    finally:
        E._LAST = None


def test_transient_activation_failure_retries_fast_then_backs_off():
    offline = ActivationResult(activated=False, mode="none", reason="offline")
    assert E.is_transient_activation_failure(offline) is True
    assert [E.activation_refresh_delay(offline, n) for n in range(1, 6)] == [2.0, 5.0, 15.0, 60.0, 60.0]


def test_authoritative_activation_denial_and_success_keep_normal_heartbeat():
    expired = ActivationResult(activated=False, mode="trial", reason="trial_expired")
    paid = ActivationResult(activated=True, mode="paid", reason="ok")
    assert E.is_transient_activation_failure(expired) is False
    assert E.activation_refresh_delay(expired, 1) == 60.0
    assert E.activation_refresh_delay(paid, 1) == 60.0


@pytest.mark.parametrize(
    ("reason", "expected_text", "needs_token"),
    [
        ("state_error", "服务器激活服务暂时异常", False),
        ("rate_limited", "激活请求过于频繁", False),
        ("server_error", "激活服务器暂时异常", False),
        ("upgrade_required", "当前版本已停用", False),
        ("server_rejected", "激活请求被服务器拒绝", True),
    ],
)
def test_activation_state_keeps_server_failures_distinct(
    reason, expected_text, needs_token
):
    result = ActivationResult(activated=False, mode="paid", reason=reason)

    state = E.activation_state(result, now=NOW)

    assert not state["run_full"]
    assert state["reason"] == reason
    assert expected_text in state["text"]
    assert state["needs_token"] is needs_token


@pytest.mark.parametrize("reason", ["state_error", "server_error"])
def test_recoverable_server_failures_use_fast_retry(reason):
    result = ActivationResult(activated=False, mode="paid", reason=reason)
    assert E.is_transient_activation_failure(result)
    assert E.activation_refresh_delay(result, 1) == 2.0
