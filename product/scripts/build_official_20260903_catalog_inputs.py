#!/usr/bin/env python3
"""Build the exact September 3 hotfix catalog-only package inputs.

This builder deliberately owns only the three lookup tables whose official
bytes changed the product catalog: Item, BidMap, and Hero.  It does not build
Drop/RankMap inputs, droppable subsets, weights, priors, fingerprints, or
release metadata.

Both independently extracted TextAsset passes are required.  The official
files use an UTF-8 BOM and CRLF; the package representation is the historical
canonical form used by the table loaders: no BOM, LF row separators, no final
newline, then strict Base64.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.bid_map_table import parse_bid_map_table  # noqa: E402
from bidking_lab.extract.hero_table import parse_hero_table  # noqa: E402
from bidking_lab.extract.item_table import parse_item_table  # noqa: E402


SCHEMA = "bidking_official_20260903_hotfix_catalog_inputs_v1"
PACKAGE_GENERATION = "68171c-2026-09-03-907"
STEAM_BUILD_ID = "25096377"


class OfficialCatalogBuildError(ValueError):
    """The input pair or generated catalog does not match the frozen profile."""


@dataclass(frozen=True)
class TableIdentity:
    raw_bytes: int
    raw_sha256: str
    rows: int
    columns: int
    canonical_bytes: int
    canonical_sha256: str
    package_bytes: int
    package_sha256: str


TABLE_IDENTITIES: Mapping[str, TableIdentity] = {
    "Item": TableIdentity(
        raw_bytes=519_429,
        raw_sha256=(
            "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
        ),
        rows=1_427,
        columns=42,
        canonical_bytes=518_000,
        canonical_sha256=(
            "298307a54a1eccb0a66df741b6d3177634ce78068efddef52779059f90c0d257"
        ),
        package_bytes=690_668,
        package_sha256=(
            "00f9cc95e6951f436522a214bd16641e3e8f813ec4455999c72de6c9bd2de9e4"
        ),
    ),
    "BidMap": TableIdentity(
        raw_bytes=104_011,
        raw_sha256=(
            "f93a30b6bbcad22a003cf1363919af1d42a2eb600ec9ee006dc37865a0fc15a6"
        ),
        rows=246,
        columns=28,
        canonical_bytes=103_763,
        canonical_sha256=(
            "c68b07b6b4356cf91400482e7ee7f39038372339905d69f03b2590d0f29a9092"
        ),
        package_bytes=138_352,
        package_sha256=(
            "65174e54a6ac3675c39e19b193cb70df06d19f56e920242a2a50e665f53bd505"
        ),
    ),
    "Hero": TableIdentity(
        raw_bytes=9_879,
        raw_sha256=(
            "2d22ce736b5cd2ca1a4bb87b4830045a340684a5f0dc2e216da2672f029935d7"
        ),
        rows=22,
        columns=21,
        canonical_bytes=9_855,
        canonical_sha256=(
            "fb12971b5a2689310b23adae6509d6c2fe4e05a573d4c1f127fd1a685b603791"
        ),
        package_bytes=13_140,
        package_sha256=(
            "c370291e0692f8d67a84d6f4edc5ff5f0d13c22b62f45f476627ea2e9d77a91d"
        ),
    ),
}

# Frozen after the first deterministic render from the byte-identical pass pair.
# These identities describe lookup catalogs only; they confer no distribution
# or quote authority.
PROCESSED_IDENTITIES: Mapping[str, Mapping[str, Any]] = {
    "data/processed/items.json": {
        "bytes": 694_889,
        "sha256": (
            "49f4103b3e77987b801ca7b611eafe24f4d7b67c1e96bd833e7a5b694c8bbe1c"
        ),
    },
    "data/processed/maps.json": {
        "bytes": 181_897,
        "sha256": (
            "891efc9b461820cfed8611fd3f28588831aa22ae75f52a368b28fc0b641032f1"
        ),
    },
    "data/processed/heroes.json": {
        "bytes": 5_626,
        "sha256": (
            "525582d5006cd97d03d5c66526f1a56d58fdcfb8b2a38b69ef328171a536aeef"
        ),
    },
}

APPROVED_DISPLAY_CATALOG = {
    "git_blob": "25f9bc4621b0645b3ff69476a675bdc9aaa40c1f",
    "bytes": 646_276,
    "sha256": "730e30993465a60820c1d80ebe2aad00c0d9673562fa8ac2772ca4af5759dacb",
    "canonical_sha256": (
        "2519b65e97d28aad71705f1f6555dd75d57c1ce16adb38edac0752aaca734074"
    ),
    "ids_sha256": "857d89172fad3c11bcd168e939932dca41190409ef42aa6e35c7b57fa568ec8f",
    "rows": 1_336,
}
SHARED_SEMANTIC_FIELDS = (
    "description",
    "name_key",
    "desc_key",
    "quality",
    "quality_color",
    "value",
    "shape_w",
    "shape_h",
    "tags",
    "allowed_shelves",
    "icon_name",
    "model_name",
)

SETTLEMENT_CANARIES: Mapping[int, tuple[str, int, tuple[int, int]]] = {
    1_916_039: ("油纸伞", 127_200, (1, 2)),
    1_916_040: ("舞狮头", 156_000, (2, 2)),
    1_916_045: ("鲁班锁积木", 80_600, (1, 1)),
    1_916_048: ("龙泉莲纹碗", 483_000, (1, 1)),
    1_916_051: ("蜀锦香囊", 147_500, (1, 1)),
    1_916_054: ("湖笔狼毫", 114_600, (1, 2)),
    1_916_055: ("玉镇纸", 462_700, (2, 2)),
    1_916_059: ("凤穿牡丹尊", 1_606_000, (3, 3)),
}
S2_EXCHANGE_PART_IDS = tuple(range(1_006_010, 1_006_018))
S2_EXCHANGE_OUTPUT_ID = 1_006_018
AUTUMN_Q6_IDS = (1_046_013, 1_056_016, *range(1_096_012, 1_096_016))
PASS_Q6_ID = 1_306_018


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_read(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise OfficialCatalogBuildError(f"missing/non-regular input: {path}")
    before = path.stat()
    payload = path.read_bytes()
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(payload) != after.st_size
    ):
        raise OfficialCatalogBuildError(f"input changed while reading: {path}")
    return payload


def _canonicalize_pair(
    name: str,
    pass1_path: Path,
    pass2_path: Path,
    *,
    identity: TableIdentity | None = None,
) -> tuple[list[list[str]], bytes, bytes, dict[str, Any]]:
    expected = identity or TABLE_IDENTITIES[name]
    pass1 = _stable_read(pass1_path)
    pass2 = _stable_read(pass2_path)
    if pass1 != pass2:
        raise OfficialCatalogBuildError(f"{name} extraction passes differ")
    if len(pass1) != expected.raw_bytes or _sha256(pass1) != expected.raw_sha256:
        raise OfficialCatalogBuildError(f"{name} raw TextAsset identity mismatch")
    if not pass1.startswith(b"\xef\xbb\xbf"):
        raise OfficialCatalogBuildError(f"{name} raw TextAsset BOM mismatch")
    body = pass1[3:]
    if body.endswith(b"\r\n"):
        raise OfficialCatalogBuildError(f"{name} raw TextAsset has a final newline")
    if body.count(b"\r\n") != expected.rows - 1:
        raise OfficialCatalogBuildError(f"{name} raw TextAsset row separator mismatch")
    if body.count(b"\n") != expected.rows - 1 or body.count(b"\r") != expected.rows - 1:
        raise OfficialCatalogBuildError(f"{name} raw TextAsset line endings are mixed")
    try:
        lines = body.decode("utf-8").split("\r\n")
    except UnicodeDecodeError as exc:
        raise OfficialCatalogBuildError(f"{name} raw TextAsset is not UTF-8") from exc
    rows = [line.split("\t") for line in lines]
    if len(rows) != expected.rows or {len(row) for row in rows} != {expected.columns}:
        raise OfficialCatalogBuildError(f"{name} row/column identity mismatch")
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    package = base64.b64encode(canonical)
    if (
        len(canonical) != expected.canonical_bytes
        or _sha256(canonical) != expected.canonical_sha256
        or len(package) != expected.package_bytes
        or _sha256(package) != expected.package_sha256
    ):
        raise OfficialCatalogBuildError(f"{name} canonical/package identity mismatch")
    receipt = {
        "pass1": {"bytes": len(pass1), "sha256": _sha256(pass1)},
        "pass2": {"bytes": len(pass2), "sha256": _sha256(pass2)},
        "passes_byte_identical": True,
        "rows": len(rows),
        "columns": expected.columns,
        "canonical": {"bytes": len(canonical), "sha256": _sha256(canonical)},
        "package_base64": {"bytes": len(package), "sha256": _sha256(package)},
    }
    return rows, canonical, package, receipt


def _render_json(records: Sequence[Mapping[str, Any]]) -> bytes:
    return json.dumps(
        list(records),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ids_sha256(values: Sequence[int]) -> str:
    return _sha256(json.dumps(list(values), separators=(",", ":")).encode("ascii"))


def _load_approved_display_catalog(path: Path | None) -> tuple[bytes, str]:
    if path is not None:
        raw = _stable_read(path)
        source = str(path)
    else:
        try:
            completed = subprocess.run(
                [
                    "git",
                    "cat-file",
                    "blob",
                    str(APPROVED_DISPLAY_CATALOG["git_blob"]),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OfficialCatalogBuildError(
                "approved display catalog Git blob is unavailable"
            ) from exc
        raw = completed.stdout
        source = f"git-blob:{APPROVED_DISPLAY_CATALOG['git_blob']}"
    if (
        len(raw) != APPROVED_DISPLAY_CATALOG["bytes"]
        or _sha256(raw) != APPROVED_DISPLAY_CATALOG["sha256"]
    ):
        raise OfficialCatalogBuildError("approved display catalog identity mismatch")
    return raw, source


def _apply_approved_display_names(
    official_records: Sequence[Mapping[str, Any]],
    approved_raw: bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        approved_value = json.loads(approved_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialCatalogBuildError("approved display catalog is not UTF-8 JSON") from exc
    if approved_value.__class__ is not list:
        raise OfficialCatalogBuildError("approved display catalog must be an exact list")
    approved: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(approved_value):
        if row.__class__ is not dict:
            raise OfficialCatalogBuildError(
                f"approved display catalog row {index} is not an object"
            )
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in approved:
            raise OfficialCatalogBuildError(
                "approved display catalog has invalid/duplicate item_id"
            )
        approved[item_id] = row
    approved_ids = sorted(approved)
    if (
        len(approved) != APPROVED_DISPLAY_CATALOG["rows"]
        or _sha256(_canonical_json(approved_value))
        != APPROVED_DISPLAY_CATALOG["canonical_sha256"]
        or _ids_sha256(approved_ids) != APPROVED_DISPLAY_CATALOG["ids_sha256"]
    ):
        raise OfficialCatalogBuildError(
            "approved display catalog row/canonical identity mismatch"
        )

    official: dict[int, dict[str, Any]] = {}
    for row in official_records:
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in official:
            raise OfficialCatalogBuildError(
                "official catalog has invalid/duplicate item_id"
            )
        official[item_id] = dict(row)
    if not set(approved).issubset(official):
        raise OfficialCatalogBuildError(
            "approved display catalog is not a subset of the official catalog"
        )
    semantic_differences = {
        field: sorted(
            item_id
            for item_id in approved
            if approved[item_id].get(field) != official[item_id].get(field)
        )
        for field in SHARED_SEMANTIC_FIELDS
    }
    if any(semantic_differences.values()):
        raise OfficialCatalogBuildError(
            "shared official catalog semantic fields changed unexpectedly"
        )
    display_name_differences = sorted(
        item_id
        for item_id in approved
        if approved[item_id].get("name") != official[item_id].get("name")
    )
    if len(display_name_differences) != 138:
        raise OfficialCatalogBuildError(
            "shared official/display name difference identity mismatch"
        )
    for item_id, row in approved.items():
        name = row.get("name")
        if name.__class__ is not str or not name:
            raise OfficialCatalogBuildError(
                f"approved display name is invalid for item_id={item_id}"
            )
        official[item_id]["name"] = name
    rendered = [official[item_id] for item_id in sorted(official)]
    added_ids = sorted(set(official) - set(approved))
    if len(added_ids) != 91:
        raise OfficialCatalogBuildError("official catalog added-id count mismatch")
    return rendered, {
        "policy": "shared_name_only_from_approved_0_3_4_new_ids_from_official",
        "shared_rows": len(approved),
        "added_rows": len(added_ids),
        "added_ids_sha256": _ids_sha256(added_ids),
        "display_name_difference_rows": len(display_name_differences),
        "display_name_difference_ids_sha256": _ids_sha256(
            display_name_differences
        ),
        "shared_semantic_difference_counts": {
            field: len(ids) for field, ids in semantic_differences.items()
        },
    }


def _model_records(
    rows: Sequence[Sequence[str]],
    parser: Callable[[Sequence[Sequence[str]]], Mapping[int, Any]],
) -> list[dict[str, Any]]:
    parsed = parser(rows)
    return [
        parsed[record_id].model_dump(exclude={"raw_row"})
        for record_id in sorted(parsed)
    ]


def _assert_semantic_canaries(
    *,
    items: Mapping[int, Any],
    maps: Mapping[int, Any],
    heroes: Mapping[int, Any],
) -> dict[str, Any]:
    if len(items) != 1_427 or len(maps) != 246 or len(heroes) != 22:
        raise OfficialCatalogBuildError("processed catalog row count mismatch")
    for item_id, (name, value, shape) in SETTLEMENT_CANARIES.items():
        item = items.get(item_id)
        if item is None or (
            item.name,
            item.quality,
            item.value,
            (item.shape_w, item.shape_h),
        ) != (name, 6, value, shape):
            raise OfficialCatalogBuildError(
                f"settlement canary item_id={item_id} mismatch"
            )
    for item_id in S2_EXCHANGE_PART_IDS:
        item = items.get(item_id)
        if item is None or (
            item.quality,
            item.value,
            item.raw_row[39],
        ) != (6, 4_000_000, "[402]"):
            raise OfficialCatalogBuildError(f"S2 exchange part {item_id} mismatch")
    exchange = items.get(S2_EXCHANGE_OUTPUT_ID)
    if exchange is None or (
        exchange.quality,
        exchange.value,
        (exchange.shape_w, exchange.shape_h),
        exchange.raw_row[39],
    ) != (6, 35_000_000, (6, 4), "[]"):
        raise OfficialCatalogBuildError("S2 exchange output mismatch")
    for item_id in (*AUTUMN_Q6_IDS, PASS_Q6_ID):
        item = items.get(item_id)
        if item is None or item.quality != 6 or item.value <= 0:
            raise OfficialCatalogBuildError(f"q6 lifecycle canary {item_id} mismatch")
    required_maps = {*range(5_203, 5_220), *range(5_601, 5_612)}
    if not required_maps.issubset(maps):
        raise OfficialCatalogBuildError("official activity/ranked map block is incomplete")
    hero = heroes.get(303)
    if hero is None or hero.name != "印第安纳":
        raise OfficialCatalogBuildError("Hero[303] identity mismatch")
    return {
        "settlement_canary_item_ids": sorted(SETTLEMENT_CANARIES),
        "settlement_canary_value_sum": sum(
            value for _name, value, _shape in SETTLEMENT_CANARIES.values()
        ),
        "s2_exchange_part_ids": list(S2_EXCHANGE_PART_IDS),
        "s2_exchange_output_id": S2_EXCHANGE_OUTPUT_ID,
        "autumn_q6_ids": list(AUTUMN_Q6_IDS),
        "pass_q6_id": PASS_Q6_ID,
        "activity_map_ids": list(range(5_203, 5_220)),
        "ranked_map_ids": list(range(5_601, 5_612)),
        "hero_ids": [303],
        "distribution_or_quote_authority": False,
    }


def build_outputs(
    *,
    pass1_dir: Path,
    pass2_dir: Path,
    approved_display_catalog: Path | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    rows: dict[str, list[list[str]]] = {}
    outputs: dict[str, bytes] = {}
    tables_receipt: dict[str, Any] = {}
    for name in TABLE_IDENTITIES:
        table_rows, _canonical, package, table_receipt = _canonicalize_pair(
            name,
            pass1_dir / f"{name}.txt",
            pass2_dir / f"{name}.txt",
        )
        rows[name] = table_rows
        outputs[f"data/raw/tables/{name}.txt"] = package
        tables_receipt[name] = table_receipt

    items = parse_item_table(rows["Item"])
    maps = parse_bid_map_table(rows["BidMap"])
    heroes = parse_hero_table(rows["Hero"])
    canaries = _assert_semantic_canaries(items=items, maps=maps, heroes=heroes)

    approved_raw, approved_source = _load_approved_display_catalog(
        approved_display_catalog
    )
    items_records, overlay_receipt = _apply_approved_display_names(
        _model_records(rows["Item"], parse_item_table),
        approved_raw,
    )
    outputs["data/processed/items.json"] = _render_json(items_records)
    outputs["data/processed/maps.json"] = _render_json(
        _model_records(rows["BidMap"], parse_bid_map_table)
    )
    outputs["data/processed/heroes.json"] = _render_json(
        _model_records(rows["Hero"], parse_hero_table)
    )
    output_receipt: dict[str, Any] = {}
    for relative, payload in sorted(outputs.items()):
        identity = {
            "bytes": len(payload),
            "sha256": _sha256(payload),
        }
        expected = PROCESSED_IDENTITIES.get(relative)
        if expected is not None and identity != expected:
            raise OfficialCatalogBuildError(
                f"generated output identity mismatch: {relative}"
            )
        output_receipt[relative] = identity
    return outputs, {
        "schema": SCHEMA,
        "steam_build_id": STEAM_BUILD_ID,
        "package_generation": PACKAGE_GENERATION,
        "tables": tables_receipt,
        "display_overlay": {
            "source": approved_source,
            "bytes": len(approved_raw),
            "sha256": _sha256(approved_raw),
            **overlay_receipt,
        },
        "canaries": canaries,
        "outputs": output_receipt,
        "scope": {
            "catalog_lookup": True,
            "maps_lookup": True,
            "heroes_lookup": True,
            "drop_or_rankmap_changed": False,
            "distribution_or_quote_authority": False,
            "release_contract_changed": False,
        },
    }


def write_outputs(output_root: Path, outputs: Mapping[str, bytes]) -> None:
    for relative, payload in sorted(outputs.items()):
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise OfficialCatalogBuildError(f"output is a symlink: {target}")
        temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
        temporary.write_bytes(payload)
        os.replace(temporary, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pass1-dir", required=True, type=Path)
    parser.add_argument("--pass2-dir", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--approved-display-catalog", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs, receipt = build_outputs(
            pass1_dir=args.pass1_dir,
            pass2_dir=args.pass2_dir,
            approved_display_catalog=args.approved_display_catalog,
        )
        write_outputs(args.output_root, outputs)
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, OfficialCatalogBuildError, ValueError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print("wrote exact September 3 hotfix catalog inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
