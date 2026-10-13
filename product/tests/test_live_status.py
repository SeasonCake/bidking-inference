from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "live_status.py"
    spec = importlib.util.spec_from_file_location("live_status", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _healthy_snapshot(now: float) -> dict:
    return {
        "created_at": now - 5.0,
        "file": "aisha_shipwreck_sample.json",
        "hero": "aisha",
        "map_id": 2501,
        "round": 4,
        "processing_seconds": 4.2,
        "n_trials": 500,
        "shadow_trials": 80,
        "ui_contract": {
            "baseline": {
                "decision": {
                    "action": "可守不抢",
                    "current_highest": "玩家A 500,000",
                    "risk_band": "防守区",
                    "probe_bid": "580,000",
                    "defend_bid": "620,000",
                    "attack_bid": "620,000",
                    "stop_price": "700,000",
                },
                "posterior": {
                    "status": "ok",
                    "matched": 72,
                    "total": 80,
                    "decision_value_range": "580,000 / 690,000 / 830,000",
                    "total_cells_range": "118 / 126 / 134",
                    "q6_decision_value_range": "0 / 120,000 / 420,000",
                },
            },
            "q6_risk_reference": {
                "risk": True,
                "affects_bid": False,
                "bid_floor_applied": False,
                "practical_reference_p90": 420000,
            },
            "fallback": {
                "active": False,
                "affects_bid": False,
                "mode": "",
            },
        },
    }


def test_live_status_reports_healthy_log_dir(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: True)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(
        tmp_path / "model_eval.jsonl",
        [
            {
                "ts": now - 4.0,
                "file": "aisha_shipwreck_sample.json",
                "zero_posterior_match": False,
                "layout_conflict": False,
            }
        ],
    )
    _write_json(
        tmp_path / "processed_files.json",
        {
            str((tmp_path / "aisha_shipwreck_sample.json").resolve()): {
                "name": "aisha_shipwreck_sample.json",
                "status": "ok",
                "processed_at": now - 3.0,
            }
        },
    )
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 60.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "process_name": "BidKing.exe",
            "active_flows": 2,
            "sniffed_packets": 12,
            "raw_packets": 10,
            "accepted_frames": 3,
            "active_session_id": "2501:123",
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert status["level"] == "ok"
    assert status["snapshot"]["source_file"] == "aisha_shipwreck_sample.json"
    assert status["baseline"]["action"] == "可守不抢"
    assert status["baseline"]["defend_bid"] == "620,000"
    assert status["baseline"]["posterior_status"] == "ok"
    assert status["q6"]["risk"] is True
    assert status["q6"]["affects_bid"] is False
    assert status["q6"]["bid_floor_applied"] is False
    assert status["fallback"]["active"] is False
    assert status["lock"]["pid_running"] is True
    assert status["capture_source"]["source"] == "windivert"
    assert status["capture_source"]["sniffed_packets"] == 12
    assert status["capture_source"]["accepted_frames"] == 3
    assert status["capture_source"]["active_session_map_id"] == 2501
    assert status["processed_files"]["status_counts"] == {"ok": 1}

    text = module.format_status_text(status)
    assert "BidKing live status: OK" in text
    assert "aisha_shipwreck_sample.json" in text
    assert "defend=620,000" in text
    assert "Q6: risk=True affects_bid=False floor=False" in text
    assert "running=True" in text
    assert "Capture: source=windivert" in text
    assert "session=2501:123 map=2501" in text


def test_live_status_reports_last_ignored_capture_sample(tmp_path: Path) -> None:
    module = _module()
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 4.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 60.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "process_name": "BidKing.exe",
            "active_flows": 1,
            "sniffed_packets": 12,
            "raw_packets": 10,
            "accepted_frames": 3,
            "ignored_frames": 7,
            "ignored_reasons": {
                "rev_not_game_frame": 5,
                "send_wrong_session": 2,
            },
            "ignored_samples": [
                {
                    "reason": "rev_not_game_frame",
                    "direction": "REV",
                    "sort_id": 18,
                    "capture_time": "2026-06-03 00:00:01.000",
                    "message_id": "0x0021",
                    "packet_tag": 0,
                    "src": "8.133.195.27:10000",
                    "dst": "198.18.0.1:60213",
                    "raw_length": 64,
                    "raw_prefix": "abcd",
                    "active_session_id": "2401:123",
                }
            ],
            "active_session_id": "2401:123",
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert status["capture_source"]["ignored_frames"] == 7
    assert status["capture_source"]["ignored_reasons"] == {
        "rev_not_game_frame": 5,
        "send_wrong_session": 2,
    }
    assert status["capture_source"]["last_ignored_sample"]["message_id"] == "0x0021"
    text = module.format_status_text(status)
    assert "ignored=7" in text
    assert "Ignored: rev_not_game_frame×5 / send_wrong_session×2" in text
    assert "last rev_not_game_frame msg=0x0021" in text


def test_live_status_warns_on_stale_slow_fallback_and_bid_affecting_q6(
    tmp_path: Path,
) -> None:
    module = _module()
    now = 1_000.0
    snapshot = _healthy_snapshot(now)
    snapshot["created_at"] = now - 100.0
    snapshot["processing_seconds"] = 22.5
    snapshot["ui_contract"]["baseline"]["decision"]["action"] = ""
    snapshot["ui_contract"]["baseline"]["posterior"]["status"] = "zero_match"
    snapshot["ui_contract"]["q6_risk_reference"]["affects_bid"] = True
    snapshot["ui_contract"]["q6_risk_reference"]["bid_floor_applied"] = True
    snapshot["ui_contract"]["fallback"]["active"] = True
    snapshot["ui_contract"]["fallback"]["affects_bid"] = False
    snapshot["ui_contract"]["fallback"]["mode"] = "v1_map_prior_zero_match"
    _write_json(tmp_path / "latest_snapshot.json", snapshot)
    _append_jsonl(
        tmp_path / "model_eval.jsonl",
        [{"ts": now - 10.0, "file": "old.json"}],
    )
    _append_jsonl(
        tmp_path / "monitor_errors.jsonl",
        [
            {
                "ts": now - 2.0,
                "name": "bad.json",
                "error_type": "ValueError",
                "error": "malformed",
            }
        ],
    )

    status = module.build_live_status(
        tmp_path,
        now=now,
        stale_seconds=30.0,
        slow_seconds=15.0,
    )

    assert status["level"] == "warn"
    assert status["fallback"]["active"] is True
    assert status["q6"]["affects_bid"] is True
    assert status["q6"]["bid_floor_applied"] is True
    assert any("stale" in warning for warning in status["warnings"])
    assert any("slow" in warning for warning in status["warnings"])
    assert any("baseline action is empty" in warning for warning in status["warnings"])
    assert any("zero_match" in warning for warning in status["warnings"])
    assert any("fallback is active" in warning for warning in status["warnings"])
    assert any("q6 risk is affecting bid" in warning for warning in status["warnings"])
    assert any("monitor error" in warning for warning in status["warnings"])


def test_live_status_errors_when_snapshot_missing(tmp_path: Path) -> None:
    module = _module()

    status = module.build_live_status(tmp_path, now=1_000.0)

    assert status["level"] == "error"
    assert status["errors"] == ["latest_snapshot.json is missing"]
    text = module.format_status_text(status)
    assert "BidKing live status: ERROR" in text
    assert "ERROR: latest_snapshot.json is missing" in text


def test_live_status_uses_snapshot_file_age_when_created_at_missing(
    tmp_path: Path,
) -> None:
    module = _module()
    now = 1_000.0
    snapshot = _healthy_snapshot(now)
    snapshot.pop("created_at")
    snapshot_path = tmp_path / "latest_snapshot.json"
    _write_json(snapshot_path, snapshot)
    os.utime(snapshot_path, (now - 100.0, now - 100.0))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])

    status = module.build_live_status(
        tmp_path,
        now=now,
        stale_seconds=30.0,
    )

    assert status["level"] == "warn"
    assert status["snapshot"]["age_seconds"] is None
    assert status["snapshot"]["file_age_seconds"] == 100.0
    assert any("latest snapshot is stale" in warning for warning in status["warnings"])


