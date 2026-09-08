"""Reusable, synthetic failure workflows: snapshots, refreshes and staged saves.

No network, account, GUI, private data or application-specific schema is used.
Run this file directly for deterministic examples. Helpers are reference code,
not additions to the installed auction_inference API.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Callable


class SnapshotWriteError(OSError):
    """Keep the write error even when cleanup also fails."""

    def __init__(self, operation_error: Exception, cleanup_error: OSError | None):
        super().__init__(f"snapshot write failed: {type(operation_error).__name__}")
        self.operation_error = operation_error
        self.cleanup_error = cleanup_error


def write_json_snapshot(
    destination: Path,
    value: object,
    *,
    replace: Callable[[Path, Path], None] = os.replace,
) -> None:
    """Replace one small JSON file; never remove the old target on failure.

    Serialization happens before touching disk. The caller supplies an existing
    parent directory. This is same-directory replacement, not a claim about
    power-loss durability, network filesystems or concurrent writers.
    """
    encoded = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(encoded)
        replace(temporary, destination)
    except Exception as error:
        cleanup_error = None
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup:
            cleanup_error = cleanup
        raise SnapshotWriteError(error, cleanup_error) from error


@dataclass(frozen=True)
class RefreshTicket:
    number: int
    revision: str
    stale: bool


class RefreshGate:
    """Single-event-loop request gate; the caller determines freshness.

    A matching accepted result suppresses repeated ticks. Failure permits retry.
    Tickets are opaque local handles: an old or foreign ticket cannot complete
    the current request, including an A -> B -> A revision sequence.
    """

    def __init__(self) -> None:
        self._identity: tuple[str, bool] | None = None
        self._pending: RefreshTicket | None = None
        self._accepted: tuple[str, bool] | None = None
        self.submitted = 0

    def request(self, revision: str, *, stale: bool) -> RefreshTicket | None:
        if type(revision) is not str or not revision:
            raise ValueError("revision must be a non-empty string")
        if type(stale) is not bool:
            raise TypeError("stale must be a boolean")
        identity = (revision, stale)
        if identity != self._identity:
            self._identity, self._pending, self._accepted = identity, None, None
        if self._pending is not None or self._accepted == identity:
            return None
        self.submitted += 1
        self._pending = RefreshTicket(self.submitted, revision, stale)
        return self._pending

    def accept(self, ticket: RefreshTicket) -> bool:
        if ticket is not self._pending or ticket is None:
            return False
        self._accepted, self._pending = self._identity, None
        return True

    def fail(self, ticket: RefreshTicket) -> bool:
        if ticket is not self._pending or ticket is None:
            return False
        self._pending = None
        return True


@dataclass(frozen=True)
class SaveOutcome:
    remote: str
    local: str
    recovery: str
    error_type: str | None = None
    recovery_error_type: str | None = None


def _local_step(step: Callable[[], None]) -> None:
    if step() is not None:
        raise TypeError("local steps must return None or raise")


def save_in_stages(
    accept_remote: Callable[[], bool],
    save_local: Callable[[], None],
    restore_local: Callable[[], None],
) -> SaveOutcome:
    """Report independent outcomes without automatically resending a request.

    The remote callback must return an actual bool. Local callbacks return None
    on success and raise on failure. Adapters, persistence across process death
    and reconciliation of uncertain remote outcomes are outside this example.
    """
    try:
        accepted = accept_remote()
        if type(accepted) is not bool:
            raise TypeError("remote acknowledgement must be a boolean")
    except Exception as error:
        return SaveOutcome(
            "unknown", "not_attempted", "not_needed", type(error).__name__
        )
    if not accepted:
        return SaveOutcome("rejected", "not_attempted", "not_needed")
    try:
        _local_step(save_local)
    except Exception as error:
        try:
            _local_step(restore_local)
        except Exception as recovery_error:
            return SaveOutcome(
                "accepted",
                "failed",
                "failed",
                type(error).__name__,
                type(recovery_error).__name__,
            )
        return SaveOutcome("accepted", "failed", "restored", type(error).__name__)
    return SaveOutcome("accepted", "committed", "not_needed")


def _atomic_demo(fault: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="inference-snapshot-") as directory:
        root = Path(directory)
        target, sibling = root / "state.json", root / "keep.txt"
        target.write_text('{"revision": 1}\n', encoding="utf-8")
        sibling.write_text("keep", encoding="utf-8")
        value = {"revision": 2, "label": "demo"}
        error_type = None

        def locked(_source: Path, _destination: Path) -> None:
            raise PermissionError("synthetic replacement failure")

        try:
            write_json_snapshot(
                target,
                {"bad": object()} if fault == "encode" else value,
                replace=locked if fault == "replace" else os.replace,
            )
        except (TypeError, SnapshotWriteError) as error:
            cause = (
                error.operation_error
                if isinstance(error, SnapshotWriteError)
                else error
            )
            error_type = type(cause).__name__
        return {
            "target": json.loads(target.read_text(encoding="utf-8")),
            "sibling_preserved": sibling.read_text(encoding="utf-8") == "keep",
            "temporary_files": len(list(root.glob(".state.json.*.tmp"))),
            "error_type": error_type,
        }


def _refresh_demo(late: bool) -> dict[str, object]:
    gate = RefreshGate()
    first = gate.request("document-a", stale=False)
    assert first is not None
    if late:
        middle = gate.request("document-b", stale=False)
        current = gate.request("document-a", stale=False)
        assert middle is not None and current is not None
        return {
            "old_applied": gate.accept(first),
            "middle_applied": gate.accept(middle),
            "current_applied": gate.accept(current),
            "submitted": gate.submitted,
        }
    gate.accept(first)
    stale = gate.request("document-a", stale=True)
    assert stale is not None
    gate.accept(stale)
    extra = sum(gate.request("document-a", stale=True) is not None for _ in range(10))
    return {"submitted": gate.submitted, "extra_requests_after_stale_success": extra}


def _staged_demo(fault: str) -> dict[str, object]:
    state = {"revision": 1, "remote_calls": 0}

    def remote() -> bool:
        state["remote_calls"] += 1
        if fault == "remote":
            raise ConnectionError("synthetic response unavailable")
        return True

    def local() -> None:
        state["revision"] = 2
        if fault in {"local", "restore"}:
            raise OSError("synthetic local save failure")

    def restore() -> None:
        if fault == "restore":
            raise OSError("synthetic recovery failure")
        state["revision"] = 1

    return {"outcome": asdict(save_in_stages(remote, local, restore)), "state": state}


SCENARIOS: dict[str, Callable[[], dict[str, object]]] = {
    "atomic-success": lambda: _atomic_demo("none"),
    "atomic-encode-error": lambda: _atomic_demo("encode"),
    "atomic-replace-error": lambda: _atomic_demo("replace"),
    "refresh-once": lambda: _refresh_demo(False),
    "refresh-late-result": lambda: _refresh_demo(True),
    "staged-success": lambda: _staged_demo("none"),
    "staged-local-error": lambda: _staged_demo("local"),
    "staged-recovery-error": lambda: _staged_demo("restore"),
    "staged-remote-error": lambda: _staged_demo("remote"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    args = parser.parse_args()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    print(
        json.dumps(
            {"synthetic": True, "cases": {name: SCENARIOS[name]() for name in names}},
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
