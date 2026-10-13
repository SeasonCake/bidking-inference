"""Per-round VALUE-tier trace for a HeroRefDiag export — answers 'why is the bid pinned high?'.

Reusable inspection artifact: feed an unzipped export dir with round_snapshots/r0X_bidding.json
(+ r0X_settled.json for truth). Prints per round: hero, total count/grid, red count/cells/value
ranges, the three value tiers (保守/参考/激进), and value-relevant notes — so we can SEE which
component (red value, base value, a floor/anchor note) holds the price up, BEFORE tuning. Read-only.

Run: python scripts/trace_value_tiers.py "<export dir>"
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import run_reference_engine  # noqa: E402


def _parse_kv(text):
    out = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = int(float(v))
        except ValueError:
            pass
    return out


def _m(x):
    return f"{x/1e6:.2f}M" if x else "0"


def main(d: str):
    base = Path(d)
    snaps = sorted(glob.glob(str(base / "round_snapshots" / "r*_bidding.json")))
    settled = sorted(glob.glob(str(base / "round_snapshots" / "r*_settled.json")))
    truth = None
    if settled:
        s = json.loads(Path(settled[-1]).read_text(encoding="utf-8-sig"))
        qc = _parse_kv(s.get("final_quality_counts"))
        qg = _parse_kv(s.get("final_quality_cells"))
        truth = (qc, qg, s.get("final_total_value") or s.get("total_value"))
    print(f"=== {base.name} ===")
    if truth:
        qc, qg, tv = truth
        print(f"TRUTH: q6(red) count={qc.get('q6',0)} cells={qg.get('q6',0)}  total_value={_m(tv) if tv else '?'}  "
              f"all_counts={dict(qc)}")
    for snap in snaps:
        rnd = Path(snap).name.split("_")[0]
        data = json.loads(Path(snap).read_text(encoding="utf-8-sig"))
        try:
            r = run_reference_engine(data)
        except Exception as e:
            print(f"{rnd}: ERROR {e}")
            continue
        hero = (data.get("hero_key") or data.get("hero") or data.get("hero_name")
                or (data.get("snapshot") or {}).get("hero_key") or "?")
        rc = r.red_count_range
        rcell = r.red_cells_range
        rv = r.red_value_range
        gv = r.total_grid_range
        ev = r.evidence
        # revealed/known red floor vs prior-center red — phantom red = prior pushes red above revealed
        revealed_q6 = 0
        if ev is not None:
            revealed_q6 = (getattr(ev, "fixed_counts", {}) or {}).get("q6",
                           (getattr(ev, "min_counts", {}) or {}).get("q6", 0))
        red_mid = rc[1] or 0
        red_val_mid = rv[1] or 0
        per_red = red_val_mid / red_mid if red_mid else 0
        base_val = (r.balanced or 0) - red_val_mid  # non-red base
        print(f"     RED: revealed={revealed_q6}  prior_center={red_mid}  "
              f"phantom={max(0, red_mid - revealed_q6)}  per_red_item={_m(per_red)}  "
              f"base(non-red)={_m(base_val)}  red%={int(100*red_val_mid/(r.balanced or 1))}%")
        # value-relevant notes only
        vnotes = [n for n in (r.notes or ()) if any(t in n.lower() for t in
                  ("red", "floor", "anchor", "cap", "value", "tier105", "activity", "ship", "strip", "keep", "min_"))]
        print(f"{rnd}: hero={hero}  保守={_m(r.conservative)} 参考={_m(r.balanced)} 激进={_m(r.aggressive)}"
              f"  | p25/50/75={_m(r.value_p25)}/{_m(r.value_p50)}/{_m(r.value_p75)}")
        print(f"     red count={rc}  red cells={rcell}  red value={tuple(_m(x) if x else '0' for x in rv)}"
              f"  grid={gv}")
        if vnotes:
            print(f"     notes: {vnotes}")


if __name__ == "__main__":
    main(sys.argv[1])
