"""Negative-space audit: do cells that are EMPTY in aisha's decision-round view but ENCLOSED by
revealed items turn into high-tier (gold/red) at settlement?

User's idea (C): the revealed white/green/blue/purple outlines bound empty regions; those bounded
empties are the unrevealed high-tier items, so "enclosed empty cell count" is a hard lower bound on
gold+red cells -- robust even when the exact shape is unknown. Caveat: deeper rows can stay
genuinely empty (true empty grid, not an item), which inflates the bound.

This measures, across aisha library samples at the decision (last pre-settlement) round:
  - recall: of the unrevealed high-tier (G/R) cells at settlement, how many were ENCLOSED in the
    decision view (i.e. would the negative-space rule have caught them)?
  - precision: of the ENCLOSED-empty cells in the decision view, how many actually became high-tier
    (vs low/mid, vs stayed truly empty)?
  - split shallow (row <= 6) vs deep (row >= 7) to size the deep-empty error the user flagged.

Enclosure = the empty cell has a revealed cell both to its LEFT and RIGHT in the same row AND both
ABOVE and BELOW in the same column (4-way bounded). Read-only.
Run: python scripts/audit_negative_space.py [cap]
"""

from __future__ import annotations

import glob
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import json  # noqa: E402

from ahmad_ref_engine import _minimap_items_from_snapshot  # noqa: E402
from layout_depth_policy import GRID_COLUMNS, WAREHOUSE_ROWS, _item_top_row  # noqa: E402
from minimap_audit_matrix import decode_item_col  # noqa: E402

EXPORT_GLOB = str(ROOT / ".tmp/nsaudit/*/round_snapshots")

HIGH = {"G", "R"}  # gold / red
QSYM = {1: "w", 2: "v", 3: "b", 4: "p", 5: "G", 6: "R"}
SHALLOW_MAX_ROW = 6
# 隐秘(超富)拍卖图族:入场门槛高、爆率超高(高品~46% vs villa~5-17% / shipwreck~20-34%),误差天生大、
# 非典型,样本筛查/矩阵尽量不用(用户 2026-06-19)。26xx 实测高品 46% 远超其它。
_HIDDEN_MAP_FAMILIES = {"26"}


def _occupancy(items: list[dict]) -> dict[tuple[int, int], str]:
    """(row, col) -> quality symbol, expanding each item over its footprint (1-based)."""
    grid: dict[tuple[int, int], str] = {}
    for it in items:
        row = _item_top_row(it, columns=GRID_COLUMNS)
        col = decode_item_col(it, columns=GRID_COLUMNS)
        if row is None or col is None:
            continue
        sym = QSYM.get(int(it.get("quality") or 0), "?")
        w = max(1, int(it.get("width") or 1))
        h = max(1, int(it.get("height") or 1))
        for dr in range(h):
            for dc in range(w):
                rr, cc = row + dr, col + dc
                if 1 <= rr <= WAREHOUSE_ROWS and 1 <= cc <= GRID_COLUMNS:
                    grid[(rr, cc)] = sym
    return grid


_ITEM_SHAPES: dict[int, tuple[int, int]] | None = None


def _item_shapes() -> dict[int, tuple[int, int]]:
    """item_id -> (shape_w, shape_h) from Item.txt, for reconstructing unrevealed settled footprints."""
    global _ITEM_SHAPES
    if _ITEM_SHAPES is None:
        from bidking_lab.extract.item_table import load_item_table

        path = ROOT / "data" / "raw" / "tables" / "Item.txt"
        _ITEM_SHAPES = {
            int(it.item_id): (int(it.shape_w), int(it.shape_h))
            for it in load_item_table(path).values()
        } if path.is_file() else {}
    return _ITEM_SHAPES


