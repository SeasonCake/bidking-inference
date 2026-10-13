#!/usr/bin/env python3
"""Build current Item category authority from exact frozen inputs.

The official Item TextAsset is accepted only under an exact approved package
profile.  ``ms_idMap`` associations are kept
as route metadata by the release contract; they never create random-drop
eligibility here.  Eligibility remains an explicit input and is checked
against Item/catalog identity before a deterministic authority and receipt are
written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.data.item_category_authority import (  # noqa: E402
    PRIMARY_CATEGORY_POLICY,
    SCHEMA_VERSION,
    TAXONOMY,
    TAXONOMY_IDS,
    authority_identity_payload,
    canonical_json_bytes,
    sha256_json,
    validate_authority_document,
)
from bidking_lab.extract.item_table import (  # noqa: E402
    ITEM_CATALOG_0_3_4_CANONICAL_SHA256,
    ITEM_CATALOG_0_3_4_ROWS,
    ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256,
    ITEM_CATALOG_OFFICIAL_20260903_ROWS,
    ITEM_CATALOG_OFFICIAL_20260910_CANONICAL_SHA256,
    ITEM_CATALOG_OFFICIAL_20260910_ROWS,
    ITEM_TABLE_0_3_4_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_GENERATION,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
    ITEM_TABLE_OFFICIAL_20260910_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES,
    ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_ROWS,
    ITEM_TABLE_UPDATE606_CANONICAL_SHA256,
    parse_item_table,
    ITEM_TABLE_OFFICIAL_20261008_GENERATION,
    ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_SHA256,
    ITEM_CATALOG_OFFICIAL_20261008_CANONICAL_SHA256,
)
from bidking_lab.extract.official_20261008_model_profile import (
    TEXT_ASSET_IDENTITIES as OCTOBER_TEXT_IDENTITIES,
    DROPPABLE_CANONICAL_SHA256 as OCTOBER_DROPPABLE_CANONICAL_SHA256,
    UNION_IDS_SHA256 as OCTOBER_UNION_IDS_SHA256,
)
from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_PACKAGE_GENERATION,
    CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
    CURRENT_MODEL_UNION_ITEM_ROWS,
    CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS,
    RETIRED_SUMMER_PARTY_2026_ITEM_IDS,
    CURRENT_TEXT_ASSET_IDENTITIES,
    PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION,
    PREVIOUS_0_3_4_PACKAGE_GENERATION,
)
from bidking_lab.extract.tables import assert_uniform_columns, iter_table_rows  # noqa: E402
from bidking_lab.extract.update606_release_contract import (  # noqa: E402
    UPDATE606_PACKAGE_GENERATION,
    UPDATE606_TEXT_ASSET_IDENTITIES,
    update606_text_asset_rows,
)

DEFAULT_OUTPUT = ROOT / "data" / "processed" / "item_category_authority_0_3_2.json"
UPDATE606_ITEMS_CANONICAL_ROWS = 1_336
UPDATE606_ITEMS_CANONICAL_SHA256 = (
    "18e4efa914cae2d19705d242e11730def4f50f288673820d98da03638f2972d2"
)
UPDATE606_HISTORICAL_ONLY_IDS = (
    241001,
    241002,
    241003,
    241004,
    241005,
    241006,
    251001,
    251002,
    1046010,
    1046011,
    1046012,
)
UPDATE606_HISTORICAL_ONLY_IDS_SHA256 = (
    "172a74dd8638d8764e8da1685104c1fb9114676bc7a9fce1415ed102521bcc00"
)
UPDATE606_OFFICIAL_ONLY_IDS = (
    11,
    1004,
    1014,
    1024,
    1034,
    1044,
    1054,
    1064,
    1074,
    1084,
    1094,
    1104,
    7203,
    7204,
    120010,
    120404,
    120504,
    122101,
    122102,
    132014,
    132024,
    132034,
    132044,
    132054,
    132064,
    132074,
    132084,
    132094,
    132104,
    132114,
    140016,
    140703,
    140704,
    150022,
    150023,
    150031,
    150302,
    190001,
    190002,
    200001,
    200002,
    1306017,
    1410403,
    1410502,
)
UPDATE606_OFFICIAL_ONLY_IDS_SHA256 = (
    "54a42719b7c502f21479ee47a5740f26941df0651e7ccc8ed86a9c1f0d481884"
)
LEGACY_CATEGORY_CANONICAL_ROWS = 684
LEGACY_CATEGORY_CANONICAL_SHA256 = (
    "92937ef4a04dbd3d9d274402ce40b9c5edfcce5a77e2dc2f52617554be697305"
)
LEGACY_DROPPABLE_CANONICAL_ROWS = 680
LEGACY_DROPPABLE_CANONICAL_SHA256 = (
    "f92df28fa00acd354f50dc89a6cd1a2a816ccc7abaca0c9ff9f4245a00fb71cc"
)
CURRENT_DROPPABLE_CANONICAL_ROWS = CURRENT_MODEL_UNION_ITEM_ROWS
CURRENT_DROPPABLE_CANONICAL_SHA256 = (
    "266fa23dafb3d5cf00eb2f5a01cb791e8d794c6a049dcf6d5b02f648ce439667"
)
LEGACY_CATEGORY_CONTRACT = "exact_positive_membership_hint_not_primary"
LEGACY_POSITIVE_NONPRIMARY_IDS = (
    1016008,
    1016010,
    1016011,
    1016012,
    1016013,
    1016014,
)
LEGACY_POSITIVE_NONPRIMARY_SHA256 = (
    "af8c22931e9f6bf4c23afb27fb01727cda318b24f81f18b7cf37361b38ee4de6"
)


class AuthorityBuildError(ValueError):
    """An input is not exact or conflicts with the update606 authority."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityBuildError(
            f"invalid JSON input {path.name}: {exc.__class__.__name__}"
        ) from exc
    return value, raw


