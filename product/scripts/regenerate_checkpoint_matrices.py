"""Regenerate checkpoint matrix reports (live + feel-filtered eval samples)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AHMAD_SRC = ROOT / "hero_ref" / "src"
for path in (SCRIPTS, AHMAD_SRC, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import (  # noqa: E402
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import _minimap_grid_items, load_monitor_tables  # noqa: E402
from checkpoint_aisha_grid_sample20 import (  # noqa: E402
    RoundRow,
    _evaluate_payload,
    _format_matrix_report,
    _minimap_items,
)
from filter_aisha_feel_samples import PUBLIC_INFO_LABELS, _reject_reasons  # noqa: E402
from layout_depth_policy import _deepest_minimap_bottom_row, _known_minimap_cells  # noqa: E402
from minimap_audit_matrix import render_minimap_matrix  # noqa: E402
from organize_fatbeans_real_samples import sample_meta  # noqa: E402

REPORT_DIR = ROOT / "data/reports"
LIVE_SNAP_DIR = ROOT / "data/logs/live/round_snapshots"
FATBEANS_DIR = ROOT / "data/samples/fatbeans"
MATRIX_INDEX = REPORT_DIR / "checkpoint_matrix_index.md"
FILTER_JSON = REPORT_DIR / "aisha_feel_sample_filter.json"

AISHA_SKILL_ROUNDS: tuple[tuple[int, int, str], ...] = (
    (1, 1001034, "白"),
    (2, 1001033, "绿"),
    (3, 1001032, "蓝"),
    (4, 1001031, "紫"),
)
QUALITY_CN = {1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}


@dataclass(frozen=True)
class MatrixPick:
    slug: str
    source_file: str
    note: str
    feel_eligible: bool


# Feel 样本：艾莎1白时无 total_cells / warehouse_total_cells / 公开总格(200009)
FEEL_PICKS: tuple[MatrixPick, ...] = (
    MatrixPick(
        slug="2403_0053",
        source_file="fatbeans_valid_aisha_2403_4rounds_2403_1295019013566459_0053.json",
        note="2403 深仓 truth=95 · 估格 progression 正常",
        feel_eligible=True,
    ),
    MatrixPick(
        slug="2403_0050",
        source_file="fatbeans_valid_aisha_2403_3rounds_2403_1295018573729172_0050.json",
        note="2403 深仓 truth=110 · 无 early total_cells",
        feel_eligible=True,
    ),
    MatrixPick(
        slug="2405_127412794",
        source_file="fatbeans_valid_aisha_2405_4rounds_2405_1274127940692446_0066.json",
        note="2405 别墅 truth=137 · 四轮技能帧齐",
        feel_eligible=True,
    ),
    MatrixPick(
        slug="2401_0036",
        source_file="fatbeans_valid_aisha_2401_4rounds_2401_1295018569271465_0036.json",
        note="2401 浅局 truth=59",
        feel_eligible=True,
    ),
    MatrixPick(
        slug="2507_0195",
        source_file="fatbeans_valid_aisha_2507_5rounds_2507_1295018593862787_0195.json",
        note="2507 沉船 truth=68 · 公开信息仅 R1",
        feel_eligible=True,
    ),
    MatrixPick(
        slug="2601_0222",
        source_file="fatbeans_valid_aisha_2601_4rounds_2601_1295018741324567_0222.json",
        note="2601 hidden truth=136 · 公开信息仅 R1",
        feel_eligible=True,
    ),
)

# 对照：有明确总格或 progression 异常，不做 feel 锚点
EXCLUDED_PICKS: tuple[MatrixPick, ...] = (
    MatrixPick(
        slug="2403_0052_locked",
        source_file="fatbeans_valid_aisha_2403_4rounds_2403_1295018989226069_0052.json",
        note="对照：reading 锁 warehouse_total_cells=98，R2 起估格全 98",
        feel_eligible=False,
    ),
    MatrixPick(
        slug="2403_0051_bad",
        source_file="fatbeans_valid_aisha_2403_3rounds_2403_1295019017648392_0051.json",
        note="对照：R1/R2 偏高，R3 贴结算 111",
        feel_eligible=False,
    ),
)


def _skill_ids(snapshot: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for key in ("skill_reveals", "skill_reveal_rows"):
        rows = snapshot.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("skill_id") is not None:
                out.add(int(row["skill_id"]))
    return out


def _snapshot_at_batch(
    *, events, pre_batches: list, batch_no: int, map_id: int | None, hero: str = "aisha"
) -> dict[str, Any]:
    # hero defaults to "aisha" for back-compat, but cross-hero audits MUST pass the
    # real hero: it drives the engine's hero-scoped paths (draw-hero count/value scale,
    # sparse_early grid spec, aisha footroom). Running a sophie sample as "aisha" measures
    # the wrong model entirely.
    prefix = pre_batches[:batch_no]
    sort_id = _prefix_sort_id(prefix)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    snap = _build_snapshot(
        hero=hero,
        events=prefix_events,
        prefix_batches=prefix,
        map_id=map_id,
    )
    # ⚠️ ROUND FIDELITY (2026-07-09): do NOT override context.round with batch_no. batch_no is the
    # STATE-FRAME iteration index (public-info / skill / tool / grid are each their own frame ⇒ ~2-3
    # frames per game round), NOT the game round. _build_snapshot already sets context.round to the correct
    # action_round (the bidding round the engine's round-gated logic expects, matching live). The old
    # `["round"] = int(batch_no)` clobbered that back to the frame index, so every audit built on this
    # helper (ab_aisha_*, audit_*) ran round-gated logic at ~2x the true round. Iterate batch_no for the
    # checkpoint-matrix rows if you must, but the engine must see the true round.
    return snap


def _format_public_info(snapshot: dict[str, Any]) -> str:
    rows = snapshot.get("public_info_rows") or []
    if not rows:
        return "公开信息: 无"
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        info_id = row.get("info_id")
        value = row.get("value")
        label = PUBLIC_INFO_LABELS.get(int(info_id) if info_id is not None else 0, f"info_{info_id}")
        parts.append(f"{label}={value}")
    return "公开信息: " + "; ".join(parts)


def _format_skill_frame(snapshot: dict[str, Any], skill_id: int) -> str:
    for key in ("skill_reveal_rows", "skill_reveals"):
        rows = snapshot.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or int(row.get("skill_id") or 0) != int(skill_id):
                continue
            items = row.get("observed_items") or row.get("revealed_items_detail") or ()
            by_quality: Counter[int] = Counter()
            cell_sum = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                quality = int(item.get("quality") or 0)
                if quality <= 0:
                    continue
                by_quality[quality] += 1
                cell_sum += int(item.get("cells") or 1)
            tier_parts = [
                f"{QUALITY_CN.get(quality, '?')}×{count}"
                for quality, count in sorted(by_quality.items())
            ]
            tool = str(row.get("tool") or "").strip()
            summary = str(row.get("revealed_summary") or "").strip()
            head = tool or summary or f"skill_{skill_id}"
            tiers = "+".join(tier_parts) if tier_parts else "无道具明细"
            return f"技能帧: {head} · {tiers} · 约{cell_sum}格"
    return f"技能帧: skill_{skill_id} (capture 无明细)"


def _format_bridge(snapshot: dict[str, Any]) -> str:
    bridge = snapshot.get("structured_ref_inputs") or {}
    parts: list[str] = []
    total_cells = bridge.get("total_cells")
    total_count = bridge.get("total_count")
    parts.append(f"bridge.total_cells={'—' if total_cells is None else total_cells}")
    parts.append(f"bridge.total_count={'—' if total_count is None else total_count}")
    split_counts = bridge.get("split_counts") if isinstance(bridge.get("split_counts"), dict) else {}
    split_cells = bridge.get("split_quality_cells") if isinstance(bridge.get("split_quality_cells"), dict) else {}
    if split_counts:
        split_text = ", ".join(
            f"{tier} {split_counts.get(tier, '—')}/{split_cells.get(tier, '—')}"
            for tier in ("white", "green")
        )
        parts.append(f"split({split_text})")
    counts = bridge.get("counts") if isinstance(bridge.get("counts"), dict) else {}
    cells = bridge.get("quality_cells") if isinstance(bridge.get("quality_cells"), dict) else {}
    tier_keys = [key for key in ("q3", "q4", "q5", "q6") if key in counts or key in cells]
    if tier_keys:
        tier_text = ", ".join(
            f"{key} {counts.get(key, '—')}/{cells.get(key, '—')}" for key in tier_keys
        )
        parts.append(f"tiers({tier_text})")
    return "bridge: " + " · ".join(parts)


def _round_extras(snapshot: dict[str, Any], skill_id: int) -> list[str]:
    return [
        _format_public_info(snapshot),
        _format_skill_frame(snapshot, skill_id),
        _format_bridge(snapshot),
    ]


def _live_session_matrix_report() -> str:
    paths = sorted(LIVE_SNAP_DIR.glob("*.json"))
    settled_path = LIVE_SNAP_DIR / "r03_settled.json"
    session_truth = 74
    if settled_path.is_file():
        settled_payload = json.loads(settled_path.read_text(encoding="utf-8"))
        session_truth = int(settled_payload.get("inventory_cells") or session_truth)
    rounds: list[tuple[str, dict[str, Any], str, RoundRow | None, list[str]]] = []
    session_id = "?"
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        session_id = str(payload.get("session_id") or session_id)
        snap = payload
        ctx = snap.get("ui_contract", {}).get("context", {})
        rnd = ctx.get("round")
        phase = ctx.get("phase")
        label = f"R{rnd} {phase} ({path.name})"
        items = snap.get("ui_contract", {}).get("minimap", {}).get("items") or []
        matrix, _, _ = render_minimap_matrix(items)
        truth = int(payload.get("inventory_cells") or session_truth)
        eval_payload = {
            "truth": {"total_cells": truth},
            "snapshot": snap,
            "eval_round": rnd,
            "source_file": path.name,
            "map_id": payload.get("map_id"),
            "source_kind": "live",
        }
        row = _evaluate_payload(eval_payload)
        extras = [_format_public_info(snap), _format_bridge(snap)]
        rounds.append((label, snap, matrix, row, extras))
    return _format_matrix_report(
        title="Checkpoint 矩阵 A · Live 2401 正样本",
        session_label=session_id,
        rounds=rounds,
    )


def _eval_session_matrix_report(*, pick: MatrixPick) -> str:
    recording = FATBEANS_DIR / pick.source_file
    if not recording.is_file():
        return _format_matrix_report(
            title=f"Checkpoint · Eval {pick.source_file}",
            session_label=f"missing recording: {pick.source_file}",
            rounds=[],
        )

    events = parse_fatbeans_capture(recording)
    pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    settlement = _settlement_breakdown(events)
    truth_payload = {
        "total_cells": int(settlement["total_cells"]),
        "total_items": int(settlement.get("total_items") or 0),
    }
    bid_rounds = sample_meta(recording).rounds
    prefix_snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre_batches, batch_no=index, map_id=map_id)
        for index in range(1, len(pre_batches) + 1)
    ]

    white_snap = next((snap for snap in prefix_snaps if 1001034 in _skill_ids(snap)), None)
    reject = _reject_reasons(white_snap) if white_snap else ["no_white_skill_frame"]

    rounds: list[tuple[str, dict[str, Any], str, RoundRow | None, list[str]]] = []
    notes: list[str] = [
        pick.note,
        f"feel_eligible={'Y' if pick.feel_eligible and not reject else 'N'}",
        f"文件名 {bid_rounds}rounds = 出价窗口数；状态批 B1–B{len(pre_batches)}。",
        "矩阵行 = 艾莎技能帧（白→绿→蓝→紫）首次出现时快照。",
    ]
    if reject:
        notes.append("艾莎1白时明确信息: " + "; ".join(reject))
    elif white_snap:
        pub = _format_public_info(white_snap)
        if pub != "公开信息: 无":
            notes.append(f"艾莎1白 {pub}（总件公开 OK，总格未锁）")

    for skill_round, skill_id, skill_label in AISHA_SKILL_ROUNDS:
        snap_at_skill: dict[str, Any] | None = None
        batch_no = 0
        for index, snap in enumerate(prefix_snaps, start=1):
            if skill_id in _skill_ids(snap):
                snap_at_skill = snap
                batch_no = index
                break
        if snap_at_skill is None:
            notes.append(f"- 艾莎{skill_label} skill_{skill_id}：capture 缺帧，跳过")
            continue
        # 引擎按 ui_contract.context.round 做轮次门控（deep_visibility/footroom）；
        # 这里必须喂艾莎技能轮(1-4)，不能用状态批号 batch_no，否则 round 错位导致估格失真。
        snap_at_skill.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        payload = {
            "truth": truth_payload,
            "snapshot": snap_at_skill,
            "eval_round": skill_round,
            "source_file": pick.source_file,
            "map_id": map_id,
            "source_kind": "fatbeans_batch_b",
        }
        row = _evaluate_payload(payload)
        items = _minimap_items(snap_at_skill)
        matrix, _, _ = render_minimap_matrix(items)
        label = (
            f"艾莎{skill_round}{skill_label} · 状态批B{batch_no}/{len(pre_batches)}"
            f" · 样本{bid_rounds}轮出价"
        )
        rounds.append((label, snap_at_skill, matrix, row, _round_extras(snap_at_skill, skill_id)))

    batches = live_batches_from_fatbeans_events(events)
    tables = load_monitor_tables()
    settled_items = _minimap_grid_items(batches, tables.items)
    matrix, _, _ = render_minimap_matrix(settled_items)
    truth = int(settlement["total_cells"])
    row = RoundRow(
        source_file=pick.source_file,
        map_id=map_id,
        source_kind="fatbeans_batch_b",
        eval_round=0,
        truth_cells=truth,
        grid_low=truth,
        grid_mid=truth,
        grid_high=truth,
        raw_deepest=_deepest_minimap_bottom_row(settled_items),
        known_cells=_known_minimap_cells(settled_items),
        cells_hit=True,
        mid_gap=0,
        low_gap=0,
        high_gap=0,
        status="settled_truth",
    )
    settled_extras = ["公开信息: —（结算）", "技能帧: —", f"bridge: settled truth={truth}"]
    rounds.append((f"结算 settled · truth={truth}", {}, matrix, row, settled_extras))

    tag = "Feel" if pick.feel_eligible and not reject else "对照"
    title = f"Checkpoint · {tag} · {pick.source_file}"
    session_label = f"map={map_id} · {pick.source_file}\n\n" + "\n".join(notes)
    return _format_matrix_report(title=title, session_label=session_label, rounds=rounds)


def main() -> int:
    from filter_aisha_feel_samples import main as run_filter

    run_filter()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    index_lines = [
        "# Checkpoint 矩阵索引",
        "",
        "艾莎估格 feel 核对。**仅 Feel 段样本**在艾莎1白时无明确总格（无 bridge.total_cells / warehouse_total_cells / 公开总格200009）。",
        "符号 **v=绿**（避免与金 G 混淆）。每节含：公开信息、技能帧道具、bridge。",
        "",
        f"完整筛选表: [{FILTER_JSON.name}]({FILTER_JSON.name})",
        "",
        "## Live",
        "",
    ]

    matrix_live = REPORT_DIR / "checkpoint_matrix_live_2401_1425860746576504.md"
    if LIVE_SNAP_DIR.is_dir() and any(LIVE_SNAP_DIR.glob("*.json")):
        matrix_live.write_text(_live_session_matrix_report(), encoding="utf-8")
        index_lines.append(f"- [{matrix_live.name}]({matrix_live.name}) — Live 2401")
        print(f"Matrix A: {matrix_live}")
    else:
        index_lines.append("- Matrix A skipped")
        print("Matrix A skipped")

    index_lines.extend(["", "## Feel 样本（无 early 总格锁）", ""])
    for pick in FEEL_PICKS:
        out_path = REPORT_DIR / f"checkpoint_matrix_feel_{pick.slug}.md"
        out_path.write_text(_eval_session_matrix_report(pick=pick), encoding="utf-8")
        index_lines.append(f"- [{out_path.name}]({out_path.name}) — {pick.note}")
        print(f"Feel: {out_path}")

    index_lines.extend(["", "## 对照样本（有明确信息 / progression 异常）", ""])
    for pick in EXCLUDED_PICKS:
        out_path = REPORT_DIR / f"checkpoint_matrix_ref_{pick.slug}.md"
        out_path.write_text(_eval_session_matrix_report(pick=pick), encoding="utf-8")
        index_lines.append(f"- [{out_path.name}]({out_path.name}) — {pick.note}")
        print(f"Ref: {out_path}")

    MATRIX_INDEX.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(f"Index: {MATRIX_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
