"""Replay a live HeroRefDiag export round-by-round and compare engine output to settled truth.

Reusable inspection artifact for the count/grid calibration work: feed an unzipped export dir that
has `round_snapshots/r0X_bidding.json` (+ `r0X_settled.json` for truth) and it re-runs the CURRENT
engine on each pre-settlement round, printing total grid range, total count, and per-quality count /
grid vs the settled `final_quality_*` truth. Use to verify a model change on the exact live vault the
user flagged, BEFORE/AFTER (git stash) without touching the live pipeline.

Read-only. Run: python scripts/replay_live_export.py .tmp/exp2408
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

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

# engine QMAP collapses raw quality 2 into the q1 bucket.
QKEYS = ("q1", "q3", "q4", "q5", "q6")


def _parse_kv(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = int(float(v))
        except ValueError:
            continue
    return out


def _truth(settled: dict) -> tuple[dict[str, int], dict[str, int], int, int]:
    qc = _parse_kv(settled.get("final_quality_counts"))
    qg = _parse_kv(settled.get("final_quality_cells"))
    counts = {
        "q1": qc.get("q1", 0) + qc.get("q2", 0),
        "q3": qc.get("q3", 0),
        "q4": qc.get("q4", 0),
        "q5": qc.get("q5", 0),
        "q6": qc.get("q6", 0),
    }
    cells = {
        "q1": qg.get("q1", 0) + qg.get("q2", 0),
        "q3": qg.get("q3", 0),
        "q4": qg.get("q4", 0),
        "q5": qg.get("q5", 0),
        "q6": qg.get("q6", 0),
    }
    return counts, cells, int(settled.get("inventory_count") or 0), int(settled.get("inventory_cells") or 0)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python scripts/replay_live_export.py <export_dir>")
        return 1
    export = Path(argv[1])
    snap_dir = export / "round_snapshots"
    settled_files = sorted(glob.glob(str(snap_dir / "*_settled.json")))
    if not settled_files:
        print(f"no settled snapshot in {snap_dir}")
        return 1
    settled = json.load(open(settled_files[-1], encoding="utf-8"))
    t_counts, t_cells, t_total, t_total_cells = _truth(settled)
    map_id = settled.get("map_id")
    print(f"=== replay {export.name}  map={map_id}  truth: total {t_total} items / {t_total_cells} cells ===")
    print(f"truth per-quality counts: " + " ".join(f"{k}={t_counts[k]}" for k in QKEYS))
    print(f"truth per-quality cells:  " + " ".join(f"{k}={t_cells[k]}" for k in QKEYS))
    print()

    bidding = sorted(glob.glob(str(snap_dir / "r*_bidding.json")))
    for bf in bidding:
        snap = json.load(open(bf, encoding="utf-8"))
        rno = snap.get("round")
        res = run_reference_engine(
            prepare_reference_engine_snapshot(snap), max_combos=8000
        ).as_dict()
        grid = res.get("total_grid_range") or [None, None, None]
        ranges = res.get("quality_count_ranges") or {}
        est_total = sum(int((ranges.get(q) or [0, 0, 0])[1] or 0) for q in QKEYS)
        print(
            f"R{rno}: grid {grid} (truth {t_total_cells})  |  total count mid={est_total} (truth {t_total})"
        )
        for q in QKEYS:
            rng = ranges.get(q) or [None, None, None]
            print(f"     {q} count {rng} (truth {t_counts[q]})")
        notes = res.get("source_notes") or res.get("notes") or []
        rel = [n for n in notes if any(t in str(n) for t in ("avg_cells", "richness", "per_item", "grid_avg", "residual", "total_grid"))]
        if rel:
            print(f"     notes: {rel}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
