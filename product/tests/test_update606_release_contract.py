from __future__ import annotations

import json

import pytest

from bidking_lab.extract import bid_map_table
from bidking_lab.extract.bid_map_table import (
    BID_MAP_0_3_4_PROFILE,
    BID_MAP_OFFICIAL_20260903_PROFILE,
    BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE,
    BID_MAP_TABLE_COLUMN_COUNT_UPDATE606,
    BID_MAP_UPDATE606_COLUMN_NAMES,
    BID_MAP_UPDATE606_PROFILE,
    parse_bid_map_table,
)
from bidking_lab.extract.update606_release_contract import (
    UPDATE606_MANIFEST_SHA256,
    UPDATE606_PACKAGE_GENERATION,
    UPDATE606_SCRIPTS_SHA256,
    UPDATE606_TABLES_BUNDLE_SHA256,
    _rows_from_exact_text,
    _validate_activity,
    _validate_activity_map,
    validate_update606_release_contract,
)


def _update606_rows() -> list[list[str]]:
    ids = [
        *range(1, 206),
        5506,
        5606,
        6001,
        *range(5001, 5011),
        *range(5101, 5111),
    ]
    costs = ("10", "15", "20", "25", "30")
    rows: list[list[str]] = []
    for index, map_id in enumerate(ids):
        row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT_UPDATE606
        row[0] = str(map_id)
        row[1] = f"map-{map_id}"
        row[2] = "desc"
        row[7] = "501" if map_id in range(5101, 5111) else "101"
        row[9] = "[[]]"
        row[10] = "ui_value_low"
        row[11] = "30" if map_id in {5506, 6001} else costs[index % len(costs)]
        row[12] = "[1,1,1000]"
        row[15] = "[[1,1,5000]]"
        row[17] = (
            f"[9999,{map_id - 100},22,44]"
            if map_id in range(5101, 5111)
            else f"[9999,{map_id},10,20]"
        )
        row[18] = "4"
        row[19] = "[1000,900,800,700,0]"
        row[20] = (
            "[30105,0,0,0,0]"
            if map_id in range(5101, 5111)
            else "[102,0,0,0,0]"
        )
        rows.append(row)
    next(row for row in rows if row[0] == "5101")[9] = (
        "[[5101,10],[5102,10],[5102,10],[5103,10],[5104,10],"
        "[5105,10],[5106,10],[5107,10],[5108,10],[5109,10],[5110,10]]"
    )
    return rows


