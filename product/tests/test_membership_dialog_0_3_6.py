"""Presentation and actual dialog handler paths; no visible Tk or real accounts."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from bidking_lab.secure import enforce
from bidking_lab.secure.membership_ui import renewal_available, renewal_feedback, SWITCH_CONFIRMATION
from test_activation_clear_preflight_hotfix2 import calls, dialog, dialog_handler_code
from test_activation_clear_runtime_deny_0_3_1 import isolated_clear_runtime

STATE = {"run_full": True, "mode": "paid", "plan_kind": "30d",
         "membership_card_capability": {"schema_version": 1, "pet_quota_schema_version": 2}}


def result(status="redeemed", *, days=30):
    return SimpleNamespace(activated=True, mode="paid", reason="ok", card_redemption={
        "schema_version": 1, "status": status, "committed": True,
        "credited_seconds": days * 86400, "membership_exp": 1_800_000_000,
        "pet_quota_added_seconds": 43200 if days == 30 else 0,
    })


@pytest.mark.parametrize("plan", ["30d", "90d", "365d"])
def test_only_capable_current_standard_membership_has_renewal_action(plan):
    state = dict(STATE, plan_kind=plan)
    assert renewal_available(state)
    assert not renewal_available(state, diagnostic=True)
    for key, value in (("plan_kind", "legacy_permanent"), ("plan_kind", []),
                       ("mode", "trial"), ("run_full", False), ("membership_card_capability", None),
                       ("membership_card_capability", {"schema_version": True, "pet_quota_schema_version": 2})):
        assert not renewal_available(dict(state, **{key: value}))


def test_confirmed_replay_does_not_claim_another_credit():
    success, replay = renewal_feedback(result()), renewal_feedback(result("replayed"))
    assert success.succeeded and "增加 30 天" in success.text and "12 小时" in success.text
    assert replay.succeeded and "未重复加时" in replay.text
    assert "增加 30 天" not in replay.text and "配额增加" not in replay.text
    assert "不会暂停计时" in SWITCH_CONFIRMATION


@pytest.mark.parametrize("status", ["switch_required_permissions", "switch_required_used_card", "unsupported_plan"])
def test_operation_choice_never_labels_valid_switchable_card_invalid(status):
    value = SimpleNamespace(activated=False, reason="card_action", card_redemption={"status": status, "committed": False})
    feedback = renewal_feedback(value)
    assert feedback.allow_switch and not feedback.succeeded
    assert "更换使用" in feedback.text and "token 无效" not in feedback.text


@pytest.mark.parametrize("value", [None, SimpleNamespace(activated=True, mode="paid"),
                                   SimpleNamespace(activated=False, reason="confirmation_pending",
                                                   card_redemption={"status": "confirmation_pending", "committed": None})])
def test_missing_confirmation_is_not_a_credit_or_a_switch(value):
    feedback = renewal_feedback(value)
    assert not feedback.succeeded and not feedback.allow_switch and feedback.confirmation_pending
    assert "同一张卡重试" in feedback.text


def open_renewal(view):
    view.self._act_state = copy.deepcopy(STATE)
    current = view.reopen()
    current.entry.value = "synthetic-new-card"
    assert current.ok_btn.options["text"] == "续费"
    return current


def finish(view):
    view.workers.pop(0)()
    view.callbacks.pop(0)()


def test_actual_handler_renew_never_calls_switch_and_deduplicates_clicks(dialog, monkeypatch):
    view = open_renewal(dialog)
    redeemed, switched = [], []
    monkeypatch.setattr(enforce, "redeem_membership_card", lambda **kw: redeemed.append(kw) or result())
    monkeypatch.setattr(enforce, "refresh_activation", lambda **kw: switched.append(kw))
    view._submit()
    view._submit()
    assert len(view.workers) == 1 and view.clear_btn.options["state"] == "disabled"
    assert view.entry.options["state"] == "disabled"
    finish(view)
    assert redeemed == [{"card_token": "synthetic-new-card"}] and not switched
    assert "续费成功" in view.status.options["text"]
    assert not view.entry.value and not view.callbacks  # Receipt remains visible; no 900ms auto-close.
    assert view.entry.options["state"] == "normal"


@pytest.mark.parametrize("response", [
    SimpleNamespace(activated=False, reason="card_action", card_redemption={"status": "switch_required_permissions", "committed": False}),
    SimpleNamespace(activated=False, reason="confirmation_pending", card_redemption={"status": "confirmation_pending", "committed": None}),
])
def test_actual_handler_operation_failure_keeps_card_and_never_reads_failure_as_current_A(dialog, monkeypatch, response):
    view = open_renewal(dialog)
    monkeypatch.setattr(enforce, "redeem_membership_card", lambda **_kw: response)
    monkeypatch.setattr(enforce, "refresh_activation", lambda **_kw: pytest.fail("automatic switch"))
    monkeypatch.setattr(enforce, "activation_state", lambda *_: pytest.fail("operation result treated as current membership"))
    view._submit()
    finish(view)
    assert view.entry.value == "synthetic-new-card"
    assert view.entry.options["state"] == "normal"
    assert view.ok_btn.options["state"] == "normal"
    assert not view.callbacks


def test_actual_switch_has_explicit_confirmation_and_original_activation_route(dialog, monkeypatch):
    from tkinter import messagebox
    view = open_renewal(dialog)
    switched = []
    monkeypatch.setattr(enforce, "redeem_membership_card", lambda **_kw: pytest.fail("switch redeemed card"))
    monkeypatch.setattr(enforce, "refresh_activation", lambda **kw: switched.append(kw) or SimpleNamespace(activated=True, mode="paid"))
    monkeypatch.setattr(enforce, "activation_state", lambda *_: {"mode": "paid"})
    monkeypatch.setattr(messagebox, "askyesno", lambda *_a, **_kw: False)
    view._submit("switch")
    assert not view.workers and not switched
    monkeypatch.setattr(messagebox, "askyesno", lambda *_a, **_kw: True)
    view._submit("switch")
    finish(view)
    assert switched == [{"ui_token": "synthetic-new-card"}]


@pytest.mark.parametrize("status", ["redeemed", "replayed"])
def test_renewal_success_only_clears_the_submitted_input(dialog, monkeypatch, status):
    view = open_renewal(dialog)
    redeemed = []
    monkeypatch.setattr(enforce, "redeem_membership_card",
                        lambda **kw: redeemed.append(kw) or result(status))
    view._submit()
    assert view.entry.options["state"] == "disabled"
    # Defense for a programmatic/replaced widget value: Tk's disabled state
    # already prevents user edits, but the old callback still owns only B.
    view.entry.value = "synthetic-different-card"
    finish(view)
    assert redeemed == [{"card_token": "synthetic-new-card"}]
    assert view.entry.value == "synthetic-different-card"
    assert view.entry.options["state"] == "normal"


def test_close_or_clear_new_generation_prevents_late_renewal_ui_success(dialog, monkeypatch):
    view = open_renewal(dialog)
    monkeypatch.setattr(enforce, "redeem_membership_card", lambda **_kw: result())
    view._submit()
    view.workers.pop(0)()
    view._close()
    view.callbacks.pop(0)()
    assert "续费成功" not in view.status.options["text"]


def test_capable_long_membership_keeps_same_entry_reachable_without_changing_legacy(monkeypatch):
    from test_live_overlay import _ahmad_overlay_module
    module = _ahmad_overlay_module()
    monkeypatch.setattr(enforce, "persistent_authoritative_deny_state", lambda: None)
    state = dict(STATE, plan_kind="365d", seconds_left=200 * 86400, text="会员 · 剩余 200 天")
    monkeypatch.setattr(enforce, "activation_state", lambda: state)
    instance = object.__new__(module.AhmadTkOverlay)
    instance._act_ready = True
    instance._expired_latched = False
    instance.activation_label = SimpleNamespace(configure=lambda **_kw: None)
    available, visibility = [], []
    instance._set_activation_entry_available = available.append
    instance._show_activation_label = lambda: visibility.append("show")
    instance._hide_activation_label = lambda: visibility.append("hide")
    assert instance._update_activation_ui() is False
    assert available[-1] is True and visibility[-1] == "show"
    state.pop("membership_card_capability")
    assert instance._update_activation_ui() is False
    assert available[-1] is False and visibility[-1] == "hide"
