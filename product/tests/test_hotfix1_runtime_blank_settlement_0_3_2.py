from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.live.fatbeans import FatbeansActionResult, FatbeansStateEvent
from bidking_lab.live.live_refresh_signatures import (
    inference_semantic_signature_from_states,
)
from test_fatbeans_webhook_monitor import _module as _monitor_module
from test_live_overlay import _ahmad_server_module


def test_semantic_signature_sorts_mixed_optional_wire_values() -> None:
    """Real 5601/4510 facts mix absent and numeric results in one action set."""
    absent = FatbeansActionResult(
        action_id=100115,
        result=None,
        result_field=7,
    )
    numeric = FatbeansActionResult(
        action_id=100115,
        result=35,
        result_field=7,
    )
    state = FatbeansStateEvent(
        sort_id=9,
        capture_time="2026-08-09T17:33:57+08:00",
        message_id=0x25,
        session_id="5601:runtime-regression",
        map_id=5601,
        round_index=1,
        action_results=(absent, numeric),
    )

    forward = inference_semantic_signature_from_states((state,))
    reverse = inference_semantic_signature_from_states(
        (replace(state, action_results=(numeric, absent)),)
    )

    assert forward is not None
    assert reverse == forward


def _monitor(module: Any, tmp_path: Path) -> Any:
    return module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=tmp_path / "logs",
            raw_dir=tmp_path / "raw",
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=20,
            roi_trials=0,
            shadow_trials=20,
            full_shadow_trials=20,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=30.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )


