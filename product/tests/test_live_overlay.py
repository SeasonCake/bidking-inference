from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import queue
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import time
import zipfile
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _overlay_module():
    path = ROOT / "scripts" / "run_live_overlay.py"
    spec = importlib.util.spec_from_file_location("run_live_overlay", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ahmad_overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("ahmad_tk_overlay", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _network_status_module():
    path = ROOT / "hero_ref" / "tools" / "network_connection_status.py"
    spec = importlib.util.spec_from_file_location("network_connection_status", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ahmad_server_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location("ahmad_live_panel_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _class_method_ast(module, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"{class_name}.{method_name} not found")


def test_paid_activation_row_only_shows_in_last_90_days(monkeypatch) -> None:
    """长期/无期限会员隐藏整行；最后 90 天显示倒计时和换绑入口。"""
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    cases = [
        (None, True),
        (90 * 86400 + 1, True),
        (90 * 86400, False),
        (89 * 86400, False),
    ]
    for seconds_left, expect_hidden in cases:
        state = {
            "run_full": True,
            "mode": "paid",
            "seconds_left": seconds_left,
            "warn": False,
            "text": "会员",
        }
        monkeypatch.setattr(enforce, "activation_state", lambda state=state: state)
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay._act_ready = True
        overlay._expired_latched = False
        overlay.activation_label = Widget()
        calls: list[str] = []
        overlay._hide_activation_label = lambda: calls.append("hide")  # type: ignore[method-assign]
        overlay._show_activation_label = lambda: calls.append("show")  # type: ignore[method-assign]
        overlay._set_activation_entry_available = (  # type: ignore[method-assign]
            lambda available: calls.append(f"entry:{available}")
        )

        assert module.AhmadTkOverlay._update_activation_ui(overlay) is False
        assert calls == (["entry:False", "hide"] if expect_hidden else ["entry:True", "show"])
        if not expect_hidden:
            assert overlay.activation_label.kwargs["text"] == "会员"


def test_valid_trial_from_server_clears_previous_expired_latch(monkeypatch) -> None:
    """试用时长从 1h 延到 3h 后，服务端有效结果必须让本会话从旧到期锁存中恢复。"""
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    monkeypatch.setattr(
        enforce,
        "activation_state",
        lambda: {
            "run_full": True,
            "mode": "trial",
            "reason": "ok",
            "seconds_left": 3600,
            "warn": False,
            "text": "试用中 · 剩余 1 小时 0 分",
        },
    )
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = True
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None  # type: ignore[method-assign]
    overlay._hide_activation_label = lambda: None  # type: ignore[method-assign]
    entry_available: list[bool] = []
    overlay._set_activation_entry_available = entry_available.append  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is False
    assert overlay._expired_latched is False
    assert entry_available == [True]
    assert overlay.activation_label.kwargs["text"] == "免费额度 · 试用中 · 剩余 1 小时 0 分"


def test_paid_countdown_is_not_labeled_as_free_quota(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    monkeypatch.setattr(
        enforce,
        "activation_state",
        lambda: {
            "run_full": True,
            "mode": "paid",
            "seconds_left": 29 * 86400,
            "warn": False,
            "text": "会员 · 剩余 29 天 0 时",
        },
    )
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = False
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None  # type: ignore[method-assign]
    overlay._hide_activation_label = lambda: None  # type: ignore[method-assign]
    entry_available: list[bool] = []
    overlay._set_activation_entry_available = entry_available.append  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is False
    assert entry_available == [True]
    assert overlay.activation_label.kwargs["text"] == "会员 · 剩余 29 天 0 时"


def test_expired_paid_membership_restores_token_entry(monkeypatch) -> None:
    """过期付费状态需要恢复续费入口，不能因 mode=paid 永久隐藏。"""
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    monkeypatch.setattr(
        enforce,
        "activation_state",
        lambda: {
            "run_full": False,
            "mode": "paid",
            "seconds_left": 0,
            "warn": True,
            "text": "会员已到期",
        },
    )
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = False
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None  # type: ignore[method-assign]
    overlay._hide_activation_label = lambda: None  # type: ignore[method-assign]
    entry_available: list[bool] = []
    overlay._set_activation_entry_available = entry_available.append  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
    assert entry_available == [True]
    assert overlay.activation_label.kwargs["text"] == "🔒 会员已到期"


def test_activation_state_error_is_visible_as_server_failure(monkeypatch) -> None:
    """503 state_error must not be rendered as offline, expired membership, or bad token."""
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce
    from bidking_lab.secure.client_activate import ActivationResult

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    state = enforce.activation_state(
        ActivationResult(activated=False, mode="paid", reason="state_error")
    )
    monkeypatch.setattr(enforce, "activation_state", lambda: state)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._expired_latched = False
    overlay.activation_label = Widget()
    overlay._show_activation_label = lambda: None  # type: ignore[method-assign]
    overlay._hide_activation_label = lambda: None  # type: ignore[method-assign]
    overlay._set_activation_entry_available = lambda _available: None  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._update_activation_ui(overlay) is True
    text = overlay.activation_label.kwargs["text"]
    assert "服务器激活服务暂时异常" in text
    assert "offline" not in text
    assert "会员已到期" not in text
    assert "token 无效" not in text


def test_activation_entry_widget_uses_paid_change_token_copy_and_keeps_click_target() -> None:
    """有效会员显示“更换 token”，入口本体、点击热区和 tooltip 都保持可用。"""
    module = _ahmad_overlay_module()

    class Action:
        def __init__(self) -> None:
            self.mapped = True
            self.pack_options: dict[str, Any] = {}

        def winfo_manager(self) -> str:
            return "pack" if self.mapped else ""

        def pack_forget(self) -> None:
            self.mapped = False

        def pack(self, **kwargs: Any) -> None:
            self.mapped = True
            self.pack_options = kwargs

        def configure(self, **kwargs: Any) -> None:
            self.pack_options.update(kwargs)

    class Label:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}
            self.bound = True

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *_args: Any) -> None:
            self.bound = True

        def unbind(self, *_args: Any) -> None:
            self.bound = False

    class Tip:
        def __init__(self) -> None:
            self.text = "初始"
            self.hidden = False

        def hide(self) -> None:
            self.hidden = True

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.activation_action = Action()
    overlay.activation_label = Label()
    overlay.activation_tip = Tip()
    overlay.activation_action_tip = Tip()
    overlay._act_state = {"run_full": True, "mode": "paid"}
    overlay._open_token_dialog = lambda _event=None: None  # type: ignore[method-assign]

    module.AhmadTkOverlay._set_activation_entry_available(overlay, True)
    assert overlay.activation_action.mapped is True
    assert overlay.activation_action.pack_options["text"] == "更换 token"
    assert overlay.activation_label.bound is True
    assert overlay.activation_label.kwargs["cursor"] == "hand2"
    assert overlay.activation_tip.text == "点击此处更换 token"
    assert overlay.activation_action_tip.text == "打开 token 更换与购买说明"

    overlay._act_state = {"run_full": True, "mode": "trial"}
    module.AhmadTkOverlay._set_activation_entry_available(overlay, True)
    assert overlay.activation_action.pack_options["text"] == "点此输入/购买 token"
    assert overlay.activation_tip.text == "点击此处输入激活 token"


def test_ahmad_summary_diagnostic_log_records_display_ref_inputs(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._manual_active = True
    overlay.settlement_values_hidden = True
    overlay._last_summary_log_signature = None
    summary = {
        "status": "ok",
        "context": {
            "hero": "ahmed",
            "map_id": 2401,
            "round": 3,
            "phase": "settled",
            "session_id": "s1",
        },
        "reference": {
            "source": "ref_v0",
            "balanced": "685,641",
            "v3_balanced": "477,562",
            "ref_minus_v3_balanced": "+208,079",
        },
        "red": {"uncertainty_summary": "未锁 白绿10/15/15"},
        "evidence": {
            "ref_input_summary": "总件 38 · 总格 83 · 金均价 34288.75",
            "candidate_summary": "总件 38 · 总格 83",
            "next_info_hint": "优先补白绿件数或均格",
            "public_numeric_summary": "金均价 34288.75",
            "diagnostics": "public_q5_avg_value;quality_value_q5_count_derived",
            "manual_overlay": True,
        },
        "ahmed_ref": {
            "status": "ok",
            "combo_count": 12,
            "evidence": {
                "source_notes": [
                    "public_q5_avg_value",
                    "quality_value_q5_count_derived",
                ],
                "fixed_counts": {"q5": 4},
                "avg_values": {"q5": 34288.75},
                "quality_values": {"q5": 137155},
            },
        },
        "minimap": {"summary_text": "4 件 · public_info", "quality_counts": {"q6": 1}},
        "diagnostics": {
            "rare_signals": {
                "present": True,
                "summary": "终极审计:diagnostic_only / 金均价:ref_v0_constraint",
                "role_counts": {"diagnostic_only": 1, "ref_v0_constraint": 1},
                "actions": [
                    {
                        "action_id": 100121,
                        "label": "终极审计",
                        "semantic": "total_value",
                        "ref_v0_role": "diagnostic_only",
                        "result": 728211,
                    }
                ],
                "public_info": [
                    {
                        "info_id": 200037,
                        "label": "金均价",
                        "semantic": "q5_avg_value",
                        "ref_v0_role": "ref_v0_constraint",
                        "value": 34288.75,
                    }
                ],
            },
        },
        "truth": {
            "available": True,
            "total_value": 728211,
            "total_items": 38,
            "total_cells": 83,
            "q6": {"count": 3, "cells": 7, "value": 301000},
        },
        "flags": [{"label": "公开信息", "level": "neutral", "detail": "金均价 34288.75"}],
    }

    module.AhmadTkOverlay._record_summary_diagnostic(
        overlay,
        summary,
        render_mode="manual_overlay_settled",
    )
    module.AhmadTkOverlay._record_summary_diagnostic(
        overlay,
        summary,
        render_mode="manual_overlay_settled",
    )

    rows = (tmp_path / module.SUMMARY_DIAGNOSTIC_LOG).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["render_mode"] == "manual_overlay_settled"
    assert row["manual_active"] is True
    assert row["settlement_values_hidden"] is True
    assert row["reference"]["balanced"] == "685,641"
    assert row["evidence"]["public_numeric_summary"] == "金均价 34288.75"
    assert row["evidence"]["candidate_summary"] == "总件 38 · 总格 83"
    assert row["evidence"]["next_info_hint"] == "优先补白绿件数或均格"
    assert row["ref_v0"]["evidence"]["avg_values"] == {"q5": 34288.75}
    assert row["ref_v0"]["evidence"]["fixed_counts"] == {"q5": 4}
    assert row["truth"]["total_value"] == 728211
    assert row["diagnostics"]["rare_signals"]["present"] is True
    assert row["diagnostics"]["rare_signals"]["role_counts"] == {
        "diagnostic_only": 1,
        "ref_v0_constraint": 1,
    }
    assert row["diagnostics"]["rare_signals"]["actions"][0]["action_id"] == 100121


def test_ahmad_portable_diagnostic_profile_skips_continuous_ui_summary(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "portable"
    overlay._manual_active = False
    overlay.settlement_values_hidden = False
    overlay._last_summary_log_signature = None

    module.AhmadTkOverlay._record_summary_diagnostic(
        overlay,
        {"status": "ok", "context": {"session_id": "s1"}},
        render_mode="live",
    )

    assert not (tmp_path / module.SUMMARY_DIAGNOSTIC_LOG).exists()
    assert module._normalize_diagnostic_profile("stable") == "portable"


def test_ahmad_topmost_toggle_updates_root_and_popups() -> None:
    module = _ahmad_overlay_module()

    class Window:
        def __init__(self) -> None:
            self.attrs: list[tuple[str, bool]] = []

        def attributes(self, key: str, value: bool) -> None:
            self.attrs.append((key, value))

    class Button:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Window()
    overlay._minimap_popup = Window()
    overlay._pinned_minimap_popup = Window()
    overlay.topmost_button = Button()
    overlay.topmost_tip = Tip()
    overlay.topmost_enabled = True

    assert module.AhmadTkOverlay.toggle_topmost(overlay) == "break"

    assert overlay.topmost_enabled is False
    assert overlay.root.attrs[-1] == ("-topmost", False)
    assert overlay._minimap_popup.attrs[-1] == ("-topmost", False)
    assert overlay._pinned_minimap_popup.attrs[-1] == ("-topmost", False)
    assert overlay.topmost_button.kwargs["text"] == "T"
    assert overlay.topmost_tip.text == module.TOPMOST_OFF_TIP


def test_settlement_action_cell_toggles_clickable_and_restores() -> None:
    """U4: 结算时「动作」单元格变可点「查看对局记录」, 切到其它状态恢复普通标签并清掉点击态。"""
    module = _ahmad_overlay_module()

    class FakeLabel:
        def __init__(self, fg: str) -> None:
            self.kw = {
                "fg": fg,
                "text": "",
                "font": (module.FONT_UI, module.SUMMARY_COMPACT_FONT_SIZE, "bold"),
            }
            self.binds: dict = {}
            self._hover_tip = None

        def cget(self, key: str):
            return self.kw.get(key)

        def configure(self, **kw) -> None:
            self.kw.update(kw)

        config = configure

        def bind(self, seq, fn=None, add=None):
            del add
            if fn is None:
                value = self.binds.get(seq, "")
                return value if type(value) is str else ""
            self.binds[seq] = fn
            return f"fake-binding-{len(self.binds)}"

        def unbind(self, seq, funcid=None) -> None:
            del funcid
            self.binds.pop(seq, None)

    overlay = object.__new__(module.AhmadTkOverlay)
    lbl = FakeLabel(fg="#cccccc")
    overlay.action_rows = {"动作": lbl}
    cls = module.AhmadTkOverlay

    normal_font = lbl.kw["font"]

    # 结算 -> 可点文字；动作色变化，但字形保持与同卡普通动作一致。
    cls._apply_action_cell(overlay, "结算完成", next_step="复核结算")
    assert lbl.kw["text"] == "查看对局记录"
    assert lbl.kw["cursor"] == "hand2"
    assert lbl.kw["font"] == normal_font
    assert lbl.kw["highlightthickness"] == 0
    assert "<Button-1>" in lbl.binds
    assert "<Return>" in lbl.binds
    assert "<space>" in lbl.binds
    assert lbl.kw["takefocus"] is True
    assert getattr(lbl, "_action_clickable") is True
    assert "当前动作\n  结算完成" in lbl._hover_tip.text
    assert "下一步\n  复核结算" in lbl._hover_tip.text

    # 切到出价轮 -> 恢复普通标签 + 清点击态 + 恢复原色 + 解绑
    cls._apply_action_cell(overlay, "可出价", next_step="补紫/金件数或均格")
    assert lbl.kw["text"] == "可出价 · 补紫/金件数或均格"
    assert "<Button-1>" not in lbl.binds
    assert "<Return>" not in lbl.binds
    assert "<space>" not in lbl.binds
    assert lbl.kw["cursor"] == ""
    assert lbl.kw["fg"] == "#cccccc"
    assert lbl.kw["font"] == (
        module.FONT_UI,
        module.SUMMARY_COMPACT_FONT_SIZE,
        "bold",
    )
    assert getattr(lbl, "_action_clickable") is False
    assert "下一步\n  补紫/金件数或均格" in lbl._hover_tip.text


def test_compatibility_action_cell_opens_guide_and_clears_on_recovery() -> None:
    module = _ahmad_overlay_module()

    class FakeLabel:
        def __init__(self) -> None:
            self.kw = {"fg": "#cccccc", "text": ""}
            self.binds: dict = {}
            self._hover_tip = None

        def cget(self, key: str):
            return self.kw.get(key)

        def configure(self, **kw) -> None:
            self.kw.update(kw)

        config = configure

        def bind(self, seq, fn=None, add=None):
            del add
            if fn is None:
                value = self.binds.get(seq, "")
                return value if type(value) is str else ""
            self.binds[seq] = fn
            return f"fake-binding-{len(self.binds)}"

        def unbind(self, seq, funcid=None) -> None:
            del funcid
            self.binds.pop(seq, None)

    overlay = object.__new__(module.AhmadTkOverlay)
    label = FakeLabel()
    overlay.action_rows = {"动作": label}
    cls = module.AhmadTkOverlay

    cls._apply_action_cell(
        overlay,
        "查看兼容步骤",
        next_step="按兼容顺序重启",
        compatibility_guidance=True,
    )
    assert label.kw["text"] == "查看兼容步骤 ▸"
    assert label.kw["cursor"] == "hand2"
    assert label.binds["<Button-1>"] == overlay._open_compatibility_guidance

    cls._apply_action_cell(overlay, "等待对局数据", next_step="进入对局即可")
    assert label.kw["text"] == "等待对局数据 · 进入对局即可"
    assert "<Button-1>" not in label.binds
    assert label.kw["cursor"] == ""
    assert label.kw["fg"] == "#cccccc"


def test_combined_action_binding_cycles_keep_pet_feedback_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_overlay_module()

    class FakeLabel:
        def __init__(self) -> None:
            self.kw = {
                "fg": "#cccccc",
                "text": "",
                "font": (module.FONT_UI, module.SUMMARY_COMPACT_FONT_SIZE, "bold"),
            }
            self.bindings: dict[str, object] = {
                "<Button-1>": "external-tcl-binding"
            }
            self._hover_tip = None

        def cget(self, key: str):
            return self.kw.get(key)

        def configure(self, **kw) -> None:
            self.kw.update(kw)

        config = configure

        def bind(self, sequence, callback=None, add=None):
            del add
            if callback is None:
                binding = self.bindings.get(sequence, "")
                return binding if type(binding) is str else ""
            self.bindings[sequence] = callback
            return "combined-owned-binding"

        def unbind(self, sequence, funcid=None) -> None:
            del funcid
            self.bindings.pop(sequence, None)

    class Companion:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def show_touch_feedback(self, text: str, *, duration_ms: int) -> None:
            self.calls.append(("feedback", text, duration_ms))

        def meow(self) -> None:
            self.calls.append(("action", "meow"))

    overlay = object.__new__(module.AhmadTkOverlay)
    label = FakeLabel()
    companion = Companion()
    overlay.action_rows = {"动作": label}
    overlay._pet_companion = companion
    overlay._record_pet_touch = lambda: (1, 1)
    overlay._next_pet_companion_action = lambda: "meow"
    monkeypatch.setattr(module.random, "choice", lambda _values: "喵～")

    overlay._apply_action_cell(
        "查看兼容步骤",
        next_step="按兼容顺序重启",
        compatibility_guidance=True,
    )
    overlay._apply_action_cell("结算完成", next_step="复核结算")
    overlay._apply_action_cell("等待对局数据", next_step="进入对局即可")
    assert label.bindings["<Button-1>"] == "external-tcl-binding"

    overlay._interact_with_pet_companion()
    assert companion.calls == [
        ("feedback", "互动 · 喵～  +1", 1050),
        ("action", "meow"),
    ]


def test_theme_recolour_updates_cached_action_foreground_in_both_directions() -> None:
    """主题切换必须同步动作标签的普通态缓存，不能把浅色墨色恢复到暗色卡片。"""
    module = _ahmad_overlay_module()

    class FakeLabel:
        def __init__(self, **options) -> None:
            self.options = dict(options)

        def cget(self, key: str):
            return self.options.get(key)

        def configure(self, **kwargs) -> None:
            self.options.update(kwargs)

        config = configure

        def winfo_children(self) -> list:
            return []

    overlay = object.__new__(module.AhmadTkOverlay)
    warm = module.THEMES["warm"]
    dark = module.THEMES["dark"]
    label = FakeLabel(fg=warm["ACCENT"], bg=warm["PANEL"])
    label._action_normal_fg = warm["TEXT"]

    module.AhmadTkOverlay._replace_theme_colors(
        overlay,
        label,
        module._theme_replacements(warm, dark),
    )
    assert label.cget("fg") == dark["ACCENT"]
    assert label.cget("bg") == dark["PANEL"]
    assert label._action_normal_fg == dark["TEXT"]

    module.AhmadTkOverlay._replace_theme_colors(
        overlay,
        label,
        module._theme_replacements(dark, warm),
    )
    assert label.cget("fg") == warm["ACCENT"]
    assert label.cget("bg") == warm["PANEL"]
    assert label._action_normal_fg == warm["TEXT"]


def test_pinned_minimap_sync_skips_when_user_detached(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module,
        "_monitor_work_area_for_point",
        lambda _root, _x, _y: (0, 0, 1920, 1080),
    )

    class Popup:
        def __init__(self) -> None:
            self.x = 120
            self.y = 80
            self.geometry_calls: list[str] = []

        def winfo_x(self) -> int:
            return self.x

        def winfo_y(self) -> int:
            return self.y

        def winfo_width(self) -> int:
            return 292

        def winfo_height(self) -> int:
            return 382

        def geometry(self, value: str) -> None:
            self.geometry_calls.append(value)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = type(
        "Root",
        (),
        {
            "winfo_screenwidth": lambda self: 1920,
            "winfo_screenheight": lambda self: 1080,
        },
    )()
    popup = Popup()
    overlay._pinned_minimap_popup = popup
    overlay._pinned_offset = (40, 12)
    overlay._pinned_minimap_follow = False

    module.AhmadTkOverlay._sync_pinned_minimap_position(overlay, root_x=0, root_y=0)

    assert popup.geometry_calls == []


def test_pinned_minimap_drag_detaches_from_main_window(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module,
        "_monitor_work_area_for_point",
        lambda _root, _x, _y: (0, 0, 1920, 1080),
    )

    class Popup:
        def __init__(self) -> None:
            self.x = 100
            self.y = 200
            self.geometry_calls: list[str] = []

        def winfo_x(self) -> int:
            return self.x

        def winfo_y(self) -> int:
            return self.y

        def winfo_width(self) -> int:
            return 292

        def winfo_height(self) -> int:
            return 382

        def geometry(self, value: str) -> None:
            self.geometry_calls.append(value)
            if value.startswith("+"):
                parts = value.split("+")
                self.x = int(parts[1])
                self.y = int(parts[2])

    class Title:
        def __init__(self) -> None:
            self.text = "常驻地图"
            self.updates: list[str] = []

        def cget(self, key: str) -> str:
            if key == "text":
                return self.text
            raise KeyError(key)

        def configure(self, **kwargs: str) -> None:
            if "text" in kwargs:
                self.text = str(kwargs["text"])
                self.updates.append(self.text)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = type(
        "Root",
        (),
        {
            "winfo_screenwidth": lambda self: 1920,
            "winfo_screenheight": lambda self: 1080,
        },
    )()
    popup = Popup()
    overlay._pinned_minimap_popup = popup
    overlay._pinned_offset = (50, 10)
    overlay._pinned_minimap_follow = True
    overlay._pinned_minimap_drag_offset = (5, 5)
    overlay._pinned_title = Title()

    class Event:
        x_root = 150
        y_root = 250

    assert module.AhmadTkOverlay._drag_pinned_minimap(overlay, Event()) == "break"
    assert overlay._pinned_minimap_follow is False
    assert overlay._pinned_offset is None
    assert popup.geometry_calls == ["+145+245"]
    assert overlay._pinned_title.text.endswith(module.MINIMAP_PINNED_FREE_SUFFIX)


def test_pinned_minimap_bounds_keep_secondary_monitor_coordinates(monkeypatch) -> None:
    module = _ahmad_overlay_module()

    def work_area(_root, x: int, _y: int) -> tuple[int, int, int, int]:
        if x < 0:
            return (-1920, 0, 0, 1040)
        if x >= 2560:
            return (2560, 201, 4267, 1220)
        return (0, 0, 2560, 1392)

    monkeypatch.setattr(module, "_monitor_work_area_for_point", work_area)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = object()

    # 旧实现把两个位置都夹回主屏 0..2560；新实现保留副屏的正/负桌面坐标。
    assert module.AhmadTkOverlay._popup_geometry_bounds(
        overlay,
        popup_w=292,
        popup_h=382,
        root_x=2700,
        root_y=300,
    ) == (2700, 300)
    assert module.AhmadTkOverlay._popup_geometry_bounds(
        overlay,
        popup_w=292,
        popup_h=382,
        root_x=-1800,
        root_y=200,
    ) == (-1800, 200)

    # 同一副屏内仍保留工作区边界门：越过右边和任务栏上沿时只夹回该副屏。
    assert module.AhmadTkOverlay._popup_geometry_bounds(
        overlay,
        popup_w=292,
        popup_h=382,
        root_x=4200,
        root_y=1100,
    ) == (3975, 838)


def test_minimap_popup_opens_beside_main_window_on_secondary_monitor(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module,
        "_monitor_work_area_for_point",
        lambda _root, _x, _y: (2560, 201, 4267, 1220),
    )

    class Root:
        def winfo_x(self) -> int:
            return 2560

        def winfo_y(self) -> int:
            return 201

        def winfo_width(self) -> int:
            return 510

        def winfo_height(self) -> int:
            return 500

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()

    assert module.AhmadTkOverlay._minimap_popup_position_near_root(
        overlay,
        popup_w=292,
        popup_h=382,
        right_gap=8,
    ) == (3078, 209)


def test_ahmad_taskbar_mode_switches_borderless_flag() -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.overrideredirect_values: list[bool] = []
            self.attributes_calls: list[tuple[Any, ...]] = []
            self.after_delays: list[int] = []

        def overrideredirect(self, value: bool) -> None:
            self.overrideredirect_values.append(value)

        def attributes(self, *args: Any) -> None:
            # _apply_taskbar_mode -> _apply_windows_toolwindow calls this on Windows.
            self.attributes_calls.append(args)

        def after(self, _delay_ms: int, _callback: Any = None) -> str:
            self.after_delays.append(_delay_ms)
            return "after#stub"

    root = Root()

    module._apply_taskbar_mode(root, show_taskbar=False)  # type: ignore[attr-defined]
    module._apply_taskbar_mode(root, show_taskbar=True)  # type: ignore[attr-defined]

    # Current contract: the overlay stays BORDERLESS in both modes; show_taskbar earns
    # a taskbar/Alt-Tab entry via WS_EX_APPWINDOW, not by dropping overrideredirect.
    assert root.overrideredirect_values == [True, True]
    # show_taskbar=False applies the tool-window ex-style (-toolwindow) to hide it from
    # the taskbar; show_taskbar=True does not.
    assert ("-toolwindow", True) in root.attributes_calls
    # Taskbar registration now happens once in _reveal_root_when_ready while the complete window is
    # transparent; no 150/900ms callbacks are left to hide/show an already visible UI.
    assert root.after_delays == []


def test_ahmad_reveal_root_maps_complete_taskbar_window_while_transparent(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    events: list[tuple[Any, ...]] = []

    class Root:
        def __init__(self) -> None:
            self.alpha = 1.0

        def update_idletasks(self) -> None:
            events.append(("layout", self.alpha))

        def deiconify(self) -> None:
            events.append(("show", self.alpha))

        def attributes(self, key: str, value: float | None = None) -> float | None:
            assert key == "-alpha"
            if value is None:
                events.append(("alpha-read", self.alpha))
                return self.alpha
            self.alpha = float(value)
            events.append(("alpha-set", self.alpha))
            return None

    root = Root()
    monkeypatch.setattr(module.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        module,
        "_apply_windows_appwindow",
        lambda _root, *, enabled: events.append(("appwindow", enabled, _root.alpha)),
    )

    module._reveal_root_when_ready(root, show_taskbar=True)

    assert events == [
        ("layout", 1.0),
        ("alpha-read", 1.0),
        ("alpha-set", 0.0),
        ("show", 0.0),
        ("layout", 0.0),
        ("appwindow", True, 0.0),
        ("layout", 0.0),
        ("alpha-set", 1.0),
    ]


def test_ahmad_windows_toolwindow_enabled_for_floating_overlay(monkeypatch) -> None:
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.attrs: list[tuple[str, bool]] = []

        def attributes(self, key: str, value: bool) -> None:
            self.attrs.append((key, value))

    widget = Widget()
    monkeypatch.setattr(module.os, "name", "nt", raising=False)

    module._apply_windows_toolwindow(widget, enabled=True)  # type: ignore[attr-defined]
    module._apply_windows_toolwindow(widget, enabled=False)  # type: ignore[attr-defined]

    assert widget.attrs == [("-toolwindow", True)]


def test_ahmad_compute_ui_scale_uses_min_ratio_and_clamps() -> None:
    module = _ahmad_overlay_module()

    assert module.MINI_INITIAL_HEIGHT > module.MINI_BASE_HEIGHT
    assert module.compute_ui_scale(
        440,
        397,
        base_width=module.MINI_BASE_WIDTH,
        base_height=module.MINI_BASE_HEIGHT,
    ) == 1.0
    enlarged = module.compute_ui_scale(
        880,
        794,
        base_width=module.MINI_BASE_WIDTH,
        base_height=module.MINI_BASE_HEIGHT,
    )
    assert enlarged == module.UI_SCALE_MAX
    mid = module.compute_ui_scale(
        500,
        450,
        base_width=module.MINI_BASE_WIDTH,
        base_height=module.MINI_BASE_HEIGHT,
    )
    assert mid == min(500 / module.MINI_BASE_WIDTH, 450 / module.MINI_BASE_HEIGHT)
    assert module.compute_ui_scale(
        510,
        module.MINI_INITIAL_HEIGHT,
        base_width=module.MINI_BASE_WIDTH,
        base_height=module.MINI_BASE_HEIGHT,
    ) == 510 / module.MINI_BASE_WIDTH


def test_ahmad_scaled_font_rounds_with_floor() -> None:
    module = _ahmad_overlay_module()

    assert module.scaled_font(module.FONT_UI, 12, "bold", scale=1.25) == (
        module.FONT_UI,
        15,
        "bold",
    )
    assert module.scaled_font(module.FONT_NUMERIC, 15, scale=0.5) == (
        module.FONT_NUMERIC,
        8,
    )


def test_ahmad_compute_fitted_mini_height_clamps() -> None:
    module = _ahmad_overlay_module()

    assert module.compute_fitted_mini_height(300) == 320
    assert module.compute_fitted_mini_height(10) == 320
    assert module.compute_fitted_mini_height(2000) == 1500
    assert module.compute_fitted_mini_height(302) == 320
    assert module.compute_fitted_mini_height(303) == 321


def test_ahmad_compute_mini_ui_scale_provides_default_width_scale() -> None:
    module = _ahmad_overlay_module()

    assert module.compute_mini_ui_scale(440) == 1.0
    assert module.compute_mini_ui_scale(550) == 550 / module.MINI_BASE_WIDTH
    assert module.compute_mini_ui_scale(880) == module.UI_SCALE_MAX


def test_ahmad_mini_resize_target_separates_width_and_vertical_typography() -> None:
    module = _ahmad_overlay_module()

    width, scale = module.compute_mini_resize_target(440, 400, 1.0, 0, 100)
    assert width == 440
    assert abs(scale - 1.25) < 1e-9

    width, scale = module.compute_mini_resize_target(440, 400, 1.0, 110, 0)
    assert width == 550
    assert abs(scale - 1.25) < 1e-9

    width, scale = module.compute_mini_resize_target(440, 400, 1.0, 0, -100)
    assert width == 440
    assert scale == module.UI_SCALE_MIN


def test_ahmad_end_resize_syncs_ui_scale() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay._ui_scale = 1.0
    overlay._minimap_data = {}
    overlay._scaled_layout_specs = []
    overlay.style = SimpleNamespace(configure=lambda **kwargs: None)
    overlay.outer = SimpleNamespace(winfo_children=lambda: [])
    applied: list[tuple[int, bool]] = []
    overlay.root = SimpleNamespace(geometry=lambda: "520x360+0+0", update_idletasks=lambda: None, after=lambda *a, **k: "after#0", after_cancel=lambda _id: None)
    overlay._apply_mini_resize_layout = lambda width, finalize=False: applied.append((width, finalize))  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._end_resize(overlay, SimpleNamespace()) == "break"
    assert applied == [(520, True)]


def test_ahmad_atomic_layout_update_is_nested_and_exception_safe(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    layout_flushes: list[bool] = []
    redraw_states: list[bool] = []
    overlay.root = SimpleNamespace(update_idletasks=lambda: layout_flushes.append(True))
    monkeypatch.setattr(
        module,
        "_set_windows_redraw",
        lambda _root, *, enabled: redraw_states.append(enabled) or True,
    )

    with module.AhmadTkOverlay._atomic_layout_update(overlay):
        with module.AhmadTkOverlay._atomic_layout_update(overlay):
            assert overlay._atomic_layout_depth == 2

    assert redraw_states == [False, True]
    assert layout_flushes == [True]
    assert overlay._atomic_layout_depth == 0

    try:
        with module.AhmadTkOverlay._atomic_layout_update(overlay):
            raise RuntimeError("layout failed")
    except RuntimeError as exc:
        assert str(exc) == "layout failed"
    else:
        raise AssertionError("layout exception was swallowed")
    assert redraw_states == [False, True, False, True]
    assert layout_flushes == [True, True]
    assert overlay._atomic_layout_depth == 0


def test_ahmad_resize_motion_coalesces_to_latest_pointer_each_frame() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    callbacks: list[tuple[int, Any]] = []

    class Root:
        def after(self, delay: int, callback: Any) -> str:
            callbacks.append((delay, callback))
            return f"after#{len(callbacks)}"

    overlay.root = Root()
    overlay._resize_anchor = (100, 200, 500, 400)
    overlay._resize_pending_pointer = None
    overlay._resize_frame_after_id = None
    overlay.details_expanded = True
    applied: list[str] = []
    scales: list[float] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]
    overlay._queue_ui_scale = lambda scale, force=False: scales.append(scale)  # type: ignore[method-assign]

    assert module.AhmadTkOverlay._resize_window(
        overlay, SimpleNamespace(x_root=110, y_root=220)
    ) == "break"
    assert module.AhmadTkOverlay._resize_window(
        overlay, SimpleNamespace(x_root=140, y_root=250)
    ) == "break"

    # Two high-frequency mouse events schedule one live frame and retain only the newest position.
    assert len(callbacks) == 1
    assert callbacks[0][0] == module.RESIZE_FRAME_INTERVAL_MS
    assert applied == []
    callbacks[0][1]()
    assert applied == ["540x450"]
    assert scales == [
        module.compute_ui_scale(
            540,
            450,
            base_width=module.DETAILS_BASE_WIDTH,
            base_height=module.DETAILS_BASE_HEIGHT,
        )
    ]


def test_ahmad_resize_throttles_typography_without_postponing_continuous_drag() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    callbacks: list[tuple[str, int, Any]] = []
    cancelled: list[str] = []

    class Root:
        def after(self, delay: int, callback: Any) -> str:
            after_id = f"after#{len(callbacks) + 1}"
            callbacks.append((after_id, delay, callback))
            return after_id

        def after_cancel(self, after_id: str) -> None:
            cancelled.append(after_id)

    overlay.root = Root()
    overlay._resize_active = True
    overlay._ui_scale = 1.0
    overlay._live_scale_pending = None
    overlay._live_scale_after_id = None
    applied: list[float] = []
    overlay._apply_ui_scale = lambda: applied.append(overlay._ui_scale)  # type: ignore[method-assign]

    module.AhmadTkOverlay._queue_ui_scale(overlay, 1.2)
    module.AhmadTkOverlay._queue_ui_scale(overlay, 1.3)

    assert applied == []
    assert cancelled == []
    assert [delay for _, delay, _ in callbacks] == [module.UI_SCALE_LIVE_INTERVAL_MS]
    # The first scheduled callback consumes the newest target. Repeated pointer events do not move
    # the deadline, so continuous drag receives a real intermediate render instead of a release jump.
    callbacks[0][2]()
    assert applied == [1.3]

    module.AhmadTkOverlay._queue_ui_scale(overlay, 1.5)
    assert [delay for _, delay, _ in callbacks] == [
        module.UI_SCALE_LIVE_INTERVAL_MS,
        module.UI_SCALE_LIVE_INTERVAL_MS,
    ]


def test_ahmad_live_mini_scale_refits_height_before_release() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    geometry_calls: list[tuple[str, bool]] = []
    overlay.details_expanded = False
    overlay._resize_active = True
    overlay.shell = SimpleNamespace()
    overlay.root = SimpleNamespace(
        update_idletasks=lambda: None,
        geometry=lambda: "700x480+20+20",
        winfo_reqheight=lambda: 526,
    )
    overlay._custom_mini_size = None
    overlay._estimate_mini_content_height = lambda flush=False: 526  # type: ignore[method-assign]
    overlay._apply_window_geometry = (  # type: ignore[method-assign]
        lambda size, flush=True: geometry_calls.append((size, flush))
    )

    module.AhmadTkOverlay._refit_mini_during_live_scale(overlay)

    assert overlay._custom_mini_size == (700, 526)
    assert geometry_calls == [("700x526", False)]


def test_ahmad_resize_toolbar_wraps_only_while_target_width_is_too_narrow() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    events: list[tuple[str, str, dict[str, Any]]] = []

    class Holder:
        def __init__(self, width: int) -> None:
            self.width = width

        def winfo_reqwidth(self) -> int:
            return self.width

    class Widget:
        def __init__(self, name: str, width: int = 60) -> None:
            self.name = name
            self.master = Holder(width)

        def winfo_reqwidth(self) -> int:
            return self.master.winfo_reqwidth()

        def pack_forget(self) -> None:
            events.append((self.name, "pack_forget", {}))

        def grid(self, **kwargs: Any) -> None:
            events.append((self.name, "grid", kwargs))

        def grid_forget(self) -> None:
            events.append((self.name, "grid_forget", {}))

        def pack(self, **kwargs: Any) -> None:
            events.append((self.name, "pack", kwargs))

    names = ("settlement", "export", "map", "theme", "manual", "mode")
    controls = [Widget(name) for name in names]
    (
        overlay.settlement_button,
        overlay.export_diag_button,
        overlay.map_button,
        overlay.theme_button,
        overlay.manual_button,
        overlay.mode_button,
    ) = controls
    overlay.status = Holder(86)
    overlay._resize_shell_chrome_width = 30
    overlay._resize_toolbar_compact = False

    module.AhmadTkOverlay._update_resize_toolbar_layout(overlay, 510)

    assert overlay._resize_toolbar_compact is True
    grid_events = [event for event in events if event[1] == "grid"]
    assert [(event[2]["row"], event[2]["column"]) for event in grid_events] == [
        (0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)
    ]
    event_count = len(events)
    module.AhmadTkOverlay._update_resize_toolbar_layout(overlay, 510)
    assert len(events) == event_count, "同一布局档位不应重复触发 geometry manager"

    module.AhmadTkOverlay._update_resize_toolbar_layout(overlay, 600)

    assert overlay._resize_toolbar_compact is False
    assert [event[0] for event in events if event[1] == "pack"] == list(reversed(names))


def test_ahmad_resize_preview_explains_mini_auto_height_and_details_size() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)

    class Tip:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, str]] = []
            self.tip = None

        def show_at_coords(self, x: int, y: int, text: str) -> None:
            self.calls.append((x, y, text))

    tip = Tip()
    overlay._active_resize_tip = tip
    overlay.details_expanded = False
    module.AhmadTkOverlay._show_resize_preview(
        overlay, 510, 477, 1.16, x_root=100, y_root=200
    )
    assert "宽度 510" in tip.calls[-1][2]
    assert "目标排版 116%" in tip.calls[-1][2]
    assert "左右调宽 · 上下调字号" in tip.calls[-1][2]

    overlay.details_expanded = True
    module.AhmadTkOverlay._show_resize_preview(
        overlay, 800, 900, 1.25, x_root=110, y_root=210
    )
    assert "800 × 900" in tip.calls[-1][2]
    assert "125%" in tip.calls[-1][2]


def test_ahmad_apply_mini_resize_layout_preserves_last_live_height_between_frames() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay._ui_scale = 1.0
    overlay._minimap_data = {}
    overlay._scaled_layout_specs = []
    overlay.style = SimpleNamespace(configure=lambda **kwargs: None)
    overlay.outer = SimpleNamespace(winfo_children=lambda: [])
    overlay.shell = SimpleNamespace(winfo_reqheight=lambda: 330)
    overlay._resize_active = True
    overlay.root = SimpleNamespace(
        update_idletasks=lambda: None,
        geometry=lambda: "520x348+0+0",
    )
    applied: list[str] = []
    measured: list[int] = []
    estimate_flushes: list[bool] = []

    overlay._apply_ui_scale = lambda: None  # type: ignore[method-assign]
    overlay._queue_ui_scale = lambda scale, force=False: None  # type: ignore[method-assign]
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]
    overlay._estimate_mini_content_height = (  # type: ignore[method-assign]
        lambda *, flush=True: estimate_flushes.append(bool(flush)) or 348
    )
    overlay._measure_window_height_for_width = lambda width, probe_height=None: measured.append(width) or 400  # type: ignore[method-assign]

    module.AhmadTkOverlay._apply_mini_resize_layout(overlay, 520, finalize=False)
    overlay._resize_active = False
    module.AhmadTkOverlay._apply_mini_resize_layout(overlay, 520, finalize=True)

    assert applied == ["520x348", "520x400"]
    assert measured == [520]
    assert estimate_flushes == []


def test_ahmad_ui_prefs_roundtrip(tmp_path, monkeypatch) -> None:
    module = _ahmad_overlay_module()
    # UI 偏好存【包外每用户目录】%LOCALAPPDATA%\bidking(换新包不丢配色), 非快照同级。
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    snapshot_path = tmp_path / "pkg" / "data" / "logs" / "live" / "latest_snapshot.json"
    prefs_path = module.ui_prefs_path_for_snapshot(snapshot_path)  # 走包外目录(_app_dir 建目录)
    assert prefs_path == tmp_path / "bidking" / module.UI_PREFS_FILENAME  # 包外, 不在快照(包内)目录下
    payload = {
        "schema_version": module.UI_PREFS_SCHEMA_VERSION,
        "theme_name": "dark",
        "details_expanded": False,
        "ui_scale": 1.12,
        "pet_preferences": {
            "enabled": True,
            "color": "ragdoll",
            "settlement_feedback_enabled": False,
            "position": [-1580, 720],
        },
        "pet_intro_seen": True,
        "pet_discovery_seen": True,
        "pet_touch_date": module.normalize_pet_touch_state(None, 0, 0)[0],
        "pet_touch_today": 7,
        "pet_touch_total": 42,
        "pet_trial_seconds_remaining": 437,
        "mini_ui_scale": 1.24,
        "window_position": [120, 40],
        "custom_mini_size": [520, 360],
        "custom_details_size": None,
    }
    module.write_ui_prefs(prefs_path, payload)
    loaded = module.read_ui_prefs(prefs_path)
    assert loaded == {
        "schema_version": module.UI_PREFS_SCHEMA_VERSION,
        "theme_name": "dark",
        "details_expanded": False,
        "ui_scale": 1.12,
        "pet_preferences": {
            "enabled": True,
            "color": "ragdoll",
            "settlement_feedback_enabled": False,
            "position": [-1580, 720],
        },
        "pet_intro_seen": True,
        "pet_discovery_seen": True,
        "pet_touch_date": module.normalize_pet_touch_state(None, 0, 0)[0],
        "pet_touch_today": 7,
        "pet_touch_total": 42,
        "pet_trial_seconds_remaining": 437,
        "mini_ui_scale": 1.24,
        "window_position": [120, 40],
        "custom_mini_size": [520, 360],
        "custom_details_size": None,
    }


def test_pet_preferences_persist_only_for_explicit_annual_or_permanent_access() -> None:
    module = _ahmad_overlay_module()

    def _overlay_for(access, *, pending=None):
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay.root = SimpleNamespace(geometry=lambda: "510x449+20+0")
        overlay._current_window_size = lambda: (510, 449)  # type: ignore[method-assign]
        overlay._consume_pet_trial_time = lambda: None  # type: ignore[method-assign]
        overlay._current_pet_access = lambda: access  # type: ignore[method-assign]
        overlay.theme_name = "warm"
        overlay.details_expanded = False
        overlay._ui_scale = 1.0
        overlay._pet_enabled = True
        overlay._pet_color = "navy"
        overlay._pet_position = (-1580, 720)
        overlay._pet_settlement_feedback_enabled = False
        overlay._pet_companion = None
        overlay._pet_intro_seen = True
        overlay._pet_discovery_seen = True
        (
            overlay._pet_touch_date,
            overlay._pet_touch_today,
            overlay._pet_touch_total,
        ) = module.normalize_pet_touch_state(None, 7, 42)
        overlay._pet_trial_seconds_remaining = 437
        overlay._custom_mini_scale = None
        overlay._custom_mini_size = None
        overlay._custom_details_size = None
        overlay._pending_pet_preferences = pending
        return overlay

    free = module.PetAccessState(
        "trial_active",
        True,
        437,
        "trial_active",
    )
    free_payload = module.AhmadTkOverlay._collect_ui_prefs_payload(
        _overlay_for(free)
    )
    assert "pet_preferences" not in free_payload
    assert free_payload["pet_trial_seconds_remaining"] == 437
    assert free_payload["pet_touch_total"] == 42

    annual = module.PetAccessState(
        "full",
        True,
        None,
        "eligible_plan",
        "365d",
    )
    annual_payload = module.AhmadTkOverlay._collect_ui_prefs_payload(
        _overlay_for(annual)
    )
    assert annual_payload["pet_preferences"] == {
        "enabled": True,
        "color": "navy",
        "settlement_feedback_enabled": False,
        "position": [-1580, 720],
    }

    saved = {
        "enabled": True,
        "color": "orange",
        "settlement_feedback_enabled": False,
        "position": [560, 100],
    }
    checking = module.PetAccessState(
        "checking",
        False,
        None,
        "checking",
    )
    checking_payload = module.AhmadTkOverlay._collect_ui_prefs_payload(
        _overlay_for(checking, pending=saved)
    )
    assert checking_payload["pet_preferences"] == saved

    short_plan = module.PetAccessState(
        "locked",
        False,
        None,
        "entitlement_pending",
        "30d",
    )
    short_payload = module.AhmadTkOverlay._collect_ui_prefs_payload(
        _overlay_for(short_plan, pending=saved)
    )
    assert "pet_preferences" not in short_payload

    restored = _overlay_for(annual, pending=saved)
    restored._pet_enabled = False
    restored._pet_color = "ragdoll"
    restored._pet_position = None
    restored._pet_settlement_feedback_enabled = True
    restored._pet_restore_pending = False
    assert (
        module.AhmadTkOverlay._resolve_pending_pet_preferences(restored, annual)
        == "applied"
    )
    assert restored._pet_enabled
    assert restored._pet_color == "orange"
    assert restored._pet_position == (560, 100)
    assert not restored._pet_settlement_feedback_enabled
    assert restored._pet_restore_pending

    rejected = _overlay_for(free, pending=saved)
    rejected._pet_enabled = False
    rejected._pet_color = "ragdoll"
    rejected._pet_position = None
    rejected._pet_restore_pending = False
    assert (
        module.AhmadTkOverlay._resolve_pending_pet_preferences(rejected, free)
        == "cleared"
    )
    assert not rejected._pet_enabled
    assert rejected._pet_color == "ragdoll"
    assert rejected._pet_position is None


def test_ahmad_ui_prefs_restore_clamps_only_disconnected_monitor_positions(
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    cases = (
        ((-2035, 315), (0, 0, 2560, 1392), "595x489+0+315"),
        ((-2000, 315), (-2560, 0, 0, 1392), "595x489+-2000+315"),
    )
    for saved_position, work_area, expected_geometry in cases:
        overlay = object.__new__(module.AhmadTkOverlay)
        applied: list[str] = []
        overlay.root = SimpleNamespace(
            geometry=lambda value: applied.append(value),
            update_idletasks=lambda: None,
        )
        overlay._pending_window_position = saved_position
        overlay._current_window_size = lambda: (595, 489)  # type: ignore[method-assign]
        monkeypatch.setattr(
            module,
            "_monitor_work_area_for_point",
            lambda _widget, _x, _y, area=work_area: area,
        )

        module.AhmadTkOverlay._apply_pending_window_position(overlay)

        assert applied == [expected_geometry]
        assert overlay._pending_window_position is None


def test_ahmad_ui_prefs_path_falls_back_to_snapshot_sibling(tmp_path, monkeypatch) -> None:
    """取不到包外每用户目录时(_app_dir 抛错)回退快照同级目录 — dev/异常兜底, 不崩。"""
    module = _ahmad_overlay_module()
    import bidking_lab.secure.client_activate as ca

    def _boom() -> Path:
        raise RuntimeError("no app dir")

    monkeypatch.setattr(ca, "_app_dir", _boom)
    snapshot_path = tmp_path / "latest_snapshot.json"
    assert module.ui_prefs_path_for_snapshot(snapshot_path) == tmp_path / module.UI_PREFS_FILENAME


def test_ahmad_save_ui_prefs_skips_when_window_destroyed(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._ui_prefs_enabled = True
    overlay._ui_prefs_save_after_id = "pending"
    overlay._ui_prefs_path = "ignored.json"
    overlay.root = SimpleNamespace(winfo_exists=lambda: False)

    writes: list[object] = []
    monkeypatch.setattr(module, "write_ui_prefs", lambda *a, **k: writes.append(a))

    module.AhmadTkOverlay._save_ui_prefs_if_enabled(overlay)

    assert writes == []
    assert overlay._ui_prefs_save_after_id is None


def test_ahmad_save_ui_prefs_swallows_tclerror_after_destroy(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._ui_prefs_enabled = True
    overlay._ui_prefs_save_after_id = None
    overlay._ui_prefs_path = "ignored.json"

    def _raise() -> bool:
        raise module.tk.TclError("application has been destroyed")

    overlay.root = SimpleNamespace(winfo_exists=_raise)

    writes: list[object] = []
    monkeypatch.setattr(module, "write_ui_prefs", lambda *a, **k: writes.append(a))

    module.AhmadTkOverlay._save_ui_prefs_if_enabled(overlay)

    assert writes == []


def test_unload_windivert_driver_removes_service_inside_package(tmp_path, monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.os, "name", "nt")
    root = tmp_path
    binary = f"\\??\\{root}\\BidKingHeroMonitor\\_internal\\pydivert\\windivert_dll\\WinDivert64.sys"

    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> SimpleNamespace:
        calls.append(cmd)
        if cmd[:2] == ["sc", "qc"]:
            return SimpleNamespace(returncode=0, stdout=f"        BINARY_PATH_NAME   : {binary}\n")
        return SimpleNamespace(returncode=0, stdout="")

    result = module._unload_windivert_driver_under(root, runner=runner)

    assert result is True
    assert ["sc", "stop", "WinDivert"] in calls
    assert ["sc", "delete", "WinDivert"] in calls


def test_unload_windivert_driver_skips_service_outside_package(tmp_path, monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.os, "name", "nt")
    binary = "\\??\\C:\\Windows\\System32\\drivers\\WinDivert64.sys"

    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> SimpleNamespace:
        calls.append(cmd)
        if cmd[:2] == ["sc", "qc"]:
            return SimpleNamespace(returncode=0, stdout=f"BINARY_PATH_NAME : {binary}\n")
        return SimpleNamespace(returncode=0, stdout="")

    result = module._unload_windivert_driver_under(tmp_path, runner=runner)

    assert result is False
    assert ["sc", "stop", "WinDivert"] not in calls
    assert ["sc", "delete", "WinDivert"] not in calls


def test_unload_windivert_driver_handles_missing_service(tmp_path, monkeypatch) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.os, "name", "nt")

    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=1060, stdout="The specified service does not exist")

    result = module._unload_windivert_driver_under(tmp_path, runner=runner)

    assert result is False
    assert calls == [["sc", "qc", "WinDivert"]]


def test_settle_initial_layout_grows_details_height_to_fit_content() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay._custom_details_size = (520, 300)
    overlay.root = SimpleNamespace(winfo_exists=lambda: True, update_idletasks=lambda: None)
    overlay._sync_ui_scale_from_window = lambda **k: None  # type: ignore[method-assign]
    overlay._current_window_size = lambda: (520, 300)  # type: ignore[method-assign]
    overlay._measure_window_height_for_width = (  # type: ignore[method-assign]
        lambda width, probe_height=None: 460
    )
    overlay._screen_size = lambda: (1920, 1080)  # type: ignore[method-assign]
    applied: list[str] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]

    module.AhmadTkOverlay._settle_initial_layout(overlay)

    assert applied == ["520x460"]
    assert overlay._custom_details_size == (520, 460)


def test_settle_initial_layout_refits_mini_to_content() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay.root = SimpleNamespace(winfo_exists=lambda: True, update_idletasks=lambda: None)
    overlay._current_window_size = lambda: (480, 360)  # type: ignore[method-assign]
    calls: list[tuple[int, bool]] = []
    overlay._apply_mini_resize_layout = (  # type: ignore[method-assign]
        lambda width, finalize=False: calls.append((width, finalize))
    )

    module.AhmadTkOverlay._settle_initial_layout(overlay)

    assert calls == [(480, True)]


def test_settle_initial_layout_noop_when_window_destroyed() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay.root = SimpleNamespace(winfo_exists=lambda: False)
    applied: list[str] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]

    module.AhmadTkOverlay._settle_initial_layout(overlay)

    assert applied == []


def test_grow_window_for_manual_grows_to_fit_panel() -> None:
    # Entering 手填 grows the (taller) manual content; the window must grow to fit
    # so the mid-window panel isn't clipped below the fold.
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay._custom_details_size = (520, 300)
    overlay.root = SimpleNamespace(update_idletasks=lambda: None)
    overlay._current_window_size = lambda: (520, 300)  # type: ignore[method-assign]
    overlay._measure_window_height_for_width = (  # type: ignore[method-assign]
        lambda width, probe_height=None: 470
    )
    overlay._screen_size = lambda: (1920, 1080)  # type: ignore[method-assign]
    applied: list[str] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]

    module.AhmadTkOverlay._grow_window_for_manual(overlay)

    assert applied == ["520x470"]
    assert overlay._custom_details_size == (520, 470)


def test_grow_window_for_manual_noop_when_collapsed() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay.root = SimpleNamespace(update_idletasks=lambda: None)
    applied: list[str] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]

    module.AhmadTkOverlay._grow_window_for_manual(overlay)

    assert applied == []


def test_grow_window_for_manual_never_shrinks() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay._custom_details_size = None
    overlay.root = SimpleNamespace(update_idletasks=lambda: None)
    overlay._current_window_size = lambda: (520, 600)  # type: ignore[method-assign]
    overlay._measure_window_height_for_width = (  # type: ignore[method-assign]
        lambda width, probe_height=None: 460
    )
    overlay._screen_size = lambda: (1920, 1080)  # type: ignore[method-assign]
    applied: list[str] = []
    overlay._apply_window_geometry = lambda size, flush=True: applied.append(size)  # type: ignore[method-assign]

    module.AhmadTkOverlay._grow_window_for_manual(overlay)

    # measured (460) < current (600): keep the taller window, never shrink.
    assert applied == []


def _stub_open_manual_panel_overlay(module, *, mapped: bool, flag: bool):
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = flag
    overlay.details_card = SimpleNamespace(winfo_ismapped=lambda: mapped)
    toggles: list[bool] = []
    overlay.toggle_details = lambda: toggles.append(True)  # type: ignore[method-assign]
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay.manual_entries = {}
    overlay._has_manual_inputs = lambda: False  # type: ignore[method-assign]
    overlay._manual_values_from_summary = lambda data: {}  # type: ignore[method-assign]
    overlay._is_settlement_summary = lambda data: False  # type: ignore[method-assign]
    overlay._set_manual_edit_enabled = lambda enabled: None  # type: ignore[method-assign]
    overlay._set_manual_settlement_button_state = lambda: None  # type: ignore[method-assign]
    overlay._grow_window_for_manual = lambda: None  # type: ignore[method-assign]
    return overlay, toggles


def test_open_manual_panel_reexpands_when_flag_desynced() -> None:
    # details_expanded claims expanded, but the card is actually unmapped (the
    # group-reported "手填 shows nothing"). Reconcile to reality and re-expand.
    module = _ahmad_overlay_module()
    overlay, toggles = _stub_open_manual_panel_overlay(module, mapped=False, flag=True)

    result = module.AhmadTkOverlay.open_manual_panel(overlay)

    assert result == "break"
    assert overlay.details_expanded is False  # reconciled from winfo_ismapped()
    assert toggles == [True]  # auto-expanded
    assert overlay._manual_auto_expanded_details is True


def test_open_manual_panel_expands_when_collapsed_even_if_card_still_in_pack_tree() -> None:
    """mapped=True 但 flag=False = mini 态(卡片仍在 pack 树里, 只是窗口缩回去挡住了它) -> 必须展开。

    ⚠️本测试原本断言的是相反行为("mapped=True 就当已可见, 不展开"), 该假设已被作者 2026-07-11 的
    实测【直接证伪】, 是一个真实死锁的根源(审计窗口取证):

        [manual-diag] flag_before=False winfo_ismapped=False auto_expanded=True  -> 展开      (第1次, 正常)
        [manual-diag] flag_before=False winfo_ismapped=True  auto_expanded=False -> 跳过展开   (第2次起, 永久卡死)

    机制: `winfo_ismapped()` 回答的是"控件在不在 pack 树里", **不是**"用户看不看得见"。收回 mini 时
    只把窗口高度缩回去(510x454), details_card 仍留在 pack 树中(实测 Canvas req_h=648 y=422, 被挡在
    可视区外), 于是 winfo_ismapped() 恒为 True -> auto_expanded 恒为 False -> toggle_details() 永远
    不被调用 -> 手填面板再也打不开(按钮态却照样翻成"实时")。「详情」按钮走同一判断, 一起废。

    正确语义: 以 details_expanded(模式标志)为准; 只在"标志说已展开、卡片却真的不在 pack 树里"时才纠偏。
    反向(标志 False + mapped True)是 mini 态的正常情形, 绝不能据此认为已展开。
    """
    module = _ahmad_overlay_module()
    overlay, toggles = _stub_open_manual_panel_overlay(module, mapped=True, flag=False)

    result = module.AhmadTkOverlay.open_manual_panel(overlay)

    assert result == "break"
    assert toggles == [True], "mini 态下点手填必须展开(修前这里永久卡死)"
    assert overlay._manual_auto_expanded_details is True


def test_ahmad_ui_prefs_clamps_invalid_sizes() -> None:
    module = _ahmad_overlay_module()

    normalized = module.normalize_ui_prefs_payload(
        {
            "custom_mini_size": [200, 100],
            "custom_details_size": [2000, 5000],
        }
    )
    assert normalized is not None
    assert normalized["custom_mini_size"] == [430, 320]
    assert normalized["custom_details_size"] == [1200, 1500]


def test_ahmad_ui_prefs_migrates_pet_defaults_and_rejects_invalid_position() -> None:
    module = _ahmad_overlay_module()

    normalized = module.normalize_ui_prefs_payload(
        {
            "schema_version": 1,
            "pet_color": "unknown",
            "pet_position": ["bad", 20],
        }
    )

    assert normalized is not None
    assert normalized["schema_version"] == module.UI_PREFS_SCHEMA_VERSION
    assert normalized["pet_preferences"] == {
        "enabled": False,
        "color": "ragdoll",
        "settlement_feedback_enabled": True,
    }
    assert normalized["pet_intro_seen"] is False
    assert normalized["pet_discovery_seen"] is False
    assert normalized["pet_touch_today"] == 0
    assert normalized["pet_touch_total"] == 0
    assert normalized["pet_trial_seconds_remaining"] is None
    assert "pet_enabled" not in normalized
    assert "pet_color" not in normalized
    assert "pet_position" not in normalized


def test_ahmad_canvas_draw_size_uses_layout_not_stale_default() -> None:
    module = _ahmad_overlay_module()
    canvas = SimpleNamespace(
        update_idletasks=lambda: None,
        winfo_width=lambda: 400,
        winfo_height=lambda: 360,
    )

    assert module.canvas_draw_size(canvas, min_width=260, min_height=320) == (400, 360)


def test_ahmad_draw_minimap_empty_state_centers_in_visible_canvas() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    rects: list[tuple[int, int, int, int]] = []
    texts: list[tuple[float, float]] = []

    class Canvas:
        def update_idletasks(self) -> None:
            return None

        def winfo_width(self) -> int:
            return 400

        def winfo_height(self) -> int:
            return 360

        def delete(self, _tag: str) -> None:
            return None

        def configure(self, **_kwargs: Any) -> None:
            return None

        def create_rectangle(self, x0: int, y0: int, x1: int, y1: int, **_kwargs: Any) -> int:
            rects.append((x0, y0, x1, y1))
            return 1

        def create_line(self, *_args: Any, **_kwargs: Any) -> int:
            return 1

        def create_text(self, x: float, y: float, **_kwargs: Any) -> int:
            texts.append((x, y))
            return 1

    module.AhmadTkOverlay._draw_minimap(
        overlay,
        Canvas(),
        {},
        None,
        min_width=260,
        min_height=320,
    )
    x0, y0, x1, y1 = rects[0]
    grid_w = x1 - x0
    grid_h = y1 - y0
    assert x0 == round((400 - grid_w) / 2)
    assert y0 == round((360 - grid_h) / 2)
    assert texts[0] == (200.0, 180.0)


def test_ahmad_current_window_size_prefers_geometry_over_winfo() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay.root = SimpleNamespace(
        geometry=lambda: "440x360+20+0",
        winfo_width=lambda: 920,
        winfo_height=lambda: 880,
        update_idletasks=lambda: None,
    )
    overlay.shell = SimpleNamespace(winfo_reqwidth=lambda: 420, winfo_reqheight=lambda: 342)

    assert module.AhmadTkOverlay._current_window_size(overlay) == (440, 360)


def test_ahmad_set_details_mode_mini_restores_saved_size_and_scale() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay._mini_layout_snapshot = ((480, 360), (480, 360), 0.91)
    overlay._custom_mini_size = (480, 360)
    overlay._ui_scale = 1.6
    overlay._minimap_data = {}
    overlay._scaled_layout_specs = []
    overlay.mode_button = SimpleNamespace(configure=lambda **kwargs: None)
    overlay.details_card = SimpleNamespace(pack_forget=lambda: None)
    overlay.minimap_card = SimpleNamespace(pack_forget=lambda: None)
    overlay.outer = SimpleNamespace(winfo_children=lambda: [])
    overlay.style = SimpleNamespace(configure=lambda **kwargs: None)
    applied: list[str] = []
    scales: list[float] = []
    geometry_state = {"value": "920x880+20+0"}

    def _geometry(value: str | None = None) -> str:
        if value is not None:
            geometry_state["value"] = value
        return geometry_state["value"]

    overlay.root = SimpleNamespace(
        minsize=lambda *args: None,
        update_idletasks=lambda: None,
        geometry=_geometry,
        winfo_reqheight=lambda: 348,
        after_cancel=lambda _id: None,
    )

    def _apply(size: str, flush: bool = True) -> None:
        applied.append(size)
        geometry_state["value"] = f"{size}+20+0"

    overlay._apply_window_geometry = _apply  # type: ignore[method-assign]
    overlay._apply_layout_scale = lambda: None  # type: ignore[method-assign]
    overlay._apply_button_style_scale = lambda: None  # type: ignore[method-assign]
    overlay._apply_font_scale = lambda widget: None  # type: ignore[method-assign]
    overlay.shell = SimpleNamespace(winfo_reqheight=lambda: 330)
    overlay.details_expanded = False

    module.AhmadTkOverlay._restore_mini_layout_after_details(overlay)
    scales.append(float(overlay._ui_scale))

    assert applied == ["480x348"]
    assert overlay._custom_mini_size == (480, 348)
    assert abs(scales[-1] - 0.91) < 1e-9
    assert scales[-1] < 1.6


def test_ahmad_export_button_is_mini_visible_and_map_button_keeps_preview_hover() -> None:
    module = _ahmad_overlay_module()
    init = _class_method_ast(module, "AhmadTkOverlay", "__init__")

    export_parent = None
    map_tip_assigned = False
    map_button_binds: list[str] = []
    for node in ast.walk(init):
        if (
            isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Attribute)
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id == "self"
            and node.targets[0].attr == "export_diag_button"
            and isinstance(node.value, ast.Call)
            and node.value.args
            and isinstance(node.value.args[0], ast.Name)
        ):
            export_parent = node.value.args[0].id
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "bind"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
            and node.func.value.attr == "map_button"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            map_button_binds.append(str(node.args[0].value))
        if (
            isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Attribute)
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id == "self"
            and node.targets[0].attr == "map_tip"
        ):
            map_tip_assigned = True

    assert export_parent == "header_right"
    assert "<Enter>" in map_button_binds
    assert "<Leave>" in map_button_binds
    assert "<Button-1>" in map_button_binds
    assert map_tip_assigned is False


def test_ahmad_export_diagnostic_tip_requires_private_support_delivery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: tmp_path)
    text = module._diagnostic_export_tip(tmp_path / "latest_snapshot.json")  # type: ignore[attr-defined]

    assert "异常" in text
    assert "当前可用的对局状态和近期日志" in text
    assert str(tmp_path / "BidKingLive" / "exports") in text
    assert "脱敏" not in text
    assert "仅私发给支持人员" in text
    assert "不要发群或公开上传" in text


def test_quality_summary_rows_are_ordered_from_high_to_low_quality() -> None:
    module = _ahmad_overlay_module()

    assert tuple(
        item[0] if isinstance(item, tuple) else item
        for item in module.QUALITY_SUMMARY_ROW_ORDER
    ) == ("红件", "红格", "金品", "紫品", "蓝品", "白绿")


@pytest.mark.parametrize(
    ("structured_key", "before", "after"),
    (
        ("avg_cells", {"q3": 1.0}, {"q3": 1.1666666}),
        ("avg_cells", {"q1": 2.0}, {"q1": 2.14}),
        ("quality_cells", {"q5": 2}, {"q5": 3}),
        ("quality_values", {"q4": 60_000}, {"q4": 65_229}),
    ),
)
def test_live_inference_signature_tracks_same_round_structured_quality_updates(
    structured_key: str,
    before: dict[str, float | int],
    after: dict[str, float | int],
) -> None:
    """Decoded same-frame evidence must refresh now, not at the next round."""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    snapshot = {
        "session_id": "2521:same-round",
        "map_id": 2521,
        "round": 4,
        "phase": "bidding",
        "hero": "ahmed",
        "inference_signature": ["2521:same-round", 2521, 3, 99, 7, [], [], [], []],
        "structured_ref_inputs": {structured_key: before},
        "latest_bids": {"A": 100},
    }

    initial = module.AhmadTkOverlay._live_inference_signature(overlay, snapshot)
    snapshot["latest_bids"] = {"A": 999_999}
    assert module.AhmadTkOverlay._live_inference_signature(overlay, snapshot) == initial

    snapshot["structured_ref_inputs"] = {structured_key: after}
    assert module.AhmadTkOverlay._live_inference_signature(overlay, snapshot) != initial


def test_quality_display_freshness_requires_current_hard_pair(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    candidate = SimpleNamespace(key="q5", status="ready", candidates=(object(),))
    empty = SimpleNamespace(key="q5", status="ready", candidates=())
    monkeypatch.setattr(
        module,
        "detail_composition_view",
        lambda summary: SimpleNamespace(
            status="ready",
            qualities=(candidate if summary.get("paired") else empty,),
        ),
    )
    base = {
        "context": {"session_id": "safe", "round": 1, "phase": "bidding"},
        "paired": False,
    }
    assert not module._current_summary_has_fresh_paired_quality(base)
    base["paired"] = True
    assert module._current_summary_has_fresh_paired_quality(base)
    base["continuity"] = {"source_round": "0"}
    assert not module._current_summary_has_fresh_paired_quality(base)
    base["continuity"]["current_joint_exact_qualities"] = ["q5"]
    assert module._current_summary_fresh_paired_quality_keys(base) == {"q5"}
    base["continuity"] = {"source_round": "1"}
    assert module._current_summary_has_fresh_paired_quality(base)


def test_safe_frame_telemetry_is_bounded_and_value_free(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.red_count_triplet_labels = {}
    overlay._summary_worker_seq = 9
    for index in range(70):
        module.AhmadTkOverlay._record_safe_frame_telemetry(
            overlay,
            {
                "context": {
                    "session_id": f"PRIVATE-{index // 2}",
                    "round": index % 5,
                },
                "ahmed_ref": {"notes": ["aisha_quote_pass:item"]},
            },
            paired_presence=True,
        )
    path = tmp_path / module.SAFE_FRAME_TELEMETRY_LOG
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == module.SAFE_FRAME_TELEMETRY_LIMIT
    assert all(set(row) == {
        "schema", "session_seq", "round", "applied_seq", "paired_presence",
        "red_paired_physical", "q6_status", "q6_correlation_state",
        "q6_reason_code", "q6_candidate_count", "q6_candidates_physical",
        "q6_triplet_complete", "total_count_present", "total_count_sources",
        "current_direct_action",
        "item_signal", "item_pass_applied", "hard_joint_quote_floor_applied",
        "font_size_min",
    } for row in rows)
    assert all(row["schema"] == 3 for row in rows)
    assert all(row["q6_reason_code"] == "unavailable" for row in rows)
    assert all(row["q6_candidate_count"] == 0 for row in rows)
    assert all(row["total_count_present"] is False for row in rows)
    assert all(row["total_count_sources"] == [] for row in rows)
    assert "PRIVATE" not in path.read_text(encoding="utf-8")


def test_rapid_summary_results_apply_only_newest_seq(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_seq = 8
    overlay._summary_worker_running = True
    overlay._summary_worker_signature = (8, 8)
    overlay._summary_worker_mode = "thread_rollback"
    overlay._summary_worker_executor = None
    overlay._summary_worker_pending = None
    overlay._compute_pending_trace_seq = 0
    overlay.snapshot_path = Path("latest_snapshot.json")
    applied: list[str] = []
    overlay._apply_live_summary = lambda _snapshot, summary: applied.append(
        summary["context"]["session_id"]
    )
    overlay._clear_compute_pending = lambda *_args, **_kwargs: None
    overlay._record_ui_runtime_status = lambda *_args, **_kwargs: None
    overlay._maybe_start_pending_summary_worker = lambda: None
    overlay._mark_compute_timestamp = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        module.AhmadTkOverlay, "_summary_result_owner_is_closed", lambda *_args: False
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_summary_result_superseded_by_compute_trace",
        lambda *_args: False,
    )
    overlay._summary_result_queue.put(
        {
            "seq": 7,
            "signature": (7, 7),
            "snapshot": {"session_id": "old"},
            "summary": {"context": {"session_id": "old"}},
        }
    )
    overlay._summary_result_queue.put(
        {
            "seq": 8,
            "signature": (8, 8),
            "snapshot": {"session_id": "current"},
            "summary": {"context": {"session_id": "current"}},
        }
    )

    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert applied == ["current"]


def test_ahmad_ui_runtime_status_dedupes_repeated_state(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._last_signature = (1, 2)
    overlay._last_summary = {
        "status": "ok",
        "context": {"session_id": "2401:test", "phase": "bidding"},
        "reference": {"source": "ref_v0"},
    }
    overlay._act_diag = {
        "elapsed_ms": 321.0,
        "activated": False,
        "mode": "none",
        "reason": "offline",
        "source": "server",
        "next_retry_seconds": 2.0,
        "consecutive_transient_failures": 1,
    }

    overlay._record_ui_runtime_status(
        "idle_no_change",
        snapshot_signature=(1, 2),
        capture_status={"source": "windivert", "raw_packets": 1},
    )
    status_path = tmp_path / module.UI_RUNTIME_STATUS
    first = status_path.read_text(encoding="utf-8")

    overlay._record_ui_runtime_status(
        "idle_no_change",
        snapshot_signature=(1, 2),
        capture_status={"source": "windivert", "raw_packets": 1},
    )
    assert status_path.read_text(encoding="utf-8") == first

    overlay._record_ui_runtime_status(
        "idle_no_change",
        snapshot_signature=(1, 2),
        capture_status={"source": "windivert", "raw_packets": 2},
    )
    updated = json.loads(status_path.read_text(encoding="utf-8"))
    assert updated["capture"]["raw_packets"] == 2
    assert updated["activation"]["elapsed_ms"] == 321.0
    assert updated["activation"]["next_retry_seconds"] == 2.0


def test_ahmad_export_diagnostic_package_defaults_to_redacted_support_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: tmp_path)
    monkeypatch.setattr(
        module,
        "_validated_external_live_root",
        lambda _path, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(module, "_show_export_in_explorer", lambda _path: True)
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    raw_json = tmp_path / "fatbeans_webhook_live.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_tables_dir = tmp_path / "data" / "raw" / "tables"
    raw_tables_dir.mkdir(parents=True)
    raw_jsonl = raw_dir / "fatbeans_webhook_live.jsonl"
    ui_log = tmp_path / module.SUMMARY_DIAGNOSTIC_LOG
    ui_runtime = tmp_path / module.UI_RUNTIME_STATUS
    monitor_stderr = tmp_path / "monitor.stderr.log"
    raw_json.write_text('[{"SortID":1}]', encoding="utf-8")
    raw_jsonl.write_text('{"SortID":1}\n', encoding="utf-8")
    ui_log.write_text('{"render_mode":"live"}\n', encoding="utf-8")
    ui_runtime.write_text('{"event":"idle_no_change"}', encoding="utf-8")
    monitor_stderr.write_text("[listen] ok\n", encoding="utf-8")
    (tmp_path / "upload_status.txt").write_text(
        "2026-07-16 19:47:40 OK 200 host=collector map=5610 hero=gabriela\n",
        encoding="utf-8",
    )
    (tmp_path / "upload_status.jsonl").write_text(
        "2026-07-16 19:47:40 OK 200 host=collector map=5610 hero=gabriela\n",
        encoding="utf-8",
    )
    for name in ("BidMap.txt", "Drop.txt", "Item.txt"):
        (raw_tables_dir / name).write_text(name, encoding="utf-8")
    snapshot = {
        "schema_version": 1,
        "created_at": 123456.0,
        "session_id": "2401:test",
        "hero": "ahmed",
        "map_id": 2401,
        "round": 2,
        "phase": "bidding",
        "n_trials": 500,
        "roi_trials": 250,
        "shadow_trials": 80,
        "formal_mode": "v3_practical",
        "raw_capture": str(raw_json),
        "raw_capture_jsonl": str(raw_jsonl),
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._last_live_snapshot = snapshot
    overlay._last_summary = {
        "status": "ok",
        "context": {"session_id": "2401:test", "hero": "ahmed"},
        "reference": {"source": "ref_v0", "readiness": "live_ready"},
        "evidence": {
            "candidate_summary": "总件 38 · 总格 83",
            "next_info_hint": "优先补白绿件数或均格",
        },
    }
    overlay._last_live_summary = {}
    overlay.status = Widget()
    overlay.manual_status = Widget()
    overlay.export_diag_tip = Tip()
    overlay.show_taskbar = True

    assert module.AhmadTkOverlay.export_diagnostic_package(overlay) == "break"

    export_path = overlay.last_diagnostic_export_path
    assert export_path is not None
    assert export_path.is_file()
    assert str(export_path.resolve()) in overlay.export_diag_tip.text
    assert overlay.status.kwargs["text"] == "已导出诊断"
    with zipfile.ZipFile(export_path) as archive:
        names = set(archive.namelist())
        assert "latest_snapshot.json" in names
        assert "fatbeans_webhook_live.json" not in names
        assert "raw/fatbeans_webhook_live.jsonl" not in names
        assert module.SUMMARY_DIAGNOSTIC_LOG in names
        assert module.UI_RUNTIME_STATUS in names
        assert "monitor.stderr.log" in names
        assert "upload_status.txt" in names
        assert "upload_status.jsonl" in names
        assert "hero_ref_current_summary.json" in names
        assert "BUILD_EXPORT_MANIFEST.json" in names
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json").decode("utf-8"))
        assert manifest["version"]["schema_version"] == 1
        assert manifest["parameters"]["formal_mode"] == "v3_practical"
        assert manifest["parameters"]["n_trials"] == 500
        assert manifest["current_summary"]["candidate_summary"] == "总件 38 · 总格 83"
        assert manifest["current_summary"]["next_info_hint"] == "优先补白绿件数或均格"
        assert manifest["startup"]["launch_mode"] == "taskbar"
        assert manifest["startup"]["show_taskbar"] is True
        assert manifest["package"]["is_public_safe"] is False
        assert manifest["package"]["includes_raw_tables"] is True
        assert manifest["log_summary"]["raw_tables"]["present"] is True
        assert manifest["log_summary"]["raw_tables"]["required_present"] is True
        assert manifest["log_summary"]["diagnostic_profile"] == "engineering"
        assert manifest["log_summary"]["continuous_ui_summary"] is True
        assert manifest["package"]["privacy_mode"] == "support-safe"
        assert manifest["package"]["contains_unredacted_raw"] is False
        assert manifest["log_summary"]["export_includes_raw"] is False
        assert manifest["log_summary"]["export_includes_ui_summary"] is True
        assert manifest["log_summary"]["latest_snapshot"]["exists"] is True
        assert "ui_health" in manifest["log_summary"]
        assert manifest["log_summary"]["ui_runtime_status"]["exists"] is True
        assert manifest["log_summary"]["monitor_stderr"]["exists"] is True
        assert manifest["log_summary"]["upload_status"]["exists"] is True
        assert manifest["log_summary"]["upload_status_history"]["exists"] is True
        assert manifest["log_summary"]["ui_summary"]["exists"] is True
        assert manifest["log_summary"]["included_count"] >= 4


def test_scoped_health_log_drops_stale_tail_keeps_recent(tmp_path: Path) -> None:
    """_scoped_health_log must exclude days-old rows (the cumulative-tail misattribution
    that made a 06-24 stall read as a 07-04-game stall) while keeping the current sitting.
    Unparseable lines are kept (never silently drop unknown signal)."""
    module = _ahmad_overlay_module()
    now = 1_783_000_000.0
    health = tmp_path / module.UI_HEALTH_LOG
    stale = now - 10 * 86400  # 10 days old -> must be dropped
    recent = now - 120  # 2 min old -> must be kept
    health.write_text(
        "\n".join(
            [
                json.dumps({"logged_at": stale, "event": "ui_event_loop_stall_suspected", "gap_seconds": 10.1}),
                json.dumps({"logged_at": recent, "event": "ui_event_loop_stall_suspected", "gap_seconds": 5.2}),
                "not-json-keep-me",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    text, stats = module._scoped_health_log(health, now_ts=now)

    assert text is not None
    kept = [json.loads(l) for l in text.splitlines() if l.strip().startswith("{")]
    assert [r["logged_at"] for r in kept] == [recent]  # stale row gone, recent kept
    assert "not-json-keep-me" in text  # unparseable line preserved
    assert stats["filtered"] is True
    assert stats["full_lines"] == 3
    assert stats["kept_lines"] == 2  # recent row + unparseable line
    assert stats["dropped_stale_lines"] == 1
    assert stats["full_span"] == [stale, recent]


def test_note_pool_fallback_logs_once_per_session() -> None:
    """The pool->in-thread fallback is otherwise silent; _note_pool_fallback records it ONCE
    (guard flag) so a real recurrence is unambiguous without spamming the health log."""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = Path("latest_snapshot.json")
    rows: list[dict[str, Any]] = []
    overlay._record_ui_health = lambda row: rows.append(row)  # type: ignore[method-assign,assignment]

    module.AhmadTkOverlay._note_pool_fallback(overlay, "pool_submit_failed", "BrokenProcessPool()")
    module.AhmadTkOverlay._note_pool_fallback(overlay, "pool_create_failed", "again")

    assert len(rows) == 1
    assert rows[0]["event"] == "ref_engine_pool_fallback_in_thread"
    assert rows[0]["reason"] == "pool_submit_failed"


def test_scoped_health_log_missing_file_falls_back_to_raw(tmp_path: Path) -> None:
    """Read failure -> (None, stats) so the export bundles the raw file unchanged
    rather than dropping the health log over a filtering nicety."""
    module = _ahmad_overlay_module()
    text, stats = module._scoped_health_log(tmp_path / "nope.jsonl", now_ts=1_783_000_000.0)
    assert text is None
    assert stats["filtered"] is False


def test_diagnostic_export_scopes_health_log_to_recent_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """End-to-end: a health log with a stale tail + a recent row is bundled with ONLY the
    recent row, and the manifest carries the scope stats so the reader knows filtering ran."""
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: tmp_path)
    monkeypatch.setattr(
        module,
        "_validated_external_live_root",
        lambda _path, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(module, "_show_export_in_explorer", lambda _path: True)
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    now = time.time()
    health = tmp_path / module.UI_HEALTH_LOG
    stale_ts = now - 10 * 86400
    recent_ts = now - 60
    health.write_text(
        "\n".join(
            [
                json.dumps({"logged_at": stale_ts, "event": "ui_event_loop_stall_suspected", "gap_seconds": 9.9}),
                json.dumps({"logged_at": recent_ts, "event": "idle_no_change"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = {
        "schema_version": 1,
        "created_at": now,
        "session_id": "2401:test",
        "hero": "tatiana",
        "map_id": 2401,
        "round": 3,
        "phase": "bidding",
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args: Any, **kwargs: Any) -> None:
            pass

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._last_live_snapshot = snapshot
    overlay._last_summary = {
        "status": "ok",
        "context": {"session_id": "2401:test", "hero": "tatiana"},
        "reference": {"source": "ref", "readiness": "live_ready"},
        "evidence": {},
    }
    overlay._last_live_summary = {}
    overlay.status = Widget()
    overlay.manual_status = Widget()
    overlay.export_diag_tip = Tip()
    overlay.show_taskbar = False

    assert module.AhmadTkOverlay.export_diagnostic_package(overlay) == "break"
    export_path = overlay.last_diagnostic_export_path
    with zipfile.ZipFile(export_path) as archive:
        assert module.UI_HEALTH_LOG in set(archive.namelist())
        bundled = archive.read(module.UI_HEALTH_LOG).decode("utf-8")
        rows = [json.loads(l) for l in bundled.splitlines() if l.strip()]
        assert [r["event"] for r in rows] == ["idle_no_change"]  # stale stall dropped
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json").decode("utf-8"))
        scope = manifest["log_summary"]["ui_health_scope"]
        assert scope["filtered"] is True
        assert scope["full_lines"] == 2
        assert scope["kept_lines"] == 1
        assert scope["dropped_stale_lines"] == 1


def test_ahmad_diagnostic_export_works_without_latest_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: tmp_path)
    monkeypatch.setattr(
        module,
        "_validated_external_live_root",
        lambda _path, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(module, "_show_export_in_explorer", lambda _path: True)
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    ui_runtime = tmp_path / module.UI_RUNTIME_STATUS
    monitor_stderr = tmp_path / "monitor.stderr.log"
    monitor_lock = tmp_path / "monitor.lock"
    ui_runtime.write_text(
        json.dumps({"event": "waiting_for_snapshot", "capture": {"wait_state": "no_capture_status"}}),
        encoding="utf-8",
    )
    monitor_stderr.write_text("[error] pydivert is not installed\n", encoding="utf-8")
    monitor_lock.write_text('{"pid":1234}', encoding="utf-8")

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._last_live_snapshot = {}
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay.status = Widget()
    overlay.manual_status = Widget()
    overlay.export_diag_tip = Tip()
    overlay.show_taskbar = False
    overlay.diagnostic_profile = "portable"

    assert module.AhmadTkOverlay.export_diagnostic_package(overlay) == "break"

    export_path = overlay.last_diagnostic_export_path
    assert export_path is not None
    with zipfile.ZipFile(export_path) as archive:
        names = set(archive.namelist())
        assert "latest_snapshot.json" not in names
        assert module.UI_RUNTIME_STATUS in names
        assert "monitor.stderr.log" in names
        assert "monitor.lock" not in names
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json").decode("utf-8"))
        assert manifest["session_id"] == "no_snapshot"
        assert manifest["version"]["source_file"] is None
        assert manifest["log_summary"]["latest_snapshot"]["exists"] is False
        assert manifest["log_summary"]["ui_runtime_status"]["exists"] is True
        assert manifest["log_summary"]["monitor_stderr"]["exists"] is True
    assert overlay.status.kwargs["text"] == "已导出诊断"


def test_ahmad_public_safe_diagnostic_export_omits_raw_and_ui_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_jsonl = raw_dir / "windivert_live.jsonl"
    ui_log = tmp_path / module.SUMMARY_DIAGNOSTIC_LOG
    ui_health = tmp_path / module.UI_HEALTH_LOG
    ui_runtime = tmp_path / module.UI_RUNTIME_STATUS
    capture_status = tmp_path / "capture_source_status.json"
    helper_status = tmp_path / "wss_helper_status.json"
    network_status = tmp_path / "network_connection_status.json"
    raw_jsonl.write_text('{"SortID":1}\n', encoding="utf-8")
    ui_log.write_text('{"render_mode":"live"}\n', encoding="utf-8")
    ui_health.write_text('{"event":"ui_event_loop_stall_suspected"}\n', encoding="utf-8")
    ui_runtime.write_text('{"event":"waiting_for_snapshot"}', encoding="utf-8")
    capture_status.write_text('{"source":"windivert"}', encoding="utf-8")
    helper_status.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "exited_unexpectedly",
                "helper_exit_code": 1,
                "error_code": "wss_redirector_elevation_denied",
            }
        ),
        encoding="utf-8",
    )
    network_status.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "recognized",
            }
        ),
        encoding="utf-8",
    )
    snapshot = {
        "schema_version": 1,
        "created_at": 123456.0,
        "session_id": "2401:test",
        "hero": "ahmed",
        "map_id": 2401,
        "round": 2,
        "phase": "bidding",
        "formal_mode": "v3_practical",
        "raw_capture": str(raw_jsonl),
        "raw_capture_jsonl": str(raw_jsonl),
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    export_path = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "2401:test"}},
        diagnostic_profile="public-safe",
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )

    with zipfile.ZipFile(export_path) as archive:
        names = set(archive.namelist())
        assert "latest_snapshot.json" in names
        assert "capture_source_status.json" in names
        assert "wss_helper_status.json" in names
        assert "network_connection_status.json" in names
        assert "hero_ref_current_summary.json" in names
        assert "BUILD_EXPORT_MANIFEST.json" in names
        assert module.UI_HEALTH_LOG in names
        assert module.UI_RUNTIME_STATUS in names
        assert module.SUMMARY_DIAGNOSTIC_LOG not in names
        assert "raw/windivert_live.jsonl" not in names
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json").decode("utf-8"))
        assert manifest["startup"]["launch_mode"] == "floating"
        assert manifest["startup"]["show_taskbar"] is False
        assert manifest["package"]["is_public_safe"] is True
        assert manifest["package"]["includes_raw_tables"] is False
        assert manifest["log_summary"]["raw_tables"]["present"] is False
        assert manifest["log_summary"]["diagnostic_profile"] == "public-safe"
        assert manifest["log_summary"]["ui_runtime_status"]["exists"] is True
        assert manifest["log_summary"]["continuous_ui_summary"] is False
        assert manifest["log_summary"]["export_includes_raw"] is False
        assert manifest["log_summary"]["export_includes_ui_summary"] is False


def test_default_diagnostic_export_redacts_canaries_and_excludes_raw_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    quote_receipt = _coherent_quote_receipt()
    quote_identity = _quote_identity_for_receipt(quote_receipt)
    snapshot_path = tmp_path / "latest_snapshot.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_capture = raw_dir / "windivert_live.jsonl"
    raw_capture.write_text('{"raw_only":"RAW_PACKET_CANARY"}\n', encoding="utf-8")
    (tmp_path / "upload_status.jsonl").write_text(
        json.dumps(
            {
                "player_ranks": {
                    "甲": {"p": 1, "t": 2, "id": "ONE_CHAR_STABLE_ID_CANARY"},
                    "乙丙": {"p": 3, "t": 4, "id": "TWO_CHAR_STABLE_ID_CANARY"},
                },
                "latest_bids": {"甲": 100, "乙丙": 200},
                "note": "JSONL 中甲和乙丙都出现",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "capture_source_status.json").write_text(
        json.dumps({"note": "普通 JSON 中甲、乙丙和三字玩家都出现"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "monitor_log_session.json").write_text(
        json.dumps(
            {
                "launch_id": "11111111-2222-4333-8444-555555555555",
                "started_at": 123456.0,
                "stdout_file": "monitor.stdout.log",
                "stderr_file": "monitor.stderr.log",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "wss_helper_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "exited_unexpectedly",
                "launch_id": "11111111-2222-4333-8444-555555555555",
                "helper_exit_code": 1,
                "error_code": "wss_redirector_elevation_denied",
                "raw_stderr": "token=PRIVATE_HELPER_TOKEN endpoint=198.51.100.9",
            }
        ),
        encoding="utf-8",
    )
    token = "v1|private-user|100|9999999999|r." + "a" * 64
    snapshot = {
        "schema_version": 1,
        "session_id": "2401:test",
        "hero": "ahmed",
        "player": "PRIVATE_PLAYER_CANARY",
        "self_name": "PRIVATE_SELF_NAME_CANARY",
        "local_player_id": "PRIVATE_LOCAL_PLAYER_ID_CANARY",
        "player_ranks": {
            "三字玩家": {
                "id": "STABLE-ID-CANARY",
                "rank": 2,
            },
            "甲": {"p": 1, "t": 2, "id": "ONE_CHAR_STABLE_ID_CANARY"},
            "乙丙": {"p": 3, "t": 4, "id": "TWO_CHAR_STABLE_ID_CANARY"},
        },
        "latest_bids": {"三字玩家": 1234, "甲": 1235, "乙丙": 1236},
        "player_id": "PRIVATE_TOP_LEVEL_PLAYER_ID_CANARY",
        "device_id": "PRIVATE_DEVICE_CANARY",
        "token": token,
        "Data": "PRIVATE_PACKET_BASE64_CANARY",
        "snapshot_path": r"C:\Users\example\Documents\latest_snapshot.json",
        "raw_capture_jsonl": str(raw_capture),
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    (tmp_path / "monitor.stderr.log").write_text(
        "api_key=PRIVATE_KEY_CANARY Bearer PRIVATE_BEARER_CANARY "
        "203.0.113.77 AA-BB-CC-DD-EE-FF private@example.com "
        r"C:\Users\example\Documents"
        + " ordinary text mentions 三字玩家、甲、乙丙、STABLE-ID-CANARY "
          "and PRIVATE_SELF_NAME_CANARY\n",
        encoding="utf-8",
    )

    support = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={
            "status": "ok",
            "player": "PRIVATE_SUMMARY_PLAYER",
            "latest_bids": {"PRIVATE_SUMMARY_BIDDER_CANARY": 2345},
            "quote_receipt": quote_receipt,
            "quote_identity": quote_identity,
        },
        diagnostic_profile="portable",
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(support) as archive:
        names = set(archive.namelist())
        members = {
            name: archive.read(name).decode("utf-8", errors="replace")
            for name in names
        }
    assert "raw/windivert_live.jsonl" not in names
    assert "monitor_log_session.json" in names
    assert "wss_helper_status.json" in names
    for canary in (
        "PRIVATE_PLAYER_CANARY",
        "PRIVATE_SELF_NAME_CANARY",
        "PRIVATE_LOCAL_PLAYER_ID_CANARY",
        "三字玩家",
        "甲",
        "乙丙",
        "STABLE-ID-CANARY",
        "ONE_CHAR_STABLE_ID_CANARY",
        "TWO_CHAR_STABLE_ID_CANARY",
        "PRIVATE_TOP_LEVEL_PLAYER_ID_CANARY",
        "PRIVATE_DEVICE_CANARY",
        "PRIVATE_PACKET_BASE64_CANARY",
        token,
        "PrivateUser",
        "PRIVATE_KEY_CANARY",
        "PRIVATE_BEARER_CANARY",
        "203.0.113.77",
        "AA-BB-CC-DD-EE-FF",
        "private@example.com",
        "PRIVATE_SUMMARY_PLAYER",
        "PRIVATE_SUMMARY_BIDDER_CANARY",
        "RAW_PACKET_CANARY",
        "PRIVATE_HELPER_TOKEN",
        "198.51.100.9",
    ):
        leaking_members = [name for name, text in members.items() if canary in text]
        assert not leaking_members, f"{canary} leaked in ZIP members: {leaking_members}"
    assert any("<redacted>" in text for text in members.values())
    exported_summary = json.loads(members["hero_ref_current_summary.json"])
    assert exported_summary["quote_receipt"] == quote_receipt
    assert exported_summary["quote_identity"] == quote_identity

    raw = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        diagnostic_profile="engineering",
        raw_forensics=True,
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(raw) as archive:
        raw_names = set(archive.namelist())
        raw_members = {
            name: archive.read(name).decode("utf-8", errors="replace")
            for name in raw_names
        }
        assert b"RAW_PACKET_CANARY" in archive.read("raw/windivert_live.jsonl")
        assert any("STABLE-ID-CANARY" in text for text in raw_members.values())
        assert any("甲" in text and "乙丙" in text for text in raw_members.values())
        raw_manifest = json.loads(raw_members["BUILD_EXPORT_MANIFEST.json"])
        assert raw_manifest["package"]["privacy_mode"] == "raw-forensics"
        assert raw_manifest["package"]["contains_unredacted_raw"] is True


def test_round_snapshots_require_explicit_raw_forensics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "windivert_live.jsonl").write_text('{"SortID":1}\n', encoding="utf-8")
    round_dir = tmp_path / "round_snapshots"
    round_dir.mkdir()
    (round_dir / "r02_bidding.json").write_text('{"round":2}', encoding="utf-8")
    (round_dir / "r03_bidding.json").write_text('{"round":3}', encoding="utf-8")
    snapshot = {
        "schema_version": 1,
        "session_id": "2402:test",
        "hero": "ahmed",
        "map_id": 2402,
        "round": 3,
        "phase": "bidding",
        "raw_capture_jsonl": str(raw_dir / "windivert_live.jsonl"),
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    portable = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "2402:test"}},
        diagnostic_profile="portable",
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(portable) as archive:
        names = set(archive.namelist())
    assert "round_snapshots/r02_bidding.json" not in names
    assert "round_snapshots/r03_bidding.json" not in names

    raw = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "2402:test"}},
        diagnostic_profile="engineering",
        raw_forensics=True,
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(raw) as archive:
        raw_names = set(archive.namelist())
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json").decode("utf-8"))
    assert "round_snapshots/r02_bidding.json" in raw_names
    assert "round_snapshots/r03_bidding.json" in raw_names
    assert manifest["package"]["privacy_mode"] == "raw-forensics"
    assert manifest["package"]["contains_unredacted_raw"] is True


def test_overlay_summary_lines_include_q6_and_diagnostics() -> None:
    overlay = _overlay_module()
    lines = overlay._summary_lines(
        {
            "hero": "ethan",
            "map_id": 2401,
            "round": 3,
            "known_value_sum": 801824,
            "panel": {
                "summary_rows": (
                    {
                        "topic": "当前最高价是否可追",
                        "conclusion": "可守不抢",
                        "detail": "停止价 587,459",
                    },
                ),
                "layout_stages": (
                    {
                        "stage": "R3 / sort 20",
                        "known_cells": "90",
                        "estimate": "120/140/160",
                        "confidence": "中",
                        "risk": "中",
                    },
                ),
            },
            "v2_posterior_rows": (
                {
                    "q6样本率": "12.0%",
                    "q6件数 P10/P50/P90": "0 / 1 / 2",
                    "q6价值 P10/P50/P90": "0 / 120,000 / 360,000",
                    "q6先验缺口": "件数P90低1.00",
                    "q6先验风险参考": "486,510",
                    "q6实战参考P90": "486,510",
                    "诊断": "footprint_overlap_cells:2",
                },
            ),
            "model_eval": {
                "q6_p90_misses_truth": True,
                "layout_conflict": True,
                "decision_value_p50_error": -442506,
                "warehouse_p50_error": 8,
            },
        }
    )

    assert lines[0].startswith("ETHAN  |  map 2401")
    assert any(line.startswith("决策:") for line in lines)
    assert any(line.startswith("红货:") for line in lines)
    assert any("件数 0 / 1 / 2" in line for line in lines)
    assert any("先验缺口" in line for line in lines)
    assert any("实战参考P90" in line for line in lines)
    assert any("q6 P90" in line for line in lines)
    assert any("footprint" in line for line in lines)
    assert any("决策P50误差" in line for line in lines)


def test_overlay_warns_when_snapshot_is_stale() -> None:
    overlay = _overlay_module()

    lines = overlay._summary_lines(
        {
            "created_at": time.time() - 180,
            "map_id": 2401,
            "round": 1,
            "panel": {"summary_rows": ()},
        }
    )

    assert any("超过 120 秒未更新" in line for line in lines)


def test_overlay_warns_when_latest_inference_is_slow() -> None:
    overlay = _overlay_module()

    lines = overlay._summary_lines(
        {
            "created_at": time.time(),
            "map_id": 2401,
            "round": 1,
            "processing_seconds": 18.25,
            "panel": {"summary_rows": ()},
        }
    )

    assert any("本次推理耗时 18.2s" in line for line in lines)


def test_overlay_window_geometry_uses_compact_default() -> None:
    overlay = _overlay_module()

    geometry = overlay._default_window_geometry(1920, 1080)

    assert geometry.startswith("480x420")


def test_overlay_hover_position_stays_inside_screen_edges() -> None:
    overlay = _overlay_module()

    x, y = overlay._bounded_popup_position(
        pointer_x=1880,
        pointer_y=1030,
        popup_width=420,
        popup_height=360,
        screen_width=1920,
        screen_height=1080,
    )

    assert x + 420 + overlay.HOVER_MARGIN <= 1920
    assert y + 360 + overlay.HOVER_MARGIN <= 1080
    assert x < 1880
    assert y < 1030


def test_overlay_detail_window_size_expands_within_screen() -> None:
    overlay = _overlay_module()

    assert overlay._detail_window_size(
        1920,
        1080,
        requested_width=1200,
        requested_height=1200,
    ) == (980, 860)
    assert overlay._detail_window_size(
        900,
        700,
        requested_width=300,
        requested_height=300,
    ) == (820, 620)


def test_overlay_snapshot_signature_changes_when_file_changes(tmp_path: Path) -> None:
    overlay = _overlay_module()
    snapshot = tmp_path / "latest_snapshot.json"
    snapshot.write_text('{"a": 1}', encoding="utf-8")
    first = overlay._snapshot_file_signature(snapshot)

    snapshot.write_text('{"a": 1, "b": 2}', encoding="utf-8")
    second = overlay._snapshot_file_signature(snapshot)

    assert first != second


def test_capture_status_signature_ignores_timestamp_only_changes() -> None:
    overlay = _overlay_module()
    first = {
        "ts": 1000.0,
        "source": "windivert",
        "process_name": "BidKing.exe",
        "active_flows": 1,
        "sniffed_packets": 100,
        "raw_packets": 1,
        "accepted_frames": 0,
        "ignored_frames": 1,
        "dropped_bytes": 0,
        "active_session_id": None,
    }
    second = dict(first)
    second["ts"] = 1002.0
    second["sniffed_packets"] = 120

    assert overlay._capture_status_signature(first) == overlay._capture_status_signature(second)

    second["raw_packets"] = 2
    second["ignored_frames"] = 2
    second["dropped_bytes"] = 1
    assert overlay._capture_status_signature(first) == overlay._capture_status_signature(second)

    first["raw_packets"] = 0
    assert overlay._capture_status_signature(first) != overlay._capture_status_signature(second)


def test_ahmad_overlay_waiting_diagnostics_use_capture_status() -> None:
    overlay = _ahmad_overlay_module()
    status = {
        "ts": time.time(),
        "source": "windivert",
        "process_name": "BidKing.exe",
        "active_flows": 1,
        "sniffed_packets": 211,
        "raw_packets": 211,
        "accepted_frames": 0,
        "ignored_frames": 66,
        "ignored_reasons": {"rev_not_game_frame": 63},
        "active_session_id": "",
    }

    diagnostics = overlay._capture_wait_diagnostics(status)

    assert diagnostics["subtitle"] == "已抓到流量，但未解析到对局状态帧"
    assert diagnostics["action"] == "等待状态帧"
    assert diagnostics["recent"] == "rev_not_game_frame x63"
    assert "备用启动" in diagnostics["note"]

    status["accepted_frames"] = 3
    status["active_session_id"] = "2405:9000000000000011"
    diagnostics = overlay._capture_wait_diagnostics(status)

    assert diagnostics["subtitle"] == "已识别会话，等待估价状态帧"
    assert diagnostics["state"] == "session_waiting_snapshot"
    assert diagnostics["session"] == "2405:9000000000000011"


def test_capture_wait_diagnostics_prioritizes_npcap_device_open_error() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics({
        "source": "npcap",
        "active_flows": 1,
        "sniffed_packets": 0,
        "error_code": "npcap_device_open_failed",
        "error_message": "无法打开抓包网卡: Realtek Gaming 2.5GbE Family Controller",
        "error_hint": "混杂模式和普通抓包兼容模式均无法打开该网卡；请导出诊断。",
    })

    assert diagnostics["subtitle"] == "网卡抓包初始化失败"
    assert diagnostics["action"] == "请导出诊断"
    assert diagnostics["next_hint"] == "导出诊断后复验"
    assert diagnostics["flags"][0]["label"] == "网卡初始化失败"
    assert "加速器" not in json.dumps(diagnostics, ensure_ascii=False)


def test_capture_wait_diagnostics_uses_neutral_no_sniff_wording() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics({
        "source": "npcap",
        "active_flows": 1,
        "sniffed_packets": 0,
        "raw_packets": 0,
        "accepted_frames": 0,
    })

    assert diagnostics["subtitle"] == "检测到游戏连接，但未收到游戏网卡流量"
    assert diagnostics["action"] == "检查抓包网卡"
    assert diagnostics["flags"][0]["level"] == "watch"
    assert "疑似" not in json.dumps(diagnostics, ensure_ascii=False)


def test_capture_wait_diagnostics_treats_wss_connected_without_frames_as_normal_wait() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics({
        "source": "wss_local",
        "active_flows": 1,
        "sniffed_packets": 0,
        "raw_packets": 0,
        "accepted_frames": 0,
    })

    assert diagnostics["subtitle"] == "已连接游戏，等待对局数据"
    assert diagnostics["action"] == "等待对局数据"
    assert diagnostics["next_hint"] == "进入对局即可"
    assert diagnostics["state"] == "wss_flow_waiting_data"
    assert diagnostics["flags"] == [
        {
            "label": "游戏已连接",
            "level": "neutral",
            "detail": "flow 1 / raw 0 / accepted 0",
        }
    ]
    assert "网卡" not in json.dumps(diagnostics, ensure_ascii=False)


def test_capture_wait_diagnostics_prefers_positive_wss_history_over_closed_flow() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics({
        "source": "wss_local",
        "active_flows": 0,
        "sniffed_packets": 8,
        "raw_packets": 8,
        "accepted_frames": 6,
        "active_session_id": "2410:synthetic",
    })

    assert diagnostics["subtitle"] == "读取链路正常，等待新局状态"
    assert diagnostics["action"] == "等待状态更新"
    assert diagnostics["next_hint"] == "进入新局即可"
    assert diagnostics["state"] == "wss_session_waiting_snapshot"
    assert diagnostics["flags"][0]["label"] == "读取正常"
    assert diagnostics["flags"][0]["level"] == "neutral"


def test_capture_wait_diagnostics_separates_game_websocket_failure() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_network_connection_status": {
                "schema_version": 1,
                "game_connection_state": "failed",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "evidence": {
                    "player_log_error_counts": {
                        "websocket_unable_to_connect": 2
                    }
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "游戏连接服务器失败"
    assert diagnostics["state"] == "game_connection_failed"
    assert diagnostics["action"] == "切换网络后重试"
    assert "报价或解析故障" in diagnostics["note"]


def test_capture_wait_diagnostics_stops_showing_old_failure_after_wss_recovers() -> None:
    network = _network_status_module()
    overlay = _ahmad_overlay_module()
    capture_status = {
        "source": "wss_local",
        "active_flows": 1,
        "raw_packets": 8,
        "accepted_frames": 3,
        "active_session_id": "recovered-session",
    }
    capture_status["_network_connection_status"] = (
        network.derive_network_connection_status(
            game_running=True,
            player_log_error_counts={"websocket_unable_to_connect": 1},
            capture_status=capture_status,
            helper_status={"phase": "helper_running"},
        )
    )

    diagnostics = overlay._capture_wait_diagnostics(capture_status)

    assert diagnostics["subtitle"] == "读取链路正常，等待新局状态"
    assert diagnostics["state"] == "wss_session_waiting_snapshot"
    assert diagnostics["subtitle"] != "游戏连接服务器失败"


def test_capture_wait_diagnostics_separates_local_wss_intercept_failure() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_network_connection_status": {
                "schema_version": 1,
                "game_connection_state": "connected",
                "wss_intercept_state": "failed",
                "capture_decode_state": "idle",
                "evidence": {
                    "helper_error_code": "wss_redirector_start_failed"
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "本地读取组件启动失败"
    assert diagnostics["state"] == "wss_intercept_failed"
    assert diagnostics["action"] == "请导出诊断"


def test_capture_wait_diagnostics_labels_stable_missing_redirector() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_network_connection_status": {
                "schema_version": 1,
                "game_connection_state": "connecting",
                "wss_intercept_state": "failed",
                "capture_decode_state": "idle",
                "evidence": {
                    "helper_error_code": "wss_redirector_missing_stable"
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "本地兼容组件未接管"
    assert diagnostics["state"] == "wss_local_intercept_missing"
    assert diagnostics["action"] == "查看兼容步骤"
    assert diagnostics["next_hint"] == "按兼容顺序重启"
    assert diagnostics["flags"] == [
        {
            "label": "本地未接管",
            "level": "risk",
            "detail": "wss_redirector_missing_stable",
        }
    ]
    assert "系统安全设置不变" in diagnostics["note"]
    assert diagnostics["subtitle"] != "游戏正在连接服务器"


def test_capture_wait_diagnostics_prioritizes_preload_access_denied_recovery() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "error_code": "wss_redirector_missing_stable",
                "windivert_preload_state": "failed_access_denied",
                "windivert_preload_phase": "start_product",
                "monitor_elevation_state": "elevated",
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "connected",
                "wss_intercept_state": "failed",
                "capture_decode_state": "idle",
                "evidence": {
                    "helper_error_code": "wss_redirector_missing_stable",
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "本地兼容组件被系统拒绝"
    assert diagnostics["state"] == "wss_preload_access_denied"
    assert diagnostics["action"] == "查看兼容步骤"
    assert diagnostics["next_hint"] == "按兼容顺序重启"
    assert "不锁定具体安全软件" in diagnostics["note"]


def test_capture_wait_diagnostics_labels_current_stale_product_service_conflict() -> None:
    overlay = _ahmad_overlay_module()
    private_path = r"C:\\Users\\example\\old-package\\WinDivert64.sys"

    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "windivert_preload_state": "failed_service_conflict",
                "windivert_preload_phase": "verify_product",
                "binary_path": private_path,
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "not_running",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "evidence": {},
            },
        }
    )

    assert diagnostics["subtitle"] == "检测到旧版兼容服务残留"
    assert diagnostics["state"] == "wss_stale_product_service_conflict"
    assert diagnostics["action"] == "请联系作者修复"
    assert diagnostics["next_hint"] == "完全退出后修复并重启"
    assert diagnostics["flags"] == [
        {
            "label": "旧版服务残留",
            "level": "risk",
            "detail": "verify_product",
        }
    ]
    rendered = json.dumps(diagnostics, ensure_ascii=False)
    assert "不要更换 token" in rendered
    assert "不要处理其他 WinDivert 服务" in rendered
    assert private_path not in rendered


def test_capture_wait_diagnostics_prioritizes_same_path_mixed_generation() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "windivert_preload_state": "failed_same_path_mixed_generation",
                "windivert_preload_phase": "verify_product",
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "not_running",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "evidence": {},
            },
        }
    )

    assert diagnostics["state"] == "wss_same_path_mixed_generation"
    assert diagnostics["action"] == "完整解压到新的最终目录"
    assert diagnostics["next_hint"] == "完全退出后迁移新目录"
    assert "不要先删除旧目录" in diagnostics["note"]
    assert "不会停止、删除或重配" in diagnostics["note"]


def test_capture_wait_diagnostics_requests_reboot_after_stale_service_repair() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "windivert_preload_state": "repaired_restart_required",
                "windivert_preload_phase": "complete",
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "not_running",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "evidence": {},
            },
        }
    )

    assert diagnostics["subtitle"] == "旧版兼容服务清理已提交，需要重启"
    assert diagnostics["action"] == "重启电脑后重新启动"
    assert diagnostics["state"] == (
        "wss_stale_product_service_repaired_restart_required"
    )
    assert diagnostics["next_hint"] == "重启后先开 Hero Ref"
    assert "不要处理名为 WinDivert 的其他服务" in diagnostics["note"]


def test_capture_wait_diagnostics_does_not_override_attached_session_with_reboot() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 2,
            "accepted_frames": 1,
            "active_session_id": "current-session",
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "windivert_preload_state": "repaired_restart_required",
                "windivert_preload_phase": "complete",
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "recognized",
                "evidence": {},
            },
        }
    )

    assert diagnostics["state"] == "wss_session_waiting_snapshot"
    assert "重启" not in diagnostics["subtitle"]


def test_combined_repair_projection_preserves_action_binding_and_pet_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_overlay_module()

    class FakeLabel:
        def __init__(self) -> None:
            self.kw = {
                "fg": "#cccccc",
                "text": "",
                "font": (module.FONT_UI, module.SUMMARY_COMPACT_FONT_SIZE, "bold"),
            }
            self.bindings: dict[str, object] = {
                "<Button-1>": "external-tcl-binding"
            }
            self._hover_tip = None

        def cget(self, key: str):
            return self.kw.get(key)

        def configure(self, **kw) -> None:
            self.kw.update(kw)

        config = configure

        def bind(self, sequence, callback=None, add=None):
            del add
            if callback is None:
                binding = self.bindings.get(sequence, "")
                return binding if type(binding) is str else ""
            self.bindings[sequence] = callback
            return "combined-repair-binding"

        def unbind(self, sequence, funcid=None) -> None:
            del funcid
            self.bindings.pop(sequence, None)

    class Companion:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def show_touch_feedback(self, text: str, *, duration_ms: int) -> None:
            self.calls.append(("feedback", text, duration_ms))

        def meow(self) -> None:
            self.calls.append(("action", "meow"))

    overlay = object.__new__(module.AhmadTkOverlay)
    label = FakeLabel()
    companion = Companion()
    overlay.action_rows = {"动作": label}
    overlay._pet_companion = companion
    overlay._record_pet_touch = lambda: (1, 1)
    overlay._next_pet_companion_action = lambda: "meow"
    monkeypatch.setattr(module.random, "choice", lambda _values: "喵～")

    base_status = {
        "source": "wss_local",
        "active_flows": 0,
        "raw_packets": 0,
        "accepted_frames": 0,
        "_current_launch_id": "launch-current",
        "_wss_helper_status": {
            "launch_id": "launch-current",
            "windivert_preload_state": "repaired_restart_required",
            "windivert_preload_phase": "complete",
        },
        "_network_connection_status": {
            "schema_version": 1,
            "launch_id": "launch-current",
            "game_connection_state": "not_running",
            "wss_intercept_state": "ready",
            "capture_decode_state": "idle",
            "evidence": {},
        },
    }
    restart = module._capture_wait_diagnostics(base_status)
    overlay._apply_action_cell(
        restart["action"],
        next_step=restart["next_hint"],
        compatibility_guidance=False,
    )
    overlay._interact_with_pet_companion()

    assert restart["state"] == "wss_stale_product_service_repaired_restart_required"
    assert label.bindings["<Button-1>"] == "external-tcl-binding"
    assert overlay._pet_companion is companion
    assert companion.calls[0] == ("feedback", "互动 · 喵～  +1", 1050)

    attached = module._capture_wait_diagnostics(
        {
            **base_status,
            "active_flows": 1,
            "raw_packets": 2,
            "accepted_frames": 1,
            "active_session_id": "current-session",
            "_network_connection_status": {
                **base_status["_network_connection_status"],
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "recognized",
            },
        }
    )
    assert attached["state"] == "wss_session_waiting_snapshot"
    assert "重启" not in attached["subtitle"]
    assert overlay._pet_companion is companion


@pytest.mark.parametrize(
    ("helper_launch_id", "preload_phase"),
    (("launch-old", "verify_product"), ("launch-current", "start_product")),
)
def test_capture_wait_diagnostics_does_not_mislabel_stale_or_wrong_phase_conflict(
    helper_launch_id: str,
    preload_phase: str,
) -> None:
    overlay = _ahmad_overlay_module()

    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": helper_launch_id,
                "windivert_preload_state": "failed_service_conflict",
                "windivert_preload_phase": preload_phase,
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "not_running",
                "wss_intercept_state": "ready",
                "capture_decode_state": "idle",
                "evidence": {},
            },
        }
    )

    assert diagnostics["state"] == "game_not_running"
    assert diagnostics["subtitle"] == "读取已就绪，等待启动游戏"


def test_capture_wait_diagnostics_prefers_recovered_wss_over_old_conflict_state() -> None:
    overlay = _ahmad_overlay_module()

    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 3,
            "accepted_frames": 2,
            "active_session_id": "current-session",
            "_current_launch_id": "launch-current",
            "_wss_helper_status": {
                "launch_id": "launch-current",
                "windivert_preload_state": "failed_service_conflict",
                "windivert_preload_phase": "verify_product",
            },
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "launch-current",
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "recognized",
                "evidence": {},
            },
        }
    )

    assert diagnostics["state"] == "wss_session_waiting_snapshot"
    assert diagnostics["subtitle"] == "读取链路正常，等待新局状态"


def test_capture_wait_diagnostics_labels_known_runtime_compatibility_failure() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
            "error_code": "wss_runtime_compatibility_failed",
            "_network_connection_status": {
                "schema_version": 1,
                "game_connection_state": "connected",
                "wss_intercept_state": "failed",
                "capture_decode_state": "idle",
                "evidence": {
                    "helper_error_code": "wss_runtime_compatibility_failed"
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "本地兼容组件启动失败"
    assert diagnostics["state"] == "wss_runtime_compatibility_failed"
    assert diagnostics["action"] == "请导出诊断"
    assert diagnostics["flags"] == [
        {
            "label": "运行时兼容失败",
            "level": "risk",
            "detail": "wss_runtime_compatibility_failed",
        }
    ]
    assert "保持系统安全功能开启" in diagnostics["note"]
    assert "游戏网络、报价或解析故障" in diagnostics["note"]


@pytest.mark.parametrize(
    ("capture_status", "expected_state"),
    [
        (
            {
                "source": "wss_local",
                "active_flows": 1,
                "raw_packets": 0,
                "accepted_frames": 0,
            },
            "wss_flow_waiting_data",
        ),
        (
            {
                "source": "wss_local",
                "active_flows": 0,
                "raw_packets": 8,
                "accepted_frames": 0,
            },
            "wss_data_unrecognized",
        ),
        (
            {
                "source": "wss_local",
                "active_flows": 0,
                "raw_packets": 8,
                "accepted_frames": 3,
            },
            "wss_session_waiting_snapshot",
        ),
        (
            {
                "source": "wss_local",
                "active_flows": 0,
                "raw_packets": 0,
                "accepted_frames": 0,
                "active_session_id": "current-session",
            },
            "wss_session_waiting_snapshot",
        ),
    ],
)
def test_capture_wait_diagnostics_prefers_current_wss_health_over_old_helper_failure(
    capture_status: dict[str, object],
    expected_state: str,
) -> None:
    network = _network_status_module()
    overlay = _ahmad_overlay_module()
    helper_status = {
        "phase": "exited_unexpectedly",
        "launch_id": "current-launch",
        "error_code": "wss_runtime_compatibility_failed",
        "runtime_compatibility_state": "failed_known_historical_driver",
    }
    network_status = network.derive_network_connection_status(
        launch_id="current-launch",
        game_running=True,
        capture_status=capture_status,
        helper_status=helper_status,
    )

    diagnostics = overlay._capture_wait_diagnostics(
        {
            **capture_status,
            "error_code": "wss_runtime_compatibility_failed",
            "_current_launch_id": "current-launch",
            "_network_connection_status": network_status,
        }
    )

    assert network_status["game_connection_state"] == "connected"
    assert network_status["wss_intercept_state"] == "attached"
    assert diagnostics["state"] == expected_state
    assert diagnostics["state"] != "wss_runtime_compatibility_failed"


def test_capture_wait_diagnostics_labels_received_wss_data_as_unrecognized() -> None:
    overlay = _ahmad_overlay_module()
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 12,
            "accepted_frames": 0,
            "_network_connection_status": {
                "schema_version": 1,
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "unrecognized",
                "evidence": {
                    "active_flows": 1,
                    "wss_raw_count": 12,
                    "accepted_frames": 0,
                },
            },
        }
    )

    assert diagnostics["subtitle"] == "已收到游戏数据，当前版本尚未识别"
    assert diagnostics["state"] == "wss_data_unrecognized"
    assert "网络连接失败" in diagnostics["note"]


def test_overlay_scroll_fraction_is_clamped() -> None:
    overlay = _overlay_module()

    assert overlay._clamp_scroll_fraction(None) == 0.0
    assert overlay._clamp_scroll_fraction("bad") == 0.0
    assert overlay._clamp_scroll_fraction(-0.25) == 0.0
    assert overlay._clamp_scroll_fraction(0.4) == 0.4
    assert overlay._clamp_scroll_fraction(1.25) == 1.0


def test_overlay_exit_cleanup_terminates_unique_pids_and_removes_locks(
    tmp_path: Path,
) -> None:
    overlay = _overlay_module()
    lock_path = tmp_path / "monitor.lock"
    lock_path.write_text('{"pid": 123}', encoding="utf-8")
    terminated: list[int] = []

    overlay._cleanup_exit_targets(
        [123, 123, 456],
        [lock_path],
        terminate_fn=terminated.append,
    )

    assert terminated == [123, 456]
    assert not lock_path.exists()


def test_overlay_exit_cleanup_is_not_gated_on_user_close() -> None:
    overlay = _overlay_module()

    terminated: list[int] = []

    overlay._cleanup_exit_targets(
        [321],
        [],
        terminate_fn=terminated.append,
    )

    assert terminated == [321]


def test_overlay_detects_watched_monitor_pid_exit() -> None:
    overlay = _overlay_module()

    running = {111: True, 222: False}

    assert (
        overlay._watched_pid_exited(
            [111],
            running_fn=lambda pid: running[pid],
        )
        is False
    )
    assert (
        overlay._watched_pid_exited(
            [111, 222],
            running_fn=lambda pid: running[pid],
        )
        is True
    )


def test_overlay_section_style_classifies_key_topics() -> None:
    overlay = _overlay_module()

    assert overlay._section_style("MiniMap")["badge"] == "地图"
    assert overlay._section_style("q6 风险参考", "已触发")["tag"] == "warn"
    assert overlay._section_style("v3 实战参考", "低估风险")["badge"] == "V3"
    assert overlay._section_style("Fallback 出价参考")["badge"] == "兜底"
    assert overlay._section_style("下一步道具")["tag"] == "good"


def test_overlay_draw_minimap_renders_quality_only_markers() -> None:
    overlay = _overlay_module()
    calls: list[tuple[str, tuple, dict]] = []

    class DummyCanvas:
        def configure(self, **kwargs):
            calls.append(("configure", (), kwargs))

        def create_line(self, *args, **kwargs):
            calls.append(("line", args, kwargs))

        def create_rectangle(self, *args, **kwargs):
            calls.append(("rectangle", args, kwargs))

        def create_oval(self, *args, **kwargs):
            calls.append(("oval", args, kwargs))

    instance = overlay.Overlay.__new__(overlay.Overlay)
    overlay.Overlay._draw_minimap(
        instance,
        DummyCanvas(),
        {
            "columns": 10,
            "viewport_rows": 13,
            "max_rows": 25,
            "rows_hint": 8,
            "items": [
                {
                    "row": 2,
                    "col": 5,
                    "width": 2,
                    "height": 2,
                    "quality": 5,
                    "render_mode": "footprint",
                },
                {
                    "row": 8,
                    "col": 7,
                    "width": 1,
                    "height": 1,
                    "quality": 2,
                    "render_mode": "marker",
                },
            ],
        },
    )

    assert any(kind == "rectangle" for kind, *_ in calls)
    assert any(kind == "oval" for kind, *_ in calls)
    assert overlay._section_style("正式出价", "停止追价")["tag"] == "bad"


def test_ahmad_tk_minimap_unknown_footprint_uses_stripes_without_permanent_text() -> None:
    module = _ahmad_overlay_module()
    calls: list[tuple[str, tuple, dict]] = []

    class DummyCanvas:
        def delete(self, *args, **kwargs):
            calls.append(("delete", args, kwargs))

        def update_idletasks(self):
            calls.append(("update_idletasks", (), {}))

        def winfo_width(self):
            return 300

        def winfo_height(self):
            return 140

        def configure(self, **kwargs):
            calls.append(("configure", (), kwargs))

        def create_line(self, *args, **kwargs):
            calls.append(("line", args, kwargs))

        def create_rectangle(self, *args, **kwargs):
            calls.append(("rectangle", args, kwargs))

        def create_oval(self, *args, **kwargs):
            calls.append(("oval", args, kwargs))

        def create_text(self, *args, **kwargs):
            calls.append(("text", args, kwargs))

        def tag_bind(self, *args, **kwargs):
            calls.append(("tag_bind", args, kwargs))

    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    module.AhmadTkOverlay._draw_minimap(
        instance,
        DummyCanvas(),
        {
            "status": "available",
            "columns": 10,
            "viewport_rows": 13,
            "items": [
                {
                    "row": 4,
                    "col": 5,
                    "width": 2,
                    "height": 1,
                    "quality": None,
                    "category_label": "古董",
                    "shape_key": "21",
                    "render_mode": "footprint",
                    "tooltip": "技能 10002071 / 品质? / 21 / 古董",
                }
            ],
        },
        None,
    )

    item_calls = [entry for entry in calls if entry[2].get("tags") == ("item_0",)]
    assert any(kind == "rectangle" for kind, *_ in item_calls)
    assert any(kind == "line" for kind, *_ in item_calls)
    assert not any(kind == "text" for kind, *_ in calls)


def test_ahmad_tk_minimap_draws_explicit_marker_as_oval_even_with_shape() -> None:
    module = _ahmad_overlay_module()
    calls: list[tuple[str, tuple, dict]] = []

    class DummyCanvas:
        def delete(self, *args, **kwargs):
            calls.append(("delete", args, kwargs))

        def update_idletasks(self):
            calls.append(("update_idletasks", (), {}))

        def winfo_width(self):
            return 300

        def winfo_height(self):
            return 140

        def configure(self, **kwargs):
            calls.append(("configure", (), kwargs))

        def create_line(self, *args, **kwargs):
            calls.append(("line", args, kwargs))

        def create_rectangle(self, *args, **kwargs):
            calls.append(("rectangle", args, kwargs))

        def create_oval(self, *args, **kwargs):
            calls.append(("oval", args, kwargs))

        def create_text(self, *args, **kwargs):
            calls.append(("text", args, kwargs))

        def tag_bind(self, *args, **kwargs):
            calls.append(("tag_bind", args, kwargs))

    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    module.AhmadTkOverlay._draw_minimap(
        instance,
        DummyCanvas(),
        {
            "status": "available",
            "columns": 10,
            "viewport_rows": 13,
            "items": [
                {
                    "row": 2,
                    "col": 5,
                    "width": 2,
                    "height": 2,
                    "quality": "q6",
                    "source": "public_info",
                    "layout_source": "public_info",
                    "render_mode": "marker",
                    "shape_key": "22",
                    "cells": 4,
                }
            ],
        },
        None,
    )

    assert any(
        kind == "oval" and kwargs.get("tags") == ("item_0",)
        for kind, _args, kwargs in calls
    )
    assert not any(
        kind == "rectangle" and kwargs.get("tags") == ("item_0",)
        for kind, _args, kwargs in calls
    )


def test_ahmad_tk_minimap_skips_redraw_when_unchanged() -> None:
    module = _ahmad_overlay_module()
    calls: list[str] = []

    class DummyCanvas:
        def delete(self, *args, **kwargs):
            calls.append("delete")

        def update_idletasks(self):
            pass

        def winfo_width(self):
            return 300

        def winfo_height(self):
            return 160

        def configure(self, **kwargs):
            pass

        def create_line(self, *args, **kwargs):
            pass

        def create_rectangle(self, *args, **kwargs):
            pass

        def create_oval(self, *args, **kwargs):
            pass

        def create_text(self, *args, **kwargs):
            pass

        def tag_bind(self, *args, **kwargs):
            pass

    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    instance._minimap_canvas_signatures = {}
    canvas = DummyCanvas()

    def minimap_payload() -> dict:
        return {
            "status": "available",
            "columns": 10,
            "viewport_rows": 13,
            "items": [
                {
                    "row": 2,
                    "col": 5,
                    "width": 2,
                    "height": 2,
                    "quality": "q6",
                    "render_mode": "marker",
                    "shape_key": "22",
                    "cells": 4,
                }
            ],
        }

    module.AhmadTkOverlay._draw_minimap(instance, canvas, minimap_payload(), None)
    first_deletes = calls.count("delete")
    assert first_deletes >= 1

    # Identical data + same canvas size: redraw is skipped (no flicker).
    module.AhmadTkOverlay._draw_minimap(instance, canvas, minimap_payload(), None)
    assert calls.count("delete") == first_deletes

    # Changed data forces a redraw again.
    changed = minimap_payload()
    changed["items"][0]["row"] = 4
    module.AhmadTkOverlay._draw_minimap(instance, canvas, changed, None)
    assert calls.count("delete") == first_deletes + 1


def test_ahmad_return_to_live_collapses_auto_expanded_details() -> None:
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    toggle_calls: list[bool] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_live_session_id = ""
    overlay.manual_entries = {}
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._last_live_summary = {}
    overlay._last_summary = {}
    overlay.render = lambda data: None
    overlay.render_standby = lambda data: None
    overlay.render_missing = lambda message: None
    overlay._set_manual_edit_enabled = lambda enabled: None
    overlay._auto_sync_manual_inputs = lambda data: None
    overlay._has_manual_inputs = lambda: False
    overlay._manual_values_from_summary = lambda data: {}
    overlay._is_settlement_summary = lambda data: False
    overlay._set_manual_settlement_button_state = lambda: None

    def fake_toggle() -> None:
        overlay.details_expanded = not overlay.details_expanded
        toggle_calls.append(overlay.details_expanded)

    overlay.toggle_details = fake_toggle

    module.AhmadTkOverlay.open_manual_panel(overlay)
    assert overlay.details_expanded is True
    assert overlay._manual_auto_expanded_details is True

    module.AhmadTkOverlay.return_to_live_mode(overlay)
    assert overlay.details_expanded is False
    assert overlay._manual_auto_expanded_details is False
    assert toggle_calls == [True, False]


def test_ahmad_footer_bilibili_opens_creator_space(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    opened: list[tuple[str, int, bool]] = []

    def fake_open(url: str, *, new: int = 0, autoraise: bool = True) -> bool:
        opened.append((url, new, autoraise))
        return True

    monkeypatch.setattr(module.webbrowser, "open", fake_open)
    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)

    module.AhmadTkOverlay._open_credit_bilibili(instance)

    assert opened == [(module.CREDIT_BILIBILI_URL, 2, True)]


def test_ahmad_footer_cloud_big_data_reuses_package_url_and_bounds_clicks(
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    package_entry = (
        ROOT / "scripts" / "pkg_assets" / "公共大数据平台.html"
    ).read_text(encoding="utf-8-sig")
    opened: list[tuple[str, int, bool]] = []
    scheduled: list[tuple[int, Any]] = []

    class Button:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {"state": "normal"}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    class Tip:
        def __init__(self) -> None:
            self.text = module.CLOUD_BIG_DATA_TIP_TEXT

        def set_text(self, text: str) -> None:
            self.text = text

    class Root:
        def after(self, delay_ms: int, callback: Any) -> str:
            scheduled.append((delay_ms, callback))
            return f"after#{len(scheduled)}"

    def fake_open(url: str, *, new: int = 0, autoraise: bool = True) -> bool:
        opened.append((url, new, autoraise))
        return True

    assert module.PUBLIC_BIG_DATA_URL == "https://api.bidking.example.invalid/public"
    assert module.PUBLIC_BIG_DATA_URL in package_entry
    assert 'window.location.replace("https://api.bidking.example.invalid/public")' in package_entry
    monkeypatch.setattr(module.webbrowser, "open", fake_open)
    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    instance.root = Root()
    instance.footer_cloud_big_data = Button()
    instance.footer_cloud_big_data_tip = Tip()
    instance._cloud_big_data_opening = False
    instance._cloud_big_data_reset_after_id = None

    for _ in range(8):
        assert module.AhmadTkOverlay._open_cloud_big_data(instance) == "break"

    assert opened == [(module.PUBLIC_BIG_DATA_URL, 2, True)]
    assert instance.footer_cloud_big_data.kwargs["state"] == "disabled"
    assert instance.footer_cloud_big_data_tip.text == "正在打开大数据…"
    assert len(scheduled) == 1
    assert scheduled[0][0] == module.CLOUD_BIG_DATA_OPEN_COOLDOWN_MS

    scheduled.pop()[1]()
    assert instance._cloud_big_data_opening is False
    assert instance.footer_cloud_big_data.kwargs["state"] == "normal"
    assert instance.footer_cloud_big_data_tip.text == module.CLOUD_BIG_DATA_TIP_TEXT

    module.AhmadTkOverlay._open_cloud_big_data(instance)
    assert opened == [
        (module.PUBLIC_BIG_DATA_URL, 2, True),
        (module.PUBLIC_BIG_DATA_URL, 2, True),
    ]


def test_ahmad_footer_cloud_big_data_browser_failure_is_visible_and_fail_soft(
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    scheduled: list[Any] = []

    class Button:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {"state": "normal"}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    class Tip:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    class Root:
        def after(self, _delay_ms: int, callback: Any) -> str:
            scheduled.append(callback)
            return "after#failure"

    def fail_open(*_args: Any, **_kwargs: Any) -> bool:
        raise OSError("no default browser")

    monkeypatch.setattr(module.webbrowser, "open", fail_open)
    instance = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    instance.root = Root()
    instance.footer_cloud_big_data = Button()
    instance.footer_cloud_big_data_tip = Tip()
    instance._cloud_big_data_opening = False
    instance._cloud_big_data_reset_after_id = None

    assert module.AhmadTkOverlay._open_cloud_big_data(instance) == "break"
    assert instance.footer_cloud_big_data.kwargs["state"] == "disabled"
    assert instance.footer_cloud_big_data.kwargs["disabledforeground"] == module.BAD
    assert "打开失败" in instance.footer_cloud_big_data_tip.text

    scheduled.pop()()
    assert instance.footer_cloud_big_data.kwargs["state"] == "normal"
    assert "打开失败" in instance.footer_cloud_big_data_tip.text


def test_overlay_quality_style_distinguishes_white_and_unknown() -> None:
    overlay = _overlay_module()

    unknown = overlay._quality_style(None)
    white = overlay._quality_style(1)

    assert unknown["fill"] != white["fill"]
    assert unknown["fill"] == ""
    assert unknown["unknown"] == "1"
    assert unknown["hatch"] == "///"
    assert unknown["stipple"] == ""
    assert white["unknown"] == ""
    assert white["stipple"] == ""
    assert white["fill"].lower() == "#f8fafc"
    assert white["outline"].lower() == "#cbd5e1"


def test_demo_snapshot_has_compact_overlay_sections() -> None:
    overlay = _overlay_module()

    lines = overlay._summary_lines(overlay._demo_snapshot())
    model = overlay._overlay_model(overlay._demo_snapshot())

    assert any(line.startswith("决策:") for line in lines)
    assert any(line.startswith("价值:") for line in lines)
    assert any(line.startswith("仓储:") for line in lines)
    assert any(line.startswith("红货:") for line in lines)
    assert any(line.startswith("布局:") for line in lines)
    assert any(line.startswith("道具:") for line in lines)
    assert model["title"].startswith("ETHAN")
    assert model["decision"][0] == "可守不抢"
    assert [row[0] for row in model["metrics"]] == [
        "P50估值",
        "防守价",
        "当前最高",
        "红品数量",
    ]
    assert model["metrics"][3][1] == "件数 0 / 1 / 1"
    assert any(section[0] == "鉴影命中" for section in model["sections"])
    assert any(alert[0].startswith("q6 P90") for alert in model["alerts"])
    assert any(alert[0].startswith("q6 件数/格数低于先验") for alert in model["alerts"])
    assert model["interaction"]["hover"]["enabled"] is True
    assert model["interaction"]["detail"]["enabled"] is True
    assert model["interaction"]["mini"]["sections"] == model["sections"][:4]


def test_overlay_model_uses_ui_contract_shadow_reference() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "hero": "aisha",
            "map_id": 2501,
            "round": 4,
            "known_value_sum": 800000,
            "bid_rows": [
                {
                    "建议": "可守不抢",
                    "当前最高": "玩家A 500,000",
                    "风险带": "防守区",
                    "停止价": "680,000",
                }
            ],
            "v2_posterior_rows": [
                {
                    "q6样本率": "12.0%",
                    "q6决策价值 P10/P50/P90": "0 / 100,000 / 300,000",
                }
            ],
            "ui_contract": {
                "context": {
                    "hero": "aisha",
                    "map_id": 2501,
                    "round": 4,
                    "known_value_sum": 800000,
                },
                "source": {"file": "sample.json"},
                "baseline": {
                    "decision": {
                        "action": "可守不抢",
                        "current_highest": "玩家A 500,000",
                        "risk_band": "防守区",
                        "defend_bid": "450,000",
                        "stop_price": "680,000",
                    },
                    "posterior": {
                        "decision_value_range": "300,000 / 500,000 / 700,000",
                        "raw_value_range": "300,000 / 540,000 / 900,000",
                        "q6_sample_rate": "12.0%",
                        "q6_decision_value_range": "0 / 100,000 / 300,000",
                        "q6_count_range": "0 / 1 / 2",
                    },
                    "layout": {
                        "known_cells": "108",
                        "estimate": "108/120/140",
                        "confidence": "中",
                        "risk": "中",
                    },
                },
                "q6_risk_reference": {
                    "risk": True,
                    "prior_gap": "件数P90低1.00",
                    "practical_reference_p90": "486,510",
                },
                "minimap": {
                    "status": "available",
                    "known_items": 2,
                    "default_cells": 130,
                    "max_cells": 250,
                    "viewport_rows": 13,
                    "max_rows": 25,
                    "scrollable": False,
                    "quality_counts": {"q5": 1, "q6": 1},
                    "quality_reveal_counts": {"q2": 3, "q3": 3, "q4": 1, "q5": 1, "q6": 1},
                    "quality_reveal_unplaced_counts": {"q6": 1},
                    "category_counts": {"能源": 1, "古董": 1},
                    "columns": 10,
                    "rows_hint": 20,
                    "items": [
                        {
                            "row": 2,
                            "col": 4,
                            "width": 4,
                            "height": 4,
                            "quality": 6,
                            "shape_key": "44",
                            "item_name": "民用垂直起降飞行器",
                            "display_label": "",
                            "local_index": 14,
                        },
                        {
                            "row": 18,
                            "col": 1,
                            "width": 2,
                            "height": 2,
                            "quality": 5,
                            "shape_key": "22",
                            "item_name": "青铜古镜",
                            "display_label": "",
                            "local_index": 170,
                        },
                    ],
                },
                "constraints": {
                    "summary": {
                        "input_total_item_count": 42,
                        "input_warehouse_total_cells": 123,
                        "known_purple_item_count": 10,
                        "known_gold_item_count": 3,
                        "known_red_item_count": 1,
                        "anchor_count": 4,
                        "shape_target_count": 12,
                        "category_target_count": 2,
                        "category_exclusion_count": 1,
                        "public_constraint_key": "max_item_cells",
                    },
                    "public_info": {
                        "public_numeric_summary": "紫均格 2.90 / 随机9均价 15,296.33",
                    },
                },
                "truth": {
                    "available": True,
                    "source": "settlement_or_sample_replay",
                    "total_value": 800000,
                    "total_items": 42,
                    "total_cells": 123,
                    "q6": {"count": 1, "cells": 16, "value": 320000},
                    "top_item": {
                        "name": "民用垂直起降飞行器",
                        "quality": 6,
                        "cells": 16,
                        "value": 320000,
                    },
                },
                "actions": {
                    "latest_result": {
                        "tool": "宝光四鉴",
                        "result": "12",
                        "revealed_items": "0",
                    },
                    "results": (
                        {
                            "tool": "宝光四鉴",
                            "result": "12",
                            "revealed_items": "0",
                        },
                    ),
                    "sent": (),
                },
                "diagnostics": {
                    "posterior": "q6_below_drop_prior:0.12<prior:0.80",
                    "layout": {
                        "conflict": False,
                        "bottom_row": 18,
                        "bottom_row_risk": True,
                        "bottom_row_risk_threshold": 12,
                    },
                    "q6": {
                        "p90_misses_truth": True,
                        "below_drop_prior": True,
                        "top_size_band": "large",
                    },
                    "sampling": {
                        "relaxed_exact_used": False,
                        "n_trials": 500,
                        "shadow_trials": 80,
                        "processing_seconds": 1.2,
                    },
                },
                "interaction": {
                    "compact": {
                        "purpose": "always_on_top_core_tips",
                        "fields": ("baseline.decision.action",),
                    },
                    "hover": {
                        "purpose": "expanded_quick_context",
                        "fields": ("constraints.summary", "minimap"),
                    },
                    "detail": {
                        "purpose": "click_to_open_full_reasoning",
                        "fields": ("truth", "shadows", "diagnostics"),
                        "collapsible": True,
                        "renderers": (),
                    },
                },
                "shadows": [
                    {
                        "label": "profile_b5",
                        "display_mode": "debug_only",
                        "active": True,
                        "q6_decision_value_p90": 420000,
                        "q6_p90_delta": 120000,
                        "trials": 80,
                    },
                    {
                        "label": "aisha_deep_floor1",
                        "display_mode": "risk_reference_candidate",
                        "active": True,
                        "q6_decision_value_p90": 486510,
                        "q6_p90_delta": 186510,
                        "trials": 80,
                    },
                ],
            },
        }
    )

    shadow_sections = [
        section for section in model["sections"] if section[0] == "Shadow 风险参考"
    ]
    assert shadow_sections
    assert "aisha_deep_floor1" in shadow_sections[0][1]
    assert "profile_b5" not in shadow_sections[0][1]
    minimap_section = next(section for section in model["sections"] if section[0] == "MiniMap")
    assert "已知 2 件" in minimap_section[1]
    assert "能源×1" in minimap_section[2]
    assert "公共品质 Q2×3 / Q3×3 / Q4×1 / Q5×1 / Q6×1" in minimap_section[2]
    assert "未定位 Q6×1" in minimap_section[2]
    constraints_section = next(
        section for section in model["sections"] if section[0] == "输入约束"
    )
    assert "总件 42" in constraints_section[1]
    assert "总格 123" in constraints_section[1]
    assert "紫×10" in constraints_section[2]
    assert "红×1" in constraints_section[2]
    assert "反排1" in constraints_section[2]
    assert "紫均格 2.90" in constraints_section[2]
    action_section = next(
        section for section in model["sections"] if section[0] == "最近道具"
    )
    assert "宝光四鉴: 12" in action_section[1]
    geometry = overlay._minimap_canvas_geometry(model["minimap"])
    assert geometry["rows"] == 20
    assert geometry["visible_rows"] == 13
    assert geometry["height"] > geometry["visible_height"]
    assert all(
        not str(item.get("display_label") or "").strip()
        for item in model["minimap"]["items"]
    )
    assert model["minimap"]["known_items"] == 2
    assert model["minimap"]["default_cells"] == 130
    assert model["minimap"]["max_cells"] == 250
    assert model["minimap"]["capacity_text"] == "当前123格"
    assert "最高" not in model["minimap"]["capacity_text"]
    assert model["title"] == "AISHA  ·  map 2501  ·  R4"
    assert model["decision"][0] == "可守不抢"
    assert model["metrics"][0] == (
        "P50估值",
        "500,000",
        "P10/P50/P90 300,000 / 500,000 / 700,000",
        "normal",
    )
    assert model["metrics"][1][0] == "防守价"
    assert model["metrics"][1][1] == "450,000"
    assert model["metrics"][2][0] == "当前最高"
    assert model["metrics"][3][0] == "红品数量"
    assert model["metrics"][3][1] == "红品 1件"
    assert "样本 12.0%" in model["metrics"][3][2]
    assert any("UI契约 q6 风险参考" in alert[0] for alert in model["alerts"])
    assert any("aisha_deep_floor1 tail-risk shadow" in alert[0] for alert in model["alerts"])
    interaction = model["interaction"]
    assert interaction["mini"]["purpose"] == "always_on_top_core_tips"
    assert interaction["mini"]["metrics"][0][0] == "P50估值"
    assert interaction["hover"]["enabled"] is True
    assert interaction["hover"]["sections"][0][0] == "正式出价"
    assert any(section[0] == "MiniMap" for section in interaction["hover"]["sections"])
    assert any(section[0] == "输入约束" for section in interaction["hover"]["sections"])
    assert any(section[0] == "q6 风险参考" for section in interaction["hover"]["sections"])
    posterior_section = next(
        section
        for section in interaction["hover"]["sections"]
        if section[0] == "后验概览"
    )
    assert "已知红品 1件" in posterior_section[2]
    round_section = next(
        section for section in interaction["hover"]["sections"] if section[0] == "轮次仓位参考"
    )
    assert round_section[1] == "R4 参考 454,546"
    assert "P50 500,000 ÷ 1.1" in round_section[2]
    assert "不改变正式出价" in round_section[2]
    assert interaction["detail"]["enabled"] is True
    assert interaction["detail"]["collapsible"] is True
    assert interaction["detail"]["renderers"] == ()
    assert any(section[0] == "结算/Truth" for section in interaction["detail"]["sections"])
    assert any(section[0] == "Shadow 明细" for section in interaction["detail"]["sections"])
    assert any(section[0] == "诊断明细" for section in interaction["detail"]["sections"])


def test_overlay_model_surfaces_live_health_warnings() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "file": "slow.json",
            "created_at": time.time(),
            "hero": "aisha",
            "map_id": 2501,
            "round": 4,
            "ui_contract": {
                "context": {
                    "hero": "aisha",
                    "map_id": 2501,
                    "round": 4,
                    "known_value_sum": 800000,
                },
                "source": {
                    "file": "slow.json",
                    "processing_seconds": 18.25,
                },
                "baseline": {
                    "decision": {
                        "action": "可守不抢",
                        "current_highest": "玩家A 500,000",
                        "risk_band": "防守区",
                        "stop_price": "680,000",
                    },
                    "posterior": {
                        "status": "matched",
                        "match_text": "80/80",
                        "decision_value_range": "300,000 / 500,000 / 700,000",
                    },
                },
                "q6_risk_reference": {
                    "risk": True,
                    "prior_gap": "件数P90低1.00",
                    "practical_reference_p90": "486,510",
                    "affects_bid": True,
                    "bid_floor_applied": True,
                },
            },
        }
    )

    assert model["status"] == ("慢", "warn")
    assert "推理 18.2s" in model["subtitle"]
    assert any("本次推理耗时 18.2s" in alert[0] for alert in model["alerts"])
    assert any("q6 风险参考不应影响正式出价" in alert[0] for alert in model["alerts"])


def test_overlay_model_switches_to_settlement_view_when_settled() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "file": "settled.json",
            "hero": "ethan",
            "map_id": 2401,
            "round": 5,
            "phase": "settled",
            "known_value_sum": 801824,
            "ui_contract": {
                "context": {
                    "hero": "ethan",
                    "map_id": 2401,
                    "round": 5,
                    "phase": "settled",
                    "known_value_sum": 801824,
                    "inventory_count": 38,
                    "inventory_cells": 112,
                },
                "source": {"file": "settled.json"},
                "baseline": {
                    "decision": {
                        "action": "仍在可防守区",
                        "current_highest": "玩家A 500,000",
                        "risk_band": "防守区",
                        "defend_bid": "650,000",
                        "stop_price": "900,000",
                    },
                    "posterior": {
                        "decision_value_range": "500,000 / 750,000 / 900,000",
                    },
                },
                "truth": {
                    "available": True,
                    "source": "settlement_or_sample_replay",
                    "total_value": 801824,
                    "total_items": 38,
                    "total_cells": 112,
                    "q6": {"count": 1, "cells": 9, "value": 320000},
                    "top_item": {
                        "name": "永乐大典",
                        "quality": 6,
                        "cells": 9,
                        "value": 320000,
                    },
                },
            },
        }
    )

    assert model["decision"][0] == "结算 801,824"
    assert "总件 38" in model["decision"][1]
    assert [metric[0] for metric in model["metrics"]] == [
        "结算总值",
        "总件/总格",
        "红货 q6",
        "最高货",
    ]
    assert model["metrics"][0][1] == "801,824"
    assert any(section[0] == "结算/Truth" for section in model["sections"])


def test_overlay_model_hides_old_settlement_after_retention_window() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 90,
            "file": "settled.json",
            "phase": "settled",
            "ui_contract": {
                "context": {"phase": "settled", "known_value_sum": 801824},
                "truth": {
                    "available": True,
                    "total_value": 801824,
                },
            },
        }
    )

    assert model["status"] == ("待机", "dim")
    assert model["decision"][0] == "等待对局开始"
    assert model["decision"][1] == "上一局结算已结束"
    assert model["subtitle"] == "等待下一局开始"
    assert [metric[0] for metric in model["metrics"]] == [
        "P50估值",
        "防守价",
        "当前最高",
        "q6风险",
    ]
    assert any(section[0] == "监听状态" for section in model["sections"])
    assert model["interaction"]["hover"]["enabled"] is False
    assert model["interaction"]["detail"]["enabled"] is False


def test_overlay_model_review_snapshot_keeps_stale_settlement() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 90,
            "file": "settled.json",
            "phase": "settled",
            "ui_contract": {
                "context": {"phase": "settled", "known_value_sum": 801824},
                "truth": {
                    "available": True,
                    "total_value": 801824,
                },
            },
        },
        review_snapshot=True,
    )

    assert model["decision"][0] == "结算 801,824"
    assert any(metric[0] == "结算总值" for metric in model["metrics"])
    assert model["interaction"]["detail"]["enabled"] is True


def test_overlay_model_retains_recent_settlement_when_capture_session_is_stale() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 5,
            "phase": "settled",
            "map_id": 2409,
            "round": 4,
            "ui_contract": {
                "context": {"phase": "settled", "session_id": "2409:old-session"},
                "truth": {"available": True, "total_value": 500000},
            },
            "_capture_source_status": {
                "ts": time.time() - 30,
                "source": "windivert",
                "active_flows": 1,
                "raw_packets": 120,
                "accepted_frames": 6,
                "active_session_id": "2504:9000000000000006",
            },
        }
    )

    assert model["decision"][0] == "结算 500,000"
    assert any(metric[0] == "结算总值" for metric in model["metrics"])


def test_overlay_model_shows_new_session_loading_for_recent_settlement() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 5,
            "phase": "settled",
            "map_id": 2409,
            "round": 4,
            "ui_contract": {
                "context": {"phase": "settled", "session_id": "2409:old-session"},
                "truth": {"available": True, "total_value": 500000},
            },
            "_capture_source_status": {
                "ts": time.time() - 1,
                "source": "windivert",
                "active_flows": 1,
                "raw_packets": 120,
                "accepted_frames": 6,
                "active_session_id": "2504:9000000000000006",
            },
        }
    )

    assert model["decision"][0] == "新局监听中"
    assert model["subtitle"] == "监听中，已抓到新局 map 2504"


def test_overlay_model_hides_old_live_snapshot_when_capture_session_advances() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 5,
            "phase": "bidding",
            "map_id": 2401,
            "round": 3,
            "ui_contract": {
                "context": {
                    "phase": "bidding",
                    "session_id": "2401:old-session",
                },
                "baseline": {
                    "decision": {
                        "action": "小幅进攻",
                        "current_highest": "玩家A 200,000",
                    },
                },
            },
            "_capture_source_status": {
                "ts": time.time() - 1,
                "source": "windivert",
                "active_flows": 1,
                "raw_packets": 240,
                "accepted_frames": 10,
                "active_session_id": "2407:9000000000000010",
            },
        }
    )

    assert model["decision"][0] == "新局监听中"
    assert model["subtitle"] == "监听中，已抓到新局 map 2407"
    assert not any("小幅进攻" in str(value) for value in model.values())


def test_overlay_model_shows_new_session_loading_after_settlement() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 90,
            "file": "settled.json",
            "phase": "settled",
            "_capture_source_status": {
                "ts": time.time() - 1,
                "source": "windivert",
                "active_flows": 1,
                "raw_packets": 120,
                "accepted_frames": 8,
                "active_session_id": "2401:new-session-id",
            },
            "ui_contract": {
                "context": {"phase": "settled", "known_value_sum": 801824},
                "truth": {
                    "available": True,
                    "total_value": 801824,
                },
            },
        }
    )

    assert model["decision"][0] == "新局监听中"
    assert model["subtitle"] == "监听中，已抓到新局 map 2401"
    assert any(section[0] == "当前会话" for section in model["sections"])
    assert any(
        section[0] == "当前地图" and section[1] == "2401"
        for section in model["sections"]
    )


def test_overlay_model_hides_stale_non_settlement_snapshot() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 180,
            "hero": "ethan",
            "map_id": 2401,
            "round": 3,
            "ui_contract": {
                "context": {"hero": "ethan", "map_id": 2401, "round": 3},
                "baseline": {
                    "decision": {
                        "action": "小幅进攻",
                        "current_highest": "玩家A 200,000",
                    },
                    "posterior": {
                        "decision_value_range": "100,000 / 200,000 / 300,000",
                    },
                },
            },
        }
    )

    assert model["status"] == ("待机", "dim")
    assert model["decision"][0] == "等待对局开始"
    assert model["decision"][1] == "不显示过期旧局出价"
    assert model["subtitle"] == "等待新的实时对局状态"
    assert model["metrics"][0][1] == "--"
    assert not any("小幅进攻" in str(value) for value in model.values())


def test_overlay_model_uses_capture_status_for_stale_snapshot_waiting_copy() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "created_at": time.time() - 180,
            "hero": "ethan",
            "map_id": 2401,
            "round": 3,
            "_capture_source_status": {
                "ts": time.time() - 1,
                "source": "windivert",
                "active_flows": 1,
                "raw_packets": 0,
                "accepted_frames": 0,
            },
            "ui_contract": {
                "context": {"hero": "ethan", "map_id": 2401, "round": 3},
                "baseline": {
                    "decision": {"action": "小幅进攻"},
                },
            },
        }
    )

    assert model["status"] == ("待机", "dim")
    assert model["subtitle"] == "监听中，等待对局数据"
    assert model["decision"][1] == "已连接游戏服务器，等待下一条对局状态包"
    assert any(section[0] == "抓包状态" for section in model["sections"])


def test_overlay_model_empty_snapshot_uses_standby_copy() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model({})

    assert model["empty"] is True
    assert model["status"] == ("待机", "dim")
    assert model["decision"][0] == "等待对局开始"
    assert model["decision"][1] == "等待实时数据"
    assert model["subtitle"] == "等待实时对局状态"
    assert model["metrics"][0][0] == "P50估值"
    assert model["sections"][0][0] == "监听状态"


def test_overlay_model_surfaces_zero_match_baseline() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "file": "zero_match.json",
            "hero": "aisha",
            "map_id": 2601,
            "round": 3,
            "known_value_sum": 1200000,
            "ui_contract": {
                "context": {
                    "hero": "aisha",
                    "map_id": 2601,
                    "round": 3,
                    "known_value_sum": 1200000,
                },
                "source": {"file": "zero_match.json"},
                "baseline": {
                    "decision": {
                        "action": "",
                    },
                    "posterior": {
                        "match_text": "0/80",
                        "status": "zero_match",
                    },
                    "layout": {},
                },
            },
        }
    )

    assert model["decision"] == (
        "后验无匹配",
        "匹配 0/80  |  复核公开约束/布局解析",
        "bad",
    )
    assert any("baseline 后验无匹配" in alert[0] for alert in model["alerts"])


def test_overlay_model_uses_zero_match_fallback_reference() -> None:
    overlay = _overlay_module()

    model = overlay._overlay_model(
        {
            "file": "zero_match.json",
            "hero": "aisha",
            "map_id": 2601,
            "round": 3,
            "known_value_sum": 1200000,
            "ui_contract": {
                "context": {
                    "hero": "aisha",
                    "map_id": 2601,
                    "round": 3,
                    "known_value_sum": 1200000,
                },
                "source": {"file": "zero_match.json"},
                "baseline": {
                    "decision": {
                        "action": "",
                    },
                    "posterior": {
                        "match_text": "0/80",
                        "status": "zero_match",
                    },
                    "layout": {},
                },
                "fallback": {
                    "active": True,
                    "affects_bid": False,
                    "decision": {
                        "action": "停止追价",
                        "current_highest": "玩家A 500,000",
                        "risk_band": "过热区",
                        "probe_bid": "180,000",
                        "defend_bid": "196,000",
                        "attack_bid": "252,000",
                        "stop_price": "420,000",
                        "next_info_hint": "优先补轮廓或具体物品",
                        "rationale": "低信息阶段按地图后验保守折价",
                        "player_risks": [
                            {
                                "current_bid": "玩家A 500,000",
                                "risk_band": "过热区",
                            }
                        ],
                    },
                    "posterior": {
                        "raw_value_range": "180,000 / 280,000 / 420,000",
                        "match_text": "22/80",
                    },
                },
            },
        }
    )

    assert model["decision"] == (
        "低置信参考：停止追价",
        "v2匹配 0/80  |  最高 玩家A 500,000  |  停止 420,000  |  过热区",
        "bad",
    )
    assert model["metrics"][0] == (
        "fallback P50",
        "280,000",
        "v1低置信 / 22/80",
        "warn",
    )
    fallback_section = next(
        section for section in model["sections"] if section[0] == "Fallback 出价参考"
    )
    assert "探价 180,000" in fallback_section[1]
    assert "防守 196,000" in fallback_section[1]
    assert "可追(P90) 252,000" in fallback_section[1]
    assert "停止 420,000" in fallback_section[1]
    assert "对手：玩家A 500,000 过热区" in fallback_section[2]
    assert "补信息：优先补轮廓或具体物品" in fallback_section[2]
    assert any("v1 fallback 已生成低置信参考" in alert[0] for alert in model["alerts"])
    assert any(
        section[0] == "Fallback 出价参考"
        for section in model["interaction"]["hover"]["sections"]
    )
    assert any(
        section[0] == "Fallback 出价参考"
        for section in model["interaction"]["detail"]["sections"]
    )


def test_overlay_hover_surfaces_size_bucket_section() -> None:
    overlay = _overlay_module()
    from bidking_lab.runtime.snapshot import ui_contract_from_artifact

    contract = ui_contract_from_artifact(
        {
            "action_result_rows": [
                {
                    "action_id": 100172,
                    "tool": "四格均价",
                    "result": 135000,
                    "sort": 3,
                }
            ],
            "v2_posterior_rows": [
                {
                    "诊断": (
                        "size_bucket:4:avg=135000:tier=rich_pool:strength=soft"
                    ),
                }
            ],
        }
    )
    sections = overlay._ui_contract_hover_sections(contract)
    size_section = next(section for section in sections if section[0] == "N格均价")
    assert "四格均价" in size_section[1]
    assert "tier=rich_pool" in size_section[2]


def test_overlay_constraints_section_shows_activity_map_alias() -> None:
    overlay = _overlay_module()

    section = overlay._ui_contract_constraints_section(
        {
            "constraints": {
                "summary": {
                    "known_grid_items": 2,
                    "map_alias_label": "活动图 2527->旧沉船 2507",
                }
            }
        }
    )

    assert section is not None
    assert section[0] == "输入约束"
    assert "已知 2 件" in section[1]
    assert "活动图 2527->旧沉船 2507" in section[2]


def test_overlay_surfaces_v3_practical_reference_without_changing_decision() -> None:
    overlay = _overlay_module()
    contract = {
        "context": {"hero": "ethan", "map_id": 2401, "round": 2},
        "baseline": {
            "decision": {
                "action": "可守不抢",
                "current_highest": "玩家A 320,000",
                "risk_band": "防守区",
                "defend_bid": "380,000",
                "stop_price": "520,000",
            },
            "posterior": {
                "decision_value_range": "200,000 / 420,000 / 650,000",
                "match_text": "18/80",
                "status": "matched",
            },
            "layout": {},
        },
        "diagnostics": {
            "v3_practical": {
                "available": True,
                "ready": False,
                "affects_bid": False,
                "active": False,
                "candidate": True,
                "source": "formal_value",
                "mode": "value_floor_watch",
                "status": "watch_raise_candidate",
                "recommendation": "raise_watch",
                "confidence": "medium",
                "source_lanes": "formal_value,underestimate_repair",
                "risk_flags": "value_floor_candidate,q6_under_candidate",
                "reason": "formal value floor lifts underestimation watch",
                "formal_decision_value_p50": 480000,
                "formal_decision_value_p90": 780000,
                "total_value_p90": 980000,
                "baseline_formal_decision_value_p50": 420000,
                "baseline_formal_decision_value_p90": 650000,
                "delta_formal_decision_value_p50": 60000,
                "delta_formal_decision_value_p90": 130000,
                "raw_total_gap_to_formal_p90": 200000,
                "q6_formal_decision_value_p50": 120000,
                "q6_formal_decision_value_p90": 260000,
                "baseline_q6_formal_decision_value_p90": 180000,
                "q6_value_p90": 420000,
                "delta_q6_formal_decision_value_p50": 40000,
                "delta_q6_formal_decision_value_p90": 80000,
                "q6_raw_gap_to_formal_p90": 160000,
            }
        },
    }

    model = overlay._overlay_model(
        {
            "hero": "ethan",
            "map_id": 2401,
            "round": 2,
            "ui_contract": contract,
        }
    )

    assert model["decision"][0] == "可守不抢"
    assert any(section[0] == "v3 实战参考" for section in model["sections"])
    assert any(
        section[0] == "v3 实战参考"
        and "低估风险" in section[1]
        and "正式P90 650,000 -> v3P90 780,000" in section[1]
        and "P90 780,000" in section[1]
        and "ΔP90 130,000" in section[1]
        and "仓库上限ΔP90 200,000" in section[1]
        and "q6上限ΔP90 160,000" in section[1]
        and "证据 formal_value,underestimate_repair" in section[2]
        and "风险 value_floor_candidate,q6_under_candidate" in section[2]
        and "置信 中" in section[2]
        and "不影响正式出价" in section[2]
        for section in model["interaction"]["hover"]["sections"]
    )
    assert any(
        section[0] == "v3 实战参考"
        and "正式P50 420,000" in section[1]
        and "正式q6P90 180,000" in section[1]
        and "仓库上限P90 980,000" in section[1]
        and "q6上限P90 420,000" in section[1]
        and "formal value floor" in section[2]
        for section in model["interaction"]["detail"]["sections"]
    )
    assert any("v3 实战参考提示低估风险" in alert[0] for alert in model["alerts"])
    assert any("正式P90 650,000 -> v3P90 780,000" in alert[0] for alert in model["alerts"])


def test_overlay_uses_v3_formal_decision_with_v2_reference_section() -> None:
    overlay = _overlay_module()
    contract = {
        "context": {"hero": "ethan", "map_id": 2401, "round": 3},
        "baseline": {
            "official": True,
            "affects_bid": True,
            "source": "v3_practical",
            "decision": {
                "action": "小幅进攻",
                "current_highest": "玩家A 500,000",
                "risk_band": "进攻区",
                "defend_bid": "560,000",
                "stop_price": "900,000",
                "evidence": "v3 practical formal",
            },
            "posterior": {
                "decision_value_range": "300,000 / 640,000 / 900,000",
                "match_text": "18/80",
                "status": "matched",
            },
        },
        "v2_reference": {
            "available": True,
            "affects_bid": False,
            "decision": {
                "action": "可守不抢",
                "current_highest": "玩家A 500,000",
                "risk_band": "防守区",
                "defend_bid": "420,000",
                "stop_price": "650,000",
                "evidence": "v2 decision_value",
            },
        },
    }

    model = overlay._overlay_model(
        {
            "hero": "ethan",
            "map_id": 2401,
            "round": 3,
            "ui_contract": contract,
        }
    )

    assert model["decision"][0] == "小幅进攻"
    hover_sections = model["interaction"]["hover"]["sections"]
    assert hover_sections[0][0] == "正式出价 v3"
    assert "来源 v3 practical" in hover_sections[0][2]
    v2_section = next(section for section in hover_sections if section[0] == "v2 对照")
    assert v2_section[1] == "可守不抢"
    assert "停止 650,000" in v2_section[2]
    assert "当前不影响正式出价" in v2_section[2]


def test_overlay_v3_practical_model_eval_contract_chain_labels_baseline_p90() -> None:
    overlay = _overlay_module()
    from bidking_lab.runtime.snapshot import ui_contract_from_artifact

    contract = ui_contract_from_artifact(
        {
            "model_eval": {
                "v3_practical_available": True,
                "v3_practical_ready": True,
                "v3_practical_affects_bid": False,
                "v3_practical_active": False,
                "v3_practical_candidate": True,
                "v3_practical_source": "q6_prior_floor",
                "v3_practical_mode": "q6_prior_floor_watch",
                "v3_practical_status": "watch_q6_prior_floor",
                "v3_practical_recommendation": "raise_watch",
                "v3_practical_confidence": "low_medium",
                "v3_practical_source_lanes": "formal_value+prior_q6_floor",
                "v3_practical_risk_flags": "q6_prior_floor_watch",
                "v3_practical_formal_decision_value_p50": 420000,
                "v3_practical_formal_decision_value_p90": 1399123,
                "v3_practical_baseline_formal_decision_value_p50": 210231,
                "v3_practical_baseline_formal_decision_value_p90": 504178,
                "v3_practical_delta_formal_decision_value_p50": 0,
                "v3_practical_delta_formal_decision_value_p90": 894945,
                "v3_practical_q6_formal_decision_value_p90": 1050000,
                "v3_practical_baseline_q6_formal_decision_value_p90": 300000,
                "v3_practical_delta_q6_formal_decision_value_p90": 750000,
                "v3_practical_total_value_p90": 1510000,
                "v3_practical_q6_value_p90": 1200000,
                "v3_practical_raw_total_gap_to_formal_p90": 110877,
                "v3_practical_q6_raw_gap_to_formal_p90": 150000,
            }
        }
    )

    hover = overlay._ui_contract_hover_sections(contract)
    practical = next(section for section in hover if section[0] == "v3 实战参考")

    assert "正式P90 504,178 -> v3P90 1,399,123" in practical[1]
    assert "ΔP90 894,945" in practical[1]
    assert "仓库上限ΔP90 110,877" in practical[1]
    assert "q6上限ΔP90 150,000" in practical[1]
    assert "证据 formal_value+prior_q6_floor" in practical[2]
    assert "风险 q6_prior_floor_watch" in practical[2]
    assert "置信 中低" in practical[2]
    assert "只读参考，不影响正式出价" in practical[2]


def test_overlay_labels_v3_practical_ceiling_watch() -> None:
    overlay = _overlay_module()
    contract = {
        "diagnostics": {
            "v3_practical": {
                "available": True,
                "ready": True,
                "affects_bid": False,
                "active": False,
                "candidate": True,
                "source": "q6_value_residual",
                "mode": "q6_value_ceiling_watch",
                "status": "watch_q6_value_ceiling",
                "recommendation": "ceiling_watch",
                "confidence": "low_medium",
                "source_lanes": "q6_value_residual",
                "risk_flags": "q6_value_ceiling_watch",
                "formal_decision_value_p50": 520000,
                "formal_decision_value_p90": 820000,
                "baseline_formal_decision_value_p50": 420000,
                "baseline_formal_decision_value_p90": 600000,
                "delta_formal_decision_value_p50": 100000,
                "delta_formal_decision_value_p90": 220000,
            }
        },
    }

    sections = overlay._ui_contract_hover_sections(contract)
    practical = next(section for section in sections if section[0] == "v3 实战参考")

    assert "参考上沿" in practical[1]
    assert "正式P90 600,000 -> v3P90 820,000" in practical[1]
    assert "P90 820,000" in practical[1]
    assert "ΔP90 220,000" in practical[1]
    assert "不影响正式出价" in practical[2]


def test_overlay_v3_practical_passthrough_stays_read_only() -> None:
    overlay = _overlay_module()
    contract = {
        "baseline": {
            "decision": {"action": "开局观察"},
            "posterior": {"decision_value_range": "100,000 / 240,000 / 380,000"},
        },
        "diagnostics": {
            "v3_practical": {
                "available": True,
                "ready": False,
                "affects_bid": False,
                "active": False,
                "candidate": False,
                "status": "baseline_passthrough",
                "recommendation": "baseline",
                "confidence": "low",
                "formal_decision_value_p50": 240000,
                "formal_decision_value_p90": 380000,
                "baseline_formal_decision_value_p50": 240000,
                "baseline_formal_decision_value_p90": 380000,
                "delta_formal_decision_value_p50": 0,
            }
        },
    }

    sections = overlay._ui_contract_hover_sections(contract)
    practical = next(section for section in sections if section[0] == "v3 实战参考")

    assert "未触发" in practical[1]
    assert "P50 240,000" in practical[1]
    assert "正式P90 380,000 -> v3P90 380,000" in practical[1]
    assert "只读参考，不影响正式出价" in practical[2]
    assert overlay._ui_contract_alerts(contract) == []


def test_ahmad_overlay_latest_result_text_falls_back_to_latest_sent() -> None:
    module = _ahmad_overlay_module()

    assert module.AhmadTkOverlay._latest_result_text(  # type: ignore[attr-defined]
        SimpleNamespace(),
        {"latest_result": {"tool": "宝光四鉴", "result": "12"}},
    ) == "宝光四鉴=12"
    assert module.AhmadTkOverlay._latest_result_text(  # type: ignore[attr-defined]
        SimpleNamespace(),
        {"latest_sent": {"tool": "普品扫描", "result": 9}},
    ) == "普品扫描=9"
    assert module.AhmadTkOverlay._latest_result_text(  # type: ignore[attr-defined]
        SimpleNamespace(),
        {"latest_result": {"tool": "普品均格", "result": "1.7999999523162842"}},
    ) == "普品均格=1.8"


def test_ahmad_overlay_mini_input_text_keeps_compact_totals() -> None:
    module = _ahmad_overlay_module()

    assert module.AhmadTkOverlay._mini_input_text(  # type: ignore[attr-defined]
        SimpleNamespace(),
        {
            "ref_input_summary": (
                "总件 38 · 总格 83.0 · 件数 白绿件 15，蓝件 9，紫件 8，金件 4，红件 2 "
                "· 格数 白绿格 27，蓝格 14，紫格 33，金格 7，红格 2"
            ),
            "ref_status": "ok",
        },
    ) == "总件 38 · 总格 83.0"
    assert module.AhmadTkOverlay._mini_input_text(  # type: ignore[attr-defined]
        SimpleNamespace(),
        {"ref_input_summary": "-", "ref_status": "missing_total_count"},
    ) == "missing_total_count"


def test_ahmad_overlay_mini_candidate_and_next_info_use_actionable_fields() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "evidence": {
            "candidate_summary": "总件 38 · 总格 83",
            "next_info_hint": "优先补白绿/蓝件数或均格",
            "ref_input_summary": "总件 38 · 总格 83 · 件数 白绿件 15",
        },
        "ahmed_ref": {},
    }

    assert module.AhmadTkOverlay._mini_candidate_text(overlay, data) == "总件 38 · 总格 83"
    assert module.split_candidate_dimension_summary("总件 38 · 总格 83") == (
        "38件",
        "83格",
    )
    assert module.split_candidate_dimension_summary(
        "估总件 36/38/40 · 估总格 70/82/94格"
    ) == ("估 36/38/40件", "估 70/82/94格")
    assert module.split_candidate_dimension_summary("missing_total_count") == ("-", "-")
    assert module.split_candidate_dimension_summary(bytearray(b"not text")) == ("-", "-")
    assert module.AhmadTkOverlay._mini_next_info_text(overlay, data) == "优先补白绿/蓝件数或均格"


def test_ahmad_server_candidate_summary_and_next_info_hint() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [70, 82, 94],
        "quality_count_ranges": {
            "q1": [10, 15, 15],
            "q3": [8, 11, 13],
            "q4": [4, 4, 4],
            "q5": [2, 2, 2],
            "q6": [1, 1, 1],
        },
        "evidence": {"total_count": 38},
    }

    assert module._candidate_summary(result) == "总件 38 · 估总格 70/82/94格"  # type: ignore[attr-defined]
    assert module._next_info_hint(result) == "优先补白绿/蓝件数或均格"  # type: ignore[attr-defined]

    result["evidence"]["total_grid_target"] = 120.00000476837158
    assert module._candidate_summary(result) == "总件 38 · 估总格 70/82/94格"  # type: ignore[attr-defined]
    assert module._next_info_hint(result) == "优先补白绿/蓝件数或均格"  # type: ignore[attr-defined]

    result["evidence"]["source_notes"] = ["settlement_review_total_grid"]
    assert module._candidate_summary(result) == "总件 38 · 总格 120"  # type: ignore[attr-defined]


def test_ahmad_server_ref_waiting_text_is_hero_specific() -> None:
    module = _ahmad_server_module()
    ahmed_grid_only = {
        "status": "missing_total_count",
        "notes": ["waiting_total_count", "waiting_total_count:grid_only"],
        "evidence": {"total_grid_target": 152, "total_count": None},
    }
    ethan_grid_only = dict(ahmed_grid_only)
    assert module._ref_waiting_display_text("ahmed", ahmed_grid_only) == "等待总件数"  # type: ignore[attr-defined]
    assert module._ref_waiting_display_text("ethan", ethan_grid_only) == "等待公开输入"  # type: ignore[attr-defined]
    assert module._ref_waiting_flag_label("ethan", ethan_grid_only) == "等待公开输入"  # type: ignore[attr-defined]
    assert module._next_info_hint(ahmed_grid_only, hero_key="ahmed") == "先补总件"  # type: ignore[attr-defined]
    # Ethan now uses Aisha-style 等待X品信息 (never 件数, he cannot lock count): with no per-tier
    # cell info yet, the next step is the first two uncovered 鉴格流 tiers 白绿→蓝.
    assert (
        module._next_info_hint(ethan_grid_only, hero_key="ethan") == "等待白绿品和蓝品信息"  # type: ignore[attr-defined]
    )


def test_ahmad_server_aisha_d1_flag_detail_filters_weak_shadow_discounts() -> None:
    module = _ahmad_server_module()

    # Near-1.0 shadow discount is noise — do not surface a flag.
    assert module._aisha_d1_flag_detail(["aisha_d1_shadow_q6_discount=0.9@r5"]) == ""  # type: ignore[attr-defined]
    # Meaningful shadow discount surfaces.
    assert (
        module._aisha_d1_flag_detail(["aisha_d1_shadow_q6_discount=0.55@r2"])  # type: ignore[attr-defined]
        == "aisha_d1_shadow_q6_discount=0.55@r2"
    )
    # apply notes always surface (they change the bid).
    assert (
        module._aisha_d1_flag_detail(["aisha_d1_apply_q6_discount=0.85@r4"])  # type: ignore[attr-defined]
        == "aisha_d1_apply_q6_discount=0.85@r4"
    )
    # Non-d1 notes ignored.
    assert module._aisha_d1_flag_detail(["aisha_layout_band_widen_applied"]) == ""  # type: ignore[attr-defined]


def test_ahmad_server_aisha_defense_multiplier_hint_by_round() -> None:
    module = _ahmad_server_module()

    assert module._aisha_defense_multiplier_hint(1) == "R1防守×2.0"  # type: ignore[attr-defined]
    assert module._aisha_defense_multiplier_hint(2) == "R2防守×1.6"  # type: ignore[attr-defined]
    assert module._aisha_defense_multiplier_hint(3) == "R3防守×1.3"  # type: ignore[attr-defined]
    assert module._aisha_defense_multiplier_hint(4) == "R4防守×1.1"  # type: ignore[attr-defined]
    assert module._aisha_defense_multiplier_hint(5) == "R5防守×1.1"  # type: ignore[attr-defined]
    assert module._aisha_defense_multiplier_hint(None) == ""  # type: ignore[attr-defined]


def test_ahmad_server_infer_hero_from_skill_reveals() -> None:
    module = _ahmad_server_module()
    snapshot = {
        "skill_reveal_rows": [
            {"skill_id": 1001034, "hero_id": 103, "revealed_items": 2},
        ],
    }
    assert module._infer_hero_from_skill_reveals(snapshot) == "aisha"  # type: ignore[attr-defined]
    assert module._snapshot_hero_key(snapshot, {}) == "aisha"  # type: ignore[attr-defined]


def test_ahmad_server_recognizes_liss_302_as_generic_ref(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    monkeypatch.setattr(module, "run_reference_engine", None)
    created_at = time.time()
    snapshot = {
        "created_at": created_at,
        "ui_contract": {
            "context": {
                "hero_id": 302,
                "map_id": 2401,
                "round": 1,
                "phase": "bidding",
                "session_id": "2401:liss",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    labels = {flag.get("label") for flag in result.get("flags") or []}

    assert module.normalize_hero_key("莉丝") == "liss"
    assert module._snapshot_hero_key(snapshot, snapshot["ui_contract"]["context"]) == "liss"  # type: ignore[attr-defined]
    assert result["context"]["hero"] == "liss"
    assert result["context"]["is_supported_ref_hero"] is True
    assert "通用 Ref" in labels
    assert "未识别英雄" not in labels
    unknown_snapshot = {"ui_contract": {"context": {"hero_id": 999}}}
    unknown_key = module._snapshot_hero_key(  # type: ignore[attr-defined]
        unknown_snapshot,
        unknown_snapshot["ui_contract"]["context"],
    )
    assert unknown_key == ""
    assert module.is_supported_ref_hero(unknown_key) is False


def test_ahmad_server_passes_concrete_list_to_typed_note_helpers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The encrypted Cython module enforces list[str] at helper call boundaries."""
    module = _ahmad_server_module()
    observed: list[list[str]] = []

    class _Result:
        def as_dict(self):
            return {
                "status": "ok",
                "source": "ref_v0",
                "notes": ["native-summary-probe"],
                "evidence": {"hero": "liss"},
            }

    def _quote_pass_kind(ref_notes):
        assert type(ref_notes) is list
        observed.append(ref_notes)
        return ""

    monkeypatch.setattr(module, "run_reference_engine", lambda *_args, **_kwargs: _Result())
    monkeypatch.setattr(module, "_aisha_quote_pass_kind", _quote_pass_kind)
    created_at = time.time()
    snapshot = {
        "created_at": created_at,
        "ui_contract": {
            "context": {
                "hero_id": 302,
                "map_id": 2401,
                "round": 1,
                "phase": "bidding",
                "session_id": "2401:native-list-contract",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
    }

    summary = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["ahmed_ref"]["status"] == "ok"
    assert observed == [["native-summary-probe"]]


def _warehouse_source_summary_snapshot(qualification: str) -> dict:
    created_at = time.time()
    official_distribution_eligible = (
        qualification == "official_random_warehouse"
    )
    quote_policy = (
        "normal"
        if official_distribution_eligible
        else "advisory_only_no_clamp"
    )
    warehouse_source = {
        "qualification": qualification,
        "reason": "test_source",
        "map_id": 2401,
        "map_name": "未知别墅",
        "source_generation": "postupdate_player_warehouse_activity",
        "official_distribution_eligible": official_distribution_eligible,
        "quote_policy": quote_policy,
    }
    return {
        "created_at": created_at,
        "ui_contract": {
            "context": {
                "hero_id": 302,
                "map_id": 2401,
                "round": 1,
                "phase": "bidding",
                "session_id": "2401:warehouse-source",
                "warehouse_source": warehouse_source,
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": (
                    official_distribution_eligible
                ),
                "quote_policy": quote_policy,
            },
            "baseline": {
                "decision": {
                    "defend_bid": 100_000,
                    "attack_bid": 200_000,
                    "stop_price": 300_000,
                },
                "posterior": {},
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
    }


def test_ahmad_server_marks_player_warehouse_without_changing_quote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.setattr(module, "run_reference_engine", None)
    official = module.summarize_snapshot(
        _warehouse_source_summary_snapshot("official_random_warehouse"),
        snapshot_path=tmp_path / "official_snapshot.json",
    )
    custom = module.summarize_snapshot(
        _warehouse_source_summary_snapshot("player_custom_warehouse"),
        snapshot_path=tmp_path / "custom_snapshot.json",
    )

    custom_flags = {
        flag.get("label"): flag
        for flag in custom.get("flags") or []
    }
    assert "玩家自定义仓" in custom_flags
    assert custom_flags["玩家自定义仓"]["level"] == "watch"
    assert "报价仅作参考" in custom_flags["玩家自定义仓"]["detail"]
    assert official["reference"] == custom["reference"]
    assert custom["context"]["warehouse_source_qualification"] == (
        "player_custom_warehouse"
    )


def test_ahmad_server_marks_unknown_postupdate_warehouse_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.setattr(module, "run_reference_engine", None)
    result = module.summarize_snapshot(
        _warehouse_source_summary_snapshot(
            "postupdate_unknown_warehouse_source"
        ),
        snapshot_path=tmp_path / "unknown_source_snapshot.json",
    )

    flags = {
        flag.get("label"): flag
        for flag in result.get("flags") or []
    }
    assert "仓来源待确认" in flags
    assert flags["仓来源待确认"]["level"] == "watch"
    assert "样本已隔离" in flags["仓来源待确认"]["detail"]


def test_ahmad_server_labels_raven_preopen_alias_as_template_estimate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.setattr(module, "run_reference_engine", None)
    snapshot = _warehouse_source_summary_snapshot(
        "postupdate_unknown_warehouse_source"
    )
    alias = {
        "mode": "raven_preopen_510x_to_250x_template",
        "display_label": "模板估算",
        "source_map_id": 5102,
        "model_map_id": 2502,
        "estimated": True,
        "observed_raven_games": 0,
        "pool_graph_claim": False,
        "exact_rate_claim": False,
        "source_generation": "d66aee-2026-08-20-599",
    }
    snapshot["ui_contract"]["context"].update(
        {
            "map_id": 5102,
            "model_map_id": 2502,
            "map_alias": alias,
            "map_alias_mode": alias["mode"],
        }
    )
    snapshot["ui_contract"]["source"].update(
        {"map_alias": alias, "map_alias_mode": alias["mode"]}
    )

    result = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "raven_template_snapshot.json",
    )
    flags = {flag["label"]: flag for flag in result.get("flags") or []}

    assert "模板估算" in flags
    assert "非精确掉落池或概率" in flags["模板估算"]["detail"]
    assert "地图 fallback" not in flags

    alias["source_generation"] = "wrong-generation"
    rejected = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "raven_template_wrong_generation.json",
    )
    rejected_labels = {
        flag["label"] for flag in rejected.get("flags") or []
    }
    assert "模板估算" not in rejected_labels
    assert "地图 fallback" not in rejected_labels


def test_ahmad_server_compact_grid_cells() -> None:
    module = _ahmad_server_module()
    assert module._compact_grid_cells(112.4) == "112"  # type: ignore[attr-defined]
    assert module._compact_grid_cells(86.0) == "86"  # type: ignore[attr-defined]
    assert module._range_text([80.1, 84.66, 90.0], suffix="格") == "80 / 85 / 90格"  # type: ignore[attr-defined]
    module = _ahmad_server_module()

    assert module._aisha_should_dual_pass("aisha", 1, "bidding") is True  # type: ignore[attr-defined]
    assert module._aisha_should_dual_pass("aisha", 5, "bidding") is True  # type: ignore[attr-defined]
    assert module._aisha_should_dual_pass("aisha", 6, "bidding") is False  # type: ignore[attr-defined]
    assert module._aisha_should_dual_pass("aisha", 1, "settled") is False  # type: ignore[attr-defined]
    assert module._aisha_should_dual_pass("ahmed", 1, "bidding") is False  # type: ignore[attr-defined]


def test_hero_ref_live_schedule_classification_and_combos() -> None:
    tools_dir = ROOT / "hero_ref" / "tools"
    path = tools_dir / "hero_ref_live_schedule.py"
    spec = importlib.util.spec_from_file_location("hero_ref_live_schedule_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.hero_inference_category("aisha") == module.HeroInferenceCategory.MULTI_ROUND_SKILL
    assert module.hero_inference_category("ahmed") == module.HeroInferenceCategory.PROGRESSIVE_COUNT
    assert module.hero_inference_category("victor") == module.HeroInferenceCategory.EARLY_SINGLE_SKILL
    assert module.hero_inference_category("tatiana") == module.HeroInferenceCategory.STAGED_ROUNDS
    assert module.hero_inference_category("chenmei") == module.HeroInferenceCategory.STAGED_ROUNDS
    assert module.hero_inference_category("george") == module.HeroInferenceCategory.STAGED_ROUNDS
    assert module.hero_inference_category("raven") == module.HeroInferenceCategory.LATE_SINGLE_SKILL
    wuqilin = module.get_hero_schedule("wuqilin")
    assert wuqilin is not None
    assert wuqilin.skill_round_labels[4] == "R4古董1/3完整信息"
    assert module.PRIORITY_MANAGED_HERO_KEYS == frozenset(
        {"aisha", "ahmed", "raven", "wuqilin", "sophie", "gabriela"}
    )
    assert module.hero_max_combos_for_round("aisha", 1) == 2500
    assert module.hero_max_combos_for_round("aisha", 3) == 8000
    assert module.hero_max_combos_for_round("raven", 2) == 1500
    assert module.hero_max_combos_for_round("raven", 5) == 12000
    assert len(module.hero_classification_table()) == 20


@pytest.mark.parametrize(
    ("hero_key", "wait_hint"),
    [
        ("carlos", "等待R2家具+数码品质"),
        ("takeda", "等待R2书画古籍品质"),
    ],
)
def test_postupdate_r2_missing_cumulative_frame_fails_open_with_real_signal(
    monkeypatch: pytest.MonkeyPatch,
    hero_key: str,
    wait_hint: str,
) -> None:
    tools_dir = ROOT / "hero_ref" / "tools"
    path = tools_dir / "hero_ref_live_schedule.py"
    spec = importlib.util.spec_from_file_location(
        f"hero_ref_live_schedule_{hero_key}_test", path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    snapshot = {
        "minimap": {
            "items": [
                {
                    "runtime_id": 1758737576918150,
                    "quality": 5,
                    "cells": 4,
                }
            ]
        }
    }

    assert module.hero_quote_ready(snapshot, hero_key, 2) == (True, "")
    assert module.hero_quote_ready({}, hero_key, 2) == (False, wait_hint)

    monkeypatch.setenv(module.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, "1")
    assert module.hero_quote_ready(snapshot, hero_key, 2) == (
        False,
        wait_hint,
    )


@pytest.mark.parametrize(
    ("hero_key", "map_id", "runtime_id"),
    [
        ("carlos", 2406, 1758737576918150),
        ("takeda", 2410, 1758737577366500),
    ],
)
def test_server_postupdate_r2_missing_frame_keeps_product_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hero_key: str,
    map_id: int,
    runtime_id: int,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.delenv(
        "BIDKING_DISABLE_SCHEDULED_SKILL_SPARSE_FAIL_OPEN",
        raising=False,
    )
    calls: list[int] = []

    def _track(_snapshot, **kwargs):
        calls.append(int(kwargs.get("max_combos", 0)))

        class _Result:
            def as_dict(self):
                return {
                    "status": "count_prior",
                    "source": "ref_v0",
                    "conservative": 300_000,
                    "balanced": 400_000,
                    "aggressive": 500_000,
                    "combo_count": 8000,
                    "total_grid_range": [76, 82, 90],
                    "quality_count_ranges": {
                        "q1": [10, 12, 14],
                        "q3": [5, 7, 9],
                        "q4": [3, 5, 7],
                        "q5": [1, 2, 3],
                        "q6": [0, 1, 2],
                    },
                    "quality_cells_ranges": {
                        "q1": [20, 24, 28],
                        "q3": [10, 14, 18],
                        "q4": [6, 10, 14],
                        "q5": [2, 4, 6],
                        "q6": [0, 3, 6],
                    },
                    "red_count_range": [0, 1, 2],
                    "red_cells_range": [0, 3, 6],
                    "red_value_range": [0, 100_000, 200_000],
                    "notes": [],
                    "evidence": {
                        "hero": hero_key,
                        "source_notes": [],
                    },
                }

        return _Result()

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "minimap": {
            "items": [
                {
                    "runtime_id": runtime_id,
                    "quality": 5,
                    "cells": 4,
                }
            ]
        },
        "ui_contract": {
            "context": {
                "hero": hero_key,
                "map_id": map_id,
                "phase": "bidding",
                "round": 2,
                "session_id": f"{map_id}:postupdate-r2-fail-open",
            },
        },
    }

    result = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert calls == [8000]
    assert result["ahmed_ref"]["status"] == "count_prior"
    assert result["reference"]["balanced"] == "400,000"
    assert result["red"]["quality_count_summary"] != "-"
    assert "hero_ref_waiting" not in result["ahmed_ref"]["notes"]


def test_manual_engine_max_combos_caps_at_manual_ceiling() -> None:
    # Manual recompute runs synchronously on the UI thread, so it is hard-capped at
    # _MANUAL_MAX_COMBOS (2500) to avoid 16-30s freezes on deep warehouses. The live
    # schedule (monitor process) is unaffected. The cap floors every round at 2500
    # and only lets a hero's own lower budget (e.g. raven R2=1500) through unchanged.
    module = _ahmad_overlay_module()
    cap = module._MANUAL_MAX_COMBOS
    assert cap == 2500
    assert module._manual_engine_max_combos({"hero": "aisha", "round": 3}) == cap  # min(8000, 2500)
    assert module._manual_engine_max_combos({"hero": "aisha", "round": 5}) == cap  # min(12000, 2500)
    assert module._manual_engine_max_combos({"hero": "raven", "round": 2}) == 1500  # below the cap
    assert module._manual_engine_max_combos({"hero": "aisha"}) == cap  # no round -> cap


def test_ahmad_server_ahmed_waits_without_skill_or_public(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    calls: list[int] = []

    def _track(_snapshot, **kwargs):
        calls.append(int(kwargs.get("max_combos", 0)))
        class _Result:
            def as_dict(self):
                return {"status": "ok", "source": "ref_v0", "notes": [], "evidence": {"hero": "ahmed"}}

        return _Result()

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "phase": "bidding",
                "round": 1,
                "session_id": "2406:ahmed-wait",
            },
        },
    }
    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    assert calls == []
    assert "hero_ref_waiting" in result["ahmed_ref"].get("notes", [])


def test_ahmad_server_settled_snapshot_stale_when_capture_session_advances(
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    snapshot_path = log_dir / "latest_snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    (log_dir / "capture_source_status.json").write_text(
        json.dumps(
            {
                "ts": time.time(),
                "active_session_id": "2401:9000000000000016",
            }
        ),
        encoding="utf-8",
    )
    snapshot = {
        "created_at": time.time() - 5,
        "phase": "settled",
        "session_id": "2401:9000000000000015",
        "ui_contract": {
            "context": {
                "phase": "settled",
                "session_id": "2401:9000000000000015",
            },
            "truth": {"available": True, "total_value": 623840},
        },
    }
    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    assert result["status"] == "stale_snapshot"
    assert result["stale"]["reason"] == "session_ahead"


def test_ahmad_server_age_stale_keeps_last_quote_in_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_server_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    stale_at = time.time() - 65

    def _fake_engine(*_args, **_kwargs):
        class _Result:
            def as_dict(self):
                return {
                    "status": "count_prior",
                    "source": "ref_v0",
                    "balanced": 384_321,
                    "conservative": 264_000,
                    "aggressive": 520_000,
                    "notes": [],
                    "evidence": {"hero": "gabriela"},
                    "combo_count": 864,
                }

        return _Result()

    monkeypatch.setattr(module, "run_reference_engine", _fake_engine)
    snapshot = {
        "created_at": stale_at,
        "ui_contract": {
            "context": {
                "hero": "gabriela",
                "phase": "reading",
                "round": 1,
                "map_id": 2401,
                "session_id": "2401:9000000000000017",
            },
        },
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    os.utime(snapshot_path, (stale_at, stale_at))
    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    assert result["status"] == "ok"
    assert result["reference"]["balanced"] == "384,321"
    assert result["stale"]["reason"] == "stale_snapshot"
    assert result["stale"]["keep_last_quote"] is True
    assert any(flag.get("label") == "快照过期" for flag in result.get("flags") or [])


def test_ahmad_server_age_stale_keep_last_quote_rollback_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.setenv(module.HERO_REF_DISABLE_STALE_KEEP_LAST_QUOTE, "1")
    snapshot_path = tmp_path / "latest_snapshot.json"
    stale_at = time.time() - 65
    snapshot = {
        "created_at": stale_at,
        "ui_contract": {
            "context": {
                "hero": "gabriela",
                "phase": "bidding",
                "round": 1,
                "session_id": "2401:9000000000000017",
            },
        },
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    os.utime(snapshot_path, (stale_at, stale_at))
    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    assert result["status"] == "stale_snapshot"
    assert result["reference"]["balanced"] == "-"


def test_ahmad_server_age_stale_keep_last_quote_skipped_outside_live_phases(
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    stale_at = time.time() - 65
    snapshot = {
        "created_at": stale_at,
        "ui_contract": {
            "context": {
                "hero": "gabriela",
                "phase": "manual",
                "round": 1,
                "session_id": "2401:manual",
            },
        },
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    os.utime(snapshot_path, (stale_at, stale_at))
    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    assert result["status"] == "stale_snapshot"


def test_ahmad_overlay_needs_stale_refresh_when_capture_session_advances(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    snapshot_path = log_dir / "latest_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "created_at": time.time() - 5,
                "phase": "settled",
                "session_id": "2403:9000000000000013",
                "ui_contract": {
                    "context": {
                        "phase": "settled",
                        "session_id": "2403:9000000000000013",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (log_dir / "capture_source_status.json").write_text(
        json.dumps(
            {
                "ts": time.time(),
                "active_session_id": "2401:9000000000000014",
            }
        ),
        encoding="utf-8",
    )
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._manual_active = False
    overlay._last_summary = {
        "status": "ok",
        "context": {
            "phase": "settled",
            "session_id": "2403:9000000000000013",
        },
    }
    assert module.AhmadTkOverlay._needs_stale_refresh(overlay) is True


def test_ahmad_server_ahmed_runs_after_total_skill(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    calls: list[int] = []

    def _track(_snapshot, **kwargs):
        calls.append(int(kwargs.get("max_combos", 0)))
        class _Result:
            def as_dict(self):
                return {"status": "ok", "source": "ref_v0", "notes": [], "evidence": {"hero": "ahmed"}}

        return _Result()

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "skill_reveal_rows": [
            {"hero_id": 204, "skill_id": 100204, "result": 39, "revealed_items": 0},
        ],
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "phase": "bidding",
                "round": 1,
                "session_id": "2406:ahmed-skill",
            },
        },
    }
    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    assert calls == [2500]
    assert result["ahmed_ref"].get("status") == "ok"


def test_ahmad_server_aisha_item_frame_ready() -> None:
    module = _ahmad_server_module()

    assert module._aisha_item_frame_ready({"action_result_rows": []}, 1) is False  # type: ignore[attr-defined]
    r1_skill = {
        "skill_id": 1001034,
        "hero_id": 103,
        "sort": 10,
        "revealed_items": 3,
    }
    r1_prop = {
        "sort": 12,
        "action_id": 100163,
        "result": 120000,
        "revealed_items_detail": [{"quality": 5}],
    }
    assert module._aisha_item_frame_ready(  # type: ignore[attr-defined]
        {
            "skill_reveal_rows": [r1_skill],
            "action_result_rows": [r1_prop],
        },
        1,
    ) is True
    assert module._aisha_item_frame_ready(  # type: ignore[attr-defined]
        {
            "skill_reveal_rows": [r1_skill],
            "action_result_rows": [
                {**r1_prop, "sort": 8},
            ],
        },
        1,
    ) is False


def test_ahmad_server_aisha_r2_ignores_r1_prop_without_r2_prop() -> None:
    module = _ahmad_server_module()

    snapshot = {
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 3,
            },
            {
                "skill_id": 1001033,
                "hero_id": 103,
                "sort": 20,
                "revealed_items": 2,
            },
        ],
        "action_result_rows": [
            {
                "sort": 12,
                "action_id": 100163,
                "result": 120000,
                "revealed_items_detail": [{"quality": 5}],
            }
        ],
    }
    assert module._aisha_item_frame_ready(snapshot, 2) is False  # type: ignore[attr-defined]


def test_ahmad_server_aisha_early_round_quote_ready() -> None:
    module = _ahmad_server_module()
    public_info = {"public_numeric_summary": "公开3件"}
    skill_row = {
        "skill_id": 1001034,
        "hero_id": 103,
        "revealed_items": 4,
        "observed_items": [{"quality": 1, "cells": 4}],
    }
    public_row = {"info_id": 200004, "revealed_items": 3}

    ready, hint = module._aisha_early_round_quote_ready(  # type: ignore[attr-defined]
        {"skill_reveal_rows": [skill_row], "public_info_rows": [public_row]},
        1,
        public_info,
    )
    assert ready is True
    assert hint == ""

    ready, hint = module._aisha_early_round_quote_ready(  # type: ignore[attr-defined]
        {"public_info_rows": [public_row]},
        1,
        public_info,
    )
    assert ready is False
    assert hint == "等待白品技能帧"

    ready, hint = module._aisha_early_round_quote_ready(  # type: ignore[attr-defined]
        {"skill_reveal_rows": [skill_row]},
        1,
        {},
    )
    assert ready is True
    assert hint == ""


def test_ahmad_server_aisha_round_public_info_ready() -> None:
    module = _ahmad_server_module()
    r1_public = {"info_id": 200004, "sort": 8, "revealed_items": 3}
    r2_skill = {
        "skill_id": 1001033,
        "hero_id": 103,
        "sort": 20,
        "revealed_items": 2,
    }
    r2_public = {"info_id": 200013, "sort": 22, "value": 12}

    assert module._aisha_round_public_info_ready(  # type: ignore[attr-defined]
        {"skill_reveal_rows": [r2_skill], "public_info_rows": [r1_public, r2_public]},
        2,
    ) is True
    assert module._aisha_round_public_info_ready(  # type: ignore[attr-defined]
        {"skill_reveal_rows": [r2_skill], "public_info_rows": [r1_public]},
        2,
    ) is False


def test_ahmad_server_aisha_r1_quotes_with_skill_only_no_public(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(snapshot.get("audit_aisha_engine_pass"))
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-skill-only",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == [module.AISHA_ENGINE_PASS_SKILL]  # type: ignore[attr-defined]
    assert "aisha_pre_r3_calculator_deferred" not in result["ahmed_ref"].get("notes", [])
    assert result["reference"]["balanced"] != "-"
    assert result["reference"]["action"] != "防守参考"
    assert not any(flag["label"] == "R1–R2防守" for flag in result["flags"])
    assert any(flag["label"] == "R1防守×2.0" for flag in result["flags"])


def test_ahmad_server_aisha_r1_quotes_with_warehouse_grid_only(tmp_path: Path, monkeypatch) -> None:
    """R1 total warehouse cells without total_count or avg_cells should still quote."""
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(snapshot.get("audit_aisha_engine_pass"))
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-warehouse-r1",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "action_result_rows": [
            {"sort": 11, "action_id": 100103, "result": 156},
        ],
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured[0] == module.AISHA_ENGINE_PASS_SKILL  # type: ignore[attr-defined]
    assert "waiting_total_count" not in result["ahmed_ref"].get("notes", [])
    assert result["reference"]["balanced"] != "-"
    assert result["reference"]["readiness"] in {"count_prior", "live_ready", "ok"}


def test_ahmad_server_aisha_r1_waits_without_skill_and_public(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(True)
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-wait-r1",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == []
    assert "aisha_early_round_waiting" in result["ahmed_ref"].get("notes", [])
    assert any(flag["label"] == "等待白品技能帧" for flag in result["flags"])
    assert result["reference"]["balanced"] == "-"


def test_ahmad_server_aisha_r1_uses_lightweight_engine(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(
            {
                "audit": snapshot.get("audit_aisha_early_round"),
                "pass": snapshot.get("audit_aisha_engine_pass"),
                **kwargs,
            }
        )
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-light-r1",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {"public_numeric_summary": "公开3件"},
            },
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 3}],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert len(captured) == 1
    assert captured[0]["audit"] is True
    assert captured[0]["pass"] == module.AISHA_ENGINE_PASS_SKILL  # type: ignore[attr-defined]
    assert "aisha_pre_r3_calculator_deferred" not in result["ahmed_ref"].get("notes", [])
    assert any(flag["label"] == "R1–R2轻量" for flag in result["flags"])
    assert not any(flag["label"] == "R1–R2防守" for flag in result["flags"])
    assert any(flag["label"] == "R1防守×2.0" for flag in result["flags"])
    assert result["reference"]["balanced"] != "-"
    assert "总件 28" not in result["red"]["uncertainty_summary"]


def test_ahmad_server_aisha_r1_runs_skill_and_item_dual_pass(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(
            {
                "pass": snapshot.get("audit_aisha_engine_pass"),
                "actions": len(snapshot.get("action_result_rows") or []),
                **kwargs,
            }
        )
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-dual-r1",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {"public_numeric_summary": "公开3件"},
            },
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 3}],
        "action_result_rows": [
            {
                "sort": 12,
                "action_id": 100163,
                "result": 120000,
                "revealed_items_detail": [{"quality": 5, "cells": 3, "row": 16}],
            }
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert len(captured) == 2
    assert captured[0]["pass"] == module.AISHA_ENGINE_PASS_SKILL  # type: ignore[attr-defined]
    assert captured[1]["pass"] == module.AISHA_ENGINE_PASS_ITEM  # type: ignore[attr-defined]
    assert "aisha_pre_r3_calculator_deferred" not in result["ahmed_ref"].get("notes", [])
    assert result["reference"]["balanced"] != "-"


def test_ahmad_server_aisha_applies_same_round_item_even_when_skill_budget_exceeded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    monkeypatch.setattr(module, "AISHA_ITEM_PASS_SKILL_BUDGET_MS", 0.0)
    captured: list[str | None] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(snapshot.get("audit_aisha_engine_pass"))
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 1,
                "session_id": "2404:aisha-budget-skip",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {"public_numeric_summary": "公开3件"},
            },
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 3}],
        "action_result_rows": [
            {
                "sort": 12,
                "round": 1,
                "action_id": 100163,
                "result": 120000,
                "revealed_items_detail": [{"quality": 5, "cells": 3, "row": 16}],
            }
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == [  # type: ignore[attr-defined]
        module.AISHA_ENGINE_PASS_SKILL,
        module.AISHA_ENGINE_PASS_ITEM,
    ]
    ref_notes = result.get("ahmed_ref", {}).get("notes", [])
    assert not any(str(note).startswith("aisha_item_pass_skipped:") for note in ref_notes)
    assert "aisha_quote_pass:item" in ref_notes


def test_ahmad_server_aisha_future_round_item_does_not_apply_early(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    captured: list[str | None] = []
    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda snapshot, **_kwargs: captured.append(snapshot.get("audit_aisha_engine_pass"))
        or {"status": "count_prior", "notes": []},
    )
    snapshot = {
        "created_at": time.time(),
        "round": 1,
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 2404, "phase": "bidding", "round": 1},
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 4,
                "observed_items": [{"quality": 1, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 3}],
        "action_result_rows": [
            {"sort": 12, "round": 2, "action_id": 100163, "result": 120000}
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == [module.AISHA_ENGINE_PASS_SKILL]  # type: ignore[attr-defined]


def test_ahmad_server_aisha_r3_runs_dual_pass_skill_only(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(
            {
                "audit": snapshot.get("audit_aisha_early_round"),
                "pass": snapshot.get("audit_aisha_engine_pass"),
                **kwargs,
            }
        )
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 3,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 3,
                "session_id": "2404:aisha-run-r3",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {"public_numeric_summary": "公开蓝均格"},
            },
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001032,
                "hero_id": 103,
                "sort": 30,
                "revealed_items": 2,
                "observed_items": [{"quality": 3, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 2}],
    }

    module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert len(captured) == 1
    assert captured[0].get("audit") is None
    assert captured[0]["pass"] == module.AISHA_ENGINE_PASS_SKILL  # type: ignore[attr-defined]
    assert captured[0]["max_combos"] == 8_000


def test_ahmad_server_aisha_r5_keeps_delayed_inferred_zero_in_skill_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A result-less R4 tool becomes a confirmed zero only after the R5 state lands."""
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
        captured.append(
            {
                "pass": snapshot.get("audit_aisha_engine_pass"),
                "top_ids": [str(row.get("action_id")) for row in snapshot.get("action_result_rows") or []],
                "ui_ids": [str(row.get("action_id")) for row in actions.get("results") or []],
            }
        )
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    inferred_zero = {
        "sort": 26,
        "action_id": 100113,
        "result": 0,
        "inferred_zero": True,
        "result_inference": "sent_action_without_result_after_later_state",
    }
    ordinary_old_action = {"sort": 19, "action_id": 100115, "result": 32}
    snapshot = {
        "created_at": time.time(),
        "round": 5,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 5611,
                "phase": "bidding",
                "round": 5,
                "action_round": 5,
                "observed_round": 4,
                "session_id": "5611:aisha-delayed-zero",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
            "actions": {
                "results": [
                    {**inferred_zero, "action_id": "100113", "result": "0", "inferred_zero": "True"},
                    {**ordinary_old_action, "action_id": "100115", "result": "32"},
                ]
            },
        },
        # Cumulative skill rows are re-stamped by the later state. The zero action
        # therefore sorts before this R4-skill copy even though it was used in R4.
        "skill_reveal_rows": [
            {
                "sort": 32,
                "skill_id": 1001031,
                "hero_id": 103,
                "revealed_items": 11,
                "observed_items": [{"quality": 4, "cells": 4}],
            }
        ],
        "action_result_rows": [inferred_zero, ordinary_old_action],
        "structured_ref_inputs": {
            "total_count": 32,
            "fixed_counts": {"q1": 12, "q3": 7, "q4": 11},
            "quality_cells": {"q1": 29, "q3": 12, "q4": 51},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == [
        {"pass": module.AISHA_ENGINE_PASS_SKILL, "top_ids": ["100113"], "ui_ids": ["100113"]}
    ]
    ref = result["ahmed_ref"]
    assert ref["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert ref["quality_cells_ranges"]["q5"] == [0, 0, 0]
    assert ref["evidence"]["fixed_counts"]["q5"] == 0
    assert ref["evidence"]["avg_cells"]["q5"] == 0.0
    assert "action_100113_q5_avg_cells_inferred_zero" in ref["notes"]


def test_ahmad_server_aisha_skill_pass_keeps_only_persistent_item_floors() -> None:
    module = _ahmad_server_module()
    rows = [
        {
            "sort": 3,
            "action_id": 100129,
            "result": 5,
            "result_field": 14,
            "revealed_items_detail": [
                {
                    "runtime_id": 6001,
                    "item_id": 1916005,
                    "quality": 6,
                    "value": 1_041_000,
                    "shape_code": 11,
                    "cells": 1,
                },
                {
                    "runtime_id": 4001,
                    "item_id": 1054007,
                    "quality": 4,
                    "value": 18_240,
                    "shape_code": 22,
                    "cells": 4,
                },
            ],
        },
        {"sort": 19, "action_id": 100115, "result": 28},
    ]

    kept = module._aisha_skill_pass_action_rows(rows)  # type: ignore[attr-defined]

    assert len(kept) == 1
    assert kept[0]["action_id"] == 100129
    assert kept[0]["persistent_item_floor_only"] is True
    assert "result" not in kept[0]
    assert "result_field" not in kept[0]
    assert [item["value"] for item in kept[0]["revealed_items_detail"]] == [
        1_041_000,
        18_240,
    ]


def test_ahmad_server_aisha_old_known_value_survives_later_skill_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "round": 4,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2541,
                "phase": "bidding",
                "round": 4,
                "action_round": 4,
                "observed_round": 3,
                "session_id": "2541:aisha-persistent-item-floor",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "sort": 30,
                "skill_id": 1001031,
                "hero_id": 103,
                "revealed_items": 1,
                "observed_items": [
                    {"runtime_id": 4002, "quality": 4, "shape_code": 11, "cells": 1}
                ],
            }
        ],
        # This R1 item is older than the current skill frame, so it must not trigger an item pass.
        # Its disclosed value is nevertheless a permanent floor for every later quote.
        "action_result_rows": [
            {
                "sort": 3,
                "action_id": 100129,
                "result": 1,
                "revealed_items_detail": [
                    {
                        "runtime_id": 6001,
                        "item_id": 1916005,
                        "quality": 6,
                        "value": 2_000_000,
                        "shape_code": 11,
                        "cells": 1,
                    }
                ],
            }
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q4": 1},
            "quality_cells": {"q4": 1},
        },
    }

    result = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    ref = result["ahmed_ref"]
    assert "aisha_quote_pass:skill" in ref["notes"]
    assert "aisha_quote_pass:skill_no_items_this_round" in ref["notes"]
    assert ref["evidence"]["quality_value_floors"]["q6"] == 2_000_000
    assert module._parse_money(result["reference"]["conservative"]) >= 2_000_000
    assert any(
        str(note).startswith("known_quality_value_quote_floor:2000000:")
        for note in ref["notes"]
    )

    monkeypatch.setenv(module.AISHA_PERSISTENT_ITEM_FLOORS_DISABLE, "1")
    legacy = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "legacy_snapshot.json",
    )
    assert legacy["ahmed_ref"]["evidence"]["quality_value_floors"].get("q6") is None
    assert module._parse_money(legacy["reference"]["conservative"]) < 2_000_000


def test_ahmad_server_aisha_r2_runs_skill_only_when_no_r2_prop(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    captured: list[dict[str, Any]] = []
    original = module.run_reference_engine

    def _track(snapshot, **kwargs):
        captured.append(snapshot.get("audit_aisha_engine_pass"))
        return original(snapshot, **kwargs)

    monkeypatch.setattr(module, "run_reference_engine", _track)
    snapshot = {
        "created_at": time.time(),
        "round": 2,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 2,
                "session_id": "2404:aisha-r2-no-prop",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {
                "public_info": {"public_numeric_summary": "公开3件"},
            },
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001034,
                "hero_id": 103,
                "sort": 10,
                "revealed_items": 3,
                "observed_items": [{"quality": 1, "cells": 4}],
            },
            {
                "skill_id": 1001033,
                "hero_id": 103,
                "sort": 20,
                "revealed_items": 2,
                "observed_items": [{"quality": 2, "cells": 3}],
            },
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 3}],
        "action_result_rows": [
            {
                "sort": 12,
                "action_id": 100163,
                "result": 120000,
                "revealed_items_detail": [{"quality": 5, "cells": 3}],
            }
        ],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8},
            "quality_cells": {"q1": 16},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert captured == [module.AISHA_ENGINE_PASS_SKILL]  # type: ignore[attr-defined]
    assert "aisha_pre_r3_calculator_deferred" not in result["ahmed_ref"].get("notes", [])
    assert any(flag["label"] == "R1–R2轻量" for flag in result["flags"])
    assert not any(flag["label"] == "R1–R2防守" for flag in result["flags"])
    assert result["reference"]["balanced"] != "-"


def test_ahmad_server_aisha_live_hides_ref_minus_v3(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_server_module()
    original = module.run_reference_engine

    def _fake(_snapshot, **kwargs):
        return original(
            {
                **_snapshot,
                "structured_ref_inputs": {
                    "total_count": 28,
                    "fixed_counts": {"q1": 8, "q3": 6, "q4": 4, "q5": 3},
                    "quality_cells": {"q1": 16, "q3": 12, "q4": 11, "q5": 9},
                },
            },
            **kwargs,
        )

    monkeypatch.setattr(module, "run_reference_engine", _fake)
    snapshot = {
        "created_at": time.time(),
        "round": 3,
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 3,
                "session_id": "2404:aisha-ref-minus",
            },
            "baseline": {
                "decision": {"attack_bid": 500_000},
                "posterior": {},
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": time.time()},
        },
        "skill_reveal_rows": [
            {
                "skill_id": 1001032,
                "hero_id": 103,
                "sort": 30,
                "revealed_items": 2,
                "observed_items": [{"quality": 3, "cells": 4}],
            }
        ],
        "public_info_rows": [{"info_id": 200004, "revealed_items": 2}],
        "structured_ref_inputs": {
            "total_count": 28,
            "fixed_counts": {"q1": 8, "q3": 6, "q4": 4, "q5": 3},
            "quality_cells": {"q1": 16, "q3": 12, "q4": 11, "q5": 9},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["reference"]["ref_minus_v3_balanced"] == "-"
    assert result["reference"]["ref_minus_v3_balanced_raw"] is None
    assert not any(flag["label"] == "v3价差" for flag in result["flags"])


def test_ahmad_server_aisha_next_info_hint_r1_waits_green_and_blue() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "quality_count_ranges": {
            "q1": [8, 12, 16],
            "q3": [6, 10, 14],
            "q4": [4, 6, 8],
            "q5": [2, 3, 4],
            "q6": [0, 1, 1],
        },
        "evidence": {"total_count": 32},
    }

    hint = module._next_info_hint(result, hero_key="aisha", round_no=1)  # type: ignore[attr-defined]

    assert hint == "等待绿品和蓝品信息"
    assert "白绿" not in hint
    assert "总件" not in hint


def test_ahmad_server_aisha_missing_total_count_r1_waits_green_and_blue() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "missing_total_count",
        "notes": ["waiting_total_count"],
        "quality_count_ranges": {
            "q1": [6, 12, 18],
            "q3": [4, 10, 16],
        },
        "evidence": {"total_count": None, "total_grid_target": 140},
    }

    hint = module._next_info_hint(result, hero_key="aisha", round_no=1)  # type: ignore[attr-defined]

    assert hint == "等待绿品和蓝品信息"
    assert module._ref_waiting_display_text("aisha", result, round_no=1) == hint  # type: ignore[attr-defined]


def test_ahmad_server_aisha_next_info_hint_puts_total_count_last() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [120, 120, 120],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [3, 3, 3],
            "q6": [0, 1, 1],
        },
        "evidence": {
            "total_count": None,
            "total_grid_target": 120,
            "fixed_counts": {"q3": 9, "q4": 6, "q5": 3},
            "quality_cells": {"q3": 14, "q4": 18},
            "avg_cells": {"q5": 2.5},
        },
    }

    hint = module._next_info_hint(result, hero_key="aisha")  # type: ignore[attr-defined]

    assert hint == "等待总件信息"


def test_ahmad_server_aisha_next_info_hint_gold_value_alone_still_waits_gold() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [120, 120, 120],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [3, 3, 3],
            "q6": [0, 1, 1],
        },
        "evidence": {
            "total_count": 33,
            "fixed_counts": {"q3": 9, "q4": 6, "q5": 3},
            "quality_cells": {"q3": 14, "q4": 18},
            "avg_values": {"q5": 26730},
        },
    }

    hint = module._next_info_hint(result, hero_key="aisha")  # type: ignore[attr-defined]

    assert hint == "等待金品信息"
    assert "估价" not in hint


def test_ahmad_server_aisha_next_info_hint_waits_green_and_purple_after_blue_locked() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "quality_count_ranges": {
            "q1": [8, 12, 16],
            "q3": [9, 9, 9],
            "q4": [4, 8, 12],
            "q5": [2, 3, 4],
            "q6": [0, 1, 1],
        },
        "evidence": {
            "total_count": 32,
            "fixed_counts": {"q3": 9},
            "quality_cells": {"q3": 14},
        },
    }

    hint = module._next_info_hint(result, hero_key="aisha", round_no=3)  # type: ignore[attr-defined]

    assert hint == "等待绿品和紫品信息"


def test_ahmad_server_aisha_next_info_hint_waits_gold_then_total_grid() -> None:
    module = _ahmad_server_module()
    purple_done = {
        "status": "ok",
        "total_grid_range": [90, 110, 130],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [2, 4, 6],
            "q6": [0, 1, 1],
        },
        "evidence": {
            "total_count": 33,
            "fixed_counts": {"q3": 9, "q4": 6},
            "quality_cells": {"q3": 14, "q4": 18},
        },
    }
    gold_done = {
        **purple_done,
        "quality_count_ranges": {
            **purple_done["quality_count_ranges"],
            "q5": [3, 3, 3],
        },
        "evidence": {
            **purple_done["evidence"],
            "fixed_counts": {**purple_done["evidence"]["fixed_counts"], "q5": 3},
            "avg_cells": {"q5": 2.5},
        },
    }

    assert (
        module._next_info_hint(purple_done, hero_key="aisha")  # type: ignore[attr-defined]
        == "等待金品和总格信息"
    )
    assert (
        module._next_info_hint(gold_done, hero_key="aisha")  # type: ignore[attr-defined]
        == "等待总格信息"
    )


def test_ahmad_server_aisha_next_info_hint_purple_known_gold_unknown_uses_and() -> None:
    module = _ahmad_server_module()
    # Purple locked, gold still open, totals missing: gold cannot be back-solved
    # from totals, so it joins them with "和".
    result = {
        "status": "ok",
        "total_grid_range": [90, 110, 130],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [2, 4, 6],
            "q6": [0, 1, 1],
        },
        "evidence": {
            "total_count": None,
            "fixed_counts": {"q3": 9, "q4": 6},
            "quality_cells": {"q3": 14, "q4": 18},
        },
    }

    assert (
        module._next_info_hint(result, hero_key="aisha")  # type: ignore[attr-defined]
        == "等待金品和总格、总件信息"
    )


def test_ahmad_server_aisha_next_info_hint_all_known_matches_complete_phrase() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [120, 120, 120],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [3, 3, 3],
            "q6": [1, 1, 1],
        },
        "evidence": {
            "total_count": 33,
            "total_grid_target": 120,
            "fixed_counts": {"q3": 9, "q4": 6, "q5": 3},
            "quality_cells": {"q3": 14, "q4": 18},
            "avg_cells": {"q5": 2.5},
        },
    }

    assert (
        module._next_info_hint(result, hero_key="aisha")  # type: ignore[attr-defined]
        == "信息已足够，观察出价"
    )


def test_ahmad_server_next_info_hint_prefers_gold_before_total_grid() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [80, 96, 112],
        "quality_count_ranges": {
            "q1": [14, 14, 14],
            "q3": [9, 9, 9],
            "q4": [6, 6, 6],
            "q5": [3, 3, 6],
            "q6": [1, 1, 1],
        },
        "evidence": {
            "total_count": 33,
        },
    }

    hint = module._next_info_hint(result)  # type: ignore[attr-defined]

    assert hint == "补金件数或均格"
    assert "总格" not in hint
    assert "红" not in hint


def test_ahmad_server_next_info_hint_targets_q6_grid_when_red_count_locked() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [113, 113, 113],
        "quality_count_ranges": {
            "q1": [9, 9, 9],
            "q3": [15, 15, 15],
            "q4": [8, 8, 8],
            "q5": [6, 6, 6],
            "q6": [1, 1, 1],
        },
        "evidence": {
            "total_count": 39,
            "avg_cells": {"q1": 20 / 9, "q3": 40 / 15, "q4": 2.5, "q5": 5.0},
            "quality_cells": {"q4": 20, "q5": 30},
        },
    }

    assert module._next_info_hint(result) == "优先补总格/全均格"  # type: ignore[attr-defined]


def test_ahmad_server_next_info_hint_draw_hero_names_props() -> None:
    """Gabriela/Sophie/Raven share the cheap-prop progression; their R4 tools differ."""
    module = _ahmad_server_module()
    unlocked = {
        "q1": [0, 5, None],
        "q3": [0, 4, None],
        "q4": [0, 2, None],
        "q5": [0, 1, None],
    }
    quality_gap = {
        "status": "count_prior",
        "quality_count_ranges": unlocked,
        "evidence": {"total_count": 18, "total_grid_target": 40},
    }
    # R1-R3 starts from the same player workflow, independent of which model gap happens
    # to be widest at this refresh.
    assert module._next_info_hint(quality_gap, hero_key="sophie", round_no=2) == "宝光四鉴/随机抽检（2）"
    quality_known = {
        "status": "count_prior",
        "quality_count_ranges": {"q1": [5, 5, 5], "q3": [4, 4, 4], "q4": [2, 2, 2], "q5": [1, 1, 1]},
        "evidence": {"total_count": 18, "total_grid_target": 40},
    }
    assert module._next_info_hint(quality_known, hero_key="sophie", round_no=3) == "宝光四鉴/随机抽检（2）"
    # Early game with missing total count: still cheap quality reveal, NOT 库存清点.
    miss_count = {"status": "missing_total_count", "evidence": {"total_count": None}}
    assert module._next_info_hint(miss_count, hero_key="sophie", round_no=1) == "宝光四鉴/随机抽检（2）"
    assert module._next_info_hint(miss_count, hero_key="sophie", round_no=2) == "宝光四鉴/随机抽检（2）"
    # Decision window: Gabriela/Sophie use a 至宝 tool; Raven really uses 全库透视.
    assert module._next_info_hint(miss_count, hero_key="sophie", round_no=4) == "使用至宝道具"
    assert module._next_info_hint(miss_count, hero_key="gabriela", round_no=5) == "使用至宝道具"
    assert module._next_info_hint(miss_count, hero_key="raven", round_no=4) == "全库透视"
    # Ref heroes keep the original generic copy (unchanged).
    assert module._next_info_hint(miss_count, hero_key="ahmed") == "先补总件"
    assert module._next_info_hint(quality_gap, hero_key="ahmed") == "优先补白绿/蓝件数或均格"


def test_ahmad_server_draw_hero_prop_hint_filters_used_and_stays_stable() -> None:
    """4542-shaped sequence: sent/results both consume a prop; backfill never flickers."""
    module = _ahmad_server_module()
    result = {
        "status": "count_prior",
        "quality_count_ranges": {"q1": [0, 5, None]},
        "evidence": {"total_count": 36, "total_grid_target": 90},
    }
    session_id = "4542:9000000000000021"

    def hint(*, sent=(), results=(), round_no=2, session=session_id):
        return module._next_info_hint(  # type: ignore[attr-defined]
            result,
            hero_key="gabriela",
            round_no=round_no,
            session_id=session,
            phase="bidding",
            actions={
                "sent": [{"action_id": action_id} for action_id in sent],
                "results": [{"action_id": action_id} for action_id in results],
            },
        )

    assert hint(round_no=1) == "宝光四鉴/随机抽检（2）"

    # A sent action is consumed immediately even before a result arrives. The replacement
    # is one stable 50/50 choice, never a redraw-time random flip.
    after_four = hint(sent=(100136,), round_no=1)
    assert after_four == hint(sent=(100136,), round_no=1)
    after_four_parts = after_four.split("/")
    assert after_four_parts[0] == "随机抽检（2）"
    assert after_four_parts[1] in {"宝光双鉴", "随机抽检（1）"}
    assert hint(sent=(100136,), session="stable-0") != hint(
        sent=(100136,), session="stable-2"
    )

    # Result-only capture loss also consumes the prop. Once both opening/recommended props
    # are used, the backup that was not previously selected fills the vacant slot.
    after_four_and_double = hint(sent=(100136,), results=(100135,), round_no=2)
    assert set(after_four_and_double.split("/")) == {"随机抽检（2）", "随机抽检（1）"}
    assert hint(sent=(100136,), results=(100135, 100129), round_no=3) == "随机抽检（1）"
    assert (
        hint(sent=(100136, 100128), results=(100135, 100129), round_no=3)
        == "常用道具已用，观察出价"
    )

    # R4 always advances to the hero-specific endgame tool, regardless of cheap-prop state.
    assert hint(sent=(100136,), results=(100135, 100129), round_no=4) == "使用至宝道具"
    assert (
        module._next_info_hint(  # type: ignore[attr-defined]
            result,
            hero_key="gabriela",
            round_no=4,
            phase="settled",
        )
        == ""
    )


def test_ahmad_server_next_info_hint_never_recommends_red_or_known_gold_value() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [100, 100, 100],
        "quality_count_ranges": {
            "q1": [12, 12, 12],
            "q3": [10, 10, 10],
            "q4": [8, 8, 8],
            "q5": [1, 2, 3],
            "q6": [0, 1, 2],
        },
        "evidence": {
            "total_count": 35,
            "total_grid_target": 100,
            "avg_values": {"q5": 26730},
        },
    }

    hint = module._next_info_hint(result)  # type: ignore[attr-defined]

    assert hint == "补金件数或均格"
    assert "红" not in hint
    assert "均价" not in hint


def test_ahmad_server_next_info_hint_does_not_use_red_as_only_remaining_input() -> None:
    module = _ahmad_server_module()
    result = {
        "status": "ok",
        "total_grid_range": [100, 100, 100],
        "quality_count_ranges": {
            "q1": [12, 12, 12],
            "q3": [10, 10, 10],
            "q4": [8, 8, 8],
            "q5": [3, 3, 3],
            "q6": [0, 1, 2],
        },
        "evidence": {
            "total_count": 35,
            "total_grid_target": 100,
        },
    }

    hint = module._next_info_hint(result)  # type: ignore[attr-defined]

    assert hint == "信息已足够，观察出价"
    assert "红" not in hint


def test_ahmad_settlement_hide_masks_values_but_keeps_counts() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.settlement_values_hidden = True
    data = {
        "context": {"phase": "settled"},
        "truth": {
            "available": True,
            "total_value": 520000,
            "total_items": 21,
            "total_cells": 34,
            "q6": {"count": 1, "cells": 3, "value": 160000},
        },
    }

    assert module.AhmadTkOverlay._settlement_values_are_hidden(overlay, data) is True
    assert module.AhmadTkOverlay._settlement_display_value(overlay, data, "520,000") == "已隐藏"
    truth_text = module.AhmadTkOverlay._truth_text(overlay, data)
    assert "价值隐藏" in truth_text
    assert "21件/34格" in truth_text
    assert "红1件/3格" in truth_text
    assert "520000" not in truth_text


def test_ahmad_settlement_hide_toggle_can_be_preset_before_settlement() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    settled = {"context": {"phase": "settled"}, "truth": {"available": True}}
    not_settled = {"context": {"phase": "bidding"}, "truth": {"available": False}}
    rendered: list[dict] = []
    overlay.settlement_values_hidden = False
    overlay._last_summary = not_settled
    overlay._last_live_summary = {}
    overlay._set_settlement_button_state = lambda data: None  # type: ignore[method-assign]
    overlay.render = lambda data: rendered.append(data)  # type: ignore[method-assign]

    assert module.AhmadTkOverlay.toggle_settlement_values(overlay) == "break"
    assert overlay.settlement_values_hidden is True
    assert rendered == []

    overlay._last_summary = settled
    rendered.clear()
    assert module.AhmadTkOverlay.toggle_settlement_values(overlay) == "break"
    assert overlay.settlement_values_hidden is False
    assert rendered == [settled]


def test_ahmad_hover_copy_keeps_settlement_and_bilibili_clear() -> None:
    module = _ahmad_overlay_module()

    assert "http" not in module.BILIBILI_TIP_TEXT.lower()
    assert "Bilibili" in module.BILIBILI_TIP_TEXT
    assert "只影响界面，不影响计算" in module.SETTLEMENT_HIDE_TIP
    assert "只影响界面，不影响计算" in module.SETTLEMENT_SHOW_TIP
    assert "不删除 live 日志" in module.MANUAL_CLEAR_SETTLEMENT_TIP

    class Button:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.settlement_button = Button()
    overlay.settlement_tip = Tip()
    overlay.settlement_values_hidden = False
    settled = {"context": {"phase": "settled"}, "truth": {"available": True}}
    module.AhmadTkOverlay._set_settlement_button_state(overlay, settled)

    assert overlay.settlement_button.kwargs["text"] == "藏价"
    assert overlay.settlement_tip.text == module.SETTLEMENT_HIDE_TIP

    overlay.settlement_values_hidden = True
    module.AhmadTkOverlay._set_settlement_button_state(overlay, settled)
    assert overlay.settlement_button.kwargs["text"] == "显价"
    assert overlay.settlement_tip.text == module.SETTLEMENT_SHOW_TIP

    module.AhmadTkOverlay._set_settlement_button_state(overlay, {"context": {"phase": "bidding"}})
    assert overlay.settlement_button.kwargs["text"] == "显价"
    assert overlay.settlement_tip.text == module.SETTLEMENT_SHOW_TIP

    overlay.settlement_values_hidden = False
    module.AhmadTkOverlay._set_settlement_button_state(overlay, {"context": {"phase": "bidding"}})
    assert overlay.settlement_button.kwargs["text"] == "藏价"
    assert overlay.settlement_tip.text == module.SETTLEMENT_HIDE_TIP


def test_ahmad_manual_quality_cells_do_not_require_quality_count() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2401"),
        "total_count": Entry("39"),
        "q1_cells": Entry("16"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["total_count"] == 39
    assert ref_inputs["quality_cells"] == {"q1": 16}
    assert ref_inputs["fixed_counts"] == {}
    assert ref_inputs["avg_cells"] == {}


def test_ahmad_manual_total_cells_without_total_count() -> None:
    """断网备用: 只填总格(不填总件)应成功——引擎锁总格, overlay 推红格(总格−已知品质格)。
    群友 0710 反馈"艾莎应该可以只填总格能把红格算出来"; 修前 6519 强制总件把这拦了。"""
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("aisha"),
        "map_id": Entry("2405"),
        "total_cells": Entry("90"),  # 只填总格, 不填总件
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["total_count"] is None
    assert ref_inputs["total_cells"] == 90


def test_ahmad_manual_requires_at_least_one_numeric_input() -> None:
    """只填英雄/地图、无任何数值输入 → 拒绝(总件放开后的兜底, 防全空手填)。"""
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("aisha"),
        "map_id": Entry("2405"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "至少" in error


def test_apply_manual_inputs_gated_when_trial_expired_does_not_compute() -> None:
    """降级态(试用到期/未激活)点「应用并启用」必须不计算、不渲染、不进手动态。
    否则 _start_manual_summary_worker 降级返回 False 会落到 inline _manual_result_summary
    跑引擎并 render, 绕过 refresh 的降级 gate = 到期后门。"""
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._act_state = {
        "run_full": False,
        "text": "试用已结束 · 需激活 token",
    }
    overlay._manual_active = False
    overlay.manual_status = Widget()
    calls: list[str] = []
    overlay._manual_inputs_snapshot = lambda: (calls.append("snapshot") or (None, ""))  # type: ignore[method-assign,assignment]
    overlay._start_manual_summary_worker = lambda **_kw: calls.append("worker") or True  # type: ignore[method-assign,assignment]
    overlay._manual_result_summary = lambda _snap: calls.append("compute") or {}  # type: ignore[method-assign,assignment]
    overlay.render = lambda _summary: calls.append("render")  # type: ignore[method-assign,assignment]

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    # 关键: 引擎不跑、界面不渲染、连输入校验都不进(短路在最前面)。
    assert calls == []
    assert overlay._manual_active is False
    assert "试用已结束" in overlay.manual_status.kwargs.get("text", "")


def test_apply_manual_inputs_not_gated_when_active_proceeds_to_validation() -> None:
    """正常态(run_full=True)必须放行到输入校验, 确认降级闸没误伤正常手填。"""
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def configure(self, **kwargs: Any) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = True
    overlay._act_state = {"run_full": True}
    overlay.manual_status = Widget()
    reached: list[str] = []
    # 校验直接返回错误即可 — 只验证降级闸放行到了这一步。
    overlay._manual_inputs_snapshot = lambda: (reached.append("validate") or (None, "需要填写总件"))  # type: ignore[method-assign,assignment]

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    assert reached == ["validate"]
    assert overlay.manual_status.kwargs.get("text") == "需要填写总件"


def test_ahmad_manual_integer_total_avg_resolves_to_grid() -> None:
    """Integer 全均格 like 2.0/3.0 map to an exact grid (36/54 for count 18) and
    must not be rejected as 无法对应整数总格 (trailing-zero string-match bug)."""
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    # Pure helper: integer averages resolve to a unique grid.
    assert module._manual_avg_grid_options_from_text(18, 2.0, "2.0") == [36]
    assert module._manual_avg_grid_options_from_text(18, 3.0, "3.0") == [54]
    # Through the full manual snapshot build (count + integer 全均格).
    for avg_text, expected_cells in (("2.0", 36), ("3.0", 54), ("2.5", 45)):
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay.manual_entries = {
            "hero": Entry("gabriela"),
            "map_id": Entry("2409"),
            "total_count": Entry("18"),
            "total_avg": Entry(avg_text),
        }
        snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)
        assert error == "", f"avg={avg_text} should not error, got {error!r}"
        assert snapshot is not None
        assert snapshot["structured_ref_inputs"]["total_cells"] == expected_cells


def test_ahmad_manual_values_from_live_summary_keep_victor_zero_and_total_avg() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "victor", "map_id": 2404},
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "total_grid_target": 34.00000476837158,
                "avg_cells": {
                    "q1": 1.1428571428571428,
                    "q3": 2.0,
                    "q4": 1.8,
                    "q5": 0,
                    "q6": 1.0,
                },
                "fixed_counts": {"q1": 7, "q3": 8, "q4": 5, "q5": 0, "q6": 1},
                "count_sums": {"q4q5q6": 6},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "victor"
    assert values["map_id"] == 2404
    assert values["total_count"] == 21
    assert values["total_cells"] == "34"
    assert values["total_avg"] == "1.619"
    assert values["q1_avg"] == "1.1429"
    assert values["q1_count"] == 7
    assert values["q1_cells"] == "8"
    assert values["q3_avg"] == "2"
    assert values["q3_count"] == 8
    assert values["q3_cells"] == "16"
    assert values["q4_avg"] == "1.8"
    assert values["q4_count"] == 5
    assert values["q4_cells"] == "9"
    assert values["q5_avg"] == "0"
    assert values["q5_count"] == 0
    assert values["q5_cells"] == "0"
    assert values["q6_avg"] == "1"
    assert values["q6_count"] == 1
    assert values["q6_cells"] == "1"
    assert values["q4q5_count"] == 6


def test_ahmad_manual_values_from_live_summary_prefills_quality_value_fields() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "ahmed", "map_id": 2401},
        "ahmed_ref": {
            "quality_count_ranges": {
                "q4": [8, 8, 8],
                "q5": [4, 4, 4],
            },
            "evidence": {
                "hero": "ahmed",
                "map_id": 2401,
                "total_count": 30,
                "avg_values": {"q4": 6328.75, "q5": 34288.75},
                "quality_values": {"q4": 50630, "q5": 137155},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["q4_avg_value"] == "6328.75"
    assert values["q4_value_sum"] == "50630"
    assert values["q4_count"] == 8
    assert values["q5_avg_value"] == "34288.75"
    assert values["q5_value_sum"] == "137155"
    assert values["q5_count"] == 4


def test_ahmad_manual_values_do_not_promote_estimated_total_grid_to_input() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "ahmed", "map_id": 2405},
        "ahmed_ref": {
            "total_grid_range": [70, 82, 94],
            "evidence": {
                "hero": "ahmed",
                "map_id": 2405,
                "total_count": 33,
            },
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "ahmed"
    assert values["map_id"] == 2405
    assert values["total_count"] == 33
    assert "total_cells" not in values
    assert "total_avg" not in values


def test_ahmad_manual_values_from_live_summary_derives_counts_from_avg_and_cells() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "维克托", "map_id": 2404},
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "avg_cells": {
                    "q1": 1.1428571428571428,
                    "q3": 2.0,
                    "q5": 6.0,
                },
                "quality_cells": {
                    "q1": 8,
                    "q3": 16,
                    "q5": 24,
                },
                "fixed_counts": {},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "维克托"
    assert values["q1_count"] == 7
    assert values["q3_count"] == 8
    assert values["q5_count"] == 4
    assert values["q1_cells"] == "8"
    assert values["q3_cells"] == "16"
    assert values["q5_cells"] == "24"


def test_ahmad_manual_values_from_live_summary_does_not_fill_lower_bounds_as_exact_counts() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "victor", "map_id": 2404},
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "min_counts": {"q4": 2, "q5": 1},
                "fixed_counts": {},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "victor"
    assert "q4_count" not in values
    assert "q5_count" not in values


def test_ahmad_manual_values_from_live_summary_does_not_fill_split_lower_bound_as_exact_q1() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "aisha", "map_id": 2404},
        "ahmed_ref": {
            "evidence": {
                "hero": "aisha",
                "map_id": 2404,
                "total_count": 21,
                "min_counts": {"q1": 3},
                "fixed_counts": {},
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
                "split_avg_cells": {"white": 5 / 3},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "aisha"
    assert values["white_count"] == 3
    assert values["white_cells"] == "5"
    assert "green_count" not in values
    assert "q1_count" not in values
    assert "q1_cells" not in values


def test_ahmad_manual_values_from_live_summary_prefills_aisha_split_low_quality() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "aisha", "map_id": 2401},
        "ahmed_ref": {
            "evidence": {
                "hero": "aisha",
                "map_id": 2401,
                "total_count": 33,
                "split_avg_cells": {"white": 1.6666666667, "green": 2.0},
                "split_quality_cells": {"white": 5, "green": 8},
                "split_counts": {"white": 3, "green": 4},
                "fixed_counts": {"q1": 7},
                "quality_cells": {"q1": 13},
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "aisha"
    assert values["white_avg"] == "1.6667"
    assert values["white_count"] == 3
    assert values["white_cells"] == "5"
    assert values["green_avg"] == "2"
    assert values["green_count"] == 4
    assert values["green_cells"] == "8"
    assert values["q1_count"] == 7
    assert values["q1_cells"] == "13"


def test_ahmad_manual_values_from_live_summary_prefers_supported_evidence_hero() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    data = {
        "context": {"hero": "?", "map_id": 2404},
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
            }
        },
    }

    values = module.AhmadTkOverlay._manual_values_from_summary(overlay, data)

    assert values["hero"] == "victor"


def test_ahmad_server_summary_prefers_ref_evidence_hero_when_context_unknown(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "?",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "2404:test",
            }
        },
        "structured_ref_inputs": {
            "hero": "victor",
            "total_count": 6,
            "count_sums": {"q4q5q6": 2},
            "fixed_counts": {"q4": 1, "q5": 0, "q6": 1},
            "avg_cells": {"q4": 1.0, "q5": 0.0, "q6": 3.0},
            "quality_cells": {"q4": 1, "q5": 0, "q6": 3},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["context"]["hero"] == "victor"
    assert result["context"]["is_supported_ref_hero"] is True


def test_ahmad_server_summary_locks_zero_gold_avg_in_mini(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "2404:zero-gold",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
        "structured_ref_inputs": {
            "hero": "victor",
            "total_count": 21,
            "total_cells": 34,
            "avg_cells": {"q4": 1.8, "q5": 0.0},
            "count_sums": {"q4q5q6": 6},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["status"] == "ok"
    ref = result["ahmed_ref"]
    assert ref["evidence"]["fixed_counts"]["q5"] == 0
    assert ref["quality_count_ranges"]["q5"] == [0, 0, 0]
    assert ref["quality_cells_ranges"]["q5"] == [0, 0, 0]
    assert result["red"]["quality_count_summary"] == "紫5件/9格 · 金0件/0格"
    assert "金0件" in result["red"]["quality_count_summary"]


def test_ahmad_server_summary_pairs_red_candidates_with_gold_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return {
                "status": "ok",
                "source": "ref_v0",
                "notes": [],
                "conservative": 100000,
                "balanced": 120000,
                "aggressive": 140000,
                "red_count_range": [2, 3, 4],
                "red_cells_range": [4, 6, 8],
                "red_value_range": [30000, 40000, 50000],
                "total_grid_range": [30, 30, 30],
                "quality_count_ranges": {
                    "q1": [7, 7, 7],
                    "q3": [4, 4, 4],
                    "q4": [5, 5, 5],
                    "q5": [2, 3, 4],
                    "q6": [2, 3, 4],
                },
                "quality_cells_ranges": {
                    "q1": [5, 5, 5],
                    "q3": [6, 6, 6],
                    "q4": [7, 7, 7],
                    "q5": [4, 6, 8],
                    "q6": [4, 6, 8],
                },
                "evidence": {
                    "hero": "victor",
                    "total_count": 20,
                    "count_sums": {"q4q5q6": 11},
                    "total_grid_target": 30,
                },
            }

    monkeypatch.setattr(module, "run_reference_engine", lambda snapshot, **kwargs: FakeRefResult())
    monkeypatch.setattr(module, "hero_should_run_scheduled_inference", lambda *args, **kwargs: False)
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "2404:red-pair",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
        "structured_ref_inputs": {"hero": "victor", "total_count": 20},
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["red"]["quality_count_summary"] == "紫5件/7格 · 金件 2/3/4 4/6/8格"
    assert result["red"]["count_range"] == "2 / 3 / 4"
    assert result["red"]["cells_range"] == "4 / 6 / 8"


def test_ahmad_server_red_display_keeps_policy_order_even_if_legacy_env_is_set(
    monkeypatch,
) -> None:
    module = _ahmad_server_module()
    result = {
        "red_count_range": [0, 1, 3],
        "red_cells_range": [0, 4, 9],
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [9, 9, 9],
            "q4": [12, 12, 12],
            "q5": [1, 3, 4],
            "q6": [0, 1, 3],
        },
        "evidence": {
            "total_count": 35,
            "count_sums": {"q4q5q6": 16},
        },
    }

    for legacy_value in (None, "0", "1"):
        if legacy_value is None:
            monkeypatch.delenv("BIDKING_ENABLE_INVERSE_GOLD_RED_DISPLAY", raising=False)
        else:
            monkeypatch.setenv("BIDKING_ENABLE_INVERSE_GOLD_RED_DISPLAY", legacy_value)
        red_count_range, red_cells_range = module._red_display_ranges(result)  # type: ignore[attr-defined]
        red_value_range = module._red_display_value_range(  # type: ignore[attr-defined]
            {**result, "red_value_range": [100, 200, 300]},
            red_count_range,
        )

        assert red_count_range == [0, 1, 3]
        assert red_cells_range == [0, 4, 9]
        assert red_value_range == [100, 200, 300]


def test_ahmad_server_summary_keeps_locked_red_triplet_with_minimap_floor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return {
                "status": "ok",
                "source": "ref_v0",
                "notes": [],
                "conservative": 648829,
                "balanced": 703823,
                "aggressive": 758817,
                "red_count_range": [3, 3, 3],
                "red_cells_range": [9, 10, 11],
                "red_value_range": [368557, 485120, 595000],
                "quality_count_ranges": {
                    "q4": [12, 12, 12],
                    "q5": [5, 5, 5],
                    "q6": [3, 3, 3],
                },
                "evidence": {
                    "hero": "victor",
                    "total_count": 47,
                    "count_sums": {"q4q5q6": 20},
                },
            }

    monkeypatch.setattr(module, "run_reference_engine", lambda snapshot, **kwargs: FakeRefResult())
    monkeypatch.setattr(module, "hero_should_run_scheduled_inference", lambda *args, **kwargs: False)
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "2404:locked-red-floor",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"summary": {}, "counts": {}, "public_info": {}},
            "minimap": {
                "status": "available",
                "columns": 10,
                "items": [
                    {
                        "row": 0,
                        "col": 0,
                        "quality": 6,
                        "render_mode": "footprint",
                        "width": 3,
                        "height": 3,
                        "cells": 9,
                    },
                    {
                        "row": 0,
                        "col": 4,
                        "quality": 6,
                        "render_mode": "footprint",
                        "width": 1,
                        "height": 1,
                        "cells": 1,
                    },
                    {
                        "row": 1,
                        "col": 4,
                        "quality": 6,
                        "render_mode": "footprint",
                        "width": 1,
                        "height": 1,
                        "cells": 1,
                    },
                ],
            },
        },
        "structured_ref_inputs": {"hero": "victor", "total_count": 47},
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["red"]["count_range"] == "3 / 3 / 3"
    assert result["red"]["cells_range"] == "11 / 11 / 11"


def test_ahmad_server_summary_keeps_public_info_marker_soft(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2408,
                "phase": "bidding",
                "session_id": "2408:test",
            },
            "minimap": {
                "status": "available",
                "columns": 10,
                "viewport_rows": 13,
                "items": [
                    {
                        "row": 3,
                        "col": 4,
                        "width": 2,
                        "height": 2,
                        "quality": "q6",
                        "source": "public_info",
                        "layout_source": "public_info",
                        "render_mode": "marker",
                        "shape_key": "22",
                    }
                ],
            },
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["minimap"]["items"][0]["render_mode"] == "marker"
    assert result["minimap"]["items"][0]["source"] == "public_info"
    assert result["minimap"]["items"][0]["shape_key"] == "22"
    assert result["minimap"]["items"][0]["label"] == "红品≥1"
    assert "红品≥1" in result["minimap"]["items"][0]["tooltip"]
    assert "轮廓 2x2/4格" in result["minimap"]["items"][0]["tooltip"]
    assert "公共抽检" in result["minimap"]["items"][0]["tooltip"]
    assert "item" not in result["minimap"]["items"][0]["tooltip"].lower()


def test_ahmad_server_summary_labels_marker_only_live_known_count() -> None:
    module = _ahmad_server_module()
    runtime_id = 1741282964991045
    summary = module._minimap_summary(
        {"phase": "bidding"},
        {
            "context": {"phase": "bidding"},
            "minimap": {
                "status": "available",
                "layout_source": "live_known",
                "known_items": 14,
                "drawable_items": 0,
                "quality_counts": {"q4": 6, "q6": 3},
                "items": [
                    {
                        "row": 1,
                        "col": 1,
                        "width": 1,
                        "height": 1,
                        "quality": 6,
                        "runtime_id": runtime_id,
                        "local_index": 0,
                        "source": "public_info",
                        "render_mode": "marker",
                    }
                ],
            },
        },
    )

    assert summary["summary_text"] == "14 个标记 · live_known"
    assert summary["known_items"] == 14
    assert summary["drawable_items"] == 0
    assert summary["items"][0]["runtime_id"] == runtime_id


def test_ahmad_server_summary_keeps_public_info_item_name(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2408,
                "phase": "bidding",
                "session_id": "2408:test",
            },
            "minimap": {
                "status": "available",
                "columns": 10,
                "viewport_rows": 13,
                "items": [
                    {
                        "row": 3,
                        "col": 4,
                        "width": 2,
                        "height": 2,
                        "quality": "q6",
                        "item_id": 1001,
                        "item_name": "民用垂直起降飞行器",
                        "source": "public_info",
                        "layout_source": "public_info",
                        "render_mode": "marker",
                        "shape_key": "22",
                    }
                ],
            },
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    item = result["minimap"]["items"][0]

    assert item["label"] == "民用垂直起降飞行器"
    assert "民用垂直起降飞行器" in item["tooltip"]
    assert "红品" in item["tooltip"]
    assert "红品≥1" not in item["tooltip"]


def test_ahmad_server_summary_keeps_old_settlement_review_until_next_session(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot = {
        "created_at": time.time() - 600,
        "phase": "settled",
        "inventory_count": 21,
        "inventory_cells": 34,
        "settlement_incomplete": False,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q1=20;q6=1",
        "final_quality_cells": "q1=31;q6=3",
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2404,
                "phase": "settled",
                "session_id": "2404:done",
            },
            "baseline": {
                "decision": {"attack_bid": "450000"},
                "posterior": {},
            },
            "source": {"created_at": time.time() - 600},
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "captured_items": 21,
                "captured_cells": 34,
                "total_value": 520000,
                "total_items": 21,
                "total_cells": 34,
                "q6": {"count": 1, "cells": 3, "value": 160000},
                "top_item": {"name": "test", "value": 160000},
            },
        },
        "structured_ref_inputs": {
            "hero": "victor",
            "total_count": 21,
            "total_cells": 34,
            "avg_cells": {"q4": 1.8, "q5": 0.0},
            "count_sums": {"q4q5q6": 6},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert result["status"] == "ok"
    assert result["reference"]["source"] == "settlement"
    assert result["reference"]["conservative"] != "450,000"
    assert result["reference"]["balanced"] == "520,000"
    assert result["reference"]["aggressive"].startswith("+")
    assert "pre-settlement replay estimate" in result["reference"]["note"]
    assert result["truth"]["available"] is True

    truth_first = module.summarize_snapshot(
        snapshot,
        snapshot_path=snapshot_path,
        defer_settlement_estimate=True,
    )
    assert truth_first["reference"]["conservative"] == "更新中"
    assert truth_first["reference"]["balanced"] == "520,000"
    assert truth_first["reference"]["aggressive"] == "-"
    assert truth_first["truth"]["available"] is True
    assert (
        truth_first["diagnostics"]["performance"]["settlement_ref_engine_ms"]
        is None
    )
    assert (
        truth_first["diagnostics"]["performance"]["settlement_estimate_state"]
        == "pending"
    )


def test_ahmad_server_summary_settlement_truth_overrides_stale_live_actions(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot = {
        "created_at": time.time(),
        "phase": "settled",
        "inventory_count": 14,
        "inventory_cells": 43,
        "settlement_incomplete": False,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q2=3;q3=2;q4=4;q5=2;q6=3",
        "final_quality_cells": "q2=6;q3=10;q4=12;q5=8;q6=7",
        "structured_ref_inputs": {
            "hero": "ahmed",
            "total_count": 21,
            "fixed_counts": {"q1": 3, "q3": 9, "q5": 2},
            "quality_cells": {"q1": 25},
            "avg_cells": {"q1": 1.6666666269302368},
        },
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4521,
                "phase": "settled",
                "session_id": "4521:settled",
            },
            "baseline": {
                "decision": {"attack_bid": "450000"},
                "posterior": {},
            },
            "actions": {
                "results": [
                    {"action_id": 100117, "result": 9},
                    {"action_id": 100104, "result": 25},
                    {"action_id": 100110, "result": 1.6666666269302368},
                ]
            },
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "captured_items": 14,
                "captured_cells": 43,
                "total_value": 867739,
                "total_items": 14,
                "total_cells": 43,
                "q6": {"count": 3, "cells": 7, "value": 420000},
                "top_item": {"name": "test", "value": 250000},
            },
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    ref_evidence = result["ahmed_ref"]["evidence"]

    assert result["status"] == "ok"
    assert result["reference"]["source"] == "settlement"
    assert result["reference"]["balanced"] == "867,739"
    assert result["truth"]["total_items"] == 14
    assert result["truth"]["total_cells"] == 43
    assert result["ahmed_ref"]["status"] == "ok"
    assert result["ahmed_ref"]["combo_count"] == 1
    assert ref_evidence["total_count"] == 14
    assert ref_evidence["total_grid_target"] == 43.0
    assert ref_evidence["fixed_counts"] == {"q1": 3, "q3": 2, "q4": 4, "q5": 2, "q6": 3}
    assert ref_evidence["quality_cells"] == {"q1": 6.0, "q3": 10.0, "q4": 12.0, "q5": 8.0, "q6": 7.0}


def test_ahmad_ref_input_summary_shows_quality_lower_bounds() -> None:
    module = _ahmad_server_module()
    summary = module._ref_input_summary(  # type: ignore[attr-defined]
        {
            "evidence": {
                "total_count": 21,
                "fixed_counts": {"q4": 7},
                "min_counts": {"q1": 4, "q3": 2, "q4": 7, "q5": 1},
            }
        }
    )

    assert "下界 白绿≥4，蓝≥2，金≥1" in summary
    assert "紫≥7" not in summary


def test_ahmad_ref_input_summary_shows_estimated_total_grid_without_hard_target() -> None:
    module = _ahmad_server_module()
    summary = module._ref_input_summary(  # type: ignore[attr-defined]
        {
            "total_grid_range": [70, 82, 94],
            "evidence": {
                "total_count": 33,
            },
        }
    )

    assert "总件 33" in summary
    assert "估总格 70/82/94格" in summary
    assert "总格 82" not in summary


def test_ahmad_ref_input_summary_uses_compact_exact_avg_display() -> None:
    module = _ahmad_server_module()
    summary = module._ref_input_summary(  # type: ignore[attr-defined]
        {
            "evidence": {
                "total_count": 55,
                "avg_cells": {
                    "q1": 1.8095238208770752,
                    "q4": 2.9000000953674316,
                },
            }
        }
    )

    assert "白绿均格 1.8095" in summary
    assert "紫均格 2.9" in summary
    assert "白绿均格 1.81" not in summary
    assert "紫均格 2.90" not in summary


def test_ahmad_ref_input_summary_prioritizes_counts_and_cells_before_avg() -> None:
    module = _ahmad_server_module()
    summary = module._ref_input_summary(  # type: ignore[attr-defined]
        {
            "evidence": {
                "total_count": 38,
                "total_grid_target": 83.00000476837158,
                "avg_cells": {"q1": 1.8, "q3": 1.5556, "q4": 4.125},
                "quality_cells": {"q1": 27, "q3": 14, "q4": 33},
                "fixed_counts": {"q1": 15, "q3": 9, "q4": 8},
            }
        }
    )

    assert summary.startswith("总件 38 · 总格 83 · 件数 白绿件 15")
    assert summary.find("格数 白绿格 27") < summary.find("白绿均格 1.8")
    assert summary.find("紫均格 4.125") > summary.find("格数 白绿格 27")


def test_ahmad_manual_input_summary_prioritizes_counts_and_cells_before_avg() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    summary = module.AhmadTkOverlay._manual_input_summary(
        overlay,
        {
            "total_count": 38,
            "total_grid_target": 83.00000476837158,
            "avg_cells": {"q1": 1.8, "q3": 1.5556, "q4": 4.125},
            "quality_cells": {"q1": 27, "q3": 14, "q4": 33},
            "fixed_counts": {"q1": 15, "q3": 9, "q4": 8},
        },
    )

    assert summary.startswith("总件 38 · 总格 83 · 件数 白绿件 15")
    assert summary.find("格数 白绿格 27") < summary.find("白绿均格 1.8")
    assert summary.find("紫均格 4.125") > summary.find("格数 白绿格 27")


def test_ahmad_tk_summary_label_splits_long_input_summary() -> None:
    module = _ahmad_overlay_module()

    class Label:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    label = Label()
    overlay = object.__new__(module.AhmadTkOverlay)

    module.AhmadTkOverlay._set_summary_label(
        overlay,
        label,
        (
            "总件 38 · 总格 83.0 · 全均格 2.1842 · 白绿均格 1.8 · "
            "蓝均格 1.5556 · 紫均格 4.125 · 金均格 1.75 · 红均格 1 · "
            "格数 白绿格 27.0，蓝格 14.0，紫格 33.0，金格 7.0，红格 2.0"
        ),
        line_limit=42,
    )

    assert "\n" in label.kwargs["text"]
    assert label.kwargs["height"] == 2
    assert label.kwargs["justify"] == "right"
    assert "总件 38" in label.kwargs["text"]
    assert "白绿均格 1.8" in label.kwargs["text"]


def test_ahmad_ref_input_summary_shows_aisha_split_low_quality() -> None:
    module = _ahmad_server_module()
    summary = module._ref_input_summary(  # type: ignore[attr-defined]
        {
            "evidence": {
                "total_count": 33,
                "split_avg_cells": {"white": 1.6666666667, "green": 2.0},
                "split_quality_cells": {"white": 5, "green": 8},
                "split_counts": {"white": 3, "green": 4},
                "fixed_counts": {"q1": 7},
                "quality_cells": {"q1": 13},
            }
        }
    )

    assert "白均格 1.6667" in summary
    assert "绿均格 2" in summary
    assert "分格 白格 5，绿格 8" in summary
    assert "分件 白件 3，绿件 4" in summary
    assert "件数 白绿件 7" in summary


def _canonical_low_quality_composition(
    module,
    *,
    q1_rows: list[dict[str, object]],
    q3_rows: list[dict[str, object]],
    q1_strength: str,
    q3_strength: str,
) -> dict[str, object]:
    return module._quality_composition_payload(  # type: ignore[attr-defined]
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q1": q1_rows,
                "q3": q3_rows,
            },
            "quality_lock": {
                "q1": {"strength": q1_strength, "reason": "joint"},
                "q3": {"strength": q3_strength, "reason": "joint"},
            },
        }
    )


def test_ahmad_quality_uncertainty_summary_keeps_locked_and_unlocked_counts() -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[
            {"count": 10, "cells": 20, "prob": 0.4},
            {"count": 15, "cells": 30, "prob": 0.6},
        ],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_strength="probabilistic",
        q3_strength="exact",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [10, 15, 15],
                "q3": [13, 13, 13],
                "q4": [9, 9, 9],
                "q5": [4, 4, 8],
                "q6": [0, 3, 5],
            }
        },
        quality_composition=quality_composition,
    )

    assert summary == "已锁 蓝13件/18格 · 未锁 白绿10/15/15"
    assert "蓝13" in summary
    assert "金4" not in summary
    assert "红0" not in summary


def _summarize_low_quality_joint(
    module,
    monkeypatch,
    tmp_path: Path,
    *,
    q1_rows: list[dict[str, object]],
    q3_rows: list[dict[str, object]],
    q1_range: list[int] | None = None,
    q3_range: list[int] | None = None,
    q1_strength: str = "probabilistic",
    q3_strength: str = "probabilistic",
) -> dict[str, object]:
    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return {
                "status": "ok",
                "source": "ref_v0",
                "notes": [],
                "conservative": 100_000,
                "balanced": 120_000,
                "aggressive": 140_000,
                "quality_count_ranges": {
                    "q1": q1_range or [10, 15, 15],
                    "q3": q3_range or [13, 13, 13],
                },
                "quality_cells_ranges": {
                    "q1": [20, 30, 30],
                    "q3": [18, 18, 20],
                },
                "quality_count_cells_candidates": {
                    "q1": q1_rows,
                    "q3": q3_rows,
                },
                "quality_lock": {
                    "q1": {"strength": q1_strength, "reason": "joint"},
                    "q3": {"strength": q3_strength, "reason": "joint"},
                },
                "evidence": {"hero": "victor", "total_count": 35},
            }

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda _snapshot, **_kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *_args, **_kwargs: False,
    )
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2404,
                "phase": "bidding",
                "session_id": "2404:collapsed-blue-multi-joint",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
        "structured_ref_inputs": {"hero": "victor", "total_count": 35},
    }

    return module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )


@pytest.mark.parametrize("reverse", (False, True))
def test_ahmad_summary_never_calls_collapsed_multi_joint_blue_locked(
    monkeypatch,
    tmp_path: Path,
    reverse: bool,
) -> None:
    module = _ahmad_server_module()
    q1_rows = [
        {"count": 10, "cells": 20, "prob": 0.4},
        {"count": 15, "cells": 30, "prob": 0.6},
    ]
    q3_rows = [
        {"count": 13, "cells": 18, "prob": 0.6},
        {"count": 14, "cells": 20, "prob": 0.4},
    ]
    if reverse:
        q1_rows.reverse()
        q3_rows.reverse()
    result = _summarize_low_quality_joint(
        module,
        monkeypatch,
        tmp_path,
        q1_rows=q1_rows,
        q3_rows=q3_rows,
    )

    assert result["quality_composition"]["qualities"]["q3"]["status"] == "ready"
    assert result["quality_composition"]["qualities"]["q3"]["lock_strength"] == "probabilistic"
    assert "已锁 蓝13件" not in result["red"]["uncertainty_summary"]
    assert "未锁" in result["red"]["uncertainty_summary"]


@pytest.mark.parametrize(
    ("q1_exact", "q3_exact"),
    ((True, True), (True, False), (False, True), (False, False)),
)
def test_ahmad_quality_summary_uses_canonical_q1_q3_lock_matrix(
    q1_exact: bool,
    q3_exact: bool,
) -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=(
            [{"count": 10, "cells": 20, "prob": 1.0}]
            if q1_exact
            else [
                {"count": 10, "cells": 20, "prob": 0.6},
                {"count": 15, "cells": 30, "prob": 0.4},
            ]
        ),
        q3_rows=(
            [{"count": 13, "cells": 18, "prob": 1.0}]
            if q3_exact
            else [
                {"count": 13, "cells": 18, "prob": 0.6},
                {"count": 14, "cells": 20, "prob": 0.4},
            ]
        ),
        q1_strength="exact" if q1_exact else "probabilistic",
        q3_strength="exact" if q3_exact else "probabilistic",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [10, 10, 15],
                "q3": [13, 13, 13],
            }
        },
        quality_composition=quality_composition,
    )

    assert ("已锁 白绿10件/20格" in summary) is q1_exact
    assert ("蓝13件/18格" in summary) is q3_exact
    if not q1_exact:
        assert "白绿10/10/15" in summary
    if not q3_exact:
        assert "蓝13" in summary


def _quality_state_payload(
    *,
    status: object = "exact",
    shown_probability: object = 1.0,
    other_probability: object = 0.0,
    truncated: object = False,
    cap_hit: object = False,
) -> dict[str, object]:
    return {
        "status": "ready",
        "reason": "test",
        "qualities": {
            "q3": {
                "status": status,
                "source": "pricing_posterior",
                "lock_strength": "exact",
                "lock_reason": "joint",
                "candidates": [
                    {"count": 13, "cells": 18, "probability": shown_probability}
                ],
                "shown_probability": shown_probability,
                "other_probability": other_probability,
                "truncated": truncated,
                "cap_hit": cap_hit,
                "pricing_posterior": True,
                "display_reconciled": False,
            }
        },
    }


@pytest.mark.parametrize(
    "quality_composition",
    [
        None,
        {"status": "conflict", "reason": "conflict", "qualities": {}},
        _quality_state_payload(status="stale"),
        _quality_state_payload(status="loading"),
        _quality_state_payload(truncated=True),
        _quality_state_payload(cap_hit=True),
        _quality_state_payload(
            shown_probability=0.9,
            other_probability=0.1,
            truncated=True,
        ),
    ],
)
def test_ahmad_quality_summary_non_strict_states_never_lock_blue(
    quality_composition: object,
) -> None:
    module = _ahmad_server_module()
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {"quality_count_ranges": {"q3": [13, 13, 13]}},
        quality_composition=quality_composition,
    )

    assert "已锁 蓝" not in summary
    assert summary == "未锁 蓝13"


def test_ahmad_quality_summary_malformed_typed_status_is_no_magic_fail_closed() -> None:
    module = _ahmad_server_module()
    magic_calls: list[str] = []

    class BombStr(str):
        def __bool__(self) -> bool:
            magic_calls.append("bool")
            raise AssertionError("must not call bool")

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            raise AssertionError("must not compare")

        def __str__(self) -> str:
            magic_calls.append("str")
            raise AssertionError("must not stringify")

    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {"quality_count_ranges": {"q3": [13, 13, 13]}},
        quality_composition=_quality_state_payload(status=BombStr("exact")),
    )

    assert summary == "未锁 蓝13"
    assert magic_calls == []


@pytest.mark.parametrize("blue_exact", (False, True))
def test_ahmad_aisha_r1_only_calls_canonical_blue_locked(
    blue_exact: bool,
) -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[
            {"count": 7, "cells": 13, "prob": 0.5},
            {"count": 8, "cells": 15, "prob": 0.5},
        ],
        q3_rows=(
            [{"count": 6, "cells": 12, "prob": 1.0}]
            if blue_exact
            else [
                {"count": 6, "cells": 12, "prob": 0.7},
                {"count": 7, "cells": 14, "prob": 0.3},
            ]
        ),
        q1_strength="probabilistic",
        q3_strength="exact" if blue_exact else "probabilistic",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {"q1": [7, 7, 8], "q3": [6, 6, 6]},
            "evidence": {
                "hero": "aisha",
                "split_counts": {"white": 3},
                "split_quality_cells": {"white": 5},
                "fixed_counts": {},
            },
        },
        quality_composition=quality_composition,
    )

    assert summary.startswith("已见 白品3件/5格")
    assert ("已锁 蓝6件/12格" in summary) is blue_exact
    assert ("未锁 绿、蓝" in summary) is (not blue_exact)


@pytest.mark.parametrize("white_green_exact", (False, True))
def test_ahmad_aisha_r2_only_calls_canonical_white_green_locked(
    white_green_exact: bool,
) -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=(
            [{"count": 7, "cells": 13, "prob": 1.0}]
            if white_green_exact
            else [
                {"count": 7, "cells": 13, "prob": 0.8},
                {"count": 8, "cells": 15, "prob": 0.2},
            ]
        ),
        q3_rows=[
            {"count": 6, "cells": 12, "prob": 0.7},
            {"count": 7, "cells": 14, "prob": 0.3},
        ],
        q1_strength="exact" if white_green_exact else "probabilistic",
        q3_strength="probabilistic",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {"q1": [7, 7, 7], "q3": [6, 6, 7]},
            "quality_cells_ranges": {"q1": [13, 13, 13], "q3": [12, 12, 14]},
            "evidence": {"hero": "aisha", "fixed_counts": {"q1": 7}},
        },
        quality_composition=quality_composition,
    )

    assert ("已锁 白绿7件/13格" in summary) is white_green_exact
    if not white_green_exact:
        assert summary == "未锁 白绿7 蓝6/6/7"


def test_ahmad_quality_uncertainty_summary_applies_lower_bounds() -> None:
    module = _ahmad_server_module()
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [0, 2, 2],
                "q3": [0, 1, 4],
            },
            "evidence": {"min_counts": {"q1": 1, "q3": 2}},
        },
        count_floors={"q3": 3},
    )

    assert "白绿1/2/2" in summary
    assert "蓝3/3/4" in summary


def test_ahmad_quality_uncertainty_summary_shows_locked_counts() -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[{"count": 10, "cells": 27, "prob": 1.0}],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_strength="exact",
        q3_strength="exact",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [10, 10, 10],
                "q3": [13, 13, 13],
                "q4": [9, 9, 9],
                "q5": [4, 4, 4],
                "q6": [3, 3, 3],
            }
        },
        quality_composition=quality_composition,
    )

    assert summary.startswith("已锁 ")
    assert "白绿10" in summary
    assert "蓝13" in summary
    assert "紫9" not in summary
    assert "金4" not in summary


def test_ahmad_quality_uncertainty_summary_exact_does_not_require_legacy_range() -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[{"count": 10, "cells": 27, "prob": 1.0}],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_strength="exact",
        q3_strength="exact",
    )

    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {},
        quality_composition=quality_composition,
    )

    assert summary == "已锁 白绿10件/27格 蓝13件/18格"


def test_ahmad_quality_uncertainty_summary_shows_locked_count_and_cells() -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[{"count": 10, "cells": 27, "prob": 1.0}],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_strength="exact",
        q3_strength="exact",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [10, 10, 10],
                "q3": [13, 13, 13],
            },
            "quality_cells_ranges": {
                "q1": [27, 27, 27],
                "q3": [18, 18, 18],
            },
        },
        quality_composition=quality_composition,
    )

    assert summary == "已锁 白绿10件/27格 蓝13件/18格"


def test_ahmad_quality_uncertainty_summary_uses_evidence_cells_when_range_open() -> None:
    module = _ahmad_server_module()
    quality_composition = _canonical_low_quality_composition(
        module,
        q1_rows=[{"count": 10, "cells": 27, "prob": 1.0}],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_strength="exact",
        q3_strength="exact",
    )
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [10, 10, 10],
                "q3": [13, 13, 13],
            },
            "quality_cells_ranges": {
                "q1": [20, 30, 30],
            },
            "evidence": {
                "quality_cells": {"q1": 27, "q3": 18},
            },
        },
        quality_composition=quality_composition,
    )

    assert "白绿10件/27格" in summary
    assert "蓝13件/18格" in summary


def test_ahmad_mask_player_display_name() -> None:
    module = _ahmad_server_module()
    mask = module._mask_player_display_name  # type: ignore[attr-defined]
    assert mask("张") == "张"
    assert mask("张三") == "张*三"
    assert mask("玩家A") == "玩*A"
    assert mask("欧阳娜娜") == "欧**娜"


def test_ahmad_mask_bidder_display_text() -> None:
    module = _ahmad_server_module()
    mask = module._mask_bidder_display_text  # type: ignore[attr-defined]
    assert mask("玩家A 500,000") == "玩*A 500,000"
    assert mask("总值 1,887,823") == "总值 1,887,823"
    assert mask("-") == "-"


def test_ahmad_server_summary_masks_current_highest_name(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2404,
                "phase": "bidding",
                "round": 3,
                "session_id": "2404:mask-highest",
            },
            "baseline": {
                "decision": {
                    "current_highest": "张三丰 320,000",
                    "action": "可守不抢",
                },
                "posterior": {},
            },
            "source": {"created_at": time.time()},
        },
    }
    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    assert result["reference"]["current_highest"] == "张*丰 320,000"


def test_ahmad_purple_gold_quality_summary_shows_locked_count_and_cells() -> None:
    module = _ahmad_server_module()
    summary = module._purple_gold_quality_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q4": [9, 9, 9],
                "q5": [4, 4, 4],
            },
            "quality_cells_ranges": {
                "q4": [28, 28, 28],
                "q5": [7, 7, 7],
            },
        }
    )

    assert summary == "紫9件/28格 · 金4件/7格"


def test_ahmad_purple_gold_quality_summary_keeps_unlocked_count_and_cells_range() -> None:
    module = _ahmad_server_module()
    summary = module._purple_gold_quality_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q4": [5, 5, 5],
                "q5": [2, 3, 4],
            },
            "quality_cells_ranges": {
                "q4": [7, 7, 7],
                "q5": [4, 6, 8],
            },
        }
    )

    assert summary == "紫5件/7格 · 金件 2/3/4 4/6/8格"


def test_ahmad_purple_gold_locked_count_keeps_possible_cells_range() -> None:
    module = _ahmad_server_module()
    summary = module._purple_gold_quality_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {"q4": [5, 5, 5], "q5": [3, 3, 3]},
            "quality_cells_ranges": {"q4": [7, 9, 12], "q5": [6, 9, 12]},
            "evidence": {"hero": "aisha", "quality_values": {"q4": 50_000}},
        }
    )

    assert summary == "紫5件 约7/9/12格 · 金3件 6/9/12格"


def test_chenmei_2407_gold_avg_locks_count_without_faking_cells() -> None:
    module = _ahmad_server_module()
    summary = module._purple_gold_quality_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {"q4": [11, 11, 11], "q5": [8, 8, 8]},
            "quality_cells_ranges": {"q4": [33, 33, 33], "q5": [24, 26, 29]},
            "evidence": {
                "hero": "chenmei",
                "avg_values": {"q5": 37494.125},
                "source_notes": ["public_q5_avg_value"],
            },
        }
    )

    assert summary == "紫11件/33格 · 金8件 24/26/29格"


def test_ahmad_ethan_purple_gold_range_appends_cells_with_approx_purple() -> None:
    module = _ahmad_server_module()
    ref_result = {
        "quality_count_ranges": {
            "q4": [8, 12, 15],
            "q5": [3, 4, 5],
        },
        "quality_cells_ranges": {
            "q4": [16, 24, 32],
            "q5": [6, 8, 10],
        },
        "evidence": {
            "hero": "ethan",
            # 紫格子来自估算(quality_values),无 quality_cells[q4] 扫描锁定 → 标「约」
            "quality_values": {"q4": 50000},
        },
    }
    summary = module._purple_gold_quality_summary(ref_result)  # type: ignore[attr-defined]
    # 紫:range 件 + 「约」格子范围;金(硬锁)无「约」
    assert summary == "紫件 8/12/15 约16/24/32格 · 金件 3/4/5 6/8/10格"


def test_ahmad_ethan_purple_scan_locked_cells_no_approx() -> None:
    module = _ahmad_server_module()
    ref_result = {
        "quality_count_ranges": {"q4": [8, 12, 15], "q5": [3, 4, 5]},
        "quality_cells_ranges": {"q4": [16, 24, 32], "q5": [6, 8, 10]},
        "evidence": {
            "hero": "ethan",
            # 紫被扫描锁定(quality_cells[q4]) → 不标「约」
            "quality_cells": {"q4": 24},
            "quality_values": {"q4": 50000},
        },
    }
    summary = module._purple_gold_quality_summary(ref_result)  # type: ignore[attr-defined]
    assert "约" not in summary
    assert summary == "紫件 8/12/15 16/24/32格 · 金件 3/4/5 6/8/10格"


def test_ahmad_ethan_low_quality_range_appends_cells() -> None:
    module = _ahmad_server_module()
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {
                "q1": [11, 14, 17],
                "q3": [4, 6, 6],
            },
            "quality_cells_ranges": {
                "q1": [22, 28, 34],
                "q3": [8, 12, 12],
            },
            "evidence": {"hero": "ethan"},
        }
    )
    # 低品(白绿/蓝)range 模式带格子范围,硬可锁 → 无「约」
    assert summary == "未锁 白绿11/14/17 22/28/34格 蓝4/6/6 8/12/12格"


def test_ahmad_non_ethan_aisha_purple_gold_also_shows_cells() -> None:
    module = _ahmad_server_module()
    ref_result = {
        "quality_count_ranges": {"q4": [8, 12, 15], "q5": [3, 4, 5]},
        "quality_cells_ranges": {"q4": [16, 24, 32], "q5": [6, 8, 10]},
        "evidence": {"hero": "aisha", "quality_values": {"q4": 50000}},
    }
    summary = module._purple_gold_quality_summary(ref_result)  # type: ignore[attr-defined]
    assert summary == "紫件 8/12/15 约16/24/32格 · 金件 3/4/5 6/8/10格"


def test_ahmad_tk_splits_purple_gold_into_readable_rows_without_losing_units() -> None:
    module = _ahmad_overlay_module()

    assert module.split_purple_gold_display_summary(
        "紫件 8/12/15 约16/24/32格 · 金件 3/4/5 6/8/10格"
    ) == (
        "8/12/15件 · 约16/24/32格",
        "3/4/5件 · 6/8/10格",
    )
    assert module.split_purple_gold_display_summary(
        "紫16件/57格 · 金2件/16格"
    ) == ("16件 · 57格", "2件 · 16格")
    assert module.split_purple_gold_display_summary("-") == ("-", "-")


def test_ahmad_tk_merges_action_and_next_step_without_duplicate_or_loss() -> None:
    module = _ahmad_overlay_module()

    assert module.merge_action_next_step_display(
        "估计参考", "补紫/金件数或均格"
    ) == "估计参考 · 补紫/金件数或均格"
    assert module.merge_action_next_step_display("等待新局", "等待新局") == "等待新局"
    assert module.merge_action_next_step_display("-", "等待证据") == "等待证据"
    hover = module.action_next_step_hover(
        "估计参考", "补紫/金件数或均格", "抽取道具后刷新"
    )
    assert "当前动作\n  估计参考" in hover
    assert "下一步\n  补紫/金件数或均格" in hover
    assert "道具/技能说明\n  抽取道具后刷新" in hover


def test_ahmad_quality_probability_summary_and_hover_use_joint_states() -> None:
    module = _ahmad_server_module()
    ref_result = {
        "quality_count_ranges": {"q5": [3, 3, 3], "q6": [1, 3, 3]},
        "quality_cells_ranges": {"q5": [12, 12, 12], "q6": [2, 9, 12]},
        "quality_count_cells_candidates": {
            "q5": [{"count": 3, "cells": 12, "prob": 1.0}],
            "q6": [
                {"count": 3, "cells": 9, "prob": 0.62},
                {"count": 1, "cells": 2, "prob": 0.23},
                {"count": 3, "cells": 12, "prob": 0.10},
            ],
        },
        "quality_count_candidates": {
            "q6": [{"value": 3, "prob": 0.72}, {"value": 1, "prob": 0.23}],
        },
        "quality_cells_candidates": {
            "q6": [
                {"value": 9, "prob": 0.62},
                {"value": 2, "prob": 0.23},
                {"value": 12, "prob": 0.10},
            ],
        },
        "notes": ["grid_identity_reconciled:144"],
    }

    assert module._quality_probability_summary(ref_result) == (  # type: ignore[attr-defined]
        "金3件12格100%·红3件9格62%"
    )
    hover = module._quality_probability_hover(ref_result)  # type: ignore[attr-defined]
    assert "金品：3件/12格 100%" in hover
    assert "红品：3件/9格 62%" in hover
    assert "1件/2格 23%" in hover
    assert "3件/12格 10%" in hover
    assert "其他 5%" in hover
    assert "联合概率按报价候选，格数主行已做空间对账" in hover
    assert "红件候选：3件 72% · 1件 23% · 其他 5%" == (
        module._quality_dimension_probability_hover(  # type: ignore[attr-defined]
            ref_result, "q6", "count"
        )
    )


def test_ahmad_low_quality_count_hover_keeps_rare_candidates_without_false_pairing() -> None:
    module = _ahmad_server_module()
    ref_result = {
        "quality_count_ranges": {"q1": [14, 14, 22], "q3": [9, 9, 9]},
        "quality_count_candidates": {
            "q1": [
                {"value": 14, "prob": 0.9657},
                {"value": 21, "prob": 0.0338},
                {"value": 22, "prob": 0.0004},
            ]
        },
        # Deliberately different cell support: the count hover must not fabricate count/cell pairs.
        "quality_cells_candidates": {
            "q1": [
                {"value": 20, "prob": 0.50},
                {"value": 30, "prob": 0.50},
            ]
        },
    }

    assert module._low_quality_count_probability_hover(ref_result) == (  # type: ignore[attr-defined]
        "白绿件候选：14件 96.6% · 21件 3.4% · 22件 <0.1%；蓝件候选：9件 100%"
    )


def test_ahmad_tk_probability_hover_uses_panel_candidate_details() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    red = {
        "count_range": "1 / 3 / 3",
        "cells_range": "2 / 9 / 12",
        "count_probability_hover": "红件候选：3件 72% · 1件 23% · 其他 5%",
        "cells_probability_hover": "红格候选：9格 62% · 2格 23% · 12格 10% · 其他 5%",
        "quality_probability_hover": (
            "金品：3件/12格 100%；红品：3件/9格 62% · 1件/2格 23%"
        ),
        "uncertainty_summary": "未锁 白绿14/14/21",
        "low_quality_count_probability_hover": (
            "白绿件候选：14件 96.6% · 21件 3.4%；蓝件候选：9件 100%"
        ),
    }

    count_hover = overlay._red_hover("红件", red, {})
    cells_hover = overlay._red_hover("红格", red, {})
    joint_hover = overlay._red_hover("金红概率", red, {})
    low_hover = overlay._red_hover("低品件", red, {})

    assert "红件\n  保守 1 / 中位 3 / 激进 3" in count_hover
    assert "红件候选\n  3件 72%\n  1件 23%\n  其他 5%" in count_hover
    assert "红格候选\n  9格 62%" in cells_hover
    assert "金品\n  3件/12格 100%" in joint_hover
    assert "红品\n  3件/9格 62%\n  1件/2格 23%" in joint_hover
    assert "低品件/格\n  未锁 白绿14/14/21" in low_hover
    assert "白绿件候选\n  14件 96.6%\n  21件 3.4%" in low_hover
    assert "蓝件候选\n  9件 100%" in low_hover


def test_ahmad_html_low_quality_row_binds_probability_hover() -> None:
    module = _ahmad_server_module()

    assert "$('q6Note').title = text(red.low_quality_count_probability_hover, '');" in (
        module.INDEX_HTML  # type: ignore[attr-defined]
    )


def test_ahmad_html_footer_links_bilibili_instead_of_private_github() -> None:
    module = _ahmad_server_module()
    html = module.INDEX_HTML  # type: ignore[attr-defined]

    assert 'href="https://space.bilibili.com/88048665?spm_id_from=333.1007.0.0"' in html
    assert ">Bilibili</a>" in html
    assert "github.com/SeasonCake/bidking-lab" not in html


def test_ahmad_tk_probability_hover_groups_long_content_without_dropping_copy() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    red = {
        "quality_count_summary": "紫16件/57格 · 金2件/16格",
        "purple_gold_probability_hover": "紫品：16件/57格 100%；金品：2件/16格 100%",
        "quality_probability_hover": (
            "金品：2件/16格 100%；红品：0件/0格 25.4% · 5件/15格 24.8% · "
            "5件/16格 24.8% · 5件/17格 24.8%；联合概率按报价候选"
        ),
    }

    purple_gold = overlay._red_hover("紫金件", red, {})
    joint = overlay._red_hover("金红概率", red, {})

    assert purple_gold == (
        "紫金件/格\n  紫16件/57格\n  金2件/16格\n\n"
        "紫品\n  16件/57格 100%\n\n金品\n  2件/16格 100%\n\n"
        "每档顺序：保守 / 中位 / 激进；件数与格数并列"
    )
    assert joint == (
        "金品\n  2件/16格 100%\n\n"
        "红品\n  0件/0格 25.4%\n  5件/15格 24.8%\n"
        "  5件/16格 24.8%\n  5件/17格 24.8%\n\n联合概率按报价候选"
    )


def test_ahmad_tk_probability_compaction_never_truncates_red_segment() -> None:
    module = _ahmad_overlay_module()
    full = "金2件16格100%·红0件0格25.4%"

    variants = module._red_priority_probability_variants(full)  # type: ignore[attr-defined]

    assert variants == (
        full,
        "金…·红0件0格25.4%",
        "…·红0件0格25.4%",
        "红0件0格25.4%",
    )
    assert all("红0件0格25.4%" in value for value in variants)


def test_ahmad_non_ethan_aisha_low_quality_unchanged_count_only() -> None:
    module = _ahmad_server_module()
    summary = module._quality_uncertainty_summary(  # type: ignore[attr-defined]
        {
            "quality_count_ranges": {"q1": [11, 14, 17], "q3": [4, 6, 6]},
            "quality_cells_ranges": {"q1": [22, 28, 34], "q3": [8, 12, 12]},
            "evidence": {"hero": "aisha"},
        }
    )
    assert summary == "未锁 白绿11/14/17 蓝4/6/6"
    assert "格" not in summary


def test_ahmad_manual_field_layout_preserves_input_contract() -> None:
    module = _ahmad_overlay_module()
    base_keys = [key for key, _label, _default in module.MANUAL_BASE_FIELDS]  # type: ignore[attr-defined]
    quality_keys = [
        key
        for _label, avg_key, count_key, cells_key in module.MANUAL_QUALITY_ROWS  # type: ignore[attr-defined]
        for key in (avg_key, count_key, cells_key)
    ]
    value_keys = [
        key
        for _label, avg_value_key, value_sum_key in module.MANUAL_VALUE_ROWS  # type: ignore[attr-defined]
        for key in (avg_value_key, value_sum_key)
    ]
    extra_keys = [key for key, _label, _default in module.MANUAL_EXTRA_FIELDS]  # type: ignore[attr-defined]
    keys = [*base_keys, *quality_keys, *value_keys, *extra_keys]

    assert [label for label, *_keys in module.MANUAL_QUALITY_ROWS] == [  # type: ignore[attr-defined]
        "白",
        "绿",
        "白绿",
        "蓝",
        "紫",
        "金",
        "红",
    ]
    assert [label for label, *_keys in module.MANUAL_VALUE_ROWS] == [  # type: ignore[attr-defined]
        "白",
        "绿",
        "白绿",
        "蓝",
        "紫",
        "金",
        "红",
    ]
    assert keys == [
        "hero",
        "map_id",
        "total_count",
        "total_cells",
        "total_avg",
        "white_avg",
        "white_count",
        "white_cells",
        "green_avg",
        "green_count",
        "green_cells",
        "q1_avg",
        "q1_count",
        "q1_cells",
        "q3_avg",
        "q3_count",
        "q3_cells",
        "q4_avg",
        "q4_count",
        "q4_cells",
        "q5_avg",
        "q5_count",
        "q5_cells",
        "q6_avg",
        "q6_count",
        "q6_cells",
        "white_avg_value",
        "white_value_sum",
        "green_avg_value",
        "green_value_sum",
        "q1_avg_value",
        "q1_value_sum",
        "q3_avg_value",
        "q3_value_sum",
        "q4_avg_value",
        "q4_value_sum",
        "q5_avg_value",
        "q5_value_sum",
        "q6_avg_value",
        "q6_value_sum",
        "q4q5_count",
    ]
    assert len(keys) == len(set(keys))


def test_ahmad_manual_inline_derivation_covers_all_qualities_and_totals() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "total_count": Entry("20"),
        "total_cells": Entry(""),
        "total_avg": Entry("2.5"),
        "white_avg": Entry("1.5"),
        "white_count": Entry(""),
        "white_cells": Entry("3"),
        "green_avg": Entry("2"),
        "green_count": Entry(""),
        "green_cells": Entry("4"),
        "q1_avg": Entry("2"),
        "q1_count": Entry(""),
        "q1_cells": Entry("4"),
        "q3_avg": Entry("2"),
        "q3_count": Entry(""),
        "q3_cells": Entry("6"),
        "q4_avg": Entry("1.8"),
        "q4_count": Entry(""),
        "q4_cells": Entry("9"),
        "q5_avg": Entry("6"),
        "q5_count": Entry(""),
        "q5_cells": Entry("24"),
        "q6_avg": Entry("3"),
        "q6_count": Entry(""),
        "q6_cells": Entry("6"),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["total_cells"].get() == "50"
    assert overlay.manual_entries["white_count"].get() == "2"
    assert overlay.manual_entries["green_count"].get() == "2"
    assert overlay.manual_entries["q1_count"].get() == "2"
    assert overlay.manual_entries["q3_count"].get() == "3"
    assert overlay.manual_entries["q4_count"].get() == "5"
    assert overlay.manual_entries["q5_count"].get() == "4"
    assert overlay.manual_entries["q6_count"].get() == "2"


def test_ahmad_manual_inline_derivation_covers_quality_values() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "white_count": Entry("3"),
        "white_avg_value": Entry(""),
        "white_value_sum": Entry("300"),
        "green_count": Entry("4"),
        "green_avg_value": Entry("200"),
        "green_value_sum": Entry(""),
        "q5_count": Entry("4"),
        "q5_avg_value": Entry(""),
        "q5_value_sum": Entry("137155"),
        "q4_count": Entry("8"),
        "q4_avg_value": Entry("6328.75"),
        "q4_value_sum": Entry(""),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["white_avg_value"].get() == "100"
    assert overlay.manual_entries["green_value_sum"].get() == "800"
    assert overlay.manual_entries["q5_avg_value"].get() == "34288.75"
    assert overlay.manual_entries["q4_value_sum"].get() == "50630"


def test_ahmad_manual_inline_derivation_refreshes_total_avg_after_total_cells_change() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = {"total_cells"}
    overlay._manual_autofill_values = {"total_cells": "10", "total_avg": "0.3125"}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "total_count": Entry("32"),
        "total_cells": Entry("100"),
        "total_avg": Entry("0.3125"),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["total_cells"].get() == "100"
    assert overlay.manual_entries["total_avg"].get() == "3.125"


def test_ahmad_manual_inline_derivation_refreshes_auto_total_cells_after_avg_change() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = {"total_avg"}
    overlay._manual_autofill_values = {"total_cells": "90"}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "total_count": Entry("45"),
        "total_cells": Entry("90"),
        "total_avg": Entry("2.86"),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["total_cells"].get() == "129"
    assert overlay.manual_entries["total_avg"].get() == "2.86"


def test_ahmad_manual_inline_derivation_keeps_just_cleared_total_cells_empty() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {"total_avg": "2.5"}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "total_count": Entry("40"),
        "total_cells": Entry(""),
        "total_avg": Entry("2.5"),
    }

    module.AhmadTkOverlay._on_manual_var_write(overlay, "total_cells")

    assert overlay.manual_entries["total_cells"].get() == ""


def test_ahmad_manual_inline_derivation_refills_total_cells_after_avg_reentered() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = {"total_cells", "total_count"}
    overlay._manual_autofill_values = {"total_avg": "2.439"}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "total_count": Entry("40"),
        "total_cells": Entry(""),
        "total_avg": Entry("2.5"),
    }

    module.AhmadTkOverlay._on_manual_var_write(overlay, "total_avg")

    assert overlay.manual_entries["total_cells"].get() == "100"
    assert "total_cells" not in overlay._manual_dirty_fields


def test_ahmad_manual_inline_derivation_keeps_user_cleared_field_empty() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = {"q3_count"}
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "q3_avg": Entry("2.5"),
        "q3_count": Entry(""),
        "q3_cells": Entry("290"),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["q3_count"].get() == ""
    assert overlay.manual_entries["q3_cells"].get() == "290"


def test_ahmad_manual_inline_derivation_merges_white_green_only_when_q1_is_empty() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "white_count": Entry("3"),
        "white_cells": Entry("5"),
        "green_count": Entry("4"),
        "green_cells": Entry("8"),
        "q1_avg": Entry(""),
        "q1_count": Entry(""),
        "q1_cells": Entry(""),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["q1_count"].get() == "7"
    assert overlay.manual_entries["q1_cells"].get() == "13"
    assert overlay.manual_entries["q1_avg"].get() == "1.8571"

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "white_count": Entry("3"),
        "white_cells": Entry("5"),
        "green_count": Entry("4"),
        "green_cells": Entry("8"),
        "q1_avg": Entry("2"),
        "q1_count": Entry(""),
        "q1_cells": Entry(""),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["q1_avg"].get() == "2"
    assert overlay.manual_entries["q1_count"].get() == ""
    assert overlay.manual_entries["q1_cells"].get() == ""


def test_ahmad_manual_focus_selects_existing_text_for_easy_replace(monkeypatch) -> None:
    module = _ahmad_overlay_module()

    class FakeEntry:
        def __init__(self, value: str = "116") -> None:
            self.value = value
            self.state = "normal"
            self.selected: tuple[int, str] | None = None
            self.cursor: str | None = None

        def cget(self, key: str) -> str:
            return self.state if key == "state" else ""

        def get(self) -> str:
            return self.value

        def after_idle(self, callback) -> None:
            callback()

        def selection_range(self, start: int, end: str) -> None:
            self.selected = (start, end)

        def icursor(self, pos: str) -> None:
            self.cursor = pos

    monkeypatch.setattr(module.tk, "Entry", FakeEntry)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._manual_programmatic_update = False
    entry = FakeEntry()

    module.AhmadTkOverlay._focus_manual_entry(overlay, SimpleNamespace(widget=entry), field="q3_count")

    assert entry.selected == (0, "end")
    assert entry.cursor == "end"


def test_ahmad_manual_inline_derivation_derives_avg_and_cells_without_overwriting_user_values() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = {"q4_cells"}
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "q1_avg": Entry(""),
        "q1_count": Entry("4"),
        "q1_cells": Entry("8"),
        "q4_avg": Entry("1.8"),
        "q4_count": Entry("5"),
        "q4_cells": Entry("99"),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["q1_avg"].get() == "2"
    assert overlay.manual_entries["q4_cells"].get() == "99"


def test_ahmad_manual_inline_derivation_accepts_display_rounded_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_vars = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay.manual_entries = {
        "q3_avg": Entry("1.8462"),
        "q3_count": Entry("13"),
        "q3_cells": Entry(""),
        "q4_avg": Entry("5.3333"),
        "q4_count": Entry(""),
        "q4_cells": Entry("16"),
        "q5_avg": Entry("1.8"),
        "q5_count": Entry("6"),
        "q5_cells": Entry(""),
    }

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay)

    assert overlay.manual_entries["q3_cells"].get() == "24"
    assert overlay.manual_entries["q4_count"].get() == "3"
    assert overlay.manual_entries["q5_cells"].get() == ""


def test_ahmad_manual_state_auto_resets_on_settlement_and_session_change() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "q5_count": Entry("4"),
    }
    overlay.manual_vars = {}
    overlay.manual_buttons = {}
    overlay.manual_status = Status()
    overlay._manual_active = True
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay._manual_dirty_fields = {"q5_count"}
    overlay._manual_autofill_values = {"hero": "victor", "map_id": "2404"}
    overlay._manual_live_session_id = "2404:old"
    overlay._manual_programmatic_update = False

    assert module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        {
            "status": "ok",
            "context": {"phase": "settled", "session_id": "2404:old"},
            "truth": {"available": True},
        },
    )
    module.AhmadTkOverlay._reset_manual_state(overlay, "auto")

    assert overlay._manual_active is False
    assert overlay._manual_snapshot == {}
    assert overlay._manual_live_session_id == ""
    assert overlay.manual_entries["hero"].get() == ""
    assert overlay.manual_entries["q5_count"].get() == ""
    assert overlay._manual_dirty_fields == set()
    assert overlay._manual_autofill_values == {}

    overlay.manual_entries["hero"].insert(0, "victor")
    overlay._manual_live_session_id = "2404:old"

    assert module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        {"status": "ok", "context": {"phase": "bidding", "session_id": "2404:new"}},
    )


def test_ahmad_manual_state_does_not_reset_on_live_refresh_or_monitor_restart() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "q5_count": Entry("4"),
    }
    overlay._manual_active = True
    overlay._manual_edit_enabled = True
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay._manual_dirty_fields = {"q5_count"}
    overlay._manual_autofill_values = {}
    overlay._manual_live_session_id = "2404:live"

    same_live_update = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "2404:live"},
    }
    monitor_restarted = {
        "status": "stale_snapshot",
        "context": {"phase": "bidding", "session_id": "2404:live"},
        "stale": {"reason": "monitor_restarted"},
    }
    settled_without_truth = {
        "status": "ok",
        "context": {"phase": "settled", "session_id": "2404:live"},
        "truth": {"available": False},
    }

    assert not module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        same_live_update,
    )
    assert not module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        monitor_restarted,
    )
    assert not module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        settled_without_truth,
    )


def test_ahmad_manual_state_resets_on_session_ahead_or_settlement_truth() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {"hero": Entry("victor")}
    overlay._manual_active = True
    overlay._manual_edit_enabled = True
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_live_session_id = "2404:live"

    assert module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        {
            "status": "stale_snapshot",
            "context": {"phase": "bidding", "session_id": "2404:live"},
            "stale": {"reason": "session_ahead"},
        },
    )
    assert module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        {
            "status": "ok",
            "context": {"phase": "settled", "session_id": "2404:live"},
            "truth": {"available": True},
        },
    )


def test_ahmad_manual_settlement_unlock_keeps_same_settlement_editable() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {"hero": Entry("ahmed")}
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = True
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_live_session_id = "2404:live"

    same_settlement = {
        "status": "ok",
        "context": {"phase": "settled", "session_id": "2404:live"},
        "truth": {"available": True},
    }
    next_settlement = {
        "status": "ok",
        "context": {"phase": "settled", "session_id": "2404:new"},
        "truth": {"available": True},
    }

    assert not module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        same_settlement,
    )
    assert module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        next_settlement,
    )


def test_ahmad_prefill_manual_inputs_uses_derived_quality_counts() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry(),
        "map_id": Entry(),
        "total_count": Entry(),
        "total_cells": Entry(),
        "total_avg": Entry(),
        "q5_avg": Entry(),
        "q5_count": Entry(),
        "q5_cells": Entry(),
        "q4q5_count": Entry(),
    }
    overlay.manual_vars = {}
    overlay.manual_status = Status()
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"hero": "victor", "map_id": 2404, "phase": "bidding", "session_id": "2404:live"},
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "avg_cells": {"q5": 6.0},
                "quality_cells": {"q5": 24},
                "fixed_counts": {},
                "count_sums": {"q4q5q6": 6},
            }
        },
    }

    module.AhmadTkOverlay.prefill_manual_inputs(overlay)

    assert overlay.manual_entries["hero"].get() == "victor"
    assert overlay.manual_entries["map_id"].get() == "2404 养生学家居所"
    assert overlay.manual_entries["q5_avg"].get() == "6"
    assert overlay.manual_entries["q5_cells"].get() == "24"
    assert overlay.manual_entries["q5_count"].get() == "4"
    assert overlay.manual_entries["q4q5_count"].get() == "6"
    assert overlay._manual_live_session_id == "2404:live"


def test_ahmad_open_manual_panel_prefills_empty_live_context() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay.manual_entries = {
        "hero": Entry(),
        "map_id": Entry(),
        "total_count": Entry(),
    }
    overlay.manual_vars = {}
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"hero": "aisha", "map_id": 2521, "phase": "bidding", "session_id": "2521:live"},
        "ahmed_ref": {"evidence": {"total_count": 38}},
    }
    overlay._set_manual_edit_enabled = lambda enabled: setattr(overlay, "_manual_edit_enabled", enabled)  # type: ignore[method-assign]
    overlay._set_manual_settlement_button_state = lambda: None  # type: ignore[method-assign]

    assert module.AhmadTkOverlay.open_manual_panel(overlay) == "break"

    assert overlay.manual_entries["hero"].get() == "aisha"
    assert overlay.manual_entries["map_id"].get() == "2521 未知残骸"
    assert overlay.manual_entries["total_count"].get() == "38"
    assert overlay.manual_status.kwargs["text"] == "已填入当前，待应用"


def test_ahmad_open_manual_panel_does_not_overwrite_existing_inputs() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = True
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
    }
    overlay.manual_vars = {}
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"hero": "aisha", "map_id": 2521, "phase": "bidding", "session_id": "2521:live"},
        "ahmed_ref": {"evidence": {"total_count": 38}},
    }
    overlay._set_manual_edit_enabled = lambda enabled: setattr(overlay, "_manual_edit_enabled", enabled)  # type: ignore[method-assign]
    overlay._set_manual_settlement_button_state = lambda: None  # type: ignore[method-assign]

    assert module.AhmadTkOverlay.open_manual_panel(overlay) == "break"

    assert overlay.manual_entries["hero"].get() == "victor"
    assert overlay.manual_entries["map_id"].get() == "2404"
    assert overlay.manual_entries["total_count"].get() == "21"
    # Fields already carry values (read-only mirror / prior input) → 手填 enters override
    # mode, not a blank "待填写" form.
    assert overlay.manual_status.kwargs["text"] == "可编辑，覆盖实时值"


def test_ahmad_clear_settlement_manual_values_keeps_hero_map_and_unlocks_edit() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.kwargs = {}

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

        def cget(self, key: str) -> str:
            return str(self.kwargs.get(key, "normal"))

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2404"),
        "total_count": Entry("25"),
        "total_cells": Entry("50"),
        "q5_count": Entry("4"),
    }
    overlay.manual_vars = {}
    overlay.manual_buttons = {"清结算": Widget()}
    overlay.manual_clear_settlement_tip = Tip()
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_dirty_fields = {"q5_count"}
    overlay._manual_autofill_values = {"total_count": "25"}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    rendered: list[dict] = []
    overlay.render = lambda summary: rendered.append(summary)
    settled = {
        "status": "ok",
        "context": {"hero": "ahmed", "map_id": 2404, "phase": "settled", "session_id": "2404:live"},
        "truth": {"available": True},
        "ahmed_ref": {
            "evidence": {
                "hero": "ahmed",
                "map_id": 2404,
                "total_count": 25,
                "total_grid_target": 50,
                "fixed_counts": {"q5": 4},
            }
        },
    }
    overlay._last_summary = settled
    overlay._last_live_summary = settled

    assert module.AhmadTkOverlay.clear_settlement_manual_values(overlay) == "break"

    assert overlay._manual_edit_enabled is True
    assert overlay._manual_settlement_edit_unlocked is True
    assert overlay.manual_entries["hero"].get() == "ahmed"
    assert "2404" in overlay.manual_entries["map_id"].get()
    assert overlay.manual_entries["total_count"].get() == ""
    assert overlay.manual_entries["q5_count"].get() == ""
    assert overlay._last_summary["context"]["phase"] == "manual"
    assert overlay._last_summary["truth"]["available"] is False
    assert overlay._last_summary["reference"]["readiness"] == "settlement_cleared"
    assert rendered == [overlay._last_summary]
    assert overlay.manual_buttons["清结算"].kwargs["state"] == "disabled"
    assert "清结算" in overlay.manual_status.kwargs["text"]


def test_ahmad_manual_toggle_returns_to_live_without_clearing_inputs() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.kwargs = {"state": "disabled"}

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

        def cget(self, key: str) -> str:
            return str(self.kwargs.get(key, ""))

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    class Tip:
        def __init__(self) -> None:
            self.text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    render_calls: list[dict] = []
    standby_calls: list[dict] = []
    missing_calls: list[str] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.details_expanded = False
    overlay.toggle_details = lambda: setattr(overlay, "details_expanded", True)
    overlay.manual_entries = {
        "hero": Entry(),
        "map_id": Entry(),
        "total_count": Entry(),
        "q5_count": Entry(),
    }
    overlay.manual_vars = {}
    overlay.manual_buttons = {
        "应用并启用": Widget(),
        "填入当前": Widget(),
        "清空手动": Widget(),
    }
    overlay.manual_button = Widget()
    overlay.manual_tip = Tip()
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {
            "hero": "victor",
            "map_id": 2404,
            "phase": "bidding",
            "session_id": "2404:live",
        },
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "fixed_counts": {"q5": 4},
            }
        },
    }
    overlay.render = lambda data: render_calls.append(data)
    overlay.render_standby = lambda data: standby_calls.append(data)
    overlay.render_missing = lambda message: missing_calls.append(message)

    assert module.AhmadTkOverlay.toggle_manual_mode(overlay) == "break"

    assert overlay.details_expanded is True
    assert overlay._manual_edit_enabled is True
    assert overlay.manual_entries["hero"].get() == "victor"
    assert overlay.manual_entries["map_id"].get() == "2404 养生学家居所"
    assert overlay.manual_entries["total_count"].get() == "21"
    assert overlay.manual_entries["hero"].kwargs["state"] == "normal"
    assert overlay.manual_button.kwargs["text"] == "实时"
    assert overlay.manual_tip.text == module.MANUAL_RETURN_TIP_TEXT

    overlay._manual_active = True
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay.manual_entries["hero"].value = "victor"
    overlay._manual_dirty_fields.add("hero")

    assert module.AhmadTkOverlay.toggle_manual_mode(overlay) == "break"

    assert overlay._manual_active is False
    assert overlay._manual_edit_enabled is False
    assert overlay._manual_snapshot == {}
    assert overlay.manual_entries["hero"].get() == "victor"
    assert overlay.manual_entries["hero"].kwargs["state"] == "disabled"
    assert overlay.manual_button.kwargs["text"] == "手填"
    assert overlay.manual_tip.text == module.MANUAL_TIP_TEXT
    assert "不会覆盖当前输入" in overlay.manual_tip.text
    assert render_calls == [overlay._last_live_summary]
    assert standby_calls == []
    assert missing_calls == []


def test_ahmad_clear_manual_inputs_keeps_manual_edit_mode() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.kwargs = {"state": "normal"}

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

        def cget(self, key: str) -> str:
            return str(self.kwargs.get(key, ""))

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    render_calls: list[dict] = []
    standby_calls: list[dict] = []
    missing_calls: list[str] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404 养生学家居所"),
        "total_count": Entry("21"),
        "q5_count": Entry("4"),
        "white_value_sum": Entry("300"),
        "q5_avg_value": Entry("34288.75"),
        "q5_value_sum": Entry("137155"),
    }
    overlay.manual_vars = {}
    overlay.manual_buttons = {"清结算": Widget()}
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = True
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay._manual_dirty_fields = {"hero", "q5_count"}
    overlay._manual_autofill_values = {"total_count": "21"}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = "2404:live"
    overlay._last_summary = {"status": "ok"}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "2404:live"},
    }
    overlay.render = lambda data: render_calls.append(data)
    overlay.render_standby = lambda data: standby_calls.append(data)
    overlay.render_missing = lambda message: missing_calls.append(message)
    overlay._set_manual_settlement_button_state = lambda: None

    assert module.AhmadTkOverlay.clear_manual_inputs(overlay) == "break"

    assert overlay._manual_active is False
    assert overlay._manual_edit_enabled is True
    assert overlay._manual_snapshot == {}
    assert overlay._manual_summary == {}
    assert overlay._manual_live_session_id == ""
    assert overlay.manual_entries["hero"].get() == ""
    assert overlay.manual_entries["q5_count"].get() == ""
    assert overlay.manual_entries["white_value_sum"].get() == ""
    assert overlay.manual_entries["q5_avg_value"].get() == ""
    assert overlay.manual_entries["q5_value_sum"].get() == ""
    assert overlay.manual_status.kwargs["text"] == "已清空，待填写"
    assert render_calls == []
    assert standby_calls == []
    assert missing_calls == []


def test_ahmad_manual_return_to_live_autofills_missing_fields_from_cached_live() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.kwargs = {"state": "normal"}

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

        def cget(self, key: str) -> str:
            return str(self.kwargs.get(key, ""))

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    render_calls: list[dict] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2403 科学家居所"),
        "total_count": Entry("30"),
        "q5_count": Entry("manual-same"),
        "q6_count": Entry(""),
    }
    overlay.manual_vars = {}
    overlay.manual_buttons = {}
    overlay.manual_button = Widget()
    overlay.manual_status = Widget()
    overlay.manual_card = Widget()
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_dirty_fields = {"q5_count"}
    overlay._manual_autofill_values = {
        "hero": "ahmed",
        "map_id": "2403 科学家居所",
        "total_count": "30",
    }
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = "2403:live"
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {
            "hero": "ahmed",
            "map_id": 2403,
            "phase": "bidding",
            "session_id": "2403:live",
        },
        "ahmed_ref": {
            "evidence": {
                "hero": "ahmed",
                "map_id": 2403,
                "total_count": 30,
                "fixed_counts": {"q5": 0, "q6": 1},
            }
        },
    }
    overlay.render = lambda data: render_calls.append(data)
    overlay.render_standby = lambda data: None
    overlay.render_missing = lambda message: None

    assert module.AhmadTkOverlay.return_to_live_mode(overlay) == "break"

    assert overlay._manual_edit_enabled is False
    assert overlay.manual_entries["q5_count"].get() == "manual-same"
    assert overlay.manual_entries["q6_count"].get() == "1"
    assert overlay.manual_entries["q6_count"].kwargs["state"] == "disabled"
    assert "q5_count" in overlay._manual_dirty_fields
    assert render_calls == [overlay._last_live_summary]


def test_ahmad_auto_sync_readonly_backfills_fresh_blank_manual_form() -> None:
    # Detail-fill (b): before the user ever opens 手填, the locked (disabled) detail
    # fields mirror the live evidence read-only instead of staying blank. The entries
    # stay disabled and manual mode is NOT activated; 手填 later unlocks override.
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self) -> None:
            self.value = ""
            self.kwargs = {"state": "disabled"}

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

        def cget(self, key: str) -> str:
            return str(self.kwargs.get(key, ""))

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry(),
        "map_id": Entry(),
        "total_count": Entry(),
        "q5_count": Entry(),
    }
    overlay.manual_vars = {}
    overlay.manual_status = Status()
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    live_summary = {
        "status": "ok",
        "context": {
            "hero": "victor",
            "map_id": 2404,
            "phase": "bidding",
            "session_id": "2404:live",
        },
        "ahmed_ref": {
            "evidence": {
                "hero": "victor",
                "map_id": 2404,
                "total_count": 21,
                "fixed_counts": {"q5": 4},
            },
        },
    }

    module.AhmadTkOverlay._auto_sync_manual_inputs(overlay, live_summary)

    # Read-only mirror is populated...
    assert overlay.manual_entries["hero"].get() == "victor"
    assert overlay.manual_entries["total_count"].get() == "21"
    assert overlay.manual_entries["q5_count"].get() == "4"
    assert overlay._manual_live_session_id == "2404:live"
    # ...but the fields stay locked and manual mode is not activated.
    assert overlay.manual_entries["total_count"].kwargs["state"] == "disabled"
    assert overlay._manual_active is False
    assert overlay._manual_edit_enabled is False
    # The mirror is auto data (overridable), not user-dirtied input.
    assert overlay._manual_dirty_fields == set()


def test_ahmad_manual_state_does_not_reset_when_first_live_frame_arrives() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {"hero": Entry("victor")}
    overlay._manual_active = True
    overlay._manual_edit_enabled = True
    overlay._manual_snapshot = {"hero": "victor"}
    overlay._manual_summary = {"status": "ok"}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_live_session_id = ""

    assert not module.AhmadTkOverlay._should_reset_manual_for_summary(
        overlay,
        {
            "status": "ok",
            "context": {"phase": "bidding", "session_id": "2404:live"},
        },
    )


def test_ahmad_manual_input_summary_shows_quality_lower_bounds() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)

    summary = module.AhmadTkOverlay._manual_input_summary(
        overlay,
        {
            "total_count": 21,
            "fixed_counts": {"q4": 7},
            "min_counts": {"q1": 4, "q3": 2, "q4": 7, "q6": 1},
        },
    )

    assert "下界 白绿≥4，蓝≥2，红≥1" in summary
    assert "紫≥7" not in summary


def test_ahmad_manual_input_summary_shows_quality_value_inputs() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)

    summary = module.AhmadTkOverlay._manual_input_summary(
        overlay,
        {
            "total_count": 30,
            "avg_values": {"q5": 34288.75},
            "quality_values": {"q5": 137155},
        },
    )

    assert "金均价 34288.75" in summary
    assert "金总价 137155" in summary


def test_ahmad_manual_snapshot_allows_total_avg_and_zero_gold() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "total_avg": Entry("1.619"),
        "q4_avg": Entry("1.8"),
        "q5_avg": Entry("0"),
        "q4q5_count": Entry("6"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["total_count"] == 21
    assert ref_inputs["total_cells"] == 34
    assert ref_inputs["avg_cells"] == {"q4": 1.8, "q5": 0.0}
    assert ref_inputs["fixed_counts"]["q5"] == 0
    assert ref_inputs["quality_cells"]["q5"] == 0
    assert ref_inputs["count_sums"] == {"q4q5q6": 6}


def test_ahmad_manual_zero_avg_autofills_empty_count_and_cells_on_edit() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "q5_avg": Entry("0"),
        "q5_count": Entry(""),
        "q5_cells": Entry(""),
        "q5_avg_value": Entry(""),
        "q5_value_sum": Entry(""),
    }
    overlay.manual_vars = {}
    overlay._manual_programmatic_update = False
    overlay._manual_dirty_fields = {"q5_avg", "q5_count", "q5_cells"}
    overlay._manual_autofill_values = {}

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay, trigger_field="q5_avg")

    assert overlay.manual_entries["q5_count"].get() == "0"
    assert overlay.manual_entries["q5_cells"].get() == "0"
    assert overlay.manual_entries["q5_avg_value"].get() == "0"
    assert overlay.manual_entries["q5_value_sum"].get() == "0"
    assert overlay._manual_autofill_values["q5_count"] == "0"
    assert overlay._manual_autofill_values["q5_cells"] == "0"
    assert overlay._manual_autofill_values["q5_avg_value"] == "0"
    assert overlay._manual_autofill_values["q5_value_sum"] == "0"


def test_ahmad_manual_zero_avg_does_not_autofill_over_conflict() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "q5_avg": Entry("0"),
        "q5_count": Entry("4"),
        "q5_cells": Entry(""),
        "q5_avg_value": Entry(""),
        "q5_value_sum": Entry(""),
    }
    overlay.manual_vars = {}
    overlay._manual_programmatic_update = False
    overlay._manual_dirty_fields = {"q5_avg", "q5_count"}
    overlay._manual_autofill_values = {}

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay, trigger_field="q5_avg")

    assert overlay.manual_entries["q5_count"].get() == "4"
    assert overlay.manual_entries["q5_cells"].get() == ""
    assert overlay.manual_entries["q5_avg_value"].get() == ""
    assert overlay.manual_entries["q5_value_sum"].get() == ""


def test_ahmad_manual_zero_avg_apply_fills_empty_count_and_cells() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q5_avg": Entry("0"),
        "q5_count": Entry(""),
        "q5_cells": Entry(""),
        "q5_avg_value": Entry(""),
        "q5_value_sum": Entry(""),
    }
    overlay.manual_vars = {}
    overlay._manual_autofill_values = {}
    overlay._manual_dirty_fields = set()

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert overlay.manual_entries["q5_count"].get() == "0"
    assert overlay.manual_entries["q5_cells"].get() == "0"
    assert overlay.manual_entries["q5_avg_value"].get() == "0"
    assert overlay.manual_entries["q5_value_sum"].get() == "0"
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["fixed_counts"]["q5"] == 0
    assert ref_inputs["quality_cells"]["q5"] == 0
    assert ref_inputs["avg_values"]["q5"] == 0
    assert ref_inputs["quality_values"]["q5"] == 0


def test_ahmad_manual_zero_avg_value_autofills_quality_absent() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "q5_avg": Entry(""),
        "q5_count": Entry(""),
        "q5_cells": Entry(""),
        "q5_avg_value": Entry("0"),
        "q5_value_sum": Entry(""),
    }
    overlay.manual_vars = {}
    overlay._manual_programmatic_update = False
    overlay._manual_dirty_fields = {"q5_avg_value"}
    overlay._manual_autofill_values = {}

    module.AhmadTkOverlay._sync_manual_derived_fields(overlay, trigger_field="q5_avg_value")

    assert overlay.manual_entries["q5_avg"].get() == "0"
    assert overlay.manual_entries["q5_count"].get() == "0"
    assert overlay.manual_entries["q5_cells"].get() == "0"
    assert overlay.manual_entries["q5_value_sum"].get() == "0"


def test_ahmad_manual_zero_value_sum_apply_feeds_engine_exact_zero() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, value: str) -> None:
            self.value = value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q5_avg": Entry(""),
        "q5_count": Entry(""),
        "q5_cells": Entry(""),
        "q5_avg_value": Entry(""),
        "q5_value_sum": Entry("0"),
    }
    overlay.manual_vars = {}
    overlay._manual_autofill_values = {}
    overlay._manual_dirty_fields = set()

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["avg_cells"]["q5"] == 0
    assert ref_inputs["fixed_counts"]["q5"] == 0
    assert ref_inputs["quality_cells"]["q5"] == 0
    assert ref_inputs["avg_values"]["q5"] == 0
    assert ref_inputs["quality_values"]["q5"] == 0

    result = module.run_reference_engine(snapshot, max_combos=60000).as_dict()
    assert result["evidence"]["fixed_counts"]["q5"] == 0
    assert result["evidence"]["quality_cells"]["q5"] == 0


def test_ahmad_manual_table_keeps_quality_and_value_field_order() -> None:
    module = _ahmad_overlay_module()

    assert module.MANUAL_QUALITY_ROWS == (
        ("白", "white_avg", "white_count", "white_cells"),
        ("绿", "green_avg", "green_count", "green_cells"),
        ("白绿", "q1_avg", "q1_count", "q1_cells"),
        ("蓝", "q3_avg", "q3_count", "q3_cells"),
        ("紫", "q4_avg", "q4_count", "q4_cells"),
        ("金", "q5_avg", "q5_count", "q5_cells"),
        ("红", "q6_avg", "q6_count", "q6_cells"),
    )
    assert module.MANUAL_VALUE_ROWS == (
        ("白", "white_avg_value", "white_value_sum"),
        ("绿", "green_avg_value", "green_value_sum"),
        ("白绿", "q1_avg_value", "q1_value_sum"),
        ("蓝", "q3_avg_value", "q3_value_sum"),
        ("紫", "q4_avg_value", "q4_value_sum"),
        ("金", "q5_avg_value", "q5_value_sum"),
        ("红", "q6_avg_value", "q6_value_sum"),
    )


def test_ahmad_manual_snapshot_keeps_quality_avg_value_and_value_sum() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("4406"),
        "total_count": Entry("10"),
        "q5_avg_value": Entry("34288.75"),
        "q5_value_sum": Entry("137155"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["avg_values"] == {"q5": 34288.75}
    assert ref_inputs["quality_values"] == {"q5": 137155}

    result = module.run_reference_engine(snapshot, max_combos=60000).as_dict()
    assert result["evidence"]["fixed_counts"]["q5"] == 4
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]


def test_ahmad_manual_snapshot_combines_white_green_value_inputs_as_q1() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("aisha"),
        "map_id": Entry("2401"),
        "total_count": Entry("12"),
        "white_count": Entry("3"),
        "white_value_sum": Entry("300"),
        "green_count": Entry("4"),
        "green_avg_value": Entry("200"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["split_counts"] == {"white": 3, "green": 4}
    assert ref_inputs["quality_values"] == {"q1": 1100}
    assert ref_inputs["avg_values"] == {"q1": 1100 / 7}


def test_ahmad_manual_snapshot_rejects_single_side_white_green_value() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("aisha"),
        "map_id": Entry("2401"),
        "total_count": Entry("12"),
        "white_value_sum": Entry("300"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "白/绿价值需同时填写" in error
    assert "缺少绿" in error


def test_ahmad_manual_snapshot_rejects_mismatched_quality_avg_value_and_sum() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2401"),
        "total_count": Entry("10"),
        "q5_count": Entry("4"),
        "q5_avg_value": Entry("34288.75"),
        "q5_value_sum": Entry("999999"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "金均价与金总价/金件不一致" in error


def test_ahmad_manual_snapshot_rejects_zero_avg_with_nonzero_quality_inputs() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q5_avg": Entry("0"),
        "q5_count": Entry("4"),
        "q5_cells": Entry("1"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "金均格为0时" in error
    assert "金件" in error or "金格" in error


def test_ahmad_manual_snapshot_accepts_total_only_with_live_context_fallback() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry(""),
        "map_id": Entry(""),
        "total_count": Entry("35"),
        "total_cells": Entry("105"),
    }
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {
            "hero": "ahmed",
            "map_id": 2403,
            "phase": "bidding",
            "session_id": "2403:live",
        },
        "ahmed_ref": {"evidence": {"hero": "ahmed", "map_id": 2403}},
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["hero"] == "ahmed"
    assert snapshot["map_id"] == 2403
    assert snapshot["structured_ref_inputs"] == {
        "total_count": 35,
        "avg_cells": {},
        "fixed_counts": {},
        "total_cells": 105,
    }
    source = snapshot["ui_contract"]["source"]
    assert source["manual_map_input"] == ""
    assert source["manual_context_fallback"] == {
        "hero": "live_context",
        "map_id": "live_context",
    }

    result = module.run_reference_engine(snapshot, max_combos=60000).as_dict()
    assert result["status"] == "count_prior"
    assert result["balanced"] not in (None, "")
    assert result["total_grid_range"] == [105, 105, 105]


def test_ahmad_manual_snapshot_uses_quality_avg_after_total_only_fallback() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry(""),
        "map_id": Entry(""),
        "total_count": Entry("35"),
        "total_cells": Entry("105"),
        "q4_avg": Entry("3.5"),
    }
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"hero": "ahmed", "map_id": 2403, "phase": "bidding"},
        "ahmed_ref": {"evidence": {"hero": "ahmed", "map_id": 2403}},
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["structured_ref_inputs"]["avg_cells"] == {"q4": 3.5}

    result = module.run_reference_engine(snapshot, max_combos=60000).as_dict()
    assert result["status"] == "count_prior"
    assert result["balanced"] not in (None, "")
    assert result["quality_count_ranges"]["q4"][0] > 0


def test_ahmad_manual_snapshot_accepts_chinese_map_name() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("维克托"),
        "map_id": Entry("养生学家居所"),
        "total_count": Entry("21"),
        "q5_count": Entry("4"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["map_id"] == 2404
    assert snapshot["map_name"] == "养生学家居所"
    assert snapshot["ui_contract"]["context"]["map_id"] == 2404
    assert snapshot["ui_contract"]["context"]["map_name"] == "养生学家居所"
    assert snapshot["ui_contract"]["source"]["manual_map_input"] == "养生学家居所"


def test_ahmad_manual_snapshot_accepts_all_chinese_hero_aliases() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    for hero_text, expected in (
        ("艾哈迈德", "ahmed"),
        ("维克托", "victor"),
        ("艾莎", "aisha"),
    ):
        overlay = object.__new__(module.AhmadTkOverlay)
        overlay.manual_entries = {
            "hero": Entry(hero_text),
            "map_id": Entry("养生学家居所"),
            "total_count": Entry("21"),
            "q5_count": Entry("4"),
        }

        snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

        assert error == ""
        assert snapshot is not None
        assert snapshot["hero"] == expected
        assert snapshot["map_id"] == 2404
        assert snapshot["map_name"] == "养生学家居所"


def test_ahmad_manual_snapshot_accepts_project_processed_map_id_and_name() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("维克托"),
        "map_id": Entry("3101 未知快递"),
        "total_count": Entry("21"),
        "q5_count": Entry("4"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["map_id"] == 3101
    assert snapshot["map_name"] == "未知快递"


def test_ahmad_map_rows_fill_red_crate_names_when_maps_json_lags(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    maps_path = tmp_path / "maps.json"
    maps_path.write_text(
        json.dumps([{"map_id": 2404, "name": "养生学家居所"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_processed_maps_candidates", lambda: (maps_path,))
    module._processed_map_name_rows.cache_clear()
    try:
        rows = dict(module._processed_map_name_rows())
        assert rows[2545] == "殖民商船宝库(红箱活动)"
        assert rows[4545] == "殖民商船宝库(红箱活动)"
        assert 2551 not in rows
        assert 4551 not in rows
        assert module._manual_map_display_value(2545) == "2545 殖民商船宝库(红箱活动)"
    finally:
        module._processed_map_name_rows.cache_clear()


def test_ahmad_manual_snapshot_rejects_unknown_chinese_map_name() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("维克托"),
        "map_id": Entry("不存在地图"),
        "total_count": Entry("21"),
        "q5_count": Entry("4"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "地图无法识别" in error


def test_ahmad_manual_snapshot_derives_counts_from_avg_and_cells_and_normalizes_hero() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("维克托"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q1_avg": Entry("1.1428571428571428"),
        "q1_cells": Entry("8"),
        "q3_avg": Entry("2"),
        "q3_cells": Entry("16"),
        "q5_avg": Entry("6"),
        "q5_cells": Entry("24"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["hero"] == "victor"
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["fixed_counts"] == {"q1": 7, "q3": 8, "q5": 4}
    assert ref_inputs["quality_cells"] == {"q1": 8, "q3": 16, "q5": 24}
    assert ref_inputs["avg_cells"] == {"q1": 1.1428571428571428, "q3": 2.0, "q5": 6.0}


def test_ahmad_manual_snapshot_accepts_display_rounded_avg_when_count_and_cells_match() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2404"),
        "total_count": Entry("24"),
        "q3_avg": Entry("1.8462"),
        "q3_count": Entry("13"),
        "q3_cells": Entry("24"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["fixed_counts"] == {"q3": 13}
    assert ref_inputs["quality_cells"] == {"q3": 24}
    assert ref_inputs["avg_cells"] == {"q3": 24 / 13}


def test_ahmad_manual_display_avg_keeps_trailing_zero_semantics() -> None:
    module = _ahmad_overlay_module()

    assert module._manual_avg_grid_options_from_text(21, 1.8, "1.80") == [38]
    assert module._manual_avg_grid_options_from_text(5, 1.8, "1.80") == []
    assert module._manual_avg_grid_options_from_text(5, 1.8, "1.8") == [9]
    assert module._manual_avg_grid_options_from_text(11, 2.9, "2.90") == [32]
    assert module._manual_avg_grid_options_from_text(10, 2.9, "2.90") == []


def test_ahmad_manual_snapshot_accepts_trailing_zero_display_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2401"),
        "total_count": Entry("55"),
        "q1_avg": Entry("1.80"),
        "q1_cells": Entry("38"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["fixed_counts"] == {"q1": 21}
    assert ref_inputs["quality_cells"] == {"q1": 38}
    assert ref_inputs["avg_cells"] == {"q1": 38 / 21}


def test_ahmad_manual_snapshot_uses_display_avg_for_total_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2401"),
        "total_count": Entry("21"),
        "total_avg": Entry("1.619"),
        "q4_avg": Entry("1.8"),
        "q4_count": Entry("5"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["structured_ref_inputs"]["total_cells"] == 34


def test_ahmad_manual_snapshot_rejects_trimmed_exact_ratio_for_total_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2401"),
        "total_count": Entry("10"),
        "total_avg": Entry("2.90"),
        "q4_avg": Entry("1.8"),
        "q4_count": Entry("5"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "全均格" in error


def test_ahmad_manual_snapshot_rejects_conflicting_total_cells_and_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("艾哈迈德"),
        "map_id": Entry("设计师居所"),
        "total_count": Entry("45"),
        "total_cells": Entry("90"),
        "total_avg": Entry("2.86"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "全均格与总格/总件不一致" in error


def test_ahmad_manual_snapshot_rejects_trimmed_exact_ratio_for_trailing_zero_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("ahmed"),
        "map_id": Entry("2401"),
        "total_count": Entry("55"),
        "q4_avg": Entry("2.90"),
        "q4_count": Entry("10"),
        "q4_cells": Entry("29"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "紫均格" in error
    assert "不一致" in error


def test_ahmad_manual_snapshot_rejects_over_rounded_one_decimal_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q4_avg": Entry("1.8"),
        "q4_count": Entry("6"),
        "q4_cells": Entry("11"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "紫均格" in error
    assert "不一致" in error


def test_ahmad_manual_snapshot_accepts_aisha_split_and_merged_low_quality() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("艾莎"),
        "map_id": Entry("2401"),
        "total_count": Entry("33"),
        "white_count": Entry("3"),
        "white_cells": Entry("5"),
        "green_avg": Entry("2"),
        "green_cells": Entry("8"),
        "q1_count": Entry("7"),
        "q1_cells": Entry("13"),
        "q3_count": Entry("6"),
        "q3_cells": Entry("12"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    assert snapshot["hero"] == "aisha"
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["split_counts"] == {"white": 3, "green": 4}
    assert ref_inputs["split_quality_cells"] == {"white": 5, "green": 8}
    assert ref_inputs["split_avg_cells"] == {"white": 5 / 3, "green": 2.0}
    assert ref_inputs["fixed_counts"] == {"q1": 7, "q3": 6}
    assert ref_inputs["quality_cells"] == {"q1": 13, "q3": 12}


def test_ahmad_manual_snapshot_keeps_white_only_split_separate_from_q1_avg() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("aisha"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "white_count": Entry("3"),
        "white_cells": Entry("5"),
        "q1_avg": Entry("2"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert error == ""
    assert snapshot is not None
    ref_inputs = snapshot["structured_ref_inputs"]
    assert ref_inputs["split_counts"] == {"white": 3}
    assert ref_inputs["split_quality_cells"] == {"white": 5}
    assert ref_inputs["split_avg_cells"] == {"white": 5 / 3}
    assert ref_inputs["avg_cells"] == {"q1": 2.0}
    assert ref_inputs["fixed_counts"] == {}
    assert "quality_cells" not in ref_inputs


def test_ahmad_manual_snapshot_rejects_impossible_avg_count_pair() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q4_count": Entry("3"),
        "q4_avg": Entry("0"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "紫均格" in error
    assert "紫件" in error


def test_ahmad_manual_snapshot_rejects_fractional_avg_count_product() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("victor"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q4_count": Entry("4"),
        "q4_avg": Entry("1.8"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "紫均格" in error
    assert "整数格数" in error


def test_ahmad_manual_snapshot_rejects_impossible_avg_cells_pair_without_count() -> None:
    module = _ahmad_overlay_module()

    class Entry:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {
        "hero": Entry("维克托"),
        "map_id": Entry("2404"),
        "total_count": Entry("21"),
        "q4_avg": Entry("1.8"),
        "q4_cells": Entry("8"),
    }

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert "紫均格与紫格" in error
    assert "整数件数" in error


def test_ahmad_manual_overlay_keeps_live_context_and_merges_inputs() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    live_snapshot = {
        "created_at": time.time(),
        "hero": "ahmed",
        "map_id": 2401,
        "round": 1,
        "phase": "bidding",
        "session_id": "2401:live",
        "structured_ref_inputs": {
            "avg_cells": {"q5": 4.6667},
            "fixed_counts": {"q5": 2},
        },
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2401,
                "round": 1,
                "phase": "bidding",
                "session_id": "2401:live",
            },
            "constraints": {},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
    }
    manual_snapshot = {
        "structured_ref_inputs": {
            "total_count": 39,
            "quality_cells": {"q1": 16},
            "avg_cells": {"q3": 1.55},
            "fixed_counts": {},
        }
    }

    overlaid = module.AhmadTkOverlay._snapshot_with_manual_overlay(
        overlay,
        live_snapshot,
        manual_snapshot,
    )

    context = overlaid["ui_contract"]["context"]
    ref_inputs = overlaid["structured_ref_inputs"]
    assert context["session_id"] == "2401:live"
    assert context["round"] == 1
    assert ref_inputs["total_count"] == 39
    assert ref_inputs["quality_cells"] == {"q1": 16}
    assert ref_inputs["avg_cells"] == {"q5": 4.6667, "q3": 1.55}
    assert ref_inputs["fixed_counts"] == {"q5": 2}
    assert overlaid["ui_contract"]["constraints"]["structured_ref_inputs"] == ref_inputs


def test_aisha_manual_total_and_gold_cells_reach_exact_red_residual_summary(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    live_snapshot = {
        "created_at": time.time(),
        "hero": "aisha",
        "map_id": 4550,
        "round": 5,
        "phase": "bidding",
        "session_id": "4550:hard-red-residual",
        "structured_ref_inputs": {
            "fixed_counts": {"q1": 15, "q3": 11, "q4": 8},
            "min_counts": {"q6": 1},
            "avg_cells": {"q1": 2.0},
            "quality_cells": {"q3": 30, "q4": 29},
        },
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 4550,
                "round": 5,
                "phase": "bidding",
                "session_id": "4550:hard-red-residual",
            },
            "constraints": {},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
    }
    manual_snapshot = {
        "structured_ref_inputs": {
            "total_count": 43,
            "total_cells": 134,
            "quality_cells": {"q5": 18},
        }
    }

    overlaid = module.AhmadTkOverlay._snapshot_with_manual_overlay(
        overlay,
        live_snapshot,
        manual_snapshot,
    )
    summary = module.summarize_snapshot(
        overlaid,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["red"]["cells_range"] == "27 / 27 / 27"
    assert summary["ahmed_ref"]["quality_cells_ranges"]["q6"] == [27, 27, 27]
    assert "red_cells_locked_to_hard_total_residual:27cells" in summary["ahmed_ref"]["notes"]


def test_ahmad_apply_manual_from_settlement_uses_standalone_manual_result() -> None:
    module = _ahmad_overlay_module()

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    overlay = object.__new__(module.AhmadTkOverlay)
    manual_snapshot = {"structured_ref_inputs": {"total_count": 25}}
    manual_summary = {"status": "ok", "context": {"phase": "manual"}}
    rendered: list[dict] = []
    overlay._manual_inputs_snapshot = lambda: (manual_snapshot, "")  # type: ignore[method-assign]
    overlay._manual_result_summary = lambda snapshot: manual_summary  # type: ignore[method-assign]
    overlay._manual_overlay_summary = lambda *_args: (_ for _ in ()).throw(AssertionError("overlay path should not run"))  # type: ignore[method-assign]
    overlay._set_manual_edit_enabled = lambda enabled: setattr(overlay, "_manual_edit_enabled", enabled)  # type: ignore[method-assign]
    overlay._set_manual_button_state = lambda: None  # type: ignore[method-assign]
    overlay.render = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.render_standby = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.manual_status = Status()
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = True
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._last_summary = {}
    overlay._last_live_snapshot = {"phase": "settled"}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"phase": "settled", "session_id": "2404:live"},
        "truth": {"available": True},
    }

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    assert overlay._manual_active is True
    assert overlay._manual_snapshot == manual_snapshot
    assert overlay._last_summary == manual_summary
    assert rendered == [manual_summary]
    assert "结算页已脱离" in overlay.manual_status.kwargs["text"]


def test_ahmad_apply_manual_uses_standalone_result_while_live_continues() -> None:
    module = _ahmad_overlay_module()

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    manual_snapshot = {
        "hero": "ahmed",
        "map_id": 2403,
        "structured_ref_inputs": {"total_count": 35, "total_cells": 105},
    }
    manual_summary = {"status": "ok", "context": {"phase": "manual"}}
    rendered: list[dict] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._manual_inputs_snapshot = lambda: (manual_snapshot, "")  # type: ignore[method-assign]
    overlay._manual_result_summary = lambda snapshot: manual_summary  # type: ignore[method-assign]
    overlay._manual_overlay_summary = lambda *_args: (_ for _ in ()).throw(AssertionError("manual apply must not merge live snapshot"))  # type: ignore[method-assign]
    overlay._set_manual_edit_enabled = lambda enabled: setattr(overlay, "_manual_edit_enabled", enabled)  # type: ignore[method-assign]
    overlay._set_manual_button_state = lambda: None  # type: ignore[method-assign]
    overlay.render = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.render_standby = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.manual_status = Status()
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._last_summary = {}
    overlay._last_live_snapshot = {
        "structured_ref_inputs": {"fixed_counts": {"q5": 99}},
    }
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "live-session"},
        "truth": {"available": False},
    }

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    assert overlay._manual_active is True
    assert overlay._manual_snapshot == manual_snapshot
    assert overlay._last_summary == manual_summary
    assert rendered == [manual_summary]
    assert "实时后台" in overlay.manual_status.kwargs["text"]


def test_ahmad_apply_manual_uses_background_worker_when_available() -> None:
    module = _ahmad_overlay_module()

    class Status:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

    manual_snapshot = {
        "hero": "ahmed",
        "map_id": 2403,
        "structured_ref_inputs": {"total_count": 35, "total_cells": 105},
    }
    manual_summary = {"status": "ok", "context": {"phase": "manual"}}
    rendered: list[dict] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._manual_inputs_snapshot = lambda: (manual_snapshot, "")  # type: ignore[method-assign]
    overlay._manual_result_summary = lambda snapshot: manual_summary  # type: ignore[method-assign]
    overlay._set_manual_edit_enabled = lambda enabled: setattr(overlay, "_manual_edit_enabled", enabled)  # type: ignore[method-assign]
    overlay._set_manual_button_state = lambda: None  # type: ignore[method-assign]
    overlay.render = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.render_standby = lambda summary: rendered.append(summary)  # type: ignore[method-assign]
    overlay.manual_status = Status()
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_worker_seq = 0
    overlay._manual_input_revision = 7
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._last_summary = {}
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "live-session"},
        "truth": {"available": False},
    }

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    assert rendered == []
    assert overlay._manual_worker_running is True
    assert overlay.manual_status.kwargs["text"] == "手动计算中..."

    for _ in range(50):
        module.AhmadTkOverlay._drain_manual_summary_results(overlay)
        if rendered:
            break
        time.sleep(0.01)

    assert overlay._manual_worker_running is False
    assert overlay._manual_active is True
    assert overlay._manual_snapshot == manual_snapshot
    assert overlay._last_summary == manual_summary
    assert rendered == [manual_summary]


def test_ahmad_server_marks_sparse_exact_total_prior_as_fast_path(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    snapshot = {
        "created_at": time.time(),
        "hero": "ahmed",
        "map_id": 2401,
        "phase": "bidding",
        "session_id": "2401:live",
        "structured_ref_inputs": {"total_count": 33},
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2401,
                "phase": "bidding",
                "session_id": "2401:live",
            },
            "constraints": {"public_info": {}},
            "baseline": {"decision": {}, "posterior": {}},
            "source": {"created_at": time.time()},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["reference"]["readiness"] == "sparse_exact_prior"
    assert result["reference"]["source"] == "ref_prior"
    assert result["red"]["uncertainty_summary"].startswith("未锁 ")
    assert any(flag["label"] == "宽约束快速" for flag in result["flags"])
    assert not any(flag["label"] == "总件估计" for flag in result["flags"])


def test_ahmad_server_preserves_rare_signal_diagnostics(tmp_path: Path) -> None:
    module = _ahmad_server_module()
    rare_signals = {
        "present": True,
        "summary": "终极审计:diagnostic_only",
        "role_counts": {"diagnostic_only": 1},
        "actions": [
            {
                "action_id": 100121,
                "label": "终极审计",
                "semantic": "total_value",
                "ref_v0_role": "diagnostic_only",
                "result": 728211,
            }
        ],
        "public_info": [],
    }
    snapshot = {
        "created_at": time.time(),
        "hero": "ahmed",
        "map_id": 2401,
        "phase": "bidding",
        "session_id": "2401:live",
        "structured_ref_inputs": {"total_count": 33},
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2401,
                "phase": "bidding",
                "session_id": "2401:live",
            },
            "constraints": {"public_info": {}},
            "baseline": {"decision": {}, "posterior": {}},
            "diagnostics": {"rare_signals": rare_signals},
            "source": {"created_at": time.time()},
        },
    }

    result = module.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")

    assert result["diagnostics"]["rare_signals"] == rare_signals


def test_ahmad_refresh_does_not_reschedule_after_watched_pid_exit(monkeypatch, tmp_path: Path) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def destroy(self) -> None:
            self.destroyed = True

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: True)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = (123,)
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "portable"

    module.AhmadTkOverlay.refresh(overlay)

    assert overlay.root.destroyed is True
    assert overlay.root.after_calls == []


def test_notify_watched_monitor_exit_one_shot_and_optout(tmp_path: Path, monkeypatch) -> None:
    """The monitor-exit popup fires at most once (one-shot guard) and is a safe no-op when opted
    out / under pytest, so teardown never blocks on a modal dialog. It must never raise."""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    # PYTEST_CURRENT_TEST is set during pytest -> the dialog path is skipped; call must be inert.
    module.AhmadTkOverlay._notify_watched_monitor_exit(overlay)
    assert overlay._monitor_exit_notified is True
    # Second call is a no-op (one-shot): still True, still no raise.
    module.AhmadTkOverlay._notify_watched_monitor_exit(overlay)
    assert overlay._monitor_exit_notified is True

    # Explicit kill-switch also short-circuits before any dialog work, even without pytest env.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("HERO_REF_NO_EXIT_DIALOG", "1")
    overlay2 = object.__new__(module.AhmadTkOverlay)
    overlay2.snapshot_path = tmp_path / "latest_snapshot.json"
    module.AhmadTkOverlay._notify_watched_monitor_exit(overlay2)
    assert overlay2._monitor_exit_notified is True


def test_refresh_watched_monitor_exit_notifies_before_destroy(tmp_path: Path, monkeypatch) -> None:
    """When the watched monitor pid is gone, refresh() must fire the exit notification and then
    tear down (destroy, no reschedule). Guards the wiring so a future edit can't drop the notice."""
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def destroy(self) -> None:
            self.destroyed = True

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: True)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = (123,)
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "portable"
    notified: list[bool] = []
    overlay._notify_watched_monitor_exit = lambda: notified.append(True)  # type: ignore[method-assign]

    module.AhmadTkOverlay.refresh(overlay)

    assert notified == [True]  # notice fired
    assert overlay.root.destroyed is True
    assert overlay.root.after_calls == []


def test_ahmad_render_missing_merges_action_and_next_step_without_loss() -> None:
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.title = Widget()
    overlay.subtitle = Widget()
    overlay.status = Widget()
    overlay.action_rows = {
        "动作": Widget(),
        "最近": Widget(),
        "候选": Widget(),
    }
    overlay.evidence_rows = {
        "匹配": Widget(),
        "密度": Widget(),
        "诊断": Widget(),
    }
    overlay.detail_rows = {
        "外援": Widget(),
        "备注": Widget(),
    }
    overlay._render_flags = lambda *args, **kwargs: None
    overlay._set_settlement_button_state = lambda *args, **kwargs: None
    overlay._clear_values = lambda *args, **kwargs: None
    overlay._render_minimap = lambda *args, **kwargs: None
    overlay._record_summary_diagnostic = lambda *args, **kwargs: None
    overlay._capture_status = lambda: {
        "source": "windivert",
        "active_flows": 1,
        "sniffed_packets": 1,
        "raw_packets": 0,
        "accepted_frames": 0,
    }

    module.AhmadTkOverlay.render_missing(overlay, "等待 latest_snapshot.json")

    assert overlay.action_rows["动作"].kwargs["text"] == "等待对局包 · 进入新局或用道具"
    tip = overlay.action_rows["动作"]._hover_tip
    assert "当前动作\n  等待对局包" in tip.text
    assert "下一步\n  进入新局或用道具" in tip.text
    assert overlay.evidence_rows["匹配"].kwargs["text"] == "no_raw_packets"


def test_ahmad_render_missing_surfaces_windivert_open_error() -> None:
    module = _ahmad_overlay_module()

    class Widget:
        def __init__(self) -> None:
            self.kwargs = {}

        def configure(self, **kwargs) -> None:
            self.kwargs.update(kwargs)

        def bind(self, *args, **kwargs) -> None:
            pass

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.title = Widget()
    overlay.subtitle = Widget()
    overlay.status = Widget()
    overlay.action_rows = {
        "动作": Widget(),
        "最近": Widget(),
        "候选": Widget(),
    }
    overlay.evidence_rows = {
        "匹配": Widget(),
        "密度": Widget(),
        "诊断": Widget(),
    }
    overlay.detail_rows = {
        "外援": Widget(),
        "备注": Widget(),
    }
    rendered_flags = []
    overlay._render_flags = rendered_flags.extend
    overlay._set_settlement_button_state = lambda *args, **kwargs: None
    overlay._clear_values = lambda *args, **kwargs: None
    overlay._render_minimap = lambda *args, **kwargs: None
    overlay._record_summary_diagnostic = lambda *args, **kwargs: None
    overlay._capture_status = lambda: {
        "source": "windivert",
        "active_flows": 2,
        "sniffed_packets": 0,
        "raw_packets": 0,
        "accepted_frames": 0,
        "error_code": "windivert_dependency_missing",
        "error_message": "[WinError 2]",
        "error_hint": "重新解压 full 包，信任整个文件夹后用管理员入口启动。",
    }

    module.AhmadTkOverlay.render_missing(overlay, "等待 latest_snapshot.json")

    assert overlay.action_rows["动作"].kwargs["text"] == "检查防火墙/安全软件 · 按提示排查抓包"
    assert "下一步\n  按提示排查抓包" in overlay.action_rows["动作"]._hover_tip.text
    assert overlay.evidence_rows["匹配"].kwargs["text"] == "windivert_dependency_missing"
    assert rendered_flags[0]["level"] == "risk"
    assert "WinError 2" in rendered_flags[0]["detail"]


def test_ahmad_refresh_writes_runtime_status_while_waiting_for_packets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.after_calls: list[tuple[int, object]] = []

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    snapshot_path = tmp_path / "latest_snapshot.json"
    missing_calls: list[str] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = ()
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = snapshot_path
    overlay.diagnostic_profile = "portable"
    overlay._last_signature = None
    overlay._last_inference_signature = None
    overlay._last_bid_signature = ()
    overlay._last_capture_status_signature = None
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay._last_live_snapshot = {}
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = False
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_worker_seq = 0
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay.render_missing = lambda message: missing_calls.append(message)
    overlay._capture_status = lambda: {
        "source": "windivert",
        "process_name": "BidKing.exe",
        "active_flows": 1,
        "sniffed_packets": 12,
        "raw_packets": 0,
        "accepted_frames": 0,
        "ignored_frames": 0,
    }

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: False)

    module.AhmadTkOverlay.refresh(overlay)

    runtime_path = tmp_path / module.UI_RUNTIME_STATUS
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert missing_calls == ["等待 latest_snapshot.json"]
    assert payload["event"] == "waiting_for_snapshot"
    assert payload["snapshot_exists"] is False
    assert payload["capture"]["wait_state"] == "no_raw_packets"
    assert payload["capture"]["active_flows"] == 1
    assert payload["capture"]["raw_packets"] == 0
    assert overlay.root.after_calls == [(1000, overlay.refresh)]


def test_ahmad_refresh_summarizes_snapshot_in_background_worker(monkeypatch, tmp_path: Path) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def destroy(self) -> None:
            self.destroyed = True

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text('{"file": "snapshot"}', encoding="utf-8")
    rendered: list[dict] = []
    summary = {
        "status": "ok",
        "updated_at_text": "12:34:56",
        "context": {"phase": "bidding", "session_id": "2404:new"},
        "reference": {},
        "evidence": {},
        "red": {},
        "ahmed_ref": {"evidence": {}},
        "diagnostics": {"rare_signals": {}},
        "minimap": {},
        "truth": {"available": False},
    }

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = ()
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = snapshot_path
    overlay.diagnostic_profile = "portable"
    overlay._last_signature = (0, 0)
    overlay._last_inference_signature = None
    overlay._last_bid_signature = ()
    overlay._last_capture_status_signature = None
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay._last_live_snapshot = {}
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = False
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_worker_seq = 0
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_live_session_id = ""
    overlay.render = lambda data: rendered.append(data)
    overlay.render_standby = lambda data: rendered.append(data)
    overlay.render_missing = lambda message: None
    overlay._record_ui_health = lambda row: None
    overlay._capture_status = lambda: {}

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: False)
    monkeypatch.setattr(module, "_read_json", lambda path: {"file": "snapshot"})
    # The process-pool path runs the real summarize_snapshot in a SUBPROCESS (bypassing this mock); the
    # worker-lifecycle logic under test (drain/coalesce/stale) is identical for both paths, so exercise
    # the mockable in-thread worker here.
    monkeypatch.setenv("HERO_REF_DISABLE_PROCESS_ENGINE", "1")
    monkeypatch.setattr(module, "summarize_snapshot", lambda snapshot, snapshot_path: dict(summary))

    module.AhmadTkOverlay.refresh(overlay)

    assert rendered == []
    assert overlay._summary_worker_running is True

    for _ in range(50):
        module.AhmadTkOverlay.refresh(overlay)
        if rendered:
            break
        time.sleep(0.01)

    assert overlay._summary_worker_running is False
    assert overlay._last_live_summary["context"]["session_id"] == "2404:new"
    assert rendered == [overlay._last_live_summary]


def test_ahmad_summary_watchdog_does_not_duplicate_long_in_thread_inference(monkeypatch) -> None:
    """A deep Gabriela call can legitimately exceed the process watchdog threshold.

    Python cannot cancel the original thread, so declaring it recovered would start a second engine
    pass and make both calls slower. Keep the worker/pending snapshot intact until the real result lands.
    """
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 7
    overlay._summary_worker_signature = (7, 7)
    overlay._summary_worker_pending = {
        "snapshot": {"ref": "new"},
        "signature": (8, 8),
        "started_at": 10.0,
    }
    overlay._summary_worker_started_wall = 10.0
    overlay._summary_worker_mode = "thread"
    overlay.snapshot_path = Path("latest_snapshot.json")
    health: list[dict] = []
    runtime: list[str] = []
    overlay._record_ui_health = lambda row: health.append(row)
    overlay._record_ui_runtime_status = lambda event, **_kwargs: runtime.append(event)

    monkeypatch.setattr(module.time, "monotonic", lambda: 200.0)
    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert overlay._summary_worker_running is True
    assert overlay._summary_worker_seq == 7
    assert overlay._summary_worker_signature == (7, 7)
    assert overlay._summary_worker_pending["signature"] == (8, 8)
    assert health == []
    assert runtime == []


def test_ahmad_summary_watchdog_recovers_only_hung_process_worker(monkeypatch) -> None:
    module = _ahmad_overlay_module()

    class Executor:
        def __init__(self) -> None:
            self.shutdown_calls: list[tuple[bool, bool]] = []

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.shutdown_calls.append((wait, cancel_futures))

    executor = Executor()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 2
    overlay._summary_worker_signature = (2, 2)
    overlay._summary_worker_pending = None
    overlay._summary_worker_started_wall = 0.0
    overlay._summary_worker_mode = "process"
    overlay._ref_engine_executor = executor
    overlay._last_inference_signature = (2, 2)
    overlay._last_signature = (2, 2)
    overlay.snapshot_path = Path("latest_snapshot.json")
    health: list[dict] = []
    runtime: list[str] = []
    overlay._record_ui_health = lambda row: health.append(row)
    overlay._record_ui_runtime_status = lambda event, **_kwargs: runtime.append(event)

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module, "SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS", 90.0)
    module.AhmadTkOverlay._drain_live_summary_results(overlay)

    assert overlay._summary_worker_running is False
    assert overlay._summary_worker_mode is None
    assert overlay._summary_worker_seq == 3
    assert overlay._ref_engine_pool_disabled is True
    assert overlay._ref_engine_executor is None
    assert overlay._last_inference_signature is None
    assert overlay._last_signature is None
    assert executor.shutdown_calls == [(False, True)]
    assert health[0]["event"] == "summary_worker_timeout_recovered"
    assert runtime == ["summary_worker_timeout_recovered"]


def test_ahmad_pending_same_ref_inputs_reuses_finished_worker_result(monkeypatch) -> None:
    """Pending mtime/bid churn must not bypass ref-cache and launch a duplicate full pass."""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    pending_snapshot = {
        "ref": "same",
        "ui_contract": {
            "context": {"phase": "bidding"},
            "baseline": {"decision": {"current_highest": "123"}},
        },
    }
    overlay._summary_worker_pending = {
        "snapshot": pending_snapshot,
        "signature": (9, 9),
        "started_at": 1.0,
    }
    overlay._ref_cache_signature = ("same",)
    overlay._ref_cache_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "S1"},
        "reference": {"balanced": "456,000", "current_highest": "100"},
        "diagnostics": {},
    }
    overlay._last_signature = (8, 8)
    applied: list[tuple[dict, dict]] = []
    runtime: list[str] = []
    started: list[dict] = []
    overlay._apply_live_summary = lambda snapshot, summary: applied.append((snapshot, summary))
    overlay._mark_summary_performance = lambda *_args: None
    overlay._record_ui_runtime_status = lambda event, **_kwargs: runtime.append(event)
    overlay._start_live_summary_worker = lambda **kwargs: started.append(kwargs)

    monkeypatch.setattr(
        module,
        "ref_input_signature_from_snapshot",
        lambda snapshot: (snapshot["ref"],),
    )
    module.AhmadTkOverlay._maybe_start_pending_summary_worker(overlay)

    assert overlay._summary_worker_pending is None
    assert started == []
    assert overlay._last_signature == (9, 9)
    assert applied[0][0] is pending_snapshot
    assert applied[0][1]["reference"]["balanced"] == "456,000"
    assert applied[0][1]["reference"]["current_highest"] == "1*3"
    assert applied[0][1]["performance"]["pending_ref_cache_hit"] is True
    assert runtime == ["summary_pending_cache_hit"]


def test_ahmad_pending_changed_ref_inputs_starts_new_worker(monkeypatch) -> None:
    """A real skill/public-info change must not borrow the prior quote cache."""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    pending_snapshot = {
        "ref": "changed",
        "ui_contract": {"context": {"phase": "bidding"}},
    }
    overlay._summary_worker_pending = {
        "snapshot": pending_snapshot,
        "signature": (10, 10),
        "started_at": 2.0,
    }
    overlay._ref_cache_signature = ("old",)
    overlay._ref_cache_summary = {
        "status": "ok",
        "reference": {"balanced": "456,000"},
    }
    started: list[dict] = []
    overlay._start_live_summary_worker = lambda **kwargs: started.append(kwargs)

    monkeypatch.setattr(
        module,
        "ref_input_signature_from_snapshot",
        lambda snapshot: (snapshot["ref"],),
    )
    module.AhmadTkOverlay._maybe_start_pending_summary_worker(overlay)

    assert overlay._summary_worker_pending is None
    assert started == [
        {
            "snapshot": pending_snapshot,
            "signature": (10, 10),
            "started_at": 2.0,
        }
    ]


def test_ahmad_inference_status_stays_compact_with_or_without_previous_quote(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._summary_worker_started_wall = 10.0
    overlay._last_live_summary = {}
    monkeypatch.setattr(module.time, "monotonic", lambda: 20.0)

    first = module.AhmadTkOverlay._inference_status_text(overlay)
    assert first == "推理中"

    overlay._last_live_summary = {
        "status": "ok",
        "reference": {"balanced": "456,000"},
    }
    updating = module.AhmadTkOverlay._inference_status_text(overlay)
    assert updating == first


def test_ahmad_overlay_defers_live_summary_while_dragging() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._drag_active = True
    overlay._resize_active = False
    overlay._deferred_live_apply = None
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._last_live_snapshot = {}
    overlay._last_live_summary = {}
    overlay.render = lambda data: None
    overlay.render_standby = lambda data: None
    overlay._should_reset_manual_for_summary = lambda summary: False
    overlay._auto_sync_manual_inputs = lambda summary: None

    snapshot = {"session_id": "2404:defer"}
    summary = {"status": "ok", "context": {"session_id": "2404:defer"}}

    overlay._apply_live_summary(snapshot, summary)
    assert overlay._deferred_live_apply == {"snapshot": snapshot, "summary": summary}
    assert overlay._last_live_summary == {}

    overlay._drag_active = False
    overlay._flush_deferred_live_summary()
    assert overlay._deferred_live_apply is None
    assert overlay._last_live_summary == summary


def test_ahmad_live_summary_isolates_pet_celebration_failure() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    rendered: list[dict[str, Any]] = []
    overlay._act_ready = False
    overlay._drag_active = False
    overlay._resize_active = False
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._last_live_snapshot = {}
    overlay._last_live_summary = {}
    overlay._last_summary = {}
    overlay._did_settle_after_content = True
    overlay._ref_cache_signature = ("old",)
    overlay._ref_cache_summary = {"status": "ok"}
    overlay._ref_cache_snapshot = {"session_id": "old"}
    overlay.render = lambda data: rendered.append(data)
    overlay.render_standby = lambda data: rendered.append(data)
    overlay._should_reset_manual_for_summary = lambda summary: False
    overlay._auto_sync_manual_inputs = lambda summary: None

    def _raise_pet_error(snapshot: dict[str, Any]) -> bool:
        raise OverflowError(f"malformed Pet input: {snapshot['session_id']}")

    overlay._maybe_play_pet_settlement_reaction = _raise_pet_error
    snapshot = {"phase": "settled", "session_id": "2201:pet-error"}
    summary = {
        "status": "ok",
        "context": {"phase": "settled", "session_id": "2201:pet-error"},
    }

    overlay._apply_live_summary(snapshot, summary)

    assert rendered == [summary]
    assert overlay._last_live_snapshot == snapshot
    assert overlay._last_live_summary == summary
    assert overlay._last_summary == summary
    assert overlay._ref_cache_signature is None
    assert overlay._ref_cache_summary is None
    assert overlay._ref_cache_snapshot is None


def test_ahmad_refresh_coalesces_summary_worker_to_latest_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.after_calls: list[tuple[int, object]] = []

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text('{"file": "snapshot"}', encoding="utf-8")
    rendered: list[dict] = []
    summarize_calls: list[str] = []

    def _summarize(snapshot: dict, snapshot_path: Path) -> dict:
        session_id = snapshot.get("session_id", "?")
        summarize_calls.append(str(session_id))
        if session_id == "2404:slow":
            time.sleep(0.05)
        return {
            "status": "ok",
            "updated_at_text": "12:34:56",
            "context": {"phase": "bidding", "session_id": session_id},
            "reference": {},
            "evidence": {},
            "red": {},
            "ahmed_ref": {"evidence": {}},
            "diagnostics": {"rare_signals": {}},
            "minimap": {},
            "truth": {"available": False},
        }

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = ()
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = snapshot_path
    overlay.diagnostic_profile = "portable"
    overlay._last_signature = None
    overlay._last_inference_signature = None
    overlay._last_bid_signature = ()
    overlay._last_capture_status_signature = None
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay._last_live_snapshot = {}
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = False
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._summary_worker_pending = None
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_worker_seq = 0
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_live_session_id = ""
    overlay.render = lambda data: rendered.append(data)
    overlay.render_standby = lambda data: rendered.append(data)
    overlay.render_missing = lambda message: None
    overlay._record_ui_health = lambda row: None
    overlay._capture_status = lambda: {}
    overlay._should_reset_manual_for_summary = lambda summary: False
    overlay._auto_sync_manual_inputs = lambda summary: None

    def _apply_live_summary(snapshot: dict, summary: dict) -> None:
        overlay._last_live_snapshot = snapshot
        overlay._last_live_summary = summary
        overlay._last_summary = summary
        overlay.render(summary)

    overlay._apply_live_summary = _apply_live_summary
    signature_state = {"value": 0}

    def _signature() -> tuple[int, int]:
        return (signature_state["value"], 0)

    overlay._snapshot_signature = _signature

    read_queue: list[dict] = [
        {"session_id": "2404:slow"},
        {"session_id": "2404:latest"},
    ]

    def _read_json(path: Path) -> dict:
        if read_queue:
            signature_state["value"] += 1
            return read_queue.pop(0)
        return {"session_id": "2404:latest"}

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: False)
    monkeypatch.setattr(module, "_read_json", _read_json)
    # Process-pool path runs summarize_snapshot in a subprocess (bypasses this mock); test the shared
    # worker-lifecycle logic via the mockable in-thread worker.
    monkeypatch.setenv("HERO_REF_DISABLE_PROCESS_ENGINE", "1")
    monkeypatch.setattr(module, "summarize_snapshot", _summarize)

    module.AhmadTkOverlay.refresh(overlay)
    module.AhmadTkOverlay.refresh(overlay)

    for _ in range(100):
        module.AhmadTkOverlay.refresh(overlay)
        if (
            not overlay._summary_worker_running
            and overlay._summary_worker_pending is None
            and overlay._last_live_summary.get("context", {}).get("session_id")
            == "2404:latest"
        ):
            break
        time.sleep(0.01)

    assert overlay._last_live_summary["context"]["session_id"] == "2404:latest"
    assert summarize_calls[-1] == "2404:latest"
    assert "2404:slow" in summarize_calls


def test_ahmad_refresh_skips_stale_worker_result_when_pending_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def after(self, interval: int, callback: object) -> None:
            return None

    snapshot_path = tmp_path / "latest_snapshot.json"
    rendered_sessions: list[str] = []

    def _summarize(snapshot: dict, snapshot_path: Path) -> dict:
        session_id = str(snapshot.get("session_id", "?"))
        if session_id == "2404:slow":
            time.sleep(0.06)
        return {
            "status": "ok",
            "updated_at_text": "12:34:56",
            "context": {"phase": "bidding", "session_id": session_id},
            "reference": {},
            "evidence": {},
            "red": {},
            "ahmed_ref": {"evidence": {}},
            "diagnostics": {"rare_signals": {}},
            "minimap": {},
            "truth": {"available": False},
        }

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = ()
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = snapshot_path
    overlay.diagnostic_profile = "portable"
    overlay._last_signature = None
    overlay._last_inference_signature = None
    overlay._last_bid_signature = ()
    overlay._last_capture_status_signature = None
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    overlay._last_summary = {}
    overlay._last_live_summary = {}
    overlay._last_live_snapshot = {}
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = False
    overlay._summary_worker_seq = 0
    overlay._summary_worker_signature = None
    overlay._summary_worker_pending = None
    overlay._manual_result_queue = queue.Queue()
    overlay._manual_worker_running = False
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay.render = lambda data: rendered_sessions.append(
        str(data.get("context", {}).get("session_id"))
    )
    overlay.render_standby = lambda data: None
    overlay.render_missing = lambda message: None
    overlay._record_ui_health = lambda row: None
    overlay._capture_status = lambda: {}
    overlay._should_reset_manual_for_summary = lambda summary: False
    overlay._auto_sync_manual_inputs = lambda summary: None

    def _apply_live_summary(snapshot: dict, summary: dict) -> None:
        overlay._last_live_snapshot = snapshot
        overlay._last_live_summary = summary
        overlay._last_summary = summary
        overlay.render(summary)

    overlay._apply_live_summary = _apply_live_summary
    signature_state = {"value": 0}

    def _signature() -> tuple[int, int]:
        return (signature_state["value"], 0)

    overlay._snapshot_signature = _signature

    read_queue = [{"session_id": "2404:slow"}, {"session_id": "2404:latest"}]

    def _read_json(path: Path) -> dict:
        if read_queue:
            signature_state["value"] += 1
            return read_queue.pop(0)
        return {"session_id": "2404:latest"}

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: False)
    monkeypatch.setattr(module, "_read_json", _read_json)
    # Process-pool path runs summarize_snapshot in a subprocess (bypasses this mock); test the shared
    # worker-lifecycle logic via the mockable in-thread worker.
    monkeypatch.setenv("HERO_REF_DISABLE_PROCESS_ENGINE", "1")
    monkeypatch.setattr(module, "summarize_snapshot", _summarize)

    module.AhmadTkOverlay.refresh(overlay)
    module.AhmadTkOverlay.refresh(overlay)

    for _ in range(120):
        module.AhmadTkOverlay.refresh(overlay)
        if (
            not overlay._summary_worker_running
            and overlay._summary_worker_pending is None
            and overlay._last_live_summary.get("context", {}).get("session_id")
            == "2404:latest"
        ):
            break
        time.sleep(0.01)

    assert overlay._last_live_summary["context"]["session_id"] == "2404:latest"
    assert rendered_sessions[-1] == "2404:latest"


def test_ahmad_refresh_freezes_view_while_manual_edit_is_open(monkeypatch, tmp_path: Path) -> None:
    module = _ahmad_overlay_module()

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def destroy(self) -> None:
            self.destroyed = True

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text('{"file": "snapshot"}', encoding="utf-8")
    render_calls: list[dict] = []
    standby_calls: list[dict] = []
    missing_calls: list[str] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = ()
    overlay._exit_cleanup_done = False
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay.snapshot_path = snapshot_path
    overlay.diagnostic_profile = "portable"
    overlay._last_signature = (0, 0)
    overlay._last_inference_signature = None
    overlay._last_bid_signature = ()
    overlay._last_capture_status_signature = None
    overlay._ref_cache_signature = None
    overlay._ref_cache_summary = None
    overlay._ref_cache_snapshot = None
    overlay._last_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "2404:old"},
    }
    overlay._last_live_summary = {
        "status": "ok",
        "context": {"phase": "bidding", "session_id": "2404:old"},
    }
    overlay._manual_active = False
    overlay._manual_edit_enabled = True
    overlay._manual_settlement_edit_unlocked = False
    overlay._manual_snapshot = {}
    overlay._manual_summary = {}
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_programmatic_update = False
    overlay._manual_live_session_id = ""
    overlay.render = lambda data: render_calls.append(data)
    overlay.render_standby = lambda data: standby_calls.append(data)
    overlay.render_missing = lambda message: missing_calls.append(message)
    overlay._set_manual_settlement_button_state = lambda: None

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: False)
    monkeypatch.setattr(
        module,
        "summarize_snapshot",
        lambda snapshot, snapshot_path: {
            "status": "ok",
            "updated_at_text": "12:34:56",
            "context": {"phase": "bidding", "session_id": "2404:new"},
            "reference": {},
            "evidence": {},
            "red": {},
            "ahmed_ref": {"evidence": {}},
            "diagnostics": {"rare_signals": {}},
            "minimap": {},
            "truth": {"available": False},
        },
    )

    module.AhmadTkOverlay.refresh(overlay)

    assert render_calls == []
    assert standby_calls == []
    assert missing_calls == []
    assert overlay._last_live_summary["context"]["session_id"] == "2404:new"
    assert overlay._last_summary["context"]["session_id"] == "2404:old"
    assert overlay.root.after_calls == [(1000, overlay.refresh)]


def test_ahmad_overlay_user_close_runs_exit_cleanup_once(monkeypatch, tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    cleanup_calls: list[tuple[tuple[int, ...], tuple[Path, ...]]] = []
    destroyed: list[bool] = []

    monkeypatch.setattr(
        module,
        "_cleanup_exit_targets",
        lambda pids, lock_paths: cleanup_calls.append((tuple(pids), tuple(lock_paths))),
    )
    overlay = object.__new__(module.AhmadTkOverlay)
    lock_path = tmp_path / "monitor.lock"
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._stop_pids_on_exit = (123,)
    overlay._cleanup_lock_paths = (lock_path,)
    overlay.keep_monitor_on_close = False
    overlay._exit_cleanup_done = False
    overlay._hide_minimap_popup = lambda: None
    overlay._hide_pinned_minimap = lambda: None
    overlay.root = SimpleNamespace(destroy=lambda: destroyed.append(True))

    module.AhmadTkOverlay._on_user_close(overlay)
    module.AhmadTkOverlay._run_exit_cleanup(overlay)

    assert cleanup_calls == [((123,), (lock_path,))]
    assert destroyed == [True]


@pytest.mark.parametrize("game_state", [True, None])
def test_ahmad_overlay_user_close_preserves_wss_monitor_while_game_may_be_active(
    monkeypatch,
    tmp_path: Path,
    game_state: bool | None,
) -> None:
    module = _ahmad_overlay_module()
    cleanup_calls: list[tuple[tuple[int, ...], tuple[Path, ...]]] = []
    destroyed: list[bool] = []
    monkeypatch.setattr(
        module,
        "_named_process_running_state",
        lambda _name: game_state,
    )
    monkeypatch.setattr(
        module,
        "_cleanup_exit_targets",
        lambda pids, lock_paths, **_kwargs: cleanup_calls.append(
            (tuple(pids), tuple(lock_paths))
        ),
    )

    lock_path = tmp_path / "monitor.lock"
    lock_path.write_text('{"pid": 123}', encoding="utf-8")
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._stop_pids_on_exit = (123,)
    overlay._cleanup_lock_paths = (lock_path,)
    overlay.keep_monitor_on_close = False
    overlay.protect_monitor_while_game_running = True
    overlay._exit_cleanup_done = False
    overlay._hide_minimap_popup = lambda: None
    overlay._hide_pinned_minimap = lambda: None
    overlay._save_ui_prefs_if_enabled = lambda: None
    overlay._destroy_pet_companion = lambda: None
    overlay.root = SimpleNamespace(
        withdraw=lambda: None,
        update_idletasks=lambda: None,
        destroy=lambda: destroyed.append(True),
    )

    module.AhmadTkOverlay._on_user_close(overlay)

    assert cleanup_calls == []
    assert lock_path.exists()
    assert destroyed == [True]


def test_ahmad_overlay_user_close_reclaims_wss_monitor_after_game_exits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    cleanup_calls: list[tuple[tuple[int, ...], tuple[Path, ...]]] = []
    monkeypatch.setattr(module, "_named_process_running_state", lambda _name: False)
    monkeypatch.setattr(
        module,
        "_cleanup_exit_targets",
        lambda pids, lock_paths, **_kwargs: cleanup_calls.append(
            (tuple(pids), tuple(lock_paths))
        ),
    )

    lock_path = tmp_path / "monitor.lock"
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._stop_pids_on_exit = (123,)
    overlay._cleanup_lock_paths = (lock_path,)
    overlay.keep_monitor_on_close = False
    overlay.protect_monitor_while_game_running = True
    overlay._exit_cleanup_done = False
    overlay._hide_minimap_popup = lambda: None
    overlay._hide_pinned_minimap = lambda: None
    overlay._save_ui_prefs_if_enabled = lambda: None
    overlay._destroy_pet_companion = lambda: None
    overlay.root = SimpleNamespace(
        withdraw=lambda: None,
        update_idletasks=lambda: None,
        destroy=lambda: None,
    )

    module.AhmadTkOverlay._on_user_close(overlay)

    assert overlay.keep_monitor_on_close is False
    assert cleanup_calls == [((123,), (lock_path,))]


def test_ahmad_cleanup_exit_targets_terminates_unique_pids_and_removes_locks(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    lock_path = tmp_path / "monitor.lock"
    lock_path.write_text('{"pid": 123}', encoding="utf-8")
    terminated: list[int] = []

    module._cleanup_exit_targets(
        [123, 123, 456],
        [lock_path],
        terminate_fn=terminated.append,
    )

    assert terminated == [123, 456]
    assert not lock_path.exists()


def test_ahmad_run_exit_cleanup_falls_back_to_monitor_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    lock_path = snapshot_path.parent / "monitor.lock"
    lock_path.write_text('{"pid": 4321}', encoding="utf-8")
    cleanup_calls: list[tuple[tuple[int, ...], tuple[Path, ...]]] = []
    monkeypatch.setattr(
        module,
        "_cleanup_exit_targets",
        lambda pids, lock_paths, **kwargs: cleanup_calls.append(
            (tuple(pids), tuple(lock_paths))
        ),
    )

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._stop_pids_on_exit = ()
    overlay._cleanup_lock_paths = ()
    overlay.keep_monitor_on_close = False
    overlay._exit_cleanup_done = False

    module.AhmadTkOverlay._run_exit_cleanup(overlay)

    assert cleanup_calls == [((4321,), (lock_path,))]


def test_ahmad_run_exit_cleanup_skips_when_keep_monitor_on_close(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    lock_path = snapshot_path.parent / "monitor.lock"
    lock_path.write_text('{"pid": 4321}', encoding="utf-8")
    cleanup_calls: list[tuple[tuple[int, ...], tuple[Path, ...]]] = []
    monkeypatch.setattr(
        module,
        "_cleanup_exit_targets",
        lambda pids, lock_paths, **kwargs: cleanup_calls.append(
            (tuple(pids), tuple(lock_paths))
        ),
    )

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._stop_pids_on_exit = ()
    overlay._cleanup_lock_paths = ()
    overlay.keep_monitor_on_close = True
    overlay._exit_cleanup_done = False

    module.AhmadTkOverlay._run_exit_cleanup(overlay)

    assert cleanup_calls == []
    assert lock_path.exists()


def test_ahmad_refresh_runs_exit_cleanup_on_watched_pid_exit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    cleanup_calls: list[bool] = []

    class Root:
        def __init__(self) -> None:
            self.destroyed = False
            self.after_calls: list[tuple[int, object]] = []

        def destroy(self) -> None:
            self.destroyed = True

        def after(self, interval: int, callback: object) -> None:
            self.after_calls.append((interval, callback))

    monkeypatch.setattr(module, "_watched_pid_exited", lambda _pids: True)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.root = Root()
    overlay.interval_ms = 1000
    overlay.exit_when_pids = (123,)
    overlay._exit_cleanup_done = False
    overlay.keep_monitor_on_close = False
    overlay._stop_pids_on_exit = ()
    overlay._cleanup_lock_paths = ()
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "portable"
    overlay._last_ui_heartbeat_at = time.monotonic()
    overlay._last_ui_stall_bucket = 0
    overlay._run_exit_cleanup = lambda: cleanup_calls.append(True)

    module.AhmadTkOverlay.refresh(overlay)

    assert cleanup_calls == [True]
    assert overlay.root.destroyed is True


def test_pre_settlement_clone_strips_settlement_grade_evidence() -> None:
    # Regression: the settled-view "估价" card must reproduce the last live
    # (pre-settlement) estimate, not re-estimate using settlement truth. The
    # clone used for that reconstruction must drop settlement-grade evidence
    # (notably ui_contract.constraints, which carries exact settlement counts),
    # otherwise the engine reads it back and inflates the displayed estimate.
    server = _ahmad_server_module()
    snapshot = {
        "phase": "settled",
        "inventory_count": 28,
        "inventory_cells": 75,
        "known_value_sum": 576647,
        "final_quality_counts": "q1=6;q3=12;q4=4;q5=3;q6=3",
        "final_q6_count": 3,
        "final_decision_value": 576647,
        "model_eval": {"leak": "shadow"},
        "minimap_grid_items": [{"quality": 6}],
        "structured_ref_inputs": {"counts": {"q5": 3}, "field_updates": []},
        "ui_contract": {
            "phase": "settled",
            "context": {"phase": "settled"},
            "truth": {"available": True, "total_value": 576647},
            "constraints": {"counts": {"q6": 3, "q5": 3}},
            "minimap": {"items": [{"quality": 6}]},
            "posterior": {"q6": 3},
        },
    }

    cloned = server._clone_as_pre_settlement_snapshot(snapshot)

    assert cloned["phase"] == "bidding"
    assert cloned["ui_contract"]["context"]["phase"] == "bidding"
    assert cloned["ui_contract"]["truth"]["available"] is False
    assert "constraints" not in cloned["ui_contract"]
    assert "minimap" not in cloned["ui_contract"]
    assert not any(key.startswith("final_") for key in cloned)
    for key in ("inventory_count", "inventory_cells", "known_value_sum", "minimap_grid_items", "model_eval"):
        assert key not in cloned
    # Pre-settlement live evidence stream must be preserved.
    assert cloned["structured_ref_inputs"] == snapshot["structured_ref_inputs"]
    # Source snapshot is untouched (clone is deep).
    assert snapshot["ui_contract"]["constraints"] == {"counts": {"q6": 3, "q5": 3}}
    assert snapshot["final_q6_count"] == 3


def test_quote_receipt_is_sessionless_and_binds_input_and_result() -> None:
    server = _ahmad_server_module()
    overlay = _ahmad_overlay_module()
    snapshot = {
        "phase": "bidding",
        "formal_mode": "v3_practical",
        "observed_round": 4,
        "file": "private/source/path.json",
        "player_id": 887766,
        "structured_ref_inputs": {"quality_cells": {"q5": 30}},
        "public_info_rows": [
            {
                "info_id": 200010,
                "map_id": 2501,
                "value": 14,
                "value_field": "field_7",
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2501,
                "round": 4,
                "action_round": 4,
                "phase": "bidding",
                "session_id": "private-session-A",
            }
        },
    }
    result = {
        "status": "count_prior",
        "source": "ref_v0",
        "notes": ["action_100107_q5_cells"],
        "conservative": 600_000,
        "balanced": 700_000,
        "aggressive": 800_000,
        "value_p25": 620_000,
        "value_p50": 720_000,
        "value_p75": 820_000,
        "combo_count": 7,
    }

    receipt = server._quote_receipt(
        snapshot,
        result,
        source="live_ref_result",
        decision_range="600,000 / 700,000 / 800,000",
        value_range="620,000 / 720,000 / 820,000",
    )

    assert receipt["schema"] == "hero_ref_quote_receipt_v1"
    assert receipt["round"] == 4
    assert receipt["action_round"] == 4
    assert receipt["observed_round"] == 4
    assert receipt["phase"] == "bidding"
    assert receipt["formal_mode"] == "v3_practical"
    assert receipt["balanced"] == 700_000
    assert set(receipt) == overlay._QUOTE_RECEIPT_REQUIRED_KEYS
    assert len(receipt["input_fingerprint"]) == 64
    assert len(receipt["result_identity"]) == 64
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "private-session-A" not in serialized
    assert "private/source/path.json" not in serialized
    assert "887766" not in serialized

    other_identity = copy.deepcopy(snapshot)
    other_identity["file"] = "other/private/path.json"
    other_identity["player_id"] = 112233
    other_identity["ui_contract"]["context"]["session_id"] = "private-session-B"
    same_semantics = server._quote_receipt(
        other_identity,
        result,
        source="live_ref_result",
        decision_range=receipt["decision_range"],
        value_range=receipt["value_range"],
    )
    assert same_semantics["input_fingerprint"] == receipt["input_fingerprint"]
    assert same_semantics["result_identity"] == receipt["result_identity"]

    changed_input = copy.deepcopy(snapshot)
    changed_input["public_info_rows"][0]["value"] = 15
    changed_input_receipt = server._quote_receipt(
        changed_input,
        result,
        source="live_ref_result",
        decision_range=receipt["decision_range"],
        value_range=receipt["value_range"],
    )
    assert changed_input_receipt["input_fingerprint"] != receipt["input_fingerprint"]

    changed_result = dict(result, balanced=700_001)
    changed_result_receipt = server._quote_receipt(
        snapshot,
        changed_result,
        source="live_ref_result",
        decision_range=receipt["decision_range"],
        value_range=receipt["value_range"],
    )
    assert changed_result_receipt["result_identity"] != receipt["result_identity"]

    incomplete_input = copy.deepcopy(snapshot)
    incomplete_input.pop("observed_round")
    assert server._quote_receipt(
        incomplete_input,
        result,
        source="live_ref_result",
        decision_range=receipt["decision_range"],
        value_range=receipt["value_range"],
    ) == {}


def _coherent_quote_receipt() -> dict[str, object]:
    return {
        "schema": "hero_ref_quote_receipt_v1",
        "source": "live_ref_result",
        "round": 4,
        "action_round": 4,
        "observed_round": 4,
        "phase": "bidding",
        "formal_mode": "v3_practical",
        "input_fingerprint_scope": "ref_inputs_without_session_v1",
        "input_fingerprint": "a" * 64,
        "result_identity": "b" * 64,
        "result_status": "count_prior",
        "result_combo_count": 7,
        "balanced": 640_000,
        "decision_range": "500,000 / 640,000 / 800,000",
        "value_range": "550,000 / 690,000 / 850,000",
    }


def _quote_identity_for_receipt(receipt: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "hero_ref_quote_identity_v1",
        "source": receipt["source"],
        "input_fingerprint_scope": receipt["input_fingerprint_scope"],
        "input_fingerprint": receipt["input_fingerprint"],
        "result_identity": receipt["result_identity"],
    }


def _coherent_live_quote_summary(
    receipt: dict[str, object],
    *,
    session_id: str = "S1",
) -> dict[str, object]:
    return {
        "context": {
            "session_id": session_id,
            "phase": receipt["phase"],
            "round": receipt["round"],
            "action_round": receipt["action_round"],
            "observed_round": receipt["observed_round"],
            "formal_mode": receipt["formal_mode"],
        },
        "reference": {
            "conservative": "100,000",
            "balanced": f"{int(receipt['balanced']):,}",
            "aggressive": "800,000",
            "decision_range": receipt["decision_range"],
            "raw_value_range": receipt["value_range"],
            "total_value_range": receipt["value_range"],
        },
        "evidence": {
            "ref_status": receipt["result_status"],
            "ref_combo_count": str(receipt["result_combo_count"]),
        },
        "quote_receipt": copy.deepcopy(receipt),
        "quote_identity": _quote_identity_for_receipt(receipt),
    }


def test_settlement_estimate_override_replays_last_pre_settlement_balanced() -> None:
    """Settled 估价 card shows the last on-screen 参考, not the divergent re-run estimate."""
    overlay = _ahmad_overlay_module()

    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override

    # Pre-settlement rounds cache the 参考 (balanced) for this session.
    receipt = _coherent_quote_receipt()
    identity = _quote_identity_for_receipt(receipt)
    override(fake, _coherent_live_quote_summary(receipt))
    assert fake._last_pre_settlement_estimate == (
        "S1",
        640000.0,
        receipt,
        identity,
    )

    # Settlement rewrites 估价 to the cached 参考 and 差值 to 结算 - 估价.
    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "diagnostics": {
            "performance": {"settlement_estimate_state": "pending"}
        },
        "reference": {
            "conservative": "385,181",
            "balanced": "440,000",
            "aggressive": "+54,545",
            "decision_range": "300,000 / 385,181 / 500,000",
            "raw_value_range": "320,000 / 400,000 / 520,000",
            "total_value_range": "320,000 / 400,000 / 520,000",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "640,000"
    assert settled["reference"]["aggressive"] == "-200,000"
    assert settled["reference"]["decision_range"] == "500,000 / 640,000 / 800,000"
    assert settled["reference"]["raw_value_range"] == "550,000 / 690,000 / 850,000"
    assert settled["reference"]["total_value_range"] == "550,000 / 690,000 / 850,000"
    assert settled["quote_receipt"] == dict(
        receipt,
        settlement_review_applied=True,
    )
    assert settled["quote_identity"] == identity
    assert (
        settled["diagnostics"]["performance"]["settlement_estimate_state"]
        == "cached_receipt"
    )
    assert fake._last_pre_settlement_estimate == (
        "S1",
        640000.0,
        receipt,
        identity,
    )
    assert overlay._valid_quote_receipt(settled["quote_receipt"]) is False

    # A different session does not borrow the stale cache.
    other = {
        "context": {"session_id": "S2", "phase": "settled"},
        "truth": {"available": True},
        "reference": {"conservative": "385,181", "balanced": "440,000", "aggressive": "+54,545"},
    }
    override(fake, other)
    assert other["reference"]["conservative"] == "385,181"


@pytest.mark.parametrize("marker", [True, False])
def test_live_settlement_marker_is_invalid_and_clears_same_session_cache(
    marker: bool,
) -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    override(fake, _coherent_live_quote_summary(_coherent_quote_receipt()))
    assert fake._last_pre_settlement_estimate is not None

    current = dict(
        _coherent_quote_receipt(),
        round=5,
        action_round=5,
        observed_round=5,
        balanced=2_040_000,
        decision_range="1,800,000 / 2,040,000 / 2,200,000",
        value_range="1,900,000 / 2,100,000 / 2,300,000",
        settlement_review_applied=marker,
    )
    live = _coherent_live_quote_summary(current)

    assert overlay._valid_quote_receipt(current) is False
    assert overlay._valid_quote_receipt(current, summary=live) is False
    override(fake, live)
    assert fake._last_pre_settlement_estimate is None

    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "626,866",
            "balanced": "1,529,263",
            "aggressive": "+902,397",
            "decision_range": "549,560 / 626,866 / 714,938",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "626,866"
    assert "quote_receipt" not in settled


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty",
        "zero",
        "bool",
        "string_subclass",
        "root_only",
        "whitespace",
    ],
)
def test_live_receipt_invalid_context_session_clears_cache_and_preserves_panel_clone(
    case: str,
) -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    receipt = _coherent_quote_receipt()

    override(fake, _coherent_live_quote_summary(receipt))
    assert fake._last_pre_settlement_estimate is not None

    live = _coherent_live_quote_summary(receipt)
    if case == "missing":
        live["context"].pop("session_id")
    elif case == "empty":
        live["context"]["session_id"] = ""
    elif case == "zero":
        live["context"]["session_id"] = 0
    elif case == "bool":
        live["context"]["session_id"] = True
    elif case == "string_subclass":
        class SessionId(str):
            pass

        live["context"]["session_id"] = SessionId("S1")
    elif case == "root_only":
        live["context"].pop("session_id")
        live["session_id"] = "S1"
    elif case == "whitespace":
        live["context"]["session_id"] = " "
    else:  # pragma: no cover - selector table is exhaustive
        raise AssertionError(case)

    assert overlay._valid_quote_receipt(receipt, summary=live) is False
    override(fake, live)
    assert fake._last_pre_settlement_estimate is None

    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "626,866",
            "balanced": "1,529,263",
            "aggressive": "+902,397",
            "decision_range": "549,560 / 626,866 / 714,938",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "626,866"
    assert "quote_receipt" not in settled


def test_settlement_override_rejects_incomplete_producer_receipt() -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    override(fake, _coherent_live_quote_summary(_coherent_quote_receipt()))
    assert fake._last_pre_settlement_estimate is not None

    current = dict(
        _coherent_quote_receipt(),
        round=5,
        action_round=5,
        observed_round=5,
        balanced=2_040_000,
        decision_range="1,800,000 / 2,040,000 / 2,200,000",
        value_range="1,900,000 / 2,100,000 / 2,300,000",
    )
    live = _coherent_live_quote_summary(current)
    incomplete = dict(current)
    for key in (
        "source",
        "input_fingerprint_scope",
        "result_status",
        "result_combo_count",
        "balanced",
    ):
        incomplete.pop(key)
    live["quote_receipt"] = incomplete

    assert overlay._valid_quote_receipt(incomplete) is False
    override(fake, live)
    assert fake._last_pre_settlement_estimate is None

    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "626,866",
            "balanced": "1,529,263",
            "aggressive": "+902,397",
            "decision_range": "549,560 / 626,866 / 714,938",
            "raw_value_range": "648,974 / 697,441 / 745,908",
            "total_value_range": "648,974 / 697,441 / 745,908",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "626,866"
    assert "quote_receipt" not in settled


def test_settlement_override_rejects_receipt_mismatched_to_current_summary() -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    override(fake, _coherent_live_quote_summary(_coherent_quote_receipt()))

    current = dict(
        _coherent_quote_receipt(),
        round=5,
        action_round=5,
        observed_round=5,
        balanced=2_040_000,
        decision_range="1,800,000 / 2,040,000 / 2,200,000",
        value_range="1,900,000 / 2,100,000 / 2,300,000",
    )
    live = _coherent_live_quote_summary(current)
    mismatch = dict(
        current,
        round=4,
        action_round=4,
        observed_round=4,
        formal_mode="v2",
        balanced=700_000,
        decision_range="600,000 / 700,000 / 800,000",
        value_range="650,000 / 750,000 / 850,000",
    )
    live["quote_receipt"] = mismatch
    live["quote_identity"] = _quote_identity_for_receipt(mismatch)

    assert overlay._valid_quote_receipt(mismatch) is True
    assert overlay._valid_quote_receipt(mismatch, summary=live) is False
    override(fake, live)
    assert fake._last_pre_settlement_estimate is None

    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "626,866",
            "balanced": "1,529,263",
            "aggressive": "+902,397",
            "decision_range": "549,560 / 626,866 / 714,938",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "626,866"
    assert "quote_receipt" not in settled


@pytest.mark.parametrize(
    "case",
    [
        *(f"missing_receipt:{key}" for key in _coherent_quote_receipt()),
        "missing_receipt",
        "malformed_receipt_extra",
        "malformed_receipt_round_bool",
        "missing_reference",
        "missing_evidence",
        "missing_identity",
        "missing_context_round",
        "mismatch_source",
        "mismatch_phase",
        "mismatch_round",
        "mismatch_action_round",
        "mismatch_observed_round",
        "mismatch_formal_mode",
        "mismatch_balanced",
        "mismatch_decision_range",
        "mismatch_value_range",
        "mismatch_result_status",
        "mismatch_result_combo_count",
        "mismatch_input_identity",
        "mismatch_result_identity",
    ],
)
def test_same_session_receipt_or_summary_mismatch_atomically_clears_cache(
    case: str,
) -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    override(fake, _coherent_live_quote_summary(_coherent_quote_receipt()))
    assert fake._last_pre_settlement_estimate is not None

    receipt = dict(
        _coherent_quote_receipt(),
        round=5,
        action_round=5,
        observed_round=5,
        balanced=2_040_000,
        decision_range="1,800,000 / 2,040,000 / 2,200,000",
        value_range="1,900,000 / 2,100,000 / 2,300,000",
    )
    live = _coherent_live_quote_summary(receipt)
    if case.startswith("missing_receipt:"):
        live["quote_receipt"].pop(case.split(":", 1)[1])
    elif case == "missing_receipt":
        live.pop("quote_receipt")
    elif case == "malformed_receipt_extra":
        live["quote_receipt"]["unowned"] = "field"
    elif case == "malformed_receipt_round_bool":
        live["quote_receipt"]["round"] = True
    elif case == "missing_reference":
        live.pop("reference")
    elif case == "missing_evidence":
        live.pop("evidence")
    elif case == "missing_identity":
        live.pop("quote_identity")
    elif case == "missing_context_round":
        live["context"].pop("round")
    elif case == "mismatch_source":
        live["quote_receipt"]["source"] = "settlement_clone"
        live["quote_identity"]["source"] = "settlement_clone"
    elif case == "mismatch_phase":
        live["quote_receipt"]["phase"] = "reading"
    elif case == "mismatch_round":
        live["quote_receipt"]["round"] = 4
    elif case == "mismatch_action_round":
        live["quote_receipt"]["action_round"] = 4
    elif case == "mismatch_observed_round":
        live["quote_receipt"]["observed_round"] = 4
    elif case == "mismatch_formal_mode":
        live["quote_receipt"]["formal_mode"] = "v2"
    elif case == "mismatch_balanced":
        live["quote_receipt"]["balanced"] = 700_000
    elif case == "mismatch_decision_range":
        live["quote_receipt"]["decision_range"] = "600,000 / 700,000 / 800,000"
    elif case == "mismatch_value_range":
        live["quote_receipt"]["value_range"] = "650,000 / 750,000 / 850,000"
    elif case == "mismatch_result_status":
        live["quote_receipt"]["result_status"] = "ok"
    elif case == "mismatch_result_combo_count":
        live["quote_receipt"]["result_combo_count"] = 8
    elif case == "mismatch_input_identity":
        live["quote_receipt"]["input_fingerprint"] = "c" * 64
    elif case == "mismatch_result_identity":
        live["quote_receipt"]["result_identity"] = "d" * 64
    else:  # pragma: no cover - selector table is exhaustive
        raise AssertionError(case)

    override(fake, live)
    assert fake._last_pre_settlement_estimate is None


def test_settlement_estimate_override_refuses_unreceipted_live_quote() -> None:
    """A missing receipt must keep the reproducible Panel settlement-clone quote."""
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=(
            "S1",
            1_900_000.0,
            {
                "schema": "hero_ref_quote_receipt_v1",
                "phase": "bidding",
                "round": 4,
                "formal_mode": "v3_practical",
                "input_fingerprint": "a" * 64,
                "result_identity": "b" * 64,
                "decision_range": "1,700,000 / 1,900,000 / 2,100,000",
                "value_range": "1,800,000 / 2,000,000 / 2,200,000",
            },
        ),
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override

    override(
        fake,
        {
            "context": {"session_id": "S1", "phase": "bidding"},
            "reference": {"balanced": "2,040,000"},
        },
    )
    assert fake._last_pre_settlement_estimate is None

    settled = {
        "context": {"session_id": "S1", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "626,866",
            "balanced": "1,529,263",
            "aggressive": "+902,397",
            "decision_range": "549,560 / 626,866 / 714,938",
        },
        "quote_receipt": {
            "schema": "hero_ref_quote_receipt_v1",
            "phase": "bidding",
            "round": 5,
            "formal_mode": "v3_practical",
            "input_fingerprint": "c" * 64,
            "result_identity": "d" * 64,
            "decision_range": "549,560 / 626,866 / 714,938",
            "value_range": "648,974 / 697,441 / 745,908",
        },
    }
    override(fake, settled)
    assert settled["reference"]["conservative"] == "626,866"
    assert settled["reference"]["decision_range"] == "549,560 / 626,866 / 714,938"
    assert settled["quote_receipt"]["result_identity"] == "d" * 64


def _product_shaped_unreceipted_live_quote() -> dict[str, Any]:
    return {
        "status": "ok",
        "context": {
            "session_id": "2544:9000000000000057",
            "hero": "maria",
            "map_id": 2544,
            "formal_mode": "v3_practical",
            "is_supported_ref_hero": True,
            "phase": "reading",
            "round": 1,
            "action_round": 1,
            "observed_round": 0,
        },
        "reference": {
            "source": "ref_prior",
            "balanced": "989,902",
            "decision_range": "788,359 / 989,902 / 1,206,443",
            "raw_value_range": "900,000 / 1,050,000 / 1,200,000",
            "total_value_range": "900,000 / 1,050,000 / 1,200,000",
        },
        "quote_receipt": {},
        "quote_identity": {},
    }


def test_settlement_estimate_override_replays_product_shaped_unreceipted_display() -> None:
    """The settled card keeps the actual same-session quote already shown to the user."""
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _last_pre_settlement_display_quote=None,
        _summary_session_id=lambda data: (data.get("context") or {}).get("session_id") or "",
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    override = overlay.AhmadTkOverlay._apply_settlement_estimate_override
    live = _product_shaped_unreceipted_live_quote()

    override(fake, live)
    assert fake._last_pre_settlement_estimate is None
    assert fake._last_pre_settlement_display_quote == (
        "2544:9000000000000057",
        989902.0,
        "788,359 / 989,902 / 1,206,443",
        "900,000 / 1,050,000 / 1,200,000",
    )

    settled = {
        "context": {
            "session_id": "2544:9000000000000057",
            "phase": "settled",
        },
        "truth": {"available": True},
        "diagnostics": {
            "performance": {"settlement_estimate_state": "computed"}
        },
        "reference": {
            "conservative": "298,381",
            "balanced": "720,593",
            "aggressive": "+422,212",
            "decision_range": "190,367 / 298,381 / 419,802",
            "raw_value_range": "218,941 / 346,015 / 488,865",
            "total_value_range": "218,941 / 346,015 / 488,865",
            "note": (
                "Settlement review: top cards show a pre-settlement replay "
                "estimate, final total, and final-minus-estimate delta.; legacy"
            ),
        },
        "quote_receipt": {},
        "quote_identity": {},
    }

    override(fake, settled)

    assert settled["reference"]["conservative"] == "989,902"
    assert settled["reference"]["aggressive"] == "-269,309"
    assert settled["reference"]["decision_range"] == (
        "788,359 / 989,902 / 1,206,443"
    )
    assert settled["reference"]["raw_value_range"] == (
        "900,000 / 1,050,000 / 1,200,000"
    )
    assert settled["reference"]["total_value_range"] == (
        "900,000 / 1,050,000 / 1,200,000"
    )
    assert "last on-screen Hero Ref estimate" in settled["reference"]["note"]
    assert "pre-settlement replay estimate" not in settled["reference"]["note"]
    assert settled["reference"]["note"].endswith("; legacy")
    assert settled["quote_receipt"] == {}
    assert settled["quote_identity"] == {}
    assert (
        settled["diagnostics"]["performance"]["settlement_estimate_state"]
        == "cached_display"
    )

    other_session = {
        "context": {"session_id": "2541:other", "phase": "settled"},
        "truth": {"available": True},
        "reference": {
            "conservative": "1,146,828",
            "balanced": "1,333,728",
            "aggressive": "+186,900",
        },
    }
    override(fake, other_session)
    assert other_session["reference"]["conservative"] == "1,146,828"


@pytest.mark.parametrize(
    "mutation",
    (
        "garbage_decision_range",
        "nan_value_range",
        "decision_middle_mismatch",
        "truth_available_int",
        "broken_receipt",
        "broken_identity",
    ),
)
def test_settlement_display_quote_cache_rejects_malformed_summary(
    mutation: str,
) -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _last_pre_settlement_display_quote=None,
        _is_settlement_summary=lambda _data: False,
    )
    live = _product_shaped_unreceipted_live_quote()
    if mutation == "garbage_decision_range":
        live["reference"]["decision_range"] = "garbage"
    elif mutation == "nan_value_range":
        live["reference"]["raw_value_range"] = "nan / x / -1"
        live["reference"]["total_value_range"] = "nan / x / -1"
    elif mutation == "decision_middle_mismatch":
        live["reference"]["decision_range"] = (
            "788,359 / 999,999 / 1,206,443"
        )
    elif mutation == "truth_available_int":
        live["truth"] = {"available": 1}
    elif mutation == "broken_receipt":
        live["quote_receipt"] = {"schema": "broken"}
    elif mutation == "broken_identity":
        live["quote_identity"] = {"schema": "broken"}
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    overlay.AhmadTkOverlay._apply_settlement_estimate_override(fake, live)

    assert fake._last_pre_settlement_display_quote is None


def test_settlement_display_quote_cache_does_not_override_broken_settled_receipt() -> None:
    overlay = _ahmad_overlay_module()
    fake = SimpleNamespace(
        _last_pre_settlement_estimate=None,
        _last_pre_settlement_display_quote=None,
        _is_settlement_summary=lambda data: (
            (data.get("context") or {}).get("phase") == "settled"
            and bool((data.get("truth") or {}).get("available"))
        ),
    )
    overlay.AhmadTkOverlay._apply_settlement_estimate_override(
        fake,
        _product_shaped_unreceipted_live_quote(),
    )
    assert fake._last_pre_settlement_display_quote is not None
    settled = {
        "context": {
            "session_id": "2544:9000000000000057",
            "phase": "settled",
        },
        "truth": {"available": True},
        "reference": {
            "conservative": "298,381",
            "balanced": "720,593",
            "aggressive": "+422,212",
        },
        "quote_receipt": {"schema": "broken"},
        "quote_identity": {},
    }

    overlay.AhmadTkOverlay._apply_settlement_estimate_override(fake, settled)

    assert settled["reference"]["conservative"] == "298,381"
    assert settled["reference"]["aggressive"] == "+422,212"


def test_ahmad_settled_snapshot_applies_truth_first_without_clone(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Settlement preempts with the deferred-estimate summary, never the heavy clone."""
    module = _ahmad_overlay_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    settled_summary = {"status": "ok", "context": {"phase": "settled"}, "truth": {"available": True}}
    applied: list[tuple[dict, dict]] = []

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True  # simulate an in-flight slow bidding compute
    overlay._summary_worker_seq = 5
    overlay._summary_worker_signature = (3, 3)
    overlay._summary_worker_pending = {"snapshot": {}, "signature": (4, 4), "started_at": 0.0}
    overlay._last_signature = (3, 3)
    overlay._mark_summary_performance = lambda summary, key, start: None
    overlay._apply_live_summary = lambda snap, summ: applied.append((snap, summ))

    calls: list[bool] = []

    def summarize(
        snapshot: dict,
        *,
        snapshot_path: Path,
        defer_settlement_estimate: bool = False,
    ) -> dict:
        calls.append(defer_settlement_estimate)
        return dict(settled_summary)

    monkeypatch.setattr(module, "summarize_snapshot", summarize)

    settled_snapshot = {"phase": "settled", "ui_contract": {"context": {"phase": "settled"}}}
    started = module.AhmadTkOverlay._start_live_summary_worker(
        overlay, snapshot=settled_snapshot, signature=(9, 9), started_at=0.0
    )

    assert started is True
    # in-flight bidding abandoned: seq bumped so its late result is dropped on the seq mismatch
    assert overlay._summary_worker_seq > 5
    assert overlay._summary_worker_running is False
    assert overlay._summary_worker_pending is None
    # Truth-first result applied immediately; no heavy fallback was requested.
    assert applied == [(settled_snapshot, dict(settled_summary))]
    assert calls == [True]
    assert overlay._last_signature == (9, 9)
    assert overlay._summary_result_queue.empty()


def test_ahmad_settled_missing_receipt_applies_truth_before_async_estimate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    applied: list[dict] = []
    inline_calls: list[bool] = []
    submitted: list[tuple[object, tuple, dict]] = []
    futures: list[object] = []

    def summarize(
        snapshot: dict,
        *,
        snapshot_path: Path,
        defer_settlement_estimate: bool = False,
    ) -> dict:
        inline_calls.append(defer_settlement_estimate)
        if not defer_settlement_estimate:
            return {
                "status": "ok",
                "context": {"phase": "settled", "session_id": "S1"},
                "truth": {"available": True, "total_value": 123},
                "reference": {
                    "conservative": "100",
                    "balanced": "123",
                    "aggressive": "+23",
                },
                "diagnostics": {
                    "performance": {"settlement_estimate_state": "computed"}
                },
            }
        return {
            "status": "ok",
            "context": {"phase": "settled", "session_id": "S1"},
            "truth": {"available": True, "total_value": 123},
            "reference": {
                "conservative": "更新中",
                "balanced": "123",
                "aggressive": "-",
            },
            "diagnostics": {
                "performance": {"settlement_estimate_state": "pending"}
            },
        }

    class PendingFuture:
        def __init__(self, function: object, args: tuple, kwargs: dict) -> None:
            self.function = function
            self.args = args
            self.kwargs = kwargs

        def add_done_callback(self, callback: object) -> None:
            self.callback = callback

        def result(self) -> object:
            return self.function(*self.args, **self.kwargs)

        def finish(self) -> None:
            self.callback(self)

    class Executor:
        def submit(self, function: object, *args: object, **kwargs: object) -> PendingFuture:
            submitted.append((function, args, kwargs))
            future = PendingFuture(function, args, kwargs)
            futures.append(future)
            return future

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._summary_result_queue = queue.Queue()
    overlay._summary_worker_running = True
    overlay._summary_worker_seq = 5
    overlay._summary_worker_signature = (3, 3)
    overlay._summary_worker_pending = None
    overlay._summary_worker_mode = "process"
    overlay._summary_worker_executor = None
    overlay._ref_engine_executor_mode = "process"
    overlay._ref_engine_liveness_state = "ready"
    overlay._ref_engine_transient_executor = None
    overlay._exit_cleanup_done = False
    overlay._mark_summary_performance = lambda summary, key, start: None
    overlay._apply_live_summary = lambda snap, summary: applied.append(summary)
    overlay._ensure_ref_engine_executor = lambda: Executor()
    overlay._ensure_ref_engine_liveness_lock = lambda: threading.Lock()
    overlay._record_ui_runtime_status = lambda *_args, **_kwargs: None
    overlay._maybe_start_pending_summary_worker = lambda: None

    monkeypatch.setattr(module, "summarize_snapshot", summarize)
    settled_snapshot = {
        "phase": "settled",
        "ui_contract": {"context": {"phase": "settled", "session_id": "S1"}},
    }
    started = module.AhmadTkOverlay._start_live_summary_worker(
        overlay,
        snapshot=settled_snapshot,
        signature=(9, 9),
        started_at=0.0,
    )

    assert started is True
    assert inline_calls == [True]
    assert applied[0]["truth"]["total_value"] == 123
    assert applied[0]["reference"]["conservative"] == "更新中"
    assert len(submitted) == 1
    assert submitted[0][0] is summarize
    assert submitted[0][1] == (settled_snapshot,)
    assert submitted[0][2] == {"snapshot_path": snapshot_path}
    assert overlay._summary_worker_running is True
    assert overlay._summary_worker_mode == "process"
    trace_seq = overlay._compute_pending_trace_seq
    assert overlay._summary_result_owner_is_closed(
        {
            "compute_monitor_generation": 0,
            "compute_trace_seq": trace_seq,
        }
    ) is False
    assert overlay._summary_result_owner_is_closed(
        {
            "compute_monitor_generation": 1,
            "compute_trace_seq": trace_seq,
        }
    ) is True
    assert overlay._summary_result_superseded_by_compute_trace(
        {
            "compute_monitor_generation": 0,
            "compute_trace_seq": trace_seq - 1,
        }
    ) is True

    futures[0].finish()
    overlay._drain_live_summary_results()

    assert len(applied) == 2
    assert applied[-1]["reference"] == {
        "conservative": "100",
        "balanced": "123",
        "aggressive": "+23",
    }
    assert overlay._compute_pending is False
    assert overlay._compute_terminal_trace_seq == trace_seq


def test_read_json_retries_transient_read_failure(monkeypatch, tmp_path: Path) -> None:
    """跨进程读写竞争(monitor 的 os.replace 换名瞬间, 本进程 open 撞 sharing-violation/瞬时失败)时,
    _read_json 短重试读到完整内容 —— 而非把单次失败当"无快照"让 refresh() 把整个 UI blank 成占位。
    (实测 raven 那局 67/116 tick missing、整局不显示的根因。)"""
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)  # 别真睡, 测试跑快
    p = tmp_path / "latest_snapshot.json"
    p.write_text(json.dumps({"ok": 1}), encoding="utf-8")
    calls = {"n": 0}
    real_read = module.Path.read_text

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("sharing violation (os.replace race)")
        return real_read(self, *a, **k)

    monkeypatch.setattr(module.Path, "read_text", flaky)
    out = module._read_json(p)
    assert out == {"ok": 1}       # 前两次失败后重试读到完整内容
    assert calls["n"] == 3        # 失败 x2 + 成功 x1


def test_read_json_missing_file_returns_empty_fast(monkeypatch, tmp_path: Path) -> None:
    """文件确实不存在 = 真无快照 -> 立即返回 {}, 不浪费重试/不睡(避免真缺失时每 tick 卡几十 ms)。"""
    module = _ahmad_overlay_module()
    slept = {"n": 0}
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: slept.__setitem__("n", slept["n"] + 1))
    out = module._read_json(tmp_path / "does_not_exist.json")
    assert out == {}
    assert slept["n"] == 0        # FileNotFound -> 立即返回


def test_read_json_persistent_failure_returns_empty(monkeypatch, tmp_path: Path) -> None:
    """持续读失败(非 FileNotFound)重试耗尽后返回 {}(有界重试, 不无限循环/不崩)。"""
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)
    p = tmp_path / "latest_snapshot.json"
    p.write_text("{}", encoding="utf-8")

    def always_fail(self, *a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(module.Path, "read_text", always_fail)
    assert module._read_json(p) == {}


def test_recent_settlement_archive_requires_explicit_raw_forensics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # 卡死常由【上一局结算异常】引起；导出必须带上 raw/archive/ 里最近的结算归档(*_reset.json)，
    # 即便当前快照已是新局/空，也只能在明确 raw_forensics 下带入，且只带最新 2 份。
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    snapshot_path = tmp_path / "latest_snapshot.json"
    archive_dir = tmp_path / "raw" / "archive"
    archive_dir.mkdir(parents=True)
    old = archive_dir / "2401_aaa_reset.json"
    mid = archive_dir / "2402_bbb_reset.json"
    new = archive_dir / "2410_ccc_reset.json"
    for p in (old, mid, new):
        p.write_text('{"frames": []}', encoding="utf-8")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(mid, (1_600_000_100, 1_600_000_100))
    os.utime(new, (1_600_000_200, 1_600_000_200))
    snapshot = {
        "schema_version": 1,
        "session_id": "2410:test",
        "hero": "ahmed",
        "map_id": 2410,
        "round": 1,
        "phase": "bidding",
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    support = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "2410:test"}},
        diagnostic_profile="engineering",
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(support) as archive:
        assert not any(name.endswith("_reset.json") for name in archive.namelist())

    raw = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "2410:test"}},
        diagnostic_profile="engineering",
        raw_forensics=True,
        _documents_root_for_test=tmp_path,
        _live_root_for_test=snapshot_path.parent,
    )
    with zipfile.ZipFile(raw) as archive:
        names = set(archive.namelist())
    reset_names = [n for n in names if n.endswith("_reset.json")]
    assert any("2410_ccc_reset.json" in n for n in reset_names), names
    assert any("2402_bbb_reset.json" in n for n in reset_names), names
    assert not any("2401_aaa_reset.json" in n for n in reset_names), names


def test_clamp_rect_into_workarea_pulls_offscreen_window_back() -> None:
    # 详情展开后窗口变高, 靠屏底会导致底部到屏外(群友小分辨率"点详情后下面信息不显示")。
    # _clamp_rect_into_workarea 把窗口位置拉回屏内(窗口高度本就 clamp 到 work_h-72, 只重定位)。
    module = _ahmad_overlay_module()
    clamp = module._clamp_rect_into_workarea
    # 靠屏底: window 480x600 at y=400, work 0..728 → bottom 1000>728 → 上移到 728-600=128
    assert clamp(100, 400, 480, 600, 0, 0, 1366, 728) == (100, 128)
    # 右溢出: x=1000 → 拉到 1366-480=886
    assert clamp(1000, 10, 480, 300, 0, 0, 1366, 728) == (886, 10)
    # 已在屏内: 不动
    assert clamp(50, 50, 480, 300, 0, 0, 1366, 728) == (50, 50)
    # 窗口比工作区还高: 贴顶(至少露出顶部)
    assert clamp(100, 400, 480, 800, 0, 0, 1366, 728) == (100, 0)
    # 任务栏在上(top=40): 夹到 top 而非 0
    assert clamp(100, 10, 480, 300, 0, 40, 1366, 768) == (100, 40)


def test_find_calculator_launcher_prefers_exe_then_ps1(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    root = tmp_path / "pkg"
    live = root / "data" / "logs" / "live"
    live.mkdir(parents=True)
    snapshot = live / "latest_snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "_validated_package_root", lambda: root)
    # 无启动器 -> None
    assert module._find_calculator_launcher(snapshot) is None
    # 只有 ps1 -> ps1 回退
    (root / "scripts").mkdir()
    (root / "scripts" / "start_live_npcap_overlay.ps1").write_text("# launcher", encoding="utf-8")
    result = module._find_calculator_launcher(snapshot)
    assert result is not None and result[0] == "ps1"
    assert result[1].name == "start_live_npcap_overlay.ps1"
    # exe 存在 -> 优先 exe
    (root / "BidKingLive.exe").write_text("MZ", encoding="utf-8")
    result2 = module._find_calculator_launcher(snapshot)
    assert result2 is not None and result2[0] == "exe"
    assert result2[1].name == "BidKingLive.exe"


def test_named_process_running_state_distinguishes_running_absent_and_unknown() -> None:
    module = _ahmad_overlay_module()

    def process_iter_with_game(_attrs):
        return [
            SimpleNamespace(info={"name": "steam.exe"}),
            SimpleNamespace(info={"name": "BIDKING.EXE"}),
        ]

    def process_iter_without_game(_attrs):
        return [SimpleNamespace(info={"name": "steam.exe"})]

    def process_iter_failed(_attrs):
        raise RuntimeError("enumeration unavailable")

    assert (
        module._named_process_running_state(
            "BidKing.exe",
            process_iter=process_iter_with_game,
        )
        is True
    )
    assert (
        module._named_process_running_state(
            "BidKing.exe",
            process_iter=process_iter_without_game,
        )
        is False
    )
    assert (
        module._named_process_running_state(
            "BidKing.exe",
            process_iter=process_iter_failed,
        )
        is None
    )


def test_spawn_restart_relauncher_waits_for_pids_then_launches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    exe = tmp_path / "BidKingLive.exe"
    exe.write_text("MZ", encoding="utf-8")
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    if os.name != "nt":
        raised = False
        try:
            module._spawn_restart_relauncher(
                ("exe", exe),
                [111, 222],
                restart_log_path=tmp_path / "restart.log",
            )
        except RuntimeError:
            raised = True
        assert raised
        return
    calls: dict[str, Any] = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    module._spawn_restart_relauncher(
        ("exe", exe),
        [111, 222],
        restart_log_path=tmp_path / "restart.log",
        _popen=fake_popen,
    )
    args = calls["args"]
    assert args[0] == "powershell.exe"
    script = args[-1]
    assert "111,222" in script  # 先等这些 pid(旧 monitor+overlay)退出
    assert "Get-Process -Id" in script
    # 无 ps1 时回退直接起壳 exe
    assert "Start-Process -FilePath" in script and "BidKingLive.exe" in script
    assert "restart.log" in script  # 可观测: 写重启日志
    # CREATE_NO_WINDOW(不是 DETACHED_PROCESS——后者让 powershell 无控制台→脚本静默不跑=重启失效真根因)
    assert calls["kwargs"].get("creationflags", 0) & 0x08000000  # CREATE_NO_WINDOW
    assert not (calls["kwargs"].get("creationflags", 0) & 0x00000008)  # 绝不带 DETACHED_PROCESS
    assert calls["kwargs"].get("creationflags", 0) & 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def test_spawn_restart_relauncher_prefers_ps1_restart_with_embedded_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """打包版结构下(壳 exe + scripts/ps1 + runtime/python): 重启走脚本 -Restart + 内嵌 python,
    脚本自清旧实例、最稳。带 -WorkingDirectory(否则壳解析相对资源会因 cwd=system32 失败)。"""
    module = _ahmad_overlay_module()
    if os.name != "nt":
        return
    root = tmp_path / "pkg"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_live_npcap_overlay.ps1").write_text("# launcher", encoding="utf-8")
    (root / "runtime" / "python").mkdir(parents=True)
    (root / "runtime" / "python" / "python.exe").write_text("MZ", encoding="utf-8")
    (root / "BidKingLive.exe").write_text("MZ", encoding="utf-8")
    monkeypatch.setattr(module, "_validated_package_root", lambda: root)
    calls: dict[str, Any] = {}

    def fake_popen(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    module._spawn_restart_relauncher(
        ("exe", root / "BidKingLive.exe"),
        [111],
        diagnostic_profile="engineering",
        restart_log_path=tmp_path / "restart.log",
        _popen=fake_popen,
    )
    script = calls["args"][-1]
    assert "start_live_npcap_overlay.ps1" in script
    assert "-Restart" in script                       # 脚本自清旧实例
    assert "-DiagnosticProfile" in script and "engineering" in script
    assert "python.exe" in script                     # 内嵌 python 传给脚本
    assert "-WorkingDirectory" in script              # cwd 指向包根
    assert "-WindowStyle Hidden" in script            # 隐藏脚本自身的 powershell 窗口(别弹空白窗)
    assert "restart.log" in script


def test_restart_calculator_forwards_current_diagnostic_profile(tmp_path: Path, monkeypatch) -> None:
    module = _ahmad_overlay_module()
    launcher = ("exe", tmp_path / "BidKingLive.exe")
    launcher[1].write_text("MZ", encoding="utf-8")
    captured: dict[str, Any] = {}

    class Root:
        def withdraw(self):
            pass

        def update_idletasks(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(module, "_find_calculator_launcher", lambda _path: launcher)
    monkeypatch.setattr(module, "_named_process_running_state", lambda _name: False)

    def capture(_launcher, _pids, *, diagnostic_profile, **_kwargs):
        captured["profile"] = diagnostic_profile

    monkeypatch.setattr(module, "_spawn_restart_relauncher", capture)
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.diagnostic_profile = "engineering"
    overlay._stop_pids_on_exit = []
    overlay.root = Root()
    overlay._save_ui_prefs_if_enabled = lambda: None
    overlay._destroy_pet_companion = lambda: None
    overlay._run_exit_cleanup = lambda: None

    assert module.AhmadTkOverlay.restart_calculator(overlay) == "break"
    assert captured["profile"] == "engineering"


def test_refresh_interface_repaints_only_without_touching_process_lifecycle(
    monkeypatch,
) -> None:
    module = _ahmad_overlay_module()
    process_checks: list[bool] = []
    monkeypatch.setattr(
        module,
        "_named_process_running_state",
        lambda _name: process_checks.append(True),
    )
    render_calls: list[dict[str, Any]] = []
    runtime_events: list[str] = []
    tip_texts: list[str] = []
    cleanup_calls: list[bool] = []
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._last_summary = {"status": "ok", "reference": {"balanced": "100"}}
    overlay._last_live_summary = {}
    overlay._stop_pids_on_exit = (123,)
    overlay._cleanup_lock_paths = (Path("monitor.lock"),)
    overlay._hide_minimap_popup = lambda: None
    overlay._hide_pinned_minimap = lambda: None
    overlay._update_pet_tooltip = lambda: None
    overlay.render = lambda data: render_calls.append(data)
    overlay.render_standby = lambda data: render_calls.append(data)
    overlay.render_missing = lambda _message: None
    overlay._redraw_minimap_popup = lambda: None
    overlay._redraw_pinned_minimap = lambda: None
    overlay._record_ui_runtime_status = lambda event, **_kwargs: runtime_events.append(event)
    overlay.refresh_tip = SimpleNamespace(set_text=lambda text: tip_texts.append(text))
    overlay._run_exit_cleanup = lambda: cleanup_calls.append(True)

    for _ in range(10):
        assert module.AhmadTkOverlay.refresh_interface(overlay) == "break"

    assert len(render_calls) == 10
    assert runtime_events == ["interface_refreshed"] * 10
    assert tip_texts == [module.REFRESH_TIP] * 10
    assert process_checks == []
    assert cleanup_calls == []
    assert overlay._stop_pids_on_exit == (123,)
    assert overlay._cleanup_lock_paths == (Path("monitor.lock"),)


@pytest.mark.parametrize(
    ("game_state", "expected_text"),
    [
        (True, "请先完全退出 BidKing，再重启计算器"),
        (None, "未能确认游戏是否已退出；请先完全退出 BidKing，再重启计算器"),
    ],
)
def test_restart_calculator_fail_safe_preserves_overlay_and_monitor(
    tmp_path: Path,
    monkeypatch,
    game_state: bool | None,
    expected_text: str,
) -> None:
    module = _ahmad_overlay_module()
    launcher_calls: list[Path] = []
    relaunch_calls: list[bool] = []
    cleanup_calls: list[bool] = []
    destroyed: list[bool] = []
    status_text: list[str] = []
    tip_text: list[str] = []
    monkeypatch.setattr(module, "_named_process_running_state", lambda _name: game_state)
    monkeypatch.setattr(
        module,
        "_find_calculator_launcher",
        lambda path: launcher_calls.append(path),
    )
    monkeypatch.setattr(
        module,
        "_spawn_restart_relauncher",
        lambda *_args, **_kwargs: relaunch_calls.append(True),
    )

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._stop_pids_on_exit = (123,)
    overlay.status = SimpleNamespace(
        configure=lambda **kwargs: status_text.append(str(kwargs["text"]))
    )
    overlay.restart_tip = SimpleNamespace(set_text=lambda text: tip_text.append(str(text)))
    overlay._run_exit_cleanup = lambda: cleanup_calls.append(True)
    overlay.root = SimpleNamespace(destroy=lambda: destroyed.append(True))

    for _ in range(2):
        assert module.AhmadTkOverlay.restart_calculator(overlay) == "break"

    assert launcher_calls == []
    assert relaunch_calls == []
    assert cleanup_calls == []
    assert destroyed == []
    assert status_text == [expected_text, expected_text]
    assert tip_text == [expected_text, expected_text]


def test_spawn_restart_relauncher_breakaway_fallback_on_oserror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Job 不允许 breakaway 时首个 popen 抛 OSError -> 回退到不带该 flag,仍以 DETACHED 起(尽力而为)。"""
    module = _ahmad_overlay_module()
    if os.name != "nt":
        return
    exe = tmp_path / "BidKingLive.exe"
    exe.write_text("MZ", encoding="utf-8")
    monkeypatch.setattr(module, "_validated_package_root", lambda: tmp_path)
    flags_seen: list[int] = []

    def fake_popen(args, **kwargs):
        f = kwargs.get("creationflags", 0)
        flags_seen.append(f)
        if f & 0x01000000:  # breakaway attempt -> simulate job that forbids it
            raise OSError("breakaway not permitted")
        return object()

    module._spawn_restart_relauncher(
        ("exe", exe),
        [111],
        restart_log_path=tmp_path / "restart.log",
        _popen=fake_popen,
    )
    assert len(flags_seen) == 2                        # 先带 breakaway 抛错, 再回退
    assert flags_seen[0] & 0x01000000                  # 第一次带 breakaway
    assert not (flags_seen[1] & 0x01000000)            # 回退不带
    assert flags_seen[1] & 0x08000000                  # 仍保留 CREATE_NO_WINDOW
    assert not (flags_seen[1] & 0x00000008)            # 绝不带 DETACHED_PROCESS
