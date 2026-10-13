from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .size_avg_evidence import _float32_interval_from_wire


Q6_FULL_AVG_INFO_ID = 200038
Q6_FULL_AVG_SEMANTIC = "q6_avg_value"
Q6_FULL_AVG_VALUE_FIELDS = frozenset({9, 11})


@dataclass(frozen=True)
class Q6FullAverageEvidence:
    """Strict wire-level mean equation for public full-q6 average value.

    This record deliberately carries no catalog interpretation.  The reference
    engine must combine it with the canonical current-map candidate generation.
    """

    status: str
    conflicts: tuple[str, ...] = ()
    source_session_id: str | None = None
    source_sort: int | None = None
    source_field: int | None = None
    wire_numeric_type: str | None = None
    raw_bits_hex: str | None = None
    raw_value: float | None = None
    avg_interval: tuple[float, float] | None = None
    duplicate_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        interval = self.avg_interval
        payload["avg_interval"] = (
            [float(interval[0]), float(interval[1])] if interval is not None else None
        )
        payload["conflicts"] = list(self.conflicts)
        return payload


@dataclass(frozen=True)
class Q6FullAverageIntegerTotalBounds:
    """Finite integer-total window represented by one Float32 mean equation."""

    status: str
    lower: int | None = None
    upper: int | None = None


def _strict_int(value: Any) -> int | None:
    return value if type(value) is int else None  # noqa: E721 - wire JSON contract


def _targeted_q6_full_avg_row(row: Mapping[str, Any]) -> bool:
    # The strict lane owns one concrete public-info wire ID.  Historical or
    # source-external semantic-only q6 facts remain on the Lane-A compatibility
    # path; treating them as malformed 200038 would silently disable that path.
    return row.get("info_id") == Q6_FULL_AVG_INFO_ID


def q6_full_average_value_evidence_from_public_rows(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    current_session_id: Any,
) -> Q6FullAverageEvidence:
    attempted = 0
    valid: list[Q6FullAverageEvidence] = []
    conflicts: list[str] = []

    if rows is None or isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        return Q6FullAverageEvidence(status="not_applicable")

    for raw_row in rows:
        if type(raw_row) is not dict:  # noqa: E721 - exact JSON boundary
            continue
        row: Mapping[str, Any] = raw_row
        if not _targeted_q6_full_avg_row(row):
            continue
        attempted += 1
        row_conflicts: list[str] = []
        if _strict_int(row.get("info_id")) != Q6_FULL_AVG_INFO_ID:
            row_conflicts.append("invalid_info_id")
        if "semantic" in row and row.get("semantic") != Q6_FULL_AVG_SEMANTIC:
            row_conflicts.append("invalid_semantic")
        if "kind" in row and row.get("kind") != "avg_value":
            row_conflicts.append("invalid_kind")
        if "quality" in row and _strict_int(row.get("quality")) != 6:
            row_conflicts.append("invalid_quality")

        source_session = row.get("session_id")
        if type(current_session_id) is not str or not current_session_id:  # noqa: E721
            row_conflicts.append("invalid_current_session")
        if type(source_session) is not str or not source_session:  # noqa: E721
            row_conflicts.append("invalid_source_session")
        elif source_session != current_session_id:
            row_conflicts.append("stale_or_cross_session")

        source_field = _strict_int(row.get("value_field"))
        if source_field not in Q6_FULL_AVG_VALUE_FIELDS:
            row_conflicts.append("invalid_source_field")
        if row.get("value_wire_type") != "float32":
            row_conflicts.append("invalid_wire_type")
        if row.get("value_endian") != "little":
            row_conflicts.append("invalid_endian")
        parser_conflicts = row.get("value_conflicts")
        if parser_conflicts not in (None, (), []):
            row_conflicts.append("parser_duplicate_or_wire_conflict")

        raw_value = row.get("value")
        if type(raw_value) is not float or not math.isfinite(raw_value) or raw_value <= 0:  # noqa: E721
            row_conflicts.append("invalid_positive_float32_value")
        interval = _float32_interval_from_wire(
            raw_hex=row.get("value_raw_hex"),
            parsed_value=raw_value,
        )
        if interval is None:
            row_conflicts.append("float32_wire_mismatch")

        if row_conflicts:
            conflicts.extend(row_conflicts)
            continue
        raw_hex = str(row["value_raw_hex"]).lower()
        valid.append(
            Q6FullAverageEvidence(
                status="ok",
                source_session_id=source_session,
                source_sort=_strict_int(row.get("sort")),
                source_field=source_field,
                wire_numeric_type="float32",
                raw_bits_hex=raw_hex,
                raw_value=float(raw_value),
                avg_interval=(float(interval[0]), float(interval[1])),
                duplicate_count=1,
            )
        )

    if attempted == 0:
        return Q6FullAverageEvidence(status="not_applicable")
    if conflicts:
        return Q6FullAverageEvidence(
            status="conflict",
            conflicts=tuple(dict.fromkeys(conflicts)),
            duplicate_count=attempted,
        )
    fingerprints = {
        (
            item.source_session_id,
            item.source_field,
            item.wire_numeric_type,
            item.raw_bits_hex,
            item.raw_value,
            item.avg_interval,
        )
        for item in valid
    }
    if len(valid) != attempted or len(fingerprints) != 1:
        return Q6FullAverageEvidence(
            status="conflict",
            conflicts=("duplicate_conflict",),
            duplicate_count=attempted,
        )
    selected = valid[0]
    return Q6FullAverageEvidence(
        status="ok",
        source_session_id=selected.source_session_id,
        source_sort=max(
            (item.source_sort for item in valid if item.source_sort is not None),
            default=None,
        ),
        source_field=selected.source_field,
        wire_numeric_type=selected.wire_numeric_type,
        raw_bits_hex=selected.raw_bits_hex,
        raw_value=selected.raw_value,
        avg_interval=selected.avg_interval,
        duplicate_count=len(valid),
    )


