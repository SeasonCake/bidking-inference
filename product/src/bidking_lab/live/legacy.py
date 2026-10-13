"""Adapter from the current Streamlit observation dict to live events."""

from __future__ import annotations

from typing import Any, Mapping

from bidking_lab.live.types import (
    AuctionPhase,
    FieldUpdate,
    LiveEventKind,
    LiveObservationBatch,
    ObservationSource,
    SourceConfidence,
)


def _copy_if_present(
    fields: dict[tuple[str, ...], Any],
    obs: Mapping[str, Any],
    key: str,
    path: tuple[str, ...],
) -> None:
    if key in obs:
        fields[path] = obs[key]


def _copy_positive_int(
    fields: dict[tuple[str, ...], Any],
    obs: Mapping[str, Any],
    key: str,
    path: tuple[str, ...],
) -> None:
    value = obs.get(key)
    if value is None or value == "":
        return
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return
    if parsed > 0:
        fields[path] = parsed


def _copy_positive_amount(
    fields: dict[tuple[str, ...], Any],
    obs: Mapping[str, Any],
    key: str,
    path: tuple[str, ...],
) -> None:
    value = obs.get(key)
    if value is None or value == "":
        return
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return
    if parsed > 0:
        fields[path] = parsed


def _warehouse_is_estimate(obs: Mapping[str, Any]) -> bool:
    return str(obs.get("warehouse_cells_mode") or "").strip().lower() == "estimate"


def _sum_if_present(obs: Mapping[str, Any], *keys: str) -> int | None:
    if not any(key in obs for key in keys):
        return None
    total = 0
    for key in keys:
        value = obs.get(key)
        if value is None or value == "":
            continue
        total += int(value)
    return total


def legacy_obs_fields(obs: Mapping[str, Any]) -> dict[tuple[str, ...], Any]:
    """Normalize effective UI observation keys into live logical paths.

    Huge-item selectors must already be normalized by the UI boundary into
    ``*_huge_band`` plus ``*_huge_cells_override``. The legacy Streamlit
    selector can contain ``item:<name>`` values whose cell resolution belongs
    to the UI item catalogue, not this source-agnostic adapter.
    """
    fields: dict[tuple[str, ...], Any] = {}
    for legacy_key, field_key in (("map_id", "map_id"), ("hero", "hero")):
        _copy_if_present(fields, obs, legacy_key, ("session", field_key))
    if _warehouse_is_estimate(obs):
        _copy_positive_int(
            fields,
            obs,
            "warehouse_cells",
            ("session", "warehouse_total_cells_approx"),
        )
        _copy_positive_int(
            fields,
            obs,
            "warehouse_cells_tolerance",
            ("session", "warehouse_total_cells_tolerance"),
        )
    else:
        _copy_positive_int(
            fields,
            obs,
            "warehouse_cells",
            ("session", "warehouse_total_cells"),
        )
    _copy_positive_int(
        fields,
        obs,
        "total_item_count",
        ("session", "total_item_count"),
    )

    hero = obs.get("hero")
    if hero == "aisha":
        if obs.get("aisha_split"):
            _copy_positive_int(fields, obs, "white_cells", ("bucket", "1", "total_cells"))
            _copy_positive_int(fields, obs, "white_count", ("bucket", "1", "count"))
            _copy_positive_int(fields, obs, "green_cells", ("bucket", "2", "total_cells"))
            _copy_positive_int(fields, obs, "green_count", ("bucket", "2", "count"))
        else:
            cells = _sum_if_present(obs, "white_cells", "green_cells")
            count = _sum_if_present(obs, "white_count", "green_count")
            if cells is not None and cells > 0:
                fields[("bucket", "1", "total_cells")] = cells
            if count is not None and count > 0:
                fields[("bucket", "1", "count")] = count
    else:
        _copy_positive_int(fields, obs, "wg_cells", ("bucket", "1", "total_cells"))

    _copy_positive_int(fields, obs, "blue_cells", ("bucket", "3", "total_cells"))
    _copy_positive_int(fields, obs, "blue_count", ("bucket", "3", "count"))

    for prefix, quality in (("purple", "4"), ("gold", "5")):
        for suffix, field_key in (("cells", "total_cells"), ("value", "value_sum"), ("avg_raw", "avg_cells")):
            _copy_if_present(
                fields,
                obs,
                f"{prefix}_{suffix}",
                ("bucket", quality, field_key),
            )
        _copy_positive_int(
            fields,
            obs,
            f"{prefix}_count",
            ("bucket", quality, "count"),
        )
        _copy_positive_amount(
            fields,
            obs,
            f"{prefix}_avg_value",
            ("bucket", quality, "avg_value"),
        )
    for prefix, quality in (("purple", "4"), ("gold", "5")):
        if quality == "5" and hero == "aisha":
            continue
        for suffix in ("huge_band", "huge_cells_override"):
            _copy_if_present(
                fields,
                obs,
                f"{prefix}_{suffix}",
                ("bucket", quality, suffix),
            )

    if obs.get("red_confirmed_none"):
        fields[("bucket", "6", "total_cells")] = 0
        fields[("bucket", "6", "count")] = 0
        fields[("bucket", "6", "huge_band")] = "none"
    elif obs.get("small_warehouse_confirmed"):
        warehouse = int(obs.get("warehouse_cells") or 0)
        fields[("bucket", "6", "total_cells")] = max(2, warehouse // 20)
        fields[("bucket", "6", "huge_band")] = "none"
    else:
        _copy_if_present(fields, obs, "red_cells_total", ("bucket", "6", "total_cells"))
        if hero != "aisha":
            _copy_if_present(fields, obs, "red_huge_band", ("bucket", "6", "huge_band"))
            _copy_if_present(
                fields,
                obs,
                "red_huge_cells_override",
                ("bucket", "6", "huge_cells_override"),
            )
        if "red_value_lo" in obs or "red_value_hi" in obs:
            lo = obs.get("red_value_lo")
            hi = obs.get("red_value_hi")
            if lo is not None and hi is not None and int(lo) > 0 and int(hi) >= int(lo):
                fields[("bucket", "6", "value_range")] = (int(lo), int(hi))
            else:
                fields[("bucket", "6", "value_range")] = None

    return fields


def live_batch_from_legacy_obs(
    obs: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    source: ObservationSource,
    event_kind: LiveEventKind,
    confidence: SourceConfidence | None = None,
    phase: AuctionPhase = "reading",
) -> LiveObservationBatch:
    """Emit updates for paths whose normalized legacy values changed."""
    current_fields = legacy_obs_fields(obs)
    previous_fields = legacy_obs_fields(previous or {})
    confidence = confidence or ("high" if source == "ocr" else "exact")
    updates: list[FieldUpdate] = []
    for path in sorted(set(current_fields) | set(previous_fields)):
        before = previous_fields.get(path)
        after = current_fields.get(path)
        if before == after:
            continue
        updates.append(
            FieldUpdate(
                path=path,
                value=after,
                source=source,
                confidence=confidence,
            )
        )
    return LiveObservationBatch(
        source=source,
        event_kind=event_kind,
        phase=phase,
        field_updates=tuple(updates),
    )


__all__ = ("legacy_obs_fields", "live_batch_from_legacy_obs")
