"""A/B regression: AISHA early-round count window expand (2507 live blank fix).

Uses **real valid fatbeans captures only** — no synth, no mixed/quarantine, no npcap
synthetic replays. Sample pool = `sample_classification.json` → `clean` ∩
`fatbeans_valid_aisha_*` with organize `sample_class==valid` and ≥2 bid rounds.

Compares R1–R2 with live-style `audit_aisha_early_round` (fix ON vs rollback).
R3+ without early flag must be byte-identical (fix must not touch late rounds).

Usage:
  python scripts/regress_aisha_early_count_window.py
  python scripts/regress_aisha_early_count_window.py --post0625-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for sub in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_aisha_gap import (  # noqa: E402
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
)
from ahmad_ref_engine import (  # noqa: E402
    AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND,
    AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from hero_ref_live_schedule import EARLY_ROUND_ENGINE_FLAG, hero_max_combos_for_round  # noqa: E402
from organize_fatbeans_real_samples import sample_meta  # noqa: E402

FATBEANS_DIR = ROOT / "data" / "samples" / "fatbeans"
CLASSIFICATION = ROOT / "scripts" / "calibration" / "sample_classification.json"
ERA_MANIFEST = ROOT / "data" / "sample_manifests" / "era_partition_noon_2026-06-27.json"
MIN_BID_ROUNDS = 2
QUOTE_DELTA_PCT_WARN = 3.0

# Known abnormal / anchor-excluded real captures (progression lock, bad cohort) — not synth,
# but not suitable as regression guardrails for this patch.
HARDCODED_EXCLUDES = frozenset(
    {
        "fatbeans_valid_aisha_2403_4rounds_2403_1295018989226069_0052.json",  # early total_cells lock
        "fatbeans_valid_aisha_2403_3rounds_2403_1295019017648392_0051.json",  # progression outlier
    }
)


@dataclass
class RoundDiff:
    file: str
    map_id: int | None
    round_no: int
    status_off: str
    status_on: str
    balanced_off: int | None
    balanced_on: int | None
    combo_off: int
    combo_on: int
    expanded: bool
    center_note: str | None


@dataclass
class Report:
    pool_size: int = 0
    scanned_files: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    early_rounds: int = 0
    improvements: list[RoundDiff] = field(default_factory=list)
    regressions: list[RoundDiff] = field(default_factory=list)
    quote_shifts: list[RoundDiff] = field(default_factory=list)
    late_mismatches: list[RoundDiff] = field(default_factory=list)
    unchanged_early: int = 0


def _load_clean_aisha_names() -> list[str]:
    payload = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    return sorted(
        {
            str(row["sample"])
            for row in payload.get("clean", [])
            if str(row.get("sample", "")).startswith("fatbeans_valid_aisha_")
        }
    )


def _post0625_names(all_names: list[str]) -> set[str]:
    by_session = {n for n in all_names if ("142586" in n or "146922" in n)}
    if not ERA_MANIFEST.is_file():
        return by_session
    era = json.loads(ERA_MANIFEST.read_text(encoding="utf-8"))
    new_basenames = {
        Path(row["path"]).name
        for row in era.get("rows", [])
        if row.get("era") == "NEW"
    }
    return {n for n in all_names if n in new_basenames} | by_session


def _resolve_sample_paths(names: list[str], post0625_only: bool) -> list[Path]:
    post = _post0625_names(names)
    out: list[Path] = []
    for name in names:
        if post0625_only and name not in post:
            continue
        if name in HARDCODED_EXCLUDES:
            continue
        path = FATBEANS_DIR / name
        if path.is_file():
            out.append(path)
    return sorted(out)


def _skip(report: Report, reason: str) -> None:
    report.skipped[reason] = report.skipped.get(reason, 0) + 1


def _ok_result(res: dict[str, Any]) -> bool:
    status = str(res.get("status") or "")
    bal = res.get("balanced")
    if status in {"missing_total_count", "no_reachable_combo"}:
        return False
    try:
        return bal is not None and int(bal) > 0
    except (TypeError, ValueError):
        return False


def _run_engine(snapshot: dict[str, Any], *, round_no: int, rollback: bool) -> dict[str, Any]:
    if rollback:
        os.environ[AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND] = "1"
    else:
        os.environ.pop(AISHA_DISABLE_EARLY_COUNT_WINDOW_EXPAND, None)
    snap = dict(snapshot)
    prepared = prepare_reference_engine_snapshot(snap)
    budget = hero_max_combos_for_round("aisha", round_no)
    return run_reference_engine(prepared, max_combos=budget).as_dict()


def _center_from_notes(notes: list[str]) -> str | None:
    for note in notes:
        if note.startswith("total_count_prior_center:"):
            return note
    return None


def _classify_early_diff(diff: RoundDiff, res_off: dict[str, Any], res_on: dict[str, Any], report: Report) -> None:
    ok_off = _ok_result(res_off)
    ok_on = _ok_result(res_on)

    if not ok_off and ok_on:
        report.improvements.append(diff)
        return
    if ok_off and not ok_on:
        report.regressions.append(diff)
        return
    if ok_off and ok_on:
        try:
            b_off = int(diff.balanced_off or 0)
            b_on = int(diff.balanced_on or 0)
            if b_off > 0 and b_on != b_off:
                pct = abs(b_on - b_off) / b_off * 100.0
                if pct > QUOTE_DELTA_PCT_WARN or diff.expanded:
                    report.quote_shifts.append(diff)
                    return
        except (TypeError, ValueError):
            pass
    report.unchanged_early += 1


def _audit_file(path: Path, report: Report) -> None:
    name = path.name
    if not name.startswith("fatbeans_valid_aisha_"):
        _skip(report, "not_valid_prefix")
        return
    if "mixed" in name or "synth" in name.lower() or "invalid" in name:
        _skip(report, "synth_or_quarantine_name")
        return

    try:
        meta = sample_meta(path)
    except Exception:
        _skip(report, "meta_error")
        return
    if meta.sample_class != "valid":
        _skip(report, f"class:{meta.sample_class}")
        return
    if meta.hero != "aisha":
        _skip(report, "not_aisha")
        return
    if meta.constraint_conflict_windows:
        _skip(report, "constraint_conflict")
        return
    if meta.rounds < MIN_BID_ROUNDS:
        _skip(report, f"rounds<{MIN_BID_ROUNDS}")
        return

    try:
        events = parse_fatbeans_capture(path)
    except Exception:
        _skip(report, "parse_error")
        return

    map_id = _map_id_from_events(events)
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    if len(pre) < MIN_BID_ROUNDS:
        _skip(report, "live_rounds_short")
        return

    report.scanned_files += 1

    for round_no in range(1, min(3, len(pre) + 1)):
        prefix = pre[:round_no]
        sort_id = _prefix_sort_id(prefix)
        prefix_events = _events_through_sort(events, sort_id) if sort_id else events
        snapshot = _build_snapshot(
            hero="aisha",
            events=prefix_events,
            prefix_batches=prefix,
            map_id=map_id,
        )
        snapshot[EARLY_ROUND_ENGINE_FLAG] = True
        report.early_rounds += 1

        res_off = _run_engine(snapshot, round_no=round_no, rollback=True)
        res_on = _run_engine(snapshot, round_no=round_no, rollback=False)
        notes_on = [str(n) for n in res_on.get("notes") or []]
        expanded = any(n.startswith(AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE) for n in notes_on)
        diff = RoundDiff(
            file=name,
            map_id=map_id,
            round_no=round_no,
            status_off=str(res_off.get("status") or ""),
            status_on=str(res_on.get("status") or ""),
            balanced_off=res_off.get("balanced"),
            balanced_on=res_on.get("balanced"),
            combo_off=int(res_off.get("combo_count") or 0),
            combo_on=int(res_on.get("combo_count") or 0),
            expanded=expanded,
            center_note=_center_from_notes(notes_on),
        )
        _classify_early_diff(diff, res_off, res_on, report)

    for round_no in range(3, len(pre) + 1):
        prefix = pre[:round_no]
        sort_id = _prefix_sort_id(prefix)
        prefix_events = _events_through_sort(events, sort_id) if sort_id else events
        snapshot = _build_snapshot(
            hero="aisha",
            events=prefix_events,
            prefix_batches=prefix,
            map_id=map_id,
        )
        res_off = _run_engine(snapshot, round_no=round_no, rollback=True)
        res_on = _run_engine(snapshot, round_no=round_no, rollback=False)
        if (
            res_off.get("status") != res_on.get("status")
            or res_off.get("balanced") != res_on.get("balanced")
            or res_off.get("combo_count") != res_on.get("combo_count")
        ):
            report.late_mismatches.append(
                RoundDiff(
                    file=name,
                    map_id=map_id,
                    round_no=round_no,
                    status_off=str(res_off.get("status") or ""),
                    status_on=str(res_on.get("status") or ""),
                    balanced_off=res_off.get("balanced"),
                    balanced_on=res_on.get("balanced"),
                    combo_off=int(res_off.get("combo_count") or 0),
                    combo_on=int(res_on.get("combo_count") or 0),
                    expanded=False,
                    center_note=None,
                )
            )


def _print_rows(title: str, rows: list[RoundDiff], limit: int = 25) -> None:
    print(f"\n=== {title} ({len(rows)}) ===")
    for row in rows[:limit]:
        pct = ""
        if row.balanced_off and row.balanced_on:
            try:
                pct = f" dBal={100*(int(row.balanced_on)-int(row.balanced_off))/int(row.balanced_off):+.1f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                pct = ""
        print(
            f"  {row.file[:44]:44} map={row.map_id} R{row.round_no} "
            f"{row.status_off}/{row.balanced_off} -> {row.status_on}/{row.balanced_on}{pct} "
            f"combo {row.combo_off}->{row.combo_on} "
            f"{'EXPAND' if row.expanded else ''} {row.center_note or ''}"
        )
    if len(rows) > limit:
        print(f"  ... +{len(rows) - limit} more")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--post0625-only",
        action="store_true",
        help="restrict to NEW-era captures (era manifest or 142586/146922 session ids)",
    )
    args = parser.parse_args()

    clean_names = _load_clean_aisha_names()
    paths = _resolve_sample_paths(clean_names, args.post0625_only)
    report = Report(pool_size=len(paths))

    t0 = time.time()
    for idx, path in enumerate(paths, 1):
        _audit_file(path, report)
        if idx % 10 == 0:
            print(f"  progress {idx}/{len(paths)} scanned={report.scanned_files}", flush=True)

    elapsed = time.time() - t0
    print(
        f"pool={report.pool_size} scanned={report.scanned_files} "
        f"early_rounds={report.early_rounds} unchanged_early={report.unchanged_early} "
        f"elapsed={elapsed:.1f}s"
    )
    if report.skipped:
        print("skipped:", dict(sorted(report.skipped.items())))
    _print_rows("IMPROVEMENTS (rollback blank -> fix quotes)", report.improvements)
    _print_rows("REGRESSIONS (rollback OK -> fix broken)", report.regressions)
    _print_rows("QUOTE SHIFTS (>3% or window expanded)", report.quote_shifts, limit=40)
    _print_rows("LATE R3+ MISMATCH (must be 0)", report.late_mismatches)

    if report.regressions or report.late_mismatches:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
