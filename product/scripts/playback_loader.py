"""Loader for the game's local `.playback` session recordings.

The game (post-2026-06-18 YooAsset build) auto-saves each session under
`%LOCALAPPDATA%Low\\laolin\\BidKing\\<map>_<session>.playback`. Each file is a
sequence of length-prefixed (4-byte little-endian) protobuf messages; every
message is exactly the *state payload* that `fatbeans._parse_state_event`
extracts from a wire frame (field 1 session, 2 map, 3 round, 4 inventory,
5 bids, 6 skill_reveals, 7 public_infos, 8 actions).

We rebuild `FatbeansStateEvent`s from those payloads and wrap them in a
`FatbeansCaptureEvents`, so the existing engine/eval can replay a `.playback`
the same way it replays a fatbeans capture -- WITHOUT the packet/frame layer.

IMPORTANT validity caveat: the local `.playback` trove only covers PREVIOUS
activity runs (sealed-cabin 2521-2530, session-id <~1.40e15) plus normal maps.
It does NOT contain the CURRENT activity (2531+/4531+, session-id ~1.469e15).
Treat ingested samples as low-validity prev-activity references, not current.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import bidking_lab.live.fatbeans as fb  # noqa: E402
from bidking_lab.live.fatbeans import FatbeansCaptureEvents, FatbeansStateEvent  # noqa: E402

# State message ids the fatbeans pipeline recognises (non-settlement vs settlement).
_STATE_MSG_ID = 0x0025
_SETTLE_MSG_ID = 0x002D


def _split_messages(blob: bytes) -> list[bytes]:
    """Split a .playback blob into its length-prefixed payload messages."""
    out: list[bytes] = []
    i = 0
    n = len(blob)
    while i + 4 <= n:
        length = struct.unpack_from("<I", blob, i)[0]
        i += 4
        if length <= 0 or i + length > n:
            break
        out.append(blob[i : i + length])
        i += length
    return out


def _state_from_payload(payload: bytes, sort_id: int) -> FatbeansStateEvent | None:
    fields = fb._parse_fields(payload)
    session_id = fb._text(fb._first(fields, 1))
    map_id = fb._int(fb._first(fields, 2))
    if session_id is None and map_id is None:
        return None
    bids = tuple(
        parsed
        for raw in fb._all(fields, 5)
        if isinstance(raw, bytes)
        for parsed in (fb._parse_player_bid(raw),)
        if parsed is not None
    )
    actions = tuple(
        parsed
        for raw in fb._all(fields, 8)
        if isinstance(raw, bytes)
        for parsed in (fb._parse_action_result(raw),)
        if parsed is not None
    )
    skill_reveals = tuple(
        parsed
        for raw in fb._all(fields, 6)
        if isinstance(raw, bytes)
        for parsed in (fb._parse_skill_reveal(raw),)
        if parsed is not None
    )
    public_infos = tuple(
        parsed
        for raw in fb._all(fields, 7)
        if isinstance(raw, bytes)
        for parsed in (fb._parse_public_info(raw),)
        if parsed is not None
    )
    inventory_items = fb._parse_inventory_items(fb._first(fields, 4))
    # A frame that carries the full settled inventory is the settlement frame.
    message_id = _SETTLE_MSG_ID if inventory_items else _STATE_MSG_ID
    return FatbeansStateEvent(
        sort_id=sort_id,
        capture_time="",
        message_id=message_id,
        session_id=session_id,
        map_id=map_id,
        round_index=fb._int(fb._first(fields, 3)),
        bids=bids,
        action_results=actions,
        skill_reveals=skill_reveals,
        public_infos=public_infos,
        inventory_items=inventory_items,
    )


def parse_playback_capture(path: str | Path) -> FatbeansCaptureEvents:
    """Parse a `.playback` file into FatbeansCaptureEvents (states only)."""
    blob = Path(path).read_bytes()
    states = []
    for idx, payload in enumerate(_split_messages(blob), start=1):
        ev = _state_from_payload(payload, sort_id=idx)
        if ev is not None:
            states.append(ev)
    return FatbeansCaptureEvents(
        packets=(), frames=(), sends=(), states=tuple(states), statuses=()
    )


def settlement_truth(events: FatbeansCaptureEvents) -> dict[str, int]:
    """Deepest settled inventory -> {total_cells,total_items,q6_count,...}."""
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        items = state.inventory_items
        return {
            "total_items": len(items),
            "total_cells": sum(int(it.cells or 0) for it in items),
            "q6_count": sum(1 for it in items if int(it.quality or 0) == 6),
            "q5_count": sum(1 for it in items if int(it.quality or 0) == 5),
        }
    return {}


def map_id_of(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.map_id is not None:
            return int(state.map_id)
    return None
