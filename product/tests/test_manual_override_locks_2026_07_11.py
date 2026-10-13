# -*- coding: utf-8 -*-
"""手填(manual)硬覆盖与锁定的设计门(审计窗口 2026-07-11 · bug批次车道2 · #1/#6).

作者期望: 手填 = 硬覆盖并锁定,离线+实时都能用。群友报"手填了结果还是一样"/"补的条件不会被锁定计算"。

**取证结论(2026-07-11 审计窗口)**: HEAD 上手填链路是【通的】——快照带 phase=manual、手填件数进
fixed_counts(硬锁)、手填总格进 total_cells、引擎认(改总格输出跟着变)、live 帧到达时被
`_apply_live_summary` 的 `if self._manual_active:` 分支挡住(只改状态文本、不 render)。
群友的症状归因于【版本】: 他们跑 v0.2.6,而"总件不再强制"的修复(commit 105f064)只进了 0.2.6-hotfix1
——v0.2.6 上不填总件会让整个手填被拒(旧代码 `if total_count is None: return None, "需要填写总件"`),
用户看不到状态栏小字,体感就是"填了没反应"。

本文件把上述【正确行为】锁成回归门,防止再被改坏。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402


class _Entry:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


def _manual_snapshot(**fields: str):
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {k: _Entry(v) for k, v in fields.items()}
    return module.AhmadTkOverlay._manual_inputs_snapshot(overlay)


def _engine(snapshot: dict) -> dict:
    return run_reference_engine(snapshot, max_combos=8000).as_dict()


def test_manual_snapshot_carries_manual_phase_and_hard_locks() -> None:
    """手填值必须以【硬锁语义】进快照: phase=manual + 件数进 fixed_counts + 总格进 total_cells。"""
    snap, err = _manual_snapshot(
        hero="ahmed", map_id="5611", total_count="40", total_cells="149", q4_count="9"
    )
    assert err == "" and snap is not None
    assert snap["phase"] == "manual"
    assert snap["ui_contract"]["context"]["phase"] == "manual"
    ri = snap["structured_ref_inputs"]
    assert ri["total_count"] == 40
    assert ri["total_cells"] == 149
    assert ri["fixed_counts"] == {"q4": 9}   # 手填件数 = 硬锁, 不是软先验


def test_manual_total_cells_change_moves_engine_output() -> None:
    """作者原话#1: 改了总格结果还是一样 —— 引擎必须跟着手填的总格走(锁死 + 报价变化)。"""
    seen = {}
    for cells in ("120", "149", "180"):
        snap, err = _manual_snapshot(
            hero="ahmed", map_id="5611", total_count="40", total_cells=cells, q4_count="9"
        )
        assert err == "" and snap is not None
        res = _engine(snap)
        want = int(cells)
        # 手填总格 = 确定值 -> 三档锁死到该值
        assert list(res["total_grid_range"]) == [want, want, want], cells
        seen[cells] = res["balanced"]
    # 不同总格必须给出不同报价(否则就是"改了没反应")
    assert len({v for v in seen.values() if v is not None}) == len(seen), seen


def test_manual_count_locks_that_quality() -> None:
    """作者原话#6(血色): 补进去的条件要被【直接锁定】计算 —— 手填件数必须把该档锁成单值。"""
    snap, err = _manual_snapshot(
        hero="ahmed", map_id="5611", total_count="40", total_cells="149", q4_count="9"
    )
    assert err == "" and snap is not None
    res = _engine(snap)
    assert list(res["quality_count_ranges"]["q4"]) == [9, 9, 9]


def test_manual_total_cells_alone_is_accepted() -> None:
    """105f064 的回归门: 只填总格(数不出总件)必须被接受。v0.2.6 会拒("需要填写总件"),
    这正是群友"手填没反应"的版本根因。"""
    snap, err = _manual_snapshot(hero="aisha", map_id="2405", total_cells="90")
    assert err == "" and snap is not None
    assert snap["structured_ref_inputs"]["total_cells"] == 90
    assert snap["structured_ref_inputs"]["total_count"] is None


def test_live_summary_does_not_overwrite_active_manual_result() -> None:
    """live 帧到达【不得】回冲手填结果: _manual_active 时只更新状态文本, 不 render。"""
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)

    rendered: list[dict] = []
    overlay.render = lambda summary: rendered.append(summary)          # type: ignore[assignment]
    overlay.render_standby = lambda summary: rendered.append(summary)  # type: ignore[assignment]

    overlay._act_ready = False
    overlay._act_state = {"run_full": True}
    overlay._manual_active = True                       # 手填已生效
    overlay._manual_live_session_id = ""
    overlay._deferred_live_apply = None
    overlay._ui_interaction_busy = lambda: False        # type: ignore[assignment]
    overlay._should_reset_manual_for_summary = lambda data: False  # type: ignore[assignment]
    overlay._summary_session_id = lambda data: "sess-1"            # type: ignore[assignment]

    class _Status:
        def configure(self, **kw) -> None:
            pass

    overlay.manual_status = _Status()

    live_summary = {"status": "ok", "context": {"session_id": "sess-1"}}
    module.AhmadTkOverlay._apply_live_summary(overlay, {"snap": 1}, live_summary)

    assert rendered == [], "live 帧不得在手填生效时重新渲染(会把手填结果冲掉)"
    assert overlay._manual_active is True
