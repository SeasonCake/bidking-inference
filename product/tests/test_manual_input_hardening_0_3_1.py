from __future__ import annotations

import time

import pytest

from test_live_overlay import _ahmad_overlay_module


class _Entry:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def delete(self, _start: int, _end: str) -> None:
        self.value = ""

    def insert(self, _index: int, value: str) -> None:
        self.value = str(value)


class _Status:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def configure(self, **kwargs: object) -> None:
        self.kwargs.update(kwargs)


def _overlay(module, **fields: str):
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.manual_entries = {key: _Entry(value) for key, value in fields.items()}
    overlay.manual_vars = {}
    overlay.manual_status = _Status()
    overlay._manual_programmatic_update = False
    overlay._manual_dirty_fields = set()
    overlay._manual_autofill_values = {}
    overlay._manual_input_revision = 0
    overlay._manual_active = False
    return overlay


def _snapshot(module, **fields: str):
    overlay = _overlay(module, **fields)
    return module.AhmadTkOverlay._manual_inputs_snapshot(overlay)


def test_manual_typing_purple_count_does_not_mutate_low_quality_row() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        white_count="3",
        white_cells="5",
        green_count="4",
        green_cells="8",
        q1_avg="",
        q1_count="",
        q1_cells="",
        q4_avg="2",
        q4_count="1",
        q4_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_count")

    assert overlay.manual_entries["q4_cells"].get() == "2"
    assert overlay.manual_entries["q1_avg"].get() == ""
    assert overlay.manual_entries["q1_count"].get() == ""
    assert overlay.manual_entries["q1_cells"].get() == ""


def test_manual_stringvar_trace_keeps_unrelated_quality_rows_stable() -> None:
    module = _ahmad_overlay_module()
    tcl = module.tk.Tcl()
    overlay = _overlay(
        module,
        white_count="3",
        white_cells="5",
        green_count="4",
        green_cells="8",
        q1_avg="",
        q1_count="",
        q1_cells="",
        q4_avg="2",
        q4_count="",
        q4_cells="",
    )
    q4_count_var = module.tk.StringVar(master=tcl, value="")

    class _VarEntry(_Entry):
        def __init__(self, var) -> None:
            self.var = var

        def get(self) -> str:
            return self.var.get()

        def delete(self, _start: int, _end: str) -> None:
            self.var.set("")

        def insert(self, _index: int, value: str) -> None:
            self.var.set(str(value))

    overlay.manual_vars["q4_count"] = q4_count_var
    overlay.manual_entries["q4_count"] = _VarEntry(q4_count_var)
    q4_count_var.trace_add(
        "write",
        lambda *_args: module.AhmadTkOverlay._on_manual_var_write(
            overlay,
            "q4_count",
        ),
    )

    q4_count_var.set("1")
    q4_count_var.set("10")

    assert overlay.manual_entries["q4_cells"].get() == "20"
    assert overlay.manual_entries["q1_avg"].get() == ""
    assert overlay.manual_entries["q1_count"].get() == ""
    assert overlay.manual_entries["q1_cells"].get() == ""


def test_manual_trace_fails_closed_on_finite_product_overflow() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        q4_count="2",
        q4_avg="1e308",
        q4_cells="",
        q5_count="7",
        q5_avg="3",
        q5_cells="21",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_avg")

    assert overlay.manual_status.kwargs == {
        "text": "输入无效：紫均格超出可计算范围",
        "fg": module.BAD,
    }
    assert overlay.manual_entries["q4_count"].get() == "2"
    assert overlay.manual_entries["q4_avg"].get() == "1e308"
    assert overlay.manual_entries["q4_cells"].get() == ""
    assert overlay.manual_entries["q5_count"].get() == "7"
    assert overlay.manual_entries["q5_avg"].get() == "3"
    assert overlay.manual_entries["q5_cells"].get() == "21"


def test_manual_trace_rejects_extreme_count_before_grid_dp() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        q4_count="1e308",
        q4_avg="1",
        q4_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_count")

    assert overlay.manual_status.kwargs == {
        "text": "输入无效：紫件超出可计算范围",
        "fg": module.BAD,
    }
    assert overlay.manual_entries["q4_cells"].get() == ""


