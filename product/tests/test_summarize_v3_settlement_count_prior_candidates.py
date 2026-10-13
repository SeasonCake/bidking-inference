import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_settlement_count_prior_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_settlement_count_prior_candidates",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _item(item_id: int, *, runtime_id: int, cells: int = 4) -> SimpleNamespace:
    return SimpleNamespace(runtime_id=runtime_id, item_id=item_id, cells=cells)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _state(
    map_id: int,
    item_ids: list[int],
    *,
    capture_time: str = "2026-06-06T12:00:00+08:00",
    session_token: str = "9000000000000004",
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=0x002D,
        capture_time=capture_time,
        session_id=f"{map_id}:{session_token}",
        round_index=5,
        map_id=map_id,
        inventory_items=tuple(
            _item(item_id, runtime_id=10_000 + index)
            for index, item_id in enumerate(item_ids)
        ),
    )


def _tables(*, include_map: bool = True) -> SimpleNamespace:
    raw_row = [""] * 23
    raw_row[14] = "[4,4,4]"
    raw_row[17] = "[9999,2601,1,2]"
    item_ids = (1001, 1002, 1003, 1004, 1005)
    maps = {}
    if include_map:
        maps[2601] = SimpleNamespace(
            map_id=2601,
            name="count_prior_map",
            rounds_total=25,
            drop_pool_id=2601,
            items_per_session_min=1,
            items_per_session_max=2,
            sub_pool_weights=[],
            round_category_hints=[103, 0, 0, 0, 0],
            raw_row=raw_row,
        )
    return SimpleNamespace(
        maps=maps,
        drops={
            2601: SimpleNamespace(
                entries=tuple(
                    SimpleNamespace(
                        category=101,
                        item_id=item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    )
                    for item_id in item_ids
                )
            )
        },
        items={
            1001: SimpleNamespace(
                item_id=1001, value=100, quality=6, shape_w=2, shape_h=2, tags=(103,)
            ),
            1002: SimpleNamespace(
                item_id=1002, value=100, quality=5, shape_w=2, shape_h=2, tags=(103,)
            ),
            1003: SimpleNamespace(
                item_id=1003, value=100, quality=4, shape_w=2, shape_h=2, tags=(104,)
            ),
            1004: SimpleNamespace(
                item_id=1004, value=100, quality=3, shape_w=2, shape_h=2, tags=(105,)
            ),
            1005: SimpleNamespace(
                item_id=1005, value=100, quality=6, shape_w=2, shape_h=2, tags=(102,)
            ),
        },
    )


def _patch_parser(
    monkeypatch,
    module,
    states_by_name,
    payloads_by_name=None,
    frame_meta_by_name=None,
):
    payloads_by_name = payloads_by_name or {}
    frame_meta_by_name = frame_meta_by_name or {}
    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda path: SimpleNamespace(states=(states_by_name[Path(path).name],)),
    )
    monkeypatch.setattr(
        module,
        "_latest_settlement_payload",
        lambda path: (
            payloads_by_name.get(Path(path).name),
            None,
            {
                "settlement_frame_count": (
                    1
                    if Path(path).name in payloads_by_name
                    or Path(path).name in frame_meta_by_name
                    else 0
                ),
                **frame_meta_by_name.get(Path(path).name, {}),
            },
        ),
    )