def test_worker_reclaims_current_rows_when_lightweight_probe_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached probe failure must not let an older trace reject publication."""
    module = _monitor_module()
    monitor = _monitor(module, tmp_path)
    signature_calls = 0
    builds: list[str] = []
    writes: list[dict[str, Any]] = []

    def semantic_signature(_payload: str | bytes) -> tuple[str, int]:
        nonlocal signature_calls
        signature_calls += 1
        if signature_calls == 1:
            raise TypeError("mixed optional wire values")
        return ("current", 1)

    monkeypatch.setattr(module, "_semantic_signature_from_payload", semantic_signature)
    monkeypatch.setattr(
        module,
        "build_monitor_artifact_from_payload",
        lambda _payload, *, file, **_kwargs: builds.append(file)
        or {"file": file, "phase": "bidding"},
    )
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture_payload",
        lambda *args, **kwargs: SimpleNamespace(states=()),
    )
    monkeypatch.setattr(
        module,
        "write_monitor_logs",
        lambda artifact, **_kwargs: writes.append(dict(artifact)),
    )

    with monitor._lock:
        monitor._active_compute_trace = {"semantic_recognized": 1.0}
        monitor._active_compute_signature = ("older", 1)
        monitor._active_compute_generation = monitor._row_generation
        monitor._compute_trace_seq = 1

    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    assert monitor._last_probed_semantic_count == 1
    assert monitor._last_probed_semantic_signature is None

    monitor._process_snapshot(force=False)

    assert signature_calls == 2
    assert builds == [monitor.raw_path.name]
    assert len(writes) == 1
    assert monitor.status()["last_processed_count"] == 1
    assert monitor._active_compute_signature == ("current", 1)


def test_maria_r1_uses_labelled_count_prior_instead_of_opening_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_server_module()

    class _Result:
        @staticmethod
        def as_dict() -> dict[str, Any]:
            return {
                "status": "count_prior",
                "source": "ref_v0",
                "balanced": 646_956,
                "notes": ["count_prior_enumerated"],
            }

    monkeypatch.setattr(
        module,
        "hero_quote_ready",
        lambda *_args, **_kwargs: (False, "R1白绿蓝价值/品质"),
    )
    monkeypatch.setattr(module, "run_reference_engine", lambda *_args, **_kwargs: _Result())
    monkeypatch.setattr(module, "prepare_reference_engine_snapshot", dict)
    monkeypatch.setattr(module, "hero_max_combos_for_round", lambda *_args: 2_500)

    result = module._hero_single_pass_ref_result(  # type: ignore[attr-defined]
        {"hero": "maria", "round": 1, "phase": "reading"},
        hero_key="maria",
        round_no=1,
        public_info={},
    )

    assert result["status"] == "count_prior"
    assert result["balanced"] == 646_956
    assert "hero_ref_provisional" in result["notes"]
    assert "hero_ref_wait:R1白绿蓝价值/品质" in result["notes"]

    later_round = module._hero_single_pass_ref_result(  # type: ignore[attr-defined]
        {"hero": "maria", "round": 2, "phase": "reading"},
        hero_key="maria",
        round_no=2,
        public_info={},
    )
    assert later_round["status"] == "missing_total_count"
    assert later_round.get("balanced") is None

    monkeypatch.setenv("DRAW_HERO_DISABLE_PROVISIONAL_QUOTE", "1")
    disabled = module._hero_single_pass_ref_result(  # type: ignore[attr-defined]
        {"hero": "maria", "round": 1, "phase": "reading"},
        hero_key="maria",
        round_no=1,
        public_info={},
    )
    assert disabled["status"] == "missing_total_count"
    assert disabled.get("balanced") is None


def test_settlement_truth_survives_unrelated_ui_projection_budget_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ahmad_server_module()

    class _Result:
        @staticmethod
        def as_dict() -> dict[str, Any]:
            return {
                "status": "ok",
                "source": "ref_v0",
                "balanced": 800_000,
                "conservative": 700_000,
                "aggressive": 900_000,
                "notes": ["settlement_review"],
            }

    # Reproduce the Phase1 projector outcome seen on the large real 5601/4510
    # settlement contracts without carrying a 16k-node fixture in the repository.
    monkeypatch.setattr(
        module,
        "prepare_reference_engine_snapshot",
        lambda snapshot: {**snapshot, "ui_contract": {}},
    )
    monkeypatch.setattr(module, "run_reference_engine", lambda *_args, **_kwargs: _Result())
    snapshot = {
        "created_at": time.time(),
        "session_id": "5601:settlement-regression",
        "map_id": 5601,
        "hero": "maria",
        "round": 4,
        "phase": "settled",
        "inventory_cells": 114,
        "final_quality_counts": "q1=2;q2=10;q3=9;q4=5;q5=5;q6=6",
        "final_quality_cells": "q1=3;q2=26;q3=14;q4=14;q5=35;q6=22",
        "ui_contract": {
            "context": {
                "session_id": "5601:settlement-regression",
                "map_id": 5601,
                "hero": "maria",
                "round": 4,
                "phase": "settled",
            },
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "total_value": 1_520_058,
                "total_items": 37,
                "total_cells": 114,
                "q6": {"count": 6, "cells": 22, "value": 1_121_979},
                "top_item": {
                    "id": 1_026_007,
                    "name": "超声波诊断仪",
                    "quality": 6,
                    "value": 475_000,
                    "cells": 9,
                },
            },
        },
    }

    summary = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["truth"]["available"] is True
    assert summary["truth"]["complete"] is True
    assert summary["truth"]["total_value"] == 1_520_058
    assert summary["reference"]["source"] == "settlement"
    assert summary["reference"]["action"] == "结算完成"
    assert summary["reference"]["balanced"] == "1,520,058"


def test_settlement_truth_projection_rejects_protocol_subclasses_without_calls() -> None:
    module = _ahmad_server_module()
    calls: list[str] = []

    class HostileDict(dict):
        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            calls.append("get")
            raise AssertionError("must not call hostile get")

        def items(self):
            calls.append("items")
            raise AssertionError("must not call hostile items")

        def __iter__(self):
            calls.append("iter")
            raise AssertionError("must not call hostile iter")

    class HostileStr(str):
        def __str__(self) -> str:
            calls.append("str")
            raise AssertionError("must not stringify hostile scalar")

    assert module._settlement_truth_snapshot(HostileDict(available=True)) == {}  # type: ignore[attr-defined]
    assert module._settlement_truth_snapshot(  # type: ignore[attr-defined]
        {"available": True, "source": HostileStr("settlement")}
    ) == {}
    assert calls == []