def test_live_status_warns_when_monitor_lock_pid_is_not_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: False)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 987654, "started_at": now - 50.0})

    status = module.build_live_status(tmp_path, now=now)

    assert status["level"] == "warn"
    assert status["lock"]["pid_running"] is False
    assert any("lock pid is not running" in warning for warning in status["warnings"])
    assert "running=False" in module.format_status_text(status)


def test_live_status_warns_when_capture_status_exists_but_lock_missing(
    tmp_path: Path,
) -> None:
    module = _module()
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "active_flows": 1,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
    )

    status = module.build_live_status(
        tmp_path,
        now=now,
        stale_seconds=999999.0,
    )

    assert status["level"] == "warn"
    assert status["lock"]["exists"] is False
    assert status["capture_source"]["raw_packets"] == 0
    assert any("monitor.lock missing" in warning for warning in status["warnings"])
    text = module.format_status_text(status)
    assert "Capture: source=windivert" in text
    assert "WARN: live monitor is not running" in text


def test_live_status_warns_when_active_flow_has_no_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: True)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 20.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "active_flows": 1,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert status["level"] == "warn"
    assert any("no new payload" in warning for warning in status["warnings"])


def test_live_status_warns_when_payload_has_no_accepted_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: True)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 20.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "active_flows": 1,
            "raw_packets": 4,
            "accepted_frames": 0,
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert status["level"] == "warn"
    assert any("no auction frames" in warning for warning in status["warnings"])


