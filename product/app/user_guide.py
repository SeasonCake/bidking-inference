"""User guide links and helpers (see ``docs/INSTRUCTIONS.zh-CN.md`` / ``docs/instructions.html``)."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import streamlit as st

_REPO = Path(__file__).resolve().parent.parent
_INSTRUCTIONS_MD = _REPO / "docs" / "INSTRUCTIONS.zh-CN.md"
_INSTRUCTIONS_HTML = _REPO / "docs" / "instructions.html"
_GITHUB_INSTRUCTIONS_HTML = (
    "https://github.com/SeasonCake/bidking-lab/blob/main/docs/instructions.html"
)


def instructions_html_path() -> Path | None:
    return _INSTRUCTIONS_HTML if _INSTRUCTIONS_HTML.is_file() else None


def open_instructions_html() -> None:
    """Open the static manual in the system browser (file:// is blocked from Streamlit)."""
    path = instructions_html_path()
    if path is not None:
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            webbrowser.open(path.resolve().as_uri())
        return
    webbrowser.open(_GITHUB_INSTRUCTIONS_HTML)


def render_instructions_link(
    *,
    label: str = "📖 操作说明（浏览器打开）",
    help: str = "用系统默认浏览器打开说明页（本地 HTML，不占侧栏）",
) -> None:
    st.button(label, key="open_instructions_html", help=help, on_click=open_instructions_html)
    if instructions_html_path() is not None:
        st.caption(f"本地：`{instructions_html_path().relative_to(_REPO)}`")
    else:
        st.caption("将打开 GitHub 上的说明页。")