def _catalog_by_id(value: Any, *, source_name: str) -> dict[int, dict[str, Any]]:
    rows = list(value.values()) if value.__class__ is dict else value
    if rows.__class__ is not list:
        raise AuthorityBuildError(f"{source_name} must be an exact list/object")
    out: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if row.__class__ is not dict:
            raise AuthorityBuildError(f"{source_name} row {index} must be an exact object")
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in out:
            raise AuthorityBuildError(f"{source_name} has invalid/duplicate item_id")
        out[item_id] = row
    return out


def _legacy_by_id(value: Any) -> dict[int, int]:
    if value.__class__ is not dict:
        raise AuthorityBuildError("legacy category input must be an exact object")
    out: dict[int, int] = {}
    for raw_item_id, category in value.items():
        if (
            raw_item_id.__class__ is not str
            or not raw_item_id.isascii()
            or not raw_item_id.isdigit()
            or category.__class__ is not int
            or category not in TAXONOMY_IDS
        ):
            raise AuthorityBuildError("legacy category input is malformed")
        out[int(raw_item_id)] = category
    return out


def _require_canonical_identity(
    value: Any,
    *,
    label: str,
    rows: int,
    sha256: str,
) -> None:
    if len(value) != rows or _sha256(canonical_json_bytes(value)) != sha256:
        raise AuthorityBuildError(f"{label} canonical identity mismatch")


def _identity_fields(row: Any) -> dict[str, Any]:
    return {
        "quality": row.quality,
        "value": row.value,
        "shape_w": row.shape_w,
        "shape_h": row.shape_h,
        "name_key": row.name_key,
        "desc_key": row.desc_key,
    }


