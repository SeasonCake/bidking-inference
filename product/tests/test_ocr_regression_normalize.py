"""Regression tests for OCR normalize (fixture strings + optional user screenshots)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidking_lab.capture.ocr import image_bytes_to_text
from bidking_lab.capture.ocr_normalize import normalize_ocr_text
from bidking_lab.capture.parser import parse_panel_text

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ocr_regression"
_CASES_PATH = _FIXTURES / "normalize_cases.json"

# User-provided regression screenshots (skip if not on disk).
REGRESSION_IMAGES: tuple[tuple[str, Path], ...] = (
    ("desktop_r3_ethan", Path(r"C:\Users\example\Pictures\Desktop Screenshot 2026.05.15 - 11.10.26.79.png")),
    ("wechat_r4", Path(r"C:\Users\example\Pictures\微信图片_20260517163704.jpg")),
    ("wechat_r5_gabriela", Path(r"C:\Users\example\Pictures\微信图片_20260517145925.jpg")),
    ("wechat_r3_warehouse", Path(r"C:\Users\example\Pictures\微信图片_20260517135143.jpg")),
    ("wechat_r4_metrics", Path(r"C:\Users\example\Pictures\微信图片_20260517135136.jpg")),
    ("wechat_r4_aisha", Path(r"C:\Users\example\Pictures\微信图片_20260517223852.jpg")),
)

_REPO_SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "panel_round4_1920x1080.png"


def _load_map_names() -> dict[int, str]:
    maps_path = Path(__file__).resolve().parents[1] / "data" / "processed" / "maps.json"
    if not maps_path.is_file():
        return {}
    raw = json.loads(maps_path.read_text(encoding="utf-8"))
    return {int(m["map_id"]): str(m["name"]) for m in raw}


@pytest.fixture(scope="module")
def map_names() -> dict[int, str]:
    return _load_map_names()


@pytest.fixture(scope="module")
def ocr_text_cache() -> dict[Path, tuple[str, str | None]]:
    return {}


def _ocr_text_for(
    path: Path,
    cache: dict[Path, tuple[str, str | None]],
) -> tuple[str, str | None]:
    """Run OCR at most once per image path within this module."""
    resolved = path.resolve()
    if resolved not in cache:
        cache[resolved] = image_bytes_to_text(path.read_bytes(), crop_panel=True)
    return cache[resolved]


def _ocr_text_or_skip(
    path: Path,
    cache: dict[Path, tuple[str, str | None]],
) -> str:
    text, err = _ocr_text_for(path, cache)
    if err is not None:
        pytest.skip(err)
    if not text:
        pytest.skip("OCR returned no text")
    return text


@pytest.mark.parametrize("case", json.loads(_CASES_PATH.read_text(encoding="utf-8")), ids=lambda c: c["id"])
def test_normalize_fixture_cases(case: dict) -> None:
    out = normalize_ocr_text(case["raw"])
    for frag in case.get("expect_contains", []):
        assert frag in out, f"missing {frag!r} in {out!r}"
    for frag in case.get("expect_not_contains", []):
        assert frag not in out, f"unexpected {frag!r} in {out!r}"


@pytest.mark.slow
def test_panel_round4_sample_normalize_and_parse(
    map_names: dict[int, str],
    ocr_text_cache: dict[Path, tuple[str, str | None]],
) -> None:
    if not _REPO_SAMPLES.is_file():
        pytest.skip("panel_round4 sample missing")
    text = _ocr_text_or_skip(_REPO_SAMPLES, ocr_text_cache)
    norm = normalize_ocr_text(text)
    assert "轮廓" in norm
    assert "轮属" not in norm
    parsed = parse_panel_text(text, map_names=map_names)
    keys = set(parsed.suggestion_map().keys())
    assert {"blue_cells", "wg_cells", "purple_cells", "gold_cells"}.issubset(keys)


@pytest.mark.parametrize("label,path", REGRESSION_IMAGES, ids=[x[0] for x in REGRESSION_IMAGES])
@pytest.mark.slow
def test_user_regression_image_ocr_smoke(
    label: str,
    path: Path,
    ocr_text_cache: dict[Path, tuple[str, str | None]],
) -> None:
    if not path.is_file():
        pytest.skip(f"missing {path}")
    text = _ocr_text_or_skip(path, ocr_text_cache)
    norm = normalize_ocr_text(text)
    assert norm  # normalize must not blank the panel


@pytest.mark.slow
@pytest.mark.parametrize("label,path,expected_keys", [
    (
        "desktop_r3_ethan",
        REGRESSION_IMAGES[0][1],
        {"wg_cells", "blue_cells", "purple_avg_raw"},
    ),
    (
        "wechat_r4",
        REGRESSION_IMAGES[1][1],
        {"wg_cells", "blue_cells", "purple_cells", "gold_cells"},
    ),
    (
        "wechat_r3_warehouse",
        REGRESSION_IMAGES[3][1],
        {"warehouse_cells", "wg_cells", "blue_cells", "purple_avg_raw"},
    ),
    (
        "wechat_r4_metrics",
        REGRESSION_IMAGES[4][1],
        {"wg_cells", "blue_cells", "purple_avg_raw", "purple_value"},
    ),
    (
        "wechat_r4_aisha",
        REGRESSION_IMAGES[5][1],
        {"gold_cells"},
    ),
], ids=["desktop", "wechat_r4", "wechat_r3_wh", "wechat_metrics", "wechat_aisha"])
def test_user_regression_parse_keys(
    label: str,
    path: Path,
    expected_keys: set[str],
    map_names: dict[int, str],
    ocr_text_cache: dict[Path, tuple[str, str | None]],
) -> None:
    if not path.is_file():
        pytest.skip(f"missing {path}")
    if not map_names:
        pytest.skip("maps.json missing")
    text = _ocr_text_or_skip(path, ocr_text_cache)
    parsed = parse_panel_text(text, map_names=map_names)
    got = set(parsed.suggestion_map().keys())
    missing = expected_keys - got
    assert not missing, f"missing keys {missing}; got {sorted(got)}"

@pytest.mark.slow
def test_user_regression_map_names_when_present(
    map_names: dict[int, str],
    ocr_text_cache: dict[Path, tuple[str, str | None]],
) -> None:
    if not map_names:
        pytest.skip("maps.json missing")
    cases = [
        (REGRESSION_IMAGES[0][1], "望族居所"),
        (REGRESSION_IMAGES[2][1], "末日庇护所"),
    ]
    # 35136 / 223852 crops often omit the map title line — no assert
    for path, name_fragment in cases:
        if not path.is_file():
            pytest.skip(f"missing {path}")
        text = _ocr_text_or_skip(path, ocr_text_cache)
        parsed = parse_panel_text(text, map_names=map_names)
        assert parsed.map_id is not None
        assert name_fragment in (parsed.map_name or "")
