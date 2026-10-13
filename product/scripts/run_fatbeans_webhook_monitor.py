"""Receive Fatbeans WebHook packets and feed the live monitor.

Fatbeans sends one captured packet per HTTP POST. This receiver keeps the
request path read-only and returns "allow" immediately, then debounces the
accumulated TCP stream in a background worker and reuses the existing Fatbeans
JSON parser / live monitor artifact builder.
"""

from __future__ import annotations

import argparse
import atexit
import base64
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import mmap
import os
from pathlib import Path
import queue
import re
import sys
import struct
import subprocess
import threading
import time
import traceback
import uuid
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab._atomic_json import atomic_write_json as _atomic_write_json  # noqa: E402
from bidking_lab.live.monitor import (  # noqa: E402
    _FULL_WIRE_CAPACITY,
    _full_wire_tokens,
    _full_wire_decode,
    _full_mmap_tokens,
    _full_tables_snapshot,
    _fresh_session_game_version,
    _has_authoritative_completed_action_result,
    _settlement_category_model_map_matches_physical,
    build_monitor_artifact_from_payload,
    load_monitor_tables,
    write_monitor_logs,
)
from bidking_lab.live.fatbeans import parse_fatbeans_capture_payload  # noqa: E402
from bidking_lab.live.fact_receipts import FactReceiptWriter  # noqa: E402
from bidking_lab.live.warehouse_source import (  # noqa: E402
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
)
from bidking_lab.simulation.basic_mc import DropPoolResolutionError  # noqa: E402
from bidking_lab.live.live_refresh_signatures import (
    bid_signature_from_payload,
    inference_semantic_signature_from_payload,
    refresh_latest_snapshot_bids,
)  # noqa: E402
from package_generation_identity import (  # noqa: E402
    compute_package_generation_identity,
    is_generation_digest,
)


ALLOW_RESPONSE = {
    "ErrorCode": 0,
    "ErrorMsg": "ok",
    "OpCode": 0,
    "NewData": "",
    "Request": None,
    "Response": None,
}

FAST_BOOTSTRAP_MIN_ROWS = 3
# Roll back pending-row scheduling + same-session snapshot age refresh (0x0077 heartbeat track).
FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH = "FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH"
COMPUTE_STATUS_FILENAME = "monitor_compute_status.json"
COMPUTE_STATUS_SCHEMA_VERSION = 1
_COMPUTE_SIGNATURE_UNSET = object()
_COMPUTE_GENERATION_UNSET = object()
_COMPUTE_TERMINAL_REASONS = frozenset(
    {
        "drop_pool_incomplete",
        "inference_input_error",
        "inference_error",
    }
)


def _support_safe_inference_failure_reason(exc: BaseException) -> str:
    if isinstance(exc, DropPoolResolutionError):
        return "drop_pool_incomplete"
    if isinstance(exc, (OSError, ValueError)):
        return "inference_input_error"
    return "inference_error"


def _monitor_heartbeat_refresh_enabled() -> bool:
    return os.environ.get(FATBEANS_DISABLE_MONITOR_HEARTBEAT_REFRESH) != "1"


def _artifact_phase(artifact: Mapping[str, Any]) -> str:
    ui_contract = artifact.get("ui_contract")
    if isinstance(ui_contract, dict):
        context = ui_contract.get("context")
        if isinstance(context, dict) and context.get("phase"):
            return str(context["phase"])
    return str(artifact.get("phase") or "")


def _fact_digest(value: Any) -> str:
    """Hash an in-memory projection without persisting its potentially rich body."""
    try:
        encoded = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    except Exception:
        encoded = b"<unserializable>"
    return hashlib.sha256(encoded).hexdigest()


def _exact_int(value: Any, *, minimum: int = 0) -> int | None:
    if type(value) is not int or value < minimum:  # noqa: E721 - wire boundary
        return None
    return value


def _is_current_completed_action(
    row: Mapping[str, Any], *, session_id: str, action_round: int, observed_round: int
) -> bool:
    if type(row) is not dict:  # noqa: E721 - normalized artifact boundary
        return False
    declared = _exact_int(dict.get(row, "round"), minimum=1)
    action_id = _exact_int(dict.get(row, "action_id"), minimum=1)
    source_message_id = _exact_int(dict.get(row, "source_message_id"), minimum=1)
    return (
        type(dict.get(row, "session_id")) is str  # noqa: E721
        and dict.get(row, "session_id") == session_id
        and type(dict.get(row, "source_kind")) is str  # noqa: E721
        and dict.get(row, "source_kind") == "state_replay"
        and source_message_id == 0x0025
        and action_id is not None
        and declared is not None
        and declared == observed_round
        and declared + 1 == action_round
        and _exact_int(dict.get(row, "promoted_action_round"), minimum=1)
        == action_round
        and dict.get(row, "promotion_reason") == "completed_round_state_replay"
        and _has_authoritative_completed_action_result(row)
    )


