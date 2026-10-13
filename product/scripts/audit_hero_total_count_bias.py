"""Per-hero total-count (and per-quality) estimation bias at the last pre-settlement round.

Reusable calibration audit (consolidated from the throwaway draw-hero bias probe). For every
hero present in data/samples/fatbeans, replay the engine at the last pre-settlement batch and
compare its count estimate (range mid) against settlement truth. Reports signed bias + MAE +
spread per hero, which drives:
  - draw-hero total-count prior calibration (which heroes systematically under-estimate),
  - the signalled-vs-draw classification (signalled heroes sit near 0 bias),
  - P2 confidence scoring (per-hero MAE / dispersion is the inherent-variance signal).

Run: python scripts/audit_hero_total_count_bias.py [hero ...]
With no args, audits every hero found in the sample dir.
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

QMAP = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}
SAMPLE_DIR = ROOT / "data/samples/fatbeans"
# Signalled heroes have plenty of samples; a cap keeps the audit fast without changing the
# per-hero bias signal (we only need a representative sample to classify, not exhaustive truth).
MAX_PER_HERO = 40


def _heroes_present() -> list[str]:
    names = set()
    for f in glob.glob(str(SAMPLE_DIR / "*.json")):
        stem = Path(f).stem  # fatbeans_<kind>_<hero>_<map>_<rounds>_...
        parts = stem.split("_")
        if len(parts) >= 3:
            names.add(parts[2])
    return sorted(names)


def _audit_hero(hero: str) -> dict | None:
    files = sorted(glob.glob(str(SAMPLE_DIR / f"*_{hero}_*.json")))[:MAX_PER_HERO]
    bias: dict[str, list[int]] = defaultdict(list)
    mae: dict[str, list[int]] = defaultdict(list)
    ti_list: list[int] = []
    n = 0
    for f in files:
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        s = _settlement_breakdown(events)
        truth = {int(k): int(v) for k, v in (s.get("counts") or {}).items()}
        ti = int(s.get("total_items") or 0)
        if not truth or ti <= 0:
            continue
        map_id = _map_id_from_events(events)
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            continue
        rno = len(pre)  # last pre-settlement round
        last = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=rno, map_id=map_id)
        ctx = last.setdefault("ui_contract", {}).setdefault("context", {})
        ctx["hero"] = hero  # replay reconstruction omits hero; engine gate needs it
        n += 1
        ti_list.append(ti)
        res = run_reference_engine(prepare_reference_engine_snapshot(last), max_combos=8000).as_dict()
        ranges = res.get("quality_count_ranges") or {}
        tot_mid = sum(int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QMAP)
        bias["TOTAL"].append(tot_mid - ti)
        for q, qs in QMAP.items():
            t = sum(truth.get(x, 0) for x in qs)
            m = (ranges.get(q) or [None, None, None])[1]
            if m is not None:
                bias[q].append(m - t)
                mae[q].append(abs(m - t))
    if n == 0:
        return None
    tb = bias["TOTAL"]
    return {
        "hero": hero,
        "n": n,
        "mean_truth": statistics.mean(ti_list),
        "total_bias": statistics.mean(tb),
        "total_mae": statistics.mean([abs(x) for x in tb]),
        "total_std": statistics.pstdev(tb) if len(tb) > 1 else 0.0,
        "rel_bias": statistics.mean(tb) / statistics.mean(ti_list) if ti_list else 0.0,
        "q_bias": {q: statistics.mean(bias[q]) for q in QMAP if bias[q]},
        "q_mae": {q: statistics.mean(mae[q]) for q in QMAP if mae[q]},
    }


def main(argv: list[str]) -> int:
    heroes = argv[1:] or _heroes_present()
    rows = []
    for h in heroes:
        r = _audit_hero(h)
        if r:
            rows.append(r)
            print(
                f"[done] {r['hero']:12s} n={r['n']:>3d} bias={r['total_bias']:+.1f} "
                f"rel={100 * r['rel_bias']:+.0f}%",
                flush=True,
            )
    rows.sort(key=lambda r: r["rel_bias"])  # most under-estimated first
    print()
    print(f"{'hero':12s} {'n':>3s} {'truth':>6s} {'totBias':>8s} {'rel%':>6s} {'MAE':>5s} {'std':>5s}")
    print("-" * 56)
    for r in rows:
        print(
            f"{r['hero']:12s} {r['n']:>3d} {r['mean_truth']:>6.0f} "
            f"{r['total_bias']:>+8.1f} {100 * r['rel_bias']:>+5.0f} "
            f"{r['total_mae']:>5.1f} {r['total_std']:>5.1f}"
        )
    print()
    for r in rows:
        qb = "  ".join(f"{q}={r['q_bias'][q]:+.1f}" for q in QMAP if q in r["q_bias"])
        print(f"{r['hero']:12s} {qb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
