"""Tests for capture text parser (no OCR)."""

from bidking_lab.capture.apply import (
    apply_capture_result,
    clear_readings_for_map_change,
    hydrate_reading_widgets_from_obs,
    ocr_should_clear_readings,
    reading_widget_key,
    sync_obs_from_reading_widgets,
)
from bidking_lab.capture.parser import parse_panel_text

MAP_NAMES = {
    2405: "望族居所",
    2510: "现代货轮娱乐库",
}


def test_ethan_screenshot_panel():
    text = (
        "\u7b2c3\u8f6e\n"
        "\u826f\u54c1\u626b\u63cf:\u826f\u54c1\u626b\u63cf\n"
        "\u6240\u6709\u84dd\u8272\u54c1\u8d28\u85cf\u54c1\u603b\u5360\u4f4d\u6570\u4e3a15\u683c\n"
        "\u4f0a\u68ee:\u7a7a\u95f4\u89c9\u77e5\n"
        "\u663e\u793a\u6240\u6709\u5df2\u77e5\u54c1\u8d28\u7684\u85cf\u54c1\u5404\u81ea\u7684\u8f6e\u5ed3\n"
        "\u666e\u54c1\u626b\u63cf:\u666e\u54c1\u626b\u63cf\n"
        "\u6240\u6709\u767d\u8272\u548c\u7eff\u8272\u54c1\u8d28\u85cf\u54c1\u603b\u5360\u4f4d\u6570\u4e3a 22 \u683c\n"
        "\u671b\u65cf\u5c45\u6240:\u7ade\u62cd\u4fe1\u606f\n"
        "\u968f\u673a\u663e\u793a9\u4ef6\u85cf\u54c1\u7684\u54c1\u8d28\n"
        "\u4f18\u54c1\u5747\u683c:\u4f18\u54c1\u5747\u683c\n"
        "\u6240\u6709\u7d2b\u8272\u54c1\u8d28\u85cf\u54c1\u5e73\u5747\u5360\u4f4d\u7ea63.27\u683c\n"
    )
    r = parse_panel_text(text, map_names=MAP_NAMES)
    m = r.suggestion_map()
    assert r.map_id == 2405
    assert m["blue_cells"] == 15
    assert m["wg_cells"] == 22
    assert m["purple_avg_raw"] == "3.27"
    assert "total_item_count" not in m  # 「随机显示9件藏品的品质」为无用 hint
    assert any("显示" in x for x in r.ignored)


def test_ignore_quality_fluff():
    text = "显示紫色品质藏品的轮廓"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestions == []
    assert len(r.ignored) >= 1


def test_map_purple_avg_value():
    text = "紫色品质藏品均价为 9,400 silver"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["purple_avg_value"] == 9400


def test_map_name_ocr_typos_doomsday():
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "data/processed/maps.json").read_text(
            encoding="utf-8",
        ),
    )
    names = {int(m["map_id"]): str(m["name"]) for m in raw}
    text = "末日底护所：完拍信息\n"
    r = parse_panel_text(text, map_names=names)
    assert r.map_name == "末日庇护所"


def test_map_name_longest_match():
    names = {2405: "望族居所", 2403: "设计师居所", 2510: "现代货轮娱乐库"}
    text = "设计师居所:竞拍信息\n"
    r = parse_panel_text(text, map_names=names)
    assert r.map_id == 2403


def test_purple_value_total_phrase():
    text = "所有紫色品质藏品的总价值为86490"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["purple_value"] == 86490


def test_apply_preserves_readings_not_in_ocr():
    """OCR must not clear fields the panel did not mention."""
    from bidking_lab.capture.apply import reading_widget_key
    from bidking_lab.capture.types import CaptureParseResult, FieldSuggestion

    obs = {"purple_cells": 40, "gold_cells": 12}
    ui: dict = {"obs_readings_rev": 0}
    ui[reading_widget_key("obs_reading_purple_cells", ui)] = 40
    ui[reading_widget_key("obs_reading_gold_cells", ui)] = 12
    result = CaptureParseResult(
        suggestions=[
            FieldSuggestion("wg_cells", 22, "白+绿总格"),
            FieldSuggestion("blue_cells", 15, "蓝品总格"),
        ],
    )
    apply_capture_result(result, obs, ui)
    assert obs["wg_cells"] == 22
    assert obs["blue_cells"] == 15
    assert obs["purple_cells"] == 40
    assert obs["gold_cells"] == 12


