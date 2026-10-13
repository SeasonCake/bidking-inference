"""Eyeball reports: per-round minimap matrix + geometry-prototype estimate.

Like checkpoint_matrix_live_*.md but (a) shows the geometry-prototype proposed
range next to the engine baseline, and (b) renders EVERY row 1..N contiguously
(empty rows drawn as dots) so depth/density is read at a glance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AHMAD_SRC = ROOT / "hero_ref" / "src"
for path in (SCRIPTS, AHMAD_SRC, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from aisha_geometry_floor import adjust_range, compute_features  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import _minimap_grid_items, load_monitor_tables  # noqa: E402
from checkpoint_aisha_grid_sample20 import _evaluate_payload, _minimap_items  # noqa: E402
from layout_depth_policy import GRID_COLUMNS, _item_top_row  # noqa: E402
from minimap_audit_matrix import MATRIX_LEGEND, decode_item_col, item_matrix_symbol  # noqa: E402
from organize_fatbeans_real_samples import sample_meta  # noqa: E402
from regenerate_checkpoint_matrices import (  # noqa: E402
    AISHA_SKILL_ROUNDS,
    MatrixPick,
    _format_public_info,
    _format_skill_frame,
    _skill_ids,
    _snapshot_at_batch,
)

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
REPORT_DIR = ROOT / "data/reports"
MIN_BOTTOM_ROW = 14  # always render rows 1..14 (user request: show empty rows too)

PICKS: tuple[MatrixPick, ...] = (
    MatrixPick("2405_127412794", "fatbeans_valid_aisha_2405_4rounds_2405_1274127940692446_0066.json", "别墅深仓 truth=137", True),
    MatrixPick("2601_0222", "fatbeans_valid_aisha_2601_4rounds_2601_1295018741324567_0222.json", "2601 深仓 truth=136（公开信息仅 R1）", True),
    MatrixPick("2503_0154", "fatbeans_valid_aisha_2503_3rounds_2503_1295018669251860_0154.json", "沉船深仓 truth=116", True),
    MatrixPick("2403_0050", "fatbeans_valid_aisha_2403_3rounds_2403_1295018573729172_0050.json", "2403 深仓 truth=110（无 early total_cells）", True),
    MatrixPick("2410_0005", "fatbeans_valid_aisha_2410_5rounds_2410_1367517613200016_0005.json", "别墅浅仓 truth=65（高估观察）", True),
)


def _build_grid(items: list[dict[str, Any]]) -> dict[tuple[int, int], str]:
    grid: dict[tuple[int, int], str] = {}
    for item in items:
        symbol, _ = item_matrix_symbol(item)
        row = _item_top_row(item, columns=GRID_COLUMNS)
        col = decode_item_col(item, columns=GRID_COLUMNS)
        if row is None or col is None:
            continue
        width = max(1, int(item.get("width") or 1))
        height = max(1, int(item.get("height") or 1))
        for dr in range(height):
            for dc in range(width):
                rr, cc = row + dr, col + dc
                if 1 <= cc <= GRID_COLUMNS:
                    grid[(rr, cc)] = symbol
    return grid


def _render_full(items: list[dict[str, Any]], bottom_row: int) -> str:
    grid = _build_grid(items)
    lines = ["     " + " ".join(f"{c:2d}" for c in range(1, GRID_COLUMNS + 1))]
    for row in range(1, bottom_row + 1):
        cells = " ".join(f"{grid.get((row, col), '.'):>2}" for col in range(1, GRID_COLUMNS + 1))
        lines.append(f"{row:2d}: {cells}")
    return "\n".join(lines)


def _grid_bottom(items: list[dict[str, Any]]) -> int:
    bottoms: list[int] = []
    for it in items:
        top = _item_top_row(it)
        if top is None:
            continue
        bottoms.append(int(top) + max(0, int(it.get("height") or 1) - 1))
    return max(bottoms) if bottoms else 0


# 艾莎无法看到金(G)/红(R)藏品：结算里被金红占的格子，出价阶段对艾莎不可见，
# 因此「估值 < 真值」中由金红填充的那部分是 **可接受误差**（用户裸玩同样看不到）。
# 真正值得修的是：估值连「可见品质(白w/绿v/蓝b/紫p)」的格数都没覆盖到，或明显高估。
INVISIBLE_SYMBOLS = {"G", "R"}


def _settled_visibility(settled_items: list[dict[str, Any]]) -> dict[str, int]:
    """结算矩阵按品质统计可见/不可见格数（用 footprint 占格，去重到格）。"""
    grid = _build_grid(settled_items)
    visible = sum(1 for sym in grid.values() if sym not in INVISIBLE_SYMBOLS)
    invisible = sum(1 for sym in grid.values() if sym in INVISIBLE_SYMBOLS)
    return {"visible": visible, "invisible": invisible, "total": visible + invisible}


def _annotate_round(d_range: tuple, truth: int, visible_cells: int, round_no: int) -> str:
    """对 D 估计给一句诊断标签：欠估金红=可接受；欠估可见品质或高估=⚠。"""
    lo, mid, hi = d_range
    if mid is None:
        return "—"
    gap = mid - truth
    hit = lo is not None and hi is not None and lo <= truth <= hi
    # D mid 是否已覆盖「全部可见品质格」：覆盖到了，剩余 gap 只能是金红不可见 → 可接受
    covers_visible = mid >= visible_cells
    if hit:
        return f"✓命中（mid={mid}, 真值在区间内）"
    if gap < 0:  # 欠估
        if covers_visible:
            invis_gap = truth - visible_cells
            return (
                f"可接受欠估：mid={mid} 已盖住全部可见品质≈{visible_cells} 格，"
                f"剩余 {truth - mid} 格缺口落在金/红不可见区（艾莎裸玩同样看不到）"
            )
        short = visible_cells - mid
        tag = "⚠R1低信息" if round_no == 1 else "⚠值得看"
        return (
            f"{tag}：mid={mid} 连可见品质≈{visible_cells} 格都没盖住（差 {short} 格可见），"
            f"非纯不可见误差"
        )
    # 高估
    return f"⚠高估：mid={mid} > 真值 {truth}（+{gap}），可见品质≈{visible_cells} 格"


def _report(pick: MatrixPick) -> str:
    recording = FATBEANS_DIR / pick.source_file
    events = parse_fatbeans_capture(recording)
    pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    settlement = _settlement_breakdown(events)
    truth = int(settlement["total_cells"])
    truth_payload = {"total_cells": truth, "total_items": int(settlement.get("total_items") or 0)}
    bid_rounds = sample_meta(recording).rounds
    prefix_snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre_batches, batch_no=i, map_id=map_id)
        for i in range(1, len(pre_batches) + 1)
    ]

    batches = live_batches_from_fatbeans_events(events)
    tables = load_monitor_tables()
    settled_items = _minimap_grid_items(batches, tables.items)
    vis = _settled_visibility(settled_items)

    # global bottom row so every round aligns to the same depth
    bottoms = [_grid_bottom(settled_items), MIN_BOTTOM_ROW]
    round_blocks: list[tuple] = []
    for skill_round, skill_id, skill_label in AISHA_SKILL_ROUNDS:
        snap = next((s for s in prefix_snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        payload = {
            "truth": truth_payload, "snapshot": snap, "eval_round": skill_round,
            "source_file": pick.source_file, "map_id": map_id, "source_kind": "fatbeans_batch_b",
        }
        rr = _evaluate_payload(payload)
        items = _minimap_items(snap)
        feat = compute_features(items, known_cells=rr.known_cells or 0)
        base = (rr.grid_low, rr.grid_mid, rr.grid_high)
        strat = {
            "A baseline": base,
            "B band": adjust_range(base, feat, round_no=skill_round, mode="band")[0],
            "C perrow": adjust_range(base, feat, round_no=skill_round, mode="perrow")[0],
            "D band+R1宽": adjust_range(base, feat, round_no=skill_round, mode="band", r1_wide=True)[0],
        }
        bottoms.append(_grid_bottom(items))
        round_blocks.append((skill_round, skill_label, skill_id, snap, feat, base, strat, items))

    bottom = max(bottoms)

    out: list[str] = [
        f"# 几何原型肉眼对照 · {pick.slug} — {pick.note}",
        "",
        f"map={map_id} · {bid_rounds}轮出价 · truth={truth}",
        "",
        f"坐标 1 基；{MATRIX_LEGEND}",
        "策略: A=baseline引擎 · B=band-max几何 · C=per-row几何 · D=band+R1宽区间",
        "估格 lo/mid/hi（gap=mid−truth, ✗=未命中）",
        "几何: 最深行 / 占用行(连续度) / 深带最宽 / 深带行均填充 / taper / f_low",
        "",
        f"**可见性拆分（结算）**：总 {vis['total']} 格 = 可见品质(白绿蓝紫) **{vis['visible']}** 格"
        f" + 金/红不可见 **{vis['invisible']}** 格。",
        "> 艾莎看不到金/红藏品：估值低于真值时，只要 mid 已盖住「可见品质」格数，"
        "剩余缺口落在金红不可见区，属**可接受误差**（用户裸玩同样看不到）。",
        "> **⚠诊断**标注的才是值得修的：要么 mid 连可见品质都没盖住（真欠估），要么高估。",
        "",
    ]

    def _fmt(rng: tuple) -> str:
        lo, mid, hi = rng
        if mid is None:
            return "—"
        hit = lo is not None and hi is not None and lo <= truth <= hi
        return f"{lo}/{mid}/{hi} ({mid - truth:+d}){'' if hit else '✗'}"

    for skill_round, skill_label, skill_id, snap, feat, base, strat, items in round_blocks:
        cont = f"{feat.occupied_rows}/{feat.deepest}={feat.contiguity:.2f}" if feat.deepest else "—"
        out.append(f"## R{skill_round}{skill_label}")
        out.append(f"- {_format_public_info(snap)}")
        out.append(f"- {_format_skill_frame(snap, skill_id)}")
        for name, rng in strat.items():
            tag = "**" if name.startswith("D") else ""
            out.append(f"- {tag}{name}{tag}: {_fmt(rng)}")
        out.append(f"- **诊断**: {_annotate_round(strat['D band+R1宽'], truth, vis['visible'], skill_round)}")
        out.append(
            f"- 几何: 最深行={feat.deepest} · 占用{cont} · 深带最宽={feat.deep_max_col}"
            f" · 深带行均填充={feat.deep_rowfill:.2f} · taper={feat.taper_loss} · f_low={feat.f_low:.2f}"
            f" · known={feat.known_cells}"
        )
        out.append("")
        out.append("```")
        out.append(_render_full(items, bottom))
        out.append("```")
        out.append("")

    out.append(f"## 结算 settled · truth={truth}")
    out.append("")
    out.append("```")
    out.append(_render_full(settled_items, bottom))
    out.append("```")
    out.append("")
    return "\n".join(out)


def main() -> int:
    for pick in PICKS:
        text = _report(pick)
        path = REPORT_DIR / f"geometry_eyeball_{pick.slug}.md"
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