def _validate_catalog_overlap(items: dict[int, Any], catalog: dict[int, dict[str, Any]]) -> None:
    for item_id, row in catalog.items():
        item = items.get(item_id)
        if item is None:
            continue
        for field, expected in _identity_fields(item).items():
            observed = row.get(field)
            if observed.__class__ is not expected.__class__ or observed != expected:
                raise AuthorityBuildError(
                    f"items.json identity conflict item_id={item_id} field={field}"
                )
        tags = row.get("tags")
        if tags.__class__ is not list or any(tag.__class__ is not int for tag in tags):
            raise AuthorityBuildError(f"items.json tags malformed item_id={item_id}")
        projected = [tag for tag in tags if tag in TAXONOMY_IDS]
        source_projected = [tag for tag in item.tags if tag in TAXONOMY_IDS]
        if projected != source_projected:
            raise AuthorityBuildError(f"items.json taxonomy conflict item_id={item_id}")


def _validate_droppable_overlap(
    items: dict[int, Any],
    catalog: dict[int, dict[str, Any]],
    droppable: dict[int, dict[str, Any]],
) -> None:
    for item_id, row in droppable.items():
        item = items.get(item_id)
        categories = [tag for tag in item.tags if tag in TAXONOMY_IDS] if item else []
        if item is None or not categories or item_id not in catalog:
            raise AuthorityBuildError(
                f"droppable item_id={item_id} lacks Item/catalog category authority"
            )
        for field, expected in _identity_fields(item).items():
            observed = row.get(field)
            if observed.__class__ is not expected.__class__ or observed != expected:
                raise AuthorityBuildError(
                    f"items_droppable identity conflict item_id={item_id} field={field}"
                )


