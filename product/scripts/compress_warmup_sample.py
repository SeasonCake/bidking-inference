"""Compress the in-game Desktop screenshot for OCR warm-up (repo-friendly size).

Reads the large 4K PNG from ``data/samples/``, writes
``game_warmup_1920x1080.jpg`` (1920×1080, JPEG q=88).

Usage::

    cd bidking-lab
    C:\\Python313\\python.exe scripts/compress_warmup_sample.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_SRC = _REPO / "data" / "samples" / "Desktop_Screenshot_2026.05.15 - 11.10.26.79.png"
_OUT = _REPO / "data" / "samples" / "game_warmup_1920x1080.jpg"


def compress_warmup_sample(
    src: Path,
    dest: Path = _OUT,
    *,
    width: int = 1920,
    quality: int = 88,
) -> Path:
    from PIL import Image

    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w != width:
        scale = width / w
        im = im.resize((width, int(h * scale)), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, format="JPEG", quality=quality, optimize=True)
    return dest


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_SRC
    if not src.is_file():
        print(f"Source not found: {src}", file=sys.stderr)
        return 1
    out = compress_warmup_sample(src)
    print(f"Wrote {out} ({out.stat().st_size // 1024} KiB, {out.suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
