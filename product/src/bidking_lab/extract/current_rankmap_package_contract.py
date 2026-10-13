"""Current official RankMap authority for calibrated auction distributions.

Since 2026-06-25, the established distribution-input contract has not depended
on a Drop/DropMap TextAsset.  This is not a 68171c gap or degraded fallback: the
contract binds current eligible support, exact RankMap/BidMap/Item/Map tables,
the validated value-weight algorithm, and settlement calibration.  It does not
represent that modeled result as an official item-level graph.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import re
from typing import Any, Mapping

from bidking_lab.extract.bid_map_table import (
    BID_MAP_0_3_4_CANONICAL_SHA256,
    BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256,
    BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256,
    BID_MAP_UPDATE606_CANONICAL_SHA256,
    parse_bid_map_table,
)
from bidking_lab.extract.item_table import (
    ITEM_TABLE_0_3_4_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
    ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256,
    ITEM_TABLE_UPDATE606_CANONICAL_SHA256,
    parse_item_table,
)
from bidking_lab.extract.tables import assert_uniform_columns, iter_table_rows


class Update606PackageInputError(ValueError):
    """A package input is missing, mixed-generation, or outside authority."""


def _runtime_authority_load_receipt() -> tuple[object, ...]:
    """Bind this loaded authority module to the file importlib executed."""

    spec = globals().get("__spec__")
    origin = getattr(spec, "origin", None)
    if type(origin) is not str or not os.path.isabs(origin):  # noqa: E721
        raise ImportError(
            "current_rankmap_package_contract runtime authority origin is unavailable"
        )
    try:
        with open(origin, "rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ImportError(
            "current_rankmap_package_contract runtime authority file is unavailable"
        ) from exc
    identity_before = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
        int(before.st_ctime_ns),
        int(before.st_nlink),
    )
    identity_after = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
        int(after.st_ctime_ns),
        int(after.st_nlink),
    )
    if identity_before != identity_after:
        raise ImportError(
            "current_rankmap_package_contract runtime authority changed while loading"
        )
    return (origin, *identity_after, digest.hexdigest())


_CURRENT_RANKMAP_RUNTIME_AUTHORITY_LOAD_RECEIPT = (
    _runtime_authority_load_receipt()
)


# Runtime input identities live with the protected current RankMap authority.
# The update606 builders import and re-export these values, but packaged runtime
# modules must not depend on either build-only contract module.
UPDATE606_PACKAGE_BID_MAP = {
    "bytes": 128_440,
    "sha256": "3e443478451c930e46d44b63abee5eb561a3444eae6c4db086a409671f753c48",
    "decoded_sha256": BID_MAP_UPDATE606_CANONICAL_SHA256,
    "rows": 228,
    "columns": 28,
}
UPDATE606_PACKAGE_ITEM = {
    "bytes": 639_724,
    "sha256": "1a60bfb7ea0a960d2ff06805954e3cd6ba16944b3f6c2d75d641e78d3ec9c11f",
    "decoded_sha256": ITEM_TABLE_UPDATE606_CANONICAL_SHA256,
    "rows": 1336,
    "columns": 41,
}
PREVIOUS_0_3_4_PACKAGE_BID_MAP = {
    "bytes": 129_336,
    "sha256": "12483a896692e177cf94ec272442b794f8781b16aaf013338caf1dc7b6a06166",
    "decoded_sha256": BID_MAP_0_3_4_CANONICAL_SHA256,
    "rows": 229,
    "columns": 28,
}
PREVIOUS_0_3_4_PACKAGE_ITEM = {
    "bytes": 638_024,
    "sha256": "f5106709c6a042ef6901f339321e203778df12def829a3020952f4e45033f994",
    "decoded_sha256": ITEM_TABLE_0_3_4_CANONICAL_SHA256,
    "rows": 1_336,
    "columns": 41,
}
PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP = {
    "bytes": 138_356,
    "sha256": "34be4bbb51a730e4b410dad87490b7ecb78f7e3b9034c1b88c20743b0a7d9b5e",
    "decoded_sha256": BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256,
    "rows": 246,
    "columns": 28,
}
PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM = {
    "bytes": 689_916,
    "sha256": "9cdcca9b2f55133d647a303fc38a6b6c9f9eb33630cf51008e09c8a0c0d9ee2e",
    "decoded_sha256": ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
    "rows": 1_427,
    "columns": 42,
}
PREVIOUS_ITEMS_CATALOG = {
    "bytes": 694_889,
    "canonical_sha256": "a66b1c7fdc1b6b18e13a20e6189d2f23d045b3eb1f45f9a1ca165d70c28f7f86",
    "rows": 1_427,
    "sha256": "49f4103b3e77987b801ca7b611eafe24f4d7b67c1e96bd833e7a5b694c8bbe1c",
}
CURRENT_ITEMS_CATALOG = {
    "bytes": 696_961,
    "canonical_sha256": "25828807f7527fe37702323eb4da38f8bdb4db70ae846e3798782eced810238d",
    "rows": 1_432,
    "sha256": "1c41d6bd0900510c75fe7b3fd8b68e646d75219af2d3e73f3c8aecd84cace0f1",
}
CURRENT_ADDED_ITEM_ROWS = 5
CURRENT_ADDED_ITEM_IDS_SHA256 = "2b55f8a4fd5cdb830cb8ef0274ca7f2d1e55c706fb7dae204e753c2c85c9c593"
HISTORICAL_LOCALIZATION_ITEM_ID_ALIASES = {
    1046010: 1036006,
    1046011: 1036007,
    1046012: 1036008,
}
LEGACY_PACKAGE_DROP = {
    "bytes": 294_160,
    "sha256": "9a8ed5748d05d774fb49f3aabd0769c5e81afe149d1ffaa8dcce92314948c307",
    "decoded_sha256": (
        "91a7485d1526f87419d8459284326bf9acdf3333b382de3f7bf782c26d68c3a7"
    ),
}
CURRENT_PACKAGE_BID_MAP = {
    "bytes": 138_300,
    "sha256": "03fb51becf3793d197a5901140d3b0ebbb0f1fe8502782f0793eed735b5f4f16",
    "decoded_sha256": "32dfa2d3af71e289adc7c123fb3d696c77563a6c799f5be0087155462d2e5b0a",
    "rows": 246,
    "columns": 28,
}
CURRENT_PACKAGE_ITEM = {
    "bytes": 692_184,
    "sha256": "cb15d92ba307831d3da42875a6c542b5675baa48e74d37290cfbcd1a86594949",
    "decoded_sha256": "95172eef82711ff111f52055095edfcfbea84080b23ebb8d84b73811d2920b9f",
    "rows": 1_432,
    "columns": 42,
}
# Static source compatibility is separate from the b049 calibrated model inputs.
# Advancing this table contract does not advance CURRENT_* distribution authority.
OFFICIAL_20261008_PACKAGE_GENERATION = "76b7ca-2026-10-08-723"
OFFICIAL_20261008_PACKAGE_TABLES = {
    "Item": {
        "raw_bytes": 561370,
        "raw_sha256": "125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372",
        "decoded_bytes": 559862,
        "decoded_sha256": "b7c541b971e01615a8fbf5b5420934937e74a29068672427a663dd30f11f0901",
        "bytes": 746484,
        "sha256": "380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40",
        "rows": 1506,
        "columns": 45,
    },
    "BidMap": {
        "raw_bytes": 103972,
        "raw_sha256": "67918be0098eb8f65772c34e0ef14bded7929878a832e64e448d2d9b724ba211",
        "decoded_bytes": 103724,
        "decoded_sha256": "ede496337b027bea71625d2aaf2a838eacc08d39d6c3a4efc1ef18dd178e6e85",
        "bytes": 138300,
        "sha256": "5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020",
        "rows": 246,
        "columns": 28,
    },
}
LEGACY_MAP_PAYLOADS: Mapping[str, Mapping[str, Any]] = {
    "drop_weights_all_maps_0627": {
        "source_bytes": 3_577_956,
        "source_sha256": (
            "ec5970c2c7108b1f0c03ad9b1c62d18400fc7935885938c50a0292983c935b45"
        ),
        "container": None,
        "render": "default",
        "output_maps": 101,
        "output_bytes": 2_664_405,
        "output_sha256": (
            "ce0db9680b53e393bc7a0db9e89e668ff3674701bdd524ed3b000f822bc8df11"
        ),
    },
    "drop_table_priors_0627": {
        "source_bytes": 30_211,
        "source_sha256": (
            "7ff3859d88a74e184074a8df58d11a284755376eeb6cced7befc697212a3d02c"
        ),
        "container": None,
        "render": "default",
        "output_maps": 72,
        "output_bytes": 21_239,
        "output_sha256": (
            "b9461153ccd18f82833b238f92f8599496ddc03977e889ef514b5932c870fe15"
        ),
    },
    "quality_combo_presolve_q456": {
        "source_bytes": 15_710_713,
        "source_sha256": (
            "c2cd901aaf32fabdee3cf7f2b7a233fcd049c3f37d59f62e4dd577f95130b5df"
        ),
        "container": "maps",
        "render": "compact",
        "output_maps": 123,
        "output_bytes": 15_444_339,
        "output_sha256": (
            "7b851efb21d08221c09c12c74065a86a5bdfde158f001598e62c857271cfdd42"
        ),
    },
}


PREVIOUS_0_3_4_STEAM_BUILD_ID = "24962364"
PREVIOUS_0_3_4_PACKAGE_GENERATION = "4ae819-2026-08-27-665"
PREVIOUS_OFFICIAL_20260903_STEAM_BUILD_ID = "25094235"
PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION = "eb1e8f-2026-09-03-711"
CURRENT_STEAM_BUILD_ID = "25221911"
CURRENT_PACKAGE_GENERATION = "b049c3-2026-09-10-597"
CURRENT_APP_MANIFEST = {
    "app_id": "4128580",
    "build_id": CURRENT_STEAM_BUILD_ID,
    "install_dir": "BidKing",
    "max_bytes": 4_096,
}
CURRENT_VERSION_FILE = {
    "bytes": 21,
    "sha256": "f8cadb9599e42402f3ec6d1445589ba33563d5289a3095d4b004a43a4533d401",
}
CURRENT_BUILDIN_CATALOG_BYTES = {
    "bytes": 36_579,
    "sha256": "eb02eb015f3e56d55a483a0654ebcc5a88da7e94b708fe63f51e8d31aeaf917e",
}
CURRENT_BUILDIN_CATALOG_JSON = {
    "bytes": 72_701,
    "sha256": "0cd1622266db4a9e5b6b3dba3f2fe7d8c814d7e115a18b1ed22e393f75ae0a0c",
}
CURRENT_PACKAGE_HASH_FILE = {
    "bytes": 8,
    "sha256": "77012d20eccedaa2564d3e9f6302e988c00356c0375898fcf62872901724d073",
}
CURRENT_MANIFEST = {
    "bytes": 365_256,
    "sha256": "62f4357a140946b5a33a6f8a7d0a101c33eb95ca393bce002482923d24412e3c",
}
CURRENT_TABLES_BUNDLE = {
    "bytes": 3_533_190,
    "md5": "b03724ad1e3b18fafec718901d2c82f0",
    "sha256": "581f681a35b01c481c79c4617138e17fa27fff1d23530d482ad8f4cb9eff085c",
}
CURRENT_SCRIPTS = {
    "bytes": 5_840_896,
    "mvid": "12543d94-a9a0-4755-bcf3-c5fda394fbc6",
    "sha256": "ee5a86dd3165bc0392b04ecced9f212a1afa2b8b78ff97c4767d97725d2cc744",
}


@dataclass(frozen=True)
class CurrentTextAssetIdentity:
    rows: int
    columns: int
    utf8_size: int
    utf8_sha256: str


CURRENT_TEXT_ASSET_IDENTITIES: Mapping[str, CurrentTextAssetIdentity] = {
    "Activity": CurrentTextAssetIdentity(
        44,
        19,
        6_580,
        "dcb063f062e116fa3383e9294cc465853e20aceb0a683b63b71dd46b76e6d7e9",
    ),
    "ActivityMap": CurrentTextAssetIdentity(
        31,
        20,
        8_817,
        "b91586436e977a63a6f478a82944c159e7e2a76bf9d0ab48e6ffe857947b83db",
    ),
    "BattleItem": CurrentTextAssetIdentity(
        64,
        6,
        4_468,
        "fc8c5231c723a80d4e338069aa09e32c32a5f4a3d5cc05f5e5d3e8094c844846",
    ),
    "BidMap": CurrentTextAssetIdentity(
        246,
        28,
        103_973,
        "7e5606adbd50b5e106265757704e87af103a9edaa21922fee061aa117b77e6b7",
    ),
    "Hero": CurrentTextAssetIdentity(
        22,
        21,
        9_879,
        "2d22ce736b5cd2ca1a4bb87b4830045a340684a5f0dc2e216da2672f029935d7",
    ),
    "Item": CurrentTextAssetIdentity(
        1_432,
        42,
        520_572,
        "8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47",
    ),
    "Map": CurrentTextAssetIdentity(
        12,
        27,
        2_586,
        "3d0a74f55f5d91e99116f15b1b96270e10a3e3a91db05318a8e6c8ae0cb56b1d",
    ),
    "RankMap": CurrentTextAssetIdentity(
        176,
        7,
        88_931,
        "30cafbffea27bc8ed029b74bae12caba50bd8e3ead4986e2ea46546d81aee13a",
    ),
}

CURRENT_RANKMAP_PACKAGE = {
    "bytes": 118_344,
    "decoded_bytes": 88_756,
    "decoded_sha256": "f1029d7c54852d8a60c660d0182ebdd15233dda2d1b413e762d359a9ed8c59cb",
    "sha256": "e6b4fae0caa8e71b342c79c3fc2235a9285ac4d28b918604af088b156885d1a7",
}
ROUTE_STABLE_RANKMAP_MAP_IDS = tuple(range(2101, 2108))
CHANGED_ROUTE_RANKMAP_MAP_IDS = tuple(range(5601, 5612))
CURRENT_RANKMAP_MAP_IDS = ROUTE_STABLE_RANKMAP_MAP_IDS + CHANGED_ROUTE_RANKMAP_MAP_IDS
RETIRED_SUMMER_PARTY_2026_ITEM_IDS = (
    1_016_008,
    1_016_009,
    1_016_010,
    1_016_011,
    1_016_012,
    1_016_013,
    1_016_014,
    1_096_011,
)
CURRENT_ALL_MAP_Q6_ITEM_IDS = tuple(range(1_916_039, 1_916_070))
CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS = (
    1_046_013,
    1_056_016,
    1_096_012,
    1_096_013,
    1_096_014,
    1_096_015,
)
CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS = tuple(range(1_006_010, 1_006_018))
CURRENT_RANKED_ONLY_MECH_Q6_ITEM_IDS = tuple(range(1_006_001, 1_006_008))
CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS = (1_006_018, 1_306_018)
CURRENT_S2_DIRECT_Q6_CANARIES: Mapping[int, tuple[int, tuple[int, int]]] = {
    1_916_039: (127_200, (1, 2)),
    1_916_040: (156_000, (2, 2)),
    1_916_045: (80_600, (1, 1)),
    1_916_048: (483_000, (1, 1)),
    1_916_051: (147_500, (1, 1)),
    1_916_054: (114_600, (1, 2)),
    1_916_055: (462_700, (2, 2)),
    1_916_059: (1_606_000, (3, 3)),
}
CURRENT_MODEL_UNION_ITEM_ROWS = 717
CURRENT_MODEL_UNION_ITEM_IDS_SHA256 = (
    "2c4810e6d8f94247edff451d878e93ea62ff5e67aaaf5b9309bb9e0dd0cd1766"
)
PREVIOUS_CURRENT_RANKMAP_DISTRIBUTION_INPUTS: Mapping[str, Mapping[str, Any]] = {
    "weights": {
        "bytes": 391_658,
        "maps": 18,
        "sha256": "cc931a8afde1f78cf74e265afd85f465e96d3793dbd48a56198829111adde8e5",
    },
    "priors": {
        "bytes": 4_438,
        "maps": 18,
        "sha256": "3f4fafb4c6ecf8b2b23dc71ea1d85f8e73d63ffa493f38967c5e2993e984ae27",
    },
}
PREVIOUS_ALL_MAP_DISTRIBUTION_INPUTS: Mapping[str, Mapping[str, Any]] = {
    "weights": {
        "bytes": 2_485_268,
        "maps": 101,
        "sha256": "5808321f22f3e37cf6ed1a5761893e645284f0a2d315c1e859fd0832e0d67207",
    },
    "priors": {
        "bytes": 20_740,
        "maps": 72,
        "sha256": "dad18c5102fb20a64404e1093164b321c79037d95b6b1e3b8ddc24a1c5ad5ed8",
    },
}
CURRENT_ALL_MAP_IDS_SHA256 = (
    "228cc1f5ef22b92c118e6ce6564615eb581767a3e426677503b1b2a3d77d3f64"
)
CURRENT_ALL_MAP_PRIOR_IDS_SHA256 = (
    "d3594b46539cf9ff81094ab6e3a43cc3c65e0eb33d689041f2a210692c2839f6"
)
CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS: Mapping[str, Mapping[str, Any]] = {
    "weights": {
        "bytes": 2_664_405,
        "maps": 101,
        "sha256": "ce0db9680b53e393bc7a0db9e89e668ff3674701bdd524ed3b000f822bc8df11",
    },
    "priors": {
        "bytes": 21_239,
        "maps": 72,
        "sha256": "b9461153ccd18f82833b238f92f8599496ddc03977e889ef514b5932c870fe15",
    },
}
S2_CLOUD_CALIBRATION = {
    "sessions": 191,
    "item_count": 7_126,
    "quality_counts": {
        "q1": 2_138,  # q1 + q2, matching the model's collapsed bucket
        "q3": 1_664,
        "q4": 1_774,
        "q5": 885,
        "q6": 665,
    },
    "method": "conservative_wilson95_session_effective_n",
    "source_scope": "postupdate_s2_settlement_5601_through_5611",
}
RAVEN_CHAOTIC_SPACE_MAP_IDS = tuple(range(5101, 5111))
RAVEN_CHAOTIC_SPACE_POOL_IDS = tuple(range(5001, 5011))
RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON = (
    "raven_chaotic_space_distribution_input_unavailable"
)
CURRENT_RED_CRATE_OPEN_MAP_IDS = tuple(range(2541, 2551))
CURRENT_RED_CRATE_SEALED_MAP_IDS = tuple(range(4541, 4551))
PREDECESSOR_RED_CRATE_ACTIVITY_OPEN_MAP_IDS = tuple(range(2531, 2541))
PREDECESSOR_RED_CRATE_ACTIVITY_SEALED_MAP_IDS = tuple(range(4531, 4541))


def raven_chaotic_space_authority_receipt() -> dict[str, Any]:
    return {
        "activity_id": 40002,
        "activity_map_id": 20002,
        "hero_id": 301,
        "map_group_id": 501,
        "map_ids": list(RAVEN_CHAOTIC_SPACE_MAP_IDS),
        "pool_ids": list(RAVEN_CHAOTIC_SPACE_POOL_IDS),
        "model_authority": False,
        "quote_available": False,
        "qualification_reason": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
    }


def _validate_current_red_crate_activity_blocks(
    indexed: Mapping[str, Mapping[int, list[str]]],
) -> dict[str, Any]:
    """Validate static 41-50 BidMap blocks without claiming runtime openness."""

    try:
        activity_open = json.loads(indexed["ActivityMap"][10003][12])
        activity_sealed = json.loads(indexed["ActivityMap"][10103][12])
        open_rows = [indexed["BidMap"][map_id] for map_id in CURRENT_RED_CRATE_OPEN_MAP_IDS]
        sealed_rows = [
            indexed["BidMap"][map_id]
            for map_id in CURRENT_RED_CRATE_SEALED_MAP_IDS
        ]
        open_root = json.loads(open_rows[0][9])
        sealed_root = json.loads(sealed_rows[0][9])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(
            "current red-crate activity structure malformed"
        ) from exc

    expected_open_root = [
        [map_id, 100 if index == 0 else 20]
        for index, map_id in enumerate(CURRENT_RED_CRATE_OPEN_MAP_IDS)
    ]
    expected_sealed_root = [
        [map_id, 100 if index == 0 else 20]
        for index, map_id in enumerate(CURRENT_RED_CRATE_SEALED_MAP_IDS)
    ]
    if (
        activity_open != list(PREDECESSOR_RED_CRATE_ACTIVITY_OPEN_MAP_IDS)
        or activity_sealed != list(PREDECESSOR_RED_CRATE_ACTIVITY_SEALED_MAP_IDS)
        or open_root != expected_open_root
        or sealed_root != expected_sealed_root
    ):
        raise Update606PackageInputError(
            "current red-crate activity root binding mismatch"
        )

    try:
        block_ok = all(
            row[7] == category
            and json.loads(row[9]) == (expected_root if index == 0 else [[]])
            and json.loads(row[17])
            == [9999, map_id if category == "105" else map_id - 2000, 22, 44]
            and row[18] == "4"
            and json.loads(row[20]) == [103, 0, 0, 0, 0]
            for category, map_ids, rows, expected_root in (
                (
                    "105",
                    CURRENT_RED_CRATE_OPEN_MAP_IDS,
                    open_rows,
                    expected_open_root,
                ),
                (
                    "305",
                    CURRENT_RED_CRATE_SEALED_MAP_IDS,
                    sealed_rows,
                    expected_sealed_root,
                ),
            )
            for index, (map_id, row) in enumerate(zip(map_ids, rows))
        )
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(
            "current red-crate activity row malformed"
        ) from exc
    if not block_ok:
        raise Update606PackageInputError(
            "current red-crate activity row binding mismatch"
        )
    return {
        "open_map_ids": list(CURRENT_RED_CRATE_OPEN_MAP_IDS),
        "sealed_map_ids": list(CURRENT_RED_CRATE_SEALED_MAP_IDS),
        "static_structure_present": True,
        "runtime_open_claim": False,
        "activity_map_routes": {
            "open": list(PREDECESSOR_RED_CRATE_ACTIVITY_OPEN_MAP_IDS),
            "sealed": list(PREDECESSOR_RED_CRATE_ACTIVITY_SEALED_MAP_IDS),
        },
        "round_skill_group_ids": [103, 0, 0, 0, 0],
        "round_skill_group_policy": "identity_only_not_distribution_input",
    }


def _validate_auction_hall_6001_static_structure(
    indexed: Mapping[str, Mapping[int, list[str]]],
) -> dict[str, Any]:
    """Validate 6001 structure while leaving official quote authority false."""

    try:
        row = indexed["BidMap"][6001]
        rank_present = 6001 in indexed["RankMap"]
        structure_ok = (
            row[7] == "601"
            and json.loads(row[9]) == [[]]
            and json.loads(row[17]) == [9999, 2601, 44, 44]
            and row[18] == "10"
            and json.loads(row[20]) == [103, 103, 103, 104, 105]
        )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(
            "current auction-hall 6001 structure malformed"
        ) from exc
    if not rank_present or not structure_ok:
        raise Update606PackageInputError(
            "current auction-hall 6001 structure mismatch"
        )
    return {
        "map_id": 6001,
        "static_table_present": True,
        "drop_pool_id": 2601,
        "items_per_session": [44, 44],
        "round_skill_group_ids": [103, 103, 103, 104, 105],
        "round_skill_group_policy": "identity_only_not_distribution_input",
        "distribution_authority": False,
        "quote_available": False,
        "lifecycle": "dormant_history_no_current_entry",
    }


CURRENT_RANKMAP_ALL_IDS_SHA256 = (
    "7e3219a0fb64e8280be83d30191a4262864da84b2e7833209f6f81a4f850bee5"
)
CURRENT_RANKMAP_SCOPE_IDENTITIES: Mapping[str, Mapping[str, Any]] = {
    "route_stable_210x": {
        "map_ids": ROUTE_STABLE_RANKMAP_MAP_IDS,
        "map_ids_sha256": "f15ccadd8dd234cdb6437c8ea6eee105f072fa60ce752db93fe5660ef49d4beb",
        "rankmap_rows_sha256": "96ee0fa683aa425ff0c95ca8f221696b863b07da8e5a6f54ccfdf852b1a4e31e",
        "bidmap_rows_sha256": "ca73ed5bfa53fa8a63395c7b0e4fc32120c1c3b7e27ac5394f51e3a0ec8019bf",
        "tier": "101",
        "previous_item_rows": 673,
        "previous_item_ids_sha256": "b975fffe4e1c490e03830fc41a9a18a761b08089f6bab958482afaf70a4fd3ef",
        "item_rows": 702,
        "item_ids_sha256": "11b87878669d4843b7efffc9ff7dc407ebef6ab411686295f3e4efb02417b99e",
    },
    "changed_route_ranked_560x": {
        "map_ids": CHANGED_ROUTE_RANKMAP_MAP_IDS,
        "map_ids_sha256": "30304d395c315131761d8af09ca9976e1c689c12d184f2f65182120320dde548",
        "rankmap_rows_sha256": "e8144753dce78269ac1aca49ac34a198717616fc1684f4dbae13206b35b7bdb1",
        "bidmap_rows_sha256": "65a334765aeb9bc29d5062e4f3f5a7f813cc39458bccb48d298e5526933093f4",
        "tier": "402",
        "previous_item_rows": 680,
        "previous_item_ids_sha256": "abdd31dd36d9257b98f0a9c5fbf269ef467cae1ab0f41387be6b5b9985a36264",
        "item_rows": 711,
        "item_ids_sha256": "5df532896ab6340cbab36d23bdee67086dd1133a6eb7fa24277084c0220a91cc",
    },
}
CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS: Mapping[str, Mapping[str, Any]] = {
    "weights": {
        "bytes": 467_354,
        "maps": 18,
        "sha256": "6adc5792bae22bba8ca972b1397e30f7d651145b5b306516e18725d3809beec2",
    },
    "priors": {
        "bytes": 4_966,
        "maps": 18,
        "sha256": "d543ce6e998c6bc8dffa4d27b7453ede04f713cbe75be288ab7dc10032a46c26",
    },
}
CURRENT_RANKMAP_AUTHORITY_OUTPUT = {
    "bytes": 9_566,
    "sha256": "ced9f94228007252381a2c97fe8f23057276dcb72465bd146e7fd211cdfae156",
}
PREVIOUS_RED_LONGTAIL_NORMALIZED = {
    "bytes": 898,
    "items": 48,
    "sha256": "e0711f12e2c846bad7550e885d751cf58e3cb21481169f4ae8e39931d9bd699b",
}
CURRENT_RED_LONGTAIL_NORMALIZED = {
    "bytes": 1_258,
    "items": 75,
    "sha256": "02701ac82528b0b398d7e9a39880a95449120de5b7d2bab9c7f5964c9143322a",
}
CURRENT_RANKMAP_AUTHORITY_MODE = "rankmap_drop_model_algorithm_prior"

# Runtime pins for the separately audited 76b7ca model. Keep the b049 CURRENT_*
# builder/history contract intact; selection is by the package baseline below.
# The build-only official_20261008_model_profile is not a runtime dependency.
OFFICIAL_20261008_RUNTIME_MODEL = {
    "baseline_id": "package-data-2026-10-08-76b7ca-0.3.6",
    "generation": OFFICIAL_20261008_PACKAGE_GENERATION,
    "schema": "bidking_official_20261008_model_authority_v1",
    "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
    "scope_map_count": 112,
    "files": {
        "data/raw/tables/Item.txt": OFFICIAL_20261008_PACKAGE_TABLES["Item"],
        "data/raw/tables/BidMap.txt": OFFICIAL_20261008_PACKAGE_TABLES["BidMap"],
        "data/raw/tables/RankMap.txt": CURRENT_RANKMAP_PACKAGE,
        "data/processed/drop_weights_all_maps_0627.json": {
            "bytes": 2556143, "sha256": "b7c55c9ee0a8b9211b4a70f53f1c67d4e3f037d4a2faa8e7550e1f240d4499d3"},
        "data/processed/drop_table_priors_0627.json": {
            "bytes": 21239, "sha256": "83f2d76cfffb907fd3cd5116f9dcde880112255b818ea1e9dafe361eab901ef9"},
        "data/processed/rankmap_drop_weights_current.json": {
            "bytes": 449613, "sha256": "08ba45ee553bdd08effe338b91237efcaef1115c82ab649eaf95a2ab72aa5453"},
        "data/processed/rankmap_drop_priors_current.json": {
            "bytes": 4966, "sha256": "6db45943867d9028f0b2e2a13fc18f6ff10a8c4f0bc70e0cd8d68195ef139713"},
        "data/processed/rankmap_distribution_authority_current.json": {
            "bytes": 24690, "sha256": "55b56d8c506685a806f0a9891e8999ac2d1e467ac25598c551d35579eaec4bfe"},
    },
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ids_sha256(values: tuple[int, ...]) -> str:
    return _sha256(json.dumps(values, separators=(",", ":")).encode("ascii"))


def _rows_sha256(rows: list[list[str]]) -> str:
    return _sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8"))


def _strict_json(raw: bytes, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise Update606PackageInputError(f"{label} has duplicate key: {key}")
            out[key] = value
        return out

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(f"{label} is not strict UTF-8 JSON") from exc


def _catalog_by_id(value: Any, *, label: str) -> dict[int, dict[str, Any]]:
    if value.__class__ is not list:
        raise Update606PackageInputError(f"{label} must be an exact list")
    out: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(value):
        if row.__class__ is not dict:
            raise Update606PackageInputError(f"{label} row {index} is not an object")
        item_id = row.get("item_id")
        if item_id.__class__ is not int or item_id <= 0 or item_id in out:
            raise Update606PackageInputError(
                f"{label} has invalid/duplicate item_id"
            )
        out[item_id] = row
    return out


def _canonical_catalog_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_current_items_catalog(
    item_text_asset: bytes,
    previous_catalog: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Build the September catalog with the approved shared display overlay."""

    rows = _exact_rows("Item", item_text_asset)
    items = parse_item_table(rows)
    if (
        previous_catalog.__class__ is not bytes
        or len(previous_catalog) != PREVIOUS_ITEMS_CATALOG["bytes"]
        or _sha256(previous_catalog) != PREVIOUS_ITEMS_CATALOG["sha256"]
    ):
        raise Update606PackageInputError("previous items catalog identity mismatch")
    previous_value = _strict_json(previous_catalog, label="previous items catalog")
    previous = _catalog_by_id(previous_value, label="previous items catalog")
    if (
        len(previous) != PREVIOUS_ITEMS_CATALOG["rows"]
        or _sha256(_canonical_catalog_json(previous_value))
        != PREVIOUS_ITEMS_CATALOG["canonical_sha256"]
        or not set(previous).issubset(items)
    ):
        raise Update606PackageInputError("previous items catalog canonical mismatch")

    catalog: list[dict[str, Any]] = []
    for item_id in sorted(items):
        row = items[item_id].model_dump(exclude={"raw_row"})
        if item_id in previous:
            row["name"] = previous[item_id]["name"]
        catalog.append(row)
    current = {int(row["item_id"]): row for row in catalog}
    added_ids = tuple(sorted(set(current) - set(previous)))
    if (
        len(added_ids) != CURRENT_ADDED_ITEM_ROWS
        or _ids_sha256(added_ids) != CURRENT_ADDED_ITEM_IDS_SHA256
    ):
        raise Update606PackageInputError("current added Item identity mismatch")
    differences = {
        field: tuple(
            item_id for item_id in sorted(previous)
            if previous[item_id].get(field) != current[item_id].get(field)
        )
        for field in previous[next(iter(previous))]
    }
    differences = {field: ids for field, ids in differences.items() if ids}
    if differences:
        raise Update606PackageInputError("current items catalog semantic delta mismatch")
    alias_ids = set(HISTORICAL_LOCALIZATION_ITEM_ID_ALIASES)
    canonical_ids = set(HISTORICAL_LOCALIZATION_ITEM_ID_ALIASES.values())
    if alias_ids & set(current) or not canonical_ids.issubset(current):
        raise Update606PackageInputError("historical localization alias identity mismatch")

    rendered = json.dumps(
        catalog,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    if (
        len(catalog) != CURRENT_ITEMS_CATALOG["rows"]
        or len(rendered) != CURRENT_ITEMS_CATALOG["bytes"]
        or _sha256(rendered) != CURRENT_ITEMS_CATALOG["sha256"]
        or _sha256(_canonical_catalog_json(catalog))
        != CURRENT_ITEMS_CATALOG["canonical_sha256"]
    ):
        raise Update606PackageInputError("current items catalog output mismatch")
    return rendered, {
        "schema": "bidking_current_items_catalog_receipt_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "official_item": asdict(CURRENT_TEXT_ASSET_IDENTITIES["Item"]),
        "previous_catalog": dict(PREVIOUS_ITEMS_CATALOG),
        "display_overlay_fields": ["name"],
        "shared_rows": len(previous),
        "added_rows": len(added_ids),
        "added_item_ids_sha256": CURRENT_ADDED_ITEM_IDS_SHA256,
        "semantic_differences": {
            field: list(ids) for field, ids in differences.items()
        },
        "historical_localization_item_id_aliases": {
            str(alias): canonical
            for alias, canonical in sorted(
                HISTORICAL_LOCALIZATION_ITEM_ID_ALIASES.items()
            )
        },
        "aliases_are_item_rows": False,
        "output": dict(CURRENT_ITEMS_CATALOG),
    }


def validate_current_appmanifest(raw: bytes) -> dict[str, Any]:
    """Validate stable Steam identity while excluding mutable local timestamps."""

    if type(raw) is not bytes or not 0 < len(raw) <= CURRENT_APP_MANIFEST["max_bytes"]:  # noqa: E721
        raise Update606PackageInputError("current appmanifest size is invalid")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Update606PackageInputError("current appmanifest is not UTF-8") from exc
    if "\x00" in text:
        raise Update606PackageInputError("current appmanifest contains NUL")

    def exact_field(name: str, expected: str) -> None:
        matches = re.findall(
            rf'^\s*"{re.escape(name)}"\s+"([^"]*)"\s*$',
            text,
            flags=re.MULTILINE,
        )
        if matches != [expected]:
            raise Update606PackageInputError(
                f"current appmanifest {name} mismatch"
            )

    exact_field("appid", CURRENT_APP_MANIFEST["app_id"])
    exact_field("buildid", CURRENT_APP_MANIFEST["build_id"])
    exact_field("installdir", CURRENT_APP_MANIFEST["install_dir"])
    return {
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "app_id": CURRENT_APP_MANIFEST["app_id"],
        "build_id": CURRENT_APP_MANIFEST["build_id"],
        "install_dir": CURRENT_APP_MANIFEST["install_dir"],
    }


def _exact_rows(name: str, raw: bytes) -> list[list[str]]:
    identity = CURRENT_TEXT_ASSET_IDENTITIES[name]
    if (
        type(raw) is not bytes  # noqa: E721 - authority does not coerce
        or len(raw) != identity.utf8_size
        or _sha256(raw) != identity.utf8_sha256
    ):
        raise Update606PackageInputError(f"current {name} TextAsset identity mismatch")
    try:
        rows = list(iter_table_rows(raw.decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise Update606PackageInputError(f"current {name} TextAsset is not UTF-8") from exc
    if len(rows) != identity.rows or assert_uniform_columns(rows) != identity.columns:
        raise Update606PackageInputError(
            f"current {name} TextAsset row/column identity mismatch"
        )
    if rows and rows[0] and rows[0][0].startswith("\ufeff"):
        rows[0][0] = rows[0][0][1:]
    return rows


def build_current_package_table(
    name: str,
    raw: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Build an exact Base64 package table for current BidMap or Item."""

    expected = {
        "BidMap": CURRENT_PACKAGE_BID_MAP,
        "Item": CURRENT_PACKAGE_ITEM,
    }.get(name)
    if expected is None:
        raise Update606PackageInputError(f"unsupported current package table: {name!r}")
    rows = _exact_rows(name, raw)
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    package = base64.b64encode(canonical)
    if (
        len(rows) != expected["rows"]
        or assert_uniform_columns(rows) != expected["columns"]
        or _sha256(canonical) != expected["decoded_sha256"]
        or len(package) != expected["bytes"]
        or _sha256(package) != expected["sha256"]
    ):
        raise Update606PackageInputError(f"current {name} package identity mismatch")
    return package, {
        "schema": "bidking_current_package_table_receipt_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "table": name,
        "official_text_asset": asdict(CURRENT_TEXT_ASSET_IDENTITIES[name]),
        "canonical": {
            "bytes": len(canonical),
            "sha256": _sha256(canonical),
        },
        "package_base64": {
            "bytes": len(package),
            "sha256": _sha256(package),
        },
    }


def build_official_20261008_package_table(
    name: str,
    raw: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Build exact 76b7ca lookup tables without claiming new model authority."""
    expected = OFFICIAL_20261008_PACKAGE_TABLES.get(name)
    if expected is None:
        raise Update606PackageInputError(f"unsupported October package table: {name!r}")
    if raw.__class__ is not bytes or len(raw) != expected["raw_bytes"] or _sha256(raw) != expected["raw_sha256"]:
        raise Update606PackageInputError(f"October {name} TextAsset identity mismatch")
    rows = list(iter_table_rows(raw.decode("utf-8")))
    if rows and rows[0] and rows[0][0].startswith("\ufeff"):
        rows[0][0] = rows[0][0][1:]
    if len(rows) != expected["rows"] or assert_uniform_columns(rows) != expected["columns"]:
        raise Update606PackageInputError(f"October {name} row/column identity mismatch")
    (parse_item_table if name == "Item" else parse_bid_map_table)(rows)
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    packaged = base64.b64encode(canonical)
    if (
        len(canonical) != expected["decoded_bytes"]
        or _sha256(canonical) != expected["decoded_sha256"]
        or len(packaged) != expected["bytes"]
        or _sha256(packaged) != expected["sha256"]
    ):
        raise Update606PackageInputError(f"October {name} canonical/package identity mismatch")
    return packaged, {
        "schema": "bidking_official_20261008_static_package_table_v1",
        "package_generation": OFFICIAL_20261008_PACKAGE_GENERATION,
        "table": name,
        "input_identity": dict(expected),
        "distribution_authority": False,
        "quote_authority": False,
    }


def build_current_rankmap_package_table(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    rows = _exact_rows("RankMap", raw)
    canonical = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    package = base64.b64encode(canonical)
    expected = CURRENT_RANKMAP_PACKAGE
    if (
        len(canonical) != expected["decoded_bytes"]
        or _sha256(canonical) != expected["decoded_sha256"]
        or len(package) != expected["bytes"]
        or _sha256(package) != expected["sha256"]
    ):
        raise Update606PackageInputError("current RankMap package identity mismatch")
    return package, {
        "schema": "bidking_current_rankmap_package_table_receipt_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "drop_or_dropmap_textasset": False,
        "official_text_asset": {
            "bytes": len(raw),
            "rows": len(rows),
            "columns": assert_uniform_columns(rows),
            "sha256": _sha256(raw),
        },
        "package_base64": {
            "bytes": len(package),
            "sha256": _sha256(package),
        },
    }


def validate_current_release_inputs(
    *,
    appmanifest: bytes,
    version_file: bytes,
    buildin_catalog_bytes: bytes,
    buildin_catalog_json: bytes,
    package_hash_file: bytes,
    manifest: bytes,
    tables_bundle: bytes,
    scripts: bytes,
    text_assets: Mapping[str, bytes],
) -> dict[str, Any]:
    exact = {
        "version_file": (version_file, CURRENT_VERSION_FILE),
        "buildin_catalog_bytes": (
            buildin_catalog_bytes,
            CURRENT_BUILDIN_CATALOG_BYTES,
        ),
        "buildin_catalog_json": (
            buildin_catalog_json,
            CURRENT_BUILDIN_CATALOG_JSON,
        ),
        "package_hash_file": (package_hash_file, CURRENT_PACKAGE_HASH_FILE),
        "manifest": (manifest, CURRENT_MANIFEST),
        "tables_bundle": (tables_bundle, CURRENT_TABLES_BUNDLE),
        "scripts": (scripts, CURRENT_SCRIPTS),
    }
    for label, (raw, identity) in exact.items():
        if identity["bytes"] and (
            type(raw) is not bytes  # noqa: E721
            or len(raw) != identity["bytes"]
            or _sha256(raw) != identity["sha256"]
        ):
            raise Update606PackageInputError(f"current {label} identity mismatch")
    appmanifest_receipt = validate_current_appmanifest(appmanifest)
    try:
        version_text = version_file.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise Update606PackageInputError("current release metadata is not UTF-8") from exc
    if version_text != CURRENT_PACKAGE_GENERATION:
        raise Update606PackageInputError("current package generation mismatch")
    bundle_md5 = hashlib.md5(  # noqa: S324 - YooAsset filename identity
        tables_bundle,
        usedforsecurity=False,
    ).hexdigest()
    if bundle_md5 != CURRENT_TABLES_BUNDLE["md5"]:
        raise Update606PackageInputError("current tables bundle MD5 mismatch")
    required = set(CURRENT_TEXT_ASSET_IDENTITIES)
    if type(text_assets) is not dict or set(text_assets) != required:  # noqa: E721
        raise Update606PackageInputError("current TextAsset set mismatch")
    rows = {name: _exact_rows(name, text_assets[name]) for name in sorted(required)}
    rank_ids = tuple(int(row[0]) for row in rows["RankMap"])
    if len(set(rank_ids)) != len(rank_ids) or _ids_sha256(rank_ids) != CURRENT_RANKMAP_ALL_IDS_SHA256:
        raise Update606PackageInputError("current RankMap row-set identity mismatch")
    indexed = {
        name: {int(row[0]): row for row in table_rows}
        for name, table_rows in rows.items()
    }
    if any(len(indexed[name]) != len(table_rows) for name, table_rows in rows.items()):
        raise Update606PackageInputError("current TextAsset has duplicate id")
    try:
        activity = indexed["Activity"][40002]
        activity_map = indexed["ActivityMap"][20002]
        raven_hero = indexed["Hero"][301]
        activity_parent = indexed["Map"][501]
        bid_rows = [indexed["BidMap"][map_id] for map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS]
        rank_rows = [indexed["RankMap"][map_id] for map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS]
    except KeyError as exc:
        raise Update606PackageInputError(
            "current Raven chaotic-space cross-table row missing"
        ) from exc
    try:
        raven_binding_ok = (
            json.loads(activity[16]) == [2, 501]
            and json.loads(activity[18]) == [20002]
            and json.loads(activity_map[1]) == [501]
            and json.loads(activity_map[5]) == [[3, 301]]
            and json.loads(activity_map[12]) == [5101]
            and raven_hero[19] == "301"
            and activity_parent[17] == "501"
            and json.loads(bid_rows[0][9])
            == [
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
            and all(
                row[7] == "501"
                and json.loads(row[17])
                == [9999, map_id - 100, 33, 44]
                and json.loads(row[20]) == [30105, 0, 0, 0, 0]
                for map_id, row in zip(RAVEN_CHAOTIC_SPACE_MAP_IDS, bid_rows)
            )
            and tuple(int(row[0]) for row in rank_rows)
            == RAVEN_CHAOTIC_SPACE_MAP_IDS
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Update606PackageInputError(
            "current Raven chaotic-space cross-table value malformed"
        ) from exc
    if not raven_binding_ok:
        raise Update606PackageInputError(
            "current Raven chaotic-space cross-table binding mismatch"
        )
    if (
        set(CURRENT_RANKMAP_MAP_IDS) & set(RAVEN_CHAOTIC_SPACE_MAP_IDS)
        or set(CURRENT_RANKMAP_MAP_IDS) & set(RAVEN_CHAOTIC_SPACE_POOL_IDS)
    ):
        raise Update606PackageInputError(
            "Raven unmodeled scope entered current RankMap model authority"
        )
    red_crate_activity = _validate_current_red_crate_activity_blocks(indexed)
    auction_hall_6001 = _validate_auction_hall_6001_static_structure(indexed)
    cosmetic_quality = {
        190001: "0",
        190002: "2",
        200001: "0",
        200002: "2",
    }
    if not set(cosmetic_quality).issubset(indexed["Item"]) or any(
        indexed["Item"][item_id][8] != expected_quality
        or indexed["Item"][item_id][9] != "0"
        for item_id, expected_quality in cosmetic_quality.items()
    ):
        raise Update606PackageInputError(
            "current cosmetic item identity mismatch"
        )
    return {
        "schema": "bidking_current_rankmap_release_inputs_receipt_v1",
        "steam_build_id": CURRENT_STEAM_BUILD_ID,
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "appmanifest": appmanifest_receipt,
        "appmanifest_contract": dict(CURRENT_APP_MANIFEST),
        "version_file": dict(CURRENT_VERSION_FILE),
        "buildin_catalog_bytes": dict(CURRENT_BUILDIN_CATALOG_BYTES),
        "buildin_catalog_json": dict(CURRENT_BUILDIN_CATALOG_JSON),
        "package_hash_file": dict(CURRENT_PACKAGE_HASH_FILE),
        "manifest": dict(CURRENT_MANIFEST),
        "tables_bundle": dict(CURRENT_TABLES_BUNDLE),
        "scripts": dict(CURRENT_SCRIPTS),
        "text_assets": {
            name: asdict(CURRENT_TEXT_ASSET_IDENTITIES[name])
            for name in sorted(required)
        },
        "rankmap_row_ids_sha256": CURRENT_RANKMAP_ALL_IDS_SHA256,
        "distribution_authority": CURRENT_RANKMAP_AUTHORITY_MODE,
        "drop_or_dropmap_textasset": False,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "official_distribution_authority": False,
        "runtime_open_claim": True,
        "runtime_open_scope": "s2_ranked_5601_through_5611",
        "current_red_crate_activity": red_crate_activity,
        "recognized_unmodeled_scopes": {
            "large_auction_6001": auction_hall_6001
        },
        "raven_chaotic_space": raven_chaotic_space_authority_receipt(),
    }


def _current_item_semantics(rows: list[list[str]]) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        try:
            item_id = int(row[0])
            shape = int(row[7])
            quality = int(row[8])
            value = int(row[9])
            tags = json.loads(row[6])
            map_ids = json.loads(row[39])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise Update606PackageInputError(
                f"current Item row {index} is malformed"
            ) from exc
        if (
            item_id <= 0
            or item_id in items
            or quality not in range(7)
            or value < 0
            or type(tags) is not list  # noqa: E721
            or any(type(tag) is not int for tag in tags)  # noqa: E721
            or type(map_ids) is not list  # noqa: E721
            or any(type(map_id) is not int for map_id in map_ids)  # noqa: E721
        ):
            raise Update606PackageInputError(
                f"current Item row {index} has invalid semantics"
            )
        items[item_id] = {
            "quality": quality,
            "value": value,
            "shape": (shape // 10, shape % 10),
            "tags": tuple(tags),
            "map_ids": tuple(map_ids),
        }
    return items


def _current_droppable_ids(
    raw: bytes,
    items: Mapping[int, Mapping[str, Any]],
) -> tuple[int, ...]:
    value = _strict_json(raw, label="current items_droppable")
    indexed = _catalog_by_id(value, label="current items_droppable")
    ids = tuple(indexed)
    if ids != tuple(sorted(ids)):
        raise Update606PackageInputError("current items_droppable is not sorted")
    if (
        len(ids) != CURRENT_MODEL_UNION_ITEM_ROWS
        or _ids_sha256(ids) != CURRENT_MODEL_UNION_ITEM_IDS_SHA256
    ):
        raise Update606PackageInputError("current items_droppable identity mismatch")
    for item_id, row in indexed.items():
        item = items.get(item_id)
        if item is None:
            raise Update606PackageInputError("current droppable Item row is missing")
        expected = {
            "quality": item["quality"],
            "value": item["value"],
            "shape_w": item["shape"][0],
            "shape_h": item["shape"][1],
            "tags": list(item["tags"]),
        }
        if any(row.get(field) != field_value for field, field_value in expected.items()):
            raise Update606PackageInputError(
                f"current items_droppable semantic mismatch item_id={item_id}"
            )
    return ids


def _validate_current_lifecycle(items: Mapping[int, Mapping[str, Any]]) -> None:
    expected_routes = {
        CURRENT_ALL_MAP_Q6_ITEM_IDS: (
            101, 102, 103, 104, 105, 106, 201, 304, 305, 402, 501, 601
        ),
        CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS: (
            101, 102, 103, 104, 105, 106, 201, 304, 305, 501, 601
        ),
        CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS: (402,),
    }
    for item_ids, routes in expected_routes.items():
        for item_id in item_ids:
            item = items.get(item_id)
            if (
                item is None
                or item["quality"] != 6
                or item["value"] <= 0
                or math.prod(item["shape"]) <= 0
                or item["map_ids"] != routes
            ):
                raise Update606PackageInputError(
                    f"current lifecycle identity mismatch item_id={item_id}"
                )
    for item_id in (*RETIRED_SUMMER_PARTY_2026_ITEM_IDS, *CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS):
        item = items.get(item_id)
        if item is None or item["map_ids"]:
            raise Update606PackageInputError(
                f"current excluded lifecycle identity mismatch item_id={item_id}"
            )
    for item_id, (value, shape) in CURRENT_S2_DIRECT_Q6_CANARIES.items():
        item = items.get(item_id)
        if item is None or (item["quality"], item["value"], item["shape"]) != (
            6,
            value,
            shape,
        ):
            raise Update606PackageInputError(
                f"current S2 direct canary mismatch item_id={item_id}"
            )


def _col6_weight(anchors: Any, value: int) -> float:
    if (
        type(anchors) is not list  # noqa: E721
        or len(anchors) < 4
        or len(anchors) % 2
        or any(type(number) not in {int, float} for number in anchors)
    ):
        raise Update606PackageInputError("current RankMap value curve is malformed")
    points = [(float(anchors[index]), float(anchors[index + 1])) for index in range(0, len(anchors), 2)]
    if any(x <= 0 or y <= 0 for x, y in points) or any(
        points[index][0] >= points[index + 1][0]
        for index in range(len(points) - 1)
    ):
        raise Update606PackageInputError("current RankMap value curve is invalid")
    xs = [math.log10(point[0]) for point in points]
    ys = [math.log10(max(point[1], 0.01)) for point in points]
    position = math.log10(max(value, 1))
    if position <= xs[0]:
        slot = 0
    elif position >= xs[-1]:
        slot = len(xs) - 2
    else:
        slot = next(
            index
            for index in range(len(xs) - 1)
            if xs[index] <= position <= xs[index + 1]
        )
    ratio = (position - xs[slot]) / (xs[slot + 1] - xs[slot])
    return 10 ** (ys[slot] + ratio * (ys[slot + 1] - ys[slot]))


def _quality_bucket(quality: int) -> str:
    if quality in (1, 2):
        return "q1"
    if quality in (3, 4, 5, 6):
        return f"q{quality}"
    raise Update606PackageInputError("modeled support contains non-physical quality")


def _current_item_fitted_values(
    items: Mapping[int, Mapping[str, Any]],
    droppable_ids: tuple[int, ...],
) -> dict[int, int]:
    shape_floor: dict[tuple[int, tuple[int, int]], int] = {}
    normal_values: dict[tuple[int, tuple[int, int]], list[int]] = {}
    per_cell: dict[int, list[float]] = {}
    for item_id in droppable_ids:
        item = items[item_id]
        shape = tuple(sorted(item["shape"]))
        key = (int(item["quality"]), shape)
        shape_floor[key] = min(shape_floor.get(key, 10**12), int(item["value"]))
    for item_id in droppable_ids:
        item = items[item_id]
        quality = int(item["quality"])
        shape = tuple(sorted(item["shape"]))
        key = (quality, shape)
        value = int(item["value"])
        cells = math.prod(item["shape"])
        if cells > 0 and value < 5 * shape_floor[key]:
            normal_values.setdefault(key, []).append(value)
            per_cell.setdefault(quality, []).append(value / cells)

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    fitted: dict[int, int] = {}
    for item_id, item in items.items():
        quality = int(item["quality"])
        shape = tuple(sorted(item["shape"]))
        key = (quality, shape)
        value = int(item["value"])
        cells = math.prod(item["shape"])
        if cells <= 0:
            fitted[item_id] = 0
            continue
        if quality != 6:
            fitted[item_id] = value
            continue
        floor = shape_floor.get(key)
        if floor and value >= 5 * floor:
            values = normal_values.get(key)
            fitted[item_id] = int(sum(values) / len(values)) if values else value
        elif per_cell.get(quality) and value / cells >= 3 * median(per_cell[quality]):
            fitted[item_id] = int(median(per_cell[quality]) * cells)
        else:
            fitted[item_id] = value
    return fitted


def build_current_red_longtail_normalized(
    *,
    item_text_asset: bytes,
    items_droppable: bytes,
) -> tuple[bytes, dict[str, Any]]:
    """Build one current-reference q6 normalization lookup for all Item rows."""

    items = _current_item_semantics(_exact_rows("Item", item_text_asset))
    droppable_ids = _current_droppable_ids(items_droppable, items)
    fitted = _current_item_fitted_values(items, droppable_ids)
    red_longtail = {
        str(item_id): fitted[item_id]
        for item_id in sorted(items)
        if items[item_id]["quality"] == 6
        and math.prod(items[item_id]["shape"]) > 0
        and fitted[item_id] != items[item_id]["value"]
    }
    raw = json.dumps(
        red_longtail,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = CURRENT_RED_LONGTAIL_NORMALIZED
    if expected["bytes"] and (
        len(red_longtail) != expected["items"]
        or len(raw) != expected["bytes"]
        or _sha256(raw) != expected["sha256"]
    ):
        raise Update606PackageInputError("current red longtail output identity mismatch")
    return raw, {
        "schema": "bidking_current_red_longtail_normalized_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "reference_items": len(droppable_ids),
        "reference_item_ids_sha256": CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
        "catalog_q6_rows": sum(item["quality"] == 6 for item in items.values()),
        "output": {
            "bytes": len(raw),
            "items": len(red_longtail),
            "sha256": _sha256(raw),
        },
    }


def _probabilities_for_support(
    *,
    support: tuple[int, ...],
    items: Mapping[int, Mapping[str, Any]],
    anchors: Any,
    quality_probs: Mapping[str, Any],
) -> dict[int, float]:
    by_quality: dict[str, list[int]] = {key: [] for key in ("q1", "q3", "q4", "q5", "q6")}
    for item_id in support:
        by_quality[_quality_bucket(int(items[item_id]["quality"]))].append(item_id)
    probabilities: dict[int, float] = {}
    masses: dict[str, float] = {}
    for quality in by_quality:
        mass = quality_probs.get(quality)
        if type(mass) not in {int, float} or not math.isfinite(float(mass)) or not 0 <= mass <= 1:
            raise Update606PackageInputError("previous quality prior is malformed")
        masses[quality] = float(mass)
    mass_total = sum(masses.values())
    if not 0.999 <= mass_total <= 1.001:
        raise Update606PackageInputError("previous quality prior is not normalized")
    masses = {quality: mass / mass_total for quality, mass in masses.items()}
    for quality, item_ids in by_quality.items():
        mass = masses[quality]
        if not item_ids:
            if mass:
                raise Update606PackageInputError("quality prior has no modeled support")
            continue
        raw_weights = [_col6_weight(anchors, int(items[item_id]["value"])) for item_id in item_ids]
        total = sum(raw_weights)
        values = [round(float(mass) * weight / total, 12) for weight in raw_weights]
        values[-1] = round(values[-1] + float(mass) - sum(values), 12)
        for item_id, probability in zip(item_ids, values):
            if not 0 <= probability <= 1:
                raise Update606PackageInputError("modeled probability is invalid")
            probabilities[item_id] = probability
    if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=2e-11):
        raise Update606PackageInputError("modeled probabilities are not normalized")
    for quality, item_ids in by_quality.items():
        observed = sum(probabilities[item_id] for item_id in item_ids)
        if not math.isclose(observed, masses[quality], rel_tol=0.0, abs_tol=2e-11):
            raise Update606PackageInputError("modeled quality bucket did not round-trip")
    return probabilities


def _entry_quality_probabilities(
    entry: Mapping[str, Any],
    items: Mapping[int, Mapping[str, Any]],
) -> dict[str, float]:
    item_rows = entry.get("items")
    if type(item_rows) is not dict:  # noqa: E721
        raise Update606PackageInputError("previous modeled items are malformed")
    quality = {key: 0.0 for key in ("q1", "q3", "q4", "q5", "q6")}
    for item_id_text, triple in item_rows.items():
        try:
            item_id = int(item_id_text)
            probability = float(triple[0])
            bucket = _quality_bucket(int(items[item_id]["quality"]))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise Update606PackageInputError("previous modeled item is malformed") from exc
        if not math.isfinite(probability) or probability < 0:
            raise Update606PackageInputError("previous modeled probability is invalid")
        quality[bucket] += probability
    total = sum(quality.values())
    if not 0.999 <= total <= 1.001:
        raise Update606PackageInputError("previous modeled probability sum is invalid")
    return {key: value / total for key, value in quality.items()}


def _quality_fitted_mean(
    entry: Mapping[str, Any],
    items: Mapping[int, Mapping[str, Any]],
    quality: str,
) -> float:
    numerator = 0.0
    denominator = 0.0
    item_rows = entry.get("items")
    if type(item_rows) is not dict:  # noqa: E721
        raise Update606PackageInputError("modeled items are malformed")
    for item_id_text, triple in item_rows.items():
        item_id = int(item_id_text)
        if _quality_bucket(int(items[item_id]["quality"])) != quality:
            continue
        probability = float(triple[0])
        fitted = int(triple[2])
        numerator += probability * fitted
        denominator += probability
    return numerator / denominator if denominator > 0 else 0.0


def _updated_quality_prior(
    *,
    previous_entry: Mapping[str, Any],
    current_entry: Mapping[str, Any],
    previous_prior: Mapping[str, Any],
    items: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    quality_probs = previous_prior.get("quality_probs")
    quality_values = previous_prior.get("quality_values")
    value_source = previous_prior.get("_value_src")
    if (
        type(quality_probs) is not dict  # noqa: E721
        or set(quality_probs) != {"q1", "q3", "q4", "q5", "q6"}
        or type(quality_values) is not dict  # noqa: E721
        or set(quality_values) != {"q1", "q3", "q4", "q5", "q6"}
        or any(type(value) is not int or value < 0 for value in quality_values.values())  # noqa: E721
        or (value_source is not None and type(value_source) is not dict)  # noqa: E721
    ):
        raise Update606PackageInputError("previous quality-value prior is malformed")
    result = {
        "quality_probs": dict(quality_probs),
        "quality_values": dict(quality_values),
    }
    if value_source is not None:
        result["_value_src"] = dict(value_source)
    previous_q6 = _quality_fitted_mean(previous_entry, items, "q6")
    current_q6 = _quality_fitted_mean(current_entry, items, "q6")
    if previous_q6 > 0 and current_q6 > 0 and float(quality_probs["q6"]) > 0:
        scaled = int(round(int(quality_values["q6"]) * current_q6 / previous_q6))
        if scaled != int(quality_values["q6"]):
            result["quality_values"]["q6"] = scaled
            result.setdefault("_value_src", {})["q6"] = (
                "previous_empirical_scaled_by_current_q6_model_delta"
            )
    return result


def build_current_all_map_distribution(
    *,
    rankmap_text_asset: bytes,
    bidmap_text_asset: bytes,
    item_text_asset: bytes,
    map_text_asset: bytes,
    previous_weights: bytes,
    previous_priors: bytes,
    items_droppable: bytes,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Refresh all 101 current modeled maps against one 68171c support/reference."""

    rows = {
        "RankMap": _exact_rows("RankMap", rankmap_text_asset),
        "BidMap": _exact_rows("BidMap", bidmap_text_asset),
        "Item": _exact_rows("Item", item_text_asset),
        "Map": _exact_rows("Map", map_text_asset),
    }
    for label, raw in (("weights", previous_weights), ("priors", previous_priors)):
        expected = PREVIOUS_ALL_MAP_DISTRIBUTION_INPUTS[label]
        if len(raw) != expected["bytes"] or _sha256(raw) != expected["sha256"]:
            raise Update606PackageInputError(f"previous all-map {label} identity mismatch")
    old_weights = _strict_json(previous_weights, label="previous all-map weights")
    old_priors = _strict_json(previous_priors, label="previous all-map priors")
    if type(old_weights) is not dict or type(old_priors) is not dict:  # noqa: E721
        raise Update606PackageInputError("previous all-map distribution is malformed")
    map_ids = tuple(int(map_id) for map_id in old_weights)
    prior_ids = tuple(int(map_id) for map_id in old_priors)
    if (
        len(map_ids) != PREVIOUS_ALL_MAP_DISTRIBUTION_INPUTS["weights"]["maps"]
        or _ids_sha256(map_ids) != CURRENT_ALL_MAP_IDS_SHA256
        or len(prior_ids) != PREVIOUS_ALL_MAP_DISTRIBUTION_INPUTS["priors"]["maps"]
        or _ids_sha256(prior_ids) != CURRENT_ALL_MAP_PRIOR_IDS_SHA256
        or not set(prior_ids).issubset(map_ids)
    ):
        raise Update606PackageInputError("previous all-map map scope mismatch")

    rank_by_id = {int(row[0]): row for row in rows["RankMap"]}
    bid_by_id = {int(row[0]): row for row in rows["BidMap"]}
    items = _current_item_semantics(rows["Item"])
    droppable_ids = _current_droppable_ids(items_droppable, items)
    _validate_current_lifecycle(items)
    fitted = _current_item_fitted_values(items, droppable_ids)
    target_support_expected = set(droppable_ids)
    target_support_expected.difference_update(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)
    target_support_expected.difference_update(CURRENT_RANKED_ONLY_MECH_Q6_ITEM_IDS)
    target_support = tuple(sorted(target_support_expected))
    if (
        len(target_support) != 702
        or _ids_sha256(target_support)
        != CURRENT_RANKMAP_SCOPE_IDENTITIES["route_stable_210x"]["item_ids_sha256"]
    ):
        raise Update606PackageInputError("current all-map support identity mismatch")

    output_weights: dict[str, Any] = {}
    output_priors: dict[str, Any] = {}
    q6_value_deltas: dict[str, int] = {}
    for map_id in map_ids:
        previous = old_weights.get(str(map_id))
        rank_row = rank_by_id.get(map_id)
        bid_row = bid_by_id.get(map_id)
        if type(previous) is not dict or rank_row is None or bid_row is None:  # noqa: E721
            raise Update606PackageInputError(f"all-map source row missing map_id={map_id}")
        previous_items = previous.get("items")
        if (
            type(previous_items) is not dict  # noqa: E721
            or previous.get("n_items") != 673
            or previous.get("count") != 16.0
        ):
            raise Update606PackageInputError(f"previous all-map row malformed map_id={map_id}")
        previous_support = tuple(sorted(int(item_id) for item_id in previous_items))
        if (
            len(previous_support) != 673
            or _ids_sha256(previous_support)
            != CURRENT_RANKMAP_SCOPE_IDENTITIES["route_stable_210x"]["previous_item_ids_sha256"]
        ):
            raise Update606PackageInputError(f"previous all-map support mismatch map_id={map_id}")
        for item_id_text, triple in previous_items.items():
            item = items.get(int(item_id_text))
            if (
                type(triple) is not list  # noqa: E721
                or len(triple) != 3
                or item is None
                or triple[1] != item["value"]
                or type(triple[2]) is not int  # noqa: E721
            ):
                raise Update606PackageInputError(f"previous all-map Item mismatch map_id={map_id}")
        previous_prior = old_priors.get(str(map_id))
        quality_probs = (
            previous_prior.get("quality_probs")
            if type(previous_prior) is dict  # noqa: E721
            else _entry_quality_probabilities(previous, items)
        )
        if (
            type(quality_probs) is not dict  # noqa: E721
            or set(quality_probs) != {"q1", "q3", "q4", "q5", "q6"}
        ):
            raise Update606PackageInputError(f"all-map quality prior malformed map_id={map_id}")
        try:
            anchors = json.loads(rank_row[6])
        except (TypeError, json.JSONDecodeError) as exc:
            raise Update606PackageInputError(f"RankMap curve malformed map_id={map_id}") from exc
        probabilities = _probabilities_for_support(
            support=target_support,
            items=items,
            anchors=anchors,
            quality_probs=quality_probs,
        )
        current = {
            "count": 16.0,
            "n_items": len(target_support),
            "items": {
                str(item_id): [probabilities[item_id], items[item_id]["value"], fitted[item_id]]
                for item_id in target_support
            },
        }
        output_weights[str(map_id)] = current
        if previous_prior is not None:
            updated = _updated_quality_prior(
                previous_entry=previous,
                current_entry=current,
                previous_prior=previous_prior,
                items=items,
            )
            output_priors[str(map_id)] = updated
            q6_value_deltas[str(map_id)] = (
                updated["quality_values"]["q6"]
                - previous_prior["quality_values"]["q6"]
            )

    weights_raw = json.dumps(output_weights, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    priors_raw = json.dumps(output_priors, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for label, raw in (("weights", weights_raw), ("priors", priors_raw)):
        expected = CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS[label]
        if expected["bytes"] and (
            len(raw) != expected["bytes"] or _sha256(raw) != expected["sha256"]
        ):
            raise Update606PackageInputError(f"current all-map {label} output mismatch")
    return weights_raw, priors_raw, {
        "schema": "bidking_current_all_map_distribution_authority_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "official_distribution_authority": False,
        "model_distribution_authority": True,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "map_rows": len(output_weights),
        "map_ids_sha256": CURRENT_ALL_MAP_IDS_SHA256,
        "previous_item_rows_per_map": 673,
        "current_item_rows_per_map": len(target_support),
        "current_item_ids_sha256": _ids_sha256(target_support),
        "removed_item_ids": list(RETIRED_SUMMER_PARTY_2026_ITEM_IDS),
        "added_all_map_item_ids": list(CURRENT_ALL_MAP_Q6_ITEM_IDS),
        "added_non_s2_item_ids": list(CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS),
        "quality_probability_policy": "previous_map_mass_preserved",
        "quality_value_policy": (
            "q1_through_q5_previous_empirical_preserved;"
            "q6_previous_empirical_scaled_by_current_model_delta"
        ),
        "q6_quality_value_changed_maps": sum(delta != 0 for delta in q6_value_deltas.values()),
        "outputs": {
            "weights": {"bytes": len(weights_raw), "maps": len(output_weights), "sha256": _sha256(weights_raw)},
            "priors": {"bytes": len(priors_raw), "maps": len(output_priors), "sha256": _sha256(priors_raw)},
        },
    }


def _s2_empirical_calibration_receipt(quality_probs: Mapping[str, Any]) -> dict[str, Any]:
    sessions = int(S2_CLOUD_CALIBRATION["sessions"])
    total = int(S2_CLOUD_CALIBRATION["item_count"])
    counts = S2_CLOUD_CALIBRATION["quality_counts"]
    z = 1.959963984540054
    rows: dict[str, Any] = {}
    for quality in ("q1", "q3", "q4", "q5", "q6"):
        observed = int(counts[quality]) / total
        denominator = 1 + z * z / sessions
        center = (observed + z * z / (2 * sessions)) / denominator
        half = z * math.sqrt(
            observed * (1 - observed) / sessions
            + z * z / (4 * sessions * sessions)
        ) / denominator
        lower, upper = center - half, center + half
        retained = quality_probs.get(quality)
        if type(retained) not in {int, float} or not lower <= float(retained) <= upper:
            raise Update606PackageInputError(
                f"S2 retained quality prior rejected by empirical gate: {quality}"
            )
        rows[quality] = {
            "observed_share": round(observed, 12),
            "retained_prior": float(retained),
            "difference_pp": round((observed - float(retained)) * 100, 6),
            "ci95": [round(lower, 12), round(upper, 12)],
            "retained_prior_inside_ci": True,
        }
    return {
        "source_scope": S2_CLOUD_CALIBRATION["source_scope"],
        "sessions": sessions,
        "settled_items": total,
        "method": S2_CLOUD_CALIBRATION["method"],
        "item_events_are_not_treated_as_independent_trials": True,
        "decision": "retain_previous_quality_prior",
        "qualities": rows,
    }


def build_current_rankmap_distribution(
    *,
    rankmap_text_asset: bytes,
    bidmap_text_asset: bytes,
    item_text_asset: bytes,
    map_text_asset: bytes,
    calibrated_weights: bytes,
    calibrated_priors: bytes,
    current_all_map_weights: bytes,
    current_all_map_priors: bytes,
    items_droppable: bytes,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Build the eb1 modeled distribution from exact support and prior evidence."""

    rows = {
        "RankMap": _exact_rows("RankMap", rankmap_text_asset),
        "BidMap": _exact_rows("BidMap", bidmap_text_asset),
        "Item": _exact_rows("Item", item_text_asset),
        "Map": _exact_rows("Map", map_text_asset),
    }
    for label, raw in (("weights", calibrated_weights), ("priors", calibrated_priors)):
        expected = PREVIOUS_CURRENT_RANKMAP_DISTRIBUTION_INPUTS[label]
        if len(raw) != expected["bytes"] or _sha256(raw) != expected["sha256"]:
            raise Update606PackageInputError(
                f"previous current RankMap {label} identity mismatch"
            )
    previous_weights = _strict_json(calibrated_weights, label="previous current weights")
    previous_priors = _strict_json(calibrated_priors, label="previous current priors")
    for label, raw in (
        ("weights", current_all_map_weights),
        ("priors", current_all_map_priors),
    ):
        expected = CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS[label]
        if len(raw) != expected["bytes"] or _sha256(raw) != expected["sha256"]:
            raise Update606PackageInputError(f"current all-map {label} identity mismatch")
    current_all_weights = _strict_json(
        current_all_map_weights,
        label="current all-map weights",
    )
    current_all_priors = _strict_json(
        current_all_map_priors,
        label="current all-map priors",
    )
    expected_keys = {str(map_id) for map_id in CURRENT_RANKMAP_MAP_IDS}
    if (
        type(previous_weights) is not dict  # noqa: E721
        or type(previous_priors) is not dict  # noqa: E721
        or set(previous_weights) != expected_keys
        or set(previous_priors) != expected_keys
        or type(current_all_weights) is not dict  # noqa: E721
        or type(current_all_priors) is not dict  # noqa: E721
    ):
        raise Update606PackageInputError("previous current RankMap scope mismatch")

    rank_by_id = {int(row[0]): row for row in rows["RankMap"]}
    bid_by_id = {int(row[0]): row for row in rows["BidMap"]}
    items = _current_item_semantics(rows["Item"])
    if len(rank_by_id) != len(rows["RankMap"]) or len(bid_by_id) != len(rows["BidMap"]):
        raise Update606PackageInputError("current table has duplicate id")
    droppable_ids = _current_droppable_ids(items_droppable, items)
    _validate_current_lifecycle(items)
    fitted_current = _current_item_fitted_values(items, droppable_ids)

    target_weights: dict[str, Any] = {}
    target_priors: dict[str, Any] = {}
    scope_receipts: dict[str, Any] = {}
    s2_calibration: dict[str, Any] | None = None
    for scope_name, identity in CURRENT_RANKMAP_SCOPE_IDENTITIES.items():
        map_ids = tuple(identity["map_ids"])
        try:
            rank_rows = [rank_by_id[map_id] for map_id in map_ids]
            bid_rows = [bid_by_id[map_id] for map_id in map_ids]
        except KeyError as exc:
            raise Update606PackageInputError(f"{scope_name} current row missing") from exc
        if (
            _ids_sha256(map_ids) != identity["map_ids_sha256"]
            or _rows_sha256(rank_rows) != identity["rankmap_rows_sha256"]
            or _rows_sha256(bid_rows) != identity["bidmap_rows_sha256"]
            or {row[7] for row in bid_rows} != {identity["tier"]}
        ):
            raise Update606PackageInputError(f"{scope_name} route/table identity mismatch")

        previous_support: tuple[int, ...] | None = None
        target_support: tuple[int, ...] | None = None
        for map_id, rank_row in zip(map_ids, rank_rows):
            entry = previous_weights.get(str(map_id))
            prior = previous_priors.get(str(map_id))
            item_rows = entry.get("items") if type(entry) is dict else None  # noqa: E721
            all_map_prior = current_all_priors.get(str(map_id))
            quality_probs = (
                all_map_prior.get("quality_probs")
                if scope_name == "route_stable_210x"
                and type(all_map_prior) is dict  # noqa: E721
                else prior.get("quality_probs")
                if type(prior) is dict  # noqa: E721
                else None
            )
            if (
                type(item_rows) is not dict  # noqa: E721
                or entry.get("n_items") != len(item_rows)
                or entry.get("count") != 16.0
                or type(quality_probs) is not dict  # noqa: E721
                or set(quality_probs) != {"q1", "q3", "q4", "q5", "q6"}
                or not math.isclose(
                    sum(float(value) for value in quality_probs.values()),
                    1.0,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                raise Update606PackageInputError(f"{scope_name} previous model malformed")
            base_ids: list[int] = []
            previous_triples: dict[int, list[Any]] = {}
            for raw_item_id, triple in item_rows.items():
                if (
                    type(raw_item_id) is not str  # noqa: E721
                    or not raw_item_id.isascii()
                    or not raw_item_id.isdecimal()
                    or type(triple) is not list  # noqa: E721
                    or len(triple) != 3
                ):
                    raise Update606PackageInputError(f"{scope_name} previous item malformed")
                item_id = int(raw_item_id)
                current_item = items.get(item_id)
                if (
                    current_item is None
                    or type(triple[1]) is not int  # noqa: E721
                    or type(triple[2]) is not int  # noqa: E721
                    or triple[1] != current_item["value"]
                ):
                    raise Update606PackageInputError(
                        f"{scope_name} previous/current Item mismatch"
                    )
                base_ids.append(item_id)
                previous_triples[item_id] = triple
            base = tuple(sorted(base_ids))
            if (
                len(base) != identity["previous_item_rows"]
                or _ids_sha256(base) != identity["previous_item_ids_sha256"]
            ):
                raise Update606PackageInputError(f"{scope_name} previous support mismatch")
            candidate = set(base) - set(RETIRED_SUMMER_PARTY_2026_ITEM_IDS)
            candidate.update(CURRENT_ALL_MAP_Q6_ITEM_IDS)
            if scope_name == "route_stable_210x":
                candidate.update(CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS)
            else:
                candidate.update(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)
            support = tuple(sorted(candidate))
            if (
                len(support) != identity["item_rows"]
                or _ids_sha256(support) != identity["item_ids_sha256"]
                or not set(support).issubset(droppable_ids)
                or set(CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS) & set(support)
                or set(RETIRED_SUMMER_PARTY_2026_ITEM_IDS) & set(support)
            ):
                raise Update606PackageInputError(f"{scope_name} current support mismatch")
            if previous_support is not None and previous_support != base:
                raise Update606PackageInputError(f"{scope_name} previous map support drift")
            if target_support is not None and target_support != support:
                raise Update606PackageInputError(f"{scope_name} current map support drift")
            previous_support, target_support = base, support

            if scope_name == "changed_route_ranked_560x":
                calibration = _s2_empirical_calibration_receipt(quality_probs)
                if s2_calibration is not None and s2_calibration != calibration:
                    raise Update606PackageInputError("S2 prior differs across current maps")
                s2_calibration = calibration
            try:
                anchors = json.loads(rank_row[6])
            except (TypeError, json.JSONDecodeError) as exc:
                raise Update606PackageInputError("current RankMap value curve is malformed") from exc
            probabilities = _probabilities_for_support(
                support=support,
                items=items,
                anchors=anchors,
                quality_probs=quality_probs,
            )
            output_items: dict[str, list[Any]] = {}
            for item_id in support:
                output_items[str(item_id)] = [
                    probabilities[item_id],
                    int(items[item_id]["value"]),
                    fitted_current[item_id],
                ]
            current_entry = {
                "count": 16.0,
                "n_items": len(output_items),
                "items": output_items,
            }
            current_prior = _updated_quality_prior(
                previous_entry=entry,
                current_entry=current_entry,
                previous_prior=prior,
                items=items,
            )
            if scope_name == "route_stable_210x" and (
                current_all_weights.get(str(map_id)) != current_entry
                or all_map_prior != current_prior
            ):
                raise Update606PackageInputError(
                    "route-stable current all-map overlap mismatch"
                )
            target_weights[str(map_id)] = current_entry
            target_priors[str(map_id)] = current_prior

        assert previous_support is not None and target_support is not None
        scope_receipts[scope_name] = {
            "map_ids": list(map_ids),
            "map_ids_sha256": identity["map_ids_sha256"],
            "rankmap_rows_sha256": identity["rankmap_rows_sha256"],
            "bidmap_rows_sha256": identity["bidmap_rows_sha256"],
            "tier": identity["tier"],
            "previous_item_rows": len(previous_support),
            "previous_item_ids_sha256": identity["previous_item_ids_sha256"],
            "current_item_rows": len(target_support),
            "current_item_ids_sha256": identity["item_ids_sha256"],
            "removed_item_ids": list(RETIRED_SUMMER_PARTY_2026_ITEM_IDS),
            "added_all_map_item_ids": list(CURRENT_ALL_MAP_Q6_ITEM_IDS),
            "added_scope_item_ids": list(
                CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS
                if scope_name == "route_stable_210x"
                else CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS
            ),
        }

    if s2_calibration is None:
        raise Update606PackageInputError("S2 empirical calibration was not exercised")
    weights_raw = json.dumps(target_weights, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    priors_raw = json.dumps(target_priors, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for label, raw in (("weights", weights_raw), ("priors", priors_raw)):
        identity = CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS[label]
        if identity["bytes"] and (
            len(raw) != identity["bytes"] or _sha256(raw) != identity["sha256"]
        ):
            raise Update606PackageInputError(f"current RankMap {label} output identity mismatch")
    receipt = {
        "schema": "bidking_current_rankmap_distribution_authority_v2",
        "steam_build_id": CURRENT_STEAM_BUILD_ID,
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "drop_or_dropmap_textasset": False,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "claims_item_level_drop_graph": False,
        "modeled_item_probabilities": True,
        "official_distribution_authority": False,
        "model_distribution_authority": True,
        "runtime_open_claim": True,
        "runtime_open_scope": "s2_ranked_5601_through_5611",
        "sources": {
            "RankMap": asdict(CURRENT_TEXT_ASSET_IDENTITIES["RankMap"]),
            "BidMap": asdict(CURRENT_TEXT_ASSET_IDENTITIES["BidMap"]),
            "Item": asdict(CURRENT_TEXT_ASSET_IDENTITIES["Item"]),
            "Map": asdict(CURRENT_TEXT_ASSET_IDENTITIES["Map"]),
            "items_droppable": {
                "bytes": len(items_droppable),
                "rows": len(droppable_ids),
                "item_ids_sha256": CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
                "sha256": _sha256(items_droppable),
            },
            "previous_current_weights": {
                "bytes": len(calibrated_weights),
                "sha256": _sha256(calibrated_weights),
            },
            "previous_current_priors": {
                "bytes": len(calibrated_priors),
                "sha256": _sha256(calibrated_priors),
            },
            "current_all_map_weights": {
                "bytes": len(current_all_map_weights),
                "sha256": _sha256(current_all_map_weights),
            },
            "current_all_map_priors": {
                "bytes": len(current_all_map_priors),
                "sha256": _sha256(current_all_map_priors),
            },
        },
        "algorithm": {
            "probability": "retained_quality_mass_times_rankmap_col6_value_weight_within_quality",
            "quality_prior_decision": "retained_after_current_empirical_ci_gate",
            "fitted_value": "all_current_support_union717_reference_normalized",
            "quality_values": (
                "q1_through_q5_previous_empirical_preserved;"
                "q6_previous_empirical_scaled_by_scope_current_model_delta"
            ),
        },
        "empirical_calibration": s2_calibration,
        "direct_s2_q6_canaries": {
            str(item_id): {"value": value, "shape": list(shape)}
            for item_id, (value, shape) in CURRENT_S2_DIRECT_Q6_CANARIES.items()
        },
        "scopes": scope_receipts,
        "outputs": {
            "weights": {"bytes": len(weights_raw), "maps": len(target_weights), "sha256": _sha256(weights_raw)},
            "priors": {"bytes": len(priors_raw), "maps": len(target_priors), "sha256": _sha256(priors_raw)},
        },
        "chenmei": {"map_id": 5506, "status": "pending_isolated"},
        "ranked_5606": {
            "map_id": 5606,
            "status": "modeled_empirically_calibrated",
            "runtime_open_claim": True,
        },
        "recognized_unmodeled_scopes": {
            "large_auction_6001": {
                "map_id": 6001,
                "static_table_present": 6001 in rank_by_id,
                "lifecycle": "dormant_history_no_current_entry",
                "distribution_authority": False,
                "quote_available": False,
            }
        },
        "raven_chaotic_space": raven_chaotic_space_authority_receipt(),
    }
    return weights_raw, priors_raw, receipt


def build_activity_2511_provisional_distribution(
    *,
    rankmap_text_asset: bytes,
    bidmap_text_asset: bytes,
    item_text_asset: bytes,
    items_droppable: bytes,
    static_data: bytes,
) -> bytes:
    """Selected b049-only, uncalibrated 2511 reference; no official authority.

    Reuse the current non-ranked 702-item support and existing col6 weighting.
    RankMap size/tag/value bands are bound as identity, not reinterpreted as
    hard counts/filters. No neighboring map prior or activity multiplier enters.
    """
    rows = {name: _exact_rows(name, raw) for name, raw in (
        ("RankMap", rankmap_text_asset), ("BidMap", bidmap_text_asset),
        ("Item", item_text_asset))}
    if CURRENT_PACKAGE_GENERATION != "b049c3-2026-09-10-597":
        raise Update606PackageInputError("2511 provisional generation mismatch")
    if _sha256(static_data) != "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676":
        raise Update606PackageInputError("2511 legacy quality prior input mismatch")
    rank = next(row for row in rows["RankMap"] if row[0] == "2511")
    bid = next(row for row in rows["BidMap"] if row[0] == "2511")
    items = _current_item_semantics(rows["Item"])
    droppable = _current_droppable_ids(items_droppable, items)
    _validate_current_lifecycle(items)
    support = tuple(sorted(set(droppable)
        - set(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)
        - set(CURRENT_RANKED_ONLY_MECH_Q6_ITEM_IDS)))
    if len(support) != 702 or _ids_sha256(support) != CURRENT_RANKMAP_SCOPE_IDENTITIES["route_stable_210x"]["item_ids_sha256"]:
        raise Update606PackageInputError("2511 current non-ranked support mismatch")
    masses = dict(zip(("q1", "q3", "q4", "q5", "q6"), (280, 240, 240, 120, 70)))
    quality_probs = {key: value / 950 for key, value in masses.items()}
    probabilities = _probabilities_for_support(support=support, items=items,
        anchors=json.loads(rank[6]), quality_probs=quality_probs)
    fitted = _current_item_fitted_values(items, droppable)
    entry = {"n_items": len(support), "items": {
        str(item_id): [probabilities[item_id], items[item_id]["value"], fitted[item_id]]
        for item_id in support}}
    prior = {"quality_probs": quality_probs, "quality_values": {
        key: int(round(_quality_fitted_mean(entry, items, key))) for key in masses}}
    result = {
        "schema": "bidking_activity_2511_provisional_b049_v1",
        "generation": CURRENT_PACKAGE_GENERATION, "map_id": 2511,
        "lifecycle": "provisional_uncalibrated", "provisional_quote_available": True,
        "official_distribution_authority": False, "model_distribution_authority": False,
        "official_distribution_eligible": False, "empirically_calibrated": False,
        "notice": "暂估价 · 本周掉率未校准",
        "algorithm": {
            "quality_prior_source": "legacy_StaticData_DropWeights_tier105",
            "quality_mass_six_colors": [80, 200, 240, 240, 120, 70],
            "quality_mass_collapsed": masses, "mass_total": 950,
            "probability": "legacy_quality_mass_times_this_map_col6_within_quality",
            "value": "this_map_col6_weighted_existing_current_fitted_catalog_value",
            "rankmap_size_tag_value_bands": "identity_only_not_hard_count_or_filter",
            "support": "current_non_ranked_702_item_support",
            "activity_multiplier": None, "calibration_sessions": 0,
        },
        "inputs": {name: {"bytes": len(raw), "sha256": _sha256(raw)} for name, raw in (
            ("RankMap", rankmap_text_asset), ("BidMap", bidmap_text_asset),
            ("Item", item_text_asset), ("items_droppable", items_droppable), ("StaticData", static_data))},
        "map_rows": {"RankMap": rank, "BidMap": bid},
        "support_ids_sha256": _ids_sha256(support), "weights": entry, "prior": prior,
    }
    return (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def workflow_input_fingerprint() -> str:
    payload = {
        "steam_build_id": CURRENT_STEAM_BUILD_ID,
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "appmanifest": CURRENT_APP_MANIFEST,
        "version_file": CURRENT_VERSION_FILE,
        "buildin_catalog_bytes": CURRENT_BUILDIN_CATALOG_BYTES,
        "buildin_catalog_json": CURRENT_BUILDIN_CATALOG_JSON,
        "package_hash_file": CURRENT_PACKAGE_HASH_FILE,
        "manifest": CURRENT_MANIFEST,
        "tables_bundle": CURRENT_TABLES_BUNDLE,
        "scripts": CURRENT_SCRIPTS,
        "text_assets": {
            name: asdict(identity)
            for name, identity in sorted(CURRENT_TEXT_ASSET_IDENTITIES.items())
        },
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "runtime_open_claim": True,
        "runtime_open_scope": "s2_ranked_5601_through_5611",
        "modeled_item_probabilities": True,
        "official_distribution_authority": False,
        "current_model_union": {
            "items": CURRENT_MODEL_UNION_ITEM_ROWS,
            "item_ids_sha256": CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
        },
        "current_all_map_scope": {
            "maps": CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["weights"]["maps"],
            "map_ids_sha256": CURRENT_ALL_MAP_IDS_SHA256,
            "items_per_map": 702,
            "item_ids_sha256": CURRENT_RANKMAP_SCOPE_IDENTITIES[
                "route_stable_210x"
            ]["item_ids_sha256"],
        },
        "quality_value_policy": (
            "q1_through_q5_previous_empirical_preserved;"
            "q6_previous_empirical_scaled_by_scope_current_model_delta"
        ),
        "lifecycle": {
            "retired_summer": list(RETIRED_SUMMER_PARTY_2026_ITEM_IDS),
            "all_map": list(CURRENT_ALL_MAP_Q6_ITEM_IDS),
            "non_s2_autumn": list(CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS),
            "s2_shadow_puppets": list(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS),
            "exchange_or_pass_excluded": list(CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS),
        },
        "empirical_calibration": S2_CLOUD_CALIBRATION,
        "recognized_unmodeled_scopes": {
            "large_auction_6001": {
                "map_id": 6001,
                "static_table_present": True,
                "lifecycle": "dormant_history_no_current_entry",
                "distribution_authority": False,
                "quote_available": False,
            }
        },
        "scopes": {
            name: {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in identity.items()
            }
            for name, identity in CURRENT_RANKMAP_SCOPE_IDENTITIES.items()
        },
        "raven_chaotic_space": raven_chaotic_space_authority_receipt(),
    }
    return _sha256(_canonical_json(payload))


CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT = workflow_input_fingerprint()


__all__ = (
    "OFFICIAL_20261008_PACKAGE_GENERATION",
    "OFFICIAL_20261008_PACKAGE_TABLES",
    "OFFICIAL_20261008_RUNTIME_MODEL",
    "CHANGED_ROUTE_RANKMAP_MAP_IDS",
    "CURRENT_ADDED_ITEM_IDS_SHA256",
    "CURRENT_ADDED_ITEM_ROWS",
    "CURRENT_ALL_MAP_Q6_ITEM_IDS",
    "CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS",
    "CURRENT_ALL_MAP_IDS_SHA256",
    "CURRENT_ALL_MAP_PRIOR_IDS_SHA256",
    "CURRENT_APP_MANIFEST",
    "CURRENT_BUILDIN_CATALOG_BYTES",
    "CURRENT_BUILDIN_CATALOG_JSON",
    "CURRENT_ITEMS_CATALOG",
    "CURRENT_MANIFEST",
    "CURRENT_MODEL_UNION_ITEM_IDS_SHA256",
    "CURRENT_MODEL_UNION_ITEM_ROWS",
    "CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS",
    "CURRENT_RANKED_ONLY_MECH_Q6_ITEM_IDS",
    "CURRENT_PACKAGE_GENERATION",
    "CURRENT_PACKAGE_BID_MAP",
    "CURRENT_PACKAGE_ITEM",
    "CURRENT_PACKAGE_HASH_FILE",
    "CURRENT_RANKMAP_AUTHORITY_MODE",
    "CURRENT_RANKMAP_AUTHORITY_OUTPUT",
    "CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS",
    "CURRENT_RANKMAP_MAP_IDS",
    "CURRENT_RANKMAP_PACKAGE",
    "CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT",
    "CURRENT_RED_LONGTAIL_NORMALIZED",
    "CURRENT_SCRIPTS",
    "CURRENT_S2_DIRECT_Q6_CANARIES",
    "CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS",
    "CURRENT_STEAM_BUILD_ID",
    "CURRENT_TABLES_BUNDLE",
    "CURRENT_TEXT_ASSET_IDENTITIES",
    "CURRENT_VERSION_FILE",
    "HISTORICAL_LOCALIZATION_ITEM_ID_ALIASES",
    "LEGACY_MAP_PAYLOADS",
    "LEGACY_PACKAGE_DROP",
    "PREVIOUS_ITEMS_CATALOG",
    "PREVIOUS_0_3_4_PACKAGE_BID_MAP",
    "PREVIOUS_0_3_4_PACKAGE_GENERATION",
    "PREVIOUS_0_3_4_PACKAGE_ITEM",
    "PREVIOUS_0_3_4_STEAM_BUILD_ID",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM",
    "PREVIOUS_OFFICIAL_20260903_STEAM_BUILD_ID",
    "PREVIOUS_CURRENT_RANKMAP_DISTRIBUTION_INPUTS",
    "PREVIOUS_ALL_MAP_DISTRIBUTION_INPUTS",
    "PREVIOUS_RED_LONGTAIL_NORMALIZED",
    "RAVEN_CHAOTIC_SPACE_MAP_IDS",
    "RAVEN_CHAOTIC_SPACE_POOL_IDS",
    "RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON",
    "ROUTE_STABLE_RANKMAP_MAP_IDS",
    "RETIRED_SUMMER_PARTY_2026_ITEM_IDS",
    "CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS",
    "S2_CLOUD_CALIBRATION",
    "UPDATE606_PACKAGE_BID_MAP",
    "UPDATE606_PACKAGE_ITEM",
    "Update606PackageInputError",
    "build_current_rankmap_distribution",
    "build_current_all_map_distribution",
    "build_current_red_longtail_normalized",
    "build_current_items_catalog",
    "build_current_package_table",
    "build_official_20261008_package_table",
    "build_current_rankmap_package_table",
    "raven_chaotic_space_authority_receipt",
    "validate_current_appmanifest",
    "validate_current_release_inputs",
)
