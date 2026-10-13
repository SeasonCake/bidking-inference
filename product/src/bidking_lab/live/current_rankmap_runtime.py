"""Fail-closed runtime binding for the current official RankMap prior.

Since 2026-06-25, the established distribution-input contract has used current
eligible support, RankMap/quality weights, the validated value-weight algorithm,
and settlement calibration without a Drop/DropMap TextAsset.  The binding
supplies that modeled distribution to the *same* pool ids named by the exact
current BidMap; it never aliases an id or claims an official item-level graph.
"""

from __future__ import annotations

import hashlib
import importlib
from importlib.machinery import ExtensionFileLoader, ModuleSpec, SourceFileLoader
import json
import math
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag

from bidking_lab.extract.bid_map_table import (
    BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION,
    BidMap,
)
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.extract.tables import decode_table_text, iter_table_rows
from bidking_lab.secure.data_crypt import DataKeyUnavailable, load_text
from bidking_lab.simulation.basic_mc import (
    FlattenedPool,
    _promote_verified_drop_pool_policy_generation,
    bind_rankmap_algorithm_prior,
)


_CURRENT_AUTHORITY_NAME = (
    "bidking_lab.extract.current_rankmap_package_contract"
)
_REPARSE_POINT = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class CurrentRankMapRuntimeAuthorityError(ImportError):
    """The protected current RankMap authority is not physically canonical."""


def _file_receipt(path: Path) -> tuple[int, int, int, int, int, int, str]:
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority file is unavailable"
        ) from exc
    before_identity = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
        int(before.st_ctime_ns),
        int(before.st_nlink),
    )
    after_identity = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
        int(after.st_ctime_ns),
        int(after.st_nlink),
    )
    if before_identity != after_identity:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority changed while validating"
        )
    return (*after_identity, digest.hexdigest())


def _require_regular_nonreparse_path(path: Path, *, leaf: bool) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority path is unavailable"
        ) from exc
    attributes = int(getattr(details, "st_file_attributes", 0))
    if path.is_symlink() or attributes & _REPARSE_POINT:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority path is non-canonical"
        )
    if leaf:
        if not stat.S_ISREG(details.st_mode) or int(details.st_nlink) != 1:
            raise CurrentRankMapRuntimeAuthorityError(
                "current_rankmap_package_contract runtime authority file identity is invalid"
            )
    elif not stat.S_ISDIR(details.st_mode):
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority ancestor is invalid"
        )


def _runtime_module_origin() -> tuple[Path, Path, str]:
    module = sys.modules.get(__name__)
    if type(module) is not ModuleType:  # noqa: E721
        raise CurrentRankMapRuntimeAuthorityError(
            "current RankMap runtime module identity is invalid"
        )
    spec = getattr(module, "__spec__", None)
    origin = getattr(module, "__file__", None)
    if (
        type(spec) is not ModuleSpec  # noqa: E721
        or spec.name != __name__
        or type(origin) is not str  # noqa: E721
        or spec.origin != origin
        or not os.path.isabs(origin)
    ):
        raise CurrentRankMapRuntimeAuthorityError(
            "current RankMap runtime module origin is invalid"
        )
    runtime_path = Path(origin)
    filename = runtime_path.name
    stem = "current_rankmap_runtime"
    if filename == f"{stem}.py" and type(spec.loader) is SourceFileLoader:  # noqa: E721
        authority_suffix = ".py"
    elif (
        filename.startswith(f"{stem}.")
        and filename.endswith(".pyd")
        and type(spec.loader) is ExtensionFileLoader  # noqa: E721
    ):
        authority_suffix = filename[len(stem) :]
    else:
        raise CurrentRankMapRuntimeAuthorityError(
            "current RankMap runtime module format is invalid"
        )
    if getattr(module, "__loader__", None) is not spec.loader:
        raise CurrentRankMapRuntimeAuthorityError(
            "current RankMap runtime loader identity is invalid"
        )
    source_root = runtime_path.parents[2]
    package_root = source_root.parent
    expected_runtime = source_root / "bidking_lab" / "live" / filename
    if str(runtime_path) != str(expected_runtime):
        raise CurrentRankMapRuntimeAuthorityError(
            "current RankMap runtime module is outside the approved root"
        )
    for component in (
        package_root,
        source_root,
        source_root / "bidking_lab",
        source_root / "bidking_lab" / "live",
    ):
        _require_regular_nonreparse_path(component, leaf=False)
    _require_regular_nonreparse_path(runtime_path, leaf=True)
    return runtime_path, source_root, authority_suffix


