#!/usr/bin/env python3
"""Read-only intake for BidKing 0.3.4 official content generations.

This tool is deliberately pre-authority. It inventories one extracted official
input root, matches only exact generation-indexed frozen profiles, and reports
unknown generations as observed-unfrozen even when their build/date/table shape
resembles a known profile. It projects only structural S2/activity/map-6001
facts and never creates a BuildInputBundle receipt, distribution authority,
quote, build, or server state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_APP_MANIFEST,
    CURRENT_RANKMAP_MAP_IDS,
)
from bidking_lab.extract.bid_map_table import (  # noqa: E402
    BID_MAP_OFFICIAL_20260903_GENERATION,
    BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION,
    BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
    BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
    BID_MAP_OFFICIAL_20260903_RAW_TEXT_BYTES,
    BID_MAP_OFFICIAL_20260903_RAW_TEXT_SHA256,
)
from bidking_lab.extract.item_table import (  # noqa: E402
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES,
    ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256,
)


SCHEMA = "bidking_0_3_4_official_update_intake_v2"
MAX_INPUT_BYTES = 128 * 1024 * 1024
ARTIFACT_NAMES = (
    "BuildinCatalog.bytes",
    "BuildinCatalog.json",
    "manifest.bytes",
    "tables.bundle",
    "Scripts.dll",
)


class FrozenTextAssetIdentity:
    __slots__ = ("rows", "columns", "utf8_size", "utf8_sha256")

    def __init__(
        self,
        rows: int,
        columns: int,
        utf8_size: int,
        utf8_sha256: str,
    ) -> None:
        self.rows = rows
        self.columns = columns
        self.utf8_size = utf8_size
        self.utf8_sha256 = utf8_sha256


class FrozenGenerationProfile:
    __slots__ = ("steam_build_id", "text_assets", "artifacts")

    def __init__(
        self,
        *,
        steam_build_id: str,
        text_assets: Mapping[str, object],
        artifacts: Mapping[str, Mapping[str, object]],
    ) -> None:
        self.steam_build_id = steam_build_id
        self.text_assets = text_assets
        self.artifacts = artifacts


PREVIOUS_0_3_4_STEAM_BUILD_ID = "24962364"
PREVIOUS_0_3_4_PACKAGE_GENERATION = "4ae819-2026-08-27-665"
PREVIOUS_0_3_4_TEXT_ASSET_IDENTITIES: Mapping[
    str, FrozenTextAssetIdentity
] = {
    "Activity": FrozenTextAssetIdentity(
        26,
        19,
        3_763,
        "12217a85182fda813cbf37a21c7e6ec0fc6dd298e45078ba377df5e2aed1e564",
    ),
    "ActivityMap": FrozenTextAssetIdentity(
        27,
        18,
        5_337,
        "414123d8540049fd1b8708777ea748e5b96914490753a897f2bea05c1bde7924",
    ),
    "BattleItem": FrozenTextAssetIdentity(
        64,
        6,
        4_468,
        "fc8c5231c723a80d4e338069aa09e32c32a5f4a3d5cc05f5e5d3e8094c844846",
    ),
    "BidMap": FrozenTextAssetIdentity(
        229,
        28,
        97_231,
        "7d4d810b63441a462c486a1e308fd7a1db93712de2713cfe3e5943383f8bc006",
    ),
    "Hero": FrozenTextAssetIdentity(
        21,
        21,
        9_349,
        "ae7997405c26bfe0b776d22ac0f3c5abb229075453d645668beaa2e0654342ac",
    ),
    "Item": FrozenTextAssetIdentity(
        1_336,
        41,
        479_855,
        "7ac11867652b21ebbd62461cd95da84addfe721dea5c01307e4dd568fd60933c",
    ),
    "Map": FrozenTextAssetIdentity(
        12,
        24,
        2_038,
        "d7741e6a57af91cd5467f047a77bab3b367e6554862ecc1127c9e8d5d488a714",
    ),
    "RankMap": FrozenTextAssetIdentity(
        176,
        7,
        88_931,
        "30cafbffea27bc8ed029b74bae12caba50bd8e3ead4986e2ea46546d81aee13a",
    ),
}
PREVIOUS_0_3_4_ARTIFACT_IDENTITIES: Mapping[str, Mapping[str, object]] = {
    "DefaultPackage.version": {
        "bytes": 21,
        "sha256": "d562da15785eb4164e2524783d42641e80ca0554fb9b73b8abc67f701ec1a8fb",
    },
    f"DefaultPackage_{PREVIOUS_0_3_4_PACKAGE_GENERATION}.hash": {
        "bytes": 8,
        "sha256": "75d02a9115e55ef23918f877660d55cd17bb21151698c5acd45d445a8302e941",
    },
    "BuildinCatalog.bytes": {
        "bytes": 32_529,
        "sha256": "d01d382d916497eff5f3d1e2707e1b74ff7cabf42cd6d7639e19d2a57f5e290e",
    },
    "BuildinCatalog.json": {
        "bytes": 64_655,
        "sha256": "5cf4b13e542a48976e5e2c731115e8cf8e342188a08118a7c0d36dcb17785b2e",
    },
    "manifest.bytes": {
        "bytes": 334_378,
        "sha256": "02eeb15fa6a29aebaa200c23b6b7cd462676efaf0d586208a1a2d57062f35be2",
    },
    "tables.bundle": {
        "bytes": 3_265_778,
        "sha256": "4ebb59711f38e5ec84a91483ef9629e0eb27455e279d326ca6d1bd946aa24adf",
    },
    "Scripts.dll": {
        "bytes": 5_691_392,
        "sha256": "3b816baf8aa0fff15acaabe79633f3828aec0cacc55bb12d1631d560f9a5fffb",
    },
}


OFFICIAL_20260903_STEAM_BUILD_ID = "25094235"
OFFICIAL_20260903_PACKAGE_GENERATION = BID_MAP_OFFICIAL_20260903_GENERATION
OFFICIAL_20260903_TEXT_ASSET_IDENTITIES: Mapping[
    str, FrozenTextAssetIdentity
] = {
    "Activity": FrozenTextAssetIdentity(
        44,
        19,
        6_580,
        "9fd3f8b946cef0f9cd5228488a3a4331f0c877ccbdfc968a68372d359a47bb3c",
    ),
    "ActivityMap": FrozenTextAssetIdentity(
        31,
        20,
        8_813,
        "6112d32662a68b1d70069a3c7d002c3cbc85c921fcd5e12d5e91927de42dfbb4",
    ),
    "BattleItem": FrozenTextAssetIdentity(
        64,
        6,
        4_468,
        "fc8c5231c723a80d4e338069aa09e32c32a5f4a3d5cc05f5e5d3e8094c844846",
    ),
    "BidMap": FrozenTextAssetIdentity(
        246,
        28,
        BID_MAP_OFFICIAL_20260903_RAW_TEXT_BYTES,
        BID_MAP_OFFICIAL_20260903_RAW_TEXT_SHA256,
    ),
    "Hero": FrozenTextAssetIdentity(
        22,
        21,
        9_879,
        "2d22ce736b5cd2ca1a4bb87b4830045a340684a5f0dc2e216da2672f029935d7",
    ),
    "Item": FrozenTextAssetIdentity(
        1_427,
        42,
        ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES,
        ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256,
    ),
    "Map": FrozenTextAssetIdentity(
        12,
        27,
        2_583,
        "8c703f62829259be2ba64be3126f00aa56f775e613a6ed22f268f44a54b33647",
    ),
    "RankMap": FrozenTextAssetIdentity(
        176,
        7,
        88_931,
        "30cafbffea27bc8ed029b74bae12caba50bd8e3ead4986e2ea46546d81aee13a",
    ),
}
OFFICIAL_20260903_ARTIFACT_IDENTITIES: Mapping[str, Mapping[str, object]] = {
    "DefaultPackage.version": {
        "bytes": 21,
        "sha256": "b34e83cfe07d5a8dfde27ac4766a10a203602a7e93e58e93f6354bfab3c112a3",
    },
    f"DefaultPackage_{OFFICIAL_20260903_PACKAGE_GENERATION}.hash": {
        "bytes": 8,
        "sha256": "9b3cc5faf68f4597191b1111449abdb17dfd152a10b0b02b3fff6a81ce250b52",
    },
    "BuildinCatalog.bytes": {
        "bytes": 36_579,
        "sha256": "1a9907f74e8bdf8ca787edb36dda4a135f184d1290c5b93d1657075f83fd2d25",
    },
    "BuildinCatalog.json": {
        "bytes": 72_701,
        "sha256": "2e4082ee19ca24b30ec397a7b1b34833117ec3fa8fd777b537df86fb6e433693",
    },
    "manifest.bytes": {
        "bytes": 362_726,
        "sha256": "efac730f6e11fcc54054fd2834480945a96d34f1c88389dc4360c939627146c3",
    },
    "tables.bundle": {
        "bytes": 3_527_580,
        "sha256": "d8d5da4fe559bf9af4fbaff25916e551a5b4cdd28ffecae16270ce9f8499e3c1",
    },
    "Scripts.dll": {
        "bytes": 5_793_280,
        "sha256": "64fda3c0b1aa8602466e2a055d39337c1df607a8b43ce996e30250d59c423740",
    },
}

OFFICIAL_20260903_HOTFIX_STEAM_BUILD_ID = "25096377"
OFFICIAL_20260903_HOTFIX_PACKAGE_GENERATION = (
    BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION
)
OFFICIAL_20260903_HOTFIX_TEXT_ASSET_IDENTITIES: Mapping[
    str, FrozenTextAssetIdentity
] = {
    **OFFICIAL_20260903_TEXT_ASSET_IDENTITIES,
    "BidMap": FrozenTextAssetIdentity(
        246,
        28,
        BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
        BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
    ),
    "Item": FrozenTextAssetIdentity(
        1_427,
        42,
        ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES,
        ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256,
    ),
}
OFFICIAL_20260903_HOTFIX_ARTIFACT_IDENTITIES: Mapping[
    str, Mapping[str, object]
] = {
    "DefaultPackage.version": {
        "bytes": 21,
        "sha256": "ada996c31f1ad90f7a302e1f08d149fee586982381cdf334a97815469916ecaf",
    },
    f"DefaultPackage_{OFFICIAL_20260903_HOTFIX_PACKAGE_GENERATION}.hash": {
        "bytes": 8,
        "sha256": "afc548607f8ccf2b9a1606552820333c595c953c1393f0dda3cab7a19884252e",
    },
    "BuildinCatalog.bytes": {
        "bytes": 36_579,
        "sha256": "f0b8c4836e65a117fbc9853967f5740752031af97a06477c937ab9d89b24f8e3",
    },
    "BuildinCatalog.json": {
        "bytes": 72_701,
        "sha256": "9c8e7907ddd5ce570e54f94542c6745b136dcff3104bf97104ec132fde9c120f",
    },
    "manifest.bytes": {
        "bytes": 362_926,
        "sha256": "4b518d2f3bd15e2893f9f945c87c8e51c28a1a88ee8f13ffcf61e77be192cbd4",
    },
    "tables.bundle": {
        "bytes": 3_528_137,
        "sha256": "da5115d57aac46bd79433116cb45ba319b562c6d2e83b24e6a47bdc95be70208",
    },
    "Scripts.dll": {
        "bytes": 5_808_128,
        "sha256": "2c76aa7194e611a23a38e1931261f2f48d011bf803fc86e1b40b8f458f2d5aca",
    },
}

OFFICIAL_20260910_PACKAGE_GENERATION = "b049c3-2026-09-10-597"
OFFICIAL_20260910_TEXT_ASSET_IDENTITIES: Mapping[str, FrozenTextAssetIdentity] = {
    **OFFICIAL_20260903_HOTFIX_TEXT_ASSET_IDENTITIES,
    "Activity": FrozenTextAssetIdentity(
        44, 19, 6580, "dcb063f062e116fa3383e9294cc465853e20aceb0a683b63b71dd46b76e6d7e9"
    ),
    "ActivityMap": FrozenTextAssetIdentity(
        31, 20, 8817, "b91586436e977a63a6f478a82944c159e7e2a76bf9d0ab48e6ffe857947b83db"
    ),
    "BidMap": FrozenTextAssetIdentity(
        246, 28, 103973, "7e5606adbd50b5e106265757704e87af103a9edaa21922fee061aa117b77e6b7"
    ),
    "Item": FrozenTextAssetIdentity(
        1432, 42, 520572, "8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47"
    ),
    "Map": FrozenTextAssetIdentity(
        12, 27, 2586, "3d0a74f55f5d91e99116f15b1b96270e10a3e3a91db05318a8e6c8ae0cb56b1d"
    ),
}
OFFICIAL_20260910_ARTIFACT_IDENTITIES: Mapping[str, Mapping[str, object]] = {
    "DefaultPackage.version": {
        "bytes": 21, "sha256": "f8cadb9599e42402f3ec6d1445589ba33563d5289a3095d4b004a43a4533d401",
    },
    f"DefaultPackage_{OFFICIAL_20260910_PACKAGE_GENERATION}.hash": {
        "bytes": 8, "sha256": "77012d20eccedaa2564d3e9f6302e988c00356c0375898fcf62872901724d073",
    },
    "BuildinCatalog.bytes": {
        "bytes": 36579, "sha256": "eb02eb015f3e56d55a483a0654ebcc5a88da7e94b708fe63f51e8d31aeaf917e",
    },
    "BuildinCatalog.json": {
        "bytes": 72701, "sha256": "0cd1622266db4a9e5b6b3dba3f2fe7d8c814d7e115a18b1ed22e393f75ae0a0c",
    },
    "manifest.bytes": {
        "bytes": 365256, "sha256": "62f4357a140946b5a33a6f8a7d0a101c33eb95ca393bce002482923d24412e3c",
    },
    "tables.bundle": {
        "bytes": 3533190, "sha256": "581f681a35b01c481c79c4617138e17fa27fff1d23530d482ad8f4cb9eff085c",
    },
    "Scripts.dll": {
        "bytes": 5840896, "sha256": "ee5a86dd3165bc0392b04ecced9f212a1afa2b8b78ff97c4767d97725d2cc744",
    },
}


FROZEN_GENERATION_PROFILES: Mapping[str, FrozenGenerationProfile] = {
    PREVIOUS_0_3_4_PACKAGE_GENERATION: FrozenGenerationProfile(
        steam_build_id=PREVIOUS_0_3_4_STEAM_BUILD_ID,
        text_assets=PREVIOUS_0_3_4_TEXT_ASSET_IDENTITIES,
        artifacts=PREVIOUS_0_3_4_ARTIFACT_IDENTITIES,
    ),
    OFFICIAL_20260903_PACKAGE_GENERATION: FrozenGenerationProfile(
        steam_build_id=OFFICIAL_20260903_STEAM_BUILD_ID,
        text_assets=OFFICIAL_20260903_TEXT_ASSET_IDENTITIES,
        artifacts=OFFICIAL_20260903_ARTIFACT_IDENTITIES,
    ),
    OFFICIAL_20260903_HOTFIX_PACKAGE_GENERATION: FrozenGenerationProfile(
        steam_build_id=OFFICIAL_20260903_HOTFIX_STEAM_BUILD_ID,
        text_assets=OFFICIAL_20260903_HOTFIX_TEXT_ASSET_IDENTITIES,
        artifacts=OFFICIAL_20260903_HOTFIX_ARTIFACT_IDENTITIES,
    ),
    OFFICIAL_20260910_PACKAGE_GENERATION: FrozenGenerationProfile(
        steam_build_id="25221911",
        text_assets=OFFICIAL_20260910_TEXT_ASSET_IDENTITIES,
        artifacts=OFFICIAL_20260910_ARTIFACT_IDENTITIES,
    ),
}
LATEST_FROZEN_PACKAGE_GENERATION = OFFICIAL_20260910_PACKAGE_GENERATION


class OfficialUpdateIntakeError(ValueError):
    """The prospective official input is incomplete, unsafe, or inconsistent."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_regular_read(root: Path, relative: str) -> bytes:
    path = root / Path(relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        before = path.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise OfficialUpdateIntakeError(
            f"missing/unreadable input: {relative}"
        ) from exc
    reparse = bool(
        int(getattr(before, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or reparse
        or int(before.st_nlink) != 1
        or int(before.st_size) < 1
        or int(before.st_size) > MAX_INPUT_BYTES
    ):
        raise OfficialUpdateIntakeError(f"unsafe input leaf: {relative}")
    try:
        raw = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise OfficialUpdateIntakeError(
            f"input changed while reading: {relative}"
        ) from exc
    identity_before = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
    )
    identity_after = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
    )
    if identity_before != identity_after or len(raw) != int(before.st_size):
        raise OfficialUpdateIntakeError(f"input changed while reading: {relative}")
    return raw


def _exact_manifest_field(text: str, name: str) -> str:
    matches = re.findall(
        rf'^\s*"{re.escape(name)}"\s+"([^"]*)"\s*$',
        text,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise OfficialUpdateIntakeError(f"appmanifest {name} is missing/ambiguous")
    return matches[0]


def _appmanifest_identity(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OfficialUpdateIntakeError("appmanifest is not UTF-8") from exc
    if "\x00" in text:
        raise OfficialUpdateIntakeError("appmanifest contains NUL")
    identity = {
        key: _exact_manifest_field(text, key)
        for key in ("appid", "buildid", "installdir")
    }
    if identity["appid"] != CURRENT_APP_MANIFEST["app_id"]:
        raise OfficialUpdateIntakeError("appmanifest appid mismatch")
    if identity["installdir"] != CURRENT_APP_MANIFEST["install_dir"]:
        raise OfficialUpdateIntakeError("appmanifest installdir mismatch")
    if re.fullmatch(r"[0-9]{1,20}", identity["buildid"]) is None:
        raise OfficialUpdateIntakeError("appmanifest buildid is invalid")
    return identity


def _package_generation(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise OfficialUpdateIntakeError("DefaultPackage.version is not UTF-8") from exc
    if (
        re.fullmatch(r"[0-9a-f]{6,40}-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{1,9}", value)
        is None
    ):
        raise OfficialUpdateIntakeError("DefaultPackage.version is malformed")
    return value


def _text_rows(name: str, raw: bytes) -> list[list[str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OfficialUpdateIntakeError(f"{name} TextAsset is not UTF-8") from exc
    rows = [line.split("\t") for line in text.splitlines()]
    if rows and rows[0] and rows[0][0].startswith("\ufeff"):
        rows[0][0] = rows[0][0][1:]
    if not rows or not rows[0]:
        raise OfficialUpdateIntakeError(f"{name} TextAsset is empty")
    columns = len(rows[0])
    if columns < 1 or any(len(row) != columns for row in rows):
        raise OfficialUpdateIntakeError(f"{name} TextAsset columns are inconsistent")
    return rows


def _indexed(name: str, rows: list[list[str]]) -> dict[int, list[str]]:
    indexed: dict[int, list[str]] = {}
    for ordinal, row in enumerate(rows):
        try:
            row_id = int(row[0])
        except (TypeError, ValueError) as exc:
            raise OfficialUpdateIntakeError(
                f"{name} row[{ordinal}] id is invalid"
            ) from exc
        if row_id in indexed:
            raise OfficialUpdateIntakeError(f"{name} duplicate id={row_id}")
        indexed[row_id] = row
    return indexed


def _int_array(
    raw: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> list[int]:
    if allow_empty and raw == "":
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OfficialUpdateIntakeError(f"{label} is not JSON") from exc
    if value.__class__ is not list or any(
        item.__class__ is not int for item in value
    ):
        raise OfficialUpdateIntakeError(f"{label} is not an exact integer array")
    return value


def _row_sha256(row: list[str] | None) -> str | None:
    return _sha256("\t".join(row).encode("utf-8")) if row is not None else None


def _critical_projection(rows: Mapping[str, list[list[str]]]) -> dict[str, Any]:
    bid_map = _indexed("BidMap", rows["BidMap"])
    activity_map = _indexed("ActivityMap", rows["ActivityMap"])
    activity = _indexed("Activity", rows["Activity"])
    rank_map = _indexed("RankMap", rows["RankMap"])

    activity_maps_opening_6001 = sorted(
        row_id
        for row_id, row in activity_map.items()
        if len(row) > 12
        and 6001 in _int_array(
            row[12],
            label=f"ActivityMap[{row_id}].bid_map_pool",
            allow_empty=True,
        )
    )
    activity_routes_opening_6001 = sorted(
        row_id
        for row_id, row in activity.items()
        if len(row) > 18
        and set(
            _int_array(
                row[18],
                label=f"Activity[{row_id}].ms_idMap",
                allow_empty=True,
            )
        ).intersection(activity_maps_opening_6001)
    )
    map_6001 = bid_map.get(6001)
    successor_activity_ids = sorted(
        row_id for row_id in bid_map if 2551 <= row_id <= 2599 or 4551 <= row_id <= 4599
    )
    official_update_activity_ids = sorted(
        row_id for row_id in bid_map if 5203 <= row_id <= 5219
    )
    official_update_ranked_ids = sorted(
        row_id for row_id in bid_map if 5601 <= row_id <= 5611
    )
    current_rankmap_ids = set(CURRENT_RANKMAP_MAP_IDS)
    ranked_scope = sorted(
        row_id for row_id in rank_map if row_id in current_rankmap_ids
    )
    return {
        "rankmap": {
            "rows": len(rank_map),
            "modeled_scope_present_in_table": ranked_scope,
            "modeled_scope_complete_in_table": (
                ranked_scope == sorted(CURRENT_RANKMAP_MAP_IDS)
            ),
            "map_6001_present_in_table": 6001 in rank_map,
            "runtime_availability": "not_established_by_static_intake",
            "runtime_open_claim": False,
        },
        "activity": {
            "successor_candidate_map_ids": successor_activity_ids,
            "official_update_activity_map_ids": official_update_activity_ids,
            "official_update_ranked_map_ids": official_update_ranked_ids,
            "activity_map_rows_routing_6001": activity_maps_opening_6001,
            "activity_rows_routing_6001": activity_routes_opening_6001,
            "runtime_availability": "not_established_by_static_intake",
            "runtime_open_claim": False,
        },
        "map_6001": {
            "recognized": map_6001 is not None,
            "bidmap_parent": (
                map_6001[7] if map_6001 is not None and len(map_6001) > 7 else None
            ),
            "bidmap_row_sha256": _row_sha256(map_6001),
            "static_route_present": bool(activity_routes_opening_6001),
            "runtime_availability": "not_established_by_static_intake",
            "runtime_open_claim": False,
            "distribution_authority": False,
            "quote_available": False,
            "qualification": "structure_only_distribution_contract_required",
        },
    }


def inspect(root: Path) -> dict[str, Any]:
    requested_root = root.absolute()
    root_info = requested_root.stat(follow_symlinks=False)
    root_reparse = bool(
        int(getattr(root_info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_reparse
    ):
        raise OfficialUpdateIntakeError("intake root is unsafe")
    root = requested_root.resolve(strict=True)

    artifact_root = "official/artifacts"
    text_root = "official/textassets"
    app_relative = f"{artifact_root}/appmanifest_{CURRENT_APP_MANIFEST['app_id']}.acf"
    app_raw = _stable_regular_read(root, app_relative)
    app_identity = _appmanifest_identity(app_raw)
    version_raw = _stable_regular_read(root, f"{artifact_root}/DefaultPackage.version")
    generation = _package_generation(version_raw)
    hash_relative = f"{artifact_root}/DefaultPackage_{generation}.hash"

    artifact_files: dict[str, bytes] = {
        "appmanifest": app_raw,
        "DefaultPackage.version": version_raw,
        f"DefaultPackage_{generation}.hash": _stable_regular_read(root, hash_relative),
    }
    for name in ARTIFACT_NAMES:
        artifact_files[name] = _stable_regular_read(root, f"{artifact_root}/{name}")

    selected_profile = FROZEN_GENERATION_PROFILES.get(generation)
    comparison_generation = (
        generation
        if selected_profile is not None
        else LATEST_FROZEN_PACKAGE_GENERATION
    )
    comparison_profile = FROZEN_GENERATION_PROFILES[comparison_generation]
    parsed_rows: dict[str, list[list[str]]] = {}
    text_receipts: dict[str, dict[str, Any]] = {}
    changed_text_assets: list[str] = []
    for name in sorted(comparison_profile.text_assets):
        raw = _stable_regular_read(root, f"{text_root}/{name}.txt")
        rows = _text_rows(name, raw)
        identity = comparison_profile.text_assets[name]
        digest = _sha256(raw)
        matches_comparison_profile = (
            len(raw) == identity.utf8_size
            and digest == identity.utf8_sha256
            and len(rows) == identity.rows
            and len(rows[0]) == identity.columns
        )
        if not matches_comparison_profile:
            changed_text_assets.append(name)
        parsed_rows[name] = rows
        text_receipts[name] = {
            "bytes": len(raw),
            "sha256": digest,
            "rows": len(rows),
            "columns": len(rows[0]),
            "comparison_profile_generation": comparison_generation,
            "matches_comparison_profile": matches_comparison_profile,
            "matches_current_generation": (
                selected_profile is not None
                and generation == LATEST_FROZEN_PACKAGE_GENERATION
                and matches_comparison_profile
            ),
        }

    if (
        selected_profile is not None
        and app_identity["buildid"] != selected_profile.steam_build_id
    ):
        raise OfficialUpdateIntakeError(
            "Steam build and YooAsset package generation are mixed"
        )
    expected_artifacts = comparison_profile.artifacts
    changed_artifacts = sorted(
        name
        for name, identity in expected_artifacts.items()
        if name not in artifact_files
        or len(artifact_files[name]) != identity["bytes"]
        or _sha256(artifact_files[name]) != identity["sha256"]
    )
    if selected_profile is not None and changed_text_assets:
        raise OfficialUpdateIntakeError(
            "frozen generation carries changed TextAsset identities"
        )
    if selected_profile is not None and changed_artifacts:
        raise OfficialUpdateIntakeError(
            "frozen generation carries changed artifact identities"
        )
    matches_latest_profile = (
        selected_profile is not None
        and generation == LATEST_FROZEN_PACKAGE_GENERATION
    )
    if matches_latest_profile:
        state = "current_frozen_input"
    elif selected_profile is not None:
        state = "predecessor_input"
    elif int(app_identity["buildid"]) < int(
        FROZEN_GENERATION_PROFILES[LATEST_FROZEN_PACKAGE_GENERATION].steam_build_id
    ):
        state = "predecessor_input"
    else:
        state = "observed_unfrozen"
    critical = _critical_projection(parsed_rows)
    artifact_receipts = {
        name: {"bytes": len(raw), "sha256": _sha256(raw)}
        for name, raw in sorted(artifact_files.items())
    }
    return {
        "schema": SCHEMA,
        "state": state,
        "authority": "pre_authority_structure_only",
        "matched_frozen_profile": selected_profile is not None,
        "comparison_profile_generation": comparison_generation,
        "latest_frozen_profile_generation": LATEST_FROZEN_PACKAGE_GENERATION,
        "steam": {
            "app_id": app_identity["appid"],
            "build_id": app_identity["buildid"],
            "matches_current_generation": (
                matches_latest_profile
                and app_identity["buildid"] == selected_profile.steam_build_id
            ),
        },
        "package_generation": generation,
        "package_generation_matches_current": matches_latest_profile,
        "artifacts": artifact_receipts,
        "changed_artifacts": changed_artifacts,
        "text_assets": text_receipts,
        "changed_text_assets": changed_text_assets,
        "critical": critical,
        "build_input_bundle": "not_implemented_not_claimed",
        "server_actions": "not_run",
        "next_step": (
            "use_exact_current_generation_contracts"
            if state == "current_frozen_input"
            else (
                "replace_predecessor_with_current_or_later_input"
                if state == "predecessor_input"
                else "freeze_exact_generation_inputs_before_consumer_authority"
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = inspect(args.root)
    except (OfficialUpdateIntakeError, OSError, ValueError) as exc:
        print(f"official-update-intake-not-ready: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 3 if receipt["state"] == "predecessor_input" else 0


if __name__ == "__main__":
    raise SystemExit(main())
