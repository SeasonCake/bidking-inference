"""Read screenshot bytes from the OS clipboard (local desktop use)."""

from __future__ import annotations

import io
from pathlib import Path

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _image_bytes_from_path(path: str) -> tuple[bytes, str | None]:
    try:
        from PIL import Image
    except ImportError:
        return b"", "需要 Pillow：pip install pillow"
    p = Path(path.strip().strip('"'))
    if not p.is_file() or p.suffix.lower() not in _IMAGE_SUFFIXES:
        return b"", None
    try:
        with Image.open(p) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG", compress_level=1)
            return buf.getvalue(), None
    except OSError as exc:
        return b"", f"无法读取剪贴板图片文件: {exc}"


def _paths_from_clipboard_payload(data: object) -> list[str]:
    if isinstance(data, (list, tuple)):
        return [str(x) for x in data if x]
    if isinstance(data, str):
        s = data.strip().strip('"')
        return [s] if s else []
    return []


def clipboard_image_bytes() -> tuple[bytes, str | None]:
    """Return PNG bytes from clipboard image, or ``(b'', error)``."""
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return b"", "需要 Pillow：pip install pillow"

    try:
        data = ImageGrab.grabclipboard()
    except Exception as exc:  # noqa: BLE001
        return b"", f"读取剪贴板失败: {exc}"

    if isinstance(data, Image.Image):
        buf = io.BytesIO()
        rgb = data.convert("RGB") if data.mode != "RGB" else data
        rgb.save(buf, format="PNG", compress_level=1)
        return buf.getvalue(), None

    for path in _paths_from_clipboard_payload(data):
        png, err = _image_bytes_from_path(path)
        if png:
            return png, None
        if err:
            return b"", err

    if data is None:
        return b"", "剪贴板中没有图片。请先 Win+Shift+S / 游戏截图后复制，再点按钮。"

    return b"", "剪贴板内容不是图片（若为截图，请用 Win+Shift+S 复制后再试）。"
