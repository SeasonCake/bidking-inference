"""mitmdump addon that emits only sanitized BidKing auction frames.

This file intentionally does not save mitmproxy flows.  The parent monitor reads
the prefixed stdout records and ignores every other mitmdump line.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _local_compat_core import (  # noqa: E402
    FRAME_STREAM_PREFIX,
    FLOW_STREAM_PREFIX,
    WssFrameSanitizer,
)


DEFAULT_SERVER_HOST = "release.bid-king.cn"


def _flow_id(flow: Any) -> str:
    return str(getattr(flow, "id", "") or id(flow))


def _flow_host(flow: Any) -> str:
    request = getattr(flow, "request", None)
    return str(
        getattr(request, "pretty_host", "")
        or getattr(request, "host", "")
        or ""
    ).strip().lower()


class BidKingWssAddon:
    def __init__(self) -> None:
        self.server_host = (
            os.environ.get("BIDKING_WSS_SERVER_HOST", DEFAULT_SERVER_HOST)
            .strip()
            .lower()
        )
        self.sanitizer = WssFrameSanitizer()

    @staticmethod
    def _emit_flow_event(event: str, flow: Any) -> None:
        print(
            FLOW_STREAM_PREFIX
            + json.dumps(
                {"event": event, "flow_id": _flow_id(flow)},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            flush=True,
        )

    def websocket_start(self, flow: Any) -> None:
        if _flow_host(flow) == self.server_host:
            self._emit_flow_event("open", flow)

    def websocket_message(self, flow: Any) -> None:
        if _flow_host(flow) != self.server_host:
            return
        websocket = getattr(flow, "websocket", None)
        messages = getattr(websocket, "messages", None)
        if not messages:
            return
        message = messages[-1]
        content = getattr(message, "content", None)
        if not isinstance(content, bytes):
            return

        direction = "SEND" if bool(getattr(message, "from_client", False)) else "REV"
        for frame in self.sanitizer.feed(
            flow_id=_flow_id(flow),
            direction=direction,
            payload=content,
        ):
            record = {
                "captured_at_ms": int(time.time() * 1000),
                # Privacy-safe process-local timing only. The parent compares
                # this system monotonic clock with its parser/monitor/UI stamps;
                # no payload, identity, endpoint, or absolute path is added.
                "captured_monotonic": time.monotonic(),
                "flow_id": _flow_id(flow),
                "direct": frame.direction,
                "message_id": frame.message_id,
                "data_length": len(frame.raw),
                "data_b64": base64.b64encode(frame.raw).decode("ascii"),
            }
            print(
                FRAME_STREAM_PREFIX
                + json.dumps(record, ensure_ascii=True, separators=(",", ":")),
                flush=True,
            )

    def websocket_end(self, flow: Any) -> None:
        self.sanitizer.close_flow(_flow_id(flow))
        if _flow_host(flow) == self.server_host:
            self._emit_flow_event("close", flow)

    def websocket_error(self, flow: Any) -> None:
        self.sanitizer.close_flow(_flow_id(flow))
        if _flow_host(flow) == self.server_host:
            self._emit_flow_event("close", flow)


addons = [BidKingWssAddon()]
