"""Aggregate map-title OCR misses from ``capture_diag.jsonl``.

Requires prior sessions logged with ``BIDKING_CAPTURE_DIAG=1``.

Usage::

    cd bidking-lab
    set BIDKING_CAPTURE_DIAG=1
    rem ... use Streamlit, reproduce a bad map line ...
    C:\\Python313\\python.exe scripts/propose_map_fixes_from_diag.py
    C:\\Python313\\python.exe scripts/propose_map_fixes_from_diag.py --min-count 2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT = _REPO / "data" / "logs" / "capture_diag.jsonl"


def _load_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=_DEFAULT,
        help=f"JSONL path (default: {_DEFAULT})",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=1,
        help="Only show fragments seen at least N times",
    )
    args = parser.parse_args(argv)

    rows = _load_rows(args.log)
    if not rows:
        print(f"No rows in {args.log}", file=sys.stderr)
        print("Set BIDKING_CAPTURE_DIAG=1 and capture once in Streamlit.", file=sys.stderr)
        return 1

    fragments: Counter[str] = Counter()
    hints: dict[str, str] = {}

    for row in rows:
        if not row.get("needs_attention"):
            continue
        map_block = row.get("map") or {}
        if map_block.get("status") != "line_unmatched":
            continue
        for tl in map_block.get("title_lines") or []:
            if tl.get("matched"):
                continue
            norm = str(tl.get("normalized_fragment") or tl.get("fragment") or "")
            if len(norm) < 2:
                continue
            fragments[norm] += 1
            best = tl.get("best_map_name")
            if best and norm not in hints:
                hints[norm] = best

    print(f"Log: {args.log} ({len(rows)} events, min_count={args.min_count})")
    print("Suggested map_fragment_fixes pairs (review before adding to JSON):\n")
    shown = 0
    for frag, count in fragments.most_common():
        if count < args.min_count:
            break
        guess = hints.get(frag, "?")
        print(f"  [{count}x]  ({frag!r}, {guess!r})")
        shown += 1
    if not shown:
        print("  (no line_unmatched fragments; good or diag not enabled yet)")
    print(
        "\nNext: add pairs to scripts/build_map_fragment_fixes.py _MANUAL, "
        "then run build_map_fragment_fixes.py",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