def integer_total_bounds_from_q6_full_average(
    evidence: Q6FullAverageEvidence,
    count: int,
) -> Q6FullAverageIntegerTotalBounds:
    if evidence.status != "ok" or evidence.avg_interval is None or count <= 0:
        return Q6FullAverageIntegerTotalBounds(status="invalid_evidence")
    lower_product = float(evidence.avg_interval[0]) * int(count)
    upper_product = float(evidence.avg_interval[1]) * int(count)
    if not math.isfinite(lower_product) or not math.isfinite(upper_product):
        return Q6FullAverageIntegerTotalBounds(status="unrepresentable_interval")
    try:
        lower = math.ceil(lower_product)
        upper = math.floor(upper_product)
    except (OverflowError, ValueError):
        return Q6FullAverageIntegerTotalBounds(status="unrepresentable_interval")
    if lower > upper:
        return Q6FullAverageIntegerTotalBounds(status="empty_integer_total_interval")
    # This is a safety envelope, not a silent empty-set shortcut.  Current canonical q6 values
    # need at most 129 integers through the engine's count ceiling.  If a future catalog expands
    # beyond this bounded window, the engine publishes one explicit whole-lane complexity status
    # and never keeps a partial count/cell/value support.
    if upper - lower + 1 > 4096:
        return Q6FullAverageIntegerTotalBounds(status="integer_interval_complexity_guard")
    return Q6FullAverageIntegerTotalBounds(
        status="ok",
        lower=int(lower),
        upper=int(upper),
    )


__all__ = [
    "Q6FullAverageEvidence",
    "Q6FullAverageIntegerTotalBounds",
    "Q6_FULL_AVG_INFO_ID",
    "Q6_FULL_AVG_SEMANTIC",
    "integer_total_bounds_from_q6_full_average",
    "q6_full_average_value_evidence_from_public_rows",
]
