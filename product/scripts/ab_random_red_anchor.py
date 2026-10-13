"""A/B harness for the public-random-avg red anchor + hard-floor calibration (aisha).

Read-only: monkeypatches the engine in-process, never edits source. Variants:
  BASE      : current engine (threshold=100k per-item avg, soft floor only).
  H_ANCHOR  : lower red-anchor threshold to per-item avg >= ANCHOR_AVG_T, value
              floor = ANCHOR_VALUE_MUL * draw_total, with a capacity guard
              (draw_total <= CAP_SLACK * cap_upper) to kill floor>truth misanchor.
  H_FLOOR   : hard floor cons/bal >= random_value_floor (draw_total), same guard.
  BOTH      : H_ANCHOR + H_FLOOR.

cap_upper = BASE-run aggressive (p90-derived) total value, a bid-time-only upper
estimate (never truth). On a damaged floor>capacity frame the guard disarms.

Usage:
  python scripts/ab_random_red_anchor.py 2406      # replay user export
  python scripts/ab_random_red_anchor.py sweep      # full-library aisha sweep
"""
from __future__ import annotations
import sys, os, json, re, glob, time, dataclasses
for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ahmad_ref_engine as eng  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402

ANCHOR_AVG_T = 20_000.0     # per-item avg threshold to arm the red anchor (was 100k)
ANCHOR_VALUE_MUL = 0.88     # q6 value floor = MUL * draw_total (p10 cover, was 0.95)
CAP_SLACK = 1.15            # draw_total allowed up to CAP_SLACK * cap_upper estimate

# --- anchor monkeypatch state (read by patched _public_random_value_floors wrapper) ---
_ANCHOR_ARMED = False       # when True, the engine uses ANCHOR_AVG_T + capacity guard
_CAP_UPPER: float | None = None

_orig_floors = eng._public_random_value_floors


def _patched_floors(public_info):
    """Pass-through, but when the anchor is armed apply the capacity guard by zeroing
    the floor rows whose draw_total exceeds CAP_SLACK*cap_upper, so the in-engine
    anchor (and the soft random floor) never fires on a damaged floor>capacity frame."""
    rows = _orig_floors(public_info)
    if not _ANCHOR_ARMED or _CAP_UPPER is None:
        return rows
    kept = tuple((sc, vf) for (sc, vf) in rows if vf <= CAP_SLACK * _CAP_UPPER)
    return kept


eng._public_random_value_floors = _patched_floors


def _run(snapshot, *, max_combos, variant, cap_upper):
    global _ANCHOR_ARMED, _CAP_UPPER
    saved_thr = eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD
    saved_mul = None
    _ANCHOR_ARMED = variant in ("H_ANCHOR", "BOTH")
    _CAP_UPPER = cap_upper
    try:
        if variant in ("H_ANCHOR", "BOTH"):
            eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = ANCHOR_AVG_T
        res = eng.run_reference_engine(snapshot, max_combos=max_combos)
        return _apply_hard_floor(snapshot, res, apply=variant in ("H_FLOOR", "BOTH"),
                                 cap_upper=cap_upper)
    finally:
        eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = saved_thr
        _ANCHOR_ARMED = False
        _CAP_UPPER = None


def _apply_hard_floor(snapshot, res, *, apply, cap_upper):
    if not apply:
        return res
    ev = eng.extract_evidence(eng.prepare_reference_engine_snapshot(snapshot))
    floor = eng._random_value_floor(ev)
    if floor is None:
        return res
    if cap_upper is not None and floor > CAP_SLACK * cap_upper:
        return res
    cons = max(res.conservative or 0, int(round(floor)))
    bal = max(res.balanced or 0, int(round(floor)))
    agg = max(res.aggressive or 0, bal)
    return dataclasses.replace(res, conservative=cons, balanced=bal, aggressive=agg)


def run_all_variants(snapshot, *, max_combos):
    """BASE first to derive cap_upper, then the three guarded variants."""
    base = _run(snapshot, max_combos=max_combos, variant="BASE", cap_upper=None)
    cap_upper = float(base.aggressive or 0) or None  # p90-derived total = bid-time upper
    out = {"BASE": base}
    for v in ("H_ANCHOR", "H_FLOOR", "BOTH"):
        out[v] = _run(snapshot, max_combos=max_combos, variant=v, cap_upper=cap_upper)
    return out, cap_upper


