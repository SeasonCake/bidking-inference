"""Exact release-input contract for the current official game generation.

This module binds the five authoritative TextAssets present in the official
``d66aee-2026-08-20-599`` tables bundle.  It deliberately does *not* create a
Drop/DropMap contract: neither asset exists in that exact package manifest or
bundle, so Item map associations are not distribution or opening evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from bidking_lab.extract.bid_map_table import (
    BID_MAP_UPDATE606_CANONICAL_SHA256,
    BID_MAP_UPDATE606_COLUMN_NAMES,
    parse_bid_map_table,
)


UPDATE606_STEAM_BUILD_ID = "24830618"
UPDATE606_PACKAGE_GENERATION = "d66aee-2026-08-20-599"
UPDATE606_MANIFEST_SHA256 = (
    "7973b7ea036392da4bbab7d900c216c83a54da01488f1bc16ae0f59a68a0bd4d"
)
UPDATE606_TABLES_BUNDLE_MD5 = "2695e7dc3c1568c03efebc883d0918c6"
UPDATE606_TABLES_BUNDLE_SHA256 = (
    "27cf1023d708d408d5af8f113f23dcb1370b8acd96b3bfea33d0880a8d8a1528"
)
UPDATE606_TABLES_BUNDLE_SIZE = 3_239_960
UPDATE606_SCRIPTS_SHA256 = (
    "4bb6e3edd5b1d75086d05ef236a536a854012293160262e725daa15bcef8b36b"
)
UPDATE606_SCRIPTS_SIZE = 5_538_816


@dataclass(frozen=True)
class TextAssetIdentity:
    rows: int
    columns: int
    utf8_size: int
    utf8_sha256: str


UPDATE606_TEXT_ASSET_IDENTITIES: Mapping[str, TextAssetIdentity] = {
    "Activity": TextAssetIdentity(
        rows=20,
        columns=19,
        utf8_size=2_829,
        utf8_sha256=(
            "a67d0c583b67a079d3ac275be72482f5e9a87bd1e9dc3a53816ee8a8bb21f333"
        ),
    ),
    "ActivityMap": TextAssetIdentity(
        rows=11,
        columns=18,
        utf8_size=2_159,
        utf8_sha256=(
            "e4c9642efaffc44ca4bb6b48a99d89f8ea1d0c3b7ec66dc0a717b5a887b373f8"
        ),
    ),
    "BidMap": TextAssetIdentity(
        rows=228,
        columns=28,
        utf8_size=96_559,
        utf8_sha256=(
            "42f8fb1e2545d0ae43ee6ebe5bc059eba006757f592db40b8d4f88822adc3711"
        ),
    ),
    "Item": TextAssetIdentity(
        rows=1_336,
        columns=41,
        utf8_size=481_130,
        utf8_sha256=(
            "00ae68117de8923a3b50e3d0194dd8cac3267ab70404d410240a3ef75269e0b3"
        ),
    ),
    "Map": TextAssetIdentity(
        rows=12,
        columns=24,
        utf8_size=2_130,
        utf8_sha256=(
            "d2137db5bd99d11a5db85014ad5fa72c36260b2266ccc3ca898488aa6e934e1b"
        ),
    ),
}

# No exact Drop/DropMap TextAsset exists in this official manifest/bundle.
UPDATE606_DISTRIBUTION_AUTHORITY = "absent_fail_closed"


def _workflow_input_payload() -> dict[str, object]:
    return {
        "schema": "bidking_update606_release_inputs_v1",
        "steam_build_id": UPDATE606_STEAM_BUILD_ID,
        "package_generation": UPDATE606_PACKAGE_GENERATION,
        "manifest_sha256": UPDATE606_MANIFEST_SHA256,
        "tables_bundle_md5": UPDATE606_TABLES_BUNDLE_MD5,
        "tables_bundle_sha256": UPDATE606_TABLES_BUNDLE_SHA256,
        "tables_bundle_size": UPDATE606_TABLES_BUNDLE_SIZE,
        "scripts_sha256": UPDATE606_SCRIPTS_SHA256,
        "scripts_size": UPDATE606_SCRIPTS_SIZE,
        "bid_map_column_names": list(BID_MAP_UPDATE606_COLUMN_NAMES),
        "text_assets": {
            name: asdict(identity)
            for name, identity in sorted(UPDATE606_TEXT_ASSET_IDENTITIES.items())
        },
        "distribution_authority": UPDATE606_DISTRIBUTION_AUTHORITY,
    }


def _payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


UPDATE606_WORKFLOW_INPUT_FINGERPRINT = _payload_sha256(
    _workflow_input_payload()
)


@dataclass(frozen=True)
class Update606ReleaseContractReceipt:
    package_generation: str
    workflow_input_fingerprint: str
    bid_map_canonical_sha256: str
    bid_map_column_names: tuple[str, ...]
    table_rows: Mapping[str, int]
    distribution_authority: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "bidking_update606_release_contract_receipt_v1",
            **asdict(self),
        }


def _rows_from_exact_text(name: str, value: object) -> list[list[str]]:
    if type(value) is not str:  # noqa: E721 - no implicit bytes/object coercion
        raise ValueError(f"update606 {name} TextAsset must be an exact string")
    raw = value.encode("utf-8")
    identity = UPDATE606_TEXT_ASSET_IDENTITIES[name]
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if len(raw) != identity.utf8_size or observed_sha256 != identity.utf8_sha256:
        raise ValueError(
            f"update606 {name} TextAsset identity mismatch: "
            f"size={len(raw)}, sha256={observed_sha256}"
        )
    lines = value.splitlines()
    if len(lines) != identity.rows:
        raise ValueError(
            f"update606 {name} requires {identity.rows} rows, got {len(lines)}"
        )
    rows = [line.split("\t") for line in lines]
    if rows and rows[0] and rows[0][0].startswith("\ufeff"):
        rows[0][0] = rows[0][0][1:]
    for index, row in enumerate(rows):
        if len(row) != identity.columns:
            raise ValueError(
                f"update606 {name} row[{index}] requires "
                f"{identity.columns} columns, got {len(row)}"
            )
    return rows


def update606_text_asset_rows(name: str, value: object) -> list[list[str]]:
    """Return rows only after one TextAsset matches its exact official identity."""

    if name not in UPDATE606_TEXT_ASSET_IDENTITIES:
        raise ValueError(f"unknown update606 TextAsset: {name!r}")
    return _rows_from_exact_text(name, value)


def _indexed(rows: list[list[str]], *, name: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for index, row in enumerate(rows):
        try:
            row_id = int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"update606 {name} row[{index}] id is invalid") from exc
        if row_id in out:
            raise ValueError(f"update606 {name} duplicate id={row_id}")
        out[row_id] = row
    return out


def _json_exact(value: str, *, context: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"update606 {context} is not exact JSON") from exc


def _validate_bid_map(rows: list[list[str]]) -> None:
    parsed = parse_bid_map_table(rows)
    if len(parsed) != 228:
        raise ValueError("update606 BidMap parsed identity mismatch")
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    if canonical_sha256 != BID_MAP_UPDATE606_CANONICAL_SHA256:
        raise ValueError(
            "update606 BidMap canonical identity mismatch: "
            f"{canonical_sha256}"
        )
    indexed = _indexed(rows, name="BidMap")
    if indexed[5506][7] != "501" or indexed[6001][7] != "601":
        raise ValueError("update606 BidMap 5506/6001 parent-map binding mismatch")
    if {row[11] for row in rows} != {"10", "15", "20", "25", "30"}:
        raise ValueError("update606 BidMap item_cost domain mismatch")
    raven_ids = set(range(5101, 5111))
    if not raven_ids.issubset(indexed):
        raise ValueError("current BidMap Raven 510x scope is incomplete")
    if parsed[5101].sub_pool_weights != [
        (5101, 10),
        (5102, 10),
        (5102, 10),
        (5103, 10),
        (5104, 10),
        (5105, 10),
        (5106, 10),
        (5107, 10),
        (5108, 10),
        (5109, 10),
        (5110, 10),
    ]:
        raise ValueError("current BidMap 5101 duplicate weight identity mismatch")
    for row_id in raven_ids:
        row = indexed[row_id]
        if (
            row[7] != "501"
            or _json_exact(
                row[17], context=f"BidMap[{row_id}].auction_rounds_rate"
            ) != [9999, row_id - 100, 22, 44]
            or _json_exact(
                row[20], context=f"BidMap[{row_id}].round_skill_group_ids"
            ) != [30105, 0, 0, 0, 0]
        ):
            raise ValueError(f"current BidMap[{row_id}] Raven identity mismatch")
    # 2501..2510 / 4501..4510 already carried their parent group.  update606
    # fills the formerly-empty group on the remaining 80 activity rows, so
    # the final same-generation domain is the complete two 50-map blocks.
    open_activity_ids = set(range(2501, 2551))
    sealed_activity_ids = set(range(4501, 4551))
    if {
        row_id for row_id, row in indexed.items() if row[7] == "105"
    } != open_activity_ids:
        raise ValueError("update606 BidMap map_group=105 identity mismatch")
    if {
        row_id for row_id, row in indexed.items() if row[7] == "305"
    } != sealed_activity_ids:
        raise ValueError("update606 BidMap map_group=305 identity mismatch")
    for row_id in range(2531, 2541):
        expected = [9999, row_id, 34, 55]
        if _json_exact(
            indexed[row_id][17], context=f"BidMap[{row_id}].auction_rounds_rate"
        ) != expected:
            raise ValueError(
                f"update606 BidMap[{row_id}] auction_rounds_rate mismatch"
            )
    for row_id in range(4531, 4541):
        expected = [9999, row_id - 2000, 34, 55]
        if _json_exact(
            indexed[row_id][17], context=f"BidMap[{row_id}].auction_rounds_rate"
        ) != expected:
            raise ValueError(
                f"update606 BidMap[{row_id}] auction_rounds_rate mismatch"
            )


def _validate_activity_map(rows: list[list[str]]) -> None:
    indexed = _indexed(rows, name="ActivityMap")
    expected = {
        10102: (
            "暗拍沉船白色降红色升",
            [4521, 4522, 4523, 4524, 4545, 4526, 4527, 4528, 4529, 4530],
        ),
        10103: ("暗拍沉船数量up", list(range(4531, 4541))),
    }
    for row_id, (label, pool) in expected.items():
        row = indexed[row_id]
        if row[8] != label:
            raise ValueError(f"update606 ActivityMap[{row_id}] effect mismatch")
        if _json_exact(
            row[12], context=f"ActivityMap[{row_id}].bid_map_pool"
        ) != pool:
            raise ValueError(f"update606 ActivityMap[{row_id}] pool mismatch")
    if _json_exact(
        indexed[10003][12], context="ActivityMap[10003].bid_map_pool"
    ) != list(range(2531, 2541)):
        raise ValueError("update606 ActivityMap[10003] pool mismatch")
    if _json_exact(
        indexed[20001][1], context="ActivityMap[20001].map_id"
    ) != [501] or _json_exact(
        indexed[20001][12], context="ActivityMap[20001].bid_map_pool"
    ) != [5506]:
        raise ValueError("update606 ActivityMap 501 -> 5506 binding mismatch")
    if _json_exact(
        indexed[30001][1], context="ActivityMap[30001].map_id"
    ) != [601]:
        raise ValueError("update606 ActivityMap parent 601 binding mismatch")
    large_auction_pool = _json_exact(
        indexed[30001][12], context="ActivityMap[30001].bid_map_pool"
    )
    if large_auction_pool != [4541] or 6001 in large_auction_pool:
        raise ValueError(
            "update606 ActivityMap 601 route must not imply 6001 opening"
        )
    if (
        _json_exact(indexed[20002][1], context="ActivityMap[20002].map_id")
        != [501]
        or _json_exact(
            indexed[20002][5], context="ActivityMap[20002].hero"
        ) != [[3, 301]]
        or _json_exact(
            indexed[20002][12], context="ActivityMap[20002].bid_map_pool"
        ) != [5101]
    ):
        raise ValueError("current ActivityMap 20002 Raven binding mismatch")


def _validate_activity(rows: list[list[str]]) -> None:
    indexed = _indexed(rows, name="Activity")
    if _json_exact(indexed[30002][18], context="Activity[30002].ms_idMap") != [
        10002,
        10102,
    ]:
        raise ValueError("update606 Activity 10102 effect route mismatch")
    if _json_exact(indexed[30003][18], context="Activity[30003].ms_idMap") != [
        10003,
        10103,
    ]:
        raise ValueError("update606 Activity 10103 effect route mismatch")
    if _json_exact(indexed[40001][16], context="Activity[40001].path") != [2, 501]:
        raise ValueError("update606 Activity 40001 -> parent 501 mismatch")
    if _json_exact(
        indexed[40001][18], context="Activity[40001].ms_idMap"
    ) != [20001]:
        raise ValueError("update606 Activity 40001 -> ActivityMap 20001 mismatch")
    if _json_exact(
        indexed[50001][18], context="Activity[50001].ms_idMap"
    ) != [30001]:
        raise ValueError("update606 Activity 50001 -> ActivityMap 30001 mismatch")
    if (
        _json_exact(indexed[40002][16], context="Activity[40002].path")
        != [2, 501]
        or _json_exact(
            indexed[40002][18], context="Activity[40002].ms_idMap"
        ) != [20002]
    ):
        raise ValueError("current Activity 40002 -> ActivityMap 20002 mismatch")


def _validate_map(rows: list[list[str]]) -> None:
    indexed = _indexed(rows, name="Map")
    if indexed[501][17] != "501" or indexed[601][17] != "601":
        raise ValueError("update606 Map 501/601 self identity mismatch")
    if indexed[501][14] != "1" or indexed[601][14] != "1":
        raise ValueError("update606 Map 501/601 activity type mismatch")


def _validate_item(rows: list[list[str]]) -> None:
    associated = []
    for row in rows:
        raw_map_ids = _json_exact(row[39], context=f"Item[{row[0]}].ms_idMap")
        if not isinstance(raw_map_ids, list):
            raise ValueError(f"update606 Item[{row[0]}].ms_idMap is not a list")
        if 501 in raw_map_ids or 601 in raw_map_ids:
            if not ({501, 601}.issubset(raw_map_ids)):
                raise ValueError(
                    f"update606 Item[{row[0]}] has mixed 501/601 association"
                )
            associated.append(int(row[0]))
    if len(associated) != 666:
        raise ValueError(
            "update606 Item 501/601 association count mismatch: "
            f"{len(associated)}"
        )
    indexed = _indexed(rows, name="Item")
    added = {190001, 190002, 200001, 200002}
    if not added.issubset(indexed) or any(
        indexed[item_id][8] != "0" or indexed[item_id][9] != "0"
        for item_id in added
    ):
        raise ValueError("current Item quality-zero cosmetic identity mismatch")


def validate_update606_release_contract(
    text_assets: Mapping[str, object],
    *,
    package_generation: object,
    manifest_sha256: object,
    tables_bundle_sha256: object,
    scripts_sha256: object,
) -> Update606ReleaseContractReceipt:
    """Validate one same-generation five-table input set and semantic canaries."""

    exact_metadata = {
        "package_generation": (package_generation, UPDATE606_PACKAGE_GENERATION),
        "manifest_sha256": (manifest_sha256, UPDATE606_MANIFEST_SHA256),
        "tables_bundle_sha256": (
            tables_bundle_sha256,
            UPDATE606_TABLES_BUNDLE_SHA256,
        ),
        "scripts_sha256": (scripts_sha256, UPDATE606_SCRIPTS_SHA256),
    }
    for name, (observed, expected) in exact_metadata.items():
        if type(observed) is not str or observed.lower() != expected:  # noqa: E721
            raise ValueError(f"update606 {name} identity mismatch")

    required = set(UPDATE606_TEXT_ASSET_IDENTITIES)
    observed = set(text_assets) if isinstance(text_assets, Mapping) else set()
    if observed != required:
        raise ValueError(
            "update606 TextAsset set mismatch: "
            f"missing={sorted(required - observed)!r}, "
            f"extra={sorted(observed - required)!r}"
        )

    rows = {
        name: _rows_from_exact_text(name, text_assets[name])
        for name in sorted(required)
    }
    _validate_bid_map(rows["BidMap"])
    _validate_activity_map(rows["ActivityMap"])
    _validate_activity(rows["Activity"])
    _validate_map(rows["Map"])
    _validate_item(rows["Item"])

    return Update606ReleaseContractReceipt(
        package_generation=UPDATE606_PACKAGE_GENERATION,
        workflow_input_fingerprint=UPDATE606_WORKFLOW_INPUT_FINGERPRINT,
        bid_map_canonical_sha256=BID_MAP_UPDATE606_CANONICAL_SHA256,
        bid_map_column_names=BID_MAP_UPDATE606_COLUMN_NAMES,
        table_rows={
            name: UPDATE606_TEXT_ASSET_IDENTITIES[name].rows
            for name in sorted(required)
        },
        distribution_authority=UPDATE606_DISTRIBUTION_AUTHORITY,
    )


def load_update606_text_asset_directory(path: Path) -> dict[str, str]:
    """Read the five previously extracted TextAssets without fallback paths."""

    root = path.resolve(strict=True)
    out: dict[str, str] = {}
    for name in sorted(UPDATE606_TEXT_ASSET_IDENTITIES):
        candidate = root / f"{name}.txt"
        raw = candidate.read_bytes()
        try:
            out[name] = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"update606 {name} is not UTF-8") from exc
    return out


__all__ = (
    "BID_MAP_UPDATE606_COLUMN_NAMES",
    "TextAssetIdentity",
    "UPDATE606_DISTRIBUTION_AUTHORITY",
    "UPDATE606_MANIFEST_SHA256",
    "UPDATE606_PACKAGE_GENERATION",
    "UPDATE606_WORKFLOW_INPUT_FINGERPRINT",
    "UPDATE606_SCRIPTS_SHA256",
    "UPDATE606_STEAM_BUILD_ID",
    "UPDATE606_TABLES_BUNDLE_MD5",
    "UPDATE606_TABLES_BUNDLE_SHA256",
    "UPDATE606_TEXT_ASSET_IDENTITIES",
    "Update606ReleaseContractReceipt",
    "load_update606_text_asset_directory",
    "update606_text_asset_rows",
    "validate_update606_release_contract",
)
