"""Small parsers for inference diagnostic marker strings."""

from __future__ import annotations

from collections.abc import Sequence


def _diagnostic_text(diagnostics: str | Sequence[str] | None) -> str:
    if diagnostics is None:
        return ""
    if isinstance(diagnostics, str):
        return diagnostics
    return ";".join(str(part) for part in diagnostics)


def layout_conflict_root(
    diagnostics: str | Sequence[str] | None,
    *,
    footprint_count: int | None = None,
    trusted_footprint_count: int | None = None,
) -> str:
    """Return stable layout-conflict markers from posterior diagnostics."""

    text = _diagnostic_text(diagnostics)
    markers: list[str] = []
    if "footprint_overlap_cells:" in text or "footprint_overlap" in text:
        markers.append("footprint_overlap")
    if "footprint_overflow:" in text or "footprint_overflow" in text:
        markers.append("footprint_overflow")
    if "footprint_count_relaxed:" in text or "footprint_count_relaxed" in text:
        markers.append("footprint_count_relaxed")
    if "all_footprints_untrusted" in text:
        markers.append("all_footprints_untrusted")
    if "partial_footprints_untrusted" in text:
        markers.append("partial_footprints_untrusted")
    if (
        footprint_count is not None
        and trusted_footprint_count is not None
        and footprint_count > 0
        and trusted_footprint_count < footprint_count
    ):
        if trusted_footprint_count <= 0:
            markers.append("all_footprints_untrusted")
        else:
            markers.append("partial_footprints_untrusted")
    return ";".join(dict.fromkeys(markers))


def has_layout_conflict(
    diagnostics: str | Sequence[str] | None,
    *,
    footprint_count: int | None = None,
    trusted_footprint_count: int | None = None,
) -> bool:
    return bool(
        layout_conflict_root(
            diagnostics,
            footprint_count=footprint_count,
            trusted_footprint_count=trusted_footprint_count,
        )
    )
