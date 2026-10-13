from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _pet_access():
    return _load("pet_access_a3_contract", "pet_access.py")


def _overlay():
    return _load("ahmad_tk_overlay_a3_contract", "ahmad_tk_overlay.py")


def _metered_state(
    *,
    duration_days: int | None,
    status: str,
    remaining_seconds: int,
) -> dict[str, Any]:
    server_now = 2_000_000_000
    if duration_days is None:
        mode = "trial"
        plan_kind = "trial"
        kind = "trial"
        total_seconds = 600
    else:
        mode = "paid"
        plan_kind = "30d" if duration_days == 30 else "custom"
        kind = "quota"
        total_seconds = 43_200 if duration_days == 30 else 86_400
    visible = status == "active"
    pet: dict[str, Any] = {
        "kind": kind,
        "total_seconds": total_seconds,
        "status": status,
        "server_now": server_now,
        "entitlement_id": "a" * 64,
        "usage_revision": 7,
        "remaining_seconds": remaining_seconds,
        "last_seq": -1 if status == "available" else 4,
        "visible": visible,
        "visibility_lease_exp": server_now + 15 if visible else server_now,
        "entitlement_exp": server_now + 86_400,
    }
    if duration_days is not None:
        pet["duration_days"] = duration_days
    return {
        "mode": mode,
        "reason": "ok",
        "run_full": True,
        "needs_token": False,
        "plan_kind": plan_kind,
        "feature_entitlements": {"pet_companion": kind},
        "feature_usage": {"pet_companion": pet},
    }


def _full_state(duration_days: int | None) -> dict[str, Any]:
    server_now = 2_000_000_000
    plan_kind = (
        "legacy_permanent"
        if duration_days is None
        else "90d"
        if duration_days == 90
        else "365d"
        if duration_days == 365
        else "custom"
    )
    pet: dict[str, Any] = {
        "kind": "full",
        "status": "active",
        "server_now": server_now,
    }
    if duration_days is not None:
        pet.update(
            duration_days=duration_days,
            entitlement_exp=server_now + 86_400,
        )
    return {
        "mode": "paid",
        "reason": "ok",
        "run_full": True,
        "needs_token": False,
        "plan_kind": plan_kind,
        "feature_entitlements": {"pet_companion": "full"},
        "feature_usage": {"pet_companion": pet},
    }


class _BombString(str):
    calls = 0

    def __eq__(self, other: object) -> bool:
        type(self).calls += 1
        raise RuntimeError("must not compare protocol-backed Pet authority")

    def __ne__(self, other: object) -> bool:
        type(self).calls += 1
        raise RuntimeError("must not compare protocol-backed Pet authority")


@pytest.mark.parametrize(
    ("duration_days", "total_seconds"),
    [
        (30, 43_200),
        (31, 86_400),
        (60, 86_400),
        (89, 86_400),
    ],
)
@pytest.mark.parametrize(
    ("authority_status", "remaining", "ui_status", "allowed"),
    [
        ("available", None, "quota_available", False),
        ("active", -1, "quota_active", True),
        ("paused", -1, "quota_paused", False),
        ("exhausted", 0, "quota_exhausted", False),
    ],
)
def test_a3_consumes_only_server_visible_quota_authority(
    duration_days: int,
    total_seconds: int,
    authority_status: str,
    remaining: int | None,
    ui_status: str,
    allowed: bool,
) -> None:
    module = _pet_access()
    balance = total_seconds if remaining is None else (
        total_seconds + remaining if remaining < 0 else remaining
    )
    state = _metered_state(
        duration_days=duration_days,
        status=authority_status,
        remaining_seconds=balance,
    )

    access = module.pet_access_from_activation(
        state,
        trial_seconds_remaining=999_999_999,
        now=1,
    )

    assert access.status == ui_status
    assert access.allowed is allowed
    assert access.seconds_left == balance
    assert access.total_seconds == total_seconds
    assert access.duration_days == duration_days
    assert access.can_start_usage is (
        authority_status in {"available", "paused"}
    )


@pytest.mark.parametrize(
    ("duration_days", "plan_label"),
    [
        (90, "90 天猫猫权限"),
        (120, "120 天猫猫权限"),
        (365, "365 天猫猫权限"),
        (None, "历史永久猫猫权限"),
    ],
)
def test_a3_full_authority_is_subscription_bound_or_permanent(
    duration_days: int | None,
    plan_label: str,
) -> None:
    module = _pet_access()
    access = module.pet_access_from_activation(_full_state(duration_days))

    assert access.status == "full"
    assert access.allowed
    assert access.seconds_left is None
    assert access.duration_days == duration_days
    assert access.can_persist_preferences
    assert module.pet_access_tier_label(access) == plan_label
    assert "12 小时" not in module.pet_access_contract_text(access)


