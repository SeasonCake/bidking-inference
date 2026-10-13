"""Cross-validate v3 settlement count-prior shadow evidence by session holdout."""

from __future__ import annotations

import argparse
import hashlib
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

from summarize_v3_settlement_count_prior_candidates import (  # noqa: E402
    _audit_file,
    _counter_dict,
    _numeric_summary,
    _resolve_paths,
    load_monitor_tables,
)


DEFAULT_GROUP_BY = "map_id"


def _stable_fold(value: Any, folds: int) -> int:
    if folds <= 1:
        return 0
    digest = hashlib.sha1(str(value or "unknown").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _round_metric(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _percentile(values: Iterable[Any], quantile: float) -> float | None:
    seq = sorted(
        value
        for value in (_float_or_none(item) for item in values)
        if value is not None
    )
    if not seq:
        return None
    index = min(
        len(seq) - 1,
        max(0, int(round((len(seq) - 1) * float(quantile)))),
    )
    return seq[index]


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _coverage_rate(rows: Iterable[Mapping[str, Any]], pred_key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get("truth_non_temp_count"))
        if pred is not None and truth is not None:
            values.append(1.0 if truth <= pred else 0.0)
    return _mean(values)


def _negative_rows(rows: Iterable[Mapping[str, Any]], field: str) -> int:
    out = 0
    for row in rows:
        value = _float_or_none(row.get(field))
        if value is not None and value < 0.0:
            out += 1
    return out


def _session_id(row: Mapping[str, Any]) -> str:
    value = row.get("session_id") or row.get("file") or row.get("path")
    return str(value or "unknown")


def _group_value(row: Mapping[str, Any], group_by: str) -> str:
    value = row.get(group_by)
    return str(value) if value not in (None, "") else "none"


def _candidate_status(row: Mapping[str, Any]) -> str:
    if int(row.get("missing_table_rows") or 0) > 0:
        return "missing_table_shadow_only"
    if int(row.get("sample_limited_rows") or 0) > 0:
        return "blocked_low_sample"
    candidate_rows = int(row.get("candidate_rows") or 0)
    p95_coverage = _float_or_none(row.get("holdout_p95_coverage"))
    prior_coverage = _float_or_none(row.get("prior_max_coverage"))
    if candidate_rows <= 0:
        return "table_caps_cover_observed_shadow_only"
    if p95_coverage is None:
        return "blocked_low_sample"
    if prior_coverage is not None and p95_coverage < prior_coverage:
        return "blocked_holdout_regression"
    if p95_coverage < 0.80:
        return "blocked_holdout_under_coverage"
    return "watch_settlement_count_prior_candidate"


def _rows_for_paths(paths: Iterable[Path]) -> tuple[dict[str, Any], ...]:
    tables = load_monitor_tables()
    rows: list[dict[str, Any]] = []
    for path in _resolve_paths(paths):
        row = _audit_file(path, tables=tables)
        row["session_id"] = Path(path).stem
        rows.append(row)
    return tuple(rows)


def _eval_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_by: str,
    folds: int,
    min_train_sessions: int,
    quantile: float,
) -> tuple[dict[str, Any], ...]:
    seq = tuple(row for row in rows if row.get("status") == "ok")
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in seq:
        by_group[_group_value(row, group_by)].append(row)

    out: list[dict[str, Any]] = []
    for group, group_rows in by_group.items():
        group_seq = tuple(group_rows)
        for row in group_seq:
            session_id = _session_id(row)
            fold = _stable_fold(session_id, folds)
            train = tuple(
                other
                for other in group_seq
                if _stable_fold(_session_id(other), folds) != fold
            )
            train_sessions = {_session_id(item) for item in train}
            truth = _float_or_none(row.get("non_temp_inventory_count"))
            prior_max = _float_or_none(row.get("bidmap_items_per_session_max"))
            round_cap = _float_or_none(row.get("bidmap_raw_round_cap_max"))
            train_p95 = (
                _percentile(
                    (item.get("non_temp_inventory_count") for item in train),
                    quantile,
                )
                if len(train_sessions) >= int(min_train_sessions)
                else None
            )
            train_max = (
                max(
                    value
                    for value in (
                        _float_or_none(item.get("non_temp_inventory_count"))
                        for item in train
                    )
                    if value is not None
                )
                if len(train_sessions) >= int(min_train_sessions)
                and any(
                    _float_or_none(item.get("non_temp_inventory_count")) is not None
                    for item in train
                )
                else None
            )
            table_status = str(row.get("table_status") or "none")
            sample_limited = (
                table_status == "ok" and len(train_sessions) < int(min_train_sessions)
            )
            out.append(
                {
                    "group": group,
                    "session_id": session_id,
                    "fold": fold,
                    "table_status": table_status,
                    "sample_limited": sample_limited,
                    "train_sessions": len(train_sessions),
                    "truth_non_temp_count": truth,
                    "truth_inventory_count": _float_or_none(row.get("inventory_count")),
                    "known_temp_zodiac_count": _float_or_none(
                        row.get("known_temp_zodiac_count")
                    ),
                    "prior_max": prior_max,
                    "round_cap": round_cap,
                    "train_p95": train_p95,
                    "train_max": train_max,
                    "candidate_applied": (
                        prior_max is not None
                        and train_p95 is not None
                        and train_p95 > prior_max
                    ),
                    "prior_max_delta": (
                        prior_max - truth
                        if prior_max is not None and truth is not None
                        else None
                    ),
                    "round_cap_delta": (
                        round_cap - truth
                        if round_cap is not None and truth is not None
                        else None
                    ),
                    "p95_delta": (
                        train_p95 - truth
                        if train_p95 is not None and truth is not None
                        else None
                    ),
                    "max_delta": (
                        train_max - truth
                        if train_max is not None and truth is not None
                        else None
                    ),
                    "p95_lift_over_prior": (
                        train_p95 - prior_max
                        if train_p95 is not None and prior_max is not None
                        else None
                    ),
                    "example_file": row.get("file"),
                }
            )
    return tuple(out)


def _summarize_eval_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_by: str,
    top: int,
) -> list[dict[str, Any]]:
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[str(row.get("group") or "none")].append(row)

    out: list[dict[str, Any]] = []
    for group, seq_list in by_group.items():
        seq = tuple(seq_list)
        row = {
            "group_by": group_by,
            "group": group,
            "rows": len(seq),
            "sessions": len({_session_id(item) for item in seq}),
            "table_statuses": _counter_dict(
                (item.get("table_status") for item in seq),
                top=top,
            ),
            "truth_non_temp_count": _numeric_summary(
                item.get("truth_non_temp_count") for item in seq
            ),
            "train_sessions": _numeric_summary(
                item.get("train_sessions") for item in seq
            ),
            "train_p95": _numeric_summary(item.get("train_p95") for item in seq),
            "train_max": _numeric_summary(item.get("train_max") for item in seq),
            "prior_max": _numeric_summary(item.get("prior_max") for item in seq),
            "round_cap": _numeric_summary(item.get("round_cap") for item in seq),
            "prior_max_coverage": _round_metric(_coverage_rate(seq, "prior_max"), 6),
            "round_cap_coverage": _round_metric(_coverage_rate(seq, "round_cap"), 6),
            "holdout_p95_coverage": _round_metric(_coverage_rate(seq, "train_p95"), 6),
            "holdout_max_coverage": _round_metric(_coverage_rate(seq, "train_max"), 6),
            "prior_max_under_rows": _negative_rows(seq, "prior_max_delta"),
            "round_cap_under_rows": _negative_rows(seq, "round_cap_delta"),
            "holdout_p95_under_rows": _negative_rows(seq, "p95_delta"),
            "holdout_max_under_rows": _negative_rows(seq, "max_delta"),
            "candidate_rows": sum(1 for item in seq if item.get("candidate_applied")),
            "sample_limited_rows": sum(1 for item in seq if item.get("sample_limited")),
            "missing_table_rows": sum(
                1 for item in seq if item.get("table_status") == "missing_bidmap"
            ),
            "p95_delta": _numeric_summary(item.get("p95_delta") for item in seq),
            "p95_lift_over_prior": _numeric_summary(
                item.get("p95_lift_over_prior") for item in seq
            ),
            "examples": [
                str(item.get("example_file"))
                for item in sorted(
                    seq,
                    key=lambda value: -float(value.get("truth_non_temp_count") or 0.0),
                )[:3]
            ],
        }
        row["candidate_status"] = _candidate_status(row)
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            0
            if item["candidate_status"] == "watch_settlement_count_prior_candidate"
            else 1,
            -int(item["candidate_rows"]),
            -float(item["truth_non_temp_count"]["max"] or 0.0),
            str(item["group"]),
        ),
    )


