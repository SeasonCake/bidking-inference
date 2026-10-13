"""Source-agnostic observation contracts for realtime monitoring.

These types are deliberately small. They reserve room for packet-capture
signals without tying the inference engine to ProtoHub, OCR, Streamlit, or
any specific transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ObservationSource = Literal["manual", "ocr", "packet", "derived"]
SourceConfidence = Literal["low", "medium", "high", "exact"]
AuctionPhase = Literal[
    "unknown",
    "map_select",
    "reading",
    "bidding",
    "settled",
]
LiveEventKind = Literal[
    "manual_update",
    "ocr_update",
    "session_started",
    "round_changed",
    "tool_revealed",
    "public_info_changed",
    "session_settled",
    "heartbeat",
]


_SOURCE_PRIORITY: dict[ObservationSource, int] = {
    "derived": 10,
    "ocr": 20,
    "manual": 30,
    "packet": 40,
}
_RECOMPUTE_EVENTS: frozenset[LiveEventKind] = frozenset(
    {
        "manual_update",
        "ocr_update",
        "session_started",
        "round_changed",
        "tool_revealed",
        "public_info_changed",
        "session_settled",
    }
)
GRID_COLUMNS = 10


def source_priority(source: ObservationSource) -> int:
    """Return merge priority for conflicting updates.

    Packet observations are highest because they should come from game state
    rather than screen text. Manual remains above OCR so the user can override
    recognition mistakes.
    """
    return _SOURCE_PRIORITY[source]


def event_requests_recompute(event_kind: LiveEventKind) -> bool:
    """Return whether a batch can change inference recommendations."""
    return event_kind in _RECOMPUTE_EVENTS


@dataclass(frozen=True)
class GridFootprint:
    """Warehouse grid footprint using 1-based row/column coordinates."""

    row: int
    col: int
    width: int
    height: int

    @property
    def right_col(self) -> int:
        return self.col + self.width - 1

    @property
    def bottom_row(self) -> int:
        return self.row + self.height - 1


def shape_key_dimensions(shape_key: str | int | None) -> tuple[int, int] | None:
    """Parse a Fatbeans-style shape code into ``(width, height)``.

    The captures observed so far encode shapes as ``width * 10 + height``:
    ``23`` means 2 columns by 3 rows, ``61`` means 6 columns by 1 row.
    """
    if shape_key is None:
        return None
    try:
        code = int(shape_key)
    except (TypeError, ValueError):
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width, height


def grid_footprint(
    local_index: int | None,
    shape_key: str | int | None,
    *,
    columns: int = GRID_COLUMNS,
) -> GridFootprint | None:
    """Convert ``local_index + shape_key`` to a 1-based grid footprint.

    Fatbeans omits protobuf default ``0`` in some cases, so ``None`` is
    interpreted as top-left index 0 when a shape is present.
    """
    dims = shape_key_dimensions(shape_key)
    if dims is None or columns <= 0:
        return None
    local = 0 if local_index is None else local_index
    if local < 0:
        return None
    width, height = dims
    return GridFootprint(
        row=local // columns + 1,
        col=local % columns + 1,
        width=width,
        height=height,
    )


@dataclass(frozen=True)
class FieldUpdate:
    """One observed field update from manual UI, OCR, packet, or derived logic.

    ``path`` is a stable logical path, not a Streamlit widget key. Examples:
    ``("session", "warehouse_total_cells")`` or
    ``("bucket", "4", "avg_cells")``.
    """

    path: tuple[str, ...]
    value: Any
    source: ObservationSource
    confidence: SourceConfidence
    observed_at_ms: int | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class GridItemObservation:
    """One visible/known warehouse item footprint.

    This is the reserved interface for future Ethan / packet-capture data:
    it can represent unknown-quality outlines, known-quality items, or exact
    item ids without forcing every case into ``QualityBucketObs`` too early.
    """

    cells: int
    source: ObservationSource
    confidence: SourceConfidence
    runtime_id: int | None = None
    item_id: int | None = None
    quality: int | None = None
    shape_key: str | None = None
    value: int | None = None
    local_index: int | None = None
    category: int | None = None
    categories: tuple[int, ...] = ()
    excluded_categories: tuple[int, ...] = ()
    excluded_qualities: tuple[int, ...] = ()
    observed_at_ms: int | None = None

    def footprint(self) -> GridFootprint | None:
        """Return a 1-based grid footprint when this item has a shape."""
        return grid_footprint(self.local_index, self.shape_key)


@dataclass(frozen=True)
class LiveObservationBatch:
    """A batch of observations from one discrete game or UI event.

    ``heartbeat`` batches may carry transport metadata, but do not make
    inference results stale. Emit a semantic event when game facts change.
    """

    source: ObservationSource
    event_kind: LiveEventKind = "manual_update"
    phase: AuctionPhase = "unknown"
    field_updates: tuple[FieldUpdate, ...] = ()
    grid_items: tuple[GridItemObservation, ...] = ()
    sequence: int | None = None
    observed_at_ms: int | None = None
    # Exact owner session from packet-backed batches.  Legacy/manual producers
    # may omit it, but current-session consumers must not treat None as a wildcard.
    session_id: str | None = None