def test_apply_overwrites_reading_when_ocr_repeats_field():
    from bidking_lab.capture.apply import reading_widget_key
    from bidking_lab.capture.types import CaptureParseResult, FieldSuggestion

    obs = {"wg_cells": 20, "blue_cells": 10}
    ui: dict = {"obs_readings_rev": 0}
    ui[reading_widget_key("obs_reading_wg_cells", ui)] = 20
    ui[reading_widget_key("obs_reading_blue_cells", ui)] = 10
    result = CaptureParseResult(
        suggestions=[
            FieldSuggestion("wg_cells", 22, "白+绿总格"),
            FieldSuggestion("blue_cells", 15, "蓝品总格"),
        ],
    )
    apply_capture_result(result, obs, ui)
    assert obs["wg_cells"] == 22
    assert obs["blue_cells"] == 15
    assert ui[reading_widget_key("obs_reading_wg_cells", ui)] == 22


def test_apply_sets_streamlit_widget_keys():
    from bidking_lab.capture.apply import reading_widget_key

    text = (
        "\u6240\u6709\u84dd\u8272\u54c1\u8d28\u85cf\u54c1\u603b\u5360\u4f4d\u6570\u4e3a15\u683c\n"
        "\u6240\u6709\u767d\u8272\u548c\u7eff\u8272\u54c1\u8d28\u85cf\u54c1\u603b\u5360\u4f4d\u6570\u4e3a 22 \u683c\n"
        "\u6240\u6709\u7d2b\u8272\u54c1\u8d28\u85cf\u54c1\u5e73\u5747\u5360\u4f4d\u7ea63.27\u683c\n"
    )
    result = parse_panel_text(text, map_names=MAP_NAMES)
    obs: dict = {}
    ui: dict = {}
    apply_capture_result(result, obs, ui)
    assert obs["wg_cells"] == 22
    assert obs["blue_cells"] == 15
    assert ui[reading_widget_key("obs_reading_wg_cells", ui)] == 22
    assert ui[reading_widget_key("obs_reading_blue_cells", ui)] == 15
    assert ui[reading_widget_key("purple_avg_raw_widget", ui)] == "3.27"


def test_total_item_count_hint():
    text = "本场共有35件藏品"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["total_item_count"] == 35


def test_purple_count_not_total_on_quality_item_line() -> None:
    text = "本场拍卖共有紫色品质道具10件\n"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    m = r.suggestion_map()
    assert m.get("purple_count") == 10
    assert "total_item_count" not in m


def test_total_item_count_bencang_and_yipin_typo():
    for text in ("本仓共有35件藏品", "仓库共有 42 件意品"):
        r = parse_panel_text(text, map_names=MAP_NAMES)
        assert r.suggestion_map()["total_item_count"] in (35, 42)


def test_parse_silver_amount_european_decimal_comma() -> None:
    from bidking_lab.capture.parser import parse_silver_amount

    assert parse_silver_amount("6328,75") == 6328.75


def test_gold_avg_value_decimal_from_map_hint() -> None:
    text = "金色品质藏品均价为 32507.6 silver"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["gold_avg_value"] == 32507.6


def test_purple_avg_value_from_pingjun_jiazhi_phrase() -> None:
    text = "所有紫色品质藏品平均价值约为6328.75"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["purple_avg_value"] == 6328.75


def test_map_line_and_gold_avg_value_on_same_line() -> None:
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "data/processed/maps.json").read_text(
            encoding="utf-8",
        ),
    )
    names = {int(m["map_id"]): str(m["name"]) for m in raw}
    text = "末日庇护所：竞拍信息 所有金色品质藏品平均价值约为35100"
    r = parse_panel_text(text, map_names=names)
    assert r.map_name == "末日庇护所"
    assert r.suggestion_map()["gold_avg_value"] == 35100


def test_random_sample_avg_value_not_total_item_count() -> None:
    """奢华养老院 R1：随机选择的 N 件藏品平均价值 ≠ 本场总件数。"""
    text = "随机选择的 9 件藏品平均价值约为 15296.33"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert "total_item_count" not in r.suggestion_map()
    assert any("随机选择" in ln or "平均价值" in ln for ln in r.ignored)