@pytest.mark.parametrize(
    ("avg_text", "expected_cells"),
    (("1e1", "20"), ("2", "4")),
)
def test_manual_trace_keeps_representable_scientific_and_plain_values(
    avg_text: str,
    expected_cells: str,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        q4_count="2",
        q4_avg=avg_text,
        q4_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_avg")

    assert overlay.manual_entries["q4_cells"].get() == expected_cells
    assert overlay.manual_status.kwargs["text"] == "待应用"
    assert overlay.manual_status.kwargs["fg"] == module.WARM


def test_manual_high_cell_total_survives_trace_and_snapshot() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        hero="ahmed",
        map_id="2404",
        total_count="44",
        total_cells="792",
        q4_count="44",
        q4_avg="18",
        q4_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_avg")

    assert overlay.manual_entries["q4_cells"].get() == "792"
    assert overlay.manual_status.kwargs["text"] == "待应用"
    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)
    assert error == ""
    assert snapshot is not None
    structured = snapshot["structured_ref_inputs"]
    assert structured["fixed_counts"]["q4"] == 44
    assert structured["quality_cells"]["q4"] == 792


@pytest.mark.parametrize(
    ("quality_key", "label"),
    (("q4", "紫"), ("q3", "蓝")),
)
def test_manual_snapshot_rejects_count_derived_above_item_limit(
    quality_key: str,
    label: str,
) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        hero="ahmed",
        map_id="2404",
        **{
            f"{quality_key}_count": "",
            f"{quality_key}_avg": "1",
            f"{quality_key}_cells": "513",
        },
    )
    overlay._manual_dirty_fields.add(f"{quality_key}_count")

    snapshot, error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert snapshot is None
    assert error == f"{label}件超出可计算范围"


def test_manual_white_green_derived_total_reuses_item_count_limit() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        hero="ahmed",
        map_id="2404",
        white_count="300",
        green_count="213",
        q1_count="",
    )
    overlay._manual_dirty_fields.add("q1_count")

    trace_error = module.AhmadTkOverlay._sync_manual_derived_fields(
        overlay,
        trigger_field="white_count",
    )
    snapshot, snapshot_error = module.AhmadTkOverlay._manual_inputs_snapshot(overlay)

    assert trace_error == "输入无效：白绿件超出可计算范围"
    assert snapshot is None
    assert snapshot_error == "白绿件超出可计算范围"


def test_manual_explicit_item_and_cell_maximums_remain_representable() -> None:
    module = _ahmad_overlay_module()

    snapshot, error = _snapshot(
        module,
        hero="ahmed",
        map_id="2404",
        total_count="512",
        total_cells="9216",
        q4_count="512",
        q4_avg="18",
        q4_cells="9216",
    )

    assert error == ""
    assert snapshot is not None
    structured = snapshot["structured_ref_inputs"]
    assert structured["fixed_counts"]["q4"] == 512
    assert structured["quality_cells"]["q4"] == 9216


@pytest.mark.parametrize("avg_text", ("nan", "inf", "-inf"))
def test_manual_trace_marks_nonfinite_average_invalid(avg_text: str) -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        q4_count="2",
        q4_avg=avg_text,
        q4_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "q4_avg")

    assert overlay.manual_status.kwargs == {
        "text": "输入无效：紫均格需要有限数字",
        "fg": module.BAD,
    }
    assert overlay.manual_entries["q4_cells"].get() == ""


def test_manual_white_green_edit_still_updates_merged_low_quality_row() -> None:
    module = _ahmad_overlay_module()
    overlay = _overlay(
        module,
        white_count="3",
        white_cells="5",
        green_count="4",
        green_cells="8",
        q1_avg="",
        q1_count="",
        q1_cells="",
    )

    module.AhmadTkOverlay._on_manual_var_write(overlay, "white_count")

    assert overlay.manual_entries["q1_count"].get() == "7"
    assert overlay.manual_entries["q1_cells"].get() == "13"
    assert overlay.manual_entries["q1_avg"].get() == "1.8571"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("total_count", "-1", "总件不能为负数"),
        ("total_cells", "-1", "总格不能为负数"),
        ("total_cells", "inf", "总格需要有限数字"),
        ("total_cells", "9217", "总格超出可计算范围"),
        ("total_avg", "nan", "全均格需要有限数字"),
        ("q4_count", "-10", "紫件不能为负数"),
        ("q4_cells", "-5", "紫格不能为负数"),
        ("q4_cells", "9217", "紫格超出可计算范围"),
        ("q4_avg", "abc", "紫均格需要数字"),
        ("q4_avg", "inf", "紫均格需要有限数字"),
        ("q4_avg_value", "nan", "紫均价需要有限数字"),
        ("q4_value_sum", "inf", "紫总价需要有限数字"),
        ("q4q5_count", "-1", "紫金红件不能为负数"),
    ),
)
def test_manual_snapshot_rejects_malformed_or_negative_numbers(
    field: str,
    value: str,
    message: str,
) -> None:
    module = _ahmad_overlay_module()
    fields = {
        "hero": "ahmed",
        "map_id": "2404",
        "total_count": "20",
    }
    fields[field] = value

    snapshot, error = _snapshot(module, **fields)

    assert snapshot is None
    assert error == message


