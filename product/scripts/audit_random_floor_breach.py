"""Hard-floor (public random-avg total) breach audit + A/B prototype.

Question: ev._random_value_floor = max(N*avg) is the value of N specific items that ARE in the
warehouse, so it is a HARD lower bound on the warehouse total value. Today it is only a SOFT
log-weight on the value distribution. This script measures, across the full library (26xx excluded):

  1. Among samples WITH a random_value_floor at a given evaluated round, how often the engine's
     conservative / balanced tier prints BELOW that floor, and by how much.
  2. A/B prototype: monkeypatch the three-tier construction so _cons_value/_bal_value are lifted to
     max(., random_floor). Re-measure 2406 R1/R2, library lift, and the over>10% rate (F1/F2/F3
     phantom-red regression guard).
  3. Reports floor-SAFETY violations (floor > settlement truth) so we know the hard floor is not
     blindly safe.

Read-only except for an in-process monkeypatch. 26xx excluded per project caliber.
Run: python scripts/audit_random_floor_breach.py
"""

from __future__ import annotations

import glob
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402
from audit_aisha_gap import (  # noqa: E402
    SAMPLE_ROOTS,
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)


RANDOM_N_BY_ID = {200031: 3, 200032: 6, 200033: 9, 200034: 12}


def _has_random_signal(events) -> bool:
    """Cheap pre-filter: any pre-settlement frame carries a public random-avg fact (200031-034)?
    Lets us skip the (expensive) engine run on files that can never produce a random_value_floor."""
    for ev in getattr(events, "states", []) or []:
        if getattr(ev, "settlement_loss_units", None) is not None:
            continue
        for pi in getattr(ev, "public_infos", ()) or ():
            if int(getattr(pi, "info_id", 0)) in RANDOM_N_BY_ID and float(getattr(pi, "value", 0) or 0) > 0:
                return True
    return False


def _is_26xx(map_id: int | None) -> bool:
    return map_id is not None and 2600 <= int(map_id) < 2700


def _iter_sample_files() -> list[Path]:
    files: list[Path] = []
    for root in SAMPLE_ROOTS:
        files.extend(Path(f) for f in glob.glob(str(root / "*.json")))
    return sorted(set(files))


def _eval_round(events, pre_batches, audit_round, map_id):
    """Return (result_dict, evidence_obj) for hero=aisha at the given pre-settlement round."""
    prefix_batches = pre_batches[:audit_round]
    if not prefix_batches:
        return None, None
    sort_id = _prefix_sort_id(prefix_batches)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    snapshot = _build_snapshot(
        hero="aisha", events=prefix_events, prefix_batches=prefix_batches, map_id=map_id
    )
    result = run_reference_engine(snapshot, max_combos=50_000)
    return result.as_dict(), result


def _collect_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = _iter_sample_files()
    n_engine = 0
    for i, f in enumerate(files):
        if i % 200 == 0:
            print(f"  ...scanned {i}/{len(files)} files, {n_engine} engine-runs, {len(rows)} rows",
                  flush=True)
        try:
            events = parse_fatbeans_capture(f)
        except Exception:
            continue
        if not _has_random_signal(events):
            continue
        n_engine += 1
        map_id = _map_id_from_events(events)
        if _is_26xx(map_id):
            continue
        settlement = _settlement_breakdown(events)
        if not settlement:
            continue
        truth_total = int(settlement.get("total_value") or 0)
        counts = {int(k): int(v) for k, v in (settlement.get("counts") or {}).items()}
        if truth_total <= 0:
            continue
        red_count = counts.get(6, 0)
        try:
            pre_batches = [
                b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"
            ]
        except Exception:
            continue
        n_rounds = len(pre_batches)
        if n_rounds < 2:
            continue
        # Evaluate every pre-settlement round; keep rounds that carry a random floor.
        for rnd in range(1, n_rounds + 1):
            try:
                result, robj = _eval_round(events, pre_batches, rnd, map_id)
            except Exception:
                continue
            if result is None:
                continue
            ev = getattr(robj, "evidence", None)
            floor = eng._random_value_floor(ev) if ev is not None else None
            if not floor or floor <= 0:
                continue
            cons = result.get("conservative")
            bal = result.get("balanced")
            rows.append(
                {
                    "file": f.name,
                    "map": map_id,
                    "round": rnd,
                    "floor": float(floor),
                    "cons": float(cons) if cons is not None else None,
                    "bal": float(bal) if bal is not None else None,
                    "truth_total": truth_total,
                    "red_count": red_count,
                    "pure_prior_red": bool(eng._is_pure_prior_red(ev)) if ev is not None else None,
                    "q6_anchored": (ev.quality_value_floors.get("q6") if ev is not None else None)
                    not in (None, "", 0, 0.0),
                }
            )
    return rows


def _pct(num, den):
    return (100.0 * num / den) if den else 0.0


