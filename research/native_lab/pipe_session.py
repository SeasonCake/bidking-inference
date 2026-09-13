"""Bounded client for this lab's own JSONL process, with retained handles and EOF."""

from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class PipeSession:
    def __init__(self, executable: Path, mode: str, epoch: int, env: dict[str, str]):
        self.process = subprocess.Popen(
            [str(executable), "server", mode, str(epoch)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.inbox = queue.Queue(maxsize=64)
        self.stop_reader = threading.Event()
        self.closed_cleanly = False

        def deliver(value):
            while not self.stop_reader.is_set():
                try:
                    self.inbox.put(value, timeout=0.1)
                    return
                except queue.Full:
                    pass

        def read():
            try:
                while not self.stop_reader.is_set():
                    line = self.process.stdout.readline(4097)
                    if not line:
                        deliver(None)
                        return
                    if len(line) > 4096:
                        raise ValueError("JSONL line exceeds fixture contract")
                    deliver(json.loads(line))
            except Exception as error:
                deliver(error)

        self.reader = threading.Thread(
            target=read, name="native-lab-json-reader", daemon=True
        )
        self.reader.start()

    def _next(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Native fixture response deadline")
        try:
            item = self.inbox.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("Native fixture response deadline") from error
        if item is None:
            raise RuntimeError("Native fixture closed before response")
        if isinstance(item, Exception):
            raise item
        return item

    def batch(
        self, repeats: int = 1, slow_consumer: bool = False
    ) -> tuple[list[dict], dict]:
        if type(repeats) is not int or not 1 <= repeats <= 1000:
            raise ValueError("Use 1..1000 eight-event batches")
        self.process.stdin.write(f"{repeats}\n".encode("ascii"))
        self.process.stdin.flush()
        deadline = time.monotonic() + 10
        queue_full_observed = False
        if slow_consumer:
            # Deliberately stop consuming until the owned 64-line reader queue fills.
            fill_deadline = min(deadline, time.monotonic() + 1)
            while not self.inbox.full() and time.monotonic() < fill_deadline:
                time.sleep(0.005)
            queue_full_observed = self.inbox.full()
        rows = []
        while True:
            item = self._next(deadline)
            if item.get("control") == "batch_end":
                if item.get("events") != len(rows) or len(rows) != repeats * 8:
                    raise ValueError("Native fixture lost/duplicated an event")
                item["bounded_queue_full_observed"] = queue_full_observed
                return rows, item
            if "control" in item or len(rows) >= repeats * 8:
                raise ValueError("Unexpected native fixture response")
            rows.append(item)

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.stdin.write(b"quit\n")
                self.process.stdin.flush()
                item = self._next(time.monotonic() + 5)
                if item != {"control": "closed"}:
                    raise ValueError("Missing native close acknowledgement")
                self.process.wait(timeout=5)
                self.closed_cleanly = self.process.returncode == 0
        finally:
            self.stop_reader.set()
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process.stdin.close()
            self.reader.join(timeout=3)
            self.process.stdout.close()
            if self.reader.is_alive():
                raise RuntimeError("Owned JSON reader did not exit")
