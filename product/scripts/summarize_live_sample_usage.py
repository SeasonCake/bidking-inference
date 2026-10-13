"""Summarize live raw sessions for hero/tool recommendation priors.

The output is intentionally descriptive, not causal: an action row means the
tool was used in sessions with the reported settlement outcomes. It does not
prove the tool caused the outcome.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402

HERO_SLUG_BY_ID: dict[int, str] = {
    101: "fatima",
    102: "chenmei",
    103: "aisha",
    104: "gabriela",
    105: "tatiana",
    106: "naomi",
    107: "sophie",
    108: "maria",
    109: "helena",
    110: "isabella",
    201: "george",
    202: "carlos",
    203: "leonard",
    204: "ahmed",
    205: "ivan",
    206: "takeda",
    207: "wuqilin",
    208: "ethan",
    209: "victor",
    301: "raven",
}


@dataclass(frozen=True)
class SessionUsage:
    path: str
    session_id: str
    map_id: int | None
    local_player_id: int | None
    local_hero: str | None
    opponent_heroes: tuple[str, ...]
    local_action_ids: tuple[int, ...]
    settled: bool
    final_value: int | None
    final_cells: int | None
    final_item_count: int | None
    local_final_bid: int | None
    highest_bid: int | None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _median(values: Iterable[int | float | None], *, digits: int = 1) -> int | float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    value = statistics.median(clean)
    if value.is_integer():
        return int(value)
    return round(value, digits)


def _mean(values: Iterable[int | float | bool | None], *, digits: int = 3) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(statistics.mean(clean), digits)


def _latest_session_id(events: FatbeansCaptureEvents) -> str:
    for state in reversed(events.states):
        if state.session_id:
            return str(state.session_id)
    for send in reversed(events.sends):
        if send.session_id:
            return str(send.session_id)
    return ""


def _latest_map_id(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.map_id is not None:
            return int(state.map_id)
    session_id = _latest_session_id(events)
    if ":" in session_id:
        return _safe_int(session_id.split(":", 1)[0])
    return None


def _latest_local_player_id(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.player_id is not None:
            return int(state.player_id)
    return None


def _latest_bid_by_player(events: FatbeansCaptureEvents) -> dict[int, int]:
    latest: dict[int, int] = {}
    for state in events.states:
        for bid in state.bids:
            if bid.current_value is not None:
                latest[int(bid.player_id)] = int(bid.current_value)
    return latest


def _latest_hero_by_player(events: FatbeansCaptureEvents) -> dict[int, str]:
    latest: dict[int, str] = {}
    for state in events.states:
        for bid in state.bids:
            if bid.hero_id is None:
                continue
            hero = HERO_SLUG_BY_ID.get(int(bid.hero_id))
            if hero:
                latest[int(bid.player_id)] = hero
    return latest


def _inventory_totals(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Any],
) -> tuple[int | None, int | None, int | None]:
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        final_value = 0
        for inv_item in state.inventory_items:
            item = items.get(int(inv_item.item_id))
            if item is not None:
                final_value += int(getattr(item, "value", 0) or 0)
        return (
            final_value,
            sum(int(item.cells) for item in state.inventory_items),
            len(state.inventory_items),
        )
    return None, None, None


def session_usage_from_events(
    path: Path,
    events: FatbeansCaptureEvents,
    *,
    items: Mapping[int, Any],
) -> SessionUsage:
    local_player_id = _latest_local_player_id(events)
    hero_by_player = _latest_hero_by_player(events)
    bid_by_player = _latest_bid_by_player(events)
    final_value, final_cells, final_item_count = _inventory_totals(events, items)
    opponent_heroes = {
        hero
        for player_id, hero in hero_by_player.items()
        if local_player_id is not None and player_id != local_player_id
    }
    local_action_ids = tuple(
        int(send.value)
        for send in events.sends
        if send.kind == "action" and send.value is not None
    )
    return SessionUsage(
        path=str(path),
        session_id=_latest_session_id(events),
        map_id=_latest_map_id(events),
        local_player_id=local_player_id,
        local_hero=(
            hero_by_player.get(local_player_id)
            if local_player_id is not None
            else None
        ),
        opponent_heroes=tuple(sorted(opponent_heroes)),
        local_action_ids=local_action_ids,
        settled=any(state.message_id == 0x002D or state.inventory_items for state in events.states),
        final_value=final_value,
        final_cells=final_cells,
        final_item_count=final_item_count,
        local_final_bid=(
            bid_by_player.get(local_player_id)
            if local_player_id is not None
            else None
        ),
        highest_bid=max(bid_by_player.values()) if bid_by_player else None,
    )


def _counter_rows(
    counter: Counter[Any],
    *,
    labels: Mapping[Any, str] | None = None,
    total: int | None = None,
    top: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    denom = total if total is not None else sum(counter.values())
    for key, count in counter.most_common(top):
        row: dict[str, Any] = {"key": key, "count": count}
        if labels and key in labels:
            row["label"] = labels[key]
        if denom:
            row["share"] = round(count / denom, 4)
        rows.append(row)
    return rows


def _action_labels(action_names: Mapping[int, str]) -> dict[int, str]:
    return {int(action_id): name for action_id, name in action_names.items() if name}


def _outcome_row(
    key: Any,
    sessions: Sequence[SessionUsage],
    *,
    label: str | None = None,
) -> dict[str, Any]:
    settled = [session for session in sessions if session.settled]
    final_values = [session.final_value for session in settled]
    final_cells = [session.final_cells for session in settled]
    local_bids = [session.local_final_bid for session in sessions]
    highest_bids = [session.highest_bid for session in sessions]

    local_bid_to_value = [
        session.local_final_bid / session.final_value
        for session in settled
        if session.local_final_bid is not None
        and session.final_value is not None
        and session.final_value > 0
    ]
    highest_bid_to_value = [
        session.highest_bid / session.final_value
        for session in settled
        if session.highest_bid is not None
        and session.final_value is not None
        and session.final_value > 0
    ]
    local_over_value = [
        1.0 if session.local_final_bid > session.final_value else 0.0
        for session in settled
        if session.local_final_bid is not None and session.final_value is not None
    ]

    row: dict[str, Any] = {
        "key": key,
        "sessions": len(sessions),
        "settled_sessions": len(settled),
        "median_final_value": _median(final_values),
        "median_final_cells": _median(final_cells),
        "median_local_final_bid": _median(local_bids),
        "median_highest_bid": _median(highest_bids),
        "median_local_bid_to_value": _median(local_bid_to_value, digits=3),
        "median_highest_bid_to_value": _median(highest_bid_to_value, digits=3),
        "local_over_value_rate": _mean(local_over_value, digits=3),
    }
    if label:
        row["label"] = label
    return row


def _group_outcomes(
    sessions: Sequence[SessionUsage],
    keyfunc: Any,
    *,
    labels: Mapping[Any, str] | None = None,
    min_sessions: int,
    top: int,
) -> list[dict[str, Any]]:
    groups: dict[Any, list[SessionUsage]] = defaultdict(list)
    for session in sessions:
        keys = keyfunc(session)
        if keys is None:
            continue
        if isinstance(keys, (str, int)):
            keys = (keys,)
        for key in keys:
            if key is None:
                continue
            groups[key].append(session)
    rows = [
        _outcome_row(
            key,
            group,
            label=labels.get(key) if labels else None,
        )
        for key, group in groups.items()
        if len(group) >= min_sessions
    ]
    rows.sort(
        key=lambda row: (
            int(row["sessions"]),
            int(row["settled_sessions"]),
            int(row.get("median_final_value") or 0),
        ),
        reverse=True,
    )
    return rows[:top]


def _recommended_loadouts(
    sessions: Sequence[SessionUsage],
    *,
    action_names: Mapping[int, str],
    min_sessions: int,
) -> dict[str, list[dict[str, Any]]]:
    global_actions: Counter[int] = Counter()
    per_hero: dict[str, Counter[int]] = defaultdict(Counter)
    hero_sessions: Counter[str] = Counter()
    for session in sessions:
        if not session.local_hero:
            continue
        hero_sessions[session.local_hero] += 1
        unique_actions = set(session.local_action_ids)
        global_actions.update(unique_actions)
        per_hero[session.local_hero].update(unique_actions)

    labels = _action_labels(action_names)
    out: dict[str, list[dict[str, Any]]] = {}
    for hero, counter in sorted(per_hero.items()):
        if hero_sessions[hero] < min_sessions:
            continue
        chosen: list[int] = []
        for action_id, _count in counter.most_common():
            if action_id not in chosen:
                chosen.append(action_id)
            if len(chosen) >= 5:
                break
        for action_id, _count in global_actions.most_common():
            if action_id not in chosen:
                chosen.append(action_id)
            if len(chosen) >= 5:
                break
        out[hero] = [
            {
                "action_id": action_id,
                "action": labels.get(action_id, ""),
                "hero_sessions": counter.get(action_id, 0),
                "global_sessions": global_actions.get(action_id, 0),
                "hero_share": (
                    round(counter.get(action_id, 0) / hero_sessions[hero], 4)
                    if hero_sessions[hero]
                    else None
                ),
            }
            for action_id in chosen[:5]
        ]
    return out


def summarize_usage(
    sessions: Sequence[SessionUsage],
    *,
    action_names: Mapping[int, str],
    top: int = 20,
    min_group_sessions: int = 1,
) -> dict[str, Any]:
    local_heroes = Counter(
        session.local_hero for session in sessions if session.local_hero
    )
    opponent_heroes = Counter(
        hero for session in sessions for hero in set(session.opponent_heroes)
    )
    maps = Counter(session.map_id for session in sessions if session.map_id is not None)
    action_send_counts = Counter(
        action_id for session in sessions for action_id in session.local_action_ids
    )
    action_session_counts = Counter(
        action_id for session in sessions for action_id in set(session.local_action_ids)
    )
    action_labels = _action_labels(action_names)

    return {
        "schema": "live_sample_usage.v1",
        "notes": [
            "Action outcome groups are descriptive and non-causal.",
            "Opponent tool usage is not inferred unless packets expose stable player attribution.",
        ],
        "sessions": len(sessions),
        "settled_sessions": sum(1 for session in sessions if session.settled),
        "sessions_with_local_player": sum(
            1 for session in sessions if session.local_player_id is not None
        ),
        "sessions_with_local_hero": sum(
            1 for session in sessions if session.local_hero
        ),
        "local_heroes": _counter_rows(local_heroes, total=len(sessions), top=top),
        "opponent_heroes": _counter_rows(opponent_heroes, total=len(sessions), top=top),
        "maps": _counter_rows(maps, total=len(sessions), top=top),
        "local_action_sends": _counter_rows(
            action_send_counts,
            labels=action_labels,
            top=top,
        ),
        "local_action_sessions": _counter_rows(
            action_session_counts,
            labels=action_labels,
            total=len(sessions),
            top=top,
        ),
        "outcome_by_local_hero": _group_outcomes(
            sessions,
            lambda session: session.local_hero,
            min_sessions=min_group_sessions,
            top=top,
        ),
        "outcome_by_local_action": _group_outcomes(
            sessions,
            lambda session: set(session.local_action_ids),
            labels=action_labels,
            min_sessions=min_group_sessions,
            top=top,
        ),
        "recommended_loadout_candidates": _recommended_loadouts(
            sessions,
            action_names=action_names,
            min_sessions=min_group_sessions,
        ),
    }


def _discover_paths(roots: Sequence[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
            continue
        if not root.exists():
            continue
        paths.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
        )
    return sorted(set(paths))


def _load_action_names(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    names: dict[int, str] = {}
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, Mapping):
                continue
            action_id = _safe_int(row.get("battle_item_id") or row.get("id"))
            name = str(row.get("name") or "")
            if action_id is not None and name:
                names[action_id] = name
    return names


def load_session_usages(
    paths: Sequence[Path],
    *,
    items: Mapping[int, Any],
) -> tuple[list[SessionUsage], dict[str, Any]]:
    sessions: list[SessionUsage] = []
    errors: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    duplicates = 0
    parsed = 0
    for path in paths:
        try:
            events = parse_fatbeans_capture(path)
            parsed += 1
            session = session_usage_from_events(path, events, items=items)
        except Exception as exc:  # noqa: BLE001 - batch CLI should continue
            errors.append({"path": str(path), "error": str(exc)})
            continue
        dedupe_key = f"session:{session.session_id}" if session.session_id else f"path:{path}"
        if dedupe_key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(dedupe_key)
        sessions.append(session)
    diagnostics = {
        "paths": len(paths),
        "parsed": parsed,
        "deduped_sessions": len(sessions),
        "duplicates_skipped": duplicates,
        "errors": len(errors),
        "error_examples": errors[:5],
    }
    return sessions, diagnostics


def build_summary(
    paths: Sequence[Path],
    *,
    items: Mapping[int, Any],
    action_names: Mapping[int, str],
    top: int,
    min_group_sessions: int,
) -> dict[str, Any]:
    sessions, diagnostics = load_session_usages(paths, items=items)
    return {
        "diagnostics": diagnostics,
        **summarize_usage(
            sessions,
            action_names=action_names,
            top=top,
            min_group_sessions=min_group_sessions,
        ),
    }


def _print_counter_section(title: str, rows: Sequence[Mapping[str, Any]]) -> None:
    print(title)
    for row in rows:
        label = row.get("label")
        suffix = f" {label}" if label else ""
        share = row.get("share")
        share_text = f" share={share}" if share is not None else ""
        print(f"  {row.get('key')}{suffix}: {row.get('count')}{share_text}")


def _print_outcome_section(title: str, rows: Sequence[Mapping[str, Any]]) -> None:
    print(title)
    for row in rows:
        label = row.get("label")
        key = f"{row.get('key')} {label}" if label else str(row.get("key"))
        print(
            "  "
            f"{key}: sessions={row.get('sessions')} "
            f"settled={row.get('settled_sessions')} "
            f"median_final={row.get('median_final_value')} "
            f"median_local_bid_to_value={row.get('median_local_bid_to_value')} "
            f"over_value_rate={row.get('local_over_value_rate')}"
        )


def print_text_summary(summary: Mapping[str, Any]) -> None:
    diagnostics = summary.get("diagnostics", {})
    print(
        "live_sample_usage "
        f"paths={diagnostics.get('paths')} parsed={diagnostics.get('parsed')} "
        f"sessions={summary.get('sessions')} duplicates={diagnostics.get('duplicates_skipped')} "
        f"errors={diagnostics.get('errors')}"
    )
    print(
        f"settled_sessions={summary.get('settled_sessions')} "
        f"with_local_player={summary.get('sessions_with_local_player')} "
        f"with_local_hero={summary.get('sessions_with_local_hero')}"
    )
    print()
    _print_counter_section("local_heroes", summary.get("local_heroes", ()))
    print()
    _print_counter_section("opponent_heroes", summary.get("opponent_heroes", ()))
    print()
    _print_counter_section("local_action_sessions", summary.get("local_action_sessions", ()))
    print()
    _print_outcome_section("outcome_by_local_action", summary.get("outcome_by_local_action", ()))
    print()
    print("recommended_loadout_candidates")
    for hero, rows in sorted((summary.get("recommended_loadout_candidates") or {}).items()):
        rendered = [
            f"{row.get('action') or row.get('action_id')}({row.get('hero_sessions')}/{row.get('global_sessions')})"
            for row in rows
        ]
        print(f"  {hero}: " + " / ".join(rendered))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-root",
        action="append",
        type=Path,
        help="Raw capture file or directory. Defaults to data/logs/live/raw.",
    )
    parser.add_argument(
        "--battle-items",
        type=Path,
        default=ROOT / "data" / "processed" / "battle_items.json",
    )
    parser.add_argument("--tables-dir", type=Path, default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-group-sessions", type=int, default=1)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    raw_roots = args.raw_root or [ROOT / "data" / "logs" / "live" / "raw"]
    paths = _discover_paths(raw_roots)
    tables = load_monitor_tables(tables_dir=args.tables_dir)
    action_names = _load_action_names(args.battle_items)
    summary = build_summary(
        paths,
        items=tables.items,
        action_names=action_names,
        top=max(1, int(args.top)),
        min_group_sessions=max(1, int(args.min_group_sessions)),
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_text_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