def _library_grids(sample: Path):
    """(dec_grid, set_grid) for a library sample. Revealed items keep their EXACT decision footprint;
    unrevealed settled items are reconstructed from item_table shape at their local_index top-left."""
    from audit_aisha_gap import _map_id_from_events
    from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture
    from regenerate_checkpoint_matrices import _snapshot_at_batch

    try:
        events = parse_fatbeans_capture(sample)
    except Exception:
        return None, None
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    if len(pre) < 2:
        return None, None
    map_id = _map_id_from_events(events)
    dec = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=len(pre), map_id=map_id)
    dec_items = _minimap_items_from_snapshot(dec) or []
    dec_grid = _occupancy(dec_items)
    revealed_rt = {it.get("runtime_id") for it in dec_items if it.get("runtime_id") is not None}
    state = next((s for s in reversed(events.states) if s.inventory_items), None)
    if state is None:
        return None, None
    shapes = _item_shapes()
    set_grid = dict(dec_grid)  # revealed items keep their exact footprint
    for it in state.inventory_items:
        q = int(it.quality or 0)
        if q <= 0 or it.runtime_id in revealed_rt:
            continue
        li = it.local_index
        if li is None:
            continue
        row = int(li) // GRID_COLUMNS + 1
        col = int(li) % GRID_COLUMNS + 1
        w, h = shapes.get(int(it.item_id), (1, 1))
        w, h = max(1, w), max(1, h)
        sym = QSYM.get(q, "?")
        for dr in range(h):
            for dc in range(w):
                rr, cc = row + dr, col + dc
                if 1 <= rr <= WAREHOUSE_ROWS and 1 <= cc <= GRID_COLUMNS:
                    set_grid.setdefault((rr, cc), sym)
    return dec_grid, set_grid


def _enclosed(cell: tuple[int, int], revealed: set[tuple[int, int]]) -> bool:
    r, c = cell
    left = any((r, cc) in revealed for cc in range(1, c))
    right = any((r, cc) in revealed for cc in range(c + 1, GRID_COLUMNS + 1))
    up = any((rr, c) in revealed for rr in range(1, r))
    down = any((rr, c) in revealed for rr in range(r + 1, WAREHOUSE_ROWS + 1))
    return left and right and up and down


def _accumulate(dec_grid, set_grid, tp_recall, tot_high, enclosed_outcome) -> bool:
    revealed = set(dec_grid)
    if not revealed or not set_grid:
        return False
    for cell, sym in set_grid.items():
        if cell in revealed:
            continue  # aisha already saw it
        depth = "shallow" if cell[0] <= SHALLOW_MAX_ROW else "deep"
        if sym in HIGH:
            tot_high[depth] += 1
            if _enclosed(cell, revealed):
                tp_recall[depth] += 1
    all_cells = {
        (r, c) for r in range(1, WAREHOUSE_ROWS + 1) for c in range(1, GRID_COLUMNS + 1)
    }
    for cell in all_cells - revealed:
        if not _enclosed(cell, revealed):
            continue
        depth = "shallow" if cell[0] <= SHALLOW_MAX_ROW else "deep"
        outcome = set_grid.get(cell)
        if outcome in HIGH:
            enclosed_outcome[depth]["high"] += 1
        elif outcome is not None:
            enclosed_outcome[depth]["low_mid"] += 1
        else:
            enclosed_outcome[depth]["empty"] += 1
    return True


def _render_matrix(grid: dict, *, restrict: set | None = None, max_row: int | None = None) -> str:
    """Render a (row,col)->symbol grid as a 1-based matrix. restrict=show only those cells (others
    blank), so a HIDDEN overlay only paints what aisha could NOT see at decision."""
    if max_row is None:
        src = restrict if restrict else set(grid)
        max_row = max((r for r, _ in src), default=1)
    header = "      " + "".join(str(c % 10) for c in range(1, GRID_COLUMNS + 1))
    lines = [header]
    for r in range(1, int(max_row) + 1):
        row_cells = []
        for c in range(1, GRID_COLUMNS + 1):
            if restrict is not None and (r, c) not in restrict:
                row_cells.append(" ")
            else:
                row_cells.append(grid.get((r, c), "."))
        lines.append(f"  r{r:>2} {''.join(row_cells)}")
    return "\n".join(lines)


