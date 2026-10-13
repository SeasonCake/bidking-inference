"""Audit source support-depth policies for v3 CSE.

This script is diagnostic-only. It evaluates whether a source-aware expansion
candidate should require more train-fold source support before using a primary
or fallback group. It never modifies CSE artifacts, sampler behavior, live/UI,
formal/value logic, or official bidding.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from summarize_v3_capacity_source_expansion_holdout import (  # noqa: E402
    GROUP_BY_CHOICES,
    _counter_dict,
    _float_or_none,
    _format_counts,
    _group_value,
    _numeric_summary,
    _rate,
    _round_metric,
    _rows_for_paths,
    _session_id,
    _source_semantics_truth,
    _stable_fold,
    _unique_round_overflow,
)

DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
SOURCE_FILTER_CHOICES = (
    "all",
    "external",
    "payload",
    "partial_payload",
    "numeric_empty",
)
DEFAULT_SOURCE_FILTER_PAIRS = (
    ("all", "all"),
    ("external", "external"),
    ("payload", "payload"),
    ("partial_payload", "partial_payload"),
    ("all", "external"),
    ("external", "all"),
)
DEFAULT_MIN_TRAIN_SOURCE_ROWS = (1, 2, 3, 4, 5)


def _source_context(row: Mapping[str, Any]) -> str:
    return str(row.get("source_context_class") or "")


def _source_filter_match(row: Mapping[str, Any], source_filter: str) -> bool:
    if not _source_semantics_truth(row):
        return False
    context = _source_context(row)
    if source_filter == "all":
        return True
    if source_filter == "external":
        return context in {"public_total_confirmed", "direct_action_full_confirmed"}
    if source_filter == "payload":
        return context.startswith("payload_verified_") or context.startswith(
            "payload_unverified_"
        )
    if source_filter == "partial_payload":
        return context == "payload_verified_partial_action_only"
    if source_filter == "numeric_empty":
        return context == "payload_verified_empty_action_results"
    raise ValueError(f"unsupported source_filter: {source_filter}")


def _train_support(
    rows: Iterable[Mapping[str, Any]],
    *,
    fold: int,
    folds: int,
    source_filter: str,
) -> dict[str, Any]:
    train = tuple(
        row
        for row in rows
        if _stable_fold(_session_id(row), folds) != fold
    )
    train_sessions = {_session_id(row) for row in train}
    source_rows = tuple(row for row in train if _source_filter_match(row, source_filter))
    return {
        "train_sessions": len(train_sessions),
        "train_source_rows": len(source_rows),
        "train_source_context_classes": _counter_dict(
            (_source_context(row) for row in source_rows),
            top=8,
        ),
        "train_source_map_ids": _counter_dict(
            (row.get("map_id") for row in source_rows),
            top=8,
        ),
    }


def _support_passes(
    support: Mapping[str, Any],
    *,
    min_train_sessions: int,
    min_train_source_rows: int,
) -> bool:
    return int(support.get("train_sessions") or 0) >= int(min_train_sessions) and int(
        support.get("train_source_rows") or 0
    ) >= int(min_train_source_rows)


def _missed_examples(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> list[dict[str, Any]]:
    selected = sorted(
        rows,
        key=lambda row: (
            -float(row.get("unique_round_excess_after_temp") or 0.0),
            str(row.get("file") or ""),
        ),
    )[:top]
    return [
        {
            "file": row.get("file"),
            "map_id": row.get("map_id"),
            "map_family": row.get("map_family"),
            "fold": row.get("fold"),
            "primary_group": row.get("primary_group"),
            "fallback_group": row.get("fallback_group"),
            "primary_train_sessions": row.get("primary_train_sessions"),
            "primary_train_source_rows": row.get("primary_train_source_rows"),
            "fallback_train_sessions": row.get("fallback_train_sessions"),
            "fallback_train_source_rows": row.get("fallback_train_source_rows"),
            "source_context_class": row.get("source_context_class"),
            "mechanism_class": row.get("mechanism_class"),
            "unique_round_excess_after_temp": row.get(
                "unique_round_excess_after_temp"
            ),
        }
        for row in selected
    ]


def evaluate_support_depth_policy(
    rows: Iterable[Mapping[str, Any]],
    *,
    primary_group_by: str = "map_id",
    fallback_group_by: str | None = None,
    source_filter: str = "all",
    fallback_source_filter: str | None = None,
    min_train_source_rows: int = 1,
    min_train_sessions: int = 4,
    folds: int = 5,
    top: int = 8,
) -> dict[str, Any]:
    if primary_group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"unsupported primary_group_by: {primary_group_by}")
    if fallback_group_by is not None and fallback_group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"unsupported fallback_group_by: {fallback_group_by}")
    if source_filter not in SOURCE_FILTER_CHOICES:
        raise ValueError(f"unsupported source_filter: {source_filter}")
    fallback_source_filter = fallback_source_filter or source_filter
    if fallback_source_filter not in SOURCE_FILTER_CHOICES:
        raise ValueError(f"unsupported fallback_source_filter: {fallback_source_filter}")

    seq = tuple(row for row in rows if row.get("status") == "ok")
    by_primary: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_fallback: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in seq:
        by_primary[_group_value(row, primary_group_by)].append(row)
        if fallback_group_by is not None:
            by_fallback[_group_value(row, fallback_group_by)].append(row)

    truth_rows = 0
    source_truth_rows = 0
    candidate_rows = 0
    covered_rows = 0
    covered_source_rows = 0
    false_positive_rows = 0
    primary_candidate_rows = 0
    fallback_candidate_rows = 0
    primary_train_source_values: list[int] = []
    fallback_train_source_values: list[int] = []
    missed: list[dict[str, Any]] = []
    candidate_source_counts: Counter[str] = Counter()

    for row in seq:
        fold = _stable_fold(_session_id(row), folds)
        primary_group = _group_value(row, primary_group_by)
        primary_support = _train_support(
            by_primary.get(primary_group, ()),
            fold=fold,
            folds=folds,
            source_filter=source_filter,
        )
        fallback_group = (
            _group_value(row, fallback_group_by)
            if fallback_group_by is not None
            else None
        )
        fallback_support = {
            "train_sessions": 0,
            "train_source_rows": 0,
            "train_source_context_classes": {},
            "train_source_map_ids": {},
        }
        candidate_source = "none"
        if _support_passes(
            primary_support,
            min_train_sessions=min_train_sessions,
            min_train_source_rows=min_train_source_rows,
        ):
            candidate_source = "primary"
        elif fallback_group is not None:
            fallback_support = _train_support(
                by_fallback.get(fallback_group, ()),
                fold=fold,
                folds=folds,
                source_filter=fallback_source_filter,
            )
            if _support_passes(
                fallback_support,
                min_train_sessions=min_train_sessions,
                min_train_source_rows=min_train_source_rows,
            ):
                candidate_source = "fallback"

        truth_unique = _unique_round_overflow(row)
        truth_source = _source_semantics_truth(row)
        if truth_unique:
            truth_rows += 1
        if truth_source:
            source_truth_rows += 1
        if candidate_source != "none":
            candidate_rows += 1
            candidate_source_counts[candidate_source] += 1
            if candidate_source == "primary":
                primary_candidate_rows += 1
                primary_train_source_values.append(
                    int(primary_support.get("train_source_rows") or 0)
                )
            else:
                fallback_candidate_rows += 1
                fallback_train_source_values.append(
                    int(fallback_support.get("train_source_rows") or 0)
                )
            if truth_unique:
                covered_rows += 1
            else:
                false_positive_rows += 1
            if truth_source:
                covered_source_rows += 1
        elif truth_unique:
            missed.append(
                {
                    **row,
                    "fold": fold,
                    "primary_group": primary_group,
                    "fallback_group": fallback_group,
                    "primary_train_sessions": primary_support.get("train_sessions"),
                    "primary_train_source_rows": primary_support.get(
                        "train_source_rows"
                    ),
                    "fallback_train_sessions": fallback_support.get(
                        "train_sessions"
                    ),
                    "fallback_train_source_rows": fallback_support.get(
                        "train_source_rows"
                    ),
                    "unique_round_excess_after_temp": _float_or_none(
                        row.get("unique_round_cap_excess_after_temp_zodiac_count")
                    ),
                }
            )

    return {
        "primary_group_by": primary_group_by,
        "fallback_group_by": fallback_group_by,
        "source_filter": source_filter,
        "fallback_source_filter": fallback_source_filter,
        "min_train_source_rows": min_train_source_rows,
        "min_train_sessions": min_train_sessions,
        "folds": folds,
        "truth_unique_round_rows": truth_rows,
        "truth_source_semantics_rows": source_truth_rows,
        "candidate_rows": candidate_rows,
        "candidate_source_counts": dict(candidate_source_counts),
        "primary_candidate_rows": primary_candidate_rows,
        "fallback_candidate_rows": fallback_candidate_rows,
        "covered_unique_round_rows": covered_rows,
        "covered_source_semantics_rows": covered_source_rows,
        "missed_unique_round_rows": max(0, truth_rows - covered_rows),
        "false_positive_candidate_rows": false_positive_rows,
        "unique_round_recall": _round_metric(_rate(covered_rows, truth_rows)),
        "source_semantics_recall": _round_metric(
            _rate(covered_source_rows, source_truth_rows)
        ),
        "candidate_precision": _round_metric(_rate(covered_rows, candidate_rows)),
        "candidate_rate": _round_metric(_rate(candidate_rows, len(seq))),
        "primary_train_source_rows": _numeric_summary(primary_train_source_values),
        "fallback_train_source_rows": _numeric_summary(fallback_train_source_values),
        "missed_examples": _missed_examples(missed, top=top),
    }


def _source_filter_pairs(raw: Iterable[str] | None) -> tuple[tuple[str, str], ...]:
    if not raw:
        return DEFAULT_SOURCE_FILTER_PAIRS
    out: list[tuple[str, str]] = []
    for item in raw:
        parts = str(item).split(":", 1)
        primary = parts[0]
        fallback = parts[1] if len(parts) == 2 else primary
        if primary not in SOURCE_FILTER_CHOICES:
            raise ValueError(f"unsupported source filter: {primary}")
        if fallback not in SOURCE_FILTER_CHOICES:
            raise ValueError(f"unsupported fallback source filter: {fallback}")
        out.append((primary, fallback))
    return tuple(out)


def summarize_support_depth_holdout(
    paths: Iterable[Path] = (),
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    primary_group_by: str = "map_id",
    fallback_group_by_values: Iterable[str | None] = (None, "map_family"),
    source_filter_pairs: Iterable[tuple[str, str]] = DEFAULT_SOURCE_FILTER_PAIRS,
    min_train_source_rows_values: Iterable[int] = DEFAULT_MIN_TRAIN_SOURCE_ROWS,
    min_train_sessions: int = 4,
    folds: int = 5,
    top: int = 8,
) -> dict[str, Any]:
    selected_paths = tuple(paths) or (DEFAULT_SAMPLE_ROOT,)
    source_rows = tuple(rows) if rows is not None else _rows_for_paths(selected_paths)
    ok_rows = tuple(row for row in source_rows if row.get("status") == "ok")
    results: list[dict[str, Any]] = []
    for fallback_group_by in fallback_group_by_values:
        for source_filter, fallback_source_filter in source_filter_pairs:
            for min_train_source_rows in min_train_source_rows_values:
                results.append(
                    evaluate_support_depth_policy(
                        ok_rows,
                        primary_group_by=primary_group_by,
                        fallback_group_by=fallback_group_by,
                        source_filter=source_filter,
                        fallback_source_filter=fallback_source_filter,
                        min_train_source_rows=int(min_train_source_rows),
                        min_train_sessions=min_train_sessions,
                        folds=folds,
                        top=top,
                    )
                )
    return {
        "sessions": len(ok_rows),
        "folds": folds,
        "min_train_sessions": min_train_sessions,
        "primary_group_by": primary_group_by,
        "fallback_group_by_values": [
            value if value is not None else "none"
            for value in fallback_group_by_values
        ],
        "source_filter_pairs": [
            f"{primary}:{fallback}"
            for primary, fallback in source_filter_pairs
        ],
        "min_train_source_rows_values": list(min_train_source_rows_values),
        "truth_context_classes": _counter_dict(
            (
                _source_context(row)
                for row in ok_rows
                if _source_semantics_truth(row)
            ),
            top=top,
        ),
        "truth_unique_round_excess_after_temp": _numeric_summary(
            row.get("unique_round_cap_excess_after_temp_zodiac_count")
            for row in ok_rows
            if _source_semantics_truth(row)
        ),
        "rows": sorted(
            results,
            key=lambda row: (
                row["unique_round_recall"] is None,
                -float(row["unique_round_recall"] or 0.0),
                -float(row["candidate_precision"] or 0.0),
                int(row["candidate_rows"]),
                str(row["fallback_group_by"] or "none"),
                str(row["source_filter"]),
                str(row["fallback_source_filter"]),
                int(row["min_train_source_rows"]),
            ),
        ),
    }


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/max={summary['max']}"
    )


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"sessions={result['sessions']}",
                f"folds={result['folds']}",
                f"min_train_sessions={result['min_train_sessions']}",
                f"primary_group_by={result['primary_group_by']}",
                f"fallback_group_by_values={','.join(result['fallback_group_by_values'])}",
                f"source_filter_pairs={','.join(result['source_filter_pairs'])}",
                f"min_train_source_rows_values={','.join(map(str, result['min_train_source_rows_values']))}",
                f"truth_context={_format_counts(result['truth_context_classes'])}",
                f"truth_excess={_format_summary(result['truth_unique_round_excess_after_temp'])}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"primary={row['primary_group_by']}",
                    f"fallback={row['fallback_group_by'] or '-'}",
                    f"source_filter={row['source_filter']}",
                    f"fallback_filter={row['fallback_source_filter']}",
                    f"min_source={row['min_train_source_rows']}",
                    f"truth={row['truth_unique_round_rows']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"sources={_format_counts(row['candidate_source_counts'])}",
                    f"covered={row['covered_unique_round_rows']}",
                    f"missed={row['missed_unique_round_rows']}",
                    f"false_positive={row['false_positive_candidate_rows']}",
                    f"recall={row['unique_round_recall']}",
                    f"precision={row['candidate_precision']}",
                    f"primary_train_source={_format_summary(row['primary_train_source_rows'])}",
                    f"fallback_train_source={_format_summary(row['fallback_train_source_rows'])}",
                )
            )
        )
        for example in row["missed_examples"][: min(top, 3)]:
            print(
                " ".join(
                    (
                        "  missed_example",
                        f"file={example['file']}",
                        f"map_id={example['map_id']}",
                        f"context={example['source_context_class']}",
                        f"primary_group={example['primary_group']}",
                        f"primary_train_source={example['primary_train_source_rows']}",
                        f"fallback_group={example['fallback_group'] or '-'}",
                        f"fallback_train_source={example['fallback_train_source_rows']}",
                        f"excess={example['unique_round_excess_after_temp']}",
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 CSE source support-depth holdout policies.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--primary-group-by",
        choices=GROUP_BY_CHOICES,
        default="map_id",
    )
    parser.add_argument(
        "--fallback-group-by",
        action="append",
        choices=("none", *GROUP_BY_CHOICES),
        help="Fallback group to evaluate. Repeat for a subset. Default: none,map_family.",
    )
    parser.add_argument(
        "--source-filter-pair",
        action="append",
        help=(
            "Source filter pair as primary[:fallback]. Choices: "
            f"{','.join(SOURCE_FILTER_CHOICES)}. Repeat for a subset."
        ),
    )
    parser.add_argument(
        "--min-train-source-rows",
        action="append",
        type=int,
        help="Minimum train source rows to evaluate. Repeat for a subset.",
    )
    parser.add_argument("--min-train-sessions", type=int, default=4)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    fallback_values = (
        tuple(
            None if value == "none" else value
            for value in args.fallback_group_by
        )
        if args.fallback_group_by
        else (None, "map_family")
    )
    result = summarize_support_depth_holdout(
        args.paths,
        primary_group_by=args.primary_group_by,
        fallback_group_by_values=fallback_values,
        source_filter_pairs=_source_filter_pairs(args.source_filter_pair),
        min_train_source_rows_values=tuple(
            args.min_train_source_rows or DEFAULT_MIN_TRAIN_SOURCE_ROWS
        ),
        min_train_sessions=args.min_train_sessions,
        folds=args.folds,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
