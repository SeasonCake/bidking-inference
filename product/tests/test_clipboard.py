"""Clipboard image helpers (mocked, no OS clipboard)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bidking_lab.capture import clipboard as cb


def test_paths_from_list_and_string():
    assert cb._paths_from_clipboard_payload([r"C:\tmp\a.png", "x"]) == [
        r"C:\tmp\a.png",
        "x",
    ]
    assert cb._paths_from_clipboard_payload(' "C:\\tmp\\b.jpg" ') == [r"C:\tmp\b.jpg"]


def test_clipboard_loads_image_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PIL import Image

    p = tmp_path / "shot.png"
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(p)

    monkeypatch.setattr(
        "PIL.ImageGrab.grabclipboard",
        lambda: [str(p)],
    )
    data, err = cb.clipboard_image_bytes()
    assert err is None
    assert len(data) > 20


def test_clipboard_non_image_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "PIL.ImageGrab.grabclipboard",
        lambda: ["not-an-image.txt"],
    )
    data, err = cb.clipboard_image_bytes()
    assert data == b""
    assert err is not None
    assert "不是图片" in err