def _sample_hidden_profile(sample: Path):
    """(reveal, deepest_known_row, hidden_cells, hidden_gold, hidden_red, dec_grid, set_grid) or None."""
    dec_grid, set_grid = _library_grids(sample)
    if not dec_grid or not set_grid:
        return None
    deepest_known = max((r for r, _ in dec_grid), default=0)
    hidden = {cell: sym for cell, sym in set_grid.items() if cell not in dec_grid}
    hidden_gold = sum(1 for s in hidden.values() if s == "G")
    hidden_red = sum(1 for s in hidden.values() if s == "R")
    reveal = len(dec_grid) / max(1, len(set_grid))
    return reveal, deepest_known, hidden, hidden_gold, hidden_red, dec_grid, set_grid


def _matrices_report(cap: int, n: int, out_path: Path) -> int:
    """Pick the N aisha library samples with the most HIDDEN big gold/red (sunk-bottom high-tier) and
    render DECISION / SETTLED / HIDDEN matrices to an MD file for the user to verify the knowability cut."""
    lib = sorted(glob.glob(str(ROOT / "data/samples/fatbeans/*aisha*4rounds*.json")))[:cap]
    profiles = []
    for f in lib:
        nm = Path(f).name
        m = re.search(r"aisha_(\d+)_", nm)
        map_id = m.group(1) if m else ""
        if map_id[:2] in _HIDDEN_MAP_FAMILIES:  # 隐秘(超富)图:误差天生大、非典型,排除(用户口径)
            continue
        p = _sample_hidden_profile(Path(f))
        if p is None:
            continue
        reveal, deepest, hidden, hg, hr, dec, setg = p
        # sunk-bottom score: hidden high-tier cells, weighted toward those BELOW the deepest known row
        below = sum(1 for (r, _), s in hidden.items() if r > deepest and s in HIGH)
        profiles.append((hg + hr + below, nm, reveal, deepest, hidden, hg, hr, dec, setg))
    profiles.sort(key=lambda x: -x[0])
    # Dedup by map so the 5 span different warehouses (not all one gold-rich shipwreck map).
    seen_maps: set[str] = set()
    picks = []
    for prof in profiles:
        nm = prof[1]
        parts = nm.split("_aisha_", 1)
        map_id = parts[1].split("_", 1)[0] if len(parts) > 1 else nm
        if map_id in seen_maps:
            continue
        seen_maps.add(map_id)
        picks.append(prof)
        if len(picks) >= n:
            break

    out_dir = out_path.parent / "aisha_matrices"
    out_dir.mkdir(parents=True, exist_ok=True)
    legend = "legend: w白 v绿 b蓝 p紫 G金 R红 .空 (HIDDEN 视图里空格=艾莎当时已看到/此格无沉底)"
    written = []
    for idx, (score, name, reveal, deepest, hidden, hg, hr, dec, setg) in enumerate(picks, 1):
        deepest_set = max((r for r, _ in setg), default=0)
        hidden_deep = sum(1 for (r, _), s in hidden.items() if r >= 7 and s in HIGH)
        body = "\n".join([
            f"# 沉底金红样本 {idx}: {name}",
            "",
            f"- reveal_ratio = **{reveal:.2f}**(已知格 / 结算总格） | 已知最深行 {deepest} / 结算最深行 {deepest_set}",
            f"- 沉底(决策看不见)高品: **金 {hg} 格 / 红 {hr} 格**；其中深行(≥7行)= {hidden_deep} 格",
            "",
            "三视图(1-based row/col)：DECISION=R4 艾莎所见 · SETTLED=结算真值(沉底件按 item_table 形状重建) · HIDDEN=两者之差(看不见的沉底金红)",
            "",
            legend,
            "",
            "```",
            "DECISION (R4 艾莎所见):",
            _render_matrix(dec, max_row=deepest_set),
            "",
            "SETTLED (结算真值):",
            _render_matrix(setg, max_row=deepest_set),
            "",
            "HIDDEN (沉底=决策时看不见的):",
            _render_matrix(setg, restrict=set(hidden), max_row=deepest_set),
            "```",
            "",
        ])
        m = re.search(r"aisha_(\d+)_", name)
        fname = f"{idx:02d}_aisha_{m.group(1) if m else 'x'}_{name.split('_')[-1].replace('.json','')}.md"
        fpath = out_dir / fname
        fpath.write_text(body, encoding="utf-8")
        written.append((fpath, reveal, hg, hr))
    # combined index
    out_path.write_text(
        "# 艾莎沉底金红样本矩阵（已排除隐秘26xx超富图，仅 villa/shipwreck）\n\n"
        + "\n".join(
            f"- [{p.name}]({p.as_posix()}) — reveal {rv:.2f}, 沉底金{hg}/红{hr}格"
            for p, rv, hg, hr in written
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(written)} per-sample matrices -> {out_dir}")
    for p, rv, hg, hr in written:
        print(f"  {p}")
    return 0


def main(argv: list[str]) -> int:
    if "--matrices" in argv:
        rest = [a for a in argv[1:] if a != "--matrices"]
        n = int(rest[0]) if rest else 5
        return _matrices_report(cap=400, n=n, out_path=ROOT / ".tmp/aisha_hidden_bottom_matrices.md")
    use_lib = "--lib" in argv
    rest = [a for a in argv[1:] if a != "--lib"]
    cap = int(rest[0]) if rest else (400 if use_lib else 150)
    tp_recall = Counter()  # unrevealed-high cells that ARE enclosed
    tot_high = Counter()  # all unrevealed-high cells
    enclosed_outcome = {"shallow": Counter(), "deep": Counter()}  # enclosed empty -> became what
    n = 0
    if use_lib:
        source = "library (item_table-reconstructed settled footprints)"
        for sample in sorted(glob.glob(str(ROOT / "data/samples/fatbeans/*_aisha_*.json")))[:cap]:
            dec_grid, set_grid = _library_grids(Path(sample))
            if dec_grid is None:
                continue
            if _accumulate(dec_grid, set_grid, tp_recall, tot_high, enclosed_outcome):
                n += 1
    else:
        source = "live exports (exact settled minimaps)"
        for snap_dir in sorted(glob.glob(EXPORT_GLOB))[:cap]:
            settled_files = sorted(glob.glob(f"{snap_dir}/*_settled.json"))
            bidding_files = sorted(glob.glob(f"{snap_dir}/r*_bidding.json"))
            if not settled_files or not bidding_files:
                continue
            settled = json.load(open(settled_files[-1], encoding="utf-8"))
            hero = (settled.get("ui_contract", {}).get("context", {}) or {}).get("hero") or settled.get("hero")
            if hero != "aisha":
                continue
            decision = json.load(open(bidding_files[-1], encoding="utf-8"))
            dec_grid = _occupancy(_minimap_items_from_snapshot(decision) or [])
            set_grid = _occupancy(_minimap_items_from_snapshot(settled) or [])
            if _accumulate(dec_grid, set_grid, tp_recall, tot_high, enclosed_outcome):
                n += 1

    print(f"=== negative-space audit ({source}, aisha decision round, n={n}) ===\n")
    print("RECALL -- of unrevealed high-tier (gold/red) cells, fraction that were enclosed:")
    for depth in ("shallow", "deep"):
        tot = tot_high[depth]
        rec = tp_recall[depth] / tot if tot else 0.0
        print(f"  {depth:<8} {tp_recall[depth]:>4}/{tot:<4} = {rec:.2f}")
    tot_all = sum(tot_high.values())
    print(f"  {'ALL':<8} {sum(tp_recall.values()):>4}/{tot_all:<4} = {sum(tp_recall.values())/tot_all if tot_all else 0:.2f}")
    print("\nPRECISION -- of ENCLOSED-empty cells, what they became at settlement:")
    for depth in ("shallow", "deep"):
        oc = enclosed_outcome[depth]
        tot = sum(oc.values())
        if not tot:
            print(f"  {depth:<8} n=0")
            continue
        print(
            f"  {depth:<8} n={tot:<5} high={oc['high']/tot:.2f}  low_mid={oc['low_mid']/tot:.2f}  "
            f"empty(true-empty err)={oc['empty']/tot:.2f}"
        )
    print(
        "\nREAD: high recall (shallow) => enclosed-empty catches the gold/red. high precision +"
        "\n  low true-empty err => enclosed-empty count is a safe high-tier lower bound. A large deep"
        "\n  true-empty fraction is the error the user flagged -> weight the bound to shallow rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
