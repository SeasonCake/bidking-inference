"""Screenshot / OCR text capture → Streamlit prefill (no inference coupling)."""

from bidking_lab.capture.apply import apply_capture_result, category_for_map_id
from bidking_lab.capture.parser import parse_panel_text
from bidking_lab.capture.screen import (
    INFO_PANEL_CROP_FRAC,
    MonitorInfo,
    ScreenCaptureConfig,
    capture_monitor_png_bytes,
    fraction_to_pixel_box,
    list_monitors,
)
from bidking_lab.capture.types import CaptureParseResult, FieldSuggestion

__all__ = [
    "CaptureParseResult",
    "FieldSuggestion",
    "INFO_PANEL_CROP_FRAC",
    "MonitorInfo",
    "ScreenCaptureConfig",
    "apply_capture_result",
    "capture_monitor_png_bytes",
    "category_for_map_id",
    "fraction_to_pixel_box",
    "list_monitors",
    "parse_panel_text",
]
