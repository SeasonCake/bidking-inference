"""Optional OCR helpers — soft dependency."""

from __future__ import annotations

import io
import logging
import threading
from typing import Any, Literal

from bidking_lab.capture.log_util import LOG, configure_capture_logging
from bidking_lab.capture.ocr_normalize import normalize_ocr_text
from bidking_lab.capture.screen import INFO_PANEL_CROP_FRAC

configure_capture_logging()

_ENGINE: Any = None
_warm_lock = threading.Lock()
_warm_status: Literal["idle", "loading", "ready", "error"] = "idle"
_warm_error: str | None = None

_PANEL_CROP = INFO_PANEL_CROP_FRAC
# OCR path calibrated on 1920×1080 game captures (notebook 06).
REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
_last_ocr_panel_meta: dict[str, object] = {}


def last_ocr_panel_meta() -> dict[str, object]:
    """Metadata from the most recent ``panel_rgb_array_for_ocr`` call."""
    return dict(_last_ocr_panel_meta)
# ``optimize=True`` is very slow on large screenshots; level 1 is enough for OCR input.
_PNG_COMPRESS_LEVEL = 1


def _save_png_bytes(img: Any) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG", compress_level=_PNG_COMPRESS_LEVEL)
    return out.getvalue()


def create_ocr_engine() -> Any:
    """Construct a new RapidOCR instance (expensive — cache in Streamlit)."""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _get_engine() -> Any:
    global _ENGINE  # noqa: PLW0603
    if _ENGINE is None:
        _ENGINE = create_ocr_engine()
    return _ENGINE


def _warmup_payload_bytes() -> bytes:
    from bidking_lab.capture.screen import OCR_WARMUP_SAMPLE

    if OCR_WARMUP_SAMPLE.is_file():
        return OCR_WARMUP_SAMPLE.read_bytes()
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("需要 Pillow：pip install pillow") from exc
    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), color=(32, 34, 38)).save(buf, format="PNG")
    return buf.getvalue()