def _approve_synthetic_identity(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = bid_map_table._canonical_table_sha256(rows)  # noqa: SLF001
    monkeypatch.setitem(
        bid_map_table._EXACT_TABLE_PROFILES,  # noqa: SLF001
        identity,
        BID_MAP_UPDATE606_PROFILE,
    )


def _0_3_4_rows() -> list[list[str]]:
    rows = _update606_rows()
    row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT_UPDATE606
    row[0] = "5202"
    row[1] = "map-5202"
    row[2] = "desc"
    row[7] = "501"
    row[9] = "[[]]"
    row[10] = "ui_value_low"
    row[11] = "10"
    row[12] = "[1,1,1000]"
    row[15] = "[[1,1,5000]]"
    row[17] = "[9999,5201,1,1]"
    row[18] = "4"
    row[19] = "[1000,900,800,700,0]"
    row[20] = "[10301,0,0,0,0]"
    rows.append(row)
    row_6001 = next(value for value in rows if value[0] == "6001")
    row_6001[7] = "601"
    row_6001[14] = "[60,60,60,60,60]"
    row_6001[17] = "[9999,2601,22,44]"
    row_6001[18] = "20"
    row_6001[20] = "[103,103,103,104,105]"
    return rows


def _approve_synthetic_0_3_4_identity(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = bid_map_table._canonical_table_sha256(rows)  # noqa: SLF001
    monkeypatch.setitem(
        bid_map_table._EXACT_TABLE_PROFILES,  # noqa: SLF001
        identity,
        BID_MAP_0_3_4_PROFILE,
    )


_OFFICIAL_20260903_ACTIVITY_REFS = {
    5203: [9999, 6101, 20, 30],
    5204: [9999, 5503, 44, 44],
    5205: [9999, 6201, 33, 44],
    5206: [9999, 2601, 33, 44],
    5207: [9999, 5502, 33, 44],
    5208: [9999, 5301, 1, 1],
    5209: [9999, 5504, 33, 44],
    5210: [9999, 6301, 33, 44],
    5211: [9999, 5303, 1, 1],
    5212: [9999, 2601, 33, 44],
    5213: [9999, 6401, 33, 44],
    5214: [9999, 5510, 33, 44],
    5215: [9999, 5305, 1, 1],
    5216: [9999, 2601, 33, 44],
    5217: [9999, 2601, 33, 44],
    5218: [9999, 2601, 33, 44],
    5219: [9999, 5307, 1, 1],
}
_OFFICIAL_20260903_ACTIVITY_SKILLS = {
    5203: [10401, 10402, 10403, 10404, 10405],
    5204: [10501, 0, 10503, 10504, 10505],
    5205: [10601, 0, 10603, 10604, 10605],
    5206: [10701, 10702, 10703, 10704, 10705],
    5207: [10901, 10902, 10903, 10904, 10905],
    5208: [11001, 0, 0, 0, 0],
    5209: [20101, 20102, 20103, 20104, 20105],
    5210: [20201, 20202, 20203, 20204, 20205],
    5211: [20301, 0, 20303, 20304, 20305],
    5212: [20401, 20402, 20403, 20404, 20405],
    5213: [20501, 20502, 20503, 0, 0],
    5214: [20601, 20602, 20603, 20604, 20605],
    5215: [20701, 20702, 20703, 20704, 0],
    5216: [20801, 20802, 20803, 20804, 20805],
    5217: [20901, 20902, 20903, 0, 0],
    5218: [30201, 30202, 0, 0, 0],
    5219: [30301, 0, 30303, 30304, 30305],
}


def _official_20260903_rows() -> list[list[str]]:
    special_ids = {
        5506,
        6001,
        *range(5001, 5011),
        *range(5101, 5111),
        *range(5202, 5220),
        *range(5601, 5612),
    }
    ids = [*range(1, 196), *sorted(special_ids)]
    assert len(ids) == 246
    costs = ("10", "15", "20", "25", "30")
    rows: list[list[str]] = []
    for index, map_id in enumerate(ids):
        row = ["0"] * BID_MAP_TABLE_COLUMN_COUNT_UPDATE606
        row[0] = str(map_id)
        row[1] = f"map-{map_id}"
        row[2] = "desc"
        row[7] = "101"
        row[9] = "[[]]"
        row[10] = "ui_value_low"
        row[11] = costs[index % len(costs)]
        row[12] = "[1,1,1000]"
        row[14] = "[30,30,30,30,30]"
        row[15] = "[[1,1,5000]]"
        row[17] = f"[9999,{map_id},10,20]"
        row[18] = "4"
        row[19] = "[1000,900,800,700,0]"
        row[20] = "[102,0,0,0,0]"
        rows.append(row)
    indexed = {int(row[0]): row for row in rows}
    indexed[5101][9] = (
        "[[5101,10],[5102,10],[5102,10],[5103,10],[5104,10],"
        "[5105,10],[5106,10],[5107,10],[5108,10],[5109,10],[5110,10]]"
    )
    for map_id in range(5101, 5111):
        indexed[map_id][7] = "601"
        indexed[map_id][17] = f"[9999,{map_id - 100},33,44]"
        indexed[map_id][20] = "[30105,0,0,0,0]"
    indexed[5202][7] = "601"
    indexed[5202][17] = "[9999,5201,1,1]"
    indexed[5202][18] = "10"
    indexed[5202][20] = "[10301,0,0,0,0]"
    for map_id in range(5203, 5220):
        row = indexed[map_id]
        is_5218 = map_id == 5218
        row[7] = "501" if is_5218 else "601"
        row[11] = "30"
        row[12] = "[1,1,25000]"
        row[14] = "[50,50,50,50,50]" if is_5218 else "[30,30,30,30,30]"
        row[15] = "[[1,1,5000000]]"
        row[17] = json.dumps(
            _OFFICIAL_20260903_ACTIVITY_REFS[map_id], separators=(",", ":")
        )
        row[18] = "4" if is_5218 else "10"
        row[20] = json.dumps(
            _OFFICIAL_20260903_ACTIVITY_SKILLS[map_id], separators=(",", ":")
        )
    ranked_root = [[map_id, 20] for map_id in range(5601, 5612)]
    for map_id in range(5601, 5612):
        row = indexed[map_id]
        row[7] = "402"
        row[8] = "2" if map_id == 5611 else "1"
        row[9] = json.dumps(
            ranked_root if map_id == 5601 else [[]], separators=(",", ":")
        )
        row[11] = "30"
        row[12] = "[1,1,15000]"
        row[14] = "[60,60,60,60,60]"
        row[15] = "[[1,1,2000000]]"
        row[17] = f"[9999,{map_id - 2900},25,30]"
        row[18] = "4"
        row[20] = "[103,0,0,0,0]"
    indexed[6001][7] = "601"
    indexed[6001][11] = "30"
    indexed[6001][14] = "[30,30,30,30,30]"
    indexed[6001][17] = "[9999,2601,44,44]"
    indexed[6001][18] = "20"
    indexed[6001][20] = "[103,103,103,104,105]"
    return rows


def _approve_synthetic_official_20260903_identity(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = bid_map_table._canonical_table_sha256(rows)  # noqa: SLF001
    monkeypatch.setitem(
        bid_map_table._EXACT_TABLE_PROFILES,  # noqa: SLF001
        identity,
        BID_MAP_OFFICIAL_20260903_PROFILE,
    )


def _approve_synthetic_official_20260903_hotfix_identity(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = bid_map_table._canonical_table_sha256(rows)  # noqa: SLF001
    monkeypatch.setitem(
        bid_map_table._EXACT_TABLE_PROFILES,  # noqa: SLF001
        identity,
        BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE,
    )


def test_update606_named_schema_and_exact_228x28_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(BID_MAP_UPDATE606_COLUMN_NAMES) == 28
    assert BID_MAP_UPDATE606_COLUMN_NAMES[11] == "item_cost"
    assert BID_MAP_UPDATE606_COLUMN_NAMES[21:] == (
        "map_resource_rectangle",
        "map_resource_bg",
        "map_resource_cube",
        "tap_up",
        "map_avg_cell_value",
        "value_bias",
        "round_w",
    )
    rows = _update606_rows()
    _approve_synthetic_identity(rows, monkeypatch)

    parsed = parse_bid_map_table(rows)

    assert len(parsed) == 228
    assert parsed[5506].raw_row[7] == "101"
    assert parsed[6001].col11_value_tier_scale_basis_points == 3000


def test_unknown_28_column_identity_is_never_position_guessed() -> None:
    with pytest.raises(ValueError, match="exact approved generation"):
        parse_bid_map_table(_update606_rows())


def test_0_3_4_named_schema_and_exact_229x28_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _0_3_4_rows()
    _approve_synthetic_0_3_4_identity(rows, monkeypatch)

    parsed = parse_bid_map_table(rows)

    assert len(parsed) == 229
    assert parsed[5202].drop_pool_id == 5201
    assert parsed[5202].items_per_session_min == 1
    assert parsed[6001].drop_pool_id == 2601
    assert parsed[6001].mode_flag == 20
    assert parsed[6001].auction_mode == "auction_hall"


def test_0_3_4_static_6001_identity_tamper_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _0_3_4_rows()
    next(row for row in rows if row[0] == "6001")[18] = "4"
    _approve_synthetic_0_3_4_identity(rows, monkeypatch)

    with pytest.raises(ValueError, match=r"BidMap\[6001\] identity mismatch"):
        parse_bid_map_table(rows)


def test_official_20260903_exact_246x28_map_canaries_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _official_20260903_rows()
    _approve_synthetic_official_20260903_identity(rows, monkeypatch)

    parsed = parse_bid_map_table(rows)

    assert len(parsed) == 246
    assert parsed[5203].drop_pool_id == 6101
    assert parsed[5219].round_skill_group_ids == [30301, 0, 30303, 30304, 30305]
    assert parsed[5601].category == 402
    assert parsed[5601].sub_pool_weights == [
        (map_id, 20) for map_id in range(5601, 5612)
    ]
    assert (parsed[6001].items_per_session_min, parsed[6001].items_per_session_max) == (
        44,
        44,
    )
    assert parsed[6001].auction_mode == "auction_hall"
    assert len(
        {
            bid_map_table.BID_MAP_OFFICIAL_20260903_RAW_TEXT_SHA256,
            bid_map_table.BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256,
            bid_map_table.BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_SHA256,
        }
    ) == 3
    assert (
        bid_map_table.BID_MAP_OFFICIAL_20260903_CANONICAL_BYTES,
        bid_map_table.BID_MAP_OFFICIAL_20260903_RAW_TEXT_BYTES,
        bid_map_table.BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_BYTES,
    ) == (103_766, 104_014, 138_356)


def test_official_20260903_hotfix_map_icons_do_not_change_typed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_rows = _official_20260903_rows()
    old_5601 = next(row for row in historical_rows if row[0] == "5601")
    old_5601[21:24] = ["iconmap_400"] * 3
    hotfix_rows = [list(row) for row in historical_rows]
    new_5601 = next(row for row in hotfix_rows if row[0] == "5601")
    new_5601[21:24] = ["iconmap_59"] * 3
    _approve_synthetic_official_20260903_identity(
        historical_rows,
        monkeypatch,
    )
    _approve_synthetic_official_20260903_hotfix_identity(
        hotfix_rows,
        monkeypatch,
    )

    historical = parse_bid_map_table(historical_rows)
    hotfix = parse_bid_map_table(hotfix_rows)

    assert {
        map_id: row.model_dump(exclude={"raw_row"})
        for map_id, row in historical.items()
    } == {
        map_id: row.model_dump(exclude={"raw_row"})
        for map_id, row in hotfix.items()
    }
    assert [
        int(before[0])
        for before, after in zip(historical_rows, hotfix_rows, strict=True)
        if before != after
    ] == [5601]
    assert (
        bid_map_table.BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES,
        bid_map_table.BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
        bid_map_table.BID_MAP_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES,
    ) == (103_763, 104_011, 138_352)


def test_official_20260903_unknown_246x28_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="exact approved generation"):
        parse_bid_map_table(_official_20260903_rows())


@pytest.mark.parametrize(
    ("map_id", "column", "value", "expected"),
    (
        (5203, 17, "[9999,6101,21,30]", r"BidMap\[5203\] activity identity"),
        (5219, 20, "[30301,0,0,0,0]", r"BidMap\[5219\] activity identity"),
        (5601, 9, "[[]]", r"BidMap\[5601\] ranked identity"),
        (5611, 17, "[9999,2711,24,30]", r"BidMap\[5611\] ranked identity"),
        (6001, 17, "[9999,2601,22,44]", r"BidMap\[6001\] static identity"),
    ),
)
def test_official_20260903_map_canary_tamper_fails_closed(
    map_id: int,
    column: int,
    value: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _official_20260903_rows()
    next(row for row in rows if row[0] == str(map_id))[column] = value
    _approve_synthetic_official_20260903_identity(rows, monkeypatch)

    with pytest.raises(ValueError, match=expected):
        parse_bid_map_table(rows)


@pytest.mark.parametrize("mutation", ("missing-row", "wrong-columns"))
def test_update606_identity_rejects_wrong_shape_even_when_hash_is_registered(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _update606_rows()
    if mutation == "missing-row":
        rows.pop()
    else:
        rows[0].pop()
    _approve_synthetic_identity(rows, monkeypatch)

    with pytest.raises(ValueError, match="exactly 228 rows|28 named columns"):
        parse_bid_map_table(rows)


def test_release_contract_rejects_missing_assets_before_semantic_use() -> None:
    with pytest.raises(ValueError, match="TextAsset set mismatch"):
        validate_update606_release_contract(
            {},
            package_generation=UPDATE606_PACKAGE_GENERATION,
            manifest_sha256=UPDATE606_MANIFEST_SHA256,
            tables_bundle_sha256=UPDATE606_TABLES_BUNDLE_SHA256,
            scripts_sha256=UPDATE606_SCRIPTS_SHA256,
        )


def test_release_contract_rejects_mixed_generation_before_assets() -> None:
    with pytest.raises(ValueError, match="package_generation identity mismatch"):
        validate_update606_release_contract(
            {},
            package_generation="89437c-2026-08-07-745",
            manifest_sha256=UPDATE606_MANIFEST_SHA256,
            tables_bundle_sha256=UPDATE606_TABLES_BUNDLE_SHA256,
            scripts_sha256=UPDATE606_SCRIPTS_SHA256,
        )


def test_release_contract_rejects_wrong_text_asset_hash() -> None:
    with pytest.raises(ValueError, match="BidMap TextAsset identity mismatch"):
        _rows_from_exact_text("BidMap", "not-the-official-table")


def _activity_map_rows() -> list[list[str]]:
    def row(row_id: int) -> list[str]:
        value = [""] * 18
        value[0] = str(row_id)
        return value

    rows = {
        row_id: row(row_id)
        for row_id in (10003, 10102, 10103, 20001, 20002, 30001)
    }
    rows[10102][8] = "暗拍沉船白色降红色升"
    rows[10102][12] = "[4521,4522,4523,4524,4545,4526,4527,4528,4529,4530]"
    rows[10103][8] = "暗拍沉船数量up"
    rows[10103][12] = "[4531,4532,4533,4534,4535,4536,4537,4538,4539,4540]"
    rows[10003][12] = "[2531,2532,2533,2534,2535,2536,2537,2538,2539,2540]"
    rows[20001][1] = "[501]"
    rows[20001][12] = "[5506]"
    rows[20002][1] = "[501]"
    rows[20002][5] = "[[3,301]]"
    rows[20002][12] = "[5101]"
    rows[30001][1] = "[601]"
    rows[30001][12] = "[4541]"
    return list(rows.values())


def test_activity_map_10102_10103_effects_and_routes_positive() -> None:
    _validate_activity_map(_activity_map_rows())


@pytest.mark.parametrize(
    ("row_id", "wrong_label"),
    (
        (10102, "暗拍沉船数量up"),
        (10103, "暗拍沉船白色降红色升"),
    ),
)
def test_activity_map_old_id_semantics_are_rejected(
    row_id: int,
    wrong_label: str,
) -> None:
    rows = _activity_map_rows()
    next(row for row in rows if row[0] == str(row_id))[8] = wrong_label

    with pytest.raises(ValueError, match="effect mismatch"):
        _validate_activity_map(rows)


def test_activity_map_601_parent_does_not_open_6001() -> None:
    rows = _activity_map_rows()
    next(row for row in rows if row[0] == "30001")[12] = "[6001]"

    with pytest.raises(ValueError, match="must not imply 6001 opening"):
        _validate_activity_map(rows)


def _activity_rows() -> list[list[str]]:
    def row(row_id: int) -> list[str]:
        value = [""] * 19
        value[0] = str(row_id)
        return value

    rows = {
        row_id: row(row_id)
        for row_id in (30002, 30003, 40001, 40002, 50001)
    }
    rows[30002][18] = "[10002,10102]"
    rows[30003][18] = "[10003,10103]"
    rows[40001][16] = "[2,501]"
    rows[40001][18] = "[20001]"
    rows[40002][16] = "[2,501]"
    rows[40002][18] = "[20002]"
    rows[50001][18] = "[30001]"
    return list(rows.values())


def test_activity_id_to_effect_routes_positive_and_reverse_control() -> None:
    rows = _activity_rows()
    _validate_activity(rows)
    next(row for row in rows if row[0] == "30002")[18] = "[10002,10103]"

    with pytest.raises(ValueError, match="10102 effect route mismatch"):
        _validate_activity(rows)
