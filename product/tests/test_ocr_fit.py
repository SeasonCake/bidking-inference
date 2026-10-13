"""OCR reference-frame downscale (no RapidOCR required)."""

import io

from PIL import Image

from bidking_lab.capture.ocr import REFERENCE_HEIGHT, REFERENCE_WIDTH, fit_reference_frame


def _png_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def test_fit_reference_frame_unchanged_at_1080p() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (REFERENCE_WIDTH, REFERENCE_HEIGHT), (0, 0, 0)).save(buf, "PNG")
    raw = buf.getvalue()
    assert fit_reference_frame(raw) == raw


def test_fit_reference_frame_scales_4k_down() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (3840, 2160), (0, 0, 0)).save(buf, "PNG")
    out = fit_reference_frame(buf.getvalue())
    w, h = _png_size(out)
    assert w == REFERENCE_WIDTH
    assert h == REFERENCE_HEIGHT


def test_fit_reference_frame_preserves_aspect_tall_crop() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (800, 1600), (0, 0, 0)).save(buf, "PNG")
    out = fit_reference_frame(buf.getvalue())
    w, h = _png_size(out)
    assert w == 540
    assert h == REFERENCE_HEIGHT
