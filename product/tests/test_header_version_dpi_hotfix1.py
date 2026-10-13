from __future__ import annotations

import copy
import sys
import tkinter.font as tkfont
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from detail_composition_view import (  # noqa: E402
    detail_composition_view,
    format_compact_quality_count,
)
from test_ahmed_2409_r4_red_count_residual_lock_hotfix1 import (  # noqa: E402
    _2409_r4_result,
)
from test_ahmed_4528_probabilistic_red_display_hotfix2 import _summary  # noqa: E402
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402


def _summary_for_header(*, positive: bool) -> dict[str, object]:
    result = _2409_r4_result()
    if not positive:
        evidence = result["evidence"]
        assert isinstance(evidence, dict)
        evidence.pop("total_count")
    payload = _quality_composition_payload(result)
    q6 = detail_composition_view({"quality_composition": payload}).quality("q6")
    summary = _summary()
    summary["quality_composition"] = payload
    red = summary["red"]
    assert isinstance(red, dict)
    red["count_range"] = "1 / 1 / 1"
    red["count_display"] = format_compact_quality_count(q6, fallback="1 / 1 / 1")
    red["cells_range"] = "2 / 3 / 8"
    return summary


def _rect(widget) -> tuple[int, int, int, int]:
    return (
        widget.winfo_rootx(),
        widget.winfo_rooty(),
        widget.winfo_width(),
        widget.winfo_height(),
    )


def _within(rect: tuple[int, int, int, int], root) -> bool:
    x, y, width, height = rect
    return (
        x >= root.winfo_rootx()
        and y >= root.winfo_rooty()
        and x + width <= root.winfo_rootx() + root.winfo_width()
        and y + height <= root.winfo_rooty() + root.winfo_height()
    )


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
@pytest.mark.parametrize("positive", (True, False), ids=("q6-count-lock", "q6-pending"))
def test_full_header_version_reflows_without_hiding_or_overlapping_at_dpi(
    tmp_path: Path,
    dpi_scale: float,
    positive: bool,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"header-{dpi_scale}-{positive}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(copy.deepcopy(_summary_for_header(positive=positive)))
        overlay.title.configure(text="阿赫麦德·博物收藏家别墅·跨轮组合校验")
        root.update_idletasks()
        root.update()

        expected_triplet = ("保1", "/", "均1", "/", "激1")
        expected_count_badge = "锁" if positive else "待"
        expected_cells = "2/3/8格"
        assert overlay.version_label.cget("text") == "v0.3.4"
        assert overlay.version_label.winfo_ismapped()
        assert overlay.red_count_triplet_frame.winfo_ismapped()
        actual_triplet = tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert actual_triplet == expected_triplet
        assert all(
            overlay.red_count_triplet_labels[key].winfo_manager() == "pack"
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert len(
            {
                overlay.red_count_triplet_labels[key].winfo_y()
                for key in ("low", "sep_left", "middle", "sep_right", "high")
            }
        ) == 1
        assert min({
            int(tkfont.Font(root=root, font=label.cget("font")).cget("size"))
            for label in overlay.red_count_triplet_labels.values()
        }) >= module.RED_COUNT_INLINE_FONT_SIZE
        assert overlay.quality_state_badges["红件"].cget("text") == expected_count_badge
        assert overlay.red_rows["红格"].cget("text") == expected_cells
        assert overlay.quality_state_badges["红格"].cget("text") == "待"
        assert (
            overlay.red_count_triplet_frame.winfo_width()
            >= overlay.red_count_triplet_frame.winfo_reqwidth()
        )
        assert all(
            button.winfo_ismapped()
            for button in (
                overlay.top_resize_grip,
                overlay.restart_button,
                overlay.topmost_button,
                overlay.close_button,
            )
        )
        title = _rect(overlay.title)
        version = _rect(overlay.version_label)
        assert _within(title, root)
        assert _within(version, root)
        assert title[1] < version[1] + version[3]
        assert version[1] < title[1] + title[3]
        if dpi_scale == 1.25 and positive:
            controls = sorted(
                (
                    overlay.top_resize_grip,
                    overlay.restart_button,
                    overlay.topmost_button,
                    overlay.close_button,
                ),
                key=lambda button: button.winfo_rootx(),
            )
            for button in controls:
                holder = button._rounded_holder
                inset = holder._round_card["inset"]
                assert button.winfo_width() >= button.winfo_reqwidth()
                assert holder.winfo_width() == button.winfo_reqwidth() + 2 * inset
            assert all(
                left.winfo_rootx() + left.winfo_width() <= right.winfo_rootx()
                for left, right in zip(controls, controls[1:])
            )
            assert title[0] + title[2] <= version[0]
        assert all(
            _within(_rect(button), root)
            for button in (
                overlay.top_resize_grip,
                overlay.restart_button,
                overlay.topmost_button,
                overlay.close_button,
            )
        )

        overlay._apply_startup_update_notice(
            {
                "ok": True,
                "app": "0.3.4",
                "latest": "0.3.4-hotfix1",
                "min": None,
                "url": "https://example.invalid/download",
            }
        )
        root.update_idletasks()
        root.update()
        assert overlay.update_notice.winfo_ismapped()
        assert overlay.version_label.winfo_ismapped()
        assert _within(_rect(overlay.version_label), root)
        update = _rect(overlay.update_notice)
        assert _within(update, root)
        assert update[0] + update[2] <= version[0]
        assert update[1] < version[1] + version[3]

        overlay._apply_startup_update_notice(None)
        root.update_idletasks()
        root.update()
        assert not overlay.update_notice.winfo_ismapped()
        assert overlay.version_label.winfo_ismapped()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_red_triplet_restores_cached_font_after_global_scale(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * 1.5)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "font-reset" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(copy.deepcopy(_summary_for_header(positive=True)))
        for _ in range(4):
            root.update_idletasks()
            root.update()
        frame = overlay.red_count_triplet_frame
        cached = int(getattr(overlay, "_red_count_triplet_fit_font", 0))
        assert cached >= module.RED_COUNT_INLINE_FONT_SIZE
        assert frame.winfo_width() >= frame.winfo_reqwidth()

        overlay._apply_ui_scale()
        for _ in range(4):
            root.update_idletasks()
            root.update()

        assert frame.winfo_width() >= frame.winfo_reqwidth()
        actual_sizes = tuple(
            int(tkfont.Font(root=root, font=segment.cget("font")).cget("size"))
            for segment in overlay.red_count_triplet_labels.values()
        )
        assert min(actual_sizes) >= module.RED_COUNT_INLINE_FONT_SIZE
        assert len(set(actual_sizes)) == 1
        configure_count = int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
        assert configure_count <= 12
        for _ in range(6):
            root.update_idletasks()
            root.update()
        assert frame.winfo_width() >= frame.winfo_reqwidth()
        actual_sizes = tuple(
            int(tkfont.Font(root=root, font=segment.cget("font")).cget("size"))
            for segment in overlay.red_count_triplet_labels.values()
        )
        assert min(actual_sizes) >= module.RED_COUNT_INLINE_FONT_SIZE
        assert len(set(actual_sizes)) == 1
        assert configure_count == int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass
