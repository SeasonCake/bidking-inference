from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json

import pytest

from bidking_lab.extract import update606_package_input_contract as contract
from bidking_lab.extract import item_table, update606_release_contract


def _encoded(rows: list[list[str]]) -> tuple[bytes, dict[str, object]]:
    decoded = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    payload = base64.b64encode(decoded)
    return payload, {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
        "rows": len(rows),
        "columns": len(rows[0]),
    }


def _row(map_id: int, *, columns: int, route: str) -> list[str]:
    row = ["0"] * columns
    row[0] = str(map_id)
    row[9] = "[[]]"
    row[17] = route
    return row


def _catalog_identity(values: tuple[int, ...]) -> dict[str, object]:
    return {
        "rows": len(values),
        "ids_sha256": contract._ids_sha256(values),  # noqa: SLF001
    }


def _synthetic_item_catalog_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    rows: list[list[str]] = []
    for index in range(item_table.ITEM_TABLE_UPDATE606_ROWS):
        item_id = index + 1
        row = ["0"] * 41
        row[0] = str(item_id)
        row[1] = f"official-name-{item_id}"
        row[2] = f"official-description-{item_id}"
        row[3] = f"itemName_{item_id}"
        row[5] = f"itemDesc_{item_id}"
        row[6] = "[101]"
        row[7] = "11"
        row[8] = "3"
        row[9] = "100"
        row[20] = "[0]"
        row[25] = f"icon_{item_id}"
        row[34] = "Cube"
        row[39] = "[501,601]" if index < 666 else "[101]"
        row[40] = ""
        rows.append(row)
    text = "\r\n".join("\t".join(row) for row in rows)
    item_raw = text.encode("utf-8")
    identity = replace(
        update606_release_contract.UPDATE606_TEXT_ASSET_IDENTITIES["Item"],
        utf8_size=len(item_raw),
        utf8_sha256=hashlib.sha256(item_raw).hexdigest(),
    )
    monkeypatch.setitem(contract.UPDATE606_TEXT_ASSET_IDENTITIES, "Item", identity)
    monkeypatch.setattr(
        item_table,
        "ITEM_TABLE_UPDATE606_CANONICAL_SHA256",
        hashlib.sha256(
            "\n".join("\t".join(row) for row in rows).encode("utf-8")
        ).hexdigest(),
    )
    parsed = item_table.parse_item_table(rows)
    legacy = [
        parsed[item_id].model_dump(exclude={"raw_row"})
        for item_id in sorted(parsed)
    ]
    legacy[0].update(
        {
            "name": "approved-display",
            "description": "approved-description",
            "icon_name": "approved-icon",
            "tags": [105],
            "quality": 6,
            "quality_color": "red",
            "value": 999,
            "_src": "approved-source",
        }
    )
    legacy.append(
        {
            "item_id": 9999,
            "name": "historical-only",
            "description": "history",
            "name_key": "itemName_9999",
            "desc_key": "itemDesc_9999",
            "quality": 1,
            "quality_color": "white",
            "value": 1,
            "shape_w": 1,
            "shape_h": 1,
            "tags": [],
            "allowed_shelves": [],
            "icon_name": "icon_9999",
            "model_name": "",
        }
    )
    legacy_raw = json.dumps(legacy, ensure_ascii=False, indent=2).encode("utf-8")
    monkeypatch.setattr(
        contract,
        "LEGACY_ITEMS_CATALOG",
        {
            "bytes": len(legacy_raw),
            "canonical_sha256": hashlib.sha256(
                contract._canonical_json_bytes(legacy)  # noqa: SLF001
            ).hexdigest(),
            "rows": len(legacy),
            "sha256": hashlib.sha256(legacy_raw).hexdigest(),
        },
    )
    overlap = tuple(range(1, item_table.ITEM_TABLE_UPDATE606_ROWS + 1))
    official_only: tuple[int, ...] = ()
    historical_only = (9999,)
    monkeypatch.setattr(contract, "UPDATE606_CATALOG_OVERLAP", _catalog_identity(overlap))
    monkeypatch.setattr(
        contract,
        "UPDATE606_CATALOG_OFFICIAL_ONLY",
        _catalog_identity(official_only),
    )
    monkeypatch.setattr(
        contract,
        "UPDATE606_CATALOG_HISTORICAL_ONLY",
        _catalog_identity(historical_only),
    )
    field_ids = {
        "name": (1,),
        "description": (1,),
        "icon_name": (1,),
        "_src": (1,),
        "_name_src": (),
    }
    monkeypatch.setattr(
        contract,
        "UPDATE606_CATALOG_FIELD_DIFFS",
        {field: _catalog_identity(ids) for field, ids in field_ids.items()},
    )
    expected: list[dict[str, object]] = []
    for item_id in sorted(parsed):
        row = parsed[item_id].model_dump(exclude={"raw_row"})
        if item_id == 1:
            row["name"] = "approved-display"
        expected.append(row)
    rendered = json.dumps(
        expected,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    monkeypatch.setattr(
        contract,
        "UPDATE606_ITEMS_CATALOG",
        {
            "bytes": len(rendered),
            "canonical_sha256": hashlib.sha256(
                contract._canonical_json_bytes(expected)  # noqa: SLF001
            ).hexdigest(),
            "rows": len(expected),
            "sha256": hashlib.sha256(rendered).hexdigest(),
        },
    )
    return item_raw, legacy_raw


def _synthetic_scope(monkeypatch: pytest.MonkeyPatch) -> contract.LegacyDistributionScope:
    legacy_rows = [
        _row(1, columns=23, route="[9999,1,1,1]"),
        _row(2, columns=23, route="[9999,2,1,1]"),
        _row(3, columns=23, route="[9999,3,1,1]"),
        _row(5506, columns=23, route="[9999,2506,1,1]"),
    ]
    update_rows = [
        _row(1, columns=28, route="[9999,1,1,1]"),
        _row(2, columns=28, route="[9999,20,1,1]"),
        _row(5506, columns=28, route="[9999,5506,1,1]"),
        _row(6001, columns=28, route="[9999,5506,1,1]"),
    ]
    legacy, legacy_identity = _encoded(legacy_rows)
    update, update_identity = _encoded(update_rows)
    drop_decoded = b"801\tlegacy-drop"
    drop = base64.b64encode(drop_decoded)
    monkeypatch.setattr(contract, "LEGACY_PACKAGE_BID_MAP", legacy_identity)
    monkeypatch.setattr(contract, "UPDATE606_PACKAGE_BID_MAP", update_identity)
    monkeypatch.setattr(
        contract,
        "LEGACY_PACKAGE_DROP",
        {
            "bytes": len(drop),
            "sha256": hashlib.sha256(drop).hexdigest(),
            "decoded_sha256": hashlib.sha256(drop_decoded).hexdigest(),
        },
    )
    allowed = (1,)
    new_only = (6001,)
    changed = (2, 5506)
    legacy_only = (3,)
    monkeypatch.setattr(contract, "LEGACY_ALLOWED_MAP_IDS_SHA256", contract._ids_sha256(allowed))  # noqa: SLF001
    monkeypatch.setattr(contract, "LEGACY_NEW_ONLY_MAP_IDS_SHA256", contract._ids_sha256(new_only))  # noqa: SLF001
    monkeypatch.setattr(contract, "LEGACY_CHANGED_ROUTE_MAP_IDS_SHA256", contract._ids_sha256(changed))  # noqa: SLF001
    monkeypatch.setattr(contract, "LEGACY_ONLY_MAP_IDS_SHA256", contract._ids_sha256(legacy_only))  # noqa: SLF001

    original = contract.derive_legacy_distribution_scope

    def derived_for_synthetic(**kwargs: bytes) -> contract.LegacyDistributionScope:
        # The production count canaries remain strict.  The unit fixture tests
        # route semantics with the same algorithm and then constructs the
        # equivalent receipt-sized scope.
        del kwargs
        return contract.LegacyDistributionScope(
            package_generation=contract.BID_MAP_UPDATE606_GENERATION,
            legacy_baseline_id=contract.LEGACY_PACKAGE_BASELINE_ID,
            policy=contract.LEGACY_DISTRIBUTION_POLICY,
            allowed_map_ids=allowed,
            new_only_map_ids=new_only,
            changed_route_map_ids=changed,
            legacy_only_map_ids=legacy_only,
        )

    # First independently exercise exact table decoding and route projection.
    decoded_legacy = contract._decode_exact_package_table(  # noqa: SLF001
        legacy,
        label="legacy BidMap",
        identity=legacy_identity,
    )
    decoded_update = contract._decode_exact_package_table(  # noqa: SLF001
        update,
        label="update606 BidMap",
        identity=update_identity,
    )
    old = {int(row[0]): row for row in decoded_legacy}
    new = {int(row[0]): row for row in decoded_update}
    assert tuple(
        sorted(
            map_id
            for map_id in set(old) & set(new)
            if map_id not in {5506, 6001}
            and contract._route_identity(old[map_id]) == contract._route_identity(new[map_id])  # noqa: SLF001
        )
    ) == allowed
    assert tuple(sorted(set(old) - set(new))) == legacy_only
    assert original is contract.derive_legacy_distribution_scope
    monkeypatch.setattr(contract, "derive_legacy_distribution_scope", derived_for_synthetic)
    return derived_for_synthetic()


def test_legacy_scope_hard_excludes_new_changed_5506_6001(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _synthetic_scope(monkeypatch)

    assert scope.allowed_map_ids == (1,)
    assert scope.new_only_map_ids == (6001,)
    assert scope.changed_route_map_ids == (2, 5506)
    assert scope.legacy_only_map_ids == (3,)
    assert 5506 not in scope.allowed_map_ids
    assert 6001 not in scope.allowed_map_ids
    assert scope.policy == contract.LEGACY_DISTRIBUTION_POLICY


def test_map_payload_filter_never_carries_denied_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _synthetic_scope(monkeypatch)
    source = json.dumps(
        {
            "1": {"value": 1},
            "2": {"value": 2},
            "3": {"value": 3},
            "5506": {},
            "6001": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    expected = json.dumps({"1": {"value": 1}}, ensure_ascii=False).encode("utf-8")
    monkeypatch.setitem(
        contract.LEGACY_MAP_PAYLOADS,
        "synthetic",
        {
            "source_bytes": len(source),
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "container": None,
            "render": "default",
            "output_maps": 1,
            "output_bytes": len(expected),
            "output_sha256": hashlib.sha256(expected).hexdigest(),
        },
    )

    rendered, receipt = contract.carry_forward_legacy_map_payload(
        "synthetic",
        source,
        scope=scope,
    )

    assert rendered == expected
    assert set(json.loads(rendered)) == {"1"}
    assert receipt["output"]["maps"] == 1


def test_map_payload_wrong_or_missing_generation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _synthetic_scope(monkeypatch)
    monkeypatch.setitem(
        contract.LEGACY_MAP_PAYLOADS,
        "synthetic",
        {
            "source_bytes": 1,
            "source_sha256": hashlib.sha256(b"x").hexdigest(),
            "container": None,
            "render": "default",
            "output_maps": 0,
            "output_bytes": 2,
            "output_sha256": hashlib.sha256(b"{}").hexdigest(),
        },
    )
    with pytest.raises(contract.Update606PackageInputError, match="source identity"):
        contract.carry_forward_legacy_map_payload("synthetic", b"y", scope=scope)


def test_historical_only_item_cannot_enter_drop_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _synthetic_scope(monkeypatch)
    source = json.dumps(
        {"1": {"items": {"9999": [1.0, 1, 1]}}},
        ensure_ascii=False,
    ).encode("utf-8")
    monkeypatch.setattr(contract, "UPDATE606_HISTORICAL_ONLY_ITEM_IDS", (9999,))
    monkeypatch.setitem(
        contract.LEGACY_MAP_PAYLOADS,
        "drop_weights_all_maps_0627",
        {
            "source_bytes": len(source),
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "container": None,
            "render": "default",
            "output_maps": 1,
            "output_bytes": len(source),
            "output_sha256": hashlib.sha256(source).hexdigest(),
        },
    )

    with pytest.raises(contract.Update606PackageInputError, match="historical-only"):
        contract.carry_forward_legacy_map_payload(
            "drop_weights_all_maps_0627",
            source,
            scope=scope,
        )


def test_package_table_rejects_unknown_or_wrong_official_bytes() -> None:
    with pytest.raises(contract.Update606PackageInputError, match="unsupported"):
        contract.build_update606_package_table("Drop", b"not-authoritative")
    with pytest.raises(contract.Update606PackageInputError, match="identity mismatch"):
        contract.build_update606_package_table("Item", b"wrong-generation")


def test_items_catalog_carries_display_only_and_marks_id_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_raw, legacy_raw = _synthetic_item_catalog_inputs(monkeypatch)

    rendered, receipt = contract.build_update606_items_catalog(item_raw, legacy_raw)
    catalog = {row["item_id"]: row for row in json.loads(rendered)}

    assert catalog[1]["name"] == "approved-display"
    assert catalog[1]["description"] == "official-description-1"
    assert catalog[1]["icon_name"] == "icon_1"
    assert "_src" not in catalog[1]
    assert catalog[1]["tags"] == [101]
    assert catalog[1]["quality"] == 3
    assert catalog[1]["value"] == 100
    assert 9999 not in catalog
    assert receipt["overlay_contract"]["official_only"]["marker"] == (
        "official_update606_only"
    )
    assert receipt["overlay_contract"]["historical_only"] == {
        "ids": [9999],
        "ids_sha256": contract._ids_sha256((9999,)),  # noqa: SLF001
        "rows": 1,
        "authority": False,
        "catalog_included": False,
        "display_fallback": "unknown_not_run",
        "droppable": False,
        "pool": False,
        "quote": False,
        "weights": False,
    }


def test_items_catalog_wrong_legacy_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_raw, legacy_raw = _synthetic_item_catalog_inputs(monkeypatch)

    with pytest.raises(contract.Update606PackageInputError, match="legacy.*identity"):
        contract.build_update606_items_catalog(item_raw, legacy_raw + b"\n")
