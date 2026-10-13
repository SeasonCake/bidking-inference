from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "hero_ref" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _pet_module():
    path = TOOLS_DIR / "pet_companion.py"
    spec = importlib.util.spec_from_file_location("pet_companion", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _overlay_module():
    path = TOOLS_DIR / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("ahmad_tk_overlay", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pet_access_module():
    path = TOOLS_DIR / "pet_access.py"
    spec = importlib.util.spec_from_file_location("pet_access_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _match_records_module():
    path = ROOT / "scripts" / "build_match_records.py"
    spec = importlib.util.spec_from_file_location("build_match_records_pet_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _new_root(tk, attempts: int = 3):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as exc:  # noqa: PERF203 - bounded environment retry
            last = exc
    pytest.skip(f"真 Tk 不可用(环境层): {last}")


def _pump_tk_until(root, predicate, *, timeout: float = 3.0) -> bool:
    import time

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.002)
    root.update()
    return bool(predicate())


def _new_pet(module, root, *, color="orange", reduced_motion=True, **kwargs):
    return module.PetCompanionOverlay(
        root,
        color=color,
        position=kwargs.pop("position", (160, 160)),
        topmost=kwargs.pop("topmost", False),
        reduced_motion=reduced_motion,
        work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
        on_click=lambda: None,
        on_context_menu=lambda _x, _y: None,
        on_position_changed=lambda _position: None,
        on_hide_requested=lambda: None,
        **kwargs,
    )


def test_pet_assets_resolve_inside_pyinstaller_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "hero_ref" / "assets" / "pet_companion"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    pet = _pet_module()

    assert pet.PET_ASSET_DIR == bundled


def test_pet_assets_cover_all_coats_with_real_transparency() -> None:
    module = _pet_module()

    assert module.pet_assets_available()
    for color in module.PET_COLOR_ORDER:
        for pose in module.PET_POSE_ORDER:
            path = module.pet_asset_path(color, pose)
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                assert rgba.size == (192, 192)
                alpha = rgba.getchannel("A")
                assert alpha.getpixel((0, 0)) == 0
                assert alpha.getbbox() is not None
                assert alpha.getextrema() == (0, 255)
        for pose in module.PET_EYE_VARIANT_POSES:
            path = module.pet_asset_path(color, pose)
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                assert rgba.size == (192, 192)
                assert rgba.getchannel("A").getpixel((0, 0)) == 0
                assert rgba.getchannel("A").getbbox() is not None

    with Image.open(module.pet_asset_path("navy", "idle")) as navy:
        bbox = navy.convert("RGBA").getchannel("A").getbbox()
        assert bbox is not None
        assert bbox[2] - bbox[0] >= 170, "深蓝猫后半身/尾巴不得再次被透明提取删除"


def test_pet_settlement_actions_have_six_authored_frames_per_coat() -> None:
    module = _pet_module()

    assert set(module.PET_SETTLEMENT_ACTIONS).isdisjoint(module.PET_ACTIONS)
    for color in module.PET_COLOR_ORDER:
        frames = module.build_pet_frames(color)
        for action in module.PET_SETTLEMENT_ACTIONS:
            transition_poses = module.PET_AUTHORED_TRANSITIONS[action]
            poses = (*transition_poses, action)
            assert len(poses) == 6
            assert set(poses) <= set(frames)
            identities: set[bytes] = set()
            for pose in poses:
                path = module.pet_asset_path(color, pose)
                with Image.open(path) as image:
                    rgba = image.convert("RGBA")
                    assert rgba.size == (192, 192)
                    assert rgba.getchannel("A").getpixel((0, 0)) == 0
                    assert rgba.getchannel("A").getbbox() is not None
                    identities.add(rgba.tobytes())
            assert len(identities) == 6


def test_pet_normalization_and_multimonitor_clamp() -> None:
    module = _pet_module()

    assert module.normalize_pet_color("NAVY") == "navy"
    assert module.normalize_pet_color("invalid") == "ragdoll"
    assert module.normalize_pet_color(None) == "ragdoll"
    assert module.normalize_pet_position([-1800, "720"]) == (-1800, 720)
    assert module.normalize_pet_position(["bad", 720]) is None
    assert module.normalize_pet_touch_state(
        "2026-07-22",
        7,
        42,
        current_date="2026-07-22",
    ) == ("2026-07-22", 7, 42)
    assert module.normalize_pet_touch_state(
        "2026-07-21",
        7,
        42,
        current_date="2026-07-22",
    ) == ("2026-07-22", 0, 42)
    assert module.clamp_pet_position(
        -2500,
        2000,
        width=176,
        height=176,
        work_area=(-1920, 0, 0, 1080),
    ) == (-1920, 904)


def test_pet_profit_reaction_requires_complete_own_win_and_positive_net() -> None:
    module = _overlay_module()
    positive = {
        "phase": "settled",
        "session_id": "2201:positive",
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 120_000,
        "self_bid": 100_000,
    }

    assert module.pet_profitable_own_settlement(positive) == (
        "2201:positive",
        18_000.0,
    )
    assert module.pet_profitable_own_settlement(
        {**positive, "self_bid": 118_000}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "self_bid": 118_001}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "winner_is_self": False}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "settlement_incomplete": True}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "settlement_incomplete": None}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "map_id": 9901}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**positive, "known_value_sum": "120000"}
    ) is None
    assert module.pet_profitable_own_settlement(
        {
            **positive,
            "map_id": 5608,
            "known_value_sum": 120_000,
            "self_bid": 104_999,
        }
    ) == ("2201:positive", 1.0)


@pytest.mark.parametrize(
    ("net_result", "action", "bubble"),
    [
        (1, "perk", "好耶！"),
        (199_999, "perk", "好耶！"),
        (200_000, "big_win", "太棒啦！"),
        (200_001, "big_win", "太棒啦！"),
        (-1, "loss_peek", "差一点点…"),
        (-199_999, "loss_peek", "差一点点…"),
        (-200_000, "loss_droop", "呜…下局再来"),
        (-200_001, "loss_droop", "呜…下局再来"),
    ],
)
def test_pet_settlement_reaction_uses_exact_200k_tiers(
    net_result: int,
    action: str,
    bubble: str,
) -> None:
    module = _overlay_module()
    snapshot = {
        "phase": "settled",
        "session_id": f"2201:tier:{net_result}",
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 502_000 + net_result,
        "self_bid": 500_000,
    }

    assert module.pet_own_settlement_net_result(snapshot) == (
        f"2201:tier:{net_result}",
        float(net_result),
    )
    assert module.pet_settlement_reaction(snapshot) == (
        f"2201:tier:{net_result}",
        float(net_result),
        action,
        bubble,
    )


def test_pet_settlement_reaction_does_nothing_on_exact_break_even() -> None:
    module = _overlay_module()
    snapshot = {
        "phase": "settled",
        "session_id": "2201:break-even",
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 502_000,
        "self_bid": 500_000,
    }

    assert module.pet_own_settlement_net_result(snapshot) == (
        "2201:break-even",
        0.0,
    )
    assert module.pet_settlement_reaction(snapshot) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("known_value_sum", 10**400),
        ("self_bid", 10**400),
        ("known_value_sum", float("nan")),
        ("self_bid", float("nan")),
        ("known_value_sum", float("inf")),
        ("self_bid", float("-inf")),
        ("known_value_sum", True),
        ("self_bid", False),
        ("known_value_sum", "120000"),
        ("self_bid", "100000"),
        ("known_value_sum", -1),
        ("self_bid", -1),
    ],
)
def test_pet_profit_reaction_fails_closed_for_malformed_amounts(
    field: str,
    value: object,
) -> None:
    module = _overlay_module()
    snapshot = {
        "phase": "settled",
        "session_id": f"2201:invalid:{field}",
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 120_000,
        "self_bid": 100_000,
        field: value,
    }

    assert module.pet_profitable_own_settlement(snapshot) is None


@pytest.mark.parametrize(
    ("map_id", "entry_fee"),
    [
        (2101, 0),
        (2201, 2_000),
        (2301, 5_000),
        (2401, 10_000),
        (2501, 25_000),
        (2601, 100_000),
        (5501, 15_000),
        (5608, 15_000),
    ],
)
def test_pet_profit_reaction_uses_authoritative_family_fee_boundaries(
    map_id: int,
    entry_fee: int,
) -> None:
    module = _overlay_module()
    base = {
        "phase": "settled",
        "session_id": f"{map_id}:fee-boundary",
        "map_id": map_id,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "self_bid": 100_000,
    }

    assert module.pet_profitable_own_settlement(
        {**base, "known_value_sum": 100_000 + entry_fee + 1}
    ) == (f"{map_id}:fee-boundary", 1.0)
    assert module.pet_profitable_own_settlement(
        {**base, "known_value_sum": 100_000 + entry_fee}
    ) is None
    assert module.pet_profitable_own_settlement(
        {**base, "known_value_sum": 100_000 + entry_fee - 1}
    ) is None


def test_pet_profit_reaction_ignores_stale_fee_and_rejects_unknown_map() -> None:
    module = _overlay_module()
    ranked = {
        "phase": "settled",
        "session_id": "5608:stale-fee",
        "map_id": 5608,
        "entry_fee_silver": 25_000,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 120_000,
        "self_bid": 100_000,
    }

    assert module.pet_profitable_own_settlement(ranked) == (
        "5608:stale-fee",
        5_000.0,
    )
    assert module.pet_profitable_own_settlement(
        {
            **ranked,
            "session_id": "9901:explicit-fee",
            "map_id": 9901,
            "entry_fee_silver": 0,
        }
    ) is None


def test_pet_entry_fee_prefixes_match_my_records_authority() -> None:
    overlay = _overlay_module()
    records = _match_records_module()

    assert set(overlay._PET_SETTLEMENT_ENTRY_FEE_BY_PREFIX) == set(
        records._FAMILY_BY_PREFIX2
    )
    for prefix, family in records._FAMILY_BY_PREFIX2.items():
        assert overlay._PET_SETTLEMENT_ENTRY_FEE_BY_PREFIX[prefix] == (
            records._FEE_BY_FAMILY[family]
        )


def test_pet_click_shows_interaction_status_before_animation_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    calls: list[tuple[object, ...]] = []

    class Companion:
        def show_touch_feedback(self, text: str, *, duration_ms: int) -> None:
            calls.append(("feedback", text, duration_ms))

        def meow(self) -> None:
            calls.append(("action", "meow"))
            raise module.tk.TclError("synthetic animation failure")

    overlay._pet_companion = Companion()
    overlay._record_pet_touch = lambda: (1, 1)
    overlay._next_pet_companion_action = lambda: "meow"
    monkeypatch.setattr(module.random, "choice", lambda _values: "喵～")

    overlay._interact_with_pet_companion()

    assert calls == [
        ("feedback", "互动 · 喵～  +1", 1050),
        ("action", "meow"),
    ]


class _SettlementReactionCompanion:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.bubbles: list[str] = []

    def perk(self) -> None:
        self.actions.append("perk")

    def big_win(self) -> None:
        self.actions.append("big_win")

    def loss_peek(self) -> None:
        self.actions.append("loss_peek")

    def loss_droop(self) -> None:
        self.actions.append("loss_droop")

    def show_bubble(self, text: str, *, duration_ms: int) -> None:
        assert duration_ms == 1800
        self.bubbles.append(text)


def _profitable_session(session_id: str) -> dict[str, object]:
    return {
        "phase": "settled",
        "session_id": session_id,
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 120_000,
        "self_bid": 100_000,
    }


def _settlement_for_net(session_id: str, net_result: int) -> dict[str, object]:
    return {
        "phase": "settled",
        "session_id": session_id,
        "map_id": 2201,
        "settlement_incomplete": False,
        "winner_is_self": True,
        "known_value_sum": 502_000 + net_result,
        "self_bid": 500_000,
    }


def test_pet_settlement_reaction_consumes_each_session_once_across_aba() -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    companion = _SettlementReactionCompanion()
    overlay._pet_companion = companion
    overlay._pet_enabled = True
    overlay._is_pet_companion_visible = lambda: True
    overlay._consumed_pet_settlement_reaction_sessions = set()
    overlay._pet_touch_today = 3
    overlay._pet_touch_total = 9
    overlay._pet_action_deck = ["perk", "meow", "stretch"]

    results = [
        overlay._maybe_play_pet_settlement_reaction(
            snapshot
        )
        for snapshot in (
            _settlement_for_net("2201:A", 10_000),
            _settlement_for_net("2201:B", -200_000),
            _settlement_for_net("2201:A", 250_000),
        )
    ]

    assert results == [True, True, False]
    assert companion.actions == ["perk", "loss_droop"]
    assert companion.bubbles == ["好耶！", "呜…下局再来"]
    assert overlay._pet_action_deck == ["meow", "stretch"]
    assert (overlay._pet_touch_today, overlay._pet_touch_total) == (3, 9)


