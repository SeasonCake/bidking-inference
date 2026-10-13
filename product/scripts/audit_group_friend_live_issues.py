# -*- coding: utf-8 -*-
"""Audit R1 hero/ref timing and settlement completeness on group-friend live captures.

Read-only. Writes JSON summary to stdout or --out path. No code fixes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "scripts", "hero_ref/src", "hero_ref/tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    hero_mode_from_state,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    build_monitor_artifact_from_events,
    load_monitor_tables,
)
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402


def _trunc(ev: FatbeansCaptureEvents, cut: int, *, inclusive: bool = False) -> FatbeansCaptureEvents:
    if inclusive:
        pred = lambda sid: int(sid) <= cut
    else:
        pred = lambda sid: int(sid) < cut
    k = lambda seq: tuple(r for r in seq if pred(int(r.sort_id)))
    return FatbeansCaptureEvents(
        packets=k(ev.packets),
        frames=k(ev.frames),
        sends=k(ev.sends),
        states=k(ev.states),
        statuses=k(ev.statuses),
    )


def _count_msg(ev: FatbeansCaptureEvents, msg_hex: str) -> int:
    target = int(msg_hex, 16)
    n = 0
    for fr in ev.frames:
        mid = getattr(fr, "message_id", None)
        if mid is not None and int(mid) == target:
            n += 1
    return n


def audit_archive(path: Path, tables) -> dict:
    ev = parse_fatbeans_capture(path)
    bids = sorted(
        (s for s in ev.sends if getattr(s, "kind", "") == "bid"),
        key=lambda s: int(s.sort_id),
    )
    skill_states = [
        st
        for st in ev.states
        if any(int(getattr(r, "skill_id", 0) or 0) == 100204 for r in (st.skill_reveals or ()))
    ]
    first_bid = int(bids[0].sort_id) if bids else None
    first_skill = int(skill_states[0].sort_id) if skill_states else None
    first_infer = next(
        (int(st.sort_id) for st in ev.states if hero_mode_from_state(st)),
        None,
    )

    r1_hero = r1_ref = None
    if first_bid is not None:
        art = build_monitor_artifact_from_events(
            _trunc(ev, first_bid),
            file=path.name,
            tables=tables,
            n_trials=1,
            roi_trials=0,
            run_debug_shadows=False,
        )
        r1_hero = art.get("hero")
        if r1_hero:
            ref = run_reference_engine(prepare_reference_engine_snapshot(art)).as_dict()
            r1_ref = ref.get("balanced")

    # After skill inclusive (simulates next snapshot after skill arrives)
    post_skill_hero = post_skill_round = post_skill_ref = None
    if first_skill is not None:
        art2 = build_monitor_artifact_from_events(
            _trunc(ev, first_skill, inclusive=True),
            file=path.name,
            tables=tables,
            n_trials=1,
            roi_trials=0,
            run_debug_shadows=False,
        )
        post_skill_hero = art2.get("hero")
        post_skill_round = art2.get("round")
        if post_skill_hero:
            ref2 = run_reference_engine(prepare_reference_engine_snapshot(art2)).as_dict()
            post_skill_ref = ref2.get("balanced")

    art_full = build_monitor_artifact_from_events(
        ev,
        file=path.name,
        tables=tables,
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )
    tic = (art_full.get("inference_input_constraints") or {}).get("total_item_count") or {}
    detail = art_full.get("settlement_incomplete_detail") or {}

    session = None
    for st in ev.states:
        if getattr(st, "session_id", None):
            session = st.session_id
            break
    if not session:
        for fr in ev.frames:
            if getattr(fr, "session_id", None):
                session = fr.session_id
                break

    parts = path.stem.split("_")
    map_hint = parts[-2] if len(parts) >= 2 else "?"

    skill_late = (
        first_bid is not None
        and first_skill is not None
        and first_skill > first_bid
    )

    return {
        "file": path.name,
        "session_id": session,
        "map_id": art_full.get("map_id") or (int(map_hint) if map_hint.isdigit() else map_hint),
        "first_bid_sort_id": first_bid,
        "first_skill_100204_sort_id": first_skill,
        "first_hero_infer_sort_id": first_infer,
        "skill_after_first_bid": skill_late,
        "r1_at_first_bid": {"hero": r1_hero, "balanced": r1_ref},
        "after_skill_inclusive": {
            "hero": post_skill_hero,
            "round": post_skill_round,
            "balanced": post_skill_ref,
        },
        "full": {
            "round": art_full.get("round"),
            "inventory_count": art_full.get("inventory_count"),
            "known_value_sum": art_full.get("known_value_sum"),
            "settlement_incomplete": art_full.get("settlement_incomplete"),
            "declared_items": tic.get("value"),
            "captured_items": detail.get("captured_items"),
            "captured_value": detail.get("captured_value"),
        },
        "frame_counts": {
            "0x002D": _count_msg(ev, "0x2d"),
            "0x0025": _count_msg(ev, "0x25"),
            "0x0022": _count_msg(ev, "0x22"),
            "total_frames": len(ev.frames),
        },
    }


def audit_ui_summary(path: Path) -> dict:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    by_session: dict[str, list] = {}
    for r in rows:
        ctx = r.get("context") or {}
        sid = ctx.get("session_id")
        if not sid:
            continue
        by_session.setdefault(sid, []).append(r)

    session_summaries = []
    for sid, sess_rows in by_session.items():
        rounds_seen = set()
        has_r1_bidding = False
        has_r1_reading = False
        first_in_game = None
        for r in sorted(sess_rows, key=lambda x: x.get("logged_at") or 0):
            ctx = r.get("context") or {}
            phase = ctx.get("phase")
            rnd = ctx.get("round")
            if phase in ("reading", "bidding", "settled") and rnd:
                rounds_seen.add((rnd, phase))
                if rnd == 1 and phase == "bidding":
                    has_r1_bidding = True
                if rnd == 1 and phase == "reading":
                    has_r1_reading = True
                if first_in_game is None and phase in ("reading", "bidding"):
                    ref = r.get("reference") or {}
                    first_in_game = {
                        "round": rnd,
                        "phase": phase,
                        "hero": ctx.get("hero"),
                        "readiness": ref.get("readiness"),
                        "balanced": ref.get("balanced"),
                        "time": r.get("updated_at_text"),
                    }
        session_summaries.append(
            {
                "session_id": sid,
                "map_id": (sess_rows[-1].get("context") or {}).get("map_id"),
                "has_r1_bidding_ui": has_r1_bidding,
                "has_r1_reading_ui": has_r1_reading,
                "first_in_game_ui": first_in_game,
                "rounds_seen": sorted(f"{r}/{p}" for r, p in rounds_seen),
            }
        )

    missing_r1_bidding = [
        s for s in session_summaries if s["first_in_game_ui"] and not s["has_r1_bidding_ui"]
    ]
    return {
        "total_ui_rows": len(rows),
        "sessions_with_game": len(session_summaries),
        "sessions_missing_r1_bidding": missing_r1_bidding,
        "lobby_not_structured": sum(
            1
            for r in rows
            if (r.get("context") or {}).get("phase") in (None, "unknown")
            and (r.get("reference") or {}).get("readiness") == "not_structured_hero"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root",
        default=r"C:\Users\example\Desktop\data (4)\data\logs\live",
    )
    parser.add_argument("--out", help="Write JSON report here")
    args = parser.parse_args()

    data = Path(args.data_root)
    reset_dir = data / "raw" / "archive" / "reset"
    tables = load_monitor_tables()

    archives = sorted(reset_dir.glob("*.json"))
    archive_rows = [audit_archive(p, tables) for p in archives]

    late = [r for r in archive_rows if r["skill_after_first_bid"]]
    r1_ok = [r for r in archive_rows if r["r1_at_first_bid"]["hero"] and r["r1_at_first_bid"]["balanced"]]
    incomplete = [r for r in archive_rows if r["full"]["settlement_incomplete"]]

    ui = {}
    ui_path = data / "hero_ref_ui_summary.jsonl"
    if ui_path.is_file():
        ui = audit_ui_summary(ui_path)

    report = {
        "data_root": str(data),
        "archive_count": len(archive_rows),
        "r1_skill_timing": {
            "skill_after_first_bid": len(late),
            "r1_hero_ref_at_first_bid": len(r1_ok),
            "late_sessions": [
                {
                    "session_id": r["session_id"],
                    "map_id": r["map_id"],
                    "first_bid": r["first_bid_sort_id"],
                    "first_skill": r["first_skill_100204_sort_id"],
                    "r1_hero": r["r1_at_first_bid"]["hero"],
                    "after_skill_round": r["after_skill_inclusive"]["round"],
                    "after_skill_ref": r["after_skill_inclusive"]["balanced"],
                }
                for r in late
            ],
        },
        "settlement": {
            "incomplete_count": len(incomplete),
            "incomplete_sessions": [
                {
                    "session_id": r["session_id"],
                    "map_id": r["map_id"],
                    "declared_items": r["full"]["declared_items"],
                    "captured_items": r["full"]["captured_items"],
                    "captured_value": r["full"]["captured_value"],
                    "inventory_count": r["full"]["inventory_count"],
                    "0x002D_frames": r["frame_counts"]["0x002D"],
                }
                for r in incomplete
            ],
        },
        "archives": archive_rows,
        "ui_summary": ui,
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
