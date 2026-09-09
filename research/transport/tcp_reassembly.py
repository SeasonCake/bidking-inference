"""Bounded TCP half-stream reassembly; extracted from the historical v0.3.0 helper."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


SEQ_MOD = 1 << 32
SEQ_HALF = 1 << 31
FlowKey = tuple[str, int, str, int]


@dataclass
class _Segment:
    start: int
    data: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.data)


@dataclass
class _FlowState:
    anchor_seq: int
    next_pos: int = 0
    pending: list[_Segment] = field(default_factory=list)
    buffered_bytes: int = 0
    last_seen: float = 0.0

    @property
    def next_seq(self) -> int:
        return (self.anchor_seq + self.next_pos) % SEQ_MOD


class TcpSeqReassembler:
    """Reassemble independent TCP half-streams with bounded memory.

    ``feed`` returns only the newly contiguous bytes. An out-of-order segment is
    held until its gap arrives; retransmissions and already-consumed overlap
    return ``b""``. Sequence arithmetic follows the 32-bit serial number space.
    """

    def __init__(
        self,
        *,
        stale_seconds: float = 30.0,
        max_streams: int = 64,
        per_stream_buffer_bytes: int = 2 * 1024 * 1024,
        global_buffer_bytes: int = 8 * 1024 * 1024,
        event_limit: int = 64,
    ) -> None:
        if stale_seconds <= 0:
            raise ValueError("stale_seconds must be positive")
        if max_streams <= 0:
            raise ValueError("max_streams must be positive")
        if per_stream_buffer_bytes <= 0 or global_buffer_bytes <= 0:
            raise ValueError("buffer caps must be positive")
        self.stale_seconds = float(stale_seconds)
        self.max_streams = int(max_streams)
        self.per_stream_buffer_bytes = int(per_stream_buffer_bytes)
        self.global_buffer_bytes = int(global_buffer_bytes)
        self._flows: dict[FlowKey, _FlowState] = {}
        self._buffered_bytes = 0
        self._salt = secrets.token_bytes(16)
        self._events: deque[dict[str, Any]] = deque(maxlen=max(0, int(event_limit)))

        self.packets = 0
        self.output_chunks = 0
        self.output_bytes = 0
        self.gap_segments = 0
        self.duplicate_segments = 0
        self.overlap_segments = 0
        self.overlap_bytes = 0
        self.stale_flows = 0
        self.evicted_flows = 0
        self.reset_flows = 0
        self.cap_drops = 0
        self.cap_drop_bytes = 0

    def _flow_digest(self, key: FlowKey) -> str:
        raw = "\x1f".join(str(part) for part in key).encode("utf-8", errors="replace")
        return hashlib.blake2s(raw, key=self._salt, digest_size=6).hexdigest()

    def _event(
        self,
        key: FlowKey,
        *,
        seq: int,
        length: int,
        verdict: str,
        emitted: int,
    ) -> None:
        if self._events.maxlen == 0:
            return
        self._events.append(
            {
                "flow": self._flow_digest(key),
                "seq": int(seq) % SEQ_MOD,
                "length": int(length),
                "verdict": verdict,
                "emitted": int(emitted),
                "buffered": self._buffered_bytes,
            }
        )

    @staticmethod
    def _relative_start(state: _FlowState, seq: int) -> int:
        delta = ((int(seq) % SEQ_MOD) - state.next_seq) % SEQ_MOD
        if delta >= SEQ_HALF:
            delta -= SEQ_MOD
        return state.next_pos + delta

    @staticmethod
    def _uncovered_pieces(
        pending: list[_Segment], start: int, data: bytes
    ) -> tuple[list[_Segment], int]:
        pieces = [_Segment(start, data)]
        overlap = 0
        for existing in pending:
            remaining: list[_Segment] = []
            for piece in pieces:
                if piece.end <= existing.start or piece.start >= existing.end:
                    remaining.append(piece)
                    continue
                overlap += min(piece.end, existing.end) - max(piece.start, existing.start)
                if piece.start < existing.start:
                    keep = existing.start - piece.start
                    remaining.append(_Segment(piece.start, piece.data[:keep]))
                if piece.end > existing.end:
                    skip = existing.end - piece.start
                    remaining.append(_Segment(existing.end, piece.data[skip:]))
            pieces = remaining
            if not pieces:
                break
        return pieces, overlap

    @staticmethod
    def _coalesce(pending: list[_Segment]) -> list[_Segment]:
        if not pending:
            return []
        ordered = sorted(pending, key=lambda segment: segment.start)
        merged = [ordered[0]]
        for segment in ordered[1:]:
            previous = merged[-1]
            if previous.end == segment.start:
                merged[-1] = _Segment(previous.start, previous.data + segment.data)
            else:
                merged.append(segment)
        return merged

    def _drop_flow(self, key: FlowKey) -> bool:
        state = self._flows.pop(key, None)
        if state is None:
            return False
        self._buffered_bytes -= state.buffered_bytes
        return True

    def sweep(self, *, now: float | None = None) -> int:
        """Discard stale state without emitting any held bytes."""

        current = time.monotonic() if now is None else float(now)
        stale = [
            key
            for key, state in self._flows.items()
            if current - state.last_seen >= self.stale_seconds
        ]
        for key in stale:
            self._drop_flow(key)
        self.stale_flows += len(stale)
        return len(stale)

    def reset(self, key: FlowKey | None = None) -> int:
        """Reset one stream or the whole capture-session state."""

        if key is not None:
            removed = 1 if self._drop_flow(key) else 0
        else:
            removed = len(self._flows)
            self._flows.clear()
            self._buffered_bytes = 0
        self.reset_flows += removed
        return removed

    def _new_state(self, key: FlowKey, seq: int, now: float) -> _FlowState:
        self.sweep(now=now)
        if len(self._flows) >= self.max_streams:
            oldest_key = min(self._flows, key=lambda item: self._flows[item].last_seen)
            self._drop_flow(oldest_key)
            self.evicted_flows += 1
        state = _FlowState(anchor_seq=int(seq) % SEQ_MOD, last_seen=now)
        self._flows[key] = state
        return state

    def _insert(
        self, state: _FlowState, start: int, data: bytes
    ) -> tuple[bool, int, int]:
        pieces, pending_overlap = self._uncovered_pieces(state.pending, start, data)
        added = sum(len(piece.data) for piece in pieces)
        if added == 0:
            return True, 0, pending_overlap
        if (
            state.buffered_bytes + added > self.per_stream_buffer_bytes
            or self._buffered_bytes + added > self.global_buffer_bytes
        ):
            self.cap_drops += 1
            self.cap_drop_bytes += added
            return False, 0, pending_overlap
        state.pending = self._coalesce(state.pending + pieces)
        state.buffered_bytes += added
        self._buffered_bytes += added
        return True, added, pending_overlap

    def _drain(self, state: _FlowState) -> bytes:
        chunks: list[bytes] = []
        while state.pending and state.pending[0].start <= state.next_pos:
            segment = state.pending.pop(0)
            state.buffered_bytes -= len(segment.data)
            self._buffered_bytes -= len(segment.data)
            if segment.end <= state.next_pos:
                continue
            trim = max(0, state.next_pos - segment.start)
            chunk = segment.data[trim:]
            chunks.append(chunk)
            state.next_pos += len(chunk)
        return b"".join(chunks)

    def feed(
        self,
        key: FlowKey,
        seq: int,
        payload: bytes,
        *,
        now: float | None = None,
    ) -> bytes:
        data = bytes(payload)
        if not data:
            return b""
        current = time.monotonic() if now is None else float(now)
        self.packets += 1
        state = self._flows.get(key)
        if state is not None and current - state.last_seen >= self.stale_seconds:
            self._drop_flow(key)
            self.stale_flows += 1
            state = None
        if state is None:
            state = self._new_state(key, seq, current)
        state.last_seen = current
        start = self._relative_start(state, seq)
        end = start + len(data)
        consumed_overlap = 0
        verdict = "inorder" if start == state.next_pos else "gap"

        if end <= state.next_pos:
            self.duplicate_segments += 1
            self.overlap_segments += 1
            self.overlap_bytes += len(data)
            self._event(
                key, seq=seq, length=len(data), verdict="duplicate", emitted=0
            )
            return b""
        if start < state.next_pos:
            consumed_overlap = state.next_pos - start
            data = data[consumed_overlap:]
            start = state.next_pos
            verdict = "overlap_extend"

        accepted, added, pending_overlap = self._insert(state, start, data)
        total_overlap = consumed_overlap + pending_overlap
        if total_overlap:
            self.overlap_segments += 1
            self.overlap_bytes += total_overlap
        if not accepted:
            self._event(key, seq=seq, length=len(payload), verdict="cap_drop", emitted=0)
            return b""
        if start > state.next_pos and added:
            self.gap_segments += 1
        if added == 0:
            self.duplicate_segments += 1
            verdict = "duplicate_pending"

        output = self._drain(state)
        if output:
            self.output_chunks += 1
            self.output_bytes += len(output)
            if verdict == "gap":
                verdict = "gap_filled"
        self._event(
            key,
            seq=seq,
            length=len(payload),
            verdict=verdict,
            emitted=len(output),
        )
        return output

    @staticmethod
    def _flow_gap_bytes(state: _FlowState) -> int:
        cursor = state.next_pos
        gaps = 0
        for segment in state.pending:
            if segment.start > cursor:
                gaps += segment.start - cursor
            cursor = max(cursor, segment.end)
        return gaps

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "packets": self.packets,
            "output_chunks": self.output_chunks,
            "output_bytes": self.output_bytes,
            "active_streams": len(self._flows),
            "buffered_bytes": self._buffered_bytes,
            "unresolved_gap_bytes": sum(
                self._flow_gap_bytes(state) for state in self._flows.values()
            ),
            "gap_segments": self.gap_segments,
            "duplicate_segments": self.duplicate_segments,
            "overlap_segments": self.overlap_segments,
            "overlap_bytes": self.overlap_bytes,
            "stale_flows": self.stale_flows,
            "evicted_flows": self.evicted_flows,
            "reset_flows": self.reset_flows,
            "cap_drops": self.cap_drops,
            "cap_drop_bytes": self.cap_drop_bytes,
            "recent_segments": list(self._events),
        }


__all__ = [
    "FlowKey",
    "SEQ_HALF",
    "SEQ_MOD",
    "TcpSeqReassembler",
]
