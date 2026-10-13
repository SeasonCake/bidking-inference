"""NDJSON debug logger for agent instrumentation (session d186da).

On by default for field troubleshooting. Disable with
``BIDKING_AGENT_DEBUG=0`` or set ``_AGENT_DEBUG_ACTIVE = False`` below.

Logs append to ``debug-d186da.log`` at repo root (bidking-lab parent) when enabled.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_LOG = Path(__file__).resolve().parent.parent.parent / "debug-d186da.log"
_SESSION = "d186da"

_AGENT_DEBUG_ACTIVE = False
_OFF = frozenset({"0", "false", "no", "off"})
_ENABLED = _AGENT_DEBUG_ACTIVE or (
    os.environ.get("BIDKING_AGENT_DEBUG", "1").strip().lower() not in _OFF
)


def agent_debug_enabled() -> bool:
    return _ENABLED


def agent_debug_log(
    *,
    location: str,
    message: str,
    data: dict | None = None,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    if not _ENABLED:
        return
    # #region agent log
    try:
        payload = {
            "sessionId": _SESSION,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def agent_phase_log(
    *,
    phase: str,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
    **data: object,
) -> None:
    """Timeline checkpoint using shared run t0 in session_state."""
    if not _ENABLED:
        return
    import streamlit as st

    t0 = float(st.session_state.get("_agent_run_t0", time.perf_counter()))
    agent_debug_log(
        location=f"streamlit_app.py:phase:{phase}",
        message=phase,
        data={"phase": phase, "elapsed_ms": int((time.perf_counter() - t0) * 1000), **data},
        hypothesis_id=hypothesis_id,
        run_id=run_id,
    )