def test_gold_value_total_phrases():
    for text, val in (
        ("所有金色品质藏品的总价值为24435", 24435),
        ("显示金色品质藏品总价值为 12000", 12000),
        ("金色品质藏品总价值为9000", 9000),
    ):
        r = parse_panel_text(text, map_names=MAP_NAMES)
        assert r.suggestion_map()["gold_value"] == val


def test_purple_value_ocr_spacing():
    text = "所有紫色品质藏品总价值为 86,490"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["purple_value"] == 86490


def test_warehouse_cells_from_panel_line():
    text = "所有藏品总占用的格子数量为159格"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["warehouse_cells"] == 159


def test_warehouse_cells_ocr_typo_yipin():
    """RapidOCR often reads 藏品 as 意品 on the warehouse banner line."""
    text = "所有意品总占用的格子数量为159格"
    r = parse_panel_text(text, map_names=MAP_NAMES)
    assert r.suggestion_map()["warehouse_cells"] == 159


def test_realistic_ocr_fragment_lines():
    """Lines extracted from a full-screen OCR run (post-normalize)."""
    text = (
        "第3轮\n"
        "所有意品总占用的格子数量为159格\n"
        "品总占位款为35格\n"
        "色品质随品总占位致为28格\n"
        "品平均占位约3.43格\n"
    )
    m = parse_panel_text(text, map_names=MAP_NAMES).suggestion_map()
    assert m["warehouse_cells"] == 159
    assert m["blue_cells"] == 35
    assert m["purple_avg_raw"] == "3.43"
    # 「色品质随品…28格」断行碎片：旧版误写入 wg；现不强制映射


def test_clear_readings_for_map_change():
    obs = {
        "wg_cells": 22,
        "blue_cells": 15,
        "purple_avg_raw": "2.66",
        "map_id": 2405,
    }
    ui = {
        reading_widget_key("obs_reading_wg_cells", {}): 22,
        reading_widget_key("obs_reading_blue_cells", {}): 15,
        reading_widget_key("purple_avg_raw_widget", {}): "2.66",
    }
    clear_readings_for_map_change(obs, ui)
    assert "wg_cells" not in obs
    assert "purple_avg_raw" not in obs
    assert ui["obs_readings_rev"] == 1
    assert reading_widget_key("obs_reading_wg_cells", ui) not in ui
    assert obs["map_id"] == 2405


def test_ocr_should_clear_readings_only_on_map_change() -> None:
    from bidking_lab.capture.types import CaptureParseResult

    same = CaptureParseResult(map_id=2407, map_name="私人会所")
    assert ocr_should_clear_readings(same, 2407) is False
    assert ocr_should_clear_readings(same, None) is False
    assert ocr_should_clear_readings(CaptureParseResult(), 2407) is False
    other = CaptureParseResult(map_id=2405, map_name="望族居所")
    assert ocr_should_clear_readings(other, 2407) is True


def test_mark_ocr_map_applied_suppresses_manual_reset() -> None:
    from bidking_lab.capture.apply import mark_ocr_map_applied_to_ui

    ui: dict = {"_tracked_map_id": 2405}
    mark_ocr_map_applied_to_ui(ui, 2407, category="mansion")
    assert ui["_tracked_map_id"] == 2407
    assert ui["_suppress_map_change_reset"] is True
    assert ui["_tracked_map_category"] == "mansion"


def test_reset_obs_for_manual_map_change_clears_avg_and_warehouse() -> None:
    from bidking_lab.capture.apply import reset_obs_for_manual_map_change

    obs = {
        "map_id": 2407,
        "warehouse_cells": 120,
        "wg_cells": 17,
        "purple_avg_raw": "2.66",
        "gold_avg_raw": "3.1",
        "purple_avg_value": 6328.75,
        "gold_avg_value": 35100,
    }
    ui: dict = {
        "obs_readings_rev": 0,
        "obs_warehouse_cells": 120,
        reading_widget_key("purple_avg_raw_widget", {}): "2.66",
    }
    reset_obs_for_manual_map_change(obs, ui, new_map_id=2405)
    assert obs["map_id"] == 2405
    assert "warehouse_cells" not in obs
    assert "purple_avg_raw" not in obs
    assert "purple_avg_value" not in obs
    assert "gold_avg_value" not in obs
    assert ui["obs_warehouse_cells"] is None
    assert ui["obs_readings_rev"] == 1


