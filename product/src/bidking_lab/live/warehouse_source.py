"""Player-custom warehouse source qualification.

PW1 exposes one privacy-safe, current-session settlement owner-evidence status.
PW2 combines that status only with a verified map-table ``auction_mode``:

* trusted open + valid owner block -> player-custom;
* trusted open + absent owner block -> source-unknown;
* trusted sealed + absent owner block -> official-random;
* contradictory/malformed owner evidence or an untrusted map contract stays
  source-unknown and carries an explicit quarantine requirement.

Localized names, UserLog, values and item counts are never source evidence.
Qualification remains metadata-only: it does not alter parser/replay
eligibility, posterior inputs or live quote calculation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
from typing import Any, Literal, Mapping


PLAYER_CUSTOM_WAREHOUSE = "player_custom_warehouse"
POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE = "postupdate_unknown_warehouse_source"
OFFICIAL_RANDOM_WAREHOUSE = "official_random_warehouse"

WarehouseSourceQualification = Literal[
    "player_custom_warehouse",
    "postupdate_unknown_warehouse_source",
    "official_random_warehouse",
]

WAREHOUSE_SOURCE_QUALIFICATIONS = frozenset(
    {
        PLAYER_CUSTOM_WAREHOUSE,
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
        OFFICIAL_RANDOM_WAREHOUSE,
    }
)

WAREHOUSE_TABLE_VERIFIED = "verified"
WAREHOUSE_TABLE_UNVERIFIED = "unverified"
WAREHOUSE_TABLE_MISSING = "missing"
WAREHOUSE_TABLE_STALE = "stale"
WAREHOUSE_TABLE_CONFLICT = "conflict"
WAREHOUSE_TABLE_STATUSES = frozenset(
    {
        WAREHOUSE_TABLE_VERIFIED,
        WAREHOUSE_TABLE_UNVERIFIED,
        WAREHOUSE_TABLE_MISSING,
        WAREHOUSE_TABLE_STALE,
        WAREHOUSE_TABLE_CONFLICT,
    }
)

PREUPDATE_OFFICIAL_GENERATION = "preupdate_official_random"
LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION = (
    "postupdate_player_warehouse_activity"
)
UPDATE606_PLAYER_WAREHOUSE_GENERATION = "cd4af1-2026-08-13-606"
D66AEE_PLAYER_WAREHOUSE_GENERATION = "d66aee-2026-08-20-599"
POSTUPDATE_PLAYER_WAREHOUSE_GENERATION = "4ae819-2026-08-27-665"
OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION = "eb1e8f-2026-09-03-711"
HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION = "68171c-2026-09-03-907"
OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION = "b049c3-2026-09-10-597"
OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION = "76b7ca-2026-10-08-723"
UNKNOWN_WAREHOUSE_GENERATION = "unknown_warehouse_generation"
WAREHOUSE_SOURCE_GENERATIONS = frozenset(
    {
        PREUPDATE_OFFICIAL_GENERATION,
        LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        UPDATE606_PLAYER_WAREHOUSE_GENERATION,
        D66AEE_PLAYER_WAREHOUSE_GENERATION,
        POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
        UNKNOWN_WAREHOUSE_GENERATION,
    }
)
RANKED_WAREHOUSE_MAP_PREFIXES = frozenset({55, 56})

# The author confirmed this is a new 2026-07-23 activity. Historical captures
# retain their old official-random semantics even when their map name starts
# with ``未知`` or their map id is 2601. The activity end is not yet known.
PLAYER_WAREHOUSE_ACTIVITY_START_DATE = date(2026, 7, 23)
UPDATE606_GENERATION_START_DATE = date(2026, 8, 13)
D66AEE_GENERATION_START_DATE = date(2026, 8, 20)
CURRENT_GENERATION_START_DATE = date(2026, 8, 27)
CURRENT_GENERATION_START_TIME = datetime(
    2026,
    8,
    27,
    12,
    tzinfo=timezone(timedelta(hours=8)),
)
OFFICIAL_20260903_START_DATE = date(2026, 9, 3)
UPDATED_BIDMAP_BUILD = "24962364"
UPDATED_BIDMAP_SHA256 = (
    "7d4d810b63441a462c486a1e308fd7a1db93712de2713cfe3e5943383f8bc006"
)
UPDATED_BIDMAP_NAME_TABLE_SHA256 = (
    "06f69afd8c2196ab945d829e9140489e2f0f4df8f7af20f614565d7af6447e52"
)
UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256 = (
    "d51d12095f867b8325da57721a9d829dee078e3f777b1ac867ee9eb6874a23ba"
)
OFFICIAL_20260903_BIDMAP_BUILD = "25094235"
OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256 = (
    "7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc"
)
OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256 = (
    "b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3"
)
OFFICIAL_20260903_PACKAGE_BIDMAP = {
    "bytes": 138_356,
    "sha256": "34be4bbb51a730e4b410dad87490b7ecb78f7e3b9034c1b88c20743b0a7d9b5e",
    "decoded_sha256": (
        "e4edc0d3cb6109f5f3409f4a14b54df1b1becceffcd8968991527b9636c61625"
    ),
    "decoded_bytes": 103_766,
    "raw_text_sha256": (
        "9a3b8fc6771f0398918fb8aba9bb524efb814843d470318830f40005fc242cfa"
    ),
    "raw_text_bytes": 104_014,
}
OFFICIAL_20260903_PACKAGE_ITEM = {
    "bytes": 689_916,
    "sha256": "9cdcca9b2f55133d647a303fc38a6b6c9f9eb33630cf51008e09c8a0c0d9ee2e",
    "decoded_sha256": (
        "04e79becd4913f3dd9f51823e8173b358a37a6b9f681909b7272e0f025db0f99"
    ),
    "decoded_bytes": 517_435,
    "raw_text_sha256": (
        "72c3430e82e82a4b01a1fa42e79335b50ab6bbbb7f09c9b438b2511caef566c3"
    ),
    "raw_text_bytes": 518_864,
}
HOTFIX_20260903_BIDMAP_BUILD = "25096377"
HOTFIX_20260903_PACKAGE_BIDMAP = {
    "bytes": 138_352,
    "sha256": "65174e54a6ac3675c39e19b193cb70df06d19f56e920242a2a50e665f53bd505",
    "decoded_sha256": (
        "c68b07b6b4356cf91400482e7ee7f39038372339905d69f03b2590d0f29a9092"
    ),
    "decoded_bytes": 103_763,
    "raw_text_sha256": (
        "f93a30b6bbcad22a003cf1363919af1d42a2eb600ec9ee006dc37865a0fc15a6"
    ),
    "raw_text_bytes": 104_011,
}
HOTFIX_20260903_PACKAGE_ITEM = {
    "bytes": 690_668,
    "sha256": "00f9cc95e6951f436522a214bd16641e3e8f813ec4455999c72de6c9bd2de9e4",
    "decoded_sha256": (
        "298307a54a1eccb0a66df741b6d3177634ce78068efddef52779059f90c0d257"
    ),
    "decoded_bytes": 518_000,
    "raw_text_sha256": (
        "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
    ),
    "raw_text_bytes": 519_429,
}
OFFICIAL_20260910_BIDMAP_BUILD = "25221911"
OFFICIAL_20260910_PACKAGE_BIDMAP = {
    "bytes": 138_300,
    "sha256": "03fb51becf3793d197a5901140d3b0ebbb0f1fe8502782f0793eed735b5f4f16",
    "decoded_sha256": "32dfa2d3af71e289adc7c123fb3d696c77563a6c799f5be0087155462d2e5b0a",
    "decoded_bytes": 103_725,
    "raw_text_sha256": "7e5606adbd50b5e106265757704e87af103a9edaa21922fee061aa117b77e6b7",
    "raw_text_bytes": 103_973,
}
OFFICIAL_20260910_PACKAGE_ITEM = {
    "bytes": 692_184,
    "sha256": "cb15d92ba307831d3da42875a6c542b5675baa48e74d37290cfbcd1a86594949",
    "decoded_sha256": "95172eef82711ff111f52055095edfcfbea84080b23ebb8d84b73811d2920b9f",
    "decoded_bytes": 519_138,
    "raw_text_sha256": "8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47",
    "raw_text_bytes": 520_572,
}

OFFICIAL_20261008_BIDMAP_BUILD = "25795126"
OFFICIAL_20261008_PACKAGE_BIDMAP = {
    "bytes": 138300,
    "sha256": "5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020",
    "decoded_sha256": "ede496337b027bea71625d2aaf2a838eacc08d39d6c3a4efc1ef18dd178e6e85",
    "decoded_bytes": 103724,
    "raw_text_sha256": "67918be0098eb8f65772c34e0ef14bded7929878a832e64e448d2d9b724ba211",
    "raw_text_bytes": 103972,
}
OFFICIAL_20261008_PACKAGE_ITEM = {
    "bytes": 746484,
    "sha256": "380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40",
    "decoded_sha256": "b7c541b971e01615a8fbf5b5420934937e74a29068672427a663dd30f11f0901",
    "decoded_bytes": 559862,
    "raw_text_sha256": "125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372",
    "raw_text_bytes": 561370,
}

D66AEE_BIDMAP_BUILD = "24830618"
D66AEE_BIDMAP_NAME_TABLE_SHA256 = (
    "6e2f8aa772c23184042cdfb0b4ce99c822676707378f9a1dc2bcaf4c10ae237f"
)
D66AEE_BIDMAP_SOURCE_CONTRACT_SHA256 = (
    "bdae544b3b324b4037d99fbc07e94e1ae55a20d9777f1f06ba15db32e54895f5"
)

_WAREHOUSE_MAP_PROFILES: Mapping[str, Mapping[str, Any]] = {
    D66AEE_PLAYER_WAREHOUSE_GENERATION: {
        "build": D66AEE_BIDMAP_BUILD,
        "name_sha256": D66AEE_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": D66AEE_BIDMAP_SOURCE_CONTRACT_SHA256,
        "unopened_map_ids": frozenset({5506, 6001}),
    },
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION: {
        "build": UPDATED_BIDMAP_BUILD,
        "name_sha256": UPDATED_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
        "unopened_map_ids": frozenset({5506}),
    },
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION: {
        "build": OFFICIAL_20260903_BIDMAP_BUILD,
        "name_sha256": OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": (
            OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "unopened_map_ids": frozenset({5506, 6001}),
    },
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION: {
        "build": HOTFIX_20260903_BIDMAP_BUILD,
        # The hotfix changes only BidMap display-resource columns.  The exact
        # name + auction-mode projection consumed by PW2 remains byte-equal.
        "name_sha256": OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": (
            OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256
        ),
        "unopened_map_ids": frozenset({5506, 6001}),
    },
    OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION: {
        "build": OFFICIAL_20260910_BIDMAP_BUILD,
        # The b049 mode10 row still denotes the 6001 auction hall; its exact
        # parsed name/auction-mode projection is unchanged, not ordinary sealed.
        "name_sha256": OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256,
        "unopened_map_ids": frozenset({5506, 6001}),
    },
    OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION: {
        "build": OFFICIAL_20261008_BIDMAP_BUILD,
        # Map names and auction modes retain the b049 projection. The new hall
        # 6010 distribution and runtime opening remain separately qualified.
        "name_sha256": OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256,
        "source_contract_sha256": OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256,
        "unopened_map_ids": frozenset({5506, 6001}),
    },
}
_EXACT_POST_SEPTEMBER_GENERATIONS = frozenset(
    {
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
    }
)
UPDATE606_UNOPENED_MAP_IDS = frozenset({5506, 6001})
CURRENT_OBSERVED_NO_DISTRIBUTION_MAP_IDS = frozenset({6001})

OWNER_EVIDENCE_ABSENT = "absent"
OWNER_EVIDENCE_VALID = "valid"
OWNER_EVIDENCE_CONFLICT = "malformed_conflict"
OWNER_EVIDENCE_STATUSES = frozenset(
    {
        OWNER_EVIDENCE_ABSENT,
        OWNER_EVIDENCE_VALID,
        OWNER_EVIDENCE_CONFLICT,
    }
)
TRUSTED_AUCTION_MODES = frozenset(
    {"auction_hall", "open", "sealed", "training"}
)

DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV = (
    "BIDKING_DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION"
)


@dataclass(frozen=True)
class PlayerWarehouseConfig:
    """Creation-screen values kept as independent diagnostic facts.

    ``auction_fee`` and ``minimum_total_value`` are different game fields.
    Neither is a bid/quote floor.
    """

    key: str
    label: str
    item_count_cap: int
    auction_fee: int
    minimum_total_value: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "units": {
                "item_count_cap": "items",
                "auction_fee": "silver",
                "minimum_total_value": "collectible_value",
            },
            "quote_clamp": False,
        }


PLAYER_WAREHOUSE_CONFIGS: Mapping[int, PlayerWarehouseConfig] = {
    2101: PlayerWarehouseConfig(
        "courier_blind_box_pile",
        "快递盲盒堆",
        item_count_cap=15,
        auction_fee=1_000,
        minimum_total_value=20_000,
    ),
    2201: PlayerWarehouseConfig(
        "abandoned_warehouse",
        "废弃仓库",
        item_count_cap=20,
        auction_fee=5_000,
        minimum_total_value=150_000,
    ),
    2301: PlayerWarehouseConfig(
        "shipping_container",
        "航运集装箱",
        item_count_cap=20,
        auction_fee=20_000,
        minimum_total_value=300_000,
    ),
    2401: PlayerWarehouseConfig(
        "vacant_villa",
        "空置别墅",
        item_count_cap=25,
        auction_fee=50_000,
        minimum_total_value=500_000,
    ),
    2501: PlayerWarehouseConfig(
        "sealed_shipwreck_hold",
        "沉船密封仓",
        item_count_cap=30,
        auction_fee=100_000,
        minimum_total_value=1_000_000,
    ),
    2601: PlayerWarehouseConfig(
        "secret_auction",
        "隐秘拍卖会",
        item_count_cap=40,
        auction_fee=500_000,
        minimum_total_value=5_000_000,
    ),
}


@dataclass(frozen=True)
class WarehouseSourceResult:
    qualification: WarehouseSourceQualification
    reason: str
    map_id: int | None
    map_name: str
    map_name_source: str
    table_status: str
    explicit_source: str
    source_generation: str
    official_distribution_eligible: bool
    map_table_build: str = ""
    map_table_sha256: str = ""
    expected_map_table_sha256: str = ""
    map_source_contract_sha256: str = ""
    expected_map_source_contract_sha256: str = ""
    map_profile_generation: str = ""
    game_version: str = ""
    auction_mode: str = ""
    owner_evidence_status: str = ""
    source_conflict: bool = False
    quarantine_required: bool = False
    parser_replay_eligible: bool = True
    quote_policy: str = "advisory_only_no_clamp"
    config: PlayerWarehouseConfig | None = None

    @property
    def clean_eval_eligible(self) -> bool:
        return self.official_distribution_eligible

    def as_dict(self) -> dict[str, Any]:
        return {
            "qualification": self.qualification,
            "reason": self.reason,
            "map_id": self.map_id,
            "map_name": self.map_name,
            "map_name_source": self.map_name_source,
            "table_status": self.table_status,
            "explicit_source": self.explicit_source,
            "source_generation": self.source_generation,
            "official_distribution_eligible": self.official_distribution_eligible,
            "map_table_build": self.map_table_build,
            "map_table_sha256": self.map_table_sha256,
            "expected_map_table_sha256": self.expected_map_table_sha256,
            "map_source_contract_sha256": self.map_source_contract_sha256,
            "expected_map_source_contract_sha256": (
                self.expected_map_source_contract_sha256
            ),
            "map_profile_generation": self.map_profile_generation,
            "game_version": self.game_version,
            "auction_mode": self.auction_mode,
            "owner_evidence_status": self.owner_evidence_status,
            "source_conflict": self.source_conflict,
            "quarantine_required": self.quarantine_required,
            "clean_eval_eligible": self.clean_eval_eligible,
            "parser_replay_eligible": self.parser_replay_eligible,
            "quote_policy": self.quote_policy,
            "config": self.config.as_dict() if self.config is not None else None,
        }


def _map_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _qualification_disabled() -> bool:
    return (
        os.environ.get(DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV, "").strip()
        == "1"
    )


def is_ranked_warehouse_map(map_id: Any) -> bool:
    """Return whether the numeric map id belongs to a ranked 55xx/56xx family."""

    mid = _map_id(map_id)
    return mid is not None and mid // 100 in RANKED_WAREHOUSE_MAP_PREFIXES


def player_warehouse_config(map_id: Any) -> PlayerWarehouseConfig | None:
    """Return the creation-screen family config for a regular 21xx-26xx map."""

    mid = _map_id(map_id)
    if mid is None:
        return None
    return PLAYER_WAREHOUSE_CONFIGS.get((mid // 100) * 100 + 1)


def warehouse_map_name_table_sha256(maps: Mapping[int, Any] | None) -> str:
    """Hash only the map-id/localized-name contract used for qualification."""

    if maps is None:
        return ""
    rows: list[tuple[int, str]] = []
    for raw_map_id, entry in maps.items():
        map_id = _map_id(raw_map_id)
        if map_id is None:
            continue
        if isinstance(entry, Mapping):
            name = entry.get("name")
        else:
            name = getattr(entry, "name", None)
        rows.append((map_id, str(name or "").strip()))
    payload = "".join(
        f"{map_id}\t{name}\n"
        for map_id, name in sorted(rows)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _map_entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(key)
    return getattr(entry, key, None)


def warehouse_map_source_contract_sha256(
    maps: Mapping[int, Any] | None,
) -> str:
    """Hash the exact map fields allowed to decide PW2 source qualification."""

    if maps is None:
        return ""
    rows: list[tuple[int, str, str]] = []
    for raw_map_id, entry in maps.items():
        map_id = _map_id(raw_map_id)
        if map_id is None:
            continue
        rows.append(
            (
                map_id,
                str(_map_entry_value(entry, "name") or "").strip(),
                str(_map_entry_value(entry, "auction_mode") or "").strip(),
            )
        )
    payload = "".join(
        f"{map_id}\t{name}\t{auction_mode}\n"
        for map_id, name, auction_mode in sorted(rows)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def warehouse_map_table_status(
    maps: Mapping[int, Any] | None,
    *,
    expected_sha256: str = UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256,
    profile_generation: str | None = None,
) -> str:
    if maps is None:
        return WAREHOUSE_TABLE_MISSING
    if profile_generation is not None:
        profile = _WAREHOUSE_MAP_PROFILES.get(profile_generation)
        if profile is None:
            return WAREHOUSE_TABLE_UNVERIFIED
        expected_sha256 = str(profile["source_contract_sha256"])
    return (
        WAREHOUSE_TABLE_VERIFIED
        if warehouse_map_source_contract_sha256(maps) == expected_sha256
        else WAREHOUSE_TABLE_STALE
    )


def warehouse_generation_from_capture_time(value: Any) -> str:
    """Classify historical captures through 2026-09-02 by rollout time."""

    if type(value) is not str:  # noqa: E721 - do not invoke conversion hooks
        return UNKNOWN_WAREHOUSE_GENERATION
    text = value.strip()
    if not text:
        return UNKNOWN_WAREHOUSE_GENERATION
    normalized = text.replace("/", "-")
    try:
        capture_date = date.fromisoformat(normalized[:10])
    except ValueError:
        return UNKNOWN_WAREHOUSE_GENERATION
    if capture_date >= OFFICIAL_20260903_START_DATE:
        return UNKNOWN_WAREHOUSE_GENERATION
    if capture_date > CURRENT_GENERATION_START_DATE:
        return POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    if capture_date == CURRENT_GENERATION_START_DATE:
        if len(normalized) == 10:
            return UNKNOWN_WAREHOUSE_GENERATION
        try:
            capture_time = datetime.fromisoformat(
                normalized[:-1] + "+00:00"
                if normalized.endswith("Z")
                else normalized
            )
        except ValueError:
            return UNKNOWN_WAREHOUSE_GENERATION
        if capture_time.tzinfo is None:
            capture_time = capture_time.replace(
                tzinfo=CURRENT_GENERATION_START_TIME.tzinfo
            )
        return (
            POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
            if capture_time >= CURRENT_GENERATION_START_TIME
            else D66AEE_PLAYER_WAREHOUSE_GENERATION
        )
    if capture_date >= D66AEE_GENERATION_START_DATE:
        return D66AEE_PLAYER_WAREHOUSE_GENERATION
    if capture_date >= UPDATE606_GENERATION_START_DATE:
        return UPDATE606_PLAYER_WAREHOUSE_GENERATION
    if capture_date >= PLAYER_WAREHOUSE_ACTIVITY_START_DATE:
        return LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    return PREUPDATE_OFFICIAL_GENERATION


def warehouse_package_generation_from_exact_inputs(
    bid_map_package: Any,
    item_package: Any,
) -> str:
    """Identify one shipped post-September table pair from exact package bytes.

    Older generations remain available through the historical capture-time
    resolver.  The live post-September path deliberately recognizes only the
    exact current pair, so an old client cannot label a new game session from
    its wall-clock date.
    """

    if type(bid_map_package) is not bytes or type(item_package) is not bytes:  # noqa: E721
        return UNKNOWN_WAREHOUSE_GENERATION
    if (
        len(bid_map_package) == OFFICIAL_20260903_PACKAGE_BIDMAP["bytes"]
        and hashlib.sha256(bid_map_package).hexdigest()
        == OFFICIAL_20260903_PACKAGE_BIDMAP["sha256"]
        and len(item_package) == OFFICIAL_20260903_PACKAGE_ITEM["bytes"]
        and hashlib.sha256(item_package).hexdigest()
        == OFFICIAL_20260903_PACKAGE_ITEM["sha256"]
    ):
        return OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    if (
        len(bid_map_package) == HOTFIX_20260903_PACKAGE_BIDMAP["bytes"]
        and hashlib.sha256(bid_map_package).hexdigest()
        == HOTFIX_20260903_PACKAGE_BIDMAP["sha256"]
        and len(item_package) == HOTFIX_20260903_PACKAGE_ITEM["bytes"]
        and hashlib.sha256(item_package).hexdigest()
        == HOTFIX_20260903_PACKAGE_ITEM["sha256"]
    ):
        return HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    if (
        len(bid_map_package) == OFFICIAL_20260910_PACKAGE_BIDMAP["bytes"]
        and hashlib.sha256(bid_map_package).hexdigest() == OFFICIAL_20260910_PACKAGE_BIDMAP["sha256"]
        and len(item_package) == OFFICIAL_20260910_PACKAGE_ITEM["bytes"]
        and hashlib.sha256(item_package).hexdigest() == OFFICIAL_20260910_PACKAGE_ITEM["sha256"]
    ):
        return OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION
    if (
        len(bid_map_package) == OFFICIAL_20261008_PACKAGE_BIDMAP["bytes"]
        and hashlib.sha256(bid_map_package).hexdigest() == OFFICIAL_20261008_PACKAGE_BIDMAP["sha256"]
        and len(item_package) == OFFICIAL_20261008_PACKAGE_ITEM["bytes"]
        and hashlib.sha256(item_package).hexdigest() == OFFICIAL_20261008_PACKAGE_ITEM["sha256"]
    ):
        return OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION
    return UNKNOWN_WAREHOUSE_GENERATION


def warehouse_generation_from_live_evidence(
    capture_time: Any,
    *,
    game_version: Any,
    package_generation: Any,
) -> str:
    """Resolve a live generation without using a post-September date as proof.

    Capture time remains the compatibility owner for historical rows before
    2026-09-03.  From that date onward, both the on-disk YooAsset version and
    the exact packaged Item/BidMap pair must identify the same generation.
    Unknown, prefixed, missing, e8, or mixed evidence stays unknown.
    """

    historical = warehouse_generation_from_capture_time(capture_time)
    capture_date: date | None = None
    if type(capture_time) is str:  # noqa: E721
        text = capture_time.strip().replace("/", "-")
        try:
            capture_date = date.fromisoformat(text[:10])
        except ValueError:
            capture_date = None
    if capture_date is not None and capture_date < OFFICIAL_20260903_START_DATE:
        return historical
    if (
        type(game_version) is str  # noqa: E721
        and type(package_generation) is str  # noqa: E721
        and package_generation in _EXACT_POST_SEPTEMBER_GENERATIONS
        and str.__eq__(game_version, package_generation)
    ):
        return package_generation
    return UNKNOWN_WAREHOUSE_GENERATION


def classify_warehouse_source(
    map_id: Any,
    map_name: Any,
    *,
    map_name_source: str = "bid_map_table",
    table_status: str = WAREHOUSE_TABLE_VERIFIED,
    explicit_source: str | None = None,
    source_generation: str = UNKNOWN_WAREHOUSE_GENERATION,
    map_table_build: str = "",
    map_table_sha256: str = "",
    expected_map_table_sha256: str = "",
    map_source_contract_sha256: str = "",
    expected_map_source_contract_sha256: str = "",
    map_profile_generation: str = "",
    game_version: str = "",
    auction_mode: Any = "",
    owner_evidence_status: Any | None = None,
) -> WarehouseSourceResult:
    """Classify source without changing quote behavior.

    ``explicit_source`` is reserved for a future stable protocol field or an
    evidence-backed manual label.  Map names and value/count heuristics cannot
    override it because they do not identify the warehouse source.
    """

    mid = _map_id(map_id)
    name = str(map_name or "").strip()
    source = str(explicit_source or "").strip()
    generation = str(source_generation or UNKNOWN_WAREHOUSE_GENERATION).strip()
    if generation not in WAREHOUSE_SOURCE_GENERATIONS:
        generation = UNKNOWN_WAREHOUSE_GENERATION
    profile_generation = (
        map_profile_generation
        if type(map_profile_generation) is str  # noqa: E721
        and map_profile_generation in _WAREHOUSE_MAP_PROFILES
        else generation
    )
    if profile_generation not in _WAREHOUSE_MAP_PROFILES:
        profile_generation = ""
    resolved_game_version = (
        game_version
        if type(game_version) is str  # noqa: E721 - identity is exact text
        else ""
    )
    status = str(table_status or WAREHOUSE_TABLE_UNVERIFIED).strip().lower()
    if status not in WAREHOUSE_TABLE_STATUSES:
        status = WAREHOUSE_TABLE_UNVERIFIED
    config = player_warehouse_config(mid)
    resolved_auction_mode = (
        auction_mode.strip()
        if type(auction_mode) is str  # noqa: E721 - reject coercive subclasses
        else ""
    )
    table_identity = {
        "map_table_build": str(map_table_build or ""),
        "map_table_sha256": str(map_table_sha256 or ""),
        "expected_map_table_sha256": str(expected_map_table_sha256 or ""),
        "map_source_contract_sha256": str(map_source_contract_sha256 or ""),
        "expected_map_source_contract_sha256": str(
            expected_map_source_contract_sha256 or ""
        ),
        "map_profile_generation": profile_generation,
        "game_version": resolved_game_version,
    }

    # A current package profile is not evidence that the running game has the
    # same generation.  In particular e8/missing/prefixed version strings must
    # remain retained-but-quarantined rather than borrowing eb1 authority.
    generation_profile_mismatch = (
        generation in _WAREHOUSE_MAP_PROFILES
        and profile_generation in _WAREHOUSE_MAP_PROFILES
        and generation != profile_generation
    )
    current_profile_unverified = (
        profile_generation in _EXACT_POST_SEPTEMBER_GENERATIONS
        and (
            generation != profile_generation
            or resolved_game_version != profile_generation
            or status != WAREHOUSE_TABLE_VERIFIED
        )
    )
    if generation_profile_mismatch or current_profile_unverified:
        return WarehouseSourceResult(
            qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            reason=(
                "pw2_map_contract_untrusted"
                if status != WAREHOUSE_TABLE_VERIFIED
                else "warehouse_generation_unverified"
            ),
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            auction_mode=resolved_auction_mode,
            owner_evidence_status=(
                owner_evidence_status
                if type(owner_evidence_status) is str  # noqa: E721
                else ""
            ),
            source_conflict=True,
            quarantine_required=True,
            config=None,
            **table_identity,
        )

    # The update606 Item associations and parent-map links do not provide an
    # opened-map distribution contract for either new route.  Current 6001
    # settlement occurrence is accepted as non-distribution evidence only.
    # This gate must
    # precede both the rollback switch and legacy/ranked fallbacks: neither may
    # turn missing distribution authority into an official-random claim.
    current_observed_without_distribution = (
        profile_generation == POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
        and mid in CURRENT_OBSERVED_NO_DISTRIBUTION_MAP_IDS
    )
    unopened_map_ids = (
        _WAREHOUSE_MAP_PROFILES.get(profile_generation, {}).get(
            "unopened_map_ids",
            UPDATE606_UNOPENED_MAP_IDS,
        )
    )
    if mid in unopened_map_ids and not current_observed_without_distribution:
        return WarehouseSourceResult(
            qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            reason=(
                "official_20260903_map_unopened"
                if profile_generation in _EXACT_POST_SEPTEMBER_GENERATIONS
                else "update606_map_unopened"
            ),
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            auction_mode=resolved_auction_mode,
            owner_evidence_status=(
                owner_evidence_status
                if type(owner_evidence_status) is str  # noqa: E721
                else ""
            ),
            source_conflict=True,
            quarantine_required=True,
            config=None,
            **table_identity,
        )

    if _qualification_disabled():
        return WarehouseSourceResult(
            qualification=OFFICIAL_RANDOM_WAREHOUSE,
            reason="qualification_disabled_rollback",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=True,
            config=None,
            **table_identity,
        )

    if owner_evidence_status is not None:
        evidence_status = (
            owner_evidence_status
            if type(owner_evidence_status) is str  # noqa: E721
            and owner_evidence_status in OWNER_EVIDENCE_STATUSES
            else OWNER_EVIDENCE_CONFLICT
        )
        pw2_common = {
            "map_id": mid,
            "map_name": name,
            "map_name_source": str(map_name_source or ""),
            "table_status": status,
            "explicit_source": source,
            "source_generation": generation,
            "map_table_build": table_identity["map_table_build"],
            "map_table_sha256": table_identity["map_table_sha256"],
            "expected_map_table_sha256": table_identity[
                "expected_map_table_sha256"
            ],
            "map_source_contract_sha256": table_identity[
                "map_source_contract_sha256"
            ],
            "expected_map_source_contract_sha256": table_identity[
                "expected_map_source_contract_sha256"
            ],
            "map_profile_generation": table_identity[
                "map_profile_generation"
            ],
            "game_version": table_identity["game_version"],
            "auction_mode": resolved_auction_mode,
            "owner_evidence_status": evidence_status,
        }

        def pw2_result(
            qualification: WarehouseSourceQualification,
            reason: str,
            *,
            eligible: bool,
            conflict: bool = False,
            resolved_config: PlayerWarehouseConfig | None = None,
        ) -> WarehouseSourceResult:
            return WarehouseSourceResult(
                qualification=qualification,
                reason=reason,
                official_distribution_eligible=eligible,
                source_conflict=conflict,
                quarantine_required=conflict,
                config=resolved_config,
                **pw2_common,
            )

        if generation == PREUPDATE_OFFICIAL_GENERATION:
            if evidence_status != OWNER_EVIDENCE_ABSENT:
                return pw2_result(
                    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                    "pw2_preupdate_owner_block_conflict",
                    eligible=False,
                    conflict=True,
                )
            return pw2_result(
                OFFICIAL_RANDOM_WAREHOUSE,
                "preupdate_official_random_generation",
                eligible=True,
            )

        if generation not in _WAREHOUSE_MAP_PROFILES:
            map_contract_untrusted = (
                status != WAREHOUSE_TABLE_VERIFIED
                or mid is None
                or resolved_auction_mode not in TRUSTED_AUCTION_MODES
            )
            return pw2_result(
                POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                (
                    "pw2_map_contract_untrusted"
                    if map_contract_untrusted
                    else "warehouse_generation_unverified"
                ),
                eligible=False,
                conflict=(
                    map_contract_untrusted
                    or evidence_status != OWNER_EVIDENCE_ABSENT
                ),
            )

        if (
            status != WAREHOUSE_TABLE_VERIFIED
            or mid is None
            or resolved_auction_mode not in TRUSTED_AUCTION_MODES
        ):
            return pw2_result(
                POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                "pw2_map_contract_untrusted",
                eligible=False,
                conflict=True,
            )

        if evidence_status == OWNER_EVIDENCE_CONFLICT:
            return pw2_result(
                POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                "pw2_owner_evidence_conflict",
                eligible=False,
                conflict=True,
            )

        if evidence_status == OWNER_EVIDENCE_VALID:
            if resolved_auction_mode in {"auction_hall", "open"}:
                return pw2_result(
                    PLAYER_CUSTOM_WAREHOUSE,
                    f"pw2_{resolved_auction_mode}_valid_owner_block",
                    eligible=False,
                    resolved_config=config,
                )
            return pw2_result(
                POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                f"pw2_{resolved_auction_mode}_owner_block_conflict",
                eligible=False,
                conflict=True,
            )

        if resolved_auction_mode in {"auction_hall", "open"}:
            return pw2_result(
                POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                f"pw2_{resolved_auction_mode}_owner_block_absent",
                eligible=False,
            )
        if resolved_auction_mode == "sealed":
            return pw2_result(
                OFFICIAL_RANDOM_WAREHOUSE,
                "pw2_sealed_owner_block_absent",
                eligible=True,
            )
        return pw2_result(
            POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            "pw2_training_owner_block_absent",
            eligible=False,
        )

    if source and source not in WAREHOUSE_SOURCE_QUALIFICATIONS:
        return WarehouseSourceResult(
            qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            reason="unsupported_explicit_source",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            config=None,
            **table_identity,
        )

    if source == PLAYER_CUSTOM_WAREHOUSE:
        if is_ranked_warehouse_map(mid):
            return WarehouseSourceResult(
                qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                reason="protocol_ranked_source_conflict",
                map_id=mid,
                map_name=name,
                map_name_source=str(map_name_source or ""),
                table_status=status,
                explicit_source=source,
                source_generation=generation,
                official_distribution_eligible=False,
                config=None,
                **table_identity,
            )
        return WarehouseSourceResult(
            qualification=PLAYER_CUSTOM_WAREHOUSE,
            reason="explicit_player_custom_source",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            config=config,
            **table_identity,
        )

    if source == OFFICIAL_RANDOM_WAREHOUSE:
        return WarehouseSourceResult(
            qualification=OFFICIAL_RANDOM_WAREHOUSE,
            reason="explicit_official_random_source",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=True,
            config=None,
            **table_identity,
        )

    if generation == PREUPDATE_OFFICIAL_GENERATION:
        return WarehouseSourceResult(
            qualification=OFFICIAL_RANDOM_WAREHOUSE,
            reason="preupdate_official_random_generation",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=True,
            config=None,
            **table_identity,
        )

    if generation not in _WAREHOUSE_MAP_PROFILES:
        return WarehouseSourceResult(
            qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            reason="warehouse_generation_unverified",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            source_conflict=True,
            quarantine_required=True,
            config=None,
            **table_identity,
        )

    if mid is None:
        return WarehouseSourceResult(
            qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            reason="missing_map_id",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=False,
            config=None,
            **table_identity,
        )

    if is_ranked_warehouse_map(mid):
        return WarehouseSourceResult(
            qualification=OFFICIAL_RANDOM_WAREHOUSE,
            reason="ranked_map_excludes_player_custom",
            map_id=mid,
            map_name=name,
            map_name_source=str(map_name_source or ""),
            table_status=status,
            explicit_source=source,
            source_generation=generation,
            official_distribution_eligible=True,
            config=None,
            **table_identity,
        )

    return WarehouseSourceResult(
        qualification=POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
        reason="postupdate_non_ranked_source_unresolved",
        map_id=mid,
        map_name=name,
        map_name_source=str(map_name_source or ""),
        table_status=status,
        explicit_source=source,
        source_generation=generation,
        official_distribution_eligible=False,
        config=None,
        **table_identity,
    )


def classify_from_map_table(
    map_id: Any,
    maps: Mapping[int, Any] | None,
    *,
    explicit_source: str | None = None,
    source_generation: str = UNKNOWN_WAREHOUSE_GENERATION,
    profile_generation: str | None = None,
    game_version: str = "",
    table_status: str | None = None,
    owner_evidence_status: Any | None = None,
) -> WarehouseSourceResult:
    mid = _map_id(map_id)
    name_table_sha256 = warehouse_map_name_table_sha256(maps)
    source_contract_sha256 = warehouse_map_source_contract_sha256(maps)
    resolved_profile_generation = (
        profile_generation
        if type(profile_generation) is str  # noqa: E721
        and profile_generation in _WAREHOUSE_MAP_PROFILES
        else (
            source_generation
            if source_generation in _WAREHOUSE_MAP_PROFILES
            else POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
        )
    )
    profile = _WAREHOUSE_MAP_PROFILES[resolved_profile_generation]
    resolved_table_status = (
        str(table_status)
        if table_status is not None
        else warehouse_map_table_status(
            maps,
            profile_generation=resolved_profile_generation,
        )
    )
    if maps is None:
        return classify_warehouse_source(
            mid,
            "",
            table_status=WAREHOUSE_TABLE_MISSING,
            explicit_source=explicit_source,
            source_generation=source_generation,
            map_table_build=str(profile["build"]),
            map_table_sha256=name_table_sha256,
            expected_map_table_sha256=str(profile["name_sha256"]),
            map_source_contract_sha256=source_contract_sha256,
            expected_map_source_contract_sha256=str(
                profile["source_contract_sha256"]
            ),
            map_profile_generation=resolved_profile_generation,
            game_version=game_version,
            owner_evidence_status=owner_evidence_status,
        )
    entry = maps.get(mid) if mid is not None else None
    if entry is None:
        return classify_warehouse_source(
            mid,
            "",
            table_status=WAREHOUSE_TABLE_MISSING,
            explicit_source=explicit_source,
            source_generation=source_generation,
            map_table_build=str(profile["build"]),
            map_table_sha256=name_table_sha256,
            expected_map_table_sha256=str(profile["name_sha256"]),
            map_source_contract_sha256=source_contract_sha256,
            expected_map_source_contract_sha256=str(
                profile["source_contract_sha256"]
            ),
            map_profile_generation=resolved_profile_generation,
            game_version=game_version,
            owner_evidence_status=owner_evidence_status,
        )
    name = _map_entry_value(entry, "name")
    auction_mode = _map_entry_value(entry, "auction_mode")
    return classify_warehouse_source(
        mid,
        name,
        table_status=resolved_table_status,
        explicit_source=explicit_source,
        source_generation=source_generation,
        map_table_build=str(profile["build"]),
        map_table_sha256=name_table_sha256,
        expected_map_table_sha256=str(profile["name_sha256"]),
        map_source_contract_sha256=source_contract_sha256,
        expected_map_source_contract_sha256=str(
            profile["source_contract_sha256"]
        ),
        map_profile_generation=resolved_profile_generation,
        game_version=game_version,
        auction_mode=auction_mode,
        owner_evidence_status=owner_evidence_status,
    )


def official_distribution_eligible(value: Any) -> bool:
    """Return the explicit eligibility bit, never infer official from absence."""

    if isinstance(value, WarehouseSourceResult):
        return value.official_distribution_eligible
    if isinstance(value, Mapping):
        if value.get("official_distribution_eligible") is not None:
            return value.get("official_distribution_eligible") is True
        return value.get("qualification") == OFFICIAL_RANDOM_WAREHOUSE
    return False


__all__ = (
    "OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION",
    "OFFICIAL_20261008_BIDMAP_BUILD",
    "OFFICIAL_20261008_PACKAGE_BIDMAP",
    "OFFICIAL_20261008_PACKAGE_ITEM",
    "DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV",
    "CURRENT_GENERATION_START_DATE",
    "CURRENT_GENERATION_START_TIME",
    "D66AEE_GENERATION_START_DATE",
    "D66AEE_BIDMAP_BUILD",
    "D66AEE_BIDMAP_NAME_TABLE_SHA256",
    "D66AEE_BIDMAP_SOURCE_CONTRACT_SHA256",
    "D66AEE_PLAYER_WAREHOUSE_GENERATION",
    "OFFICIAL_RANDOM_WAREHOUSE",
    "OWNER_EVIDENCE_ABSENT",
    "OWNER_EVIDENCE_CONFLICT",
    "OWNER_EVIDENCE_STATUSES",
    "OWNER_EVIDENCE_VALID",
    "LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION",
    "PLAYER_WAREHOUSE_ACTIVITY_START_DATE",
    "PLAYER_CUSTOM_WAREHOUSE",
    "PLAYER_WAREHOUSE_CONFIGS",
    "POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE",
    "POSTUPDATE_PLAYER_WAREHOUSE_GENERATION",
    "OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION",
    "OFFICIAL_20260903_START_DATE",
    "OFFICIAL_20260903_BIDMAP_BUILD",
    "OFFICIAL_20260903_BIDMAP_NAME_TABLE_SHA256",
    "OFFICIAL_20260903_BIDMAP_SOURCE_CONTRACT_SHA256",
    "OFFICIAL_20260903_PACKAGE_BIDMAP",
    "OFFICIAL_20260903_PACKAGE_ITEM",
    "HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION",
    "HOTFIX_20260903_BIDMAP_BUILD",
    "HOTFIX_20260903_PACKAGE_BIDMAP",
    "HOTFIX_20260903_PACKAGE_ITEM",
    "PREUPDATE_OFFICIAL_GENERATION",
    "PlayerWarehouseConfig",
    "RANKED_WAREHOUSE_MAP_PREFIXES",
    "UNKNOWN_WAREHOUSE_GENERATION",
    "WAREHOUSE_SOURCE_GENERATIONS",
    "WAREHOUSE_SOURCE_QUALIFICATIONS",
    "WAREHOUSE_TABLE_CONFLICT",
    "WAREHOUSE_TABLE_MISSING",
    "WAREHOUSE_TABLE_STALE",
    "WAREHOUSE_TABLE_UNVERIFIED",
    "WAREHOUSE_TABLE_VERIFIED",
    "UPDATED_BIDMAP_BUILD",
    "UPDATED_BIDMAP_NAME_TABLE_SHA256",
    "UPDATED_BIDMAP_SHA256",
    "UPDATED_BIDMAP_SOURCE_CONTRACT_SHA256",
    "UPDATE606_PLAYER_WAREHOUSE_GENERATION",
    "UPDATE606_UNOPENED_MAP_IDS",
    "UPDATE606_GENERATION_START_DATE",
    "WarehouseSourceResult",
    "classify_from_map_table",
    "classify_warehouse_source",
    "is_ranked_warehouse_map",
    "official_distribution_eligible",
    "CURRENT_OBSERVED_NO_DISTRIBUTION_MAP_IDS",
    "player_warehouse_config",
    "warehouse_generation_from_capture_time",
    "warehouse_generation_from_live_evidence",
    "warehouse_package_generation_from_exact_inputs",
    "warehouse_map_name_table_sha256",
    "warehouse_map_source_contract_sha256",
    "warehouse_map_table_status",
)