@pytest.mark.parametrize("hero", ("badhero", "艾沙"))
def test_manual_snapshot_rejects_unsupported_hero(hero: str) -> None:
    module = _ahmad_overlay_module()

    snapshot, error = _snapshot(
        module,
        hero=hero,
        map_id="2404",
        total_count="20",
    )

    assert snapshot is None
    assert error == f"暂不支持英雄：{hero}"


@pytest.mark.parametrize(
    ("fields", "message"),
    (
        (
            {"total_count": "2", "total_cells": "100"},
            "总件2件无法组成100格",
        ),
        (
            {"total_count": "20", "q4_count": "2", "q4_cells": "100"},
            "紫2件无法组成100格",
        ),
        (
            {"total_count": "10", "q4_count": "6", "q5_count": "5"},
            "已填品质件数合计11超过总件10",
        ),
        (
            {"total_count": "20", "total_cells": "30", "q4_cells": "20", "q5_cells": "11"},
            "已填品质格数合计31超过总格30",
        ),
        (
            {"total_count": "20", "white_count": "3", "green_count": "4", "q1_count": "8"},
            "白3件+绿4件与白绿8件不一致",
        ),
        (
            {"total_count": "20", "white_cells": "5", "green_cells": "8", "q1_cells": "14"},
            "白5格+绿8格与白绿14格不一致",
        ),
        (
            {"total_count": "20", "q4_count": "2", "q5_count": "3", "q6_count": "1", "q4q5_count": "5"},
            "紫金红件5与已填紫/金/红合计6不一致",
        ),
    ),
)
def test_manual_snapshot_rejects_cross_field_conflicts(
    fields: dict[str, str],
    message: str,
) -> None:
    module = _ahmad_overlay_module()

    snapshot, error = _snapshot(
        module,
        hero="ahmed",
        map_id="2404",
        **fields,
    )

    assert snapshot is None
    assert error == message


def test_apply_manual_inputs_fails_soft_if_validation_raises() -> None:
    module = _ahmad_overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._act_ready = False
    overlay._act_state = {"run_full": True}
    overlay.manual_status = _Status()
    overlay._manual_inputs_snapshot = lambda: (_ for _ in ()).throw(OverflowError())

    module.AhmadTkOverlay.apply_manual_inputs(overlay)

    assert overlay.manual_status.kwargs["text"] == "输入检查失败，请检查数字格式"
    assert overlay.manual_status.kwargs["fg"] == module.BAD


def test_manual_snapshot_rejects_finite_product_overflow() -> None:
    module = _ahmad_overlay_module()

    snapshot, error = _snapshot(
        module,
        hero="ahmed",
        map_id="2404",
        total_count="20",
        q4_count="2",
        q4_avg="1e308",
    )

    assert snapshot is None
    assert error == "紫均格超出可计算范围"


def test_real_tk_paste_overflow_never_escapes_trace_or_mutates_sibling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    try:
        root = module.tk.Tk()
    except module.tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    overlay = None
    callback_errors: list[tuple[object, object, object]] = []
    try:
        root.withdraw()
        root.report_callback_exception = (
            lambda exc_type, exc, tb: callback_errors.append((exc_type, exc, tb))
        )
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "manual-overflow" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        root.update()
        overlay._set_manual_edit_enabled(True)

        overlay.manual_entries["q5_count"].insert(0, "7")
        overlay.manual_entries["q5_avg"].insert(0, "3")
        sibling_before = {
            key: overlay.manual_vars[key].get()
            for key in ("q5_count", "q5_avg", "q5_cells")
        }

        # Entry.insert is the real paste boundary used by Tk, not a helper-only call.
        overlay.manual_entries["q4_count"].insert(0, "2")
        overlay.manual_entries["q4_avg"].insert(0, "1e308")
        root.update_idletasks()
        root.update()

        assert callback_errors == []
        assert overlay.manual_vars["q4_count"].get() == "2"
        assert overlay.manual_vars["q4_avg"].get() == "1e308"
        assert overlay.manual_vars["q4_cells"].get() == ""
        assert overlay.manual_status.cget("text") == "输入无效：紫均格超出可计算范围"
        assert overlay.manual_status.cget("fg") == module.BAD
        assert {
            key: overlay.manual_vars[key].get()
            for key in ("q5_count", "q5_avg", "q5_cells")
        } == sibling_before

        # Character-by-character input crosses temporary malformed states but must
        # likewise stay inside the callback boundary.
        overlay.manual_entries["q4_avg"].delete(0, "end")
        for character in "1e308":
            overlay.manual_entries["q4_avg"].insert("end", character)
        root.update_idletasks()
        root.update()
        assert callback_errors == []
        assert overlay.manual_status.cget("text") == "输入无效：紫均格超出可计算范围"

        # A representable scientific value recovers through the same real trace.
        overlay.manual_entries["q4_avg"].delete(0, "end")
        overlay.manual_entries["q4_avg"].insert(0, "1e1")
        root.update_idletasks()
        root.update()
        assert callback_errors == []
        assert overlay.manual_vars["q4_cells"].get() == "20"
        assert overlay.manual_status.cget("text") == "待应用"
    finally:
        root.destroy()


