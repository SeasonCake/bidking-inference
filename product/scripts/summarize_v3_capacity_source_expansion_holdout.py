"""Cross-validate v3 capacity/source expansion evidence by session holdout."""

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

from summarize_v3_settlement_source_semantics_audit import (  # noqa: E402
    _base_rows,
    _counter_dict,
    _format_counts,
    _format_summary,
    _mechanism_class,
    _numeric_summary,
    _safe_int,
    _source_diagnostic_for_path,
    _source_context_class,
    _source_evidence_class,
    load_monitor_tables,
)
from summarize_v3_settlement_count_prior_candidates import (  # noqa: E402
    _resolve_paths,
)

DEFAULT_GROUP_BY = "map_family"
GROUP_BY_CHOICES = (
    "map_id",
    "map_family",
    "session_token_prefix6",
    "map_id_capture_rounds",
    "map_id_round_index",
    "map_id_last_round_flag",
    "map_family_capture_rounds",
    "map_family_sub_pool_kind",
    "map_family_outer_shape",
    "map_family_payload_shape",
    "map_family_action_count",
    "map_id_payload_shape",
)
SOURCE_SEMANTICS_MECHANISMS = frozenset(
    {
        "server_side_settlement_expansion",
        "session_capacity_source_semantics",
    }
)


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


