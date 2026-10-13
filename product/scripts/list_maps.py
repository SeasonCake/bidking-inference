"""Print all maps grouped by ID prefix so we can see the main categories."""

from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    maps_path = repo_root / "data" / "processed" / "maps.json"
    maps = json.loads(maps_path.read_text(encoding="utf-8"))

    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for m in maps:
        prefix = str(m["map_id"])[:2]
        groups[prefix].append((m["map_id"], m["name"]))

    print(f"total maps: {len(maps)}\n")
    for prefix in sorted(groups):
        items = sorted(groups[prefix])
        print(f"== map_id starts with {prefix}  ({len(items)} maps) ==")
        for mid, name in items:
            print(f"  {mid}  {name}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
