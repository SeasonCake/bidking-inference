"""Build ``data/processed/map_fragment_fixes.json`` from 43 canonical map titles.

Usage::

    cd bidking-lab
    C:\\Python313\\python.exe scripts/build_map_fragment_fixes.py
    C:\\Python313\\python.exe scripts/build_map_fragment_fixes.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_OUT = _REPO / "data" / "processed" / "map_fragment_fixes.json"
_MAPS = _REPO / "data" / "processed" / "maps.json"

# Phrase-level OCR swaps (typo substring → canonical substring).
_PHRASE_TYPO: tuple[tuple[str, str], ...] = (
    ("底护所", "庇护所"),
    ("底护", "庇护"),
    ("完拍", "竞拍"),
    ("居听", "居所"),
    ("末目", "末日"),
    ("集装箱库", "集装箱"),
    ("别墅区", "别墅"),
    ("沉船遗址", "沉船"),
    ("娱乐库库", "娱乐库"),
    ("祥本", "样本"),
    ("座船", "座舰"),
)

# Single-character confusions: (canonical_char, ocr_char).
_CHAR_TYPO: tuple[tuple[str, str], ...] = (
    ("庇", "底"),
    ("护", "户"),
    ("所", "听"),
    ("目", "日"),
    ("藏", "品"),
    ("品", "藏"),
    ("艇", "船"),
    ("园", "院"),
    ("院", "园"),
    ("究", "完"),
    ("样", "祥"),
)

# Extra hand-curated pairs (beyond BUILTIN_FRAGMENT_FIXES in map_resolve.py).
_MANUAL: tuple[tuple[str, str], ...] = (
    ("学者居听", "学者居所"),
    ("设计师居听", "设计师居所"),
    ("奢华养者院", "奢华养老院"),
    ("潮朝仓库", "潮牌仓库"),
    ("潮朝集装箱", "潮牌集装箱"),
    ("生物实验完样本库", "生物实验室样本库"),
    ("生物实验室祥本库", "生物实验室样本库"),
    ("探险家座船", "探险家座舰"),
    ("军用舰船保险库", "军用舰艇保险库"),
    ("现代货轮娱乐库库", "现代货轮娱乐库"),
    ("医疗用藏集装箱", "医疗用品集装箱"),
    ("养生学家居听", "养生学家居所"),
    ("科学家居听", "科学家居所"),
    ("医园快递", "医院快递"),
)


def _clean(name: str) -> str:
    return name.replace("\u200b", "").strip()


def canonical_names() -> frozenset[str]:
    raw = json.loads(_MAPS.read_text(encoding="utf-8"))
    return frozenset(
        n for m in raw if (n := _clean(str(m["name"]))) and len(n) >= 2
    )


def generate_fixes(
    canonical: frozenset[str],
    *,
    names: dict[int, str],
) -> list[tuple[str, str]]:
    from bidking_lab.capture.map_resolve import (
        BUILTIN_FRAGMENT_FIXES,
        _apply_fragment_fixes,
        fuzzy_match_map_name,
    )

    accepted: list[tuple[str, str]] = []
    out: dict[str, str] = {}

    def add(wrong: str, right: str) -> None:
        wrong, right = wrong.strip(), right.strip()
        if len(wrong) < 2 or wrong == right or right not in canonical:
            return
        trial = list(BUILTIN_FRAGMENT_FIXES) + accepted + [(wrong, right)]
        trial.sort(key=lambda x: -len(x[0]))
        norm = _apply_fragment_fixes(wrong, trial)
        hit = fuzzy_match_map_name(norm, names, apply_fixes=False)
        if hit is None or hit[1] != right:
            return
        if wrong not in out:
            accepted.append((wrong, right))
            out[wrong] = right

    for w, r in _MANUAL:
        add(w, r)

    for name in sorted(canonical):
        for typo_phrase, canon_phrase in _PHRASE_TYPO:
            if canon_phrase not in name:
                continue
            add(name.replace(canon_phrase, typo_phrase, 1), name)

        for c_ok, c_bad in _CHAR_TYPO:
            if c_ok not in name:
                continue
            add(name.replace(c_ok, c_bad, 1), name)

    builtin_wrong = {w for w, _ in BUILTIN_FRAGMENT_FIXES}
    extras = [(w, r) for w, r in out.items() if w not in builtin_wrong]
    return sorted(extras, key=lambda x: (-len(x[0]), x[0]))


def validate(fixes: list[tuple[str, str]], canonical: frozenset[str]) -> list[str]:
    from bidking_lab.capture.map_resolve import (
        _json_fragment_fixes,
        fuzzy_match_map_name,
    )

    _json_fragment_fixes.cache_clear()
    names = {i: n for i, n in enumerate(sorted(canonical))}
    errors: list[str] = []
    for wrong, right in fixes:
        hit = fuzzy_match_map_name(wrong, names)
        if hit is None or hit[1] != right:
            errors.append(f"{wrong!r} -> {right!r} (hit={hit})")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate existing JSON without rewriting",
    )
    args = parser.parse_args(argv)
    canonical = canonical_names()
    if args.check:
        data = json.loads(_OUT.read_text(encoding="utf-8"))
        fixes = [(a, b) for a, b in data["fixes"]]
    else:
        names = {i: n for i, n in enumerate(sorted(canonical))}
        fixes = generate_fixes(canonical, names=names)
        payload = {
            "version": 1,
            "canonical_count": len(canonical),
            "fixes": fixes,
        }
        _OUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {len(fixes)} fixes -> {_OUT}")

    errs = validate(fixes, canonical)
    if errs:
        print(f"VALIDATION FAILED ({len(errs)}):", file=sys.stderr)
        for e in errs[:30]:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"OK: {len(fixes)} fixes, {len(canonical)} canonical names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