# ---------------- 2406 replay ----------------
def cmd_2406():
    base_dir = ".tmp/exp2406/round_snapshots"
    TRUTH, FLOOR_PUB, RED_TRUTH = 633_151, 281_487, 475_538
    print(f"TRUTH total={TRUTH}  public_floor={FLOOR_PUB}  red_truth={RED_TRUTH}\n")
    hdr = f"{'rnd':>4} {'variant':>9} {'cons':>9} {'参考bal':>9} {'agg':>9} {'p50':>9}  note"
    print(hdr)
    for r in ("r01", "r02", "r03", "r04"):
        bid = json.load(open(f"{base_dir}/{r}_bidding.json", "r", encoding="utf-8"))
        snap = eng.prepare_reference_engine_snapshot(bid)
        rn = eng._snapshot_round_no(snap) or int(r[2:])
        mc = hero_max_combos_for_round("aisha", rn) or 50000
        variants, cap = run_all_variants(bid, max_combos=mc)
        for v, res in variants.items():
            flag = ""
            if (res.balanced or 0) > TRUTH * 1.10:
                flag = "OVER!"
            elif (res.balanced or 0) < FLOOR_PUB:
                flag = "<pubfloor"
            print(f"{rn:>4} {v:>9} {res.conservative or 0:>9} {res.balanced or 0:>9} "
                  f"{res.aggressive or 0:>9} {res.value_p50 or 0:>9}  {flag}")
        print(f"     cap_upper(base agg)={int(cap or 0)}")
        print()


# ---------------- full-library aisha sweep ----------------
HERO_TOKENS = ("aisha",)


def _aisha_signal_files():
    """Find aisha bidding snapshots that carry a public_random_avg signal."""
    cands = []
    for pat in ("data/logs/**/round_snapshots/*bidding*.json",
                "data/logs/**/*aisha*bidding*.json",
                "data/**/*aisha*.json"):
        cands.extend(glob.glob(pat, recursive=True))
    seen = set()
    files = []
    for f in cands:
        if f in seen:
            continue
        seen.add(f)
        low = f.lower()
        if "26" == os.path.basename(f)[:2] or re.search(r"[/\\]26\d\d", low):
            continue  # exclude 26xx hidden maps
        files.append(f)
    return files


def cmd_sweep(limit=None):
    files = _aisha_signal_files()
    print(f"candidate files: {len(files)}")
    t0 = time.time()
    n = 0
    rows = []
    for f in files:
        try:
            bid = json.load(open(f, "r", encoding="utf-8"))
        except Exception:
            continue
        try:
            snap = eng.prepare_reference_engine_snapshot(bid)
            ev = eng.extract_evidence(snap)
        except Exception:
            continue
        if not ev.random_value_floors:
            continue
        floor = eng._random_value_floor(ev)
        avg = max((vf / sc for sc, vf in ev.random_value_floors if sc > 0), default=0)
        rn = eng._snapshot_round_no(snap) or 1
        mc = hero_max_combos_for_round("aisha", rn) or 50000
        try:
            variants, cap = run_all_variants(bid, max_combos=mc)
        except Exception:
            continue
        rows.append({"f": f, "rn": rn, "avg": avg, "floor": floor, "cap": cap,
                     "base_bal": variants["BASE"].balanced,
                     "anchor_bal": variants["H_ANCHOR"].balanced,
                     "floor_bal": variants["H_FLOOR"].balanced,
                     "both_bal": variants["BOTH"].balanced})
        n += 1
        if limit and n >= limit:
            break
        if n % 10 == 0:
            el = time.time() - t0
            print(f"  {n} done {el:.0f}s eta~{el/n*(len(files)-n):.0f}s", flush=True)
    out = ".tmp/ab_random_red_anchor_sweep.json"
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {out}  n={len(rows)}  elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "2406"
    if arg == "2406":
        cmd_2406()
    elif arg == "sweep":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
        cmd_sweep(lim)