def _validate_current_authority(module: object) -> tuple[object, ...]:
    if type(module) is not ModuleType:  # noqa: E721
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority module identity is invalid"
        )
    spec = getattr(module, "__spec__", None)
    origin = getattr(module, "__file__", None)
    extract_package = sys.modules.get("bidking_lab.extract")
    _runtime_path, source_root, authority_suffix = _runtime_module_origin()
    expected = (
        source_root
        / "bidking_lab"
        / "extract"
        / f"current_rankmap_package_contract{authority_suffix}"
    )
    expected_loader = (
        SourceFileLoader if authority_suffix == ".py" else ExtensionFileLoader
    )
    if (
        getattr(module, "__name__", None) != _CURRENT_AUTHORITY_NAME
        or type(extract_package) is not ModuleType  # noqa: E721
        or getattr(
            extract_package,
            "current_rankmap_package_contract",
            None,
        )
        is not module
        or getattr(module, "__package__", None) != "bidking_lab.extract"
        or type(spec) is not ModuleSpec  # noqa: E721
        or spec.name != _CURRENT_AUTHORITY_NAME
        or spec.submodule_search_locations is not None
        or type(spec.loader) is not expected_loader  # noqa: E721
        or getattr(module, "__loader__", None) is not spec.loader
        or type(origin) is not str  # noqa: E721
        or spec.origin != origin
        or not os.path.isabs(origin)
        or str(Path(origin)) != str(expected)
        or getattr(spec.loader, "name", None) != _CURRENT_AUTHORITY_NAME
        or str(getattr(spec.loader, "path", "")) != str(expected)
    ):
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority module origin is invalid"
        )
    for component in (
        source_root.parent,
        source_root,
        source_root / "bidking_lab",
        source_root / "bidking_lab" / "extract",
    ):
        _require_regular_nonreparse_path(component, leaf=False)
    _require_regular_nonreparse_path(expected, leaf=True)
    current_receipt = (str(expected), *_file_receipt(expected))
    if (
        getattr(
            module,
            "_CURRENT_RANKMAP_RUNTIME_AUTHORITY_LOAD_RECEIPT",
            None,
        )
        != current_receipt
    ):
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority load receipt is stale"
        )
    return current_receipt


_CURRENT_AUTHORITY_MODULE = importlib.import_module(_CURRENT_AUTHORITY_NAME)
_CURRENT_AUTHORITY_RECEIPT = _validate_current_authority(
    _CURRENT_AUTHORITY_MODULE
)


def require_current_rankmap_runtime_authority() -> None:
    """Revalidate the exact cached authority and its backing file."""

    module = sys.modules.get(_CURRENT_AUTHORITY_NAME)
    if module is not _CURRENT_AUTHORITY_MODULE:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority cache identity changed"
        )
    if _validate_current_authority(module) != _CURRENT_AUTHORITY_RECEIPT:
        raise CurrentRankMapRuntimeAuthorityError(
            "current_rankmap_package_contract runtime authority file identity changed"
        )


CURRENT_PACKAGE_GENERATION = _CURRENT_AUTHORITY_MODULE.CURRENT_PACKAGE_GENERATION
CURRENT_RANKMAP_AUTHORITY_MODE = (
    _CURRENT_AUTHORITY_MODULE.CURRENT_RANKMAP_AUTHORITY_MODE
)
CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS = (
    _CURRENT_AUTHORITY_MODULE.CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS
)
CURRENT_RANKMAP_AUTHORITY_OUTPUT = (
    _CURRENT_AUTHORITY_MODULE.CURRENT_RANKMAP_AUTHORITY_OUTPUT
)
CURRENT_RANKMAP_MAP_IDS = _CURRENT_AUTHORITY_MODULE.CURRENT_RANKMAP_MAP_IDS
CURRENT_RANKMAP_PACKAGE = _CURRENT_AUTHORITY_MODULE.CURRENT_RANKMAP_PACKAGE
RAVEN_CHAOTIC_SPACE_MAP_IDS = (
    _CURRENT_AUTHORITY_MODULE.RAVEN_CHAOTIC_SPACE_MAP_IDS
)
RAVEN_CHAOTIC_SPACE_POOL_IDS = (
    _CURRENT_AUTHORITY_MODULE.RAVEN_CHAOTIC_SPACE_POOL_IDS
)
RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON = (
    _CURRENT_AUTHORITY_MODULE.RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON
)
RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT = (
    _CURRENT_AUTHORITY_MODULE.raven_chaotic_space_authority_receipt()
)
LEGACY_MAP_PAYLOADS = _CURRENT_AUTHORITY_MODULE.LEGACY_MAP_PAYLOADS
LEGACY_PACKAGE_DROP = _CURRENT_AUTHORITY_MODULE.LEGACY_PACKAGE_DROP
UPDATE606_PACKAGE_BID_MAP = _CURRENT_AUTHORITY_MODULE.UPDATE606_PACKAGE_BID_MAP
UPDATE606_PACKAGE_ITEM = _CURRENT_AUTHORITY_MODULE.UPDATE606_PACKAGE_ITEM
CURRENT_PACKAGE_BID_MAP = _CURRENT_AUTHORITY_MODULE.CURRENT_PACKAGE_BID_MAP
CURRENT_PACKAGE_ITEM = _CURRENT_AUTHORITY_MODULE.CURRENT_PACKAGE_ITEM
PREVIOUS_0_3_4_PACKAGE_ITEM = (
    _CURRENT_AUTHORITY_MODULE.PREVIOUS_0_3_4_PACKAGE_ITEM
)
PREVIOUS_0_3_4_PACKAGE_GENERATION = (
    _CURRENT_AUTHORITY_MODULE.PREVIOUS_0_3_4_PACKAGE_GENERATION
)
PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP = (
    _CURRENT_AUTHORITY_MODULE.PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP
)
PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM = (
    _CURRENT_AUTHORITY_MODULE.PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM
)
PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION = (
    _CURRENT_AUTHORITY_MODULE.PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION
)


