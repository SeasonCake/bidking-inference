"""Audit: when a lone deep item reaches the bottom unsupported, does raw_deepest x columns
over-state the warehouse grid?

User insight (2402): a single large item sits alone in a deep cell with no neighbours supporting
that depth, so `depth_grid = raw_deepest * columns` (the count-anchor numerator) assumes a full
rectangle and over-states the true occupied cells. Supported-depth vaults (2408/2409) have multiple
items packed near the bottom and are NOT over-stated.

For each settled aisha library sample, at the decision (last pre-settlement) round, this reconstructs
the minimap layout (row/col/width/height via shape_key), then reports per "bottom support" bucket:
  - depth_grid = raw_deepest * AISHA_GRID_COLUMNS  (current count-anchor numerator)
  - truth total cells
  - over = depth_grid - truth   (mean / how often depth_grid > truth)
  - bottom support metrics: items reaching the deepest row, occupied cells in the deepest 2 rows.

Bucketing by bottom support shows whether sparse-bottom vaults are systematically over-stated and
where to threshold an occupancy discount. Read-only.
Run: python scripts/audit_lone_deep_overstate.py [hero] [cap]
"""

from __future__ import annotations

import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    AISHA_GRID_COLUMNS,
    _deepest_minimap_bottom_row,
    _minimap_items_from_snapshot,
)
from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402

SAMPLE_DIR = ROOT / "data/samples/fatbeans"


def _item_span(it: dict) -> tuple[int, int, int, int] | None:
    """Return (top_row, bottom_row, left_col, width) for a minimap item, or None."""
    row = it.get("row") or it.get("bottom_row")
    if row is None:
        return None
    row = int(row)
    height = int(it.get("height") or 1) or 1
    width = int(it.get("width") or 1) or 1
    # `row` is the TOP row in live snapshots; bottom = row + height - 1.
    return row, row + height - 1, int(it.get("col") or 0), width


def _bottom_support(items: list[dict], raw_deepest: int) -> tuple[int, int]:
    """(items_reaching_deepest_row, occupied_cells_in_deepest_two_rows)."""
    deep_band = {raw_deepest, raw_deepest - 1}
    reaching = 0
    occ = 0
    for it in items:
        span = _item_span(it)
        if span is None:
            continue
        top, bottom, _col, width = span
        if bottom >= raw_deepest:
            reaching += 1
        for r in range(top, bottom + 1):
            if r in deep_band:
                occ += width
    return reaching, occ


def main(argv: list[str]) -> int:
    hero = argv[1] if len(argv) > 1 else "aisha"
    cap = int(argv[2]) if len(argv) > 2 else 200

    rows: list[dict] = []
    used = 0
    for f in sorted(glob.glob(str(SAMPLE_DIR / f"*_{hero}_*.json")))[:cap]:
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        s = _settlement_breakdown(events)
        truth_cells = int(s.get("total_cells") or 0)
        if truth_cells <= 0:
            continue
        map_id = _map_id_from_events(events)
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            continue
        snap = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id)
        items = _minimap_items_from_snapshot(snap) or []
        raw_deepest = _deepest_minimap_bottom_row(items)
        if not raw_deepest or raw_deepest <= 0:
            continue
        depth_grid = int(raw_deepest) * AISHA_GRID_COLUMNS
        reaching, deep_occ = _bottom_support(items, int(raw_deepest))
        rows.append(
            {
                "fam": (map_id or 0) // 100,
                "raw_deepest": int(raw_deepest),
                "depth_grid": depth_grid,
                "truth": truth_cells,
                "over": depth_grid - truth_cells,
                "reaching": reaching,
                "deep_occ": deep_occ,
                "deep_density": deep_occ / (2 * AISHA_GRID_COLUMNS),
            }
        )
        used += 1

    if not rows:
        print(f"no samples for hero={hero}")
        return 1

    def summarize(subset: list[dict], label: str) -> None:
        if not subset:
            print(f"  {label:<28s} n=0")
            return
        over = [r["over"] for r in subset]
        over_rate = sum(1 for r in subset if r["over"] > 8) / len(subset)
        print(
            f"  {label:<28s} n={len(subset):>3d}  over_mean={statistics.mean(over):+6.1f}  "
            f"over_med={statistics.median(over):+6.1f}  over>8_rate={over_rate:.2f}  "
            f"depth_grid_mean={statistics.mean(r['depth_grid'] for r in subset):.0f}  "
            f"truth_mean={statistics.mean(r['truth'] for r in subset):.0f}"
        )

    print(f"=== lone-deep over-statement audit (hero={hero}, n={used}) ===\n")
    print("depth_grid = raw_deepest * columns; over = depth_grid - truth_cells (>0 = over-state)\n")
    print("[by items reaching the deepest row]")
    for k in (1, 2, 3):
        summarize([r for r in rows if r["reaching"] == k], f"reaching=={k}")
    summarize([r for r in rows if r["reaching"] >= 4], "reaching>=4")
    print("\n[by deep-band density (occupied cells in deepest 2 rows / 2*cols)]")
    summarize([r for r in rows if r["deep_density"] < 0.20], "density<0.20 (sparse bottom)")
    summarize([r for r in rows if 0.20 <= r["deep_density"] < 0.40], "0.20<=density<0.40")
    summarize([r for r in rows if r["deep_density"] >= 0.40], "density>=0.40 (supported)")
    print("\n[overall]")
    summarize(rows, "all")
    print(
        "\nCRUX READ: if sparse-bottom / reaching==1 buckets show large positive over_mean while"
        "\n  supported buckets are ~0, a bottom-occupancy discount on depth_grid is justified, gated"
        "\n  to the sparse bucket so supported-deep vaults (2408/2409) are untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
