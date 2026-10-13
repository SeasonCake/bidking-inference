# -*- coding: utf-8 -*-
"""A/B harness for the draw-hero REVEALED-RED anchor (count Lever1 + cells Lever2).

Both levers default ON with env rollbacks:
  DRAW_HERO_DISABLE_REVEALED_RED_COUNT=1  (Lever1: q6 count anchor toward revealed)
  DRAW_HERO_DISABLE_REVEALED_RED_CELLS=1  (Lever2: q6 cells from known footprint)

For each snapshot we run the engine TWICE in-process by toggling those env vars and
re-importing fresh module state is unnecessary (the funcs read os.environ at call time),
so we just flip the env between runs. We compare full RefResult.as_dict():
  - NO-OP samples (non-draw hero, or draw hero with no revealed red): MUST be byte-identical.
  - FIRES samples (draw hero + revealed red w/ known footprint): expected to differ; we print
    the count/cells/value deltas so a human can judge them against truth.

Usage:
  python scripts/verify_draw_revealed_red.py --glob 'data/samples/**/round_snapshots/*bidding*.json'
  python scripts/verify_draw_revealed_red.py --list samples.json     # JSON array of paths
  python scripts/verify_draw_revealed_red.py PATH1 PATH2 ...
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

import ahmad_ref_engine as E  # noqa: E402

ROLLBACK = {
    "DRAW_HERO_DISABLE_REVEALED_RED_COUNT": "1",
    "DRAW_HERO_DISABLE_REVEALED_RED_CELLS": "1",
}


def _run(path: Path, rollback: bool) -> dict:
    saved = {k: os.environ.get(k) for k in ROLLBACK}
    try:
        for k, v in ROLLBACK.items():
            if rollback:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        return E.run_reference_engine_from_path(path).as_dict()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _canon(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--glob", action="append", default=[])
    ap.add_argument("--list", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show-fires", type=int, default=40, help="max FIRES rows to print")
    args = ap.parse_args()

    paths: list[Path] = [Path(p) for p in args.paths]
    for g in args.glob:
        paths += [Path(p) for p in globlib.glob(str(ROOT / g), recursive=True)]
    if args.list:
        paths += [Path(p) for p in json.loads(Path(args.list).read_text(encoding="utf-8"))]
    # de-dup, keep order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    if args.limit:
        uniq = uniq[: args.limit]

    n_total = 0
    n_err = 0
    n_noop_ok = 0
    n_fires = 0
    noop_breaks: list[str] = []
    fires_rows: list[str] = []
    for p in uniq:
        n_total += 1
        try:
            base = _run(p, rollback=True)   # levers OFF (baseline)
            new = _run(p, rollback=False)   # levers ON (default shipped)
        except Exception as exc:  # noqa: BLE001
            n_err += 1
            print(f"ERR  {p.name}: {type(exc).__name__}: {exc}")
            continue
        fired = any("draw_revealed_red_cells_anchor" in str(nt) for nt in new.get("notes", []))
        # detect Lever1 effect even without the cells note: count range changed
        changed = _canon(base) != _canon(new)
        hero = (new.get("evidence", {}) or {}).get("hero")
        if not changed:
            n_noop_ok += 1
            continue
        # changed -> must be a draw hero with a revealed red, else it's a leak
        is_draw = E.normalize_hero_key(hero) in E._draw_hero_count_keys()
        q6_revealed = int((new.get("evidence", {}).get("min_counts", {}) or {}).get("q6", 0) or 0)
        q6_cellfloor = float((new.get("evidence", {}).get("quality_cell_floors", {}) or {}).get("q6", 0) or 0)
        legit = is_draw and (q6_revealed > 0 or q6_cellfloor > 0)
        if not legit:
            noop_breaks.append(
                f"LEAK {p}: hero={hero} q6_min={q6_revealed} q6_floor={q6_cellfloor} "
                f"cnt {base['red_count_range']}->{new['red_count_range']}"
            )
            continue
        n_fires += 1
        if len(fires_rows) < args.show_fires:
            fires_rows.append(
                f"FIRE {p.parent.parent.name}/{p.name} hero={hero} "
                f"bal {base['balanced']}->{new['balanced']} | "
                f"cnt {base['red_count_range']}->{new['red_count_range']} | "
                f"cells {base['red_cells_range']}->{new['red_cells_range']} | "
                f"val {base['red_value_range']}->{new['red_value_range']} | "
                f"fp_note={'Y' if fired else 'n'}"
            )

    print("\n=== draw revealed-red A/B ===")
    print(f"total={n_total} err={n_err} noop_identical={n_noop_ok} fires={n_fires} leaks={len(noop_breaks)}")
    if noop_breaks:
        print("\n!!! NO-OP LEAKS (changed where gate should not fire) !!!")
        for ln in noop_breaks[:50]:
            print("  " + ln)
    if fires_rows:
        print("\n--- FIRES (intended behavior change) ---")
        for ln in fires_rows:
            print("  " + ln)
    return 1 if (n_err or noop_breaks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
