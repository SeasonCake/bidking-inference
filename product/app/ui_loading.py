"""Animated loading banners for Streamlit inference status."""

from __future__ import annotations

import streamlit as st

LOADING_CSS = """
<style>
@keyframes bk-shimmer {
  0% { background-position: 200% center; }
  100% { background-position: -200% center; }
}
@keyframes bk-hourglass {
  0%, 100% { transform: rotate(0deg); opacity: 0.85; }
  50% { transform: rotate(180deg); opacity: 1; }
}
@keyframes bk-dots {
  0%, 20% { opacity: 0.25; }
  50% { opacity: 1; }
  100% { opacity: 0.25; }
}
.bk-load-banner {
  border-radius: 8px;
  padding: 0.45rem 0.65rem;
  margin: 0.15rem 0 0.35rem;
  font-size: 0.88rem;
  line-height: 1.35;
}
.bk-load-banner.loading {
  background: linear-gradient(
    90deg,
    rgba(99, 102, 241, 0.12) 0%,
    rgba(99, 102, 241, 0.32) 50%,
    rgba(99, 102, 241, 0.12) 100%
  );
  background-size: 200% auto;
  animation: bk-shimmer 2.2s linear infinite;
}
.bk-load-banner.ready {
  background: rgba(34, 197, 94, 0.12);
}
.bk-load-banner.error {
  background: rgba(239, 68, 68, 0.12);
}
.bk-hourglass {
  display: inline-block;
  animation: bk-hourglass 2.4s ease-in-out infinite;
  margin-right: 0.35rem;
}
.bk-dots span {
  animation: bk-dots 1.2s ease-in-out infinite;
}
.bk-dots span:nth-child(2) { animation-delay: 0.2s; }
.bk-dots span:nth-child(3) { animation-delay: 0.4s; }
</style>
"""


_loading_css_done = False


def inject_loading_css() -> None:
    global _loading_css_done
    if _loading_css_done:
        return
    _loading_css_done = True
    st.markdown(LOADING_CSS, unsafe_allow_html=True)


def _dots_html() -> str:
    return '<span class="bk-dots"><span>.</span><span>.</span><span>.</span></span>'


def loading_slot():
    """Return a placeholder; call ``.empty()`` after work finishes to dismiss banner."""
    return st.empty()


def render_status_banner(
    *,
    kind: str,
    message: str,
    detail: str = "",
) -> None:
    """HTML banner with shimmer / hourglass."""
    inject_loading_css()
    if kind == "loading":
        inner = (
            f'<span class="bk-hourglass">⏳</span>'
            f"<strong>{message}</strong> {_dots_html()}"
        )
        cls = "loading"
    elif kind == "ready":
        inner = f"✅ <strong>{message}</strong>"
        cls = "ready"
    elif kind == "error":
        inner = f"⚠️ <strong>{message}</strong>"
        cls = "error"
    else:
        inner = message
        cls = "loading"
    extra = (
        f'<br><span style="font-size:0.82rem;opacity:0.85">{detail}</span>'
        if detail
        else ""
    )
    st.markdown(
        f'<div class="bk-load-banner {cls}">{inner}{extra}</div>',
        unsafe_allow_html=True,
    )
