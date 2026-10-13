"""Overlay OCR panel ROI on a game screenshot (dev calibration tool).

The green box is ``INFO_PANEL_CROP_FRAC`` — the **center-left text panel**
(第N轮、各品质总占位、地图名等) used by ``capture/ocr.py``. It is **not**
the right-side warehouse grid (战利品格子); that would be a future ROI.

Usage::

    cd bidking-lab
    C:\\Python313\\python.exe scripts/preview_panel_roi.py
    C:\\Python313\\python.exe scripts/preview_panel_roi.py path/to/shot.png --crop 0.17,0.07,0.52,0.72
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_IMAGE = _REPO / "data" / "samples" / "panel_round4_1920x1080.png"
_DEFAULT_OUT = _REPO / "data" / "samples" / "panel_round4_roi_preview.png"


def _parse_crop(s: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("crop must be four comma-separated fractions")
    l, t, r, b = parts
    if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
        raise argparse.ArgumentTypeError("crop must satisfy 0<=l<r<=1 and 0<=t<b<=1")
    return (l, t, r, b)


def ocr_on_crop(
    image_path: Path,
    crop: tuple[float, float, float, float],
) -> tuple[str, str | None]:
    """Run OCR on the cropped panel only (same path as Streamlit after crop)."""
    from bidking_lab.capture.ocr import crop_info_panel, image_bytes_to_text

    cropped = crop_info_panel(image_path.read_bytes(), crop=crop)
    return image_bytes_to_text(cropped, crop_panel=False)


def render_roi_preview(
    image_path: Path,
    *,
    crop: tuple[float, float, float, float],
    out_path: Path,
    title: str | None = None,
    show_warehouse_hint: bool = False,
) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    from bidking_lab.capture.ocr import crop_info_panel
    from bidking_lab.capture.screen import fraction_to_pixel_box

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    box = fraction_to_pixel_box(w, h, crop)
    l, t, r, b = box

    overlay = img.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    # Semi-transparent fill + solid border
    draw.rectangle(box, fill=(0, 255, 120, 40), outline=(0, 255, 120), width=4)
    try:
        font = ImageFont.truetype("arial.ttf", max(14, h // 60))
    except OSError:
        font = ImageFont.load_default()
    label = title or (
        f"OCR panel ROI  L={crop[0]:.2f} T={crop[1]:.2f} "
        f"R={crop[2]:.2f} B={crop[3]:.2f}  px={box}"
    )
    draw.text((l + 8, max(0, t - 28)), label, fill=(0, 255, 120), font=font)
    draw.text(
        (l + 8, t + 8),
        "文字面板 (OCR)",
        fill=(255, 255, 255),
        font=font,
        stroke_width=2,
        stroke_fill=(0, 0, 0),
    )

    if show_warehouse_hint:
        wh_l = int(w * 0.52)
        wh_t = int(h * 0.07)
        wh_r = int(w * 0.98)
        wh_b = int(h * 0.88)
        draw.rectangle((wh_l, wh_t, wh_r, wh_b), outline=(255, 80, 80), width=2)
        draw.text(
            (wh_l + 8, wh_t + 8),
            "战利品格子 (未来视觉，非当前 OCR)",
            fill=(255, 120, 120),
            font=font,
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )

    cropped_bytes = crop_info_panel(image_path.read_bytes(), crop=crop)
    cropped = Image.open(io.BytesIO(cropped_bytes)).convert("RGB")

    # Side-by-side: full overlay | cropped OCR input
    gap = 12
    panel_h = max(overlay.height, cropped.height)
    canvas = Image.new(
        "RGB",
        (overlay.width + gap + cropped.width, panel_h + 36),
        (24, 26, 30),
    )
    canvas.paste(overlay, (0, 18))
    canvas.paste(cropped, (overlay.width + gap, 18))
    cap_draw = ImageDraw.Draw(canvas)
    cap_draw.text((8, 0), "左：叠框全图  |  右：裁切后送入 OCR", fill=(200, 200, 200), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)
    return out_path


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    from bidking_lab.capture.screen import INFO_PANEL_CROP_FRAC

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        default=_DEFAULT_IMAGE,
        help=f"screenshot path (default: {_DEFAULT_IMAGE.relative_to(_REPO)})",
    )
    parser.add_argument(
        "--crop",
        type=_parse_crop,
        default=INFO_PANEL_CROP_FRAC,
        metavar="L,T,R,B",
        help="normalized crop fractions (default: INFO_PANEL_CROP_FRAC)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUT,
        help="output PNG path",
    )
    parser.add_argument(
        "--warehouse-hint",
        action="store_true",
        help="draw red reference box for future warehouse ROI (off by default)",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="print OCR text for the green crop",
    )
    args = parser.parse_args(argv)

    if not args.image.is_file():
        print(f"Image not found: {args.image}", file=sys.stderr)
        return 1

    out = render_roi_preview(
        args.image,
        crop=args.crop,
        out_path=args.output,
        show_warehouse_hint=args.warehouse_hint,
    )
    print(f"Wrote {out}")
    print(f"  source: {args.image} ({args.image.stat().st_size // 1024} KiB)")
    print(f"  crop:   {args.crop}")
    if args.ocr:
        text, err = ocr_on_crop(args.image, args.crop)
        if err:
            print(f"  OCR error: {err}")
        else:
            print("  OCR text:")
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
