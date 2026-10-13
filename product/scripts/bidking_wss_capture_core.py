"""Privacy boundary for BidKing WebSocket application frames.

The 2026-07-23 client still uses the original big-endian length-prefixed
application frames, but transports them inside WSS.  This module is deliberately
stdlib-only so the same code can run both in the bundled mitmdump addon and in
the normal BidKing monitor process.

The important boundary is client traffic: only the two already-supported
auction action frames are allowed to leave the in-memory sanitizer.  Login,
authentication, account, lobby, and unknown client messages are discarded
before any JSON, stdout, diagnostic, or raw-monitor persistence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal


FRAME_STREAM_PREFIX = "BIDKING_WSS_FRAME "
FLOW_STREAM_PREFIX = "BIDKING_WSS_FLOW "
MAX_FRAME_BYTES = 1_000_000

Direction = Literal["SEND", "REV"]

# Keep these aligned with run_windivert_live_monitor.GameFrameGate.
ALLOWED_SEND_MESSAGE_IDS = frozenset({0x0022, 0x0026})
ALLOWED_REV_MESSAGE_IDS = frozenset({0x0021, 0x0025, 0x0027, 0x002D, 0x0077})


def frame_message_id(direction: Direction, frame: bytes) -> int | None:
    offset = 8 if direction == "SEND" else 12
    if len(frame) < offset + 4:
        return None
    return int.from_bytes(frame[offset : offset + 4], "big")


def allowed_message_ids(direction: Direction) -> frozenset[int]:
    return (
        ALLOWED_SEND_MESSAGE_IDS
        if direction == "SEND"
        else ALLOWED_REV_MESSAGE_IDS
    )


@dataclass(frozen=True)
class SanitizedFrame:
    direction: Direction
    message_id: int
    raw: bytes


class WssFrameSanitizer:
    """Reassemble WSS message bodies and emit only explicitly safe game frames.

    WebSocket messages normally contain one complete application frame, but the
    parser also accepts split or concatenated frames.  Invalid prefixes fail
    closed: the pending bytes are cleared instead of byte-scanning unknown
    client content for a coincidental frame signature.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self.max_frame_bytes = int(max_frame_bytes)
        self._buffers: dict[tuple[str, Direction], bytearray] = {}
        self.accepted: Counter[str] = Counter()
        self.discarded: Counter[str] = Counter()
        self.malformed: Counter[str] = Counter()

    def feed(
        self,
        *,
        flow_id: str,
        direction: Direction,
        payload: bytes,
    ) -> tuple[SanitizedFrame, ...]:
        if direction not in ("SEND", "REV"):
            raise ValueError(f"unsupported direction: {direction!r}")
        if not payload:
            return ()

        key = (str(flow_id), direction)
        buffer = self._buffers.setdefault(key, bytearray())
        buffer.extend(payload)
        emitted: list[SanitizedFrame] = []
        minimum = 12 if direction == "SEND" else 16

        while len(buffer) >= 4:
            frame_length = int.from_bytes(buffer[:4], "big")
            if frame_length < minimum or frame_length > self.max_frame_bytes:
                self.malformed[f"{direction.lower()}_invalid_length"] += 1
                buffer.clear()
                break
            if len(buffer) < frame_length:
                break

            raw = bytes(buffer[:frame_length])
            del buffer[:frame_length]
            message_id = frame_message_id(direction, raw)
            if message_id not in allowed_message_ids(direction):
                self.discarded[f"{direction.lower()}_not_allowlisted"] += 1
                continue

            emitted.append(
                SanitizedFrame(
                    direction=direction,
                    message_id=int(message_id),
                    raw=raw,
                )
            )
            self.accepted[f"{direction.lower()}_0x{message_id:04x}"] += 1

        if not buffer:
            self._buffers.pop(key, None)
        elif len(buffer) > self.max_frame_bytes:
            self.malformed[f"{direction.lower()}_buffer_overflow"] += 1
            self._buffers.pop(key, None)
        return tuple(emitted)

    def close_flow(self, flow_id: str) -> None:
        token = str(flow_id)
        for key in [key for key in self._buffers if key[0] == token]:
            pending = len(self._buffers.pop(key))
            if pending:
                self.malformed[f"{key[1].lower()}_truncated_on_close"] += 1

    def pending_bytes(self) -> int:
        return sum(len(buffer) for buffer in self._buffers.values())
