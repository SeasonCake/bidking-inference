"""Background MC hint runs for Streamlit (thread + cancel on input change)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Mapping

LOG = logging.getLogger("bidking_lab.ui")

READING_FP_KEYS: tuple[str, ...] = (
    "map_id", "hero", "warehouse_cells", "total_item_count",
    "wg_cells", "white_cells", "white_count", "green_cells", "green_count",
    "blue_cells", "blue_count",
    "purple_cells", "purple_count", "purple_value", "purple_avg_value",
    "purple_huge_band", "purple_avg_raw",
    "gold_cells", "gold_count", "gold_value", "gold_avg_value",
    "gold_huge_band", "gold_avg_raw",
    "red_cells_total", "red_value_lo", "red_value_hi", "red_huge_band",
    "red_confirmed_none", "small_warehouse_confirmed",
    "_canonical_input_source",
)

# Subset that actually changes MC filter / session buckets (TB #31: not avg_*).
MC_FILTER_FP_KEYS: tuple[str, ...] = tuple(
    k for k in READING_FP_KEYS
    if k not in (
        "purple_avg_raw", "purple_avg_value",
        "gold_avg_raw", "gold_avg_value",
    )
)


def _box_log(box: dict[str, Any], message: str, *args: object) -> None:
    line = message % args if args else message
    if LOG.isEnabledFor(logging.INFO):
        LOG.info(line)
    logs = box.setdefault("_log", [])
    logs.append(line)
    if len(logs) > 40:
        del logs[:-40]


def _flush_box_log(session_state: Any, box: dict[str, Any] | None) -> None:
    if session_state is None or not box:
        return
    pending = box.get("_log")
    if not pending:
        return
    try:
        from ui_log import append_infer_log

        for line in pending:
            append_infer_log(session_state, line)
        box["_log"] = []
    except Exception:  # noqa: BLE001
        pass


def inference_fingerprint(
    state: dict[str, Any] | Mapping[str, Any],
    mc: dict[str, Any],
    *,
    seed_stable: bool = True,
) -> str:
    """Fingerprint for cancel/stale detection.

    When ``seed_stable`` is False, ``seed`` is omitted so an unlocked random
  seed does not cancel background MC on every Streamlit rerun.
    """
    payload = {k: state.get(k) for k in READING_FP_KEYS}
    mc_payload = dict(mc)
    if not seed_stable:
        mc_payload.pop("seed", None)
    payload["_mc"] = mc_payload
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def hint_bundle_stale_report(
    bundle: dict[str, Any] | None,
    obs_state: dict[str, Any],
    *,
    inference_ready: bool,
) -> dict[str, Any]:
    """Why a cached hint bundle no longer matches current sidebar/obs state."""
    report: dict[str, Any] = {
        "stale": False,
        "reasons": [],
        "stored_fp": None,
        "current_fp": None,
        "changed_fields": [],
        "bundle_map_id": None,
        "bundle_warehouse_cells": None,
        "obs_map_id": obs_state.get("map_id"),
        "obs_warehouse_cells": obs_state.get("warehouse_cells"),
    }
    if bundle is None:
        return report
    report["stored_fp"] = bundle.get("readings_fp")
    report["current_fp"] = inference_fingerprint(obs_state, {}, seed_stable=True)
    report["bundle_map_id"] = bundle.get("map_id")
    report["bundle_warehouse_cells"] = bundle.get("warehouse_cells")
    if not inference_ready:
        report["stale"] = True
        report["reasons"].append("inference_not_ready")
    if bundle.get("warehouse_cells") != obs_state.get("warehouse_cells"):
        report["stale"] = True
        report["reasons"].append("warehouse_cells")
    if bundle.get("map_id") != obs_state.get("map_id"):
        report["stale"] = True
        report["reasons"].append("map_id")
    snap = bundle.get("readings_snap")
    if isinstance(snap, dict):
        for key in MC_FILTER_FP_KEYS:
            old, new = snap.get(key), obs_state.get(key)
            if old != new:
                report["changed_fields"].append(
                    {"key": key, "was": old, "now": new},
                )
        if report["changed_fields"]:
            report["stale"] = True
            if "readings_changed" not in report["reasons"]:
                report["reasons"].append("readings_changed")
    elif report["stored_fp"] is not None:
        if report["stored_fp"] != report["current_fp"]:
            report["stale"] = True
            report["reasons"].append("readings_fp_hash")
    return report


def start_background_hint(
    *,
    state: dict[str, Any],
    mc: dict[str, Any],
    compute_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None],
    mc_fingerprint: dict[str, Any] | None = None,
    session_state: Any | None = None,
) -> dict[str, Any]:
    """Start a daemon thread; returns the shared result box dict."""
    fp_mc = mc_fingerprint if mc_fingerprint is not None else mc
    fp = inference_fingerprint(state, fp_mc)

    cancel_ctx = {
        "readings_rev": int(session_state.get("obs_readings_rev", 0))
        if session_state is not None
        else 0,
        "map_id": state.get("map_id"),
        "warehouse_cells": int(state.get("warehouse_cells") or 0),
    }
    box: dict[str, Any] = {
        "status": "running",
        "fp": fp,
        "cancel_ctx": cancel_ctx,
        "gen": 0,
        "result": None,
        "error": None,
        "started_at": time.time(),
        "_log": [],
    }
    cancel = threading.Event()
    box["cancel"] = cancel

    state_snap = dict(state)
    if session_state is not None:
        try:
            from ui_log import append_infer_log

            append_infer_log(
                session_state,
                "bg_hint start fp=%s map_id=%s wh=%s trials=%s"
                % (fp, state_snap.get("map_id"), state_snap.get("warehouse_cells"), mc.get("n_trials")),
            )
        except Exception:  # noqa: BLE001
            pass
    _box_log(
        box,
        "bg_hint start fp=%s map_id=%s wh=%s trials=%s",
        fp,
        state_snap.get("map_id"),
        state_snap.get("warehouse_cells"),
        mc.get("n_trials"),
    )

    def _worker() -> None:
        t0 = time.time()
        try:
            if cancel.is_set():
                box["status"] = "cancelled"
                return
            result = compute_fn(state_snap, mc)
            if cancel.is_set():
                box["status"] = "cancelled"
                return
            box["result"] = result
            box["status"] = "done" if result is not None else "skipped"
            _box_log(
                box,
                "bg_hint worker %s in %.1fs fp=%s",
                box["status"],
                time.time() - t0,
                fp,
            )
        except Exception as exc:  # noqa: BLE001
            box["error"] = str(exc)
            box["status"] = "error"
            _box_log(
                box,
                "bg_hint worker error in %.1fs: %s",
                time.time() - t0,
                exc,
            )

    threading.Thread(target=_worker, daemon=True).start()
    return box


_BOX_KEY = "_bg_infer_box"


def _should_cancel_running(
    session_state: Any,
    state: dict[str, Any],
    cancel_ctx: dict[str, Any],
) -> bool:
    """Cancel only on explicit user edits, not render-time obs mutations."""
    rev = int(session_state.get("obs_readings_rev", 0))
    if rev != int(cancel_ctx.get("readings_rev", -1)):
        return True
    mid = state.get("map_id")
    ctx_mid = cancel_ctx.get("map_id")
    if (
        mid is not None
        and ctx_mid is not None
        and int(mid) != int(ctx_mid)
    ):
        return True
    wh = int(state.get("warehouse_cells") or 0)
    ctx_wh = int(cancel_ctx.get("warehouse_cells") or 0)
    # OCR 时仓库格数常为 0；用户事后补填不应取消已在跑的推断。
    if ctx_wh > 0 and wh > 0 and wh != ctx_wh:
        return True
    return False


def sync_background_hint(
    session_state: Any,
    *,
    state: dict[str, Any],
    mc: dict[str, Any],
    mc_fingerprint: dict[str, Any] | None = None,
) -> str:
    """Cancel stale runs; promote finished results. Returns status string."""
    box = session_state.get(_BOX_KEY)
    if not box:
        return "idle"

    _flush_box_log(session_state, box)

    if box.get("status") == "running" and _should_cancel_running(
        session_state, state, box.get("cancel_ctx") or {},
    ):
        # #region agent log
        try:
            from agent_debug_log import agent_debug_log

            agent_debug_log(
                location="bg_inference.py:sync_background_hint:cancel",
                message="bg hint cancelled by fingerprint",
                data={
                    "map_id": state.get("map_id"),
                    "ctx_map_id": (box.get("cancel_ctx") or {}).get("map_id"),
                    "warehouse_cells": state.get("warehouse_cells"),
                    "ctx_wh": (box.get("cancel_ctx") or {}).get("warehouse_cells"),
                    "readings_rev": session_state.get("obs_readings_rev"),
                    "ctx_rev": (box.get("cancel_ctx") or {}).get("readings_rev"),
                },
                hypothesis_id="H6,H7",
                run_id="post-fix",
            )
        except Exception:
            pass
        # #endregion
        cancel = box.get("cancel")
        if cancel is not None:
            cancel.set()
        box["status"] = "cancelled"
        session_state["_hint_bundle"] = None
        _flush_box_log(session_state, box)
        session_state[_BOX_KEY] = None
        try:
            from ui_log import append_infer_log

            append_infer_log(
                session_state,
                "bg_hint cancelled (user changed map/warehouse/readings)",
            )
        except Exception:  # noqa: BLE001
            pass
        return "cancelled"

    if box.get("status") == "done":
        session_state["_hint_bundle"] = box.get("result")
        elapsed = time.time() - float(box.get("started_at") or time.time())
        _flush_box_log(session_state, box)
        session_state[_BOX_KEY] = None
        try:
            from ui_log import append_infer_log

            append_infer_log(
                session_state,
                "bg_hint promoted to UI in %.1fs fp=%s" % (elapsed, box.get("fp")),
            )
        except Exception:  # noqa: BLE001
            pass
        return "done"

    if box.get("status") in ("cancelled", "error", "skipped"):
        st = str(box["status"])
        err = box.get("error")
        _flush_box_log(session_state, box)
        session_state[_BOX_KEY] = None
        try:
            from ui_log import append_infer_log

            append_infer_log(
                session_state,
                "bg_hint terminal %s%s" % (st, (" err=%s" % err) if err else ""),
            )
        except Exception:  # noqa: BLE001
            pass
        return st

    if box.get("status") == "running":
        return "running"

    return "idle"