def _round_metric(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _session_id(row: Mapping[str, Any]) -> str:
    for key in (
        "session_id",
        "file",
        "path",
        "session_token_prefix8",
        "session_token_prefix6",
    ):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "unknown"


def _group_value(row: Mapping[str, Any], group_by: str) -> str:
    if group_by == "map_id_capture_rounds":
        return (
            f"{row.get('map_id') or 'none'}"
            f"|capture_rounds={row.get('capture_rounds') or 'none'}"
        )
    if group_by == "map_id_round_index":
        return (
            f"{row.get('map_id') or 'none'}"
            f"|round_index={row.get('round_index') or 'none'}"
        )
    if group_by == "map_id_last_round_flag":
        capture_rounds = _float_or_none(row.get("capture_rounds"))
        round_index = _float_or_none(row.get("round_index"))
        if capture_rounds is None or round_index is None:
            flag = "unknown"
        elif round_index >= capture_rounds:
            flag = "last"
        else:
            flag = "not_last"
        return f"{row.get('map_id') or 'none'}|last_round={flag}"
    if group_by == "map_family_capture_rounds":
        return (
            f"{row.get('map_family') or 'none'}"
            f"|capture_rounds={row.get('capture_rounds') or 'none'}"
        )
    if group_by == "map_family_sub_pool_kind":
        return (
            f"{row.get('map_family') or 'none'}"
            f"|sub_pool={row.get('bidmap_sub_pool_kind') or 'none'}"
        )
    if group_by == "map_family_outer_shape":
        return (
            f"{row.get('map_family') or 'none'}"
            f"|outer={row.get('settlement_outer_field_shape') or 'none'}"
        )
    if group_by == "map_family_payload_shape":
        return (
            f"{row.get('map_family') or 'none'}"
            f"|payload={row.get('payload_field_shape') or 'none'}"
        )
    if group_by == "map_family_action_count":
        return (
            f"{row.get('map_family') or 'none'}"
            f"|actions={row.get('event_action_result_count_all') or 'none'}"
        )
    if group_by == "map_id_payload_shape":
        return (
            f"{row.get('map_id') or 'none'}"
            f"|payload={row.get('payload_field_shape') or 'none'}"
        )
    value = row.get(group_by)
    return str(value) if value not in (None, "") else "none"


def _unique_round_overflow(row: Mapping[str, Any]) -> bool:
    value = _float_or_none(
        row.get("unique_round_cap_excess_after_temp_zodiac_count")
    )
    return value is not None and value > 0.0


def _payload_inventory_mismatch(row: Mapping[str, Any]) -> bool:
    raw_delta = _float_or_none(row.get("raw_candidate_inventory_delta"))
    occupied_delta = _float_or_none(row.get("occupied_slot_inventory_delta"))
    return (raw_delta is not None and raw_delta != 0.0) or (
        occupied_delta is not None and occupied_delta != 0.0
    )


def _non_zodiac_missing(row: Mapping[str, Any]) -> bool:
    value = _float_or_none(row.get("non_zodiac_missing_from_drop_universe_count"))
    return value is not None and value > 0.0


def _source_semantics_truth(row: Mapping[str, Any]) -> bool:
    return (
        _unique_round_overflow(row)
        and not _non_zodiac_missing(row)
        and str(row.get("mechanism_class") or "") in SOURCE_SEMANTICS_MECHANISMS
    )


def _rows_for_paths(paths: Iterable[Path]) -> tuple[dict[str, Any], ...]:
    tables = load_monitor_tables()
    base_rows, errors = _base_rows(_resolve_paths(paths), tables=tables)
    if errors:
        raise RuntimeError(";".join(errors[:5]))

    out: list[dict[str, Any]] = []
    for row in base_rows:
        if row.get("status") != "ok":
            out.append(row)
            continue
        diag = _source_diagnostic_for_path(
            Path(str(row.get("path"))),
            inventory_count=_safe_int(row.get("inventory_count")),
        )
        enriched = {**row, **diag}
        enriched["source_evidence_class"] = _source_evidence_class(enriched, diag)
        enriched["source_context_class"] = _source_context_class(enriched, diag)
        enriched["mechanism_class"] = _mechanism_class(enriched, diag)
        out.append(enriched)
    return tuple(out)


def _eval_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_by: str,
    fallback_group_by: str | None,
    folds: int,
    min_train_sessions: int,
) -> tuple[dict[str, Any], ...]:
    seq = tuple(row for row in rows if row.get("status") == "ok")
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_fallback_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in seq:
        by_group[_group_value(row, group_by)].append(row)
        if fallback_group_by is not None:
            by_fallback_group[_group_value(row, fallback_group_by)].append(row)

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
            sample_limited = len(train_sessions) < int(min_train_sessions)
            train_source_rows = sum(
                1 for item in train if _source_semantics_truth(item)
            )
            candidate_source = (
                "primary"
                if not sample_limited and train_source_rows > 0
                else "none"
            )
            fallback_group = None
            fallback_train_sessions = 0
            fallback_train_source_rows = 0
            fallback_sample_limited = False
            if candidate_source == "none" and fallback_group_by is not None:
                fallback_group = _group_value(row, fallback_group_by)
                fallback_seq = tuple(by_fallback_group.get(fallback_group, ()))
                fallback_train = tuple(
                    other
                    for other in fallback_seq
                    if _stable_fold(_session_id(other), folds) != fold
                )
                fallback_sessions = {_session_id(item) for item in fallback_train}
                fallback_train_sessions = len(fallback_sessions)
                fallback_sample_limited = fallback_train_sessions < int(
                    min_train_sessions
                )
                fallback_train_source_rows = sum(
                    1 for item in fallback_train if _source_semantics_truth(item)
                )
                if (
                    not fallback_sample_limited
                    and fallback_train_source_rows > 0
                ):
                    candidate_source = "fallback"
            candidate_applied = candidate_source != "none"
            truth_unique = _unique_round_overflow(row)
            truth_source = _source_semantics_truth(row)
            out.append(
                {
                    "group": group,
                    "fallback_group": fallback_group,
                    "session_id": session_id,
                    "fold": fold,
                    "sample_limited": (
                        sample_limited
                        if fallback_group_by is None
                        else sample_limited and fallback_sample_limited
                    ),
                    "train_sessions": len(train_sessions),
                    "train_unique_round_rows": sum(
                        1 for item in train if _unique_round_overflow(item)
                    ),
                    "train_source_semantics_rows": train_source_rows,
                    "fallback_train_sessions": fallback_train_sessions,
                    "fallback_train_source_semantics_rows": (
                        fallback_train_source_rows
                    ),
                    "train_mechanism_classes": _counter_dict(
                        (item.get("mechanism_class") for item in train),
                        top=8,
                    ),
                    "candidate_applied": candidate_applied,
                    "candidate_source": candidate_source,
                    "truth_unique_round_overflow": truth_unique,
                    "truth_source_semantics": truth_source,
                    "covered_unique_round_overflow": candidate_applied
                    and truth_unique,
                    "covered_source_semantics": candidate_applied and truth_source,
                    "false_positive_candidate": candidate_applied
                    and not truth_unique,
                    "payload_inventory_mismatch": _payload_inventory_mismatch(row),
                    "non_zodiac_missing": _non_zodiac_missing(row),
                    "mechanism_class": row.get("mechanism_class"),
                    "source_evidence_class": row.get("source_evidence_class"),
                    "source_context_class": row.get("source_context_class"),
                    "map_id": row.get("map_id"),
                    "map_family": row.get("map_family"),
                    "unique_round_excess_after_temp": _float_or_none(
                        row.get("unique_round_cap_excess_after_temp_zodiac_count")
                    ),
                    "example_file": row.get("file"),
                }
            )
    return tuple(out)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _candidate_status(row: Mapping[str, Any]) -> str:
    truth_rows = int(row.get("truth_unique_round_rows") or 0)
    if truth_rows <= 0:
        return "within_capacity_source_semantics_shadow_only"
    if int(row.get("truth_non_zodiac_missing_rows") or 0) > 0:
        return "blocked_external_overlay_shadow_only"
    if int(row.get("truth_payload_inventory_mismatch_rows") or 0) > 0:
        return "blocked_payload_mismatch_shadow_only"
    if int(row.get("sample_limited_rows") or 0) > 0:
        return "blocked_low_sample"
    if int(row.get("candidate_rows") or 0) <= 0:
        return "blocked_no_train_source_semantics"
    recall = _float_or_none(row.get("unique_round_recall"))
    if recall is None or recall < 0.80:
        return "blocked_holdout_under_recall"
    return "watch_capacity_source_expansion_holdout"


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
        truth_unique = sum(1 for item in seq if item.get("truth_unique_round_overflow"))
        truth_source = sum(1 for item in seq if item.get("truth_source_semantics"))
        candidate_rows = sum(1 for item in seq if item.get("candidate_applied"))
        covered_unique = sum(
            1 for item in seq if item.get("covered_unique_round_overflow")
        )
        covered_source = sum(1 for item in seq if item.get("covered_source_semantics"))
        false_positive = sum(1 for item in seq if item.get("false_positive_candidate"))
        truth_payload_mismatch = sum(
            1
            for item in seq
            if item.get("truth_unique_round_overflow")
            and item.get("payload_inventory_mismatch")
        )
        truth_non_zodiac_missing = sum(
            1
            for item in seq
            if item.get("truth_unique_round_overflow")
            and item.get("non_zodiac_missing")
        )
        row = {
            "group_by": group_by,
            "group": group,
            "rows": len(seq),
            "sessions": len({_session_id(item) for item in seq}),
            "train_sessions": _numeric_summary(
                item.get("train_sessions") for item in seq
            ),
            "sample_limited_rows": sum(
                1 for item in seq if item.get("sample_limited")
            ),
            "truth_unique_round_rows": truth_unique,
            "truth_source_semantics_rows": truth_source,
            "candidate_rows": candidate_rows,
            "candidate_source_counts": _counter_dict(
                (
                    item.get("candidate_source")
                    for item in seq
                    if item.get("candidate_applied")
                ),
                top=top,
            ),
            "covered_unique_round_rows": covered_unique,
            "covered_source_semantics_rows": covered_source,
            "missed_unique_round_rows": max(0, truth_unique - covered_unique),
            "false_positive_candidate_rows": false_positive,
            "unique_round_recall": _round_metric(_rate(covered_unique, truth_unique)),
            "source_semantics_recall": _round_metric(
                _rate(covered_source, truth_source)
            ),
            "candidate_precision": _round_metric(
                _rate(covered_unique, candidate_rows)
            ),
            "candidate_rate": _round_metric(_rate(candidate_rows, len(seq))),
            "payload_inventory_mismatch_rows": sum(
                1 for item in seq if item.get("payload_inventory_mismatch")
            ),
            "truth_payload_inventory_mismatch_rows": truth_payload_mismatch,
            "non_zodiac_missing_rows": sum(
                1 for item in seq if item.get("non_zodiac_missing")
            ),
            "truth_non_zodiac_missing_rows": truth_non_zodiac_missing,
            "mechanism_classes": _counter_dict(
                (item.get("mechanism_class") for item in seq),
                top=top,
            ),
            "truth_mechanism_classes": _counter_dict(
                (
                    item.get("mechanism_class")
                    for item in seq
                    if item.get("truth_unique_round_overflow")
                ),
                top=top,
            ),
            "source_evidence_classes": _counter_dict(
                (item.get("source_evidence_class") for item in seq),
                top=top,
            ),
            "source_context_classes": _counter_dict(
                (item.get("source_context_class") for item in seq),
                top=top,
            ),
            "truth_source_evidence_classes": _counter_dict(
                (
                    item.get("source_evidence_class")
                    for item in seq
                    if item.get("truth_unique_round_overflow")
                ),
                top=top,
            ),
            "truth_source_context_classes": _counter_dict(
                (
                    item.get("source_context_class")
                    for item in seq
                    if item.get("truth_unique_round_overflow")
                ),
                top=top,
            ),
            "map_ids": _counter_dict((item.get("map_id") for item in seq), top=top),
            "unique_round_excess_after_temp": _numeric_summary(
                item.get("unique_round_excess_after_temp") for item in seq
            ),
            "examples": [
                str(item.get("example_file"))
                for item in sorted(
                    seq,
                    key=lambda value: -float(
                        value.get("unique_round_excess_after_temp") or 0.0
                    ),
                )[:3]
            ],
        }
        row["candidate_status"] = _candidate_status(row)
        out.append(row)
    return sorted(
        out,
        key=lambda item: (
            0
            if item["candidate_status"]
            == "watch_capacity_source_expansion_holdout"
            else 1,
            -int(item["truth_unique_round_rows"]),
            -int(item["candidate_rows"]),
            str(item["group"]),
        ),
    )