def test_pet_settlement_reaction_consumes_hidden_session_without_replay() -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    companion = _SettlementReactionCompanion()
    visible = {"value": False}
    overlay._pet_companion = companion
    overlay._pet_enabled = False
    overlay._is_pet_companion_visible = lambda: visible["value"]
    overlay._consumed_pet_settlement_reaction_sessions = set()
    overlay._pet_touch_today = 0
    overlay._pet_touch_total = 0
    overlay._pet_action_deck = ["perk", "meow"]

    session_a = _settlement_for_net("2201:hidden-A", -50_000)
    assert not overlay._maybe_play_pet_settlement_reaction(session_a)

    overlay._pet_enabled = True
    visible["value"] = True
    assert not overlay._maybe_play_pet_settlement_reaction(session_a)
    assert overlay._maybe_play_pet_settlement_reaction(
        _settlement_for_net("2201:visible-B", 200_000)
    )
    assert not overlay._maybe_play_pet_settlement_reaction(
        _settlement_for_net("2201:visible-B", -200_000)
    )

    assert companion.actions == ["big_win"]
    assert companion.bubbles == ["太棒啦！"]
    assert overlay._consumed_pet_settlement_reaction_sessions == {
        "2201:hidden-A",
        "2201:visible-B",
    }
    assert overlay._pet_action_deck == ["perk", "meow"]
    assert (overlay._pet_touch_today, overlay._pet_touch_total) == (0, 0)


def test_pet_settlement_feedback_toggle_consumes_without_delayed_replay() -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    companion = _SettlementReactionCompanion()
    overlay._pet_companion = companion
    overlay._pet_enabled = True
    overlay._pet_settlement_feedback_enabled = False
    overlay._is_pet_companion_visible = lambda: True
    overlay._consumed_pet_settlement_reaction_sessions = set()
    overlay._pet_touch_today = 2
    overlay._pet_touch_total = 8
    overlay._pet_action_deck = ["perk", "meow"]

    disabled_session = _settlement_for_net("2201:disabled-A", -50_000)
    assert not overlay._maybe_play_pet_settlement_reaction(disabled_session)
    assert overlay._consumed_pet_settlement_reaction_sessions == {
        "2201:disabled-A"
    }

    overlay._pet_settlement_feedback_enabled = True
    assert not overlay._maybe_play_pet_settlement_reaction(disabled_session)
    assert overlay._maybe_play_pet_settlement_reaction(
        _settlement_for_net("2201:enabled-B", -50_000)
    )
    assert companion.actions == ["loss_peek"]
    assert companion.bubbles == ["差一点点…"]
    assert (overlay._pet_touch_today, overlay._pet_touch_total) == (2, 8)


def test_pet_context_menu_labels_settlement_feedback_as_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    entries: list[tuple[str, dict[str, object]]] = []

    class Variable:
        def __init__(self, *, master: object, value: object) -> None:
            del master
            self.value = value

        def get(self) -> object:
            return self.value

        def set(self, value: object) -> None:
            self.value = value

    class Menu:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def add_command(self, **kwargs: object) -> None:
            entries.append(("command", kwargs))

        def add_separator(self) -> None:
            entries.append(("separator", {}))

        def add_radiobutton(self, **kwargs: object) -> None:
            entries.append(("radiobutton", kwargs))

        def add_checkbutton(self, **kwargs: object) -> None:
            entries.append(("checkbutton", kwargs))

        def tk_popup(self, x: int, y: int) -> None:
            assert (x, y) == (120, 240)

        def grab_release(self) -> None:
            return

    monkeypatch.setattr(module.tk, "Menu", Menu)
    monkeypatch.setattr(module.tk, "StringVar", Variable)
    monkeypatch.setattr(module.tk, "BooleanVar", Variable)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = object()
    overlay._pet_enabled = True
    overlay._pet_color = "ragdoll"
    overlay._pet_settlement_feedback_enabled = True
    overlay._current_pet_access = lambda: module.PetAccessState(
        "full",
        True,
        None,
        "eligible_plan",
        "365d",
    )
    overlay._set_pet_companion_enabled = lambda enabled: enabled
    saved: list[bool] = []
    overlay._schedule_ui_prefs_save = lambda: saved.append(True)

    assert overlay._show_pet_menu(x_root=120, y_root=240) == "break"
    labels = [str(kwargs.get("label", "")) for _kind, kwargs in entries]
    assert "结算反馈动画（测试）" in labels
    assert "  测试功能 · 后续可能调整为增值权益" in labels

    checkbutton = next(
        kwargs
        for kind, kwargs in entries
        if kind == "checkbutton"
        and kwargs.get("label") == "结算反馈动画（测试）"
    )
    variable = checkbutton["variable"]
    assert isinstance(variable, Variable)
    assert variable.get() is True
    variable.set(False)
    command = checkbutton["command"]
    assert callable(command)
    command()
    assert not overlay._pet_settlement_feedback_enabled
    assert saved == [True]


def test_pet_settlement_reaction_malformed_amount_does_not_touch_or_play() -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    companion = _SettlementReactionCompanion()
    overlay._pet_companion = companion
    overlay._pet_enabled = True
    overlay._is_pet_companion_visible = lambda: True
    overlay._consumed_pet_settlement_reaction_sessions = set()
    overlay._pet_touch_today = 4
    overlay._pet_touch_total = 12
    overlay._pet_action_deck = ["perk", "meow"]
    malformed = {
        **_profitable_session("2201:overflow"),
        "known_value_sum": 10**400,
    }

    assert not overlay._maybe_play_pet_settlement_reaction(malformed)
    assert companion.actions == []
    assert companion.bubbles == []
    assert overlay._consumed_pet_settlement_reaction_sessions == set()
    assert overlay._pet_action_deck == ["perk", "meow"]
    assert (overlay._pet_touch_today, overlay._pet_touch_total) == (4, 12)


def _signed_metered_activation(
    *,
    plan_kind: str,
    usage_kind: str,
    status: str,
    total: int,
    remaining: int,
) -> dict[str, object]:
    raw_status = status.rsplit("_", 1)[-1]
    server_now = 2_000_000_000
    visible = raw_status == "active"
    last_seq = -1 if raw_status == "available" else 0
    lease_exp = server_now + 15 if visible else server_now
    usage: dict[str, object] = {
        "kind": usage_kind,
        "status": raw_status,
        "total_seconds": total,
        "server_now": server_now,
        "entitlement_id": "a" * 64,
        "usage_revision": 1,
        "remaining_seconds": remaining,
        "last_seq": last_seq,
        "visible": visible,
        "visibility_lease_exp": lease_exp,
        "entitlement_exp": server_now + 86_400,
    }
    if usage_kind == "quota":
        usage["duration_days"] = 30 if plan_kind == "30d" else 60
    return {
        "mode": "trial" if plan_kind == "trial" else "paid",
        "run_full": True,
        "seconds_left": 30 * 86400,
        "plan_kind": plan_kind,
        "feature_entitlements": {"pet_companion": usage_kind},
        "feature_usage": {"pet_companion": usage},
    }


def test_pet_access_missing_signed_ledger_stays_pending_and_ignores_local_forgery() -> None:
    module = _pet_access_module()
    states = (
        (
            {
                "mode": "trial",
                "run_full": True,
                "plan_kind": "trial",
                "feature_entitlements": {"pet_companion": "none"},
            },
            600,
        ),
        (
            {
                "mode": "paid",
                "run_full": True,
                "plan_kind": "30d",
                "feature_entitlements": {"pet_companion": "quota"},
            },
            43_200,
        ),
        (
            {
                "mode": "paid",
                "run_full": True,
                "plan_kind": "90d",
                "feature_entitlements": {"pet_companion": "full"},
            },
            86_400,
        ),
    )
    for activation_state, forged_local_seconds in states:
        access = module.pet_access_from_activation(
            activation_state,
            trial_seconds_remaining=forged_local_seconds,
        )
        assert access.status == "entitlement_pending"
        assert not access.allowed
        assert access.seconds_left is None
        assert access.reason == "usage_ledger_pending"
        assert not access.can_persist_preferences


@pytest.mark.parametrize(
    ("status", "remaining", "allowed"),
    [
        ("trial_available", 600, False),
        ("trial_active", 599, True),
        ("trial_paused", 1, False),
        ("trial_exhausted", 0, False),
    ],
)
def test_signed_trial_contract_is_exact_for_free_and_30_day(
    status: str,
    remaining: int,
    allowed: bool,
) -> None:
    module = _pet_access_module()
    access = module.pet_access_from_activation(
        _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status=status,
            total=600,
            remaining=remaining,
        )
    )
    assert access.status == status
    assert access.allowed is allowed
    assert access.seconds_left == remaining
    assert access.total_seconds == 600
    assert access.usage_kind == "trial"
    assert not access.can_persist_preferences


@pytest.mark.parametrize(
    ("plan_kind", "duration_days", "total"),
    [
        ("30d", 30, 43_200),
        ("custom", 31, 86_400),
        ("custom", 60, 86_400),
        ("custom", 89, 86_400),
    ],
)
@pytest.mark.parametrize(
    ("status", "remaining_offset", "allowed"),
    [
        ("quota_available", 0, False),
        ("quota_active", -1, True),
        ("quota_paused", -1, False),
        ("quota_exhausted", None, False),
    ],
)
def test_signed_quota_contract_uses_authoritative_duration_and_balance(
    plan_kind: str,
    duration_days: int,
    total: int,
    status: str,
    remaining_offset: int | None,
    allowed: bool,
) -> None:
    module = _pet_access_module()
    remaining = 0 if remaining_offset is None else total + remaining_offset
    activation = _signed_metered_activation(
        plan_kind=plan_kind,
        usage_kind="quota",
        status=status,
        total=total,
        remaining=remaining,
    )
    activation["feature_usage"]["pet_companion"]["duration_days"] = duration_days
    access = module.pet_access_from_activation(
        activation
    )
    assert access.status == status
    assert access.allowed is allowed
    assert access.seconds_left == remaining
    assert access.total_seconds == total
    assert access.duration_days == duration_days
    assert access.usage_kind == "quota"
    assert module.format_pet_access_clock(access).count(":") == 2
    assert module.format_pet_quota_clock(43_200) == "12:00:00"
    assert module.format_pet_quota_clock(86_400) == "24:00:00"
    assert module.format_pet_quota_clock(1) == "00:00:01"
    assert module.format_pet_quota_clock(0) == "00:00:00"
    assert module.format_pet_access_clock(access) != "10:00"


def test_pet_access_full_upgrade_and_malformed_states_fail_closed() -> None:
    module = _pet_access_module()
    permanent = module.pet_access_from_activation(
        {
            "mode": "paid",
            "run_full": True,
            "plan_kind": "legacy_permanent",
            "feature_entitlements": {"pet_companion": "full"},
            "feature_usage": {
                "pet_companion": {
                    "kind": "full",
                    "status": "active",
                    "server_now": 2_000_000_000,
                }
            },
        },
    )
    assert permanent.status == "full" and permanent.allowed
    assert permanent.can_persist_preferences

    days_365 = module.pet_access_from_activation(
        {
            "mode": "paid",
            "run_full": True,
            "seconds_left": 42,
            "plan_kind": "365d",
            "feature_entitlements": {"pet_companion": "full"},
            "feature_usage": {
                "pet_companion": {
                    "kind": "full",
                    "status": "active",
                    "server_now": 2_000_000_000,
                    "duration_days": 365,
                    "entitlement_exp": 2_000_086_400,
                }
            },
        },
    )
    assert days_365.status == "full" and days_365.allowed
    assert days_365.can_persist_preferences

    ambiguous_finite = module.pet_access_from_activation(
        {"mode": "paid", "run_full": True, "seconds_left": 42},
    )
    assert ambiguous_finite.status == "entitlement_pending"
    assert not ambiguous_finite.allowed

    invalid_states = [
        {"mode": "paid", "run_full": False, "reason": "bad_signature"},
        {"mode": "paid", "run_full": True, "plan_kind": "unknown"},
        {
            "mode": "paid",
            "run_full": True,
            "plan_kind": "30d",
            "feature_entitlements": {"pet_companion": "quota"},
            "feature_usage": {"pet_companion": []},
        },
        _signed_metered_activation(
            plan_kind="30d",
            usage_kind="quota",
            status="quota_available",
            total=600,
            remaining=600,
        ),
        _signed_metered_activation(
            plan_kind="30d",
            usage_kind="quota",
            status="quota_active",
            total=43_200,
            remaining=True,
        ),
    ]
    for activation_state in invalid_states:
        access = module.pet_access_from_activation(activation_state)
        assert not access.allowed
        assert access.status in {"state_error", "locked"}
        assert not access.can_persist_preferences