def build_authority_document(
    *,
    item_text_asset: Path,
    items_json: Path,
    legacy_category: Path,
    items_droppable: Path,
    package_generation: str,
) -> dict[str, Any]:
    if package_generation not in {
        UPDATE606_PACKAGE_GENERATION,
        PREVIOUS_0_3_4_PACKAGE_GENERATION,
        PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION,
        ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION,
        CURRENT_PACKAGE_GENERATION,
        ITEM_TABLE_OFFICIAL_20261008_GENERATION,
    }:
        raise AuthorityBuildError("package generation is not an exact approved profile")
    item_raw = item_text_asset.read_bytes()
    try:
        item_text = item_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthorityBuildError("official Item TextAsset is not UTF-8") from exc
    try:
        if package_generation == UPDATE606_PACKAGE_GENERATION:
            item_rows = update606_text_asset_rows("Item", item_text)
            item_identity = UPDATE606_TEXT_ASSET_IDENTITIES["Item"]
            items_canonical_rows = UPDATE606_ITEMS_CANONICAL_ROWS
            items_canonical_sha256 = UPDATE606_ITEMS_CANONICAL_SHA256
        elif package_generation == PREVIOUS_0_3_4_PACKAGE_GENERATION:
            item_identity = SimpleNamespace(
                rows=1_336,
                columns=41,
                utf8_size=479_855,
                utf8_sha256=(
                    "7ac11867652b21ebbd62461cd95da84addfe721dea5c01307e4dd568fd60933c"
                ),
            )
            if (
                len(item_raw) != item_identity.utf8_size
                or _sha256(item_raw) != item_identity.utf8_sha256
            ):
                raise AuthorityBuildError("0.3.4 Item TextAsset identity mismatch")
            item_rows = list(iter_table_rows(item_text))
            if item_rows and item_rows[0][0].startswith("\ufeff"):
                item_rows[0][0] = item_rows[0][0][1:]
            if (
                len(item_rows) != item_identity.rows
                or assert_uniform_columns(item_rows) != item_identity.columns
            ):
                raise AuthorityBuildError("0.3.4 Item row/column identity mismatch")
            items_canonical_rows = ITEM_CATALOG_0_3_4_ROWS
            items_canonical_sha256 = ITEM_CATALOG_0_3_4_CANONICAL_SHA256
        elif package_generation == PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION:
            if package_generation != ITEM_TABLE_OFFICIAL_20260903_GENERATION:
                raise AuthorityBuildError(
                    "historical September Item generation constant mismatch"
                )
            item_identity = SimpleNamespace(
                rows=ITEM_TABLE_OFFICIAL_20260903_ROWS,
                columns=42,
                utf8_size=ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES,
                utf8_sha256=ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256,
            )
            if (
                len(item_raw) != item_identity.utf8_size
                or _sha256(item_raw) != item_identity.utf8_sha256
            ):
                raise AuthorityBuildError(
                    "historical September Item TextAsset identity mismatch"
                )
            item_rows = list(iter_table_rows(item_text))
            if item_rows and item_rows[0][0].startswith("\ufeff"):
                item_rows[0][0] = item_rows[0][0][1:]
            if (
                len(item_rows) != item_identity.rows
                or assert_uniform_columns(item_rows) != item_identity.columns
            ):
                raise AuthorityBuildError(
                    "historical September Item row/column identity mismatch"
                )
            items_canonical_rows = ITEM_CATALOG_OFFICIAL_20260903_ROWS
            items_canonical_sha256 = (
                ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256
            )
        elif package_generation == ITEM_TABLE_OFFICIAL_20261008_GENERATION:
            pin = OCTOBER_TEXT_IDENTITIES["Item"]
            item_identity = SimpleNamespace(rows=1506, columns=45, utf8_size=pin["bytes"], utf8_sha256=pin["sha256"])
            if len(item_raw) != item_identity.utf8_size or _sha256(item_raw) != item_identity.utf8_sha256:
                raise AuthorityBuildError("October Item TextAsset identity mismatch")
            item_rows = list(iter_table_rows(item_text))
            if item_rows and item_rows[0][0].startswith("\ufeff"):
                item_rows[0][0] = item_rows[0][0][1:]
            if len(item_rows) != 1506 or assert_uniform_columns(item_rows) != 45:
                raise AuthorityBuildError("October Item row/column identity mismatch")
            items_canonical_rows = 1506
            items_canonical_sha256 = ITEM_CATALOG_OFFICIAL_20261008_CANONICAL_SHA256
        else:
            historical_hotfix = package_generation == ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION
            item_identity = (
                SimpleNamespace(
                    rows=ITEM_TABLE_OFFICIAL_20260903_ROWS,
                    columns=42,
                    utf8_size=ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
                    utf8_sha256=ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
                ) if historical_hotfix else CURRENT_TEXT_ASSET_IDENTITIES["Item"]
            )
            if (
                len(item_raw) != item_identity.utf8_size
                or _sha256(item_raw) != item_identity.utf8_sha256
            ):
                raise AuthorityBuildError("current Item TextAsset identity mismatch")
            item_rows = list(iter_table_rows(item_text))
            if item_rows and item_rows[0][0].startswith("\ufeff"):
                item_rows[0][0] = item_rows[0][0][1:]
            if (
                len(item_rows) != item_identity.rows
                or assert_uniform_columns(item_rows) != item_identity.columns
            ):
                raise AuthorityBuildError("current Item row/column identity mismatch")
            items_canonical_rows = (ITEM_CATALOG_OFFICIAL_20260903_ROWS if historical_hotfix
                                    else ITEM_CATALOG_OFFICIAL_20260910_ROWS)
            items_canonical_sha256 = (ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256 if historical_hotfix
                                      else ITEM_CATALOG_OFFICIAL_20260910_CANONICAL_SHA256)
        items = parse_item_table(item_rows)
    except ValueError as exc:
        raise AuthorityBuildError(str(exc)) from exc

    items_value, _ = _read_json(items_json)
    legacy_value, _ = _read_json(legacy_category)
    droppable_value, _ = _read_json(items_droppable)
    catalog = _catalog_by_id(items_value, source_name="items.json")
    legacy = _legacy_by_id(legacy_value)
    droppable = _catalog_by_id(
        droppable_value,
        source_name="items_droppable",
    )
    _require_canonical_identity(
        items_value,
        label="items.json",
        rows=items_canonical_rows,
        sha256=items_canonical_sha256,
    )
    _require_canonical_identity(
        legacy_value,
        label="legacy category",
        rows=LEGACY_CATEGORY_CANONICAL_ROWS,
        sha256=LEGACY_CATEGORY_CANONICAL_SHA256,
    )
    expected_droppable_rows = (
        CURRENT_DROPPABLE_CANONICAL_ROWS
        if package_generation
        in {
            PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION,
            ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION,
            CURRENT_PACKAGE_GENERATION,
        }
        else LEGACY_DROPPABLE_CANONICAL_ROWS
    )
    expected_droppable_sha256 = (
        CURRENT_DROPPABLE_CANONICAL_SHA256
        if package_generation
        in {
            PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION,
            ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION,
            CURRENT_PACKAGE_GENERATION,
        }
        else LEGACY_DROPPABLE_CANONICAL_SHA256
    )
    if package_generation == ITEM_TABLE_OFFICIAL_20261008_GENERATION:
        expected_droppable_rows = 690
        expected_droppable_sha256 = OCTOBER_DROPPABLE_CANONICAL_SHA256
    _require_canonical_identity(
        droppable_value,
        label="items_droppable",
        rows=expected_droppable_rows,
        sha256=expected_droppable_sha256,
    )
    if not set(items).issubset(catalog):
        raise AuthorityBuildError("items.json does not cover the exact Item generation")
    if set(catalog) != set(items):
        raise AuthorityBuildError("items.json contains a non-official Item identity")
    if (
        _sha256(canonical_json_bytes(UPDATE606_HISTORICAL_ONLY_IDS))
        != UPDATE606_HISTORICAL_ONLY_IDS_SHA256
        or _sha256(canonical_json_bytes(UPDATE606_OFFICIAL_ONLY_IDS))
        != UPDATE606_OFFICIAL_ONLY_IDS_SHA256
    ):
        raise AuthorityBuildError("update606 catalog partition constant mismatch")
    _validate_catalog_overlap(items, catalog)
    legacy_nonprimary_ids: list[int] = []
    for item_id, category in legacy.items():
        item = items.get(item_id)
        categories = [tag for tag in item.tags if tag in TAXONOMY_IDS] if item else []
        if category not in categories:
            raise AuthorityBuildError(
                "legacy positive category conflict "
                f"item_id={item_id} expected_membership={category} "
                f"actual={categories}"
            )
        if category != categories[-1]:
            legacy_nonprimary_ids.append(item_id)
    if tuple(sorted(legacy_nonprimary_ids)) != LEGACY_POSITIVE_NONPRIMARY_IDS:
        raise AuthorityBuildError("legacy positive nonprimary identity mismatch")
    _validate_droppable_overlap(items, catalog, droppable)
    if package_generation == ITEM_TABLE_OFFICIAL_20261008_GENERATION:
        if len(droppable) != 690 or _sha256(canonical_json_bytes(tuple(sorted(droppable)))) != OCTOBER_UNION_IDS_SHA256:
            raise AuthorityBuildError("October droppable lifecycle identity mismatch")
    if package_generation in {
        PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION,
        ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION,
        CURRENT_PACKAGE_GENERATION,
    }:
        droppable_ids = tuple(sorted(droppable))
        if (
            len(droppable_ids) != CURRENT_MODEL_UNION_ITEM_ROWS
            or _sha256(canonical_json_bytes(droppable_ids))
            != CURRENT_MODEL_UNION_ITEM_IDS_SHA256
            or set(RETIRED_SUMMER_PARTY_2026_ITEM_IDS) & set(droppable_ids)
            or set(CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS) & set(droppable_ids)
        ):
            raise AuthorityBuildError("current droppable lifecycle identity mismatch")
    historical_only = set(UPDATE606_HISTORICAL_ONLY_IDS)
    official_only = set(UPDATE606_OFFICIAL_ONLY_IDS)
    if historical_only & (set(items) | set(catalog) | set(droppable)):
        raise AuthorityBuildError("historical-only identity entered semantic authority")
    if not official_only.issubset(items) or official_only & set(droppable):
        raise AuthorityBuildError("official-only Item droppable boundary mismatch")

    records: list[dict[str, Any]] = []
    for item_id in sorted(items):
        item = items[item_id]
        categories = [tag for tag in item.tags if tag in TAXONOMY_IDS]
        records.append(
            {
                **_identity_fields(item),
                "item_id": item_id,
                "taxonomy_tags": list(item.tags),
                "category_ids": categories,
                "primary_category": categories[-1] if categories else None,
                "catalog_present": item_id in catalog,
                "legacy_category_present": item_id in legacy,
                "current_random_drop_eligible": item_id in droppable,
            }
        )

    sources = {
        "item_table": {
            "archive_sha256": _sha256(item_raw),
            "columns": item_identity.columns,
            "compression": "none",
            "hash_mode": "raw_uncompressed_bytes",
            "rows": len(item_rows),
            "sha256": _sha256(item_raw),
        },
        "items_json": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": len(set(catalog) & set(items)),
            "rows": len(catalog),
            "sha256": _sha256(canonical_json_bytes(items_value)),
        },
        "legacy_category": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": len(set(legacy) & set(items)),
            "rows": len(legacy),
            "sha256": _sha256(canonical_json_bytes(legacy_value)),
        },
        "items_droppable": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": len(set(droppable) & set(items)),
            "rows": len(droppable),
            "sha256": _sha256(canonical_json_bytes(droppable_value)),
        },
    }
    if (
        sources["item_table"]["rows"] != item_identity.rows
        or sources["item_table"]["sha256"] != item_identity.utf8_sha256
    ):
        raise AuthorityBuildError("Item source identity changed after validation")
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generation": f"item-table-sha256:{item_identity.utf8_sha256}",
        "taxonomy": TAXONOMY,
        "primary_category_policy": PRIMARY_CATEGORY_POLICY,
        "sources": sources,
        "records": records,
        "records_sha256": sha256_json(records),
        "authority_identity": "",
    }
    document["authority_identity"] = sha256_json(authority_identity_payload(document))
    validate_authority_document(
        document,
        expected_item_table_sha256=item_identity.utf8_sha256,
    )
    return document