def _missed_examples(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> list[dict[str, Any]]:
    selected = sorted(
        (
            row
            for row in rows
            if row.get("truth_unique_round_overflow")
            and not row.get("covered_unique_round_overflow")
        ),
        key=lambda item: (
            -float(item.get("unique_round_excess_after_temp") or 0.0),
            str(item.get("map_id") or ""),
            str(item.get("example_file") or ""),
        ),
    )[:top]
    return [
        {
            "file": row.get("example_file"),
            "map_id": row.get("map_id"),
            "map_family": row.get("map_family"),
            "group": row.get("group"),
            "fallback_group": row.get("fallback_group"),
            "fold": row.get("fold"),
            "train_sessions": row.get("train_sessions"),
            "train_source_semantics_rows": row.get("train_source_semantics_rows"),
            "fallback_train_sessions": row.get("fallback_train_sessions"),
            "fallback_train_source_semantics_rows": row.get(
                "fallback_train_source_semantics_rows"
            ),
            "source_evidence_class": row.get("source_evidence_class"),
            "source_context_class": row.get("source_context_class"),
            "mechanism_class": row.get("mechanism_class"),
            "unique_round_excess_after_temp": row.get(
                "unique_round_excess_after_temp"
            ),
        }
        for row in selected
    ]


def summarize_holdout(
    paths: Iterable[Path] = (),
    *,
    group_by: str = DEFAULT_GROUP_BY,
    fallback_group_by: str | None = None,
    folds: int = 5,
    min_train_sessions: int = 4,
    top: int = 12,
    rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"unsupported group_by: {group_by}")
    if fallback_group_by is not None and fallback_group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"unsupported fallback_group_by: {fallback_group_by}")
    source_rows = tuple(rows) if rows is not None else _rows_for_paths(paths)
    eval_rows = _eval_rows(
        source_rows,
        group_by=group_by,
        fallback_group_by=fallback_group_by,
        folds=folds,
        min_train_sessions=min_train_sessions,
    )
    groups = _summarize_eval_rows(eval_rows, group_by=group_by, top=top)
    truth_rows = sum(1 for row in eval_rows if row.get("truth_unique_round_overflow"))
    source_truth_rows = sum(
        1 for row in eval_rows if row.get("truth_source_semantics")
    )
    candidate_rows = sum(1 for row in eval_rows if row.get("candidate_applied"))
    covered_rows = sum(
        1 for row in eval_rows if row.get("covered_unique_round_overflow")
    )
    covered_source_rows = sum(
        1 for row in eval_rows if row.get("covered_source_semantics")
    )
    false_positive_rows = sum(
        1 for row in eval_rows if row.get("false_positive_candidate")
    )
    truth_payload_mismatch_rows = sum(
        1
        for row in eval_rows
        if row.get("truth_unique_round_overflow")
        and row.get("payload_inventory_mismatch")
    )
    truth_non_zodiac_missing_rows = sum(
        1
        for row in eval_rows
        if row.get("truth_unique_round_overflow") and row.get("non_zodiac_missing")
    )
    return {
        "group_by": group_by,
        "fallback_group_by": fallback_group_by,
        "folds": folds,
        "min_train_sessions": min_train_sessions,
        "sessions": len(eval_rows),
        "groups": len(groups),
        "truth_unique_round_rows": truth_rows,
        "truth_source_semantics_rows": source_truth_rows,
        "candidate_rows": candidate_rows,
        "candidate_source_counts": _counter_dict(
            (
                row.get("candidate_source")
                for row in eval_rows
                if row.get("candidate_applied")
            ),
            top=top,
        ),
        "covered_unique_round_rows": covered_rows,
        "covered_source_semantics_rows": covered_source_rows,
        "missed_unique_round_rows": max(0, truth_rows - covered_rows),
        "false_positive_candidate_rows": false_positive_rows,
        "sample_limited_rows": sum(1 for row in eval_rows if row.get("sample_limited")),
        "payload_inventory_mismatch_rows": sum(
            1 for row in eval_rows if row.get("payload_inventory_mismatch")
        ),
        "truth_payload_inventory_mismatch_rows": truth_payload_mismatch_rows,
        "non_zodiac_missing_rows": sum(
            1 for row in eval_rows if row.get("non_zodiac_missing")
        ),
        "truth_non_zodiac_missing_rows": truth_non_zodiac_missing_rows,
        "unique_round_recall": _round_metric(_rate(covered_rows, truth_rows)),
        "source_semantics_recall": _round_metric(
            _rate(covered_source_rows, source_truth_rows)
        ),
        "candidate_precision": _round_metric(_rate(covered_rows, candidate_rows)),
        "candidate_rate": _round_metric(_rate(candidate_rows, len(eval_rows))),
        "mechanism_classes": _counter_dict(
            (row.get("mechanism_class") for row in eval_rows),
            top=top,
        ),
        "truth_mechanism_classes": _counter_dict(
            (
                row.get("mechanism_class")
                for row in eval_rows
                if row.get("truth_unique_round_overflow")
            ),
            top=top,
        ),
        "source_evidence_classes": _counter_dict(
            (row.get("source_evidence_class") for row in eval_rows),
            top=top,
        ),
        "source_context_classes": _counter_dict(
            (row.get("source_context_class") for row in eval_rows),
            top=top,
        ),
        "truth_source_evidence_classes": _counter_dict(
            (
                row.get("source_evidence_class")
                for row in eval_rows
                if row.get("truth_unique_round_overflow")
            ),
            top=top,
        ),
        "truth_source_context_classes": _counter_dict(
            (
                row.get("source_context_class")
                for row in eval_rows
                if row.get("truth_unique_round_overflow")
            ),
            top=top,
        ),
        "missed_examples": _missed_examples(eval_rows, top=top),
        "status_counts": dict(
            sorted(Counter(row["candidate_status"] for row in groups).items())
        ),
        "rows": groups,
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"sessions={result['sessions']}",
                f"group_by={result['group_by']}",
                f"fallback_group_by={result['fallback_group_by'] or '-'}",
                f"groups={result['groups']}",
                f"folds={result['folds']}",
                f"min_train_sessions={result['min_train_sessions']}",
                f"truth_unique_round_rows={result['truth_unique_round_rows']}",
                f"truth_source_semantics_rows={result['truth_source_semantics_rows']}",
                f"candidate_rows={result['candidate_rows']}",
                f"candidate_sources={_format_counts(result['candidate_source_counts'])}",
                f"covered_unique_round_rows={result['covered_unique_round_rows']}",
                f"missed_unique_round_rows={result['missed_unique_round_rows']}",
                f"false_positive_candidate_rows={result['false_positive_candidate_rows']}",
                f"sample_limited_rows={result['sample_limited_rows']}",
                f"payload_mismatch_rows={result['payload_inventory_mismatch_rows']}",
                f"truth_payload_mismatch_rows={result['truth_payload_inventory_mismatch_rows']}",
                f"non_zodiac_missing_rows={result['non_zodiac_missing_rows']}",
                f"truth_non_zodiac_missing_rows={result['truth_non_zodiac_missing_rows']}",
                f"unique_round_recall={result['unique_round_recall']}",
                f"source_semantics_recall={result['source_semantics_recall']}",
                f"candidate_precision={result['candidate_precision']}",
                f"candidate_rate={result['candidate_rate']}",
                f"mechanisms={_format_counts(result['truth_mechanism_classes'])}",
                f"evidence={_format_counts(result['truth_source_evidence_classes'])}",
                f"context={_format_counts(result['truth_source_context_classes'])}",
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
                    f"truth_unique={row['truth_unique_round_rows']}",
                    f"candidate_rows={row['candidate_rows']}",
                    f"candidate_sources={_format_counts(row['candidate_source_counts'])}",
                    f"covered={row['covered_unique_round_rows']}",
                    f"missed={row['missed_unique_round_rows']}",
                    f"false_positive={row['false_positive_candidate_rows']}",
                    f"sample_limited={row['sample_limited_rows']}",
                    f"recall={row['unique_round_recall']}",
                    f"precision={row['candidate_precision']}",
                    f"mechanisms={_format_counts(row['truth_mechanism_classes'])}",
                    f"evidence={_format_counts(row['truth_source_evidence_classes'])}",
                    f"context={_format_counts(row['truth_source_context_classes'])}",
                    f"excess={_format_summary(row['unique_round_excess_after_temp'])}",
                    f"maps={_format_counts(row['map_ids'])}",
                    f"examples={','.join(row['examples'])}",
                )
            )
        )
    for example in result["missed_examples"][: min(top, 5)]:
        print(
            " ".join(
                (
                    "missed_example",
                    f"file={example['file']}",
                    f"map_id={example['map_id']}",
                    f"group={example['group']}",
                    f"fold={example['fold']}",
                    f"train_sessions={example['train_sessions']}",
                    f"train_source={example['train_source_semantics_rows']}",
                    f"fallback_group={example['fallback_group'] or '-'}",
                    f"fallback_train_source={example['fallback_train_source_semantics_rows']}",
                    f"evidence={example['source_evidence_class']}",
                    f"context={example['source_context_class']}",
                    f"mechanism={example['mechanism_class']}",
                    f"excess={example['unique_round_excess_after_temp']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-validate v3 capacity/source expansion evidence.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--group-by",
        choices=GROUP_BY_CHOICES,
        default=DEFAULT_GROUP_BY,
    )
    parser.add_argument(
        "--fallback-group-by",
        choices=GROUP_BY_CHOICES,
        help=(
            "Optional fallback grouping used only when the primary group has no "
            "train source-semantics support."
        ),
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-sessions", type=int, default=4)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_holdout(
        args.paths,
        group_by=args.group_by,
        fallback_group_by=args.fallback_group_by,
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
