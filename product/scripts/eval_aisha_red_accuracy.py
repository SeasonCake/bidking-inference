"""Red (q6) accuracy eval for aisha across the sample library, bucketed by true red level.

Hypothesis under test (live-observed): the red REFERENCE (p50) is anchored near the prior center
~2 and barely tracks evidence -> over-estimates LOW-red vaults (truth 0-1) and under-estimates
HIGH-red ones (truth 4+). This eval quantifies that per bucket before any count_prior change, since
red drives a core slice of the warehouse value/price gap.

For every aisha 4-round sample with a settlement truth, reconstruct the R4 decision-round snapshot,
run the engine, and compare engine red (count/cells/value p10/p50/p90) vs settlement truth red.
Buckets by truth red COUNT: low=0-1, mid=2-3, high>=4. Reports per bucket: n, count/cells bias+MAE
on the p50 reference, and band coverage (truth within [p10,p90]).

Usage: python scripts/eval_aisha_red_accuracy.py [--limit N] [--round 4]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"


def _bucket(truth_red_count: int) -> str:
    if truth_red_count <= 1:
        return "low(0-1)"
    if truth_red_count <= 3:
        return "mid(2-3)"
    return "high(4+)"


def _mid(triplet) -> int | None:
    if not isinstance(triplet, (list, tuple)) or len(triplet) < 2 or triplet[1] is None:
        return None
    return int(triplet[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--round", type=int, default=4, help="decision round to evaluate (3 or 4)")
    args = ap.parse_args()
    skill_idx = args.round - 1
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[skill_idx]

    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    print(f"red accuracy eval: {total} aisha 4round samples @ R{skill_round}", flush=True)

    # bucket -> list of (name, truth_cnt, eng_cnt_p50, truth_cells, eng_cells_p50, cnt_covered, cells_covered)
    rows: dict[str, list] = defaultdict(list)
    skipped = 0
    start = time.time()
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_cnt = int((truth.get("counts") or {}).get(6, 0))
            t_cells = int((truth.get("cells") or {}).get(6, 0))
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            skipped += 1
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        res = run_reference_engine(
            prepare_reference_engine_snapshot(snap), max_combos=hero_max_combos_for_round("aisha", skill_round)
        ).as_dict()
        rc = res.get("red_count_range") or [None, None, None]
        xc = (res.get("quality_cells_ranges") or {}).get("q6") or [None, None, None]
        e_cnt = _mid(rc)
        e_cells = _mid(xc)
        rv = res.get("red_value_range") or [None, None, None]
        e_val = _mid(rv)
        t_val = int((truth.get("values") or {}).get(6, 0))
        ev = res.get("evidence") or {}
        min_red = int((ev.get("min_counts") or {}).get("q6", 0) or 0)
        if e_cnt is None or e_cells is None:
            skipped += 1
            continue
        cnt_cov = rc[0] is not None and rc[2] is not None and rc[0] <= t_cnt <= rc[2]
        cells_cov = xc[0] is not None and xc[2] is not None and xc[0] <= t_cells <= xc[2]
        rows[_bucket(t_cnt)].append((sample.name, t_cnt, e_cnt, t_cells, e_cells, cnt_cov, cells_cov, t_val, e_val or 0, min_red))
        if k % 20 == 0 or k == total:
            el = time.time() - start
            used = sum(len(v) for v in rows.values())
            print(f"  [progress] {k}/{total} used={used} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    print(f"\n{'bucket':10s} {'n':>4s} | {'cntBias':>8s} {'cntMAE':>7s} {'cntCov':>7s} | {'cellBias':>9s} {'cellMAE':>8s} {'cellCov':>8s}")
    for bucket in ("low(0-1)", "mid(2-3)", "high(4+)"):
        r = rows[bucket]
        if not r:
            print(f"{bucket:10s} {0:>4d} | (no samples)")
            continue
        n = len(r)
        cnt_bias = sum(row[2] - row[1] for row in r) / n
        cnt_mae = sum(abs(row[2] - row[1]) for row in r) / n
        cnt_cov = sum(1 for row in r if row[5]) / n
        cell_bias = sum(row[4] - row[3] for row in r) / n
        cell_mae = sum(abs(row[4] - row[3]) for row in r) / n
        cell_cov = sum(1 for row in r if row[6]) / n
        val_bias = sum((row[8] - row[7]) for row in r) / n / 1000.0
        val_mae = sum(abs(row[8] - row[7]) for row in r) / n / 1000.0
        avg_eng_red = sum(row[2] for row in r) / n
        avg_truth_red = sum(row[1] for row in r) / n
        frac_revealed = sum(1 for row in r if row[9] > 0) / n
        print(f"{bucket:10s} {n:>4d} | truthRed~{avg_truth_red:>4.1f} engRedP50~{avg_eng_red:>4.1f} | cntBias{cnt_bias:>+6.2f} valBias{val_bias:>+6.0f}k | revealedRed_frac={frac_revealed:>4.0%}")

    print(f"\nskipped={skipped}  used={sum(len(v) for v in rows.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
