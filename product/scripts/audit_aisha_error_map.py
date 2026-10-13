"""Phase A error map: where does aisha estimation under/over-estimate, and is value error real?

Splits the estimate error into the three things that matter for bid accuracy, per map-family and
per round, at the decision (last pre-settlement) round:
  - GRID  (total cells): the truly-estimable target -- engine total_grid_range mid vs truth cells.
  - COUNT (total + per-quality): engine quality_count_ranges mid vs truth counts.
  - VALUE: engine value_p50 vs truth, reported TWO ways:
      * raw    = vs actual settled value (dominated by long-tail reds -- a same-shape red can be
                 20-40x its typical value, so this error is largely irreducible luck).
      * robust = vs a long-tail-replaced truth, where each item's value is swapped for the EXPECTED
                 value of its (quality, cells) shape (mean over the whole sample lib). If robust MAE
                 << raw MAE, the value error is long-tail noise, not a model defect -> focus effort
                 on GRID accuracy + weighted pricing, not on chasing exact value.

Read-only. Run: python scripts/audit_aisha_error_map.py [hero] [n]
"""

from __future__ import annotations

import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402
from synthesize_draw_hero_samples import load_warehouse  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"
QMAP = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}


def _shape_expected_value(cap: int = 600) -> tuple[dict, dict]:
    """Map-probability-weighted expected value per shape, the long-tail replacement basis.

    Returns (by_family, by_global):
      by_family[(family, quality, cells)] = median value of items of that shape on that map family
      by_global[(quality, cells)]         = median value of that shape across the whole lib
    The empirical per-(family, shape) median IS the map-probability-weighted average red price the
    user described -- the sample distribution already reflects each map's drop probabilities -- and
    MEDIAN (not mean) keeps it long-tail-robust (a single 7.4M red must not drag the expected price).
    by_global is the fallback for shapes too thin on a given family.
    """
    fam_buckets: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    glob_buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for f in sorted(glob.glob(str(SAMPLE_DIR / "*.json")))[:cap]:
        wh = load_warehouse(Path(f))
        if not wh:
            continue
        fam = (wh.map_id or 0) // 100
        for it in wh.items:
            fam_buckets[(fam, it["quality"], it["cells"])].append(it["value"])
            glob_buckets[(it["quality"], it["cells"])].append(it["value"])
    by_family = {k: statistics.median(v) for k, v in fam_buckets.items() if len(v) >= 4}
    by_global = {k: statistics.median(v) for k, v in glob_buckets.items() if v}
    return by_family, by_global


def _robust_total_value(events, by_family: dict, by_global: dict, family: int) -> float | None:
    """Long-tail-replaced truth: each item valued at its map-family shape median (fallback global)."""
    for state in reversed(events.states):
        if state.inventory_items:
            total = 0.0
            for it in state.inventory_items:
                q = int(it.quality or 0)
                if q <= 0:
                    continue
                c = int(it.cells or 1)
                v = by_family.get((family, q, c))
                if v is None:
                    v = by_global.get((q, c), 0.0)
                total += v
            return total
    return None