def test_pet_frames_are_consistent_and_animated() -> None:
    module = _pet_module()

    for color in module.PET_COLOR_ORDER:
        frames = module.build_pet_frames(color)
        assert set(frames) >= {
            "normal",
            "breathe",
            "sleep_breathe",
            "blink",
            "perk",
            "bounce",
            "stretch",
            "sleep",
            "meow",
            "lick",
            "yawn",
            "chase",
        }
        assert {frame.size for frame in frames.values()} == {
            (module.PET_WINDOW_SIZE, module.PET_WINDOW_SIZE)
        }
        assert frames["normal"].tobytes() != frames["bounce"].tobytes()
        assert frames["normal"].tobytes() != frames["breathe"].tobytes()
        assert frames["normal"].tobytes() != frames["meow"].tobytes()
        assert frames["normal"].tobytes() != frames["stretch"].tobytes()
        assert frames["normal"].tobytes() != frames["sleep"].tobytes()
        assert frames["normal"].tobytes() != frames["chase"].tobytes()
        assert set(frames) >= {
            "wave",
            "roll",
            "scratch",
            "wave_enter_1",
            "wave_enter_2",
            "roll_enter_1",
            "roll_enter_2",
            "roll_enter_3",
            "scratch_enter_1",
            "scratch_enter_2",
        }
        assert set(frames) >= set(module.PET_EYE_VARIANT_POSES)
        assert {
            "tilt_left_eye",
            "tilt_right_eye",
            "squash_eye",
        } <= set(frames)
        for pose in module.PET_EYE_VARIANT_POSES:
            base_pose = "normal" if pose == "idle_eye" else pose.removesuffix("_eye")
            assert frames[pose].tobytes() != frames[base_pose].tobytes()
        assert module.pet_actions_for_color(color) == module.PET_ACTIONS
        if color == "orange":
            assert set(frames) >= set(module.PET_TRANSITION_POSE_ORDER)
        for action in module.PET_SETTLEMENT_ACTIONS:
            assert action in frames
            assert set(module.PET_AUTHORED_TRANSITIONS[action]) <= set(frames)


def test_tk_colorkey_frames_have_clean_binary_alpha_and_softer_outer_edge() -> None:
    module = _pet_module()

    for color in module.PET_COLOR_ORDER:
        source = module.build_pet_frames(color)["normal"]
        prepared = module._prepare_colorkey_frame(source)
        alpha = prepared.getchannel("A")
        assert set(alpha.getdata()) == {0, 255}
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getbbox() is not None
        assert alpha.getbbox()[2] - alpha.getbbox()[0] >= 150
        assert prepared.convert("RGB").tobytes() != source.convert("RGB").tobytes()


def test_orange_authored_transition_frames_enter_and_exit_in_reverse() -> None:
    module = _pet_module()
    frames = module.build_pet_frames("orange")

    for action, transition_poses in module.PET_AUTHORED_TRANSITIONS.items():
        entering = [
            module._authored_transition_frame_name(
                frames,
                "normal",
                action,
                index,
            )
            for index in range(len(transition_poses) + 2)
        ]
        exiting = [
            module._authored_transition_frame_name(
                frames,
                action,
                "normal",
                index,
            )
            for index in range(len(transition_poses) + 2)
        ]
        assert entering == ["normal", *transition_poses, action]
        assert exiting == [action, *reversed(transition_poses), "normal"]
        keyframes = ("normal", *transition_poses, action)
        assert len({frames[name].tobytes() for name in keyframes}) == len(keyframes)
        assert module._transition_step_count(
            frames,
            "normal",
            action,
        ) == len(keyframes)

    navy_frames = module.build_pet_frames("navy")
    assert module._authored_transition_frame_name(
        navy_frames,
        "normal",
        "meow",
        1,
    ) is None


def test_settlement_authored_transitions_reverse_cleanly_for_every_coat() -> None:
    module = _pet_module()

    for color in module.PET_COLOR_ORDER:
        frames = module.build_pet_frames(color)
        for action in module.PET_SETTLEMENT_ACTIONS:
            transition_poses = module.PET_AUTHORED_TRANSITIONS[action]
            entering = [
                module._authored_transition_frame_name(
                    frames,
                    "normal",
                    action,
                    index,
                )
                for index in range(7)
            ]
            exiting = [
                module._authored_transition_frame_name(
                    frames,
                    action,
                    "normal",
                    index,
                )
                for index in range(7)
            ]
            assert entering == ["normal", *transition_poses, action]
            assert exiting == [action, *reversed(transition_poses), "normal"]


def test_action_timing_is_gentle_and_repetition_is_intentional(
    monkeypatch,
) -> None:
    module = _pet_module()

    assert module._transition_frame_duration("perk", "normal", "perk") == 64
    assert module._transition_frame_duration("meow", "normal", "meow") == 72
    assert module._transition_frame_duration("stretch", "normal", "stretch") == 82
    assert module._transition_frame_duration("sleep", "normal", "sleep") == 90
    assert module._transition_frame_duration("wave", "normal", "wave") == 70
    assert module._transition_frame_duration("roll", "normal", "roll") == 88
    assert module._transition_frame_duration("scratch", "normal", "scratch") == 78
    assert module._transition_frame_duration("loss_peek", "normal", "loss_peek") == 108
    assert module._transition_frame_duration("loss_droop", "normal", "loss_droop") == 112
    assert module._transition_frame_duration("big_win", "normal", "big_win") == 96
    assert module._transition_frame_duration("meow", "meow", "normal") == 66

    for action in ("meow", "sleep", "stretch", "yawn"):
        action_frames = [
            name
            for name, _duration in module._SEQUENCES[action]
            if name != "normal"
        ]
        assert action_frames == [action]

    assert [name for name, _duration in module._SEQUENCES["lick"]].count("lick") == 2
    assert [name for name, _duration in module._SEQUENCES["eat"]].count("squash") == 2
    assert [name for name, _duration in module._SEQUENCES["scratch"]].count("scratch") == 2
    assert {"left", "right"} <= {
        name for name, _duration in module._SEQUENCES["chase"]
    }

    frames = module.build_pet_frames("ragdoll")
    monkeypatch.setattr(module.random, "random", lambda: 0.0)
    for mode, (_anchor, eye_frame, _duration) in module._ACTION_EYE_BEATS.items():
        lively = module._with_action_eye_beat(
            mode,
            module._SEQUENCES[mode],
            frames,
        )
        assert eye_frame in {name for name, _duration in lively}
    assert "blink" in {
        name for name, _duration in module._awake_idle_sequence(frames)
    }

    monkeypatch.setattr(module.random, "random", lambda: 0.5)
    assert "idle_eye" in {
        name for name, _duration in module._awake_idle_sequence(frames)
    }

    monkeypatch.setattr(module.random, "random", lambda: 0.99)
    for mode in module._ACTION_EYE_BEATS:
        assert module._with_action_eye_beat(
            mode,
            module._SEQUENCES[mode],
            frames,
        ) == module._SEQUENCES[mode]
    assert {
        name for name, _duration in module._awake_idle_sequence(frames)
    } == {"normal", "breathe"}

    companion = module.PetCompanionOverlay.__new__(module.PetCompanionOverlay)
    companion._pil_frames = frames
    companion._idle_pose = "normal"
    companion._current_frame_name = "normal"
    meow_steps = companion._build_sequence_steps("meow")
    assert meow_steps[0][0] == "transition"
    assert ("frame", "meow", 460) in meow_steps
    assert sum(duration for _kind, _payload, duration in meow_steps) >= 1_300
    for action in module.PET_SETTLEMENT_ACTIONS:
        steps = companion._build_sequence_steps(action)
        authored_transitions = [
            payload
            for kind, payload, _duration in steps
            if kind == "transition"
        ]
        assert len(authored_transitions) == 14
        assert ("frame", action, module._SEQUENCES[action][1][1]) in steps
        assert sum(duration for _kind, _payload, duration in steps) >= 2_500

    sleeping = module.PetCompanionOverlay.__new__(module.PetCompanionOverlay)
    sleeping._pil_frames = frames
    sleeping._idle_pose = "sleep"
    sleeping.reduced_motion = True
    rendered: list[str] = []
    sleeping._render_frame = rendered.append
    sleeping.play("loss_droop")
    assert sleeping._idle_pose == "sleep"
    assert rendered == ["sleep"]


def test_idle_animation_keeps_ground_line_and_actions_recover_to_normal() -> None:
    module = _pet_module()

    assert module.PET_TRANSITION_STEPS == 4
    for color in module.PET_COLOR_ORDER:
        frames = module.build_pet_frames(color)
        normal_bottom = frames["normal"].getchannel("A").getbbox()[3]
        breathe_bottom = frames["breathe"].getchannel("A").getbbox()[3]
        assert breathe_bottom == normal_bottom

        companion = module.PetCompanionOverlay.__new__(module.PetCompanionOverlay)
        companion._pil_frames = frames
        companion._idle_pose = "normal"
        companion._current_frame_name = "normal"
        idle_steps = companion._build_sequence_steps("idle")
        assert all(kind == "frame" for kind, _payload, _duration in idle_steps)
        idle_bottoms = [
            frames[str(payload)].getchannel("A").getbbox()[3]
            for kind, payload, _duration in idle_steps
            if kind == "frame"
        ]
        assert max(idle_bottoms) - min(idle_bottoms) <= 1
        assert idle_steps[-1][1] == "normal"

        for mode in module._SEQUENCES:
            if mode == "idle":
                continue
            if any(
                frame_name not in frames
                for frame_name, _duration in module._SEQUENCES[mode]
            ):
                continue
            companion._idle_pose = "normal"
            companion._current_frame_name = "normal"
            steps = companion._build_sequence_steps(mode)
            expected_final = "sleep" if mode == "sleep" else "normal"
            assert steps[-1][0:2] == ("frame", expected_final)
            transition_edges: dict[tuple[str, str], list[int]] = {}
            for kind, payload, _duration in steps:
                if kind == "transition":
                    source, target, index = payload
                    transition_edges.setdefault((source, target), []).append(index)
            for (source, target), indices in transition_edges.items():
                step_count = module._transition_step_count(
                    frames,
                    source,
                    target,
                )
                assert len(indices) % step_count == 0
                for start in range(0, len(indices), step_count):
                    assert indices[start : start + step_count] == list(
                        range(step_count)
                    )
            for kind, payload, _duration in steps:
                if kind != "transition":
                    continue
                source, target, index = payload
                assert module._needs_pose_transition(mode, source, target)
                assert _duration == module._transition_frame_duration(
                    mode,
                    source,
                    target,
                )
                rendered = module._build_transition_frame(
                    frames,
                    source,
                    target,
                    index,
                )
                expected = frames[source if index == 0 else target]
                assert abs(
                    rendered.getchannel("A").getbbox()[3]
                    - expected.getchannel("A").getbbox()[3]
                ) <= 1
            expected_holds = [duration for _frame_name, duration in module._SEQUENCES[mode]]
            actual_holds = [
                duration
                for kind, _payload, duration in steps
                if kind == "frame"
            ]
            assert sum(actual_holds) >= sum(expected_holds)

        companion._idle_pose = "sleep"
        companion._current_frame_name = "sleep"
        sleep_idle_steps = companion._build_sequence_steps("idle")
        assert all(kind == "frame" for kind, _payload, _duration in sleep_idle_steps)
        assert {
            str(payload)
            for kind, payload, _duration in sleep_idle_steps
            if kind == "frame"
        } == {"sleep", "sleep_breathe"}
        sleep_bottoms = [
            frames[str(payload)].getchannel("A").getbbox()[3]
            for kind, payload, _duration in sleep_idle_steps
            if kind == "frame"
        ]
        assert max(sleep_bottoms) - min(sleep_bottoms) <= 1


