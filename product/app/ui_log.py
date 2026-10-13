"""UI / background-inference logging (terminal + optional in-app ring buffer)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

LOG = logging.getLogger("bidking_lab.ui")


def configure_ui_logging(level: int | None = None) -> None:
    """Enable UI logs when ``BIDKING_UI_LOG=1`` or *level* is set."""
    if level is None:
        level = logging.INFO if os.environ.get("BIDKING_UI_LOG") else logging.WARNING
    LOG.setLevel(level)
    if level <= logging.DEBUG and not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"),
        )
        LOG.addHandler(handler)


def append_infer_log(session_state: Any, message: str) -> None:
    """Ring buffer shown in sidebar when debug expander is open."""
    if session_state is None:
        return
    stamp = time.strftime("%H:%M:%S")
    lines = list(session_state.get("_bg_infer_log") or [])
    lines.append(f"{stamp} {message}")
    session_state["_bg_infer_log"] = lines[-40:]
