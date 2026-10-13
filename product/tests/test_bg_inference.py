"""Background hint thread bookkeeping."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from bg_inference import (
    MC_FILTER_FP_KEYS,
    READING_FP_KEYS,
    hint_bundle_stale_report,
    inference_fingerprint,
    start_background_hint,
    sync_background_hint,
)


def _fake_state(**kw):
    base = {
        "map_id": 1001,
        "hero": "ethan",
        "warehouse_cells": 120,
        "total_item_count": 0,
        "wg_cells": 10,
        "white_cells": 0,
        "white_count": 0,
        "green_cells": 0,
        "green_count": 0,
        "blue_cells": 0,
        "blue_count": 0,
        "purple_cells": 0,
        "purple_count": 0,
        "purple_value": 0,
        "purple_avg_value": 0,
        "purple_huge_band": "",
        "purple_avg_raw": "",
        "gold_cells": 0,
        "gold_count": 0,
        "gold_value": 0,
        "gold_avg_value": 0,
        "gold_huge_band": "",
        "gold_avg_raw": "",
        "red_cells_total": 0,
        "red_value_lo": 0,
        "red_value_hi": 0,
        "red_huge_band": "",
        "red_confirmed_none": False,
        "small_warehouse_confirmed": False,
    }
    base.update(kw)
    return base


def test_hint_bundle_stale_report_field_diff():
    state = _fake_state(blue_cells=10)
    bundle = {
        "map_id": 1001,
        "warehouse_cells": 120,
        "readings_fp": inference_fingerprint(state, {}, seed_stable=True),
        "readings_snap": {k: state.get(k) for k in READING_FP_KEYS},
    }
    changed = dict(state)
    changed["blue_cells"] = 22
    report = hint_bundle_stale_report(
        bundle, changed, inference_ready=True,
    )
    assert report["stale"] is True
    assert "readings_changed" in report["reasons"]
    assert any(d["key"] == "blue_cells" for d in report["changed_fields"])


def test_hint_bundle_stale_report_ignores_avg_cells_only_change():
    state = _fake_state(purple_avg_raw="2.88")
    snap = {k: state.get(k) for k in MC_FILTER_FP_KEYS}
    bundle = {
        "map_id": 1001,
        "warehouse_cells": 120,
        "readings_fp": inference_fingerprint(state, {}, seed_stable=True),
        "readings_snap": snap,
    }
    changed = dict(state)
    changed["purple_avg_raw"] = "1.90"
    report = hint_bundle_stale_report(
        bundle, changed, inference_ready=True,
    )
    assert report["stale"] is False
    assert report["changed_fields"] == []


def test_hint_bundle_stale_report_tracks_canonical_input_source():
    state = _fake_state(_canonical_input_source="legacy")
    snap = {k: state.get(k) for k in MC_FILTER_FP_KEYS}
    bundle = {
        "map_id": 1001,
        "warehouse_cells": 120,
        "readings_fp": inference_fingerprint(state, {}, seed_stable=True),
        "readings_snap": snap,
    }
    changed = dict(state)
    changed["_canonical_input_source"] = "live_shadow"
    report = hint_bundle_stale_report(
        bundle, changed, inference_ready=True,
    )

    assert report["stale"] is True
    assert any(
        d["key"] == "_canonical_input_source"
        for d in report["changed_fields"]
    )


def test_hint_bundle_stale_report_warehouse_only():
    state = _fake_state()
    bundle = {
        "map_id": 1001,
        "warehouse_cells": 120,
        "readings_fp": inference_fingerprint(state, {}, seed_stable=True),
        "readings_snap": dict(state),
    }
    changed = dict(state)
    changed["warehouse_cells"] = 99
    report = hint_bundle_stale_report(
        bundle, changed, inference_ready=True,
    )
    assert report["stale"] is True
    assert "warehouse_cells" in report["reasons"]


def test_sync_promotes_done_result():
    ss: dict = {}
    state = _fake_state()
    mc = {"n_trials": 10, "seed": 1, "warehouse_tol": 8, "purple_tol": 4}
    box = {
        "status": "done",
        "fp": inference_fingerprint(state, mc),
        "result": {"ok": True},
        "error": None,
    }
    ss["_bg_infer_box"] = box
    assert sync_background_hint(ss, state=state, mc=mc) == "done"
    assert ss["_hint_bundle"] == {"ok": True}
    assert ss.get("_bg_infer_box") is None


def test_sync_cancels_on_readings_rev_change():
    ss: dict = {"obs_readings_rev": 1}
    state = _fake_state()
    mc = {"n_trials": 10, "seed": 1, "warehouse_tol": 8, "purple_tol": 4}
    cancel = threading.Event()
    ss["_bg_infer_box"] = {
        "status": "running",
        "fp": "fp",
        "cancel": cancel,
        "cancel_ctx": {"readings_rev": 0, "map_id": 1001, "warehouse_cells": 120},
        "result": None,
        "error": None,
    }
    assert sync_background_hint(ss, state=state, mc=mc) == "cancelled"
    assert cancel.is_set()
    assert ss.get("_bg_infer_box") is None


def test_sync_keeps_running_when_only_derived_fields_change():
    ss: dict = {"obs_readings_rev": 0}
    state = _fake_state(white_cells=17, green_cells=0)
    mc = {"n_trials": 10, "seed": 1, "warehouse_tol": 8, "purple_tol": 4}
    cancel = threading.Event()
    ss["_bg_infer_box"] = {
        "status": "running",
        "fp": "fp",
        "cancel": cancel,
        "cancel_ctx": {"readings_rev": 0, "map_id": 1001, "warehouse_cells": 120},
        "result": None,
        "error": None,
    }
    assert sync_background_hint(ss, state=state, mc=mc) == "running"
    assert not cancel.is_set()


def test_start_background_hint_completes():
    state = _fake_state()
    mc = {"n_trials": 10, "seed": 1, "warehouse_tol": 8, "purple_tol": 4}
    box = start_background_hint(
        state=state,
        mc=mc,
        compute_fn=lambda _s, _m: {"done": 1},
    )
    ss = {"_bg_infer_box": box}
    deadline = time.time() + 5
    while time.time() < deadline:
        status = sync_background_hint(ss, state=state, mc=mc)
        if status == "done":
            break
        time.sleep(0.05)
    assert status == "done"
    assert ss["_hint_bundle"] == {"done": 1}
