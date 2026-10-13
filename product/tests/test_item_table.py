"""Tests for Item.txt parser."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from bidking_lab.extract.item_table import (
    ITEM_TABLE_COLUMN_COUNT,
    ITEM_TABLE_COLUMN_COUNT_UPDATE606,
    ITEM_TABLE_COLUMN_COUNTS,
    load_item_table,
    parse_item_row,
    parse_item_table,
)
from bidking_lab.extract import item_table


def _make_row(**overrides: str) -> list[str]:
    row = ["0"] * ITEM_TABLE_COLUMN_COUNT
    row[0] = "1"
    row[1] = "测试物品"
    row[2] = "一个测试物品"
    row[3] = "itemName_1"
    row[5] = "itemDesc_1"
    row[6] = "[1]"
    row[8] = "3"
    row[9] = "1500"
    row[19] = "[101,102]"
    row[24] = "icon_test"
    row[33] = "Cube"
    row[37] = ""
    for k, v in overrides.items():
        idx = int(k[3:])
        row[idx] = v
    return row


def _make_40_column_row(**overrides: str) -> list[str]:
    row = _make_row()
    row.insert(10, "1")
    row.append("0")
    for k, v in overrides.items():
        idx = int(k[3:])
        row[idx] = v
    return row


def _make_update606_rows(*, associated_rows: int = 666) -> list[list[str]]:
    rows: list[list[str]] = []
    for index in range(item_table.ITEM_TABLE_UPDATE606_ROWS):
        row = ["0"] * ITEM_TABLE_COLUMN_COUNT_UPDATE606
        row[0] = str(index + 1)
        row[1] = f"item-{index + 1}"
        row[2] = "desc"
        row[3] = f"itemName_{index + 1}"
        row[5] = f"itemDesc_{index + 1}"
        row[6] = "[101]"
        row[7] = "11"
        row[8] = "3"
        row[9] = "100"
        row[20] = "[0]"
        row[25] = f"icon_{index + 1}"
        row[34] = "Cube"
        row[39] = "[501,601]" if index < associated_rows else "[101]"
        row[40] = ""
        rows.append(row)
    return rows


def _make_official_20260903_rows(
    *,
    associated_rows: int = 676,
    zero_tail_rows: int = 62,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for index in range(item_table.ITEM_TABLE_OFFICIAL_20260903_ROWS):
        row = ["0"] * item_table.ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903
        row[0] = str(index + 1)
        row[1] = f"item-{index + 1}"
        row[2] = "desc"
        row[3] = f"itemName_{index + 1}"
        row[5] = f"itemDesc_{index + 1}"
        row[6] = "[101]"
        row[7] = "23"
        row[8] = "6"
        row[9] = "100"
        row[20] = "[0]"
        row[25] = f"icon_{index + 1}"
        row[34] = "Cube"
        row[39] = "[501,601]" if index < associated_rows else "[101]"
        row[40] = ""
        row[41] = "0" if index < zero_tail_rows else "1"
        rows.append(row)
    for row, (item_id, (value, shape)) in zip(
        rows[-len(item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES) :],
        item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES.items(),
    ):
        row[0] = str(item_id)
        row[7] = str(shape[0] * 10 + shape[1])
        row[9] = str(value)
    return rows


def _approve_official_20260903_rows(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )


def _approve_official_20260903_hotfix_rows(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )


def _approve_update606_rows(
    rows: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_UPDATE606_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )


def _write_synthetic_update606_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, list[dict[str, object]]]:
    rows = _make_update606_rows()
    _approve_update606_rows(rows, monkeypatch)
    parsed = parse_item_table(rows)
    catalog = [
        parsed[item_id].model_dump(exclude={"raw_row"})
        for item_id in sorted(parsed)
    ]
    catalog[0].update(
        {
            "name": "approved-display-name",
            "description": "rejected-legacy-description",
            "icon_name": "rejected-legacy-icon",
            # These semantic conflicts prove the overlay allowlist is narrow.
            "tags": [105],
            "allowed_shelves": [999],
            "quality": 6,
            "quality_color": "red",
            "value": 999_999,
            "model_name": "WrongModel",
        }
    )
    raw_catalog = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    monkeypatch.setattr(item_table, "ITEM_CATALOG_UPDATE606_ROWS", len(catalog))
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_UPDATE606_CANONICAL_SHA256",
        hashlib.sha256(raw_catalog).hexdigest(),
    )
    item_path = tmp_path / "data" / "raw" / "tables" / "Item.txt"
    item_path.parent.mkdir(parents=True)
    item_path.write_bytes(
        base64.b64encode("\n".join("\t".join(row) for row in rows).encode("utf-8"))
    )
    catalog_path = tmp_path / "data" / "processed" / "items.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False),
        encoding="utf-8",
    )
    return item_path, catalog


def _write_synthetic_official_20260903_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, list[dict[str, object]]]:
    rows = _make_official_20260903_rows()
    _approve_official_20260903_rows(rows, monkeypatch)
    parsed = parse_item_table(rows)
    catalog = [
        parsed[item_id].model_dump(exclude={"raw_row"})
        for item_id in sorted(parsed)
    ]
    catalog[0].update(
        {
            "name": "approved-20260903-display-name",
            "description": "rejected-catalog-description",
            "icon_name": "rejected-catalog-icon",
            "tags": [105],
            "allowed_shelves": [999],
            "quality": 1,
            "quality_color": "white",
            "value": 999_999,
            "shape_w": 6,
            "shape_h": 7,
            "model_name": "WrongModel",
        }
    )
    raw_catalog = json.dumps(
        catalog,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_OFFICIAL_20260903_ROWS",
        len(catalog),
    )
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_OFFICIAL_20260903_BYTES",
        len(raw_catalog),
    )
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_OFFICIAL_20260903_SHA256",
        hashlib.sha256(raw_catalog).hexdigest(),
    )
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256",
        item_table._canonical_json_sha256(catalog),  # noqa: SLF001
    )
    item_path = tmp_path / "data" / "raw" / "tables" / "Item.txt"
    item_path.parent.mkdir(parents=True)
    item_path.write_bytes(
        base64.b64encode("\n".join("\t".join(row) for row in rows).encode("utf-8"))
    )
    catalog_path = tmp_path / "data" / "processed" / "items.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_bytes(raw_catalog)
    return item_path, catalog


def test_parse_item_row_basic() -> None:
    item = parse_item_row(_make_row())
    assert item.item_id == 1
    assert item.name == "测试物品"
    assert item.description == "一个测试物品"
    assert item.quality == 3
    assert item.quality_color == "blue"
    assert item.value == 1500
    assert item.tags == [1]
    assert item.allowed_shelves == [101, 102]
    assert item.icon_name == "icon_test"
    assert item.model_name == "Cube"
    assert len(item.raw_row) == ITEM_TABLE_COLUMN_COUNT


def test_parse_item_row_2026_07_23_layout() -> None:
    item = parse_item_row(
        _make_40_column_row(
            col0="1016011",
            col1="航海罗盘",
            col7="22",
            col8="6",
            col9="3615569",
            col20="[0]",
            col25="icon_1016011",
            col34="Cube",
        )
    )

    assert item.item_id == 1016011
    assert item.name == "航海罗盘"
    assert (item.shape_w, item.shape_h) == (2, 2)
    assert item.quality == 6
    assert item.value == 3_615_569
    assert item.allowed_shelves == [0]
    assert item.icon_name == "icon_1016011"
    assert item.model_name == "Cube"
    assert len(item.raw_row) == 40


def test_quality_color_mapping() -> None:
    colors = {0: "none", 1: "white", 2: "green", 3: "blue", 4: "purple", 5: "gold", 6: "red"}
    for q, expected in colors.items():
        item = parse_item_row(_make_row(**{"col8": str(q)}))
        assert item.quality == q
        assert item.quality_color == expected


def test_rejects_wrong_column_count() -> None:
    assert ITEM_TABLE_COLUMN_COUNTS == (38, 40)
    with pytest.raises(ValueError, match=r"must have one of \(38, 40\) columns"):
        parse_item_row(["1", "x"])


def test_update606_exact_1336x41_table_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows()
    _approve_update606_rows(rows, monkeypatch)

    parsed = parse_item_table(rows)

    assert len(parsed) == item_table.ITEM_TABLE_UPDATE606_ROWS
    assert parsed[1].allowed_shelves == [0]
    assert parsed[1].icon_name == "icon_1"
    assert parsed[1].model_name == "Cube"
    assert len(parsed[1].raw_row) == 41


def test_standalone_or_unknown_41_column_row_is_never_position_guessed() -> None:
    row = _make_update606_rows()[0]
    with pytest.raises(ValueError, match=r"one of \(38, 40\) columns"):
        parse_item_row(row)
    with pytest.raises(ValueError, match="exact approved generation"):
        parse_item_table(_make_update606_rows())


def test_0_3_4_exact_1336x41_table_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows(
        associated_rows=item_table.ITEM_TABLE_0_3_4_ASSOCIATED_501_601_ROWS
    )
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_0_3_4_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )

    parsed = parse_item_table(rows)

    assert len(parsed) == item_table.ITEM_TABLE_0_3_4_ROWS
    assert sum(item.raw_row[39] == "[501,601]" for item in parsed.values()) == 639


def test_0_3_4_wrong_501_601_association_count_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows(associated_rows=638)
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_0_3_4_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )

    with pytest.raises(ValueError, match="association count mismatch"):
        parse_item_table(rows)


def test_official_20260903_exact_1427x42_table_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_official_20260903_rows()
    _approve_official_20260903_rows(rows, monkeypatch)

    parsed = parse_item_table(rows)

    assert len(parsed) == item_table.ITEM_TABLE_OFFICIAL_20260903_ROWS
    assert len(parsed[1].raw_row) == 42
    assert parsed[1].allowed_shelves == [0]
    assert parsed[1].icon_name == "icon_1"
    assert parsed[1].model_name == "Cube"
    assert sum(item.raw_row[39] == "[501,601]" for item in parsed.values()) == 676
    assert sum(item.raw_row[41] == "0" for item in parsed.values()) == 62
    assert {
        item_id: (
            parsed[item_id].value,
            (parsed[item_id].shape_w, parsed[item_id].shape_h),
        )
        for item_id in item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES
    } == dict(item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES)
    assert sum(
        parsed[item_id].value
        for item_id in item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES
    ) == 3_177_600


def test_official_20260903_hotfix_profile_changes_only_unconsumed_raw_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_rows = _make_official_20260903_rows()
    hotfix_rows = [list(row) for row in historical_rows]
    hotfix_rows[0][29] = "[1002]"
    hotfix_rows[0][30] = "0.0872653475393201"
    hotfix_rows[1][30] = "0.0161339421613394"
    _approve_official_20260903_rows(historical_rows, monkeypatch)
    _approve_official_20260903_hotfix_rows(hotfix_rows, monkeypatch)

    historical = parse_item_table(historical_rows)
    hotfix = parse_item_table(hotfix_rows)

    assert set(historical) == set(hotfix)
    assert {
        item_id: item.model_dump(exclude={"raw_row"})
        for item_id, item in historical.items()
    } == {
        item_id: item.model_dump(exclude={"raw_row"})
        for item_id, item in hotfix.items()
    }
    assert {
        column
        for before, after in zip(historical_rows, hotfix_rows, strict=True)
        for column, (old, new) in enumerate(zip(before, after, strict=True))
        if old != new
    } == {29, 30}


def test_official_20260903_unknown_42_column_identity_fails_closed() -> None:
    assert (
        item_table.ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256
        != item_table.ITEM_TABLE_0_3_4_CANONICAL_SHA256
    )
    assert len(
        {
            item_table.ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256,
            item_table.ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
            item_table.ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_SHA256,
        }
    ) == 3
    assert (
        item_table.ITEM_TABLE_OFFICIAL_20260903_CANONICAL_BYTES,
        item_table.ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES,
        item_table.ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_BYTES,
    ) == (517_435, 518_864, 689_916)
    assert (
        item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES,
        item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
        item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES,
    ) == (518_000, 519_429, 690_668)
    assert len(
        {
            item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
            item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256,
            item_table.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_SHA256,
        }
    ) == 3
    with pytest.raises(ValueError, match="exact approved generation"):
        parse_item_table(_make_official_20260903_rows())


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("partial-association", "mixed 501/601 association"),
        ("association-count", "association count mismatch"),
        ("tail-domain", "tail field is outside exact domain"),
        ("tail-count", "tail field count mismatch"),
        ("item-canary", "quality/value/shape mismatch"),
    ),
)
def test_official_20260903_semantic_canary_tamper_fails_closed(
    mutation: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_official_20260903_rows()
    if mutation == "partial-association":
        rows[0][39] = "[501]"
    elif mutation == "association-count":
        rows[675][39] = "[101]"
    elif mutation == "tail-domain":
        rows[0][41] = "2"
    elif mutation == "item-canary":
        canary_id = next(iter(item_table.ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES))
        next(row for row in rows if row[0] == str(canary_id))[9] = "0"
    else:
        rows[61][41] = "1"
    _approve_official_20260903_rows(rows, monkeypatch)

    with pytest.raises(ValueError, match=expected):
        parse_item_table(rows)


def test_official_20260903_bom_is_not_part_of_canonical_row_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_official_20260903_rows()
    _approve_official_20260903_rows(rows, monkeypatch)
    rows[0][0] = "\ufeff" + rows[0][0]

    with pytest.raises(ValueError, match="unrecognized table sha256"):
        parse_item_table(rows)


def test_official_20260903_exact_catalog_overlays_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_path, _catalog = _write_synthetic_official_20260903_package(
        tmp_path,
        monkeypatch,
    )

    items = load_item_table(item_path)

    assert len(items) == item_table.ITEM_TABLE_OFFICIAL_20260903_ROWS
    assert items[1].name == "approved-20260903-display-name"
    assert items[1].description == "desc"
    assert items[1].icon_name == "icon_1"
    assert items[1].tags == [101]
    assert items[1].allowed_shelves == [0]
    assert items[1].quality == 6
    assert items[1].quality_color == "red"
    assert items[1].value == 100
    assert (items[1].shape_w, items[1].shape_h) == (2, 3)
    assert items[1].model_name == "Cube"


@pytest.mark.parametrize(
    "mutation",
    ("missing", "stale-1336", "rows", "format-only", "canonical-tamper"),
)
def test_official_20260903_catalog_identity_fail_closed(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_path, catalog = _write_synthetic_official_20260903_package(
        tmp_path,
        monkeypatch,
    )
    catalog_path = tmp_path / "data" / "processed" / "items.json"
    if mutation == "missing":
        catalog_path.unlink()
    elif mutation == "stale-1336":
        catalog_path.write_text(
            json.dumps(catalog[:1336], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif mutation == "rows":
        shortened = catalog[:-1]
        raw = json.dumps(
            shortened,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ).encode("utf-8")
        catalog_path.write_bytes(raw)
        monkeypatch.setattr(
            item_table,
            "ITEM_CATALOG_OFFICIAL_20260903_BYTES",
            len(raw),
        )
        monkeypatch.setattr(
            item_table,
            "ITEM_CATALOG_OFFICIAL_20260903_SHA256",
            hashlib.sha256(raw).hexdigest(),
        )
        monkeypatch.setattr(
            item_table,
            "ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256",
            item_table._canonical_json_sha256(shortened),  # noqa: SLF001
        )
    elif mutation == "format-only":
        catalog_path.write_text(
            json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    else:
        catalog[0]["name"] = "tampered-name"
        raw = json.dumps(
            catalog,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ).encode("utf-8")
        catalog_path.write_bytes(raw)
        monkeypatch.setattr(
            item_table,
            "ITEM_CATALOG_OFFICIAL_20260903_BYTES",
            len(raw),
        )
        monkeypatch.setattr(
            item_table,
            "ITEM_CATALOG_OFFICIAL_20260903_SHA256",
            hashlib.sha256(raw).hexdigest(),
        )

    with pytest.raises(
        ValueError,
        match="catalog is missing|catalog file identity mismatch|catalog identity mismatch",
    ):
        load_item_table(item_path)


def test_0_3_4_runtime_catalog_requires_same_generation_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows(
        associated_rows=item_table.ITEM_TABLE_0_3_4_ASSOCIATED_501_601_ROWS
    )
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_0_3_4_CANONICAL_SHA256",
        item_table._canonical_table_sha256(rows),  # noqa: SLF001
    )
    parsed = parse_item_table(rows)
    catalog = [
        parsed[item_id].model_dump(exclude={"raw_row"})
        for item_id in sorted(parsed)
    ]
    catalog[0]["name"] = "0.3.4-display-name"
    monkeypatch.setattr(item_table, "ITEM_CATALOG_0_3_4_ROWS", len(catalog))
    monkeypatch.setattr(
        item_table,
        "ITEM_CATALOG_0_3_4_CANONICAL_SHA256",
        item_table._canonical_json_sha256(catalog),  # noqa: SLF001
    )
    item_path = tmp_path / "data" / "raw" / "tables" / "Item.txt"
    item_path.parent.mkdir(parents=True)
    item_path.write_bytes(
        base64.b64encode("\n".join("\t".join(row) for row in rows).encode("utf-8"))
    )
    catalog_path = tmp_path / "data" / "processed" / "items.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    items = load_item_table(item_path)

    assert items[1].name == "0.3.4-display-name"
    assert items[1].quality == 3


@pytest.mark.parametrize("mutation", ("missing-row", "wrong-columns", "mixed-width"))
def test_update606_shape_and_mixed_generation_fail_closed(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows()
    if mutation == "missing-row":
        rows.pop()
    elif mutation == "wrong-columns":
        rows[0].pop()
    else:
        rows[0] = _make_40_column_row(col0="1")
    _approve_update606_rows(rows, monkeypatch)

    with pytest.raises(ValueError, match="mixed|exactly 1336 rows|41 columns"):
        parse_item_table(rows)


def test_update606_501_601_association_must_stay_paired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_update606_rows()
    rows[0][39] = "[501]"
    _approve_update606_rows(rows, monkeypatch)

    with pytest.raises(ValueError, match="mixed 501/601 association"):
        parse_item_table(rows)


def test_update606_runtime_catalog_overlays_display_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_path, _ = _write_synthetic_update606_package(tmp_path, monkeypatch)

    items = load_item_table(item_path)

    assert items[1].name == "approved-display-name"
    assert items[1].description == "desc"
    assert items[1].icon_name == "icon_1"
    assert items[1].tags == [101]
    assert items[1].allowed_shelves == [0]
    assert items[1].quality == 3
    assert items[1].value == 100
    assert items[1].model_name == "Cube"
    assert 9_999 not in items


@pytest.mark.parametrize("mutation", ("missing", "wrong"))
def test_update606_runtime_catalog_missing_or_wrong_fails_closed(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_path, catalog = _write_synthetic_update606_package(tmp_path, monkeypatch)
    catalog_path = tmp_path / "data" / "processed" / "items.json"
    if mutation == "missing":
        catalog_path.unlink()
    else:
        catalog[0]["name"] = "unapproved-display"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="catalog is missing|catalog identity mismatch"):
        load_item_table(item_path)


def test_rejects_out_of_range_quality() -> None:
    with pytest.raises(Exception):
        parse_item_row(_make_row(**{"col8": "9"}))


def test_parse_item_table_dedups() -> None:
    rows = [_make_row(), _make_row(**{"col0": "1"})]
    with pytest.raises(ValueError, match="duplicate item_id"):
        parse_item_table(rows)


def test_parse_item_table_round_trip() -> None:
    r1 = _make_row(**{"col0": "1"})
    r2 = _make_row(**{"col0": "2", "col8": "6", "col9": "19000000"})
    out = parse_item_table([r1, r2])
    assert set(out.keys()) == {1, 2}
    assert out[2].quality_color == "red"
    assert out[2].value == 19_000_000


def test_official_update_collectibles_are_available_to_runtime_catalog() -> None:
    items = load_item_table(Path("data/raw/tables/Item.txt"))
    expected = {
        1006009: ("龙蛋", 1_233_333, 2, 3),
        1016008: ("天空之镜", 1_314_520, 1, 1),
        1016009: ("假日酒店房卡", 888_888, 1, 1),
        1016010: ("海螺留声机", 633_633, 3, 2),
        1016011: ("航海罗盘", 3_615_569, 2, 2),
        1016012: ("珊瑚礁摆件", 3_582_000, 2, 2),
        1016013: ("海王三叉戟", 1_999_999, 2, 3),
        1016014: ("广东人字拖", 888_888, 2, 2),
        1096011: ("“保熟” 西瓜", 713_400, 2, 2),
    }

    assert {
        item_id: (items[item_id].name, items[item_id].value, items[item_id].shape_w, items[item_id].shape_h)
        for item_id in expected
    } == expected
    assert all(items[item_id].quality == 6 for item_id in expected)
