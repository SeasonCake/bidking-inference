"""Drain owned operations without accepting new work during shutdown."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class Lease:
    token: int
    owner: str


class Coordinator:
    """A process-local coordination contract, not an authorization/security boundary."""

    def __init__(self, on_closed: Callable[[], None] | None = None):
        self._condition = threading.Condition()
        self._leases: dict[int, Lease] = {}
        self._next = 0
        self.state = "running"
        self._on_closed = on_closed

    def acquire(self, owner: str) -> Lease:
        if not isinstance(owner, str) or not owner:
            raise ValueError("An owner is required")
        with self._condition:
            if self.state != "running":
                raise RuntimeError("Shutdown has started; new work is not accepted")
            self._next += 1
            lease = Lease(self._next, owner)
            self._leases[lease.token] = lease
            return lease

    def _close_if_drained(self) -> Callable[[], None] | None:
        if self.state == "draining" and not self._leases:
            self.state = "closed"
            self._condition.notify_all()
            return self._on_closed
        return None

    def complete(self, lease: Lease, owner: str) -> bool:
        with self._condition:
            actual = self._leases.get(lease.token)
            if actual is not lease or actual.owner != owner:
                return False
            del self._leases[lease.token]
            callback = self._close_if_drained()
        if callback:
            callback()  # Outside the lock; a reentrant callback sees 'closed'.
        return True

    def shutdown(self) -> None:
        with self._condition:
            if self.state == "running":
                self.state = "draining"
            callback = self._close_if_drained()
        if callback:
            callback()

    def wait_closed(self, timeout: float) -> bool:
        if not 0 <= timeout <= 60:
            raise ValueError("Use a finite wait between 0 and 60 seconds")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self.state != "closed":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True