PACKAGE_BASELINE_ID = (
    "package-data-2026-09-10-b049-activities"
)
HISTORICAL_AUCTION_HALL_6001_PACKAGE_BASELINE_ID = (
    "package-data-2026-08-27-0.3.4-4ae819"
)
_PACKAGE_FILES: Mapping[str, Mapping[str, Any]] = {
    "data/raw/tables/Item.txt": CURRENT_PACKAGE_ITEM,
    "data/raw/tables/BidMap.txt": CURRENT_PACKAGE_BID_MAP,
    "data/raw/tables/RankMap.txt": CURRENT_RANKMAP_PACKAGE,
    "data/processed/rankmap_drop_weights_current.json": (
        CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS["weights"]
    ),
    "data/processed/rankmap_drop_priors_current.json": (
        CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS["priors"]
    ),
    "data/processed/rankmap_distribution_authority_current.json": {
        **CURRENT_RANKMAP_AUTHORITY_OUTPUT,
    },
}
AUCTION_HALL_6001_SHADOW_RELATIVE = (
    "data/processed/auction_hall_6001_shadow_distribution.json"
)
AUCTION_HALL_6001_SHADOW = {
    "bytes": 279_564,
    "sha256": "b7135585a6e6230ac52522ab71f4d7943b068f880e61ec7817ca5342ee6808fa",
}
AUCTION_HALL_6001_PRIMARY_SCENARIO = (
    "group2601_placement_aware_candidate"
)
AUCTION_HALL_6001_UPPER_SCENARIO = "all_current_catalog_upper_envelope"
AUCTION_HALL_6001_REFERENCE_DISABLE_ENV = (
    "BIDKING_DISABLE_AUCTION_HALL_6001_PROVISIONAL_REFERENCE"
)
_AUCTION_HALL_6001_RECEIPT_KEYS = (
    "schema_version",
    "status",
    "generation",
    "map_id",
    "drop_pool_id",
    "table_identity",
    "primary_scenario",
    "primary_support_items",
    "upper_envelope_scenario",
    "upper_envelope_items",
    "provisional_distribution_available",
    "provisional_quote_available",
    "distribution_authority",
    "official_quote_authority",
    "bid_mode",
    "confidence",
)