def test_sync_preserves_obs_when_widget_key_absent() -> None:
    """Non-obs tabs do not render inputs; sync must not wipe OCR-filled obs."""
    obs = {"wg_cells": 12, "blue_cells": 35, "purple_cells": 34}
    ui: dict = {"obs_readings_rev": 0}
    sync_obs_from_reading_widgets(obs, ui)
    assert obs == {"wg_cells": 12, "blue_cells": 35, "purple_cells": 34}


def test_sync_clears_obs_when_widget_explicitly_none() -> None:
    obs = {"wg_cells": 12}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("obs_reading_wg_cells", ui)
    ui[wkey] = None
    sync_obs_from_reading_widgets(obs, ui, allow_clear=True)
    assert "wg_cells" not in obs


def test_sync_keeps_obs_when_widget_none_but_allow_clear_false() -> None:
    obs = {"wg_cells": 12, "blue_cells": 35}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("obs_reading_wg_cells", ui)
    ui[wkey] = None
    sync_obs_from_reading_widgets(obs, ui, allow_clear=False)
    assert obs == {"wg_cells": 12, "blue_cells": 35}


def test_hydrate_force_numeric_overwrites_stale_widget() -> None:
    obs = {"wg_cells": 17, "blue_cells": 18}
    ui: dict = {"obs_readings_rev": 0}
    wg_key = reading_widget_key("obs_reading_wg_cells", ui)
    ui[wg_key] = None
    hydrate_reading_widgets_from_obs(obs, ui, force_numeric=True)
    assert ui[wg_key] == 17


def test_hydrate_refills_none_widget_from_obs_for_tab_return() -> None:
    obs = {"wg_cells": 22, "blue_cells": 15}
    ui: dict = {"obs_readings_rev": 0}
    wg_key = reading_widget_key("obs_reading_wg_cells", ui)
    ui[wg_key] = None
    hydrate_reading_widgets_from_obs(obs, ui)
    assert ui[wg_key] == 22


def test_hydrate_does_not_refill_after_obs_cleared() -> None:
    obs: dict = {}
    ui: dict = {"obs_readings_rev": 0}
    wg_key = reading_widget_key("obs_reading_wg_cells", ui)
    ui[wg_key] = None
    hydrate_reading_widgets_from_obs(obs, ui)
    assert ui[wg_key] is None
    hydrate_reading_widgets_from_obs(obs, ui, force_numeric=True)
    assert ui[wg_key] is None


