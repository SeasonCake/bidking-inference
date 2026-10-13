"""Data types for screenshot / OCR text capture (Layer 3 — no inference imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from bidking_lab.capture.diag import MapResolutionDiag

CaptureSource = Literal[
    "tool_scan",       # 普品/良品/优品/极品/珍品扫描
    "map_hint",        # 地图名:竞拍信息 等
    "map_metric",      # 地图给出的均价/均格/件数
    "hero_skill",      # 英雄技能说明（通常仅记录，少填表）
    "ignored",         # 已知无用行
    "unknown",
]


@dataclass
class ParsedLine:
    """One line from the game info panel."""

    raw: str
    source: CaptureSource
    note: str = ""


@dataclass
class FieldSuggestion:
    """A single value to prefill in the Streamlit obs dict."""

    key: str
    value: Any
    label: str
    confidence: float = 1.0
    source_line: str = ""


@dataclass
class CaptureParseResult:
    """Output of :func:`parse_panel_text` — consumed by UI apply only."""

    lines: list[ParsedLine] = field(default_factory=list)
    suggestions: list[FieldSuggestion] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    hero: str | None = None
    map_id: int | None = None
    map_name: str | None = None
    map_diag: MapResolutionDiag | None = None

    def suggestion_map(self) -> dict[str, Any]:
        return {s.key: s.value for s in self.suggestions}
