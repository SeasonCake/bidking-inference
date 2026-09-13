"""Deterministic synthetic consumer for equivalent encoded and typed producers."""

from __future__ import annotations

from collections import Counter
import copy
import json
from typing import Any


class EpochConsumer:
    """The owner selects the epoch; a late event cannot activate its own epoch."""

    def __init__(self, epoch: int):
        self.epoch = self._integer(epoch)
        self.facts: dict[str, Any] = {}
        self.last_sequence: dict[str, int] = {}
        self.last_payload: dict[str, str] = {}
        self.counts: Counter[str] = Counter()
        self.closed = False

    @staticmethod
    def _integer(value: Any) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("Epoch and sequence must be nonnegative integers")
        return value

    def activate(self, epoch: int) -> None:
        epoch = self._integer(epoch)
        if self.closed or epoch <= self.epoch:
            raise ValueError("Activation needs a newer epoch on an open consumer")
        self.epoch = epoch
        self.facts.clear()
        self.last_sequence.clear()
        self.last_payload.clear()

    def accept(self, event: dict[str, Any]) -> str:
        def finish(status: str) -> str:
            self.counts[status] += 1
            return status

        if self.closed:
            return finish("closed")
        try:
            if (
                not isinstance(event, dict)
                or event.get("schema_version") != 1
                or type(event.get("schema_version")) is not int
            ):
                return finish("schema")
            producer = event["producer_id"]
            if type(producer) is not str or not producer:
                return finish("schema")
            epoch, sequence = (
                self._integer(event["epoch"]),
                self._integer(event["sequence"]),
            )
            facts = event["facts"]
            if not isinstance(facts, dict) or set(facts) - {"count", "cells"}:
                return finish("schema")
            if any(
                value is not None and (type(value) is not int or value < 0)
                for value in facts.values()
            ):
                return finish("schema")
        except (KeyError, ValueError):
            return finish("schema")
        if epoch != self.epoch:
            return finish("stale_epoch" if epoch < self.epoch else "future_epoch")
        payload = json.dumps(facts, sort_keys=True, separators=(",", ":"))
        previous = self.last_sequence.get(producer, -1)
        if sequence < previous:
            return finish("stale_sequence")
        if sequence == previous:
            return finish(
                "duplicate" if payload == self.last_payload[producer] else "conflict"
            )
        self.last_sequence[producer] = sequence
        self.last_payload[producer] = payload
        # Snapshot contract: absence clears the old field; null remains an explicit unknown.
        self.facts = copy.deepcopy(facts)
        return finish("accepted")

    def close(self) -> None:
        self.closed = True


class BoundedQueue:
    """Single-threaded teaching queue with explicit reject-new overflow policy."""

    def __init__(self, capacity: int):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Capacity must be a positive integer")
        from collections import deque

        self._items = deque()
        self.capacity = capacity
        self.rejected = 0

    def put(self, event: dict[str, Any]) -> bool:
        if len(self._items) >= self.capacity:
            self.rejected += 1
            return False
        self._items.append(copy.deepcopy(event))
        return True

    def drain(self, consumer: EpochConsumer) -> list[str]:
        return [consumer.accept(self._items.popleft()) for _ in range(len(self._items))]
