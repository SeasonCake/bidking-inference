"""Move activity-map Fatbeans samples out of the default baseline archive.

Run this after ``organize_fatbeans_real_samples.py``.  The real-sample
organizer dedupes/canonicalizes captures, while this script keeps known
activity map ranges in a separate reference cohort so default baseline
evaluation does not silently mix prior-drift samples.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.organize_fatbeans_real_samples import (  # noqa: E402
    DEFAULT_ARCHIVE,
    SampleMeta,
    _canonical_prefix,
    _iter_json,
    _rel,
    sample_meta,
)

DEFAULT_ACTIVITY_DIR = ROOT / "data" / "samples" / "fatbeans_activity_20260605_shipwreck"
DEFAULT_RANGES = ((2521, 2530), (4521, 4530))


@dataclass(frozen=True)
class ActivityAction:
    action: str
    source: str
    destination: str | None
    sample_class: str
    reason: str
    session_token: str | None
    hero: str | None
    map_id: int | None
    rounds: int
    ready_windows: int
    no_state_windows: int
    sha256: str


def _parse_ranges(values: Iterable[str] | None) -> tuple[tuple[int, int], ...]:
    if not values:
        return DEFAULT_RANGES
    ranges: list[tuple[int, int]] = []
    for value in values:
        text = value.strip()
        if "-" in text:
            lo_text, hi_text = text.split("-", 1)
            lo, hi = int(lo_text), int(hi_text)
        else:
            lo = hi = int(text)
        if hi < lo:
            raise ValueError(f"invalid range: {value}")
        ranges.append((lo, hi))
    return tuple(ranges)


def _is_activity_map(map_id: int | None, ranges: tuple[tuple[int, int], ...]) -> bool:
    if map_id is None:
        return False
    return any(lo <= int(map_id) <= hi for lo, hi in ranges)


def _used_activity_indices(activity_dir: Path) -> set[int]:
    used: set[int] = set()
    for path in activity_dir.glob("*.json"):
        stem = path.stem
        suffix = stem.rsplit("_", 1)[-1]
        if len(suffix) == 4 and suffix.isdigit():
            used.add(int(suffix))
    return used


def _next_activity_destination(
    meta: SampleMeta,
    *,
    activity_dir: Path,
    used_indices: set[int],
) -> Path:
    index = 1
    while index in used_indices:
        index += 1
    used_indices.add(index)
    return activity_dir / f"{_canonical_prefix(meta)}_{index:04d}.json"


def build_plan(
    paths: Iterable[Path],
    *,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
    ranges: tuple[tuple[int, int], ...] = DEFAULT_RANGES,
) -> dict[str, Any]:
    used_indices = _used_activity_indices(activity_dir)
    actions: list[ActivityAction] = []
    for path in _iter_json(paths):
        meta = sample_meta(path)
        if not _is_activity_map(meta.map_id, ranges):
            actions.append(
                ActivityAction(
                    action="keep",
                    source=_rel(path),
                    destination=None,
                    sample_class=meta.sample_class,
                    reason="non_activity_map",
                    session_token=meta.session_token,
                    hero=meta.hero,
                    map_id=meta.map_id,
                    rounds=meta.rounds,
                    ready_windows=meta.ready_windows,
                    no_state_windows=meta.no_state_windows,
                    sha256=meta.sha256,
                )
            )
            continue
        try:
            path.resolve().relative_to(activity_dir.resolve())
        except ValueError:
            destination = _next_activity_destination(
                meta,
                activity_dir=activity_dir,
                used_indices=used_indices,
            )
            action = "move"
            reason = "activity_map_move_to_reference_cohort"
        else:
            destination = path
            action = "keep"
            reason = "already_activity_reference"
        if destination.exists() and destination.resolve() != path.resolve():
            action = "error"
            reason = "destination_exists"
        actions.append(
            ActivityAction(
                action=action,
                source=_rel(path),
                destination=_rel(destination),
                sample_class=meta.sample_class,
                reason=reason,
                session_token=meta.session_token,
                hero=meta.hero,
                map_id=meta.map_id,
                rounds=meta.rounds,
                ready_windows=meta.ready_windows,
                no_state_windows=meta.no_state_windows,
                sha256=meta.sha256,
            )
        )
    summary = {
        "source": "fatbeans_activity_sample_organizer",
        "activity_dir": _rel(activity_dir),
        "activity_ranges": [f"{lo}-{hi}" for lo, hi in ranges],
        "input_files": len(actions),
        "move": sum(1 for action in actions if action.action == "move"),
        "keep": sum(1 for action in actions if action.action == "keep"),
        "errors": sum(1 for action in actions if action.action == "error"),
        "activity_files": sum(
            1 for action in actions if _is_activity_map(action.map_id, ranges)
        ),
        "activity_valid": sum(
            1
            for action in actions
            if _is_activity_map(action.map_id, ranges)
            and action.sample_class == "valid"
        ),
        "activity_mixed": sum(
            1
            for action in actions
            if _is_activity_map(action.map_id, ranges)
            and action.sample_class == "mixed"
        ),
    }
    return {"summary": summary, "actions": [asdict(action) for action in actions]}


def _absolute(rel_or_abs: str | None) -> Path | None:
    if rel_or_abs is None:
        return None
    path = Path(rel_or_abs)
    return path if path.is_absolute() else ROOT / path


def apply_plan(plan: dict[str, Any]) -> None:
    actions = [action for action in plan["actions"] if action["action"] == "move"]
    destinations = [_absolute(action["destination"]) for action in actions]
    resolved_destinations = [path.resolve() for path in destinations if path is not None]
    if len(resolved_destinations) != len(set(resolved_destinations)):
        raise ValueError("duplicate destinations in activity plan")
    for action in actions:
        source = _absolute(action["source"])
        destination = _absolute(action["destination"])
        if source is None or destination is None:
            raise ValueError(f"bad action: {action}")
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def _print_summary(plan: dict[str, Any], *, dry_run: bool) -> None:
    summary = plan["summary"]
    mode = "dry_run" if dry_run else "apply"
    print(
        " ".join(
            (
                f"organize_fatbeans_activity_samples: mode={mode}",
                f"input_files={summary['input_files']}",
                f"activity_files={summary['activity_files']}",
                f"activity_valid={summary['activity_valid']}",
                f"activity_mixed={summary['activity_mixed']}",
                f"move={summary['move']}",
                f"keep={summary['keep']}",
                f"errors={summary['errors']}",
            )
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Input sample roots. Defaults to data/samples/fatbeans and the activity cohort.",
    )
    parser.add_argument("--activity-dir", type=Path, default=DEFAULT_ACTIVITY_DIR)
    parser.add_argument(
        "--activity-map-range",
        action="append",
        help="Activity map id or inclusive range, e.g. 2521-2530. Defaults to 2521-2530 and 4521-4530.",
    )
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    ranges = _parse_ranges(args.activity_map_range)
    paths = args.paths or [DEFAULT_ARCHIVE, args.activity_dir]
    plan = build_plan(paths, activity_dir=args.activity_dir, ranges=ranges)
    if args.apply and plan["summary"]["errors"]:
        print("Refusing to apply plan with errors.", file=sys.stderr)
        return 2
    if args.apply:
        apply_plan(plan)
    if args.manifest_output is not None:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(plan, dry_run=not args.apply)
        if args.manifest_output is not None:
            print(f"manifest_output={_rel(args.manifest_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
