# -*- coding: utf-8 -*-
"""真 Tk 回归门: 用过一次「手填」之后, 卡片必须还能重新显示(审计窗口 2026-07-11, 0712 订正).

为什么必须用真 Tk: 这个 bug 发生时 279 个 mock/headless 测试【全绿】, 而「手填」「详情」面板对每个用户
都是永久坏的。mock 测的是"toggle 有没有被调用", 坏的是 Tk 的真实映射语义 —— 只有真控件树能抓到。

⚠️【0712 订正 · 首版这个文件里有一道假门】: 首版用「详情」按钮来验 winfo_ismapped, 但走【详情】路径时
content 的 mapped 标志会正确跟着 holder 变 0 —— 那道断言在修复前就是绿的, 从未红过。
真 Tk 三路实测(Windows Tk 8.6)才定住的机制是【路径相关】的:

    裸 Canvas+Frame  pack_forget(holder) -> holder=0  content=0   (祖先 unmap 会清子标志)
    overlay 走【详情】 收起              -> holder=0  content=0   (同上, 正常)
    overlay 走【手填->实时】收起          -> holder=0  content=1   ★ content 的标志【残留】

即: 只有【用过一次手填】之后, content.winfo_ismapped() 才会与 holder 脱节并恒为 1, 于是调用方的
`if not card.winfo_ismapped(): card.pack(...)` 守卫永久失效, 卡片再也回不来。这与作者的原话完全吻合——
「第一次手填切换完, 完全切不回去了, 包括详情按钮」。所以门必须走【手填】路径, 走详情路径抓不到。

本文件锁死三条不变量(全部在修复前【实测为红】):
  1. 收起后 card.winfo_ismapped() 必须报 holder 的状态(修复前: 走手填路径后恒为 1)
  2. 反复点「手填」必须每次都真的把面板打开(修复前: 第 2 次起窗口停在 mini, 打不开)
  3. 反复开合「详情」必须幂等且高度一致(修复前: 首展 1143, 之后 1320 —— 首展底部被裁 ~177px)
"""
from __future__ import annotations

import copy
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

tk = pytest.importorskip("tkinter")
tkfont = pytest.importorskip("tkinter.font")

from test_live_overlay import (  # noqa: E402
    _ahmad_overlay_module,
    _ahmad_server_module,
    _summarize_low_quality_joint,
)
from test_quality_composition_producer_0_3_1 import (  # noqa: E402
    _native_2401_summary,
)
from detail_composition_view import (  # noqa: E402
    QUALITY_COMPOSITION_SCHEMA_VERSION,
)


_QUALITY_KEYS = ("q1", "q3", "q4", "q5", "q6")


def _current_quality_composition(
    raw_block: dict[str, object],
) -> dict[str, object]:
    """Migrate stable test fixtures onto the producer-owned current wire."""

    block = dict(raw_block)
    raw_qualities = block.pop("qualities", {})
    assert isinstance(raw_qualities, dict)
    qualities = dict(raw_qualities)
    for key in _QUALITY_KEYS:
        if key in block:
            qualities[key] = block.pop(key)
    for raw_quality in qualities.values():
        if not isinstance(raw_quality, dict):
            continue
        if raw_quality.get("status") not in {"ready", "exact"}:
            continue
        raw_quality.setdefault("compact_count_candidates", [])
        raw_quality.setdefault("compact_count_triplet", [])
        raw_quality.setdefault("count_correlation_state", "independent")
        raw_quality.setdefault("count_correlation_note", "")
    block["schema_version"] = QUALITY_COMPOSITION_SCHEMA_VERSION
    block["qualities"] = qualities
    return block


