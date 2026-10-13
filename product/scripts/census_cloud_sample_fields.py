"""Census replay/truth fields in a collector JSONL export.

This is deliberately a schema census, not an eligibility decision. It reports
the evidence needed to separate a truth pool from a replay pool without
silently treating final settlement truth as pre-settlement replay evidence.
Supports plain ``.jsonl`` and gzip-compressed exports.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO


DEFAULT_HEROES = ("raven", "gabriela")
HERO_ALIASES = {
    "raven": "raven",
    "拉文": "raven",
    "gabriela": "gabriela",
    "gabriella": "gabriela",
    "加布里埃拉": "gabriela",
}
TRUTH_CORE_FIELDS = (
    "final_quality_counts",
    "final_quality_cells",
    "final_quality_values",
    "inventory_count",
    "inventory_cells",
    "known_value_sum",
)
TRUTH_OPTIONAL_FIELDS = ("total_item_count", "catalog_item_count")
# client_version 是上传协议版本（当前 sample_upload_v0），app_version 才是用户实际运行的产品版本。
SCHEMA_MARKER_FIELDS = ("uid_hash", "client_version", "app_version")
REF_CORE_FIELDS = (
    "hero",
    "counts",
    "avg_cells",
    "avg_values",
    "quality_cells",
    "quality_values",
    "count_sums",
    "total_count",
    "total_cells",
    "minimap_items",
)
REPORT_METRICS = (
    "rows",
    "truth.core_complete",
    "truth.settlement_incomplete_true",
    "truth.q6_parseable",
    "truth.q6_explicit",
    "truth.q6_zero_or_absent",
    "truth.q6_positive",
    "replay.snapshot_nonempty",
    "replay.ref_inputs_nonempty",
    "replay.ref_core_keys_present",
    "replay.ref_counts_q6_parseable",
    "replay.ref_counts_q6_positive",
    "replay.minimap_items_nonempty",
    "replay.minimap_q6_positive",
    "replay.minimap_q6_positioned",
    "replay.minimap_q6_without_position",
    "replay.minimap_q6_equals_final_q6",
    "replay.minimap_q6_less_than_final_q6",
    "replay.minimap_q6_greater_than_final_q6",
)
_NONNEGATIVE_INT = re.compile(r"^(?:0|[1-9][0-9]*)$")
_MISSING = object()


def _normalise_hero(value: Any) -> str:
    key = str(value or "").strip().lower()
    return HERO_ALIASES.get(key, key)


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != {} and value != []


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    text = str(value).strip()
    return int(text) if _NONNEGATIVE_INT.fullmatch(text) else None


def _quality_count(
    value: Any, quality: str, *, absent_is_zero: bool = False
) -> tuple[int | None, bool]:
    raw: Any = _MISSING
    if isinstance(value, Mapping):
        raw = value.get(quality, _MISSING)
    elif isinstance(value, str):
        for part in value.split(";"):
            key, sep, candidate = part.partition("=")
            if sep and key.strip().lower() == quality:
                raw = candidate.strip()
                break
    if raw is _MISSING:
        return (0, False) if absent_is_zero and _present(value) else (None, False)
    return (_as_nonnegative_int(raw), True)


def _item_quality(item: Mapping[str, Any]) -> int | None:
    return _as_nonnegative_int(item.get("quality"))


def _positioned(item: Mapping[str, Any]) -> bool:
    return _as_nonnegative_int(item.get("row")) is not None and _as_nonnegative_int(
        item.get("col")
    ) is not None


def _open_text(path: Path) -> TextIO:
    with path.open("rb") as probe:
        is_gzip = probe.read(2) == b"\x1f\x8b"
    if is_gzip:
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _new_hero_state() -> dict[str, Any]:
    return {
        "metrics": Counter(),
        "sessions": set(),
        "client_versions": Counter(),
        "app_versions": Counter(),
    }


def _accumulate(state: dict[str, Any], record: Mapping[str, Any]) -> None:
    metrics: Counter[str] = state["metrics"]
    metrics["rows"] += 1

    session_hash = record.get("session_hash")
    if _present(session_hash):
        state["sessions"].add(str(session_hash))

    for field in (*TRUTH_CORE_FIELDS, *TRUTH_OPTIONAL_FIELDS, *SCHEMA_MARKER_FIELDS):
        if _present(record.get(field)):
            metrics[f"field.{field}"] += 1
    if all(_present(record.get(field)) for field in TRUTH_CORE_FIELDS):
        metrics["truth.core_complete"] += 1
    if record.get("settlement_incomplete") is True:
        metrics["truth.settlement_incomplete_true"] += 1

    truth_q6, truth_q6_explicit = _quality_count(
        record.get("final_quality_counts"), "q6", absent_is_zero=True
    )
    if truth_q6 is not None:
        metrics["truth.q6_parseable"] += 1
        if truth_q6_explicit:
            metrics["truth.q6_explicit"] += 1
        if truth_q6 > 0:
            metrics["truth.q6_positive"] += 1
        else:
            metrics["truth.q6_zero_or_absent"] += 1

    version = record.get("client_version")
    if _present(version):
        state["client_versions"][str(version)] += 1
    app_version = record.get("app_version")
    if _present(app_version):
        state["app_versions"][str(app_version)] += 1

    snapshot = record.get("snapshot")
    if _present(snapshot):
        metrics["replay.snapshot_nonempty"] += 1

    ref_inputs = record.get("ref_inputs")
    if not isinstance(ref_inputs, Mapping) or not ref_inputs:
        return
    metrics["replay.ref_inputs_nonempty"] += 1
    if all(field in ref_inputs for field in REF_CORE_FIELDS):
        metrics["replay.ref_core_keys_present"] += 1

    ref_q6, _ = _quality_count(ref_inputs.get("counts"), "q6")
    if ref_q6 is not None:
        metrics["replay.ref_counts_q6_parseable"] += 1
        if ref_q6 > 0:
            metrics["replay.ref_counts_q6_positive"] += 1

    raw_items = ref_inputs.get("minimap_items")
    items = [item for item in raw_items if isinstance(item, Mapping)] if isinstance(raw_items, list) else []
    if items:
        metrics["replay.minimap_items_nonempty"] += 1
    red_items = [item for item in items if _item_quality(item) == 6]
    if red_items:
        metrics["replay.minimap_q6_positive"] += 1
    if any(_positioned(item) for item in red_items):
        metrics["replay.minimap_q6_positioned"] += 1
    if any(not _positioned(item) for item in red_items):
        metrics["replay.minimap_q6_without_position"] += 1

    if truth_q6 is not None:
        minimap_q6 = len(red_items)
        if minimap_q6 == truth_q6:
            metrics["replay.minimap_q6_equals_final_q6"] += 1
        elif minimap_q6 < truth_q6:
            metrics["replay.minimap_q6_less_than_final_q6"] += 1
        else:
            metrics["replay.minimap_q6_greater_than_final_q6"] += 1


def census(path: Path, heroes: tuple[str, ...]) -> dict[str, Any]:
    wanted = {_normalise_hero(hero) for hero in heroes}
    states: defaultdict[str, dict[str, Any]] = defaultdict(_new_hero_state)
    scanned_rows = 0
    bad_json = 0

    with _open_text(path) as handle:
        for line in handle:
            scanned_rows += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                bad_json += 1
                continue
            if not isinstance(record, Mapping):
                bad_json += 1
                continue
            hero = _normalise_hero(record.get("hero"))
            if hero in wanted:
                _accumulate(states[hero], record)

    hero_results: dict[str, Any] = {}
    for hero in sorted(wanted):
        state = states[hero]
        for metric in REPORT_METRICS:
            state["metrics"].setdefault(metric, 0)
        for field in (*TRUTH_CORE_FIELDS, *TRUTH_OPTIONAL_FIELDS, *SCHEMA_MARKER_FIELDS):
            state["metrics"].setdefault(f"field.{field}", 0)
        metrics = dict(sorted(state["metrics"].items()))
        metrics["unique_session_hashes"] = len(state["sessions"])
        hero_results[hero] = {
            "metrics": metrics,
            # 两个版本字段必须分栏，避免把协议常量 sample_upload_v0 误报成产品版本。
            "client_versions": dict(sorted(state["client_versions"].items())),
            "app_versions": dict(sorted(state["app_versions"].items())),
        }
    return {
        "file": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "scanned_rows": scanned_rows,
        "bad_json": bad_json,
        "heroes": hero_results,
        "definitions": {
            "truth.core_complete": list(TRUTH_CORE_FIELDS),
            "replay.ref_core_keys_present": list(REF_CORE_FIELDS),
            "note": "missing q6 in final settlement tally means zero; missing q6 in ref_inputs remains missing. Minimap q6 is pre-settlement evidence, so equality with final q6 is descriptive, not an automatic eligibility gate",
        },
    }


def _validation_errors(
    result: Mapping[str, Any], *, strict: bool, min_rows: int
) -> list[str]:
    errors: list[str] = []
    scanned_rows = int(result.get("scanned_rows") or 0)
    bad_json = int(result.get("bad_json") or 0)
    if scanned_rows < min_rows:
        errors.append(f"scanned_rows={scanned_rows} is below required minimum {min_rows}")
    if strict and bad_json:
        errors.append(f"bad_json={bad_json}, strict mode requires zero")
    return errors


def _self_test() -> None:
    raven = _new_hero_state()
    _accumulate(
        raven,
        {
            "session_hash": "known-good-raven",
            "final_quality_counts": "q3=2;q6=1",
            "final_quality_cells": "q3=4;q6=2",
            "final_quality_values": "q3=100;q6=900",
            "inventory_count": 3,
            "inventory_cells": 6,
            "known_value_sum": 1000,
            "total_item_count": 3,
            "catalog_item_count": 3,
            "uid_hash": "example",
            "client_version": "self-test",
            "app_version": "0.2.7-self-test",
            "snapshot": {"round": 4},
            "ref_inputs": {
                "hero": "raven",
                "counts": {"q6": 1},
                "avg_cells": {},
                "avg_values": {},
                "quality_cells": {},
                "quality_values": {},
                "count_sums": {},
                "total_count": 3,
                "total_cells": 6,
                "minimap_items": [{"quality": 6, "row": 1, "col": 2}],
            },
        },
    )
    metrics = raven["metrics"]
    assert metrics["truth.core_complete"] == 1
    assert metrics["replay.ref_inputs_nonempty"] == 1
    assert metrics["replay.snapshot_nonempty"] == 1
    assert metrics["replay.minimap_q6_positioned"] == 1
    assert metrics["replay.minimap_q6_equals_final_q6"] == 1
    assert metrics["field.app_version"] == 1
    assert raven["client_versions"] == Counter({"self-test": 1})
    assert raven["app_versions"] == Counter({"0.2.7-self-test": 1})

    gabriela = _new_hero_state()
    _accumulate(gabriela, {"session_hash": "known-negative", "final_quality_counts": "q6=0"})
    assert gabriela["metrics"]["rows"] == 1
    assert gabriela["metrics"]["replay.ref_inputs_nonempty"] == 0
    assert _validation_errors(
        {"scanned_rows": 0, "bad_json": 0}, strict=True, min_rows=1
    )
    assert _validation_errors(
        {"scanned_rows": 10, "bad_json": 1}, strict=True, min_rows=1
    )
    assert not _validation_errors(
        {"scanned_rows": 10, "bad_json": 0}, strict=True, min_rows=1
    )
    print("self-test passed: known replay-positive and replay-negative records distinguished")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="collector .jsonl or gzip export")
    parser.add_argument("--heroes", nargs="+", default=list(DEFAULT_HEROES))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--strict", action="store_true", help="fail if any JSON row is bad")
    parser.add_argument(
        "--min-rows", type=int, default=1, help="fail below this total row count (default: 1)"
    )
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.path is None:
        parser.error("path is required unless --self-test is used")
    if not args.path.is_file():
        parser.error(f"file not found: {args.path}")
    if args.min_rows < 0:
        parser.error("--min-rows must be non-negative")
    result = census(args.path, tuple(args.heroes))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    errors = _validation_errors(result, strict=args.strict, min_rows=args.min_rows)
    if errors:
        for error in errors:
            print(f"census validation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
