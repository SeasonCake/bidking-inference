"""Streamlit UI theme: sidebar sections, tabs, and shared accents (release polish)."""

from __future__ import annotations

from functools import lru_cache
import inspect

import streamlit as st

# Align with chart_style.py slate / indigo palette
_ACCENT_SESSION = "#6366f1"
_ACCENT_WAREHOUSE = "#0d9488"
_ACCENT_CAPTURE = "#d97706"
_ACCENT_HINT = "#7c3aed"


@lru_cache(maxsize=None)
def layout_width_kwargs(component: str, *, stretch: bool) -> dict[str, object]:
    """Use the non-deprecated Streamlit width API available at runtime."""
    params = inspect.signature(getattr(st, component)).parameters
    if "width" in params:
        return {"width": "stretch" if stretch else "content"}
    return {"use_container_width": stretch}

APP_THEME_CSS = f"""
<style>
/* ----- Main area ----- */
[data-testid="stAppViewContainer"] .main .block-container {{
  padding-top: 1.25rem;
  max-width: 92rem;
}}
[data-testid="stAppViewContainer"] .main h1 {{
  font-weight: 700;
  letter-spacing: -0.02em;
  background: linear-gradient(
    105deg,
    {_ACCENT_SESSION} 0%,
    {_ACCENT_HINT} 42%,
    #334155 88%
  );
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  padding-bottom: 0.1rem;
}}
.bk-muted {{
  color: rgba(100, 116, 139, 0.95);
  font-size: 0.88rem;
  line-height: 1.45;
  margin: 0.1rem 0 0.35rem;
}}
.bk-muted a {{
  color: {_ACCENT_SESSION};
  text-decoration: none;
  font-weight: 500;
}}
.bk-muted a:hover {{
  text-decoration: underline;
}}
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  gap: 0.35rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.35);
}}
[data-testid="stTabs"] [data-baseweb="tab"] {{
  border-radius: 8px 8px 0 0;
  padding: 0.45rem 0.85rem;
  font-weight: 500;
}}
[data-testid="stTabs"] [aria-selected="true"] {{
  background: rgba(99, 102, 241, 0.12);
  color: {_ACCENT_SESSION};
}}
.bk-tab-status {{
  min-height: 1.35rem;
  font-size: 0.82rem;
  color: rgba(71, 85, 105, 0.95);
  margin: 0 0 0.35rem;
  line-height: 1.35;
}}
.bk-tab-status--active {{
  color: {_ACCENT_HINT};
  font-weight: 500;
}}
[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(button[kind="primary"]) button {{
  min-height: 2.4rem;
}}
.main h3, .main [data-testid="stHeader"] h3 {{
  font-size: 1.05rem;
  font-weight: 600;
  color: #3730a3;
  border-left: 3px solid {_ACCENT_SESSION};
  padding-left: 0.55rem;
  margin-top: 0.75rem;
  margin-bottom: 0.35rem;
}}
.bk-tab-lead {{
  font-size: 0.88rem;
  color: rgba(71, 85, 105, 0.95);
  line-height: 1.45;
  margin: 0 0 0.5rem;
  padding: 0.4rem 0.65rem;
  border-radius: 8px;
  background: rgba(99, 102, 241, 0.06);
  border: 1px solid rgba(99, 102, 241, 0.12);
}}

/* ----- Sidebar compact layout ----- */
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
  padding-top: 0.35rem;
}}
[data-testid="stSidebar"] .block-container {{
  padding-top: 0.5rem;
  padding-bottom: 0.75rem;
}}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div {{
  gap: 0.18rem;
}}
[data-testid="stSidebar"] hr, .bk-hr {{
  margin: 0.35rem 0;
  border: none;
  border-top: 1px solid rgba(148, 163, 184, 0.28);
}}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4,
[data-testid="stSidebar"] h5, [data-testid="stSidebar"] h6 {{
  font-size: 0.95rem;
  margin: 0.15rem 0 0.05rem;
  padding: 0;
  line-height: 1.25;
}}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{
  font-size: 0.86rem;
  margin-bottom: 0.05rem;
}}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] p {{
  line-height: 1.3;
}}
[data-testid="stSidebar"] .stCaption {{
  margin-top: -0.1rem;
  margin-bottom: 0.05rem;
  font-size: 0.8rem;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] section {{
  padding-top: 0;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] small {{
  display: none;
}}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
  min-height: 5.5rem;
  padding: 0.25rem 0.5rem 0.65rem;
  border-radius: 10px;
  border-color: rgba(99, 102, 241, 0.25) !important;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] section {{
  padding-top: 0.2rem !important;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderFiles"] {{
  margin-top: 0.45rem !important;
  padding-top: 0.1rem !important;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {{
  margin-top: 0.35rem !important;
}}
[data-testid="stSidebar"] div[data-testid="stAlert"] p {{
  margin: 0.05rem 0 !important;
  line-height: 1.32 !important;
  font-size: 0.82rem;
}}
[data-testid="stSidebar"] div[data-testid="stAlert"] {{
  padding: 0.35rem 0.5rem !important;
  margin-bottom: 0.1rem !important;
}}
[data-testid="stSidebar"] .bk-load-banner {{
  padding: 0.3rem 0.5rem !important;
  margin: 0.05rem 0 0.15rem !important;
  font-size: 0.84rem !important;
  border-radius: 8px;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] details {{
  margin-bottom: 0.1rem;
  border-radius: 8px;
  border-color: rgba(148, 163, 184, 0.35);
}}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {{
  padding-top: 0.2rem;
  padding-bottom: 0.2rem;
  min-height: 0;
  font-size: 0.86rem;
}}
[data-testid="stSidebar"] [data-baseweb="radio"] {{
  margin-bottom: 0.1rem;
}}

/* Colored sidebar section headers */
.bk-section {{
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0.4rem 0 0.28rem;
  padding: 0.22rem 0.5rem;
  border-radius: 6px;
  background: rgba(99, 102, 241, 0.06);
}}
.bk-section__bar {{
  flex-shrink: 0;
  width: 4px;
  height: 1.15em;
  border-radius: 3px;
  background: var(--bk-accent, {_ACCENT_SESSION});
}}
.bk-section__icon {{
  font-size: 1rem;
  line-height: 1;
  opacity: 0.92;
}}
.bk-section__label {{
  font-size: 1.02rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--bk-accent, {_ACCENT_SESSION});
}}
.bk-section--session {{
  --bk-accent: {_ACCENT_SESSION};
  background: rgba(99, 102, 241, 0.09);
}}
.bk-section--warehouse {{
  --bk-accent: {_ACCENT_WAREHOUSE};
  background: rgba(13, 148, 136, 0.08);
}}
.bk-section--capture {{
  --bk-accent: {_ACCENT_CAPTURE};
  background: rgba(217, 119, 6, 0.08);
}}
.bk-section--advanced {{
  --bk-accent: #64748b;
  background: rgba(100, 116, 139, 0.08);
}}
.bk-section--advanced .bk-section__label {{
  font-size: 0.92rem;
  font-weight: 600;
}}

/* Primary actions in sidebar */
[data-testid="stSidebar"] button[kind="primary"] {{
  background: linear-gradient(135deg, {_ACCENT_SESSION}, {_ACCENT_HINT});
  border: none;
}}

/* Streamlit dark theme */
[data-theme="dark"] [data-testid="stAppViewContainer"] .main h1 {{
  background: none;
  -webkit-text-fill-color: #e2e8f0;
  color: #e2e8f0;
}}
[data-theme="dark"] .main h3,
[data-theme="dark"] .main [data-testid="stHeader"] h3 {{
  color: #c7d2fe;
  border-left-color: {_ACCENT_SESSION};
}}
[data-theme="dark"] .bk-muted {{
  color: rgba(148, 163, 184, 0.92);
}}
[data-theme="dark"] .bk-tab-lead {{
  background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.22);
  color: rgba(226, 232, 240, 0.9);
}}
[data-theme="dark"] .bk-section--session {{
  background: rgba(99, 102, 241, 0.14);
}}
[data-theme="dark"] .bk-section--warehouse {{
  background: rgba(13, 148, 136, 0.12);
}}
[data-theme="dark"] .bk-section--capture {{
  background: rgba(217, 119, 6, 0.12);
}}
</style>
"""


