"""Audit source-key candidates for v3 capacity/source expansion.

This script is diagnostic-only. It tests whether action payload shape or action
id signatures improve capacity/source expansion holdout precision enough to be
considered for a future source-aware prior. It does not modify CSE artifacts,
posterior sampling, formal/value logic, live/UI behavior, or bidding.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from summarize_v3_capacity_source_expansion_holdout import (  # noqa: E402
    _counter_dict,
    _float_or_none,
    _format_counts,
    _numeric_summary,
    _rate,
    _round_metric,
    _rows_for_paths,
    _session_id,
    _source_semantics_truth,
    _stable_fold,
    _unique_round_overflow,
)
from summarize_v3_capacity_source_expansion_payload_only_audit import (  # noqa: E402
    _action_payload_shapes_by_file,
)

SOURCE_KEY_CHOICES = (
    "map_id",
    "map_family",
    "source_shape",
    "map_family_source_shape",
    "map_id_source_shape",
    "source_shape_signature",
    "map_family_source_shape_signature",
    "map_id_source_shape_signature",
)
DEFAULT_SOURCE_KEYS = (
    "map_id",
    "map_family",
    "source_shape",
    "map_family_source_shape",
    "map_id_source_shape",
    "source_shape_signature",
    "map_family_source_shape_signature",
    "map_id_source_shape_signature",
)
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"


def _base_file(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or "").split("#", 1)[0]


def _shape_for_row(
    row: Mapping[str, Any],
    shapes_by_file: Mapping[str, Mapping[str, Any]],
) -> str:
    shape = shapes_by_file.get(_base_file(row), {}).get(
        "source_action_payload_shape_class"
    )
    return str(shape) if shape not in (None, "") else "none"


def _action_signature_for_row(
    row: Mapping[str, Any],
    shapes_by_file: Mapping[str, Mapping[str, Any]],
) -> str:
    action_ids = shapes_by_file.get(_base_file(row), {}).get("source_action_ids")
    if not isinstance(action_ids, Mapping) or not action_ids:
        return "none"
    return "+".join(str(key) for key in sorted(action_ids))


def _shape_signature_for_row(
    row: Mapping[str, Any],
    shapes_by_file: Mapping[str, Mapping[str, Any]],
) -> str:
    shape = _shape_for_row(row, shapes_by_file)
    if shape == "numeric_only_result":
        return _action_signature_for_row(row, shapes_by_file)
    return shape


def _source_key_value(
    row: Mapping[str, Any],
    *,
    source_key: str,
    shapes_by_file: Mapping[str, Mapping[str, Any]],
) -> str:
    map_id = str(row.get("map_id") or "none")
    map_family = str(row.get("map_family") or "none")
    shape = _shape_for_row(row, shapes_by_file)
    signature = _shape_signature_for_row(row, shapes_by_file)
    if source_key == "map_id":
        return map_id
    if source_key == "map_family":
        return map_family
    if source_key == "source_shape":
        return shape
    if source_key == "map_family_source_shape":
        return f"{map_family}|shape={shape}"
    if source_key == "map_id_source_shape":
        return f"{map_id}|shape={shape}"
    if source_key == "source_shape_signature":
        return signature
    if source_key == "map_family_source_shape_signature":
        return f"{map_family}|sig={signature}"
    if source_key == "map_id_source_shape_signature":
        return f"{map_id}|sig={signature}"
    raise ValueError(f"unsupported source_key: {source_key}")


def _candidate_key_func(
    source_key: str,
    shapes_by_file: Mapping[str, Mapping[str, Any]],
) -> Callable[[Mapping[str, Any]], str]:
    return lambda row: _source_key_value(
        row,
        source_key=source_key,
        shapes_by_file=shapes_by_file,
    )


def _missed_examples(
    rows: Iterable[Mapping[str, Any]],
    *,
    key_func: Callable[[Mapping[str, Any]], str],
    shapes_by_file: Mapping[str, Mapping[str, Any]],
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
            "group": key_func(row),
            "fold": row.get("fold"),
            "train_sessions": row.get("train_sessions"),
            "train_source_semantics_rows": row.get("train_source_semantics_rows"),
            "source_shape": _shape_for_row(row, shapes_by_file),
            "source_shape_signature": _shape_signature_for_row(row, shapes_by_file),
            "source_context_class": row.get("source_context_class"),
            "mechanism_class": row.get("mechanism_class"),
            "unique_round_excess_after_temp": row.get(
                "unique_round_excess_after_temp"
            ),
        }
        for row in selected
    ]


def _evaluate_source_key(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_key: str,
    shapes_by_file: Mapping[str, Mapping[str, Any]],
    folds: int,
    min_train_sessions: int,
    top: int,
) -> dict[str, Any]:
    seq = tuple(row for row in rows if row.get("status") == "ok")
    key_func = _candidate_key_func(source_key, shapes_by_file)
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in seq:
        by_group[key_func(row)].append(row)

    truth_rows = 0
    source_truth_rows = 0
    candidate_rows = 0
    covered_rows = 0
    covered_source_rows = 0
    false_positive_rows = 0
    sample_limited_rows = 0
    missed: list[dict[str, Any]] = []
    candidate_sources: Counter[str] = Counter()

    for group_rows in by_group.values():
        group_seq = tuple(group_rows)
        for row in group_seq:
            fold = _stable_fold(_session_id(row), folds)
            train = tuple(
                other
                for other in group_seq
                if _stable_fold(_session_id(other), folds) != fold
            )
            train_sessions = {_session_id(item) for item in train}
            train_source_rows = sum(1 for item in train if _source_semantics_truth(item))
            sample_limited = len(train_sessions) < int(min_train_sessions)
            candidate_applied = not sample_limited and train_source_rows > 0
            truth_unique = _unique_round_overflow(row)
            truth_source = _source_semantics_truth(row)

            if sample_limited:
                sample_limited_rows += 1
            if truth_unique:
                truth_rows += 1
            if truth_source:
                source_truth_rows += 1
            if candidate_applied:
                candidate_rows += 1
                candidate_sources["primary"] += 1
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
                        "train_sessions": len(train_sessions),
                        "train_source_semantics_rows": train_source_rows,
                        "unique_round_excess_after_temp": _float_or_none(
                            row.get("unique_round_cap_excess_after_temp_zodiac_count")
                        ),
                    }
                )

    return {
        "source_key": source_key,
        "groups": len(by_group),
        "truth_unique_round_rows": truth_rows,
        "truth_source_semantics_rows": source_truth_rows,
        "candidate_rows": candidate_rows,
        "candidate_source_counts": dict(candidate_sources),
        "covered_unique_round_rows": covered_rows,
        "covered_source_semantics_rows": covered_source_rows,
        "missed_unique_round_rows": max(0, truth_rows - covered_rows),
        "false_positive_candidate_rows": false_positive_rows,
        "sample_limited_rows": sample_limited_rows,
        "unique_round_recall": _round_metric(_rate(covered_rows, truth_rows)),
        "source_semantics_recall": _round_metric(
            _rate(covered_source_rows, source_truth_rows)
        ),
        "candidate_precision": _round_metric(_rate(covered_rows, candidate_rows)),
        "candidate_rate": _round_metric(_rate(candidate_rows, len(seq))),
        "missed_examples": _missed_examples(
            missed,
            key_func=key_func,
            shapes_by_file=shapes_by_file,
            top=top,
        ),
    }


def _shape_counts(
    rows: Iterable[Mapping[str, Any]],
    *,
    shapes_by_file: Mapping[str, Mapping[str, Any]],
    truth_only: bool = False,
) -> dict[str, int]:
    selected = (
        row
        for row in rows
        if row.get("status") == "ok"
        and (not truth_only or _source_semantics_truth(row))
    )
    return _counter_dict((_shape_for_row(row, shapes_by_file) for row in selected), top=8)


def summarize_source_key_holdout(
    paths: Iterable[Path] = (),
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    source_shapes_by_file: Mapping[str, Mapping[str, Any]] | None = None,
    source_keys: Iterable[str] = DEFAULT_SOURCE_KEYS,
    folds: int = 5,
    min_train_sessions: int = 4,
    top: int = 8,
) -> dict[str, Any]:
    selected_paths = tuple(paths) or (DEFAULT_SAMPLE_ROOT,)
    source_rows = tuple(rows) if rows is not None else _rows_for_paths(paths)
    source_shape_errors: list[str] = []
    if source_shapes_by_file is None:
        source_shapes_by_file = _action_payload_shapes_by_file(
            selected_paths,
            files=(_base_file(row) for row in source_rows if row.get("status") == "ok"),
            errors=source_shape_errors,
        )
    invalid_keys = [key for key in source_keys if key not in SOURCE_KEY_CHOICES]
    if invalid_keys:
        raise ValueError(f"unsupported source key(s): {','.join(invalid_keys)}")

    ok_rows = tuple(row for row in source_rows if row.get("status") == "ok")
    results = [
        _evaluate_source_key(
            ok_rows,
            source_key=key,
            shapes_by_file=source_shapes_by_file,
            folds=folds,
            min_train_sessions=min_train_sessions,
            top=top,
        )
        for key in source_keys
    ]
    return {
        "sessions": len(ok_rows),
        "folds": folds,
        "min_train_sessions": min_train_sessions,
        "source_shape_parse_errors": len(source_shape_errors),
        "source_shape_error_examples": source_shape_errors[:top],
        "source_shape_counts": _shape_counts(
            ok_rows,
            shapes_by_file=source_shapes_by_file,
        ),
        "truth_source_shape_counts": _shape_counts(
            ok_rows,
            shapes_by_file=source_shapes_by_file,
            truth_only=True,
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
                str(row["source_key"]),
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
                f"source_shapes={_format_counts(result['source_shape_counts'])}",
                f"truth_source_shapes={_format_counts(result['truth_source_shape_counts'])}",
                f"truth_excess={_format_summary(result['truth_unique_round_excess_after_temp'])}",
                f"source_shape_parse_errors={result['source_shape_parse_errors']}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"source_key={row['source_key']}",
                    f"groups={row['groups']}",
                    f"truth_unique={row['truth_unique_round_rows']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"candidate_sources={_format_counts(row['candidate_source_counts'])}",
                    f"covered={row['covered_unique_round_rows']}",
                    f"missed={row['missed_unique_round_rows']}",
                    f"false_positive={row['false_positive_candidate_rows']}",
                    f"sample_limited={row['sample_limited_rows']}",
                    f"recall={row['unique_round_recall']}",
                    f"precision={row['candidate_precision']}",
                    f"candidate_rate={row['candidate_rate']}",
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
                        f"group={example['group']}",
                        f"fold={example['fold']}",
                        f"train_sessions={example['train_sessions']}",
                        f"train_source={example['train_source_semantics_rows']}",
                        f"shape={example['source_shape']}",
                        f"signature={example['source_shape_signature']}",
                        f"context={example['source_context_class']}",
                        f"mechanism={example['mechanism_class']}",
                        f"excess={example['unique_round_excess_after_temp']}",
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 CSE source-key holdout candidates.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--source-key",
        action="append",
        choices=SOURCE_KEY_CHOICES,
        help="Source key to evaluate. Repeat to compare a subset.",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-sessions", type=int, default=4)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)
    result = summarize_source_key_holdout(
        args.paths,
        source_keys=tuple(args.source_key or DEFAULT_SOURCE_KEYS),
        folds=args.folds,
        min_train_sessions=args.min_train_sessions,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
