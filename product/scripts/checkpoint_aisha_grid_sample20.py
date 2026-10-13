"""Checkpoint: sample 20 Aisha games from eval slices, grid error by round + matrix reports."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AHMAD_SRC = ROOT / "hero_ref" / "src"
for path in (SCRIPTS, AHMAD_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_ref_engine import (  # noqa: E402
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from audit_aisha_gap import _mid_gap, _triplet, _truth_in_range  # noqa: E402
from layout_depth_policy import _deepest_minimap_bottom_row, _known_minimap_cells  # noqa: E402
from minimap_audit_matrix import MATRIX_LEGEND, render_minimap_matrix  # noqa: E402

MANIFEST = ROOT / "data" / "fixtures" / "aisha_eval_slices" / "manifest.json"
REPORT_JSON = ROOT / "data" / "reports" / "checkpoint_aisha_grid_sample20.json"
REPORT_TXT = ROOT / "data" / "reports" / "checkpoint_aisha_grid_sample20.txt"
LIVE_SNAP_DIR = ROOT / "data" / "logs" / "live" / "round_snapshots"
MATRIX_LIVE = ROOT / "data" / "reports" / "checkpoint_matrix_live_2401_1425860746576504.md"
MATRIX_SAMPLE = ROOT / "data" / "reports" / "checkpoint_matrix_eval_deep_2403_fatbeans_129501901.md"
SAMPLE_COUNT = 20
MAX_COMBOS = 8_000


@dataclass
class RoundRow:
    source_file: str
    map_id: int | None
    source_kind: str
    eval_round: int
    truth_cells: int
    grid_low: int | None
    grid_mid: int | None
    grid_high: int | None
    raw_deepest: int | None
    known_cells: int
    cells_hit: bool
    mid_gap: int
    low_gap: int
    high_gap: int
    status: str


def _minimap_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    ui = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    minimap = ui.get("minimap") if isinstance(ui.get("minimap"), dict) else {}
    items = minimap.get("items")
    if isinstance(items, list) and items:
        return list(items)
    root_items = snapshot.get("minimap_grid_items")
    return list(root_items) if isinstance(root_items, list) else []


def _evaluate_payload(payload: dict[str, Any]) -> RoundRow:
    truth_cells = int(payload["truth"]["total_cells"])
    snapshot = dict(payload["snapshot"])
    if payload.get("eval_round") is not None:
        snapshot["round"] = payload["eval_round"]
    prepared = prepare_reference_engine_snapshot(snapshot)
    result = run_reference_engine(prepared, max_combos=MAX_COMBOS).as_dict()
    lo, mid, hi = _triplet(result, "total_grid_range")
    items = _minimap_items(prepared)
    return RoundRow(
        source_file=str(payload.get("source_file") or ""),
        map_id=payload.get("map_id"),
        source_kind=str(payload.get("source_kind") or ""),
        eval_round=int(payload.get("eval_round") or 0),
        truth_cells=truth_cells,
        grid_low=lo,
        grid_mid=mid,
        grid_high=hi,
        raw_deepest=_deepest_minimap_bottom_row(items),
        known_cells=_known_minimap_cells(items),
        cells_hit=bool(_truth_in_range(truth_cells, lo, mid, hi)),
        mid_gap=int(_mid_gap(truth_cells, lo, mid, hi) or 0),
        low_gap=int(truth_cells - (lo or truth_cells)),
        high_gap=int((hi or truth_cells) - truth_cells),
        status=str(result.get("status") or ""),
    )


def _select_twenty_games(entries: list[dict[str, Any]]) -> list[str]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        src = str(entry.get("source_file") or "")
        if src:
            by_source[src].append(entry)
    sources = sorted(by_source.keys())
    if len(sources) <= SAMPLE_COUNT:
        return sources
    # Stable spread: hash-sort for reproducible diverse pick across maps/kinds.
    def sort_key(name: str) -> str:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return digest

    ranked = sorted(sources, key=sort_key)
    step = len(ranked) / SAMPLE_COUNT
    picked: list[str] = []
    for index in range(SAMPLE_COUNT):
        picked.append(ranked[int(index * step)])
    return picked


def _round_stats(rows: list[RoundRow]) -> dict[str, Any]:
    by_round: dict[int, list[RoundRow]] = defaultdict(list)
    for row in rows:
        by_round[row.eval_round].append(row)
    out: dict[str, Any] = {}
    for rnd in sorted(by_round):
        bucket = by_round[rnd]
        out[f"R{rnd}"] = {
            "n": len(bucket),
            "hit_rate": sum(1 for r in bucket if r.cells_hit) / len(bucket),
            "avg_abs_mid_gap": statistics.mean(abs(r.mid_gap) for r in bucket),
            "avg_mid_gap": statistics.mean(r.mid_gap for r in bucket),
            "avg_low_gap": statistics.mean(r.low_gap for r in bucket),
            "avg_high_gap": statistics.mean(r.high_gap for r in bucket),
        }
    return out


def _format_matrix_report(
    *,
    title: str,
    session_label: str,
    rounds: list[tuple[str, dict[str, Any], str, RoundRow | None, list[str]]],
) -> str:
    lines = [
        f"# {title}",
        "",
        f"会话/样本: `{session_label}`",
        "",
        f"坐标 **1 基** row/col；符号: {MATRIX_LEGEND}",
        "",
    ]
    for label, snap, matrix, row, extras in rounds:
        lines.append(f"## {label}")
        for extra in extras:
            lines.append(f"- {extra}")
        if row is not None:
            lines.append(
                f"- truth={row.truth_cells}  估总格 **{row.grid_low}/{row.grid_mid}/{row.grid_high}**"
                f"  raw={row.raw_deepest} known={row.known_cells}"
                f"  mid_gap={row.mid_gap:+d}  hit={'Y' if row.cells_hit else 'N'}"
                f"  status={row.status}"
            )
        lines.append("")
        lines.append("```")
        lines.append(matrix.rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _live_session_matrix_report() -> str:
    paths = sorted(LIVE_SNAP_DIR.glob("*.json"))
    settled_path = LIVE_SNAP_DIR / "r03_settled.json"
    session_truth = 74
    if settled_path.is_file():
        settled_payload = json.loads(settled_path.read_text(encoding="utf-8"))
        session_truth = int(settled_payload.get("inventory_cells") or session_truth)
    rounds: list[tuple[str, dict[str, Any], str, RoundRow | None]] = []
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
        rounds.append((label, snap, matrix, row, []))
    return _format_matrix_report(
        title="Checkpoint 矩阵 A · Live 2401 正样本",
        session_label=session_id,
        rounds=rounds,
    )


def _eval_session_matrix_report(source_file: str, entries: list[dict[str, Any]]) -> str:
    rounds: list[tuple[str, dict[str, Any], str, RoundRow | None]] = []
    for entry in sorted(entries, key=lambda item: int(item.get("eval_round") or 0)):
        path = ROOT / entry["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        truth_cells = int(payload["truth"]["total_cells"])
        row = _evaluate_payload(payload)
        snap = payload["snapshot"]
        items = _minimap_items(snap)
        matrix, _, _ = render_minimap_matrix(items)
        label = f"R{payload.get('eval_round')} truth={truth_cells} ({path.name})"
        rounds.append((label, snap, matrix, row, []))
    map_id = entries[0].get("map_id")
    return _format_matrix_report(
        title=f"Checkpoint 矩阵 B · Eval {source_file}",
        session_label=f"map={map_id} · {source_file}",
        rounds=rounds,
    )


def main() -> int:
    t0 = time.perf_counter()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = list(manifest.get("slices") or [])
    picked_sources = _select_twenty_games(entries)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        src = str(entry.get("source_file") or "")
        if src in picked_sources:
            by_source[src].append(entry)

    all_rows: list[RoundRow] = []
    game_summaries: list[dict[str, Any]] = []
    for source_file in picked_sources:
        game_rows: list[RoundRow] = []
        for entry in sorted(by_source[source_file], key=lambda item: int(item.get("eval_round") or 0)):
            path = ROOT / entry["path"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            row = _evaluate_payload(payload)
            game_rows.append(row)
            all_rows.append(row)
        truth = game_rows[-1].truth_cells if game_rows else None
        game_summaries.append(
            {
                "source_file": source_file,
                "map_id": game_rows[0].map_id if game_rows else None,
                "source_kind": game_rows[0].source_kind if game_rows else "",
                "rounds": len(game_rows),
                "truth_cells": truth,
                "final_mid_gap": game_rows[-1].mid_gap if game_rows else None,
                "any_hit": any(r.cells_hit for r in game_rows),
                "rounds_detail": [
                    {
                        "round": r.eval_round,
                        "truth": r.truth_cells,
                        "grid": [r.grid_low, r.grid_mid, r.grid_high],
                        "raw": r.raw_deepest,
                        "known": r.known_cells,
                        "mid_gap": r.mid_gap,
                        "hit": r.cells_hit,
                    }
                    for r in game_rows
                ],
            }
        )

    round_stats = _round_stats(all_rows)
    elapsed = time.perf_counter() - t0

    report = {
        "schema_version": 1,
        "checkpoint": "2026-06-15_aisha_grid_depth_v1",
        "sample_games": SAMPLE_COUNT,
        "slice_rows": len(all_rows),
        "elapsed_sec": round(elapsed, 1),
        "overall": {
            "hit_rate": sum(1 for r in all_rows if r.cells_hit) / max(1, len(all_rows)),
            "avg_abs_mid_gap": statistics.mean(abs(r.mid_gap) for r in all_rows),
        },
        "by_round": round_stats,
        "games": game_summaries,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    txt_lines = [
        "Aisha grid checkpoint — 20-game sample from eval slices",
        f"elapsed={elapsed:.1f}s rows={len(all_rows)}",
        f"overall hit={report['overall']['hit_rate']*100:.1f}% avg|mid_gap|={report['overall']['avg_abs_mid_gap']:.2f}",
        "",
    ]
    for label, stats in round_stats.items():
        txt_lines.append(
            f"{label}: n={stats['n']} hit={stats['hit_rate']*100:.1f}% "
            f"avg_mid_gap={stats['avg_mid_gap']:+.1f} avg_low_gap={stats['avg_low_gap']:+.1f} "
            f"avg_high_gap={stats['avg_high_gap']:+.1f}"
        )
    txt_lines.extend(["", "Per-game final round mid_gap:"])
    for game in game_summaries:
        txt_lines.append(
            f"  map={game['map_id']} {game['source_file'][:48]} "
            f"truth={game['truth_cells']} final_mid_gap={game['final_mid_gap']:+d} "
            f"any_hit={game['any_hit']}"
        )
    REPORT_TXT.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    # Matrix B: prefer windivert 2403 deep session (full manifest), else 2403 in sample, else highest truth.
    all_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        src = str(entry.get("source_file") or "")
        if src:
            all_by_source[src].append(entry)

    def _matrix_b_candidates() -> list[str]:
        out: list[str] = []
        for src in sorted(all_by_source):
            if "2403" not in src:
                continue
            if src.startswith("fatbeans_valid_aisha_2403"):
                out.insert(0, src)
            elif "windivert" in src.lower():
                out.append(src)
            else:
                out.append(src)
        return out

    matrix_b_source = next(iter(_matrix_b_candidates()), None)
    if matrix_b_source is None:
        matrix_b_source = max(
            picked_sources,
            key=lambda name: max(
                int((json.loads((ROOT / e["path"]).read_text(encoding="utf-8"))["truth"]["total_cells"]))
                for e in by_source[name]
            ),
        )
    matrix_b_entries = all_by_source.get(matrix_b_source) or by_source[matrix_b_source]
    MATRIX_SAMPLE.write_text(
        _eval_session_matrix_report(matrix_b_source, matrix_b_entries),
        encoding="utf-8",
    )
    if LIVE_SNAP_DIR.is_dir() and any(LIVE_SNAP_DIR.glob("*.json")):
        MATRIX_LIVE.write_text(_live_session_matrix_report(), encoding="utf-8")

    print(REPORT_TXT.read_text(encoding="utf-8"))
    print(f"\nJSON: {REPORT_JSON}")
    print(f"Matrix A: {MATRIX_LIVE}")
    print(f"Matrix B: {MATRIX_SAMPLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