def main() -> int:
    rows = _collect_rows()
    print(f"sample-rounds with random_value_floor (26xx excluded): {len(rows)}")
    if not rows:
        return 0

    # ---- Part 0: floor SAFETY (floor > truth_total => hard floor would over-state) ----
    unsafe = [r for r in rows if r["floor"] > r["truth_total"]]
    print(f"\n[floor SAFETY] floor>truth_total: {len(unsafe)}/{len(rows)} "
          f"({_pct(len(unsafe), len(rows)):.1f}%)")
    for r in sorted(unsafe, key=lambda x: x["floor"] - x["truth_total"], reverse=True)[:10]:
        over = r["floor"] - r["truth_total"]
        print(f"   {r['file'][:42]:42s} map={r['map']} R{r['round']} "
              f"floor={r['floor']:.0f} truth={r['truth_total']:.0f} over=+{over:.0f} "
              f"({_pct(over, r['truth_total']):.0f}%) red={r['red_count']}")

    # ---- Part 1: breach stats (tier prints below the floor) ----
    for tier in ("cons", "bal"):
        vals = [r for r in rows if r[tier] is not None]
        breach = [r for r in vals if r[tier] < r["floor"]]
        print(f"\n[{tier} breach] below floor: {len(breach)}/{len(vals)} "
              f"({_pct(len(breach), len(vals)):.1f}%)")
        if breach:
            shortfalls = [r["floor"] - r[tier] for r in breach]
            shortfall_pct = [100.0 * (r["floor"] - r[tier]) / r["floor"] for r in breach]
            print(f"   shortfall abs: med={statistics.median(shortfalls):.0f} "
                  f"max={max(shortfalls):.0f}")
            print(f"   shortfall %floor: med={statistics.median(shortfall_pct):.1f}% "
                  f"max={max(shortfall_pct):.1f}%")
            # How many of the breaches are on samples that actually have a big red (lift justified)?
            with_red = [r for r in breach if r["red_count"] > 0]
            no_red = [r for r in breach if r["red_count"] == 0]
            print(f"   breaches with real red: {len(with_red)}  | breaches with ZERO red: {len(no_red)}")
            # Of the no-red breaches, would the hard floor over-state truth?
            no_red_overstate = [r for r in no_red if r["floor"] > r["truth_total"]]
            print(f"   of zero-red breaches, floor>truth: {len(no_red_overstate)}")

    # ---- Part 2: A/B prototype: lift cons & bal to max(., floor) ----
    # Pure analytic A/B on the collected rows (the lift is a monotone max, so we can apply it here).
    print("\n[A/B prototype: _cons/_bal = max(., random_floor)]")
    print("By construction max() never lowers a tier, so the only risk is OVER-statement.")
    # over>10% rate before vs after, per tier, vs settlement truth.
    for tier in ("cons", "bal"):
        vals = [r for r in rows if r[tier] is not None]
        before_over = [r for r in vals if r[tier] > 1.10 * r["truth_total"]]
        lifted = [max(r[tier], r["floor"]) for r in vals]
        after_over = [
            r for r, lv in zip(vals, lifted) if lv > 1.10 * r["truth_total"]
        ]
        newly_over = [
            r for r, lv in zip(vals, lifted)
            if lv > 1.10 * r["truth_total"] and r[tier] <= 1.10 * r["truth_total"]
        ]
        n_lifted = sum(1 for r, lv in zip(vals, lifted) if lv > r[tier] + 0.5)
        lift_amts = [lv - r[tier] for r, lv in zip(vals, lifted) if lv > r[tier] + 0.5]
        print(f"  {tier}: lifted {n_lifted}/{len(vals)} rows "
              f"(med lift={statistics.median(lift_amts):.0f})" if lift_amts
              else f"  {tier}: lifted 0/{len(vals)} rows")
        print(f"     over>10% rate: {len(before_over)} -> {len(after_over)} "
              f"(newly over from lift: {len(newly_over)})")
        for r in newly_over:
            print(f"       NEW-OVER {r['file'][:40]:40s} map={r['map']} R{r['round']} "
                  f"{tier} {r[tier]:.0f}->{r['floor']:.0f} truth={r['truth_total']:.0f} "
                  f"red={r['red_count']} pure_prior={r['pure_prior_red']}")

    # ---- 2406 spotlight ----
    print("\n[2406 spotlight]")
    for r in sorted([r for r in rows if r["map"] == 2406], key=lambda x: (x["file"], x["round"])):
        cons_lift = max(r["cons"], r["floor"]) if r["cons"] is not None else None
        bal_lift = max(r["bal"], r["floor"]) if r["bal"] is not None else None
        print(f"   {r['file'][:36]:36s} R{r['round']} floor={r['floor']:.0f} "
              f"cons={r['cons']:.0f}->{cons_lift:.0f} bal={r['bal']:.0f}->{bal_lift:.0f} "
              f"truth={r['truth_total']:.0f} red={r['red_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