def _new_root(attempts: int = 3):
    """建 Tk 根窗口, 对【环境层】的偶发失败重试。

    实测(审计窗口 0712): 本机 Python 3.13 的 Tk 初始化会间歇性报
    `couldn't read file ".../tk8.6/ttk/scale.tcl": no such file or directory` —— 而该文件确实存在,
    裸 Tk 连开 12 次零失败, 只在 pytest 进程里偶发(约 1/3), 大概率是杀软扫描导致的瞬时读失败。
    这是环境问题, 不是产品问题; 但门一抖就会被 deselect(实际已被调参窗口 deselect 过)= 等于没门。
    所以: 只建【一个】根窗口(首版建了 4 个 = 4 倍暴露), 并对这类环境失败做有界重试。
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as exc:  # noqa: PERF203
            last = exc
    pytest.skip(f"真 Tk 不可用(环境层): {last}")


def _physical_outer_rect(module, widget) -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetAncestor(wintypes.HWND(widget.winfo_id()), 2)
            if not hwnd:
                hwnd = wintypes.HWND(widget.winfo_id())
            rect = wintypes.RECT()
            result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd,
                9,  # DWMWA_EXTENDED_FRAME_BOUNDS: physical desktop pixels.
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if result == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    return module._window_outer_rect(widget)


def _mapped_descendants(parent):
    stack = [parent]
    result = []
    while stack:
        widget = stack.pop()
        result.append(widget)
        stack.extend(widget.winfo_children())
    return result


def test_token_dialog_maps_real_activation_reasons_without_leaking_exceptions(
    monkeypatch, tmp_path
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    from bidking_lab.secure import enforce
    from bidking_lab.secure.client_activate import ActivationResult

    root = _new_root()
    current = {"reason": "offline", "raise": False}
    synthetic_token = "v1|audit-ui|1785500000." + "a" * 64

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    def refresh_activation(*, ui_token):
        assert ui_token == synthetic_token
        if current["raise"]:
            raise RuntimeError(r"C:\Users\example\token-secret")
        return ActivationResult(
            activated=False,
            mode="none",
            reason=str(current["reason"]),
        )

    monkeypatch.setattr(enforce, "refresh_activation", refresh_activation)
    try:
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "activation-reasons" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.update()
        monkeypatch.setattr(module, "threading", SimpleNamespace(Thread=ImmediateThread))
        cases = (
            ("offline", False, "激活网络诊断"),
            ("bad_token", False, "token 无效"),
            ("token_persist_failed", False, "程序目录或当前身份的写入权限"),
            ("unexpected_reason", False, "激活未通过"),
            ("offline", True, "激活异常 · 请稍后重试或运行激活网络诊断"),
        )
        for reason, should_raise, expected in cases:
            current["reason"] = reason
            current["raise"] = should_raise
            ov._open_token_dialog()
            root.update()
            dialog = ov._token_dialog
            assert dialog is not None
            widgets = _mapped_descendants(dialog)
            token_entry = next(
                widget
                for widget in widgets
                if isinstance(widget, tk.Entry) and str(widget.cget("state")) == "normal"
            )
            activate_button = next(
                widget
                for widget in widgets
                if isinstance(widget, tk.Button) and str(widget.cget("text")) == "激活"
            )
            token_entry.insert(0, synthetic_token)
            activate_button.invoke()
            deadline = time.monotonic() + 3
            texts: list[str] = []
            while time.monotonic() < deadline:
                root.update_idletasks()
                root.update()
                texts = [
                    str(widget.cget("text"))
                    for widget in _mapped_descendants(dialog)
                    if isinstance(widget, tk.Label)
                ]
                if any(expected in text for text in texts):
                    break
                time.sleep(0.01)
            assert any(expected in text for text in texts)
            visible = " ".join(texts).lower()
            for forbidden in (
                "unexpected_reason",
                "runtimeerror",
                "private-user",
                "token-secret",
                "c:\\users",
            ):
                assert forbidden not in visible
            dialog.destroy()
            ov._token_dialog = None
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _composition_fixture() -> dict[str, object]:
    def candidates(count: int) -> list[dict[str, object]]:
        weights = (0.30, 0.18, 0.12, 0.08, 0.06, 0.05, 0.04, 0.03)
        return [
            {
                "count": index,
                "cells": index * 6,
                "probability": weights[index],
            }
            for index in range(count)
        ]

    data = {
        "reference": {
            "source": "ref_prior",
            "total_grid_range": "108 / 126 / 144格",
            "total_value_range": "9,999,999 / 12,888,888 / 18,888,888",
            "note": "posterior coverage kept",
        },
        "red": {"value_range": "888,888 / 1,288,888 / 2,188,888"},
        "evidence": {
            "match_text": "structured",
            "information_density": "high",
            "ref_input_summary": "总件 24 · 总格 126",
            "latest_result": {"tool": "全库透视", "result": 1},
        },
        "minimap": {
            "known_items": 22,
            "final_total_items": 24,
            "layout_source": "public_outline",
            "quality_counts": {"q6": 2, "q5": 4, "q4": 6, "q3": 8},
            "columns": 12,
            "viewport_rows": 12,
        },
        "red_outline_value_summary": {
            "status": "outline_complete",
            "eligibility_complete": True,
            "floor_value": 888888,
            "p50_value": 1288888,
            "p90_value": 2188888,
        },
        "quality_composition": {
            "status": "ready",
            "q6": {
                "status": "ready",
                "source": "pricing_posterior",
                "lock_strength": "probabilistic",
                "lock_reason": "joint posterior",
                "candidates": candidates(8),
                "shown_probability": 0.86,
                "other_probability": 0.14,
                "truncated": True,
                "cap_hit": True,
                "pricing_posterior": True,
                "display_reconciled": False,
            },
            "q5": {
                "status": "exact",
                "source": "exact_evidence",
                "lock_strength": "exact",
                "lock_reason": "count and cells exact",
                "candidates": [{"count": 4, "cells": 24, "probability": 1.0}],
                "shown_probability": 1.0,
                "other_probability": 0.0,
                "truncated": False,
                "cap_hit": False,
                "pricing_posterior": True,
                "display_reconciled": False,
            },
            "q4": {
                "status": "unavailable",
                "reason": "等待结构化组合数据",
                "lock_strength": "unavailable",
                "candidates": [],
            },
            "q3": {
                "status": "unavailable",
                "reason": "等待结构化组合数据",
                "lock_strength": "unavailable",
                "candidates": [],
            },
            "q1": {
                "status": "unavailable",
                "reason": "等待结构化组合数据",
                "lock_strength": "unavailable",
                "candidates": [],
            },
        },
    }
    quality_block = data["quality_composition"]
    assert isinstance(quality_block, dict)
    data["quality_composition"] = _current_quality_composition(quality_block)
    return data


def _compact_2510_fixture() -> dict[str, object]:
    data = {
        "status": "ok",
        "updated_at_text": "20:10:00",
        "context": {
            "hero": "ahmed",
            "map_id": 2510,
            "round": 4,
            "phase": "bidding",
            "session_id": "2510:sanitized",
        },
        "reference": {
            "source": "ref_prior",
            "conservative": "400,000",
            "balanced": "500,000",
            "aggressive": "600,000",
            "action": "估计参考",
            "total_grid_range": "118 / 118 / 118格",
            "total_value_range": "800,000 / 900,000 / 1,000,000",
        },
        "red": {
            "count_range": "6 / 6 / 6",
            "cells_range": "25 / 25 / 25",
            "value_range": "360,000 / 360,000 / 360,000",
            "quality_count_summary": "紫8件/28格 · 金6/6/12件 · 19/19/38格",
            "quality_probability_summary": "金6件19格97.3%·红6件25格97.3%",
            "count_probability_hover": "红件候选：6件 97.3% · 0件 2.7%",
            "cells_probability_hover": "红格候选：25格 97.3% · 0格 2.7%",
            "quality_probability_hover": (
                "金品：6件/19格 97.3% · 12件/38格 2.7%；"
                "红品：6件/25格 97.3% · 0件/0格 2.7%"
            ),
            "uncertainty_summary": "概率估计",
        },
        "evidence": {
            "match_text": "structured",
            "information_density": "high",
            "latest_result": {},
        },
        "quality_composition": {
            "status": "ready",
            "q6": {
                "status": "ready",
                "source": "pricing_posterior",
                "lock_strength": "probabilistic",
                "lock_reason": "combo_multi_solution",
                "candidates": [
                    {"count": 6, "cells": 25, "probability": 0.9733},
                    {"count": 0, "cells": 0, "probability": 0.0267},
                ],
                "shown_probability": 1.0,
                "other_probability": 0.0,
                "truncated": False,
                "cap_hit": False,
                "pricing_posterior": True,
                "display_reconciled": False,
            },
        },
        "ahmed_ref": {
            "status": "ok",
            "quality_lock": {
                "q5": {
                    "strength": "probabilistic",
                    "reason": "combo_multi_solution",
                },
                "q6": {
                    "strength": "probabilistic",
                    "reason": "combo_multi_solution",
                },
            },
        },
        "minimap": {},
        "truth": {"available": False},
        "flags": [],
    }
    quality_block = data["quality_composition"]
    assert isinstance(quality_block, dict)
    data["quality_composition"] = _current_quality_composition(quality_block)
    return data


def _truncated_marginal_quality_payload(server) -> dict[str, object]:
    return server._quality_composition_payload(  # type: ignore[attr-defined]
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q1": [
                    {"count": 9, "cells": 19, "prob": 0.0575},
                    {"count": 9, "cells": 20, "prob": 0.0575},
                    {"count": 10, "cells": 21, "prob": 0.0488},
                    {"count": 10, "cells": 22, "prob": 0.0488},
                ],
                "q3": [
                    {"count": 11, "cells": 24, "prob": 0.0692},
                    {"count": 11, "cells": 25, "prob": 0.0692},
                    {"count": 10, "cells": 21, "prob": 0.0673},
                    {"count": 10, "cells": 23, "prob": 0.0673},
                ],
                "q6": [{"count": 3, "cells": 25, "prob": 1.0}],
            },
            "quality_count_candidates": {
                "q1": [
                    {"value": 7, "prob": 0.2},
                    {"value": 9, "prob": 0.1149},
                ],
                "q3": [
                    {"value": 11, "prob": 0.1383},
                    {"value": 10, "prob": 0.1345},
                ],
            },
            "quality_cells_candidates": {
                "q1": [
                    {"value": 19, "prob": 0.0575},
                    {"value": 21, "prob": 0.0488},
                ],
                "q3": [
                    {"value": 24, "prob": 0.0692},
                    {"value": 21, "prob": 0.0673},
                ],
            },
            "quality_lock": {
                "q1": {"strength": "probabilistic", "reason": "partial_reveal_min"},
                "q3": {"strength": "probabilistic", "reason": "partial_reveal_min"},
                "q6": {"strength": "exact", "reason": "exact_lock"},
            },
        }
    )


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_truncated_engine_marginals_keep_low_quality_rows_visible(
    tmp_path,
    dpi_scale,
) -> None:
    server = _ahmad_server_module()
    data = _compact_2510_fixture()
    data["quality_composition"] = _truncated_marginal_quality_payload(server)
    red = data["red"]
    assert isinstance(red, dict)
    red["count_range"] = "0 / 1 / 3"
    red["cells_range"] = "0 / 4 / 24"

    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"truncated-marginal-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        ov.render(data)
        root.update_idletasks()
        root.update()

        assert ov._detail_composition_view.status == "ready"
        for key in ("白绿", "蓝品"):
            text = str(ov.red_rows[key].cget("text"))
            assert text != "-"
            assert "冲突" not in text
            assert ov.quality_state_badges[key].cget("text") == "未锁"
            assert "\n" not in text
        assert ov.red_rows["白绿"].cget("text") == "7–10件·19–22格"
        assert ov.red_rows["蓝品"].cget("text") == "10–11件·21–25格"
        assert ov.red_rows["红件"].cget("text") == "3件"
        red_font = tkfont.Font(font=ov.red_rows["红件"].cget("font"))
        assert abs(red_font.actual("size")) >= module.SUMMARY_VALUE_FONT_SIZE
        assert str(red_font.actual("weight")) == "bold"

        remaining_row = ov.action_rows["红/空格"].master
        remaining_caption = next(
            child
            for child in remaining_row.winfo_children()
            if isinstance(child, tk.Label) and child is not ov.action_rows["红/空格"]
        )
        remaining_caption_font = tkfont.Font(font=remaining_caption.cget("font"))
        assert remaining_caption.cget("text") == "红+空"
        assert (
            remaining_caption_font.measure("红+空")
            <= remaining_caption.winfo_width()
        )
        assert (
            remaining_caption.winfo_x() + remaining_caption.winfo_width()
            <= ov.action_rows["红/空格"].winfo_x()
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_2510_compact_and_details_keep_probabilistic_lock_visible_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"2510-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        data = _compact_2510_fixture()
        ov.render(data)
        root.update_idletasks()
        root.update()

        count_tip = ov.red_rows["红件"]._hover_tip
        assert count_tip is not None
        assert "最可能 6件" in count_tip.text
        assert "6件 97.3%" in count_tip.text
        assert "0件 2.7%" in count_tip.text
        assert "锁定" not in count_tip.text
        assert "唯一" not in count_tip.text

        count_tip.show_at_coords(
            ov.red_rows["红件"].winfo_rootx(),
            ov.red_rows["红件"].winfo_rooty(),
        )
        root.update_idletasks()
        root.update()
        assert count_tip.tip is not None and count_tip.tip.winfo_ismapped()
        assert count_tip._label is not None
        assert count_tip._label.winfo_width() >= count_tip._label.winfo_reqwidth()
        assert count_tip._label.winfo_height() >= count_tip._label.winfo_reqheight()

        count_tip.hide()
        ov.toggle_details()
        ov._update_detail_rows(data)
        root.update_idletasks()
        root.update()
        q6_row = ov.detail_composition_rows[0]
        assert q6_row["quality"].cget("text") == "红"
        assert q6_row["count"].cget("text") == "6"
        assert q6_row["cells"].cget("text") == "25"
        assert q6_row["probability"].cget("text") == "97.3%"
        assert q6_row["state"].cget("text") == "最可能"
        assert q6_row["state"].winfo_ismapped()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


def _set_2510_q6_lock_case(data: dict[str, object], case: str) -> None:
    quality_block = data["quality_composition"]
    assert isinstance(quality_block, dict)
    qualities = quality_block["qualities"]
    assert isinstance(qualities, dict)
    q6 = qualities["q6"]
    assert isinstance(q6, dict)
    red = data["red"]
    assert isinstance(red, dict)
    red["count_probability_hover"] = "红件候选：6件 100%"
    red["cells_probability_hover"] = "红格候选：25格 100%"
    q6.update(
        {
            "status": "exact",
            "lock_strength": "exact",
            "lock_reason": case,
            "candidates": [{"count": 6, "cells": 25, "probability": 1.0}],
            "shown_probability": 1.0,
            "other_probability": 0.0,
            "truncated": False,
            "cap_hit": False,
        }
    )
    if case == "cap_hit":
        q6["cap_hit"] = True
    elif case == "nextafter_other":
        probability = math.nextafter(1.0, 0.0)
        q6["candidates"] = [
            {"count": 6, "cells": 25, "probability": probability}
        ]
        q6["shown_probability"] = probability
        q6["other_probability"] = 1.0 - probability
    elif case == "epsilon_other":
        q6["candidates"] = [
            {"count": 6, "cells": 25, "probability": 0.9999995}
        ]
        q6["shown_probability"] = 0.9999995
        q6["other_probability"] = 0.0000005
    elif case == "truncated":
        q6["truncated"] = True
    elif case == "missing_truncated":
        q6.pop("truncated")
    elif case == "missing_cap_hit":
        q6.pop("cap_hit")
    elif case == "probabilistic_near_one":
        probability = math.nextafter(1.0, 0.0)
        q6.update(
            {
                "status": "ready",
                "lock_strength": "probabilistic",
                "candidates": [
                    {"count": 6, "cells": 25, "probability": probability}
                ],
                "shown_probability": probability,
                "other_probability": 1.0 - probability,
            }
        )
    elif case == "probabilistic_missing_flags":
        q6.update(
            {
                "status": "ready",
                "lock_strength": "probabilistic",
                "lock_reason": "missing_completion_flags",
            }
        )
        q6.pop("truncated")
        q6.pop("cap_hit")


class _2510BombStr(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __str__(self):
        return self._explode("str")

    def __eq__(self, other):
        del other
        return self._explode("eq")


class _2510BombFloat(float):
    def __new__(cls, value: float):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __str__(self):
        return self._explode("str")

    def __float__(self):
        return self._explode("float")

    def __eq__(self, other):
        del other
        return self._explode("eq")


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_2510_malicious_scalar_projection_is_visible_conflict_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=(
                tmp_path / f"2510-stable-projection-{dpi_scale}" / "latest_snapshot.json"
            ),
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        details_opened = False

        for field, value in (
            ("status", _2510BombStr("exact")),
            ("lock_strength", _2510BombStr("exact")),
            ("shown_probability", _2510BombFloat(1.0)),
            ("other_probability", _2510BombFloat(0.0)),
        ):
            data = _compact_2510_fixture()
            _set_2510_q6_lock_case(data, "strict_exact")
            quality_block = data["quality_composition"]
            assert isinstance(quality_block, dict)
            qualities = quality_block["qualities"]
            assert isinstance(qualities, dict)
            q6 = qualities["q6"]
            assert isinstance(q6, dict)
            q6[field] = value

            ov.render(data)
            root.update_idletasks()
            root.update()
            if not details_opened:
                ov.toggle_details()
                details_opened = True
            ov._update_detail_rows(data)
            ov._select_detail_composition_filter("q6")
            root.update_idletasks()
            root.update()

            count_tip = ov.red_rows["红件"]._hover_tip
            assert count_tip is not None
            q6_row = ov.detail_composition_rows[0]
            footer = str(ov.detail_composition_footer.cget("text"))
            visible = "\n".join(
                (
                    count_tip.text,
                    str(q6_row["state"].cget("text")),
                    str(q6_row["probability"].cget("text")),
                    footer,
                )
            )
            assert str(q6_row["state"].cget("text")) == "冲突"
            assert str(q6_row["probability"].cget("text")) == "-"
            assert footer == "组合数据存在冲突"
            assert "锁定" not in visible
            assert "唯一" not in visible
            assert "100%" not in visible
            assert value.magic_calls == []
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_2510_root_conflict_cannot_leak_strict_child_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=(
                tmp_path
                / f"2510-root-conflict-{dpi_scale}"
                / "latest_snapshot.json"
            ),
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        details_opened = False

        for root_case in (
            "invalid_status",
            "invalid_reason",
            "partial_invalid_qualities",
            "explicit_conflict",
        ):
            data = _compact_2510_fixture()
            _set_2510_q6_lock_case(data, "strict_exact")
            quality_block = data["quality_composition"]
            assert isinstance(quality_block, dict)
            canary = None
            if root_case == "invalid_status":
                quality_block["status"] = []
            elif root_case == "invalid_reason":
                canary = _2510BombStr("ready")
                quality_block["reason"] = canary
            elif root_case == "partial_invalid_qualities":
                qualities = quality_block["qualities"]
                assert isinstance(qualities, dict)
                q6 = qualities["q6"]
                quality_block.clear()
                quality_block.update(
                    {
                        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
                        "status": "ready",
                        "qualities": {
                            "q6": q6,
                            "q5": [],
                        },
                    }
                )
            else:
                quality_block["status"] = "conflict"

            ov.render(data)
            root.update_idletasks()
            root.update()
            if not details_opened:
                ov.toggle_details()
                details_opened = True
            ov._update_detail_rows(data)
            ov._select_detail_composition_filter("q6")
            root.update_idletasks()
            root.update()

            count_tip = ov.red_rows["红件"]._hover_tip
            assert count_tip is not None
            q6_row = ov.detail_composition_rows[0]
            footer = str(ov.detail_composition_footer.cget("text"))
            visible = "\n".join(
                (
                    count_tip.text,
                    str(q6_row["state"].cget("text")),
                    str(q6_row["probability"].cget("text")),
                    footer,
                )
            )
            assert str(q6_row["state"].cget("text")) == "冲突"
            assert str(q6_row["probability"].cget("text")) == "-"
            assert footer == "组合数据存在冲突"
            assert "锁定" not in visible
            assert "唯一" not in visible
            assert "100%" not in visible
            if canary is not None:
                assert canary.magic_calls == []
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
@pytest.mark.parametrize(
    "declared_status",
    ("unavailable", "loading", "error", "stale", "no_reachable"),
)
def test_2510_declared_nonready_root_cannot_mask_sibling_conflict_at_all_dpi(
    tmp_path, dpi_scale, declared_status
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=(
                tmp_path
                / f"2510-sibling-conflict-{declared_status}-{dpi_scale}"
                / "latest_snapshot.json"
            ),
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        data = _compact_2510_fixture()
        _set_2510_q6_lock_case(data, "strict_exact")
        quality_block = data["quality_composition"]
        assert isinstance(quality_block, dict)
        qualities = quality_block["qualities"]
        assert isinstance(qualities, dict)
        q6 = qualities["q6"]
        flat_alias_canary = _2510BombStr("exact")
        extra_quality_canary = _2510BombStr("exact")
        quality_block.clear()
        quality_block.update(
            {
                "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
                "status": declared_status,
                "reason": f"declared {declared_status} must not mask conflict",
                "q6": flat_alias_canary,
                "qualities": {
                    "q6": q6,
                    "q5": [],
                    "q2": extra_quality_canary,
                },
            }
        )

        ov.render(data)
        root.update_idletasks()
        root.update()
        count_tip = ov.red_rows["红件"]._hover_tip
        assert count_tip is not None
        ov.toggle_details()
        ov._update_detail_rows(data)
        ov._select_detail_composition_filter("q6")
        root.update_idletasks()
        root.update()

        q6_row = ov.detail_composition_rows[0]
        footer = str(ov.detail_composition_footer.cget("text"))
        visible = "\n".join(
            (
                count_tip.text,
                str(q6_row["state"].cget("text")),
                str(q6_row["probability"].cget("text")),
                footer,
            )
        )
        assert str(q6_row["state"].cget("text")) == "冲突"
        assert str(q6_row["probability"].cget("text")) == "-"
        assert footer == "组合数据存在冲突"
        assert "锁定" not in visible
        assert "唯一" not in visible
        assert "100%" not in visible
        assert flat_alias_canary.magic_calls == []
        assert extra_quality_canary.magic_calls == []
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
@pytest.mark.parametrize(
    "lock_case",
    (
        "strict_exact",
        "cap_hit",
        "nextafter_other",
        "epsilon_other",
        "truncated",
        "missing_truncated",
        "missing_cap_hit",
        "probabilistic_near_one",
        "probabilistic_missing_flags",
    ),
)
def test_2510_strict_exact_contract_matches_compact_and_details_at_all_dpi(
    tmp_path, dpi_scale, lock_case
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=(
                tmp_path / f"2510-lock-{lock_case}-{dpi_scale}" / "latest_snapshot.json"
            ),
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        data = _compact_2510_fixture()
        _set_2510_q6_lock_case(data, lock_case)
        ov.render(data)
        root.update_idletasks()
        root.update()

        count_tip = ov.red_rows["红件"]._hover_tip
        assert count_tip is not None
        ov.toggle_details()
        ov._update_detail_rows(data)
        if lock_case == "probabilistic_missing_flags":
            ov._select_detail_composition_filter("q6")
        root.update_idletasks()
        root.update()
        q6_row = ov.detail_composition_rows[0]
        footer = str(ov.detail_composition_footer.cget("text"))

        if lock_case == "strict_exact":
            assert "唯一锁定（联合候选 100%）" in count_tip.text
            assert str(q6_row["state"].cget("text")) == "锁定"
            assert str(q6_row["probability"].cget("text")) == "100%"
            assert "冲突" not in footer
        elif lock_case == "probabilistic_near_one":
            assert "6件 <100%" in count_tip.text
            assert "其他 <0.1%" in count_tip.text
            assert str(q6_row["state"].cget("text")) == "最可能"
            assert str(q6_row["probability"].cget("text")) == "<100%"
            assert "锁定" not in count_tip.text + footer
            assert "唯一" not in count_tip.text + footer
        elif lock_case == "probabilistic_missing_flags":
            assert "6件 <100%" in count_tip.text
            assert str(q6_row["state"].cget("text")) == "最可能"
            assert str(q6_row["probability"].cget("text")) == "<100%"
            assert footer == "覆盖 <100% · 其他 0%"
            assert "锁定" not in count_tip.text + footer
            assert "唯一" not in count_tip.text + footer
            assert "6件 100%" not in count_tip.text
            assert "覆盖 100%" not in footer
        else:
            assert "锁定" not in count_tip.text + footer
            assert "唯一" not in count_tip.text + footer
            assert "100%" not in count_tip.text + footer
            assert str(q6_row["state"].cget("text")) == "冲突"
            assert str(q6_row["probability"].cget("text")) == "-"
            assert footer == "组合数据存在冲突"
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_compatibility_guidance_dialog_is_complete_and_uncropped_at_all_dpi(
    monkeypatch, tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"compatibility-dpi-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        ov._open_compatibility_guidance()
        root.update_idletasks()
        root.update()

        dialog = ov._compatibility_dialog
        assert dialog is not None
        assert dialog.winfo_ismapped()
        visible = "\n".join(
            str(widget.cget("text"))
            for widget in _mapped_descendants(dialog)
            if isinstance(widget, (tk.Label, tk.Button))
        )
        for required in (
            "火绒、360、游戏加速器",
            "整个文件夹",
            "信任区/白名单",
            "完整退出",
            "仅关闭主窗口可能仍有后台",
            "先启动 Hero Ref",
            "再启动游戏",
            "不要卸载或永久关闭",
            "不要反复更换 token",
        ):
            assert required in visible

        dialog_left = dialog.winfo_rootx()
        dialog_top = dialog.winfo_rooty()
        dialog_right = dialog_left + dialog.winfo_width()
        dialog_bottom = dialog_top + dialog.winfo_height()
        assert 0 <= dialog_left < dialog_right <= dialog.winfo_screenwidth()
        assert 0 <= dialog_top < dialog_bottom <= dialog.winfo_screenheight()
        for widget in _mapped_descendants(dialog):
            if isinstance(widget, tk.Toplevel) or not widget.winfo_ismapped():
                continue
            left = widget.winfo_rootx()
            top = widget.winfo_rooty()
            right = left + widget.winfo_width()
            bottom = top + widget.winfo_height()
            assert dialog_left <= left <= right <= dialog_right + 1
            assert dialog_top <= top <= bottom <= dialog_bottom + 1

        first_geometry = dialog.geometry()
        ov._open_compatibility_guidance()
        root.update_idletasks()
        root.update()
        assert ov._compatibility_dialog is dialog
        assert dialog.geometry() == first_geometry
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_footer_cloud_big_data_real_tk_keeps_order_bindings_and_geometry_across_states(
    monkeypatch,
    tmp_path,
    dpi_scale,
) -> None:
    module = _ahmad_overlay_module()
    opened: list[tuple[str, int, bool]] = []

    def fake_open(url: str, *, new: int = 0, autoraise: bool = True) -> bool:
        opened.append((url, new, autoraise))
        return True

    monkeypatch.setattr(module.webbrowser, "open", fake_open)
    bidding = _native_2401_summary(
        tmp_path / f"footer-cloud-bidding-{dpi_scale}",
        phase="bidding",
    )
    settled = _native_2401_summary(
        tmp_path / f"footer-cloud-settled-{dpi_scale}",
        phase="settled",
    )
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.attributes("-alpha", 0.0)
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path
            / f"footer-cloud-overlay-{dpi_scale}"
            / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        root.update_idletasks()
        root.update()
        ov._apply_ui_scale()
        root.update_idletasks()
        root.update()

        cloud_path = str(ov.footer_cloud_big_data)
        bili_path = str(ov.footer_bilibili)
        bili_binding = ov.footer_bilibili.bind("<Button-1>")
        cloud_binding = ov.footer_cloud_big_data.bind("<ButtonRelease-1>")
        cloud_return_binding = ov.footer_cloud_big_data.bind("<Return>")
        cloud_space_binding = ov.footer_cloud_big_data.bind("<space>")
        assert isinstance(ov.footer_cloud_big_data, tk.Label)
        assert str(ov.footer_cloud_big_data.cget("text")) == module.CLOUD_BIG_DATA_TEXT
        assert module.CLOUD_BIG_DATA_TEXT == "☁ 大数据"
        assert "云端" not in str(ov.footer_cloud_big_data.cget("text"))
        assert str(ov.footer_bilibili.cget("text")) == "Bilibili"
        assert bili_binding and cloud_binding
        assert bili_binding != cloud_binding
        assert cloud_return_binding and cloud_space_binding
        assert ov.footer_bilibili.bind("<ButtonRelease-1>") == ""
        assert str(ov.footer_cloud_big_data.cget("takefocus")) not in {"", "0", "false"}
        assert int(ov.footer_cloud_big_data.cget("highlightthickness")) >= 1

        def assert_footer_geometry(width: int) -> tuple[int, int]:
            root.geometry(f"{width}x{max(477, root.winfo_reqheight())}+0+0")
            root.update_idletasks()
            root.update()
            ov._apply_mini_resize_layout(width, finalize=True)
            root.update_idletasks()
            root.update()
            assert str(ov.footer_cloud_big_data) == cloud_path
            assert str(ov.footer_bilibili) == bili_path
            assert ov.footer_cloud_big_data.winfo_ismapped()
            assert ov.footer_bilibili.winfo_ismapped(), {
                "footer": (ov.footer_row.winfo_width(), ov.footer_row.winfo_reqwidth()),
                "credit": (ov.footer.winfo_width(), ov.footer.winfo_reqwidth()),
                "links": (ov.footer_links.winfo_width(), ov.footer_links.winfo_reqwidth()),
                "bilibili": (
                    ov.footer_bilibili.winfo_width(),
                    ov.footer_bilibili.winfo_reqwidth(),
                ),
                "cloud": (
                    ov.footer_cloud_big_data.winfo_width(),
                    ov.footer_cloud_big_data.winfo_reqwidth(),
                ),
                "support": (ov.support_frame.winfo_width(), ov.support_frame.winfo_reqwidth()),
                "grip": (ov.resize_grip.winfo_width(), ov.resize_grip.winfo_reqwidth()),
            }
            assert ov.footer_cloud_big_data.bind("<ButtonRelease-1>") == cloud_binding
            assert ov.footer_bilibili.bind("<Button-1>") == bili_binding
            bili_right = ov.footer_bilibili.winfo_rootx() + ov.footer_bilibili.winfo_width()
            cloud_left = ov.footer_cloud_big_data.winfo_rootx()
            cloud_right = cloud_left + ov.footer_cloud_big_data.winfo_width()
            support_left = ov.support_frame.winfo_rootx()
            footer_left = ov.footer_row.winfo_rootx()
            footer_right = footer_left + ov.footer_row.winfo_width()
            original_bili_left = ov.footer.winfo_rootx() + ov.footer.winfo_width()
            assert abs(ov.footer_bilibili.winfo_rootx() - original_bili_left) <= 1
            assert bili_right <= cloud_left
            assert cloud_left - bili_right <= 8
            if ov._footer_groups_wrapped:
                meta_bottom = (
                    ov.footer_meta_row.winfo_rooty()
                    + ov.footer_meta_row.winfo_height()
                )
                actions_top = ov.footer_actions_row.winfo_rooty()
                assert meta_bottom <= actions_top
            else:
                assert cloud_right <= support_left
            assert footer_left <= ov.footer_bilibili.winfo_rootx()
            assert cloud_right <= footer_right
            cloud_font = tkfont.Font(font=ov.footer_cloud_big_data.cget("font"))
            assert (
                cloud_font.measure(module.CLOUD_BIG_DATA_TEXT)
                <= ov.footer_cloud_big_data.winfo_width()
            )
            return (
                ov.footer_cloud_big_data.winfo_width(),
                ov.footer_cloud_big_data.winfo_height(),
            )

        initial_size = assert_footer_geometry(510)
        initial_font = tkfont.Font(font=ov.footer_cloud_big_data.cget("font"))
        initial_metrics = (
            initial_size,
            ov.footer_cloud_big_data.winfo_reqheight(),
            initial_font.actual("size"),
            float(ov._ui_scale),
        )
        state_updates = (
            ("offline", lambda: ov.render_missing("离线 · 等待读图")),
            (
                "reading",
                lambda: ov.render_standby(
                    {
                        "status": "stale_snapshot",
                        "stale": {"reason": "session_ahead", "age_seconds": 0},
                        "updated_at_text": "读图中",
                    }
                ),
            ),
            ("bidding", lambda: ov.render(bidding)),
            ("settled", lambda: ov.render(settled)),
            ("dark-theme", lambda: ov.apply_theme("dark")),
            ("warm-theme", lambda: ov.apply_theme("warm")),
        )
        for state_name, update in state_updates:
            update()
            ov.subtitle.configure(text="兼容/离线状态刷新：" + "长中文字段" * 18)
            current_size = assert_footer_geometry(510)
            current_font = tkfont.Font(font=ov.footer_cloud_big_data.cget("font"))
            assert current_size == initial_size, (
                state_name,
                initial_metrics,
                (
                    current_size,
                    ov.footer_cloud_big_data.winfo_reqheight(),
                    current_font.actual("size"),
                    float(ov._ui_scale),
                ),
            )

        # Source-external positive control: a deliberately overlong CJK label
        # must be wider than the available parcel, while Bilibili keeps the
        # original left anchor and its distinct Tcl binding.
        long_cloud_text = "☁ " + "公共大数据平台入口" * 8
        ov.footer_cloud_big_data.configure(text=long_cloud_text)
        ov._stabilize_footer_link_height()
        ov._layout_footer_groups(force=True)
        assert_footer_geometry(510)
        long_cloud_font = tkfont.Font(font=ov.footer_cloud_big_data.cget("font"))
        assert long_cloud_font.measure(long_cloud_text) > ov.footer_cloud_big_data.winfo_width()
        assert ov.footer_bilibili.winfo_width() >= ov.footer_bilibili.winfo_reqwidth()
        assert ov.footer_bilibili.bind("<Button-1>") == bili_binding

        ov.footer_cloud_big_data.configure(text=module.CLOUD_BIG_DATA_TEXT)
        ov._stabilize_footer_link_height()
        ov._layout_footer_groups(force=True)
        assert assert_footer_geometry(510) == initial_size

        ov._open_compatibility_guidance()
        root.update_idletasks()
        root.update()
        assert ov._compatibility_dialog is not None
        assert ov._compatibility_dialog.winfo_ismapped()
        assert assert_footer_geometry(510) == initial_size
        ov._compatibility_dialog.destroy()
        ov._compatibility_dialog = None

        # The product minimum is 430 logical pixels.  The right-side cluster
        # must compress without overlap or losing either external destination.
        assert_footer_geometry(430)
        assert_footer_geometry(510)

        ov.footer_cloud_big_data.focus_force()
        root.update_idletasks()
        assert root.focus_get() == ov.footer_cloud_big_data

        ov.footer_bilibili.event_generate("<Button-1>", x=2, y=2)
        for _ in range(8):
            ov.footer_cloud_big_data.event_generate("<ButtonRelease-1>", x=2, y=2)
        root.update_idletasks()
        assert opened == [
            (module.CREDIT_BILIBILI_URL, 2, True),
            (module.PUBLIC_BIG_DATA_URL, 2, True),
        ]
        assert str(ov.footer_cloud_big_data.cget("state")) == "disabled"
        assert (
            ov.footer_cloud_big_data.winfo_width(),
            ov.footer_cloud_big_data.winfo_height(),
        ) == initial_size
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_detail_composition_real_tk_stays_inside_510_width_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.attributes("-alpha", 0.0)
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"dpi-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        target_height = max(700, min(1300, root.winfo_screenheight() - 40))
        root.geometry(f"510x{target_height}+0+0")
        ov._custom_details_size = (510, target_height)
        ov.toggle_details()
        ov._update_detail_rows(_composition_fixture())
        root.update_idletasks()
        root.update()

        assert root.winfo_width() == 510
        assert ov.manual_card.master.winfo_ismapped() == 0
        assert ov.minimap_card.master.winfo_ismapped() == 0
        assert ov.detail_diagnostics_content.winfo_ismapped() == 0
        assert ov.detail_composition_footer.cget("text").startswith("选择品质")
        visible_rows = [
            row
            for row in ov.detail_composition_rows
            if row["quality"].winfo_ismapped()
        ]
        assert len(visible_rows) == 5

        table_parent = visible_rows[0]["quality"].master
        table_left = table_parent.winfo_rootx()
        table_right = table_left + table_parent.winfo_width()
        for row in visible_rows:
            positions = []
            for key in ("quality", "count", "cells", "probability", "state"):
                widget = row[key]
                left = widget.winfo_rootx()
                right = left + widget.winfo_width()
                assert table_left <= left < right <= table_right + 1
                positions.append(left)
            assert positions == sorted(positions)

        ov._select_detail_composition_filter("q6")
        root.update_idletasks()
        root.update()
        visible_rows = [
            row
            for row in ov.detail_composition_rows
            if row["quality"].winfo_ismapped()
        ]
        assert len(visible_rows) == 8
        assert "其他 14.0%" in str(ov.detail_composition_footer.cget("text"))
        assert "已截断" in str(ov.detail_composition_footer.cget("text"))
        assert "达到候选上限" in str(ov.detail_composition_footer.cget("text"))

        first_geometry = root.geometry()
        root.update_idletasks()
        root.update()
        assert root.geometry() == first_geometry
        assert (
            ov.detail_composition_card.master.winfo_rootx()
            + ov.detail_composition_card.master.winfo_width()
            <= root.winfo_rootx() + root.winfo_width() + 1
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_native_2401_producer_reaches_visible_detail_rows_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    summary = _native_2401_summary(
        tmp_path / f"native-2401-{dpi_scale}",
        phase="settled",
    )
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.attributes("-alpha", 0.0)
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path
            / f"native-2401-overlay-{dpi_scale}"
            / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        target_height = max(700, min(1300, root.winfo_screenheight() - 40))
        root.geometry(f"510x{target_height}+0+0")
        ov._custom_details_size = (510, target_height)
        ov.toggle_details()
        ov._update_detail_rows(summary)
        root.update_idletasks()
        root.update()

        visible_rows = [
            row
            for row in ov.detail_composition_rows
            if row["quality"].winfo_ismapped()
        ]
        expected = (
            ("红", "3", "7"),
            ("金", "1", "1"),
            ("紫", "12", "49"),
            ("蓝", "6", "44"),
            ("白绿", "4", "21"),
        )
        assert len(visible_rows) == len(expected)
        for row, (quality, count, cells) in zip(
            visible_rows,
            expected,
            strict=True,
        ):
            assert str(row["quality"].cget("text")) == quality
            assert str(row["count"].cget("text")) == count
            assert str(row["cells"].cget("text")) == cells
            assert str(row["probability"].cget("text")) == "100%"
            assert str(row["state"].cget("text")) == "锁定"

        assert str(ov.detail_composition_footer.cget("text")) == (
            "选择品质查看完整候选 · 概率不重新归一化"
        )
        card = ov.detail_composition_card.master
        assert card.winfo_rootx() >= root.winfo_rootx()
        assert (
            card.winfo_rootx() + card.winfo_width()
            <= root.winfo_rootx() + root.winfo_width() + 1
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_settlement_match_record_action_keeps_compact_type_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path
            / f"match-record-action-{dpi_scale}"
            / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        ov.render(_native_2401_summary(tmp_path / f"match-{dpi_scale}", phase="settled"))
        root.update_idletasks()
        root.update()
        ov._apply_action_cell("可出价", next_step="补紫/金件数或均格")
        root.update_idletasks()
        action = ov.action_rows["动作"]
        baseline_font = tkfont.Font(font=action.cget("font"))
        baseline_size = abs(baseline_font.actual("size"))
        baseline_weight = str(baseline_font.actual("weight"))

        ov._apply_action_cell("结算完成", next_step="复核结算")
        root.update_idletasks()

        action_font = tkfont.Font(font=action.cget("font"))
        assert action.cget("text") == "查看对局记录"
        assert abs(action_font.actual("size")) == baseline_size
        assert str(action_font.actual("weight")) == baseline_weight
        assert action.winfo_x() + action.winfo_width() <= action.master.winfo_width()

        ov._apply_action_cell("可出价", next_step="补紫/金件数或均格")
        root.update_idletasks()
        restored_font = tkfont.Font(font=action.cget("font"))
        assert str(restored_font.actual("weight")) == baseline_weight
        assert abs(restored_font.actual("size")) == baseline_size
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
@pytest.mark.parametrize("entry_state", ("ordinary", "compatibility"))
def test_settlement_action_restores_preexisting_real_tk_binding_at_all_dpi(
    tmp_path,
    dpi_scale,
    entry_state,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path
            / f"match-record-binding-{entry_state}-{dpi_scale}"
            / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        action = ov.action_rows["动作"]
        calls: list[str] = []
        action.bind("<Button-1>", lambda _event: calls.append("ordinary"))
        original_binding = action.bind("<Button-1>")
        ov._open_match_records = lambda _event=None: calls.append("settlement")
        ov._open_compatibility_guidance = (
            lambda _event=None: calls.append("compatibility")
        )

        if entry_state == "compatibility":
            ov._apply_action_cell(
                "查看兼容步骤",
                next_step="按兼容顺序重启",
                compatibility_guidance=True,
            )
            action.event_generate("<Button-1>", x=2, y=2)
            root.update()
            assert calls == ["compatibility"]

        ov._apply_action_cell("结算完成", next_step="复核结算")
        action.event_generate("<Button-1>", x=2, y=2)
        root.update()
        assert calls[-1] == "settlement"
        assert "ordinary" not in calls

        ov._apply_action_cell("可出价", next_step="补紫/金件数或均格")
        root.update_idletasks()
        assert action.bind("<Button-1>") == original_binding
        action.event_generate("<Button-1>", x=2, y=2)
        root.update()
        assert calls[-1] == "ordinary"
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


def test_detail_composition_real_tk_state_switches_and_fold_controls(tmp_path) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        root.attributes("-alpha", 0.0)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "states" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        ov.toggle_details()

        for status, message in (
            ("loading", "组合计算中"),
            ("error", "组合暂不可用"),
            ("conflict", "组合数据存在冲突"),
            ("no_reachable", "没有可达组合"),
            ("stale", "显示的是旧组合结果"),
        ):
            ov._render_detail_composition(
                {
                    "quality_composition": _current_quality_composition(
                        {"status": status}
                    )
                }
            )
            root.update_idletasks()
            assert str(ov.detail_composition_footer.cget("text")) == message

        ov._toggle_detail_composition()
        root.update_idletasks()
        assert ov.detail_composition_content.winfo_ismapped() == 0
        assert str(ov.detail_composition_toggle.cget("text")) == "展开"
        ov._toggle_detail_composition()
        ov._toggle_detail_diagnostics()
        ov._update_detail_rows(_composition_fixture())
        root.update_idletasks()
        assert ov.detail_composition_content.winfo_ismapped() == 1
        assert ov.detail_diagnostics_content.winfo_ismapped() == 1
        assert str(ov.detail_diagnostics_toggle.cget("text")) == "收起"
        assert "22/24件" in str(ov.evidence_rows["仓库轮廓"].cget("text"))
        assert "public_outline" in str(
            ov.evidence_rows["仓库轮廓"].cget("text")
        )
        assert "12x12 · 144格" in str(
            ov.evidence_rows["仓库轮廓"].cget("text")
        )
        assert str(ov.detail_rows["轮廓值"].cget("text")) == (
            "底价 888,888 · P50 1,288,888 · P90 2,188,888"
        )
        incomplete = _composition_fixture()
        incomplete["red_outline_value_summary"] = {
            "status": "outline_complete",
            "eligibility_complete": False,
            "floor_value": 888888,
            "p50_value": 1288888,
            "p90_value": 2188888,
        }
        ov._update_detail_rows(incomplete)
        assert str(ov.detail_rows["轮廓值"].cget("text")) == (
            "未满足完整轮廓条件"
        )
        assert ov.minimap_card.master.winfo_ismapped() == 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_detail_composition_real_tk_exposes_malformed_ready_payloads(
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    exact = {
        "status": "exact",
        "source": "exact_evidence",
        "lock_strength": "exact",
        "candidates": [{"count": 2, "cells": 12, "probability": 1.0}],
        "shown_probability": 1.0,
        "other_probability": 0.0,
        "truncated": False,
        "cap_hit": False,
        "pricing_posterior": True,
        "display_reconciled": False,
    }
    try:
        root.attributes("-alpha", 0.0)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "fail-closed" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        ov.toggle_details()

        current = _current_quality_composition(
            {"status": "ready", "qualities": {"q6": copy.deepcopy(exact)}}
        )
        ov._render_detail_composition({"quality_composition": current})
        root.update_idletasks()
        assert str(ov.detail_composition_footer.cget("text")).startswith(
            "选择品质"
        )

        malformed = (
            (
                {"status": "ready", "q6": exact},
                "品质组合 schema 代际冲突",
            ),
            (
                {"status": "ready", "qualities": {"q6": exact}},
                "品质组合 schema 代际冲突",
            ),
            (
                {"status": "ready", "qualities": []},
                "品质组合 schema 代际冲突",
            ),
            (
                {
                    "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
                    "status": "ready",
                    "qualities": {"q6": True},
                },
                "组合数据存在冲突",
            ),
            (
                _current_quality_composition(
                {
                    "status": "ready",
                    "qualities": {
                        "q6": {
                            **exact,
                            "status": "ready",
                            "lock_strength": "probabilistic",
                            "candidates": [
                                {
                                    "count": True,
                                    "cells": 12,
                                    "probability": 1.0,
                                }
                            ],
                        }
                    },
                },
                ),
                "组合数据存在冲突",
            ),
        )
        for block, expected_footer in malformed:
            ov._render_detail_composition({"quality_composition": block})
            root.update_idletasks()
            assert str(ov.detail_composition_footer.cget("text")) == expected_footer
            visible_states = {
                str(row["state"].cget("text"))
                for row in ov.detail_composition_rows
                if row["state"].winfo_ismapped()
            }
            assert "冲突" in visible_states
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_detail_composition_natural_window_and_scroll_stay_inside_physical_workarea(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.attributes("-alpha", 0.0)
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"natural-dpi-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry("+0+0")
        ov.toggle_details()
        ov._update_detail_rows(_composition_fixture())
        root.update_idletasks()
        root.update()

        first_geometry = root.geometry()
        root.update_idletasks()
        root.update()
        assert root.geometry() == first_geometry

        outer = _physical_outer_rect(module, root)
        work = module._monitor_work_area_for_point(root, outer[0], outer[1])
        assert work[0] <= outer[0] < outer[2] <= work[2]
        assert work[1] <= outer[1] < outer[3] <= work[3]

        scroll_region = tuple(
            int(float(part))
            for part in str(ov.details_scroll_canvas.cget("scrollregion")).split()
        )
        assert len(scroll_region) == 4
        assert scroll_region[3] > ov.details_scroll_canvas.winfo_height()
        assert 210 <= ov.details_scroll_canvas.winfo_height() <= 360
        assert ov.details_scroll_canvas.yview()[0] == pytest.approx(0.0)

        canvas = ov.details_scroll_canvas
        before = canvas.yview()
        event = SimpleNamespace(
            x_root=canvas.winfo_rootx() + 10,
            y_root=canvas.winfo_rooty() + 10,
            delta=-120,
        )
        assert ov._scroll_details_viewport(event) == "break"
        root.update_idletasks()
        assert canvas.yview()[0] > before[0]
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


def test_details_composition_real_tk_rollback_restores_legacy_cards(
    monkeypatch, tmp_path
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setenv("BIDKING_DISABLE_DETAILS_COMPOSITION_VIEW", "1")
    root = _new_root()
    try:
        root.attributes("-alpha", 0.0)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "rollback" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        ov.toggle_details()
        root.update_idletasks()
        root.update()

        assert ov.details_composition_view_enabled is False
        assert "匹配" in ov.evidence_rows
        assert "决策" in ov.detail_rows
        assert not hasattr(ov, "detail_composition_card")
        assert ov.manual_card.master.winfo_ismapped() == 1
        assert ov.minimap_card.master.winfo_ismapped() == 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_startup_reveal_keeps_real_tk_transparent_until_taskbar_ready(monkeypatch, tmp_path) -> None:
    """生产启动保持真实映射尺寸，但 taskbar 完成前透明度必须为 0。"""
    module = _ahmad_overlay_module()
    root = _new_root()
    root.attributes("-alpha", 0.0)
    appwindow_states: list[tuple[bool, float]] = []
    try:
        module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "startup" / "latest_snapshot.json",
            interval_ms=100_000,
            show_taskbar=True,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        assert root.winfo_ismapped(), "透明构建必须保持映射，否则默认窗口尺寸会从 510px 缩到 440px"
        assert float(root.attributes("-alpha")) == pytest.approx(0.0)

        monkeypatch.setattr(module.os, "name", "nt", raising=False)
        monkeypatch.setattr(
            module,
            "_apply_windows_appwindow",
            lambda widget, *, enabled: appwindow_states.append(
                (bool(widget.winfo_ismapped()), float(widget.attributes("-alpha")))
            ),
        )
        module._reveal_root_when_ready(root, show_taskbar=True, restore_alpha=1.0)
        root.update_idletasks()

        assert appwindow_states == [(True, 0.0)]
        assert root.winfo_ismapped(), "taskbar 注册完成后 root 没有显现"
        assert float(root.attributes("-alpha")) == pytest.approx(1.0)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.fixture(scope="module")
def overlay(tmp_path_factory):
    module = _ahmad_overlay_module()
    root = _new_root()
    ov = module.AhmadTkOverlay(
        root,
        # 不存在的快照 -> standby, 不会拉起 monitor
        snapshot_path=tmp_path_factory.mktemp("ov") / "latest_snapshot.json",
        interval_ms=100_000,
        ui_prefs_enabled=False,
    )
    root.update()
    try:
        yield ov, root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_action_text_keeps_real_tk_contrast_across_theme_switch(overlay) -> None:
    """真实 Tk：结算点击态跨主题后恢复建议，前景色必须跟随当前主题。"""
    module = _ahmad_overlay_module()
    ov, root = overlay
    action = ov.action_rows["动作"]

    try:
        ov.apply_theme("warm")
        ov._apply_action_cell("结算完成", next_step="复核结算")
        assert str(action.cget("fg")).lower() == module.THEMES["warm"]["ACCENT"]

        ov.apply_theme("dark")
        root.update_idletasks()
        assert str(action.cget("fg")).lower() == module.THEMES["dark"]["TEXT"]
        assert str(action.cget("bg")).lower() == module.THEMES["dark"]["PANEL"]

        ov._apply_action_cell("结算完成", next_step="复核结算")
        ov.apply_theme("warm")
        root.update_idletasks()
        assert str(action.cget("fg")).lower() == module.THEMES["warm"]["TEXT"]
        assert str(action.cget("bg")).lower() == module.THEMES["warm"]["PANEL"]
    finally:
        ov.apply_theme("warm")
        root.update_idletasks()


def test_game_launch_candidate_is_clickable_only_for_exact_ready_state(overlay) -> None:
    module = _ahmad_overlay_module()
    ov, root = overlay
    candidate = ov.action_rows["候选"]
    launch_id = "launch-real-tk"
    capture_status = {
        "source": "wss_local",
        "_current_launch_id": launch_id,
        "_network_connection_status": {
            "schema_version": 1,
            "updated_at": time.time(),
            "launch_id": launch_id,
            "game_connection_state": "not_running",
            "wss_intercept_state": "ready",
            "capture_decode_state": "idle",
            "evidence": {
                "game_running": False,
                "player_log_error_counts": {},
                "active_flows": 0,
                "wss_raw_count": 0,
                "accepted_frames": 0,
                "ignored_frames": 0,
                "helper_phase": "helper_running",
                "helper_error_code": "",
            },
        },
        "_wss_helper_status": {
            "schema_version": 2,
            "launch_id": launch_id,
            "phase": "helper_running",
            "error_code": "",
        },
    }
    original_probe = ov._game_process_state_probe
    original_launcher = ov._game_uri_launcher
    original_capture_status = ov._capture_status
    original_context = ov._game_launch_candidate_context
    original_manual_active = ov._manual_active
    original_scale = ov._ui_scale
    launched: list[str] = []
    try:
        ov._game_launch_candidate_context = True
        ov._game_launch_pending_until = 0.0
        ov._game_launch_error_until = 0.0
        ov._game_process_state_probe = lambda _name: False
        ov._game_uri_launcher = lambda: launched.append(module.STEAM_GAME_URI)
        ov._capture_status = lambda: capture_status

        for scale in (1.0, 1.25, 1.5, 1.75):
            ov._ui_scale = scale
            ov._apply_ui_scale()
            ov._sync_game_launch_candidate(capture_status)
            root.update_idletasks()
            root.update()
            font = tkfont.Font(font=candidate.cget("font"))
            assert candidate.cget("text") == module.GAME_LAUNCH_READY_TEXT
            assert ov._candidate_row_label.cget("text") == "启动"
            assert ov.action_rows["总格"].cget("text") == "-"
            assert candidate.cget("cursor") == "hand2"
            assert ov._game_launch_candidate_bindings
            assert font.measure(module.GAME_LAUNCH_READY_TEXT) <= candidate.winfo_width()
            assert candidate.winfo_x() + candidate.winfo_width() <= candidate.master.winfo_width()

        candidate.event_generate("<Button-1>", x=2, y=2)
        root.update_idletasks()
        assert launched == [module.STEAM_GAME_URI]
        assert candidate.cget("text") == module.GAME_LAUNCH_PENDING_TEXT
        assert not ov._game_launch_candidate_bindings
        candidate.event_generate("<Button-1>", x=2, y=2)
        assert launched == [module.STEAM_GAME_URI]

        ov._game_launch_pending_until = 0.0
        ov._game_process_state_probe = lambda _name: True
        ov._sync_game_launch_candidate(capture_status)
        root.update_idletasks()
        assert candidate.cget("text") == "-"
        assert ov._candidate_row_label.cget("text") == "总件"
        assert candidate.cget("cursor") == ""
        assert not ov._game_launch_candidate_bindings

        ov._game_process_state_probe = lambda _name: False
        ov._manual_active = True
        ov._sync_game_launch_candidate(capture_status)
        root.update_idletasks()
        assert candidate.cget("text") == "-"
        assert not ov._game_launch_candidate_bindings

        ov._manual_active = False
        ov._sync_game_launch_candidate(capture_status)
        ov._game_uri_launcher = lambda: (_ for _ in ()).throw(OSError("blocked"))
        candidate.event_generate("<Button-1>", x=2, y=2)
        root.update_idletasks()
        assert candidate.cget("text") == module.GAME_LAUNCH_ERROR_TEXT
        assert not ov._game_launch_candidate_bindings

        ov._game_launch_error_until = 0.0
        ov._sync_game_launch_candidate(capture_status)
        ov._set_candidate_autofit("估总件 19/31/43 · 估总格 59/76/99格")
        root.update_idletasks()
        assert candidate.cget("text") != module.GAME_LAUNCH_READY_TEXT
        assert candidate.cget("cursor") == ""
        assert not ov._game_launch_candidate_bindings
    finally:
        ov._game_process_state_probe = original_probe
        ov._game_uri_launcher = original_launcher
        ov._capture_status = original_capture_status
        ov._game_launch_candidate_context = original_context
        ov._manual_active = original_manual_active
        ov._game_launch_pending_until = 0.0
        ov._game_launch_error_until = 0.0
        ov._ui_scale = original_scale
        ov._apply_ui_scale()
        ov._set_candidate_autofit("-")
        root.update_idletasks()


def test_candidate_count_and_grid_use_two_aligned_rows(overlay) -> None:
    ov, root = overlay
    full_summary = "总件 38 · 估总格 70/82/94格"

    ov._set_candidate_dimension_rows(
        {"evidence": {"candidate_summary": full_summary}}
    )
    root.update_idletasks()

    assert ov._candidate_row_label.cget("text") == "总件"
    assert ov.action_rows["候选"].cget("text") == "38件"
    assert ov.action_rows["总格"].cget("text") == "估 70/82/94格"
    assert ov.action_rows["候选"]._hover_tip.text == full_summary
    assert ov.action_rows["总格"]._hover_tip.text == full_summary
    assert abs(
        ov.red_rows["白绿"].master.winfo_rooty()
        - ov.action_rows["候选"].master.winfo_rooty()
    ) <= 1
    assert abs(
        ov.red_rows["蓝品"].master.winfo_rooty()
        - ov.action_rows["总格"].master.winfo_rooty()
    ) <= 1

    ov._set_candidate_dimension_rows({"evidence": {}})
    root.update_idletasks()
    assert ov.action_rows["候选"].cget("text") == "-"
    assert ov.action_rows["总格"].cget("text") == "-"


def test_restart_guard_is_visible_in_real_tk_without_closing_overlay(
    monkeypatch,
    overlay,
) -> None:
    ov, root = overlay
    # `_ahmad_overlay_module()` reloads the file on every call. Patch the exact
    # globals dict owned by this fixture's class, otherwise the real restart path
    # would still run while a separately loaded module is patched.
    restart_globals = ov.restart_calculator.__func__.__globals__
    relaunch_calls: list[bool] = []
    monkeypatch.setitem(
        restart_globals,
        "_named_process_running_state",
        lambda _name: True,
    )
    monkeypatch.setitem(
        restart_globals,
        "_spawn_restart_relauncher",
        lambda *_args, **_kwargs: relaunch_calls.append(True),
    )
    try:
        assert ov.restart_calculator() == "break"
        root.update_idletasks()
        assert root.winfo_exists()
        assert str(ov.status.cget("text")) == restart_globals["RESTART_GAME_RUNNING_TEXT"]
        assert ov.restart_tip.text == restart_globals["RESTART_GAME_RUNNING_TEXT"]
        assert relaunch_calls == []
    finally:
        ov.status.configure(text="")
        ov.restart_tip.set_text(restart_globals["RESTART_TIP"])
        root.update_idletasks()


def test_header_refresh_is_ui_only_in_real_tk(monkeypatch, overlay) -> None:
    ov, root = overlay
    refresh_globals = ov.refresh_interface.__func__.__globals__
    process_calls: list[bool] = []
    relaunch_calls: list[bool] = []
    cleanup_calls: list[bool] = []
    monkeypatch.setitem(
        refresh_globals,
        "_named_process_running_state",
        lambda _name: process_calls.append(True),
    )
    monkeypatch.setitem(
        refresh_globals,
        "_spawn_restart_relauncher",
        lambda *_args, **_kwargs: relaunch_calls.append(True),
    )
    monkeypatch.setattr(ov, "_run_exit_cleanup", lambda: cleanup_calls.append(True))
    stop_pids_before = tuple(ov._stop_pids_on_exit)

    ov.restart_button.event_generate("<Button-1>", x=5, y=5)
    root.update_idletasks()

    assert root.winfo_exists()
    assert ov.refresh_tip.text == refresh_globals["REFRESH_TIP"]
    assert process_calls == []
    assert relaunch_calls == []
    assert cleanup_calls == []
    assert tuple(ov._stop_pids_on_exit) == stop_pids_before


def test_resize_affordances_are_visible_consistent_and_still_draggable(overlay) -> None:
    """顶部保留明确箭头；footer 使用熟悉的角落三角，两处仍可直接拖动。"""
    ov, root = overlay
    module = _ahmad_overlay_module()
    root.geometry("430x520+20+0")
    root.update_idletasks()
    root.update()

    for grip, tip, glyph, color in (
        (ov.top_resize_grip, ov.top_resize_tip, "↘", module.WARM),
        (ov.resize_grip, ov.resize_grip_tip, "◢", module.BORDER),
    ):
        assert grip.winfo_ismapped()
        assert str(grip.cget("text")) == glyph
        assert str(grip.cget("fg")).lower() == color.lower()
        assert str(grip.cget("cursor")) == "sizing"
        assert f"拖动 {glyph} 缩放窗口" in tip.text
        assert grip.bind("<ButtonPress-1>")
        assert grip.bind("<B1-Motion>")
        assert grip.bind("<ButtonRelease-1>")

    assert hasattr(ov.top_resize_grip, "_rounded_holder")
    assert (
        ov.top_resize_grip._rounded_holder._round_card["outline"].lower()
        == module.WARM.lower()
    )
    assert not hasattr(ov.resize_grip, "_rounded_holder")
    assert str(ov.resize_grip.cget("bg")).lower() == module.BG.lower()
    assert str(ov.resize_grip.cget("fg")).lower() == module.BORDER.lower()
    assert int(ov.resize_grip.cget("highlightthickness")) == 0

    header_controls = (
        ov.top_resize_grip.master,
        ov.restart_button.master,
        ov.topmost_button.master,
        ov.close_button.master,
    )
    assert all(widget.winfo_ismapped() for widget in header_controls)
    assert all(
        left.winfo_x() + left.winfo_width() <= right.winfo_x()
        for left, right in zip(header_controls, header_controls[1:])
    ), "最窄 mini 宽度下，缩放按钮与刷新/置顶/关闭按钮重叠"
    header_sizes = {
        (widget.winfo_width(), widget.winfo_height())
        for widget in header_controls
    }
    assert len(header_sizes) == 1, f"顶部四个操作按钮尺寸不一致: {header_sizes}"

    footer_grip = ov.resize_grip
    footer_right = ov.footer_row.winfo_rootx() + ov.footer_row.winfo_width()
    grip_right = footer_grip.winfo_rootx() + footer_grip.winfo_width()
    support_right = ov.support_frame.winfo_rootx() + ov.support_frame.winfo_width()
    assert grip_right == footer_right, "右下缩放按钮没有贴齐 footer 的可用右边界"
    assert support_right <= footer_grip.winfo_rootx(), "右下缩放按钮与猫猫入口重叠"
    assert 8 <= footer_grip.winfo_width() < ov.top_resize_grip.master.winfo_width()
    assert 12 <= footer_grip.winfo_height() <= ov.top_resize_grip.master.winfo_height()

    # 真拖动链会临时把 tooltip 换成实时尺寸；松手后两处必须各自恢复原 glyph 文案。
    for grip, tip, glyph in (
        (ov.top_resize_grip, ov.top_resize_tip, "↘"),
        (ov.resize_grip, ov.resize_grip_tip, "◢"),
    ):
        start_x = grip.winfo_rootx() + max(1, grip.winfo_width() // 2)
        start_y = grip.winfo_rooty() + max(1, grip.winfo_height() // 2)
        ov._begin_resize(SimpleNamespace(widget=grip, x_root=start_x, y_root=start_y))
        ov._resize_window(
            SimpleNamespace(widget=grip, x_root=start_x + 8, y_root=start_y + 6)
        )
        ov._end_resize(
            SimpleNamespace(widget=grip, x_root=start_x + 8, y_root=start_y + 6)
        )
        root.update_idletasks()
        root.update()
        assert f"拖动 {glyph} 缩放窗口" in tip.text


def test_paid_membership_real_tk_entry_appears_and_is_clickable_in_last_90_days(
    monkeypatch, tmp_path
) -> None:
    """完整 Overlay 真 Tk 门：隐藏恢复后高度贴合，且临期会员双入口仍打开真实对话框。"""
    module = _ahmad_overlay_module()
    from bidking_lab.secure import enforce

    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    state = {
        "run_full": True,
        "mode": "paid",
        "seconds_left": 89 * 86400,
        "warn": False,
        "text": "会员 · 剩余 89 天",
    }
    monkeypatch.setattr(enforce, "activation_state", lambda: state)

    root = _new_root()
    try:
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "activation-height" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        ov._act_ready = True
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False

        def pump(cycles: int = 3) -> None:
            for _ in range(cycles):
                root.update_idletasks()
                root.update()

        def natural_mini_height() -> int:
            pump()
            actual = int(root.winfo_height())
            outer_req = int(ov.outer.winfo_reqheight())
            shell_req = int(ov.shell.winfo_reqheight())
            assert actual == outer_req == shell_req
            return actual

        pump()
        assert ov.activation_row.winfo_manager() == "pack"
        assert ov.activation_row.winfo_ismapped()
        visible_height = natural_mini_height()
        activation_height = int(ov.activation_row.winfo_reqheight())
        assert activation_height > 0
        initial_position = (int(root.winfo_x()), int(root.winfo_y()))

        # 记录真实执行次数但不绕过实现；每次 Map 最多消费一个 pending。
        refit_runs: list[tuple[object, bool]] = []
        real_refit = ov._do_refit_mini_for_activation

        def counted_refit() -> None:
            refit_runs.append((state.get("seconds_left"), bool(root.winfo_ismapped())))
            real_refit()

        ov._do_refit_mini_for_activation = counted_refit

        # 已显示的完整会员行在 withdraw 后切长期会员：布局立即移除，高度在 root Map 后只重贴一次。
        root.withdraw()
        root.update_idletasks()
        assert ov.activation_row.winfo_manager() == "pack"
        assert not ov.activation_row.winfo_ismapped()
        state.update(seconds_left=90 * 86400 + 1, text="会员 · 剩余 90 天")
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        assert ov.activation_row.winfo_manager() == ""
        assert ov.activation_action.winfo_manager() == ""
        assert ov._activation_refit_pending is True
        assert refit_runs == []
        ov._on_root_map_activation_refit(SimpleNamespace(widget=ov.activation_row))
        assert ov._activation_refit_pending is True
        assert ov._activation_refit_after_id is None

        # 历史无期限状态与重复 tick 不得累积 pending 或提前执行隐藏窗口 geometry。
        state.update(seconds_left=None, text="会员")
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        assert ov.activation_row.winfo_manager() == ""
        for _ in range(3):
            assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        assert ov._activation_refit_pending is True
        assert refit_runs == []

        root.deiconify()
        pump()
        assert ov.activation_row.winfo_manager() == ""
        assert not ov.activation_row.winfo_ismapped()
        assert not ov.activation_action.winfo_ismapped()
        assert len(refit_runs) == 1
        assert ov._activation_refit_pending is False
        assert ov._activation_refit_after_id is None
        hidden_height = natural_mini_height()
        assert visible_height - hidden_height == activation_height
        assert (int(root.winfo_x()), int(root.winfo_y())) == initial_position
        for _ in range(3):
            assert module.AhmadTkOverlay._update_activation_ui(ov) is False
            pump(1)
        assert len(refit_runs) == 1
        assert int(root.winfo_height()) == hidden_height

        # 90 天整是可见边界；隐藏窗口内恢复 pack，Map 后恢复含会员行的自然高度。
        root.withdraw()
        root.update_idletasks()
        state.update(seconds_left=90 * 86400, text="会员 · 剩余 90 天")
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        assert ov.activation_row.winfo_manager() == "pack"
        assert ov.activation_action.winfo_manager() == "pack"
        assert not ov.activation_row.winfo_ismapped()
        assert ov._activation_refit_pending is True
        before_map_runs = len(refit_runs)
        root.deiconify()
        pump()

        assert ov.activation_row.winfo_ismapped()
        assert ov.activation_action.winfo_ismapped()
        assert ov.activation_action.cget("text") == "更换 token"
        assert ov.activation_label.cget("text") == "会员 · 剩余 90 天"
        assert ov.activation_label.cget("cursor") == "hand2"
        assert len(refit_runs) == before_map_runs + 1
        assert natural_mini_height() == visible_height

        # 两个产品入口都走真实 token 对话框；第二次点击复用同一实例，且三个既有操作仍在。
        ov.activation_action.invoke()
        pump(1)
        dialog = ov._token_dialog
        assert dialog is not None
        assert dialog.winfo_ismapped()
        label_hits: list[tk.Widget] = []
        ov.activation_label.bind(
            "<Button-1>", lambda event: label_hits.append(event.widget), add="+"
        )
        ov.activation_label.event_generate("<Button-1>")
        pump(1)
        assert label_hits == [ov.activation_label]
        assert ov._token_dialog is dialog
        button_widgets: dict[str, tk.Button] = {}
        labels: set[str] = set()
        label_widgets: list[tk.Label] = []
        entries: list[tk.Entry] = []
        mapped_widgets: list[tk.Widget] = []
        stack = [dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if widget.winfo_ismapped():
                mapped_widgets.append(widget)
            if isinstance(widget, tk.Button):
                button_widgets[str(widget.cget("text"))] = widget
            elif isinstance(widget, tk.Label):
                labels.add(str(widget.cget("text")))
                label_widgets.append(widget)
            elif isinstance(widget, tk.Entry):
                entries.append(widget)
        assert {"激活", "取消", "换绑/清除"} <= set(button_widgets)
        assert {
            "所有档位均可正常使用计算器",
            "绑定设备：",
            "可漫游：",
            "30天 ¥12.88",
            "90天 ¥28.88",
            "365天 ¥68.88",
            "30天 ¥19.99",
            "90天 ¥39.99",
            "365天 ¥89.99",
            "｜",
            "首次与再次购买同价；再次购买会保留并延长已有时间",
            "当前为一次性预付，不会自动扣款；更换档位请先联系",
            "猫猫：免费10分钟 ｜ 30天12小时 ｜ 31–89天24小时 ｜ 90天及以上订阅期内不限时",
            "90/365天可用：我的记录 ｜ 365天另含：完整私有大数据与高级个性化",
            "音效、动画与互动成长将逐步扩充",
            "漫游档支持换设备重新绑定 ｜ 历史永久用户保留既有权益",
        } <= labels
        assert not any("新购" in text or "续费" in text for text in labels)

        price_box = next(
            widget.master
            for widget in label_widgets
            if str(widget.cget("text")) == "绑定设备："
        )
        price_cells = {
            (int(widget.grid_info()["row"]), int(widget.grid_info()["column"])): str(
                widget.cget("text")
            )
            for widget in label_widgets
            if widget.master is price_box and widget.winfo_manager() == "grid"
        }
        assert {
            (1, 0): "绑定设备：",
            (1, 1): "30天 ¥12.88",
            (1, 2): "｜",
            (1, 3): "90天 ¥28.88",
            (1, 4): "｜",
            (1, 5): "365天 ¥68.88",
            (2, 0): "可漫游：",
            (2, 1): "30天 ¥19.99",
            (2, 2): "｜",
            (2, 3): "90天 ¥39.99",
            (2, 4): "｜",
            (2, 5): "365天 ¥89.99",
        }.items() <= price_cells.items()
        for separator_column in (2, 4):
            separator_x = {
                widget.winfo_x()
                for widget in label_widgets
                if widget.master is price_box
                and widget.winfo_manager() == "grid"
                and int(widget.grid_info()["row"]) in (1, 2)
                and int(widget.grid_info()["column"]) == separator_column
            }
            assert len(separator_x) == 1, (
                f"价格分隔符第 {separator_column} 列未对齐: {separator_x}"
            )

        # 空 token 仍停留在本地校验，不触发激活线程；清除按钮保持可用但不在测试中改本机状态。
        token_entry = next(entry for entry in entries if str(entry.cget("state")) == "normal")
        token_entry.delete(0, "end")
        button_widgets["激活"].invoke()
        pump(1)
        assert "请先粘贴 token" in labels | {
            str(widget.cget("text"))
            for widget in mapped_widgets
            if isinstance(widget, tk.Label)
        }
        assert str(button_widgets["换绑/清除"].cget("state")) == "normal"

        # 聊天正文/换行/额外前后缀必须在本地拒绝；不能先 strip 后发往服务端。
        token_entry.insert(0, "群友 08-01: v1|buyer|1785500000." + "a" * 64)
        with monkeypatch.context() as local_patch:
            local_patch.setattr(
                module.threading,
                "Thread",
                lambda *_args, **_kwargs: pytest.fail(
                    "invalid token input must not start an activation thread"
                ),
            )
            button_widgets["激活"].invoke()
        pump(1)
        assert "粘贴内容不是单一token，请重新复制纯token" in {
            str(widget.cget("text"))
            for widget in mapped_widgets
            if isinstance(widget, tk.Label)
        }
        assert str(button_widgets["激活"].cget("state")) == "normal"
        token_entry.delete(0, "end")

        # 联系方式仍是可选择、可复制的只读输入框。
        contact_entry = next(
            entry for entry in entries if str(entry.cget("state")) == "readonly"
        )
        previous_clipboard: str | None
        try:
            previous_clipboard = root.clipboard_get()
        except tk.TclError:
            previous_clipboard = None
        try:
            contact_entry.focus_set()
            contact_entry.selection_range(0, "end")
            pump(1)
            assert contact_entry.selection_present()
            contact_entry.event_generate("<<Copy>>")
            pump(1)
            assert root.clipboard_get() == contact_entry.get()
        finally:
            root.clipboard_clear()
            if previous_clipboard is not None:
                root.clipboard_append(previous_clipboard)

        # 对话框与所有 mapped 子控件均在工作区/父容器四边之内。
        dialog_x = int(dialog.winfo_rootx())
        dialog_y = int(dialog.winfo_rooty())
        dialog_w = int(dialog.winfo_width())
        dialog_h = int(dialog.winfo_height())
        work_left, work_top, work_right, work_bottom = module._monitor_work_area_for_point(
            root, dialog_x, dialog_y
        )
        assert work_left <= dialog_x
        assert work_top <= dialog_y
        assert dialog_x + dialog_w <= work_right + 1
        assert dialog_y + dialog_h <= work_bottom + 1
        for widget in mapped_widgets:
            if isinstance(widget, tk.Toplevel):
                continue
            parent_name = widget.winfo_parent()
            if not parent_name:
                continue
            parent = widget.nametowidget(parent_name)
            assert widget.winfo_x() >= -1
            assert widget.winfo_y() >= -1
            assert widget.winfo_x() + widget.winfo_width() <= parent.winfo_width() + 1
            assert widget.winfo_y() + widget.winfo_height() <= parent.winfo_height() + 1

        # 取消必须走正式关闭路径；再次从同一产品入口打开时创建新实例且合同仍完整。
        button_widgets["取消"].invoke()
        pump(1)
        assert ov._token_dialog is None
        assert not dialog.winfo_exists()
        ov.activation_action.invoke()
        pump(1)
        reopened_dialog = ov._token_dialog
        assert reopened_dialog is not None
        assert reopened_dialog is not dialog
        assert reopened_dialog.winfo_ismapped()
        reopened_labels = {
            str(widget.cget("text"))
            for widget in reopened_dialog.winfo_children()
            if isinstance(widget, tk.Label)
        }
        assert "粘贴你的激活 token" in reopened_labels
        reopened_buttons = []
        stack = [reopened_dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if isinstance(widget, tk.Button):
                reopened_buttons.append(widget)
        next(button for button in reopened_buttons if button.cget("text") == "取消").invoke()
        pump(1)
        assert ov._token_dialog is None

        # 长期 → 89 天 → 长期循环三次：每次 Map 只执行一个 refit，高度不累积也不双向震荡。
        hidden_heights: list[int] = []
        visible_heights: list[int] = []
        for _ in range(3):
            root.withdraw()
            root.update_idletasks()
            state.update(seconds_left=90 * 86400 + 1, text="会员 · 剩余 90 天")
            before_map_runs = len(refit_runs)
            assert module.AhmadTkOverlay._update_activation_ui(ov) is False
            for _ in range(3):
                assert module.AhmadTkOverlay._update_activation_ui(ov) is False
            assert ov.activation_row.winfo_manager() == ""
            assert len(refit_runs) == before_map_runs
            root.deiconify()
            pump()
            assert len(refit_runs) == before_map_runs + 1
            hidden_heights.append(natural_mini_height())

            root.withdraw()
            root.update_idletasks()
            state.update(seconds_left=89 * 86400, text="会员 · 剩余 89 天")
            before_map_runs = len(refit_runs)
            assert module.AhmadTkOverlay._update_activation_ui(ov) is False
            assert ov.activation_row.winfo_manager() == "pack"
            assert ov.activation_action.winfo_manager() == "pack"
            root.deiconify()
            pump()
            assert len(refit_runs) == before_map_runs + 1
            assert ov.activation_row.winfo_ismapped()
            assert ov.activation_action.winfo_ismapped()
            assert ov.activation_label.cget("text") == "会员 · 剩余 89 天"
            visible_heights.append(natural_mini_height())

        assert set(hidden_heights) == {hidden_height}
        assert set(visible_heights) == {visible_height}
        assert (int(root.winfo_x()), int(root.winfo_y())) == initial_position

        # 非付费、有效试用和到期状态继续显示通用入口，且保持含激活行的自然高度。
        for next_state in (
            {"run_full": False, "mode": "none", "warn": True, "text": "需激活 token"},
            {"run_full": True, "mode": "trial", "warn": False, "text": "剩余 8 小时"},
            {"run_full": False, "mode": "trial", "warn": True, "text": "试用已结束"},
        ):
            state.clear()
            state.update(next_state)
            assert module.AhmadTkOverlay._update_activation_ui(ov) is (not next_state["run_full"])
            pump()
            assert ov.activation_row.winfo_manager() == "pack"
            assert ov.activation_row.winfo_ismapped()
            assert ov.activation_action.winfo_ismapped()
            assert ov.activation_action.cget("text") == "点此输入/购买 token"
            assert natural_mini_height() == visible_height

        # details 模式不消费 mini pending，也不覆盖当前详情尺寸。
        state.clear()
        state.update(run_full=True, mode="trial", warn=False, text="剩余 8 小时")
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        ov.toggle_details()
        pump()
        assert ov.details_expanded is True
        details_size = (int(root.winfo_width()), int(root.winfo_height()))
        ov._custom_details_size = details_size
        root.withdraw()
        root.update_idletasks()
        state.clear()
        state.update(
            run_full=True,
            mode="paid",
            seconds_left=90 * 86400 + 1,
            warn=False,
            text="会员 · 剩余 90 天",
        )
        before_details_runs = len(refit_runs)
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        assert ov._activation_refit_pending is False
        root.deiconify()
        pump()
        assert ov.details_expanded is True
        assert (int(root.winfo_width()), int(root.winfo_height())) == details_size
        assert ov._custom_details_size == details_size
        assert len(refit_runs) == before_details_runs

        # 历史合同对自定义 mini 是保留宽度、在 activation 翻转后把高度重贴到自然内容。
        ov.toggle_details()
        pump()
        assert ov.details_expanded is False
        state.clear()
        state.update(run_full=True, mode="trial", warn=False, text="剩余 8 小时")
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        pump()
        custom_width, custom_height = 560, 600
        current_x, current_y = int(root.winfo_x()), int(root.winfo_y())
        ov._custom_mini_size = (custom_width, custom_height)
        root.geometry(f"{custom_width}x{custom_height}+{current_x}+{current_y}")
        pump()
        root.withdraw()
        root.update_idletasks()
        state.clear()
        state.update(
            run_full=True,
            mode="paid",
            seconds_left=90 * 86400 + 1,
            warn=False,
            text="会员 · 剩余 90 天",
        )
        assert module.AhmadTkOverlay._update_activation_ui(ov) is False
        root.deiconify()
        pump()
        custom_fitted_height = natural_mini_height()
        assert int(root.winfo_width()) == custom_width
        assert custom_fitted_height != custom_height
        assert ov._custom_mini_size == (custom_width, custom_fitted_height)
        assert (int(root.winfo_x()), int(root.winfo_y())) == (current_x, current_y)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_token_pricing_dialog_full_window_stays_inside_workarea_at_175_percent(
    monkeypatch, tmp_path
) -> None:
    """增高后的价格对话框在 175% 和屏幕边缘也必须包含标题栏完整落入工作区。"""
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", 2.333333)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pricing-175" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.update()
        left, top, right, bottom = module._monitor_work_area_for_point(root, 0, 0)
        root.geometry(
            f"+{max(left, right - int(root.winfo_width()) - 18)}"
            f"+{max(top, bottom - int(root.winfo_height()) - 70)}"
        )
        root.update()

        ov._open_token_dialog()
        root.update()
        dialog = ov._token_dialog
        assert dialog is not None
        assert dialog.winfo_viewable()
        outer_left, outer_top, outer_right, outer_bottom = module._window_outer_rect(dialog)
        work_left, work_top, work_right, work_bottom = module._monitor_work_area_for_point(
            root, outer_left, outer_top
        )
        assert work_left <= outer_left
        assert work_top <= outer_top
        assert outer_right <= work_right
        assert outer_bottom <= work_bottom
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.update_idletasks()
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("scaling", (1.333333, 1.666667, 2.0, 2.333333))
def test_network_diagnostic_profile_is_visible_and_commercially_isolated(
    monkeypatch, tmp_path, scaling
) -> None:
    """100%–175% 真 Tk：内测标记、已有 token 入口和无商业承诺必须同时成立。"""
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(module.AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", scaling)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"network-diagnostic-{scaling}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
            product_profile="network-diagnostic",
        )
        root.attributes("-topmost", False)
        root.update()

        assert ov.product_profile == "network-diagnostic"
        assert ov.product_profile_badge.winfo_ismapped()
        assert ov.product_profile_badge.cget("text") == "网络诊断内测"
        assert ov.activation_action.cget("text") == "输入已有 token"
        assert ov.support_can.winfo_manager() == ""

        ov.toggle_details()
        root.update()
        assert ov.details_expanded
        assert ov.product_profile_badge.winfo_ismapped()
        ov.toggle_details()
        root.update()
        assert not ov.details_expanded
        assert ov.product_profile_badge.winfo_ismapped()

        ov.activation_action.invoke()
        root.update()
        dialog = ov._token_dialog
        assert dialog is not None
        assert dialog.winfo_viewable()
        assert dialog.title() == "激活 BidKing · 网络诊断内测"
        widgets = _mapped_descendants(dialog)
        labels = {
            str(widget.cget("text"))
            for widget in widgets
            if isinstance(widget, tk.Label)
        }
        buttons = {
            str(widget.cget("text")): widget
            for widget in widgets
            if isinstance(widget, tk.Button)
        }
        visible = "\n".join(labels)
        assert {
            "网络诊断内测包",
            "保留已有 token 激活与计算功能",
            "购买/再次购买与猫猫新权益暂不在本包启用",
            "90 天猫猫等新权益以线上正式服务为准",
            "已有 token 可继续在此激活；本包不提供购买或新权益开通。",
            "内测支持",
        } <= labels
        assert {"激活", "取消", "换绑/清除"} <= set(buttons)
        for forbidden in (
            "30天 ¥12.88",
            "30天 ¥11.11",
            "90天 ¥28.88",
            "365天 ¥68.88",
            "30天 ¥19.99",
            "90天 ¥39.99",
            "365天 ¥89.99",
            "90/365天有效期内可用",
            "所有档位均可正常使用计算器",
        ):
            assert forbidden not in visible

        outer_left, outer_top, outer_right, outer_bottom = module._window_outer_rect(
            dialog
        )
        work_left, work_top, work_right, work_bottom = module._monitor_work_area_for_point(
            root,
            outer_left,
            outer_top,
        )
        assert work_left <= outer_left
        assert work_top <= outer_top
        assert outer_right <= work_right
        assert outer_bottom <= work_bottom
        for widget in widgets:
            if isinstance(widget, tk.Toplevel) or not widget.winfo_ismapped():
                continue
            parent_name = widget.winfo_parent()
            if not parent_name:
                continue
            parent = widget.nametowidget(parent_name)
            assert widget.winfo_x() >= -1
            assert widget.winfo_y() >= -1
            assert widget.winfo_x() + widget.winfo_width() <= parent.winfo_width() + 1
            assert widget.winfo_y() + widget.winfo_height() <= parent.winfo_height() + 1

        buttons["取消"].invoke()
        root.update()
        assert ov._token_dialog is None
        ov.activation_action.invoke()
        root.update()
        reopened = ov._token_dialog
        assert reopened is not None
        assert reopened.winfo_viewable()
        reopened_buttons = {
            str(widget.cget("text")): widget
            for widget in _mapped_descendants(reopened)
            if isinstance(widget, tk.Button)
        }
        reopened_buttons["取消"].invoke()
        root.update()
        assert ov._token_dialog is None
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.update_idletasks()
            root.destroy()
        except tk.TclError:
            pass


def test_cards_reopen_after_manual_round_trip(overlay) -> None:
    """一条真实点击序列走完三条不变量(合并成单测 = 只建一个 Tk, 把环境抖动的暴露面降到最低)。"""
    ov, root = overlay

    def pump() -> None:
        root.update_idletasks()
        root.update()

    def packed(card) -> bool:
        # card.master = holder(真正被 pack/unpack 的控件); card 本身是 _card() 返回的内层 content。
        return bool(card.master.winfo_ismapped())

    pump()
    mini_h = root.winfo_height()
    assert not packed(ov.details_card), "起始应是 mini 态"

    # ---- 不变量 2 + 1: 反复「手填 -> 实时」, 每次都必须真的打开, 且收起后 ismapped 不许说谎 ----
    for attempt in range(1, 4):
        ov.toggle_manual_mode()  # 点「手填」
        pump()
        assert packed(ov.details_card), f"第{attempt}次点手填: 详情卡片没回来"
        assert packed(ov.manual_card), f"第{attempt}次点手填: 手填卡片没回来"
        assert root.winfo_height() > mini_h, f"第{attempt}次点手填: 窗口没长高, 面板被裁在可视区外"

        ov.toggle_manual_mode()  # 点「实时」
        pump()
        assert not packed(ov.details_card), f"第{attempt}次点实时: 没收回 mini"
        # ★ 真门: 走过手填之后, content 的 mapped 标志会残留为 1(修复前实测), 必须被代理到 holder 纠正
        assert bool(ov.details_card.winfo_ismapped()) is False, (
            f"第{attempt}次收起后 card.winfo_ismapped() 仍报 True = 没代理到 holder; "
            "任何 `if not card.winfo_ismapped(): card.pack(...)` 守卫都会被永久跳过"
        )

    # ---- 不变量 3: 反复开合「详情」必须幂等, 每次展开的窗口高度一致 ----
    heights = []
    for _ in range(3):
        ov.toggle_details()
        pump()
        assert packed(ov.details_card)
        heights.append(root.winfo_height())
        ov.toggle_details()
        pump()
        assert not packed(ov.details_card)

    assert len(set(heights)) == 1, (
        f"每次展开的窗口高度应一致, 实测 {heights}(状态在漂移)。"
        "修复前首展只 fit 到 1143 而内容需要 1344 -> 详情/小地图底部被裁 ~177px, 第二次起才对。"
    )


def test_live_shrink_preview_keeps_real_toolbar_non_overlapping(overlay) -> None:
    """最坏中间态：正文仍是宽窗 160%，窗口已缩回 510；关键操作区必须先按目标档位收紧。"""
    ov, root = overlay
    assert ov.details_expanded is False
    root.geometry("1010x600+20+0")
    ov._ui_scale = 1.6
    ov._apply_ui_scale()
    root.update_idletasks()
    root.update()

    ov._resize_active = True
    ov._resize_anchor = (100, 100, 1010, 600)
    ov._resize_toolbar_compact = False
    ov._active_resize_tip = None
    try:
        # dx=-500 -> target width 510. Cancel the 140ms full-tree pass so this assertion really
        # inspects the transient state that produced the author's overlap screenshot.
        ov._apply_resize_pointer(-400, 100)
        ov._cancel_live_ui_scale_timer()
        root.update_idletasks()
        root.update()

        assert ov._ui_scale == pytest.approx(1.6), "正文已提前全量重排，测试没有命中中间态"
        assert ov._resize_toolbar_compact is True
        header_right = ov.mode_button.master
        assert ov.status.winfo_x() + ov.status.winfo_width() <= header_right.winfo_x()
        holders = (
            ov.settlement_button.master,
            ov.export_diag_button.master,
            ov.map_button.master,
            ov.theme_button.master,
            ov.manual_button.master,
            ov.mode_button,
        )
        for row in (holders[:3], holders[3:]):
            assert all(
                left.winfo_x() + left.winfo_width() <= right.winfo_x()
                for left, right in zip(row, row[1:])
            ), "缩放预览中同行工具栏按钮发生重叠"
    finally:
        ov._end_resize(SimpleNamespace(x_root=-400, y_root=100))
        root.update_idletasks()
        root.update()
        assert ov._resize_toolbar_compact is False
        assert all(widget.winfo_manager() == "pack" for widget in holders)


def test_live_resize_commits_real_scale_and_mini_height_before_release(overlay) -> None:
    """Continuous drag must expose the real layout and fitted border before ButtonRelease."""
    ov, root = overlay
    module = _ahmad_overlay_module()
    assert ov.details_expanded is False
    root.geometry("510x477+20+0")
    ov._ui_scale = module.compute_mini_ui_scale(510)
    ov._apply_ui_scale()
    root.update_idletasks()
    root.update()

    ov._resize_active = True
    ov._resize_anchor = (100, 100, 510, 477)
    ov._resize_pending_pointer = None
    ov._resize_frame_after_id = None
    ov._active_resize_tip = None
    before_release: tuple[int, int] | None = None
    try:
        ov._apply_resize_pointer(400, 100)  # horizontal +300
        assert ov._live_scale_pending == pytest.approx(module.compute_mini_ui_scale(810))
        # Execute the already-scheduled live commit deterministically; this is still before release.
        ov._cancel_live_ui_scale_timer()
        ov._apply_pending_ui_scale_after()
        root.update_idletasks()
        root.update()

        before_release = ov._current_window_size()
        assert ov._ui_scale == pytest.approx(module.compute_mini_ui_scale(810))
        assert before_release[0] == 810
        assert before_release[1] == root.winfo_reqheight()
        assert before_release[1] != 477, "高度仍停在拖动前，真实排版没有在松手前落地"
    finally:
        ov._end_resize(SimpleNamespace(x_root=400, y_root=100))
        root.update_idletasks()
        root.update()

    assert before_release is not None
    assert ov._current_window_size() == before_release, "松手后边框仍发生最终跳变"


def test_live_vertical_resize_enlarges_typography_without_forcing_width(overlay) -> None:
    """Mini vertical grip motion must be a real font-size control, not hidden horizontal growth."""
    ov, root = overlay
    module = _ahmad_overlay_module()
    assert ov.details_expanded is False
    root.geometry("510x477+20+0")
    ov._ui_scale = 1.0
    ov._custom_mini_scale = 1.0
    ov._apply_ui_scale()
    root.update_idletasks()
    root.update()

    ov._resize_active = True
    ov._resize_anchor = (100, 100, 510, 477)
    ov._resize_scale_anchor = 1.0
    ov._resize_pending_pointer = None
    ov._resize_frame_after_id = None
    ov._active_resize_tip = None
    try:
        ov._apply_resize_pointer(100, 220)  # vertical +120, horizontal 0
        expected_scale = min(module.UI_SCALE_MAX, 597 / 477)
        assert ov._live_scale_pending == pytest.approx(expected_scale)
        ov._cancel_live_ui_scale_timer()
        ov._apply_pending_ui_scale_after()
        root.update_idletasks()
        root.update()

        width, height = ov._current_window_size()
        assert width == 510
        assert height == root.winfo_reqheight()
        assert ov._ui_scale == pytest.approx(expected_scale)
        assert ov._ui_scale > 1.0
    finally:
        ov._end_resize(SimpleNamespace(x_root=100, y_root=220))
        root.update_idletasks()
        root.update()


def test_quality_card_keeps_purple_and_gold_on_distinct_readable_rows(overlay) -> None:
    ov, root = overlay
    module = _ahmad_overlay_module()
    assert "紫品" in ov.red_rows and "金品" in ov.red_rows
    assert "紫金件" not in ov.red_rows
    assert "金红概率" not in ov.red_rows
    assert tuple(key for key in ov.red_rows if key != "_card") == (
        "红件",
        "红格",
        "金品",
        "紫品",
        "蓝品",
        "白绿",
    )
    assert tuple(key for key in ov.action_rows if key != "_card") == (
        "动作",
        "最近",
        "金红概率",
        "红/空格",
        "候选",
        "总格",
    )
    assert tuple(
        ov.price_title_labels[key].cget("text")
        for key in ("conservative", "balanced", "aggressive")
    ) == ("保守", "参考", "激进")

    ov.render(_compact_2510_fixture())
    root.update_idletasks()
    root.update()
    triplet_frame = ov.red_count_triplet_frame
    assert triplet_frame is not None and triplet_frame.winfo_manager()
    red_badge = ov.quality_state_badges["红件"]
    assert red_badge.cget("text") == "待"
    assert (
        triplet_frame.winfo_x() + triplet_frame.winfo_width()
        <= red_badge.winfo_x()
    )
    assert abs(
        triplet_frame.winfo_width()
        - sum(child.winfo_width() for child in triplet_frame.winfo_children())
    ) <= 2

    purple, gold = module.split_purple_gold_display_summary(
        "紫件 9/12/15 41格 · 金件 5/5/10 21/21/42格"
    )
    root.geometry(f"440x{max(477, root.winfo_height())}+20+0")
    root.update_idletasks()
    root.update()
    ov._ui_scale = 1.0
    ov._set_autofit_label(
        ov.red_rows["紫品"],
        purple,
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    ov._set_autofit_label(
        ov.red_rows["金品"],
        gold,
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    ov._set_autofit_label(
        ov.red_rows["白绿"],
        "12件 · 20/26/30格",
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    ov._set_autofit_label(
        ov.red_rows["蓝品"],
        "8件 · 13/15/17格",
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    ov._apply_action_cell("估计参考", next_step="补紫/金件数或均格")
    ov._set_candidate_autofit("估总件 19/31/43 · 估总格 59/76/99格")
    ov._set_autofit_label(
        ov.action_rows["金红概率"],
        "金5件21格85.8%·红4件12格7.2%",
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
        preserve_red_segment=True,
        stabilize_font=True,
    )
    root.update_idletasks()
    root.update()

    assert ov.red_rows["紫品"].cget("text") == "9/12/15件 · 41格"
    assert ov.red_rows["金品"].cget("text") == "5/5/10件 · 21/21/42格"
    assert ov.action_rows["动作"].cget("text") == "估计参考 · 补紫/金件数或均格"
    assert "下一步\n  补紫/金件数或均格" in ov.action_rows["动作"]._hover_tip.text
    assert "红4件12格7.2%" in ov.action_rows["金红概率"].cget("text")
    quality_card = ov.red_rows["_card"].master
    action_card = ov.action_rows["_card"].master
    assert quality_card.winfo_y() == action_card.winfo_y()
    assert quality_card.winfo_height() == action_card.winfo_height()
    purple_row = ov.red_rows["紫品"].master
    gold_row = ov.red_rows["金品"].master
    gold_probability_row = ov.action_rows["金红概率"].master
    blue_row = ov.red_rows["蓝品"].master
    white_green_row = ov.red_rows["白绿"].master
    candidate_row = ov.action_rows["候选"].master
    total_grid_row = ov.action_rows["总格"].master
    assert gold_row.winfo_y() + gold_row.winfo_height() <= purple_row.winfo_y()
    # Font rasterization can contribute one pixel between independently packed cards; keep the
    # semantic rows visually aligned without forcing fixed heights that break DPI scaling.
    assert abs(gold_row.winfo_y() - gold_probability_row.winfo_y()) <= 1
    assert abs(blue_row.winfo_y() - candidate_row.winfo_y()) <= 1
    assert abs(white_green_row.winfo_y() - total_grid_row.winfo_y()) <= 1
    purple_font = abs(tkfont.Font(font=ov.red_rows["紫品"].cget("font")).actual("size"))
    gold_font = abs(tkfont.Font(font=ov.red_rows["金品"].cget("font")).actual("size"))
    gold_probability_font = abs(
        tkfont.Font(font=ov.action_rows["金红概率"].cget("font")).actual("size")
    )
    low_quality_font = abs(
        tkfont.Font(font=ov.red_rows["白绿"].cget("font")).actual("size")
    )
    candidate_font = abs(
        tkfont.Font(font=ov.action_rows["候选"].cget("font")).actual("size")
    )
    assert max(purple_font, gold_font) <= module.SUMMARY_COMPACT_FONT_SIZE
    assert abs(gold_font - gold_probability_font) <= 1
    assert abs(low_quality_font - candidate_font) <= 1

    remaining_row = ov.action_rows["红/空格"].master
    remaining_caption = next(
        child
        for child in remaining_row.winfo_children()
        if isinstance(child, tk.Label) and child is not ov.action_rows["红/空格"]
    )
    remaining_caption_font = tkfont.Font(font=remaining_caption.cget("font"))
    assert remaining_caption.cget("text") == "红+空"
    assert remaining_caption_font.measure("红+空") <= remaining_caption.winfo_width()
    assert (
        remaining_caption.winfo_x() + remaining_caption.winfo_width()
        <= ov.action_rows["红/空格"].winfo_x()
    )

    # This row updates on every inference pass. A long value may shrink once,
    # but a later short value must not make the text visibly pulse larger again.
    ov._ui_scale = module.UI_SCALE_MAX
    ov._set_autofit_label(
        ov.action_rows["金红概率"],
        "金123456件123456格99.999%·红123456件123456格99.999%",
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
        preserve_red_segment=True,
        stabilize_font=True,
    )
    root.update_idletasks()
    long_font = abs(
        tkfont.Font(font=ov.action_rows["金红概率"].cget("font")).actual("size")
    )
    ov._set_autofit_label(
        ov.action_rows["金红概率"],
        "红1件1格9%",
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
        preserve_red_segment=True,
        stabilize_font=True,
    )
    root.update_idletasks()
    short_font = abs(
        tkfont.Font(font=ov.action_rows["金红概率"].cget("font")).actual("size")
    )
    assert long_font == short_font
    assert short_font >= module.SUMMARY_COMPACT_MIN_FONT_SIZE

    # Exact 2406 diagnostic text at its 592 px resized window. A stored enlarged
    # mini scale exercises the wrap-first branch seen in the screenshot.
    purple_2406, gold_2406 = module.split_purple_gold_display_summary(
        "紫件 5/10/10 约11/23/25格 · 金件 2/4/6 6/10/16格"
    )
    root.geometry(f"592x{max(477, root.winfo_height())}+20+0")
    root.update_idletasks()
    root.update()
    ov._ui_scale = module.UI_SCALE_MAX
    ov._set_autofit_label(
        ov.red_rows["紫品"],
        purple_2406,
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    ov._set_autofit_label(
        ov.red_rows["金品"],
        gold_2406,
        base_font_pt=module.SUMMARY_COMPACT_FONT_SIZE,
        min_font_pt=module.SUMMARY_COMPACT_MIN_FONT_SIZE,
    )
    root.update_idletasks()
    root.update()
    assert ov.red_rows["紫品"].cget("text") == "5/10/10件 · 约11/23/25格"
    assert ov.red_rows["金品"].cget("text") == "2/4/6件 · 6/10/16格"
    assert "\n" not in ov.red_rows["紫品"].cget("text")
    assert "\n" not in ov.red_rows["金品"].cget("text")
    assert int(ov.red_rows["紫品"].cget("height")) == 1
    assert int(ov.red_rows["金品"].cget("height")) == 1
    assert abs(tkfont.Font(font=ov.red_rows["紫品"].cget("font")).actual("size")) >= 6
    assert abs(tkfont.Font(font=ov.red_rows["金品"].cget("font")).actual("size")) >= 6
    assert quality_card.winfo_height() == action_card.winfo_height()


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_quality_state_badges_stay_visible_and_stable_at_all_dpi(
    tmp_path, dpi_scale
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"quality-badges-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        data = _compact_2510_fixture()
        quality_block = data["quality_composition"]
        assert isinstance(quality_block, dict)
        qualities = quality_block["qualities"]
        assert isinstance(qualities, dict)
        qualities.update(
            {
                "q1": {
                    "status": "ready",
                    "source": "pricing_posterior",
                    "lock_strength": "probabilistic",
                    "lock_reason": "joint posterior",
                    "candidates": [
                        {"count": 10, "cells": 20, "probability": 0.8},
                        {"count": 12, "cells": 24, "probability": 0.2},
                    ],
                    "shown_probability": 1.0,
                    "other_probability": 0.0,
                    "truncated": False,
                    "cap_hit": False,
                    "pricing_posterior": True,
                    "display_reconciled": False,
                },
                "q3": {
                    "status": "exact",
                    "source": "exact_evidence",
                    "lock_strength": "exact",
                    "lock_reason": "joint singleton",
                    "candidates": [
                        {"count": 4, "cells": 12, "probability": 1.0}
                    ],
                    "shown_probability": 1.0,
                    "other_probability": 0.0,
                    "truncated": False,
                    "cap_hit": False,
                    "pricing_posterior": True,
                    "display_reconciled": False,
                },
                "q4": {
                    "status": "ready",
                    "source": "pricing_posterior",
                    "lock_strength": "probabilistic",
                    "lock_reason": "joint posterior",
                    "candidates": [
                        {"count": 8, "cells": 28, "probability": 0.75},
                        {"count": 8, "cells": 30, "probability": 0.25},
                    ],
                    "shown_probability": 1.0,
                    "other_probability": 0.0,
                    "truncated": False,
                    "cap_hit": False,
                    "pricing_posterior": True,
                    "display_reconciled": False,
                },
                "q5": {
                    "status": "exact",
                    "source": "exact_evidence",
                    "lock_strength": "exact",
                    "lock_reason": "joint singleton",
                    "candidates": [
                        {"count": 6, "cells": 19, "probability": 1.0}
                    ],
                    "shown_probability": 1.0,
                    "other_probability": 0.0,
                    "truncated": False,
                    "cap_hit": False,
                    "pricing_posterior": True,
                    "display_reconciled": False,
                },
            }
        )
        data["quality_composition"] = _current_quality_composition(quality_block)
        red = data["red"]
        assert isinstance(red, dict)
        red["quality_count_summary"] = "紫8/6件 · 28/20格 · 金6件/19格"
        red["uncertainty_summary"] = "已锁 蓝4件/12格 · 未锁 白绿10/12"

        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        ov.render(data)
        root.update_idletasks()
        root.update()

        badges = ov.quality_state_badges
        assert badges["紫品"].cget("text") == "件锁"
        assert badges["金品"].cget("text") == "锁定"
        assert badges["白绿"].cget("text") == "未锁"
        assert badges["蓝品"].cget("text") == "锁定"
        assert root.winfo_width() == 510
        for key in ("紫品", "金品", "白绿", "蓝品"):
            badge = badges[key]
            value = ov.red_rows[key]
            badge_font = tkfont.Font(font=badge.cget("font"))
            assert badge_font.measure(str(badge.cget("text"))) <= badge.winfo_width()
            assert value.winfo_x() + value.winfo_width() <= badge.winfo_x()
            assert badge.winfo_x() + badge.winfo_width() <= badge.master.winfo_width()
            assert "\n" not in str(value.cget("text"))

        reserved_widths = {
            key: badges[key].winfo_width()
            for key in ("紫品", "金品", "白绿", "蓝品")
        }
        qualities["q4"] = {
            "status": "exact",
            "source": "exact_evidence",
            "lock_strength": "exact",
            "lock_reason": "joint singleton",
            "candidates": [
                {"count": 8, "cells": 28, "probability": 1.0}
            ],
            "shown_probability": 1.0,
            "other_probability": 0.0,
            "truncated": False,
            "cap_hit": False,
            "pricing_posterior": True,
            "display_reconciled": False,
        }
        data["quality_composition"] = _current_quality_composition(quality_block)
        ov.render(data)
        root.update_idletasks()
        root.update()
        assert badges["紫品"].cget("text") == "锁定"
        assert badges["紫品"].winfo_width() == reserved_widths["紫品"]

        qualities["q4"] = {
            "status": "conflict",
            "reason": "conflict",
            "lock_strength": "unavailable",
            "candidates": [],
        }
        data["quality_composition"] = _current_quality_composition(quality_block)
        ov.render(data)
        root.update_idletasks()
        root.update()
        assert badges["紫品"].cget("text") == "冲突"
        assert badges["紫品"].winfo_manager()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_native_summary_and_compact_badge_share_low_quality_state_at_all_dpi(
    tmp_path,
    monkeypatch,
    dpi_scale,
) -> None:
    server = _ahmad_server_module()
    probabilistic = _summarize_low_quality_joint(
        server,
        monkeypatch,
        tmp_path / "probabilistic",
        q1_rows=[
            {"count": 10, "cells": 20, "prob": 0.4},
            {"count": 15, "cells": 30, "prob": 0.6},
        ],
        q3_rows=[
            {"count": 13, "cells": 18, "prob": 0.6},
            {"count": 14, "cells": 20, "prob": 0.4},
        ],
    )
    exact = _summarize_low_quality_joint(
        server,
        monkeypatch,
        tmp_path / "exact",
        q1_rows=[{"count": 10, "cells": 20, "prob": 1.0}],
        q3_rows=[{"count": 13, "cells": 18, "prob": 1.0}],
        q1_range=[10, 10, 10],
        q3_range=[13, 13, 13],
        q1_strength="exact",
        q3_strength="exact",
    )
    assert probabilistic["red"]["uncertainty_summary"] == (
        "未锁 白绿10/15/15 蓝13"
    )
    assert "已锁 蓝13件" not in probabilistic["red"]["uncertainty_summary"]
    assert exact["red"]["uncertainty_summary"] == (
        "已锁 白绿10件/20格 蓝13件/18格"
    )

    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"native-chain-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")

        ov.render(probabilistic)
        root.update_idletasks()
        root.update()
        white_green_badge = ov.quality_state_badges["白绿"]
        blue_badge = ov.quality_state_badges["蓝品"]
        white_green_value = ov.red_rows["白绿"]
        blue_value = ov.red_rows["蓝品"]
        assert white_green_badge.cget("text") == "未锁"
        assert blue_badge.cget("text") == "未锁"
        assert white_green_value.cget("text") == "10/15件·20/30格"
        assert blue_value.cget("text") == "13–14件·18/20格"
        probabilistic_widths = {
            "白绿": white_green_badge.winfo_width(),
            "蓝品": blue_badge.winfo_width(),
        }
        for value, badge in (
            (white_green_value, white_green_badge),
            (blue_value, blue_badge),
        ):
            assert value.winfo_x() + value.winfo_width() <= badge.winfo_x()
            assert badge.winfo_x() + badge.winfo_width() <= badge.master.winfo_width()

        ov.render(exact)
        root.update_idletasks()
        root.update()
        assert white_green_badge.cget("text") == "锁定"
        assert blue_badge.cget("text") == "锁定"
        assert white_green_value.cget("text") == "10件·20格"
        assert blue_value.cget("text") == "13件·18格"
        assert "蓝13件/18格" in ov._red_hover("低品件", exact["red"], {})
        assert white_green_badge.winfo_width() == probabilistic_widths["白绿"]
        assert blue_badge.winfo_width() == probabilistic_widths["蓝品"]
        assert root.winfo_width() == 510
        assert "\n" not in str(white_green_value.cget("text"))
        assert "\n" not in str(blue_value.cget("text"))
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


def test_same_session_quality_conflict_keeps_last_verified_view_with_marker(
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "continuity" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        safe = _compact_2510_fixture()
        safe_context = safe["context"]
        assert isinstance(safe_context, dict)
        safe_context["round"] = 1
        ov._apply_live_summary(safe, safe, force=True)
        root.update_idletasks()
        assert ov.price_labels["balanced"].cget("text") == "500,000"

        conflict = copy.deepcopy(safe)
        conflict_context = conflict["context"]
        assert isinstance(conflict_context, dict)
        conflict_context["round"] = 3
        conflict["reference"] = {
            "source": "ref_waiting",
            "conservative": "-",
            "balanced": "-",
            "aggressive": "-",
            "action": "等待外部输入",
        }
        conflict["quality_composition"] = {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": "conflict",
            "reason": "quality_composition_conflict",
            "qualities": {},
        }
        conflict["flags"] = [
            {
                "label": "组合更新冲突",
                "level": "danger",
                "detail": "当前结构化组合不可验证",
            }
        ]
        ov._apply_live_summary(conflict, conflict, force=True)
        root.update_idletasks()

        assert ov.title.cget("text") == "ahmed · 2510 · R3"
        assert "沿用R1已验证结果" in ov.subtitle.cget("text")
        assert ov.price_labels["balanced"].cget("text") == "500,000"
        assert ov._last_live_summary["continuity"] == {
            "reason": "quality_composition_conflict",
            "source_round": "1",
        }
        flag_texts = [
            str(child.cget("text"))
            for child in ov.flags.winfo_children()
            if child.winfo_class() == "Label"
        ]
        assert "沿用R1" in flag_texts
        assert "组合更新冲突" in flag_texts

        new_session = copy.deepcopy(conflict)
        new_context = new_session["context"]
        assert isinstance(new_context, dict)
        new_context["session_id"] = "2510:new-session"
        ov._apply_live_summary(new_session, new_session, force=True)
        root.update_idletasks()
        assert ov.price_labels["balanced"].cget("text") == "-"
        assert ov._last_live_summary["continuity"] == {
            "reason": "quality_composition_retrying",
            "problem": "quality_composition_conflict",
        }
        assert ov.action_rows["动作"].cget("text") == "重新校验本轮输入"
        assert ov._last_inference_signature is None
        assert all(
            badge.cget("text") != "冲突"
            for badge in ov.quality_state_badges.values()
        )

        # The same exact conflict after the one bounded retry is real, not hidden.
        ov._last_inference_signature = ("already-retried",)
        ov._apply_live_summary(new_session, new_session, force=True)
        root.update_idletasks()
        assert "continuity" not in ov._last_live_summary
        assert ov._last_inference_signature == ("already-retried",)
        assert ov.quality_state_badges["红件"].cget("text") == "待"

        recovered = copy.deepcopy(safe)
        recovered_context = recovered["context"]
        assert isinstance(recovered_context, dict)
        recovered_context["session_id"] = "2510:new-session"
        recovered_context["round"] = 2
        ov._apply_live_summary(recovered, recovered, force=True)
        root.update_idletasks()
        assert ov.price_labels["balanced"].cget("text") == "500,000"
        assert getattr(ov, "_ref_problem_retry_signature", None) is None
        assert all(
            badge.cget("text") != "冲突"
            for badge in ov.quality_state_badges.values()
        )
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_same_session_transient_ref_error_keeps_last_verified_view_and_retries_once(
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "transient-error" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        safe = _compact_2510_fixture()
        safe_context = safe["context"]
        assert isinstance(safe_context, dict)
        safe_context["round"] = 2
        ov._apply_live_summary(safe, safe, force=True)

        failed = copy.deepcopy(safe)
        failed_context = failed["context"]
        assert isinstance(failed_context, dict)
        failed_context["round"] = 4
        failed["reference"] = {
            "source": "ref_waiting",
            "conservative": "-",
            "balanced": "-",
            "aggressive": "-",
            "action": "等待外援输入 · error",
        }
        failed["ahmed_ref"] = {"status": "error", "notes": ["redacted"]}

        ov._last_inference_signature = ("current",)
        ov._apply_live_summary(failed, failed, force=True)
        root.update_idletasks()

        assert ov.price_labels["balanced"].cget("text") == "500,000"
        assert ov._last_live_summary["continuity"] == {
            "reason": "ref_engine_transient_error",
            "source_round": "2",
        }
        assert ov._last_inference_signature is None
        assert "沿用R2已验证结果" in ov.subtitle.cget("text")

        ov._last_inference_signature = ("already-retried",)
        ov._apply_live_summary(failed, failed, force=True)
        assert ov._last_inference_signature == ("already-retried",)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_settled_quality_rows_show_facts_without_repeating_lock_badges(
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        ov = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "settled" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        summary = _native_2401_summary(tmp_path, phase="settled")
        ov.render(summary)
        root.update_idletasks()
        root.update()

        assert summary["context"]["phase"] == "settled"
        assert ov.red_rows["红件"].cget("text") == "3件"
        red_font = tkfont.Font(font=ov.red_rows["红件"].cget("font"))
        expected_red_size = max(
            6,
            int(round(module.SUMMARY_VALUE_FONT_SIZE * ov._ui_scale)),
        )
        assert abs(red_font.actual("size")) >= expected_red_size
        assert str(red_font.actual("weight")) == "bold"
        assert all(
            not badge.winfo_manager()
            for badge in ov.quality_state_badges.values()
        )
        for key in ("红件", "紫品", "金品", "白绿", "蓝品"):
            text = str(ov.red_rows[key].cget("text"))
            assert "锁定" not in text
            assert "100%" not in text
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- #
# 真 Tk 门: 【全新安装默认是 mini】+ prefs 恢复双向都对(发布安全窗口 2026-07-12)
#
# 起因: 0.2.7 实机验证时作者报"默认开的是详细界面, 切不回去"。查下来【不是 bug】——
#   偏好缓存 %LOCALAPPDATA%\bidking\hero_ref_ui_prefs.json 里 details_expanded=true,
#   是他以前跑源码留下的。那个文件【在包外面, 跨安装持久化】, 重装包也不会重置。
#
# 🔴 但这暴露了一个真的覆盖缺口: 上面那个真 Tk 门用的是 ui_prefs_enabled=False,
#    【prefs 恢复这条路一个真 Tk 测试都没有】⇒ "全新安装默认是 mini"从来没被锁住过。
#    这次是靠作者实机才发现的, 下次该由门发现。
#
# ⚠️ 必须真 Tk: mock 测不出 pack/unpack 的真实映射语义(见本文件开头那段血泪)。
# --------------------------------------------------------------------------- #
def _overlay_with_prefs(module, root, tmp_dir, prefs):
    """按给定 prefs 起一个 overlay(prefs=None 表示【全新安装, 没有该文件】)。"""
    import json

    snap = tmp_dir / "latest_snapshot.json"          # 不存在 -> standby, 不会拉起 monitor
    pp = module.ui_prefs_path_for_snapshot(snap)
    pp.parent.mkdir(parents=True, exist_ok=True)
    if prefs is None:
        pp.unlink(missing_ok=True)
    else:
        pp.write_text(json.dumps(prefs), encoding="utf-8")
    ov = module.AhmadTkOverlay(root, snapshot_path=snap, interval_ms=100_000, ui_prefs_enabled=True)
    root.update_idletasks()
    root.update()
    return ov


def test_fresh_install_defaults_to_mini_and_prefs_restore_both_ways(tmp_path) -> None:
    """三条不变量(只建一个 Tk root —— 环境层偶发失败见 _new_root 的注释):

    1. **全新安装(无 prefs 文件)-> mini**。这是群友第一次打开看到的东西。
    2. prefs 说 details_expanded=True -> 详细(= 记住上次的状态, 这是【设计如此】, 不是 bug)。
    3. prefs 说 details_expanded=False -> mini(恢复不能只单向对)。
    """
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        # ① 全新安装: 没有 prefs 文件 -> 必须是 mini
        ov = _overlay_with_prefs(module, root, tmp_path / "fresh", None)
        assert ov.details_expanded is False, "全新安装默认开成了【详细】—— 群友第一次打开就是大窗口"
        assert not ov.details_card.master.winfo_ismapped(), "mini 态下详情卡仍然可见"
        assert not ov.minimap_card.master.winfo_ismapped(), "mini 态下小地图卡仍然可见"
        assert ov.mode_button.cget("text") == "详情", "mini 态的按钮该显示'详情'(点它去详细)"

        # ② prefs 说展开 -> 详细。这是【记住上次状态】, 是设计如此。
        #    作者 0.2.7 实机看到的就是这个 —— 他自己的历史 prefs, 不是产品默认。
        ov.details_expanded = False
        ov._apply_ui_prefs_payload({"schema_version": module.UI_PREFS_SCHEMA_VERSION,
                              "details_expanded": True, "theme_name": "warm"})
        root.update_idletasks(); root.update()
        assert ov.details_expanded is True
        assert ov.details_card.master.winfo_ismapped(), "prefs 说展开, 但详情卡没出来"
        assert ov.mode_button.cget("text") == "迷你"

        # ③ prefs 说 mini -> 回到 mini。恢复【双向】都要对。
        ov._apply_ui_prefs_payload({"schema_version": module.UI_PREFS_SCHEMA_VERSION,
                              "details_expanded": False, "theme_name": "warm"})
        root.update_idletasks(); root.update()
        assert ov.details_expanded is False
        assert not ov.details_card.master.winfo_ismapped(), "prefs 说 mini, 详情卡却没收起 —— 这才是真'切不回去'"
        assert ov.mode_button.cget("text") == "详情"
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
