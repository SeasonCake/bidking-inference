"""Per-stream TCP sequence diagnostics: is the capture LOSING bytes, or just reordering them?

WHY THIS EXISTS — the wedge's old root-cause story is falsified
--------------------------------------------------------------
GameFrameGate wedges were long attributed to "desync locked onto a FALSE length prefix"
(see GameFrameGate.STALE_BUFFER_SECONDS). The 0711 hotfix1 diag packages (13 packages,
30 discard-resyncs) refute that in 27 of 30 cases: the wedged buffer holds a REAL frame
header — valid length, monotonic counter, packet_tag inside the observed live range, and a
msgid the gate routinely parses-and-ignores (0x5 / 0x179) — and is short by only 45–9,990
bytes. Example: header declares 20,093 B, buffer holds 10,103 B. The frame's TAIL NEVER
ARRIVED. (The remaining 3 ARE false prefixes, with absurd lengths and protobuf-garbage in
the msgid slot — most likely a downstream CONSEQUENCE of an earlier missing-byte desync.)

That distinction matters because it rules out reordering as the primary mechanism:
GameFrameGate concatenates with a naive ``buffer.extend(payload)``, so out-of-order bytes
still land in the buffer — the frame would complete (with a corrupt body), not wedge. A
buffer that NEVER reaches frame_len means those bytes were never captured AT ALL. And since
the game client itself renders the data fine, the bytes did cross the NIC — TCP guarantees
that. So the loss is inside OUR capture pipeline, not on the wire.

kernel_drop (pcap ps_drop) and queue_full_drops are 0 in every one of those packages, which
rules out the two loss paths that were already counted. This module counts what nobody was
counting, by tracking each TCP half-stream's sequence space:

    gap      seq > expected  -> observed a forward jump; it may be a real hole OR reordering
    overlap  seq < expected  -> retransmit / late segment; it may fill an earlier jump

Read the counters as a decision table:
    unresolved_gap_bytes > 0 -> bytes are still absent at the observation boundary.
    gaps > 0 but unresolved=0-> every forward jump was later filled; this was reordering.
    both 0 across a wedge    -> the bytes reached the gate and the gate mishandled them.

The npcap monitor runs TWO of these, one BEFORE the cross-adapter dedup and one AFTER, so a
single session separates "npcap never gave us the bytes" from "our own dedup ate them":
identical gap counts exonerate the dedup; more gaps after it convict it.

Strictly read-only: never drops, reorders, rewrites, or delays a packet. Kill switch
BIDKING_DISABLE_STREAM_DIAG=1 skips construction entirely at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# TCP sequence numbers are 32-bit and wrap. Treat a forward delta in the low half of the
# space as a real gap and anything beyond as a backward move (retransmit/reorder), which is
# the standard serial-number comparison (RFC 1982) and keeps a wrap from reading as a 4GB gap.
_SEQ_MOD = 1 << 32
_SEQ_HALF = 1 << 31

# Per-stream state is dropped once a capture has seen this many distinct half-streams, so a
# long session on a busy box cannot grow the tracker without bound. Game traffic uses very
# few streams; this is purely a safety cap.
_MAX_STREAMS = 64

StreamKey = tuple[str, int, str, int]


@dataclass
class StreamSeqDiag:
    """Track the TCP sequence space of each captured half-stream. Read-only."""

    label: str = ""
    packets: int = 0
    gaps: int = 0
    gap_bytes: int = 0
    overlaps: int = 0
    max_gap_bytes: int = 0
    filled_gap_bytes: int = 0
    # Size of the MOST RECENT gap. Callers logging a gap must report this, not max_gap_bytes —
    # the max is a session-wide high-water mark and would mislabel every later gap with it.
    last_gap_bytes: int = 0
    _next_seq: dict[StreamKey, int] = field(default_factory=dict)
    # Normalized half-open sequence ranges that were jumped over and have not yet been seen.
    # Ranges are split at the 32-bit wrap, so every tuple obeys start < end.
    _holes: dict[StreamKey, list[tuple[int, int]]] = field(default_factory=dict)

    @staticmethod
    def _parts(seq: int, length: int) -> list[tuple[int, int]]:
        if length <= 0:
            return []
        start = seq % _SEQ_MOD
        end = start + length
        if end <= _SEQ_MOD:
            return [(start, end)]
        return [(start, _SEQ_MOD), (0, end % _SEQ_MOD)]

    def _add_hole(self, key: StreamKey, seq: int, length: int) -> None:
        holes = self._holes.setdefault(key, [])
        holes.extend(self._parts(seq, length))
        holes.sort()
        merged: list[tuple[int, int]] = []
        for start, end in holes:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self._holes[key] = merged

    def _cover_holes(self, key: StreamKey, seq: int, length: int) -> None:
        holes = self._holes.get(key)
        if not holes:
            return
        before = sum(end - start for start, end in holes)
        for cover_start, cover_end in self._parts(seq, length):
            remaining: list[tuple[int, int]] = []
            for start, end in holes:
                if cover_end <= start or cover_start >= end:
                    remaining.append((start, end))
                    continue
                if start < cover_start:
                    remaining.append((start, cover_start))
                if cover_end < end:
                    remaining.append((cover_end, end))
            holes = remaining
        after = sum(end - start for start, end in holes)
        self.filled_gap_bytes += before - after
        if holes:
            self._holes[key] = holes
        else:
            self._holes.pop(key, None)

    @property
    def unresolved_gaps(self) -> int:
        return sum(len(holes) for holes in self._holes.values())

    @property
    def unresolved_gap_bytes(self) -> int:
        return sum(end - start for holes in self._holes.values() for start, end in holes)

    def observe(self, key: StreamKey, seq: int, payload_len: int) -> str:
        """Classify one captured segment. Returns 'first' | 'inorder' | 'gap' | 'overlap'.

        seq == 0 means the backend does not supply sequence numbers (WinDivert path) — such a
        packet is ignored rather than treated as a wrap to 0.
        """
        if not seq or payload_len <= 0:
            return "inorder"
        self.packets += 1
        # A late/out-of-order segment can close a previously observed forward jump. Historical
        # gap counters remain useful as reorder pressure; only unresolved_* means still missing.
        self._cover_holes(key, seq, payload_len)
        expected = self._next_seq.get(key)
        end = (seq + payload_len) % _SEQ_MOD
        if expected is None:
            if len(self._next_seq) >= _MAX_STREAMS:
                self._next_seq.clear()
                self._holes.clear()
            self._next_seq[key] = end
            return "first"
        delta = (seq - expected) % _SEQ_MOD
        if delta == 0:
            self._next_seq[key] = end
            return "inorder"
        if delta < _SEQ_HALF:
            # seq jumped past expected. Record an open range; a later segment may still fill it.
            self.gaps += 1
            self.gap_bytes += delta
            self.last_gap_bytes = delta
            self.max_gap_bytes = max(self.max_gap_bytes, delta)
            self._add_hole(key, expected, delta)
            self._next_seq[key] = end
            return "gap"
        # seq sits BEHIND the high-water mark: a retransmit or a reordered segment. Do not rewind
        # the expectation. A larger retransmit may nevertheless extend beyond the high-water
        # mark; advance to its end or the next in-order segment would look like a false gap.
        self.overlaps += 1
        end_delta = (end - expected) % _SEQ_MOD
        if 0 < end_delta < _SEQ_HALF:
            self._next_seq[key] = end
        return "overlap"

    def as_dict(self) -> dict[str, int]:
        return {
            "packets": self.packets,
            "gaps": self.gaps,
            "gap_bytes": self.gap_bytes,
            "max_gap_bytes": self.max_gap_bytes,
            "overlaps": self.overlaps,
            "unresolved_gaps": self.unresolved_gaps,
            "unresolved_gap_bytes": self.unresolved_gap_bytes,
            "filled_gap_bytes": self.filled_gap_bytes,
        }
