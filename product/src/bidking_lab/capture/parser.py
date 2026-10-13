"""Parse OCR / copy-pasted text from the game info panel."""

from __future__ import annotations

import re
from typing import Mapping

from bidking_lab.capture.diag import collect_map_resolution_diag
from bidking_lab.capture.log_util import LOG, configure_capture_logging
from bidking_lab.capture.map_resolve import (
    best_map_in_panel_text,
    fuzzy_match_map_name,
    normalize_map_fragment,
)
from bidking_lab.capture.ocr_normalize import normalize_ocr_text

configure_capture_logging()
from bidking_lab.capture.patterns import (
    AVG_CELLS_RULES,
    IGNORE_PATTERNS,
    MAP_METRIC_RULES,
    MAP_NAME_PATTERN,
    QUALITY_CELLS_RULES,
    SESSION_PANEL_RULES,
    TOOL_SCAN_RULES,
)
from bidking_lab.capture.types import (
    CaptureParseResult,
    CaptureSource,
    FieldSuggestion,
    ParsedLine,
)


def _normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if s:
            lines.append(s)
    return lines


def _parse_int(s: str) -> int:
    return int(s.replace(",", "").replace("，", ""))


def parse_silver_amount(raw: str | float | int) -> float:
    """Parse silver price text (commas, dot or European decimal comma)."""
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(" ", "").replace("，", "")
    if not s:
        raise ValueError("empty silver amount")
    # ``6328,75`` → decimal; ``9,400`` → thousands (2dp part has ≤2 digits).
    if "," in s and "." not in s:
        parts = s.split(",")
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            if len(parts[1]) <= 2:
                s = parts[0].replace(",", "") + "." + parts[1]
            else:
                s = parts[0].replace(",", "") + parts[1]
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", "")
    return float(s)


def _should_ignore(line: str) -> bool:
    return any(pat.search(line) for pat in IGNORE_PATTERNS)


def _match_rules(
    line: str,
    rules: tuple[tuple[re.Pattern[str], str, str], ...],
    *,
    source: CaptureSource,
) -> FieldSuggestion | None:
    for pat, key, label in rules:
        m = pat.search(line)
        if not m:
            continue
        raw_val = m.group(1)
        if key.endswith("_avg_raw"):
            value: str | int = raw_val.strip()
        elif key in ("purple_avg_value", "gold_avg_value"):
            value = parse_silver_amount(raw_val)
        elif key in ("purple_value", "gold_value"):
            value = _parse_int(raw_val)
        else:
            value = _parse_int(raw_val)
        return FieldSuggestion(
            key=key,
            value=value,
            label=label,
            confidence=0.92,
            source_line=line,
        )
    return None


def resolve_map_id(line: str, map_names: Mapping[int, str]) -> tuple[int | None, str | None]:
    m = MAP_NAME_PATTERN.search(line)
    if m:
        fragment = normalize_map_fragment(m.group(1).strip())
        exact: list[tuple[int, str]] = []
        for mid, name in map_names.items():
            clean = name.replace("\u200b", "").strip()
            if clean == fragment:
                exact.append((mid, clean))
        if len(exact) == 1:
            return exact[0]
        if exact:
            exact.sort(key=lambda x: len(x[1]), reverse=True)
            return exact[0]
        fuzzy = fuzzy_match_map_name(fragment, map_names)
        if fuzzy is not None:
            return fuzzy
        return best_map_in_panel_text(line, map_names)
    return best_map_in_panel_text(line, map_names)


def parse_panel_text(
    text: str,
    *,
    map_names: Mapping[int, str] | None = None,
) -> CaptureParseResult:
    """Parse panel text into UI field suggestions (no SessionObs)."""
    map_names = map_names or {}
    text = normalize_ocr_text(text)
    result = CaptureParseResult()
    seen_keys: set[str] = set()

    if map_names:
        blob = text.replace("\r\n", "\n")
        mid, name = best_map_in_panel_text(blob, map_names)
        if mid is not None:
            result.map_id = mid
            result.map_name = name

    for line in _normalize_lines(text):
        if _should_ignore(line):
            result.ignored.append(line)
            result.lines.append(ParsedLine(line, "ignored", "规则过滤"))
            continue

        mid, mname = resolve_map_id(line, map_names)
        if mid is not None:
            result.map_id = mid
            result.map_name = mname
            result.lines.append(ParsedLine(line, "map_hint", mname or ""))
            # Same line often has 平均价值/均格 after 「地图：竞拍信息」— keep parsing.

        if any(
            tok in line
            for tok in (
                "空间觉知", "遗珍慧眼", "启迪之光",
                "加布里埃拉", "加布里埃", "艾莎",
            )
        ):
            result.lines.append(ParsedLine(line, "hero_skill", "技能说明"))
            if "显示" in line and "轮廓" in line:
                result.ignored.append(line)
            continue

        sug: FieldSuggestion | None = None
        for rules, src in (
            (SESSION_PANEL_RULES, "map_hint"),
            (TOOL_SCAN_RULES, "tool_scan"),
            (QUALITY_CELLS_RULES, "map_metric"),
            (AVG_CELLS_RULES, "tool_scan"),
            (MAP_METRIC_RULES, "map_metric"),
        ):
            sug = _match_rules(line, rules, source=src)
            if sug:
                break

        if sug is None:
            result.unknown.append(line)
            result.lines.append(ParsedLine(line, "unknown"))
            continue

        # Later lines do not override earlier unless higher confidence map metric
        if sug.key in seen_keys and sug.key not in ("purple_avg_raw", "gold_avg_raw"):
            continue
        seen_keys.add(sug.key)
        result.suggestions.append(sug)
        result.lines.append(
            ParsedLine(line, "tool_scan" if "扫描" in line else "map_metric", sug.label),
        )

    if map_names:
        result.map_diag = collect_map_resolution_diag(
            text,
            map_names,
            resolved_map_id=result.map_id,
            resolved_map_name=result.map_name,
        )
        LOG.info(
            "parse map_diag: status=%s title_lines=%d",
            result.map_diag.status,
            len(result.map_diag.title_lines),
        )

    keys = [s.key for s in result.suggestions]
    LOG.info(
        "parse: map_id=%s suggestions=%s ignored=%d unknown=%d",
        result.map_id,
        keys,
        len(result.ignored),
        len(result.unknown),
    )
    return result
