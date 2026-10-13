from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


LAB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LAB_ROOT.parents[1]
LAB_SRC = LAB_ROOT / "src"
PROJECT_SRC = PROJECT_ROOT / "src"
if str(LAB_SRC) not in sys.path:
    sys.path.insert(0, str(LAB_SRC))
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from ahmad_ref_engine import run_reference_engine  # noqa: E402

try:
    from bidking_lab.live.fatbeans import (  # type: ignore[import-not-found]
        live_batches_from_fatbeans_events,
        parse_fatbeans_capture,
    )
except Exception:  # noqa: BLE001 - diagnostic script should fail with context below
    live_batches_from_fatbeans_events = None  # type: ignore[assignment]
    parse_fatbeans_capture = None  # type: ignore[assignment]


def _money(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def _path_text(path: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in path)


def _build_snapshot(
    *,
    sample_path: Path,
    batch: Any,
    state: dict[tuple[str, ...], Any],
) -> dict[str, Any]:
    hero = state.get(("session", "hero"))
    map_id = state.get(("session", "map_id"))
    round_index = state.get(("session", "round"))
    field_updates = [
        {
            "path": list(path),
            "value": value,
            "source": "fatbeans_state",
        }
        for path, value in sorted(state.items(), key=lambda item: _path_text(item[0]))
        if path[:1] in {("session",), ("bucket",)}
    ]
    return {
        "hero": hero,
        "map_id": map_id,
        "round": round_index,
        "phase": getattr(batch, "phase", "") or "",
        "session_id": sample_path.stem,
        "file": str(sample_path),
        "ahmad_ref_inputs": {
            "field_updates": field_updates,
            "source": "fatbeans_live_batch_state",
        },
    }


def _row_from_result(
    *,
    sample_path: Path,
    batch: Any,
    result: Any,
) -> dict[str, Any]:
    evidence = result.evidence
    return {
        "sample": sample_path.name,
        "seq": getattr(batch, "sequence", None),
        "event": getattr(batch, "event_kind", ""),
        "phase": getattr(batch, "phase", ""),
        "round": getattr(evidence, "phase", ""),
        "map_id": evidence.map_id,
        "hero": evidence.hero,
        "total_count": evidence.total_count,
        "fixed_counts": dict(evidence.fixed_counts),
        "avg_cells": dict(evidence.avg_cells),
        "status": result.status,
        "combo_count": result.combo_count,
        "red_count_range": list(result.red_count_range),
        "total_grid_range": list(result.total_grid_range),
        "balanced": result.balanced,
        "raw_p50": result.value_p50,
        "notes": list(result.notes),
    }


def _iter_rows(sample_paths: list[Path], *, include_settled: bool) -> list[dict[str, Any]]:
    if parse_fatbeans_capture is None or live_batches_from_fatbeans_events is None:
        raise RuntimeError("bidking_lab live parser is unavailable; run from bidking-lab project root")
    rows: list[dict[str, Any]] = []
    for sample_path in sample_paths:
        events = parse_fatbeans_capture(sample_path)
        batches = live_batches_from_fatbeans_events(events)
        state: dict[tuple[str, ...], Any] = {}
        for batch in batches:
            for update in getattr(batch, "field_updates", ()) or ():
                state[tuple(str(part) for part in update.path)] = update.value
            if not include_settled and getattr(batch, "event_kind", "") == "session_settled":
                continue
            snapshot = _build_snapshot(sample_path=sample_path, batch=batch, state=state)
            result = run_reference_engine(snapshot)
            if result.status == "not_ahmed":
                continue
            rows.append(
                _row_from_result(
                    sample_path=sample_path,
                    batch=batch,
                    result=result,
                )
            )
    return rows


def _print_summary(rows: list[dict[str, Any]]) -> None:
    headers = [
        "sample",
        "seq",
        "event",
        "map",
        "total",
        "avg",
        "fixed",
        "status",
        "combos",
        "red",
        "balanced",
        "notes",
    ]
    print("\t".join(headers))
    for row in rows:
        avg = ",".join(f"{key}={value:.3g}" for key, value in sorted(row["avg_cells"].items()))
        fixed = ",".join(f"{key}={value}" for key, value in sorted(row["fixed_counts"].items()))
        notes = ";".join(str(note) for note in row["notes"][:8])
        print(
            "\t".join(
                [
                    str(row["sample"]),
                    str(row["seq"]),
                    str(row["event"]),
                    str(row["map_id"]),
                    str(row["total_count"] or "-"),
                    avg or "-",
                    fixed or "-",
                    str(row["status"]),
                    str(row["combo_count"]),
                    "/".join(str(value or "?") for value in row["red_count_range"]),
                    _money(row["balanced"]),
                    notes,
                ]
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke isolated Ahmed ref_v0 on real Fatbeans sample batches.",
    )
    parser.add_argument(
        "samples",
        nargs="*",
        type=Path,
        help="Fatbeans sample JSON files. Defaults to data/samples/fatbeans/fatbeans_valid_ahmed_*.json.",
    )
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument(
        "--include-settled",
        action="store_true",
        help="Also print settlement batches. Defaults to prebid/live-like rows only.",
    )
    args = parser.parse_args(argv)

    sample_paths = args.samples or sorted(
        (PROJECT_ROOT / "data" / "samples" / "fatbeans").glob("fatbeans_valid_ahmed_*.json")
    )
    rows = _iter_rows([path.resolve() for path in sample_paths], include_settled=args.include_settled)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        _print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
