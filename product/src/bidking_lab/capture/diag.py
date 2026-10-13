"""Structured capture diagnostics (JSONL) for field issues in production use.

Enable file logging with ``BIDKING_CAPTURE_DIAG=1``. Log path defaults to
``data/logs/capture_diag.jsonl`` under the repo (gitignored).

This complements console logging in :mod:`log_util` — use diag when you need
to aggregate map-title OCR misses and later propose ``map_fragment_fixes``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from bidking_lab.capture.map_resolve import (
    _clean_map_title,
    _name_ratio,
    fuzzy_match_map_name,
    normalize_map_fragment,
)
from bidking_lab.capture.patterns import MAP_NAME_PATTERN

MapDiagStatus = Literal[
    "resolved",
    "line_unmatched",
    "no_map_line",
    "ambiguous_lines",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LOG = _REPO_ROOT / "data" / "logs" / "capture_diag.jsonl"


@dataclass
class MapTitleLineDiag:
    raw_line: str
    fragment: str
    normalized_fragment: str
    matched: bool
    map_id: int | None = None
    map_name: str | None = None
    best_ratio: float = 0.0
    best_map_name: str | None = None


@dataclass
class MapResolutionDiag:
    """How map title OCR related to the parse result."""

    status: MapDiagStatus
    map_id: int | None = None
    map_name: str | None = None
    title_lines: list[MapTitleLineDiag] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _best_fuzzy_ratio(
    fragment: str,
    map_names: Mapping[int, str],
) -> tuple[float, int | None, str | None]:
    norm = normalize_map_fragment(fragment)
    best_ratio = 0.0
    best_mid: int | None = None
    best_name: str | None = None
    for mid, name in map_names.items():
        clean = _clean_map_title(name)
        if not clean:
            continue
        if norm == clean:
            return 1.0, mid, clean
        score = _name_ratio(norm, clean)
        if score > best_ratio:
            best_ratio = score
            best_mid = mid
            best_name = clean
    return best_ratio, best_mid, best_name


def collect_map_resolution_diag(
    text: str,
    map_names: Mapping[int, str],
    *,
    resolved_map_id: int | None,
    resolved_map_name: str | None,
) -> MapResolutionDiag:
    """Classify map OCR: resolved vs title line seen but unmatched vs absent."""
    if not map_names:
        return MapResolutionDiag(
            status="no_map_line",
            map_id=resolved_map_id,
            map_name=resolved_map_name,
        )

    title_lines: list[MapTitleLineDiag] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = MAP_NAME_PATTERN.search(line)
        if not m:
            continue
        fragment = m.group(1).strip()
        norm = normalize_map_fragment(fragment)
        hit = fuzzy_match_map_name(fragment, map_names)
        ratio, best_mid, best_name = _best_fuzzy_ratio(fragment, map_names)
        title_lines.append(
            MapTitleLineDiag(
                raw_line=line,
                fragment=fragment,
                normalized_fragment=norm,
                matched=hit is not None,
                map_id=hit[0] if hit else None,
                map_name=hit[1] if hit else None,
                best_ratio=round(ratio, 4),
                best_map_name=best_name if hit is None else hit[1],
            ),
        )

    if not title_lines:
        return MapResolutionDiag(
            status="no_map_line",
            map_id=resolved_map_id,
            map_name=resolved_map_name,
        )

    unmatched = [t for t in title_lines if not t.matched]
    matched_ids = {t.map_id for t in title_lines if t.matched}
    if len(matched_ids) > 1:
        status: MapDiagStatus = "ambiguous_lines"
    elif unmatched:
        status = "line_unmatched"
    elif resolved_map_id is not None:
        status = "resolved"
    else:
        status = "line_unmatched"

    return MapResolutionDiag(
        status=status,
        map_id=resolved_map_id,
        map_name=resolved_map_name,
        title_lines=title_lines,
    )


def diag_log_path() -> Path:
    raw = os.environ.get("BIDKING_CAPTURE_DIAG_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_LOG


def capture_diag_enabled() -> bool:
    return os.environ.get("BIDKING_CAPTURE_DIAG", "").strip() in (
        "1",
        "true",
        "yes",
        "on",
    )


def append_capture_diag(event: dict[str, Any]) -> Path | None:
    """Append one JSON object per line; no-op when diag disabled."""
    if not capture_diag_enabled():
        return None
    path = diag_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def record_capture_session(
    *,
    source: str,
    crop_panel: bool,
    map_diag: MapResolutionDiag,
    suggestion_keys: list[str],
    apply_map_id_before: int | None = None,
    apply_map_id_after: int | None = None,
    map_auto_switched: bool | None = None,
    user_map_preserved: bool = False,
) -> Path | None:
    """Log one capture apply cycle (parse + optional apply outcome)."""
    needs_attention = (
        map_diag.status == "line_unmatched"
        or (
            map_diag.status in ("resolved", "ambiguous_lines")
            and map_auto_switched is False
            and apply_map_id_after is None
            and bool(suggestion_keys)
        )
    )
    return append_capture_diag(
        {
            "source": source,
            "crop_panel": crop_panel,
            "needs_attention": needs_attention,
            "map": map_diag.to_dict(),
            "suggestion_keys": suggestion_keys,
            "apply": {
                "map_id_before": apply_map_id_before,
                "map_id_after": apply_map_id_after,
                "map_auto_switched": map_auto_switched,
                "user_map_preserved": user_map_preserved,
            },
        },
    )
