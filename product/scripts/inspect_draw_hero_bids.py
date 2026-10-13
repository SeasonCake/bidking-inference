"""Inspect the 3-tier bids (conservative/balanced/aggressive) vs truth for a hero.

Read-only. For each decision-round sample it prints the engine tiers, the value
percentiles they come from, and the settled truth value, so we can see whether a
draw-hero's bids are systematically off (the "weird sophie bid" report).

Run: python scripts/inspect_draw_hero_bids.py [hero] [n]
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


def _money(x) -> str:
    try:
        return f"{int(x):>11,}"
    except Exception:
        return f"{str(x):>11s}"


def main(argv: list[str]) -> int:
    hero = argv[1] if len(argv) > 1 else "sophie"
    cap = int(argv[2]) if len(argv) > 2 else 30
    rows = []
    brackets = {"cons_over": 0, "agg_under": 0, "n": 0}
    print(f"{'session':>20s} {'cons':>11s} {'bal':>11s} {'aggr':>11s} | "
          f"{'p25':>11s} {'p50':>11s} {'p75':>11s} | {'TRUTH':>11s}  bracket")
    for f in sorted(glob.glob(str(SAMPLE_DIR / f"*_{hero}_*.json")))[:cap]:
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        s = _settlement_breakdown(events)
        truth_value = int(s.get("total_value") or 0)
        if truth_value <= 0:
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
        cons = res.get("conservative") or 0
        bal = res.get("balanced") or 0
        aggr = res.get("aggressive") or 0
        p25 = res.get("value_p25") or 0
        p50 = res.get("value_p50") or 0
        p75 = res.get("value_p75") or 0
        sess = (s.get("session") or Path(f).stem)[-20:]
        # bracket: does [cons, aggr] contain truth?
        if cons > truth_value:
            mark = "CONS>TRUTH"
            brackets["cons_over"] += 1
        elif aggr < truth_value:
            mark = "AGGR<TRUTH"
            brackets["agg_under"] += 1
        else:
            mark = "ok"
        brackets["n"] += 1
        rows.append((p50, truth_value, bal))
        print(f"{sess:>20s} {_money(cons)} {_money(bal)} {_money(aggr)} | "
              f"{_money(p25)} {_money(p50)} {_money(p75)} | {_money(truth_value)}  {mark}")

    if rows:
        bal_bias = statistics.mean([b - t for _, t, b in rows])
        p50_bias = statistics.mean([p - t for p, t, _ in rows])
        bal_over = sum(1 for _, t, b in rows if b > t)
        print(f"\nn={brackets['n']}  balanced bias(mean)={bal_bias:+,.0f}  "
              f"p50 bias(mean)={p50_bias:+,.0f}")
        print(f"balanced > truth in {bal_over}/{len(rows)} samples "
              f"({100*bal_over/len(rows):.0f}%)")
        print(f"bracket misses: CONS>TRUTH={brackets['cons_over']} "
              f"AGGR<TRUTH={brackets['agg_under']} (want both ~0; truth inside [cons,aggr])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