def _auction_hall_6001_plain_string_fields(
    value: Any,
    expected_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    """Copy exact-string fields without invoking external key protocols."""

    if type(value) is not dict:  # noqa: E721 - external boundary
        return None
    fields: dict[str, Any] = {}
    for key, field_value in dict.items(value):
        if type(key) is not str or key not in expected_keys:  # noqa: E721
            return None
        fields[key] = field_value
    if len(fields) != len(expected_keys):
        return None
    return fields


def canonical_auction_hall_6001_provisional_receipt(
    value: Any,
) -> dict[str, Any] | None:
    """Validate and project a 6001 receipt to protocol-free builtin values."""

    fields = _auction_hall_6001_plain_string_fields(
        value,
        _AUCTION_HALL_6001_RECEIPT_KEYS,
    )
    if fields is None:
        return None
    identity = _auction_hall_6001_plain_string_fields(
        fields["table_identity"],
        ("bytes", "sha256"),
    )
    if identity is None:
        return None
    if (
        type(fields["schema_version"]) is not int  # noqa: E721
        or fields["schema_version"] != 1
        or type(fields["status"]) is not str  # noqa: E721
        or not str.__eq__(fields["status"], "provisional_estimated")
        or type(fields["generation"]) is not str  # noqa: E721
        or not str.__eq__(
            fields["generation"],
            PREVIOUS_0_3_4_PACKAGE_GENERATION,
        )
        or type(fields["map_id"]) is not int  # noqa: E721
        or fields["map_id"] != 6001
        or type(fields["drop_pool_id"]) is not int  # noqa: E721
        or fields["drop_pool_id"] != 2601
        or type(identity["bytes"]) is not int  # noqa: E721
        or identity["bytes"] != AUCTION_HALL_6001_SHADOW["bytes"]
        or type(identity["sha256"]) is not str  # noqa: E721
        or not str.__eq__(
            identity["sha256"],
            AUCTION_HALL_6001_SHADOW["sha256"],
        )
        or type(fields["primary_scenario"]) is not str  # noqa: E721
        or not str.__eq__(
            fields["primary_scenario"],
            AUCTION_HALL_6001_PRIMARY_SCENARIO,
        )
        or type(fields["primary_support_items"]) is not int  # noqa: E721
        or fields["primary_support_items"] != 673
        or type(fields["upper_envelope_scenario"]) is not str  # noqa: E721
        or not str.__eq__(
            fields["upper_envelope_scenario"],
            AUCTION_HALL_6001_UPPER_SCENARIO,
        )
        or type(fields["upper_envelope_items"]) is not int  # noqa: E721
        or fields["upper_envelope_items"] != 680
        or fields["provisional_distribution_available"] is not True
        or fields["provisional_quote_available"] is not True
        or fields["distribution_authority"] is not False
        or fields["official_quote_authority"] is not False
        or type(fields["bid_mode"]) is not str  # noqa: E721
        or not str.__eq__(fields["bid_mode"], "provisional_estimated_only")
        or type(fields["confidence"]) is not str  # noqa: E721
        or not str.__eq__(
            fields["confidence"],
            "weak_family_2601_generation_bound",
        )
    ):
        return None
    return {
        "schema_version": 1,
        "status": "provisional_estimated",
        "generation": PREVIOUS_0_3_4_PACKAGE_GENERATION,
        "map_id": 6001,
        "drop_pool_id": 2601,
        "table_identity": {
            "bytes": AUCTION_HALL_6001_SHADOW["bytes"],
            "sha256": AUCTION_HALL_6001_SHADOW["sha256"],
        },
        "primary_scenario": AUCTION_HALL_6001_PRIMARY_SCENARIO,
        "primary_support_items": 673,
        "upper_envelope_scenario": AUCTION_HALL_6001_UPPER_SCENARIO,
        "upper_envelope_items": 680,
        "provisional_distribution_available": True,
        "provisional_quote_available": True,
        "distribution_authority": False,
        "official_quote_authority": False,
        "bid_mode": "provisional_estimated_only",
        "confidence": "weak_family_2601_generation_bound",
    }


def _plain_bytes(path: Path) -> bytes:
    return load_text(path, encoding="utf-8").encode("utf-8")


def _exact(raw: bytes, spec: Mapping[str, Any]) -> bool:
    return (
        len(raw) == spec.get("bytes")
        and hashlib.sha256(raw).hexdigest() == spec.get("sha256")
    )


def _strict_json(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)


def load_auction_hall_6001_provisional_reference(
    *,
    package_root: Path,
) -> tuple[str, dict[str, Any]]:
    """Load a compact 6001 provisional receipt without binding a Drop pool."""

    if os.environ.get(AUCTION_HALL_6001_REFERENCE_DISABLE_ENV) == "1":
        return "disabled", {}
    try:
        item_raw = _plain_bytes(package_root / "data/raw/tables/Item.txt")
    except (DataKeyUnavailable, InvalidTag, OSError, UnicodeError, ValueError):
        return "package_generation_unavailable", {}
    if _exact(item_raw, CURRENT_PACKAGE_ITEM):
        return "dormant_current_generation", {}
    if not _exact(item_raw, PREVIOUS_0_3_4_PACKAGE_ITEM):
        return "package_generation_mixed", {}
    try:
        fingerprint = _strict_json(
            (package_root / "scripts" / "package_data_fingerprints.json").read_bytes()
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return "manifest_unavailable", {}
    manifest_files = (
        fingerprint.get("files") if type(fingerprint) is dict else None  # noqa: E721
    )
    expected_manifest_entry = {
        "sha256": AUCTION_HALL_6001_SHADOW["sha256"],
        "size": AUCTION_HALL_6001_SHADOW["bytes"],
    }
    if (
        type(fingerprint) is not dict  # noqa: E721
        or fingerprint.get("baseline_id")
        != HISTORICAL_AUCTION_HALL_6001_PACKAGE_BASELINE_ID
        or type(manifest_files) is not dict  # noqa: E721
        or manifest_files.get(AUCTION_HALL_6001_SHADOW_RELATIVE)
        != expected_manifest_entry
    ):
        return "manifest_mixed", {}
    try:
        raw = _plain_bytes(package_root / AUCTION_HALL_6001_SHADOW_RELATIVE)
    except (DataKeyUnavailable, InvalidTag, OSError, UnicodeError, ValueError):
        return "sidecar_unavailable", {}
    if not _exact(raw, AUCTION_HALL_6001_SHADOW):
        return "sidecar_identity_mismatch", {}
    try:
        document = _strict_json(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return "sidecar_malformed", {}
    algorithm = document.get("algorithm") if type(document) is dict else None  # noqa: E721
    summary = document.get("summary") if type(document) is dict else None  # noqa: E721
    scenarios = summary.get("scenarios") if type(summary) is dict else None  # noqa: E721
    primary = (
        scenarios.get(AUCTION_HALL_6001_PRIMARY_SCENARIO)
        if type(scenarios) is dict  # noqa: E721
        else None
    )
    upper = (
        scenarios.get(AUCTION_HALL_6001_UPPER_SCENARIO)
        if type(scenarios) is dict  # noqa: E721
        else None
    )
    items = document.get("items") if type(document) is dict else None  # noqa: E721
    if (
        document.get("schema")
        != "bidking_auction_hall_6001_shadow_distribution_v1"
        or document.get("generation") != PREVIOUS_0_3_4_PACKAGE_GENERATION
        or document.get("map_id") != 6001
        or document.get("lifecycle") != "recognized_provisional_shadow"
        or document.get("distribution_authority") is not False
        or document.get("official_distribution_eligible") is not False
        or document.get("quote_authority") is not False
        or document.get("official_quote_authority") is not False
        or document.get("provisional_distribution_available") is not True
        or document.get("provisional_quote_available") is not True
        or document.get("affects_bid") is not True
        or document.get("bid_mode") != "provisional_estimated_only"
        or type(algorithm) is not dict  # noqa: E721
        or algorithm.get("primary_scenario")
        != AUCTION_HALL_6001_PRIMARY_SCENARIO
        or algorithm.get("upper_envelope_scenario")
        != AUCTION_HALL_6001_UPPER_SCENARIO
        or algorithm.get("round_skill_group_ids") != [103, 103, 103, 104, 105]
        or algorithm.get("round_skill_group_policy")
        != "identity_only_not_distribution_input"
        or type(primary) is not dict  # noqa: E721
        or type(upper) is not dict  # noqa: E721
        or primary.get("support_items") != 673
        or upper.get("support_items") != 680
        or type(items) is not list  # noqa: E721
        or len(items) != 680
    ):
        return "sidecar_contract_mismatch", {}
    for scenario in (primary, upper):
        probability_sum = scenario.get("probability_sum")
        if (
            type(probability_sum) is not float  # noqa: E721
            or not math.isfinite(probability_sum)
            or not math.isclose(
                probability_sum,
                1.0,
                rel_tol=0.0,
                abs_tol=2e-12,
            )
        ):
            return "sidecar_probability_mismatch", {}
    return (
        "ok",
        {
            "schema_version": 1,
            "status": "provisional_estimated",
            "generation": PREVIOUS_0_3_4_PACKAGE_GENERATION,
            "map_id": 6001,
            "drop_pool_id": 2601,
            "table_identity": {
                "bytes": AUCTION_HALL_6001_SHADOW["bytes"],
                "sha256": AUCTION_HALL_6001_SHADOW["sha256"],
            },
            "primary_scenario": AUCTION_HALL_6001_PRIMARY_SCENARIO,
            "primary_support_items": 673,
            "upper_envelope_scenario": AUCTION_HALL_6001_UPPER_SCENARIO,
            "upper_envelope_items": 680,
            "provisional_distribution_available": True,
            "provisional_quote_available": True,
            "distribution_authority": False,
            "official_quote_authority": False,
            "bid_mode": "provisional_estimated_only",
            "confidence": "weak_family_2601_generation_bound",
        },
    )


def bind_current_rankmap_algorithm_prior(
    *,
    package_root: Path,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
) -> tuple[Mapping[int, DropPool], str, dict[str, Any]]:
    """Return exact modeled pools or the unchanged strict Drop mapping."""

    require_current_rankmap_runtime_authority()

    try:
        fingerprint_raw = (package_root / "scripts" / "package_data_fingerprints.json").read_bytes()
        fingerprint = _strict_json(fingerprint_raw)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return drops, "authority_manifest_unavailable", {}
    current_profile = _CURRENT_AUTHORITY_MODULE.OFFICIAL_20261008_RUNTIME_MODEL
    if type(fingerprint) is dict and fingerprint.get("baseline_id") == current_profile["baseline_id"]:
        return _bind_official_20261008_model(
            package_root=package_root, fingerprint=fingerprint,
            maps=maps, drops=drops, items=items, profile=current_profile,
        )
    if (
        type(fingerprint) is not dict  # noqa: E721
        or fingerprint.get("baseline_id") != PACKAGE_BASELINE_ID
        or type(fingerprint.get("files")) is not dict  # noqa: E721
    ):
        return drops, "authority_manifest_mixed", {}

    raw_by_path: dict[str, bytes] = {}
    for relative, spec in _PACKAGE_FILES.items():
        manifest_spec = fingerprint["files"].get(relative)
        if (
            type(manifest_spec) is not dict  # noqa: E721
            or manifest_spec.get("size") != spec["bytes"]
            or manifest_spec.get("sha256") != spec["sha256"]
        ):
            return drops, "authority_manifest_mixed", {}
        try:
            raw = _plain_bytes(package_root / relative)
        except InvalidTag:
            return drops, "authority_input_authentication_mismatch", {}
        except (DataKeyUnavailable, OSError, UnicodeError, ValueError):
            return drops, "authority_input_unavailable", {}
        if not _exact(raw, spec):
            return drops, "authority_input_hash_mismatch", {}
        raw_by_path[relative] = raw

    try:
        weights = _strict_json(
            raw_by_path["data/processed/rankmap_drop_weights_current.json"]
        )
        priors = _strict_json(
            raw_by_path["data/processed/rankmap_drop_priors_current.json"]
        )
        authority = _strict_json(
            raw_by_path[
                "data/processed/rankmap_distribution_authority_current.json"
            ]
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return drops, "authority_payload_unavailable", {}

    expected_keys = {str(map_id) for map_id in CURRENT_RANKMAP_MAP_IDS}
    if (
        type(weights) is not dict  # noqa: E721
        or type(priors) is not dict  # noqa: E721
        or set(weights) != expected_keys
        or set(priors) != expected_keys
        or type(authority) is not dict  # noqa: E721
        or authority.get("schema")
        != "bidking_current_rankmap_distribution_authority_v2"
        or authority.get("package_generation") != CURRENT_PACKAGE_GENERATION
        or authority.get("authority_mode") != CURRENT_RANKMAP_AUTHORITY_MODE
        or authority.get("drop_or_dropmap_textasset") is not False
        or authority.get("drop_or_dropmap_absence_is_not_a_blocker") is not True
        or authority.get("claims_item_level_drop_graph") is not False
        or authority.get("modeled_item_probabilities") is not True
        or authority.get("official_distribution_authority") is not False
        or authority.get("model_distribution_authority") is not True
        or authority.get("runtime_open_claim") is not True
        or authority.get("runtime_open_scope")
        != "s2_ranked_5601_through_5611"
        or authority.get("ranked_5606")
        != {
            "map_id": 5606,
            "status": "modeled_empirically_calibrated",
            "runtime_open_claim": True,
        }
        or authority.get("recognized_unmodeled_scopes")
        != {
            "large_auction_6001": {
                "map_id": 6001,
                "static_table_present": True,
                "lifecycle": "dormant_history_no_current_entry",
                "distribution_authority": False,
                "quote_available": False,
            }
        }
        or authority.get("chenmei")
        != {"map_id": 5506, "status": "pending_isolated"}
        or authority.get("raven_chaotic_space")
        != RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT
        or type(authority.get("empirical_calibration")) is not dict  # noqa: E721
        or authority["empirical_calibration"].get("sessions") != 191
        or authority["empirical_calibration"].get("settled_items") != 7_126
        or authority["empirical_calibration"].get("decision")
        != "retain_previous_quality_prior"
    ):
        return drops, "authority_payload_malformed", {}

    flattened_by_pool_id: dict[int, FlattenedPool] = {}
    for map_id in CURRENT_RANKMAP_MAP_IDS:
        bid_map = maps.get(map_id)
        row = weights.get(str(map_id))
        item_rows = row.get("items") if type(row) is dict else None  # noqa: E721
        if bid_map is None or type(item_rows) is not dict:  # noqa: E721
            return drops, "authority_route_mismatch", {}
        item_ids: list[int] = []
        probabilities: list[float] = []
        values: list[int] = []
        try:
            for raw_item_id, triple in item_rows.items():
                if (
                    type(raw_item_id) is not str  # noqa: E721
                    or not raw_item_id.isascii()
                    or not raw_item_id.isdecimal()
                    or type(triple) is not list  # noqa: E721
                    or len(triple) != 3
                ):
                    raise ValueError("malformed item prior")
                item_id = int(raw_item_id)
                item = items.get(item_id)
                probability = float(triple[0])
                if (
                    item is None
                    or not 0.0 <= probability <= 1.0
                    or int(triple[1]) != item.value
                ):
                    raise ValueError("item prior mismatch")
                if probability > 0.0:
                    item_ids.append(item_id)
                    probabilities.append(probability)
                    values.append(item.value)
        except (TypeError, ValueError, OverflowError):
            return drops, "authority_item_mismatch", {}
        probability_sum = sum(probabilities)
        if not item_ids or not 0.999 <= probability_sum <= 1.001:
            return drops, "authority_probability_mismatch", {}
        probabilities = [value / probability_sum for value in probabilities]
        pool_id = int(bid_map.drop_pool_id)
        if pool_id in flattened_by_pool_id:
            return drops, "authority_pool_collision", {}
        flattened_by_pool_id[pool_id] = FlattenedPool(
            item_ids=item_ids,
            probabilities=probabilities,
            n_min=[1] * len(item_ids),
            n_max=[1] * len(item_ids),
            values=values,
            resolution_status="complete",
            resolution_policy=CURRENT_RANKMAP_AUTHORITY_MODE,
            resolution_generation=CURRENT_PACKAGE_GENERATION,
        )

    receipt = {
        "schema_version": 1,
        "status": "current_rankmap_algorithm_prior",
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "drop_or_dropmap_textasset": False,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "claims_item_level_drop_graph": False,
        "modeled_item_probabilities": True,
        "official_distribution_authority": False,
        "model_distribution_authority": True,
        "runtime_open_claim": True,
        "runtime_open_scope": "s2_ranked_5601_through_5611",
        "scope_map_count": len(CURRENT_RANKMAP_MAP_IDS),
        "ranked_5606": "modeled_empirically_calibrated",
        "empirical_calibration": {
            "sessions": 191,
            "settled_items": 7_126,
            "decision": "retain_previous_quality_prior",
        },
        "chenmei_5506": "pending_isolated",
        "recognized_unmodeled_scopes": {
            "large_auction_6001": {
                "map_id": 6001,
                "static_table_present": True,
                "lifecycle": "dormant_history_no_current_entry",
                "distribution_authority": False,
                "quote_available": False,
            }
        },
        "raven_chaotic_space": dict(RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT),
    }
    composition_drops = _promote_verified_drop_pool_policy_generation(
        drops,
        source_package_generation=BID_MAP_OFFICIAL_20260903_HOTFIX_GENERATION,
        package_generation=CURRENT_PACKAGE_GENERATION,
    )
    return (
        bind_rankmap_algorithm_prior(
            composition_drops,
            flattened_by_pool_id,
            package_generation=CURRENT_PACKAGE_GENERATION,
            authority_mode=CURRENT_RANKMAP_AUTHORITY_MODE,
        ),
        "ok",
        receipt,
    )


def _bind_official_20261008_model(
    *, package_root: Path, fingerprint: dict[str, Any],
    maps: Mapping[int, BidMap], drops: Mapping[int, DropPool],
    items: Mapping[int, Item], profile: Mapping[str, Any],
) -> tuple[Mapping[int, DropPool], str, dict[str, Any]]:
    """Select exact modeled distributions by physical map, including shared pools."""
    manifest_files = fingerprint.get("files")
    if type(manifest_files) is not dict:
        return drops, "authority_manifest_mixed", {}
    raw_by_path = {}
    for relative, pin in profile["files"].items():
        manifest_pin = manifest_files.get(relative)
        if (type(manifest_pin) is not dict or manifest_pin.get("size") != pin["bytes"]
                or manifest_pin.get("sha256") != pin["sha256"]):
            return drops, "authority_manifest_mixed", {}
        try:
            raw = _plain_bytes(package_root / relative)
        except InvalidTag:
            return drops, "authority_input_authentication_mismatch", {}
        except (DataKeyUnavailable, OSError, UnicodeError, ValueError):
            return drops, "authority_input_unavailable", {}
        if not _exact(raw, pin):
            return drops, "authority_input_hash_mismatch", {}
        raw_by_path[relative] = raw

    try:
        authority = _strict_json(raw_by_path["data/processed/rankmap_distribution_authority_current.json"])
        ordinary = _strict_json(raw_by_path["data/processed/drop_weights_all_maps_0627.json"])
        ranked = _strict_json(raw_by_path["data/processed/rankmap_drop_weights_current.json"])
        if (type(authority) is not dict or authority.get("schema") != profile["schema"]
                or authority.get("package_generation") != profile["generation"]
                or authority.get("authority_mode") != profile["authority_mode"]
                or authority.get("model_distribution_authority") is not True
                or authority.get("official_distribution_authority") is not False
                or authority.get("claims_item_level_drop_graph") is not False
                or authority.get("drop_or_dropmap_absence_is_not_a_blocker") is not True
                or authority.get("runtime_open_claim") is not False
                or type(ordinary) is not dict or type(ranked) is not dict):
            return drops, "authority_payload_malformed", {}
        if any(ordinary[key] != ranked[key] for key in ordinary.keys() & ranked.keys()):
            return drops, "authority_payload_malformed", {}
        weights = ordinary | ranked
        if len(weights) != profile["scope_map_count"]:
            return drops, "authority_payload_malformed", {}
        exact_maps = _CURRENT_AUTHORITY_MODULE.parse_bid_map_table(list(iter_table_rows(
            decode_table_text(raw_by_path["data/raw/tables/BidMap.txt"].decode("utf-8")))))
        exact_items = _CURRENT_AUTHORITY_MODULE.parse_item_table(list(iter_table_rows(
            decode_table_text(raw_by_path["data/raw/tables/Item.txt"].decode("utf-8")))))
    except (UnicodeError, ValueError, TypeError, KeyError):
        return drops, "authority_payload_unavailable", {}

    scoped: dict[int, tuple[int, FlattenedPool]] = {}
    for key, model in weights.items():
        if type(key) is not str or not key.isascii() or not key.isdecimal():
            return drops, "authority_route_mismatch", {}
        mid = int(key)
        bid_map, exact_map = maps.get(mid), exact_maps.get(mid)
        route_fields = ("map_id", "drop_pool_id", "sub_pool_weights", "items_per_session_min", "items_per_session_max")
        if (bid_map is None or exact_map is None
                or any(getattr(bid_map, name, None) != getattr(exact_map, name) for name in route_fields)):
            return drops, "authority_route_mismatch", {}
        item_rows = model.get("items") if type(model) is dict else None
        if type(item_rows) is not dict:
            return drops, "authority_payload_malformed", {}
        ids, probabilities, values = [], [], []
        for item_key, triple in item_rows.items():
            try:
                iid = int(item_key)
                item, exact_item = items.get(iid), exact_items.get(iid)
                if (item is None or exact_item is None or type(triple) is not list or len(triple) != 3
                        or any(getattr(item, name) != getattr(exact_item, name)
                               for name in ("value", "quality", "shape_w", "shape_h"))
                        or triple[1] != item.value):
                    return drops, "authority_item_mismatch", {}
                probability = float(triple[0])
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    return drops, "authority_probability_mismatch", {}
            except (ValueError, TypeError, OverflowError, AttributeError):
                return drops, "authority_item_mismatch", {}
            if probability > 0:
                ids.append(iid)
                probabilities.append(probability)
                values.append(item.value)
        total = sum(probabilities)
        if not ids or not math.isclose(total, 1.0, abs_tol=1e-8, rel_tol=0):
            return drops, "authority_probability_mismatch", {}
        scoped[mid] = (bid_map.drop_pool_id, FlattenedPool(
            item_ids=ids, probabilities=[value / total for value in probabilities],
            n_min=[1] * len(ids), n_max=[1] * len(ids), values=values,
            resolution_policy=profile["authority_mode"], resolution_generation=profile["generation"],
        ))
    receipt = {
        "schema_version": 1, "status": "current_rankmap_algorithm_prior",
        "authority_mode": profile["authority_mode"], "package_generation": profile["generation"],
        "drop_or_dropmap_textasset": False, "drop_or_dropmap_absence_is_not_a_blocker": True,
        "claims_item_level_drop_graph": False, "modeled_item_probabilities": True,
        "model_distribution_authority": True, "official_distribution_authority": False,
        "runtime_open_claim": False, "scope_map_count": len(scoped),
        "scope": "physical_map_conditioned_ordinary_and_ranked",
        "recognized_unmodeled_scopes": dict(authority["excluded_scopes"]),
    }
    return (bind_rankmap_algorithm_prior(
        drops, {}, package_generation=profile["generation"], authority_mode=profile["authority_mode"],
        flattened_by_map_id=scoped), "ok", receipt)


__all__ = (
    "CURRENT_RANKMAP_MAP_IDS",
    "CURRENT_RANKMAP_AUTHORITY_OUTPUT",
    "CurrentRankMapRuntimeAuthorityError",
    "CURRENT_PACKAGE_BID_MAP",
    "CURRENT_PACKAGE_ITEM",
    "AUCTION_HALL_6001_REFERENCE_DISABLE_ENV",
    "AUCTION_HALL_6001_SHADOW",
    "AUCTION_HALL_6001_SHADOW_RELATIVE",
    "LEGACY_MAP_PAYLOADS",
    "LEGACY_PACKAGE_DROP",
    "PACKAGE_BASELINE_ID",
    "HISTORICAL_AUCTION_HALL_6001_PACKAGE_BASELINE_ID",
    "PREVIOUS_0_3_4_PACKAGE_GENERATION",
    "PREVIOUS_0_3_4_PACKAGE_ITEM",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION",
    "PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM",
    "RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT",
    "RAVEN_CHAOTIC_SPACE_MAP_IDS",
    "RAVEN_CHAOTIC_SPACE_POOL_IDS",
    "RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON",
    "UPDATE606_PACKAGE_BID_MAP",
    "UPDATE606_PACKAGE_ITEM",
    "bind_current_rankmap_algorithm_prior",
    "load_auction_hall_6001_provisional_reference",
    "require_current_rankmap_runtime_authority",
)
