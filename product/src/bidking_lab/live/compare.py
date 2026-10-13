"""Compare legacy and live-derived inference sessions."""

from __future__ import annotations

from typing import Any

from bidking_lab.inference.display import Reading
from bidking_lab.inference.observation import QualityBucketObs, SessionObs


_SESSION_FIELDS: tuple[str, ...] = (
    "map_id",
    "hero",
    "warehouse_total_cells",
    "warehouse_total_cells_approx",
    "warehouse_total_cells_tolerance",
    "total_item_count",
)

_BUCKET_FIELDS: tuple[str, ...] = (
    "total_cells",
    "total_cells_approx",
    "count",
    "value_sum",
    "avg_value",
    "avg_cells",
    "value_range",
    "huge_band",
    "huge_cells_override",
)


def _value_for_compare(value: Any) -> Any:
    if isinstance(value, Reading):
        return value.raw
    return value


def _value_for_display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, tuple):
        return "–".join(str(part) for part in value)
    return str(value)


def _bucket_paths(
    bucket: QualityBucketObs,
    *,
    prefix: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {f"{prefix}._present": True}
    for field_name in _BUCKET_FIELDS:
        out[f"{prefix}.{field_name}"] = _value_for_compare(
            getattr(bucket, field_name),
        )
    return out


def flatten_session_obs(session: SessionObs | None) -> dict[str, Any]:
    """Return a stable field-path mapping for one inference session."""
    if session is None:
        return {}
    out: dict[str, Any] = {}
    for field_name in _SESSION_FIELDS:
        out[f"session.{field_name}"] = _value_for_compare(
            getattr(session, field_name),
        )
    for quality in sorted(session.buckets):
        out.update(
            _bucket_paths(
                session.buckets[quality],
                prefix=f"bucket.{quality}",
            )
        )
    return out


def compare_session_obs(
    legacy: SessionObs | None,
    live: SessionObs | None,
) -> tuple[dict[str, str], ...]:
    """Return display rows for fields where legacy and live sessions differ."""
    legacy_flat = flatten_session_obs(legacy)
    live_flat = flatten_session_obs(live)
    rows: list[dict[str, str]] = []
    for field in sorted(set(legacy_flat) | set(live_flat)):
        legacy_value = legacy_flat.get(field)
        live_value = live_flat.get(field)
        if legacy_value == live_value:
            continue
        if field not in legacy_flat:
            status = "live_only"
        elif field not in live_flat:
            status = "legacy_only"
        else:
            status = "different"
        rows.append(
            {
                "field": field,
                "legacy": _value_for_display(legacy_value),
                "live": _value_for_display(live_value),
                "status": status,
            }
        )
    return tuple(rows)


def sessions_match(legacy: SessionObs | None, live: SessionObs | None) -> bool:
    """Return whether two inference sessions are field-equivalent."""
    return not compare_session_obs(legacy, live)


__all__ = (
    "compare_session_obs",
    "flatten_session_obs",
    "sessions_match",
)