def test_a3_free_trial_and_fail_closed_states_do_not_affect_calculator() -> None:
    module = _pet_access()
    trial = _metered_state(
        duration_days=None,
        status="active",
        remaining_seconds=599,
    )
    trial_access = module.pet_access_from_activation(trial)
    assert trial_access.status == "trial_active"
    assert trial_access.allowed
    assert trial_access.total_seconds == 600

    offline = {
        "mode": "none",
        "reason": "offline",
        "run_full": False,
        "credential_present": True,
        "needs_token": False,
    }
    state_error = {
        "mode": "paid",
        "reason": "ok",
        "run_full": True,
        "plan_kind": "30d",
        "feature_entitlements": {"pet_companion": "none"},
        "feature_usage": {
            "pet_companion": {
                "kind": "none",
                "status": "state_error",
                "server_now": 2_000_000_000,
            }
        },
    }
    below_30 = {
        "mode": "paid",
        "reason": "ok",
        "run_full": True,
        "plan_kind": "custom",
        "feature_entitlements": {"pet_companion": "none"},
        "feature_usage": {
            "pet_companion": {
                "kind": "none",
                "status": "locked",
                "server_now": 2_000_000_000,
            }
        },
    }
    missing = {
        "mode": "paid",
        "reason": "ok",
        "run_full": True,
        "plan_kind": "30d",
    }
    malformed = {
        **trial,
        "feature_usage": {"pet_companion": {"kind": "trial"}},
    }
    for state, expected in (
        (offline, "state_error"),
        (state_error, "state_error"),
        (below_30, "locked"),
        (missing, "entitlement_pending"),
        (malformed, "state_error"),
    ):
        access = module.pet_access_from_activation(state)
        assert access.status == expected
        assert not access.allowed
        assert state["run_full"] is (state is not offline)


def test_a3_identity_switch_and_renewal_never_reuse_old_balance() -> None:
    module = _pet_access()
    old = _metered_state(
        duration_days=30,
        status="paused",
        remaining_seconds=7,
    )
    renewed = _full_state(90)
    switched = _metered_state(
        duration_days=60,
        status="available",
        remaining_seconds=86_400,
    )

    assert module.pet_access_from_activation(old).seconds_left == 7
    assert module.pet_access_from_activation(renewed).status == "full"
    switched_access = module.pet_access_from_activation(switched)
    assert switched_access.seconds_left == 86_400
    assert switched_access.duration_days == 60


@pytest.mark.parametrize("field", ("kind", "status"))
def test_a3_malformed_authority_is_no_magic_and_pet_only_fail_closed(
    field: str,
) -> None:
    module = _pet_access()
    state = _full_state(90)
    value = "full" if field == "kind" else "active"
    state["feature_usage"]["pet_companion"][field] = _BombString(value)
    _BombString.calls = 0

    access = module.pet_access_from_activation(state)

    assert access.status == "state_error"
    assert not access.allowed
    assert _BombString.calls == 0
    assert state["run_full"] is True


def test_formal_main_injects_product_pet_usage_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _overlay()
    captured: dict[str, Any] = {}

    class _Root:
        def attributes(self, *_args):
            return 1.0

        def withdraw(self):
            return None

        def iconbitmap(self, **_kwargs):
            return None

        def mainloop(self):
            handler = captured["handler"]
            state = module.pet_access_from_activation(
                _metered_state(
                    duration_days=30,
                    status="available",
                    remaining_seconds=43_200,
                )
            )
            assert handler("start", state) is True

    class _Overlay:
        def __init__(self, _root, **kwargs):
            captured["handler"] = kwargs.get("pet_usage_event_handler")
            captured["overlay"] = self
            self.events: list[str] = []

        def _queue_product_pet_usage_event(self, event, _state):
            self.events.append(event)
            return True

        def _save_ui_prefs_if_enabled(self):
            return None

        def _run_exit_cleanup(self):
            return None

    monkeypatch.setattr(module.tk, "Tk", _Root)
    monkeypatch.setattr(module, "AhmadTkOverlay", _Overlay)
    monkeypatch.setattr(module, "_reveal_root_when_ready", lambda *_a, **_k: None)

    assert module.main(["--snapshot", str(tmp_path / "snapshot.json")]) == 0
    assert callable(captured["handler"])
    assert captured["overlay"].events == ["start"]