def test_settlement_count_prior_candidates_quantifies_table_residuals(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    first = tmp_path / "first_2rounds_case.json"
    second = tmp_path / "second_5rounds_case.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")
    _patch_parser(
        monkeypatch,
        module,
        {
            "first_2rounds_case.json": _state(
                2601,
                [1001, 1002, 1306003],
                capture_time="2026-05-31T12:00:00+08:00",
                session_token="9000000000000001",
            ),
            "second_5rounds_case.json": _state(
                2601,
                [1001, 1002, 1003, 1004, 1005, 1306004],
                capture_time="2026-06-06T12:00:00+08:00",
                session_token="9000000000000004",
            ),
        },
    )

    result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        min_samples=2,
    )

    assert result["files"] == 2
    assert result["settlement_rows"] == 2
    assert result["overall"]["above_drop_ref_rows"] == 2
    assert result["overall"]["above_drop_ref_after_temp_zodiac_rows"] == 1
    assert result["overall"]["unique_above_drop_ref_after_temp_zodiac_rows"] == 1
    assert result["overall"]["above_round_cap_rows"] == 1
    assert result["overall"]["above_round_cap_after_temp_zodiac_rows"] == 1
    assert result["overall"]["unique_above_round_cap_after_temp_zodiac_rows"] == 1
    assert result["overall"]["residual_modes"] == {
        "activity_extras_only_drop_ref_gap": 1,
        "round_cap_overflow_after_temp": 1,
    }
    assert result["overall"]["unique_residual_modes"] == {
        "activity_extras_only_drop_ref_gap": 1,
        "unique_round_cap_overflow_after_temp": 1,
    }
    assert result["overall"]["inventory_slot_headroom_after_temp_zodiac"]["n"] == 0
    assert result["overall"]["payload_field_shapes"] == {"none": 2}
    assert result["overall"]["payload_field5_count"]["max"] == 0
    assert result["overall"]["payload_field20_present_rows"] == 0
    assert result["overall"]["round_indices"] == {"5": 2}
    assert result["overall"]["capture_rounds"] == {"2": 1, "5": 1}
    assert result["overall"]["capture_days"] == {"20260531": 1, "20260606": 1}
    assert result["overall"]["session_token_prefix6_counts"] == {
        "127412": 1,
        "129501": 1,
    }
    assert result["overall"]["bidmap_sub_pool_kind_counts"] == {"leaf": 2}
    assert result["overall"]["bidmap_sub_pool_count"]["max"] == 0
    assert result["overall"]["bidmap_rounds_total_counts"] == {"25": 2}
    assert result["overall"]["bidmap_round_category_hint_key_counts"] == {"103": 2}
    assert result["overall"]["bidmap_round_category_hint_count"]["max"] == 1
    assert result["overall"]["unique_runtime_id_count"]["max"] == 6
    assert result["overall"]["duplicate_runtime_id_count"]["max"] == 0
    assert result["overall"]["unique_item_id_count"]["max"] == 6
    assert result["overall"]["duplicate_item_id_count"]["max"] == 0
    assert result["overall"]["unique_non_temp_item_id_count"]["max"] == 5
    assert result["overall"]["duplicate_non_temp_item_id_count"]["max"] == 0
    assert result["overall"]["unique_non_temp_primary_category_count"]["max"] == 4
    assert result["overall"]["unique_hinted_non_temp_item_count"]["max"] == 2
    assert result["overall"]["unique_unhinted_non_temp_item_count"]["max"] == 3
    assert result["overall"]["unique_non_temp_primary_category_counts"] == {
        "102": 1,
        "103": 4,
        "104": 1,
        "105": 1,
    }
    assert result["overall"]["non_temp_quality_counts"] == {
        "q3": 1,
        "q4": 1,
        "q5": 2,
        "q6": 3,
    }
    assert result["overall"]["unique_non_temp_quality_counts"] == {
        "q3": 1,
        "q4": 1,
        "q5": 2,
        "q6": 3,
    }
    assert result["overall"]["unique_non_temp_inventory_cells"]["max"] == 20
    assert result["overall"]["unique_q6_non_temp_item_count"]["max"] == 2
    assert result["overall"]["unique_q6_non_temp_cells"]["max"] == 8
    assert result["overall"]["unique_runtime_item_pair_count"]["max"] == 6
    assert result["overall"]["duplicate_runtime_item_pair_count"]["max"] == 0
    assert result["overall"]["missing_from_drop_universe_count"]["max"] == 1
    assert (
        result["overall"]["known_temp_zodiac_missing_from_drop_universe_count"]["max"]
        == 1
    )
    assert result["overall"]["non_zodiac_missing_from_drop_universe_count"]["max"] == 0
    assert result["overall"]["missing_from_drop_universe_positive_rows"] == 2
    assert result["overall"]["non_zodiac_missing_from_drop_universe_positive_rows"] == 0
    assert result["overall"]["missing_from_drop_universe_examples"] == {
        "1306003": 1,
        "1306004": 1,
    }
    assert result["overall"]["full_observed_action_rows"] == 0
    assert result["overall"]["public_total_rows"] == 0
    row = result["rows"][0]
    assert row["group"] == "2601"
    assert row["candidate_status"] == "observed_exceeds_table_caps_shadow_only"
    assert row["files"] == 2
    assert row["residual_modes"] == {
        "activity_extras_only_drop_ref_gap": 1,
        "round_cap_overflow_after_temp": 1,
    }
    assert row["unique_residual_modes"] == {
        "activity_extras_only_drop_ref_gap": 1,
        "unique_round_cap_overflow_after_temp": 1,
    }
    assert row["round_indices"] == {"5": 2}
    assert row["capture_rounds"] == {"2": 1, "5": 1}
    assert row["capture_days"] == {"20260531": 1, "20260606": 1}
    assert row["session_token_prefix6_counts"] == {"127412": 1, "129501": 1}
    assert row["bidmap_sub_pool_kind_counts"] == {"leaf": 2}
    assert row["bidmap_sub_pool_count"]["max"] == 0
    assert row["bidmap_rounds_total_counts"] == {"25": 2}
    assert row["bidmap_round_category_hint_key_counts"] == {"103": 2}
    assert row["bidmap_round_category_hint_count"]["max"] == 1
    assert row["bidmap_items_per_session_max"]["max"] == 2
    assert row["bidmap_raw_round_cap_max"]["max"] == 4
    assert row["inventory_count"]["max"] == 6
    assert row["non_temp_inventory_count"]["max"] == 5
    assert row["known_temp_zodiac_count"]["max"] == 1
    assert row["unique_runtime_id_count"]["max"] == 6
    assert row["duplicate_runtime_id_count"]["max"] == 0
    assert row["unique_item_id_count"]["max"] == 6
    assert row["duplicate_item_id_count"]["max"] == 0
    assert row["unique_non_temp_item_id_count"]["max"] == 5
    assert row["duplicate_non_temp_item_id_count"]["max"] == 0
    assert row["unique_non_temp_primary_category_count"]["max"] == 4
    assert row["unique_hinted_non_temp_item_count"]["max"] == 2
    assert row["unique_unhinted_non_temp_item_count"]["max"] == 3
    assert row["unique_non_temp_primary_category_counts"] == {
        "102": 1,
        "103": 4,
        "104": 1,
        "105": 1,
    }
    assert row["non_temp_quality_counts"] == {
        "q3": 1,
        "q4": 1,
        "q5": 2,
        "q6": 3,
    }
    assert row["unique_non_temp_quality_counts"] == {
        "q3": 1,
        "q4": 1,
        "q5": 2,
        "q6": 3,
    }
    assert row["unique_non_temp_inventory_cells"]["max"] == 20
    assert row["unique_q6_non_temp_item_count"]["max"] == 2
    assert row["unique_q6_non_temp_cells"]["max"] == 8
    assert row["unique_runtime_item_pair_count"]["max"] == 6
    assert row["duplicate_runtime_item_pair_count"]["max"] == 0
    assert row["missing_from_drop_universe_count"]["max"] == 1
    assert row["known_temp_zodiac_missing_from_drop_universe_count"]["max"] == 1
    assert row["non_zodiac_missing_from_drop_universe_count"]["max"] == 0
    assert row["missing_from_drop_universe_positive_rows"] == 2
    assert row["non_zodiac_missing_from_drop_universe_positive_rows"] == 0
    assert row["missing_from_drop_universe_examples"] == {
        "1306003": 1,
        "1306004": 1,
    }
    assert row["payload_field_shapes"] == {"none": 2}
    assert row["payload_field8_count"]["max"] == 0
    assert row["payload_field20_present_rows"] == 0
    assert row["drop_ref_excess_after_temp_zodiac_count"]["max"] == 3
    assert row["unique_drop_ref_excess_after_temp_zodiac_count"]["max"] == 3
    assert row["round_cap_excess_after_temp_zodiac_count"]["max"] == 1
    assert row["unique_round_cap_excess_after_temp_zodiac_count"]["max"] == 1

    residual_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="residual_mode",
        min_samples=1,
    )
    residual_rows = {row["group"]: row for row in residual_result["rows"]}
    assert set(residual_rows) == {
        "activity_extras_only_drop_ref_gap",
        "round_cap_overflow_after_temp",
    }
    assert (
        residual_rows["round_cap_overflow_after_temp"][
            "above_round_cap_after_temp_zodiac_rows"
        ]
        == 1
    )
    assert (
        residual_rows["activity_extras_only_drop_ref_gap"][
            "above_drop_ref_after_temp_zodiac_rows"
        ]
        == 0
    )

    unique_residual_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="unique_residual_mode",
        min_samples=1,
    )
    assert {row["group"] for row in unique_residual_result["rows"]} == {
        "activity_extras_only_drop_ref_gap",
        "unique_round_cap_overflow_after_temp",
    }

    round_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="round_index",
        min_samples=1,
    )
    assert [row["group"] for row in round_result["rows"]] == ["5"]

    capture_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="capture_rounds",
        min_samples=1,
    )
    assert {row["group"] for row in capture_result["rows"]} == {"2", "5"}

    table_round_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="bidmap_rounds_total",
        min_samples=1,
    )
    assert [row["group"] for row in table_round_result["rows"]] == ["25"]

    hint_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="bidmap_round_category_hint_key",
        min_samples=1,
    )
    assert [row["group"] for row in hint_result["rows"]] == ["103"]

    sub_pool_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="bidmap_sub_pool_kind",
        min_samples=1,
    )
    assert [row["group"] for row in sub_pool_result["rows"]] == ["leaf"]

    sub_pool_count_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="bidmap_sub_pool_count",
        min_samples=1,
    )
    assert [row["group"] for row in sub_pool_count_result["rows"]] == ["0"]

    capture_day_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="capture_day",
        min_samples=1,
    )
    assert {row["group"] for row in capture_day_result["rows"]} == {
        "20260531",
        "20260606",
    }

    session_prefix_result = module.summarize_settlement_count_prior_candidates(
        [tmp_path],
        tables=_tables(),
        group_by="session_token_prefix6",
        min_samples=1,
    )
    assert {row["group"] for row in session_prefix_result["rows"]} == {
        "127412",
        "129501",
    }


