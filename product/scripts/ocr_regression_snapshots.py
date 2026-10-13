"""Print OCR + normalize + parse summary for regression screenshots.

Usage::

    cd bidking-lab
    C:\\Python313\\python.exe scripts/ocr_regression_snapshots.py
    C:\\Python313\\python.exe scripts/ocr_regression_snapshots.py path\\to\\shot.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

DEFAULT_IMAGES: tuple[Path, ...] = (
    Path(r"C:\Users\example\Pictures\Desktop Screenshot 2026.05.15 - 11.10.26.79.png"),
    Path(r"C:\Users\example\Pictures\微信图片_20260517163704.jpg"),
    Path(r"C:\Users\example\Pictures\微信图片_20260517145925.jpg"),
    Path(r"C:\Users\example\Pictures\微信图片_20260517135143.jpg"),
    Path(r"C:\Users\example\Pictures\微信图片_20260517135136.jpg"),
    Path(r"C:\Users\example\Pictures\微信图片_20260517223852.jpg"),
)


def _map_names() -> dict[int, str]:
    raw = json.loads((_REPO / "data" / "processed" / "maps.json").read_text(encoding="utf-8"))
    return {int(m["map_id"]): str(m["name"]) for m in raw}


def snapshot(path: Path, *, map_names: dict[int, str]) -> None:
    from bidking_lab.capture.ocr import image_bytes_to_text
    from bidking_lab.capture.ocr_normalize import normalize_ocr_text
    from bidking_lab.capture.parser import parse_panel_text

    if not path.is_file():
        print(f"SKIP missing: {path}")
        return
    text, err = image_bytes_to_text(path.read_bytes(), crop_panel=True)
    print("=" * 60)
    print(path.name)
    if err:
        print("ERROR:", err)
        return
    parsed = parse_panel_text(text, map_names=map_names)
    print("keys:", sorted(parsed.suggestion_map().keys()))
    print("map:", parsed.map_id, parsed.map_name)
    print("ignored:", len(parsed.ignored), "unknown:", len(parsed.unknown))
    for i, raw in enumerate(text.splitlines(), 1):
        norm = normalize_ocr_text(raw)
        mark = " *" if norm != raw else ""
        print(f"{i:2}{mark}| {raw}")
        if mark:
            print(f"      -> {norm}")
    if parsed.unknown:
        print("-- unknown --")
        for ln in parsed.unknown:
            print(" ", ln)


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]] if len(argv) > 1 else list(DEFAULT_IMAGES)
    names = _map_names()
    for p in paths:
        snapshot(p, map_names=names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
