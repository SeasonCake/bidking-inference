"""Tests for OCR string normalization."""

from bidking_lab.capture.ocr_normalize import normalize_ocr_text


def test_round4_typo_bundle() -> None:
    raw = "所有蓝色品质置品总占位数为35格\n所有自色和绿色品质震品总占位数为12格"
    out = normalize_ocr_text(raw)
    assert "藏品" in out
    assert "白色" in out
    assert "置品" not in out
    assert "自色" not in out


def test_scan_typos() -> None:
    assert "扫描" in normalize_ocr_text("极品扫罐：代优品扫装")


def test_duplicate_youpin_junge_line() -> None:
    out = normalize_ocr_text("优品优品均格：优品均格均格")
    assert "优品优品" not in out
    assert "均格均格" not in out
    assert out.startswith("优品均格")


def test_ethan_skill_colon_inserted() -> None:
    assert normalize_ocr_text("伊森空间觉知") == "伊森：空间觉知"
