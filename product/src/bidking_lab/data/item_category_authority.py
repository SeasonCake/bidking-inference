"""Validated, versioned Item category authority for calibration inputs.

The authority deliberately separates three concepts that older calibration data
conflated: all Item taxonomy tags, one deterministic primary category, and current
random-drop eligibility.  Consumers must not infer any of them from ``item_id``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..secure.data_crypt import load_text as load_moat_text

SCHEMA_VERSION = "bidking.item-category-authority/v1"
PRIMARY_CATEGORY_POLICY = "last_item_taxonomy_tag_in_100_through_111"
TAXONOMY: dict[str, dict[str, str]] = {
    "100": {"label": "特殊藏品", "slug": "special_collectible"},
    "101": {"label": "家具", "slug": "furniture"},
    "102": {"label": "医疗", "slug": "medical"},
    "103": {"label": "时尚", "slug": "fashion"},
    "104": {"label": "武器", "slug": "weapon"},
    "105": {"label": "珠宝", "slug": "jewelry"},
    "106": {"label": "古董", "slug": "antique"},
    "107": {"label": "数码", "slug": "digital"},
    "108": {"label": "能源", "slug": "energy"},
    "109": {"label": "食饮", "slug": "food_and_drink"},
    "110": {"label": "书画", "slug": "books_and_paintings"},
    "111": {"label": "未知藏品箱", "slug": "unknown_collectible_box"},
}
TAXONOMY_KEYS = tuple(str(category_id) for category_id in range(100, 112))
TAXONOMY_IDS = frozenset(int(key) for key in TAXONOMY)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_LEVEL_KEYS = {
    "authority_identity",
    "generation",
    "primary_category_policy",
    "records",
    "records_sha256",
    "schema_version",
    "sources",
    "taxonomy",
}
_RECORD_KEYS = {
    "catalog_present",
    "category_ids",
    "current_random_drop_eligible",
    "desc_key",
    "item_id",
    "legacy_category_present",
    "name_key",
    "primary_category",
    "quality",
    "shape_h",
    "shape_w",
    "taxonomy_tags",
    "value",
}


class ItemCategoryAuthorityError(ValueError):
    """The authority is malformed, inconsistent, or not the expected generation."""


def _has_exact_type(value: Any, expected: type[Any]) -> bool:
    return value.__class__ is expected


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def authority_identity_payload(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "generation": document["generation"],
        "primary_category_policy": document["primary_category_policy"],
        "records_sha256": document["records_sha256"],
        "schema_version": document["schema_version"],
        "sources": document["sources"],
        "taxonomy": document["taxonomy"],
    }


def _require_exact_dict(value: Any, *, field: str) -> dict[str, Any]:
    if not _has_exact_type(value, dict):
        raise ItemCategoryAuthorityError(f"{field} must be an exact object")
    return value


def _require_sha256(value: Any, *, field: str) -> str:
    if not _has_exact_type(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ItemCategoryAuthorityError(f"{field} must be lowercase SHA-256")
    return value


def _validate_sources(value: Any) -> dict[str, Any]:
    sources = _require_exact_dict(value, field="sources")
    expected = {"item_table", "items_json", "legacy_category", "items_droppable"}
    if set(sources) != expected:
        raise ItemCategoryAuthorityError("sources keys mismatch")
    for name, source in sources.items():
        source = _require_exact_dict(source, field=f"sources.{name}")
        expected_keys = {"hash_mode", "rows", "sha256"}
        if name == "item_table":
            expected_keys.update({"archive_sha256", "columns", "compression"})
        else:
            expected_keys.add("overlap_rows")
        if set(source) != expected_keys:
            raise ItemCategoryAuthorityError(f"sources.{name} keys mismatch")
        _require_sha256(source["sha256"], field=f"sources.{name}.sha256")
        if not _has_exact_type(source["rows"], int) or source["rows"] < 0:
            raise ItemCategoryAuthorityError(f"sources.{name}.rows must be nonnegative int")
        if name == "item_table" and (
            not _has_exact_type(source["columns"], int)
            or source["columns"] not in (38, 40, 41, 42, 45)
        ):
            raise ItemCategoryAuthorityError("sources.item_table.columns unsupported")
        if name == "item_table":
            if source["columns"] == 45 and (
                source["rows"] != 1506
                or source["sha256"] != "125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372"
            ):
                raise ItemCategoryAuthorityError("45-column Item requires exact 76b7ca identity")
            _require_sha256(
                source["archive_sha256"],
                field="sources.item_table.archive_sha256",
            )
            if source["compression"] not in ("none", "gzip"):
                raise ItemCategoryAuthorityError(
                    "sources.item_table.compression unsupported"
                )
            if source["hash_mode"] != "raw_uncompressed_bytes":
                raise ItemCategoryAuthorityError(
                    "sources.item_table.hash_mode mismatch"
                )
        elif source["hash_mode"] != "canonical_json_utf8":
            raise ItemCategoryAuthorityError(
                f"sources.{name}.hash_mode mismatch"
            )
        if name != "item_table" and (
            not _has_exact_type(source["overlap_rows"], int)
            or not 0 <= source["overlap_rows"] <= source["rows"]
        ):
            raise ItemCategoryAuthorityError(f"sources.{name}.overlap_rows invalid")
    return sources


def _validate_record(value: Any, *, previous_item_id: int) -> int:
    record = _require_exact_dict(value, field="record")
    if set(record) != _RECORD_KEYS:
        raise ItemCategoryAuthorityError("record keys mismatch")

    item_id = record["item_id"]
    if not _has_exact_type(item_id, int) or item_id <= previous_item_id:
        raise ItemCategoryAuthorityError("records must have strictly increasing positive item_id")
    for field in ("name_key", "desc_key"):
        if not _has_exact_type(record[field], str):
            raise ItemCategoryAuthorityError(f"record.{field} must be exact str")
    for field in ("quality", "value", "shape_w", "shape_h"):
        if not _has_exact_type(record[field], int):
            raise ItemCategoryAuthorityError(f"record.{field} must be exact int")
    if not 0 <= record["quality"] <= 6:
        raise ItemCategoryAuthorityError("record.quality outside 0..6")
    if record["value"] < 0 or not 0 <= record["shape_w"] <= 6 or not 0 <= record["shape_h"] <= 7:
        raise ItemCategoryAuthorityError("record value/shape outside supported range")

    tags = record["taxonomy_tags"]
    categories = record["category_ids"]
    if not _has_exact_type(tags, list) or any(
        not _has_exact_type(tag, int) for tag in tags
    ):
        raise ItemCategoryAuthorityError("record.taxonomy_tags must be exact int list")
    if not _has_exact_type(categories, list) or any(
        not _has_exact_type(tag, int) for tag in categories
    ):
        raise ItemCategoryAuthorityError("record.category_ids must be exact int list")
    expected_categories = [tag for tag in tags if tag in TAXONOMY_IDS]
    if categories != expected_categories:
        raise ItemCategoryAuthorityError("record.category_ids does not match taxonomy_tags")
    expected_primary = expected_categories[-1] if expected_categories else None
    if record["primary_category"] != expected_primary or (
        record["primary_category"] is not None
        and not _has_exact_type(record["primary_category"], int)
    ):
        raise ItemCategoryAuthorityError("record.primary_category violates policy")

    for field in (
        "catalog_present",
        "legacy_category_present",
        "current_random_drop_eligible",
    ):
        if not _has_exact_type(record[field], bool):
            raise ItemCategoryAuthorityError(f"record.{field} must be exact bool")
    if record["current_random_drop_eligible"] and (
        expected_primary is None or not record["catalog_present"]
    ):
        raise ItemCategoryAuthorityError("eligible record lacks category or catalog identity")
    if record["legacy_category_present"] and expected_primary is None:
        raise ItemCategoryAuthorityError("legacy category record lacks primary category")
    return item_id


def validate_authority_document(
    value: Any,
    *,
    expected_item_table_sha256: str | None = None,
) -> dict[str, Any]:
    document = _require_exact_dict(value, field="authority")
    if set(document) != _TOP_LEVEL_KEYS:
        raise ItemCategoryAuthorityError("authority keys mismatch")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ItemCategoryAuthorityError("schema_version mismatch")
    if document["primary_category_policy"] != PRIMARY_CATEGORY_POLICY:
        raise ItemCategoryAuthorityError("primary_category_policy mismatch")
    taxonomy = _require_exact_dict(document["taxonomy"], field="taxonomy")
    if tuple(taxonomy) != TAXONOMY_KEYS or taxonomy != TAXONOMY:
        raise ItemCategoryAuthorityError("taxonomy mismatch")
    sources = _validate_sources(document["sources"])
    item_table_sha = sources["item_table"]["sha256"]
    if document["generation"] != f"item-table-sha256:{item_table_sha}":
        raise ItemCategoryAuthorityError("generation does not bind item table SHA-256")
    if expected_item_table_sha256 is not None and item_table_sha != expected_item_table_sha256:
        raise ItemCategoryAuthorityError("unexpected item table generation")

    records = document["records"]
    if not _has_exact_type(records, list) or len(records) != sources["item_table"]["rows"]:
        raise ItemCategoryAuthorityError("records length does not match Item source")
    previous_item_id = 0
    for record in records:
        previous_item_id = _validate_record(record, previous_item_id=previous_item_id)
    overlap_contract = {
        "items_json": sum(record["catalog_present"] for record in records),
        "legacy_category": sum(
            record["legacy_category_present"] for record in records
        ),
        "items_droppable": sum(
            record["current_random_drop_eligible"] for record in records
        ),
    }
    for source_name, actual_overlap in overlap_contract.items():
        if sources[source_name]["overlap_rows"] != actual_overlap:
            raise ItemCategoryAuthorityError(
                f"sources.{source_name}.overlap_rows does not match records"
            )
    expected_records_sha = sha256_json(records)
    if _require_sha256(document["records_sha256"], field="records_sha256") != expected_records_sha:
        raise ItemCategoryAuthorityError("records_sha256 mismatch")
    expected_identity = sha256_json(authority_identity_payload(document))
    if _require_sha256(document["authority_identity"], field="authority_identity") != expected_identity:
        raise ItemCategoryAuthorityError("authority_identity mismatch")
    return document


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ItemCategoryAuthorityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ItemCategoryRecord:
    item_id: int
    category_ids: tuple[int, ...]
    primary_category: int | None
    current_random_drop_eligible: bool


@dataclass(frozen=True)
class ItemCategoryAuthority:
    generation: str
    identity: str
    records: Mapping[int, ItemCategoryRecord]

    def category_for_item(self, item_id: int) -> int | None:
        if not _has_exact_type(item_id, int) or item_id <= 0:
            return None
        record = self.records.get(item_id)
        return record.primary_category if record is not None else None

    def require_random_drop_category(self, item_id: int) -> int:
        if not _has_exact_type(item_id, int) or item_id <= 0:
            raise ItemCategoryAuthorityError("item_id must be exact positive int")
        record = self.records.get(item_id)
        if record is None or not record.current_random_drop_eligible:
            raise ItemCategoryAuthorityError(f"item_id={item_id} is not current random-drop eligible")
        if record.primary_category is None:
            raise ItemCategoryAuthorityError(f"item_id={item_id} has no primary category")
        return record.primary_category


def _project_item_category_authority(
    document: Mapping[str, Any],
) -> ItemCategoryAuthority:
    records = {
        row["item_id"]: ItemCategoryRecord(
            item_id=row["item_id"],
            category_ids=tuple(row["category_ids"]),
            primary_category=row["primary_category"],
            current_random_drop_eligible=row["current_random_drop_eligible"],
        )
        for row in document["records"]
    }
    return ItemCategoryAuthority(
        generation=document["generation"],
        identity=document["authority_identity"],
        records=MappingProxyType(records),
    )


def load_item_category_authority_document(
    path: Path,
    *,
    expected_item_table_sha256: str | None = None,
) -> tuple[ItemCategoryAuthority, dict[str, Any]]:
    """Load one authenticated, duplicate-safe snapshot and return both views."""

    try:
        # ``load_moat_text`` performs exactly one byte read.  Plaintext development
        # files are decoded directly; packaged BKENC2 members are authenticated,
        # decompressed and decoded from that same captured byte snapshot.
        snapshot = load_moat_text(path, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - encrypted input must fail closed
        raise ItemCategoryAuthorityError(f"cannot load authority: {exc.__class__.__name__}") from exc
    try:
        raw = json.loads(snapshot, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ItemCategoryAuthorityError(f"cannot load authority: {exc.__class__.__name__}") from exc
    document = validate_authority_document(
        raw,
        expected_item_table_sha256=expected_item_table_sha256,
    )
    return (
        _project_item_category_authority(document),
        document,
    )


def load_item_category_authority(
    path: Path,
    *,
    expected_item_table_sha256: str | None = None,
) -> ItemCategoryAuthority:
    authority, _document = load_item_category_authority_document(
        path,
        expected_item_table_sha256=expected_item_table_sha256,
    )
    return authority
