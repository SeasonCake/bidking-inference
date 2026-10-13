"""Full-library aisha A/B: BASE vs anchor/floor variants vs settled truth.

Read-only monkeypatch. For each aisha fatbeans sample, replay every pre-settle
batch (bidding round), run the engine under each variant, and compare 参考(balanced)
to settlement truth_total. Reports bias, MAE, over>10% rate, under-rate, and the
phantom-red (true red==0) over-rate (F1 regression probe).

Variants (see ab_random_red_anchor for semantics):
  BASE, H_ANCHOR (thr=20k, value_floor=0.88*draw_total, capacity guard),
  H_FLOOR (cons/bal>=draw_total, guard), BOTH.

Usage: python scripts/ab_anchor_fulllib.py [limit] [timeout_s]
"""
from __future__ import annotations
import sys, glob, time, json, statistics, dataclasses
from pathlib import Path
for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ahmad_ref_engine as eng  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture, live_batches_from_fatbeans_events  # noqa: E402
from audit_aisha_gap import _settlement_breakdown, _map_id_from_events  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

ANCHOR_AVG_T = 20_000.0
ANCHOR_VALUE_MUL = 0.88
CAP_SLACK = 1.15

# info_id -> sample_count N for the public "random N items avg value" facts. value=per-item avg.
RANDOM_N_BY_ID = {200031: 3, 200032: 6, 200033: 9, 200034: 12}


def _raw_random_avg_by_n(events) -> dict[int, float]:
    """Best (max) per-item avg per N from raw pre-settle frames. NEVER read settlement."""
    best: dict[int, float] = {}
    for st in getattr(events, "states", []) or []:
        if getattr(st, "settlement_loss_units", None) is not None:
            continue
        for pi in getattr(st, "public_infos", ()) or ():
            n = RANDOM_N_BY_ID.get(int(getattr(pi, "info_id", 0)))
            if n is None:
                continue
            v = float(getattr(pi, "value", 0) or 0)
            if v > 0:
                best[n] = max(best.get(n, 0.0), v)
    return best


def _inject_random_avg(snap: dict, avg_by_n: dict[int, float]) -> dict:
    """Reconstructed _snapshot_at_batch frames drop public_random_avg_values; re-inject
    them so _public_random_value_floors can see the signal (matches the export format)."""
    if not avg_by_n:
        return snap
    pub = snap.setdefault("ui_contract", {}).setdefault("constraints", {}).setdefault("public_info", {})
    rows = [
        {"info_id": next(k for k, vn in RANDOM_N_BY_ID.items() if vn == n),
         "semantic": f"random_{n}_avg_value", "kind": "random_avg_value",
         "sample_count": n, "value": float(avg), "label": f"随机{n}均价"}
        for n, avg in sorted(avg_by_n.items())
    ]
    pub["public_random_avg_values"] = rows
    return snap

_ANCHOR_ARMED = False
_CAP_UPPER = None
_COUNT_ONLY = False  # when True, strip the anchor's q6 value floor, keep only min_count q6>=1
_orig_floors = eng._public_random_value_floors
_orig_extract = eng.extract_evidence


def _patched_extract(snap):
    """When COUNT_ONLY+armed, recompute the q6 floor WITHOUT the anchor's value write:
    run the un-armed extract (threshold restored) to get the natural q6 floor, then
    overlay only the anchor's min_count q6>=1. This isolates 'disarm F1 via count' from
    'inject the inflated draw_total known-sum'."""
    if not (_COUNT_ONLY and _ANCHOR_ARMED):
        return _orig_extract(snap)
    saved = eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD
    eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = 1e18  # disarm in-engine anchor entirely
    try:
        ev = _orig_extract(snap)
    finally:
        eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = saved
    # overlay min_count q6>=1 iff the armed threshold WOULD have fired (avg>=ANCHOR_AVG_T,
    # floor within capacity guard) -> disarms F1 without the inflated value floor.
    avg = max((vf / sc for sc, vf in ev.random_value_floors if sc > 0), default=0.0)
    floor = eng._random_value_floor(ev) or 0.0
    armed = avg >= ANCHOR_AVG_T and not (_CAP_UPPER and floor > CAP_SLACK * _CAP_UPPER)
    if armed and int(ev.min_counts.get("q6", 0) or 0) < 1:
        ev.min_counts["q6"] = 1
    return ev


