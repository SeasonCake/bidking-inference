"""Inspect live monitor logs without running inference.

This is a lightweight operational check for the live monitor / overlay stack.
It reads the current log directory and reports whether the latest snapshot,
model-eval log, processed manifest, and error log look usable.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "hero_ref" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from network_connection_status import (  # noqa: E402
    NETWORK_CONNECTION_STATUS_FILENAME,
    derive_network_connection_status,
    validate_network_connection_status,
)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _first_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    return {}


def _age_seconds(ts: Any, *, now: float) -> float | None:
    try:
        return max(0.0, now - float(ts))
    except (TypeError, ValueError):
        return None


def _file_age_seconds(path: Path, *, now: float) -> float | None:
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return None


def _round_float(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _session_map_id(session: Any) -> int | None:
    text = _text(session)
    prefix = text.split(":", 1)[0]
    try:
        return int(prefix)
    except (TypeError, ValueError):
        return None


def _format_ignored_sample(sample: Any) -> str:
    if not isinstance(sample, Mapping) or not sample:
        return ""
    message_id = sample.get("message_id")
    packet_tag = sample.get("packet_tag")
    sort_id = sample.get("sort_id")
    parts = [
        _text(sample.get("reason")),
        f"msg={_text(message_id if message_id is not None else '-')}",
        f"tag={_text(packet_tag if packet_tag is not None else '-')}",
        f"sort={_text(sort_id if sort_id is not None else '-')}",
        f"{_text(sample.get('src') or '?')}->{_text(sample.get('dst') or '?')}",
    ]
    frame_session = _text(sample.get("frame_session_id") or "")
    if frame_session:
        parts.append(f"frame_session={frame_session}")
    active_session = _text(sample.get("active_session_id") or "")
    if active_session:
        parts.append(f"session={active_session}")
    return " ".join(part for part in parts if part)


def _status_level(messages: list[str], errors: list[str]) -> str:
    if errors:
        return "error"
    if messages:
        return "warn"
    return "ok"


def _pid_running(pid: Any) -> bool | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid_int,
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return None
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def build_live_status(
    log_dir: Path,
    *,
    now: float | None = None,
    stale_seconds: float = 30.0,
    slow_seconds: float = 15.0,
) -> dict[str, Any]:
    """Return a compact status object for one live monitor log directory."""

    now = time.time() if now is None else float(now)
    root = log_dir.resolve()
    snapshot_path = root / "latest_snapshot.json"
    model_eval_path = root / "model_eval.jsonl"
    error_path = root / "monitor_errors.jsonl"
    manifest_path = root / "processed_files.json"
    lock_path = root / "monitor.lock"
    capture_status_path = root / "capture_source_status.json"
    helper_status_path = root / "wss_helper_status.json"
    network_status_path = root / NETWORK_CONNECTION_STATUS_FILENAME
    lifecycle_status_path = root / "lifecycle_status.json"
    startup_session_path = root / "startup_session.json"

    artifact = _read_json(snapshot_path)
    contract = (
        artifact.get("ui_contract")
        if isinstance(artifact.get("ui_contract"), Mapping)
        else {}
    )
    baseline = _first_mapping(contract.get("baseline"))
    baseline_decision = _first_mapping(baseline.get("decision"))
    baseline_posterior = _first_mapping(baseline.get("posterior"))
    q6_risk = _first_mapping(contract.get("q6_risk_reference"))
    fallback = _first_mapping(contract.get("fallback"))

    model_rows = _read_jsonl_rows(model_eval_path)
    last_model_row = model_rows[-1] if model_rows else {}
    error_rows = _read_jsonl_rows(error_path)
    last_error = error_rows[-1] if error_rows else {}
    manifest = _read_json(manifest_path)
    capture_status = _read_json(capture_status_path)
    helper_status = _read_json(helper_status_path)
    raw_network_status = _read_json(network_status_path)
    lifecycle_status = _read_json(lifecycle_status_path)
    startup_session = _read_json(startup_session_path)
    manifest_values = [
        value for value in manifest.values()
        if isinstance(value, Mapping)
    ]
    manifest_status = Counter(
        str(value.get("status") or (
            "ignored" if value.get("ignored_at_startup") else "ok"
        ))
        for value in manifest_values
    )
    latest_manifest = max(
        manifest_values,
        key=lambda row: float(row.get("processed_at") or 0.0),
        default={},
    )
    lock = _read_json(lock_path)

    snapshot_age = _age_seconds(artifact.get("created_at"), now=now)
    snapshot_file_age = _file_age_seconds(snapshot_path, now=now)
    effective_snapshot_age = (
        snapshot_age if snapshot_age is not None else snapshot_file_age
    )
    model_age = _age_seconds(last_model_row.get("ts"), now=now)
    error_age = _age_seconds(last_error.get("ts"), now=now)
    capture_age = _age_seconds(capture_status.get("ts"), now=now)
    processing_seconds = artifact.get("processing_seconds")
    lock_pid_running = _pid_running(lock.get("pid")) if lock_path.exists() else None
    has_live_capture_status = (
        capture_status_path.exists()
        and _text(capture_status.get("source"))
        in {"windivert", "webhook", "wss_local"}
    )
    lock_age = _age_seconds(lock.get("started_at"), now=now)
    active_flows = capture_status.get("active_flows")
    sniffed_packets = capture_status.get("sniffed_packets")
    raw_packets = capture_status.get("raw_packets")
    accepted_frames = (
        capture_status.get("accepted_frames")
        if "accepted_frames" in capture_status
        else capture_status.get("accepted_packets")
    )
    ignored_frames = capture_status.get("ignored_frames")
    ignored_reasons = (
        capture_status.get("ignored_reasons")
        if isinstance(capture_status.get("ignored_reasons"), Mapping)
        else {}
    )
    ignored_samples = (
        capture_status.get("ignored_samples")
        if isinstance(capture_status.get("ignored_samples"), list)
        else []
    )
    active_session_id = capture_status.get("active_session_id")
    active_session_map_id = _session_map_id(active_session_id)
    launch_id = _text(startup_session.get("launch_id"))
    network_status = validate_network_connection_status(
        raw_network_status,
        launch_id=launch_id,
    )
    if not network_status:
        lifecycle_game_state = _text(lifecycle_status.get("game_state"))
        game_running = (
            True
            if lifecycle_game_state == "running"
            else False
            if lifecycle_game_state == "exited"
            else None
        )
        network_status = derive_network_connection_status(
            launch_id=launch_id,
            game_running=game_running,
            capture_status=capture_status,
            helper_status=helper_status,
        )

    warnings: list[str] = []
    errors: list[str] = []
    if not snapshot_path.exists():
        errors.append("latest_snapshot.json is missing")
    elif effective_snapshot_age is not None and effective_snapshot_age > stale_seconds:
        warnings.append(
            "latest snapshot is stale "
            f"({effective_snapshot_age:.1f}s > {stale_seconds:.1f}s)"
        )
    if not model_rows:
        warnings.append("model_eval.jsonl has no rows")
    if error_rows and (
        model_age is None
        or error_age is not None
        and error_age <= model_age
    ):
        warnings.append("latest monitor error is newer than or as new as model_eval")
    try:
        if processing_seconds is not None and float(processing_seconds) > slow_seconds:
            warnings.append(
                f"latest inference was slow ({float(processing_seconds):.1f}s)"
            )
    except (TypeError, ValueError):
        pass
    if _text(baseline_decision.get("action")) == "":
        warnings.append("baseline action is empty")
    if baseline_posterior.get("status") == "zero_match":
        warnings.append("baseline posterior is zero_match")
    if bool(fallback.get("active")):
        warnings.append("fallback is active")
    if bool(q6_risk.get("affects_bid")) or bool(q6_risk.get("bid_floor_applied")):
        warnings.append("q6 risk is affecting bid thresholds")
    if lock_path.exists() and lock.get("pid") and lock_pid_running is False:
        warnings.append("monitor lock pid is not running")
    if has_live_capture_status and not lock_path.exists():
        warnings.append("live monitor is not running (monitor.lock missing)")
    if has_live_capture_status and lock_path.exists() and lock_pid_running is False:
        warnings.append("live monitor process is not running")
    if network_status.get("game_connection_state") == "failed":
        warnings.append("game WebSocket connection failed in current launch")
    if network_status.get("wss_intercept_state") == "failed":
        warnings.append("WSS local interception failed")
    if network_status.get("capture_decode_state") == "unrecognized":
        warnings.append("WSS data was received but current frames are unrecognized")
    try:
        if (
            has_live_capture_status
            and lock_pid_running is True
            and lock_age is not None
            and lock_age >= 15.0
            and int(active_flows or 0) > 0
            and int(raw_packets or 0) == 0
        ):
            if int(sniffed_packets or 0) > 0:
                warnings.append(
                    "live capture saw TCP payload but none matched BidKing flow"
                )
            else:
                warnings.append(
                    "live capture has active flow but no new payload packets yet"
                )
    except (TypeError, ValueError):
        pass
    try:
        if (
            has_live_capture_status
            and lock_pid_running is True
            and int(raw_packets or 0) > 0
            and int(accepted_frames or 0) == 0
        ):
            warnings.append(
                "live capture saw payload but no auction frames were accepted"
            )
    except (TypeError, ValueError):
        pass

    return {
        "level": _status_level(warnings, errors),
        "errors": errors,
        "warnings": warnings,
        "log_dir": str(root),
        "snapshot": {
            "exists": snapshot_path.exists(),
            "path": str(snapshot_path),
            "age_seconds": _round_float(snapshot_age),
            "file_age_seconds": _round_float(snapshot_file_age),
            "source_file": artifact.get("file"),
            "formal_mode": artifact.get("formal_mode"),
            "formal_mode_reason": artifact.get("formal_mode_reason"),
            "hero": artifact.get("hero"),
            "map_id": artifact.get("map_id"),
            "round": artifact.get("round"),
            "processing_seconds": artifact.get("processing_seconds"),
            "n_trials": artifact.get("n_trials"),
            "shadow_trials": artifact.get("shadow_trials"),
        },
        "baseline": {
            "source": _text(baseline.get("source")),
            "action": _text(baseline_decision.get("action")),
            "current_highest": _text(baseline_decision.get("current_highest")),
            "risk_band": _text(baseline_decision.get("risk_band")),
            "probe_bid": _text(baseline_decision.get("probe_bid")),
            "defend_bid": _text(baseline_decision.get("defend_bid")),
            "attack_bid": _text(baseline_decision.get("attack_bid")),
            "stop_price": _text(baseline_decision.get("stop_price")),
            "posterior_status": _text(baseline_posterior.get("status")),
            "matched": baseline_posterior.get("matched"),
            "total": baseline_posterior.get("total"),
            "decision_value_range": _text(
                baseline_posterior.get("decision_value_range")
            ),
            "total_cells_range": _text(baseline_posterior.get("total_cells_range")),
        },
        "q6": {
            "risk": bool(q6_risk.get("risk")),
            "affects_bid": bool(q6_risk.get("affects_bid")),
            "bid_floor_applied": bool(q6_risk.get("bid_floor_applied")),
            "reference_p90": _text(
                q6_risk.get("practical_reference_p90")
                or q6_risk.get("prior_reference_p90")
            ),
            "decision_value_range": _text(
                baseline_posterior.get("q6_decision_value_range")
            ),
        },
        "fallback": {
            "active": bool(fallback.get("active")),
            "mode": _text(fallback.get("mode")),
            "affects_bid": bool(fallback.get("affects_bid")),
        },
        "model_eval": {
            "exists": model_eval_path.exists(),
            "path": str(model_eval_path),
            "rows": len(model_rows),
            "last_age_seconds": _round_float(model_age),
            "last_file": last_model_row.get("file"),
            "zero_match": bool(last_model_row.get("zero_posterior_match")),
            "layout_conflict": bool(last_model_row.get("layout_conflict")),
        },
        "monitor_errors": {
            "exists": error_path.exists(),
            "path": str(error_path),
            "rows": len(error_rows),
            "last_age_seconds": _round_float(error_age),
            "last_name": last_error.get("name"),
            "last_error_type": last_error.get("error_type"),
            "last_error": last_error.get("error"),
        },
        "processed_files": {
            "exists": manifest_path.exists(),
            "path": str(manifest_path),
            "total": len(manifest_values),
            "status_counts": dict(sorted(manifest_status.items())),
            "latest_name": latest_manifest.get("name"),
            "latest_age_seconds": _round_float(
                _age_seconds(latest_manifest.get("processed_at"), now=now)
            ),
        },
        "lock": {
            "exists": lock_path.exists(),
            "path": str(lock_path),
            "pid": lock.get("pid"),
            "pid_running": lock_pid_running,
            "age_seconds": _round_float(lock_age),
        },
        "capture_source": {
            "exists": capture_status_path.exists(),
            "path": str(capture_status_path),
            "source": capture_status.get("source"),
            "age_seconds": _round_float(capture_age),
            "process_name": capture_status.get("process_name"),
            "active_flows": active_flows,
            "sniffed_packets": sniffed_packets,
            "raw_packets": raw_packets,
            "accepted_frames": accepted_frames,
            "ignored_frames": ignored_frames,
            "ignored_reasons": dict(ignored_reasons),
            "ignored_samples": tuple(
                sample for sample in ignored_samples if isinstance(sample, Mapping)
            ),
            "last_ignored_sample": (
                ignored_samples[-1]
                if ignored_samples and isinstance(ignored_samples[-1], Mapping)
                else {}
            ),
            "active_session_id": active_session_id,
            "active_session_map_id": active_session_map_id,
        },
        "connection_status": network_status,
    }


def _fmt_age(value: Any) -> str:
    if value is None:
        return "?"
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "?"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60.0:.1f}h"


def format_status_text(status: Mapping[str, Any]) -> str:
    snapshot = _first_mapping(status.get("snapshot"))
    baseline = _first_mapping(status.get("baseline"))
    q6 = _first_mapping(status.get("q6"))
    fallback = _first_mapping(status.get("fallback"))
    model_eval = _first_mapping(status.get("model_eval"))
    errors = _first_mapping(status.get("monitor_errors"))
    processed = _first_mapping(status.get("processed_files"))
    lock = _first_mapping(status.get("lock"))
    capture = _first_mapping(status.get("capture_source"))
    connection = _first_mapping(status.get("connection_status"))
    lines = [
        f"BidKing live status: {str(status.get('level') or 'unknown').upper()}",
        f"LogDir: {status.get('log_dir')}",
        (
            "Snapshot: "
            f"{snapshot.get('source_file') or '?'} | "
            f"age {_fmt_age(snapshot.get('age_seconds'))} | "
            f"hero={snapshot.get('hero') or '?'} "
            f"map={snapshot.get('map_id') or '?'} "
            f"round={snapshot.get('round') or '?'} | "
            f"processing={snapshot.get('processing_seconds') or '?'}s"
        ),
        (
            "Baseline: "
            f"source={baseline.get('source') or snapshot.get('formal_mode') or '?'} | "
            f"{baseline.get('action') or 'no action'} | "
            f"{baseline.get('current_highest') or 'no bid'} | "
            f"risk={baseline.get('risk_band') or '?'} | "
            f"defend={baseline.get('defend_bid') or '?'} | "
            f"stop={baseline.get('stop_price') or '?'}"
        ),
        (
            "Posterior: "
            f"{baseline.get('posterior_status') or '?'} "
            f"{baseline.get('matched') or '?'}/{baseline.get('total') or '?'} | "
            f"value={baseline.get('decision_value_range') or '?'} | "
            f"cells={baseline.get('total_cells_range') or '?'}"
        ),
        (
            "Q6: "
            f"risk={q6.get('risk')} "
            f"affects_bid={q6.get('affects_bid')} "
            f"floor={q6.get('bid_floor_applied')} | "
            f"ref={q6.get('reference_p90') or '?'} | "
            f"range={q6.get('decision_value_range') or '?'}"
        ),
        (
            "Fallback: "
            f"active={fallback.get('active')} "
            f"affects_bid={fallback.get('affects_bid')} "
            f"mode={fallback.get('mode') or '-'}"
        ),
        (
            "Logs: "
            f"model_eval rows={model_eval.get('rows') or 0} "
            f"last_age={_fmt_age(model_eval.get('last_age_seconds'))}; "
            f"errors={errors.get('rows') or 0} "
            f"last={errors.get('last_name') or '-'} "
            f"{errors.get('last_error_type') or ''}".rstrip()
        ),
        (
            "Processed: "
            f"total={processed.get('total') or 0} "
            f"status={processed.get('status_counts') or {}} "
            f"latest={processed.get('latest_name') or '-'} "
            f"age={_fmt_age(processed.get('latest_age_seconds'))}"
        ),
        (
            "Lock: "
            f"exists={lock.get('exists')} "
            f"pid={lock.get('pid') or '-'} "
            f"running={lock.get('pid_running')} "
            f"age={_fmt_age(lock.get('age_seconds'))}"
        ),
        (
            "Capture: "
            f"source={capture.get('source') or '-'} "
            f"age={_fmt_age(capture.get('age_seconds'))} "
            f"flows={capture.get('active_flows') if capture.get('active_flows') is not None else '?'} "
            f"sniffed={capture.get('sniffed_packets') if capture.get('sniffed_packets') is not None else '?'} "
            f"raw={capture.get('raw_packets') if capture.get('raw_packets') is not None else '?'} "
            f"accepted={capture.get('accepted_frames') if capture.get('accepted_frames') is not None else '?'} "
            f"ignored={capture.get('ignored_frames') if capture.get('ignored_frames') is not None else '?'} "
            f"session={capture.get('active_session_id') or '-'} "
            f"map={capture.get('active_session_map_id') or '-'}"
        ),
        (
            "Connection: "
            f"game={connection.get('game_connection_state') or 'unknown'} "
            f"wss={connection.get('wss_intercept_state') or 'unknown'} "
            f"decode={connection.get('capture_decode_state') or 'idle'}"
        ),
    ]
    ignored_reasons = _first_mapping(capture.get("ignored_reasons"))
    if ignored_reasons or capture.get("last_ignored_sample"):
        reason_text = " / ".join(
            f"{name}×{count}"
            for name, count in list(sorted(ignored_reasons.items()))
        )
        last_sample = _format_ignored_sample(capture.get("last_ignored_sample"))
        lines.append(
            "Ignored: "
            f"{reason_text or '无'}"
            + (f" | last {last_sample}" if last_sample else "")
        )
    for error in status.get("errors") or ():
        lines.append(f"ERROR: {error}")
    for warning in status.get("warnings") or ():
        lines.append(f"WARN: {warning}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect BidKing live monitor logs and current UI contract.",
    )
    parser.add_argument(
        "--log-dir",
        default=str(ROOT / "data" / "logs" / "live"),
        help="Live monitor log directory.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=30.0,
        help="Warn when latest_snapshot.json is older than this many seconds.",
    )
    parser.add_argument(
        "--slow-seconds",
        type=float,
        default=15.0,
        help="Warn when latest inference processing time is above this threshold.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for warnings as well as errors.",
    )
    args = parser.parse_args()

    status = build_live_status(
        Path(args.log_dir),
        stale_seconds=args.stale_seconds,
        slow_seconds=args.slow_seconds,
    )
    if args.format == "json":
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(format_status_text(status))
    if status["level"] == "error":
        return 2
    if args.strict and status["level"] == "warn":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
