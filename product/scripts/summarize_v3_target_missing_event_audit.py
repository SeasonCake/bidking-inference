"""Audit v3 target-missing prior-stress rows against evidence events.

This script is shadow-only diagnostics. It replays the pre-bid Fatbeans prefix
for rows already selected by the v3 prior robustness audit and reports which
event targets, anchors, and payload fields are present or absent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bidking_lab.inference.v3 import (  # noqa: E402
    compile_feasible_summary,
    compile_hard_constraints,
    events_from_fatbeans,
)
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_calibration_path,
    _default_paths,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    _events_before_sort,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_prior_robustness_audit import (  # noqa: E402
    SUMMARY_COMPONENTS,
    _component_issue_label,
    summarize_prior_stress_details,
)


DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
DEFAULT_BUCKET = "evidence_floor_only"
KEY_TARGETS: tuple[str, ...] = (
    "session.total_count",
    "session.total_cells",
    "bucket.q6.count",
    "bucket.q6.cells",
    "bucket.q6.value",
)
NON_Q6_QUALITIES: tuple[int, ...] = (1, 2, 3, 4, 5)
Q6_RESIDUAL_FIELDS: tuple[str, ...] = ("count", "cells", "value")
_SORT_RE = re.compile(r"#prebid_r(?P<round>\d+)_sort(?P<sort>\d+)")


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _counter_dict(values: Iterable[Any], *, top: int = 12) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _merge_counter_dicts(
    values: Iterable[Mapping[str, Any]],
    *,
    top: int = 12,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        for key, count in value.items():
            parsed = _safe_int(count)
            if parsed is not None:
                counts[str(key)] += parsed
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _numeric_values(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            out.append(float(value))
        except (TypeError, ValueError, OverflowError):
            continue
    return tuple(out)


def _numeric_summary(values: Iterable[Any], *, digits: int = 3) -> dict[str, Any]:
    seq = _numeric_values(values)
    if not seq:
        return {"n": 0, "avg": None, "max": None}
    return {
        "n": len(seq),
        "avg": round(sum(seq) / len(seq), digits),
        "max": round(max(seq), digits),
    }


def _strip_row_file_ref(file_ref: Any) -> str:
    return str(file_ref or "").split("#", 1)[0]


def _sort_id_from_file_ref(file_ref: Any) -> int | None:
    match = _SORT_RE.search(str(file_ref or ""))
    if match is None:
        return None
    return _safe_int(match.group("sort"))


def _resolve_capture_path(file_ref: Any, sample_root: Path) -> Path | None:
    raw = _strip_row_file_ref(file_ref)
    if not raw:
        return None
    path = Path(raw)
    candidates = (
        path,
        ROOT / path,
        sample_root / path.name,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _target_missing_components(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        component
        for component in SUMMARY_COMPONENTS
        if _component_issue_label(row.get(component, {})) == "target_missing"
    )


def _selected_target_missing_details(
    details: Iterable[Mapping[str, Any]],
    *,
    selected_bucket: str,
    selected_map_id: int | None,
) -> tuple[Mapping[str, Any], ...]:
    out: list[Mapping[str, Any]] = []
    for row in details:
        missing = _target_missing_components(row)
        if not missing:
            continue
        if selected_bucket != "all" and row.get("consistency_bucket") != selected_bucket:
            continue
        if selected_map_id is not None and _safe_int(row.get("map_id")) != selected_map_id:
            continue
        out.append(row)
    return tuple(out)


def _event_targets(event: Any) -> tuple[str, ...]:
    return tuple(str(target) for target in (getattr(event, "targets", ()) or ()))


def _has_any_target(event: Any, targets: set[str]) -> bool:
    return any(target in targets for target in _event_targets(event))


def _iter_payload_items(event: Any) -> Iterable[Mapping[str, Any]]:
    payload = getattr(event, "payload", None) or {}
    for item in payload.get("items", ()) or ():
        if isinstance(item, Mapping):
            yield item


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _quality(item: Mapping[str, Any]) -> int | None:
    return _safe_int(item.get("quality"))


def _anchor_summary(anchors: Iterable[Any], *, has_value_field: bool) -> dict[str, int]:
    seq = tuple(anchors)
    q6 = tuple(anchor for anchor in seq if _safe_int(getattr(anchor, "quality", None)) == 6)
    return {
        "count": len(seq),
        "with_quality": sum(1 for anchor in seq if getattr(anchor, "quality", None) is not None),
        "with_shape": sum(1 for anchor in seq if getattr(anchor, "shape_key", None) is not None),
        "with_cells": sum(1 for anchor in seq if getattr(anchor, "cells", None) is not None),
        "with_value": (
            sum(1 for anchor in seq if getattr(anchor, "value", None) is not None)
            if has_value_field
            else 0
        ),
        "q6_count": len(q6),
        "q6_with_cells": sum(1 for anchor in q6 if getattr(anchor, "cells", None) is not None),
        "q6_with_value": (
            sum(1 for anchor in q6 if getattr(anchor, "value", None) is not None)
            if has_value_field
            else 0
        ),
    }


def _payload_item_summary(events: Iterable[Any]) -> dict[str, int]:
    anchor_targets = {"item_anchors", "category_anchors", "shape_anchors", "quality_floors"}
    items = tuple(
        item
        for event in events
        if _has_any_target(event, anchor_targets)
        for item in _iter_payload_items(event)
    )
    return _payload_items_summary(items)


def _payload_items_summary(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    seq = tuple(items)
    q6_items = tuple(item for item in seq if _quality(item) == 6)
    return {
        "items": len(seq),
        "with_quality": sum(1 for item in seq if _has_value(item.get("quality"))),
        "with_shape": sum(
            1
            for item in seq
            if _has_value(item.get("shape_key")) or _has_value(item.get("shape_code"))
        ),
        "with_cells": sum(1 for item in seq if _has_value(item.get("cells"))),
        "with_value": sum(1 for item in seq if _has_value(item.get("value"))),
        "q6_items": len(q6_items),
        "q6_with_shape": sum(
            1
            for item in q6_items
            if _has_value(item.get("shape_key")) or _has_value(item.get("shape_code"))
        ),
        "q6_with_cells": sum(1 for item in q6_items if _has_value(item.get("cells"))),
        "q6_with_value": sum(1 for item in q6_items if _has_value(item.get("value"))),
    }


def _constraint_anchor_summary(constraints: Any) -> dict[str, dict[str, int]]:
    return {
        "item_anchors": _anchor_summary(
            getattr(constraints, "item_anchors", {}).values(),
            has_value_field=True,
        ),
        "shape_anchors": _anchor_summary(
            getattr(constraints, "shape_anchors", {}).values(),
            has_value_field=False,
        ),
        "quality_floor_anchors": _anchor_summary(
            getattr(constraints, "quality_floor_anchors", {}).values(),
            has_value_field=False,
        ),
    }


def _summary_fields(summary: Any) -> dict[str, Any]:
    q6 = summary.bucket(6) if summary is not None else None
    return {
        "session_total_count_exact": getattr(summary, "session_total_count_exact", None),
        "session_total_cells_exact": getattr(summary, "session_total_cells_exact", None),
        "known_count_floor": getattr(summary, "known_count_floor", None),
        "known_cells_floor": getattr(summary, "known_cells_floor", None),
        "known_value_floor": getattr(summary, "known_value_floor", None),
        "q6_count_exact": q6.count_exact if q6 is not None else None,
        "q6_cells_exact": q6.cells_exact if q6 is not None else None,
        "q6_value_exact": q6.value_exact if q6 is not None else None,
        "q6_count_floor": q6.count_floor if q6 is not None else 0,
        "q6_cells_floor": q6.cells_floor if q6 is not None else 0,
        "q6_value_floor": q6.value_floor if q6 is not None else 0,
    }


def _bucket_exact(summary: Any, quality: int, field: str) -> int | None:
    bucket = summary.bucket(int(quality)) if summary is not None else None
    if bucket is None:
        return None
    return _safe_int(getattr(bucket, f"{field}_exact", None))


def _q6_explicit_exact(summary: Any, field: str) -> int | None:
    return _bucket_exact(summary, 6, field)


def _session_total_exact(
    numeric: Mapping[str, Any],
    summary: Any,
    field: str,
) -> int | None:
    if field == "count":
        return _safe_int(getattr(summary, "session_total_count_exact", None))
    if field == "cells":
        return _safe_int(getattr(summary, "session_total_cells_exact", None))
    if field == "value":
        constraint = numeric.get("session.total_value")
        return _safe_int(getattr(constraint, "value", None))
    return None


def _q6_residual_field_candidate(
    numeric: Mapping[str, Any],
    summary: Any,
    field: str,
) -> dict[str, Any]:
    explicit = _q6_explicit_exact(summary, field)
    total = _session_total_exact(numeric, summary, field)
    exact_by_quality = {
        quality: _bucket_exact(summary, quality, field)
        for quality in NON_Q6_QUALITIES
    }
    missing_qualities = tuple(
        quality
        for quality, value in exact_by_quality.items()
        if value is None
    )
    non_q6_sum = (
        sum(int(value) for value in exact_by_quality.values() if value is not None)
        if not missing_qualities
        else None
    )
    out: dict[str, Any] = {
        "status": "missing_total_exact",
        "value": None,
        "total_exact": total,
        "non_q6_exact_sum": non_q6_sum,
        "missing_non_q6_qualities": list(missing_qualities),
        "q6_explicit_exact": explicit,
    }
    if explicit is not None:
        out["status"] = "already_explicit"
        out["value"] = explicit
        return out
    if total is None:
        return out
    if missing_qualities:
        out["status"] = "missing_non_q6_exact"
        return out
    residual = int(total) - int(non_q6_sum or 0)
    if residual < 0:
        out["status"] = "negative_residual"
        out["value"] = residual
        return out
    out["status"] = "derived"
    out["value"] = residual
    return out


def _q6_residual_target_candidate(
    numeric: Mapping[str, Any],
    summary: Any,
) -> dict[str, Any]:
    fields = {
        field: _q6_residual_field_candidate(numeric, summary, field)
        for field in Q6_RESIDUAL_FIELDS
    }
    return {
        **fields,
        "derived_fields": [
            field for field, row in fields.items() if row.get("status") == "derived"
        ],
    }


def _with_q6_residual_truth(
    candidate: Mapping[str, Any],
    detail: Mapping[str, Any],
    source_row: Mapping[str, Any],
) -> dict[str, Any]:
    out = {key: (dict(value) if isinstance(value, Mapping) else value) for key, value in candidate.items()}
    truth_by_field = {
        "count": _safe_int(source_row.get("v3_truth_q6_count")),
        "cells": _safe_int(detail.get("q6_cells", {}).get("truth")),
        "value": _safe_int(detail.get("q6_value", {}).get("truth")),
    }
    for field, truth in truth_by_field.items():
        row = out.get(field)
        if not isinstance(row, dict):
            continue
        value = _safe_int(row.get("value"))
        row["truth"] = truth
        row["truth_delta"] = value - truth if value is not None and truth is not None else None
    return out


def _event_diagnostics(events: Iterable[Any], constraints: Any, summary: Any) -> dict[str, Any]:
    seq = tuple(events)
    target_counts = Counter(target for event in seq for target in _event_targets(event))
    anchor_events = tuple(
        event
        for event in seq
        if _has_any_target(
            event,
            {"item_anchors", "category_anchors", "shape_anchors", "quality_floors"},
        )
    )
    numeric = getattr(constraints, "numeric", {}) or {}
    numeric_targets = tuple(sorted(numeric))
    return {
        "event_count": len(seq),
        "event_target_counts": dict(sorted(target_counts.items())),
        "anchor_event_source_counts": _counter_dict(
            getattr(event, "source_kind", None) for event in anchor_events
        ),
        "anchor_event_source_id_counts": _counter_dict(
            f"{getattr(event, 'source_kind', None)}:{getattr(event, 'source_id', None)}"
            for event in anchor_events
        ),
        "anchor_event_semantic_counts": _counter_dict(
            getattr(event, "semantic", None) for event in anchor_events
        ),
        "payload_item_summary": _payload_item_summary(anchor_events),
        "numeric_targets": list(numeric_targets),
        "numeric_target_values": {
            target: getattr(numeric[target], "value", None) for target in numeric_targets
        },
        "numeric_target_sources": {
            target: (
                f"{getattr(numeric[target], 'source_kind', None)}:"
                f"{getattr(numeric[target], 'source_id', None)}"
            )
            for target in numeric_targets
        },
        "key_target_presence": {
            target: target in numeric_targets for target in KEY_TARGETS
        },
        "key_event_details": _key_event_details(seq),
        "summary_fields": _summary_fields(summary),
        "constraint_anchor_summary": _constraint_anchor_summary(constraints),
        "q6_residual_target_candidate": _q6_residual_target_candidate(
            numeric,
            summary,
        ),
    }


def _key_event_details(events: Iterable[Any], *, top: int = 16) -> list[dict[str, Any]]:
    interesting_targets = {
        *KEY_TARGETS,
        "item_anchors",
        "category_anchors",
        "shape_anchors",
        "quality_floors",
    }
    out: list[dict[str, Any]] = []
    for event in events:
        targets = _event_targets(event)
        if not any(target in interesting_targets for target in targets):
            continue
        out.append(
            {
                "event_id": getattr(event, "event_id", None),
                "source": (
                    f"{getattr(event, 'source_kind', None)}:"
                    f"{getattr(event, 'source_id', None)}"
                ),
                "semantic": getattr(event, "semantic", None),
                "strength": getattr(event, "strength", None),
                "targets": list(targets),
                "payload_items": _payload_items_summary(_iter_payload_items(event)),
            }
        )
    return out[:top]


def _audit_detail_row(
    detail: Mapping[str, Any],
    source_row: Mapping[str, Any],
    *,
    sample_root: Path,
) -> dict[str, Any]:
    file_ref = detail.get("file")
    path = _resolve_capture_path(file_ref, sample_root)
    if path is None:
        raise FileNotFoundError(str(file_ref or ""))
    sort_id = _safe_int(source_row.get("bid_sort_id")) or _sort_id_from_file_ref(file_ref)
    if sort_id is None:
        raise ValueError(f"missing bid sort id for {file_ref}")
    events = parse_fatbeans_capture(path)
    prefix = _events_before_sort(events, sort_id)
    evidence_events = events_from_fatbeans(prefix)
    constraints = compile_hard_constraints(evidence_events)
    summary = compile_feasible_summary(constraints)
    diagnostics = _event_diagnostics(evidence_events, constraints, summary)
    missing = _target_missing_components(detail)
    diagnostics["q6_residual_target_candidate"] = _with_q6_residual_truth(
        diagnostics["q6_residual_target_candidate"],
        detail,
        source_row,
    )
    return {
        "file": file_ref,
        "path": str(path),
        "round": detail.get("round"),
        "bid_sort_id": sort_id,
        "session_id": detail.get("session_id"),
        "hero": detail.get("hero"),
        "map_id": detail.get("map_id"),
        "hero_map_evidence_profile": detail.get("hero_map_evidence_profile"),
        "consistency_bucket": detail.get("consistency_bucket"),
        "missing_components": list(missing),
        "missing_component_pattern": "+".join(missing) if missing else "none",
        "source_total_cells": detail.get("total_cells", {}).get("source"),
        "source_q6_cells": detail.get("q6_cells", {}).get("source"),
        "source_total_value": detail.get("total_value", {}).get("source"),
        "source_q6_value": detail.get("q6_value", {}).get("source"),
        **diagnostics,
    }


def summarize_target_missing_event_audit(
    rows: Iterable[dict[str, Any]],
    *,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    selected_bucket: str = DEFAULT_BUCKET,
    selected_map_id: int | None = None,
    top: int = 12,
) -> dict[str, Any]:
    source_rows = tuple(rows)
    source_by_file = {str(row.get("file")): row for row in source_rows}
    details = summarize_prior_stress_details(source_rows)
    selected = _selected_target_missing_details(
        details,
        selected_bucket=selected_bucket,
        selected_map_id=selected_map_id,
    )
    audited: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for detail in selected:
        try:
            audited.append(
                _audit_detail_row(
                    detail,
                    source_by_file.get(str(detail.get("file")), {}),
                    sample_root=sample_root,
                )
            )
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            errors.append(
                {
                    "file": str(detail.get("file") or ""),
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
    summary = _audit_summary(
        audited,
        selected_rows=len(selected),
        errors=errors,
        top=top,
    )
    return {
        "summary": summary,
        "rows": audited,
        "errors": errors,
    }


def _presence_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    seq = tuple(rows)
    return {
        target: sum(
            1
            for row in seq
            if row.get("key_target_presence", {}).get(target) is True
        )
        for target in KEY_TARGETS
    }


def _anchor_metric_summary(
    rows: Iterable[Mapping[str, Any]],
    anchor_name: str,
    metric: str,
) -> dict[str, Any]:
    return _numeric_summary(
        row.get("constraint_anchor_summary", {})
        .get(anchor_name, {})
        .get(metric)
        for row in rows
    )


def _q6_residual_status_counts(
    rows: Iterable[Mapping[str, Any]],
    field: str,
) -> dict[str, int]:
    return _counter_dict(
        row.get("q6_residual_target_candidate", {})
        .get(field, {})
        .get("status")
        for row in rows
    )


def _q6_residual_derived_pattern_counts(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    return _counter_dict(
        "+".join(row.get("q6_residual_target_candidate", {}).get("derived_fields", ()))
        or "none"
        for row in rows
    )


def _audit_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    selected_rows: int,
    errors: Iterable[Mapping[str, str]],
    top: int,
) -> dict[str, Any]:
    seq = tuple(rows)
    err_seq = tuple(errors)
    payloads = tuple(row.get("payload_item_summary", {}) for row in seq)
    return {
        "selected_rows": selected_rows,
        "audited_rows": len(seq),
        "error_count": len(err_seq),
        "map_counts": _counter_dict((row.get("map_id") for row in seq), top=top),
        "missing_component_pattern_counts": _counter_dict(
            (row.get("missing_component_pattern") for row in seq),
            top=top,
        ),
        "key_target_presence_counts": _presence_counts(seq),
        "numeric_target_counts": _merge_counter_dicts(
            ({target: 1 for target in row.get("numeric_targets", ())} for row in seq),
            top=top,
        ),
        "event_target_counts": _merge_counter_dicts(
            (row.get("event_target_counts", {}) for row in seq),
            top=top,
        ),
        "anchor_event_source_counts": _merge_counter_dicts(
            (row.get("anchor_event_source_counts", {}) for row in seq),
            top=top,
        ),
        "anchor_event_source_id_counts": _merge_counter_dicts(
            (row.get("anchor_event_source_id_counts", {}) for row in seq),
            top=top,
        ),
        "anchor_event_semantic_counts": _merge_counter_dicts(
            (row.get("anchor_event_semantic_counts", {}) for row in seq),
            top=top,
        ),
        "q6_residual_status_counts": {
            field: _q6_residual_status_counts(seq, field)
            for field in Q6_RESIDUAL_FIELDS
        },
        "q6_residual_derived_pattern_counts": _q6_residual_derived_pattern_counts(seq),
        "payload_item_summary": {
            "items": _numeric_summary(payload.get("items") for payload in payloads),
            "with_quality": _numeric_summary(payload.get("with_quality") for payload in payloads),
            "with_shape": _numeric_summary(payload.get("with_shape") for payload in payloads),
            "with_cells": _numeric_summary(payload.get("with_cells") for payload in payloads),
            "with_value": _numeric_summary(payload.get("with_value") for payload in payloads),
            "q6_items": _numeric_summary(payload.get("q6_items") for payload in payloads),
            "q6_with_cells": _numeric_summary(
                payload.get("q6_with_cells") for payload in payloads
            ),
            "q6_with_value": _numeric_summary(
                payload.get("q6_with_value") for payload in payloads
            ),
        },
        "anchor_summary": {
            "item_anchors": {
                "count": _anchor_metric_summary(seq, "item_anchors", "count"),
                "with_value": _anchor_metric_summary(seq, "item_anchors", "with_value"),
                "q6_count": _anchor_metric_summary(seq, "item_anchors", "q6_count"),
                "q6_with_value": _anchor_metric_summary(
                    seq,
                    "item_anchors",
                    "q6_with_value",
                ),
            },
            "shape_anchors": {
                "count": _anchor_metric_summary(seq, "shape_anchors", "count"),
                "with_cells": _anchor_metric_summary(seq, "shape_anchors", "with_cells"),
                "q6_count": _anchor_metric_summary(seq, "shape_anchors", "q6_count"),
                "q6_with_cells": _anchor_metric_summary(
                    seq,
                    "shape_anchors",
                    "q6_with_cells",
                ),
            },
            "quality_floor_anchors": {
                "count": _anchor_metric_summary(seq, "quality_floor_anchors", "count"),
                "q6_count": _anchor_metric_summary(
                    seq,
                    "quality_floor_anchors",
                    "q6_count",
                ),
            },
        },
    }


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_presence(counts: Mapping[str, int], total: int) -> str:
    return ",".join(f"{key}:{counts.get(key, 0)}/{total}" for key in KEY_TARGETS)


def _format_anchor(anchor: Mapping[str, int]) -> str:
    return (
        f"count={anchor.get('count', 0)}"
        f"/with_quality={anchor.get('with_quality', 0)}"
        f"/with_shape={anchor.get('with_shape', 0)}"
        f"/with_cells={anchor.get('with_cells', 0)}"
        f"/with_value={anchor.get('with_value', 0)}"
        f"/q6={anchor.get('q6_count', 0)}"
        f"/q6_cells={anchor.get('q6_with_cells', 0)}"
        f"/q6_value={anchor.get('q6_with_value', 0)}"
    )


def _format_summary_value(summary: Mapping[str, Any]) -> str:
    return (
        f"total_count_exact={summary.get('session_total_count_exact')}"
        f"/total_cells_exact={summary.get('session_total_cells_exact')}"
        f"/known_count_floor={summary.get('known_count_floor')}"
        f"/known_cells_floor={summary.get('known_cells_floor')}"
        f"/known_value_floor={summary.get('known_value_floor')}"
        f"/q6_count_exact={summary.get('q6_count_exact')}"
        f"/q6_cells_exact={summary.get('q6_cells_exact')}"
        f"/q6_value_exact={summary.get('q6_value_exact')}"
        f"/q6_count_floor={summary.get('q6_count_floor')}"
        f"/q6_cells_floor={summary.get('q6_cells_floor')}"
        f"/q6_value_floor={summary.get('q6_value_floor')}"
    )


def _format_q6_residual(candidate: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for field in Q6_RESIDUAL_FIELDS:
        row = candidate.get(field, {})
        if not isinstance(row, Mapping):
            continue
        delta = row.get("truth_delta")
        delta_text = "" if delta is None else f"/truth_delta={delta}"
        parts.append(
            f"{field}:{row.get('status')}"
            f"/value={row.get('value')}"
            f"/total={row.get('total_exact')}"
            f"/non_q6={row.get('non_q6_exact_sum')}"
            f"/missing={','.join(str(item) for item in row.get('missing_non_q6_qualities', ())) or '-'}"
            f"{delta_text}"
        )
    return "|".join(parts) or "-"


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    summary = result["summary"]
    print(
        " ".join(
            (
                f"selected_rows={summary['selected_rows']}",
                f"audited_rows={summary['audited_rows']}",
                f"errors={summary['error_count']}",
                "maps=" + _format_counts(summary["map_counts"]),
                "missing_patterns="
                + _format_counts(summary["missing_component_pattern_counts"]),
                "key_target_presence="
                + _format_presence(
                    summary["key_target_presence_counts"],
                    int(summary["audited_rows"]),
                ),
                "numeric_targets=" + _format_counts(summary["numeric_target_counts"]),
                "event_targets=" + _format_counts(summary["event_target_counts"]),
                "anchor_source_ids="
                + _format_counts(summary["anchor_event_source_id_counts"]),
                "q6_residual_patterns="
                + _format_counts(summary["q6_residual_derived_pattern_counts"]),
                "q6_residual_cells="
                + _format_counts(summary["q6_residual_status_counts"]["cells"]),
            )
        )
    )
    for row in result["rows"][:top]:
        anchors = row["constraint_anchor_summary"]
        print(
            " ".join(
                (
                    f"file={row['file']}",
                    f"round={row['round']}",
                    f"sort={row['bid_sort_id']}",
                    f"map={row['map_id']}",
                    f"profile={row['hero_map_evidence_profile']}",
                    f"missing={row['missing_component_pattern']}",
                    "sources="
                    + (
                        f"total_cells:{row['source_total_cells']}"
                        f",q6_cells:{row['source_q6_cells']}"
                        f",total_value:{row['source_total_value']}"
                        f",q6_value:{row['source_q6_value']}"
                    ),
                    "summary=" + _format_summary_value(row["summary_fields"]),
                    "item=" + _format_anchor(anchors["item_anchors"]),
                    "shape=" + _format_anchor(anchors["shape_anchors"]),
                    "quality_floor="
                    + _format_anchor(anchors["quality_floor_anchors"]),
                    "numeric_values="
                    + _format_counts(row["numeric_target_values"]),
                    "q6_residual="
                    + _format_q6_residual(row["q6_residual_target_candidate"]),
                    "targets=" + _format_counts(row["event_target_counts"]),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 target-missing prior-stress evidence events.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument(
        "--consistency-bucket",
        default=DEFAULT_BUCKET,
        help="Prior-stress consistency bucket to inspect; use 'all' for no bucket filter.",
    )
    parser.add_argument("--map-id", type=int, default=None)
    parser.add_argument(
        "--sample-root",
        type=Path,
        default=DEFAULT_SAMPLE_ROOT,
        help="Directory used to resolve file refs from evaluated rows.",
    )
    args = parser.parse_args(argv)

    rows, eval_errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        tail_value_review_entries=load_tail_value_review_entries(
            _default_tail_value_review_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    result = summarize_target_missing_event_audit(
        rows,
        sample_root=args.sample_root,
        selected_bucket=args.consistency_bucket,
        selected_map_id=args.map_id,
        top=args.top,
    )
    result["evaluation_errors"] = eval_errors
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if eval_errors:
            print(f"evaluation_errors={len(eval_errors)}")
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