eng.extract_evidence = _patched_extract


def _patched_floors(public_info):
    rows = _orig_floors(public_info)
    if not _ANCHOR_ARMED or _CAP_UPPER is None:
        return rows
    return tuple((sc, vf) for (sc, vf) in rows if vf <= CAP_SLACK * _CAP_UPPER)


eng._public_random_value_floors = _patched_floors


def _run(snapshot, *, max_combos, variant, cap_upper):
    global _ANCHOR_ARMED, _CAP_UPPER, _COUNT_ONLY
    saved = eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD
    _ANCHOR_ARMED = variant in ("H_ANCHOR", "BOTH", "H_COUNT")
    _COUNT_ONLY = variant == "H_COUNT"
    _CAP_UPPER = cap_upper
    try:
        if variant in ("H_ANCHOR", "BOTH", "H_COUNT"):
            eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = ANCHOR_AVG_T
        res = eng.run_reference_engine(snapshot, max_combos=max_combos)
        if variant in ("H_FLOOR", "BOTH"):
            ev = eng.extract_evidence(eng.prepare_reference_engine_snapshot(snapshot))
            floor = eng._random_value_floor(ev)
            if floor is not None and not (cap_upper and floor > CAP_SLACK * cap_upper):
                cons = max(res.conservative or 0, int(round(floor)))
                bal = max(res.balanced or 0, int(round(floor)))
                agg = max(res.aggressive or 0, bal)
                res = dataclasses.replace(res, conservative=cons, balanced=bal, aggressive=agg)
        return res
    finally:
        eng.RANDOM_AVG_RED_ANCHOR_THRESHOLD = saved
        _ANCHOR_ARMED = False
        _COUNT_ONLY = False
        _CAP_UPPER = None


VARIANTS = ("BASE", "H_COUNT", "H_ANCHOR", "H_FLOOR", "BOTH")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else None
    budget = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() else 1500.0
    files = sorted(glob.glob("data/samples/fatbeans/*aisha*.json"))
    print(f"aisha fatbeans samples: {len(files)}  budget={budget}s", flush=True)
    t0 = time.time()
    recs = []  # per (sample,round): {variant: bal}, truth, red_count, has_signal
    nsamp = 0
    for f in files:
        if budget and time.time() - t0 > budget:
            print(f"[budget hit at {time.time()-t0:.0f}s, stopping]", flush=True)
            break
        try:
            events = parse_fatbeans_capture(Path(f))
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            if not pre:
                continue
            map_id = _map_id_from_events(events)
            if map_id is not None and 2600 <= int(map_id) <= 2699:
                continue  # exclude 26xx hidden
            s = _settlement_breakdown(events)
            truth = int(s.get("total_value") or 0)
            red_count = int((s.get("counts") or {}).get(6, 0) or 0)
            if truth <= 0:
                continue
        except Exception:
            continue
        # cheap pre-scan: does ANY pre-settle batch carry a random-avg signal? Skip the
        # whole (expensive) engine sweep for samples with no signal -- variants are no-ops
        # there and only inflate runtime. We still keep a thin BASE pass on a capped set of
        # no-signal phantom samples to confirm F1 regression baseline (handled separately).
        avg_by_n = _raw_random_avg_by_n(events)
        sample_has_sig = False
        snaps = {}
        for idx in range(1, len(pre) + 1):
            try:
                snap = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=idx, map_id=map_id)
            except Exception:
                continue
            if snap is None:
                continue
            snap = _inject_random_avg(snap, avg_by_n)
            snaps[idx] = snap
            ev0 = _orig_extract(eng.prepare_reference_engine_snapshot(snap))
            if ev0.random_value_floors:
                sample_has_sig = True
        if not sample_has_sig:
            continue  # no signal anywhere -> variants identical to BASE, skip heavy runs
        for idx, snap in snaps.items():
            ev = _orig_extract(eng.prepare_reference_engine_snapshot(snap))
            has_sig = bool(ev.random_value_floors)
            rn = eng._snapshot_round_no(snap) or idx
            mc = hero_max_combos_for_round("aisha", rn) or 50000
            try:
                base = _run(snap, max_combos=mc, variant="BASE", cap_upper=None)
                cap = float(base.aggressive or 0) or None
                row = {"f": Path(f).name, "rn": rn, "truth": truth, "red": red_count,
                       "sig": has_sig, "BASE": base.balanced or 0}
                if has_sig:
                    for v in ("H_COUNT", "H_ANCHOR", "H_FLOOR", "BOTH"):
                        row[v] = _run(snap, max_combos=mc, variant=v, cap_upper=cap).balanced or 0
                else:
                    # no random signal -> every variant is a no-op equal to BASE
                    for v in ("H_COUNT", "H_ANCHOR", "H_FLOOR", "BOTH"):
                        row[v] = base.balanced or 0
                recs.append(row)
            except Exception:
                continue
        nsamp += 1
        if limit and nsamp >= limit:
            break
        if nsamp % 20 == 0:
            el = time.time() - t0
            print(f"  {nsamp} samples, {len(recs)} rounds, {el:.0f}s", flush=True)

    json.dump(recs, open(".tmp/ab_anchor_fulllib.json", "w", encoding="utf-8"), ensure_ascii=False)
    _report(recs, nsamp, time.time() - t0)


