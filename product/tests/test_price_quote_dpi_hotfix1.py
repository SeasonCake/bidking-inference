from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "tools", ROOT / "hero_ref" / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_header_version_dpi_hotfix1 import _summary_for_header  # noqa: E402
from test_linked_pending_count_reference_hotfix1 import (  # noqa: E402
    _summary_for_linked_pending,
)
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402


def _summary(state: str) -> dict[str, object]:
    if state == "positive":
        summary = _summary_for_header(positive=True)
    elif state == "pending":
        summary = _summary_for_linked_pending(wide_red_reference=True)
    else:
        summary = _summary_for_header(positive=False)
    reference = summary["reference"]
    assert isinstance(reference, dict)
    reference.update(
        {
            "conservative": "381,649",
            "balanced": "422,312",
            "aggressive": "462,975",
        }
    )
    return summary


@pytest.mark.parametrize("state", ("positive", "pending", "invalid"))
@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_primary_quote_cards_fit_exact_2409_prices_at_all_dpi(
    tmp_path: Path,
    state: str,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / state / str(dpi_scale) / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(copy.deepcopy(_summary(state)))
        root.update_idletasks()
        root.update()

        assert [
            overlay.price_labels[key].cget("text")
            for key in ("conservative", "balanced", "aggressive")
        ] == ["381,649", "422,312", "462,975"]
        for label in overlay.price_labels.values():
            assert label.winfo_width() >= label.winfo_reqwidth()
            assert label.winfo_rootx() >= root.winfo_rootx()
            assert label.winfo_rootx() + label.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass
