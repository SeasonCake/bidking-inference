"""Display-only regression: negative authority gates must not request more input."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def panel():
    spec = importlib.util.spec_from_file_location(
        "ahmad_live_panel_server", ROOT / "hero_ref/tools/ahmad_live_panel_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pending_result(map_id=2511):
    return {
        "status": "no_reachable_combo",
        "conservative": None, "balanced": None, "aggressive": None,
        "notes": [
            "update606_changed_route_distribution_authority:"
            f"4ae819-2026-08-27-665:{map_id}:sub_pool_weights_changed:"
            "no_approved_distribution"
        ],
        "evidence": {"map_id": map_id, "total_count": None},
    }


@pytest.mark.parametrize("hero", ["ahmed", "victor", "aisha", "ethan", "raven"])
def test_pending_distribution_overrides_missing_evidence_copy(panel, hero):
    result = pending_result()
    original = copy.deepcopy(result)
    assert panel._next_info_hint(result, hero_key=hero) == "本局暂不提供估值"
    assert panel._ref_waiting_display_text(hero, result) == "当前仓库待适配"
    assert panel._ref_not_ready_flag_detail(result) == "当前仓库待适配"
    assert result == original


@pytest.mark.parametrize("bad_note", [
    "constraints_conflict_or_too_strict",
    "unrelated:no_approved_distribution",
    "update606_changed_route_distribution_authority:unknown_state",
])
def test_other_negative_results_keep_existing_missing_input_copy(panel, bad_note):
    result = pending_result()
    result["notes"] = [bad_note]
    assert panel._next_info_hint(result, hero_key="ahmed") == "先补总件"
    if bad_note == "constraints_conflict_or_too_strict":
        assert panel._ref_not_ready_flag_detail(result) == "约束冲突，暂无可行组合"


def test_actual_missing_total_keeps_existing_hint(panel):
    result = {"status": "missing_total_count", "notes": ["waiting_total_count:grid_only"],
              "evidence": {"total_count": None}}
    assert panel._next_info_hint(result, hero_key="ahmed") == "先补总件"
    assert panel._ref_waiting_display_text("ahmed", result) == "等待总件数"


def test_successful_quote_with_stale_note_does_not_display_adaptation(panel):
    result = pending_result()
    result.update(status="ok", conservative=1, balanced=2, aggressive=3)
    assert panel._next_info_hint(result, hero_key="ahmed") == "先补总件"


def synthetic_snapshot(*, generation_reason="", phase="bidding", map_id=2511):
    created_at = time.time()
    return {
        "created_at": created_at,
        "map_id": map_id,
        "hero": "ahmed",
        "formal_mode_reason": generation_reason,
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": map_id,
                        "round": 3, "phase": phase, "session_id": f"{map_id}:copy-fixture"},
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {"input_constraints": {
                "total_item_count": {"value": 38, "confidence": "exact"},
            }}},
            "source": {"created_at": created_at},
        },
    }


@pytest.mark.parametrize("map_id", [2511, 4511])
@pytest.mark.parametrize("generation_reason,label", [
    ("game_generation_unverified", "游戏更新待适配"),
    ("", "当前仓库待适配"),
    ("unrelated", "当前仓库待适配"),
])
def test_real_summary_and_engine_negative_gate_projection(panel, tmp_path, map_id, generation_reason, label):
    snapshot = synthetic_snapshot(generation_reason=generation_reason, map_id=map_id)
    summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "synthetic.json")
    result = summary["ahmed_ref"]
    assert result["status"] == "no_reachable_combo"
    assert [result[k] for k in ("conservative", "balanced", "aggressive")] == [None] * 3
    assert result["evidence"]["total_count"] is None
    assert summary["reference"]["action"] == label
    assert summary["evidence"]["next_info_hint"] == "本局暂不提供估值"
    assert summary["evidence"]["source_mode"] == "ref_waiting"
    assert any(flag["label"] == label for flag in summary["flags"])
    assert not any(flag["label"] == "外援未就绪" for flag in summary["flags"])
    assert summary["red"]["uncertainty_summary"] == label


def test_settled_draw_hero_still_suppresses_prop_hint(panel):
    assert panel._next_info_hint(pending_result(), hero_key="raven", phase="settled") == ""


@pytest.mark.parametrize("reason,expected", [
    ("game_generation_unverified", "游戏更新待适配"),
    ("game_generation_unverified_extra", "等待外援输入"),
    (None, "等待外援输入"),
])
def test_generation_reason_without_distribution_note(panel, monkeypatch, tmp_path, reason, expected):
    result = pending_result()
    result.update(status="missing_total_count", notes=[])
    monkeypatch.setattr(panel, "run_reference_engine", lambda *_a, **_k: SimpleNamespace(as_dict=lambda: result))
    summary = panel.summarize_snapshot(
        synthetic_snapshot(generation_reason=reason), snapshot_path=tmp_path / "synthetic.json"
    )
    assert summary["reference"]["action"] == expected
    assert summary["evidence"]["next_info_hint"] == (
        "本局暂不提供估值" if reason == "game_generation_unverified" else "先补总件"
    )


def test_reason_string_subclass_does_not_invoke_equality_hook(panel):
    class UntrustedReason(str):
        def __eq__(self, other):
            raise AssertionError("untrusted reason comparison")
    assert panel._ref_adaptation_pending_text(
        pending_result(), formal_mode_reason=UntrustedReason("game_generation_unverified")
    ) == "当前仓库待适配"


def test_generation_reason_does_not_hide_existing_quote(panel):
    result = pending_result()
    result.update(conservative=1, balanced=2, aggressive=3)
    assert panel._ref_adaptation_pending_text(result, formal_mode_reason="game_generation_unverified") == ""


def test_generation_pending_does_not_promise_aisha_skill_frame_recovery(panel, tmp_path):
    snapshot = synthetic_snapshot(generation_reason="game_generation_unverified")
    snapshot["hero"] = "aisha"
    snapshot["ui_contract"]["context"]["hero"] = "aisha"
    snapshot["ui_contract"]["constraints"]["public_info"] = {}
    summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "synthetic.json")
    assert summary["reference"]["action"] == "游戏更新待适配"
    assert not any(flag["detail"] == "技能帧就绪后再估价" for flag in summary["flags"])


def test_manual_and_actual_tk_merge_use_same_pending_copy(panel, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "ahmad_tk_overlay", ROOT / "hero_ref/tools/ahmad_tk_overlay.py"
    )
    overlay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = overlay
    spec.loader.exec_module(overlay)
    result = pending_result()
    monkeypatch.setattr(overlay, "run_reference_engine", lambda *_a, **_k: SimpleNamespace(as_dict=lambda: result))
    ui = object.__new__(overlay.AhmadTkOverlay)
    ui._manual_live_round_no = lambda _s: 3
    summary = ui._manual_result_summary(synthetic_snapshot())
    assert summary["reference"]["action"] == "当前仓库待适配"
    assert summary["evidence"]["source_mode"] == "manual_ref"
    assert summary["evidence"]["ref_status"] == "no_reachable_combo"
    assert any(flag["label"] == "手动输入" for flag in summary["flags"])
    assert any(flag["label"] == "当前仓库待适配" for flag in summary["flags"])
    next_text = ui._mini_next_info_text(summary)
    assert next_text == "本局暂不提供估值"
    assert overlay.merge_action_next_step_display(summary["reference"]["action"], next_text) == (
        "当前仓库待适配 · 本局暂不提供估值"
    )
