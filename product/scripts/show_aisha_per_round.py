"""Per-round aisha estimate audit: for each round show that round's minimap matrix AND the engine
estimate (red room re-weight ON vs OFF), so the round-by-round reasonableness is visible -- not just
the decision round. Each round's matrix + estimate are kept TOGETHER (not all estimates lumped up
front), for easy per-round comparison. Ends with the SETTLED truth matrix.

Reuses minimap_audit_matrix.render_minimap_matrix (1-based), audit_negative_space._library_grids
(item_table-reconstructed settled), and the engine. Read-only.

Run: python scripts/show_aisha_per_round.py 2503 2405 2401   # map-id substrings (picks first match each)
"""

from __future__ import annotations

import glob
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "hero_ref/tools", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import os  # noqa: E402

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from audit_negative_space import _library_grids, _render_matrix  # noqa: E402
import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import extract_evidence, prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from minimap_audit_matrix import MATRIX_LEGEND, render_minimap_matrix  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402
from ahmad_ref_engine import _minimap_items_from_snapshot  # noqa: E402


def _estimate(snap, skill_round):
    snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
    prepared = prepare_reference_engine_snapshot(snap)
    bud = hero_max_combos_for_round("aisha", skill_round)
    out = {}
    for tag, disable in (("ON", False), ("OFF", True)):
        if disable:
            os.environ["BIDKING_DISABLE_AISHA_RED_ROOM"] = "1"
        else:
            os.environ.pop("BIDKING_DISABLE_AISHA_RED_ROOM", None)
        r = run_reference_engine(prepared, max_combos=bud).as_dict()
        out[tag] = {
            "grid": r.get("total_grid_range"),
            "red_cnt": r.get("red_count_range"),
            "red_cells": (r.get("quality_cells_ranges") or {}).get("q6"),
            "val": (r.get("conservative"), r.get("balanced"), r.get("aggressive")),
            "tc": r.get("total_count_estimate"),
            "reweight": any("red_room_reweight" in n for n in (r.get("notes") or [])),
        }
    os.environ.pop("BIDKING_DISABLE_AISHA_RED_ROOM", None)
    return out


def _evidence_line(snap) -> str:
    """该轮引擎拿到的硬证据：公开固定件、揭示下限、被锁的金/红格、用了哪些道具(info_id)、红是否纯先验。"""
    prepared = prepare_reference_engine_snapshot(snap)
    ev = extract_evidence(prepared)
    fixed = {k: v for k, v in ev.fixed_counts.items() if v}
    # 金(q5)/红(q6) 被锁的格子 —— 玩家金扫类道具/技能的真正证据(不是 200xx)
    qc = {k: ev.quality_cells.get(k) for k in ("q5", "q6") if ev.quality_cells.get(k) is not None}
    minc = {k: ev.min_counts.get(k) for k in ("q5", "q6") if ev.min_counts.get(k)}
    # public_info_rows: 游戏「公开信息揭示」事件(info_id 200xx 是揭示类型、非物品、无名字)；
    # 一旦揭示就逐轮挂着，所以会重复。展示它揭了几件、什么品质，而非裸编号。
    pub = []
    for r in (snap.get("public_info_rows") or []):
        det = r.get("revealed_items_detail") or []
        quals = sorted({f"q{d.get('quality')}" for d in det if d.get("quality")})
        pub.append(f"{r.get('info_id')}(揭{len(det)}件{''.join(quals)})" if det else f"{r.get('info_id')}")
    parts = [f"固定件 {fixed or '无'}"]
    if qc:
        parts.append(f"锁定金红格 {qc}(金扫类道具)")
    if minc:
        parts.append(f"揭示下限 {minc}")
    parts.append(f"公开揭示 {pub or '无'}")
    parts.append("红=纯先验(不可见)" if eng._is_pure_prior_red(ev) else "红有证据")
    return "  证据: " + " | ".join(parts)


def _fmt_est(e):
    on, off = e["ON"], e["OFF"]
    changed = on != off
    lines = [
        f"  重加权ON : grid {on['grid']} | 红 {on['red_cnt']}件 {on['red_cells']}格 | 件数 {on['tc']} | 价值 保/参/激 {on['val']}"
        + ("  <<重加权触发" if on["reweight"] else ""),
    ]
    if changed:
        lines.append(
            f"  重加权OFF: grid {off['grid']} | 红 {off['red_cnt']}件 {off['red_cells']}格 | 件数 {off['tc']} | 价值 保/参/激 {off['val']}"
        )
    else:
        lines.append("  (重加权 ON/OFF 本轮无差异)")
    return "\n".join(lines)


def _one_sample(sample: Path, out_dir: Path) -> None:
    events = parse_fatbeans_capture(sample)
    truth = _settlement_breakdown(events) or {}
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
        for i in range(1, len(pre) + 1)
    ]
    tc = truth.get("cells", {}) or {}
    parts = [
        f"# 逐轮估计审查: {sample.name}",
        "",
        f"结算真值: 总 **{truth.get('total_cells')}** 格 / {truth.get('total_items')} 件 | "
        f"金(q5) {tc.get(5)}格 · 红(q6) {tc.get(6)}格",
        "",
        f"legend: {MATRIX_LEGEND}",
        "每轮：DECISION 小地图(该轮所见) + 引擎估计(红余量重加权 ON vs OFF)。最后是 SETTLED 真值。",
        "",
    ]
    for skill_round, skill_id, label in AISHA_SKILL_ROUNDS:
        snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            parts += [f"## R{skill_round}{label}", "（无此轮快照）", ""]
            continue
        items = _minimap_items_from_snapshot(snap) or []
        matrix, _notes, known = render_minimap_matrix(items)
        est = _estimate(snap, skill_round)
        parts += [
            f"## R{skill_round}{label}  (已知 {known} 格)",
            "```",
            matrix if matrix.strip() else "(空)",
            "```",
            _evidence_line(snap),
            _fmt_est(est),
            "",
        ]
    # settled
    dec_grid, set_grid = _library_grids(sample)
    if set_grid:
        deepest_set = max((r for r, _ in set_grid), default=0)
        parts += [
            "## SETTLED (结算真值)",
            "```",
            _render_matrix(set_grid, max_row=deepest_set),
            "```",
            "",
        ]
    fname = f"perround_{sample.name.split('_aisha_')[-1].replace('.json','')}.md"
    (out_dir / fname).write_text("\n".join(parts), encoding="utf-8")
    print(f"  {out_dir / fname}")


def main(argv: list[str]) -> int:
    pats = argv[1:] or ["2503", "2405", "2401"]
    out_dir = ROOT / ".tmp/aisha_per_round"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"per-round audit -> {out_dir}")
    for pat in pats:
        matches = sorted(glob.glob(str(ROOT / f"data/samples/fatbeans/*aisha*{pat}*.json")))
        if not matches:
            print(f"  [skip] no sample for {pat}")
            continue
        _one_sample(Path(matches[0]), out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
