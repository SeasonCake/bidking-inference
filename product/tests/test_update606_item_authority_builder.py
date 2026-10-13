from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from bidking_lab.extract import item_table, update606_release_contract


def _load_builder() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "calibration"
        / "build_item_category_authority.py"
    )
    spec = importlib.util.spec_from_file_location("update606_authority_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    builder = _load_builder()
    rows: list[list[str]] = []
    catalog: list[dict[str, object]] = []
    for index in range(item_table.ITEM_TABLE_UPDATE606_ROWS):
        item_id = index + 1
        row = ["0"] * 41
        row[0] = str(item_id)
        row[1] = f"item-{item_id}"
        row[2] = "desc"
        row[3] = f"itemName_{item_id}"
        row[5] = f"itemDesc_{item_id}"
        row[6] = "[101,106]" if index == 0 else "[101]"
        row[7] = "11"
        row[8] = "3"
        row[9] = "100"
        row[20] = "[0]"
        row[25] = f"icon_{item_id}"
        row[34] = "Cube"
        row[39] = "[501,601]" if index < 666 else "[101]"
        row[40] = ""
        rows.append(row)
        catalog.append(
            {
                "item_id": item_id,
                "name": row[1],
                "description": row[2],
                "name_key": row[3],
                "desc_key": row[5],
                "quality": 3,
                "quality_color": "blue",
                "value": 100,
                "shape_w": 1,
                "shape_h": 1,
                "tags": [101, 106] if index == 0 else [101],
                "allowed_shelves": [0],
                "icon_name": row[25],
                "model_name": "Cube",
            }
        )
    text = "\ufeff" + "\r\n".join("\t".join(row) for row in rows)
    raw = text.encode("utf-8")
    identity = replace(
        update606_release_contract.UPDATE606_TEXT_ASSET_IDENTITIES["Item"],
        utf8_size=len(raw),
        utf8_sha256=hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setitem(
        update606_release_contract.UPDATE606_TEXT_ASSET_IDENTITIES,
        "Item",
        identity,
    )
    monkeypatch.setitem(builder.UPDATE606_TEXT_ASSET_IDENTITIES, "Item", identity)
    canonical = hashlib.sha256(
        "\n".join("\t".join(row) for row in rows).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(item_table, "ITEM_TABLE_UPDATE606_CANONICAL_SHA256", canonical)
    monkeypatch.setattr(builder, "ITEM_TABLE_UPDATE606_CANONICAL_SHA256", canonical)

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    paths = {
        "item": inputs / "Item.txt",
        "items": inputs / "items.json",
        "legacy": inputs / "legacy.json",
        "droppable": inputs / "droppable.json",
    }
    paths["item"].write_bytes(raw)
    paths["items"].write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    paths["legacy"].write_text(json.dumps({"1": 101}), encoding="utf-8")
    paths["droppable"].write_text(
        json.dumps(catalog[:10], ensure_ascii=False),
        encoding="utf-8",
    )
    canonical_values = {
        "UPDATE606_ITEMS_CANONICAL_ROWS": len(catalog),
        "UPDATE606_ITEMS_CANONICAL_SHA256": hashlib.sha256(
            builder.canonical_json_bytes(catalog)
        ).hexdigest(),
        "LEGACY_CATEGORY_CANONICAL_ROWS": 1,
        "LEGACY_CATEGORY_CANONICAL_SHA256": hashlib.sha256(
            builder.canonical_json_bytes({"1": 101})
        ).hexdigest(),
        "LEGACY_DROPPABLE_CANONICAL_ROWS": 10,
        "LEGACY_DROPPABLE_CANONICAL_SHA256": hashlib.sha256(
            builder.canonical_json_bytes(catalog[:10])
        ).hexdigest(),
    }
    for name, value in canonical_values.items():
        monkeypatch.setattr(builder, name, value)
    monkeypatch.setattr(builder, "UPDATE606_HISTORICAL_ONLY_IDS", ())
    monkeypatch.setattr(
        builder,
        "UPDATE606_HISTORICAL_ONLY_IDS_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(())).hexdigest(),
    )
    monkeypatch.setattr(builder, "UPDATE606_OFFICIAL_ONLY_IDS", ())
    monkeypatch.setattr(
        builder,
        "UPDATE606_OFFICIAL_ONLY_IDS_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(())).hexdigest(),
    )
    monkeypatch.setattr(builder, "LEGACY_POSITIVE_NONPRIMARY_IDS", (1,))
    reconciliation = [
        {
            "item_id": 1,
            "legacy_positive_category": 101,
            "official_category_ids": [101, 106],
            "official_primary_category": 106,
        }
    ]
    monkeypatch.setattr(
        builder,
        "LEGACY_POSITIVE_NONPRIMARY_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(reconciliation)).hexdigest(),
    )
    paths["builder"] = builder  # type: ignore[assignment]
    return paths


def test_update606_authority_and_receipt_bind_all_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_inputs(tmp_path, monkeypatch)
    builder = values["builder"]
    document = builder.build_authority_document(
        item_text_asset=values["item"],
        items_json=values["items"],
        legacy_category=values["legacy"],
        items_droppable=values["droppable"],
        package_generation=builder.UPDATE606_PACKAGE_GENERATION,
    )
    rendered = builder.render_authority(document)
    receipt = builder.build_receipt(document, rendered)

    assert len(document["records"]) == item_table.ITEM_TABLE_UPDATE606_ROWS
    assert sum(row["current_random_drop_eligible"] for row in document["records"]) == 10
    assert document["generation"].endswith(
        document["sources"]["item_table"]["sha256"]
    )
    assert receipt["output"]["sha256"] == hashlib.sha256(rendered).hexdigest()
    assert receipt["output"]["authority_identity"] == document["authority_identity"]
    assert all("ms_idMap" not in row for row in document["records"])
    assert document["records"][0]["category_ids"] == [101, 106]
    assert document["records"][0]["primary_category"] == 106
    assert receipt["legacy_category_contract"] == (
        "exact_positive_membership_hint_not_primary"
    )
    assert receipt["legacy_positive_nonprimary_ids"] == [1]
    assert receipt["historical_only"]["authority"] is False
    assert receipt["historical_only"]["droppable"] is False


def test_update606_authority_rejects_absent_legacy_positive_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_inputs(tmp_path, monkeypatch)
    builder = values["builder"]
    invalid = {"1": 105}
    values["legacy"].write_text(json.dumps(invalid), encoding="utf-8")
    monkeypatch.setattr(
        builder,
        "LEGACY_CATEGORY_CANONICAL_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(invalid)).hexdigest(),
    )
    with pytest.raises(builder.AuthorityBuildError, match="expected_membership=105"):
        builder.build_authority_document(
            item_text_asset=values["item"],
            items_json=values["items"],
            legacy_category=values["legacy"],
            items_droppable=values["droppable"],
            package_generation=builder.UPDATE606_PACKAGE_GENERATION,
        )


def test_update606_authority_rejects_wrong_generation_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_inputs(tmp_path, monkeypatch)
    builder = values["builder"]
    with pytest.raises(builder.AuthorityBuildError, match="generation"):
        builder.build_authority_document(
            item_text_asset=values["item"],
            items_json=values["items"],
            legacy_category=values["legacy"],
            items_droppable=values["droppable"],
            package_generation="89437c-2026-08-07-745",
        )


def test_update606_authority_rejects_wrong_item_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_inputs(tmp_path, monkeypatch)
    builder = values["builder"]
    values["item"].write_bytes(values["item"].read_bytes() + b"x")
    with pytest.raises(builder.AuthorityBuildError, match="identity mismatch"):
        builder.build_authority_document(
            item_text_asset=values["item"],
            items_json=values["items"],
            legacy_category=values["legacy"],
            items_droppable=values["droppable"],
            package_generation=builder.UPDATE606_PACKAGE_GENERATION,
        )


def test_update606_authority_rejects_historical_only_droppable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_inputs(tmp_path, monkeypatch)
    builder = values["builder"]
    catalog = json.loads(values["items"].read_text(encoding="utf-8"))
    historical = {
        **catalog[0],
        "item_id": 9999,
        "name_key": "itemName_9999",
        "desc_key": "itemDesc_9999",
    }
    catalog.append(historical)
    values["items"].write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    droppable = json.loads(values["droppable"].read_text(encoding="utf-8"))
    droppable.append(historical)
    values["droppable"].write_text(
        json.dumps(droppable, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(builder, "UPDATE606_ITEMS_CANONICAL_ROWS", len(catalog))
    monkeypatch.setattr(
        builder,
        "UPDATE606_ITEMS_CANONICAL_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(catalog)).hexdigest(),
    )
    monkeypatch.setattr(builder, "UPDATE606_HISTORICAL_ONLY_IDS", (9999,))
    monkeypatch.setattr(
        builder,
        "UPDATE606_HISTORICAL_ONLY_IDS_SHA256",
        hashlib.sha256(builder.canonical_json_bytes((9999,))).hexdigest(),
    )
    monkeypatch.setattr(builder, "LEGACY_DROPPABLE_CANONICAL_ROWS", len(droppable))
    monkeypatch.setattr(
        builder,
        "LEGACY_DROPPABLE_CANONICAL_SHA256",
        hashlib.sha256(builder.canonical_json_bytes(droppable)).hexdigest(),
    )

    with pytest.raises(builder.AuthorityBuildError, match="non-official Item identity"):
        builder.build_authority_document(
            item_text_asset=values["item"],
            items_json=values["items"],
            legacy_category=values["legacy"],
            items_droppable=values["droppable"],
            package_generation=builder.UPDATE606_PACKAGE_GENERATION,
        )