def inject_app_theme() -> None:
    """Inject global CSS once per script run."""
    from ui_loading import LOADING_CSS

    st.markdown(APP_THEME_CSS + LOADING_CSS, unsafe_allow_html=True)


def sidebar_section(
    label: str,
    *,
    variant: str = "session",
    icon: str = "",
) -> None:
    """Colored section title for the sidebar."""
    icon_html = (
        f'<span class="bk-section__icon">{icon}</span>' if icon else ""
    )
    st.markdown(
        f'<div class="bk-section bk-section--{variant}">'
        f'<span class="bk-section__bar"></span>'
        f"{icon_html}"
        f'<span class="bk-section__label">{label}</span></div>',
        unsafe_allow_html=True,
    )


def sidebar_divider() -> None:
    st.markdown('<hr class="bk-hr">', unsafe_allow_html=True)


def muted_caption(markdown: str) -> None:
    """Disclaimer / secondary line under the page title."""
    st.markdown(f'<p class="bk-muted">{markdown}</p>', unsafe_allow_html=True)


def tab_lead(text: str) -> None:
    """Soft intro strip at the top of a main tab."""
    st.markdown(f'<p class="bk-tab-lead">{text}</p>', unsafe_allow_html=True)


def hint_tab_label(*, infer_status: str, done_flash: bool) -> str:
    """Fixed label for the hint tab (status shown via ``hint_tab_status_line``)."""
    del infer_status, done_flash
    return "\U0001f3af \u51fa\u4ef7\u63a8\u8350"


def hint_tab_status_line(*, infer_status: str, done_flash: bool) -> str:
    """One-line status above tabs; keeps button widths stable."""
    if infer_status == "running":
        return "\u540e\u53f0\u51fa\u4ef7\u63a8\u65ad\u8fdb\u884c\u4e2d\u2026"
    if done_flash:
        return "\u51fa\u4ef7\u63a8\u65ad\u5df2\u5b8c\u6210\uff0c\u8bf7\u67e5\u770b\u4e0b\u65b9\u7ed3\u679c"
    return ""


def render_tab_status_line(text: str) -> None:
    """Reserved strip above main tab buttons (fixed layout)."""
    cls = "bk-tab-status bk-tab-status--active" if text else "bk-tab-status"
    body = text or "\u00a0"
    st.markdown(f'<p class="{cls}">{body}</p>', unsafe_allow_html=True)


def render_main_tab_nav(
    *,
    keys: list[str],
    labels: dict[str, str],
    session_key: str = "_main_tab",
) -> str:
    """Session-persisted tab bar (survives st.rerun; unlike st.tabs)."""
    active = st.session_state.get(session_key, keys[0])
    if active not in keys:
        active = keys[0]
        st.session_state[session_key] = active
    cols = st.columns(len(keys))
    for col, key in zip(cols, keys):
        with col:
            if st.button(
                labels[key],
                key=f"ui_main_tab_{key}",
                type="primary" if key == active else "secondary",
                **layout_width_kwargs("button", stretch=True),
            ):
                if key != active:
                    st.session_state[session_key] = key
                    st.rerun()
    return st.session_state.get(session_key, active)
