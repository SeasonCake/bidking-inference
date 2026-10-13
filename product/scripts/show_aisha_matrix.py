"""Render aisha warehouses as 1-based matrices: what aisha SAW (decision round) vs the SETTLED truth.

Built to review the negative-space / shape / jianying ideas on real layouts instead of abstract
numbers (per the repo discipline: SHOW the decoded matrix before tuning). Two views per sample:
  - DECISION (last pre-settlement minimap): the revealed outlines aisha had, with the empty cells
    she could infer as high-tier "negative space".
  - SETTLED (full inventory): the ground truth -- where gold/red actually sat, and which cells that
    looked empty were genuinely empty (the deep-empty-cell caveat) vs hidden high-tier.

Coordinates are 1-based row/col (1-10); baoguang / quality-only hints (local_index only) render as
`q*`. Reuses scripts/minimap_audit_matrix.render_minimap_matrix.

Read-only. Run:
  python scripts/show_aisha_matrix.py .tmp/exp2405 [.tmp/exp2406 ...]      # live export dirs
  python scripts/show_aisha_matrix.py --lib 5                              # N random library samples
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import _minimap_items_from_snapshot  # noqa: E402
from minimap_audit_matrix import MATRIX_LEGEND, render_minimap_matrix  # noqa: E402


def _parse_kv(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = int(float(v))
            except ValueError:
                pass
    return out


def _render_snapshot(snap: dict, label: str) -> None:
    items = _minimap_items_from_snapshot(snap) or []
    matrix, notes, known = render_minimap_matrix(items)
    print(f"--- {label}: {len(items)} items, {known} known cells ---")
    print(matrix if matrix.strip() else "  (empty)")


def _show_export(export: Path) -> None:
    snap_dir = export / "round_snapshots"
    settled_files = sorted(glob.glob(str(snap_dir / "*_settled.json")))
    bidding_files = sorted(glob.glob(str(snap_dir / "r*_bidding.json")))
    if not settled_files:
        print(f"[skip] no settled snapshot in {export}")
        return
    settled = json.load(open(settled_files[-1], encoding="utf-8"))
    counts = _parse_kv(settled.get("final_quality_counts"))
    cells = _parse_kv(settled.get("final_quality_cells"))
    gold = (counts.get("q5", 0), cells.get("q5", 0))
    red = (counts.get("q6", 0), cells.get("q6", 0))
    print("=" * 64)
    print(
        f"### {export.name}  map={settled.get('map_id')}  "
        f"truth {settled.get('inventory_count')} items / {settled.get('inventory_cells')} cells "
        f"| gold {gold[0]}件/{gold[1]}格 · red {red[0]}件/{red[1]}格"
    )
    print(f"legend: {MATRIX_LEGEND}")
    if bidding_files:
        decision = json.load(open(bidding_files[-1], encoding="utf-8"))
        _render_snapshot(decision, f"DECISION (R{decision.get('round')}, what aisha saw)")
    _render_snapshot(settled, "SETTLED (ground truth)")
    print()


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--lib":
        n = int(args[1]) if len(args) > 1 else 5
        # Deterministic spread across the library (no RNG -- pick evenly spaced).
        lib = sorted(glob.glob(str(ROOT / "data/samples/fatbeans/*_aisha_*.json")))
        if not lib:
            print("no aisha library samples found")
            return 1
        step = max(1, len(lib) // n)
        picks = lib[::step][:n]
        for f in picks:
            snap = json.load(open(f, encoding="utf-8"))
            print("=" * 64)
            print(f"### {Path(f).name}")
            print(f"legend: {MATRIX_LEGEND}")
            _render_snapshot(snap, "library sample (final state)")
            print()
        return 0
    for a in args:
        _show_export(Path(a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