def _current_action_promotion(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Describe only accepted same-session completed-round action promotion."""
    session_id = artifact.get("session_id")
    action_round = _exact_int(artifact.get("action_round"), minimum=1)
    observed_round = _exact_int(artifact.get("observed_round"), minimum=1)
    rows = artifact.get("action_result_rows")
    accepted_rows: list[dict[str, Any]] = []
    if (
        type(session_id) is str  # noqa: E721
        and session_id
        and session_id.strip() == session_id
        and action_round is not None
        and observed_round is not None
        and type(rows) is list  # noqa: E721
    ):
        for row in rows:
            if type(row) is not dict or dict.get(row, "session_id") != session_id:  # noqa: E721
                continue
            if _is_current_completed_action(
                row, session_id=session_id, action_round=action_round, observed_round=observed_round
            ):
                accepted_rows.append(
                    {
                        key: dict.get(row, key)
                        for key in (
                            "action_id",
                            "result",
                            "result_field",
                            "result_wire_type",
                            "result_raw_hex",
                            "round",
                            "source_message_id",
                            "source_kind",
                            "sort",
                            "promoted_action_round",
                            "promotion_reason",
                            "revealed_items",
                        )
                        if dict.__contains__(row, key)
                    }
                )
    accepted = len(accepted_rows)
    return {
        "accepted_count": accepted,
        "action_set_hash": _fact_digest(accepted_rows),
        "reason": (
            "completed_round_state_replay_to_current_action_round"
            if accepted
            else "no_current_action_promotion"
        ),
    }


def _settlement_category_fact_receipt(
    artifact: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Copy only a fully closed, deidentified settlement-category projection."""

    value = artifact.get("settlement_category_fact_v1")
    physical_map_id = _exact_int(
        value.get("physical_map_id") if type(value) is dict else None,
        minimum=1,
    )
    model_map_id = _exact_int(
        value.get("model_map_id") if type(value) is dict else None,
        minimum=1,
    )
    artifact_physical_map_id = _exact_int(artifact.get("map_id"), minimum=1)
    artifact_model_map_id = _exact_int(artifact.get("model_map_id"), minimum=1)
    artifact_warehouse_generation = artifact.get("warehouse_source_generation")
    if (
        _artifact_phase(artifact) != "settled"
        or type(value) is not dict  # noqa: E721 - monitor boundary
        or value.get("schema") != "settlement_category_fact_v1"
        or physical_map_id is None
        or physical_map_id != artifact_physical_map_id
        or model_map_id is None
        or model_map_id != artifact_model_map_id
        or not _settlement_category_model_map_matches_physical(
            physical_map_id,
            model_map_id,
        )
        or type(value.get("warehouse_generation")) is not str  # noqa: E721
        or not value["warehouse_generation"]
        or type(artifact_warehouse_generation) is not str  # noqa: E721
        or value["warehouse_generation"] != artifact_warehouse_generation
        or value["warehouse_generation"]
        != POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
        or artifact_warehouse_generation
        != POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
        or type(value.get("category_authority_generation")) is not str  # noqa: E721
        or not value["category_authority_generation"]
        or type(value.get("settled_items_v1")) is not list  # noqa: E721
        or not 1 <= len(value["settled_items_v1"]) <= 512
    ):
        return None
    items: list[dict[str, int]] = []
    for row in value["settled_items_v1"]:
        if (
            type(row) is not dict  # noqa: E721
            or set(row) != {"item_id", "quality", "primary_category"}
            or _exact_int(row.get("item_id"), minimum=1) is None
            or _exact_int(row.get("quality"), minimum=1) not in {1, 2, 3, 4, 5, 6}
            or _exact_int(row.get("primary_category"), minimum=1) is None
        ):
            return None
        items.append({key: int(row[key]) for key in sorted(row)})
    return {
        "schema": "settlement_category_fact_v1",
        "physical_map_id": physical_map_id,
        "model_map_id": model_map_id,
        "warehouse_generation": value["warehouse_generation"],
        "category_authority_generation": value["category_authority_generation"],
        "settled_items_v1": items,
    }


def _monitor_fact_receipt(
    artifact: Mapping[str, Any],
    *,
    row_generation: int,
    source_update_seq: int,
    snapshot_mode: str,
) -> dict[str, Any]:
    """Build the bounded writer payload; keep only type/count/hash projections."""
    action_promotion = _current_action_promotion(artifact)
    ui_contract = artifact.get("ui_contract") if isinstance(artifact.get("ui_contract"), Mapping) else {}
    panel = artifact.get("panel") if isinstance(artifact.get("panel"), Mapping) else {}
    quality_rows = artifact.get("category_grid_items")
    receipt = {
        "source_update_seq": int(source_update_seq),
        "generation": int(row_generation),
        "frame": {
            "arrival_type": "monitor_refresh",
            "snapshot_mode": str(snapshot_mode),
            "capture_count": _safe_int(artifact.get("packets")) or 0,
            "frame_count": _safe_int(artifact.get("frames")) or 0,
            "state_count": _safe_int(artifact.get("states")) or 0,
            "event_count_hash": _fact_digest({
                "action_rows": len(artifact.get("action_result_rows") or ()),
                "skill_rows": len(artifact.get("skill_reveal_rows") or ()),
                "public_rows": len(artifact.get("public_info_rows") or ()),
            }),
        },
        "rounds": {
            "declared_rounds_hash": _fact_digest([
                _safe_int(row.get("round"))
                for row in (artifact.get("action_result_rows") or ())
                if isinstance(row, Mapping)
            ]),
            "current_round": _safe_int(artifact.get("round")),
            "action_round": _safe_int(artifact.get("action_round")),
            "observed_round": _safe_int(artifact.get("observed_round")),
        },
        "parser": {"outcome": "accepted", "reason": "artifact_built"},
        "promotion": action_promotion,
        "engine": {
            "result_hash": _fact_digest(artifact.get("structured_ref_inputs") or {}),
            "quote_hash": _fact_digest(
                {
                    "balanced": artifact.get("balanced"),
                    "bids": artifact.get("v3_practical_bid_rows") or (),
                    "promotion_hash": action_promotion["action_set_hash"],
                }
            ),
        },
        "quality": {
            "item_count": len(quality_rows) if isinstance(quality_rows, list) else 0,
            "cell_triplets_hash": _fact_digest(quality_rows if isinstance(quality_rows, list) else ()),
            "display_reconciled": bool(panel.get("display_reconciled")),
        },
        "panel": {
            "projection_hash": _fact_digest(ui_contract),
            "wire_digest": _fact_digest(panel),
        },
    }
    settlement_category = _settlement_category_fact_receipt(artifact)
    if settlement_category is not None:
        receipt["settlement_category_fact_v1"] = settlement_category
    return receipt


def _should_skip_fast_bootstrap_snapshot(
    artifact: Mapping[str, Any],
    *,
    row_count: int,
    snapshot_mode: str,
    min_rows: int = FAST_BOOTSTRAP_MIN_ROWS,
) -> bool:
    if snapshot_mode != "fast" or row_count >= min_rows:
        return False
    return _artifact_phase(artifact) == "settled"

def _semantic_signature_from_payload(payload: str | bytes) -> tuple[Any, ...] | None:
    """Return a stable signature for game facts that affect live recommendations.

    Auction bid updates are intentionally excluded — they refresh display-only
    fields via ``refresh_latest_snapshot_bids`` without rerunning inference.
    """
    return inference_semantic_signature_from_payload(payload)


def _allow_response() -> dict[str, Any]:
    return dict(ALLOW_RESPONSE)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _local_player_cache_path(log_dir: Path) -> Path:
    return log_dir / "local_player_cache.json"


def _load_cached_local_player_id(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return _safe_int(payload.get("local_player_id"))


def _write_cached_local_player_id(path: Path, player_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        path,
        {
            "local_player_id": int(player_id),
            "updated_at": time.time(),
        },
    )


def _latest_local_player_id_from_events(events: Any) -> int | None:
    for state in reversed(getattr(events, "states", ()) or ()):
        player_id = _safe_int(getattr(state, "player_id", None))
        if player_id is not None:
            return player_id
    return None


def _is_local_ip(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        ip = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _infer_direction(
    *,
    src_ip: Any,
    src_port: Any,
    dst_ip: Any,
    dst_port: Any,
    server_ports: Sequence[int],
) -> str | None:
    src_port_int = _safe_int(src_port)
    dst_port_int = _safe_int(dst_port)
    server_port_set = {int(port) for port in server_ports}
    if dst_port_int in server_port_set:
        return "SEND"
    if src_port_int in server_port_set:
        return "REV"

    src_local = _is_local_ip(src_ip)
    dst_local = _is_local_ip(dst_ip)
    if src_local and not dst_local:
        return "SEND"
    if dst_local and not src_local:
        return "REV"
    return None


def _capture_time() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + (
        f".{int((now % 1) * 1000):03d}"
    )


def _process_name_matches(actual: Any, expected: str | None) -> bool:
    if expected in (None, "", "*"):
        return True
    return str(actual or "").casefold() == str(expected).casefold()


def _webhook_payload_to_row(
    payload: Mapping[str, Any],
    *,
    sort_id: int,
    process_name: str | None = "BidKing.exe",
    server_ports: Sequence[int] = (10000,),
) -> dict[str, Any] | None:
    """Convert one Fatbeans WebHook payload to a Fatbeans export row.

    Non-target processes, non-TCP packets, missing data, and ambiguous
    directions are filtered out. Invalid base64 is treated as malformed input.
    """
    if not isinstance(payload, Mapping):
        return None
    protocol = str(payload.get("Protocol") or "").lower()
    if protocol and protocol != "tcp":
        return None
    if not _process_name_matches(payload.get("ProcessName"), process_name):
        return None
    data = payload.get("Data") or ""
    if not isinstance(data, str) or not data:
        return None
    decoded = base64.b64decode(data, validate=True)
    direction = _infer_direction(
        src_ip=payload.get("SrcIP"),
        src_port=payload.get("SrcPort"),
        dst_ip=payload.get("DstIP"),
        dst_port=payload.get("DstPort"),
        server_ports=server_ports,
    )
    if direction is None:
        return None

    return {
        "SortID": int(sort_id),
        "Direct": direction,
        "Protocol": "Tcp",
        "SrcIP": payload.get("SrcIP"),
        "SrcPort": _safe_int(payload.get("SrcPort")) or 0,
        "DstIP": payload.get("DstIP"),
        "DstPort": _safe_int(payload.get("DstPort")) or 0,
        "CaptureTime": str(payload.get("CaptureTime") or _capture_time()),
        "Data": data,
        "DataLength": len(decoded),
        "PID": payload.get("PID"),
        "ProcessName": payload.get("ProcessName"),
        "Url": payload.get("Url"),
    }


def _refresh_latest_snapshot_heartbeat(
    log_dir: Path,
    *,
    semantic_signature: tuple[Any, ...] | None,
) -> bool:
    """Bump ``latest_snapshot`` age when capture grew but game facts did not change.

    During long reading/bidding only ``0x0077`` heartbeats may arrive: semantics
    stay identical (inference skipped) while overlay stale detection still keys
    off snapshot ``created_at``. Refresh only when the on-disk snapshot matches
    the parsed session so cross-game residue is never touched.
    """
    if semantic_signature is None:
        return False
    session_id = semantic_signature[0]
    if not session_id:
        return False
    path = log_dir / "latest_snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if str(payload.get("session_id") or "") != str(session_id):
        return False
    phase = _artifact_phase(payload)
    if phase not in ("reading", "bidding"):
        return False
    now = time.time()
    payload["created_at"] = now
    payload["snapshot_heartbeat_at"] = now
    _atomic_write_json(path, payload)
    return True


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        fh.write("\n")


def _append_webhook_error_log(
    log_dir: Path,
    *,
    raw_path: Path,
    packet_count: int,
    exc: Exception,
) -> None:
    _append_jsonl(
        log_dir / "monitor_errors.jsonl",
        {
            "ts": time.time(),
            "path": str(raw_path.resolve()),
            "name": raw_path.name,
            "packet_count": packet_count,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": "".join(
                traceback.format_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    limit=6,
                )
            ),
        },
    )


def _looks_like_partial_stream_error(exc: Exception) -> bool:
    text = str(exc)
    return "invalid frame length" in text or "incomplete length" in text


@dataclass(frozen=True)
class _MonitorLockInspection:
    state: str
    raw: bytes | None = None


def _same_lock_path(value: object, expected: Path) -> bool:
    if type(value) is not str or not value.strip():
        return False
    try:
        actual = os.path.normcase(os.path.normpath(str(Path(value).resolve())))
        target = os.path.normcase(os.path.normpath(str(expected.resolve())))
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return actual == target


def _strict_monitor_lock_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("monitor lock object is malformed")
        result[key] = value
    return result


def _inspect_monitor_lock(
    lock_path: Path,
    *,
    monitor_path: Path | None,
    log_dir: Path,
    package_generation_identity: str | None = None,
) -> _MonitorLockInspection:
    """Classify an existing lock without weakening uncertainty into staleness.

    Only a complete identity whose PID is proven absent is reclaimable. Corrupt,
    incomplete, foreign, or uninspectable live identities remain on disk so a
    cold start cannot delete another process's lock.
    """
    try:
        raw = lock_path.read_bytes()
    except FileNotFoundError:
        return _MonitorLockInspection("absent")
    except OSError:
        return _MonitorLockInspection("unknown")

    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_monitor_lock_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _MonitorLockInspection("unknown", raw)
    if type(data) is not dict:
        return _MonitorLockInspection("unknown", raw)

    required = ("pid", "process_started_at", "monitor_path", "log_dir")
    if any(field not in data for field in required):
        return _MonitorLockInspection("unknown", raw)
    pid_value = data.get("pid")
    started_value = data.get("process_started_at")
    if isinstance(pid_value, bool) or isinstance(started_value, bool):
        return _MonitorLockInspection("unknown", raw)
    try:
        pid = int(pid_value)
        process_started_at = float(started_value)
    except (TypeError, ValueError, OverflowError):
        return _MonitorLockInspection("unknown", raw)
    if (
        pid <= 0
        or not math.isfinite(process_started_at)
        or process_started_at <= 0
    ):
        return _MonitorLockInspection("unknown", raw)

    try:
        import psutil
    except Exception:  # noqa: BLE001 — missing inspector must fail closed
        return _MonitorLockInspection("unknown", raw)
    try:
        process = psutil.Process(pid)
        process_name = (process.name() or "").lower()
        actual_started_at = float(process.create_time())
    except psutil.NoSuchProcess:
        return _MonitorLockInspection("stale", raw)
    except Exception:  # noqa: BLE001 — identity uncertainty must fail closed
        return _MonitorLockInspection("unknown", raw)

    if re.fullmatch(r"python(?:w)?(?:[0-9.]*)?\.exe", process_name) is None:
        return _MonitorLockInspection("foreign", raw)
    if monitor_path is None:
        return _MonitorLockInspection("unknown", raw)
    if not _same_lock_path(data.get("monitor_path"), monitor_path):
        return _MonitorLockInspection("foreign", raw)
    if not _same_lock_path(data.get("log_dir"), log_dir):
        return _MonitorLockInspection("foreign", raw)
    if (
        not math.isfinite(actual_started_at)
        or actual_started_at <= 0
        or abs(actual_started_at - process_started_at) > 2.0
    ):
        return _MonitorLockInspection("foreign", raw)
    if package_generation_identity is not None:
        if not is_generation_digest(package_generation_identity):
            return _MonitorLockInspection("unknown", raw)
        actual_generation = data.get("package_generation_identity")
        if actual_generation is None:
            return _MonitorLockInspection("stale_package", raw)
        if not is_generation_digest(actual_generation):
            return _MonitorLockInspection("unknown", raw)
        if actual_generation != package_generation_identity:
            return _MonitorLockInspection("stale_package", raw)
    return _MonitorLockInspection("current", raw)


def _lock_is_stale(
    lock_path: Path,
    *,
    monitor_path: Path | None = None,
    log_dir: Path | None = None,
) -> bool:
    expected_log_dir = log_dir if log_dir is not None else lock_path.parent
    return (
        _inspect_monitor_lock(
            lock_path,
            monitor_path=monitor_path,
            log_dir=expected_log_dir,
        ).state
        == "stale"
    )


def _open_windows_lock_for_identity_delete(lock_path: Path) -> int:
    """Open one NTFS file identity while denying replacement/deletion."""
    if os.name != "nt":
        raise OSError("atomic monitor lock reclamation is Windows-only")

    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(lock_path),
        0x80000000 | 0x00010000,  # GENERIC_READ | DELETE
        0x00000001,  # FILE_SHARE_READ; deny write/delete/rename
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(error, "monitor lock disappeared")
        raise OSError(error, "monitor lock identity could not be opened")
    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


def _read_lock_handle(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _mark_lock_delete_on_close(fd: int) -> None:
    import ctypes
    import msvcrt

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_int)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    set_file_information.restype = ctypes.c_int
    disposition = _FileDispositionInfo(1)
    handle = msvcrt.get_osfhandle(fd)
    if not set_file_information(
        ctypes.c_void_p(handle),
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "monitor lock identity could not be deleted")


def _delete_stale_lock_by_identity(lock_path: Path, expected_raw: bytes) -> None:
    # Keep the explicit final path reread as a cheap early conflict check. The
    # handle-bound reread below is authoritative: another file may replace the
    # path immediately after this call returns.
    if lock_path.read_bytes() != expected_raw:
        raise RuntimeError("monitor lock changed during stale check")

    fd = _open_windows_lock_for_identity_delete(lock_path)
    delete_on_close = False
    try:
        if _read_lock_handle(fd) != expected_raw:
            raise RuntimeError("monitor lock changed during stale check")
        _mark_lock_delete_on_close(fd)
        delete_on_close = True
    finally:
        os.close(fd)
    if not delete_on_close or lock_path.exists():
        raise RuntimeError("monitor stale lock could not be reclaimed")


def _validated_package_generation_identity(
    expected: object,
    *,
    monitor_path: Path,
) -> str:
    if not is_generation_digest(expected):
        raise RuntimeError("monitor package generation identity is missing")
    try:
        resolved_monitor = monitor_path.resolve(strict=True)
        package_root = resolved_monitor.parents[1]
        actual = compute_package_generation_identity(
            package_root=package_root,
            monitor_path=resolved_monitor,
            runtime_path=Path(sys.executable),
        )
    except BaseException as exc:
        raise RuntimeError(
            "monitor package generation identity unavailable"
        ) from exc
    if actual.digest != expected:
        raise RuntimeError("monitor package generation identity mismatch")
    return actual.digest


def _acquire_lock(
    log_dir: Path,
    *,
    monitor_path: Path | None = None,
    package_generation_identity: str | None = None,
) -> Path:
    if package_generation_identity is not None:
        if not is_generation_digest(package_generation_identity):
            raise RuntimeError("monitor package generation identity is invalid")
    lock_path = log_dir / "monitor.lock"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        inspection = _inspect_monitor_lock(
            lock_path,
            monitor_path=monitor_path,
            log_dir=log_dir,
            package_generation_identity=package_generation_identity,
        )
        if inspection.state == "stale":
            try:
                if inspection.raw is None:
                    raise RuntimeError("monitor lock identity is incomplete")
                _delete_stale_lock_by_identity(lock_path, inspection.raw)
            except FileNotFoundError:
                pass
            except OSError as unlink_exc:
                raise RuntimeError("monitor stale lock could not be reclaimed") from unlink_exc
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as retry_exc:
                raise RuntimeError(
                    "monitor lock changed while reclaiming a stale owner"
                ) from retry_exc
        else:
            raise RuntimeError(
                f"monitor lock is not safely reclaimable (state={inspection.state})"
            ) from exc
    process_started_at: float | None = None
    try:
        import psutil

        candidate = float(psutil.Process(os.getpid()).create_time())
        if candidate > 0:
            process_started_at = candidate
    except Exception:  # noqa: BLE001 — missing identity must remain fail-closed
        process_started_at = None

    payload_data: dict[str, object] = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "kind": "fatbeans_webhook",
    }
    if monitor_path is not None:
        # The launcher may need to fall back to monitor.lock when CIM is
        # unavailable. Bind that fallback to this exact package entrypoint and
        # log directory; a live Python PID alone is never sufficient identity.
        payload_data["monitor_path"] = str(monitor_path.resolve())
        payload_data["log_dir"] = str(log_dir.resolve())
        if process_started_at is not None:
            payload_data["process_started_at"] = process_started_at
        if package_generation_identity is not None:
            payload_data["package_generation_identity"] = package_generation_identity
    payload = json.dumps(
        payload_data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
        fh.write(b"\n")

    def _cleanup() -> None:
        try:
            lock_path.unlink()
        except OSError:
            pass

    atexit.register(_cleanup)
    return lock_path


def _append_raw_rows_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")


def _safe_file_token(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or fallback


def _capture_start_token(rows: Sequence[Mapping[str, Any]]) -> str:
    capture_time = str((rows[0] if rows else {}).get("CaptureTime") or "")
    if len(capture_time) >= 19:
        return f"{capture_time[:10]}_{capture_time[11:19].replace(':', '')}"
    return time.strftime("%Y-%m-%d_%H%M%S", time.localtime())


def _latest_row_session_id(rows: Sequence[Mapping[str, Any]]) -> str:
    for row in reversed(rows):
        session_id = row.get("SessionID")
        if session_id:
            return str(session_id)
    return ""


def _archive_reset_rows(
    *,
    raw_dir: Path,
    file_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> Path | None:
    if not rows:
        return None
    archive_dir = raw_dir / "archive" / "reset"
    stem = Path(file_name).stem
    start = _capture_start_token(rows)
    session = _safe_file_token(_latest_row_session_id(rows))
    name = f"{stem}_{start}_{session}_reset.json"
    destination = archive_dir / name
    index = 2
    while destination.exists():
        destination = archive_dir / f"{stem}_{start}_{session}_reset_{index}.json"
        index += 1
    _atomic_write_json(destination, [dict(row) for row in rows])
    return destination


@dataclass(frozen=True)
class WebhookMonitorConfig:
    log_dir: Path
    raw_dir: Path
    process_name: str | None
    server_ports: tuple[int, ...]
    n_trials: int
    roi_trials: int
    shadow_trials: int | None
    full_shadow_trials: int
    run_debug_shadows: bool
    seed: int
    formal_mode: str | None = "v3_practical"
    debounce_seconds: float = 1.0
    min_inference_interval_seconds: float = 2.0
    fast_n_trials: int | None = 20
    file_name: str = "fatbeans_webhook_live.json"
    source_name: str = "fatbeans_webhook"
    packet_count_key: str = "webhook_packets"
    archive_round_snapshots: bool = True
    full_upgrade_when_idle: bool = True
    full_upgrade_idle_seconds: float = 1.5
    full_upgrade_max_seconds: float = 6.0


class FatbeansWebhookMonitor:
    def __init__(self, *, config: WebhookMonitorConfig, tables: Any) -> None:
        self.config = config
        self.tables = tables
        self._fact_receipts = FactReceiptWriter(self.config.log_dir)
        try:
            self._rows: list[dict[str, Any]] = []
            self._next_sort_id = 1
            self._last_packet_at = 0.0
            self._last_process_request_at = 0.0
            self._last_inference_at = 0.0
            self._last_processed_count = 0
            self._last_full_processed_count = 0
            self._last_requested_count = 0
            self._last_inferred_max_sort = 0
            self._last_semantic_signature: tuple[Any, ...] | None = None
            self._last_semantic_probe_count = 0
            self._last_probed_semantic_count = 0
            self._last_probed_semantic_signature: tuple[Any, ...] | None = None
            self._last_announced_semantic_signature: tuple[Any, ...] | None = None
            # Unclaimed semantic batch: generation, session, first recognition.
            # Subsequent facts merge until the single worker takes its rows.
            self._pending_semantic_anchor: tuple[int, Any, float] | None = None
            self._row_generation = 0
            self._latest_scheduled_timing: dict[str, float] = {}
            self._compute_trace_seq = 0
            self._snapshot_publish_seq = 0
            self._compute_status_write_lock = threading.Lock()
            self._last_compute_status_trace_seq = -1
            self._active_compute_trace: dict[str, Any] | None = None
            self._active_compute_signature: tuple[Any, ...] | None = None
            self._active_compute_generation: int | None = None
            self._last_bid_signature: tuple[tuple[str, int], ...] = ()
            self._raw_persisted_count = 0
            self._last_partial_error = ""
            self._round_archive_session: str | None = None
            self._last_fast_seconds: float | None = None
            self._full_job = None
            self._full_attempted_epoch = None
            self._fast_epoch = 0
            self._full_status = {"stage": "idle"}
            # 诊断(纯附加): 会话重置时刻 + 本次重置是否已 dump 过卡死帧。用于"reset 后帧累积却一直
            # 不推理"(局间买卖流量 schedule_process=False / 误判会话重置删快照后无真状态帧)的取证。
            self._last_reset_at = 0.0
            self._stuck_dump_done = True  # start() 的首次 reset 前不 dump(无帧)
            self._known_local_player_id = _load_cached_local_player_id(
                _local_player_cache_path(self.config.log_dir)
            )
            self._received = 0
            self._accepted = 0
            self._filtered = 0
            self._lock = threading.RLock()
            self._queue: queue.Queue[object] = queue.Queue()
            self._stop = threading.Event()
            self._shutdown_wake = threading.Event()
            self._shutdown_complete = threading.Event()
            self._shutdown_stage = "running"
            self._shutdown_pending_reported = False
            self._worker = threading.Thread(
                target=self._run_worker,
                name="fatbeans-webhook-monitor",
                # Capture callers may return immediately after bounded stop().
                # Keep this original owner alive until its child is reaped and
                # the in-memory fast/settlement tail has finished.
                daemon=False,
            )
        except BaseException:
            try:
                self._fact_receipts.close()
            except BaseException:
                # Construction owns the writer, but the original constructor
                # exception remains the authoritative product failure.
                pass
            raise

    @property
    def raw_path(self) -> Path:
        return self.config.raw_dir / self.config.file_name

    @property
    def raw_jsonl_path(self) -> Path:
        stem = Path(self.config.file_name).stem
        return self.config.raw_dir / f"{stem}.jsonl"

    @property
    def compute_status_path(self) -> Path:
        return self.config.log_dir / COMPUTE_STATUS_FILENAME

    def start(self) -> None:
        self.reset_rows()
        self._worker.start()

    def request_stop(self) -> None:
        """Stop new dispatch immediately; finalization stays with the worker."""
        self._stop.set()
        self._queue.put_nowait(object())
        self._shutdown_wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        """Request shutdown; False means the original worker still owns cleanup."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        # Publish cancellation before acquiring the state lock: a slow snapshot
        # must not hold capture teardown behind its own finalization.
        self.request_stop()
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            self._pending_semantic_anchor = None
            if self._worker.ident is None:
                # Construction/early caller failure still has one original
                # worker; do not invent a parallel finalizer for repeat stop.
                self._worker.start()
        finally:
            self._lock.release()
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=max(0.0, deadline - time.monotonic()))
        complete = self._shutdown_complete.is_set() and not self._worker.is_alive()
        if not complete:
            if not self._shutdown_pending_reported:
                self._shutdown_pending_reported = True
                print("[stop-pending] monitor cleanup remains owned by its worker; "
                      "recovery continues, call stop again to wait", file=sys.stderr, flush=True)
        return complete

    def accept_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        frame_received_monotonic = time.monotonic()
        with self._lock:
            if self._stop.is_set():
                return {
                    "accepted": False,
                    "reason": "stopped",
                    "received": self._received,
                    "accepted_count": self._accepted,
                    "filtered_count": self._filtered,
                }
            self._received += 1
            try:
                row = _webhook_payload_to_row(
                    payload,
                    sort_id=self._next_sort_id,
                    process_name=self.config.process_name,
                    server_ports=self.config.server_ports,
                )
            except Exception as exc:  # noqa: BLE001 - external webhook boundary
                self._filtered += 1
                return {
                    "accepted": False,
                    "reason": f"malformed:{type(exc).__name__}:{exc}",
                    "received": self._received,
                    "accepted_count": self._accepted,
                    "filtered_count": self._filtered,
                }
            if row is None:
                self._filtered += 1
                return {
                    "accepted": False,
                    "reason": "filtered",
                    "received": self._received,
                    "accepted_count": self._accepted,
                    "filtered_count": self._filtered,
                }
            self._rows.append(row)
            self._next_sort_id += 1
            self._accepted += 1
            self._last_packet_at = time.monotonic()
            self._last_process_request_at = self._last_packet_at
            self._last_requested_count = len(self._rows)
            self._latest_scheduled_timing = {
                "wss_frame_received": frame_received_monotonic,
                "parser_recognized": self._last_packet_at,
                "monitor_state_committed": self._last_packet_at,
            }
            accepted_count = self._accepted
            received = self._received
            filtered = self._filtered
        # Semantic acknowledgement must not wait behind the single inference
        # worker.  In particular, an idle full-upgrade can spend seconds inside
        # build_monitor_artifact_from_payload while capture continues on this
        # thread.  Probe after the row commit (and outside the lock) so a new,
        # parser-confirmed signature reaches the UI before that old compute
        # returns.  This remains a lightweight parser pass only: it neither runs
        # the numeric engine nor bypasses the existing debounce/rate limit.
        self._probe_pending_semantic()
        self._queue.put_nowait(object())
        return {
            "accepted": True,
            "reason": "accepted",
            "received": received,
            "accepted_count": accepted_count,
            "filtered_count": filtered,
        }

    def accept_row(
        self,
        row: Mapping[str, Any],
        *,
        schedule_process: bool = True,
        frame_received_monotonic: float | None = None,
        parser_recognized_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Accept an already-normalized Fatbeans export row.

        This is used by source adapters that do their own packet capture but
        still want to reuse the same debounce, artifact, and log writer path.
        Rows that only preserve transport context can skip scheduling an
        expensive inference run while remaining available to the next state.
        """
        with self._lock:
            if self._stop.is_set():
                return {
                    "accepted": False,
                    "reason": "stopped",
                    "received": self._received,
                    "accepted_count": self._accepted,
                    "filtered_count": self._filtered,
                }
            self._received += 1
            normalized = dict(row)
            normalized["SortID"] = int(normalized.get("SortID") or self._next_sort_id)
            self._rows.append(normalized)
            self._next_sort_id = max(self._next_sort_id, normalized["SortID"] + 1)
            self._accepted += 1
            self._last_packet_at = time.monotonic()
            if schedule_process:
                self._last_process_request_at = self._last_packet_at
                self._last_requested_count = len(self._rows)
                received_at = (
                    float(frame_received_monotonic)
                    if isinstance(frame_received_monotonic, (int, float))
                    else self._last_packet_at
                )
                parsed_at = (
                    float(parser_recognized_monotonic)
                    if isinstance(parser_recognized_monotonic, (int, float))
                    else self._last_packet_at
                )
                self._latest_scheduled_timing = {
                    "wss_frame_received": received_at,
                    "parser_recognized": parsed_at,
                    "monitor_state_committed": self._last_packet_at,
                }
            accepted_count = self._accepted
            received = self._received
            filtered = self._filtered
        if schedule_process:
            # See accept_payload: capture/parser feedback must remain responsive
            # even while the monitor's one numeric worker is busy.  Bid-only and
            # transport rows keep schedule_process=False and never probe here.
            self._probe_pending_semantic()
            self._queue.put_nowait(object())
        return {
            "accepted": True,
            "reason": "accepted",
            "received": received,
            "accepted_count": accepted_count,
            "filtered_count": filtered,
        }

    def reset_rows(self) -> None:
        """Clear accumulated raw rows before a new live session starts."""

        def _remove_if_exists(path: Path) -> None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except PermissionError:
                pass

        with self._lock:
            try:
                archived_path = _archive_reset_rows(
                    raw_dir=self.config.raw_dir,
                    file_name=self.config.file_name,
                    rows=self._rows,
                )
                if archived_path is not None:
                    print(
                        f"[archive] {self.config.source_name} reset raw rows -> "
                        f"{archived_path}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001 - reset must not block live capture
                print(
                    f"[warn] {self.config.source_name} reset archive failed: {exc}",
                    flush=True,
                )
            self._rows = []
            self._next_sort_id = 1
            self._last_packet_at = 0.0
            self._last_process_request_at = 0.0
            self._last_processed_count = 0
            self._last_full_processed_count = 0
            self._last_requested_count = 0
            self._last_inferred_max_sort = 0
            self._last_semantic_signature = None
            self._last_semantic_probe_count = 0
            self._last_probed_semantic_count = 0
            self._last_probed_semantic_signature = None
            self._last_announced_semantic_signature = None
            self._pending_semantic_anchor = None
            self._row_generation += 1
            self._latest_scheduled_timing = {}
            self._active_compute_trace = None
            self._active_compute_signature = None
            self._active_compute_generation = None
            self._last_bid_signature = ()
            self._raw_persisted_count = 0
            self._last_partial_error = ""
            self._last_fast_seconds = None
            # Clear the inference rate-limit clock too. A new live session is a
            # fresh context that must get its first inference (R1) promptly; if
            # an inference fired just before the session boundary (e.g. a between
            # -games lobby/menu frame that the engine returns as
            # ``not_structured_hero``), leaving ``_last_inference_at`` set would
            # let ``min_inference_interval_seconds`` throttle the new session's
            # R1 inference, so the first emitted snapshot lands on R2 and R1
            # never shows ("第二轮才显示"). Resetting it bounds the throttle to
            # within a session, never across a new-game boundary.
            self._last_inference_at = 0.0
            _remove_if_exists(self.raw_path)
            _remove_if_exists(self.raw_jsonl_path)
            # 【0.2.4-hotfix3 回归修复】此处【不再删除】latest_snapshot.json。
            # 背景: 0868536 曾在此删快照以修"跨对局延迟"(上一局结算挡住新局 R1 UI), 但它默认
            # "会话推进 == 真的有新对局跟上"。局间在主界面买/卖会让 GameFrameGate 误判会话推进 →
            # 删了快照却【没有真状态帧跟上】(买卖流量是 SEND/非状态, 不触发推理)→ UI 空白卡死、
            # 需重启才恢复(群友反馈的"跨对局不加载")。
            # 但 0868536 同时加的 session-ahead 逻辑(ahmad_live_panel_server.capture_session_ahead_of_snapshot
            # + overlay _needs_stale_refresh)【本身已足够】: 抓包会话领先 on-disk 快照时, summarize 返回
            # stale_snapshot/session_ahead → overlay 显示"等待新对局"standby, 不再显示上一局结算。
            # 离线用 2410 诊断包验证: 快照【保留】时 summarize 仍= stale_snapshot/session_ahead。
            # 故删快照是多余的、且是本卡死的回归源: 保留旧快照做兜底(真新局的首帧快照会原子覆盖它),
            # 既修卡死又不回归跨对局延迟。误判会话推进的根治(GameFrameGate)另行处理。
            # 诊断: 记录本次重置时刻并重新武装卡死取证(下面 _maybe_dump_stuck_session 用)。
            self._last_reset_at = time.monotonic()
            self._stuck_dump_done = False
            # Publish reset before releasing the generation lock.  Otherwise a
            # producer could commit the first new-session row and publish its
            # pending trace in the gap, only for this older reset write (same
            # local sequence boundary) to clear it again.
            self._write_compute_status(
                event="reset",
                compute_pending=False,
                trace_seq=self._compute_trace_seq,
                timestamps={},
                counters={"accepted_rows": self._accepted, "scheduled_rows": 0},
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "received": self._received,
                "accepted": self._accepted,
                "filtered": self._filtered,
                "rows": len(self._rows),
                "last_processed_count": self._last_processed_count,
                "last_full_processed_count": self._last_full_processed_count,
                "last_requested_count": self._last_requested_count,
                "raw_path": str(self.raw_path.resolve()),
                "idle_full": dict(self._full_status),
                "shutdown": {"stage": self._shutdown_stage,
                             "complete": self._shutdown_complete.is_set()},
            }

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                self._queue.get(timeout=0.01 if self._full_job is not None else 0.2)
            except queue.Empty:
                pass
            if self._stop.is_set():
                break
            if self._full_job is not None:
                self._supervise_full_job()
            if self._full_job is not None:
                continue
            self._process_if_due()
            self._maybe_run_idle_full_upgrade()
            self._maybe_dump_stuck_session()
        self._shutdown_stage = "reaping"
        while self._full_job is not None:
            self._supervise_full_job()
            if self._full_job is not None:
                self._shutdown_stage = "reap_pending"
                # Retain responsibility after a transient OS/reap failure.
                # Repeated stop may wake us, but never accelerates retries into
                # a busy loop while the owned process cannot be confirmed dead.
                retry_at = time.monotonic() + 0.2
                while time.monotonic() < retry_at:
                    self._shutdown_wake.wait(timeout=max(0.0, retry_at - time.monotonic()))
                    self._shutdown_wake.clear()
        # This sole worker finalizes once, after actual child exit. Capture
        # resources can already be closed: this tail uses retained rows/tables.
        for stage, finish in (
            ("flushing_fast", lambda: self._process_snapshot(force=False)),
            ("closing_writer", self._fact_receipts.close),
        ):
            while True:
                self._shutdown_stage = stage
                try:
                    finish()
                    if stage == "closing_writer" and self._fact_receipts._worker.is_alive():
                        raise RuntimeError("fact_writer_pending")
                    break
                except Exception:
                    # A denied/failed write cannot discard the retained tail or
                    # masquerade as stopped. The original owner retries only
                    # this unfinished stage, without re-running a finished one.
                    self._shutdown_stage = stage + "_failed"
                    self._shutdown_wake.wait(timeout=0.2)
                    self._shutdown_wake.clear()
        self._finish_compute_trace("stopped")
        with self._lock:
            self._shutdown_stage = "stopped"
            self._shutdown_complete.set()

    def _maybe_dump_stuck_session(self) -> None:
        """诊断(纯附加, 不改任何推理/估值): 会话重置后, 若帧持续累积却一直没触发过推理
        (本案卡死态: 局间主界面买/卖流量 schedule_process=False / 误判会话重置删了快照后新
        "会话"无真状态帧), 把内存里累积的原始帧 dump 到 raw/archive/stuck/, 便于导出诊断包后
        回放坐实"卡在上一局结算"的根因。每次 reset 只 dump 一次, 不刷屏。"""
        stuck_after_seconds = 8.0
        if self._stuck_dump_done:
            return
        now = time.monotonic()
        with self._lock:
            reset_at = self._last_reset_at
            processed = self._last_processed_count
            rows = list(self._rows)
        # reset 后已产出过快照 = 正常(不卡); 帧太少 / 还没到判定窗口 = 再等。
        if reset_at <= 0.0 or processed > 0 or len(rows) < 3 or now - reset_at < stuck_after_seconds:
            return
        self._stuck_dump_done = True  # 先置位, 失败也不重试刷屏
        try:
            archive_dir = self.config.raw_dir / "archive" / "stuck"
            archive_dir.mkdir(parents=True, exist_ok=True)
            path = archive_dir / f"stuck_{int(time.time())}.json"
            _atomic_write_json(path, rows)
            msgids = [str(r.get("MessageID")) for r in rows[:24]]
            dirs = [str(r.get("Direct")) for r in rows[:24]]
            print(
                f"[stuck] {self.config.source_name} post-reset no inference "
                f"rows={len(rows)} secs={now - reset_at:.0f} msgids={msgids} dirs={dirs} "
                f"-> {path}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - 诊断绝不拖垮监控
            print(f"[warn] {self.config.source_name} stuck dump failed: {exc}", flush=True)

    def _maybe_run_idle_full_upgrade(self) -> None:
        """Reserve a supervised full upgrade of the current idle fast epoch."""
        if not self.config.full_upgrade_when_idle:
            return
        if self.config.fast_n_trials is None or self.config.fast_n_trials <= 0:
            return  # already running full inference on every update
        with self._lock:
            if self._stop.is_set() or self._full_job is not None:
                return
            if self._pending_semantic_anchor is not None:
                return
            if self._full_attempted_epoch == (self._row_generation, self._fast_epoch):
                return
            rows_count = len(self._rows)
            last_processed = self._last_processed_count
            last_full = self._last_full_processed_count
            last_packet_at = self._last_packet_at
            last_fast_seconds = self._last_fast_seconds
        if rows_count == 0 or rows_count != last_processed:
            return  # state still changing or not fast-processed yet
        if last_full >= last_processed:
            return  # this state already upgraded (or marked too heavy)
        if time.monotonic() - last_packet_at < self.config.full_upgrade_idle_seconds:
            return  # packets still arriving; stay on the fast path
        if last_fast_seconds is not None:
            ratio = max(
                1.0,
                float(self.config.n_trials) / float(max(1, self.config.fast_n_trials)),
            )
            if last_fast_seconds * ratio > self.config.full_upgrade_max_seconds:
                with self._lock:
                    if self._last_processed_count == last_processed:
                        self._last_full_processed_count = last_processed
                print(
                    f"[skip] {self.config.source_name} full upgrade too heavy "
                    f"(fast={last_fast_seconds:.2f}s x{ratio:.0f}); keeping fast",
                    flush=True,
                )
                return
        self._dispatch_idle_full()

    def _full_owner_current_locked(self, job) -> bool:
        return (
            self._full_job is job
            and not self._stop.is_set()
            and job["generation"] == self._row_generation
            and job["epoch"] == self._fast_epoch
            and job["row_count"] == len(self._rows)
            and job["signature"] == self._last_semantic_signature
            and job["trace"] == self._compute_trace_seq
            and job["tables_object"] is self.tables
            and self._pending_semantic_anchor is None
            and self._snapshot_owner_current_locked(job["generation"], job["signature"])
        )

    def _dispatch_idle_full(self) -> None:
        """Atomic reservation; encoding advances in small supervised slices."""
        with self._lock:
            if (self._stop.is_set() or self._full_job is not None
                    or self._pending_semantic_anchor is not None
                    or not self._rows or len(self._rows) != self._last_processed_count
                    or self._last_full_processed_count >= self._last_processed_count
                    or time.monotonic() - self._last_packet_at < self.config.full_upgrade_idle_seconds
                    or self._last_semantic_signature is None):
                return
            epoch = (self._row_generation, self._fast_epoch)
            if self._full_attempted_epoch == epoch:
                return
            self._full_attempted_epoch = epoch
            started = time.monotonic()
            job = {"id": uuid.uuid4().hex, "generation": self._row_generation,
                   "epoch": self._fast_epoch, "row_count": len(self._rows),
                   "signature": self._last_semantic_signature, "trace": self._compute_trace_seq,
                   "tables_object": self.tables,
                   "rows": list(self._rows), "hint": self._known_local_player_id,
                   "started": started, "deadline": started + self.config.full_upgrade_max_seconds,
                   "phases": {"reserved": started},
                   "process": None, "channel": None, "phase": "preparing", "offset": 8}
            self._full_job = job
        try:
            from bidking_lab.secure import enforce
            if enforce.current() is not None and not enforce.should_run_full():
                self._end_full_job(job, "full_gated")
                return
            mode, trials, roi, shadow, debug = self._snapshot_mode(force=True)
            parameters = dict(file=self.raw_path.name, n_trials=trials, roi_trials=roi,
                              shadow_trials=shadow, run_debug_shadows=debug,
                              seed=self.config.seed, formal_mode=self.config.formal_mode,
                              local_player_id_hint=job["hint"])
            tables = _full_tables_snapshot(self.tables, job["signature"][0])
            job["version"] = tables["session_identity"][1]
            job["identity"] = (job["id"], job["generation"], job["signature"], job["trace"],
                               job["epoch"], tables["fields"]["warehouse_package_generation"], parameters)
            # Rows are serialized incrementally too: no large json.dumps/pickle
            # on the parent's supervision path. Child joins them to a payload.
            request = {"identity": job["identity"], "rows": job["rows"],
                       "parameters": parameters, "tables": tables}
            job["encoder"] = iter(_full_wire_tokens(request))
            job["digest"] = hashlib.sha256()
            job["channel_name"] = "bidking-full-" + job["id"]
            job["channel"] = mmap.mmap(-1, _FULL_WIRE_CAPACITY, tagname=job["channel_name"])
            self._full_status = {"job": job["id"], "stage": "preparing"}
        except Exception:
            self._end_full_job(job, "full_prepare_failed")

    def _start_full_process(self, job):
        executable = Path(sys.executable)
        if getattr(sys, "frozen", False):
            executable = ROOT / "runtime" / "python" / "python.exe"
        if not executable.is_file():
            raise ValueError("full_python_missing")
        # Explicit -c prevents spawn from replaying source, embedded or native
        # WSS main. Only the protected computation module is imported.
        code = ("import sys;sys.path[:0]=" + repr([str(p) for p in sys.path]) + ";"
                "from bidking_lab.live.monitor import _full_artifact_child;"
                "_full_artifact_child(sys.argv[1])")
        if len(code) > 8192:
            raise ValueError("full_start_descriptor_size")
        return subprocess.Popen([str(executable), "-B", "-c", code, job["channel_name"]],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _reap_full_process(self, job) -> bool:
        process = job["process"]
        if process is None:
            return True
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.2)
            return process.poll() is not None
        except Exception:
            try:
                return process.poll() is not None
            except Exception:
                return False

    def _end_full_job(self, job, reason: str) -> bool:
        job["invalid"] = reason
        cleanup_started = time.monotonic()
        dead = self._reap_full_process(job)
        job["phases"]["cleanup_completed"] = time.monotonic()
        process = job["process"]
        self._full_status = {"job": job["id"], "stage": reason if dead else "full_reap_failed",
                             "elapsed": time.monotonic() - job["started"],
                             "pid": getattr(process, "pid", None),
                             "exitcode": getattr(process, "returncode", None), "reaped": dead,
                             "cleanup_seconds": time.monotonic() - cleanup_started,
                             "phase_seconds": {key: value - job["started"] for key, value in job["phases"].items()},
                             "input_bytes": job["offset"] - 8}
        if dead:
            if job["channel"] is not None:
                job["channel"].close()
            if self._full_job is job:
                self._full_job = None
        return dead

    def _supervise_full_job(self) -> None:
        job = self._full_job
        if job is None:
            return
        with self._lock:
            current = self._full_owner_current_locked(job)
        if job.get("invalid") or not current or time.monotonic() >= job["deadline"]:
            self._end_full_job(job, job.get("invalid") or
                               ("full_cancelled" if not current else "full_timeout"))
            return
        try:
            slice_end = time.monotonic() + 0.02
            if job["phase"] == "preparing":
                for _ in range(8192):
                    if time.monotonic() >= min(slice_end, job["deadline"]):
                        return
                    token = next(job["encoder"], None)
                    if token is None:
                        job["channel"][:8] = struct.pack("<Q", job["offset"] - 8)
                        job["input_digest"] = job["digest"].hexdigest()
                        job["phases"]["preparation_completed"] = time.monotonic()
                        try:
                            job["process"] = self._start_full_process(job)
                        except Exception:
                            self._end_full_job(job, "full_start_failed")
                            return
                        job["phases"]["start_returned"] = time.monotonic()
                        job["phase"] = "running"
                        self._full_status = {"job": job["id"], "stage": "running",
                                             "pid": job["process"].pid}
                        return
                    end = job["offset"] + len(token)
                    if end > _FULL_WIRE_CAPACITY:
                        self._end_full_job(job, "full_input_size")
                        return
                    job["channel"][job["offset"]:end] = token
                    job["digest"].update(token)
                    job["offset"] = end
            elif job["phase"] == "running":
                code = job["process"].poll()
                if code is None:
                    return
                job["phases"]["child_exited"] = time.monotonic()
                if code != 0:
                    self._end_full_job(job, "full_child_failed")
                    return
                size = struct.unpack("<Q", job["channel"][:8])[0]
                job["decoder"] = iter(_full_wire_decode(_full_mmap_tokens(job["channel"], size)))
                job["phase"] = "receiving"
            elif job["phase"] == "receiving":
                for _ in range(8192):
                    if time.monotonic() >= min(slice_end, job["deadline"]):
                        return
                    response = next(job["decoder"])
                    if response is not None:
                        if (type(response) is not dict or response.get("identity") != job["identity"]
                                or response.get("input_digest") != job["input_digest"]):
                            self._end_full_job(job, "full_result_invalid")
                            return
                        if response.get("error") in ("full_compute_error", "full_partial", "full_input_error"):
                            self._end_full_job(job, response["error"])
                            return
                        if (type(response.get("artifact")) is not dict
                                or response["artifact"].get("session_id") != job["signature"][0]):
                            self._end_full_job(job, "full_result_invalid")
                            return
                        job["artifact"] = response["artifact"]
                        job["phases"]["receive_completed"] = time.monotonic()
                        self._commit_full_job(job)
                        return
        except Exception:
            self._end_full_job(job, "full_channel_failed")

    def _commit_full_job(self, job) -> None:
        with self._lock:
            if self._full_job is not job:
                return
            if (not self._full_owner_current_locked(job)
                    or time.monotonic() >= job["deadline"]
                    or _fresh_session_game_version(self.tables, job["signature"][0]) != job["version"]):
                self._end_full_job(job, "full_stale")
                return
            from bidking_lab.secure import enforce
            if enforce.current() is not None and not enforce.should_run_full():
                self._end_full_job(job, "full_gated")
                return
            if not self._reap_full_process(job):
                self._end_full_job(job, "full_reap_failed")
                return
            if time.monotonic() >= job["deadline"]:
                self._end_full_job(job, "full_timeout")
                return
            # Only this success owner enters the original complete publication
            # tail. RLock retains ownership over raw/cache/bid/logs/processed.
            job["phases"]["commit_started"] = time.monotonic()
            completed = self._process_snapshot(force=True, _full_result=job["artifact"])
            job["phases"]["commit_completed"] = time.monotonic()
            self._end_full_job(job, "full_completed" if completed else "full_commit_failed")

    def _process_if_due(self) -> None:
        if self._full_job is not None:
            return
        with self._lock:
            needs_probe = self._last_requested_count > self._last_semantic_probe_count
        if needs_probe:
            self._probe_pending_semantic()
        # The parser runs outside the lock; reset or another producer may have
        # changed the generation and its clocks while it was running.
        with self._lock:
            if self._stop.is_set():
                return
            requested_count = self._last_requested_count
            last_process_request_at = self._last_process_request_at
            last_inference_at = self._last_inference_at
            last_packet_at = self._last_packet_at
            row_count = len(self._rows)
            last_processed = self._last_processed_count
            pending_anchor = self._pending_semantic_anchor
            schedule_owner = (self._row_generation, pending_anchor)
        pending_rows = (
            _monitor_heartbeat_refresh_enabled()
            and last_processed > 0
            and row_count > last_processed
        )
        if requested_count <= last_processed and not pending_rows:
            return
        now = time.monotonic()
        if pending_anchor is not None:
            debounce_anchor = pending_anchor[2]
        elif requested_count > last_processed:
            debounce_anchor = last_process_request_at
        else:
            debounce_anchor = last_packet_at
        if now - debounce_anchor < self.config.debounce_seconds:
            return
        if now - last_inference_at < self.config.min_inference_interval_seconds:
            return
        self._process_snapshot(force=False, expected_schedule_owner=schedule_owner)

    @staticmethod
    def _safe_monotonic_fields(values: Mapping[str, Any]) -> dict[str, float]:
        safe: dict[str, float] = {}
        for key in (
            "wss_frame_received",
            "parser_recognized",
            "monitor_state_committed",
            "semantic_recognized",
            "monitor_inference_started",
            "monitor_inference_completed",
            "snapshot_published",
        ):
            value = values.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                safe[key] = round(float(value), 6)
        return safe

    def _write_compute_status(
        self,
        *,
        event: str,
        compute_pending: bool,
        trace_seq: int,
        timestamps: Mapping[str, Any],
        counters: Mapping[str, Any],
        terminal_reason: str = "",
    ) -> None:
        safe_counters: dict[str, int] = {}
        for key in (
            "accepted_rows",
            "scheduled_rows",
            "semantic_seq",
            "snapshot_seq",
        ):
            value = counters.get(key)
            if type(value) is int and value >= 0:  # noqa: E721 - reject bool counters
                safe_counters[key] = value
        safe_trace_seq = max(0, int(trace_seq))
        payload = {
            "schema_version": COMPUTE_STATUS_SCHEMA_VERSION,
            "event": str(event),
            "compute_pending": bool(compute_pending),
            "trace_seq": safe_trace_seq,
            "timestamps_monotonic": self._safe_monotonic_fields(timestamps),
            "counters": safe_counters,
        }
        if terminal_reason in _COMPUTE_TERMINAL_REASONS:
            payload["terminal_reason"] = terminal_reason
        with self._compute_status_write_lock:
            if safe_trace_seq < self._last_compute_status_trace_seq:
                return
            try:
                _atomic_write_json(self.compute_status_path, payload)
            except Exception:
                # The sidecar is diagnostic/UI feedback only. It must never interrupt
                # capture, semantic parsing, snapshot publication, or inference.
                return
            self._last_compute_status_trace_seq = safe_trace_seq

    def _commit_pending_status_if_current(
        self,
        *,
        trace_seq: int,
        generation: int,
        semantic_signature: tuple[Any, ...],
        timestamps: Mapping[str, Any],
        counters: Mapping[str, Any],
    ) -> bool:
        """Publish a pending trace only while its lifecycle owner is still current.

        Semantic parsing intentionally happens outside the monitor worker, so reset
        or stop can win after the pending payload is prepared.  Revalidate under
        the state lock and keep that lock through the atomic sidecar write; this
        makes the final commit, rather than only payload construction, generation-
        and lifecycle-aware.  Equal trace numbers remain legal for the normal
        pending -> snapshot/terminal progression.
        """

        with self._lock:
            if (
                self._stop.is_set()
                or generation != self._row_generation
                or trace_seq != self._compute_trace_seq
                or semantic_signature != self._active_compute_signature
                or generation != self._active_compute_generation
            ):
                return False
            self._write_compute_status(
                event="semantic_pending",
                compute_pending=True,
                trace_seq=trace_seq,
                timestamps=timestamps,
                counters=counters,
            )
            return True

    def _adopt_recovered_semantic_if_current(
        self,
        *,
        row_count: int,
        generation: int,
        semantic_signature: tuple[Any, ...],
        pending_recognized_at: float | None = None,
        expected_trace_seq: int | None = None,
    ) -> bool:
        """Claim a worker-recovered signature after the lightweight probe failed.

        A probe exception is intentionally non-fatal, but its cached ``None`` must
        not leave an older trace owning the generation.  The numeric worker may
        retry the parser; when that succeeds, bind the recovered current rows to a
        fresh trace before inference so latest-wins publication remains enforced.
        """
        with self._lock:
            if (
                self._stop.is_set()
                or generation != self._row_generation
                or row_count != len(self._rows)
                or (expected_trace_seq is not None and expected_trace_seq != self._compute_trace_seq)
            ):
                return False
            if pending_recognized_at is not None:
                if self._pending_semantic_anchor is not None:
                    return False
                self._pending_semantic_anchor = (generation, semantic_signature[0], pending_recognized_at)
            if (
                self._active_compute_generation == generation
                and self._active_compute_signature == semantic_signature
            ):
                return True
            self._compute_trace_seq += 1
            self._last_announced_semantic_signature = semantic_signature
            trace = dict(self._latest_scheduled_timing)
            trace["semantic_recognized"] = (
                pending_recognized_at if pending_recognized_at is not None else time.monotonic()
            )
            self._active_compute_trace = trace
            self._active_compute_signature = semantic_signature
            self._active_compute_generation = generation
            trace_seq = self._compute_trace_seq
            counters = {
                "accepted_rows": self._accepted,
                "scheduled_rows": row_count,
                "semantic_seq": trace_seq,
                "snapshot_seq": self._snapshot_publish_seq,
            }
        return self._commit_pending_status_if_current(
            trace_seq=trace_seq,
            generation=generation,
            semantic_signature=semantic_signature,
            timestamps=trace,
            counters=counters,
        )

    def _probe_pending_semantic(self) -> None:
        """Recognize a trusted semantic change before the inference debounce.

        Reuse the lightweight parser for UI acknowledgement and anchor the
        unclaimed batch's debounce. Later rows cannot postpone that batch;
        the existing minimum inference interval still applies.
        """
        with self._lock:
            if self._stop.is_set():
                return
            rows = list(self._rows)
            row_count = len(rows)
            generation = self._row_generation
            timing = dict(self._latest_scheduled_timing)
            if row_count <= self._last_semantic_probe_count:
                return
        try:
            payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            semantic_signature = _semantic_signature_from_payload(payload)
        except Exception:
            semantic_signature = None
        recognized_at = time.monotonic()
        status_payload: tuple[int, dict[str, Any], dict[str, int]] | None = None
        with self._lock:
            if (
                self._stop.is_set()
                or generation != self._row_generation
                or row_count != len(self._rows)
            ):
                return
            self._last_semantic_probe_count = row_count
            self._last_probed_semantic_count = row_count
            self._last_probed_semantic_signature = semantic_signature
            if (
                semantic_signature is None
                or semantic_signature == self._last_semantic_signature
                or semantic_signature == self._last_announced_semantic_signature
            ):
                return
            if (
                self._pending_semantic_anchor is None
                or self._pending_semantic_anchor[:2] != (generation, semantic_signature[0])
            ):
                self._pending_semantic_anchor = (generation, semantic_signature[0], recognized_at)
            self._compute_trace_seq += 1
            self._last_announced_semantic_signature = semantic_signature
            trace = dict(timing)
            trace["semantic_recognized"] = recognized_at
            self._active_compute_trace = trace
            self._active_compute_signature = semantic_signature
            self._active_compute_generation = generation
            status_payload = (
                self._compute_trace_seq,
                trace,
                {
                    "accepted_rows": self._accepted,
                    "scheduled_rows": row_count,
                    "semantic_seq": self._compute_trace_seq,
                    "snapshot_seq": self._snapshot_publish_seq,
                },
            )
        if status_payload is not None:
            trace_seq, trace, counters = status_payload
            self._commit_pending_status_if_current(
                trace_seq=trace_seq,
                generation=generation,
                semantic_signature=semantic_signature,
                timestamps=trace,
                counters=counters,
            )

    def _finish_compute_trace(
        self,
        event: str,
        *,
        expected_signature: Any = _COMPUTE_SIGNATURE_UNSET,
        expected_generation: Any = _COMPUTE_GENERATION_UNSET,
        terminal_reason: str = "",
    ) -> None:
        with self._lock:
            if (
                expected_signature is not _COMPUTE_SIGNATURE_UNSET
                and self._active_compute_signature != expected_signature
            ):
                # A newer parser-confirmed trace arrived while an older monitor
                # inference was still running.  Its terminal state belongs only
                # to that old signature and must not clear the newer pending UI.
                return
            if (
                expected_generation is not _COMPUTE_GENERATION_UNSET
                and self._active_compute_generation != expected_generation
            ):
                return
            if self._active_compute_trace is None and event != "stopped":
                return
            trace = dict(self._active_compute_trace or {})
            trace_seq = self._compute_trace_seq
            counters = {
                "accepted_rows": self._accepted,
                "scheduled_rows": self._last_requested_count,
                "semantic_seq": self._compute_trace_seq,
                "snapshot_seq": self._snapshot_publish_seq,
            }
            if self._active_compute_signature == self._last_announced_semantic_signature:
                self._last_announced_semantic_signature = None
            self._active_compute_trace = None
            self._active_compute_signature = None
            self._active_compute_generation = None
            # Keep the state lock through the terminal sidecar commit.  Otherwise
            # reset/new semantic ownership could change after the checks above and
            # an equal-sequence terminal could overwrite that newer lifecycle.
            self._write_compute_status(
                event=event,
                compute_pending=False,
                trace_seq=trace_seq,
                timestamps=trace,
                counters=counters,
                terminal_reason=terminal_reason,
            )

    def _snapshot_mode(self, *, force: bool) -> tuple[str, int, int, int | None, bool]:
        if force or self.config.fast_n_trials is None or self.config.fast_n_trials <= 0:
            full_shadow = (
                self.config.shadow_trials
                if self.config.shadow_trials is not None
                else max(1, int(self.config.full_shadow_trials))
            )
            return (
                "full",
                self.config.n_trials,
                self.config.roi_trials,
                full_shadow,
                self.config.run_debug_shadows,
            )
        return (
            "fast",
            max(1, int(self.config.fast_n_trials)),
            0,
            min(
                max(1, int(self.config.full_shadow_trials)),
                max(1, int(self.config.fast_n_trials)),
            ),
            False,
        )

    @staticmethod
    def _max_sort_id(rows: Sequence[Mapping[str, Any]]) -> int:
        return max((int(row.get("SortID") or 0) for row in rows), default=0)

    def _persist_raw_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        snapshot_mode: str,
    ) -> None:
        new_rows = list(rows[self._raw_persisted_count :])
        if new_rows:
            _append_raw_rows_jsonl(self.raw_jsonl_path, new_rows)
            self._raw_persisted_count = len(rows)
        if snapshot_mode == "full":
            _atomic_write_json(self.raw_path, rows)

    def _mark_processed(
        self,
        *,
        row_count: int,
        max_sort_id: int,
        snapshot_mode: str,
        semantic_signature: tuple[Any, ...] | None = None,
        row_generation: int | None = None,
    ) -> bool:
        with self._lock:
            if row_generation is not None and row_generation != self._row_generation:
                return False
            self._mark_processed_locked(
                row_count=row_count,
                max_sort_id=max_sort_id,
                snapshot_mode=snapshot_mode,
                semantic_signature=semantic_signature,
            )
            return True

    def _mark_processed_locked(
        self,
        *,
        row_count: int,
        max_sort_id: int,
        snapshot_mode: str,
        semantic_signature: tuple[Any, ...] | None,
    ) -> None:
        self._last_processed_count = row_count
        self._last_inferred_max_sort = max_sort_id
        if snapshot_mode == "full":
            self._last_full_processed_count = row_count
        elif semantic_signature != self._last_semantic_signature:
            self._fast_epoch += 1
        if semantic_signature is not None:
            self._last_semantic_signature = semantic_signature
        self._last_inference_at = time.monotonic()
        self._last_partial_error = ""

    def _snapshot_owner_current_locked(
        self,
        row_generation: int,
        semantic_signature: tuple[Any, ...] | None,
    ) -> bool:
        if row_generation != self._row_generation:
            return False
        if self._active_compute_trace is None:
            return True
        return (
            self._active_compute_generation == row_generation
            and self._active_compute_signature == semantic_signature
        )

    def _archive_round_snapshot(self, artifact: Mapping[str, Any]) -> None:
        """Mirror each written snapshot into a per-round diagnostic archive.

        ``latest_snapshot.json`` only holds the most recent state, so a transient
        bidding-phase issue (e.g. a round that briefly stops quoting) is gone by
        the time the user exports. This keeps the latest snapshot per
        ``(round, phase)`` for the current game under ``round_snapshots/`` so the
        exact moment can be recovered from a diagnostic export. The archive is
        cleared whenever a new game session starts and never raises into the
        monitor loop.
        """
        if not self.config.archive_round_snapshots:
            return
        try:
            round_no = artifact.get("round")
            phase = artifact.get("phase")
            if round_no is None or not phase:
                return
            session_id = str(artifact.get("session_id") or "")
            archive_dir = self.config.log_dir / "round_snapshots"
            if session_id != self._round_archive_session:
                if archive_dir.exists():
                    for old in archive_dir.glob("*.json"):
                        try:
                            old.unlink()
                        except OSError:
                            pass
                self._round_archive_session = session_id
            safe_phase = "".join(
                ch if ch.isalnum() else "_" for ch in str(phase)
            )[:16]
            name = f"r{int(round_no):02d}_{safe_phase}.json"
            _atomic_write_json(archive_dir / name, artifact)
        except Exception:  # noqa: BLE001 - diagnostics must never break the monitor
            pass

    def _process_snapshot(
        self,
        *,
        force: bool,
        expected_schedule_owner: tuple[int, tuple[int, Any, float] | None] | None = None,
        _full_result: dict[str, Any] | None = None,
    ) -> bool | None:
        with self._lock:
            if self._full_job is not None and _full_result is None:
                return
            if expected_schedule_owner is not None and (
                self._stop.is_set()
                or expected_schedule_owner != (self._row_generation, self._pending_semantic_anchor)
            ):
                # The ready decision belongs to the batch checked by the tick.
                # A reset/new session in this gap must wait on its own clock.
                return
            rows = list(self._rows)
            row_generation = self._row_generation
            local_player_id_hint = self._known_local_player_id
            probed_semantic_count = self._last_probed_semantic_count
            probed_semantic_signature = self._last_probed_semantic_signature
            claimed_trace_seq = self._compute_trace_seq
            if not rows:
                return
            if not force and len(rows) <= self._last_processed_count:
                return
            current_max_sort = self._max_sort_id(rows)
            if not force and current_max_sort <= self._last_inferred_max_sort:
                return
            # Claim only the batch included in this row snapshot, under the same
            # lock. New facts arriving during processing establish a fresh anchor;
            # an old worker's completion must never clear that successor.
            self._pending_semantic_anchor = None

        raw_path = self.raw_path
        (
            snapshot_mode,
            n_trials,
            roi_trials,
            shadow_trials,
            run_debug_shadows,
        ) = self._snapshot_mode(force=force)
        with self._lock:
            if row_generation != self._row_generation:
                return
            self._persist_raw_rows(rows, snapshot_mode=snapshot_mode)
        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        semantic_signature: tuple[Any, ...] | None = None
        try:
            recovered_after_failed_probe = (
                probed_semantic_count == len(rows)
                and probed_semantic_signature is None
            )
            if (
                probed_semantic_count == len(rows)
                and probed_semantic_signature is not None
            ):
                semantic_signature = probed_semantic_signature
            else:
                try:
                    semantic_signature = _semantic_signature_from_payload(payload)
                except Exception:  # noqa: BLE001 - let the existing build path diagnose
                    semantic_signature = None
            if (
                not force
                and expected_schedule_owner is not None
                and expected_schedule_owner[1] is not None
                and semantic_signature is not None
                and semantic_signature[0] != expected_schedule_owner[1][1]
            ):
                # A failed lightweight probe can leave the previous session's
                # anchor attached to these rows. Only now has the parser trusted
                # the new session: rearm from this recognition, not its arrival
                # or the old deadline. A later producer/reset retains ownership.
                self._adopt_recovered_semantic_if_current(
                    row_count=len(rows),
                    generation=row_generation,
                    semantic_signature=semantic_signature,
                    pending_recognized_at=time.monotonic(),
                    expected_trace_seq=claimed_trace_seq,
                )
                return
            if (
                recovered_after_failed_probe
                and semantic_signature is not None
                and (force or semantic_signature != self._last_semantic_signature)
                and not self._adopt_recovered_semantic_if_current(
                    row_count=len(rows),
                    generation=row_generation,
                    semantic_signature=semantic_signature,
                )
            ):
                return
            if (
                not force
                and semantic_signature is not None
                and semantic_signature == self._last_semantic_signature
            ):
                bid_signature = bid_signature_from_payload(
                    payload,
                    local_player_id_hint=local_player_id_hint,
                )
                with self._lock:
                    if row_generation != self._row_generation:
                        return
                    pending_rows = len(rows) > self._last_processed_count
                    if bid_signature and bid_signature != self._last_bid_signature:
                        if refresh_latest_snapshot_bids(
                            self.config.log_dir,
                            payload,
                            local_player_id_hint=local_player_id_hint,
                        ):
                            self._last_bid_signature = bid_signature
                            print(
                                f"[bid] {self.config.source_name} refreshed current-highest "
                                f"without inference rows={len(rows)}",
                                flush=True,
                            )
                    self._mark_processed_locked(
                        row_count=len(rows),
                        max_sort_id=current_max_sort,
                        snapshot_mode=snapshot_mode,
                        semantic_signature=semantic_signature,
                    )
                    if pending_rows and _monitor_heartbeat_refresh_enabled():
                        if _refresh_latest_snapshot_heartbeat(
                            self.config.log_dir,
                            semantic_signature=semantic_signature,
                        ):
                            print(
                                f"[heartbeat] {self.config.source_name} refreshed "
                                f"snapshot age rows={len(rows)}",
                                flush=True,
                            )
                print(
                    f"[skip] {self.config.source_name} {snapshot_mode} "
                    f"no semantic change rows={len(rows)}",
                    flush=True,
                )
                return
            # 激活强制(②, 0.2.2 付费版): 本进程激活过(打包产品启动调了 enforce.startup_activate)
            # 且未放行(未激活 / 试用到期)→ 跳过推理 = 真不计算, 不把任何英雄估值写进快照(不泄露 + 省 CPU)。
            # enforce.current() is None = 本进程没接激活(dev/源码其它 entry)→ 不 gate, 行为不变。
            # overlay 自身另有激活检查, 降级时清空显示, 故此处只需"不算 + 不写新快照"(旧快照值被 overlay 盖住)。
            try:
                from bidking_lab.secure import enforce as _enforce
                if _enforce.current() is not None and not _enforce.should_run_full():
                    if not self._mark_processed(
                        row_count=len(rows),
                        max_sort_id=current_max_sort,
                        snapshot_mode=snapshot_mode,
                        semantic_signature=semantic_signature,
                        row_generation=row_generation,
                    ):
                        return
                    print(
                        f"[gated] {self.config.source_name} 未激活/试用到期 → 跳过推理(降级)",
                        flush=True,
                    )
                    self._finish_compute_trace(
                        "degraded",
                        expected_signature=semantic_signature,
                        expected_generation=row_generation,
                    )
                    return
            except Exception:  # noqa: BLE001  强制绝不拖垮监控
                pass
            _build_started = time.monotonic()
            with self._lock:
                if (
                    self._active_compute_trace is not None
                    and self._active_compute_generation == row_generation
                    and self._active_compute_signature == semantic_signature
                ):
                    self._active_compute_trace["monitor_inference_started"] = _build_started
            artifact = _full_result if _full_result is not None else build_monitor_artifact_from_payload(
                payload,
                file=raw_path.name,
                tables=self.tables,
                n_trials=n_trials,
                roi_trials=roi_trials,
                shadow_trials=shadow_trials,
                run_debug_shadows=run_debug_shadows,
                seed=self.config.seed,
                formal_mode=self.config.formal_mode,
                local_player_id_hint=local_player_id_hint,
            )
            _build_completed = time.monotonic()
            with self._lock:
                if row_generation != self._row_generation:
                    return
                if (
                    self._active_compute_trace is not None
                    and self._active_compute_generation == row_generation
                    and self._active_compute_signature == semantic_signature
                ):
                    self._active_compute_trace["monitor_inference_completed"] = _build_completed
                if snapshot_mode == "fast":
                    self._last_fast_seconds = time.monotonic() - _build_started
            try:
                events = parse_fatbeans_capture_payload(
                    payload,
                    local_player_id_hint=local_player_id_hint,
                )
                latest_local_player_id = _latest_local_player_id_from_events(events)
            except Exception:  # noqa: BLE001 - artifact build already succeeded
                latest_local_player_id = None
            if latest_local_player_id is not None:
                with self._lock:
                    if row_generation != self._row_generation:
                        return
                    if self._known_local_player_id != latest_local_player_id:
                        self._known_local_player_id = latest_local_player_id
                        _write_cached_local_player_id(
                            _local_player_cache_path(self.config.log_dir),
                            latest_local_player_id,
                        )
            artifact["snapshot_mode"] = snapshot_mode
            artifact["inference_profile"] = {
                "mode": snapshot_mode,
                "n_trials": n_trials,
                "roi_trials": roi_trials,
                "shadow_trials": shadow_trials,
                "run_debug_shadows": run_debug_shadows,
            }
            artifact["n_trials"] = n_trials
            artifact["roi_trials"] = roi_trials
            artifact["shadow_trials"] = shadow_trials
            artifact["source"] = self.config.source_name
            artifact["raw_capture"] = str(
                (
                    raw_path
                    if snapshot_mode == "full"
                    else self.raw_jsonl_path
                ).resolve()
            )
            artifact["raw_capture_jsonl"] = str(self.raw_jsonl_path.resolve())
            artifact["capture_rows"] = len(rows)
            artifact[self.config.packet_count_key] = len(rows)
            if semantic_signature is not None:
                artifact["inference_signature"] = list(semantic_signature)
            lb = artifact.get("latest_bids")
            if isinstance(lb, Mapping):
                latest_bid_signature = bid_signature_from_payload(
                    payload,
                    local_player_id_hint=local_player_id_hint,
                )
                with self._lock:
                    if row_generation != self._row_generation:
                        return
                    self._last_bid_signature = latest_bid_signature
            if _should_skip_fast_bootstrap_snapshot(
                artifact,
                row_count=len(rows),
                snapshot_mode=snapshot_mode,
            ):
                print(
                    f"[wait] {self.config.source_name} skip fast bootstrap "
                    f"settled snapshot rows={len(rows)}",
                    flush=True,
                )
                if not self._mark_processed(
                    row_count=len(rows),
                    max_sort_id=current_max_sort,
                    snapshot_mode=snapshot_mode,
                    semantic_signature=semantic_signature,
                    row_generation=row_generation,
                ):
                    return
                self._finish_compute_trace(
                    "skipped_bootstrap",
                    expected_signature=semantic_signature,
                    expected_generation=row_generation,
                )
                return
            # 落 sessions.jsonl + 自动上传的门控。
            # 通常只在 full 快照落盘(fast 是 live 响应用的草稿)。但【结算帧必须无条件落盘+上传】:
            #   深仓晚轮的 full 升级会被 _maybe_run_idle_full_upgrade 的「too heavy」保护跳过
            #   (保 live 响应),导致结算帧永远停在 fast → 漏写 sessions、漏上传 → 对局记录/服务器
            #   都收不到这一局(数据采集断流)。结算真值(件数/格子/价值/known_value_sum)来自 0x2d
            #   帧解析、与推理 trials 无关, 故 fast 结算帧的落盘数据是准确的, 无条件持久化是安全的。
            is_settled = _artifact_phase(artifact) == "settled"
            with self._lock:
                if not self._snapshot_owner_current_locked(
                    row_generation,
                    semantic_signature,
                ):
                    return
                # Keep generation ownership across the atomic latest snapshot,
                # append-only logs, trace publication and archive.  reset_rows
                # uses the same state lock, so an old generation can finish
                # wholly before reset or be rejected wholly after it; it can
                # never write across the generation boundary.
                write_monitor_logs(
                    artifact,
                    log_dir=self.config.log_dir,
                    append_logs=snapshot_mode == "full" or is_settled,
                )
                # Receipt writing is queued and best effort.  It deliberately
                # happens after latest publication, so a disk problem can never
                # delay a quote or turn an accepted snapshot into a failure.
                session_id = artifact.get("session_id")
                if isinstance(session_id, str) and session_id:
                    self._fact_receipts.enqueue(
                        session_id=session_id,
                        receipt=_monitor_fact_receipt(
                            artifact,
                            row_generation=row_generation,
                            source_update_seq=current_max_sort,
                            snapshot_mode=snapshot_mode,
                        ),
                    )
                published_at = time.monotonic()
                if self._active_compute_trace is not None:
                    self._active_compute_trace["snapshot_published"] = published_at
                    self._snapshot_publish_seq += 1
                    self._write_compute_status(
                        event="snapshot_published",
                        compute_pending=True,
                        trace_seq=self._compute_trace_seq,
                        timestamps=dict(self._active_compute_trace),
                        counters={
                            "accepted_rows": self._accepted,
                            "scheduled_rows": len(rows),
                            "semantic_seq": self._compute_trace_seq,
                            "snapshot_seq": self._snapshot_publish_seq,
                        },
                    )
                self._archive_round_snapshot(artifact)
        except Exception as exc:  # noqa: BLE001 - long-running monitor boundary
            with self._lock:
                if row_generation != self._row_generation:
                    return
            if _looks_like_partial_stream_error(exc):
                message = str(exc)
                if message != self._last_partial_error:
                    print(
                        f"[wait] partial Fatbeans stream: {message}",
                        file=sys.stderr,
                        flush=True,
                    )
                    self._last_partial_error = message
                self._finish_compute_trace(
                    "partial",
                    expected_signature=semantic_signature,
                    expected_generation=row_generation,
                )
                return
            _append_webhook_error_log(
                self.config.log_dir,
                raw_path=raw_path,
                packet_count=len(rows),
                exc=exc,
            )
            print(
                f"[error] webhook inference failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
            self._finish_compute_trace(
                "error",
                expected_signature=semantic_signature,
                expected_generation=row_generation,
                terminal_reason=_support_safe_inference_failure_reason(exc),
            )
            return

        if not self._mark_processed(
            row_count=len(rows),
            max_sort_id=current_max_sort,
            snapshot_mode=snapshot_mode,
            semantic_signature=semantic_signature,
            row_generation=row_generation,
        ):
            return
        count_label = self.config.packet_count_key.replace("_", " ")
        print(
            f"[ok] {self.config.source_name} {snapshot_mode} "
            f"{count_label}={len(rows)} -> "
            f"{self.config.log_dir / 'latest_snapshot.json'}",
            flush=True,
        )
        return True


def _write_json_response(
    handler: BaseHTTPRequestHandler,
    status_code: int,
    payload: Mapping[str, Any],
) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_handler(
    monitor: FatbeansWebhookMonitor,
    *,
    webhook_path: str,
    verbose: bool,
) -> type[BaseHTTPRequestHandler]:
    class FatbeansWebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.rstrip("/") == "/health":
                _write_json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        **monitor.status(),
                    },
                )
                return
            _write_json_response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.split("?", 1)[0] != webhook_path:
                _write_json_response(self, 404, _allow_response())
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(content_length)
                payload = json.loads(raw.decode("utf-8-sig") or "{}")
                if not isinstance(payload, Mapping):
                    raise ValueError("webhook payload must be a JSON object")
                result = monitor.accept_payload(payload)
                if verbose:
                    print(f"[webhook] {result}", flush=True)
            except Exception as exc:  # noqa: BLE001 - keep packet path non-blocking
                if verbose:
                    print(f"[webhook-error] {exc}", file=sys.stderr, flush=True)
            _write_json_response(self, 200, _allow_response())

        def log_message(self, fmt: str, *args: Any) -> None:
            if verbose:
                super().log_message(fmt, *args)

    return FatbeansWebhookHandler


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Receive Fatbeans WebHook packet callbacks for live monitor.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/fatbeans")
    parser.add_argument(
        "--log-dir",
        default=str(ROOT / "data" / "logs" / "live"),
        help="Directory for latest_snapshot.json and JSONL logs.",
    )
    parser.add_argument(
        "--raw-dir",
        default=None,
        help="Directory for rolling raw webhook capture JSON.",
    )
    parser.add_argument(
        "--tables-dir",
        default=None,
        help="Override raw game table directory; defaults to data/raw/tables.",
    )
    parser.add_argument(
        "--process-name",
        default="BidKing.exe",
        help="Only accept this process name; use '*' to disable process filtering.",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        action="append",
        default=None,
        help="BidKing server port used to infer SEND/REV. May be repeated.",
    )
    parser.add_argument("--debounce-seconds", type=float, default=1.0)
    parser.add_argument("--min-inference-interval-seconds", type=float, default=2.0)
    parser.add_argument("--n-trials", type=int, default=500)
    parser.add_argument("--roi-trials", type=int, default=250)
    parser.add_argument("--shadow-trials", type=int, default=None)
    parser.add_argument(
        "--full-shadow-trials",
        type=int,
        default=20,
        help="Shadow trials for full/archive snapshots (fast uses 1).",
    )
    parser.add_argument(
        "--skip-debug-shadows",
        action="store_true",
        help="Skip profile_b5 debug shadow (legacy flag; default is already off).",
    )
    parser.add_argument(
        "--enable-debug-shadows",
        action="store_true",
        help="Run profile_b5 debug shadow during live inference.",
    )
    parser.add_argument(
        "--fast-n-trials",
        type=int,
        default=10,
        help=(
            "Trials for non-force live snapshots. Use 0 to disable fast "
            "snapshots and run full inference on every update."
        ),
    )
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument(
        "--formal-mode",
        default=os.environ.get("BIDKING_LIVE_FORMAL_MODE", "v3_practical"),
        choices=("v3_practical", "v2"),
        help=(
            "Formal live bid source. Defaults to BIDKING_LIVE_FORMAL_MODE "
            "or v3_practical."
        ),
    )
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--package-generation-identity", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else log_dir / "raw"
    if not args.no_lock:
        try:
            monitor_path = Path(sys.argv[0])
            package_generation_identity = _validated_package_generation_identity(
                args.package_generation_identity,
                monitor_path=monitor_path,
            )
            _acquire_lock(
                log_dir,
                monitor_path=monitor_path,
                package_generation_identity=package_generation_identity,
            )
        except RuntimeError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
    tables = load_monitor_tables(tables_dir=args.tables_dir)
    server_ports = tuple(args.server_port or [10000])
    webhook_path = args.path if args.path.startswith("/") else f"/{args.path}"

    monitor = FatbeansWebhookMonitor(
        config=WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name=None if args.process_name == "*" else args.process_name,
            server_ports=server_ports,
            n_trials=args.n_trials,
            roi_trials=args.roi_trials,
            shadow_trials=args.shadow_trials,
            full_shadow_trials=max(1, int(args.full_shadow_trials)),
            run_debug_shadows=bool(args.enable_debug_shadows)
            and not args.skip_debug_shadows,
            seed=args.seed,
            formal_mode=args.formal_mode,
            debounce_seconds=args.debounce_seconds,
            min_inference_interval_seconds=args.min_inference_interval_seconds,
            fast_n_trials=args.fast_n_trials if args.fast_n_trials > 0 else None,
        ),
        tables=tables,
    )
    monitor.start()

    server = ThreadingHTTPServer(
        (args.host, args.port),
        _make_handler(monitor, webhook_path=webhook_path, verbose=args.verbose),
    )
    print(
        f"[listen] Fatbeans WebHook URL: "
        f"http://{args.host}:{args.port}{webhook_path}",
        flush=True,
    )
    print(
        f"[listen] process={args.process_name} server_ports={list(server_ports)} "
        f"log_dir={log_dir}",
        flush=True,
    )

    def _version_banner() -> None:  # 后台比对版本(连服务器/version), 永不阻塞或拖垮启动。
        try:
            from bidking_lab.live import version_check
            from bidking_lab.live.startup_version_notice import check_and_publish_startup_version

            st = check_and_publish_startup_version(log_dir)
            print(f"[version] {version_check.format_status_line(st)}", flush=True)
        except Exception as exc:  # noqa: BLE001  版本检查失败不影响监控
            print(f"[version] 跳过(版本检查异常: {type(exc).__name__})", flush=True)

    threading.Thread(target=_version_banner, daemon=True).start()

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("[stop] interrupted", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