def _report(recs, nsamp, elapsed):
    print(f"\n=== {nsamp} samples, {len(recs)} rounds, {elapsed:.0f}s ===")
    sig = [r for r in recs if r["sig"]]
    phantom = [r for r in recs if r["red"] == 0]
    phantom_sig = [r for r in phantom if r["sig"]]
    print(f"rounds with random signal: {len(sig)}   phantom(red==0): {len(phantom)} "
          f"(with signal: {len(phantom_sig)})\n")

    def stats(rows, v):
        if not rows:
            return None
        biases = [(r[v] - r["truth"]) / r["truth"] for r in rows]
        over = sum(1 for r in rows if r[v] > r["truth"] * 1.10) / len(rows)
        under = sum(1 for r in rows if r[v] < r["truth"] * 0.90) / len(rows)
        mae = statistics.mean(abs(r[v] - r["truth"]) for r in rows) / 1000
        return (statistics.mean(biases), statistics.median(biases), over, under, mae)

    def block(title, rows):
        print(f"-- {title} (n={len(rows)}) --")
        print(f"{'variant':>9} {'mean_bias':>10} {'med_bias':>9} {'over>10%':>9} {'under<-10%':>11} {'MAE/k':>8}")
        for v in VARIANTS:
            st = stats(rows, v)
            if st:
                print(f"{v:>9} {st[0]:>+10.3f} {st[1]:>+9.3f} {st[2]:>9.2%} {st[3]:>11.2%} {st[4]:>8.0f}")
        print()

    block("ALL rounds", recs)
    block("SIGNAL rounds (random-avg present)", sig)
    block("PHANTOM red==0 (F1 regression probe)", phantom)
    if phantom_sig:
        block("PHANTOM red==0 WITH signal (误锚 probe)", phantom_sig)

    # phantom over-rate detail: did anchor/floor newly push a phantom over truth?
    print("-- phantom red==0: newly-over (was<=1.1truth in BASE, now>1.1truth) --")
    for v in ("H_COUNT", "H_ANCHOR", "H_FLOOR", "BOTH"):
        newly = [r for r in phantom if r["BASE"] <= r["truth"] * 1.1 and r[v] > r["truth"] * 1.1]
        print(f"  {v}: {len(newly)} newly-over", end="")
        if newly:
            ex = max(newly, key=lambda r: r[v] / r["truth"])
            print(f"  worst={ex['f']} r{ex['rn']} {ex['BASE']}->{ex[v]} truth={ex['truth']}", end="")
        print()


if __name__ == "__main__":
    main()
