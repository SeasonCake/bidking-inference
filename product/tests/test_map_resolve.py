"""Fuzzy map title resolution (105+ maps)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidking_lab.capture.map_resolve import (
    best_map_in_panel_text,
    fuzzy_match_map_name,
    normalize_map_fragment,
)
from bidking_lab.capture.parser import parse_panel_text


@pytest.fixture(scope="module")
def all_map_names() -> dict[int, str]:
    path = Path(__file__).resolve().parents[1] / "data" / "processed" / "maps.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(m["map_id"]): str(m["name"]) for m in raw}


def test_normalize_map_fragment_doomsday_typo() -> None:
    assert normalize_map_fragment("末日底护所") == "末日庇护所"


def test_normalize_modern_cargo_keeps_entertainment_suffix() -> None:
    assert normalize_map_fragment("现代货轮娱乐库") == "现代货轮娱乐库"


def test_all_unique_canonical_names_exact_match(
    all_map_names: dict[int, str],
) -> None:
    from bidking_lab.capture.map_resolve import _clean_map_title

    uniq = {_clean_map_title(n) for n in all_map_names.values() if _clean_map_title(n)}
    assert len(uniq) >= 40
    for name in uniq:
        hit = fuzzy_match_map_name(name, all_map_names)
        assert hit is not None, name
        assert hit[1] == name


@pytest.mark.parametrize(
    "line,expected_name",
    [
        ("末日底护所：完拍信息", "末日庇护所"),
        ("望族居所：竞拍信息", "望族居所"),
        ("设计师居所:竞拍信息", "设计师居所"),
        ("现代货轮娱乐库：竞拍信息", "现代货轮娱乐库"),
    ],
)
def test_fuzzy_map_lines(
    line: str, expected_name: str, all_map_names: dict[int, str],
) -> None:
    hit = fuzzy_match_map_name(
        line.split("：")[0].split(":")[0], all_map_names,
    )
    assert hit is not None
    assert hit[1] == expected_name


def test_best_map_in_panel_blob(all_map_names: dict[int, str]) -> None:
    text = "第4轮\n末日底护所：完拍信息\n所有蓝色品质藏品总占位数为35格"
    mid, name = best_map_in_panel_text(text, all_map_names)
    assert name == "末日庇护所"
    assert mid is not None


def test_parse_doomsday_typo_still_maps(all_map_names: dict[int, str]) -> None:
    r = parse_panel_text("末日底护所：完拍信息\n", map_names=all_map_names)
    assert r.map_name == "末日庇护所"


def test_json_fragment_fixes_container_ku(all_map_names: dict[int, str]) -> None:
    hit = fuzzy_match_map_name("军用物资集装箱库", all_map_names)
    assert hit is not None
    assert hit[1] == "军用物资集装箱"


def test_map_fragment_fixes_json_valid() -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "build_map_fragment_fixes.py"), "--check"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
