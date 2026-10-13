"""Map name resolution for OCR panel text (105+ BidMap names)."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

from ..secure.data_crypt import load_json  # moat 透明解密(加密包)/ 明文直读(源码)

# Built-in fixes always available (also used when rebuilding map_fragment_fixes.json).
BUILTIN_FRAGMENT_FIXES: tuple[tuple[str, str], ...] = (
    ("底护所", "庇护所"),
    ("底护", "庇护"),
    ("完拍信息", "竞拍信息"),
    ("完拍", "竞拍"),
    ("娱乐库库", "娱乐库"),
    ("集装箱库", "集装箱"),
    ("别墅区", "别墅"),
    ("沉船遗址", "沉船"),
    ("居听", "居所"),
    ("末目", "末日"),
    ("末日底护所", "末日庇护所"),
    ("望族居听", "望族居所"),
    ("望族居", "望族居所"),
    ("末目庇护所", "末日庇护所"),
    ("未知残船", "未知残骸"),
    ("现代货轮", "现代货轮娱乐库"),
)

_FUZZY_MIN_RATIO = 0.84
_SHORT_NAME_LEN = 4
_SHORT_NAME_MIN_RATIO = 0.92

_JSON_PATH = Path(__file__).resolve().parents[3] / "data" / "processed" / "map_fragment_fixes.json"


def _clean_map_title(name: str) -> str:
    return name.replace("\u200b", "").strip()


def _apply_fragment_fixes(
    fragment: str,
    fixes: Iterable[tuple[str, str]],
) -> str:
    s = _clean_map_title(fragment)
    for wrong, right in fixes:
        if wrong == "现代货轮" and "娱乐库" in s:
            continue
        s = s.replace(wrong, right)
    return s.strip()


@lru_cache(maxsize=1)
def _json_fragment_fixes() -> tuple[tuple[str, str], ...]:
    if not _JSON_PATH.is_file():
        return ()
    data = load_json(_JSON_PATH)   # 加密包: 内核K解密; 源码: 明文直读
    raw = data.get("fixes") or []
    out: list[tuple[str, str]] = []
    for pair in raw:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            w, r = str(pair[0]).strip(), str(pair[1]).strip()
            if w and r and w != r:
                out.append((w, r))
    return tuple(out)


def all_fragment_fixes() -> tuple[tuple[str, str], ...]:
    """Builtin + JSON fixes, longest ``wrong`` first to avoid partial clobber."""
    merged = list(BUILTIN_FRAGMENT_FIXES) + list(_json_fragment_fixes())
    dedup: dict[str, str] = {}
    for wrong, right in merged:
        dedup[wrong] = right
    return tuple(sorted(dedup.items(), key=lambda x: (-len(x[0]), x[0])))


def normalize_map_fragment(
    fragment: str,
    *,
    extra_fixes: Iterable[tuple[str, str]] = (),
) -> str:
    """Lightweight fixes on the map title extracted from a panel line."""
    fixes = list(all_fragment_fixes())
    fixes.extend(extra_fixes)
    fixes.sort(key=lambda x: -len(x[0]))
    return _apply_fragment_fixes(fragment, fixes)


def _name_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def fuzzy_match_map_name(
    candidate: str,
    map_names: Mapping[int, str],
    *,
    min_ratio: float = _FUZZY_MIN_RATIO,
    apply_fixes: bool = True,
) -> tuple[int, str] | None:
    """Return ``(map_id, name)`` if *candidate* matches a known map title."""
    if apply_fixes:
        candidate = normalize_map_fragment(candidate)
    if len(candidate) < 2:
        return None

    threshold = (
        _SHORT_NAME_MIN_RATIO
        if len(candidate) <= _SHORT_NAME_LEN
        else min_ratio
    )
    best_mid: int | None = None
    best_name: str | None = None
    best_score = threshold

    for mid, name in map_names.items():
        clean = _clean_map_title(name)
        if not clean:
            continue
        if candidate == clean:
            return mid, clean
        score = _name_ratio(candidate, clean)
        if score > best_score:
            best_score = score
            best_mid = mid
            best_name = clean

    if best_mid is None:
        return None
    return best_mid, best_name


def best_map_in_panel_text(
    text: str,
    map_names: Mapping[int, str],
) -> tuple[int | None, str | None]:
    """Find the best map title in full panel OCR (substring + fuzzy line match)."""
    from bidking_lab.capture.patterns import MAP_NAME_PATTERN

    if not map_names:
        return None, None

    best_len = 0
    best_mid: int | None = None
    best_name: str | None = None
    for mid, name in map_names.items():
        clean = _clean_map_title(name)
        if clean and clean in text and len(clean) > best_len:
            best_len = len(clean)
            best_mid = mid
            best_name = clean

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = MAP_NAME_PATTERN.search(line)
        if not m:
            continue
        hit = fuzzy_match_map_name(m.group(1).strip(), map_names)
        if hit is None:
            continue
        mid, name = hit
        if best_mid is None or len(name) > best_len:
            best_len = len(name)
            best_mid = mid
            best_name = name

    return best_mid, best_name
