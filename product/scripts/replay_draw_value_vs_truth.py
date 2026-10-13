# -*- coding: utf-8 -*-
"""Per-ROUND replay of REAL draw-hero (gabriela/sophie) fatbeans samples vs settlement truth.

For the draw-hero value calibration专项 (the bimodal "两头极端": hidden-red vaults under-bid,
no-red vaults right, revealed-red over-bid). It replays each real sample to EVERY pre-settled
(bidding) round and runs the reference engine, recording the per-round estimate (保守/参考/激进
value, red estimate, whether red was revealed THAT round) vs the settled truth (true red count /
true cells / normalized total value, long-tail-replaced per aisha_calib).

⚠️ FAITHFUL REPLAY (fixed 2026-06-24). It uses the AUTHORITATIVE monitor builder
`build_monitor_artifact_from_events` (the same one that writes the live round_snapshots), NOT the
aisha-lite `_snapshot_at_batch` (which hardcoded action_result_rows=[] and defaulted hero=aisha ->
the no-red damp never fired -> strength sweep came out byte-identical). VALIDATION GATE: replaying
the gabriela 2401 game (session 1469222799402041) to its R1/R2 decision reproduces the live
round_snapshots EXACTLY: R1 参考 150664, R2 参考 141631 / red [0,0,1] / min_counts
{q1:3,q3:5,q4:2,q5:2}. `main` asserts this gate before any sweep (`--check-gate` runs it standalone).
Each round = events truncated to that round's bid_send, rebuilt with trials off (~1.5s/round).

Two objectives the calibration is judged on (author 2026-06-23):
  1. 当回合合理性 — each round's 参考 is sensible for that round's VISIBLE state (early = prior-heavy
     since count/grid are accurate; later with no red drawn = lower price expectation; red drawn = adjust up).
  2. decision-vs-truth gap — the LAST pre-settled round's 参考 vs the settled truth value.

Env passes straight through to the engine, so a config sweep is just repeated runs with different
DRAW_HERO_NO_RED_DAMP_STRENGTH / DRAW_HERO_REVEALED_RED_COUNT_SIGMA / future round-aware knobs.

Usage:
  PYTHONPATH="src;scripts;scripts/calibration;hero_ref/src" python scripts/replay_draw_value_vs_truth.py \
      --hero gabriela --strengths "3.5,2.0,off" --agg-out out.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

for sub in ("src", "scripts", "scripts/calibration", "hero_ref/src", "hero_ref/tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from bidking_lab.live.fatbeans import parse_fatbeans_capture, FatbeansCaptureEvents  # noqa: E402
from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from build_clean_eval_set import (  # noqa: E402
    clean_eval_files,
    sample_selection_metadata,
    selection_row_fields,
)
import aisha_calib as C  # noqa: E402

_TABLES = None


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _hero_of(path: Path) -> str:
    n = path.name.lower()
    for h in ("gabriela", "sophie", "raven", "wuqilin", "aisha", "ahmed", "ethan"):
        if h in n:
            return h
    return "gabriela"


def _truncate(ev, cut: int):
    k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
    return FatbeansCaptureEvents(
        packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
        states=k(ev.states), statuses=k(ev.statuses),
    )


def _engine_at_round(ev, cut: int, hero: str, round_no: int) -> dict:
    """Faithful per-round snapshot via the authoritative monitor builder, then the reference engine
    at the engine's DEFAULT combo cap (the one the live round_snapshots were computed with — a lower
    hero_max_combos_for_round cap changes R1 150664->137612, i.e. breaks faithfulness). Faithful is
    therefore genuinely slow on deep/rich vaults (one sample ~24s); parallelize across many shards
    rather than capping. hero/round_no kept for future per-round knobs."""
    tev = _truncate(ev, cut)
    art = build_monitor_artifact_from_events(
        tev, file="replay.json", tables=_tables(), n_trials=1, roi_trials=1, run_debug_shadows=False
    )
    return run_reference_engine(prepare_reference_engine_snapshot(art)).as_dict()


def _truth(ev) -> dict | None:
    """Settled truth: true red count, true cells, normalized total value (long-tail replaced)."""
    st = next((s for s in reversed(ev.states) if s.inventory_items), None)
    if st is None:
        return None
    tred = sum(1 for it in st.inventory_items if int(it.quality or 0) == 6)
    tred_cells = sum(int(it.cells or 0) for it in st.inventory_items if int(it.quality or 0) == 6)
    tcells = sum(int(it.cells or 0) for it in st.inventory_items)
    try:
        nt = C.normalized_truth(ev)
        tval = int(nt.get("norm_value") or 0)
        raw_val = int(nt.get("raw_value") or 0)
        has_longtail = bool(nt.get("has_longtail"))
    except Exception:
        tval = raw_val = 0
        has_longtail = False
    return {
        "true_red": tred,
        "true_red_cells": tred_cells,
        "true_cells": tcells,
        "true_items": len(st.inventory_items),
        "true_value_norm": tval,
        "true_value_raw": raw_val,
        "has_longtail": has_longtail,
    }


def replay_sample(path: Path) -> dict | None:
    ev = parse_fatbeans_capture(path)
    # per-round cut points = each bid_send sort_id (events before the bid = that round's decision state)
    bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
    if not bids:
        return None
    truth = _truth(ev)
    if truth is None:
        return None
    hero = _hero_of(path)
    rounds = []
    evd: dict = {}
    for i, b in enumerate(bids):
        try:
            res = _engine_at_round(ev, int(b.sort_id), hero, i + 1)
        except Exception as exc:  # noqa: BLE001
            rounds.append({"round_idx": i, "error": f"{type(exc).__name__}:{exc}"})
            continue
        evd = res.get("evidence", {})
        q6_min = int((evd.get("min_counts", {}) or {}).get("q6", 0) or 0)
        q6_floor = float((evd.get("quality_cell_floors", {}) or {}).get("q6", 0) or 0)
        rounds.append({
            "round_idx": i,
            "conservative": res.get("value_p25"),
            "reference": res.get("balanced"),
            "aggressive": res.get("value_p75"),
            "red_count_range": res.get("red_count_range"),
            "red_cells_range": res.get("red_cells_range"),
            "red_value_p50": (res.get("red_value_range") or [None, None, None])[1],
            "red_revealed": (q6_min > 0 or q6_floor > 0),
            "total_count": evd.get("total_count"),
        })
    return {
        "sample": path.name,
        "map_id": evd.get("map_id") if rounds and "error" not in rounds[-1] else None,
        "n_rounds": len(bids),
        "decision_round_idx": len(bids) - 1,
        "truth": truth,
        "rounds": rounds,
    }


def check_gate() -> bool:
    """Validation gate: gabriela 2401 must reproduce the current live round_snapshots. The A1 no-red
    damp ramp (commit 0dcc301) moved this game's reference R1 150664->345225 / R2 141631->326164
    (memory: "A1 2401 R2 141k->326k", truth ~509k -> low-end narrowed); the stale pre-A1 constants are
    why this gate started FAILing. Re-derive these if the engine baseline moves again."""
    hits = glob.glob(str(ROOT / "data" / "samples" / "fatbeans" / "*1469222799402041*.json"))
    if not hits:
        print("  [gate] SKIP — 2401 validation sample not in library")
        return True
    r = replay_sample(Path(hits[0]))
    rounds = r.get("rounds", []) if r else []
    # R1/R2 anchors shifted 345225/326164 -> 338196/322251 by the 2026-06-27 q6-only long-tail value
    # re-normalization (corrected value column; red range [0,1,3] unchanged). Re-derive if the value
    # baseline moves again. NOT affected by the aisha no-red damp (draw path unchanged, aisha-gated OFF).
    ok = (
        len(rounds) >= 2
        and rounds[0].get("reference") == 338196
        and rounds[1].get("reference") == 322251
        and rounds[1].get("red_count_range") == [0, 1, 3]
    )
    print(f"  [gate] gabriela 2401: R1 参考={rounds[0].get('reference') if rounds else '?'} "
          f"R2 参考={rounds[1].get('reference') if len(rounds) > 1 else '?'} "
          f"red={rounds[1].get('red_count_range') if len(rounds) > 1 else '?'} -> {'PASS' if ok else 'FAIL'}")
    return ok


def _apply_strength(s: str) -> None:
    """Configure the no-red damp for a sweep point. 'off' disables; a number sets the strength."""
    for k in ("DRAW_HERO_DISABLE_NO_RED_DAMP", "DRAW_HERO_NO_RED_DAMP_STRENGTH"):
        os.environ.pop(k, None)
    if str(s).lower() == "off":
        os.environ["DRAW_HERO_DISABLE_NO_RED_DAMP"] = "1"
    else:
        os.environ["DRAW_HERO_NO_RED_DAMP_STRENGTH"] = str(float(s))


def _bucket(tr: int) -> str:
    return "red0" if tr == 0 else ("red1-2" if tr <= 2 else "red3+")


def _aggregate(samples: list[dict], strength: str) -> list[dict]:
    """Per-(round_position, bucket) aggregate. round_position: 'r{i}' 0-based, plus 'decision'.
    Returns rows {strength, slot, bucket, n, mean_ref, mean_truth, bias_pct, mean_red_est_mid,
    frac_red_revealed}."""
    from collections import defaultdict
    acc = defaultdict(lambda: {"n": 0, "ref": 0.0, "truth": 0.0, "redmid": 0.0, "revealed": 0})
    for s in samples:
        if "truth" not in s or not s.get("rounds"):
            continue
        b = _bucket(s["truth"]["true_red"])
        tv = float(s["truth"]["true_value_norm"])
        for rd in s["rounds"]:
            ref = rd.get("reference")
            if ref is None:
                continue
            slots = [f"r{rd['round_idx']}"]
            if rd["round_idx"] == s["decision_round_idx"]:
                slots.append("decision")
            redmid = (rd.get("red_count_range") or [0, 0, 0])[1] or 0
            for slot in slots:
                key = (slot, b)
                a = acc[key]
                a["n"] += 1
                a["ref"] += float(ref)
                a["truth"] += tv
                a["redmid"] += float(redmid)
                a["revealed"] += 1 if rd.get("red_revealed") else 0
    rows = []
    for (slot, b), a in sorted(acc.items()):
        n = a["n"]
        rows.append({
            "strength": strength, "slot": slot, "bucket": b, "n": n,
            "mean_ref": round(a["ref"] / n), "mean_truth": round(a["truth"] / n),
            "bias_pct": round(100 * (a["ref"] - a["truth"]) / max(1.0, a["truth"]), 1),
            "mean_red_est_mid": round(a["redmid"] / n, 2),
            "frac_red_revealed": round(a["revealed"] / n, 2),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hero", default="gabriela", choices=["gabriela", "sophie", "both"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list", default=None, help="JSON array of sample paths (overrides --hero glob)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--strengths", default=None, help="comma list e.g. '3.5,2.0,off' -> sweep + aggregate")
    ap.add_argument("--agg-out", default=None, help="write per-(slot,bucket,strength) aggregate JSON")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--check-gate", action="store_true", help="only run the 2401 live-reproduction gate")
    ap.add_argument("--skip-gate", action="store_true", help="skip the pre-sweep validation gate (NOT recommended)")
    args = ap.parse_args()

    if args.check_gate:
        return 0 if check_gate() else 1
    if not args.skip_gate:
        # §5/§6: prove the harness reproduces a KNOWN live result before trusting any sweep
        if not check_gate():
            print("  ABORT: validation gate failed — harness no longer reproduces live 2401. Fix before measuring.")
            return 1

    if args.list:
        files = [str(ROOT / p) if not os.path.isabs(p) else p for p in json.loads(Path(args.list).read_text())]
        selector = "explicit_json_list"
    else:
        pats = (["gabriela", "sophie"] if args.hero == "both" else [args.hero])
        files = sorted({path for hero in pats for path in clean_eval_files(hero)})
        selector = "clean_eval:" + ",".join(pats)
    if args.limit:
        files = files[: args.limit]
        selector += f":limit={args.limit}"
    metadata = sample_selection_metadata(files, selector=selector)
    metadata_fields = selection_row_fields(metadata)
    print("run_metadata=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True))

    def replay_all(label: str) -> list[dict]:
        rows = []
        t0 = time.time()
        for n, f in enumerate(files, 1):
            try:
                r = replay_sample(Path(f))
            except Exception as exc:  # noqa: BLE001
                r = {"sample": Path(f).name, "error": f"{type(exc).__name__}:{exc}"}
            if r:
                r.update(metadata_fields)
                rows.append(r)
            if not args.quiet and (n % 5 == 0 or n == len(files)):
                per = (time.time() - t0) / n
                print(f"  [{label}] {n}/{len(files)} ({per:.1f}s/sample, ETA {per*(len(files)-n):.0f}s)")
        return rows

    # SWEEP MODE: multiple damp strengths -> per-(slot,bucket,strength) aggregate
    if args.strengths:
        strengths = [s.strip() for s in args.strengths.split(",") if s.strip()]
        all_agg = []
        for s in strengths:
            _apply_strength(s)
            samples = replay_all(f"strength={s}")
            aggregated = _aggregate(samples, s)
            for row in aggregated:
                row.update(metadata_fields)
            all_agg.extend(aggregated)
        print("\n=== per-(slot,bucket) bias%% across strengths (slot=r{i} 0-based + decision) ===")
        for r in all_agg:
            if r["slot"] == "decision":
                print(f"  s={r['strength']:>4} {r['bucket']:7s} n={r['n']:3d} 参考={r['mean_ref']:8d} 真值={r['mean_truth']:8d} "
                      f"bias={r['bias_pct']:+6.1f}% redmid={r['mean_red_est_mid']:.2f} reveal={r['frac_red_revealed']:.2f}")
        if args.agg_out:
            Path(args.agg_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.agg_out).write_text(json.dumps(all_agg, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\nwrote {len(all_agg)} agg rows -> {args.agg_out}")
        return 0

    out_rows = replay_all("baseline")

    # quick console summary: decision-round 参考 vs truth, bucketed by true_red
    def bucket(tr):
        return "red0" if tr == 0 else ("red1-2" if tr <= 2 else "red3+")
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0.0, 0.0])  # bucket -> [n, sum_ref, sum_truth]
    for r in out_rows:
        if "truth" not in r or not r.get("rounds"):
            continue
        dec = r["rounds"][r["decision_round_idx"]]
        if "reference" not in dec or dec.get("reference") is None:
            continue
        b = bucket(r["truth"]["true_red"])
        agg[b][0] += 1
        agg[b][1] += float(dec["reference"])
        agg[b][2] += float(r["truth"]["true_value_norm"])
    print("\n=== decision-round 参考 vs truth, by TRUE red bucket ===")
    for b in ("red0", "red1-2", "red3+"):
        n, sr, stv = agg[b]
        if n:
            print(f"  {b:7s} n={n:3d}  mean参考={sr/n:9.0f}  mean真值={stv/n:9.0f}  bias={100*(sr-stv)/max(1,stv):+6.1f}%")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out_rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {len(out_rows)} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
