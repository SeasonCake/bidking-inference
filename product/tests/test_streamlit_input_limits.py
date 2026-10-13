from pathlib import Path

from bidking_lab.capture.parser import parse_panel_text


APP_SOURCE = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def test_total_item_count_parser_accepts_values_above_legacy_ui_cap() -> None:
    result = parse_panel_text("本仓共有90件藏品", map_names={})

    assert result.suggestion_map()["total_item_count"] == 90


def test_observation_inputs_do_not_keep_legacy_small_caps() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "max_value=60" not in source
    assert "max_value=80" not in source


def test_bid_map_round_ids_are_labeled_as_skill_groups_not_item_categories() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "_map.round_skill_group_ids" in source
    assert "_map.round_category_hints" not in source
    assert "cat_names =" not in source
    assert "\\u9010\\u8f6e\\u4fe1\\u606f\\u6280\\u80fd\\u7ec4" in source
    assert "\\u4e0d\\u4ee3\\u8868\\u85cf\\u54c1\\u6389\\u843d" in source
