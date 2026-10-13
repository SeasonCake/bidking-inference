"""A/B + baseline harness for the lone-deep-large-object GRID over-estimation (aisha).

User-flagged pattern: a single >=9-cell item alone at the very bottom drags raw_deepest
deep, so the footroom path fills the rows between the dense packing and that lone tail as
ghost rows -> total grid (估总格) over-states. Evenly-packed vaults estimate fine.

This tool (1) classifies every aisha 4round sample's decision-round layout as lone-deep-big
/ lone-deep-other / packed using the engine's own geometry helpers, and (2) reports total
grid estimate vs settlement-truth total cells split by class -- so the bug pattern is proven
with data BEFORE any fix. With --fix-env "KEY=VAL[,KEY2=VAL2]" it runs each sample twice
(baseline vs the env-gated fix) and reports the delta, the same harness the fix will use.

Usage:
  python scripts/ab_aisha_grid_lone_deep.py [--round 3] [--fix-env "AISHA_FOOTROOM_LONE_DEEP_FIX=1"] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
import ahmad_ref_engine as E  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
COLS = E.AISHA_GRID_COLUMNS
BIG_ITEM_CELLS = 9  # user's threshold: a >=9-cell item alone deep is the trigger


def _mid(t):
    if not isinstance(t, (list, tuple)) or len(t) < 2 or t[1] is None:
        return None
    return int(t[1])


def _classify(items) -> tuple[str, int, int]:
    """Return (class, raw_deepest, lone_overstate_cells)."""
    raw_deepest = E._deepest_minimap_bottom_row(items)
    if not raw_deepest or raw_deepest <= 0:
        return "packed", 0, 0
    occ_adj = E._occupancy_adjusted_depth_grid(items, int(raw_deepest))
    overstate = int(raw_deepest) * COLS - int(occ_adj)
    big_deep = False
    for it in items:
        cells = E._safe_int(it.get("cells")) or 0
        bottom = E._item_bottom_row(it, columns=COLS)
        if cells >= BIG_ITEM_CELLS and bottom is not None and int(bottom) >= int(raw_deepest) - 1:
            big_deep = True
            break
    if overstate >= 10 and big_deep:
        return "lone_deep_big", int(raw_deepest), overstate
    if overstate >= 10:
        return "lone_deep_other", int(raw_deepest), overstate
    return "packed", int(raw_deepest), overstate


def _grid_mid(snap, skill_round) -> int | None:
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap),
        max_combos=hero_max_combos_for_round("aisha", skill_round),
    ).as_dict()
    return _mid(res.get("total_grid_range") or [None, None, None])


def _set_env(spec: str | None):
    keys = []
    if not spec:
        return keys
    for part in spec.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            os.environ[k.strip()] = v.strip()
            keys.append(k.strip())
    return keys


def _iter_export_snaps(exports_dir: Path, target_round: int):
    """Yield (label, fam, snap, truth_cells) from HeroRefDiag export zips (aisha only).

    Dedup by session id (the trailing digits in the filename); the user's exports re-dump
    the same session many times, and some maps (2401) are fixed-layout, so per-session dedup
    avoids over-counting one vault as N (AGENTS.md sec.4/6).
    """
    seen: dict[str, Path] = {}
    for z in sorted(exports_dir.glob("**/*.zip")):
        m = re.search(r"_(\d{13,})\.zip$", z.name)
        sess = m.group(1) if m else z.name
        seen[sess] = z  # latest wins
    for sess, z in seen.items():
        try:
            zf = zipfile.ZipFile(z)
            names = zf.namelist()
            bid_name = f"round_snapshots/r0{target_round}_bidding.json"
            if bid_name not in names:
                continue
            snap = json.loads(zf.read(bid_name).decode("utf-8", "ignore"))
            if str(snap.get("hero") or "") != "aisha":
                continue
            settled = [n for n in names if n.endswith("_settled.json")]
            if not settled:
                continue
            sett = json.loads(zf.read(sorted(settled)[-1]).decode("utf-8", "ignore"))
            truth_cells = E._safe_int(sett.get("inventory_cells"))
            if truth_cells is None or truth_cells <= 0:
                continue
            fam = str(int(snap.get("map_id")) // 100) if snap.get("map_id") is not None else "?"
            yield z.name, fam, snap, int(truth_cells)
        except Exception:  # noqa: BLE001
            continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=3)
    ap.add_argument("--fix-env", default="", help='env to A/B as the fix, e.g. "AISHA_FOOTROOM_LONE_DEEP_FIX=1"')
    ap.add_argument("--exports", default="", help="dir of HeroRefDiag export zips to eval instead of fatbeans")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[args.round - 1]

    if args.exports:
        return _run_exports(args, skill_round)

    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    mode = f"A/B fix-env={args.fix_env}" if args.fix_env else "baseline + classify"
    print(f"grid lone-deep {mode}: {total} samples @ R{skill_round}", flush=True)

    rows: list[dict] = []
    skipped = 0
    start = time.time()
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_cells = int(sum((truth.get("cells") or {}).values()))
            fam = str(int(_map_id_from_events(events)) // 100)
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
        if snap is None or t_cells <= 0:
            skipped += 1
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        items = E._minimap_items_from_snapshot(snap)
        cls, raw_deepest, overstate = _classify(items)
        # baseline
        for kk in _set_env(args.fix_env):
            os.environ.pop(kk, None)
        base = _grid_mid(snap, skill_round)
        fix = None
        if args.fix_env:
            _set_env(args.fix_env)
            fix = _grid_mid(snap, skill_round)
            for kk in _set_env(args.fix_env):
                os.environ.pop(kk, None)
        if base is None:
            skipped += 1
            continue
        rows.append({
            "fam": fam, "cls": cls, "raw_deepest": raw_deepest, "overstate": overstate,
            "t_cells": t_cells, "base": base, "fix": fix, "name": sample.name,
        })
        if k % 20 == 0 or k == total:
            el = time.time() - start
            print(f"  [progress] {k}/{total} used={len(rows)} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    _report(rows, args, skill_round, skipped)
    return 0


def _run_exports(args, skill_round) -> int:
    exports_dir = Path(args.exports)
    print(f"grid lone-deep exports @ R{args.round}: {exports_dir}", flush=True)
    rows: list[dict] = []
    skipped = 0
    for label, fam, snap, t_cells in _iter_export_snaps(exports_dir, args.round):
        try:
            items = E._minimap_items_from_snapshot(snap)
            cls, raw_deepest, overstate = _classify(items)
            for kk in _set_env(args.fix_env):
                os.environ.pop(kk, None)
            base = _grid_mid(snap, skill_round)
            fix = None
            if args.fix_env:
                _set_env(args.fix_env)
                fix = _grid_mid(snap, skill_round)
                for kk in _set_env(args.fix_env):
                    os.environ.pop(kk, None)
            if base is None:
                skipped += 1
                continue
            rows.append({
                "fam": fam, "cls": cls, "raw_deepest": raw_deepest, "overstate": overstate,
                "t_cells": t_cells, "base": base, "fix": fix, "name": label,
            })
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
    _report(rows, args, skill_round, skipped)
    return 0


def _report(rows, args, skill_round, skipped) -> None:
    def stat(rs, field):
        n = len(rs)
        if not n:
            return None
        bias = sum(r[field] - r["t_cells"] for r in rs) / n
        mae = sum(abs(r[field] - r["t_cells"]) for r in rs) / n
        over = sum(1 for r in rs if r[field] > r["t_cells"] * 1.10) / n
        return n, bias, mae, over

    print(f"\n=== grid estimate vs truth cells, by layout class @ R{skill_round} ===")
    print(f"{'class':16s} {'n':>3s} | {'baseBias':>8s} {'baseMAE':>7s} {'over%':>5s}"
          + (f" | {'fixBias':>7s} {'fixMAE':>6s} {'over%':>5s}" if args.fix_env else ""))
    grp: dict[str, list] = defaultdict(list)
    for r in rows:
        grp[r["cls"]].append(r)
    for cls in ("lone_deep_big", "lone_deep_other", "packed"):
        rs = grp.get(cls) or []
        s = stat(rs, "base")
        if not s:
            continue
        n, bias, mae, over = s
        line = f"{cls:16s} {n:>3d} | {bias:>+8.1f} {mae:>7.1f} {over:>5.0%}"
        if args.fix_env:
            sf = stat([r for r in rs if r["fix"] is not None], "fix")
            if sf:
                _, fbias, fmae, fover = sf
                line += f" | {fbias:>+7.1f} {fmae:>6.1f} {fover:>5.0%}"
        print(line)

    print("\n=== lone_deep_big samples (the user-flagged pattern) ===")
    print(f"{'sample':46s} {'fam':>4s} {'rawDeep':>7s} {'over':>4s} | t_cells  base" + ("  fix" if args.fix_env else ""))
    for r in sorted(grp.get("lone_deep_big") or [], key=lambda r: -(r["base"] - r["t_cells"]))[:20]:
        line = f"{r['name'][:46]:46s} {r['fam']:>4s} {r['raw_deepest']:>7d} {r['overstate']:>4d} | {r['t_cells']:>6d}  {r['base']:>4d}"
        if args.fix_env and r["fix"] is not None:
            line += f"  {r['fix']:>4d}"
        print(line)
    print(f"\nskipped={skipped} used={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
