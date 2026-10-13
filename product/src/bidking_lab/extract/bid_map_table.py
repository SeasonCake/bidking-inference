"""Parse Tables/BidMap.txt into typed ``BidMap`` records.

The historical 21-column row, the 23-column v300 row and the update606
28-column row have been profiled (see ``docs/bid_map_schema.md``). Steam
build 24464806 keeps the 23-column layout but scales col[11] by 100.  The
update606 layout is accepted only for its exact package generation and table
identity; its five appended/split columns are never guessed from position.
The exact paired tables are normalized to a common basis-point
representation. Schema fields confirmed by cross-checking with Scripts.dll,
Drop.txt and user-provided sample evidence:

- col[0]   map_id
- col[1]   name
- col[2]   description
- col[7]   category (101-105 / 201; 9 distinct values)
- col[9]   sub_pool_weights: ``[[map_id, weight], ...]`` for anthology
           maps (the "未知" series); leaf maps store ``[[]]``
- col[11]  value-tier-associated scale (legacy/update606 10/15/20/25/30;
           build 24464806 uses 1000/1500/2000/2500/3000). update606 names
           it ``item_cost``. It is not a session-round count.
- col[12]  entry_fee: ``[1, 1, silver]`` — currency cost to play
- col[14]  round-cap candidate: ``[40/50/60, ...]``; audit-only
- col[15]  starting_budget: ``[[1, 1, silver]]`` — bidding budget granted
- col[17]  ``[9999, drop_pool_id, items_per_session_min, items_per_session_max]``
- col[10]  value_tier_ui: ``ui_value_{low,lower,medium,higher,high}``
- col[18]  mode_flag: 4=normal auction, 1=training, 2=tutorial
- col[19]  bid_price_ladder: per-round starting price tiers
- col[20]  round_skill_group_ids: 5-element list, one per auction round.
           ``GetRoundSkill(mapId, round)`` reads this value as a
           ``Table_SkillGroup`` id, then chooses one information Skill by the
           group's own weights. ``0`` means no round information skill.
           The legacy Python field name ``round_category_hints`` is retained
           only as a serialized compatibility alias; these IDs must never be
           used as Item categories or drop weights.

Auction mode derived from map_id prefix + current col[18] mode_flag:
  2xxx + mode_flag∈{2,4} → "open"   (明拍: bids visible)
  4xxx + mode_flag=4     → "sealed" (暗拍: blind bids)
  3xxx + mode_flag=1     → "training"
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

from typing import Literal

from pydantic import BaseModel, Field

from bidking_lab.extract.tables import load_table_rows

BID_MAP_TABLE_COLUMN_COUNT = 21
BID_MAP_TABLE_COLUMN_COUNT_V2 = 23
BID_MAP_TABLE_COLUMN_COUNT_UPDATE606 = 28

AuctionMode = Literal["open", "sealed", "training", "auction_hall"]

BID_MAP_LEGACY_COL11_PROFILE = "legacy_rounds_10_15_20_25_30"
BID_MAP_BUILD_24464806_COL11_PROFILE = (
    "steam_build_24464806_yooasset_5bb5a5-2026-07-30-670"
)
BID_MAP_BUILD_24464806_CANONICAL_SHA256 = (
    "e0652b756ef4bd0f97b0907cb49caaf72b6144461701445e4ff8c7782278e399"
)
BID_MAP_UPDATE606_GENERATION = "d66aee-2026-08-20-599"
BID_MAP_UPDATE606_PROFILE = (
    "steam_build_24830618_yooasset_d66aee-2026-08-20-599"
)
BID_MAP_UPDATE606_CANONICAL_SHA256 = (
    "bc351df9b9b2c905aa1ecc0f96f74e6f9f2af845dde0b4ba049e6af5cc10eb6b"
)
BID_MAP_0_3_4_ROWS = 229
BID_MAP_0_3_4_GENERATION = "4ae819-2026-08-27-665"
BID_MAP_0_3_4_PROFILE = (
    "steam_build_24962364_yooasset_4ae819-2026-08-27-665"
)
BID_MAP_0_3_4_CANONICAL_SHA256 = (
    "77c5ed9b8fa9772d6733b297666f79c2a9e9fbc22b1f72d57ffae68195028bf3"
)
BID_MAP_OFFICIAL_20260903_ROWS = 246
BID_MAP_OFFICIAL_20260903_GENERATION = "eb1e8f-2026-09-03-711"
BID_MAP_OFFICIAL_20260903_PROFILE = (
    "steam_build_25094235_yooasset_eb1e8f-2026-09-03-711"
)
BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256 = (
    "e4edc0d3cb6109f5f3409f4a14b54df1b1becceffcd8968991527b9636c61625"
)
BID_MAP_OFFICIAL_20260903_CANONICAL_BYTES = 103_766
BID_MAP_OFFICIAL_20260903_RAW_TEXT_BYTES = 104_014
BID_MAP_OFFICIAL_20260903_RAW_TEXT_SHA256 = (
    "9a3b8fc6771f0398918fb8aba9bb524efb814843d470318830f40005fc242cfa"
)
BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_BYTES = 138_356
BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_SHA256 = (
    "34be4bbb51a730e4b410dad87490b7ecb78f7e3b9034c1b88c20743b0a7d9b5e"
)
BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION = "68171c-2026-09-03-907"
BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE = (
    "steam_build_25096377_yooasset_68171c-2026-09-03-907"
)
BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256 = (
    "c68b07b6b4356cf91400482e7ee7f39038372339905d69f03b2590d0f29a9092"
)
BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES = 103_763
BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES = 104_011
BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256 = (
    "f93a30b6bbcad22a003cf1363919af1d42a2eb600ec9ee006dc37865a0fc15a6"
)
BID_MAP_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES = 138_352
BID_MAP_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_SHA256 = (
    "65174e54a6ac3675c39e19b193cb70df06d19f56e920242a2a50e665f53bd505"
)
BID_MAP_OFFICIAL_20260910_GENERATION = "b049c3-2026-09-10-597"
BID_MAP_OFFICIAL_20260910_PROFILE = "steam_build_25221911_yooasset_b049c3-2026-09-10-597"
BID_MAP_OFFICIAL_20260910_CANONICAL_SHA256 = "32dfa2d3af71e289adc7c123fb3d696c77563a6c799f5be0087155462d2e5b0a"
BID_MAP_OFFICIAL_20260910_CANONICAL_BYTES = 103725
BID_MAP_OFFICIAL_20260910_RAW_TEXT_BYTES = 103973
BID_MAP_OFFICIAL_20260910_RAW_TEXT_SHA256 = "7e5606adbd50b5e106265757704e87af103a9edaa21922fee061aa117b77e6b7"
BID_MAP_OFFICIAL_20260910_PACKAGE_BASE64_BYTES = 138300
BID_MAP_OFFICIAL_20260910_PACKAGE_BASE64_SHA256 = "03fb51becf3793d197a5901140d3b0ebbb0f1fe8502782f0793eed735b5f4f16"
BID_MAP_OFFICIAL_20261008_GENERATION = "76b7ca-2026-10-08-723"
BID_MAP_OFFICIAL_20261008_PROFILE = "steam_build_25795126_yooasset_76b7ca-2026-10-08-723"
BID_MAP_OFFICIAL_20261008_ROWS = 246
BID_MAP_OFFICIAL_20261008_CANONICAL_SHA256 = "ede496337b027bea71625d2aaf2a838eacc08d39d6c3a4efc1ef18dd178e6e85"
BID_MAP_OFFICIAL_20261008_CANONICAL_BYTES = 103724
BID_MAP_OFFICIAL_20261008_RAW_TEXT_BYTES = 103972
BID_MAP_OFFICIAL_20261008_RAW_TEXT_SHA256 = "67918be0098eb8f65772c34e0ef14bded7929878a832e64e448d2d9b724ba211"
BID_MAP_OFFICIAL_20261008_PACKAGE_BASE64_BYTES = 138300
BID_MAP_OFFICIAL_20261008_PACKAGE_BASE64_SHA256 = "5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020"
BID_MAP_OFFICIAL_20260903_ACTIVITY_MAP_IDS = tuple(range(5203, 5220))
BID_MAP_OFFICIAL_20260903_RANKED_MAP_IDS = tuple(range(5601, 5612))
# The first 21 raw positions retain the repo's paired-table field identities.
# The update606 tail is bound by an exact old/new table comparison plus the
# added Table_BidMap field names and value shapes from the matching Scripts.dll.
# Do not derive this tuple from TypeDef declaration order: that order is not a
# serialization contract.
BID_MAP_UPDATE606_COLUMN_NAMES = (
    "map_id",
    "name",
    "description",
    "name_key",
    "area_icon_key",
    "name_key_alt",
    "desc_key",
    "map_group",
    "is_visible",
    "sub_pool_weights",
    "value_tier_ui",
    "item_cost",
    "entry_fee_silver",
    "entry_requirement",
    "round_caps_candidate",
    "starting_budget_silver",
    "unused_special_output_items",
    "auction_rounds_rate",
    "mode_flag",
    "bid_price_ladder",
    "round_category_hints",
    "map_resource_rectangle",
    "map_resource_bg",
    "map_resource_cube",
    "tap_up",
    "map_avg_cell_value",
    "value_bias",
    "round_w",
)
BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION = (
    "BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION"
)

_LEGACY_COL11_BASIS_POINTS_BY_RAW = {
    "10": 1000,
    "15": 1500,
    "20": 2000,
    "25": 2500,
    "30": 3000,
}
_BUILD_24464806_COL11_BASIS_POINTS_BY_RAW = {
    "1000": 1000,
    "1500": 1500,
    "2000": 2000,
    "2500": 2500,
    "3000": 3000,
}
_UPDATE606_COL11_BASIS_POINTS_BY_RAW = _LEGACY_COL11_BASIS_POINTS_BY_RAW
_SCALED_COL11_TABLE_PROFILES = {
    BID_MAP_BUILD_24464806_CANONICAL_SHA256:
        BID_MAP_BUILD_24464806_COL11_PROFILE,
}
_EXACT_TABLE_PROFILES = {
    **_SCALED_COL11_TABLE_PROFILES,
    BID_MAP_UPDATE606_CANONICAL_SHA256: BID_MAP_UPDATE606_PROFILE,
    BID_MAP_0_3_4_CANONICAL_SHA256: BID_MAP_0_3_4_PROFILE,
    BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256: BID_MAP_OFFICIAL_20260903_PROFILE,
    BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256: (
        BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE
    ),
    BID_MAP_OFFICIAL_20260910_CANONICAL_SHA256: BID_MAP_OFFICIAL_20260910_PROFILE,
    BID_MAP_OFFICIAL_20261008_CANONICAL_SHA256: BID_MAP_OFFICIAL_20261008_PROFILE,
}
_EXACT_28_COLUMN_PROFILES = {
    BID_MAP_UPDATE606_PROFILE,
    BID_MAP_0_3_4_PROFILE,
    BID_MAP_OFFICIAL_20260903_PROFILE,
    BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE,
    BID_MAP_OFFICIAL_20260910_PROFILE,
    BID_MAP_OFFICIAL_20261008_PROFILE,
}

_OFFICIAL_20260903_ACTIVITY_DROP_REFS = {
    5203: [9999, 6101, 20, 30],
    5204: [9999, 5503, 44, 44],
    5205: [9999, 6201, 33, 44],
    5206: [9999, 2601, 33, 44],
    5207: [9999, 5502, 33, 44],
    5208: [9999, 5301, 1, 1],
    5209: [9999, 5504, 33, 44],
    5210: [9999, 6301, 33, 44],
    5211: [9999, 5303, 1, 1],
    5212: [9999, 2601, 33, 44],
    5213: [9999, 6401, 33, 44],
    5214: [9999, 5510, 33, 44],
    5215: [9999, 5305, 1, 1],
    5216: [9999, 2601, 33, 44],
    5217: [9999, 2601, 33, 44],
    5218: [9999, 2601, 33, 44],
    5219: [9999, 5307, 1, 1],
}
_OFFICIAL_20260903_ACTIVITY_SKILL_GROUPS = {
    5203: [10401, 10402, 10403, 10404, 10405],
    5204: [10501, 0, 10503, 10504, 10505],
    5205: [10601, 0, 10603, 10604, 10605],
    5206: [10701, 10702, 10703, 10704, 10705],
    5207: [10901, 10902, 10903, 10904, 10905],
    5208: [11001, 0, 0, 0, 0],
    5209: [20101, 20102, 20103, 20104, 20105],
    5210: [20201, 20202, 20203, 20204, 20205],
    5211: [20301, 0, 20303, 20304, 20305],
    5212: [20401, 20402, 20403, 20404, 20405],
    5213: [20501, 20502, 20503, 0, 0],
    5214: [20601, 20602, 20603, 20604, 20605],
    5215: [20701, 20702, 20703, 20704, 0],
    5216: [20801, 20802, 20803, 20804, 20805],
    5217: [20901, 20902, 20903, 0, 0],
    5218: [30201, 30202, 0, 0, 0],
    5219: [30301, 0, 30303, 30304, 30305],
}

# Historical public names remain import-compatible for one release. They no
# longer describe an authoritative session-round field and must not be used by
# product consumers.
BID_MAP_LEGACY_ROUNDS_PROFILE = BID_MAP_LEGACY_COL11_PROFILE
BID_MAP_BUILD_24464806_ROUNDS_PROFILE = BID_MAP_BUILD_24464806_COL11_PROFILE
_SCALED_ROUNDS_TABLE_PROFILES = _SCALED_COL11_TABLE_PROFILES


def _infer_auction_mode(map_id: int, mode_flag: int, *, profile: str = "") -> AuctionMode:
    if map_id == 6001 and (
        mode_flag == 20
        or (mode_flag == 10 and profile == BID_MAP_OFFICIAL_20260910_PROFILE)
    ):
        return "auction_hall"
    tier = map_id // 1000
    if mode_flag == 1 or (3000 <= map_id < 4000):
        return "training"
    if tier >= 4:
        return "sealed"
    return "open"


def _parse_category(value: str, map_id: int, *, rounds_profile: str) -> int:
    text = str(value or "").strip()
    if text:
        return int(text)
    if 2501 <= map_id <= 2530:
        return 105
    if 4501 <= map_id <= 4530:
        return 305
    if rounds_profile == BID_MAP_BUILD_24464806_COL11_PROFILE:
        if 2531 <= map_id <= 2550:
            return 105
        if 4531 <= map_id <= 4550:
            return 305
    raise ValueError(f"missing category for map_id={map_id}")


def _silver_amount(blob: str) -> int:
    """Parse `[1, 1, N]` or `[[1, 1, N]]`. Return 0 for empty / missing."""
    if blob in ("", "[]", "[[]]"):
        return 0
    data = json.loads(blob)
    if not data:
        return 0
    if isinstance(data[0], list):
        if not data[0]:
            return 0
        inner = data[0]
    else:
        inner = data
    # Expected form: [cat=1, item_id=1, amount]
    if len(inner) >= 3:
        return int(inner[2])
    return 0


def _parse_sub_pool_weights(blob: str) -> list[tuple[int, int]]:
    if blob in ("", "[]", "[[]]"):
        return []
    raw = json.loads(blob)
    out: list[tuple[int, int]] = []
    for pair in raw:
        if isinstance(pair, list) and len(pair) >= 2:
            out.append((int(pair[0]), int(pair[1])))
    return out


def _parse_drop_ref(blob: str) -> tuple[int, int, int]:
    """drop_ref blob → (drop_pool_id, items_min, items_max)."""
    data = json.loads(blob)
    if len(data) < 4:
        raise ValueError(f"drop_ref must have 4 elements, got {data!r}")
    return int(data[1]), int(data[2]), int(data[3])


class BidMap(BaseModel):
    """One BidMap.txt row with simulation-relevant fields named.

    For anthology maps (``sub_pool_weights`` non-empty), ``drop_pool_id``
    is the *self* pool — but a real session randomly picks among
    sub-maps weighted by ``sub_pool_weights``, then uses that map's
    own ``drop_pool_id``. The simulator handles this routing.
    """

    map_id: int
    name: str
    description: str
    category: int
    auction_mode: AuctionMode
    sub_pool_weights: list[tuple[int, int]] = Field(default_factory=list)
    col11_value_tier_scale_basis_points: int = Field(default=0, ge=0)
    """Build-normalized col[11] value-tier-associated scale.

    update606 names the field ``item_cost``; older generations did not prove a
    source name. ``basis_points`` describes only the normalized
    1000/1500/2000/2500/3000 representation, not a proven gameplay percentage.
    """
    rounds_total: int = Field(ge=0)
    """Deprecated compatibility projection of col[11].

    Kept so historical serialized fixtures still load. It is not an
    authoritative round count and product consumers must not use it.
    """
    entry_fee_silver: int = Field(ge=0)
    starting_budget_silver: int = Field(ge=0)
    drop_pool_id: int
    items_per_session_min: int = Field(ge=0)
    items_per_session_max: int = Field(ge=0)
    value_tier_ui: str
    mode_flag: int = Field(ge=0)
    bid_price_ladder: list[int] = Field(default_factory=list)
    round_category_hints: list[int] = Field(default_factory=list)
    """Deprecated serialized name for per-round SkillGroup IDs."""
    raw_row: list[str]

    @property
    def round_skill_group_ids(self) -> list[int]:
        """Per-round ``Table_SkillGroup`` ids consumed by ``GetRoundSkill``."""

        return list(self.round_category_hints)


# Backwards-compat alias for earlier imports. v0 only exposed map_id /
# name / description / raw_row; the new BidMap is a strict superset.
BidMapSummary = BidMap


def _canonical_table_sha256(rows: Sequence[Sequence[str]]) -> str:
    try:
        canonical = "\n".join("\t".join(row) for row in rows)
    except TypeError as exc:
        raise ValueError("bid map table cells must be exact strings") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_build24464806_table(rows: Sequence[Sequence[str]]) -> None:
    if len(rows) != 196:
        raise ValueError(
            "build 24464806 BidMap identity requires exactly 196 rows, "
            f"got {len(rows)}"
        )
    for index, row in enumerate(rows):
        if len(row) != BID_MAP_TABLE_COLUMN_COUNT_V2:
            raise ValueError(
                "build 24464806 BidMap identity requires 23 columns; "
                f"row[{index}] has {len(row)}"
            )
        if (
            row[11].__class__ is not str
            or row[11] not in _BUILD_24464806_COL11_BASIS_POINTS_BY_RAW
        ):
            raise ValueError(
                "build 24464806 BidMap col[11] must be an exact approved "
                f"scaled integer at row[{index}]"
            )
    observed = {row[11] for row in rows}
    if observed != set(_BUILD_24464806_COL11_BASIS_POINTS_BY_RAW):
        raise ValueError(
            "build 24464806 BidMap col[11] canary domain mismatch: "
            f"{sorted(observed)!r}"
        )


def _validate_exact_28_column_table(
    rows: Sequence[Sequence[str]],
    *,
    profile: str,
    expected_rows: int,
) -> None:
    if len(rows) != expected_rows:
        raise ValueError(
            f"{profile} BidMap identity requires exactly {expected_rows} rows, "
            f"got {len(rows)}"
        )
    ids: set[int] = set()
    for index, row in enumerate(rows):
        if len(row) != BID_MAP_TABLE_COLUMN_COUNT_UPDATE606:
            raise ValueError(
                f"{profile} BidMap identity requires 28 named columns; "
                f"row[{index}] has {len(row)}"
            )
        try:
            map_id = int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{profile} BidMap row[{index}] has invalid map id"
            ) from exc
        if map_id in ids:
            raise ValueError(f"{profile} BidMap duplicate map_id={map_id}")
        ids.add(map_id)
        if (
            row[11].__class__ is not str
            or row[11] not in _UPDATE606_COL11_BASIS_POINTS_BY_RAW
        ) and not (
            profile == BID_MAP_OFFICIAL_20261008_PROFILE
            and map_id == 6001
            and row[11] == "100"
        ):
            raise ValueError(
                f"{profile} BidMap item_cost must be an exact approved "
                f"integer at row[{index}]"
            )
    if ids != ({int(row[0]) for row in rows}):
        raise ValueError(f"{profile} BidMap id identity mismatch")
    required = {5506, 5606, 6001, *range(5001, 5011), *range(5101, 5111)}
    official_20260903_profiles = {
        BID_MAP_OFFICIAL_20260903_PROFILE,
        BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE,
        BID_MAP_OFFICIAL_20260910_PROFILE,
        BID_MAP_OFFICIAL_20261008_PROFILE,
    }
    if profile == BID_MAP_0_3_4_PROFILE or profile in official_20260903_profiles:
        required.add(5202)
    if profile in official_20260903_profiles:
        required.update(BID_MAP_OFFICIAL_20260903_ACTIVITY_MAP_IDS)
        required.update(BID_MAP_OFFICIAL_20260903_RANKED_MAP_IDS)
    if not required.issubset(ids):
        raise ValueError(f"{profile} BidMap requires exact modeled/control map ids")
    root = next(row for row in rows if row[0] == "5101")
    expected_raw_weights = [
        [5101, 10],
        [5102, 10],
        [5102, 10],
        [5103, 10],
        [5104, 10],
        [5105, 10],
        [5106, 10],
        [5107, 10],
        [5108, 10],
        [5109, 10],
        [5110, 10],
    ]
    if json.loads(root[9]) != expected_raw_weights:
        raise ValueError(f"{profile} BidMap 5101 raw duplicate-weight identity mismatch")
    raven_category = (
        "601" if profile in official_20260903_profiles
        and profile not in {BID_MAP_OFFICIAL_20260910_PROFILE, BID_MAP_OFFICIAL_20261008_PROFILE} else "501"
    )
    raven_items_min = 33 if profile in official_20260903_profiles else 22
    for map_id in range(5101, 5111):
        row = next(row for row in rows if row[0] == str(map_id))
        if (
            row[7] != raven_category
            or json.loads(row[17])
            != [9999, map_id - 100, raven_items_min, 44]
            or json.loads(row[20]) != [30105, 0, 0, 0, 0]
        ):
            raise ValueError(f"{profile} BidMap[{map_id}] Raven identity mismatch")
    if profile == BID_MAP_0_3_4_PROFILE:
        row_5202 = next(row for row in rows if row[0] == "5202")
        if (
            row_5202[7] != "501"
            or json.loads(row_5202[17]) != [9999, 5201, 1, 1]
            or row_5202[18] != "4"
            or json.loads(row_5202[20]) != [10301, 0, 0, 0, 0]
        ):
            raise ValueError(f"{profile} BidMap[5202] identity mismatch")
        row_6001 = next(row for row in rows if row[0] == "6001")
        if (
            row_6001[7] != "601"
            or json.loads(row_6001[14]) != [60, 60, 60, 60, 60]
            or json.loads(row_6001[17]) != [9999, 2601, 22, 44]
            or row_6001[18] != "20"
            or json.loads(row_6001[20]) != [103, 103, 103, 104, 105]
        ):
            raise ValueError(f"{profile} BidMap[6001] identity mismatch")
    elif profile in official_20260903_profiles:
        _validate_official_20260903_canaries(rows, profile=profile)
    observed = {row[11] for row in rows}
    expected_costs = set(_UPDATE606_COL11_BASIS_POINTS_BY_RAW)
    if profile == BID_MAP_OFFICIAL_20261008_PROFILE:
        expected_costs.add("100")
    if observed != expected_costs:
        raise ValueError(
            "update606 BidMap item_cost canary domain mismatch: "
            f"{sorted(observed)!r}"
        )


def _validate_update606_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_28_column_table(
        rows,
        profile=BID_MAP_UPDATE606_PROFILE,
        expected_rows=228,
    )


def _validate_0_3_4_table(rows: Sequence[Sequence[str]]) -> None:
    _validate_exact_28_column_table(
        rows,
        profile=BID_MAP_0_3_4_PROFILE,
        expected_rows=BID_MAP_0_3_4_ROWS,
    )


def _validate_official_20260903_canaries(
    rows: Sequence[Sequence[str]],
    *,
    profile: str,
) -> None:
    """Bind new static map identities without claiming runtime/drop authority."""

    indexed = {int(row[0]): row for row in rows}
    b049 = profile in {BID_MAP_OFFICIAL_20260910_PROFILE, BID_MAP_OFFICIAL_20261008_PROFILE}
    october = profile == BID_MAP_OFFICIAL_20261008_PROFILE
    try:
        row_5202 = indexed[5202]
        if (
            row_5202[7] != ("501" if b049 else "601")
            or json.loads(row_5202[17]) != [9999, 5201, 1, 1]
            or row_5202[18] != ("4" if b049 else "10")
            or json.loads(row_5202[20]) != [10301, 0, 0, 0, 0]
        ):
            raise ValueError(
                f"{profile} "
                "BidMap[5202] activity identity mismatch"
            )
        for map_id in BID_MAP_OFFICIAL_20260903_ACTIVITY_MAP_IDS:
            row = indexed[map_id]
            is_5218 = map_id == 5218
            if (
                row[7] != ("501" if b049 or is_5218 else "601")
                or json.loads(row[9]) != [[]]
                or row[11] != "30"
                or json.loads(row[12]) != [1, 1, 25000]
                or json.loads(row[14])
                != ([50, 50, 50, 50, 50] if b049 or is_5218 else [30, 30, 30, 30, 30])
                or json.loads(row[15]) != [[1, 1, 5000000]]
                or json.loads(row[17])
                != _OFFICIAL_20260903_ACTIVITY_DROP_REFS[map_id]
                or row[18] != ("4" if b049 or is_5218 else "10")
                or json.loads(row[20])
                != _OFFICIAL_20260903_ACTIVITY_SKILL_GROUPS[map_id]
            ):
                raise ValueError(
                    f"{profile} "
                    f"BidMap[{map_id}] activity identity mismatch"
                )

        expected_ranked_root = [
            [map_id, 20]
            for map_id in BID_MAP_OFFICIAL_20260903_RANKED_MAP_IDS
        ]
        for map_id in BID_MAP_OFFICIAL_20260903_RANKED_MAP_IDS:
            row = indexed[map_id]
            if (
                row[7] != "402"
                or row[8] != ("2" if map_id == 5611 else "1")
                or json.loads(row[9])
                != (expected_ranked_root if map_id == 5601 else [[]])
                or row[11] != "30"
                or json.loads(row[12]) != [1, 1, 15000]
                or json.loads(row[14]) != [60, 60, 60, 60, 60]
                or json.loads(row[15]) != [[1, 1, 2000000]]
                or json.loads(row[17]) != [9999, map_id - 2900, 25, 30]
                or row[18] != "4"
                or json.loads(row[20]) != [103, 0, 0, 0, 0]
            ):
                raise ValueError(
                    f"{profile} "
                    f"BidMap[{map_id}] ranked identity mismatch"
                )

        row_6001 = indexed[6001]
        if (
            row_6001[7] != "601"
            or json.loads(row_6001[9]) != [[]]
            or json.loads(row_6001[14]) != [30, 30, 30, 30, 30]
            or json.loads(row_6001[17]) != ([9999, 6010, 1, 1] if october else [9999, 2601, 44, 44])
            or row_6001[18] != ("10" if b049 and not october else "20")
            or (october and row_6001[11] != "100")
            or json.loads(row_6001[20]) != [103, 103, 103, 104, 105]
        ):
            raise ValueError(
                f"{profile} "
                "BidMap[6001] static identity mismatch"
            )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{profile} BidMap canary is malformed"
        ) from exc


def _validate_official_20260903_table(
    rows: Sequence[Sequence[str]],
    *,
    profile: str,
) -> None:
    _validate_exact_28_column_table(
        rows,
        profile=profile,
        expected_rows=BID_MAP_OFFICIAL_20260903_ROWS,
    )


def _col11_profile_for_table(rows: Sequence[Sequence[str]]) -> str:
    table_sha256 = _canonical_table_sha256(rows)
    profile = _EXACT_TABLE_PROFILES.get(table_sha256)
    scaled_profile = _SCALED_COL11_TABLE_PROFILES.get(table_sha256)
    if scaled_profile is not None:
        profile = scaled_profile
    compatibility_profile = _SCALED_ROUNDS_TABLE_PROFILES.get(table_sha256)
    if compatibility_profile is not None:
        profile = compatibility_profile
    if profile == BID_MAP_BUILD_24464806_COL11_PROFILE:
        _validate_build24464806_table(rows)
        return profile
    if profile == BID_MAP_UPDATE606_PROFILE:
        _validate_update606_table(rows)
        return profile
    if profile == BID_MAP_0_3_4_PROFILE:
        _validate_0_3_4_table(rows)
        return profile
    if profile == BID_MAP_OFFICIAL_20260903_PROFILE:
        _validate_official_20260903_table(
            rows,
            profile=BID_MAP_OFFICIAL_20260903_PROFILE,
        )
        return profile
    if profile == BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE:
        _validate_official_20260903_table(
            rows,
            profile=BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE,
        )
        return profile
    if profile == BID_MAP_OFFICIAL_20260910_PROFILE:
        _validate_official_20260903_table(rows, profile=profile)
        return profile
    if profile == BID_MAP_OFFICIAL_20261008_PROFILE:
        _validate_exact_28_column_table(rows, profile=profile, expected_rows=BID_MAP_OFFICIAL_20261008_ROWS)
        return profile

    for index, row in enumerate(rows):
        if len(row) == BID_MAP_TABLE_COLUMN_COUNT:
            col11_index = 10
        elif len(row) == BID_MAP_TABLE_COLUMN_COUNT_V2:
            col11_index = 11
        elif len(row) == BID_MAP_TABLE_COLUMN_COUNT_UPDATE606:
            raise ValueError(
                "28-column BidMap requires an exact approved generation "
                f"and table identity; unrecognized table sha256={table_sha256}, "
                f"row index={index}"
            )
        else:
            continue
        if row[col11_index] in _BUILD_24464806_COL11_BASIS_POINTS_BY_RAW:
            raise ValueError(
                "scaled BidMap col[11] requires an approved table identity; "
                f"unrecognized table sha256={table_sha256}, row index={index}"
            )
    return BID_MAP_LEGACY_COL11_PROFILE


def _parse_col11_scale(
    value: str,
    *,
    col11_profile: str,
) -> tuple[int, int]:
    """Return ``(legacy_compat_value, normalized_basis_points)``."""
    if value.__class__ is not str:
        raise ValueError("BidMap col[11] must be an exact string integer")
    if col11_profile == BID_MAP_LEGACY_COL11_PROFILE:
        basis_points = _LEGACY_COL11_BASIS_POINTS_BY_RAW.get(value)
        if basis_points is None:
            if value in _BUILD_24464806_COL11_BASIS_POINTS_BY_RAW:
                raise ValueError(
                    "scaled BidMap col[11] requires an approved table identity"
                )
            raise ValueError(f"unsupported legacy BidMap col[11]: {value!r}")
        return int(value), basis_points
    if col11_profile == BID_MAP_BUILD_24464806_COL11_PROFILE:
        basis_points = _BUILD_24464806_COL11_BASIS_POINTS_BY_RAW.get(value)
        if basis_points is None:
            raise ValueError(
                "unsupported build 24464806 BidMap col[11]: "
                f"{value!r}"
            )
        if (
            os.environ.get(
                BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION
            )
            == "1"
        ):
            return int(value), basis_points
        return basis_points // 100, basis_points
    if col11_profile in _EXACT_28_COLUMN_PROFILES:
        if col11_profile == BID_MAP_OFFICIAL_20261008_PROFILE and value == "100":
            # Structural compatibility scale only; this is not a drop probability.
            return 100, 10000
        basis_points = _UPDATE606_COL11_BASIS_POINTS_BY_RAW.get(value)
        if basis_points is None:
            raise ValueError(
                f"unsupported exact BidMap item_cost: {value!r}"
            )
        return int(value), basis_points
    raise ValueError(f"unknown BidMap col[11] profile: {col11_profile!r}")


def parse_bid_map_row(
    row: Sequence[str],
    *,
    rounds_profile: str = BID_MAP_LEGACY_COL11_PROFILE,
) -> BidMap:
    if len(row) == BID_MAP_TABLE_COLUMN_COUNT:
        idx = {
            "category": 7,
            "sub_pool_weights": 8,
            "value_tier_ui": 9,
            "col11": 10,
            "entry_fee": 11,
            "starting_budget": 14,
            "drop_ref": 16,
            "mode_flag": 17,
            "bid_price_ladder": 18,
            "round_category_hints": 19,
        }
    elif len(row) == BID_MAP_TABLE_COLUMN_COUNT_V2:
        # Current v300 game data has two extra columns. Confirmed old fields
        # moved as follows: old col[8] -> new col[9], and old col[9+] ->
        # new col[10+]. The parser keeps the previous public schema and
        # preserves all raw columns for future mapping of the new fields.
        idx = {
            "category": 7,
            "sub_pool_weights": 9,
            "value_tier_ui": 10,
            "col11": 11,
            "entry_fee": 12,
            "starting_budget": 15,
            "drop_ref": 17,
            "mode_flag": 18,
            "bid_price_ladder": 19,
            "round_category_hints": 20,
        }
    elif (
        len(row) == BID_MAP_TABLE_COLUMN_COUNT_UPDATE606
        and rounds_profile in _EXACT_28_COLUMN_PROFILES
    ):
        # Exact old/new rows keep the established simulation fields through
        # round_category_hints at their original positions.  The former single
        # resource field at index 21 is split across indexes 21..23; the old
        # trailing flag moves to 24, followed by the three new named fields.
        # Every cell remains preserved in raw_row for consumers that explicitly
        # bind the complete named schema above.
        idx = {
            "category": 7,
            "sub_pool_weights": 9,
            "value_tier_ui": 10,
            "col11": 11,
            "entry_fee": 12,
            "starting_budget": 15,
            "drop_ref": 17,
            "mode_flag": 18,
            "bid_price_ladder": 19,
            "round_category_hints": 20,
        }
    else:
        raise ValueError(
            f"bid map row must have {BID_MAP_TABLE_COLUMN_COUNT} or "
            f"{BID_MAP_TABLE_COLUMN_COUNT_V2} columns, or the exact "
            f"{BID_MAP_TABLE_COLUMN_COUNT_UPDATE606}-column update606 "
            f"profile; got {len(row)}"
        )
    drop_pool_id, items_min, items_max = _parse_drop_ref(row[idx["drop_ref"]])
    map_id = int(row[0])
    mode_flag = int(row[idx["mode_flag"]])
    ladder_text = row[idx["bid_price_ladder"]]
    ladder_raw = json.loads(ladder_text) if ladder_text not in ("", "[]") else []
    hints_raw_text = row[idx["round_category_hints"]]
    hints_parsed = json.loads(hints_raw_text) if hints_raw_text not in ("", "[]") else []
    hints_raw = hints_parsed if isinstance(hints_parsed, list) else []
    category = _parse_category(
        row[idx["category"]],
        map_id,
        rounds_profile=rounds_profile,
    )
    if rounds_profile == BID_MAP_OFFICIAL_20261008_PROFILE and row[idx["col11"]] == "100" and map_id != 6001:
        raise ValueError("76b7ca item_cost 100 is restricted to auction hall 6001")
    rounds_compat, col11_basis_points = _parse_col11_scale(
        row[idx["col11"]],
        col11_profile=rounds_profile,
    )
    return BidMap(
        map_id=map_id,
        name=row[1],
        description=row[2],
        category=category,
        auction_mode=_infer_auction_mode(map_id, mode_flag, profile=rounds_profile),
        sub_pool_weights=_parse_sub_pool_weights(row[idx["sub_pool_weights"]]),
        col11_value_tier_scale_basis_points=col11_basis_points,
        rounds_total=rounds_compat,
        entry_fee_silver=_silver_amount(row[idx["entry_fee"]]),
        starting_budget_silver=_silver_amount(row[idx["starting_budget"]]),
        drop_pool_id=drop_pool_id,
        items_per_session_min=items_min,
        items_per_session_max=items_max,
        value_tier_ui=row[idx["value_tier_ui"]],
        mode_flag=mode_flag,
        bid_price_ladder=ladder_raw,
        round_category_hints=[int(x) for x in hints_raw],
        raw_row=list(row),
    )


def parse_bid_map_table(rows: Iterable[Sequence[str]]) -> dict[int, BidMap]:
    materialized = [list(row) for row in rows]
    col11_profile = _col11_profile_for_table(materialized)
    out: dict[int, BidMap] = {}
    for i, row in enumerate(materialized):
        try:
            bm = parse_bid_map_row(row, rounds_profile=col11_profile)
        except Exception as exc:
            raise ValueError(f"failed to parse bid map row index {i}: {exc}") from exc
        if bm.map_id in out:
            raise ValueError(f"duplicate map_id={bm.map_id} at row {i}")
        out[bm.map_id] = bm
    return out


def load_bid_map_table(path: Path) -> dict[int, BidMap]:
    return parse_bid_map_table(load_table_rows(path))


__all__ = (
    "BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION",
    "BID_MAP_BUILD_24464806_COL11_PROFILE",
    "BID_MAP_BUILD_24464806_CANONICAL_SHA256",
    "BID_MAP_BUILD_24464806_ROUNDS_PROFILE",
    "BID_MAP_0_3_4_CANONICAL_SHA256",
    "BID_MAP_0_3_4_GENERATION",
    "BID_MAP_0_3_4_PROFILE",
    "BID_MAP_0_3_4_ROWS",
    "BID_MAP_OFFICIAL_20261008_GENERATION",
    "BID_MAP_OFFICIAL_20261008_PROFILE",
    "BID_MAP_OFFICIAL_20261008_ROWS",
    "BID_MAP_OFFICIAL_20261008_CANONICAL_SHA256",
    "BID_MAP_OFFICIAL_20261008_CANONICAL_BYTES",
    "BID_MAP_OFFICIAL_20261008_RAW_TEXT_BYTES",
    "BID_MAP_OFFICIAL_20261008_RAW_TEXT_SHA256",
    "BID_MAP_OFFICIAL_20261008_PACKAGE_BASE64_BYTES",
    "BID_MAP_OFFICIAL_20261008_PACKAGE_BASE64_SHA256",
    "BID_MAP_OFFICIAL_20260903_ACTIVITY_MAP_IDS",
    "BID_MAP_OFFICIAL_20260903_CANONICAL_BYTES",
    "BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256",
    "BID_MAP_OFFICIAL_20260903_GENERATION",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_BYTES",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_BYTES",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_PACKAGE_BASE64_SHA256",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_PROFILE",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_BYTES",
    "BID_MAP_OFFICIAL_20260903_HOTFIX_RAW_TEXT_SHA256",
    "BID_MAP_OFFICIAL_20260903_PROFILE",
    "BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_BYTES",
    "BID_MAP_OFFICIAL_20260903_PACKAGE_BASE64_SHA256",
    "BID_MAP_OFFICIAL_20260903_RANKED_MAP_IDS",
    "BID_MAP_OFFICIAL_20260903_RAW_TEXT_BYTES",
    "BID_MAP_OFFICIAL_20260903_RAW_TEXT_SHA256",
    "BID_MAP_OFFICIAL_20260903_ROWS",
    "BID_MAP_TABLE_COLUMN_COUNT_UPDATE606",
    "BID_MAP_UPDATE606_CANONICAL_SHA256",
    "BID_MAP_UPDATE606_COLUMN_NAMES",
    "BID_MAP_UPDATE606_GENERATION",
    "BID_MAP_UPDATE606_PROFILE",
    "BID_MAP_LEGACY_COL11_PROFILE",
    "BID_MAP_LEGACY_ROUNDS_PROFILE",
    "BID_MAP_TABLE_COLUMN_COUNT",
    "BID_MAP_TABLE_COLUMN_COUNT_V2",
    "BidMap",
    "BidMapSummary",
    "load_bid_map_table",
    "parse_bid_map_row",
    "parse_bid_map_table",
)