def test_product_pet_usage_handler_is_nonblocking_and_serializes_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay()
    from bidking_lab.secure import enforce

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_state = _metered_state(
        duration_days=30,
        status="available",
        remaining_seconds=43_200,
    )
    overlay._pet_usage_request_queue = module.queue.Queue()
    overlay._pet_usage_worker_running = False
    overlay._pet_usage_identity_marker = None
    overlay._pet_usage_authority_snapshot = None
    overlay._pet_usage_session_id = "unused-session-marker"
    overlay._pet_usage_next_seq = 0
    called = threading.Event()
    release = threading.Event()
    received: list[dict[str, Any]] = []
    authority_a = (b"a" * 32, b"d" * 32, 0, 0, 1)
    authority_b = (b"b" * 32, b"d" * 32, 0, 0, 2)
    authority_c = (b"c" * 32, b"d" * 32, 0, 0, 3)
    authority = {"state": overlay._act_state, "snapshot": authority_a}

    def _refresh(event, *, expected_authority=None):
        assert expected_authority == authority["snapshot"]
        received.append(dict(event))
        called.set()
        release.wait(timeout=2.0)
        return SimpleNamespace(
            activated=True,
            feature_usage={
                "pet_companion": {
                    "status": "active" if event["visible"] else "paused",
                    "last_seq": event["seq"],
                    "visible": event["visible"],
                }
            },
        )

    monkeypatch.setattr(enforce, "refresh_pet_usage", _refresh)
    monkeypatch.setattr(
        enforce,
        "activation_state",
        lambda *_args, **_kwargs: authority["state"],
    )
    monkeypatch.setattr(
        enforce,
        "pet_usage_request_authority_snapshot",
        lambda: authority["snapshot"],
    )
    state = module.pet_access_from_activation(overlay._act_state)

    started = time.monotonic()
    assert overlay._queue_product_pet_usage_event("start", state) is True
    assert time.monotonic() - started < 0.25
    assert called.wait(timeout=1.0)
    release.set()
    overlay._pet_usage_request_queue.join()

    assert len(received) == 1
    assert received[0]["seq"] == 0
    assert received[0]["visible"] is True
    assert len(received[0]["session_id"]) == 32

    called.clear()
    release.clear()
    assert overlay._queue_product_pet_usage_event("hidden", state) is True
    assert called.wait(timeout=1.0)
    release.set()
    overlay._pet_usage_request_queue.join()
    assert received[1]["session_id"] == received[0]["session_id"]
    assert received[1]["seq"] == 1
    assert received[1]["visible"] is False

    switched = _metered_state(
        duration_days=60,
        status="available",
        remaining_seconds=86_400,
    )
    switched["feature_usage"]["pet_companion"]["entitlement_id"] = "b" * 64
    overlay._act_state = switched
    authority["state"] = switched
    authority["snapshot"] = authority_b
    called.clear()
    release.clear()
    switched_state = module.pet_access_from_activation(switched)
    assert overlay._queue_product_pet_usage_event("start", switched_state) is True
    assert called.wait(timeout=1.0)
    release.set()
    overlay._pet_usage_request_queue.join()
    assert received[2]["session_id"] != received[0]["session_id"]
    assert received[2]["seq"] == 0
    assert received[2]["visible"] is True

    authority["state"] = _metered_state(
        duration_days=30,
        status="available",
        remaining_seconds=43_200,
    )
    authority["state"]["feature_usage"]["pet_companion"]["entitlement_id"] = (
        "c" * 64
    )
    authority["snapshot"] = authority_c
    stale_request = {
        "session_id": received[2]["session_id"],
        "seq": 1,
        "visible": False,
    }
    overlay._pet_usage_request_queue.put(
        ("b" * 64, authority_b, stale_request)
    )
    overlay._pet_usage_request_queue.join()
    assert len(received) == 3


