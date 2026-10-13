from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
for path in (TOOLS, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import (  # noqa: E402
    INDEX_HTML,
    _quality_composition_payload,
)
from detail_composition_view import (  # noqa: E402
    QUALITY_COMPOSITION_SCHEMA_VERSION,
    detail_composition_view,
    format_compact_quality_count,
)
from test_live_overlay import (  # noqa: E402
    _ahmad_overlay_module,
    _ahmad_server_module,
)


tk = pytest.importorskip("tkinter")


def _new_root(attempts: int = 4):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as exc:  # noqa: PERF203 - bounded environment retry
            last = exc
    pytest.skip(f"real Tk unavailable after {attempts} attempts: {last}")


def _quality_payload(*, exact: bool = False) -> dict[str, object]:
    if exact:
        candidates = [{"count": 3, "cells": 10, "probability": 1.0}]
        count_candidates = [{"value": 3, "probability": 1.0}]
        return {
            "status": "exact",
            "source": "pricing_posterior",
            "lock_strength": "exact",
            "lock_reason": "single_joint_solution",
            "candidates": candidates,
            "count_candidates": count_candidates,
            "shown_probability": 1.0,
            "other_probability": 0.0,
            "truncated": False,
            "cap_hit": False,
            "pricing_posterior": True,
            "display_reconciled": False,
            "compact_count_candidates": [],
            "compact_count_triplet": [],
            "count_correlation_state": "independent",
            "count_correlation_note": "fixture has no total-count authority",
        }
    return {
        "status": "ready",
        "source": "pricing_posterior",
        "lock_strength": "probabilistic",
        "lock_reason": "combo_multi_solution",
        "candidates": [
            {"count": 3, "cells": 9, "probability": 0.3237},
            {"count": 3, "cells": 10, "probability": 0.3237},
            {"count": 3, "cells": 11, "probability": 0.3237},
        ],
        "count_candidates": [
            {"value": 3, "probability": 0.9711},
            {"value": 10, "probability": 0.0289},
        ],
        "shown_probability": 0.9711,
        "other_probability": 0.0289,
        "truncated": True,
        "cap_hit": False,
        "pricing_posterior": True,
        "display_reconciled": False,
        "compact_count_candidates": [],
        "compact_count_triplet": [],
        "count_correlation_state": "independent",
        "count_correlation_note": "fixture has no total-count authority",
    }


def _summary(*, exact: bool = False) -> dict[str, object]:
    return {
        "status": "ok",
        "updated_at_text": "19:17:57",
        "context": {
            "hero": "ahmed",
            "map_id": 4528,
            "round": 3,
            "phase": "bidding",
            "session_id": "4528:synthetic-truth-free",
        },
        "reference": {
            "source": "ref_v0",
            "conservative": "826,618",
            "balanced": "898,163",
            "aggressive": "969,708",
            "action": "估计参考",
            "total_grid_range": "190 / 190 / 190格",
            "total_value_range": "826,618 / 898,163 / 969,708",
        },
        "red": {
            "count_range": "3 / 3 / 3",
            "cells_range": "9 / 10 / 11",
            "value_range": "350,078 / 592,454 / 746,378",
            "quality_count_summary": "紫12件/72格 · 金8件/32格",
            "quality_probability_summary": "红3件10格32.4%",
            "count_probability_hover": "红件候选：3件 97.1% · 10件 2.9%",
            "cells_probability_hover": "红格候选：9格 32.4% · 10格 32.4% · 11格 32.4% · 其他 2.9%",
            "quality_probability_hover": "红品：3件/9格 32.4% · 3件/10格 32.4% · 3件/11格 32.4% · 其他 2.9%",
            "uncertainty_summary": "概率估计",
        },
        "quality_composition": {
            "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
            "status": "ready",
            "reason": "结构化联合候选",
            "qualities": {"q6": _quality_payload(exact=exact)},
        },
        "ahmed_ref": {"status": "ok"},
        "evidence": {"latest_result": {}},
        "minimap": {},
        "truth": {"available": False},
        "flags": [],
    }


def test_panel_payload_keeps_count_marginal_separate_from_joint_rows() -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [
                    {"count": 3, "cells": 9, "prob": 0.3237},
                    {"count": 3, "cells": 10, "prob": 0.3237},
                    {"count": 3, "cells": 11, "prob": 0.3237},
                ]
            },
            "quality_count_candidates": {
                "q6": [
                    {"value": 3, "prob": 0.9711},
                    {"value": 10, "prob": 0.0289},
                ]
            },
            "quality_lock": {
                "q6": {
                    "strength": "probabilistic",
                    "reason": "combo_multi_solution",
                }
            },
        }
    )

    quality = detail_composition_view({"quality_composition": payload}).quality("q6")
    assert [(row.count, row.cells) for row in quality.candidates] == [
        (3, 9),
        (3, 10),
        (3, 11),
    ]
    assert [
        (row.value, row.probability) for row in quality.count_candidates
    ] == [(3, 0.9711), (10, 0.0289)]
    assert "red.count_display" in INDEX_HTML