def test_real_tk_high_cell_input_flows_from_trace_through_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    try:
        root = module.tk.Tk()
    except module.tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    overlay = None
    callback_errors: list[tuple[object, object, object]] = []
    submitted: list[dict[str, object]] = []
    try:
        root.withdraw()
        root.report_callback_exception = (
            lambda exc_type, exc, tb: callback_errors.append((exc_type, exc, tb))
        )
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "manual-high-cells" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        root.update()
        overlay._set_manual_edit_enabled(True)

        for field, value in (
            ("hero", "ahmed"),
            ("map_id", "2404"),
            ("total_count", "44"),
            ("total_cells", "792"),
            ("q4_count", "44"),
            ("q4_avg", "18"),
        ):
            overlay.manual_entries[field].delete(0, "end")
            overlay.manual_entries[field].insert(0, value)
        root.update_idletasks()
        root.update()

        assert callback_errors == []
        assert overlay.manual_vars["q4_cells"].get() == "792"
        snapshot, error = overlay._manual_inputs_snapshot()
        assert error == ""
        assert snapshot is not None
        assert snapshot["structured_ref_inputs"]["quality_cells"]["q4"] == 792

        overlay._start_manual_summary_worker = (
            lambda **kwargs: submitted.append(kwargs["snapshot"]) or True
        )
        overlay.apply_manual_inputs()
        assert len(submitted) == 1
        assert submitted[0]["structured_ref_inputs"]["fixed_counts"]["q4"] == 44
        assert submitted[0]["structured_ref_inputs"]["quality_cells"]["q4"] == 792
        assert callback_errors == []
    finally:
        root.destroy()


def test_real_tk_dirty_empty_derived_count_fails_before_apply_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    module = _ahmad_overlay_module()
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    try:
        root = module.tk.Tk()
    except module.tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    callback_errors: list[tuple[object, object, object]] = []
    submitted: list[dict[str, object]] = []
    try:
        root.withdraw()
        root.report_callback_exception = (
            lambda exc_type, exc, tb: callback_errors.append((exc_type, exc, tb))
        )
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "manual-derived-count" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.update_idletasks()
        root.update()
        overlay._set_manual_edit_enabled(True)

        for field, value in (("hero", "ahmed"), ("map_id", "2404")):
            overlay.manual_entries[field].delete(0, "end")
            overlay.manual_entries[field].insert(0, value)

        overlay._start_manual_summary_worker = (
            lambda **kwargs: submitted.append(kwargs["snapshot"]) or True
        )
        for quality_key, label, cells in (
            ("q4", "紫", "513"),
            ("q4", "紫", "9216"),
            ("q3", "蓝", "513"),
        ):
            for suffix in ("count", "avg", "cells"):
                entry = overlay.manual_entries[f"{quality_key}_{suffix}"]
                entry.delete(0, "end")
            root.update_idletasks()
            root.update()

            count_entry = overlay.manual_entries[f"{quality_key}_count"]
            count_entry.insert(0, "1")
            count_entry.delete(0, "end")
            assert f"{quality_key}_count" in overlay._manual_dirty_fields

            started = time.perf_counter()
            overlay.manual_entries[f"{quality_key}_avg"].insert(0, "1")
            overlay.manual_entries[f"{quality_key}_cells"].insert(0, cells)
            root.update_idletasks()
            root.update()
            trace_elapsed = time.perf_counter() - started

            assert callback_errors == []
            assert trace_elapsed < 5.0
            assert overlay.manual_vars[f"{quality_key}_count"].get() == ""
            assert overlay.manual_status.cget("text") == (
                f"输入无效：{label}件超出可计算范围"
            )
            assert overlay.manual_status.cget("fg") == module.BAD

            snapshot, error = overlay._manual_inputs_snapshot()
            assert snapshot is None
            assert error == f"{label}件超出可计算范围"

            overlay.apply_manual_inputs()
            assert submitted == []
            assert overlay.manual_status.cget("text") == (
                f"{label}件超出可计算范围"
            )
            assert overlay.manual_status.cget("fg") == module.BAD
            assert callback_errors == []
    finally:
        root.destroy()