def test_product_pet_usage_worker_permanently_drops_old_a_after_aba(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay()
    from bidking_lab.secure import enforce

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._pet_usage_request_queue = module.queue.Queue()
    overlay._pet_usage_worker_running = True
    overlay._pet_usage_identity_marker = None
    overlay._pet_usage_authority_snapshot = None
    overlay._pet_usage_session_id = "unused-session-marker"
    overlay._pet_usage_next_seq = 0
    authority_a1 = (b"a" * 32, b"d" * 32, 0, 0, 1)
    authority_b = (b"b" * 32, b"d" * 32, 0, 0, 2)
    authority_a2 = (b"a" * 32, b"d" * 32, 0, 0, 3)
    authority = {"snapshot": authority_a1}
    received: list[dict[str, Any]] = []

    monkeypatch.setattr(
        enforce,
        "pet_usage_request_authority_snapshot",
        lambda: authority["snapshot"],
    )

    def _refresh(event, *, expected_authority=None):
        assert expected_authority == authority_a2
        received.append(dict(event))
        return SimpleNamespace(
            feature_usage={
                "pet_companion": {
                    "status": "active",
                    "last_seq": event["seq"],
                    "visible": event["visible"],
                }
            }
        )

    monkeypatch.setattr(enforce, "refresh_pet_usage", _refresh)

    state_a1 = _metered_state(
        duration_days=30,
        status="available",
        remaining_seconds=43_200,
    )
    overlay._act_state = state_a1
    assert overlay._queue_product_pet_usage_event(
        "start",
        module.pet_access_from_activation(state_a1),
    )
    old_a_session = overlay._pet_usage_session_id

    authority["snapshot"] = authority_b
    state_b = _metered_state(
        duration_days=60,
        status="available",
        remaining_seconds=86_400,
    )
    state_b["feature_usage"]["pet_companion"]["entitlement_id"] = "b" * 64
    overlay._act_state = state_b
    assert overlay._queue_product_pet_usage_event(
        "start",
        module.pet_access_from_activation(state_b),
    )

    authority["snapshot"] = authority_a2
    state_a2 = _metered_state(
        duration_days=30,
        status="available",
        remaining_seconds=43_200,
    )
    overlay._act_state = state_a2
    assert overlay._queue_product_pet_usage_event(
        "start",
        module.pet_access_from_activation(state_a2),
    )
    current_a_session = overlay._pet_usage_session_id
    assert current_a_session != old_a_session

    threading.Thread(
        target=overlay._run_product_pet_usage_worker,
        daemon=True,
    ).start()
    overlay._pet_usage_request_queue.join()

    assert [event["session_id"] for event in received] == [
        current_a_session
    ]
    assert received[0]["seq"] == 0


@pytest.mark.parametrize("identity_changes", (False, True))
def test_product_pet_usage_worker_rechecks_authority_before_same_seq_retry(
    monkeypatch: pytest.MonkeyPatch,
    identity_changes: bool,
) -> None:
    module = _overlay()
    from bidking_lab.secure import enforce

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._pet_usage_request_queue = module.queue.Queue()
    overlay._pet_usage_worker_running = True
    authority_a = (b"a" * 32, b"d" * 32, 0, 0, 1)
    authority_b = (b"b" * 32, b"d" * 32, 0, 0, 2)
    authority = {"snapshot": authority_a}
    event = {"session_id": "a" * 32, "seq": 7, "visible": True}
    received: list[dict[str, Any]] = []

    monkeypatch.setattr(
        enforce,
        "pet_usage_request_authority_snapshot",
        lambda: authority["snapshot"],
    )

    def _refresh(request, *, expected_authority=None):
        assert expected_authority == authority_a
        received.append(dict(request))
        if len(received) == 1:
            return SimpleNamespace(activated=False, reason="offline")
        return SimpleNamespace(
            activated=True,
            feature_usage={
                "pet_companion": {
                    "status": "active",
                    "last_seq": request["seq"],
                    "visible": request["visible"],
                }
            },
        )

    def _sleep(_seconds):
        if identity_changes:
            authority["snapshot"] = authority_b

    monkeypatch.setattr(enforce, "refresh_pet_usage", _refresh)
    monkeypatch.setattr(module.time, "sleep", _sleep)
    overlay._pet_usage_request_queue.put(("a" * 64, authority_a, event))

    threading.Thread(
        target=overlay._run_product_pet_usage_worker,
        daemon=True,
    ).start()
    overlay._pet_usage_request_queue.join()

    assert received == (
        [event]
        if identity_changes
        else [event, event]
    )


@pytest.mark.parametrize(
    "status",
    ("session_conflict", "sequence_conflict", "state_error"),
)
def test_product_pet_usage_ack_rejects_conflict_status(status: str) -> None:
    module = _overlay()
    event = {"session_id": "a" * 32, "seq": 0, "visible": True}
    result = SimpleNamespace(
        feature_usage={
            "pet_companion": {
                "status": status,
                "last_seq": 0,
                "visible": True,
            }
        }
    )

    assert not module.AhmadTkOverlay._pet_usage_event_acknowledged(
        result,
        event,
    )


def _new_root(tk):
    try:
        return tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"真 Tk 不可用(环境层): {exc}")


