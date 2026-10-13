"""Measure estimate-vs-settlement bias for category-reveal heroes (tatiana etc.).

Category-reveal heroes (塔蒂安娜/乔治/陈美 = tatiana/george/chenmei, and the
naomi/isabella/carlos family) reveal ONE category's outline+quality up front but
carry NO total-count hint. The ref engine consumes the category reveal (revealed
buckets lock) yet the TOTAL count regresses to the map-family prior (~28), so the
quote does not track vault size: rich vaults under-shoot, poor vaults over-shoot
(full 36-sample tatiana read 2026-07-03: value bias median -27%, huge variance
+144%..-82%). This harness quantifies that bias so calibration levers can be A/B'd
consistently across the workflow's prototype agents.

Bias is reported on THREE axes (value / total count / total grid-cells), because
count and grid over/under-shoot independently drive the value error — a fix must
be reasonable on all three, not just value.

--per-round evaluates EVERY bidding checkpoint (R1, R2, ... each round's snapshot),
not only the final round. IMPORTANT (author 2026-07-03 + memory round4-benchmark-
and-visible-anchor / aisha-grid-tuning-constraints): a per-round estimate must be
judged against VISIBILITY at that round, NOT against final settlement. Early rounds
legitimately under-shoot settlement (invisible depth/gold/red) and that is
ACCEPTABLE; the real per-round error is VIS_UNDER (estimating BELOW what is already
visible) and grid<->count inconsistency. Only the DECISION round (R4/last, most info,
low multiplier) is judged on settlement bias+MAE. So: read the per-round settlement
bias here as raw divergence only — do NOT treat early-round under-settlement as a
defect; cross-check VIS_UNDER via the visible-anchor path (audit_aisha_error_map.py /
apply_shallow_visible_grid_low_floor) for the true per-round criterion.

Usage:
  python scripts/measure_category_reveal_hero_bias.py --hero tatiana [--limit N]
      [--combos 15000] [--per-round] [--json out.json]

Reuses the offline replay path (audit_aisha_gap._build_snapshot -> run_reference_engine)
so it matches live wiring. CPU-bound (pure-Python DP); ~2-7s/checkpoint at 15k combos.
--per-round multiplies cost by rounds/sample; prefer --limit for a directional shard
and run the full set only for the final winner.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "hero_ref" / "src", ROOT / "src", ROOT / "scripts"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)
import audit_aisha_gap as A  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402


def _load(path: str):
    rows = json.load(open(path, encoding="utf-8"))
    return parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))


def _map_id(events) -> int | None:
    """Real map_id from the capture. Passing None (the earlier bug) makes the engine
    use the map=None default (28) for _default_total_count AND disables every
    family-scoped lever — offline replay MUST feed the real map to be faithful to live."""
    for state in reversed(events.states):
        mid = getattr(state, "map_id", None)
        if mid:
            return int(mid)
    return None


def _representative_shard(files: list[str], limit: int) -> list[str]:
    """Even stride so a shard spans the round/richness spectrum, not just the head."""
    if limit <= 0 or limit >= len(files):
        return files
    step = len(files) / limit
    return [files[int(i * step)] for i in range(limit)]


def _est_axes(res: dict) -> tuple[float | None, int | None, float | None]:
    """(value_p50, total_count_estimate, total_grid_mid) from a ref-engine result."""
    grid_range = res.get("total_grid_range") or []
    grid_mid = grid_range[1] if len(grid_range) >= 2 else None
    return res.get("value_p50"), res.get("total_count_estimate"), grid_mid


def _bias(est, truth):
    if est is None or not truth:
        return None
    return (est - truth) / truth


def _round_checkpoints(pre: list) -> list[tuple[int, list]]:
    """Per-round snapshots: each prefix that ends on a bidding batch (the states a
    live user actually quotes off), labelled by bidding-round index (1-based)."""
    checkpoints = []
    bidding_seen = 0
    for k, batch in enumerate(pre, start=1):
        if batch.phase == "bidding":
            bidding_seen += 1
            checkpoints.append((bidding_seen, pre[:k]))
    if not checkpoints:  # reading-only capture -> fall back to the full prefix
        checkpoints.append((1, pre))
    return checkpoints


def _axis_summary(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    abs_vals = [abs(v) for v in vals]
    out = {
        "n": len(vals),
        "median": round(st.median(vals), 4),
        "mean": round(sum(vals) / len(vals), 4),
        "abs_mean": round(sum(abs_vals) / len(abs_vals), 4),
        "over_gt10pct": sum(1 for v in vals if v > 0.10),
        "under_lt_neg10pct": sum(1 for v in vals if v < -0.10),
    }
    if len(vals) >= 4:
        q = st.quantiles(vals, n=4)
        out["iqr"] = round(q[2] - q[0], 4)
    return out


def measure(hero: str, limit: int, combos: int, per_round: bool) -> dict:
    pat = str(ROOT / "data" / "samples" / "fatbeans" / f"fatbeans_valid_{hero}_*.json")
    files = sorted(glob.glob(pat))
    files = _representative_shard(files, limit)
    rows = []
    t0 = time.perf_counter()
    for f in files:
        try:
            ev = _load(f)
            h = A._hero_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                continue
            truth = A._settlement_breakdown(ev)
            tv = sum(truth["values"].values())
            tc = sum(truth["counts"].values())
            tg = truth.get("total_cells")
            if tv <= 0:
                continue
            checkpoints = _round_checkpoints(pre) if per_round else [(len(pre), pre)]
            for round_idx, prefix in checkpoints:
                snap = A._build_snapshot(
                    hero=h, events=ev, prefix_batches=prefix, map_id=_map_id(ev)
                )
                res = run_reference_engine(snap, max_combos=combos).as_dict()
                ev_, ec, eg = _est_axes(res)
                rows.append(
                    {
                        "file": Path(f).name,
                        "round": round_idx,
                        "truth_count": tc,
                        "truth_value": tv,
                        "truth_cells": tg,
                        "est_value": ev_,
                        "est_count": ec,
                        "est_cells": eg,
                        "value_bias": _bias(ev_, tv),
                        "count_bias": _bias(ec, tc),
                        "grid_bias": _bias(eg, tg),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - directional harness, keep going
            rows.append({"file": Path(f).name, "error": str(exc)[:120]})
    dt = time.perf_counter() - t0

    def axis(key):
        return _axis_summary([r[key] for r in rows if r.get(key) is not None])

    summary = {
        "hero": hero,
        "checkpoints": len([r for r in rows if r.get("value_bias") is not None]),
        "samples": len({r["file"] for r in rows if "file" in r}),
        "seconds": round(dt, 1),
        "combos": combos,
        "per_round": per_round,
        "value": axis("value_bias"),
        "count": axis("count_bias"),
        "grid": axis("grid_bias"),
    }
    if per_round:
        by_round = {}
        for r in rows:
            if r.get("value_bias") is None:
                continue
            by_round.setdefault(r["round"], {"value": [], "count": [], "grid": []})
            by_round[r["round"]]["value"].append(r["value_bias"])
            if r.get("count_bias") is not None:
                by_round[r["round"]]["count"].append(r["count_bias"])
            if r.get("grid_bias") is not None:
                by_round[r["round"]]["grid"].append(r["grid_bias"])
        summary["per_round_breakdown"] = {
            f"R{idx}": {
                "n": len(v["value"]),
                "value": _axis_summary(v["value"]).get("median"),
                "value_abs_mean": _axis_summary(v["value"]).get("abs_mean"),
                "count": _axis_summary(v["count"]).get("median"),
                "grid": _axis_summary(v["grid"]).get("median"),
            }
            for idx, v in sorted(by_round.items())
        }
    return {"summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hero", default="tatiana")
    ap.add_argument("--limit", type=int, default=0, help="0 = all; else even-stride shard")
    ap.add_argument("--combos", type=int, default=15_000)
    ap.add_argument("--per-round", action="store_true", help="evaluate every bidding round, not just last")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    out = measure(args.hero, args.limit, args.combos, args.per_round)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
