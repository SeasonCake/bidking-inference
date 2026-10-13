"""Per-hero NEW-slice health check (release-readiness): for a hero's NEW-era samples, build the
faithful decision snapshot ONCE (monitor rebuild, cached), then run the CURRENT shipped engine and
report estimate-vs-settled-truth bias by map family for 件数/红/金/总格/价值. No A/B — a plain
"where does this hero stand on clean NEW data" read. Reuses the era manifest (all heroes).

用法:
  python scripts/calibration/hero_new_health.py build <hero> [limit]   # 慢: monitor 重建+缓存
  python scripts/calibration/hero_new_health.py run   <hero> [max_combos]  # 快: 缓存上跑引擎
  python scripts/calibration/hero_new_health.py auto  <hero> [max_combos]
CPU-bound, 串行。rate-敏感(红/价值)只看 NEW(本脚本即 NEW)。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("src", "scripts", "scripts/calibration", "hero_ref/src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MANIFEST = ROOT / "data" / "sample_manifests" / "era_partition_noon_2026-06-27.json"
from sample_manifest_inputs import load_sample_paths  # noqa: E402


def _cache(hero: str, override: str | Path | None = None) -> Path:
    if override is None:
        return ROOT / f"data/reports/_health_{hero}_new_snaps.json"
    path = Path(override)
    return path if path.is_absolute() else ROOT / path


def _fam(map_id: int) -> str:
    m = int(map_id or 0)
    if m // 100 == 56:
        return "56xx"
    t = (m // 100) % 10
    return {5: "25xx", 4: "24xx"}.get(t, f"{m // 100}xx")


def build(
    hero: str,
    limit: int,
    *,
    manifest: str | Path = MANIFEST,
    cache: str | Path | None = None,
):
    from bidking_lab.live import fatbeans as FB
    from bidking_lab.live.fatbeans import parse_fatbeans_capture
    from bidking_lab.live.monitor import build_monitor_artifact_from_events, load_monitor_tables
    from ahmad_ref_engine import prepare_reference_engine_snapshot
    import aisha_calib as C

    tables = load_monitor_tables()
    new_paths = load_sample_paths(manifest, hero=hero)

    def hero_of(ev):
        for s in ev.states:
            h = FB._hero_mode_from_state(s)
            if h:
                return h
        return None

    def truncate(ev, cut):
        k = lambda seq: tuple(r for r in seq if int(r.sort_id) < cut)  # noqa: E731
        return FB.FatbeansCaptureEvents(packets=k(ev.packets), frames=k(ev.frames), sends=k(ev.sends),
                                        states=k(ev.states), statuses=k(ev.statuses))

    def _nv(it):
        nv = C.normalized_item_value(int(getattr(it, "item_id", 0) or 0))
        return float(nv if nv is not None else (getattr(it, "value", 0) or 0))

    out = []
    t0 = time.time()
    for p in new_paths:
        if len(out) >= limit:
            break
        if not p.is_file():
            continue
        try:
            ev = parse_fatbeans_capture(p)
        except Exception:
            continue
        if hero_of(ev) != hero:
            continue
        try:
            truth_val = int((C.normalized_truth(ev) or {}).get("norm_value") or 0)
        except Exception:
            truth_val = 0
        if truth_val <= 0:
            continue
        st_state = next((s for s in reversed(ev.states) if s.inventory_items), None)
        if not st_state:
            continue
        items = st_state.inventory_items
        red = [it for it in items if int(it.quality or 0) == 6]
        gold = [it for it in items if int(it.quality or 0) == 5]
        bids = sorted((s for s in ev.sends if getattr(s, "kind", "") == "bid"), key=lambda s: int(s.sort_id))
        if not bids:
            continue
        cut = int(bids[-1].sort_id)
        try:
            art = build_monitor_artifact_from_events(truncate(ev, cut), file="replay.json", tables=tables,
                                                     n_trials=1, roi_trials=1, run_debug_shadows=False)
            snap = prepare_reference_engine_snapshot(art)
        except Exception:
            continue
        ctx = ((snap or {}).get("ui_contract") or {}).get("context") or {}
        rnd = ctx.get("round")
        mid = int(ctx.get("map_id") or ctx.get("mapId") or getattr(st_state, "map_id", 0) or 0)
        out.append({"sample": p.name[:46], "map_id": mid, "fam": _fam(mid),
                    "round": int(rnd) if rnd is not None else None, "truth_value": truth_val,
                    "truth_count": len(items), "true_red": len(red), "true_gold": len(gold),
                    "true_red_value": int(sum(_nv(it) for it in red)),
                    "truth_cells": sum(int(it.cells or 0) for it in items), "snapshot": snap})
        if len(out) % 25 == 0:
            print(f"  built {len(out)} ({time.time()-t0:.0f}s)", flush=True)
    cache_path = _cache(hero, cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    byf = defaultdict(int)
    for r in out:
        byf[r["fam"]] += 1
    print(f"[cache] {cache_path} ({len(out)} {hero} NEW, {time.time()-t0:.0f}s) {dict(byf)}")


def _cnt_mid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q) or [0, 0, 0]
    return int(r[1] or 0)


def run(hero: str, max_combos: int, *, cache: str | Path | None = None):
    import ahmad_ref_engine as eng
    recs = json.loads(_cache(hero, cache).read_text(encoding="utf-8"))
    print(f"[pool] {len(recs)} {hero} NEW snapshots, max_combos={max_combos}", flush=True)
    rows = []
    t0 = time.time()
    for rec in recs:
        res = eng.run_reference_engine(eng.prepare_reference_engine_snapshot(rec["snapshot"]),
                                       max_combos=max_combos).as_dict()
        est_total = sum(_cnt_mid(res, q) for q in ("q1", "q3", "q4", "q5", "q6"))
        rows.append({"fam": rec["fam"], "map_id": rec.get("map_id"), "round": rec.get("round"),
                     "truth_count": rec["truth_count"], "true_red": rec["true_red"], "true_gold": rec["true_gold"],
                     "true_red_value": rec.get("true_red_value"),
                     "truth_cells": rec["truth_cells"], "truth_value": rec["truth_value"],
                     "est_count": est_total, "est_red": _cnt_mid(res, "q6"), "est_gold": _cnt_mid(res, "q5"),
                     "est_red_value": (res.get("red_value_range") or [None, None, None])[1],
                     "est_grid": (res.get("total_grid_range") or [None, None, None])[1],
                     "est_value": res.get("balanced")})
        if len(rows) % 100 == 0:
            print(f"  {len(rows)} ({time.time()-t0:.0f}s)", flush=True)

    def bias(rs, ek, tk, nz=False):
        b = [(r[ek] - r[tk]) / r[tk] * 100 for r in rs
             if r.get(ek) is not None and r.get(tk) and (not nz or r[tk] > 0)]
        if not b:
            return None
        return {"n": len(b), "med": round(st.median(b), 1), "mean": round(st.mean(b), 1),
                "MAE": round(st.mean([abs(x) for x in b]), 1)}

    metrics = [("件数", "est_count", "truth_count", False), ("红件", "est_red", "true_red", True),
               ("金件", "est_gold", "true_gold", True), ("总格", "est_grid", "truth_cells", False),
               ("价值", "est_value", "truth_value", False)]
    ACTIVITY = {2531, 2532, 4531, 4532}  # temporary activity vaults — calibrate steady-state separately
    def _report(tag, rs):
        print(f"\n===== {hero} {tag} (n={len(rs)}) =====")
        for lbl, ek, tk, nz in metrics:
            b = bias(rs, ek, tk, nz)
            if b:
                print(f"  {lbl} med={b['med']:+6.1f}% mean={b['mean']:+6.1f}% MAE={b['MAE']:5.1f}% (n{b['n']})")
    _report("ALL", rows)
    act = [r for r in rows if int(r.get("map_id") or 0) in ACTIVITY]
    base = [r for r in rows if int(r.get("map_id") or 0) not in ACTIVITY]
    _report("活动图(2531/2532/4531/4532)", act)
    _report("稳态(非活动)", base)
    byfam = defaultdict(list)
    for r in base:  # family breakdown on steady-state only
        byfam[r["fam"]].append(r)
    for fam, rs in sorted(byfam.items(), key=lambda kv: -len(kv[1])):
        _report(f"稳态-{fam}", rs)
    out = ROOT / f"data/reports/health_{hero}_new.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"\n[out] {out} ({time.time()-t0:.0f}s)")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", nargs="?", choices=("build", "run", "auto"), default="auto")
    parser.add_argument("hero", nargs="?", default="ahmed")
    parser.add_argument("n", nargs="?", type=int, default=100000)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args(argv[1:])
    cache_path = _cache(args.hero, args.cache)
    if args.cmd == "build" or (args.cmd == "auto" and not cache_path.is_file()):
        build(
            args.hero,
            args.n if args.cmd == "build" else 100000,
            manifest=args.manifest,
            cache=args.cache,
        )
    if args.cmd in ("run", "auto"):
        run(args.hero, args.n if args.cmd == "run" else 6000, cache=args.cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
