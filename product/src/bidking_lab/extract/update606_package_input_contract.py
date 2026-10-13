"""Generation-bound package-input contract for update606.

The official update606 bundle has no Drop/DropMap authority.  The legacy Drop
table and map-keyed calibrated outputs may therefore be carried forward only
for map routes whose distribution-bearing BidMap cells are byte-for-byte stable
between the exact legacy package baseline and update606.  New-only, changed,
5506 and 6001 routes are excluded before any derived output is rendered.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping

from bidking_lab.extract.bid_map_table import (
    BID_MAP_UPDATE606_GENERATION,
)
from bidking_lab.extract.item_table import parse_item_table
from bidking_lab.extract.tables import assert_uniform_columns, iter_table_rows
from bidking_lab.extract.current_rankmap_package_contract import (
    LEGACY_MAP_PAYLOADS,
    LEGACY_PACKAGE_DROP,
    UPDATE606_PACKAGE_BID_MAP,
    UPDATE606_PACKAGE_ITEM,
    Update606PackageInputError,
)
from bidking_lab.extract.update606_release_contract import (
    UPDATE606_TEXT_ASSET_IDENTITIES,
    update606_text_asset_rows,
)


UPDATE606_AUXILIARY_TEXT_ASSETS: Mapping[str, Mapping[str, Any]] = {
    "BattleItem": {
        "bytes": 4_468,
        "sha256": "fc8c5231c723a80d4e338069aa09e32c32a5f4a3d5cc05f5e5d3e8094c844846",
        "rows": 64,
        "columns": 6,
    },
    "Hero": {
        "bytes": 9_349,
        "sha256": "ae7997405c26bfe0b776d22ac0f3c5abb229075453d645668beaa2e0654342ac",
        "rows": 21,
        "columns": 21,
    },
}
UPDATE606_AUXILIARY_PACKAGE_TABLES: Mapping[str, Mapping[str, Any]] = {
    "BattleItem": {
        "bytes": 5_956,
        "sha256": "9a721d9fae09caa47c6493ad860a979f6b9a3202fcabb68ab6f068f6fe159777",
        "decoded_bytes": 4_465,
        "decoded_sha256": (
            "41054bff3965f92b983edfe17be9e53b123d1ab9fa65ac28bfb87a8a0871ef57"
        ),
    },
    "Hero": {
        "bytes": 12_464,
        "sha256": "be633788a46af6d0237be97d41be61b63ac060ff7592c4f1aeec561acee36ba4",
        "decoded_bytes": 9_346,
        "decoded_sha256": (
            "f44183c801fde57007578f2ecf287600c21f15cc485768a19a2f89bc5cbb77d0"
        ),
    },
}
UPDATE606_ITEMS_CATALOG = {
    "bytes": 646_256,
    "canonical_sha256": (
        "18e4efa914cae2d19705d242e11730def4f50f288673820d98da03638f2972d2"
    ),
    "rows": 1_336,
    "sha256": "05f26de6ff8777526b2b1efd1f21eef98957e3030e3d241b4880fab449af4988",
}
LEGACY_ITEMS_CATALOG = {
    "bytes": 576_179,
    "canonical_sha256": (
        "22fbc8e6b7c3bbfb4faa3ee46139a0a0da5dc01845e2ab2cf19717776a8f5ac6"
    ),
    "rows": 1_303,
    "sha256": "164b5a5a667161447fde86fd306c65a38bb0d5ef2070be7eb781482bd21e84e9",
}
UPDATE606_CATALOG_OVERLAP = {
    "rows": 1_292,
    "ids_sha256": "e63256417c569b6c4963fd3a7226c1d99747ce6808314df8b4fca735b4d70a95",
}
UPDATE606_CATALOG_OFFICIAL_ONLY = {
    "rows": 44,
    "ids_sha256": "54a42719b7c502f21479ee47a5740f26941df0651e7ccc8ed86a9c1f0d481884",
}
UPDATE606_CATALOG_HISTORICAL_ONLY = {
    "rows": 11,
    "ids_sha256": "172a74dd8638d8764e8da1685104c1fb9114676bc7a9fce1415ed102521bcc00",
}
UPDATE606_HISTORICAL_ONLY_ITEM_IDS = (
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
UPDATE606_CATALOG_DISPLAY_FIELDS = ("name",)
UPDATE606_CATALOG_OBSERVED_DIFFERENCE_FIELDS = (
    "name",
    "description",
    "icon_name",
    "_src",
    "_name_src",
)
UPDATE606_CATALOG_FIELD_DIFFS = {
    "name": {
        "rows": 138,
        "ids_sha256": "3dad05283ebad8e9639cdf7a4e27696f4841b4dc3e8182da5b3e1e062048b9c9",
    },
    "description": {
        "rows": 87,
        "ids_sha256": "08da1d38980ea66e4b631d620cef04d64f16baa7cba25f0c3fa194d62f2e5f58",
    },
    "icon_name": {
        "rows": 3,
        "ids_sha256": "97a47b7b5f59446263ae7b2b3fa99dbfa75bfefd061ff92440d4b0a4c3440ef6",
    },
    "_src": {
        "rows": 84,
        "ids_sha256": "46e07003b52b58e36acec0b293abd289dadd1653b965226cb9da226cf9ea8bf9",
    },
    "_name_src": {
        "rows": 124,
        "ids_sha256": "b5a6274d71070a84c2641fc48e20373a3e29a1f9caefd26e4336224c7a1136c8",
    },
}
LEGACY_PACKAGE_BASELINE_ID = "package-data-2026-08-09-0.3.2-lane-a-package-contract"
LEGACY_DISTRIBUTION_POLICY = "legacy_distribution_carry_forward_for_legacy_maps_only"
LEGACY_PACKAGE_BID_MAP = {
    "bytes": 80_972,
    "sha256": "6c54d421fae9e238f1c1b5afc6deea88e325805ca8df706804e8dfb893f50edd",
    "decoded_sha256": "0cee2ea0fa263141d238f29110975e64c63487c777b06c999e05ff57573e4f71",
    "rows": 165,
    "columns": 23,
}
LEGACY_ALLOWED_MAP_IDS_SHA256 = (
    "c10b764dc0bb6af95848dde76b11967fd74bcecb5645d8f55312ece83b919a4e"
)
LEGACY_NEW_ONLY_MAP_IDS_SHA256 = (
    "5ce0ef61c474cd9bcd508b66f71305030486a11bb0f5d1e30b36d398776d894b"
)
LEGACY_CHANGED_ROUTE_MAP_IDS_SHA256 = (
    "70090367944442a8a84d70101523f4c4856e00f1736c5f513f231995e1658e1f"
)
LEGACY_ONLY_MAP_IDS_SHA256 = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)

def build_update606_auxiliary_package_table(
    name: str,
    text_asset: bytes,
    *,
    previous_text_asset: bytes | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Wrap one exact auxiliary TextAsset for the package table loaders.

    These two official assets contain a UTF-8 BOM and CRLF line endings.  The
    package representation removes only the BOM and Base64-encodes every
    remaining byte.  In particular it does not normalize CRLF to LF.
    BattleItem also carries the previous official generation as a required
    byte-identical control because its semantics are intentionally unchanged.
    """

    source = UPDATE606_AUXILIARY_TEXT_ASSETS.get(name)
    package_identity = UPDATE606_AUXILIARY_PACKAGE_TABLES.get(name)
    if source is None or package_identity is None:
        raise Update606PackageInputError(
            f"unsupported update606 auxiliary package table: {name!r}"
        )
    if (
        text_asset.__class__ is not bytes
        or len(text_asset) != source["bytes"]
        or _sha256(text_asset) != source["sha256"]
    ):
        raise Update606PackageInputError(
            f"official {name} TextAsset identity mismatch"
        )
    if name == "BattleItem":
        if previous_text_asset.__class__ is not bytes:
            raise Update606PackageInputError(
                "previous official BattleItem TextAsset is required"
            )
        if previous_text_asset != text_asset:
            raise Update606PackageInputError(
                "previous/update606 BattleItem generation mismatch"
            )
    elif previous_text_asset is not None:
        raise Update606PackageInputError(
            f"previous official control is unsupported for {name}"
        )
    if not text_asset.startswith(b"\xef\xbb\xbf"):
        raise Update606PackageInputError(
            f"official {name} TextAsset UTF-8 BOM mismatch"
        )
    decoded = text_asset[3:]
    try:
        rows = list(iter_table_rows(decoded.decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise Update606PackageInputError(
            f"official {name} TextAsset is not UTF-8"
        ) from exc
    if (
        len(rows) != source["rows"]
        or assert_uniform_columns(rows) != source["columns"]
    ):
        raise Update606PackageInputError(
            f"official {name} TextAsset row/column identity mismatch"
        )
    package = base64.b64encode(decoded)
    if (
        len(decoded) != package_identity["decoded_bytes"]
        or _sha256(decoded) != package_identity["decoded_sha256"]
        or len(package) != package_identity["bytes"]
        or _sha256(package) != package_identity["sha256"]
    ):
        raise Update606PackageInputError(
            f"canonical {name} package identity mismatch"
        )
    receipt: dict[str, Any] = {
        "schema": "bidking_update606_auxiliary_package_table_receipt_v1",
        "name": name,
        "package_generation": BID_MAP_UPDATE606_GENERATION,
        "bom_policy": "strip_utf8_bom_only_preserve_remaining_bytes",
        "official_text_asset": {
            "bytes": len(text_asset),
            "sha256": _sha256(text_asset),
            "rows": len(rows),
            "columns": assert_uniform_columns(rows),
        },
        "decoded": {
            "bytes": len(decoded),
            "sha256": _sha256(decoded),
        },
        "package_base64": {
            "bytes": len(package),
            "sha256": _sha256(package),
        },
    }
    if name == "BattleItem":
        receipt["previous_official_text_asset"] = {
            "package_generation": "89437c-2026-08-07-745",
            "byte_identical_to_update606": True,
            "bytes": len(previous_text_asset),
            "sha256": _sha256(previous_text_asset),
        }
    return package, receipt


def build_update606_package_table(
    name: str,
    text_asset: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Canonicalize one exact official TextAsset into package Base64 bytes."""

    package_identity = {
        "BidMap": UPDATE606_PACKAGE_BID_MAP,
        "Item": UPDATE606_PACKAGE_ITEM,
    }.get(name)
    official_identity = UPDATE606_TEXT_ASSET_IDENTITIES.get(name)
    if package_identity is None or official_identity is None:
        raise Update606PackageInputError(f"unsupported update606 package table: {name!r}")
    if (
        len(text_asset) != official_identity.utf8_size
        or _sha256(text_asset) != official_identity.utf8_sha256
    ):
        raise Update606PackageInputError(f"official {name} TextAsset identity mismatch")
    try:
        rows = update606_text_asset_rows(name, text_asset.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Update606PackageInputError(str(exc)) from exc
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    package = base64.b64encode(canonical)
    if (
        _sha256(canonical) != package_identity["decoded_sha256"]
        or len(package) != package_identity["bytes"]
        or _sha256(package) != package_identity["sha256"]
    ):
        raise Update606PackageInputError(f"canonical {name} package identity mismatch")
    receipt = {
        "schema": "bidking_update606_package_table_receipt_v1",
        "name": name,
        "package_generation": BID_MAP_UPDATE606_GENERATION,
        "official_text_asset": {
            "bytes": len(text_asset),
            "sha256": _sha256(text_asset),
        },
        "canonical_decoded": {
            "bytes": len(canonical),
            "columns": package_identity["columns"],
            "rows": package_identity["rows"],
            "sha256": _sha256(canonical),
        },
        "package_base64": {
            "bytes": len(package),
            "sha256": _sha256(package),
        },
    }
    return package, receipt


def build_update606_items_catalog(
    text_asset: bytes,
    legacy_catalog_bytes: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Build the exact update606 catalog without rewriting approved display text.

    Official update606 Item is the complete semantic authority.  The exact approved
    legacy catalog contributes only the approved display name for overlapping ids.
    Historical-only ids and differing descriptions/icons/provenance never enter the
    semantic catalog.
    """

    official_identity = UPDATE606_TEXT_ASSET_IDENTITIES["Item"]
    if (
        len(text_asset) != official_identity.utf8_size
        or _sha256(text_asset) != official_identity.utf8_sha256
    ):
        raise Update606PackageInputError("official Item TextAsset identity mismatch")
    if (
        len(legacy_catalog_bytes) != LEGACY_ITEMS_CATALOG["bytes"]
        or _sha256(legacy_catalog_bytes) != LEGACY_ITEMS_CATALOG["sha256"]
    ):
        raise Update606PackageInputError("approved legacy items catalog identity mismatch")
    try:
        rows = update606_text_asset_rows("Item", text_asset.decode("utf-8"))
        items = parse_item_table(rows)
        legacy_value = json.loads(
            legacy_catalog_bytes.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        legacy = _catalog_by_id(legacy_value, label="approved legacy items catalog")
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(str(exc)) from exc
    legacy_canonical = _canonical_json_bytes(legacy_value)
    if (
        len(legacy) != LEGACY_ITEMS_CATALOG["rows"]
        or _sha256(legacy_canonical) != LEGACY_ITEMS_CATALOG["canonical_sha256"]
    ):
        raise Update606PackageInputError(
            "approved legacy items catalog canonical identity mismatch"
        )

    official_ids = set(items)
    legacy_ids = set(legacy)
    overlap = tuple(sorted(official_ids & legacy_ids))
    official_only = tuple(sorted(official_ids - legacy_ids))
    historical_only = tuple(sorted(legacy_ids - official_ids))
    _require_id_set_identity(overlap, UPDATE606_CATALOG_OVERLAP, "catalog overlap")
    _require_id_set_identity(
        official_only,
        UPDATE606_CATALOG_OFFICIAL_ONLY,
        "catalog official-only",
    )
    _require_id_set_identity(
        historical_only,
        UPDATE606_CATALOG_HISTORICAL_ONLY,
        "catalog historical-only",
    )

    differences: dict[str, tuple[int, ...]] = {}
    for field in UPDATE606_CATALOG_OBSERVED_DIFFERENCE_FIELDS:
        if field.startswith("_"):
            differences[field] = tuple(
                item_id for item_id in overlap if field in legacy[item_id]
            )
            continue
        differences[field] = tuple(
            item_id
            for item_id in overlap
            if legacy[item_id].get(field) != getattr(items[item_id], field)
        )
    for field, ids in differences.items():
        _require_id_set_identity(
            ids,
            UPDATE606_CATALOG_FIELD_DIFFS[field],
            f"catalog field {field}",
        )

    catalog: list[dict[str, Any]] = []
    for item_id in sorted(official_ids):
        row = items[item_id].model_dump(exclude={"raw_row"})
        if item_id in legacy:
            for field in UPDATE606_CATALOG_DISPLAY_FIELDS:
                row[field] = legacy[item_id][field]
        catalog.append(row)
    rendered = json.dumps(
        catalog,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    if (
        len(catalog) != UPDATE606_ITEMS_CATALOG["rows"]
        or len(rendered) != UPDATE606_ITEMS_CATALOG["bytes"]
        or _sha256(rendered) != UPDATE606_ITEMS_CATALOG["sha256"]
        or _sha256(_canonical_json_bytes(catalog))
        != UPDATE606_ITEMS_CATALOG["canonical_sha256"]
    ):
        raise Update606PackageInputError("update606 items catalog identity mismatch")
    receipt = {
        "schema": "bidking_update606_items_catalog_receipt_v1",
        "package_generation": BID_MAP_UPDATE606_GENERATION,
        "sources": {
            "official_item": {
                "bytes": len(text_asset),
                "sha256": _sha256(text_asset),
            },
            "approved_legacy_catalog": {
                "bytes": len(legacy_catalog_bytes),
                "canonical_sha256": _sha256(legacy_canonical),
                "rows": len(legacy),
                "sha256": _sha256(legacy_catalog_bytes),
            },
        },
        "overlay_contract": {
            "display_fields": list(UPDATE606_CATALOG_DISPLAY_FIELDS),
            "excluded_overlap_fields": [
                "description",
                "icon_name",
                "_src",
                "_name_src",
            ],
            "semantic_authority": "official_update606_item",
            "overlap": _id_set_receipt(overlap),
            "official_only": {
                **_id_set_receipt(official_only),
                "marker": "official_update606_only",
            },
            "historical_only": {
                **_id_set_receipt(historical_only),
                "authority": False,
                "catalog_included": False,
                "display_fallback": "unknown_not_run",
                "droppable": False,
                "pool": False,
                "quote": False,
                "weights": False,
            },
            "field_differences": {
                field: _id_set_receipt(ids)
                for field, ids in differences.items()
            },
        },
        "output": {
            "bytes": len(rendered),
            "canonical_sha256": _sha256(_canonical_json_bytes(catalog)),
            "rows": len(catalog),
            "sha256": _sha256(rendered),
        },
    }
    return rendered, receipt


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ids_sha256(values: tuple[int, ...]) -> str:
    raw = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return _sha256(raw)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _catalog_by_id(value: Any, *, label: str) -> dict[int, dict[str, Any]]:
    if value.__class__ is not list:
        raise Update606PackageInputError(f"{label} must be an exact list")
    out: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(value):
        if row.__class__ is not dict:
            raise Update606PackageInputError(f"{label} row {index} must be an object")
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in out:
            raise Update606PackageInputError(f"{label} has invalid/duplicate item_id")
        out[item_id] = row
    return out


def _require_id_set_identity(
    values: tuple[int, ...],
    identity: Mapping[str, Any],
    label: str,
) -> None:
    if len(values) != identity["rows"] or _ids_sha256(values) != identity["ids_sha256"]:
        raise Update606PackageInputError(f"{label} identity mismatch")


def _id_set_receipt(values: tuple[int, ...]) -> dict[str, Any]:
    return {
        "ids": list(values),
        "ids_sha256": _ids_sha256(values),
        "rows": len(values),
    }


def _decode_exact_package_table(
    payload: bytes,
    *,
    label: str,
    identity: Mapping[str, Any],
) -> list[list[str]]:
    if len(payload) != identity["bytes"] or _sha256(payload) != identity["sha256"]:
        raise Update606PackageInputError(f"{label} package identity mismatch")
    try:
        decoded = base64.b64decode(payload, validate=True)
        text = decoded.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise Update606PackageInputError(f"{label} is not exact base64 UTF-8") from exc
    if _sha256(decoded) != identity["decoded_sha256"]:
        raise Update606PackageInputError(f"{label} decoded identity mismatch")
    rows = list(iter_table_rows(text))
    if len(rows) != identity["rows"] or assert_uniform_columns(rows) != identity["columns"]:
        raise Update606PackageInputError(f"{label} row/column identity mismatch")
    return rows


@dataclass(frozen=True)
class LegacyDistributionScope:
    package_generation: str
    legacy_baseline_id: str
    policy: str
    allowed_map_ids: tuple[int, ...]
    new_only_map_ids: tuple[int, ...]
    changed_route_map_ids: tuple[int, ...]
    legacy_only_map_ids: tuple[int, ...]

    def as_receipt(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema"] = "bidking_update606_legacy_distribution_scope_v1"
        value["allowed_map_ids_sha256"] = _ids_sha256(self.allowed_map_ids)
        value["new_only_map_ids_sha256"] = _ids_sha256(self.new_only_map_ids)
        value["changed_route_map_ids_sha256"] = _ids_sha256(
            self.changed_route_map_ids
        )
        value["legacy_only_map_ids_sha256"] = _ids_sha256(
            self.legacy_only_map_ids
        )
        return value


def _route_identity(row: list[str]) -> tuple[str, str]:
    # sub_pool_weights and auction_rounds_rate jointly define the route into
    # the distribution tree.  Item associations, map_group and parent ids do
    # not create distribution authority.
    return row[9], row[17]


def derive_legacy_distribution_scope(
    *,
    legacy_bid_map_package: bytes,
    update606_bid_map_package: bytes,
    legacy_drop_package: bytes,
) -> LegacyDistributionScope:
    legacy_rows = _decode_exact_package_table(
        legacy_bid_map_package,
        label="legacy BidMap",
        identity=LEGACY_PACKAGE_BID_MAP,
    )
    update_rows = _decode_exact_package_table(
        update606_bid_map_package,
        label="update606 BidMap",
        identity=UPDATE606_PACKAGE_BID_MAP,
    )
    # Drop is intentionally not parsed into new routes.  Exact bytes establish
    # only the legacy input generation; the map scope below controls use.
    if (
        len(legacy_drop_package) != LEGACY_PACKAGE_DROP["bytes"]
        or _sha256(legacy_drop_package) != LEGACY_PACKAGE_DROP["sha256"]
    ):
        raise Update606PackageInputError("legacy Drop package identity mismatch")
    try:
        decoded_drop = base64.b64decode(legacy_drop_package, validate=True)
    except ValueError as exc:
        raise Update606PackageInputError("legacy Drop is not exact base64") from exc
    if _sha256(decoded_drop) != LEGACY_PACKAGE_DROP["decoded_sha256"]:
        raise Update606PackageInputError("legacy Drop decoded identity mismatch")

    legacy = {int(row[0]): row for row in legacy_rows}
    update = {int(row[0]): row for row in update_rows}
    if len(legacy) != len(legacy_rows) or len(update) != len(update_rows):
        raise Update606PackageInputError("BidMap duplicate id")
    new_only = tuple(sorted(set(update) - set(legacy)))
    legacy_only = tuple(sorted(set(legacy) - set(update)))
    allowed = tuple(
        sorted(
            map_id
            for map_id in set(legacy) & set(update)
            if map_id not in {5506, 6001}
            and _route_identity(legacy[map_id]) == _route_identity(update[map_id])
        )
    )
    changed = tuple(sorted((set(legacy) & set(update)) - set(allowed)))
    if (
        len(allowed) != 143
        or _ids_sha256(allowed) != LEGACY_ALLOWED_MAP_IDS_SHA256
        or len(new_only) != 63
        or _ids_sha256(new_only) != LEGACY_NEW_ONLY_MAP_IDS_SHA256
        or len(changed) != 22
        or _ids_sha256(changed) != LEGACY_CHANGED_ROUTE_MAP_IDS_SHA256
        or len(legacy_only) != 0
        or _ids_sha256(legacy_only) != LEGACY_ONLY_MAP_IDS_SHA256
        or 5506 not in changed
        or 6001 not in new_only
        or not set(range(5001, 5011)).issubset(new_only)
        or not set(range(5101, 5111)).issubset(new_only)
    ):
        raise Update606PackageInputError("legacy map allowlist identity mismatch")
    return LegacyDistributionScope(
        package_generation=BID_MAP_UPDATE606_GENERATION,
        legacy_baseline_id=LEGACY_PACKAGE_BASELINE_ID,
        policy=LEGACY_DISTRIBUTION_POLICY,
        allowed_map_ids=allowed,
        new_only_map_ids=new_only,
        changed_route_map_ids=changed,
        legacy_only_map_ids=legacy_only,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Update606PackageInputError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _filter_map_document(
    value: Any,
    *,
    scope: LegacyDistributionScope,
    container: str | None,
) -> tuple[dict[str, Any], int]:
    if value.__class__ is not dict:
        raise Update606PackageInputError("legacy map payload must be an exact object")
    root = dict(value)
    maps = root.get(container) if container else root
    if maps.__class__ is not dict:
        raise Update606PackageInputError("legacy map payload container is malformed")
    allowed = set(scope.allowed_map_ids)
    filtered: dict[str, Any] = {}
    for raw_map_id, entry in maps.items():
        if (
            raw_map_id.__class__ is not str
            or not raw_map_id.isascii()
            or not raw_map_id.isdigit()
        ):
            raise Update606PackageInputError("legacy map payload key is malformed")
        map_id = int(raw_map_id)
        if map_id in allowed:
            filtered[raw_map_id] = entry
    if any(
        map_id
        in set(scope.new_only_map_ids)
        | set(scope.changed_route_map_ids)
        | set(scope.legacy_only_map_ids)
        for map_id in map(int, filtered)
    ):
        raise Update606PackageInputError("unauthorized map survived carry-forward")
    if container:
        root[container] = filtered
        return root, len(filtered)
    return filtered, len(filtered)


def _reject_historical_weight_items(value: dict[str, Any]) -> None:
    denied = {str(item_id) for item_id in UPDATE606_HISTORICAL_ONLY_ITEM_IDS}
    for map_id, entry in value.items():
        if entry.__class__ is not dict:
            continue
        item_weights = entry.get("items")
        if item_weights.__class__ is dict and denied & set(item_weights):
            raise Update606PackageInputError(
                f"historical-only item entered drop weights for map_id={map_id}"
            )


def carry_forward_legacy_map_payload(
    name: str,
    payload: bytes,
    *,
    scope: LegacyDistributionScope,
) -> tuple[bytes, dict[str, Any]]:
    contract = LEGACY_MAP_PAYLOADS.get(name)
    if contract is None:
        raise Update606PackageInputError(f"unknown legacy map payload: {name}")
    if (
        len(payload) != contract["source_bytes"]
        or _sha256(payload) != contract["source_sha256"]
    ):
        raise Update606PackageInputError(f"{name} source identity mismatch")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(f"{name} is not strict UTF-8 JSON") from exc
    filtered, map_count = _filter_map_document(
        value,
        scope=scope,
        container=contract["container"],
    )
    if name == "drop_weights_all_maps_0627":
        _reject_historical_weight_items(filtered)
    kwargs = (
        {"ensure_ascii": False, "separators": (",", ":")}
        if contract["render"] == "compact"
        else {"ensure_ascii": False}
    )
    rendered = json.dumps(filtered, **kwargs).encode("utf-8")
    if (
        map_count != contract["output_maps"]
        or len(rendered) != contract["output_bytes"]
        or _sha256(rendered) != contract["output_sha256"]
    ):
        raise Update606PackageInputError(f"{name} filtered output identity mismatch")
    receipt = {
        "schema": "bidking_update606_legacy_map_payload_receipt_v1",
        "name": name,
        "package_generation": scope.package_generation,
        "policy": scope.policy,
        "source": {
            "bytes": len(payload),
            "sha256": _sha256(payload),
        },
        "output": {
            "bytes": len(rendered),
            "maps": map_count,
            "sha256": _sha256(rendered),
        },
        "excluded_map_ids_sha256": _sha256(
            json.dumps(
                sorted(
                    set(scope.new_only_map_ids)
                    | set(scope.changed_route_map_ids)
                    | set(scope.legacy_only_map_ids)
                ),
                separators=(",", ":"),
            ).encode("ascii")
        ),
    }
    return rendered, receipt


__all__ = (
    "LEGACY_DISTRIBUTION_POLICY",
    "LEGACY_MAP_PAYLOADS",
    "LEGACY_ITEMS_CATALOG",
    "LEGACY_PACKAGE_BASELINE_ID",
    "LEGACY_PACKAGE_BID_MAP",
    "LEGACY_PACKAGE_DROP",
    "LegacyDistributionScope",
    "UPDATE606_AUXILIARY_PACKAGE_TABLES",
    "UPDATE606_AUXILIARY_TEXT_ASSETS",
    "UPDATE606_PACKAGE_BID_MAP",
    "UPDATE606_PACKAGE_ITEM",
    "UPDATE606_ITEMS_CATALOG",
    "Update606PackageInputError",
    "build_update606_auxiliary_package_table",
    "build_update606_package_table",
    "build_update606_items_catalog",
    "carry_forward_legacy_map_payload",
    "derive_legacy_distribution_scope",
)
