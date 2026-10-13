"""Reproduce cross-match settlement stickiness from HeroRefDiag exports.

Reads ``latest_snapshot.json`` + ``capture_source_status.json`` from one or more
export folders and reports whether the pre-fix UI would stick on the prior game's
settled snapshot vs the post-fix session-ahead standby path.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from ahmad_live_panel_server import capture_session_ahead_of_snapshot, summarize_snapshot  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit_export(export_dir: Path, *, refresh_capture_ts: bool) -> dict:
    work_dir = export_dir
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if refresh_capture_ts:
        temp_dir = tempfile.TemporaryDirectory(prefix="repro_cross_match_")
        work_dir = Path(temp_dir.name)
        for name in ("latest_snapshot.json", "capture_source_status.json", "hero_ref_current_summary.json"):
            src = export_dir / name
            if src.is_file():
                shutil.copy2(src, work_dir / name)
        capture_path = work_dir / "capture_source_status.json"
        capture = _load(capture_path)
        capture["ts"] = time.time()
        capture_path.write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")

    snapshot_path = work_dir / "latest_snapshot.json"
    capture_path = work_dir / "capture_source_status.json"
    summary_path = work_dir / "hero_ref_current_summary.json"
    missing = [name for name, p in (
        ("latest_snapshot.json", snapshot_path),
        ("capture_source_status.json", capture_path),
    ) if not p.is_file()]
    if missing:
        return {"export_dir": str(export_dir), "error": f"missing: {', '.join(missing)}"}

    snapshot = _load(snapshot_path)
    capture = _load(capture_path)
    capture_age_seconds = None
    try:
        capture_age_seconds = max(0.0, time.time() - float(capture.get("ts")))
    except (TypeError, ValueError):
        pass
    context = (
        snapshot.get("ui_contract", {}).get("context")
        if isinstance(snapshot.get("ui_contract"), dict)
        else {}
    )
    if not isinstance(context, dict):
        context = {}
    snapshot_session = str(context.get("session_id") or snapshot.get("session_id") or "")
    capture_session = str(capture.get("active_session_id") or "")
    ahead = capture_session_ahead_of_snapshot(
        capture,
        snapshot_session_id=snapshot_session,
    )
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    ui_summary = _load(summary_path) if summary_path.is_file() else {}
    ui_phase = (
        ui_summary.get("context", {}).get("phase")
        if isinstance(ui_summary.get("context"), dict)
        else None
    )
    ui_session = (
        ui_summary.get("context", {}).get("session_id")
        if isinstance(ui_summary.get("context"), dict)
        else None
    )
    bug_visible = (
        ahead
        and ui_summary.get("status") == "ok"
        and ui_phase == "settled"
        and ui_session == snapshot_session
    )
    result = {
        "export_dir": str(export_dir),
        "work_dir": str(work_dir),
        "snapshot_session": snapshot_session,
        "capture_session": capture_session,
        "snapshot_phase": context.get("phase") or snapshot.get("phase"),
        "capture_age_seconds_at_read": capture_age_seconds,
        "capture_session_ahead": ahead,
        "summarize_status": summary.get("status"),
        "summarize_reason": (summary.get("stale") or {}).get("reason"),
        "exported_ui_status": ui_summary.get("status"),
        "exported_ui_phase": ui_phase,
        "exported_ui_session": ui_session,
        "pre_fix_would_stick_settlement": bug_visible,
        "post_fix_summarize_standby": summary.get("status") == "stale_snapshot",
    }
    if temp_dir is not None:
        temp_dir.cleanup()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "exports",
        nargs="+",
        type=Path,
        help="HeroRefDiag export directories",
    )
    parser.add_argument(
        "--refresh-capture-ts",
        action="store_true",
        help="Set capture_source_status.ts=now to simulate the live bug moment",
    )
    args = parser.parse_args(argv)
    rows = [
        _audit_export(path.resolve(), refresh_capture_ts=args.refresh_capture_ts)
        for path in args.exports
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if not args.refresh_capture_ts:
        print(
            "\nNote: exports are usually stale (>20s). Re-run with --refresh-capture-ts "
            "to simulate the live cross-match window.",
            file=sys.stderr,
        )
        return 0
    ok = all(
        row.get("capture_session_ahead")
        and row.get("post_fix_summarize_standby")
        for row in rows
        if "error" not in row
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