def test_real_tk_slow_frame_build_keeps_constructor_and_heartbeat_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    original_build = module.build_pet_frames
    build_threads: list[int] = []

    def _slow_build(color):
        build_threads.append(threading.get_ident())
        time.sleep(0.16)
        return original_build(color)

    monkeypatch.setattr(module, "build_pet_frames", _slow_build)
    root = _new_root(tk)
    root.withdraw()
    main_thread = threading.get_ident()
    heartbeat_delays: list[float] = []
    pet = None
    try:
        heartbeat_started = time.perf_counter()
        root.after(
            10,
            lambda: heartbeat_delays.append(time.perf_counter() - heartbeat_started),
        )
        constructor_started = time.perf_counter()
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        constructor_elapsed = time.perf_counter() - constructor_started

        assert constructor_elapsed < 0.08
        assert _pump_tk_until(root, lambda: bool(heartbeat_delays), timeout=0.25)
        assert heartbeat_delays[0] < 0.08
        assert _pump_tk_until(root, lambda: len(pet._pil_frames) > 40)
        assert build_threads == [build_threads[0]]
        assert build_threads[0] != main_thread
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_slow_colorkey_prepare_never_blocks_touch_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.withdraw()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        root.update()
        original_prepare = module._prepare_colorkey_frame
        prepare_threads: list[int] = []

        def _slow_prepare(image):
            prepare_threads.append(threading.get_ident())
            time.sleep(0.12)
            return original_prepare(image)

        monkeypatch.setattr(module, "_prepare_colorkey_frame", _slow_prepare)
        heartbeat_delays: list[float] = []
        heartbeat_started = time.perf_counter()
        root.after(
            10,
            lambda: heartbeat_delays.append(time.perf_counter() - heartbeat_started),
        )
        play_started = time.perf_counter()
        pet.meow()
        play_elapsed = time.perf_counter() - play_started

        assert play_elapsed < 0.04
        assert _pump_tk_until(
            root,
            lambda: bool(heartbeat_delays) and bool(prepare_threads),
        )
        assert heartbeat_delays[0] < 0.08
        assert prepare_threads[0] != threading.get_ident()
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_frame_generation_drops_stale_same_color_and_keeps_latest_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    original_build = module.build_pet_frames
    first_started = threading.Event()
    release_first = threading.Event()
    build_calls: list[str] = []

    def _controlled_build(color):
        build_calls.append(str(color))
        call_index = len(build_calls)
        if call_index == 1:
            first_started.set()
            assert release_first.wait(2.0)
        frames = original_build(color)
        frames[f"audit_generation_{call_index}"] = frames["normal"].copy()
        return frames

    monkeypatch.setattr(module, "build_pet_frames", _controlled_build)
    root = _new_root(tk)
    root.withdraw()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        assert first_started.wait(1.0)
        pet.show()
        pet.set_color("ragdoll")
        pet.set_color("orange")
        pet.meow()
        pet.sleep()
        release_first.set()

        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current()
            and "audit_generation_2" in pet._pil_frames,
            timeout=5.0,
        )
        assert build_calls == ["orange", "orange"]
        assert "audit_generation_1" not in pet._pil_frames
        assert pet._frame_generation == 3
        assert _pump_tk_until(
            root,
            lambda: pet._mode == "sleep"
            and pet._current_frame_name == "sleep",
            timeout=4.0,
        )
    finally:
        release_first.set()
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_frame_and_transition_caches_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    monkeypatch.setattr(module, "PET_TK_FRAME_CACHE_LIMIT", 4)
    monkeypatch.setattr(module, "PET_TK_TRANSITION_CACHE_LIMIT", 3)
    monkeypatch.setattr(module, "PET_PREPARED_TRANSITION_CACHE_LIMIT", 3)
    root = _new_root(tk)
    root.withdraw()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=True,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        assert _pump_tk_until(root, pet._frames_are_current)
        for name in ("normal", "blink", "perk", "meow", "stretch", "sleep"):
            pet._request_static_render(name)
            assert _pump_tk_until(
                root,
                lambda name=name: pet._current_frame_name == name,
            )
            assert len(pet._tk_frames) <= 4

        for step in range(4):
            task = ("transition", "normal", "meow", step)
            pet._waiting_tk_task = task
            pet._request_tk_task(task)
            assert _pump_tk_until(root, lambda task=task: pet._task_is_cached(task))
            assert len(pet._transition_tk_frames) <= 3
            assert len(pet._prepared_transition_frames) <= 3
        assert len(pet._transition_tk_frames) == 3
        assert len(pet._prepared_transition_frames) == 3
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_destroy_releases_tk_cache_before_worker_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    original_build = module.build_pet_frames
    second_started = threading.Event()
    release_second = threading.Event()
    build_count = [0]

    def _blocking_second_build(color):
        build_count[0] += 1
        if build_count[0] == 2:
            second_started.set()
            assert release_second.wait(2.0)
        return original_build(color)

    monkeypatch.setattr(module, "build_pet_frames", _blocking_second_build)
    root = _new_root(tk)
    root.withdraw()
    pet = None
    released_on: list[int] = []
    main_thread = threading.get_ident()

    class _TkCacheProbe:
        def __init__(self) -> None:
            self.cycle = self

        def __del__(self):
            released_on.append(threading.get_ident())

    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=True,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        assert _pump_tk_until(root, pet._frames_are_current)
        pet._tk_frames["audit_probe"] = _TkCacheProbe()
        pet.set_color("navy")
        assert second_started.wait(1.0)
        worker = pet._frame_worker
        pet.destroy()
        pet = None
        assert released_on == [main_thread]
        release_second.set()
        worker.join(timeout=3.0)
        assert not worker.is_alive()
    finally:
        release_second.set()
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_gc_positive_control_cyclic_finalizer_runs_in_collecting_worker() -> None:
    import gc
    import threading

    gc.collect()
    released_on: list[int] = []
    collector_thread: list[int] = []
    main_thread = threading.get_ident()

    class _CyclicProbe:
        def __init__(self) -> None:
            self.cycle = self

        def __del__(self) -> None:
            released_on.append(threading.get_ident())

    probe = _CyclicProbe()
    del probe

    def _collect() -> None:
        collector_thread.append(threading.get_ident())
        gc.collect()

    worker = threading.Thread(target=_collect, name="pet-gc-positive-control")
    worker.start()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert collector_thread and collector_thread[0] != main_thread
    assert released_on == collector_thread


