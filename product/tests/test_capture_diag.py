"""Capture diagnostics (map title OCR logging)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidking_lab.capture.diag import (
    append_capture_diag,
    collect_map_resolution_diag,
    record_capture_session,
)
from bidking_lab.capture.parser import parse_panel_text


MAP_NAMES = {2405: "望族居所", 2403: "设计师居所"}


def test_diag_line_unmatched() -> None:
    text = "完全不存在的地图名：竞拍信息\n所有蓝色品质藏品总占位数为15格"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.map_diag is not None
    assert r.map_diag.status == "line_unmatched"
    assert r.map_diag.title_lines[0].matched is False


def test_diag_no_map_line() -> None:
    text = "第4轮\n所有蓝色品质藏品总占位数为15格"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.map_diag is not None
    assert r.map_diag.status == "no_map_line"


def test_diag_resolved() -> None:
    text = "望族居所：竞拍信息\n"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.map_diag is not None
    assert r.map_diag.status == "resolved"
    assert r.map_id == 2405


def test_append_capture_diag_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BIDKING_CAPTURE_DIAG", raising=False)
    assert append_capture_diag({"x": 1}) is None


def test_append_capture_diag_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log = tmp_path / "diag.jsonl"
    monkeypatch.setenv("BIDKING_CAPTURE_DIAG", "1")
    monkeypatch.setenv("BIDKING_CAPTURE_DIAG_PATH", str(log))
    from bidking_lab.capture.diag import MapResolutionDiag

    record_capture_session(
        source="test",
        crop_panel=True,
        map_diag=MapResolutionDiag(status="no_map_line"),
        suggestion_keys=["wg_cells"],
    )
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["source"] == "test"