def test_live_status_treats_wss_local_as_live_capture_and_reports_three_layers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: True)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 20.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 4,
            "accepted_frames": 0,
        },
    )
    _write_json(
        tmp_path / "network_connection_status.json",
        {
            "schema_version": 1,
            "updated_at": now - 1.0,
            "game_connection_state": "connected",
            "wss_intercept_state": "attached",
            "capture_decode_state": "unrecognized",
            "evidence": {
                "game_running": True,
                "active_flows": 1,
                "wss_raw_count": 4,
                "accepted_frames": 0,
            },
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert not any("monitor.lock missing" in item for item in status["warnings"])
    assert status["connection_status"]["game_connection_state"] == "connected"
    assert status["connection_status"]["wss_intercept_state"] == "attached"
    assert status["connection_status"]["capture_decode_state"] == "unrecognized"
    assert any("current frames are unrecognized" in item for item in status["warnings"])
    assert "Connection: game=connected wss=attached decode=unrecognized" in (
        module.format_status_text(status)
    )


def test_live_status_keeps_game_failure_separate_from_ready_wss(
    tmp_path: Path,
) -> None:
    module = _module()
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "wss_local",
            "active_flows": 0,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
    )
    _write_json(
        tmp_path / "network_connection_status.json",
        {
            "schema_version": 1,
            "updated_at": now - 1.0,
            "game_connection_state": "failed",
            "wss_intercept_state": "ready",
            "capture_decode_state": "idle",
            "evidence": {
                "game_running": True,
                "player_log_error_counts": {
                    "websocket_unable_to_connect": 1
                },
            },
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert any("game WebSocket connection failed" in item for item in status["warnings"])
    assert not any("WSS local interception failed" in item for item in status["warnings"])


def test_live_status_warns_when_sniffed_packets_do_not_match_bidking_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_pid_running", lambda _pid: True)
    now = 1_000.0
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(now))
    _append_jsonl(tmp_path / "model_eval.jsonl", [{"ts": now - 2.0}])
    _write_json(tmp_path / "monitor.lock", {"pid": 1234, "started_at": now - 20.0})
    _write_json(
        tmp_path / "capture_source_status.json",
        {
            "ts": now - 1.0,
            "source": "windivert",
            "active_flows": 1,
            "sniffed_packets": 20,
            "raw_packets": 0,
            "accepted_frames": 0,
        },
    )

    status = module.build_live_status(tmp_path, now=now)

    assert status["level"] == "warn"
    assert any("none matched BidKing flow" in warning for warning in status["warnings"])


def test_live_status_cli_strict_returns_nonzero_for_warning(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    _write_json(tmp_path / "latest_snapshot.json", _healthy_snapshot(1_000.0))

    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "live_status.py",
            "--log-dir",
            str(tmp_path),
            "--stale-seconds",
            "999999",
            "--strict",
        ],
    )

    assert module.main() == 1
    assert "model_eval.jsonl has no rows" in capsys.readouterr().out