def test_real_tk_destroy_drains_tk_cycles_before_later_worker_allocation_and_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gc
    import threading

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.withdraw()
    pet = None
    real_collect = gc.collect
    collect_calls: list[int] = []
    main_thread = threading.get_ident()

    def _tracked_collect() -> int:
        collect_calls.append(threading.get_ident())
        return real_collect()

    monkeypatch.setattr(module.gc, "collect", _tracked_collect)
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(root, pet._frames_are_current)
        pet.show_touch_feedback("安全反馈")
        pet.meow()
        root.update()
        frame_worker = pet._frame_worker

        pet.destroy()
        pet = None

        assert collect_calls == [main_thread]
        assert not frame_worker.is_alive()

        allocation_errors: list[BaseException] = []
        allocation_thread: list[int] = []

        def _allocate_and_collect() -> None:
            try:
                allocation_thread.append(threading.get_ident())
                for index in range(80):
                    image = Image.new("RGBA", (48, 48), (index, 64, 96, 255))
                    image.resize((96, 96), Image.Resampling.LANCZOS)
                real_collect()
            except BaseException as exc:  # pragma: no cover - asserted below
                allocation_errors.append(exc)

        allocator = threading.Thread(
            target=_allocate_and_collect,
            name="pet-post-destroy-allocation-control",
        )
        allocator.start()
        allocator.join(timeout=3.0)

        assert not allocator.is_alive()
        assert not allocation_errors
        assert allocation_thread and allocation_thread[0] != main_thread
        assert collect_calls == [main_thread]
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_rapid_touches_reuse_feedback_and_keep_heartbeat_at_150_percent() -> None:
    import time

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.geometry("320x200+120+80")
    root.update_idletasks()
    root.tk.call("tk", "scaling", 1.5)
    pet = None
    heartbeat_after: list[str | None] = [None]
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(root, pet._frames_are_current)
        actions = tuple(module.pet_actions_for_color("orange"))
        state = {"count": 0, "done": False}
        feedback_ids: set[int] = set()
        handler_ms: list[float] = []
        heartbeat_ms: list[float] = []
        last_heartbeat = [time.perf_counter()]

        def _heartbeat() -> None:
            now = time.perf_counter()
            heartbeat_ms.append((now - last_heartbeat[0]) * 1000.0)
            last_heartbeat[0] = now
            if not state["done"]:
                heartbeat_after[0] = root.after(5, _heartbeat)

        def _touch() -> None:
            started = time.perf_counter()
            action = actions[state["count"] % len(actions)]
            getattr(pet, action)()
            pet.show_touch_feedback(f"摸摸 {state['count']} +1")
            handler_ms.append((time.perf_counter() - started) * 1000.0)
            assert pet._touch_feedback is not None
            feedback_ids.add(id(pet._touch_feedback))
            state["count"] += 1
            if state["count"] < 120:
                root.after(1, _touch)
            else:
                state["done"] = True

        root.after(5, _heartbeat)
        root.after(1, _touch)
        assert _pump_tk_until(root, lambda: bool(state["done"]), timeout=8.0)
        assert state["count"] == 120
        assert feedback_ids and len(feedback_ids) == 1
        assert max(handler_ms) < 80.0
        assert heartbeat_ms and max(heartbeat_ms) < 80.0
        feedback = pet._touch_feedback
        assert feedback is not None and feedback.winfo_viewable()
        pet.hide_touch_feedback()
        root.update_idletasks()
        assert pet._touch_feedback is feedback
        assert not feedback.winfo_viewable()
    finally:
        if heartbeat_after[0] is not None:
            try:
                root.after_cancel(heartbeat_after[0])
            except tk.TclError:
                pass
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("scaling", [1.0, 1.25, 1.5, 1.75])
def test_real_tk_product_interaction_is_immediate_and_reaches_action_pose(
    monkeypatch: pytest.MonkeyPatch,
    scaling: float,
) -> None:
    tk = pytest.importorskip("tkinter")
    pet_module = _pet_module()
    overlay_module = _overlay_module()
    root = _new_root(tk)
    root.geometry("420x260+120+80")
    root.tk.call("tk", "scaling", scaling)
    root.update_idletasks()
    pet = None
    try:
        pet = pet_module.PetCompanionOverlay(
            root,
            color="ragdoll",
            position=(560, 100),
            topmost=True,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(root, pet._frames_are_current)
        overlay = object.__new__(overlay_module.AhmadTkOverlay)
        overlay._pet_companion = pet
        overlay._record_pet_touch = lambda: (1, 1)
        overlay._next_pet_companion_action = lambda: "meow"
        monkeypatch.setitem(
            overlay_module.PET_ACTION_BUBBLES,
            "meow",
            ("喵～",),
        )

        overlay._interact_with_pet_companion()
        root.update_idletasks()

        assert pet._touch_feedback is not None
        assert pet._touch_feedback.winfo_viewable()
        assert pet._touch_feedback_label is not None
        assert pet._touch_feedback_label.cget("text") == "互动 · 喵～  +1"
        assert _pump_tk_until(
            root,
            lambda: pet._current_frame_name == "meow",
            timeout=3.0,
        )
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_background_frame_failure_is_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()

    def _missing_assets(_color):
        raise OSError("synthetic missing asset")

    monkeypatch.setattr(module, "build_pet_frames", _missing_assets)
    root = _new_root(tk)
    root.geometry("320x200+120+80")
    root.update_idletasks()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="ragdoll",
            position=(40, 40),
            topmost=False,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(root, lambda: pet._frame_build_failed)
        assert pet.transparency_state == "ok"
        assert pet.window.winfo_viewable()
        assert not pet.canvas.find_withtag("sprite")
        assert not pet._pil_frames
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_transition_frame_failure_advances_to_static_action_pose() -> None:
    module = _pet_module()
    pet = module.PetCompanionOverlay.__new__(module.PetCompanionOverlay)
    pet._destroyed = False
    pet._frame_poll_after_id = None
    pet._frame_generation = 4
    pet._render_generation = 7
    pet._frames_color = "ragdoll"
    pet.color = "ragdoll"
    pet._waiting_tk_task = ("transition", "normal", "meow", 0)
    pet._tick_waiting = True
    pet._sequence_steps = [
        ("transition", ("normal", "meow", 0), 72),
        ("transition", ("normal", "meow", 1), 72),
        ("frame", "meow", 460),
        ("transition", ("meow", "normal", 0), 66),
    ]
    pet._frame_worker_state = module._PetFrameWorkerState()
    ticks: list[str] = []
    polls: list[str] = []
    pet._tick = lambda: ticks.append("tick")
    pet._schedule_frame_poll = lambda: polls.append("poll")
    pet._frame_worker_state.results.put(
        ("transition_error", 4, 7, ("normal", "meow", 0), None)
    )

    pet._poll_frame_results()

    assert pet._waiting_tk_task is None
    assert not pet._tick_waiting
    assert pet._sequence_steps[0] == ("frame", "meow", 460)
    assert ticks == ["tick"]
    assert polls == ["poll"]


def test_real_tk_companion_show_interact_recolor_and_hide() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.geometry("420x260+120+80")
    root.update_idletasks()
    main_was_mapped = bool(root.winfo_ismapped())
    clicks: list[str] = []
    moved: list[tuple[int, int]] = []
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="orange",
            position=(560, 100),
            topmost=True,
            reduced_motion=False,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: clicks.append("click"),
            on_context_menu=lambda _x, _y: None,
            on_position_changed=moved.append,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current() and pet._after_id is not None,
        )
        assert bool(root.winfo_ismapped()) is main_was_mapped
        assert pet.window.winfo_viewable()
        assert pet._after_id is not None
        assert pet._idle_action_after_id is not None

        first_idle_timer = pet._idle_action_after_id
        pet.meow()
        root.update()
        assert any(step[0] == "transition" for step in pet._sequence_steps)
        assert pet._idle_action_after_id is not None
        assert pet._idle_action_after_id != first_idle_timer
        for step, expected in ((0, "normal"), (1, "normal"), (2, "meow")):
            task = ("transition", "normal", "meow", step)
            pet._waiting_tk_task = task
            pet._request_tk_task(task)
            assert _pump_tk_until(root, lambda task=task: pet._task_is_cached(task))
            assert pet._render_transition("normal", "meow", step)
            assert pet._current_frame_name == expected
        pet.sleep()
        assert _pump_tk_until(root, lambda: pet._idle_pose == "sleep")
        assert pet._idle_pose == "sleep"
        assert pet._idle_action_after_id is None
        pet.set_color("navy")
        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current()
            and pet._current_frame_name == "sleep",
        )
        assert pet.color == "navy"
        assert pet._mode == "sleep"
        assert pet._current_frame_name == "sleep"
        assert pet.canvas.find_withtag("sprite")
        pet.meow()
        root.update()
        assert pet._idle_pose == "normal"
        assert pet._idle_action_after_id is not None
        pet.set_color("orange")
        assert _pump_tk_until(root, pet._frames_are_current)
        for action in module.PET_OPTIONAL_ACTIONS:
            getattr(pet, action)()
            root.update()
            assert pet._mode == action

        pet.hide()
        root.update()
        assert not pet.window.winfo_viewable()
        assert bool(root.winfo_ismapped()) is main_was_mapped
        assert pet._after_id is None
        assert pet._idle_action_after_id is None
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
@pytest.mark.parametrize(
    ("fault", "expected_state"),
    [
        ("set", "set_failed"),
        ("readback", "readback_mismatch"),
        ("native", "native_key_mismatch"),
    ],
)
def test_real_tk_transparency_faults_keep_pet_withdrawn(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    expected_state: str,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.geometry("420x320+100+100")
    root.update()
    pet = None
    original_attributes = tk.Toplevel.attributes

    def _faulted_attributes(window, *args):
        if args and args[0] == "-transparentcolor":
            if fault == "set" and len(args) == 2:
                raise tk.TclError("synthetic private detail")
            if fault == "readback" and len(args) == 1:
                return "#ffffff"
        return original_attributes(window, *args)

    if fault in {"set", "readback"}:
        monkeypatch.setattr(tk.Toplevel, "attributes", _faulted_attributes)
    if fault == "native":
        monkeypatch.setattr(
            module,
            "_apply_and_verify_native_colorkey",
            lambda _hwnd, _colorref, **_kwargs: False,
        )
    try:
        pet = _new_pet(module, root)
        pet.show()
        root.update()

        assert pet.transparency_state == expected_state
        assert not pet.window.winfo_viewable()
        with pytest.raises(AttributeError):
            pet.transparency_state = "ok"
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_windows_transparency_helper_re_resolves_recreated_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _pet_module()

    class _Window:
        def __init__(self) -> None:
            self.value = ""

        def attributes(self, name, *values):
            assert name == "-transparentcolor"
            if values:
                self.value = str(values[0])
                return None
            return self.value

    handles = iter((4101, 4102))
    native_calls: list[int] = []
    monkeypatch.setattr(module, "_windows_toplevel_hwnd", lambda _window: next(handles))
    monkeypatch.setattr(
        module,
        "_apply_and_verify_native_colorkey",
        lambda hwnd, _colorref, **_kwargs: native_calls.append(hwnd) or True,
    )
    window = _Window()

    assert module._apply_and_verify_windows_transparency(window) == "ok"
    assert module._apply_and_verify_windows_transparency(window) == "ok"
    assert native_calls == [4101, 4102]
    assert set(module.PET_TRANSPARENCY_STATES) == {
        "ok",
        "unsupported",
        "set_failed",
        "readback_mismatch",
        "native_key_mismatch",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_real_tk_transparency_self_heals_native_key_with_target_pixels() -> None:
    import ctypes
    import ctypes.wintypes
    import time

    from PIL import ImageGrab

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.configure(bg="#244466")
    root.geometry("520x520+100+100")
    root.attributes("-topmost", True)
    root.update()
    pet = None

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    get_layered = user32.GetLayeredWindowAttributes
    get_layered.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_layered.restype = ctypes.c_bool
    set_layered = user32.SetLayeredWindowAttributes
    set_layered.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_ubyte,
        ctypes.c_uint32,
    ]
    set_layered.restype = ctypes.c_bool
    get_window_long = user32.GetWindowLongW
    get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_window_long.restype = ctypes.c_long
    set_window_long = user32.SetWindowLongW
    set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    set_window_long.restype = ctypes.c_long

    def _native_values(hwnd: int) -> tuple[int, int, int] | None:
        key = ctypes.c_uint32()
        alpha = ctypes.c_ubyte()
        flags = ctypes.c_uint32()
        if not get_layered(
            hwnd,
            ctypes.byref(key),
            ctypes.byref(alpha),
            ctypes.byref(flags),
        ):
            return None
        return key.value, alpha.value, flags.value

    def _target_corner(hwnd: int) -> tuple[int, int, int]:
        rect = ctypes.wintypes.RECT()
        assert dwmapi.DwmGetWindowAttribute(
            hwnd,
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS: physical pixels, unlike Tk geometry.
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        ) == 0
        image = ImageGrab.grab()
        return image.getpixel((rect.left + 8, rect.top + 8))[:3]

    try:
        pet = _new_pet(
            module,
            root,
            position=(180, 180),
            topmost=True,
            reduced_motion=True,
        )
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok"
            and pet.window.winfo_viewable()
            and pet._frames_are_current(),
        )
        hwnd = module._windows_toplevel_hwnd(pet.window)
        expected_key = 0x00030201
        assert _native_values(hwnd) is not None
        assert _native_values(hwnd)[0] == expected_key
        assert get_window_long(hwnd, -20) & 0x00080000  # WS_EX_LAYERED
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)

        key, alpha, flags = _native_values(hwnd)
        assert set_layered(hwnd, 0x0000FF00, alpha, flags)
        user32.RedrawWindow(hwnd, None, None, 0x0085)
        time.sleep(0.08)
        assert _native_values(hwnd)[0] == 0x0000FF00
        assert _target_corner(hwnd) == (0x01, 0x02, 0x03)

        pet.show()
        assert _pump_tk_until(
            root,
            lambda: _native_values(hwnd) is not None
            and _native_values(hwnd)[0] == expected_key,
        )
        time.sleep(0.08)
        assert pet.transparency_state == "ok"
        assert get_window_long(hwnd, -20) & 0x00080000
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)

        set_window_long(hwnd, -20, get_window_long(hwnd, -20) & ~0x00080000)
        assert not get_window_long(hwnd, -20) & 0x00080000
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: bool(get_window_long(hwnd, -20) & 0x00080000)
            and _native_values(hwnd) is not None
            and _native_values(hwnd)[0] == expected_key,
        )
        assert pet.transparency_state == "ok"
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_real_tk_show_never_exposes_bad_key_while_final_verification_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import ctypes.wintypes
    import time

    from PIL import ImageGrab

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.configure(bg="#244466")
    root.geometry("520x520+100+100")
    root.attributes("-topmost", True)
    root.update()
    pet = None
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    get_layered = user32.GetLayeredWindowAttributes
    get_layered.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_layered.restype = ctypes.c_bool
    set_layered = user32.SetLayeredWindowAttributes
    set_layered.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_ubyte,
        ctypes.c_uint32,
    ]
    set_layered.restype = ctypes.c_bool

    def _native_values(hwnd: int) -> tuple[int, int, int] | None:
        key = ctypes.c_uint32()
        alpha = ctypes.c_ubyte()
        flags = ctypes.c_uint32()
        if not get_layered(
            hwnd,
            ctypes.byref(key),
            ctypes.byref(alpha),
            ctypes.byref(flags),
        ):
            return None
        return key.value, alpha.value, flags.value

    def _target_corner(hwnd: int) -> tuple[int, int, int]:
        rect = ctypes.wintypes.RECT()
        assert dwmapi.DwmGetWindowAttribute(
            hwnd,
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS uses physical screenshot pixels.
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        ) == 0
        image = ImageGrab.grab()
        return image.getpixel((rect.left + 8, rect.top + 8))[:3]

    held_idle: list[tuple[object, tuple[object, ...]]] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _release_idle() -> int:
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            callback(*args)
            released += 1
            assert released < 20, "transparency idle callbacks must stay bounded"
        return released

    try:
        pet = _new_pet(
            module,
            root,
            position=(180, 180),
            topmost=True,
            reduced_motion=True,
        )
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok"
            and pet.window.winfo_viewable()
            and pet._frames_are_current(),
        )
        hwnd = module._windows_toplevel_hwnd(pet.window)
        pet.hide()
        root.update()
        _key, alpha, flags = _native_values(hwnd)
        assert set_layered(hwnd, 0x0000FF00, alpha, flags)
        assert str(pet.window.attributes("-transparentcolor")) == "#010203"
        assert _native_values(hwnd)[0] == 0x0000FF00

        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show()
        root.update()
        assert pet.window.winfo_viewable()
        assert pet.transparency_state == "unsupported"
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)

        pet.hide()
        pet.show()
        root.update()
        assert pet.transparency_state == "unsupported"
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)
        assert _release_idle() > 0
        root.update()
        time.sleep(0.05)
        assert pet.transparency_state == "ok"
        assert pet.window.winfo_viewable()
        assert _native_values(hwnd)[0] == 0x00030201
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)

        pet.hide()
        root.update()
        _key, alpha, flags = _native_values(hwnd)
        assert set_layered(hwnd, 0x0000FF00, alpha, flags)
        pet.show()
        root.update()
        assert pet.transparency_state == "unsupported"
        assert _target_corner(hwnd) == (0x24, 0x44, 0x66)
        monkeypatch.setattr(
            module,
            "_apply_and_verify_native_colorkey",
            lambda _hwnd, _colorref, **_kwargs: False,
        )
        assert _release_idle() > 0
        root.update()
        assert pet.transparency_state == "native_key_mismatch"
        assert not pet.window.winfo_viewable()
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_real_tk_transparency_survives_logical_scaling_colors_and_parent_restore() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.geometry("520x420+100+100")
    root.update()
    pet = None
    try:
        pet = _new_pet(module, root, color="orange", reduced_motion=True)
        pet.show()
        assert _pump_tk_until(root, lambda: pet.transparency_state == "ok")

        # These are four Tk logical-scaling arms on one physical display, not a
        # claim about four physical-DPI machines.
        for scaling, color in (
            (1.0, "orange"),
            (1.25, "ragdoll"),
            (1.5, "navy"),
            (1.75, "orange"),
        ):
            root.tk.call("tk", "scaling", scaling)
            pet.set_color(color)
            assert _pump_tk_until(
                root,
                lambda color=color: pet.color == color
                and pet._frames_are_current()
                and pet.transparency_state == "ok",
            )

        pet.hide()
        root.withdraw()
        root.update()
        assert not pet.window.winfo_viewable()
        pet.show()
        root.update()
        root.deiconify()
        root.lift()
        root.focus_force()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok" and pet.window.winfo_viewable(),
        )
        pet.set_color("ragdoll")
        assert _pump_tk_until(root, pet._frames_are_current)
        pet.set_color("orange")
        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current() and pet.transparency_state == "ok",
        )
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native Z-order contract")
def test_real_tk_companion_remains_above_main_after_main_activation() -> None:
    import ctypes
    import time

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.overrideredirect(True)
    root.geometry("420x260+120+80")
    root.attributes("-topmost", True)
    pet = None
    toolwindow_calls: list[str] = []

    def _apply_toolwindow(window) -> None:
        toolwindow_calls.append("applied")
        window.attributes("-toolwindow", True)

    def _z_order() -> list[int]:
        rows: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        @callback_type
        def _collect(hwnd, _lparam):
            rows.append(int(hwnd))
            return True

        ctypes.windll.user32.EnumWindows(_collect, 0)
        return rows

    def _owner(hwnd: int) -> int:
        get_window = ctypes.windll.user32.GetWindow
        get_window.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        get_window.restype = ctypes.c_void_p
        return int(get_window(hwnd, 4) or 0)  # GW_OWNER

    try:
        pet = module.PetCompanionOverlay(
            root,
            color="ragdoll",
            position=(420, 240),
            topmost=True,
            reduced_motion=True,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
            apply_toolwindow=_apply_toolwindow,
        )
        pet.show()
        pet.show_bubble("今日撸猫 0 次 · 累计 0 次", duration_ms=5_000)
        pet.show_touch_feedback("摸摸  +1", duration_ms=5_000)
        root.update()

        root_hwnd = module._windows_toplevel_hwnd(root)
        pet_hwnd = module._windows_toplevel_hwnd(pet.window)
        assert pet.transparency_state == "ok"
        assert len(toolwindow_calls) >= 2
        assert _owner(pet_hwnd) == root_hwnd
        assert pet._bubble is not None
        assert pet._touch_feedback is not None
        bubble_hwnd = module._windows_toplevel_hwnd(pet._bubble)
        feedback_hwnd = module._windows_toplevel_hwnd(pet._touch_feedback)
        assert _owner(bubble_hwnd) == pet_hwnd
        assert _owner(feedback_hwnd) == pet_hwnd

        root.lift()
        root.focus_force()
        root.update()
        time.sleep(0.05)
        root.update()

        rows = _z_order()
        assert rows.index(pet_hwnd) < rows.index(root_hwnd)
        assert rows.index(bubble_hwnd) < rows.index(pet_hwnd)
        assert rows.index(feedback_hwnd) < rows.index(pet_hwnd)

        pet.hide()
        root.update()
        calls_before_remap = len(toolwindow_calls)
        pet.show()
        root.update()
        pet_hwnd = module._windows_toplevel_hwnd(pet.window)
        assert pet.transparency_state == "ok"
        assert len(toolwindow_calls) > calls_before_remap
        assert _owner(pet_hwnd) == root_hwnd
        root.lift()
        root.focus_force()
        root.update()
        rows = _z_order()
        assert rows.index(pet_hwnd) < rows.index(root_hwnd)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_real_tk_companion_materializes_action_frames_on_demand() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.withdraw()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="ragdoll",
            position=(40, 40),
            topmost=False,
            reduced_motion=True,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        assert _pump_tk_until(root, pet._frames_are_current)
        assert _pump_tk_until(root, lambda: set(pet._tk_frames) == {"normal"})
        assert len(pet._pil_frames) > 40
        assert set(pet._tk_frames) == {"normal"}

        pet._request_static_render("meow")
        assert _pump_tk_until(root, lambda: pet._current_frame_name == "meow")
        assert set(pet._tk_frames) == {"normal", "meow"}
        assert pet._current_frame_name == "meow"

        pet.set_color("navy")
        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current()
            and pet._current_frame_name == "normal",
        )
        assert pet.color == "navy"
        assert set(pet._tk_frames) == {"normal"}
        assert pet._current_frame_name == "normal"
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_reduced_motion_keeps_static_frame_and_no_timers() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.withdraw()
    pet = None
    try:
        pet = module.PetCompanionOverlay(
            root,
            color="ragdoll",
            position=(40, 40),
            topmost=False,
            reduced_motion=True,
            work_area_for_point=lambda _x, _y: (0, 0, 1920, 1080),
            on_click=lambda: None,
            on_context_menu=lambda _x, _y: None,
            on_position_changed=lambda _position: None,
            on_hide_requested=lambda: None,
        )
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet._frames_are_current()
            and pet._current_frame_name == "normal",
        )
        pet.sleep()
        assert _pump_tk_until(root, lambda: pet._current_frame_name == "sleep")
        assert pet._idle_pose == "sleep"
        assert pet._current_frame_name == "sleep"
        pet.stretch()
        assert _pump_tk_until(root, lambda: pet._current_frame_name == "normal")
        assert pet._idle_pose == "normal"
        assert pet._current_frame_name == "normal"
        assert pet._after_id is None
        assert pet._idle_action_after_id is None
        assert pet.canvas.find_withtag("sprite")
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_pet_action_deck_avoids_repeats_until_every_action_was_seen() -> None:
    module = _overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    overlay._pet_action_deck = []
    overlay._last_companion_action = None
    overlay._pet_color = "orange"
    available_actions = module.pet_actions_for_color("orange")

    first_cycle = [
        overlay._next_pet_companion_action()
        for _ in range(len(available_actions))
    ]
    assert len(first_cycle) == len(set(first_cycle))
    assert set(first_cycle) == set(available_actions)

    previous = first_cycle[-1]
    next_action = overlay._next_pet_companion_action()
    assert next_action != previous

    overlay._pet_color = "navy"
    overlay._pet_action_deck = ["wave", "roll", "meow"]
    navy_action = overlay._next_pet_companion_action()
    navy_actions = module.pet_actions_for_color("navy")
    assert navy_actions == available_actions
    assert module.pet_actions_for_color("ragdoll") == available_actions
    assert navy_action in navy_actions


