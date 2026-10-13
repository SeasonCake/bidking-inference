# -*- coding: utf-8 -*-
"""Phase 5 read-only A/B: measure the per-hero impact of turning ON the new drop-table value
prior (AHMAD_USE_DROP_TABLE_VALUE=1, value_fitted overlay) vs the legacy nest-price values.

For each NEW-era sample of the chosen hero, replay (faithfully, via the authoritative monitor
builder) to the DECISION round (last bid_send), build the snapshot ONCE, then run the reference
engine TWICE on it — value OFF (current default) and value ON — and compare the headline 参考
(balanced) against the settled true_value_norm (long-tail replaced). Quality prior is ON for both
runs, so this isolates the VALUE change.

Outputs signed bias% / MAE% / over-rate per config, plus per-map, so we can see how much value_fitted
moves ahmed (and where) BEFORE touching any tuning constant. max_combos is capped (both configs share
the cap, so the A/B delta is unaffected) to keep the run bounded — this is a directional measurement,
not a faithfulness gate.

  python scripts/measure_value_prior_ab.py --hero ahmed --limit 50 --max-combos 8000
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "scripts", "scripts/calibration", "hero_ref/src"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from bidking_lab.live import fatbeans as FB  # noqa: E402
from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
import aisha_calib as C  # noqa: E402  (normalized_truth: long-tail-replaced settlement value)

_TABLES = None
MANIFEST = ROOT / "data" / "sample_manifests" / "era_partition_noon_2026-06-27.json"


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _hero_of_events(ev) -> str | None:
    for s in ev.states:
        h = FB._hero_mode_from_state(s)
        if h:
            return h
    return None


def _truncate(ev, cut: int):
    k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
    return FB.FatbeansCaptureEvents(
        packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
        states=k(ev.states), statuses=k(ev.statuses),
    )


def _truth_value_norm(ev) -> int | None:
    try:
        nt = C.normalized_truth(ev)
        return int(nt.get("norm_value") or 0)
    except Exception:
        return None


_QN = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}


def _truth_quantities(ev) -> dict:
    """Settled-truth quantities (total + per-quality count/cells/normalized-value) for decomposing
    where the value error comes from. q2 folds into q1."""
    st_state = next((s for s in reversed(ev.states) if s.inventory_items), None)
    if not st_state:
        return {}
    items = st_state.inventory_items
    out = {
        "true_items": len(items),
        "true_red": sum(1 for it in items if int(it.quality or 0) == 6),
        "true_red_cells": sum(int(it.cells or 0) for it in items if int(it.quality or 0) == 6),
        "true_cells": sum(int(it.cells or 0) for it in items),
    }
    for qk, qs in _QN.items():
        sel = [it for it in items if int(it.quality or 0) in qs]
        out[f"true_count_{qk}"] = len(sel)
        out[f"true_cells_{qk}"] = sum(int(it.cells or 0) for it in sel)
        vv = 0.0
        for it in sel:
            nv = C.normalized_item_value(int(getattr(it, "item_id", 0) or 0))
            vv += float(nv if nv is not None else (getattr(it, "value", 0) or 0))
        out[f"true_value_{qk}"] = int(vv)
    return out


def _engine_per_quality(res: dict) -> dict:
    """Engine per-quality estimates (mid of each range) for the value-ON run."""
    qcr = res.get("quality_count_ranges") or {}
    qcl = res.get("quality_cells_ranges") or {}
    out = {}
    for qk in ("q1", "q3", "q4", "q5", "q6"):
        cr = qcr.get(qk) or [None, None, None]
        cl = qcl.get(qk) or [None, None, None]
        out[f"est_count_{qk}"] = cr[1] if len(cr) > 1 else None
        out[f"est_cells_{qk}"] = cl[1] if len(cl) > 1 else None
    rv = res.get("red_value_range") or [None, None, None]
    out["est_red_value"] = rv[1] if len(rv) > 1 else None
    return out


def _snapshot_round(snap) -> int | None:
    try:
        r = (((snap or {}).get("ui_contract") or {}).get("context") or {}).get("round")
        return int(r) if r is not None else None
    except (TypeError, ValueError):
        return None


def _decision_snapshot(ev):
    bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
    if not bids:
        return None
    cut = int(bids[-1].sort_id)  # decision round = last pre-settled bid
    tev = _truncate(ev, cut)
    art = build_monitor_artifact_from_events(
        tev, file="replay.json", tables=_tables(), n_trials=1, roi_trials=1, run_debug_shadows=False
    )
    return prepare_reference_engine_snapshot(art)


def _set_env(val):
    if val is None:
        os.environ.pop("AHMAD_USE_DROP_TABLE_VALUE", None)
    else:
        os.environ["AHMAD_USE_DROP_TABLE_VALUE"] = val


def _run(snapshot, env_val, max_combos: int) -> dict:
    """env_val: '0' force legacy, '1' force value-only (sf unchanged), None -> per-hero regime."""
    _set_env(env_val)
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hero", default="ahmed")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--max-combos", type=int, default=8000)
    ap.add_argument("--mode", choices=["value", "regime"], default="value",
                    help="value: A/B legacy vs value-only (sf 0.85 both). regime: legacy vs per-hero "
                         "value+safety regime (the shipped config for migrated heroes).")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv[1:])
    on_env = None if args.mode == "regime" else "1"  # regime: unset -> per-hero regime; value: force on

    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new_paths = [r["path"] for r in man["rows"] if r["era"] == "NEW"]

    # Select the hero's NEW-era samples; stratify across maps so the subset isn't one-map-dominated.
    by_map: dict[str, list[Path]] = defaultdict(list)
    scanned = 0
    for fp in new_paths:
        p = ROOT / fp if not Path(fp).is_absolute() else Path(fp)
        if not p.is_file():
            continue
        try:
            ev = FB.parse_fatbeans_capture(p)
        except Exception:
            continue
        if _hero_of_events(ev) != args.hero:
            continue
        mid = None
        for s in reversed(ev.states):
            if getattr(s, "map_id", None):
                mid = str(s.map_id)
                break
        by_map[mid or "?"].append(p)
        scanned += 1
    # round-robin across maps until limit
    picked: list[Path] = []
    pools = {m: list(v) for m, v in by_map.items()}
    while len(picked) < args.limit and any(pools.values()):
        for m in list(pools):
            if pools[m]:
                picked.append(pools[m].pop())
                if len(picked) >= args.limit:
                    break
    print(f"hero={args.hero} NEW-era candidates={scanned} across {len(by_map)} maps; running {len(picked)} "
          f"(max_combos={args.max_combos})", flush=True)

    rows = []
    t0 = time.time()
    for i, p in enumerate(picked):
        try:
            ev = FB.parse_fatbeans_capture(p)
            truth = _truth_value_norm(ev)
            if not truth:
                continue
            snap = _decision_snapshot(ev)
            if snap is None:
                continue
            off = _run(snap, "0", args.max_combos)
            on = _run(snap, on_env, args.max_combos)
            evd = on.get("evidence", {})
            tq = _truth_quantities(ev)
            # TOP-3 reveal diagnostics: is a missed-under red INVISIBLE at decision (reveal floor 0) or
            # REVEALED-but-mispriced (floor>0 yet est<truth = wiring bug)? min_counts.q6 = revealed red
            # count floor; quality_cell_floors.q6 = revealed red cells; round from the decision snapshot.
            mc = evd.get("min_counts") or {}
            qcf = evd.get("quality_cell_floors") or {}
            rows.append({
                "sample": p.name, "map_id": evd.get("map_id"), "truth": truth,
                "off": off.get("balanced"), "on": on.get("balanced"),
                "off_p50": off.get("value_p50"), "on_p50": on.get("value_p50"),
                # quantity diagnostics (value-ON run): est vs settled truth
                "est_count": evd.get("total_count"),
                "est_red_mid": (on.get("red_count_range") or [None, None, None])[1],
                "est_grid_mid": (on.get("total_grid_range") or [None, None, None])[1],
                "decision_round": _snapshot_round(snap),
                "n_revealed_red": int(mc.get("q6", 0) or 0),
                "revealed_red_cells_floor": float(qcf.get("q6", 0) or 0),
                # negative-evidence / scan diagnostics: did the engine SEE non-red items (k) and was a
                # high-tier quality scan present (q5/q6 cells known)? distinguishes "engine ignores
                # negative evidence" (bug) from "no-scan -> prior-driven" (not an engine error).
                "n_nonred_reveals": sum(int(mc.get(q, 0) or 0) for q in ("q1", "q2", "q3", "q4", "q5")),
                "has_q5_scan": bool((evd.get("quality_cells") or {}).get("q5")),
                "has_q6_scan": bool((evd.get("quality_cells") or {}).get("q6")),
                "known_minimap_cells": evd.get("known_minimap_cells"),
                **tq,
                **_engine_per_quality(on),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"sample": p.name, "error": f"{type(exc).__name__}:{exc}"})
        if (i + 1) % 5 == 0:
            el = time.time() - t0
            print(f"  {i+1}/{len(picked)}  {el:.0f}s  ({el/(i+1):.1f}s/sample)", flush=True)

    ok = [r for r in rows if "error" not in r and r.get("off") and r.get("on") and r.get("truth")]

    def stats(key):
        biases = [(r[key] - r["truth"]) / r["truth"] * 100 for r in ok]
        over = sum(1 for r in ok if r[key] > r["truth"]) / len(ok) * 100 if ok else 0
        return {
            "n": len(ok),
            "median_bias_pct": round(st.median(biases), 1) if biases else None,
            "mean_bias_pct": round(st.mean(biases), 1) if biases else None,
            "MAE_pct": round(st.mean([abs(b) for b in biases]), 1) if biases else None,
            "over_rate_pct": round(over, 1),
        }

    summary = {
        "hero": args.hero, "n_ok": len(ok), "n_err": sum(1 for r in rows if "error" in r),
        "max_combos": args.max_combos,
        "value_OFF_legacy": stats("off"),
        "value_ON_fitted": stats("on"),
    }
    # per-map signed bias delta
    permap = defaultdict(lambda: {"n": 0, "off": [], "on": []})
    for r in ok:
        m = str(r["map_id"])
        permap[m]["n"] += 1
        permap[m]["off"].append((r["off"] - r["truth"]) / r["truth"] * 100)
        permap[m]["on"].append((r["on"] - r["truth"]) / r["truth"] * 100)
    summary["per_map"] = {
        m: {"n": d["n"], "off_med": round(st.median(d["off"]), 1), "on_med": round(st.median(d["on"]), 1)}
        for m, d in sorted(permap.items(), key=lambda x: -x[1]["n"])
    }

    # quantity diagnostics: is the value-ON p50 bias clean (count/red/cells near truth)?
    def _qbias(est_key, truth_key):
        pairs = [(r[est_key], r[truth_key]) for r in ok
                 if r.get(est_key) is not None and r.get(truth_key)]
        if not pairs:
            return None
        d = [(e - t) / t * 100 for e, t in pairs]
        return {"n": len(pairs), "median_bias_pct": round(st.median(d), 1),
                "mean_bias_pct": round(st.mean(d), 1), "MAE_pct": round(st.mean([abs(x) for x in d]), 1)}
    summary["quantity_diag_value_ON"] = {
        "total_count": _qbias("est_count", "true_items"),
        "red_count": _qbias("est_red_mid", "true_red"),
        "total_grid_cells": _qbias("est_grid_mid", "true_cells"),
        "value_p50": _qbias("on_p50", "truth"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
