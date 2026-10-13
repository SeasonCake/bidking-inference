"""Parse Tables/Item.txt into typed ``Item`` records.

Only the columns we have **confirmed** via cross-checking with Drop.txt
and in-game observations get first-class fields. The remaining raw
columns are kept on ``raw_row`` for future analysis — see
``docs/item_table_schema.md`` for the column-by-column status.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..secure.data_crypt import (  # moat 目录透明解密(加密包)/ 明文直读(源码)
    encryption_format,
    load_json,
)

from pydantic import BaseModel, Field

from bidking_lab.data.quality import Quality, color_name
from bidking_lab.extract.tables import load_table_rows

ITEM_TABLE_COLUMN_COUNT = 38
ITEM_TABLE_COLUMN_COUNTS = (38, 40)
ITEM_TABLE_COLUMN_COUNT_UPDATE606 = 41
ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903 = 42
ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008 = 45
ITEM_TABLE_UPDATE606_ROWS = 1336
ITEM_TABLE_UPDATE606_GENERATION = "d66aee-2026-08-20-599"
ITEM_TABLE_UPDATE606_PROFILE = (
    "steam_build_24830618_yooasset_d66aee-2026-08-20-599"
)
ITEM_TABLE_UPDATE606_CANONICAL_SHA256 = (
    "7acfa973b2e8f5ff1bca5545057658f00c4e57ccff8e115cc9c5661bc8854db4"
)
ITEM_TABLE_0_3_4_ROWS = 1336
ITEM_TABLE_0_3_4_GENERATION = "4ae819-2026-08-27-665"
ITEM_TABLE_0_3_4_PROFILE = (
    "steam_build_24962364_yooasset_4ae819-2026-08-27-665"
)
ITEM_TABLE_0_3_4_CANONICAL_SHA256 = (
    "2af2422c558cd21a2f76cb08ce7aba2f5658aa1f23f3b3881a5673fd67eef74a"
)
ITEM_TABLE_OFFICIAL_20260903_ROWS = 1427
ITEM_TABLE_OFFICIAL_20260903_GENERATION = "eb1e8f-2026-09-03-711"
ITEM_TABLE_OFFICIAL_20260903_PROFILE = (
    "steam_build_25094235_yooasset_eb1e8f-2026-09-03-711"
)
ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256 = (
    "04e79becd4913f3dd9f51823e8173b358a37a6b9f681909b7272e0f025db0f99"
)
ITEM_TABLE_OFFICIAL_20260903_CANONICAL_BYTES = 517_435
ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES = 518_864
ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256 = (
    "72c3430e82e82a4b01a1fa42e79335b50ab6bbbb7f09c9b438b2511caef566c3"
)
ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_BYTES = 689_916
ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_SHA256 = (
    "9cdcca9b2f55133d647a303fc38a6b6c9f9eb33630cf51008e09c8a0c0d9ee2e"
)
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION = "68171c-2026-09-03-907"
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PROFILE = (
    "steam_build_25096377_yooasset_68171c-2026-09-03-907"
)
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256 = (
    "298307a54a1eccb0a66df741b6d3177634ce78068efddef52779059f90c0d257"
)
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES = 518_000
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES = 519_429
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256 = (
    "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
)
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES = 690_668
ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_SHA256 = (
    "00f9cc95e6951f436522a214bd16641e3e8f813ec4455999c72de6c9bd2de9e4"
)
ITEM_TABLE_OFFICIAL_20260910_GENERATION = "b049c3-2026-09-10-597"
ITEM_TABLE_OFFICIAL_20260910_PROFILE = "steam_build_25221911_yooasset_b049c3-2026-09-10-597"
ITEM_TABLE_OFFICIAL_20260910_ROWS = 1432
ITEM_TABLE_OFFICIAL_20260910_CANONICAL_SHA256 = "95172eef82711ff111f52055095edfcfbea84080b23ebb8d84b73811d2920b9f"
ITEM_TABLE_OFFICIAL_20260910_CANONICAL_BYTES = 519138
ITEM_TABLE_OFFICIAL_20260910_RAW_TEXT_BYTES = 520572
ITEM_TABLE_OFFICIAL_20260910_RAW_TEXT_SHA256 = "8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47"
ITEM_TABLE_OFFICIAL_20260910_PACKAGE_BASE64_BYTES = 692184
ITEM_TABLE_OFFICIAL_20260910_PACKAGE_BASE64_SHA256 = "cb15d92ba307831d3da42875a6c542b5675baa48e74d37290cfbcd1a86594949"
ITEM_CATALOG_OFFICIAL_20260910_ROWS = 1432
ITEM_CATALOG_OFFICIAL_20260910_BYTES = 696961
ITEM_CATALOG_OFFICIAL_20260910_SHA256 = "1c41d6bd0900510c75fe7b3fd8b68e646d75219af2d3e73f3c8aecd84cace0f1"
ITEM_CATALOG_OFFICIAL_20260910_CANONICAL_SHA256 = "25828807f7527fe37702323eb4da38f8bdb4db70ae846e3798782eced810238d"
ITEM_TABLE_OFFICIAL_20261008_GENERATION = "76b7ca-2026-10-08-723"
ITEM_TABLE_OFFICIAL_20261008_PROFILE = "steam_build_25795126_yooasset_76b7ca-2026-10-08-723"
ITEM_TABLE_OFFICIAL_20261008_ROWS = 1506
ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256 = "b7c541b971e01615a8fbf5b5420934937e74a29068672427a663dd30f11f0901"
ITEM_TABLE_OFFICIAL_20261008_CANONICAL_BYTES = 559862
ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_BYTES = 561370
ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_SHA256 = "125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372"
ITEM_TABLE_OFFICIAL_20261008_PACKAGE_BASE64_BYTES = 746484
ITEM_TABLE_OFFICIAL_20261008_PACKAGE_BASE64_SHA256 = "380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40"
ITEM_CATALOG_OFFICIAL_20261008_ROWS = 1506
ITEM_CATALOG_OFFICIAL_20261008_BYTES = 737910
ITEM_CATALOG_OFFICIAL_20261008_SHA256 = "7be0cb441b7b63e87e08d3f15ede1d3f4e2435e299715b13ff02fcc629287bab"
ITEM_CATALOG_OFFICIAL_20261008_CANONICAL_SHA256 = "68ccbfb4752b30d1f7b701b670bc7bd1e7bcd70954f26e2f3972cc276f36d55b"
ITEM_TABLE_UPDATE606_ASSOCIATED_501_601_ROWS = 666
ITEM_TABLE_0_3_4_ASSOCIATED_501_601_ROWS = 639
ITEM_TABLE_OFFICIAL_20260903_ASSOCIATED_501_601_ROWS = 676
ITEM_TABLE_OFFICIAL_20260903_TAIL_VALUE_COUNTS = {"0": 62, "1": 1365}
ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES: Mapping[int, tuple[int, tuple[int, int]]] = {
    1916039: (127_200, (1, 2)),
    1916040: (156_000, (2, 2)),
    1916045: (80_600, (1, 1)),
    1916048: (483_000, (1, 1)),
    1916051: (147_500, (1, 1)),
    1916054: (114_600, (1, 2)),
    1916055: (462_700, (2, 2)),
    1916059: (1_606_000, (3, 3)),
}
ITEM_CATALOG_UPDATE606_ROWS = 1_336
ITEM_CATALOG_UPDATE606_CANONICAL_SHA256 = (
    "18e4efa914cae2d19705d242e11730def4f50f288673820d98da03638f2972d2"
)
ITEM_CATALOG_0_3_4_ROWS = 1_336
ITEM_CATALOG_0_3_4_CANONICAL_SHA256 = (
    "2519b65e97d28aad71705f1f6555dd75d57c1ce16adb38edac0752aaca734074"
)
ITEM_CATALOG_OFFICIAL_20260903_ROWS = 1_427
ITEM_CATALOG_OFFICIAL_20260903_BYTES = 694_889
ITEM_CATALOG_OFFICIAL_20260903_SHA256 = (
    "49f4103b3e77987b801ca7b611eafe24f4d7b67c1e96bd833e7a5b694c8bbe1c"
)
ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256 = (
    "a66b1c7fdc1b6b18e13a20e6189d2f23d045b3eb1f45f9a1ca165d70c28f7f86"
)
ITEM_CATALOG_UPDATE606_DISPLAY_OVERLAY_FIELDS = ("name",)

_COL_ITEM_ID = 0
_COL_NAME = 1
_COL_DESC = 2
_COL_NAME_KEY = 3
_COL_DESC_KEY = 5
_COL_TAGS = 6
_COL_QUALITY = 8
_COL_VALUE = 9
_COL_SHAPE_WH = 7

_LAYOUT_COLUMNS = {
    38: {
        "allowed_shelves": 19,
        "icon_name": 24,
        "model_name": 33,
    },
    # The 2026-07-23 table inserts one field at column 10 and adds a
    # trailing field. Confirmed fields through value/shape keep their
    # positions; confirmed fields after the insertion shift right by one.
    40: {
        "allowed_shelves": 20,
        "icon_name": 25,
        "model_name": 34,
    },
    # The exact update606 extraction exposes ms_idMap at column 39 plus an
    # empty trailing cell at column 40.  The simulation fields retain their
    # established positions.  This layout is only enabled by parse_item_table
    # after the complete 1336x41 table has matched the exact official
    # canonical identity above; a standalone or unknown 41-column row is
    # never position-guessed.
    41: {
        "allowed_shelves": 20,
        "icon_name": 25,
        "model_name": 34,
    },
    # The 2026-09-03 table appends one exact binary-valued field at index 41.
    # Every established field in indexes 0..40 retains the 41-column layout.
    # The new width is enabled only after the complete 1427x42 table matches
    # the frozen canonical identity; the tail is preserved in raw_row only.
    42: {
        "allowed_shelves": 20,
        "icon_name": 25,
        "model_name": 34,
    },
    # Exact 76b7ca appends three fields without moving the established prefix.
    # Their serialized names/uses remain unconfirmed; preserve them in raw_row.
    45: {
        "allowed_shelves": 20,
        "icon_name": 25,
        "model_name": 34,
    },
}


def _parse_int_list(blob: str) -> list[int]:
    """Parse a JSON-ish int list. Tolerates ``""`` / ``"[0]"`` / ``"[[]]"``."""
    if not blob or blob == "[]":
        return []
    data = json.loads(blob)
    out: list[int] = []
    if isinstance(data, list):
        for x in data:
            if isinstance(x, int):
                out.append(x)
    return out


class Item(BaseModel):
    """One row of Item.txt, with confirmed fields named.

    The complete raw row is preserved on ``raw_row`` so callers that
    need still-unmapped columns can inspect them without re-parsing.
    """

    item_id: int
    name: str
    description: str
    name_key: str
    desc_key: str
    quality: int = Field(ge=0, le=6)
    quality_color: str
    value: int = Field(ge=0)
    shape_w: int = Field(ge=0, le=6)
    shape_h: int = Field(ge=0, le=7)
    tags: list[int]
    allowed_shelves: list[int]
    icon_name: str
    model_name: str
    raw_row: list[str]


def _parse_item_row(row: Sequence[str], *, allow_exact_profile: bool) -> Item:
    allowed_columns = ITEM_TABLE_COLUMN_COUNTS + (
        (
            ITEM_TABLE_COLUMN_COUNT_UPDATE606,
            ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903,
            ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008,
        )
        if allow_exact_profile
        else ()
    )
    if len(row) not in allowed_columns:
        raise ValueError(
            f"item row must have one of {allowed_columns} columns, got {len(row)}"
        )
    layout = _LAYOUT_COLUMNS[len(row)]
    q_raw = int(row[_COL_QUALITY])
    wh = int(row[_COL_SHAPE_WH])
    return Item(
        item_id=int(row[_COL_ITEM_ID]),
        name=row[_COL_NAME],
        description=row[_COL_DESC],
        name_key=row[_COL_NAME_KEY],
        desc_key=row[_COL_DESC_KEY],
        quality=q_raw,
        quality_color=color_name(q_raw),
        value=int(row[_COL_VALUE]),
        shape_w=wh // 10,
        shape_h=wh % 10,
        tags=_parse_int_list(row[_COL_TAGS]),
        allowed_shelves=_parse_int_list(row[layout["allowed_shelves"]]),
        icon_name=row[layout["icon_name"]],
        model_name=row[layout["model_name"]],
        raw_row=list(row),
    )


def parse_item_row(row: Sequence[str]) -> Item:
    """Parse a legacy row.

    Exact 41/42-column rows require complete-table identity and therefore must
    enter through :func:`parse_item_table`.
    """

    return _parse_item_row(row, allow_exact_profile=False)


def _canonical_table_sha256(rows: Sequence[Sequence[str]]) -> str:
    try:
        canonical = "\n".join("\t".join(row) for row in rows)
    except TypeError as exc:
        raise ValueError("item table cells must be exact strings") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_exact_item_table(
    rows: Sequence[Sequence[str]],
    *,
    profile: str,
    expected_rows: int,
    expected_columns: int,
    expected_associated: int,
    expected_hall_only_associated: int = 0,
    expected_tail_counts: dict[str, int] | None = None,
    expected_item_canaries: Mapping[int, tuple[int, tuple[int, int]]] | None = None,
) -> None:
    if len(rows) != expected_rows:
        raise ValueError(
            f"{profile} Item identity requires exactly "
            f"{expected_rows} rows, got {len(rows)}"
        )
    seen: set[int] = set()
    indexed: dict[int, Sequence[str]] = {}
    associated = 0
    hall_only_associated = 0
    tail_counts = {value: 0 for value in (expected_tail_counts or {})}
    for index, row in enumerate(rows):
        if len(row) != expected_columns:
            raise ValueError(
                f"{profile} Item identity requires {expected_columns} columns; "
                f"row[{index}] has {len(row)}"
            )
        try:
            item_id = int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{profile} Item row[{index}] has invalid item id") from exc
        if item_id <= 0 or item_id in seen:
            raise ValueError(f"{profile} Item duplicate/nonpositive item_id={item_id}")
        seen.add(item_id)
        indexed[item_id] = row
        try:
            map_ids = json.loads(row[39])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"{profile} Item row[{index}] ms_idMap is not exact JSON"
            ) from exc
        if not isinstance(map_ids, list) or any(
            value.__class__ is not int for value in map_ids
        ):
            raise ValueError(f"{profile} Item row[{index}] ms_idMap must be an int list")
        if 501 in map_ids or 601 in map_ids:
            if not {501, 601}.issubset(map_ids):
                if expected_hall_only_associated and map_ids == [601]:
                    hall_only_associated += 1
                else:
                    raise ValueError(
                        f"{profile} Item row[{index}] has mixed 501/601 association"
                    )
            else:
                associated += 1
        if expected_tail_counts is not None:
            tail = row[41]
            if tail not in tail_counts:
                raise ValueError(
                    f"{profile} Item row[{index}] tail field is outside exact domain"
                )
            tail_counts[tail] += 1
    if associated != expected_associated:
        raise ValueError(
            f"{profile} Item 501/601 association count mismatch: "
            f"{associated}"
        )
    if hall_only_associated != expected_hall_only_associated:
        raise ValueError(f"{profile} Item auction-hall-only association count mismatch")
    if expected_tail_counts is not None and tail_counts != expected_tail_counts:
        raise ValueError(
            f"{profile} Item tail field count mismatch: {tail_counts}"
        )
    for item_id, (expected_value, expected_shape) in (
        expected_item_canaries or {}
    ).items():
        row = indexed.get(item_id)
        if row is None:
            raise ValueError(f"{profile} Item canary item_id={item_id} is missing")
        try:
            shape = int(row[_COL_SHAPE_WH])
            quality = int(row[_COL_QUALITY])
            value = int(row[_COL_VALUE])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{profile} Item canary item_id={item_id} is malformed"
            ) from exc
        if (
            quality != 6
            or (value, (shape // 10, shape % 10))
            != (expected_value, expected_shape)
        ):
            raise ValueError(
                f"{profile} Item canary item_id={item_id} quality/value/shape mismatch"
            )


def _validate_update606_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_item_table(
        rows,
        profile=ITEM_TABLE_UPDATE606_PROFILE,
        expected_rows=ITEM_TABLE_UPDATE606_ROWS,
        expected_columns=ITEM_TABLE_COLUMN_COUNT_UPDATE606,
        expected_associated=ITEM_TABLE_UPDATE606_ASSOCIATED_501_601_ROWS,
    )


def _validate_0_3_4_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_item_table(
        rows,
        profile=ITEM_TABLE_0_3_4_PROFILE,
        expected_rows=ITEM_TABLE_0_3_4_ROWS,
        expected_columns=ITEM_TABLE_COLUMN_COUNT_UPDATE606,
        expected_associated=ITEM_TABLE_0_3_4_ASSOCIATED_501_601_ROWS,
    )


def _validate_official_20260903_table(
    rows: Sequence[Sequence[str]],
    *,
    profile: str,
) -> None:
    _validate_exact_item_table(
        rows,
        profile=profile,
        expected_rows=ITEM_TABLE_OFFICIAL_20260903_ROWS,
        expected_columns=ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903,
        expected_associated=ITEM_TABLE_OFFICIAL_20260903_ASSOCIATED_501_601_ROWS,
        expected_tail_counts=ITEM_TABLE_OFFICIAL_20260903_TAIL_VALUE_COUNTS,
        expected_item_canaries=ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES,
    )


def _validate_official_20260910_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_item_table(
        rows,
        profile=ITEM_TABLE_OFFICIAL_20260910_PROFILE,
        expected_rows=ITEM_TABLE_OFFICIAL_20260910_ROWS,
        expected_columns=ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903,
        expected_associated=ITEM_TABLE_OFFICIAL_20260903_ASSOCIATED_501_601_ROWS,
        expected_tail_counts={"0": 62, "1": 1370},
        expected_item_canaries=ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES,
    )
    indexed = {int(row[0]): row for row in rows}
    for item_id in (12, 13, 14, 15, 6201):
        row = indexed.get(item_id)
        if row is None or (row[7], row[8], row[9]) != (
            "0", "6" if item_id == 6201 else "0", "0"
        ):
            raise ValueError("b049 noncollectible Item identity mismatch")


def _validate_official_20261008_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_item_table(
        rows,
        profile=ITEM_TABLE_OFFICIAL_20261008_PROFILE,
        expected_rows=ITEM_TABLE_OFFICIAL_20261008_ROWS,
        expected_columns=ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008,
        expected_associated=676,
        expected_hall_only_associated=45,
        expected_tail_counts={"0": 62, "1": 1436, "": 8},
        expected_item_canaries=ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES,
    )


def parse_item_table(rows: Iterable[Sequence[str]]) -> dict[int, Item]:
    materialized = [list(row) for row in rows]
    widths = {len(row) for row in materialized}
    exact_widths = {
        ITEM_TABLE_COLUMN_COUNT_UPDATE606,
        ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903,
        ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008,
    }
    allow_exact_profile = bool(widths & exact_widths)
    if allow_exact_profile:
        if len(widths) != 1 or not widths.issubset(exact_widths):
            raise ValueError(
                "exact-profile Item cannot be mixed with legacy or wrong-width rows"
            )
        observed = _canonical_table_sha256(materialized)
        if observed == ITEM_TABLE_UPDATE606_CANONICAL_SHA256:
            _validate_update606_table(materialized)
        elif observed == ITEM_TABLE_0_3_4_CANONICAL_SHA256:
            _validate_0_3_4_table(materialized)
        elif observed == ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256:
            _validate_official_20260903_table(
                materialized,
                profile=ITEM_TABLE_OFFICIAL_20260903_PROFILE,
            )
        elif observed == ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256:
            _validate_official_20260903_table(
                materialized,
                profile=ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PROFILE,
            )
        elif observed == ITEM_TABLE_OFFICIAL_20260910_CANONICAL_SHA256:
            _validate_official_20260910_table(materialized)
        elif observed == ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256:
            _validate_official_20261008_table(materialized)
        else:
            width_label = "45" if widths == {ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008} else "41/42"
            raise ValueError(
                f"{width_label}-column Item requires an exact approved generation and "
                f"table identity; unrecognized table sha256={observed}"
            )

    out: dict[int, Item] = {}
    for i, row in enumerate(materialized):
        try:
            item = _parse_item_row(row, allow_exact_profile=allow_exact_profile)
        except Exception as exc:
            raise ValueError(f"failed to parse item row index {i}: {exc}") from exc
        if item.item_id in out:
            raise ValueError(f"duplicate item_id={item.item_id} at row {i}")
        out[item.item_id] = item
    return out


def _supplement_from_catalog(items: dict[int, Item], txt_path: Path) -> None:
    """Merge the processed catalog's collectibles that the frozen base64 ``Item.txt`` predates.

    ``Item.txt`` is the raw game table snapshotted into the build (1207 items); the 75 collectibles
    extracted 2026-06-25 from game memory live only in ``data/processed/items.json`` (1282). This is
    the SINGLE place the full catalog is assembled, so EVERY ``load_item_table`` consumer — monitor
    minimap+settlement value, engine q5/q6 count-lock, inference price/cells lookups, the drop
    sampler — sees the complete table. It eliminates the stale-table bug class at the source (a
    missing new item used to resolve to value 0 / shape None → settlement under-count + undrawable
    minimap). Additive only: ``Item.txt`` stays authoritative for ids it already carries (every
    shared id has an identical value, verified), so this can only ADD the missing new items, never
    perturb an existing one. Best-effort: a missing/odd catalog must never break the base load.
    """
    try:
        catalog = txt_path.resolve().parents[2] / "processed" / "items.json"
    except (OSError, IndexError):
        return
    if not catalog.is_file():
        return
    try:
        raw = load_json(catalog)   # 加密包: 内核K解密; 源码: 明文直读
    except (OSError, ValueError):
        return
    for entry in (raw.values() if isinstance(raw, dict) else raw):
        if not isinstance(entry, dict):
            continue
        try:
            iid = int(entry["item_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if iid in items:
            continue
        try:
            items[iid] = Item(**{**entry, "raw_row": entry.get("raw_row", [])})
        except Exception:
            continue


def _processed_catalog_path(txt_path: Path) -> Path:
    try:
        return txt_path.resolve().parents[2] / "processed" / "items.json"
    except (OSError, IndexError) as exc:
        raise ValueError("exact items catalog path cannot be resolved") from exc


def _canonical_json_sha256(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _load_exact_catalog(
    txt_path: Path,
    *,
    expected_rows: int,
    expected_canonical_sha256: str,
    profile: str,
    expected_plain_bytes: int | None = None,
    expected_plain_sha256: str | None = None,
) -> dict[int, dict[str, object]]:
    catalog_path = _processed_catalog_path(txt_path)
    if not catalog_path.is_file():
        raise ValueError(f"{profile} exact items catalog is missing")
    if (expected_plain_bytes is None) != (expected_plain_sha256 is None):
        raise ValueError(f"{profile} exact items catalog file identity is incomplete")
    if expected_plain_bytes is not None and encryption_format(catalog_path) is None:
        try:
            raw = catalog_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"{profile} exact items catalog is unreadable") from exc
        if (
            len(raw) != expected_plain_bytes
            or hashlib.sha256(raw).hexdigest() != expected_plain_sha256
        ):
            raise ValueError(
                f"{profile} exact items catalog file identity mismatch"
            )
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{profile} exact items catalog is unreadable") from exc
    else:
        try:
            value = load_json(catalog_path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"{profile} exact items catalog is unreadable") from exc
    if (
        value.__class__ is not list
        or len(value) != expected_rows
        or _canonical_json_sha256(value) != expected_canonical_sha256
    ):
        raise ValueError(f"{profile} exact items catalog identity mismatch")
    out: dict[int, dict[str, object]] = {}
    for index, entry in enumerate(value):
        if entry.__class__ is not dict:
            raise ValueError(f"{profile} items catalog row {index} is not an object")
        item_id = entry.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in out:
            raise ValueError(f"{profile} items catalog has invalid/duplicate item_id")
        out[item_id] = entry
    return out


def _overlay_exact_catalog(items: dict[int, Item], txt_path: Path) -> None:
    """Apply only the frozen name overlay to the exact official identities.

    The parsed official table remains authoritative for every semantic field.
    Exact catalog identity prevents a legacy field from overwriting tags, quality,
    value, geometry, shelf/model routing, localization keys, description or icon.
    Historical-only legacy identities are deliberately absent.
    """

    table_sha256 = _canonical_table_sha256(
        [item.raw_row for item in items.values()]
    )
    if table_sha256 == ITEM_TABLE_UPDATE606_CANONICAL_SHA256:
        profile = ITEM_TABLE_UPDATE606_PROFILE
        catalog = _load_exact_catalog(
            txt_path,
            expected_rows=ITEM_CATALOG_UPDATE606_ROWS,
            expected_canonical_sha256=ITEM_CATALOG_UPDATE606_CANONICAL_SHA256,
            profile=profile,
        )
    elif table_sha256 == ITEM_TABLE_0_3_4_CANONICAL_SHA256:
        profile = ITEM_TABLE_0_3_4_PROFILE
        catalog = _load_exact_catalog(
            txt_path,
            expected_rows=ITEM_CATALOG_0_3_4_ROWS,
            expected_canonical_sha256=ITEM_CATALOG_0_3_4_CANONICAL_SHA256,
            profile=profile,
        )
    elif table_sha256 in {
        ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
        ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256,
    }:
        profile = (
            ITEM_TABLE_OFFICIAL_20260903_PROFILE
            if table_sha256 == ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256
            else ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PROFILE
        )
        catalog = _load_exact_catalog(
            txt_path,
            expected_rows=ITEM_CATALOG_OFFICIAL_20260903_ROWS,
            expected_canonical_sha256=(
                ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256
            ),
            expected_plain_bytes=ITEM_CATALOG_OFFICIAL_20260903_BYTES,
            expected_plain_sha256=ITEM_CATALOG_OFFICIAL_20260903_SHA256,
            profile=profile,
        )
    elif table_sha256 == ITEM_TABLE_OFFICIAL_20260910_CANONICAL_SHA256:
        profile = ITEM_TABLE_OFFICIAL_20260910_PROFILE
        catalog = _load_exact_catalog(
            txt_path,
            expected_rows=ITEM_CATALOG_OFFICIAL_20260910_ROWS,
            expected_canonical_sha256=ITEM_CATALOG_OFFICIAL_20260910_CANONICAL_SHA256,
            expected_plain_bytes=ITEM_CATALOG_OFFICIAL_20260910_BYTES,
            expected_plain_sha256=ITEM_CATALOG_OFFICIAL_20260910_SHA256,
            profile=profile,
        )
    elif table_sha256 == ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256:
        profile = ITEM_TABLE_OFFICIAL_20261008_PROFILE
        catalog = _load_exact_catalog(
            txt_path,
            expected_rows=ITEM_CATALOG_OFFICIAL_20261008_ROWS,
            expected_canonical_sha256=ITEM_CATALOG_OFFICIAL_20261008_CANONICAL_SHA256,
            expected_plain_bytes=ITEM_CATALOG_OFFICIAL_20261008_BYTES,
            expected_plain_sha256=ITEM_CATALOG_OFFICIAL_20261008_SHA256,
            profile=profile,
        )
    else:
        raise ValueError("exact Item table profile is unavailable for catalog overlay")
    if not set(items).issubset(catalog):
        raise ValueError(f"{profile} items catalog does not cover official Item ids")
    for item_id, entry in catalog.items():
        if item_id not in items:
            raise ValueError(f"{profile} items catalog contains a non-official item id")
        update = {
            field: entry[field]
            for field in ITEM_CATALOG_UPDATE606_DISPLAY_OVERLAY_FIELDS
        }
        items[item_id] = items[item_id].model_copy(update=update)


def load_item_table(path: Path) -> dict[int, Item]:
    """Load one generation-bound item catalog.

    Legacy tables retain the additive processed-catalog behavior. Exact 41-column
    generations require their 1336-id catalog and apply only the approved name
    overlay. The exact 2026-09-03 42-column table is already a complete catalog and
    cannot be supplemented from an older processed generation.
    """
    items = parse_item_table(load_table_rows(path))
    exact_columns = {len(item.raw_row) for item in items.values()}
    if items and exact_columns == {ITEM_TABLE_COLUMN_COUNT_UPDATE606}:
        _overlay_exact_catalog(items, Path(path))
    elif items and exact_columns == {ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903}:
        _overlay_exact_catalog(items, Path(path))
    elif items and exact_columns == {ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008}:
        # Standalone raw-table analysis remains complete without a display file.
        # When one is present, require the new exact profile and overlay names
        # only; an older/mixed catalog must not silently supply product data.
        catalog_path = Path(path).resolve().parents[2] / "processed" / "items.json"
        if catalog_path.exists():
            _overlay_exact_catalog(items, Path(path))
    else:
        _supplement_from_catalog(items, Path(path))
    return items


__all__ = (
    "ITEM_TABLE_COLUMN_COUNT",
    "ITEM_TABLE_COLUMN_COUNTS",
    "ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20260903",
    "ITEM_TABLE_COLUMN_COUNT_OFFICIAL_20261008",
    "ITEM_TABLE_OFFICIAL_20261008_GENERATION",
    "ITEM_TABLE_OFFICIAL_20261008_PROFILE",
    "ITEM_TABLE_OFFICIAL_20261008_ROWS",
    "ITEM_TABLE_OFFICIAL_20261008_CANONICAL_SHA256",
    "ITEM_TABLE_OFFICIAL_20261008_CANONICAL_BYTES",
    "ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_BYTES",
    "ITEM_TABLE_OFFICIAL_20261008_RAW_TEXT_SHA256",
    "ITEM_TABLE_OFFICIAL_20261008_PACKAGE_BASE64_BYTES",
    "ITEM_TABLE_OFFICIAL_20261008_PACKAGE_BASE64_SHA256",
    "ITEM_CATALOG_OFFICIAL_20261008_ROWS",
    "ITEM_CATALOG_OFFICIAL_20261008_BYTES",
    "ITEM_CATALOG_OFFICIAL_20261008_SHA256",
    "ITEM_CATALOG_OFFICIAL_20261008_CANONICAL_SHA256",
    "ITEM_TABLE_COLUMN_COUNT_UPDATE606",
    "ITEM_TABLE_0_3_4_ASSOCIATED_501_601_ROWS",
    "ITEM_TABLE_0_3_4_CANONICAL_SHA256",
    "ITEM_TABLE_0_3_4_GENERATION",
    "ITEM_TABLE_0_3_4_PROFILE",
    "ITEM_TABLE_0_3_4_ROWS",
    "ITEM_TABLE_OFFICIAL_20260903_ASSOCIATED_501_601_ROWS",
    "ITEM_TABLE_OFFICIAL_20260903_CANONICAL_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_GENERATION",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_GENERATION",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_PROFILE",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_ITEM_CANARIES",
    "ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_PACKAGE_BASE64_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_PROFILE",
    "ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_BYTES",
    "ITEM_TABLE_OFFICIAL_20260903_RAW_TEXT_SHA256",
    "ITEM_TABLE_OFFICIAL_20260903_ROWS",
    "ITEM_TABLE_OFFICIAL_20260903_TAIL_VALUE_COUNTS",
    "ITEM_TABLE_UPDATE606_CANONICAL_SHA256",
    "ITEM_TABLE_UPDATE606_GENERATION",
    "ITEM_TABLE_UPDATE606_PROFILE",
    "ITEM_TABLE_UPDATE606_ROWS",
    "ITEM_CATALOG_UPDATE606_CANONICAL_SHA256",
    "ITEM_CATALOG_UPDATE606_DISPLAY_OVERLAY_FIELDS",
    "ITEM_CATALOG_UPDATE606_ROWS",
    "ITEM_CATALOG_0_3_4_CANONICAL_SHA256",
    "ITEM_CATALOG_0_3_4_ROWS",
    "ITEM_CATALOG_OFFICIAL_20260903_BYTES",
    "ITEM_CATALOG_OFFICIAL_20260903_CANONICAL_SHA256",
    "ITEM_CATALOG_OFFICIAL_20260903_ROWS",
    "ITEM_CATALOG_OFFICIAL_20260903_SHA256",
    "Item",
    "Quality",
    "load_item_table",
    "parse_item_row",
    "parse_item_table",
)
