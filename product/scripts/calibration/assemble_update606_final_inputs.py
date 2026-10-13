#!/usr/bin/env python3
"""Assemble one immutable current package/workflow input root.

The current official tables are the semantic authority for Item, BidMap,
RankMap, Map, Hero and BattleItem.  The official bundle has no Drop/DropMap
TextAsset authority, so
the exact legacy Drop and already-approved calibration documents are carried
forward only through the generation-bound stable-route filter.  The approved
680-row ``items_droppable`` document is copied exactly; it is never rebuilt
from the known-incomplete raw Drop graph.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.battle_item_table import parse_battle_item_table  # noqa: E402
from bidking_lab.extract.bid_map_table import parse_bid_map_table  # noqa: E402
from bidking_lab.extract.drop_table import load_drop_table  # noqa: E402
from bidking_lab.extract.hero_table import parse_hero_table  # noqa: E402
from bidking_lab.extract.item_table import parse_item_table  # noqa: E402
from bidking_lab.extract.tables import iter_table_rows  # noqa: E402
from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_APP_MANIFEST,
    CURRENT_BUILDIN_CATALOG_BYTES,
    CURRENT_BUILDIN_CATALOG_JSON,
    CURRENT_MANIFEST,
    CURRENT_PACKAGE_GENERATION,
    CURRENT_PACKAGE_HASH_FILE,
    CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT,
    CURRENT_SCRIPTS,
    CURRENT_TABLES_BUNDLE,
    CURRENT_TEXT_ASSET_IDENTITIES,
    CURRENT_VERSION_FILE,
    build_current_rankmap_distribution,
    build_current_rankmap_package_table,
    validate_current_appmanifest,
    validate_current_release_inputs,
)
from bidking_lab.extract.update606_package_input_contract import (  # noqa: E402
    LEGACY_MAP_PAYLOADS,
    LEGACY_PACKAGE_BID_MAP,
    LEGACY_PACKAGE_DROP,
    UPDATE606_HISTORICAL_ONLY_ITEM_IDS,
    Update606PackageInputError,
    build_update606_auxiliary_package_table,
    build_update606_items_catalog,
    build_update606_package_table,
    derive_legacy_distribution_scope,
)
from bidking_lab.simulation.basic_mc import (  # noqa: E402
    DropPoolResolutionError,
    flatten_pool,
)
from scripts.calibration.build_item_category_authority import (  # noqa: E402
    build_authority_document,
    build_receipt as build_authority_receipt,
    render_authority,
)
from scripts.calibration.carry_forward_update606_legacy_distribution import (  # noqa: E402
    prepare as prepare_legacy_distribution,
)


PACKAGE_GENERATION = CURRENT_PACKAGE_GENERATION
PREVIOUS_GENERATION = "89437c-2026-08-07-745"
LEGACY_UPDATE606_PACKAGE_GENERATION = CURRENT_PACKAGE_GENERATION
PACKAGE_MANIFEST_SHA256 = (
    "d001bec1e19ccaf37b97f618e0aa4c914ceb3a4c742505e5a847b88ea86cd9f6"
)
PACKAGE_BASELINE_ID = "package-data-2026-08-20-0.3.3-hotfix2-raven510x-d66aee"


class FinalInputAssemblyError(ValueError):
    """A source authority or generated package invariant is not exact."""


def _identity(size: int, digest: str, source: str, group: str) -> dict[str, Any]:
    return {
        "bytes": size,
        "sha256": digest,
        "source": source,
        "group": group,
    }


def _semantic_identity(
    *,
    source: str,
    group: str,
    validator: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "group": group,
        "validator": validator,
    }


AUTHORITIES: Mapping[str, Mapping[str, Any]] = {
    "official/artifacts/appmanifest_4128580.acf": _semantic_identity(
        source="official_current_steam_appmanifest",
        group="workflow_release",
        validator="current_steam_appmanifest",
    ),
    "official/artifacts/DefaultPackage.version": _identity(
        CURRENT_VERSION_FILE["bytes"],
        CURRENT_VERSION_FILE["sha256"],
        "official_current_yooasset_version",
        "workflow_release",
    ),
    "official/artifacts/BuildinCatalog.bytes": _identity(
        CURRENT_BUILDIN_CATALOG_BYTES["bytes"],
        CURRENT_BUILDIN_CATALOG_BYTES["sha256"],
        "official_current_yooasset_catalog",
        "workflow_release",
    ),
    "official/artifacts/BuildinCatalog.json": _identity(
        CURRENT_BUILDIN_CATALOG_JSON["bytes"],
        CURRENT_BUILDIN_CATALOG_JSON["sha256"],
        "official_current_yooasset_catalog",
        "workflow_release",
    ),
    "official/artifacts/DefaultPackage_d66aee-2026-08-20-599.hash": _identity(
        CURRENT_PACKAGE_HASH_FILE["bytes"],
        CURRENT_PACKAGE_HASH_FILE["sha256"],
        "official_current_yooasset_package_hash",
        "workflow_release",
    ),
    "official/artifacts/manifest.bytes": _identity(
        CURRENT_MANIFEST["bytes"],
        CURRENT_MANIFEST["sha256"],
        "official_current_install_manifest",
        "workflow_release",
    ),
    "official/artifacts/tables.bundle": _identity(
        CURRENT_TABLES_BUNDLE["bytes"],
        CURRENT_TABLES_BUNDLE["sha256"],
        "official_current_install_tables_bundle",
        "workflow_release",
    ),
    "official/artifacts/Scripts.dll": _identity(
        CURRENT_SCRIPTS["bytes"],
        CURRENT_SCRIPTS["sha256"],
        "official_current_frozen_scripts",
        "workflow_release",
    ),
    "official/textassets/Activity.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["Activity"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["Activity"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/ActivityMap.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["ActivityMap"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["ActivityMap"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/BidMap.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["BidMap"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["BidMap"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/Item.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["Item"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["Item"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/Map.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["Map"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["Map"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/RankMap.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["RankMap"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["RankMap"].utf8_sha256,
        "official_current_tables_bundle",
        "workflow_release",
    ),
    "official/textassets/BattleItem.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["BattleItem"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["BattleItem"].utf8_sha256,
        "official_current_tables_bundle",
        "supporting_authority",
    ),
    "official/textassets/Hero.txt": _identity(
        CURRENT_TEXT_ASSET_IDENTITIES["Hero"].utf8_size,
        CURRENT_TEXT_ASSET_IDENTITIES["Hero"].utf8_sha256,
        "official_current_tables_bundle",
        "supporting_authority",
    ),
    "evidence/preupdate/BattleItem.txt": _identity(
        4_468,
        "fc8c5231c723a80d4e338069aa09e32c32a5f4a3d5cc05f5e5d3e8094c844846",
        "official_preupdate_tables_bundle",
        "supporting_authority",
    ),
    "evidence/preupdate/manifest.bytes": _identity(
        341_606,
        "e4410978e1f31d8361ab88bfee69d6ac39a1434acc031fdf8138675877fa0f46",
        "official_preupdate_install_manifest",
        "supporting_authority",
    ),
    "evidence/preupdate/tables.bundle": _identity(
        3_210_559,
        "a4bab43719f04dc5185a7ebbce5e847b2d70a6d363d6d4c9a0d55e659722d7f2",
        "official_preupdate_install_tables_bundle",
        "supporting_authority",
    ),
    "legacy/raw/BidMap.txt": {
        **LEGACY_PACKAGE_BID_MAP,
        "source": "approved_legacy_package",
        "group": "supporting_authority",
    },
    "legacy/raw/Drop.txt": {
        **LEGACY_PACKAGE_DROP,
        "source": "approved_legacy_package",
        "group": "supporting_authority",
    },
    "legacy/derived/items.json": _identity(
        576_179,
        "164b5a5a667161447fde86fd306c65a38bb0d5ef2070be7eb781482bd21e84e9",
        "approved_legacy_package",
        "supporting_authority",
    ),
    "legacy/derived/items_droppable.json": _identity(
        325_697,
        "3c99d6852920a145d823ecd98b84ff3b477017ed20d35cb0a627b188902a63b7",
        "approved_legacy_package",
        "supporting_authority",
    ),
    "legacy/derived/item_category_mem_0625.json": _identity(
        10_947,
        "8aaf29d89604e5a4cca605b29ef06a6564080a17aab98a65907fde1e84671d5d",
        "approved_legacy_package",
        "supporting_authority",
    ),
    "legacy/derived/drop_weights_all_maps_0627.json": _identity(
        LEGACY_MAP_PAYLOADS["drop_weights_all_maps_0627"]["source_bytes"],
        LEGACY_MAP_PAYLOADS["drop_weights_all_maps_0627"]["source_sha256"],
        "approved_legacy_distribution_authority",
        "supporting_authority",
    ),
    "legacy/derived/drop_table_priors_0627.json": _identity(
        LEGACY_MAP_PAYLOADS["drop_table_priors_0627"]["source_bytes"],
        LEGACY_MAP_PAYLOADS["drop_table_priors_0627"]["source_sha256"],
        "approved_legacy_distribution_authority",
        "supporting_authority",
    ),
    "legacy/derived/quality_combo_presolve_q456.json": _identity(
        LEGACY_MAP_PAYLOADS["quality_combo_presolve_q456"]["source_bytes"],
        LEGACY_MAP_PAYLOADS["quality_combo_presolve_q456"]["source_sha256"],
        "approved_legacy_distribution_authority",
        "supporting_authority",
    ),
    "legacy/derived/red_longtail_normalized_0627.json": _identity(
        992,
        "c5cba6367d1a314533aead17488b12a319f24f21c3f8bf016d275472af21d5b0",
        "approved_legacy_distribution_authority",
        "supporting_authority",
    ),
    "legacy/derived/map_fragment_fixes.json": _identity(
        2_071,
        "5bd53288b9af6c261f748a131d8609e6641da30828790b83b5b47ffe97e48452",
        "approved_legacy_package",
        "supporting_authority",
    ),
    "selector/StaticData.cs": _identity(
        10_408,
        "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676",
        "approved_clean_donor_268c4b287dab815c8daa5a26ecef80d0c3c1d902",
        "selector",
    ),
    "selector/map_category_calibration_0_3_2.json": _identity(
        3_654_213,
        "37eb8f50d9be38ec3d1c85909ea3478800ecc574bda70446f05ca807983018cc",
        "approved_clean_donor_268c4b287dab815c8daa5a26ecef80d0c3c1d902",
        "selector",
    ),
}

PROCESSED_IDENTITIES: Mapping[str, Mapping[str, Any]] = {
    "data/processed/battle_items.json": _identity(
        17_982,
        "c5389aaf1a282b9c0224eb0ed296fc368fcb335fc8bd0cff883e9b289eea34e8",
        "deterministic_generator",
        "package",
    )
    | {"rows": 64},
    "data/processed/heroes.json": _identity(
        5_399,
        "22e6d5595348b9bb0744730ad7b2dce466fff48c4e5fd9c5c843b8ee58d3677e",
        "deterministic_generator",
        "package",
    )
    | {"rows": 21},
    "data/processed/maps.json": _identity(
        177_380,
        "9643a66795b7c60b97a3651b0ba415e4e1383bec7395e5f83123fa437cbb6d0e",
        "deterministic_generator",
        "package",
    )
    | {"rows": 228},
}

RAW_DROP_CLOSURE_COUNTS = {
    "empty_pool": 14,
    "missing_pool": 20,
    "unknown_item": 78,
    "valid": 31,
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ids_sha256(values: set[int] | list[int] | tuple[int, ...]) -> str:
    payload = json.dumps(sorted(values), ensure_ascii=True, separators=(",", ":"))
    return _sha256(payload.encode("ascii"))


def _fingerprint(files: Mapping[str, bytes]) -> str:
    payload = [
        {"path": path, "bytes": len(raw), "sha256": _sha256(raw)}
        for path, raw in sorted(files.items())
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _read_exact(root: Path, relative: str, contract: Mapping[str, Any]) -> bytes:
    path = root / Path(relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        info = path.stat()
        raw = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise FinalInputAssemblyError(
            f"missing/unreadable exact authority: {relative}"
        ) from exc
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if not path.is_file() or path.is_symlink() or reparse or info.st_nlink != 1:
        raise FinalInputAssemblyError(
            f"authority is not a regular single-link leaf: {relative}"
        )
    if info.st_mode & stat.S_IWRITE:
        raise FinalInputAssemblyError(f"authority is not read-only: {relative}")
    if contract.get("validator") == "current_steam_appmanifest":
        try:
            validate_current_appmanifest(raw)
        except Update606PackageInputError as exc:
            raise FinalInputAssemblyError(str(exc)) from exc
        return raw
    if len(raw) != contract["bytes"] or _sha256(raw) != contract["sha256"]:
        raise FinalInputAssemblyError(f"authority identity mismatch: {relative}")
    return raw


def _read_authorities(root: Path) -> dict[str, bytes]:
    root = root.resolve(strict=True)
    return {
        relative: _read_exact(root, relative, contract)
        for relative, contract in AUTHORITIES.items()
    }


def _validate_official_release(raw: Mapping[str, bytes]) -> dict[str, Any]:
    try:
        return validate_current_release_inputs(
            appmanifest=raw["official/artifacts/appmanifest_4128580.acf"],
            version_file=raw["official/artifacts/DefaultPackage.version"],
            buildin_catalog_bytes=raw[
                "official/artifacts/BuildinCatalog.bytes"
            ],
            buildin_catalog_json=raw[
                "official/artifacts/BuildinCatalog.json"
            ],
            package_hash_file=raw[
                "official/artifacts/DefaultPackage_d66aee-2026-08-20-599.hash"
            ],
            manifest=raw["official/artifacts/manifest.bytes"],
            tables_bundle=raw["official/artifacts/tables.bundle"],
            scripts=raw["official/artifacts/Scripts.dll"],
            text_assets={
                name: raw[f"official/textassets/{name}.txt"]
                for name in CURRENT_TEXT_ASSET_IDENTITIES
            },
        )
    except Update606PackageInputError as exc:
        raise FinalInputAssemblyError(str(exc)) from exc


def _decode_package_table(payload: bytes, *, name: str) -> list[list[str]]:
    try:
        decoded = base64.b64decode(payload, validate=True).decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise FinalInputAssemblyError(f"generated {name} package table is invalid") from exc
    return list(iter_table_rows(decoded))


def _render_processed(
    battle_package: bytes,
    hero_package: bytes,
    bid_map_package: bytes,
) -> dict[str, bytes]:
    try:
        battle = parse_battle_item_table(
            _decode_package_table(battle_package, name="BattleItem")
        )
        heroes = parse_hero_table(_decode_package_table(hero_package, name="Hero"))
        maps = parse_bid_map_table(
            _decode_package_table(bid_map_package, name="BidMap")
        )
    except ValueError as exc:
        raise FinalInputAssemblyError(f"generated processed table parse failed: {exc}") from exc
    records = {
        "data/processed/battle_items.json": [
            battle[item_id].model_dump() for item_id in sorted(battle)
        ],
        "data/processed/heroes.json": [
            heroes[hero_id].model_dump(exclude={"raw_row"})
            for hero_id in sorted(heroes)
        ],
        "data/processed/maps.json": [
            maps[map_id].model_dump(exclude={"raw_row"})
            for map_id in sorted(maps)
        ],
    }
    outputs: dict[str, bytes] = {}
    for relative, value in records.items():
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ).replace("\n", "\r\n").encode("utf-8")
        identity = PROCESSED_IDENTITIES[relative]
        if (
            len(value) != identity["rows"]
            or len(rendered) != identity["bytes"]
            or _sha256(rendered) != identity["sha256"]
        ):
            raise FinalInputAssemblyError(
                f"generated processed output identity mismatch: {relative}"
            )
        outputs[relative] = rendered
    return outputs


def _map_ids(value: Any, *, container: str | None = None) -> set[int]:
    if value.__class__ is not dict:
        raise FinalInputAssemblyError("derived map payload is not an exact object")
    maps = value.get(container) if container else value
    if maps.__class__ is not dict:
        raise FinalInputAssemblyError("derived map payload container is malformed")
    out: set[int] = set()
    for key in maps:
        if key.__class__ is not str or not key.isascii() or not key.isdigit():
            raise FinalInputAssemblyError("derived map payload has a malformed key")
        out.add(int(key))
    return out


def _validate_distribution(
    outputs: Mapping[str, bytes],
    droppable_raw: bytes,
    *,
    allowed_map_ids: set[int],
) -> dict[str, Any]:
    try:
        droppable = json.loads(droppable_raw.decode("utf-8-sig"))
        weights = json.loads(outputs["drop_weights_all_maps_0627.json"])
        priors = json.loads(outputs["drop_table_priors_0627.json"])
        presolve = json.loads(outputs["quality_combo_presolve_q456.json"])
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinalInputAssemblyError("generated distribution JSON is malformed") from exc
    if droppable.__class__ is not list:
        raise FinalInputAssemblyError("approved items_droppable is not an exact list")
    droppable_ids = {
        row.get("item_id")
        for row in droppable
        if row.__class__ is dict and row.get("item_id").__class__ is int
    }
    if len(droppable) != 680 or len(droppable_ids) != 680:
        raise FinalInputAssemblyError("approved items_droppable is not exact 680")
    weighted_items: set[int] = set()
    for map_id, entry in weights.items():
        if entry.__class__ is not dict or entry.get("items").__class__ is not dict:
            raise FinalInputAssemblyError(f"drop weight entry is malformed: {map_id}")
        for item_id in entry["items"]:
            if item_id.__class__ is not str or not item_id.isascii() or not item_id.isdigit():
                raise FinalInputAssemblyError("drop weight item key is malformed")
            weighted_items.add(int(item_id))
    if weighted_items - droppable_ids:
        raise FinalInputAssemblyError(
            "derived drop weights contain an item outside approved 680"
        )
    if set(UPDATE606_HISTORICAL_ONLY_ITEM_IDS) & weighted_items:
        raise FinalInputAssemblyError("historical-only item entered final drop weights")
    map_sets = {
        "weights": _map_ids(weights),
        "priors": _map_ids(priors),
        "presolve": _map_ids(presolve, container="maps"),
    }
    expected = {"weights": 101, "priors": 72, "presolve": 123}
    for name, ids in map_sets.items():
        if (
            len(ids) != expected[name]
            or not ids.issubset(allowed_map_ids)
            or ids & {5506, 6001}
        ):
            raise FinalInputAssemblyError(
                f"{name} independent stable-route authority mismatch"
            )
    return {
        "approved_droppable": {
            "rows": len(droppable_ids),
            "ids_sha256": _ids_sha256(droppable_ids),
            "regenerated_from_raw_drop": False,
        },
        "derived_weight_items": {
            "rows": len(weighted_items),
            "ids_sha256": _ids_sha256(weighted_items),
            "subset_of_approved_droppable": True,
        },
        "independent_map_authorities": {
            name: {"maps": len(ids), "ids_sha256": _ids_sha256(ids)}
            for name, ids in sorted(map_sets.items())
        },
        "hard_excluded_map_ids": [5506, 6001],
    }


def _raw_drop_closure(root: Path, outputs: Mapping[str, bytes]) -> dict[str, Any]:
    scope = derive_legacy_distribution_scope(
        legacy_bid_map_package=(root / "legacy/raw/BidMap.txt").read_bytes(),
        update606_bid_map_package=outputs["data/raw/tables/BidMap.txt"],
        legacy_drop_package=(root / "legacy/raw/Drop.txt").read_bytes(),
    )
    maps = parse_bid_map_table(
        _decode_package_table(outputs["data/raw/tables/BidMap.txt"], name="BidMap")
    )
    items = parse_item_table(
        _decode_package_table(outputs["data/raw/tables/Item.txt"], name="Item")
    )
    drops = load_drop_table(root / "legacy/raw/Drop.txt")
    cases: dict[str, list[int]] = {
        "empty_pool": [],
        "missing_pool": [],
        "unknown_item": [],
        "valid": [],
    }
    for map_id in scope.allowed_map_ids:
        try:
            flatten_pool(maps[map_id].drop_pool_id, drops, items)
        except DropPoolResolutionError as exc:
            if exc.reason not in cases:
                raise FinalInputAssemblyError(
                    f"unexpected raw Drop closure reason: {exc.reason}"
                ) from exc
            cases[exc.reason].append(map_id)
        else:
            cases["valid"].append(map_id)
    counts = {name: len(ids) for name, ids in cases.items()}
    if counts != RAW_DROP_CLOSURE_COUNTS:
        raise FinalInputAssemblyError(
            f"raw Drop closure census mismatch: {counts!r}"
        )
    return {
        "policy": "package_legacy_raw_runtime_atomic_fail_closed",
        "counts": counts,
        "map_ids": cases,
        "map_ids_sha256": {
            name: _ids_sha256(ids) for name, ids in cases.items()
        },
        "scope": scope.as_receipt(),
    }


def _render_receipt(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build(root: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    root = root.resolve(strict=True)
    authorities = _read_authorities(root)
    release_receipt = _validate_official_release(authorities)
    bid_map_package, bid_map_receipt = build_update606_package_table(
        "BidMap", authorities["official/textassets/BidMap.txt"]
    )
    item_package, item_receipt = build_update606_package_table(
        "Item", authorities["official/textassets/Item.txt"]
    )
    rankmap_package, rankmap_receipt = build_current_rankmap_package_table(
        authorities["official/textassets/RankMap.txt"]
    )
    hero_package, hero_receipt = build_update606_auxiliary_package_table(
        "Hero", authorities["official/textassets/Hero.txt"]
    )
    battle_package, battle_receipt = build_update606_auxiliary_package_table(
        "BattleItem",
        authorities["official/textassets/BattleItem.txt"],
        previous_text_asset=authorities["evidence/preupdate/BattleItem.txt"],
    )
    items_json, items_receipt = build_update606_items_catalog(
        authorities["official/textassets/Item.txt"],
        authorities["legacy/derived/items.json"],
    )
    outputs: dict[str, bytes] = {
        "data/raw/tables/BidMap.txt": bid_map_package,
        "data/raw/tables/Item.txt": item_package,
        "data/raw/tables/RankMap.txt": rankmap_package,
        "data/raw/tables/Hero.txt": hero_package,
        "data/raw/tables/Drop.txt": authorities["legacy/raw/Drop.txt"],
        "data/processed/items.json": items_json,
        "data/processed/items_droppable.json": authorities[
            "legacy/derived/items_droppable.json"
        ],
        "data/processed/map_fragment_fixes.json": authorities[
            "legacy/derived/map_fragment_fixes.json"
        ],
    }
    outputs.update(_render_processed(battle_package, hero_package, bid_map_package))
    rank_weights, rank_priors, rank_authority_receipt = (
        build_current_rankmap_distribution(
            rankmap_text_asset=authorities["official/textassets/RankMap.txt"],
            bidmap_text_asset=authorities["official/textassets/BidMap.txt"],
            item_text_asset=authorities["official/textassets/Item.txt"],
            map_text_asset=authorities["official/textassets/Map.txt"],
            calibrated_weights=authorities[
                "legacy/derived/drop_weights_all_maps_0627.json"
            ],
            calibrated_priors=authorities[
                "legacy/derived/drop_table_priors_0627.json"
            ],
        )
    )
    outputs["data/processed/rankmap_drop_weights_current.json"] = rank_weights
    outputs["data/processed/rankmap_drop_priors_current.json"] = rank_priors
    outputs["data/processed/rankmap_distribution_authority_current.json"] = (
        _render_receipt(rank_authority_receipt)
    )
    # ``prepare_legacy_distribution`` reads paths, so supply one private copy of
    # the already-generated update606 BidMap rather than a mutable worktree file.
    with tempfile.TemporaryDirectory(prefix="bk-update606-final-") as temporary:
        generated_bid_map = Path(temporary) / "BidMap.txt"
        generated_bid_map.write_bytes(bid_map_package)
        distribution, distribution_receipt = prepare_legacy_distribution(
            legacy_bid_map=root / "legacy/raw/BidMap.txt",
            update606_bid_map=generated_bid_map,
            legacy_drop=root / "legacy/raw/Drop.txt",
            legacy_drop_weights=root
            / "legacy/derived/drop_weights_all_maps_0627.json",
            legacy_drop_priors=root
            / "legacy/derived/drop_table_priors_0627.json",
            legacy_quality_presolve=root
            / "legacy/derived/quality_combo_presolve_q456.json",
            legacy_red_longtail=root
            / "legacy/derived/red_longtail_normalized_0627.json",
        )
    outputs.update(
        {f"data/processed/{name}": raw for name, raw in distribution.items()}
    )
    with tempfile.TemporaryDirectory(prefix="bk-update606-authority-") as temporary:
        generated_items = Path(temporary) / "items.json"
        generated_items.write_bytes(items_json)
        authority_document = build_authority_document(
            item_text_asset=root / "official/textassets/Item.txt",
            items_json=generated_items,
            legacy_category=root
            / "legacy/derived/item_category_mem_0625.json",
            items_droppable=root / "legacy/derived/items_droppable.json",
            package_generation=LEGACY_UPDATE606_PACKAGE_GENERATION,
        )
    authority = render_authority(authority_document)
    authority_receipt = build_authority_receipt(authority_document, authority)
    outputs["data/processed/item_category_authority_0_3_2.json"] = authority

    manifest = json.loads((ROOT / "scripts/package_data_manifest.json").read_text("utf-8"))
    expected_files = manifest.get("files") if manifest.__class__ is dict else None
    if (
        _sha256((ROOT / "scripts/package_data_manifest.json").read_bytes())
        != PACKAGE_MANIFEST_SHA256
        or expected_files.__class__ is not list
        or len(expected_files) != 19
        or set(outputs) != set(expected_files)
    ):
        raise FinalInputAssemblyError("package manifest/output set mismatch")
    closure_receipt = _raw_drop_closure(root, outputs)
    distribution_validation = _validate_distribution(
        {name.removeprefix("data/processed/"): raw for name, raw in outputs.items()},
        outputs["data/processed/items_droppable.json"],
        allowed_map_ids=set(closure_receipt["scope"]["allowed_map_ids"]),
    )
    package_fingerprint = _fingerprint(outputs)
    receipt = {
        "schema": "bidking_update606_final_input_generation_receipt_v1",
        "package_generation": PACKAGE_GENERATION,
        "package": {
            "baseline_id": PACKAGE_BASELINE_ID,
            "files": len(outputs),
            "fingerprint": package_fingerprint,
            "identities": {
                path: {"bytes": len(raw), "sha256": _sha256(raw)}
                for path, raw in sorted(outputs.items())
            },
        },
        "official_release_contract": release_receipt,
        "table_generation": {
            "BidMap": bid_map_receipt,
            "Item": item_receipt,
            "Hero": hero_receipt,
            "BattleItem": battle_receipt,
            "RankMap": rankmap_receipt,
            "items_catalog": items_receipt,
            "item_category_authority": authority_receipt,
            "legacy_distribution": distribution_receipt,
            "current_rankmap_distribution": rank_authority_receipt,
        },
        "distribution_validation": distribution_validation,
        "raw_drop_closure": closure_receipt,
        "package_input_policy": {
            "items_droppable": "approved_680_copy_only_not_rebuilt_from_raw_drop",
            "legacy_drop": "provenance_and_route_stable_authority_only",
            "rankmap_distribution": (
                "current_generation_algorithm_prior_not_drop_or_dropmap"
            ),
            "unknown_distribution": "fail_closed",
        },
        "match10": "deferred_excluded",
    }
    return outputs, receipt


def _entry(
    root: Path,
    relative: str,
    raw: bytes,
    *,
    group: str,
    source: str,
    generation: str,
) -> dict[str, Any]:
    return {
        "absolute_path": str((root / relative).resolve()),
        "relative_path": relative,
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "group": group,
        "source": source,
        "generation": generation,
        "read_only": True,
        "reparse_point": False,
        "hardlink_count": 1,
    }


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def commit(
    root: Path,
    output: Path,
    outputs: Mapping[str, bytes],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    output = output.resolve(strict=False)
    if output.parent != root or output.name != "final":
        raise FinalInputAssemblyError("output must be the authority root's final directory")
    manifest_path = root / "input_manifest.json"
    if output.exists() or manifest_path.exists():
        raise FinalInputAssemblyError("final output or input manifest already exists")
    stage = root / f".final-stage-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        for relative, raw in outputs.items():
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        generation_raw = _render_receipt(receipt)
        receipt_path = stage / "receipts/generation.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(generation_raw)
        stage.rename(output)
        for path in output.rglob("*"):
            if path.is_file():
                _make_read_only(path)

        authority_entries = [
            _entry(
                root,
                relative,
                raw,
                group=str(AUTHORITIES[relative]["group"]),
                source=str(AUTHORITIES[relative]["source"]),
                generation=(
                    PREVIOUS_GENERATION
                    if relative.startswith("evidence/preupdate/")
                    else PACKAGE_GENERATION
                    if relative.startswith("official/")
                    or relative.startswith("selector/")
                    else "approved_legacy_package"
                ),
            )
            for relative, raw in sorted(_read_authorities(root).items())
        ]
        package_entries = [
            _entry(
                root,
                f"final/{relative}",
                raw,
                group="package",
                source="deterministic_generator",
                generation=PACKAGE_GENERATION,
            )
            for relative, raw in sorted(outputs.items())
        ]
        workflow_files = {
            relative: (root / relative).read_bytes()
            for relative, contract in AUTHORITIES.items()
            if contract["group"] == "workflow_release"
        }
        selector_files = {
            relative: (root / relative).read_bytes()
            for relative, contract in AUTHORITIES.items()
            if contract["group"] == "selector"
        }
        manifest = {
            "schema": "bidking_update606_final_input_root_manifest_v1",
            "root": str(root),
            "package_generation": PACKAGE_GENERATION,
            "package": {
                "count": len(outputs),
                "fingerprint": receipt["package"]["fingerprint"],
            },
            "workflow": {
                "count": len(workflow_files),
                "contract_fingerprint": CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT,
                "file_fingerprint": _fingerprint(workflow_files),
            },
            "selector": {
                "count": len(selector_files),
                "file_fingerprint": _fingerprint(selector_files),
            },
            "authority": {
                "count": len(authority_entries),
                "fingerprint": _fingerprint(
                    {
                        row["relative_path"]: Path(row["absolute_path"]).read_bytes()
                        for row in authority_entries
                    }
                ),
            },
            "entries": authority_entries + package_entries,
            "generation_receipt": {
                "relative_path": "final/receipts/generation.json",
                "bytes": len(generation_raw),
                "sha256": _sha256(generation_raw),
            },
            "match10": "deferred_excluded",
            "not_package_inputs": [
                "official artifacts and TextAssets",
                "preupdate identity evidence",
                "legacy source authorities",
                "selector-only inputs",
                "receipts and input_manifest.json",
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _make_read_only(manifest_path)
        return manifest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs, receipt = build(args.authority_root)
        manifest = commit(args.authority_root, args.output_dir, outputs, receipt)
    except (
        FinalInputAssemblyError,
        Update606PackageInputError,
        OSError,
        ValueError,
    ) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print(
        "assembled update606 final inputs: "
        f"package={manifest['package']['count']} "
        f"workflow={manifest['workflow']['count']} "
        f"selector={manifest['selector']['count']} "
        f"fingerprint={manifest['package']['fingerprint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
