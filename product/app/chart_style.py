"""Shared matplotlib styling for Streamlit charts."""

from __future__ import annotations

import matplotlib.pyplot as plt


def apply_bidking_chart_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "#fafbfc",
            "axes.facecolor": "#f4f6f9",
            "axes.edgecolor": "#c5cdd8",
            "axes.labelcolor": "#334155",
            "axes.titleweight": "600",
            "axes.grid": True,
            "grid.alpha": 0.35,
            "grid.linestyle": "--",
            "grid.color": "#cbd5e1",
            "xtick.color": "#475569",
            "ytick.color": "#475569",
            "font.size": 9,
            "legend.framealpha": 0.92,
            "legend.edgecolor": "#e2e8f0",
        },
    )


def style_value_hist(ax, *, x_max: int) -> None:
    ax.set_facecolor("#f8fafc")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, x_max)


def style_roi_barh(ax) -> None:
    ax.set_facecolor("#f8fafc")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.axvline(0, color="#64748b", linewidth=0.8, alpha=0.7)
