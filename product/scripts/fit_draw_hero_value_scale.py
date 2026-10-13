"""Fit the draw-hero value haircut: what scale on the value distribution zeroes the
raw bias (engine p50 vs settled truth) for draw-heroes, without touching aisha/ahmed.

For each decision-round draw-hero sample we collect engine value_p50 and settled
truth value, then report bias-minimizing scales:
  - mean ratio   = sum(truth)/sum(p50)      (zeroes the aggregate signed bias)
  - median ratio = median(truth/p50)        (robust central, ignores long-tail luck)
Also reports the post-scale conservative-over-truth rate at the median scale, the
metric the user actually feels ("the floor bid shouldn't exceed the real value").

Read-only. Run: python scripts/fit_draw_hero_value_scale.py [hero] [n]
"""

from __future__ import annotations

import glob
import statistics
import sys
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

SAMPLE_DIR = ROOT / "data/samples/fatbeans"


def _collect(hero: str, cap: int) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(SAMPLE_DIR / f"*_{hero}_*.json")))[:cap]:
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        s = _settlement_breakdown(events)
        truth = int(s.get("total_value") or 0)
        if truth <= 0:
            continue
        map_id = _map_id_from_events(events)
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            continue
        res = run_reference_engine(
            prepare_reference_engine_snapshot(
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id, hero=hero)
            ),
            max_combos=8000,
        ).as_dict()
        p25 = res.get("value_p25") or 0
        p50 = res.get("value_p50") or 0
        cons = res.get("conservative") or 0
        if p50 <= 0:
            continue
        rows.append({"truth": truth, "p25": p25, "p50": p50, "cons": cons})
    return rows


def main(argv: list[str]) -> int:
    heroes = [argv[1]] if len(argv) > 1 else ["sophie", "gabriela"]
    cap = int(argv[2]) if len(argv) > 2 else 80
    rows = []
    for h in heroes:
        hr = _collect(h, cap)
        print(f"  {h}: n={len(hr)}")
        rows.extend(hr)
    if not rows:
        print("no rows")
        return 1
    sum_truth = sum(r["truth"] for r in rows)
    sum_p50 = sum(r["p50"] for r in rows)
    mean_ratio = sum_truth / sum_p50
    median_ratio = statistics.median(r["truth"] / r["p50"] for r in rows)
    print(f"\nn={len(rows)} heroes={'+'.join(heroes)}")
    print(f"  mean ratio   (zeroes aggregate bias) = {mean_ratio:.3f}")
    print(f"  median ratio (robust central)        = {median_ratio:.3f}")
    print(f"  current p50 bias(mean) = {statistics.mean(r['p50']-r['truth'] for r in rows):+,.0f}")

    print(f"\n{'scale':>6s} {'p50 bias':>12s} {'cons>truth%':>11s} {'cons bias':>12s}")
    for scale in (1.0, 0.90, 0.85, 0.82, 0.80, 0.78, 0.75, 0.70):
        p50b = statistics.mean(scale * r["p50"] - r["truth"] for r in rows)
        consb = statistics.mean(scale * r["cons"] - r["truth"] for r in rows)
        cons_over = sum(1 for r in rows if scale * r["cons"] > r["truth"]) / len(rows) * 100
        print(f"{scale:>6.2f} {p50b:>+12,.0f} {cons_over:>10.0f}% {consb:>+12,.0f}")
    print("\npick: scale where cons>truth% ~ aisha baseline (35%) and p50 bias ~ 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