def panel_rgb_array_for_ocr(data: bytes, *, crop_panel: bool = True) -> Any:
    """Decode once, crop/resize, return RGB ``uint8`` array for RapidOCR (no PNG round-trip)."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        if crop_panel:
            data = crop_info_panel(data)
        data = fit_reference_frame(data)
        import numpy as np
        from PIL import Image

        return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))

    img = Image.open(io.BytesIO(data)).convert("RGB")
    decoded_w, decoded_h = img.size
    crop_applied = False
    if crop_panel:
        w, h = img.size
        l, t, r, b = _PANEL_CROP
        img = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
        crop_applied = True
    w, h = img.size
    resize_applied = False
    if w > REFERENCE_WIDTH or h > REFERENCE_HEIGHT:
        resize_applied = True
        scale = min(REFERENCE_WIDTH / w, REFERENCE_HEIGHT / h)
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    arr = np.asarray(img)
    global _last_ocr_panel_meta  # noqa: PLW0603
    _last_ocr_panel_meta = {
        "decoded_size": [decoded_w, decoded_h],
        "crop_applied": crop_applied,
        "resize_applied": resize_applied,
        "crop_panel": crop_panel,
        "shape": list(arr.shape),
    }
    return arr


def prepare_image_for_ocr(data: bytes, *, crop_panel: bool = True) -> bytes:
    """Encode panel bytes for debug/warmup paths that need PNG."""
    try:
        from PIL import Image
    except ImportError:
        if crop_panel:
            data = crop_info_panel(data)
        return fit_reference_frame(data)

    img = Image.open(io.BytesIO(data))
    changed = False
    if crop_panel:
        w, h = img.size
        l, t, r, b = _PANEL_CROP
        img = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
        changed = True
    w, h = img.size
    if w > REFERENCE_WIDTH or h > REFERENCE_HEIGHT:
        scale = min(REFERENCE_WIDTH / w, REFERENCE_HEIGHT / h)
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
        changed = True
    if not changed:
        return data
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=_PNG_COMPRESS_LEVEL)
    return buf.getvalue()


def warmup_engine(engine: Any) -> None:
    """Two inference passes (loads ONNX weights; 2nd pass mirrors first real OCR)."""
    payload = _warmup_payload_bytes()
    arr = panel_rgb_array_for_ocr(payload, crop_panel=True)
    for pass_idx in (1, 2):
        result, _ = engine(arr)
        if not result and pass_idx == 1:
            LOG.warning("OCR warm-up: empty result on sample image")


def warmup_engine_screen_primary(engine: Any) -> bool:
    """Extra warm-up on live primary-monitor panel crop (matches grab-screen path)."""
    try:
        from bidking_lab.capture.screen import (
            ScreenCaptureConfig,
            capture_monitor_panel,
            list_monitors,
            resolve_monitor,
        )
    except ImportError:
        return False

    try:
        monitors = list_monitors()
        mon = resolve_monitor(monitors)
        cap = capture_monitor_panel(
            ScreenCaptureConfig(
                monitor_index=mon.index,
                include_monitor_preview=False,
            ),
        )
        arr = panel_rgb_array_for_ocr(cap.panel_png, crop_panel=False)
        for _ in range(2):
            engine(arr)
        LOG.info(
            "OCR screen warm-up ok: monitor #%s %sx%s",
            mon.index,
            mon.width,
            mon.height,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("OCR screen warm-up skipped: %s", exc)
        return False


def warm_ocr_engine() -> None:
    """Load RapidOCR via the same crop/resize path as real screenshots."""
    global _ENGINE  # noqa: PLW0603
    eng = create_ocr_engine()
    warmup_engine(eng)
    _ENGINE = eng


def bind_ocr_engine(engine: Any) -> None:
    """Pin the process-global engine (used when Streamlit caches the instance)."""
    global _ENGINE  # noqa: PLW0603
    _ENGINE = engine


def start_ocr_warmup_background() -> None:
    """Start one background warm-up per process (does not block the UI thread)."""
    global _warm_status, _warm_error  # noqa: PLW0603
    with _warm_lock:
        if _warm_status in ("loading", "ready"):
            return
        _warm_status = "loading"
        _warm_error = None

    def _worker() -> None:
        global _warm_status, _warm_error  # noqa: PLW0603
        try:
            warm_ocr_engine()
            with _warm_lock:
                _warm_status = "ready"
        except Exception as exc:  # noqa: BLE001
            with _warm_lock:
                _warm_status = "error"
                _warm_error = str(exc)

    threading.Thread(target=_worker, daemon=True, name="ocr-warmup").start()


def ocr_warmup_status() -> tuple[str, str | None]:
    """Return ``(status, error)`` where status is idle|loading|ready|error."""
    with _warm_lock:
        return _warm_status, _warm_error


def crop_info_panel(
    data: bytes,
    *,
    crop: tuple[float, float, float, float] = _PANEL_CROP,
) -> bytes:
    """Crop to the center-left bidding panel (less noise, faster OCR)."""
    try:
        from PIL import Image
    except ImportError:
        return data

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    l, t, r, b = crop
    box = (int(w * l), int(h * t), int(w * r), int(h * b))
    cropped = img.crop(box)
    return _save_png_bytes(cropped)


def fit_reference_frame(
    data: bytes,
    *,
    ref_width: int = REFERENCE_WIDTH,
    ref_height: int = REFERENCE_HEIGHT,
) -> bytes:
    """Downscale only when larger than the reference frame (faster OCR, stable ROI)."""
    try:
        from PIL import Image
    except ImportError:
        return data

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if w <= ref_width and h <= ref_height:
        return data
    scale = min(ref_width / w, ref_height / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return _save_png_bytes(img)


def image_bytes_to_text(
    data: bytes,
    *,
    crop_panel: bool = True,
    engine: Any | None = None,
) -> tuple[str, str | None]:
    """Run OCR if ``rapidocr_onnxruntime`` is installed.

    Returns ``(text, error_message)``. On success ``error_message`` is None.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except ImportError:
        return "", (
            "未安装 OCR 引擎。请用与 Streamlit 相同的 Python 执行：\n"
            "C:\\Python313\\python.exe -m pip install rapidocr-onnxruntime "
            "--default-timeout=600\n"
            "或手动复制游戏左侧文字到文本框。"
        )

    try:
        rgb = panel_rgb_array_for_ocr(data, crop_panel=crop_panel)
        ocr_engine = engine or _get_engine()
        meta = last_ocr_panel_meta()
        if LOG.isEnabledFor(logging.INFO):
            LOG.info(
                "OCR input shape=%sx%s crop_panel=%s resize_applied=%s decoded=%s",
                getattr(rgb, "shape", ("?", "?"))[1],
                getattr(rgb, "shape", ("?", "?"))[0],
                crop_panel,
                meta.get("resize_applied"),
                meta.get("decoded_size"),
            )
        result, _ = ocr_engine(rgb)
    except Exception as exc:  # noqa: BLE001
        return "", f"OCR 失败: {exc}"

    if not result:
        return "", "OCR 未识别到文字，请检查截图区域或改用手动粘贴。"

    lines = [str(row[1]).strip() for row in result if len(row) > 1 and row[1]]
    text = normalize_ocr_text("\n".join(lines))
    LOG.info(
        "OCR ok: %d lines, crop_panel=%s, crop_frac=%s",
        len(lines),
        crop_panel,
        _PANEL_CROP if crop_panel else None,
    )
    if LOG.isEnabledFor(logging.DEBUG):
        LOG.debug("OCR text:\n%s", text[:2000])
    return text, None