def test_settlement_count_prior_candidates_profiles_payload_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "payload_1round_case.json"
    sample.write_text("[]", encoding="utf-8")
    payload = b"".join(
        (
            _field_bytes(1, b"session"),
            _field_varint(2, 2601),
            _field_varint(3, 1),
            _field_bytes(5, b"\x08\x01"),
            _field_bytes(5, b"\x08\x02"),
            _field_bytes(8, b"\x08\x03"),
            _field_varint(20, 1),
        )
    )
    _patch_parser(
        monkeypatch,
        module,
        {"payload_1round_case.json": _state(2601, [1001, 1002])},
        payloads_by_name={"payload_1round_case.json": payload},
        frame_meta_by_name={
            "payload_1round_case.json": {
                "settlement_outer_field_shape": (
                    "1:0:ix1,2:2:bx1,3:0:ix1,4:0:ix1,5:0:ix1,6:2:bx4"
                ),
                "settlement_outer_field3_present": True,
                "settlement_outer_field4_present": True,
                "settlement_outer_field5_present": True,
                "settlement_outer_field6_count": 4,
            },
        },
    )

    result = module.summarize_settlement_count_prior_candidates(
        [sample],
        tables=_tables(),
        min_samples=1,
    )

    assert result["overall"]["payload_field5_count"]["max"] == 2
    assert result["overall"]["payload_field8_count"]["max"] == 1
    assert result["overall"]["payload_field20_present_rows"] == 1
    assert result["overall"]["payload_field20_values"] == {"1": 1}
    assert result["overall"]["payload_field5_child_signatures"] == {"1:0:i": 2}
    assert result["overall"]["payload_field8_child_signatures"] == {"1:0:i": 1}
    assert result["overall"]["settlement_outer_field_shapes"] == {
        "1:0:ix1,2:2:bx1,3:0:ix1,4:0:ix1,5:0:ix1,6:2:bx4": 1,
    }
    assert result["overall"]["settlement_outer_field3_present_rows"] == 1
    assert result["overall"]["settlement_outer_field4_present_rows"] == 1
    assert result["overall"]["settlement_outer_field5_present_rows"] == 1
    assert result["overall"]["settlement_outer_field6_count"]["max"] == 4
    row = result["rows"][0]
    assert row["payload_field5_count"]["max"] == 2
    assert row["payload_field8_count"]["max"] == 1
    assert row["payload_field20_present_rows"] == 1
    assert row["payload_field20_values"] == {"1": 1}
    assert row["payload_field5_child_signatures"] == {"1:0:i": 2}
    assert row["payload_field8_child_signatures"] == {"1:0:i": 1}
    assert row["settlement_outer_field_shapes"] == {
        "1:0:ix1,2:2:bx1,3:0:ix1,4:0:ix1,5:0:ix1,6:2:bx4": 1,
    }
    assert row["settlement_outer_field3_present_rows"] == 1
    assert row["settlement_outer_field4_present_rows"] == 1
    assert row["settlement_outer_field5_present_rows"] == 1
    assert row["settlement_outer_field6_count"]["max"] == 4
    assert row["payload_field_shapes"] == {
        "1:2x1,2:0x1,3:0x1,5:2x2,8:2x1,20:0x1": 1,
    }


def test_settlement_count_prior_candidates_marks_missing_table_shadow_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "activity.json"
    sample.write_text("[]", encoding="utf-8")
    _patch_parser(
        monkeypatch,
        module,
        {"activity.json": _state(2521, [1001, 1002, 1003])},
    )

    result = module.summarize_settlement_count_prior_candidates(
        [sample],
        tables=_tables(include_map=False),
        min_samples=1,
    )

    assert result["overall"]["missing_table_rows"] == 1
    assert result["rows"][0]["group"] == "2521"
    assert result["rows"][0]["candidate_status"] == "missing_table_shadow_only"
    assert result["rows"][0]["table_statuses"] == {"missing_bidmap": 1}


def test_settlement_count_prior_candidates_rejects_unknown_group_by() -> None:
    module = _load_module()

    with pytest.raises(ValueError):
        module.summarize_settlement_count_prior_candidates(
            [],
            tables=_tables(),
            group_by="unknown",
        )
