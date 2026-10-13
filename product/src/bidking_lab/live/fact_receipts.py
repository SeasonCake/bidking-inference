"""Bounded, support-safe facts retained for the active live session.

This is deliberately not a capture archive.  Callers provide an already
redacted fact projection; the writer only adds ordering, a session pseudonym
and durable writer state.  Disk failures are isolated from the quote path.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "fact_receipt_v1"
MAX_RECEIPTS_PER_SESSION = 512
MAX_BYTES_PER_SESSION = 8 * 1024 * 1024
MAX_SESSIONS = 20
_SAFE_SESSION_DIR = set("0123456789abcdef")
_SESSION_BINDING_DOMAIN = b"bidking.fact-receipt.current-session.v1\x00"
_FORBIDDEN_KEYS = frozenset({
    "raw", "raw_packet", "packet_body", "token", "username", "user_name",
    "password", "secret", "certificate", "private_key", "session_id", "device_id",
    "path", "body", "payload",
})


def fact_receipt_session_binding(session_id: object) -> str | None:
    """Return the cross-process, irreversible binding for one exact session id.

    The domain separator is public and stable; the live session id supplies the
    high-entropy authority.  No process-local salt, raw session value or secret is
    persisted, so monitor and overlay can independently derive the same binding.
    """

    if (
        type(session_id) is not str  # noqa: E721 - runtime identity boundary
        or not 1 <= len(session_id) <= 256
        or session_id.strip() != session_id
        or "\x00" in session_id
    ):
        return None
    try:
        encoded = session_id.encode("utf-8", "strict")
    except UnicodeError:
        return None
    return hashlib.sha256(_SESSION_BINDING_DOMAIN + encoded).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _support_safe(value: Any) -> bool:
    """Reject raw/identity-shaped fields even if a future caller nests them."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            text = str(key).lower()
            if text in _FORBIDDEN_KEYS or text.startswith("raw_"):
                return False
            if not _support_safe(child):
                return False
    elif isinstance(value, (list, tuple)):
        return all(_support_safe(child) for child in value)
    return True


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with tmp.open("xb") as fh:
            data = _canonical_bytes(payload)
            fh.write(data)
            fh.write(b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class FactReceiptWriter:
    """One background writer for immutable current-session receipt leaves.

    ``enqueue`` is intentionally a non-blocking best effort.  A full queue,
    permission error or disk-full only increments a redacted health counter.
    It never delays latest publication, inference or Tk rendering.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_receipts: int = MAX_RECEIPTS_PER_SESSION,
        max_bytes: int = MAX_BYTES_PER_SESSION,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self.root = Path(root) / "fact_receipts"
        self.max_receipts = max(1, int(max_receipts))
        self.max_bytes = max(1024, int(max_bytes))
        self.max_sessions = max(1, int(max_sessions))
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(
            maxsize=self.max_receipts
        )
        self._lock = threading.Lock()
        self._session_binding = ""
        self._rollover_required = False
        self._next_seq = 1
        self._written = 0
        self._bytes = 0
        self._overflowed = False
        self._coverage_reason = ""
        self._health: dict[str, int] = {"enqueued": 0, "written": 0}
        self._worker = threading.Thread(
            target=self._run, name="fact-receipt-writer", daemon=True
        )
        self._worker.start()

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        if isinstance(exc, PermissionError):
            return "permission_denied"
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            return "disk_full"
        return "write_failed"

    def _session_dir(self, binding: str) -> Path:
        if len(binding) != 64 or any(ch not in _SAFE_SESSION_DIR for ch in binding):
            raise ValueError("invalid receipt session binding")
        return self.root / binding

    def _record_health(self, key: str) -> None:
        with self._lock:
            self._health[key] = self._health.get(key, 0) + 1

    def health_snapshot(self) -> dict[str, int]:
        """Expose fixed, deidentified writer health without reading the filesystem."""

        with self._lock:
            return dict(sorted(self._health.items()))

    def enqueue(self, *, session_id: str, receipt: Mapping[str, Any]) -> bool:
        """Queue a deidentified fact. Duplicate/current ownership is checked by caller."""
        binding = fact_receipt_session_binding(session_id)
        if binding is None:
            self._record_health("invalid_session")
            return False
        try:
            clean = dict(receipt)
            # The raw identifier is never allowed past this boundary.
            clean.pop("session_id", None)
            if not _support_safe(clean):
                raise ValueError("unsafe receipt key")
            _canonical_bytes(clean)
        except Exception:
            self._record_health("invalid_receipt")
            return False
        with self._lock:
            if binding != self._session_binding:
                self._session_binding = binding
                self._rollover_required = True
                self._next_seq = 1
                self._written = 0
                self._bytes = 0
                self._overflowed = False
                self._coverage_reason = ""
            if self._overflowed:
                self._health["retention_truncated"] = self._health.get("retention_truncated", 0) + 1
                return False
            payload = dict(clean)
            payload.update(
                {
                    "schema": SCHEMA,
                    "session_binding": binding,
                    "receipt_seq": self._next_seq,
                    "recorded_at": time.time(),
                }
            )
            try:
                self._queue.put_nowait((binding, payload))
            except queue.Full:
                self._health["queue_full"] = self._health.get("queue_full", 0) + 1
                return False
            self._next_seq += 1
            self._health["enqueued"] = self._health.get("enqueued", 0) + 1
            return True

    def _write_rollover_pending(self, binding: str) -> bool:
        with self._lock:
            if binding != self._session_binding or not self._rollover_required:
                return False
            payload = {
                "schema": "fact_receipt_manifest_v1",
                "session_binding": binding,
                "coverage": "partial",
                "coverage_reason": "session_rollover_pending",
                "receipt_count": 0,
                "bytes": 0,
                "next_receipt_seq": self._next_seq,
                "writer_health": dict(sorted(self._health.items())),
            }
        _atomic_json(self.root / "current_manifest.json", payload)
        with self._lock:
            if binding == self._session_binding:
                self._rollover_required = False
        return True

    def _write_manifest(self, binding: str) -> None:
        with self._lock:
            if binding != self._session_binding:
                return
            coverage_reason = (
                "max_bytes_or_count" if self._overflowed else self._coverage_reason
            )
            payload = {
                "schema": "fact_receipt_manifest_v1",
                "session_binding": binding,
                "coverage": "partial" if coverage_reason else "complete",
                "coverage_reason": coverage_reason,
                "receipt_count": self._written,
                "bytes": self._bytes,
                "next_receipt_seq": self._next_seq,
                "writer_health": dict(sorted(self._health.items())),
            }
        _atomic_json(self._session_dir(binding) / "manifest.json", payload)
        _atomic_json(
            self.root / "writer_health.json",
            {
                "schema": "fact_receipt_health_v1",
                "session_binding": binding,
                "writer_health": payload["writer_health"],
            },
        )
        # The root manifest is the publication pointer and is written last.
        with self._lock:
            if binding != self._session_binding:
                return
        _atomic_json(self.root / "current_manifest.json", payload)

    def _prune_completed_sessions(self, current: str) -> None:
        try:
            candidates = [
                path for path in self.root.iterdir()
                if path.is_dir() and path.name != current and len(path.name) == 64
                and all(ch in _SAFE_SESSION_DIR for ch in path.name)
            ]
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for path in candidates[self.max_sessions - 1 :]:
                # Only bounded, writer-owned literal leaves are eligible.
                for leaf in path.glob("receipt-*.json"):
                    leaf.unlink(missing_ok=True)
                for name in ("manifest.json",):
                    (path / name).unlink(missing_ok=True)
                path.rmdir()
        except OSError:
            self._record_health("rotation_failed")

    def _write_one(self, binding: str, payload: dict[str, Any]) -> None:
        encoded = _canonical_bytes(payload)
        with self._lock:
            is_current = binding == self._session_binding
            overflow = self._overflowed
            would_overflow = (
                self._written >= self.max_receipts
                or self._bytes + len(encoded) > self.max_bytes
            )
            if not is_current:
                self._health["stale_session"] = self._health.get("stale_session", 0) + 1
                return
            if overflow or would_overflow:
                self._overflowed = True
                self._health["retention_truncated"] = self._health.get("retention_truncated", 0) + 1
                truncated = True
            else:
                truncated = False
        if truncated:
            self._write_manifest(binding)
            return
        target = self._session_dir(binding) / f"receipt-{int(payload['receipt_seq']):06d}.json"
        if target.exists():
            self._record_health("duplicate_seq")
            return
        _atomic_json(target, payload)
        with self._lock:
            if binding != self._session_binding:
                # It is immutable but no longer current, so export will not select it.
                self._health["written_superseded"] = self._health.get("written_superseded", 0) + 1
            else:
                self._written += 1
                self._bytes += len(encoded)
                self._health["written"] = self._health.get("written", 0) + 1
        self._write_manifest(binding)
        self._prune_completed_sessions(binding)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            binding, payload = item
            try:
                with self._lock:
                    rollover_required = (
                        binding == self._session_binding and self._rollover_required
                    )
                if rollover_required and not self._write_rollover_pending(binding):
                    continue
                self._write_one(binding, payload)
            except Exception as exc:  # receipt durability must never take down live quoting
                error_code = self._error_code(exc)
                with self._lock:
                    self._health[error_code] = self._health.get(error_code, 0) + 1
                    if binding == self._session_binding:
                        self._coverage_reason = "writer_error"

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            self._record_health("close_queue_full")
            # Shutdown may leave a daemon worker behind, but never turns monitor
            # stop into an unbounded I/O wait.
            return
        self._worker.join(timeout=1.0)


def _plain_directory(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _plain_file(path: Path) -> bool:
    info = path.lstat()
    return (
        stat.S_ISREG(info.st_mode)
        and int(getattr(info, "st_nlink", 1)) == 1
        and not bool(getattr(info, "st_file_attributes", 0) & 0x400)
    )


def _json_object(data: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if type(value) is dict else None  # noqa: E721


def current_receipt_bundle_bytes_valid(
    expected_session_id: object,
    files: Mapping[str, bytes],
) -> bool:
    """Validate one pinned bundle against the caller's exact current session."""

    binding = fact_receipt_session_binding(expected_session_id)
    if binding is None or type(files) is not dict or not files:  # noqa: E721
        return False
    current_name = "current_manifest.json"
    health_name = "writer_health.json"
    session_manifest_name = f"{binding}/manifest.json"
    current = _json_object(files.get(current_name, b""))
    session_manifest = _json_object(files.get(session_manifest_name, b""))
    health = _json_object(files.get(health_name, b""))
    if (
        current is None
        or session_manifest is None
        or health is None
        or current != session_manifest
        or current.get("schema") != "fact_receipt_manifest_v1"
        or current.get("session_binding") != binding
        or current.get("coverage_reason") == "session_rollover_pending"
        or type(current.get("receipt_count")) is not int  # noqa: E721
        or not 1 <= current["receipt_count"] <= MAX_RECEIPTS_PER_SESSION
        or health.get("schema") != "fact_receipt_health_v1"
        or health.get("session_binding") != binding
        or type(health.get("writer_health")) is not dict  # noqa: E721
        or health.get("writer_health") != current.get("writer_health")
    ):
        return False
    expected_static = {current_name, health_name, session_manifest_name}
    leaf_prefix = f"{binding}/receipt-"
    leaf_names = sorted(
        name
        for name in files
        if type(name) is str
        and name.startswith(leaf_prefix)
        and name.endswith(".json")
    )
    if set(files) != expected_static | set(leaf_names):
        return False
    if len(leaf_names) != current["receipt_count"]:
        return False
    seen_sequences: set[int] = set()
    for name in leaf_names:
        leaf = _json_object(files[name])
        if (
            leaf is None
            or leaf.get("schema") != SCHEMA
            or leaf.get("session_binding") != binding
            or type(leaf.get("receipt_seq")) is not int  # noqa: E721
            or leaf["receipt_seq"] < 1
            or leaf["receipt_seq"] in seen_sequences
            or not _support_safe(leaf)
        ):
            return False
        seen_sequences.add(leaf["receipt_seq"])
    return True


def current_receipt_bundle_paths(
    log_dir: str | Path,
    *,
    expected_session_id: object = None,
) -> list[Path]:
    """Return only a verified bundle for the caller's exact current session."""

    binding = fact_receipt_session_binding(expected_session_id)
    if binding is None:
        return []
    root = Path(log_dir) / "fact_receipts"
    manifest = root / "current_manifest.json"
    health = root / "writer_health.json"
    session_dir = root / binding
    session_manifest = session_dir / "manifest.json"
    try:
        if (
            not _plain_directory(root)
            or not _plain_file(manifest)
            or not _plain_directory(session_dir)
            or not _plain_file(session_manifest)
            or not _plain_file(health)
        ):
            return []
        leaves = sorted(session_dir.glob("receipt-*.json"))
        if len(leaves) > MAX_RECEIPTS_PER_SESSION or any(
            not _plain_file(path) for path in leaves
        ):
            return []
        paths = [manifest, health, session_manifest, *leaves]
        pinned: dict[str, bytes] = {}
        for path in paths:
            relative = path.relative_to(root).as_posix()
            pinned[relative] = path.read_bytes()
        if not current_receipt_bundle_bytes_valid(expected_session_id, pinned):
            return []
        return paths
    except (OSError, ValueError, TypeError):
        return []