def _texts(widget) -> list[str]:
    values: list[str] = []
    stack = [widget]
    while stack:
        current = stack.pop()
        stack.extend(current.winfo_children())
        try:
            text = current.cget("text")
        except Exception:  # noqa: BLE001 - heterogeneous Tk widget tree
            continue
        if type(text) is str and text:
            values.append(text)
    return values


@pytest.mark.parametrize(
    "tk_scaling",
    (1.333333, 1.666667, 2.0, 2.333333),
    ids=("100pct", "125pct", "150pct", "175pct"),
)
def test_a3_real_tk_compact_menu_dialog_and_footer_follow_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tk_scaling: float,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay()
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
    shown_menus: list[Any] = []
    monkeypatch.setattr(
        tk.Menu,
        "tk_popup",
        lambda menu, _x, _y: shown_menus.append(menu),
    )
    root = _new_root(tk)
    overlay = None
    try:
        root.tk.call("tk", "scaling", tk_scaling)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"a3-{tk_scaling}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.update()

        overlay._act_state = _metered_state(
            duration_days=30,
            status="active",
            remaining_seconds=43_199,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay.pet_launcher_timer.cget("text") == "11:59:59"
        assert "30 天猫猫额度" in overlay.pet_launcher_tip.text
        assert "12 小时" not in overlay.pet_launcher_tip.text
        overlay._show_pet_menu(x_root=20, y_root=20)
        menu_labels = [
            shown_menus[-1].entrycget(index, "label")
            for index in range(shown_menus[-1].index("end") + 1)
            if shown_menus[-1].type(index) != "separator"
        ]
        assert "30 天猫猫额度：剩余 11:59:59" in menu_labels

        overlay._show_pet_access_dialog(overlay._current_pet_access())
        root.update()
        dialog = overlay._pet_access_dialog
        assert dialog is not None and dialog.winfo_viewable()
        dialog_text = "\n".join(_texts(dialog))
        assert "30 天猫猫额度" in dialog_text
        assert "30 天授权可使用猫猫 12 小时" in dialog_text
        assert "余额以服务端签名结果为准" in dialog_text
        assert dialog.winfo_width() >= dialog.winfo_reqwidth()
        assert dialog.winfo_height() >= dialog.winfo_reqheight()
        overlay._close_pet_access_dialog()

        overlay._act_state = _metered_state(
            duration_days=60,
            status="paused",
            remaining_seconds=86_399,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert not overlay._current_pet_access().allowed
        assert "60 天猫猫额度" in overlay.pet_launcher_tip.text
        overlay._show_pet_access_dialog(overlay._current_pet_access())
        root.update()
        paused_text = "\n".join(_texts(overlay._pet_access_dialog))
        assert "继续显示猫猫" in paused_text
        assert "31–89 天授权可使用猫猫 24 小时" in paused_text
        overlay._close_pet_access_dialog()

        overlay._act_state = _full_state(90)
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay.pet_launcher_timer.winfo_manager() == ""
        assert "90 天及以上" in overlay.pet_launcher_tip.text
        assert "12 小时" not in overlay.pet_launcher_tip.text
        overlay._show_pet_access_dialog(overlay._current_pet_access())
        root.update()
        full_text = "\n".join(_texts(overlay._pet_access_dialog))
        assert "订阅有效期内不限累计时长" in full_text
        assert "不扣减猫猫计时额度" in full_text
        overlay._close_pet_access_dialog()

        overlay._act_state = {
            "mode": "none",
            "reason": "offline",
            "run_full": False,
            "credential_present": True,
            "needs_token": False,
        }
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay.pet_launcher_timer.cget("text") == "!"
        assert "主功能不受影响" in overlay.pet_launcher_tip.text

        overlay._open_token_dialog()
        root.update()
        assert overlay._token_dialog is not None
        footer_text = "\n".join(_texts(overlay._token_dialog))
        assert (
            "猫猫：免费10分钟 ｜ 30天12小时 ｜ 31–89天24小时 ｜ "
            "90天及以上订阅期内不限时"
        ) in footer_text
        assert "90/365天有效期内可用" not in footer_text
        assert overlay._token_dialog.winfo_width() >= (
            overlay._token_dialog.winfo_reqwidth()
        )
        assert overlay._token_dialog.winfo_height() >= (
            overlay._token_dialog.winfo_reqheight()
        )
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            token_dialog = getattr(overlay, "_token_dialog", None)
            if token_dialog is not None:
                try:
                    token_dialog.destroy()
                except tk.TclError:
                    pass
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass
