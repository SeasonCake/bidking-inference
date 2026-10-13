"""Live dirty-state recompute gates for the Streamlit app."""

from __future__ import annotations

from typing import Any, MutableMapping


DIRTY_SINCE_KEY = "_live_dirty_since"
DIRTY_VERSION_KEY = "_live_dirty_version"
AUTO_ARMED_KEY = "_live_auto_recompute_armed"
AUTO_REQUEST_KEY = "_request_bg_hint_live_dirty"


def update_live_dirty_clock(
    session_state: MutableMapping[str, Any],
    *,
    dirty: bool,
    version: int,
    now: float,
) -> float | None:
    """Track when the current dirty live version first appeared."""
    if not dirty:
        session_state.pop(DIRTY_SINCE_KEY, None)
        session_state.pop(DIRTY_VERSION_KEY, None)
        return None

    if (
        session_state.get(DIRTY_VERSION_KEY) != version
        or DIRTY_SINCE_KEY not in session_state
    ):
        session_state[DIRTY_VERSION_KEY] = version
        session_state[DIRTY_SINCE_KEY] = now
    return float(session_state[DIRTY_SINCE_KEY])


def maybe_schedule_live_dirty_recompute(
    session_state: MutableMapping[str, Any],
    *,
    dirty: bool,
    version: int,
    inference_ready: bool,
    worker_running: bool,
    auto_enabled: bool,
    armed: bool,
    has_pending_request: bool,
    now: float,
    debounce_sec: float = 0.0,
) -> bool:
    """Queue one background hint run when a ready live input becomes dirty."""
    dirty_since = update_live_dirty_clock(
        session_state,
        dirty=dirty,
        version=version,
        now=now,
    )
    if not dirty:
        return False
    if not auto_enabled or not armed or not inference_ready:
        return False
    if worker_running or has_pending_request:
        return False
    if dirty_since is None or now - dirty_since < debounce_sec:
        return False

    session_state["_request_bg_hint_manual"] = True
    session_state[AUTO_REQUEST_KEY] = True
    session_state["_bg_infer_status"] = "idle"
    return True

