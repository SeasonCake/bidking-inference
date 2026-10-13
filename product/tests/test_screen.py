"""Tests for capture.screen ROI math (no display required)."""

from bidking_lab.capture.screen import (
    INFO_PANEL_CROP_FRAC,
    MonitorInfo,
    apply_primary_flags,
    fraction_to_pixel_box,
)


def test_fraction_to_pixel_box_1920x1080() -> None:
    box = fraction_to_pixel_box(1920, 1080, INFO_PANEL_CROP_FRAC)
    assert box == (576, 75, 1132, 777)


def test_info_panel_crop_within_unit_square() -> None:
    l, t, r, b = INFO_PANEL_CROP_FRAC
    assert 0 <= l < r <= 1
    assert 0 <= t < b <= 1


def test_fraction_to_pixel_box_4k() -> None:
    box = fraction_to_pixel_box(3840, 2160, INFO_PANEL_CROP_FRAC)
    assert box == (1152, 151, 2265, 1555)


def test_apply_primary_flags_uses_windows_rect(monkeypatch) -> None:
    monitors = [
        MonitorInfo(1, 0, 0, 3840, 2160, False),
        MonitorInfo(2, 3840, 0, 1920, 1080, False),
    ]
    monkeypatch.setattr(
        "bidking_lab.capture.screen._windows_primary_rect",
        lambda: (3840, 0, 5760, 1080),
    )
    flagged = apply_primary_flags(monitors)
    assert [m.index for m in flagged if m.is_primary] == [2]
