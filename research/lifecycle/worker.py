"""Requesting stop and proving a thread was reaped are different operations."""

from __future__ import annotations

import threading
from typing import Callable


class WorkerOwner:
    def __init__(self, work: Callable[[threading.Event], None]):
        self.stop_requested = threading.Event()
        self.finished = threading.Event()
        self.error_type: str | None = None
        self.reaped = False

        def run():
            try:
                work(self.stop_requested)
            except BaseException as error:
                self.error_type = type(error).__name__
            finally:
                self.finished.set()

        self.thread = threading.Thread(target=run, name="synthetic-owned-worker")
        self.thread.start()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def reap(self, timeout: float) -> dict:
        if not 0 <= timeout <= 60:
            raise ValueError("Use a finite wait between 0 and 60 seconds")
        if threading.current_thread() is self.thread:
            raise RuntimeError("A worker cannot acknowledge its own thread exit")
        self.thread.join(timeout)
        self.reaped = not self.thread.is_alive()
        # Retain the handle even after a partial timeout; do not lose the owner.
        return {
            "stop_requested": self.stop_requested.is_set(),
            "finished": self.finished.is_set(),
            "reaped": self.reaped,
            "error_type": self.error_type,
        }
