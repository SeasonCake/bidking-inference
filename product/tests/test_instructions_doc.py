"""User-facing instructions doc is present and linked from the app."""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_instructions_markdown_exists() -> None:
    path = _REPO / "docs" / "INSTRUCTIONS.zh-CN.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "flowchart" in text
    assert "抓取当前屏幕" in text


def test_instructions_html_exists() -> None:
    path = _REPO / "docs" / "instructions.html"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "操作说明" in text
    assert "出价推荐" in text


def test_instructions_html_path_local() -> None:
    import sys

    _app = _REPO / "app"
    if str(_app) not in sys.path:
        sys.path.insert(0, str(_app))
    from user_guide import instructions_html_path

    path = instructions_html_path()
    assert path is not None
    assert path.name == "instructions.html"