def summarize_holdout(
    paths: Iterable[Path] = (),
    *,
    group_by: str = DEFAULT_GROUP_BY,
    folds: int = 5,
    min_train_sessions: int = 8,
    quantile: float = 0.95,
    top: int = 12,
    rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_rows = tuple(rows) if rows is not None else _rows_for_paths(paths)
    eval_rows = _eval_rows(
        source_rows,
        group_by=group_by,
        folds=folds,
        min_train_sessions=min_train_sessions,
        quantile=quantile,
    )
    groups = _summarize_eval_rows(eval_rows, group_by=group_by, top=top)
    return {
        "group_by": group_by,
        "folds": folds,
        "min_train_sessions": min_train_sessions,
        "quantile": quantile,
        "sessions": len(eval_rows),
        "candidate_rows": sum(int(row.get("candidate_rows") or 0) for row in groups),
        "sample_limited_rows": sum(
            int(row.get("sample_limited_rows") or 0) for row in groups
        ),
        "missing_table_rows": sum(
            int(row.get("missing_table_rows") or 0) for row in groups
        ),
        "status_counts": dict(
            sorted(Counter(row["candidate_status"] for row in groups).items())
        ),
        "overall": {
            "prior_max_coverage": _round_metric(
                _coverage_rate(eval_rows, "prior_max"),
                6,
            ),
            "round_cap_coverage": _round_metric(
                _coverage_rate(eval_rows, "round_cap"),
                6,
            ),
            "holdout_p95_coverage": _round_metric(
                _coverage_rate(eval_rows, "train_p95"),
                6,
            ),
            "holdout_max_coverage": _round_metric(
                _coverage_rate(eval_rows, "train_max"),
                6,
            ),
            "p95_delta": _numeric_summary(row.get("p95_delta") for row in eval_rows),
            "p95_lift_over_prior": _numeric_summary(
                row.get("p95_lift_over_prior") for row in eval_rows
            ),
        },
        "rows": groups,
    }


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/p95={summary['p95']}"
        f"/max={summary['max']}"
    )


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    overall = result["overall"]
    print(
        " ".join(
            (
                f"sessions={result['sessions']}",
                f"group_by={result['group_by']}",
                f"groups={len(result['rows'])}",
                f"folds={result['folds']}",
                f"min_train_sessions={result['min_train_sessions']}",
                f"quantile={result['quantile']}",
                f"candidate_rows={result['candidate_rows']}",
                f"sample_limited_rows={result['sample_limited_rows']}",
                f"missing_table_rows={result['missing_table_rows']}",
                f"prior_coverage={overall['prior_max_coverage']}",
                f"round_coverage={overall['round_cap_coverage']}",
                f"holdout_p95_coverage={overall['holdout_p95_coverage']}",
                f"holdout_max_coverage={overall['holdout_max_coverage']}",
                f"status_counts={_format_counts(result['status_counts'])}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"{row['group_by']}={row['group']}",
                    f"status={row['candidate_status']}",
                    f"sessions={row['sessions']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"sample_limited={row['sample_limited_rows']}",
                    f"missing_table={row['missing_table_rows']}",
                    f"table={_format_counts(row['table_statuses'])}",
                    f"truth={_format_summary(row['truth_non_temp_count'])}",
                    f"train_p95={_format_summary(row['train_p95'])}",
                    f"prior_coverage={row['prior_max_coverage']}",
                    f"round_coverage={row['round_cap_coverage']}",
                    f"p95_coverage={row['holdout_p95_coverage']}",
                    f"max_coverage={row['holdout_max_coverage']}",
                    f"p95_under={row['holdout_p95_under_rows']}/{row['rows']}",
                    f"p95_lift={_format_summary(row['p95_lift_over_prior'])}",
                    f"examples={','.join(row['examples'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate v3 settlement count-prior shadow evidence.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--group-by",
        choices=("map_id", "map_prefix3"),
        default=DEFAULT_GROUP_BY,
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-sessions", type=int, default=8)
    parser.add_argument("--quantile", type=float, default=0.95)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_holdout(
        args.paths,
        group_by=args.group_by,
        folds=args.folds,
        min_train_sessions=args.min_train_sessions,
        quantile=args.quantile,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