def render_authority(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build_receipt(document: dict[str, Any], rendered: bytes) -> dict[str, Any]:
    by_id = {row["item_id"]: row for row in document["records"]}
    reconciliation = [
        {
            "item_id": item_id,
            "legacy_positive_category": 101,
            "official_category_ids": by_id[item_id]["category_ids"],
            "official_primary_category": by_id[item_id]["primary_category"],
        }
        for item_id in LEGACY_POSITIVE_NONPRIMARY_IDS
    ]
    reconciliation_sha256 = _sha256(canonical_json_bytes(reconciliation))
    if reconciliation_sha256 != LEGACY_POSITIVE_NONPRIMARY_SHA256:
        raise AuthorityBuildError("legacy positive reconciliation identity mismatch")
    item_sha256 = document["sources"]["item_table"]["sha256"]
    if item_sha256 == UPDATE606_TEXT_ASSET_IDENTITIES["Item"].utf8_sha256:
        package_generation = UPDATE606_PACKAGE_GENERATION
        canonical_sha256 = ITEM_TABLE_UPDATE606_CANONICAL_SHA256
    elif item_sha256 == (
        "7ac11867652b21ebbd62461cd95da84addfe721dea5c01307e4dd568fd60933c"
    ):
        package_generation = PREVIOUS_0_3_4_PACKAGE_GENERATION
        canonical_sha256 = ITEM_TABLE_0_3_4_CANONICAL_SHA256
    elif item_sha256 == ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256:
        package_generation = PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION
        canonical_sha256 = ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256
    elif item_sha256 == ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256:
        package_generation = ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION
        canonical_sha256 = ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256
    elif item_sha256 == CURRENT_TEXT_ASSET_IDENTITIES["Item"].utf8_sha256:
        package_generation = CURRENT_PACKAGE_GENERATION
        canonical_sha256 = ITEM_TABLE_OFFICIAL_20260910_CANONICAL_SHA256
    elif item_sha256 == ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_SHA256:
        package_generation = ITEM_TABLE_OFFICIAL_20261008_GENERATION
        canonical_sha256 = ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256
    else:
        raise AuthorityBuildError("item authority receipt generation mismatch")
    receipt = {
        "schema": "bidking_update606_item_authority_build_receipt_v1",
        "package_generation": package_generation,
        "item_table_canonical_sha256": canonical_sha256,
        "legacy_category_contract": LEGACY_CATEGORY_CONTRACT,
        "legacy_positive_nonprimary_ids": list(LEGACY_POSITIVE_NONPRIMARY_IDS),
        "legacy_positive_nonprimary_reconciliation_sha256": (
            reconciliation_sha256
        ),
        "historical_only": {
            "authority": False,
            "catalog_included": False,
            "display_fallback": "unknown_not_run",
            "droppable": False,
            "ids": list(UPDATE606_HISTORICAL_ONLY_IDS),
            "ids_sha256": UPDATE606_HISTORICAL_ONLY_IDS_SHA256,
            "pool": False,
            "quote": False,
            "weights": False,
        },
        "official_only": {
            "catalog_present": True,
            "droppable": False,
            "ids": list(UPDATE606_OFFICIAL_ONLY_IDS),
            "ids_sha256": UPDATE606_OFFICIAL_ONLY_IDS_SHA256,
        },
        "inputs": document["sources"],
        "output": {
            "authority_identity": document["authority_identity"],
            "bytes": len(rendered),
            "records": len(document["records"]),
            "records_sha256": document["records_sha256"],
            "sha256": _sha256(rendered),
        },
    }
    if package_generation in {ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION, CURRENT_PACKAGE_GENERATION}:
        receipt["current_model_union"] = {
            "items": CURRENT_MODEL_UNION_ITEM_ROWS,
            "item_ids_sha256": CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
            "retired_summer_included": False,
            "exchange_or_pass_outputs_included": False,
        }
    elif package_generation == ITEM_TABLE_OFFICIAL_20261008_GENERATION:
        receipt["current_model_union"] = {
            "items": 690,
            "item_ids_sha256": OCTOBER_UNION_IDS_SHA256,
            "hall_only_included": False,
            "unrouted_guild_or_pass_included": False,
        }
    return receipt


def _render_receipt(receipt: dict[str, Any]) -> bytes:
    return (
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--item-text-asset", type=Path, required=True)
    parser.add_argument("--items-json", type=Path, required=True)
    parser.add_argument("--legacy-category", type=Path, required=True)
    parser.add_argument("--items-droppable", type=Path, required=True)
    parser.add_argument("--package-generation", required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        document = build_authority_document(
            item_text_asset=args.item_text_asset,
            items_json=args.items_json,
            legacy_category=args.legacy_category,
            items_droppable=args.items_droppable,
            package_generation=args.package_generation,
        )
        rendered = render_authority(document)
        receipt = _render_receipt(build_receipt(document, rendered))
        if args.check:
            if args.out.read_bytes() != rendered or args.receipt_out.read_bytes() != receipt:
                raise AuthorityBuildError(
                    "authority or receipt differs from exact regeneration"
                )
            action = "checked"
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(rendered)
            args.receipt_out.write_bytes(receipt)
            action = "wrote"
    except (AuthorityBuildError, OSError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print(
        f"{action} {args.out}: records={len(document['records'])} "
        f"identity={document['authority_identity']} receipt={args.receipt_out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
