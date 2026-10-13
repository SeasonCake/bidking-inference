from __future__ import annotations

import copy
from dataclasses import replace
from http.server import ThreadingHTTPServer
from itertools import product
import json
import os
import sys
import time
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "src"))
sys.path.insert(0, str(ROOT / "hero_ref" / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as tk_overlay  # noqa: E402


KILL = "BIDKING_DISABLE_AHMED_Q1_Q5_EMPTY_SUPPORT_ANTILOCK"
NOTE = "ahmed_q1_q5_empty_support_physical_untightened"


def _structured_inputs(*, include_q1_avg: bool = True) -> dict:
    avg_cells = {"q5": 5.111111164093018}
    if include_q1_avg:
        avg_cells["q1"] = 2.3684210777282715
    return {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "ahmed",
        "total_count": 55,
        "total_cells": None,
        "avg_cells": avg_cells,
        "counts": {"q3": 17},
        "quality_cells": {},
        "avg_values": {},
        "quality_values": {},
        "quality_value_floors": {},
        "count_sums": {},
        "scoped_count_sums": [],
        "split_avg_cells": {},
        "split_counts": {},
        "split_quality_cells": {},
        "field_updates": [],
        "minimap_items": [],
    }


def _snapshot(*, include_q1_avg: bool = True, include_public_q5_avg: bool = False) -> dict:
    structured = _structured_inputs(include_q1_avg=include_q1_avg)
    constraints: dict = {}
    public_rows: list[dict] = []
    if include_public_q5_avg:
        public_rows = [
            {
                "sort": 17,
                "time": "2026-08-10 19:13:01.607",
                "info_id": 200037,
                "map_id": 2501,
                "value": 38086.109375,
                "value_field": 9,
                "revealed_items": 0,
                "revealed_items_detail": [],
            }
        ]
        constraints["public_info"] = {
            "public_numeric_facts": [
                {
                    "info_id": 200037,
                    "semantic": "q5_avg_value",
                    "kind": "avg_value",
                    "quality": 5,
                    "label": "金均价",
                    "value": 38086.109375,
                    "display_value": "38,086.11",
                    "text": "金均价 38,086.11",
                }
            ],
            "public_avg_values": [
                {
                    "info_id": 200037,
                    "semantic": "q5_avg_value",
                    "kind": "avg_value",
                    "quality": 5,
                    "label": "金均价",
                    "value": 38086.109375,
                    "display_value": "38,086.11",
                    "text": "金均价 38,086.11",
                }
            ],
            "public_numeric_summary": "金均价 38,086.11",
        }
    action_rows = [
        {
            "sort": 3,
            "time": "2026-08-10 19:12:17.780",
            "action_id": 100117,
            "tool": "良品存量",
            "result": 17,
            "result_field": 7,
            "result_wire_type": "varint",
            "session_id": "2501:9000000000000052",
            "source_kind": "direct_action_response",
        }
    ]
    if include_q1_avg:
        action_rows.insert(
            0,
            {
                "sort": 11,
                "time": "2026-08-10 19:12:40.295",
                "action_id": 100110,
                "tool": "普品均格",
                "result": 2.3684210777282715,
                "result_field": 11,
                "result_wire_type": "float32",
                "result_raw_hex": "36941740",
                "session_id": "2501:9000000000000052",
                "round": 1,
                "source_message_id": 39,
                "source_kind": "direct_action_response",
            },
        )
    action_mirror = copy.deepcopy(action_rows)
    for row in action_mirror:
        for key in (
            "sort",
            "action_id",
            "result",
            "result_field",
            "round",
            "source_message_id",
        ):
            if key in row:
                row[key] = str(row[key])
    skill_rows = [
        {
            "sort": 9,
            "time": "2026-08-10 19:12:31.157",
            "skill_id": 100204,
            "hero_id": 204,
            "tool": "艾哈迈德·R1 总件数",
            "result": 55,
            "result_field": 7,
            "round_index": 1,
        },
        {
            "sort": 9,
            "time": "2026-08-10 19:12:31.157",
            "skill_id": 1002041,
            "hero_id": 204,
            "tool": "艾哈迈德·R2 金品均格",
            "result": 5.111111164093018,
            "result_field": 11,
            "round_index": 1,
        },
    ]
    skill_mirrors = [
        {
            "skill_id": row["skill_id"],
            "hero_id": row["hero_id"],
            "result": row["result"],
            "result_field": row["result_field"],
            "observed_items": [],
            "sort": row["sort"],
            "round_index": row["round_index"],
            "parent_skill_id": None,
        }
        for row in skill_rows
    ]
    now = time.time()
    return {
        "schema_version": 1,
        "source": "wss_local",
        "created_at": now,
        "session_id": "2501:9000000000000052",
        "hero": "ahmed",
        "map_id": 2501,
        "model_map_id": 2501,
        "round": 2,
        "action_round": 2,
        "observed_round": 1,
        "phase": "bidding",
        "action_result_rows": action_rows,
        "public_info_rows": public_rows,
        "skill_reveal_rows": skill_rows,
        "skill_reveals": skill_mirrors,
        "structured_ref_inputs": copy.deepcopy(structured),
        "ahmad_ref_inputs": copy.deepcopy(structured),
        "ui_contract": {
            "context": {
                "session_id": "2501:9000000000000052",
                "hero": "ahmed",
                "map_id": 2501,
                "model_map_id": 2501,
                "round": 2,
                "action_round": 2,
                "observed_round": 1,
                "phase": "bidding",
            },
            "source": {"created_at": now},
            "constraints": constraints,
            "truth": {"available": False},
            "baseline": {},
            "actions": {"results": action_mirror},
            "diagnostics": {},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()


@pytest.mark.parametrize("include_public_q5_avg", [False, True])
@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_real_2501_bidding_evidence_no_longer_blanks(
    monkeypatch: pytest.MonkeyPatch,
    include_public_q5_avg: bool,
    max_combos: int,
) -> None:
    monkeypatch.delenv(KILL, raising=False)

    result = _run(
        _snapshot(include_public_q5_avg=include_public_q5_avg),
        max_combos=max_combos,
    )

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 13
    assert NOTE in result["notes"]
    assert all(result[key] is not None for key in ("conservative", "balanced", "aggressive"))
    assert any(row["value"] == 6 for row in result["quality_count_candidates"]["q4"])
    assert any(row["value"] == 4 for row in result["quality_count_candidates"]["q6"])


def test_physical_retry_keeps_complete_13_state_domain_and_truth_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    prepared = engine.prepare_reference_engine_snapshot(_snapshot())
    evidence = engine.extract_evidence(prepared)
    assert evidence.ahmed_q1_q5_current_context_bound is True
    domains = engine._ahmed_q1_q5_empty_support_count_overrides(  # noqa: SLF001
        55,
        evidence=evidence,
    )
    assert domains is not None

    states = {
        (q1, q3, q4, q5, q6)
        for q1, q3, q4, q5, q6 in product(
            domains["q1"],
            domains["q3"],
            domains["q4"],
            domains["q5"],
            domains["q6"],
        )
        if q1 + q3 + q4 + q5 + q6 == 55
    }

    assert len(states) == 13
    assert (19, 17, 6, 9, 4) in states


def test_completed_replay_binding_accepts_persistent_skill_and_heartbeat() -> None:
    snapshot = _snapshot()
    snapshot["round"] = 3
    snapshot["action_round"] = 3
    snapshot["observed_round"] = 2
    context = snapshot["ui_contract"]["context"]
    context["round"] = 3
    context["action_round"] = 3
    context["observed_round"] = 2
    root_action = next(
        row for row in snapshot["action_result_rows"] if row["action_id"] == 100110
    )
    root_action.update(
        round=2,
        source_message_id=0x0025,
        source_kind="state_replay",
        sort=15,
        promoted_action_round=3,
        promotion_reason="completed_round_state_replay",
    )
    ui_action = next(
        row
        for row in snapshot["ui_contract"]["actions"]["results"]
        if row["action_id"] == "100110"
    )
    for key, value in root_action.items():
        if key in {"time", "tool", "revealed_summary", "revealed_items_detail"}:
            continue
        ui_action[key] = str(value)
    old_created_at = snapshot["created_at"]
    snapshot["created_at"] = old_created_at + 20.0
    snapshot["snapshot_heartbeat_at"] = snapshot["created_at"]

    prepared = engine.prepare_reference_engine_snapshot(snapshot)
    evidence = engine.extract_evidence(prepared)

    assert prepared["structured_ref_inputs"]["current_action_results"][0][
        "action_id"
    ] == 100110
    assert evidence.ahmed_q1_q5_current_context_bound is True


def test_completed_replay_binding_rejects_conflicting_structured_projection() -> None:
    snapshot = _snapshot()
    snapshot["round"] = 3
    snapshot["action_round"] = 3
    snapshot["observed_round"] = 2
    snapshot["ui_contract"]["context"].update(
        round=3,
        action_round=3,
        observed_round=2,
    )
    root_action = next(
        row for row in snapshot["action_result_rows"] if row["action_id"] == 100110
    )
    root_action.update(
        round=2,
        source_message_id=0x0025,
        source_kind="state_replay",
        sort=15,
        promoted_action_round=3,
        promotion_reason="completed_round_state_replay",
    )
    ui_action = next(
        row
        for row in snapshot["ui_contract"]["actions"]["results"]
        if row["action_id"] == "100110"
    )
    for key, value in root_action.items():
        if key in {"time", "tool", "revealed_summary", "revealed_items_detail"}:
            continue
        ui_action[key] = str(value)
    conflicting = {
        key: value
        for key, value in root_action.items()
        if key not in {"time", "tool", "revealed_summary", "revealed_items_detail"}
    }
    conflicting["result_raw_hex"] = "00000000"
    snapshot["structured_ref_inputs"]["current_action_results"] = [conflicting]

    prepared = engine.prepare_reference_engine_snapshot(snapshot)

    assert engine.extract_evidence(prepared).ahmed_q1_q5_current_context_bound is False


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_kill_switch_restores_exact_parent_empty_window(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.setenv(KILL, "1")

    result = _run(_snapshot(), max_combos=max_combos)

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert NOTE not in result["notes"]
    assert all(result[key] is None for key in ("conservative", "balanced", "aggressive"))
    assert result["quality_count_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}


def test_cap_guard_keeps_whole_evidence_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)

    result = _run(_snapshot(), max_combos=12)

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert "combo_cap_hit" in result["notes"]
    assert NOTE not in result["notes"]
    assert result["quality_count_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}


def test_source_external_single_avg_case_is_identical_in_default_and_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_external = _snapshot(include_q1_avg=False)
    monkeypatch.delenv(KILL, raising=False)
    default = _run(source_external)
    monkeypatch.setenv(KILL, "1")
    rollback = _run(source_external)

    assert default == rollback
    assert default["status"] == "count_prior"
    assert NOTE not in default["notes"]


def test_existing_exact_q6_lock_bypasses_retry_and_matches_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_lock = _snapshot()
    for owner in ("structured_ref_inputs", "ahmad_ref_inputs"):
        exact_lock[owner]["counts"]["q6"] = 4

    monkeypatch.delenv(KILL, raising=False)
    default = _run(exact_lock)
    monkeypatch.setenv(KILL, "1")
    rollback = _run(exact_lock)

    assert default == rollback
    assert default["status"] == "ok"
    assert default["combo_count"] == 1
    assert default["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert default["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert NOTE not in default["notes"]


def test_conflict_note_rejects_retry_before_physical_domain_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    evidence = engine.extract_evidence(
        engine.prepare_reference_engine_snapshot(_snapshot())
    )
    conflicted = replace(
        evidence,
        source_notes=tuple(evidence.source_notes)
        + ("hard_conflict:quality_count_total_count",),
    )

    assert not engine._ahmed_q1_q5_empty_support_antilock_enabled(  # noqa: SLF001
        conflicted,
        (),
    )


def _assert_current_context_guard_fails_closed(
    snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    default = _run(snapshot)
    evidence = engine.extract_evidence(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    )
    monkeypatch.setenv(KILL, "1")
    rollback = _run(snapshot)

    assert evidence.ahmed_q1_q5_current_context_bound is False
    assert default == rollback
    assert default["status"] == "no_reachable_combo"
    assert default["combo_count"] == 0
    assert NOTE not in default["notes"]
    assert default["quality_count_candidates"] == {}
    assert default["quality_count_cells_candidates"] == {}


def test_foreign_q1_action_session_rejects_empty_support_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    for row in snapshot["action_result_rows"]:
        if row.get("action_id") == 100110:
            row["session_id"] = "2501:9000000000000053"
    for row in snapshot["ui_contract"]["actions"]["results"]:
        if row.get("action_id") == "100110":
            row["session_id"] = "2501:9000000000000053"

    _assert_current_context_guard_fails_closed(snapshot, monkeypatch)


def test_root_ui_context_session_conflict_rejects_empty_support_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["session_id"] = "2501:9000000000000053"

    _assert_current_context_guard_fails_closed(snapshot, monkeypatch)


def _root_hero_conflict(snapshot: dict) -> None:
    snapshot["hero"] = "aisha"


def _ui_hero_conflict(snapshot: dict) -> None:
    snapshot["ui_contract"]["context"]["hero"] = "aisha"


def _root_hero_missing(snapshot: dict) -> None:
    snapshot.pop("hero")


def _ui_hero_missing(snapshot: dict) -> None:
    snapshot["ui_contract"]["context"].pop("hero")


def _root_hero_malformed(snapshot: dict) -> None:
    snapshot["hero"] = 204


@pytest.mark.parametrize(
    "mutate",
    [
        _root_hero_conflict,
        _ui_hero_conflict,
        _root_hero_missing,
        _ui_hero_missing,
        _root_hero_malformed,
    ],
    ids=lambda mutate: mutate.__name__,
)
def test_raw_hero_identity_is_frozen_before_prepare_projection(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    snapshot = _snapshot()
    mutate(snapshot)

    _assert_current_context_guard_fails_closed(snapshot, monkeypatch)


def test_exact_ahmed_aliases_remain_valid_current_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["hero"] = "艾哈迈德"
    snapshot["ui_contract"]["context"]["hero"] = "艾哈迈德"
    monkeypatch.delenv(KILL, raising=False)

    prepared = engine.prepare_reference_engine_snapshot(snapshot)
    evidence = engine.extract_evidence(prepared)
    result = engine.run_reference_engine(prepared, max_combos=8_000).as_dict()

    assert evidence.ahmed_q1_q5_current_context_bound is True
    assert result["status"] == "count_prior"
    assert result["combo_count"] == 13
    assert NOTE in result["notes"]


def test_damaged_or_copied_prepared_context_marker_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    prepared = engine.prepare_reference_engine_snapshot(_snapshot())
    marker_key = next(
        key for key in prepared if key.endswith("ahmed_q1_q5_context_marker")
    )
    decision_key = next(
        key for key in prepared if key.endswith("ahmed_q1_q5_context_decision")
    )

    damaged = copy.deepcopy(prepared)
    damaged[marker_key] = "0" * 64
    assert engine.extract_evidence(damaged).ahmed_q1_q5_current_context_bound is False

    copied = _snapshot()
    copied["hero"] = "aisha"
    copied[marker_key] = prepared[marker_key]
    copied[decision_key] = prepared[decision_key]
    re_prepared = engine.prepare_reference_engine_snapshot(copied)
    assert (
        engine.extract_evidence(re_prepared).ahmed_q1_q5_current_context_bound
        is False
    )
    assert _run(copied)["status"] == "no_reachable_combo"


def _wrong_root_epoch(snapshot: dict) -> None:
    snapshot["session_epoch"] = 9000000000000053


def _context_round_conflict(snapshot: dict) -> None:
    snapshot["ui_contract"]["context"]["action_round"] = 3


def _source_runtime_conflict(snapshot: dict) -> None:
    snapshot["ui_contract"]["source"]["created_at"] += 1.0


def _q1_runtime_conflict(snapshot: dict) -> None:
    for row in snapshot["ui_contract"]["actions"]["results"]:
        if row.get("action_id") == "100110":
            row["source_message_id"] = "40"


def _q1_duplicate(snapshot: dict) -> None:
    snapshot["action_result_rows"].append(
        copy.deepcopy(snapshot["action_result_rows"][0])
    )


def _q5_round_conflict(snapshot: dict) -> None:
    for row in snapshot["skill_reveals"]:
        if row.get("skill_id") == 1002041:
            row["round_index"] = 2


def _q5_mirror_missing(snapshot: dict) -> None:
    snapshot["skill_reveals"] = [
        row for row in snapshot["skill_reveals"] if row.get("skill_id") != 1002041
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        _wrong_root_epoch,
        _context_round_conflict,
        _source_runtime_conflict,
        _q1_runtime_conflict,
        _q1_duplicate,
        _q5_round_conflict,
        _q5_mirror_missing,
    ],
    ids=lambda mutate: mutate.__name__,
)
def test_current_context_identity_guard_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    snapshot = _snapshot()
    mutate(snapshot)
    _assert_current_context_guard_fails_closed(snapshot, monkeypatch)


def test_summary_panel_contract_consumes_nonempty_engine_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    snapshot = _snapshot(include_public_q5_avg=True)
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    summary = panel.summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert summary["evidence"]["ref_status"] == "count_prior"
    assert summary["reference"]["readiness"] == "sparse_exact_prior"
    assert summary["reference"]["conservative"] != "-"
    assert summary["reference"]["balanced"] != "-"
    assert summary["reference"]["aggressive"] != "-"
    labels = {row["label"] for row in summary["flags"]}
    assert "外援 ref_v0" in labels
    assert "外援未就绪" not in labels


def test_api_and_tk_use_the_same_nonempty_summary_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KILL, raising=False)
    snapshot = _snapshot(include_public_q5_avg=True)
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    config = panel.ServerConfig(project_root=tmp_path, snapshot=snapshot_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(config))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only package-shaped API probe
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            api_summary = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()

    assert api_summary["evidence"]["ref_status"] == "count_prior"
    assert api_summary["reference"]["readiness"] == "sparse_exact_prior"
    assert all(
        api_summary["reference"][key] != "-"
        for key in ("conservative", "balanced", "aggressive")
    )
    assert tk_overlay.summarize_snapshot is panel.summarize_snapshot


def test_settlement_truth_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settled = _snapshot()
    settled["phase"] = "settled"
    settled["ui_contract"]["context"]["phase"] = "settled"
    settled["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_value": 920658,
        "total_items": 55,
        "total_cells": 162,
    }
    monkeypatch.delenv(KILL, raising=False)
    default = _run(settled)
    monkeypatch.setenv(KILL, "1")
    rollback = _run(settled)

    assert default == rollback
    assert NOTE not in default["notes"]