def main(argv: list[str]) -> int:
    hero = argv[1] if len(argv) > 1 else "aisha"
    cap = int(argv[2]) if len(argv) > 2 else 120
    print("building map-family shape-expected-value table...", flush=True)
    by_family, by_global = _shape_expected_value()
    print(
        f"table ready (family-shapes={len(by_family)}, global-shapes={len(by_global)}); "
        f"running {hero} samples...",
        flush=True,
    )

    by_fam: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_round: dict[int, list[float]] = defaultdict(list)  # grid bias by round
    used = 0
    import time as _time
    files = sorted(glob.glob(str(SAMPLE_DIR / f"*_{hero}_*.json")))[:cap]
    _ntot = len(files)
    _t0 = _time.time()
    for _i, f in enumerate(files, 1):
        _el = _time.time() - _t0
        _eta = (_el / (_i - 1)) * (_ntot - _i + 1) if _i > 1 else 0.0
        print(f"[progress] {_i}/{_ntot} used={used} elapsed={_el:.0f}s eta~{_eta:.0f}s", file=sys.stderr, flush=True)
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        s = _settlement_breakdown(events)
        truth_cells = int(s.get("total_cells") or 0)
        truth_counts = {int(k): int(v) for k, v in (s.get("counts") or {}).items()}
        truth_value = int(s.get("total_value") or 0)
        if truth_cells <= 0 or not truth_counts:
            continue
        truth_total = sum(truth_counts.values())
        map_id = _map_id_from_events(events)
        fam = (map_id or 0) // 100
        robust_value = _robust_total_value(events, by_family, by_global, fam) or 0.0
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            continue
        # per-round grid bias
        for rno in range(1, len(pre) + 1):
            snap_r = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=rno, map_id=map_id, hero=hero)
            rr = run_reference_engine(prepare_reference_engine_snapshot(snap_r), max_combos=6000).as_dict()
            grid_mid = (rr.get("total_grid_range") or [None, None, None])[1]
            if grid_mid is not None:
                by_round[min(rno, 5)].append(int(grid_mid) - truth_cells)
        # decision-round (last) full split
        res = run_reference_engine(
            prepare_reference_engine_snapshot(
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id, hero=hero)
            ),
            max_combos=8000,
        ).as_dict()
        grid_mid = (res.get("total_grid_range") or [None, None, None])[1]
        ranges = res.get("quality_count_ranges") or {}
        est_total = sum(int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QMAP)
        p50 = res.get("value_p50") or 0
        fam_d = by_fam[fam]
        if grid_mid is not None:
            fam_d["grid_bias"].append(int(grid_mid) - truth_cells)
        fam_d["count_bias"].append(est_total - truth_total)
        fam_d["value_raw_err"].append(abs(p50 - truth_value))
        fam_d["value_raw_bias"].append(p50 - truth_value)  # signed: + = engine over-estimates
        if robust_value > 0:
            fam_d["value_robust_err"].append(abs(p50 - robust_value))
            fam_d["value_robust_bias"].append(p50 - robust_value)
        used += 1
        if used % 20 == 0:
            print(f"  ...{used} samples done", flush=True)

    def col(vals: list[float], signed: bool = False) -> str:
        if not vals:
            return "   -  "
        m = statistics.mean(vals)
        return f"{m:+6.0f}" if signed else f"{m:6.0f}"

    print(f"=== {hero} error map (decision round, n={used} samples) ===\n")
    print(f"{'family':>7s} {'n':>3s} {'gridBias':>9s} {'gridMAE':>8s} {'cntBias':>8s} {'valRawMAE':>10s} {'valRobMAE':>10s}")
    print("-" * 64)
    for fam in sorted(by_fam):
        d = by_fam[fam]
        n = len(d["count_bias"])
        gb = d["grid_bias"]
        print(
            f"{fam:>7d} {n:>3d} {col(gb, True):>9s} "
            f"{statistics.mean([abs(x) for x in gb]) if gb else 0:>8.1f} "
            f"{col(d['count_bias'], True):>8s} "
            f"{statistics.mean(d['value_raw_err']) if d['value_raw_err'] else 0:>10.0f} "
            f"{statistics.mean(d['value_robust_err']) if d['value_robust_err'] else 0:>10.0f}"
        )
    # overall
    allg = [x for d in by_fam.values() for x in d["grid_bias"]]
    allc = [x for d in by_fam.values() for x in d["count_bias"]]
    allvr = [x for d in by_fam.values() for x in d["value_raw_err"]]
    allvrob = [x for d in by_fam.values() for x in d["value_robust_err"]]
    allvrb = [x for d in by_fam.values() for x in d["value_raw_bias"]]
    allvrobb = [x for d in by_fam.values() for x in d["value_robust_bias"]]
    print("-" * 64)
    print(
        f"{'ALL':>7s} {len(allc):>3d} {statistics.mean(allg):+9.1f} "
        f"{statistics.mean([abs(x) for x in allg]):8.1f} "
        f"{statistics.mean(allc):+8.1f} {statistics.mean(allvr):10.0f} {statistics.mean(allvrob):10.0f}"
    )
    print(
        f"\n[value bias signed]  raw bias(mean)={statistics.mean(allvrb):+.0f}  "
        f"robust(median-truth) bias={statistics.mean(allvrobb):+.0f}  "
        f"(+ = engine over-estimates value)"
    )
    print(
        f"[value median errs]  raw MAE median={statistics.median(allvr):.0f}  "
        f"robust MAE median={statistics.median(allvrob):.0f}"
    )
    print("\n[grid bias by round]")
    for r in sorted(by_round):
        v = by_round[r]
        print(f"  R{r}: bias={statistics.mean(v):+.1f} MAE={statistics.mean([abs(x) for x in v]):.1f} n={len(v)}")
    print("\nCRUX READ: if valRobMAE << valRawMAE -> value error is long-tail luck (focus GRID + weighted price).")
    print("           grid bias sign per family/round shows where to tighten the geometry model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