def test_server_summary_exposes_the_same_probabilistic_count_display(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return {
                "status": "ok",
                "source": "ref_v0",
                "notes": [],
                "conservative": 826618,
                "balanced": 898163,
                "aggressive": 969708,
                "red_count_range": [3, 3, 3],
                "red_cells_range": [9, 10, 11],
                "red_value_range": [350078, 592454, 746378],
                "total_grid_range": [190, 190, 190],
                "quality_count_ranges": {"q6": [3, 3, 3]},
                "quality_cells_ranges": {"q6": [9, 10, 11]},
                "quality_count_cells_candidates": {
                    "q6": [
                        {"count": 3, "cells": 9, "prob": 0.3237},
                        {"count": 3, "cells": 10, "prob": 0.3237},
                        {"count": 3, "cells": 11, "prob": 0.3237},
                    ]
                },
                "quality_count_candidates": {
                    "q6": [
                        {"value": 3, "prob": 0.9711},
                        {"value": 10, "prob": 0.0289},
                    ]
                },
                "quality_lock": {
                    "q6": {
                        "strength": "probabilistic",
                        "reason": "combo_multi_solution",
                    }
                },
                "evidence": {"hero": "ahmed", "total_grid_target": 190},
            }

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda snapshot, **kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *args, **kwargs: False,
    )
    created_at = time.time()
    snapshot = {
        "created_at": created_at,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4528,
                "round": 3,
                "phase": "bidding",
                "session_id": "4528:synthetic-truth-free",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
        "structured_ref_inputs": {"hero": "ahmed", "total_cells": 190},
    }

    summary = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["red"]["count_range"] == "3 / 3 / 3"
    assert summary["red"]["count_display"] == "保 3 / 均 3 / 激 3件"
    q6 = summary["quality_composition"]["qualities"]["q6"]
    assert q6["lock_strength"] == "probabilistic"
    assert q6["count_candidates"] == [
        {"value": 3, "probability": 0.9711},
        {"value": 10, "probability": 0.0289},
    ]