def test_full_overlay_footer_toggle_never_hides_main_ui(monkeypatch, tmp_path) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-integration" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="permanent",
        )
        root.attributes("-topmost", False)
        root.update_idletasks()
        root.update()
        assert root.winfo_viewable()
        main_geometry = root.geometry()
        overlay._pet_intro_seen = True
        assert overlay.support_cat.master is overlay.pet_launcher_row
        assert overlay.pet_launcher_row.master is overlay.pet_launcher
        assert overlay.pet_launcher_timer.winfo_manager() == ""
        assert overlay.pet_launcher_progress.find_all()
        assert overlay._pet_color == "ragdoll"
        assert overlay._pet_settlement_feedback_enabled

        assert overlay.toggle_pet_companion() == "break"
        root.update_idletasks()
        root.update()
        assert root.winfo_viewable()
        assert root.geometry() == main_geometry
        assert overlay._pet_enabled
        assert overlay._pet_companion is not None
        assert overlay._pet_companion.color == "ragdoll"
        assert overlay._pet_companion.window.winfo_viewable()

        import time

        time.sleep(0.2)
        root.update()
        assert overlay._pet_companion._bubble is not None
        open_summary = str(
            overlay._pet_companion._bubble.winfo_children()[0].cget("text")
        )
        assert "今日撸猫 0 次" in open_summary
        assert "累计 0 次" in open_summary

        overlay._set_pet_color("navy")
        root.update()
        assert overlay._pet_color == "navy"
        assert overlay._pet_companion.color == "navy"

        overlay._pet_companion.canvas.event_generate(
            "<ButtonPress-1>",
            x=96,
            y=96,
        )
        overlay._pet_companion.canvas.event_generate(
            "<ButtonRelease-1>",
            x=96,
            y=96,
        )
        root.update()
        assert overlay._pet_touch_today == 1
        assert overlay._pet_touch_total == 1
        assert overlay._pet_companion._touch_feedback is not None
        feedback_text = str(
            overlay._pet_companion._touch_feedback.winfo_children()[0].cget("text")
        )
        assert "+1" in feedback_text
        assert "今日撸猫" not in feedback_text
        assert "累计" not in feedback_text

        touch_counts = (overlay._pet_touch_today, overlay._pet_touch_total)
        profitable_settlement = {
            "phase": "settled",
            "session_id": "2201:pet-profit",
            "map_id": 2201,
            "settlement_incomplete": False,
            "winner_is_self": True,
            "known_value_sum": 120_000,
            "self_bid": 100_000,
        }
        overlay._apply_live_summary(
            profitable_settlement,
            {
                "status": "ok",
                "context": {
                    "phase": "settled",
                    "session_id": "2201:pet-profit",
                    "hero": "ahmed",
                    "map_id": 2201,
                    "round": 5,
                },
                "truth": {"available": True},
                "reference": {},
                "red": {},
                "evidence": {},
                "flags": [],
            },
        )
        root.update()
        assert overlay._pet_companion._mode == "perk"
        assert overlay._pet_companion._bubble is not None
        assert str(
            overlay._pet_companion._bubble.winfo_children()[0].cget("text")
        ) == "好耶！"
        assert (overlay._pet_touch_today, overlay._pet_touch_total) == touch_counts
        assert not overlay._maybe_celebrate_profitable_pet_settlement(
            profitable_settlement
        )

        large_loss_settlement = {
            **profitable_settlement,
            "session_id": "2201:pet-large-loss",
            "known_value_sum": 302_000,
            "self_bid": 500_000,
        }
        assert overlay._maybe_play_pet_settlement_reaction(
            large_loss_settlement
        )
        root.update()
        assert overlay._pet_companion._mode == "loss_droop"
        assert str(
            overlay._pet_companion._bubble.winfo_children()[0].cget("text")
        ) == "呜…下局再来"
        assert (overlay._pet_touch_today, overlay._pet_touch_total) == touch_counts

        assert overlay.toggle_pet_companion() == "break"
        root.update()
        assert not overlay._pet_enabled
        assert not overlay._pet_companion.window.winfo_viewable()
        assert root.winfo_viewable()
        assert root.geometry() == main_geometry
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_pet_touch_counts_persist_and_reload(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    prefs_path = tmp_path / "prefs" / "hero_ref_ui_prefs.json"
    monkeypatch.setattr(
        module,
        "ui_prefs_path_for_snapshot",
        lambda _snapshot_path: prefs_path,
    )
    monkeypatch.setattr(module, "UI_PREFS_SAVE_DEBOUNCE_MS", 1)
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

    first_root = _new_root(tk)
    first_overlay = None
    try:
        first_overlay = module.AhmadTkOverlay(
            first_root,
            snapshot_path=tmp_path / "first" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=True,
            pet_access_preview="permanent",
        )
        first_root.attributes("-topmost", False)
        first_root.update()
        first_overlay._record_pet_touch()
        first_overlay._record_pet_touch()
        first_root.after(20, first_root.quit)
        first_root.mainloop()
        saved = module.read_ui_prefs(prefs_path)
        assert saved is not None
        assert saved["pet_touch_today"] == 2
        assert saved["pet_touch_total"] == 2
    finally:
        if first_overlay is not None:
            first_overlay._destroy_pet_companion()
        try:
            first_root.destroy()
        except tk.TclError:
            pass

    second_root = _new_root(tk)
    second_overlay = None
    try:
        second_overlay = module.AhmadTkOverlay(
            second_root,
            snapshot_path=tmp_path / "second" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=True,
            pet_access_preview="permanent",
        )
        second_root.attributes("-topmost", False)
        second_root.update()
        assert second_overlay._pet_touch_today == 2
        assert second_overlay._pet_touch_total == 2
    finally:
        if second_overlay is not None:
            second_overlay._destroy_pet_companion()
        try:
            second_root.destroy()
        except tk.TclError:
            pass


def test_free_pet_requires_confirmation_and_only_counts_while_visible(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-trial" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="free",
        )
        root.attributes("-topmost", False)
        root.update()
        main_geometry = root.geometry()

        assert overlay.toggle_pet_companion() == "break"
        root.update()
        assert not overlay._pet_enabled
        assert overlay._pet_access_dialog is not None
        assert str(overlay.pet_launcher_timer.cget("text")) == "10:00"
        assert overlay._current_pet_access().status == "trial_available"
        assert "10 分钟" in overlay.pet_launcher_tip.text
        assert "确认开始" in overlay.pet_launcher_tip.text
        dialog_texts: list[str] = []
        stack = [overlay._pet_access_dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                text = str(widget.cget("text"))
            except tk.TclError:
                continue
            if text:
                dialog_texts.append(text)
        assert "开始 10 分钟体验" in dialog_texts
        assert any("真实显示时计量" in text for text in dialog_texts)
        assert not overlay._pet_enabled

        overlay._start_pet_usage_from_dialog()
        root.update()
        assert overlay._pet_enabled
        assert overlay._pet_companion is not None
        assert overlay._pet_companion.window.winfo_viewable()
        assert overlay._current_pet_access().status == "trial_active"

        import time

        overlay._pet_preview_tick_monotonic = time.monotonic() - 2.2
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        active_remaining = int(overlay._current_pet_access().seconds_left or 0)
        assert active_remaining <= 598

        overlay._set_pet_companion_enabled(False)
        root.update()
        paused_remaining = int(overlay._current_pet_access().seconds_left or 0)
        assert overlay._current_pet_access().status == "trial_paused"
        assert not overlay._pet_companion.window.winfo_viewable()
        overlay._refresh_pet_access_ui(schedule_next=False)
        assert int(overlay._current_pet_access().seconds_left or 0) == paused_remaining

        overlay._set_pet_companion_enabled(True)
        root.update()
        assert not overlay._pet_enabled
        assert overlay._pet_access_dialog is not None
        overlay._start_pet_usage_from_dialog()
        root.update()
        assert overlay._current_pet_access().status == "trial_active"
        overlay._pet_preview_seconds_remaining = 1
        overlay._pet_preview_tick_monotonic = time.monotonic() - 2.0
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay._current_pet_access().status == "trial_exhausted"
        assert not overlay._pet_enabled
        assert str(overlay.pet_launcher_timer.cget("text")) == "00:00"
        assert root.winfo_viewable()
        assert root.geometry() == main_geometry
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_pet_status_island_discovers_without_opening_a_large_tooltip(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-discovery" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="quota30",
        )
        root.attributes("-topmost", False)
        root.update()
        overlay._pet_discovery_seen = False
        overlay._show_pet_discovery_tip()
        root.update()

        assert overlay.pet_launcher_tip.tip is None
        assert "左键状态岛：查看规则并确认启用" in overlay.pet_launcher_tip.text
        assert "换色：右键右下角猫猫状态岛" in overlay.pet_launcher_tip.text
        assert "桌面猫上也可右键" in overlay.pet_launcher_tip.text
        assert "当前：白色布偶" in overlay.pet_launcher_tip.text
        assert "只本地陪伴，不影响报价" in overlay.pet_launcher_tip.text
        assert overlay._pet_discovery_pulse_on
        assert overlay.pet_launcher_timer.cget("text") == "12:00:00"
        assert "30 天猫猫额度" in overlay.pet_launcher_tip.text
        holder = overlay.pet_launcher.master
        assert holder._round_card["outline"] == module.WARM

        overlay._set_pet_color("navy")
        assert "当前：深蓝" in overlay.pet_launcher_tip.text

        overlay._mark_pet_discovery_seen()
        root.update()
        assert overlay._pet_discovery_seen
        assert not overlay._pet_discovery_pulse_on
        assert overlay.pet_launcher_tip.tip is None
    finally:
        if overlay is not None:
            overlay._mark_pet_discovery_seen()
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_30_day_status_island_uses_quota_clock_and_90_day_is_full(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-quota" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="quota30",
        )
        root.attributes("-topmost", False)
        root.update()
        assert overlay._current_pet_access().status == "quota_available"
        assert str(overlay.pet_launcher_timer.cget("text")) == "12:00:00"
        assert "30 天猫猫额度" in overlay.pet_launcher_tip.text
        assert "12 小时" in overlay.pet_launcher_tip.text
        assert "10:00" not in overlay.pet_launcher_tip.text

        overlay._pet_access_preview = "full90"
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay._current_pet_access().status == "full"
        assert "90 天及以上" in overlay.pet_launcher_tip.text
        assert overlay.pet_launcher_timer.winfo_manager() == ""
        overlay._set_pet_companion_enabled(True)
        root.update()
        assert overlay._pet_enabled
        assert overlay._pet_companion is not None
        assert overlay._pet_companion.window.winfo_viewable()
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize(
    "tk_scaling",
    (1.333333, 1.666667, 2.0, 2.333333),
    ids=("100pct", "125pct", "150pct", "175pct"),
)
def test_pet_status_island_width_tracks_visible_clock(
    monkeypatch,
    tmp_path,
    tk_scaling,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        root.tk.call("tk", "scaling", tk_scaling)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-island-width" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="quota30",
        )
        root.attributes("-topmost", False)
        root.update()
        holder = overlay.pet_launcher.master

        def state_width(
            preview: str,
            seconds_left: int | None,
            expected_text: str,
        ) -> int:
            overlay._pet_access_preview = preview
            overlay._pet_preview_seconds_remaining = seconds_left
            overlay._refresh_pet_access_ui(schedule_next=False)
            root.update()
            assert str(overlay.pet_launcher_timer.cget("text")) == expected_text
            first_width = int(holder.winfo_width())
            first_request = int(holder.cget("width"))
            track = overlay.pet_launcher_progress.find_all()[0]
            track_right = int(round(overlay.pet_launcher_progress.coords(track)[2]))
            assert track_right == int(overlay.pet_launcher_progress.winfo_width())

            root.update_idletasks()
            root.update()
            assert int(holder.winfo_width()) == first_width
            assert int(holder.cget("width")) == first_request
            return first_width

        quota_widths = (
            state_width("quota30", 43_200, "12:00:00"),
            state_width("quota30", 43_199, "11:59:59"),
            state_width("quota30", 36_000, "10:00:00"),
        )
        trial_widths = (
            state_width("free", 600, "10:00"),
            state_width("free", 599, "09:59"),
            state_width("free", 1, "00:01"),
        )
        full_width = state_width("permanent", None, "")
        assert overlay.pet_launcher_timer.winfo_manager() == ""

        assert "fixed_width" not in holder._round_card
        assert len(set(quota_widths)) == 1, (
            f"{tk_scaling=}, {quota_widths=}"
        )
        assert len(set(trial_widths)) == 1, (
            f"{tk_scaling=}, {trial_widths=}"
        )
        quota_width = quota_widths[0]
        trial_width = trial_widths[0]
        assert quota_width > trial_width >= full_width, (
            f"{tk_scaling=}, "
            f"{quota_width=}, {trial_width=}, {full_width=}"
        )
        assert full_width <= 90
        assert quota_width <= 140
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_entitlement_pending_is_visually_distinct_from_quota_exhausted(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-pending" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="pending",
        )
        root.attributes("-topmost", False)
        root.update()
        assert overlay._current_pet_access().status == "entitlement_pending"
        assert str(overlay.pet_launcher_timer.cget("text")) == "…"
        assert "同步中" in overlay.pet_launcher_tip.text
        overlay._show_pet_access_dialog(overlay._current_pet_access())
        root.update()
        pending_texts: list[str] = []
        stack = [overlay._pet_access_dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                text = str(widget.cget("text"))
            except tk.TclError:
                continue
            if text:
                pending_texts.append(text)
        assert "权益同步中 · 不等于已耗尽" in pending_texts
        assert all("00:00" not in text for text in pending_texts)

        overlay._close_pet_access_dialog()
        overlay._pet_access_preview = "quota30_exhausted"
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay._current_pet_access().status == "quota_exhausted"
        assert str(overlay.pet_launcher_timer.cget("text")) == "00:00:00"
        assert "已用完" in overlay.pet_launcher_tip.text
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_signed_metering_events_are_visible_gated_and_idempotent(
    monkeypatch,
    tmp_path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    events: list[str] = []

    def usage_handler(event: str, _state) -> bool:
        events.append(event)
        if event == "start" and overlay is not None:
            overlay._act_state = _signed_metered_activation(
                plan_kind="trial",
                usage_kind="trial",
                status="trial_active",
                total=600,
                remaining=600,
            )
        return True

    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-signed-events" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_usage_event_handler=usage_handler,
        )
        root.attributes("-topmost", False)
        overlay._act_state = _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status="trial_available",
            total=600,
            remaining=600,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()

        overlay._set_pet_companion_enabled(True)
        root.update()
        assert not overlay._pet_enabled
        assert overlay._pet_access_dialog is not None
        assert events == []

        overlay._pet_usage_event_handler = None
        overlay._start_pet_usage_from_dialog()
        root.update()
        assert not overlay._pet_enabled
        assert overlay._current_pet_access().status == "trial_available"
        assert events == []

        overlay._pet_usage_event_handler = usage_handler
        overlay._show_pet_access_dialog(overlay._current_pet_access())
        overlay._start_pet_usage_from_dialog()
        assert events == ["start"]
        overlay._refresh_pet_access_ui(schedule_next=False)
        root.update()
        assert overlay._pet_enabled
        assert overlay._pet_companion is not None
        assert overlay._pet_companion.window.winfo_viewable()
        assert events == ["start", "visible"]

        overlay._set_pet_companion_enabled(True)
        root.update()
        assert events == ["start", "visible"]

        overlay._set_pet_companion_enabled(False)
        root.update()
        assert events == ["start", "visible", "hidden"]
        overlay._set_pet_companion_enabled(False)
        root.update()
        assert events == ["start", "visible", "hidden"]
    finally:
        if overlay is not None:
            overlay._close_pet_access_dialog()
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_signed_metering_waits_for_final_pet_visibility_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    held_idle: list[tuple[object, tuple[object, ...]]] = []
    events: list[str] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _release_idle() -> int:
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            callback(*args)
            released += 1
            assert released < 20
        return released

    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-async-visible" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_usage_event_handler=lambda event, _state: events.append(event) or True,
        )
        root.attributes("-topmost", False)
        overlay._act_state = _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status="trial_active",
            total=600,
            remaining=600,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        companion = overlay._create_pet_companion()
        assert companion is not None
        monkeypatch.setattr(companion.window, "after_idle", _hold_idle)

        overlay._set_pet_companion_enabled(True)
        root.update()
        assert companion.window.winfo_viewable()
        assert companion.transparency_state == "unsupported"
        assert not overlay._pet_enabled
        assert not overlay._is_pet_companion_visible()
        assert events == []

        assert _release_idle() > 0
        root.update()
        assert companion.transparency_state == "ok"
        assert overlay._pet_enabled
        assert overlay._is_pet_companion_visible()
        assert events == ["visible"]

        overlay._set_pet_companion_enabled(False)
        root.update()
        assert events == ["visible", "hidden"]

        overlay._set_pet_companion_enabled(True)
        root.update()
        pending_callbacks = len(held_idle)
        assert pending_callbacks > 0
        overlay._set_pet_companion_enabled(True)
        assert len(held_idle) == pending_callbacks
        overlay._set_pet_companion_enabled(False)
        assert not overlay._pet_enabled
        assert events == ["visible", "hidden"]
        assert _release_idle() > 0
        root.update()
        assert not companion.window.winfo_viewable()
        assert not overlay._pet_enabled
        assert events == ["visible", "hidden"]
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visibility_commit_rechecks_generation_after_real_map_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    held_idle: list[tuple[object, tuple[object, ...]]] = []
    events: list[str] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _release_one() -> None:
        callback, args = held_idle.pop(0)
        callback(*args)

    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-map-generation" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_usage_event_handler=lambda event, _state: events.append(event) or True,
        )
        root.attributes("-topmost", False)
        overlay._act_state = _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status="trial_active",
            total=600,
            remaining=600,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        companion = overlay._create_pet_companion()
        assert companion is not None
        monkeypatch.setattr(companion.window, "after_idle", _hold_idle)

        original_callback = companion._on_visibility_changed
        original_update_idletasks = companion.window.update_idletasks
        map_injected = False

        def _ready_callback(*args):
            nonlocal map_injected
            if bool(args[0]) and not map_injected:
                map_injected = True

                def _map_during_commit() -> None:
                    monkeypatch.setattr(
                        companion.window,
                        "update_idletasks",
                        original_update_idletasks,
                    )
                    companion.window.event_generate("<Map>", when="now")
                    original_update_idletasks()

                monkeypatch.setattr(
                    companion.window,
                    "update_idletasks",
                    _map_during_commit,
                )
            return original_callback(*args)

        companion._on_visibility_changed = _ready_callback
        overlay._set_pet_companion_enabled(True)
        root.update()
        assert held_idle

        request_token = companion._transparency_request_token
        released = 0
        while held_idle:
            callback, args = held_idle[0]
            callback_name = getattr(callback, "__name__", "")
            first_generation = companion._transparency_generation
            _release_one()
            released += 1
            assert released < 20
            if callback_name == "_apply_transparency_generation":
                break
        else:
            pytest.fail("transparency apply callback was not scheduled")

        assert companion._transparency_generation > first_generation
        assert companion.visibility_pending
        assert not companion.visibility_ready
        assert companion._transparency_request_token == request_token
        assert overlay._pet_visibility_pending
        assert not overlay._pet_enabled
        assert events == []

        released = 0
        while held_idle:
            _release_one()
            released += 1
            assert released < 20
        root.update()
        assert companion.visibility_ready
        assert not companion.visibility_pending
        assert companion._transparency_request_token is None
        assert overlay._pet_enabled
        assert not overlay._pet_visibility_pending
        assert events == ["visible"]

        overlay._set_pet_companion_enabled(False)
        assert events == ["visible", "hidden"]
        overlay._set_pet_companion_enabled(True)
        root.update()
        assert held_idle
        overlay._destroy_pet_companion()
        while held_idle:
            _release_one()
        root.update()
        assert overlay._pet_companion is None
        assert not overlay._pet_enabled
        assert not overlay._pet_visibility_pending
        assert events == ["visible", "hidden"]
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visibility_ack_rejects_plain_int_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[tuple[bool, object, int]] = []

    def _ack(ready: bool, token: object, generation: int) -> object:
        callbacks.append((ready, token, generation))
        return 1 if ready else True

    try:
        pet = _new_pet(module, root, on_visibility_changed=_ack)
        pet.show(visibility_token=19)
        assert _pump_tk_until(
            root,
            lambda: bool(callbacks)
            and (pet.visibility_ready or callbacks[-1][0] is False),
        )

        assert callbacks[0][0] is True
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visibility_ack_never_calls_truthy_object_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []

    class _TruthyAck:
        def __init__(self) -> None:
            self.magic_calls = 0

        def __bool__(self) -> bool:
            self.magic_calls += 1
            return True

    result = _TruthyAck()

    def _ack(ready: bool, _token: object, _generation: int) -> object:
        callbacks.append(ready)
        return result if ready else True

    try:
        pet = _new_pet(module, root, on_visibility_changed=_ack)
        pet.show(visibility_token=23)
        assert _pump_tk_until(
            root,
            lambda: bool(callbacks)
            and (pet.visibility_ready or callbacks[-1] is False),
        )

        assert result.magic_calls == 0
        assert callbacks[0] is True
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visibility_ack_accepts_only_exact_builtin_true() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)

    class _IntOne(int):
        pass

    class _TruthyAck:
        def __init__(self, *, raises: bool = False) -> None:
            self.raises = raises
            self.magic_calls = 0

        def __bool__(self) -> bool:
            self.magic_calls += 1
            if self.raises:
                raise RuntimeError("must not execute")
            return True

    truthy = _TruthyAck()
    raising_truthy = _TruthyAck(raises=True)
    invalid_results: tuple[tuple[str, object], ...] = (
        ("false", False),
        ("none", None),
        ("zero", 0),
        ("one", 1),
        ("string", "ack"),
        ("empty-list", []),
        ("list", [True]),
        ("empty-dict", {}),
        ("dict", {"ack": True}),
        ("int-subclass", _IntOne(1)),
        ("truthy-object", truthy),
        ("raising-truthy-object", raising_truthy),
    )

    def _exercise(result: object, token: int) -> None:
        pet = None
        callbacks: list[bool] = []

        def _ack(ready: bool, _token: object, _generation: int) -> object:
            callbacks.append(ready)
            return result if ready else True

        try:
            pet = _new_pet(module, root, on_visibility_changed=_ack)
            pet.show(visibility_token=token)
            assert _pump_tk_until(
                root,
                lambda: len(callbacks) >= 2 and callbacks[-1] is False,
            )
            assert callbacks[:2] == [True, False]
            assert not pet.visibility_ready
            assert not pet.visibility_pending
            assert pet._transparency_request_token is None
            assert not pet.window.winfo_viewable()
            assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
            assert module._windows_layered_alpha(
                module._windows_toplevel_hwnd(pet.window)
            ) in (None, 0)
        finally:
            if pet is not None:
                pet.destroy()

    try:
        for index, (label, result) in enumerate(invalid_results, start=1):
            try:
                _exercise(result, 100 + index)
            except AssertionError as exc:
                raise AssertionError(f"invalid ack case failed: {label}") from exc

        callbacks: list[bool] = []

        def _exact_true(ready: bool, _token: object, _generation: int) -> bool:
            callbacks.append(ready)
            return True

        accepted = _new_pet(module, root, on_visibility_changed=_exact_true)
        try:
            accepted.show(visibility_token=999)
            assert _pump_tk_until(root, lambda: accepted.visibility_ready)
            assert callbacks == [True]
            assert not accepted.visibility_pending
            assert accepted._transparency_request_token is None
            assert accepted.window.winfo_viewable()
        finally:
            accepted.destroy()

        assert truthy.magic_calls == 0
        assert raising_truthy.magic_calls == 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visibility_callback_exception_rolls_back_without_escaping() -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []

    def _raising_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        raise RuntimeError("synthetic callback failure")

    try:
        pet = _new_pet(module, root, on_visibility_changed=_raising_callback)
        pet.show(visibility_token=31)
        assert _pump_tk_until(
            root,
            lambda: callbacks == [True, False],
        )

        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
        assert module._windows_layered_alpha(
            module._windows_toplevel_hwnd(pet.window)
        ) in (None, 0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_overlay_visibility_callback_exception_emits_no_orphan_usage_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    events: list[str] = []
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-callback-exception" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_usage_event_handler=lambda event, _state: events.append(event) or True,
        )
        root.attributes("-topmost", False)
        overlay._act_state = _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status="trial_active",
            total=600,
            remaining=600,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        companion = overlay._create_pet_companion()
        assert companion is not None
        original_callback = companion._on_visibility_changed

        def _raising_ready_callback(
            ready: bool,
            token: object,
            generation: int,
        ) -> bool:
            if ready:
                raise RuntimeError("synthetic overlay callback failure")
            return original_callback(ready, token, generation)

        companion._on_visibility_changed = _raising_ready_callback
        overlay._set_pet_companion_enabled(True)
        assert _pump_tk_until(
            root,
            lambda: not overlay._pet_visibility_pending,
        )

        assert not overlay._pet_enabled
        assert events == []
        assert not companion.visibility_ready
        assert not companion.visibility_pending
        assert companion._transparency_request_token is None
        assert not companion.window.winfo_viewable()
        assert float(companion.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
@pytest.mark.parametrize(
    "error_type",
    (
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        type("SyntheticVisibilityControl", (BaseException,), {}),
    ),
)
def test_visibility_callback_control_exception_rolls_back_before_reraise(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []
    held_idle: list[tuple[object, tuple[object, ...]]] = []
    original_error = error_type("synthetic callback control")

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _raising_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        if ready:
            raise original_error
        return True

    try:
        pet = _new_pet(module, root, on_visibility_changed=_raising_callback)
        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show(visibility_token=37)
        root.update()
        assert held_idle
        caught = None
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            if getattr(callback, "__name__", "") == "_apply_transparency_generation":
                with pytest.raises(error_type) as exc_info:
                    callback(*args)
                caught = exc_info.value
                break
            callback(*args)
            released += 1
            assert released < 20
        assert caught is original_error
        assert callbacks == [True, False]
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
        assert module._windows_layered_alpha(
            module._windows_toplevel_hwnd(pet.window)
        ) in (None, 0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
@pytest.mark.parametrize(
    "terminal_error_type",
    (
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        type("SyntheticTerminalControl", (BaseException,), {}),
    ),
)
def test_failure_callback_control_exception_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    terminal_error_type: type[BaseException],
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []
    held_idle: list[tuple[object, tuple[object, ...]]] = []

    terminal_error = terminal_error_type("terminal callback control")

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _raising_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        if ready:
            raise RuntimeError("initial callback failure")
        raise terminal_error

    try:
        pet = _new_pet(module, root, on_visibility_changed=_raising_callback)
        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show(visibility_token=41)
        root.update()
        assert held_idle
        caught = None
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            if getattr(callback, "__name__", "") == "_apply_transparency_generation":
                with pytest.raises(terminal_error_type) as exc_info:
                    callback(*args)
                caught = exc_info.value
                break
            callback(*args)
            released += 1
            assert released < 20

        assert caught is terminal_error
        assert callbacks == [True, False]
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_ready_control_exception_has_priority_over_terminal_control_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []
    held_idle: list[tuple[object, tuple[object, ...]]] = []
    ready_error = KeyboardInterrupt("ready callback control")
    terminal_error = SystemExit("terminal callback control")

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _raising_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        raise ready_error if ready else terminal_error

    try:
        pet = _new_pet(module, root, on_visibility_changed=_raising_callback)
        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show(visibility_token=43)
        root.update()
        assert held_idle
        caught = None
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            if getattr(callback, "__name__", "") == "_apply_transparency_generation":
                with pytest.raises(KeyboardInterrupt) as exc_info:
                    callback(*args)
                caught = exc_info.value
                break
            callback(*args)
            released += 1
            assert released < 20

        assert caught is ready_error
        assert callbacks == [True, False]
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_ready_and_terminal_ordinary_exceptions_remain_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []
    held_idle: list[tuple[object, tuple[object, ...]]] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _raising_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        if ready:
            raise RuntimeError("ready callback failure")
        raise ValueError("terminal callback failure")

    try:
        pet = _new_pet(module, root, on_visibility_changed=_raising_callback)
        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show(visibility_token=47)
        root.update()
        assert held_idle
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            callback(*args)
            released += 1
            assert released < 20
            if getattr(callback, "__name__", "") == "_apply_transparency_generation":
                break

        assert callbacks == [True, False]
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        assert not pet.window.winfo_viewable()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
@pytest.mark.parametrize(
    "reentrant_action",
    ("hide", "show", "destroy", "repeat-terminal"),
)
def test_terminal_visibility_callback_reentry_is_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    reentrant_action: str,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    pet = None
    callbacks: list[bool] = []
    held_idle: list[tuple[object, tuple[object, ...]]] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _reentrant_callback(
        ready: bool,
        _token: object,
        _generation: int,
    ) -> bool:
        callbacks.append(ready)
        if ready:
            raise RuntimeError("ready callback failure")
        if reentrant_action == "hide":
            pet.hide()
        elif reentrant_action == "show":
            pet.show(visibility_token=53)
        elif reentrant_action == "destroy":
            pet.destroy()
        else:
            pet._withdraw_after_transparency_failure()
        return True

    try:
        pet = _new_pet(module, root, on_visibility_changed=_reentrant_callback)
        monkeypatch.setattr(pet.window, "after_idle", _hold_idle)
        pet.show(visibility_token=51)
        root.update()
        assert held_idle
        released = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            callback(*args)
            released += 1
            assert released < 20
            if getattr(callback, "__name__", "") == "_apply_transparency_generation":
                break

        while held_idle:
            callback, args = held_idle.pop(0)
            callback(*args)
            released += 1
            assert released < 20

        assert callbacks == [True, False]
        assert not pet._visibility_failure_callback_active
        assert not pet.visibility_ready
        assert not pet.visibility_pending
        assert pet._transparency_request_token is None
        if reentrant_action == "destroy":
            assert pet._destroyed
        else:
            assert not pet.window.winfo_viewable()
            assert float(pet.window.attributes("-alpha")) == pytest.approx(0.0)
    finally:
        if pet is not None and not pet._destroyed:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_real_tk_hide_show_restores_last_external_positive_alpha() -> None:
    import ctypes

    tk = pytest.importorskip("tkinter")
    module = _pet_module()
    root = _new_root(tk)
    root.geometry("520x420+100+100")
    root.update()
    pet = None
    try:
        pet = _new_pet(module, root, color="ragdoll", reduced_motion=True)
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok" and pet.window.winfo_viewable(),
        )

        pet.window.attributes("-alpha", 0.4)
        root.update()
        assert float(pet.window.attributes("-alpha")) == pytest.approx(
            0.4,
            abs=1.0 / 255.0,
        )
        pet.hide()
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok" and pet.window.winfo_viewable(),
        )
        assert float(pet.window.attributes("-alpha")) == pytest.approx(
            0.4,
            abs=1.0 / 255.0,
        )

        hwnd = module._windows_toplevel_hwnd(pet.window)
        native_alpha = ctypes.c_ubyte()
        native_key = ctypes.c_uint32()
        native_flags = ctypes.c_uint32()
        assert ctypes.windll.user32.GetLayeredWindowAttributes(
            hwnd,
            ctypes.byref(native_key),
            ctypes.byref(native_alpha),
            ctypes.byref(native_flags),
        )
        assert native_flags.value & 0x00000002  # LWA_ALPHA
        assert native_alpha.value == pytest.approx(round(0.4 * 255), abs=1)

        pet.window.attributes("-alpha", 1.0)
        root.update()
        assert ctypes.windll.user32.SetLayeredWindowAttributes(
            hwnd,
            native_key.value,
            153,
            0x00000001 | 0x00000002,
        )
        pet.hide()
        pet.show()
        assert _pump_tk_until(
            root,
            lambda: pet.transparency_state == "ok" and pet.window.winfo_viewable(),
        )
        assert float(pet.window.attributes("-alpha")) == pytest.approx(
            0.6,
            abs=1.0 / 255.0,
        )
    finally:
        if pet is not None:
            pet.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_parent_rebind_pending_does_not_duplicate_visibility_or_orphan_hidden(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    held_idle: list[tuple[object, tuple[object, ...]]] = []
    events: list[str] = []

    def _hold_idle(callback, *args):
        held_idle.append((callback, args))
        return f"held-{len(held_idle)}"

    def _release_idle() -> int:
        released = 0
        transparency_callbacks = 0
        while held_idle:
            callback, args = held_idle.pop(0)
            callback_name = getattr(callback, "__name__", "")
            assert callback_name in {
                "_apply_transparency_generation",
                "_materialize_tk_task",
            }
            callback(*args)
            released += 1
            transparency_callbacks += callback_name == "_apply_transparency_generation"
            assert released < 20
        return transparency_callbacks

    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-parent-rebind" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_usage_event_handler=lambda event, _state: events.append(event) or True,
        )
        root.attributes("-topmost", False)
        overlay._act_state = _signed_metered_activation(
            plan_kind="trial",
            usage_kind="trial",
            status="trial_active",
            total=600,
            remaining=600,
        )
        overlay._refresh_pet_access_ui(schedule_next=False)
        overlay._set_pet_companion_enabled(True)
        assert _pump_tk_until(root, lambda: overlay._pet_enabled)
        companion = overlay._pet_companion
        assert companion is not None
        assert events == ["visible"]

        monkeypatch.setattr(companion.window, "after_idle", _hold_idle)
        assert companion._mask_transparency_for_map() is not None
        companion._schedule_transparency_apply()
        overlay._refresh_pet_access_ui(schedule_next=False)
        assert companion.visibility_pending
        assert overlay._pet_enabled
        assert events == ["visible"]
        assert _release_idle() == 1
        root.update()
        assert companion.visibility_ready
        assert events == ["visible"]

        assert companion._mask_transparency_for_map() is not None
        companion._schedule_transparency_apply()
        monkeypatch.setattr(
            sys.modules[companion.__class__.__module__],
            "_apply_and_verify_native_colorkey",
            lambda _hwnd, _colorref, **_kwargs: False,
        )
        assert _release_idle() == 1
        root.update()
        assert not overlay._pet_enabled
        assert events == ["visible", "hidden"]
        overlay._destroy_pet_companion()
        assert events == ["visible", "hidden"]
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows transparency contract")
def test_visible_preview_rebind_failure_pauses_without_replaying_visibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    module = _overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root(tk)
    overlay = None
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pet-preview-rebind" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            pet_access_preview="free",
        )
        root.attributes("-topmost", False)
        overlay._pet_preview_seconds_remaining = 600
        overlay._set_controlled_pet_preview_phase("active")
        overlay._set_pet_companion_enabled(True)
        assert _pump_tk_until(root, lambda: overlay._pet_enabled)
        assert overlay._current_pet_access().status == "trial_active"

        companion = overlay._pet_companion
        assert companion is not None
        assert companion._mask_transparency_for_map() is not None
        monkeypatch.setattr(
            sys.modules[companion.__class__.__module__],
            "_apply_and_verify_native_colorkey",
            lambda _hwnd, _colorref, **_kwargs: False,
        )
        companion._schedule_transparency_apply()
        assert _pump_tk_until(root, lambda: not overlay._pet_enabled)

        assert not overlay._pet_visibility_pending
        assert not companion.visibility_ready
        assert not companion.window.winfo_viewable()
        assert overlay._current_pet_access().status == "trial_paused"
    finally:
        if overlay is not None:
            overlay._destroy_pet_companion()
        try:
            root.destroy()
        except tk.TclError:
            pass
