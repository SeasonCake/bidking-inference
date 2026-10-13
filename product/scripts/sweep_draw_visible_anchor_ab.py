"""A/B grid-accuracy sweep for the draw-hero visible-anchor + sparse-soft-target-exemption fix.

For every draw-hero (sophie/gabriela/raven) sample with a settlement truth, reconstruct the LAST
pre-settlement snapshot (most-informed estimate) and run the engine with the fix ON (default) vs
OFF (DISABLE_DRAW_VISIBLE_ANCHOR=1, which restores the old hard-capped sparse target). Reports:
  - status transitions (how many no_reachable_combo blanks the fix UNBLOCKS into an estimate),
  - grid bias/MAE on the subset where BOTH sides produce a grid (regression guard for shallow vaults).

Usage: python scripts/sweep_draw_visible_anchor_ab.py [--limit N] [--heroes sophie,gabriela]
Throwaway A/B harness — retire once the draw visible-anchor path is settled.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402
from build_clean_eval_set import (  # noqa: E402
    activity_eval_files,
    clean_eval_files,
    sample_selection_metadata,
)


def _last_pre_snapshot(sample: Path):
    events = parse_fatbeans_capture(sample)
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    if not pre:
        return None, None, None
    map_id = _map_id_from_events(events)
    snap = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id)
    truth = int((_settlement_breakdown(events) or {}).get("total_cells") or 0)
    return snap, len(pre), truth


def _grid_mid(snap, hero: str, round_no: int):
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap), max_combos=hero_max_combos_for_round(hero, round_no)
    ).as_dict()
    grid = res.get("total_grid_range") or [None, None, None]
    mid = int(grid[1]) if grid[1] is not None else None
    return res.get("status"), mid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap samples per hero (0=all)")
    ap.add_argument("--heroes", default="sophie,gabriela,raven")
    ap.add_argument(
        "--activity",
        action="store_true",
        help="Use the explicit 20260702 shipwreck activity pool instead of clean-eval",
    )
    args = ap.parse_args()
    heroes = [h.strip() for h in args.heroes.split(",") if h.strip()]

    samples: list[tuple[str, Path]] = []
    for hero in heroes:
        selected = activity_eval_files(hero) if args.activity else clean_eval_files(hero)
        hs = [Path(path) for path in selected]
        if args.limit:
            hs = hs[: args.limit]
        samples.extend((hero, s) for s in hs)
    total = len(samples)
    selector = ("activity_20260702:" if args.activity else "clean_eval:") + ",".join(heroes)
    if args.limit:
        selector += f":limit_per_hero={args.limit}"
    metadata = sample_selection_metadata((path for _, path in samples), selector=selector)
    print(f"run_metadata={metadata}", flush=True)
    print(f"draw A/B sweep: {total} samples ({', '.join(heroes)})", flush=True)

    trans: Counter = Counter()  # (status_off -> status_on)
    both: list[tuple[str, int, int, int]] = []  # name, truth, mid_off, mid_on
    unblocked: list[tuple[str, int, int]] = []  # name, truth, mid_on (OFF was no_reachable)
    start = time.time()
    for k, (hero, sample) in enumerate(samples, 1):
        try:
            snap, round_no, truth = _last_pre_snapshot(sample)
        except Exception:  # noqa: BLE001
            continue
        if snap is None or truth <= 0:
            continue
        os.environ["DISABLE_DRAW_VISIBLE_ANCHOR"] = "1"
        try:
            st_off, mid_off = _grid_mid(snap, hero, round_no)
        except Exception:  # noqa: BLE001
            st_off, mid_off = "ERR", None
        os.environ.pop("DISABLE_DRAW_VISIBLE_ANCHOR", None)
        try:
            st_on, mid_on = _grid_mid(snap, hero, round_no)
        except Exception:  # noqa: BLE001
            st_on, mid_on = "ERR", None
        trans[(st_off, st_on)] += 1
        if mid_off is not None and mid_on is not None:
            both.append((sample.name, truth, mid_off, mid_on))
        elif mid_off is None and mid_on is not None:
            unblocked.append((sample.name, truth, mid_on))
        if k % 10 == 0 or k == total:
            el = time.time() - start
            eta = el / k * (total - k)
            print(f"  [progress] {k}/{total}  elapsed={el:.0f}s  eta~{eta:.0f}s", flush=True)

    print("\n=== status transitions (OFF -> ON) ===")
    for (a, b), n in sorted(trans.items(), key=lambda kv: -kv[1]):
        print(f"  {a:>20s} -> {b:<20s} : {n}")

    print(f"\n=== UNBLOCKED (OFF blank/no_reachable -> ON estimate): {len(unblocked)} ===")
    for name, truth, mid in unblocked:
        print(f"  {name[:50]:50s} truth={truth:>4d} ON_mid={mid:>4d} d={mid - truth:>+4d}")

    print(f"\n=== both produced a grid: {len(both)} (shallow-vault regression guard) ===")
    if both:
        boff = sum(o - t for _, t, o, _ in both) / len(both)
        bon = sum(n - t for _, t, _, n in both) / len(both)
        moff = sum(abs(o - t) for _, t, o, _ in both) / len(both)
        mon = sum(abs(n - t) for _, t, _, n in both) / len(both)
        changed = [(nm, t, o, n) for nm, t, o, n in both if o != n]
        print(f"  bias OFF={boff:+.1f}  ON={bon:+.1f}   MAE OFF={moff:.1f}  ON={mon:.1f}   changed={len(changed)}")
        for nm, t, o, n in changed[:25]:
            print(f"    {nm[:48]:48s} truth={t:>4d} OFF={o:>4d} ON={n:>4d}  dOFF={o - t:>+4d} dON={n - t:>+4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