def test_hydrate_avg_raw_respects_user_cleared_widget() -> None:
    from bidking_lab.capture.apply import reconcile_avg_raw_widget_return

    obs = {"purple_avg_raw": "2.66"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("purple_avg_raw_widget", ui)
    ui[wkey] = ""
    reconcile_avg_raw_widget_return(
        obs, ui, "purple_avg_raw", "purple_avg_raw_widget", "",
    )
    hydrate_reading_widgets_from_obs(obs, ui)
    assert ui[wkey] == ""
    sync_obs_from_reading_widgets(obs, ui, allow_clear=False)
    assert "purple_avg_raw" not in obs


def test_hydrate_force_avg_raw_refills_empty_widget() -> None:
    obs = {"purple_avg_raw": "3.27"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("purple_avg_raw_widget", ui)
    ui[wkey] = ""
    hydrate_reading_widgets_from_obs(obs, ui, force_avg_raw=True)
    assert ui[wkey] == "3.27"


def test_strip_empty_avg_raw_from_obs() -> None:
    obs = {"purple_avg_raw": "", "gold_avg_raw": "4.00"}
    ui: dict = {"obs_readings_rev": 0}
    hydrate_reading_widgets_from_obs(obs, ui)
    assert "purple_avg_raw" not in obs
    assert ui[reading_widget_key("gold_avg_raw_widget", ui)] == "4.00"


def test_reconcile_avg_raw_widget_return_from_obs() -> None:
    from bidking_lab.capture.apply import reconcile_avg_raw_widget_return

    obs = {"purple_avg_raw": "3.27"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("purple_avg_raw_widget", ui)
    ui[wkey] = ""
    out = reconcile_avg_raw_widget_return(
        obs, ui, "purple_avg_raw", "purple_avg_raw_widget", "",
    )
    assert out == ""
    assert "purple_avg_raw" not in obs
    assert ui[wkey] == ""


def test_reconcile_optional_number_keeps_obs_when_widgets_not_live() -> None:
    """Hint-tab rerun must not wipe obs when number_input is unmounted."""
    from bidking_lab.capture.apply import reconcile_optional_number_field

    obs = {"purple_value": 41_632}
    ui: dict = {"obs_readings_rev": 0}
    out = reconcile_optional_number_field(
        obs,
        ui,
        obs_key="purple_value",
        base_widget_key="obs_reading_purple_value",
        widget_return=None,
        widgets_live=False,
    )
    assert out == 41_632
    assert obs["purple_value"] == 41_632


def test_effective_number_preview_falls_back_to_obs_when_widget_none() -> None:
    from bidking_lab.capture.apply import (
        effective_number_field_for_preview,
        reading_widget_key,
    )

    obs = {"purple_count": 11}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("obs_reading_purple_count", ui)
    ui[wkey] = None
    assert (
        effective_number_field_for_preview(
            obs, ui,
            obs_key="purple_count", base_widget_key="obs_reading_purple_count",
        )
        == 11
    )


def test_sync_avg_clears_obs_when_mounted_widget_empty() -> None:
    """Cleared text_input must not leave a stale OCR value in obs."""
    obs = {"purple_avg_raw": "2.90"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("purple_avg_raw_widget", ui)
    ui[wkey] = ""
    sync_obs_from_reading_widgets(obs, ui, allow_clear=False)
    assert "purple_avg_raw" not in obs


def test_sync_avg_keeps_obs_when_widget_not_mounted() -> None:
    """Hint-tab OCR can set obs before the obs-tab widget key exists."""
    obs = {"purple_avg_raw": "2.90"}
    ui: dict = {"obs_readings_rev": 0}
    sync_obs_from_reading_widgets(obs, ui, allow_clear=False)
    assert obs["purple_avg_raw"] == "2.90"


def test_relax_bucket_drops_incompatible_gold_avg_value() -> None:
    from bidking_lab.inference.display import parse_reading
    from bidking_lab.inference.observation import (
        QualityBucketObs,
        candidates_for_bucket,
        relax_bucket_for_enumeration_preview,
    )

    bucket = QualityBucketObs(
        quality=5,
        total_cells=26,
        count=5,
        avg_value=15296.33,
    )
    assert not candidates_for_bucket(bucket, warehouse_capacity=123, other_known_cells=56)
    relaxed, dropped = relax_bucket_for_enumeration_preview(
        bucket, warehouse_capacity=123, other_known_cells=56,
    )
    assert "avg_value" in dropped
    assert candidates_for_bucket(relaxed, warehouse_capacity=123, other_known_cells=56)


def test_effective_number_preview_treats_zero_as_unset() -> None:
    from bidking_lab.capture.apply import effective_number_field_for_preview

    obs = {"gold_cells": 0}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("obs_reading_gold_cells", ui)
    ui[wkey] = 0
    assert (
        effective_number_field_for_preview(
            obs, ui,
            obs_key="gold_cells", base_widget_key="obs_reading_gold_cells",
        )
        is None
    )


def test_relax_strips_stale_zero_cells_after_dropping_avg() -> None:
    from bidking_lab.inference.observation import (
        QualityBucketObs,
        relax_bucket_for_enumeration_preview,
    )

    bucket = QualityBucketObs(quality=5, total_cells=0, avg_value=35100.0)
    relaxed, dropped = relax_bucket_for_enumeration_preview(
        bucket, warehouse_capacity=123,
    )
    assert relaxed.total_cells is None
    assert "avg_value" in dropped or "total_cells_zero" in dropped


def test_apply_gold_avg_only_clears_stale_gold_cells() -> None:
    from bidking_lab.capture.types import CaptureParseResult, FieldSuggestion

    obs = {"gold_cells": 0, "gold_count": 3}
    ui: dict = {"obs_readings_rev": 0}
    ck = reading_widget_key("obs_reading_gold_cells", ui)
    ui[ck] = 0
    result = CaptureParseResult(
        suggestions=[
            FieldSuggestion("gold_avg_value", 35100.0, "金品均价", 0.9, ""),
        ],
    )
    apply_capture_result(result, obs, ui)
    assert obs.get("gold_avg_value") == "35100"
    assert "gold_cells" not in obs
    assert ck not in ui


def test_apply_gold_cells_purges_stale_avg_widgets() -> None:
    from bidking_lab.capture.types import CaptureParseResult, FieldSuggestion

    obs = {"gold_avg_value": "15296.33", "gold_count": 9}
    ui: dict = {"obs_readings_rev": 0}
    avk = reading_widget_key("gold_avg_value_widget", ui)
    ui[avk] = "15296.33"
    result = CaptureParseResult(
        suggestions=[
            FieldSuggestion("gold_cells", 26, "金品总格", 0.9, ""),
            FieldSuggestion("wg_cells", 27, "白绿", 0.9, ""),
        ],
    )
    apply_capture_result(result, obs, ui)
    assert obs.get("gold_cells") == 26
    assert "gold_avg_value" not in obs
    assert avk not in ui


def test_clear_stale_capture_fields_on_partial_gold_ocr() -> None:
    from bidking_lab.capture.apply import clear_stale_capture_fields

    obs = {
        "gold_cells": 26,
        "gold_avg_value": "15296.33",
        "gold_count": 9,
    }
    ui: dict = {"obs_readings_rev": 0}
    cleared = clear_stale_capture_fields(obs, ui, {"gold_cells"})
    assert "gold_avg_value" in cleared
    assert "gold_count" in cleared
    assert obs == {"gold_cells": 26}


def test_hydrate_refills_cleared_number_widget_when_obs_has_value() -> None:
    obs = {"gold_cells": 26}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("obs_reading_gold_cells", ui)
    ui[wkey] = None
    hydrate_reading_widgets_from_obs(obs, ui)
    assert ui[wkey] == 26


def test_sync_clears_obs_when_number_widget_none_and_allow_clear() -> None:
    obs = {"gold_cells": 26, "gold_count": 9}
    ui: dict = {"obs_readings_rev": 0}
    ck = reading_widget_key("obs_reading_gold_cells", ui)
    cn = reading_widget_key("obs_reading_gold_count", ui)
    ui[ck] = None
    ui[cn] = None
    sync_obs_from_reading_widgets(obs, ui, allow_clear=True)
    assert "gold_cells" not in obs
    assert "gold_count" not in obs


def test_huge_band_sync_and_hydrate_roundtrip() -> None:
    from bidking_lab.capture.apply import (
        hydrate_huge_bands_from_obs,
        sync_huge_bands_to_obs,
    )

    obs: dict = {"gold_huge_band": "item:单人郊游快艇"}
    ui: dict = {"obs_gold_huge_band": "none"}
    hydrate_huge_bands_from_obs(obs, ui)
    assert ui["obs_gold_huge_band"] == "item:单人郊游快艇"
    ui["obs_gold_huge_band"] = "1"
    sync_huge_bands_to_obs(obs, ui)
    assert obs["gold_huge_band"] == "1"


def test_effective_number_field_for_preview_uses_widget_or_obs() -> None:
    from bidking_lab.capture.apply import effective_number_field_for_preview

    obs = {"gold_count": 9, "gold_cells": 26}
    ui: dict = {"obs_readings_rev": 0}
    ck = reading_widget_key("obs_reading_gold_count", ui)
    ui[ck] = None
    assert (
        effective_number_field_for_preview(
            obs, ui,
            obs_key="gold_count", base_widget_key="obs_reading_gold_count",
        )
        == 9
    )
    gk = reading_widget_key("obs_reading_gold_cells", ui)
    ui[gk] = 26
    assert (
        effective_number_field_for_preview(
            obs, ui,
            obs_key="gold_cells", base_widget_key="obs_reading_gold_cells",
        )
        == 26
    )


def test_effective_text_field_for_preview_empty_widget_falls_back_to_obs() -> None:
    from bidking_lab.capture.apply import effective_text_field_for_preview

    obs = {"gold_avg_raw": "4.00"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("gold_avg_raw_widget", ui)
    ui[wkey] = ""
    assert (
        effective_text_field_for_preview(
            obs, ui, obs_key="gold_avg_raw", base_widget_key="gold_avg_raw_widget",
        )
        == "4.00"
    )


def test_reconcile_silver_avg_clears_when_widget_mounted() -> None:
    from bidking_lab.capture.apply import reconcile_avg_raw_widget_return

    obs = {"gold_avg_value": "32507.6"}
    ui: dict = {"obs_readings_rev": 0}
    wkey = reading_widget_key("gold_avg_value_widget", ui)
    ui[wkey] = ""
    out = reconcile_avg_raw_widget_return(
        obs, ui, "gold_avg_value", "gold_avg_value_widget", "",
    )
    assert out == ""
    assert "gold_avg_value" not in obs
    assert ui[wkey] == ""


def test_round4_garbled_ocr_lines():
    """R4 sample OCR typos (before/after normalize) — panel_round4 screenshot."""
    from bidking_lab.capture.ocr_normalize import normalize_ocr_text

    raw = (
        "第4轮\n"
        "所有蓝色品质置品总占位数为35格\n"
        "所有自色和绿色品质震品总占位数为12格\n"
        "所有紫色品质费品总占位数为34格\n"
        "所有金色品质品总占位数为28格\n"
    )
    norm = normalize_ocr_text(raw)
    assert "藏品" in norm
    assert "白色" in norm
    m = parse_panel_text(raw, map_names=MAP_NAMES).suggestion_map()
    assert m == {"blue_cells": 35, "wg_cells": 12, "purple_cells": 34, "gold_cells": 28}


def test_full_scan_panel_ocr_typos():
    """R4 full-screen OCR: 占位数/紧色/扫描误字 + 所有{色}…总占 lines."""
    text = (
        "第4轮\n"
        "所有蓝色品质藏品总点位数为35格\n"
        "普品扫描：普品扫描\n"
        "所有白色和绿色品质藏品总占位数为12格\n"
        "优品扫描：代优品扫描\n"
        "所有紧色品质蓝品总占位数为34格\n"
        "极品扫描：极品扫描\n"
        "所有金色品质藏品总占位数为28格\n"
    )
    m = parse_panel_text(text, map_names=MAP_NAMES).suggestion_map()
    assert m["wg_cells"] == 12
    assert m["blue_cells"] == 35
    assert m["purple_cells"] == 34
    assert m["gold_cells"] == 28


def test_apply_does_not_change_hero():
    text = "艾莎\n伊森：空间觉知\n所有蓝色品质藏品总占位数为35格"
    result = parse_panel_text(text, map_names=MAP_NAMES)
    obs = {"hero": "ethan"}
    ui = {"obs_hero": "ethan"}
    apply_capture_result(result, obs, ui)
    assert ui["obs_hero"] == "ethan"
    assert obs.get("hero") == "ethan"


def test_apply_switches_map_category():
    from bidking_lab.capture.types import CaptureParseResult

    result = CaptureParseResult(map_id=2510, map_name="现代货轮娱乐库")
    obs = {"map_id": 2403, "hero": "ethan"}
    ui: dict = {
        "obs_map_category": "mansion",
        "obs_map_select": 2403,
    }
    apply_capture_result(result, obs, ui, map_names={2510: "现代货轮娱乐库"})
    assert ui["obs_map_category"] == "shipwreck"
    rev = int(ui.get("obs_map_select_rev", 0))
    assert ui[f"obs_map_select__r{rev}"] == 2510
    assert obs["map_id"] == 2510


def test_apply_purple_count_hydrates_versioned_widget_key() -> None:
    text = "本场拍卖共有紫色品质道具10件\n"
    parsed = parse_panel_text(text, map_names=MAP_NAMES)
    assert parsed.suggestion_map()["purple_count"] == 10
    obs: dict = {}
    ui: dict = {"obs_readings_rev": 0}
    apply_capture_result(parsed, obs, ui, map_names=MAP_NAMES)
    assert obs["purple_count"] == 10
    wkey = reading_widget_key("obs_reading_purple_count", ui)
    assert ui[wkey] == 10
    ui[wkey] = None
    obs["purple_count"] = 10
    hydrate_reading_widgets_from_obs(obs, ui, force_numeric=True)
    assert ui[wkey] == 10
    sync_obs_from_reading_widgets(obs, ui, allow_clear=True)
    assert obs["purple_count"] == 10
