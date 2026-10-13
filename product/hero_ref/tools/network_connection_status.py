"""Privacy-safe connection state model for the WSS live path.

The three state axes deliberately stay independent:

* game connection describes BidKing's own server connection;
* WSS interception describes the local helper/redirector path;
* capture decode describes whether received data became recognized game frames.

Do not collapse them into one health flag.  In particular, TCP success is not
application/WebSocket success, and bytes received with zero accepted frames is
an unrecognized-data state rather than a network failure.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any


NETWORK_CONNECTION_STATUS_FILENAME = "network_connection_status.json"
NETWORK_CONNECTION_STATUS_SCHEMA_VERSION = 1

GAME_CONNECTION_STATES = frozenset(
    {"not_running", "connecting", "connected", "failed", "unknown"}
)
WSS_INTERCEPT_STATES = frozenset(
    {"not_started", "ready", "attached", "failed", "unknown"}
)
CAPTURE_DECODE_STATES = frozenset(
    {"idle", "receiving", "unrecognized", "recognized"}
)

PLAYER_LOG_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "websocket_unable_to_connect",
        re.compile(
            r"websocket\s+error\s*:\s*Unable to connect to the remote server",
            re.IGNORECASE,
        ),
    ),
)
PLAYER_LOG_ERROR_KEYS = frozenset(key for key, _pattern in PLAYER_LOG_ERROR_PATTERNS)

_FAILED_HELPER_PHASES = frozenset(
    {"helper_start_failed", "exited_unexpectedly", "monitor_guard_failed"}
)
_READY_HELPER_PHASES = frozenset({"helper_running"})
_KNOWN_HELPER_PHASES = frozenset(
    {
        "helper_running",
        "helper_start_failed",
        "exited_unexpectedly",
        "stopped",
        "monitor_guard_failed",
    }
)
_KNOWN_HELPER_ERRORS = frozenset(
    {
        "wss_redirector_elevation_denied",
        "wss_windivert_start_failed",
        "wss_redirector_start_failed",
        "wss_addon_start_failed",
        "wss_capture_helper_start_failed",
        "wss_capture_helper_exit_unclassified",
        "wss_runtime_compatibility_failed",
        "wss_redirector_missing_stable",
        "wss_child_lifecycle_guard_failed",
    }
)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, parsed)


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def safe_player_log_error_counts(value: Any) -> dict[str, int]:
    source = _mapping(value)
    return {
        key: _non_negative_int(source.get(key))
        for key in sorted(PLAYER_LOG_ERROR_KEYS)
        if _non_negative_int(source.get(key)) > 0
    }


def _capture_counter(capture: Mapping[str, Any], *names: str) -> int:
    return max((_non_negative_int(capture.get(name)) for name in names), default=0)


def derive_network_connection_status(
    *,
    launch_id: str = "",
    game_running: bool | None = None,
    game_pid: int | None = None,
    game_started_at: float | None = None,
    player_log_window_started_at: float | None = None,
    player_log_error_counts: Mapping[str, Any] | None = None,
    capture_status: Mapping[str, Any] | None = None,
    helper_status: Mapping[str, Any] | None = None,
    independent_game_connected: bool = False,
) -> dict[str, Any]:
    """Derive the three independent states from already privacy-safe evidence."""

    capture = _mapping(capture_status)
    helper = _mapping(helper_status)
    source = str(capture.get("source") or "")
    current_launch_id = str(launch_id or "")
    helper_launch_id = str(helper.get("launch_id") or "")
    if current_launch_id and helper_launch_id and helper_launch_id != current_launch_id:
        helper = {}

    error_counts = safe_player_log_error_counts(player_log_error_counts)
    fresh_ws_errors = sum(error_counts.values())

    active_flows = _capture_counter(capture, "active_flows")
    raw_count = _capture_counter(
        capture,
        "raw_packets",
        "sniffed_packets",
        "sanitized_frames",
        "wss_messages",
        "wss_bytes",
    )
    accepted_count = _capture_counter(
        capture,
        "accepted_frames",
        "accepted_packets",
    )
    accepted_present = any(
        name in capture for name in ("accepted_frames", "accepted_packets")
    )
    ignored_count = _capture_counter(capture, "ignored_frames")
    session_present = bool(str(capture.get("active_session_id") or ""))
    has_current_wss_health = bool(
        active_flows > 0
        or raw_count > 0
        or accepted_count > 0
        or session_present
    )

    if accepted_count > 0 or session_present:
        capture_decode_state = "recognized"
    elif raw_count > 0:
        capture_decode_state = "unrecognized" if accepted_present else "receiving"
    elif ignored_count > 0:
        capture_decode_state = "receiving"
    else:
        capture_decode_state = "idle"

    helper_phase = str(helper.get("phase") or "")
    if helper_phase not in _KNOWN_HELPER_PHASES:
        helper_phase = ""
    helper_error_code = str(helper.get("error_code") or "")
    helper_failed = (
        helper_phase in _FAILED_HELPER_PHASES
        or helper_error_code in _KNOWN_HELPER_ERRORS
        or bool(str(capture.get("error_code") or "").startswith("wss_"))
    )
    if has_current_wss_health:
        # Current per-game flow/data/session evidence is stronger than an older
        # helper exit recorded in the same launch.
        wss_intercept_state = "attached"
    elif helper_failed:
        wss_intercept_state = "failed"
    elif helper_phase in _READY_HELPER_PHASES or source == "wss_local":
        wss_intercept_state = "ready"
    elif game_running is False and not helper:
        wss_intercept_state = "not_started"
    else:
        wss_intercept_state = "unknown"

    game_running_state = _optional_bool(game_running)
    has_game_data = bool(
        independent_game_connected is True
        or has_current_wss_health
    )
    if game_running_state is False:
        game_connection_state = "not_running"
    elif has_game_data:
        # Active WSS flow/data/session evidence is current proof that the game
        # recovered after an earlier Player.log connection error. Fixed-endpoint
        # TCP results are intentionally absent from has_game_data.
        game_connection_state = "connected"
    elif fresh_ws_errors > 0:
        # Only errors counted inside the process-bound Player.log window reach
        # this branch. Fixed-endpoint TCP failures are intentionally not an
        # input because they are auxiliary evidence, not WebSocket proof.
        game_connection_state = "failed"
    elif game_running_state is True:
        game_connection_state = "connecting"
    else:
        game_connection_state = "unknown"

    payload: dict[str, Any] = {
        "schema_version": NETWORK_CONNECTION_STATUS_SCHEMA_VERSION,
        "updated_at": time.time(),
        "launch_id": current_launch_id,
        "game_connection_state": game_connection_state,
        "wss_intercept_state": wss_intercept_state,
        "capture_decode_state": capture_decode_state,
        "evidence": {
            "game_running": game_running_state,
            "player_log_error_counts": error_counts,
            "active_flows": active_flows,
            "wss_raw_count": raw_count,
            "accepted_frames": accepted_count,
            "ignored_frames": ignored_count,
            "helper_phase": helper_phase if helper else "",
            "helper_error_code": (
                helper_error_code if helper_error_code in _KNOWN_HELPER_ERRORS else ""
            ),
        },
    }
    if isinstance(game_pid, int) and not isinstance(game_pid, bool) and game_pid > 0:
        payload["game_pid"] = game_pid
    safe_game_started_at = _optional_non_negative_float(game_started_at)
    if safe_game_started_at is not None:
        payload["game_started_at"] = safe_game_started_at
    safe_window_started_at = _optional_non_negative_float(
        player_log_window_started_at
    )
    if safe_window_started_at is not None:
        payload["player_log_window_started_at"] = safe_window_started_at
    return payload


def validate_network_connection_status(
    value: Any,
    *,
    launch_id: str = "",
) -> dict[str, Any]:
    """Return only the fixed privacy-safe schema, or an empty mapping."""

    source = _mapping(value)
    current_launch_id = str(launch_id or "")
    sidecar_launch_id = str(source.get("launch_id") or "")
    if current_launch_id and sidecar_launch_id and sidecar_launch_id != current_launch_id:
        return {}
    game_state = str(source.get("game_connection_state") or "")
    wss_state = str(source.get("wss_intercept_state") or "")
    decode_state = str(source.get("capture_decode_state") or "")
    if (
        game_state not in GAME_CONNECTION_STATES
        or wss_state not in WSS_INTERCEPT_STATES
        or decode_state not in CAPTURE_DECODE_STATES
    ):
        return {}
    evidence = _mapping(source.get("evidence"))
    safe_updated_at = _optional_non_negative_float(source.get("updated_at"))
    safe_game_started_at = _optional_non_negative_float(
        source.get("game_started_at")
    )
    safe_window_started_at = _optional_non_negative_float(
        source.get("player_log_window_started_at")
    )
    helper_phase = str(evidence.get("helper_phase") or "")
    if helper_phase not in _KNOWN_HELPER_PHASES:
        helper_phase = ""
    return {
        "schema_version": NETWORK_CONNECTION_STATUS_SCHEMA_VERSION,
        "updated_at": safe_updated_at,
        "launch_id": sidecar_launch_id,
        "game_connection_state": game_state,
        "wss_intercept_state": wss_state,
        "capture_decode_state": decode_state,
        "game_pid": (
            _non_negative_int(source.get("game_pid"))
            if source.get("game_pid") is not None
            else None
        ),
        "game_started_at": safe_game_started_at,
        "player_log_window_started_at": safe_window_started_at,
        "evidence": {
            "game_running": _optional_bool(evidence.get("game_running")),
            "player_log_error_counts": safe_player_log_error_counts(
                evidence.get("player_log_error_counts")
            ),
            "active_flows": _non_negative_int(evidence.get("active_flows")),
            "wss_raw_count": _non_negative_int(evidence.get("wss_raw_count")),
            "accepted_frames": _non_negative_int(evidence.get("accepted_frames")),
            "ignored_frames": _non_negative_int(evidence.get("ignored_frames")),
            "helper_phase": helper_phase,
            "helper_error_code": (
                str(evidence.get("helper_error_code") or "")
                if str(evidence.get("helper_error_code") or "")
                in _KNOWN_HELPER_ERRORS
                else ""
            ),
        },
    }


class PlayerLogConnectionWindow:
    """Count fixed errors inside one game process identity window.

    The monitor starts before BidKing.  Capturing the file length at that point
    gives an equivalent fresh-start boundary even when Player.log lines do not
    carry parseable timestamps.  If Unity truncates the file on launch, the
    reader safely restarts at byte zero.  A process identity is PID plus its
    available creation time, so PID reuse cannot inherit old error counts. Raw
    log text is never retained after each scan and is never written to the
    sidecar.
    """

    _MAX_READ_BYTES = 2 * 1024 * 1024
    _TAIL_CHARS = 256

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            self._cold_start_size = max(0, int(self.path.stat().st_size))
        except OSError:
            self._cold_start_size = 0
        self._pid: int | None = None
        self._game_started_at: float | None = None
        self._has_bound_process = False
        self._offset = self._cold_start_size
        self._tail = ""
        self._counts: dict[str, int] = {}

    def _reset_for_process(self, pid: int, game_started_at: float | None) -> None:
        first_binding = not self._has_bound_process
        self._pid = int(pid)
        self._game_started_at = _optional_non_negative_float(game_started_at)
        self._has_bound_process = True
        self._counts = {}
        self._tail = ""
        try:
            current_size = max(0, int(self.path.stat().st_size))
        except OSError:
            current_size = 0
        if first_binding:
            self._offset = (
                0 if current_size < self._cold_start_size else self._cold_start_size
            )
        else:
            # A reused PID with a different creation time is a new process
            # identity. Without parseable line timestamps, the only safe
            # boundary is the file size observed when that identity is bound.
            self._offset = current_size

    def observe(
        self,
        *,
        game_running: bool | None,
        game_pid: int | None,
        game_started_at: float | None,
    ) -> dict[str, int]:
        if game_running is not True or not isinstance(game_pid, int) or game_pid <= 0:
            if game_running is False:
                self._pid = None
                self._game_started_at = None
                self._counts = {}
                self._tail = ""
            return dict(self._counts)
        observed_started_at = _optional_non_negative_float(game_started_at)
        process_changed = self._pid != game_pid
        if (
            not process_changed
            and self._game_started_at is not None
            and observed_started_at is not None
            and not math.isclose(
                self._game_started_at,
                observed_started_at,
                rel_tol=0.0,
                abs_tol=0.001,
            )
        ):
            process_changed = True
        if process_changed:
            self._reset_for_process(game_pid, game_started_at)
        elif self._game_started_at is None and observed_started_at is not None:
            # A temporarily unavailable create_time must not manufacture a
            # reset. Bind it later while preserving this PID's observations.
            self._game_started_at = observed_started_at
        try:
            size = max(0, int(self.path.stat().st_size))
            if size < self._offset:
                self._offset = 0
                self._tail = ""
                self._counts = {}
            if size <= self._offset:
                return dict(self._counts)
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(self._MAX_READ_BYTES)
                self._offset += len(chunk)
        except OSError:
            return dict(self._counts)
        previous_tail = self._tail
        text = previous_tail + chunk.decode("utf-8", errors="replace")
        for key, pattern in PLAYER_LOG_ERROR_PATTERNS:
            count = sum(1 for match in pattern.finditer(text) if match.end() > len(previous_tail))
            if count:
                self._counts[key] = self._counts.get(key, 0) + count
        self._tail = text[-self._TAIL_CHARS :]
        return dict(self._counts)


class GameCaptureLivenessEpoch:
    """Keep WSS recovery evidence inside one game process identity.

    Monitor-lifetime totals remain useful for health diagnostics, but they must
    not prove that a later BidKing process recovered. Flow identifiers stay
    internal; only privacy-safe counts and the existing session identifier are
    exposed to the network status model.
    """

    def __init__(self) -> None:
        self._pid: int | None = None
        self._game_started_at: float | None = None
        self._bound = False
        self._recording_enabled = False
        self._reliable_identity_unresolved = False
        self._active_flow_ids: set[str] = set()
        self._raw_frames = 0
        self._accepted_frames = 0
        self._ignored_frames = 0
        self._active_session_id = ""

    def _clear_capture(self) -> None:
        self._active_flow_ids.clear()
        self._raw_frames = 0
        self._accepted_frames = 0
        self._ignored_frames = 0
        self._active_session_id = ""

    def observe_process(
        self,
        *,
        game_running: bool | None,
        game_pid: int | None,
        game_started_at: float | None,
    ) -> bool:
        """Bind the epoch and return whether a real process change occurred."""

        running = _optional_bool(game_running)
        valid_pid = (
            int(game_pid)
            if isinstance(game_pid, int)
            and not isinstance(game_pid, bool)
            and game_pid > 0
            else None
        )
        observed_started_at = _optional_non_negative_float(game_started_at)
        if running is False:
            changed = self._bound
            self._pid = None
            self._game_started_at = None
            self._bound = False
            self._recording_enabled = False
            self._reliable_identity_unresolved = False
            self._clear_capture()
            return changed
        if running is not True or valid_pid is None:
            # An incomplete process query cannot safely assign new events to
            # the previous identity. Keep its epoch for a later valid query,
            # but temporarily withhold it from connection recovery. Flow-close
            # events can be missed during this gap, so never resurrect the
            # previous active-flow set when identity becomes observable again.
            if self._bound:
                self._active_flow_ids.clear()
                if self._game_started_at is not None:
                    self._reliable_identity_unresolved = True
            self._recording_enabled = False
            return False

        process_changed = not self._bound or self._pid != valid_pid
        if (
            not process_changed
            and self._reliable_identity_unresolved
            and observed_started_at is None
        ):
            # A reliable identity followed by an observation gap cannot be
            # confirmed from a recycled PID alone. Stay fail-closed until its
            # creation time returns.
            self._recording_enabled = False
            return False
        if (
            not process_changed
            and self._game_started_at is not None
            and observed_started_at is not None
            and not math.isclose(
                self._game_started_at,
                observed_started_at,
                rel_tol=0.0,
                abs_tol=0.001,
            )
        ):
            process_changed = True
        if process_changed:
            self._pid = valid_pid
            self._game_started_at = observed_started_at
            self._bound = True
            self._reliable_identity_unresolved = False
            self._clear_capture()
        elif self._game_started_at is None and observed_started_at is not None:
            # Late create_time is additional evidence for the same PID, not a
            # reason to discard capture already observed for that process.
            self._game_started_at = observed_started_at
        elif self._reliable_identity_unresolved:
            # Matching PID and reliable creation time confirm the original
            # process after the gap. Non-flow evidence remains same-process
            # evidence; active flows were deliberately invalidated above.
            self._reliable_identity_unresolved = False
        self._recording_enabled = True
        return process_changed

    def open_flow(self, flow_id: Any) -> None:
        normalized = str(flow_id or "").strip()
        if self._recording_enabled and normalized:
            self._active_flow_ids.add(normalized)

    def close_flow(self, flow_id: Any) -> None:
        normalized = str(flow_id or "").strip()
        if self._recording_enabled and normalized:
            self._active_flow_ids.discard(normalized)

    def record_raw_frame(self, flow_id: Any) -> None:
        if not self._recording_enabled:
            return
        self.open_flow(flow_id)
        self._raw_frames += 1

    def record_accepted_frame(self, session_id: Any = "") -> None:
        if not self._recording_enabled:
            return
        self._accepted_frames += 1
        normalized = str(session_id or "").strip()
        if normalized:
            self._active_session_id = normalized

    def record_ignored_frames(self, count: Any = 1) -> None:
        if self._recording_enabled:
            self._ignored_frames += _non_negative_int(count)

    def capture_status(self, *, error_code: Any = "") -> dict[str, Any]:
        if self._bound and self._recording_enabled:
            active_flows = len(self._active_flow_ids)
            raw_frames = self._raw_frames
            accepted_frames = self._accepted_frames
            ignored_frames = self._ignored_frames
            active_session_id = self._active_session_id
        else:
            active_flows = raw_frames = accepted_frames = ignored_frames = 0
            active_session_id = ""
        return {
            "source": "wss_local",
            "active_flows": active_flows,
            "sniffed_packets": raw_frames,
            "raw_packets": raw_frames,
            "accepted_frames": accepted_frames,
            "ignored_frames": ignored_frames,
            "active_session_id": active_session_id,
            **({"error_code": str(error_code)} if str(error_code or "") else {}),
        }


def atomic_write_network_status(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
