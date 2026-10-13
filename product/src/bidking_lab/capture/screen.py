"""Display capture + ROI for the center-left bidding info panel (C-36).

Coordinates are **fractions of the chosen monitor's pixel rectangle**
(left, top, right, bottom in 0..1). Calibrated on 1920×1080; the same
fractions scale to 4K/1440p because UI layout is proportional.

Pick the monitor where the **game window** is visible — Streamlit may run
on a different display than the game.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Calibrated on 1920×1080 in-game captures (notebook 06).
INFO_PANEL_CROP_FRAC: tuple[float, float, float, float] = (0.30, 0.07, 0.59, 0.72)

_REPO_ROOT = Path(__file__).resolve().parents[3]
OCR_WARMUP_SAMPLE = _REPO_ROOT / "data" / "samples" / "game_warmup_1920x1080.jpg"

PanelCropFrac = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    """One physical/virtual display from the OS."""

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool

    @property
    def pixel_box(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.left + self.width, self.top + self.height)


@dataclass(frozen=True, slots=True)
class ScreenCaptureConfig:
    monitor_index: int | None = None  # None → Windows 主屏 / first primary
    crop_frac: PanelCropFrac = INFO_PANEL_CROP_FRAC
    reference_width: int = 1920
    reference_height: int = 1080
    preview_max_width: int = 960
    include_monitor_preview: bool = False
    """Full-screen ROI preview (slow on 4K); enable only for diagnostics."""


@dataclass(frozen=True, slots=True)
class ScreenCaptureResult:
    """Cropped panel PNG + metadata for UI debug."""

    panel_png: bytes
    monitor: MonitorInfo
    crop_frac: PanelCropFrac
    crop_box: tuple[int, int, int, int]
    monitor_preview_png: bytes


def fraction_to_pixel_box(
    width: int,
    height: int,
    crop: PanelCropFrac,
) -> tuple[int, int, int, int]:
    l, t, r, b = crop
    return (int(width * l), int(height * t), int(width * r), int(height * b))


def _windows_primary_rect() -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        user32 = ctypes.windll.user32
        hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return None


def _rect_matches_monitor(
    rect: tuple[int, int, int, int],
    mon: MonitorInfo,
) -> bool:
    left, top, right, bottom = rect
    return (
        mon.left == left
        and mon.top == top
        and mon.left + mon.width == right
        and mon.top + mon.height == bottom
    )


def apply_primary_flags(monitors: list[MonitorInfo]) -> list[MonitorInfo]:
    """Set ``is_primary`` from OS (Windows), not mss enumeration order."""
    primary_rect = _windows_primary_rect()
    primary_idx: int | None = None
    if primary_rect is not None:
        for m in monitors:
            if _rect_matches_monitor(primary_rect, m):
                primary_idx = m.index
                break
    if primary_idx is None and monitors:
        primary_idx = monitors[0].index
    out: list[MonitorInfo] = []
    for m in monitors:
        out.append(
            MonitorInfo(
                index=m.index,
                left=m.left,
                top=m.top,
                width=m.width,
                height=m.height,
                is_primary=(m.index == primary_idx),
            ),
        )
    return out


def monitor_label(mon: MonitorInfo) -> str:
    role = "主屏" if mon.is_primary else "副屏"
    return (
        f"#{mon.index}  {mon.width}×{mon.height}  ({role})  "
        f"桌面位置 ({mon.left}, {mon.top})"
    )


def list_monitors() -> list[MonitorInfo]:
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError(
            "实时抓屏需要 mss：pip install mss（或 pip install -e \".[capture]\")"
        ) from exc

    raw: list[MonitorInfo] = []
    with mss.mss() as sct:
        for i, mon in enumerate(sct.monitors[1:], start=1):
            raw.append(
                MonitorInfo(
                    index=i,
                    left=int(mon["left"]),
                    top=int(mon["top"]),
                    width=int(mon["width"]),
                    height=int(mon["height"]),
                    is_primary=False,
                ),
            )
    return apply_primary_flags(raw)


def resolve_monitor(
    monitors: Sequence[MonitorInfo],
    *,
    monitor_index: int | None = None,
) -> MonitorInfo:
    if monitor_index is not None:
        for m in monitors:
            if m.index == monitor_index:
                return m
        raise ValueError(f"monitor_index={monitor_index} not found")
    for m in monitors:
        if m.is_primary:
            return m
    if not monitors:
        raise RuntimeError("no monitors detected")
    return monitors[0]


def _monitor_preview_png(
    img: object,
    *,
    crop_box: tuple[int, int, int, int],
    max_width: int,
) -> bytes:
    from PIL import Image, ImageDraw

    assert isinstance(img, Image.Image)
    preview = img.copy()
    draw = ImageDraw.Draw(preview)
    draw.rectangle(crop_box, outline=(255, 64, 64), width=max(3, preview.width // 400))
    w, h = preview.size
    if w > max_width:
        scale = max_width / w
        preview = preview.resize(
            (max_width, max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    preview.save(out, format="JPEG", quality=82, optimize=False)
    return out.getvalue()


def capture_monitor_panel(
    config: ScreenCaptureConfig | None = None,
) -> ScreenCaptureResult:
    cfg = config or ScreenCaptureConfig()
    monitors = list_monitors()
    mon = resolve_monitor(monitors, monitor_index=cfg.monitor_index)

    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("需要 mss + Pillow") from exc

    with mss.mss() as sct:
        shot = sct.grab(
            {
                "left": mon.left,
                "top": mon.top,
                "width": mon.width,
                "height": mon.height,
            },
        )
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    box = fraction_to_pixel_box(mon.width, mon.height, cfg.crop_frac)
    cropped = img.crop(box)
    cw, ch = cropped.size
    if cw > cfg.reference_width or ch > cfg.reference_height:
        scale = min(cfg.reference_width / cw, cfg.reference_height / ch)
        cropped = cropped.resize(
            (max(1, int(cw * scale)), max(1, int(ch * scale))),
            Image.Resampling.LANCZOS,
        )
    panel_out = io.BytesIO()
    cropped.save(panel_out, format="PNG", compress_level=1, optimize=False)
    if cfg.include_monitor_preview:
        preview_png = _monitor_preview_png(
            img,
            crop_box=box,
            max_width=cfg.preview_max_width,
        )
    else:
        preview_png = b""
    return ScreenCaptureResult(
        panel_png=panel_out.getvalue(),
        monitor=mon,
        crop_frac=cfg.crop_frac,
        crop_box=box,
        monitor_preview_png=preview_png,
    )


def capture_monitor_png_bytes(
    config: ScreenCaptureConfig | None = None,
) -> tuple[bytes, MonitorInfo]:
    """Backward-compatible: return ``(panel_png, monitor)`` only."""
    result = capture_monitor_panel(config)
    return result.panel_png, result.monitor
