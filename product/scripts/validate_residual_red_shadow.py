"""Offline SHADOW validation of the residual-constrained red idea (no engine change).

User-approved direction: red is judged by the space LEFT for it (red_cells ~= grid_anchor - non_red
cells), matching how aisha players reason (eyeball grid + whether gold props were worth using). The
red prior alone regresses to the map mean (eval: low-red +424k over, high-red -210k under). Question
this answers BEFORE coding: which RED-INDEPENDENT grid anchor makes (anchor - non_red_cells) track
the true red cells better than the current engine red, across the low/mid/high buckets?

For every aisha 4round sample at R4, reconstruct, run the engine, and compare per bucket:
  - truth red cells
  - engine red cells (p50) bias               <- current behavior
  - shadow residual-red bias for each anchor  <- candidate fix
Anchors (all red-independent): known_minimap_cells, deepest_row*10, deepest_row*9.5,
max(known, non_red). non_red = engine q1..q5 cells p50. grid_mid included as a contaminated control.

Usage: python scripts/validate_residual_red_shadow.py [--limit N]
Throwaway -- retire once the residual-red anchor is chosen.
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
import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
NONRED = ("q1", "q2", "q3", "q4", "q5")


def _bucket(c: int) -> str:
    return "low(0-1)" if c <= 1 else "mid(2-3)" if c <= 3 else "high(4+)"


def _p50(t) -> int:
    return int(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 and t[1] is not None else 0


def _anchors(prepared, non_red: int, grid_mid: int) -> dict[str, int]:
    items = eng._minimap_items_from_snapshot(prepared)
    known = eng._known_minimap_cells(items)
    deep = eng._deepest_minimap_bottom_row(items) or 0
    deep95 = int(round(deep * 9.5))
    try:
        fill = eng._viewport_fill_ratio(known_cells=known, deepest=deep) if deep > 0 else 1.0
    except Exception:  # noqa: BLE001
        fill = 1.0
    # Fill-adaptive: warehouse nearly fully revealed (high fill) -> the tight `known` bounds red room;
    # unrevealed depth (low fill) -> geometric deep*9.5. Matches the player heuristic (full vault = no
    # red room) and the footroom split.
    adaptive = known if fill >= 0.85 else deep95
    # Occupancy-adjusted depth grid: re-anchors when a lone deep item reaches the bottom unsupported
    # (sparse deepest row), i.e. removes the phantom room that raw deep*10 invents in tight low-red
    # vaults. This is the candidate that should close the low-red gap WITHOUT breaking high-red.
    try:
        occ = eng._occupancy_adjusted_depth_grid(items, deep) if deep > 0 else known
    except Exception:  # noqa: BLE001
        occ = deep95
    return {
        "deep*9.5": deep95,
        "occ_adj": int(occ),
        "occ_adj*0.95": int(round(occ * 0.95)),
        "grid_mid(contam)": grid_mid,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[3]  # R4

    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    print(f"residual-red shadow: {total} aisha 4round @ R{skill_round}", flush=True)

    rows: dict[str, list[dict]] = defaultdict(list)
    anchor_names: list[str] = []
    start = time.time()
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_cnt = int((truth.get("counts") or {}).get(6, 0))
            t_red_cells = int((truth.get("cells") or {}).get(6, 0))
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception:  # noqa: BLE001
            continue
        snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        prepared = prepare_reference_engine_snapshot(snap)
        res = run_reference_engine(prepared, max_combos=hero_max_combos_for_round("aisha", skill_round)).as_dict()
        qx = res.get("quality_cells_ranges") or {}
        eng_red = _p50(qx.get("q6"))
        non_red = sum(_p50(qx.get(key)) for key in NONRED)
        grid_mid = _p50(res.get("total_grid_range"))
        anchors = _anchors(prepared, non_red, grid_mid)
        anchor_names = list(anchors.keys())
        shadow = {name: max(0, a - non_red) for name, a in anchors.items()}
        items = eng._minimap_items_from_snapshot(prepared)
        known = eng._known_minimap_cells(items)
        cap = anchors.get("occ_adj", known) or known or 1
        rows[_bucket(t_cnt)].append({
            "truth": t_red_cells, "eng": eng_red, "shadow": shadow,
            "known": known, "cap": cap, "non_red": non_red,
            "fill_known_cap": known / cap, "nonred_frac": non_red / cap,
        })
        if k % 20 == 0 or k == total:
            el = time.time() - start
            print(f"  [progress] {k}/{total} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    # Report: per bucket, bias (estimate - truth) for engine vs each shadow anchor; plus overall MAE.
    print(f"\n{'bucket':10s} {'n':>3s} {'truthCell':>9s} {'engBias':>8s} " + " ".join(f"{a[:15]:>15s}" for a in anchor_names))
    overall_mae: dict[str, float] = defaultdict(float)
    overall_n = 0
    for bucket in ("low(0-1)", "mid(2-3)", "high(4+)"):
        r = rows[bucket]
        if not r:
            continue
        n = len(r)
        overall_n += n
        avg_truth = sum(x["truth"] for x in r) / n
        eng_bias = sum(x["eng"] - x["truth"] for x in r) / n
        cells = [f"{sum(x['shadow'][a] - x['truth'] for x in r) / n:>+15.1f}" for a in anchor_names]
        print(f"{bucket:10s} {n:>3d} {avg_truth:>9.1f} {eng_bias:>+8.1f} " + " ".join(cells))
        for a in anchor_names:
            overall_mae[a] += sum(abs(x["shadow"][a] - x["truth"]) for x in r)
        overall_mae["_eng"] += sum(abs(x["eng"] - x["truth"]) for x in r)

    print(f"\nOverall red-cell MAE (lower=better):  eng={overall_mae['_eng'] / overall_n:.2f}")
    for a in anchor_names:
        print(f"    shadow[{a:16s}] = {overall_mae[a] / overall_n:.2f}")

    # Occupancy diagnostic: does fullness separate low/high red? (user's claim: full vault -> few red)
    print(f"\n{'bucket':10s} {'n':>3s} {'truthRedCell':>12s} {'known':>7s} {'cap':>6s} {'nonRed':>7s} {'known/cap':>10s} {'nonRed/cap':>11s} {'cap-nonRed':>11s}")
    for bucket in ("low(0-1)", "mid(2-3)", "high(4+)"):
        r = rows[bucket]
        if not r:
            continue
        n = len(r)
        g = lambda key: sum(x[key] for x in r) / n  # noqa: E731
        print(f"{bucket:10s} {n:>3d} {g('truth'):>12.1f} {g('known'):>7.1f} {g('cap'):>6.1f} {g('non_red'):>7.1f} {g('fill_known_cap'):>10.2f} {g('nonred_frac'):>11.2f} {g('cap') - g('non_red'):>+11.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