@pytest.mark.parametrize("width", (440, 510))
@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_4528_real_tk_marks_collapsed_count_as_probabilistic_and_keeps_tail(
    tmp_path: Path,
    width: int,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=(
                tmp_path / f"{width}-{dpi_scale}" / "latest_snapshot.json"
            ),
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.geometry(f"{width}x620+0+0")
        data = _summary()
        overlay.render(data)
        root.update_idletasks()
        root.update()

        labels = overlay.red_count_triplet_labels
        main_text = "".join(
            str(labels[key].cget("text"))
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert main_text == "保3/均3/激3"
        assert labels["middle"].cget("fg") == module.BAD
        assert "bold" in str(labels["middle"].cget("font"))
        assert overlay.quality_state_badges["红件"].cget("text") == "待"
        assert "3 / 3 / 3" not in main_text
        assert "100%" not in main_text
        assert overlay.red_count_triplet_frame.winfo_width() >= 1
        badge = overlay.quality_state_badges["红件"]
        assert (
            overlay.red_count_triplet_frame.winfo_x()
            + overlay.red_count_triplet_frame.winfo_width()
            <= badge.winfo_x()
        )
        assert badge.winfo_x() + badge.winfo_width() <= badge.master.winfo_width()

        tip = labels["middle"]._hover_tip
        assert tip is not None
        assert "3件 97.1%" in tip.text
        assert "10件 2.9%" in tip.text
        assert "其他 2.9%" in tip.text

        overlay.toggle_details()
        overlay._detail_composition_filter = "q6"
        overlay._render_detail_composition(data)
        root.update_idletasks()
        root.update()
        footer = overlay.detail_composition_footer.cget("text")
        assert "红件边际 3件 97.1% · 10件 2.9%" in footer
        assert "其他 2.9%" in footer

        overlay._clear_values()
        root.update_idletasks()
        assert not overlay.red_count_triplet_frame.winfo_manager()
        assert overlay.red_rows["红件"].winfo_manager()
        assert overlay.red_rows["红件"].cget("text") == "-"
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass


def test_strict_exact_can_use_lock_copy_but_percentile_shape_cannot(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        exact = _summary(exact=True)
        overlay.render(exact)
        root.update_idletasks()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保3", "/", "均3", "/", "激3")
        assert overlay.quality_state_badges["红件"].cget("text") == "锁"
        assert overlay.red_rows["红格"].cget("text") == "10格"
        assert overlay.quality_state_badges["红格"].cget("text") == "锁"

        malformed = _summary(exact=True)
        quality = malformed["quality_composition"]["qualities"]["q6"]
        assert isinstance(quality, dict)
        quality["truncated"] = True
        overlay.render(malformed)
        root.update_idletasks()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保—", "/", "均—", "/", "激—")
        assert overlay.quality_state_badges["红件"].cget("text") == "冲突"
        assert overlay.red_rows["红格"].cget("text") == "—格"
        assert overlay.quality_state_badges["红格"].cget("text") == "冲突"
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


class _BombInt(int):
    def __new__(cls, value: int):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __int__(self):
        return self._explode("int")

    def __float__(self):
        return self._explode("float")

    def __eq__(self, other):
        del other
        return self._explode("eq")

    def __lt__(self, other):
        del other
        return self._explode("lt")


class _BombStr(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def __str__(self):
        self.magic_calls.append("str")
        raise RuntimeError("PRIVATE_STR")

    def lower(self):
        self.magic_calls.append("lower")
        raise RuntimeError("PRIVATE_LOWER")


class _BombFloat(float):
    def __new__(cls, value: float):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def __float__(self):
        self.magic_calls.append("float")
        raise RuntimeError("PRIVATE_FLOAT")


class _BombMapping(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.magic_calls = []

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def get(self, key, default=None):
        del key, default
        return self._explode("get")

    def __iter__(self):
        return self._explode("iter")

    def items(self):
        return self._explode("items")

    def keys(self):
        return self._explode("keys")


def test_malicious_count_marginal_fails_safe_without_protocol_calls() -> None:
    raw = _quality_payload()
    bomb = _BombInt(10)
    raw["count_candidates"] = [
        {"value": 3, "probability": 0.9711},
        {"value": bomb, "probability": 0.0289},
    ]

    quality = detail_composition_view(
        {"quality_composition": {"status": "ready", "q6": raw}}
    ).quality("q6")

    assert quality.status == "conflict"
    assert quality.count_candidates == ()
    assert bomb.magic_calls == []


@pytest.mark.parametrize(
    "case",
    ("status", "lock_strength", "joint_probability", "locks"),
)
def test_producer_rejects_malicious_subclasses_atomically_without_protocol_calls(
    case: str,
) -> None:
    bomb: _BombStr | _BombFloat | _BombMapping
    raw = {
        "status": "ok",
        "quality_count_cells_candidates": {
            "q6": [{"count": 3, "cells": 10, "prob": 1.0}],
        },
        "quality_lock": {
            "q6": {"strength": "exact", "reason": "single_joint_solution"},
        },
    }
    if case == "status":
        bomb = _BombStr("ok")
        raw["status"] = bomb
    elif case == "lock_strength":
        bomb = _BombStr("exact")
        raw["quality_lock"]["q6"]["strength"] = bomb
    elif case == "joint_probability":
        bomb = _BombFloat(1.0)
        raw["quality_count_cells_candidates"]["q6"][0]["prob"] = bomb
    else:
        bomb = _BombMapping(raw["quality_lock"])
        raw["quality_lock"] = bomb

    payload = _quality_composition_payload(raw)
    view = detail_composition_view({"quality_composition": payload})

    assert payload == {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }
    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)
    assert format_compact_quality_count(view.quality("q6")) == "组合冲突"
    assert bomb.magic_calls == []


def test_atomic_producer_conflict_is_fixed_across_panel_and_real_tk(
    tmp_path: Path,
) -> None:
    payload = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": _BombMapping({}),
            "quality_lock": {},
        }
    )
    view = detail_composition_view({"quality_composition": payload})
    summary = _summary()
    summary["quality_composition"] = payload
    summary["red"]["count_display"] = format_compact_quality_count(
        view.quality("q6"),
        fallback=summary["red"]["count_range"],
    )

    assert summary["red"]["count_display"] == "组合冲突"
    assert "red.count_display || red.count_range" in INDEX_HTML

    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        overlay.render(summary)
        root.update_idletasks()
        text = "".join(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert text == "保—/均—/激—"
        assert overlay.quality_state_badges["红件"].cget("text") == "冲突"
        assert "概率态" not in text
        assert "锁定" not in text
        assert "100%" not in text
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _exact_producer_input() -> dict[str, object]:
    return {
        "status": "ok",
        "quality_count_cells_candidates": {
            "q6": [{"count": 3, "cells": 10, "prob": 1}],
        },
        "quality_count_candidates": {
            "q6": [{"value": 3, "prob": 1}],
        },
        "quality_cells_candidates": {
            "q6": [{"value": 10, "prob": 1}],
        },
        "quality_lock": {
            "q6": {"strength": "exact", "reason": "single_joint_solution"},
        },
    }


@pytest.mark.parametrize("target", ("joint", "marginal"))
def test_huge_exact_int_probability_is_atomic_conflict_without_float_conversion(
    target: str,
) -> None:
    raw = _exact_producer_input()
    huge = 10**10_000
    if target == "joint":
        raw["quality_count_cells_candidates"]["q6"][0]["prob"] = huge
    else:
        raw["quality_count_candidates"]["q6"][0]["prob"] = huge

    assert _quality_composition_payload(raw) == {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }


def test_exact_int_one_probability_remains_a_valid_exact_lock() -> None:
    payload = _quality_composition_payload(_exact_producer_input())
    q6 = detail_composition_view({"quality_composition": payload}).quality("q6")

    assert q6.status == "exact"
    assert q6.lock_strength == "exact"
    assert q6.candidates[0].probability == 1.0
    assert q6.count_candidates[0].probability == 1.0
    assert q6.cells_candidates[0].probability == 1.0
    assert format_compact_quality_count(q6) == "3件"


@pytest.mark.parametrize("probability", (0, -1, 2, float("nan"), float("inf")))
@pytest.mark.parametrize("target", ("joint", "count_marginal", "cells_marginal"))
def test_out_of_domain_probability_is_atomic_conflict(
    target: str,
    probability: int | float,
) -> None:
    raw = _exact_producer_input()
    field = {
        "joint": "quality_count_cells_candidates",
        "count_marginal": "quality_count_candidates",
        "cells_marginal": "quality_cells_candidates",
    }[target]
    raw[field]["q6"][0]["prob"] = probability

    assert _quality_composition_payload(raw) == {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }


def _runtime_ref_result_with_probability(
    target: str,
    probability: int,
) -> dict[str, object]:
    raw = _exact_producer_input()
    field = {
        "joint": "quality_count_cells_candidates",
        "count_marginal": "quality_count_candidates",
        "cells_marginal": "quality_cells_candidates",
    }[target]
    raw[field]["q6"][0]["prob"] = probability
    raw.update(
        {
            "source": "ref_v0",
            "notes": [],
            "conservative": 826618,
            "balanced": 898163,
            "aggressive": 969708,
            "red_count_range": [3, 3, 3],
            "red_cells_range": [9, 10, 11],
            "red_value_range": [350078, 592454, 746378],
            "total_grid_range": [190, 190, 190],
            "quality_count_ranges": {"q6": [3, 3, 3]},
            "quality_cells_ranges": {"q6": [9, 10, 11]},
            "evidence": {"hero": "ahmed", "total_grid_target": 190},
        }
    )
    return raw


def _runtime_snapshot(session_id: str) -> dict[str, object]:
    created_at = time.time()
    return {
        "created_at": created_at,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4528,
                "round": 3,
                "phase": "bidding",
                "session_id": session_id,
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
        "structured_ref_inputs": {"hero": "ahmed", "total_cells": 190},
    }


def _assert_invalid_ref_result_is_atomic_across_products(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    case: str,
    raw: dict[str, object],
    bomb: _BombStr | _BombFloat | _BombMapping | None = None,
) -> None:
    module = _ahmad_server_module()

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return raw

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda snapshot, **kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *args, **kwargs: False,
    )
    snapshot = _runtime_snapshot(f"4528:atomic-{case}")
    snapshot_path = tmp_path / case / "latest_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    direct = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    expected_composition = {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }
    expected_ref = {
        "status": "conflict",
        "source": "ref_v0",
        "notes": ["quality_composition_conflict"],
    }
    assert direct["quality_composition"] == expected_composition
    assert direct["red"]["count_display"] == "组合冲突"
    assert direct["ahmed_ref"] == expected_ref

    config = module.ServerConfig(project_root=tmp_path, snapshot=snapshot_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["quality_composition"] == expected_composition
    assert payload["red"]["count_display"] == "组合冲突"
    assert payload["ahmed_ref"] == expected_ref
    assert "PRIVATE_" not in encoded
    assert "RuntimeError" not in encoded

    overlay_module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = overlay_module.AhmadTkOverlay(
            root,
            snapshot_path=snapshot_path,
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        overlay.render(payload)
        root.update_idletasks()
        text = "".join(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert text == "保—/均—/激—"
        assert overlay.quality_state_badges["红件"].cget("text") == "冲突"
        assert "概率态" not in text
        assert "锁定" not in text
        assert "100%" not in text
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

    if bomb is not None:
        assert bomb.magic_calls == []


def _runtime_ref_result_with_bomb(
    case: str,
) -> tuple[dict[str, object], _BombStr | _BombFloat | _BombMapping]:
    raw = _runtime_ref_result_with_probability("joint", 1)
    if case == "root_mapping":
        bomb = _BombMapping(raw)
        raw = bomb
    elif case == "status":
        bomb = _BombStr("ok")
        raw["status"] = bomb
    elif case == "lock_strength":
        bomb = _BombStr("exact")
        raw["quality_lock"]["q6"]["strength"] = bomb
    elif case == "lock_reason":
        bomb = _BombStr("single_joint_solution")
        raw["quality_lock"]["q6"]["reason"] = bomb
    elif case == "joint_probability":
        bomb = _BombFloat(1.0)
        raw["quality_count_cells_candidates"]["q6"][0]["prob"] = bomb
    elif case == "locks":
        bomb = _BombMapping(raw["quality_lock"])
        raw["quality_lock"] = bomb
    elif case in {"candidate_first", "candidate_last"}:
        bomb = _BombMapping({"count": 10, "cells": 48, "prob": 0.0289})
        candidates = raw["quality_count_cells_candidates"]["q6"]
        if case == "candidate_first":
            candidates.insert(0, bomb)
        else:
            candidates.append(bomb)
    else:  # pragma: no cover - caller owns the finite case list
        raise AssertionError(case)
    return raw, bomb


def test_exact_int_one_remains_locked_through_real_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    raw = _runtime_ref_result_with_probability("joint", 1)

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return raw

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda snapshot, **kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *args, **kwargs: False,
    )
    snapshot = _runtime_snapshot("4528:exact-int-one")

    summary = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["red"]["count_display"] == "3件"
    assert summary["quality_composition"]["qualities"]["q6"]["status"] == "exact"
    assert summary["red"]["count_probability_hover"] == "红件候选：3件 100%"


@pytest.mark.parametrize(
    "target",
    ("joint", "count_marginal", "cells_marginal"),
)
def test_huge_probability_is_safe_across_summary_loopback_and_real_tk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
) -> None:
    raw = _runtime_ref_result_with_probability(target, 10**10_000)
    _assert_invalid_ref_result_is_atomic_across_products(
        monkeypatch,
        tmp_path,
        case=f"huge-{target}",
        raw=raw,
    )


@pytest.mark.parametrize(
    "case",
    (
        "root_mapping",
        "status",
        "lock_strength",
        "lock_reason",
        "joint_probability",
        "locks",
        "candidate_first",
        "candidate_last",
    ),
)
def test_malicious_ref_result_is_atomic_across_summary_http_and_real_tk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    raw, bomb = _runtime_ref_result_with_bomb(case)
    _assert_invalid_ref_result_is_atomic_across_products(
        monkeypatch,
        tmp_path,
        case=case,
        raw=raw,
        bomb=bomb,
    )
