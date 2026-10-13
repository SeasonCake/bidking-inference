"""Live monitor artifact builder and append-only evaluation logs.

The monitor layer is intentionally source-agnostic: today it can process a
Fatbeans JSON payload or file; later a true realtime source can feed the same
``FatbeansCaptureEvents`` object without changing inference, logging, or UI.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import threading
import time
from typing import Any, Mapping, Sequence

from bidking_lab._atomic_json import atomic_write_json as _atomic_write_json
from bidking_lab.config import project_root
from bidking_lab.data.item_category_authority import (
    ItemCategoryAuthority,
    ItemCategoryAuthorityError,
    load_item_category_authority_document,
)
from bidking_lab.extract.bid_map_table import (
    BID_MAP_UPDATE606_GENERATION,
    BidMap,
    load_bid_map_table,
)
from bidking_lab.extract.drop_table import DropPool, load_drop_table
from bidking_lab.extract.item_table import Item, load_item_table
from bidking_lab.extract.tables import decode_table_text
from bidking_lab.inference.bid_strategy import recommend_bid_strategy
from bidking_lab.inference.diagnostics import layout_conflict_root
from bidking_lab.inference.size_avg_evidence import (
    SHAPE_AVG_EXACT_CONSTRAINT_DISABLE,
    SIZE_AVG_ACTION_FOOTPRINT,
    shape_average_action_observation_fingerprint,
    shape_average_value_evidence_from_action_rows,
    size_bucket_count_targets_for_structured_evidence,
    size_bucket_eval_fields,
)
from bidking_lab.inference.q6_residual import (
    AISHA_BOTTOM_ROW_RISK_THRESHOLD,
    AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD,
    actionable_random_sample_avg_values,
    aisha_bottom_row_risk,
    aisha_q6_quality_only_deep_local_risk,
    evidence_profile_key_from_problem,
    q6_conditional_target_active_for_profile,
    q6_quality_only_local_diagnostics,
    q6_residual_boost_for_profile,
    q6_residual_prior_floor_ratio_for_profile,
)
from bidking_lab.inference.v2 import (
    PHYSICAL_CATEGORY_DOMAIN_DISABLE,
    PhysicalCandidateDomain,
    ResidualProblem,
    build_residual_problem,
    canonical_map_footprint_domain,
    estimate_posterior_v2,
    evidence_store_from_fatbeans_events,
    is_tail_supported_by_evidence,
)
from bidking_lab.inference.v3 import (
    assess_prior_robustness,
    compile_feasible_summary,
    compile_hard_constraints,
    capacity_source_expansion_entry_for,
    empty_capacity_source_expansion_flat_dict,
    empty_feasible_summary_flat_dict,
    empty_formal_value_sampler_flat_dict,
    empty_posterior_flat_dict,
    empty_practical_advisory_flat_dict,
    empty_prior_calibration_flat_dict,
    empty_prior_flat_dict,
    empty_prior_robustness_flat_dict,
    empty_residual_gate_flat_dict,
    empty_settlement_count_prior_flat_dict,
    empty_tail_value_review_flat_dict,
    empty_underestimate_repair_flat_dict,
    estimate_shadow_pipeline,
    events_from_fatbeans,
    load_capacity_source_expansion_entries,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
    ordinary_shape_replacement_values,
    sample_truth_bank,
    summarize_drop_prior_flat_dict,
    settlement_count_prior_entry_for,
    tail_value_review_entry_for,
    underestimate_entry_for,
)
from bidking_lab.inference.map_likelihood import (
    QuantileSummary,
    estimate_map_likelihood,
)
from bidking_lab.inference.tool_info_roi import estimate_tool_info_roi
from bidking_lab.inference.warehouse_estimator import estimate_warehouse_cells
from bidking_lab.live.evaluation import evaluate_fatbeans_layout_events
from bidking_lab.live.fatbeans import (
    _ACTION_AVG_CELLS,
    _ACTION_COUNT,
    _ACTION_GREEN_SPLIT_FIELDS,
    _ACTION_SESSION_FIELDS,
    _ACTION_SIZE_AVG_VALUE,
    _ACTION_TOTAL_CELLS,
    _ACTION_VALUE_SUM,
    _CATEGORY_OUTLINE_ACTIONS,
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansRankScoreChange,
    POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE,
    hero_mode_from_state,
    latest_player_bids,
    local_player_bid_summary,
    parse_fatbeans_capture,
    parse_fatbeans_capture_payload,
)
from bidking_lab.live.layout import SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY
from bidking_lab.live.layout import latest_grid_batch
from bidking_lab.live.catalog_value import summarize_catalog_values
from bidking_lab.live.replay import layout_replay_stages
from bidking_lab.live.state import (
    LiveSessionState,
    apply_observation_batch,
    live_state_to_session_obs,
)
from bidking_lab.live.types import GridItemObservation, LiveObservationBatch
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events
from bidking_lab.live.warehouse_source import (
    HOTFIX_20260903_PACKAGE_ITEM,
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260903_PACKAGE_ITEM,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260910_PACKAGE_ITEM,
    OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20261008_PACKAGE_ITEM,
    OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
    OWNER_EVIDENCE_ABSENT,
    OWNER_EVIDENCE_CONFLICT,
    OWNER_EVIDENCE_VALID,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    classify_from_map_table,
    warehouse_generation_from_live_evidence,
    warehouse_package_generation_from_exact_inputs,
)
from bidking_lab.live.current_rankmap_runtime import (
    CURRENT_PACKAGE_BID_MAP,
    CURRENT_PACKAGE_GENERATION,
    CURRENT_PACKAGE_ITEM,
    CURRENT_RANKMAP_MAP_IDS,
    LEGACY_MAP_PAYLOADS,
    LEGACY_PACKAGE_DROP,
    RAVEN_CHAOTIC_SPACE_MAP_IDS,
    RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
    UPDATE606_PACKAGE_BID_MAP,
    UPDATE606_PACKAGE_ITEM,
    bind_current_rankmap_algorithm_prior,
    canonical_auction_hall_6001_provisional_receipt,
    load_auction_hall_6001_provisional_reference,
    require_current_rankmap_runtime_authority,
)
from bidking_lab.runtime import (
    layout_replay_rows_from_stages,
    tactical_panel_from_rows,
    ui_contract_from_artifact,
)
from bidking_lab.secure.data_crypt import load_text
from bidking_lab.simulation.robust_value import (
    DEFAULT_VALUE_FLOOR,
    is_confusable_long_tail,
)
from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionPolicy,
    _bind_verified_drop_pool_resolution_policy,
    flatten_pool,
)

require_current_rankmap_runtime_authority()

RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE = "raven_preopen_510x_to_250x_template"
RAVEN_TEMPLATE_ESTIMATE_DISABLE_ENV = "BIDKING_DISABLE_RAVEN_TEMPLATE_ESTIMATE"
RAVEN_TEMPLATE_ESTIMATE_LABEL = "模板估算"
_RAVEN_ROOT_SELECTOR_ENTRIES = (
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
)

_CATEGORY_LABELS = {
    101: "家具",
    102: "医疗",
    103: "时尚",
    104: "武器",
    105: "珠宝",
    106: "古董",
    107: "数码",
    108: "能源",
    109: "食饮",
    110: "书画",
}
DEFAULT_Q6_SHADOW_TRIALS_CAP = 80
SETTLEMENT_CATEGORY_FACT_SCHEMA = "settlement_category_fact_v1"
SETTLEMENT_CATEGORY_FACT_SCHEMA_V2 = "settlement_category_fact_v2"
SETTLEMENT_CATEGORY_ITEMS_SCHEMA = "settled_items_v1"
MAX_SETTLEMENT_CATEGORY_ITEMS = 512
SETTLEMENT_CATEGORY_FACTS_DISABLE = "BIDKING_DISABLE_SETTLEMENT_CATEGORY_FACTS"
OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "72c3430e82e82a4b01a1fa42e79335b50ab6bbbb7f09c9b438b2511caef566c3"
)
OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY = (
    "2e70477d3b0ccc6e1b764c5ecbfce2eb128a330a480e9e4a62875a1f52e8aab7"
)
HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
)
HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY = (
    "d23d90c0d4f432f94a129435417063177ab21646fa4987dce5118ccce5a55db5"
)
OFFICIAL_20260910_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47"
)
OFFICIAL_20260910_CATEGORY_AUTHORITY_IDENTITY = (
    "18256cb459f2c748bb1e83e6c899df255189a0f557e8a0be990c05ef865f079a"
)
OFFICIAL_20261008_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372"
)
OFFICIAL_20261008_CATEGORY_AUTHORITY_IDENTITY = (
    "36600b5116701a8152af992e51e1c7d6a0492fed0e841d36c79e0de285f89dde"
)
_CURRENT_CATEGORY_AUTHORITY_GENERATIONS = frozenset(
    {
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
    }
)


def _current_category_authority_profile(
    package_generation: str,
) -> dict[str, Any] | None:
    # Resolve constants at call time so positive-control tests can prove each
    # exact identity is actually enforced rather than captured at import.
    if package_generation == OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION:
        return {
            "package_item": OFFICIAL_20260903_PACKAGE_ITEM,
            "generation": OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION,
            "identity": OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY,
            "item_rows": 1427,
        }
    if package_generation == HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION:
        return {
            "package_item": HOTFIX_20260903_PACKAGE_ITEM,
            "generation": HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION,
            "identity": HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY,
            "item_rows": 1427,
        }
    if package_generation == OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION:
        return {
            "package_item": OFFICIAL_20260910_PACKAGE_ITEM,
            "generation": OFFICIAL_20260910_CATEGORY_AUTHORITY_GENERATION,
            "identity": OFFICIAL_20260910_CATEGORY_AUTHORITY_IDENTITY,
            "item_rows": 1432,
        }
    if package_generation == OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION:
        return {
            "package_item": OFFICIAL_20261008_PACKAGE_ITEM,
            "generation": OFFICIAL_20261008_CATEGORY_AUTHORITY_GENERATION,
            "identity": OFFICIAL_20261008_CATEGORY_AUTHORITY_IDENTITY,
            "item_rows": 1506,
            "item_columns": 45,
        }
    return None
_RAVEN_SETTLEMENT_MODEL_MAP_OFFSET = 2600
_RAVEN_SETTLEMENT_PHYSICAL_MAP_IDS = frozenset(range(5101, 5111))
LIVE_FORMAL_MODE_ENV = "BIDKING_LIVE_FORMAL_MODE"
LIVE_FORMAL_MODE_V2 = "v2"
LIVE_FORMAL_MODE_V3_PRACTICAL = "v3_practical"
_LIVE_FORMAL_MODE_ALIASES = {
    "v2": LIVE_FORMAL_MODE_V2,
    "baseline": LIVE_FORMAL_MODE_V2,
    "baseline_v2": LIVE_FORMAL_MODE_V2,
    "v3": LIVE_FORMAL_MODE_V3_PRACTICAL,
    "v3_practical": LIVE_FORMAL_MODE_V3_PRACTICAL,
    "v3-practical": LIVE_FORMAL_MODE_V3_PRACTICAL,
    "practical": LIVE_FORMAL_MODE_V3_PRACTICAL,
}
_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_CONFIDENCES = {"low", "low_medium"}
_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_FLAGS = {
    "q6_prior_floor_watch",
    "q6_prior_tail_ceiling",
    "settlement_count_prior_candidate",
    "capacity_source_candidate",
}
_LIVE_V3_STRONG_RAISE_EVIDENCE_FLAGS = {
    "value_floor_candidate",
    "underestimate_repair_candidate",
    "tail_value_candidate",
    "random_avg_value_floor_watch",
    "random_avg_high_signal_ceiling",
    "q6_value_ceiling_watch",
    "source_profile_q6_tail_ceiling",
}
_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MIN_GAP = 100_000.0
_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MAX_P50_MULTIPLIER = 1.25
_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MIN_HEADROOM = 75_000.0
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MAX_ESS = 5.0
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MIN_P50_GAP = 180_000.0
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P50_KEEP_RATIO = 0.70
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MIN_HEADROOM = 75_000.0
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P90_MULTIPLIER = 1.35
_LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P90_HEADROOM = 150_000.0
_TRUSTED_SESSION_TOTAL_CONFIDENCES = {"high", "exact"}
_SAFE_SESSION_TOTAL_FIELDS = (
    "warehouse_total_cells",
    "warehouse_total_cells_approx",
    "warehouse_total_cells_tolerance",
    "total_item_count",
)
_V3_PRIOR_CALIBRATION_CACHE: dict[int, Any] | None = None
_V3_UNDERESTIMATE_REPAIR_CACHE: dict[tuple[str, int], Any] | None = None
_V3_TAIL_VALUE_REVIEW_CACHE: dict[tuple[str, int], Any] | None = None
_V3_SETTLEMENT_COUNT_PRIOR_CACHE: dict[tuple[str, str], Any] | None = None
_V3_CAPACITY_SOURCE_EXPANSION_CACHE: dict[tuple[str, str], Any] | None = None


@dataclass
class _SessionGameVersionBindings:
    """Bind one stable disk generation to each observed semantic session."""

    first_by_session: dict[str, str] = field(default_factory=dict)
    conflicted_sessions: set[str] = field(default_factory=set)
    lock: threading.Lock = field(
        default_factory=threading.Lock,
        compare=False,
        repr=False,
    )

    def resolve(self, session_id: str | None, fresh_version: str) -> tuple[str, bool]:
        if session_id is None:
            # Live generation authority is session-bound.  A frame without a
            # confirmed semantic session can retain the freshly observed disk
            # value for diagnostics, but must not authorize RankMap or PW2.
            return fresh_version, False
        with self.lock:
            if session_id not in self.first_by_session:
                self.first_by_session[session_id] = fresh_version
                if not fresh_version:
                    self.conflicted_sessions.add(session_id)
            elif self.first_by_session[session_id] != fresh_version:
                self.conflicted_sessions.add(session_id)
            return fresh_version, session_id not in self.conflicted_sessions


@dataclass(frozen=True)
class MonitorTables:
    """Loaded local game tables used by live monitor inference."""

    maps: Mapping[int, BidMap]
    drops: Mapping[int, DropPool]
    items: Mapping[int, Item]
    rankmap_distribution_status: str = "not_configured"
    rankmap_distribution_receipt: Mapping[str, Any] = field(default_factory=dict)
    auction_hall_6001_status: str = "not_configured"
    auction_hall_6001_receipt: Mapping[str, Any] = field(default_factory=dict)
    warehouse_package_generation: str = "unknown_warehouse_generation"
    warehouse_game_version: str = ""
    warehouse_game_version_path: str = ""
    warehouse_live_identity_check: bool = False
    warehouse_session_versions: _SessionGameVersionBindings = field(
        default_factory=_SessionGameVersionBindings,
        compare=False,
        repr=False,
    )


# Private bounded transport for the idle-full worker. No pickle hooks, locks,
# filesystem writers or live monitor objects cross this boundary. Each JSON
# token is bounded and parent traversal can yield between every graph node.
_FULL_WIRE_CAPACITY = 16 * 1024 * 1024
_FULL_WIRE_TOKEN_LIMIT = 65536


def _full_wire_models() -> dict[str, type]:
    from bidking_lab.extract.drop_table import DropEntry
    from bidking_lab.simulation.basic_mc import FlattenedPool, DropPoolResolutionPolicy
    return {cls.__name__: cls for cls in (
        BidMap, DropPool, DropEntry, Item, FlattenedPool, DropPoolResolutionPolicy,
    )}


def _full_wire_tokens(value: Any):
    """Closed type vocabulary; never invoke user serialization/reduction hooks."""
    from bidking_lab.simulation.basic_mc import (
        PolicyBoundDropPools, RankMapAlgorithmPriorBoundDropPools,
        _verified_resolution_policy,
    )
    models = _full_wire_models()

    def walk(obj: Any, depth: int = 0):
        if depth > 64:
            raise ValueError("full_input_depth")
        kind = type(obj)
        if obj is None or kind in (str, int, float, bool):
            if kind is str and len(obj) > _FULL_WIRE_TOKEN_LIMIT // 6:
                # Long payloads are transported as bounded string fragments.
                yield ["text"]
                for offset in range(0, len(obj), 8192):
                    yield ["part", obj[offset:offset + 8192]]
                yield ["end"]
            else:
                yield ["value", obj]
        elif kind in (list, tuple, set, frozenset, dict):
            yield [kind.__name__]
            iterable = obj.items() if kind is dict else obj
            for entry in iterable:
                if kind is dict:
                    yield from walk(entry[0], depth + 1)
                    yield from walk(entry[1], depth + 1)
                else:
                    yield from walk(entry, depth + 1)
            yield ["end"]
        elif kind in models.values():
            yield ["model", kind.__name__]
            yield from walk(vars(obj), depth + 1)
        elif kind is PolicyBoundDropPools:
            yield ["policy", _verified_resolution_policy(obj) is not None]
            yield from walk((obj._pools, obj.resolution_policy), depth + 1)
        elif kind is RankMapAlgorithmPriorBoundDropPools:
            yield ["rankmap"]
            yield from walk((obj._pools, obj._flattened_by_pool_id,
                             obj.package_generation, obj.authority_mode), depth + 1)
        else:
            raise ValueError("full_input_type")

    for token in walk(value):
        encoded = json.dumps(token, ensure_ascii=True, allow_nan=False,
                             separators=(",", ":")).encode("ascii") + b"\n"
        if len(encoded) > _FULL_WIRE_TOKEN_LIMIT:
            raise ValueError("full_input_token_size")
        yield encoded


def _full_wire_decode(tokens):
    """Incremental inverse; the caller receives progress and the final object."""
    from bidking_lab.simulation.basic_mc import (
        PolicyBoundDropPools, RankMapAlgorithmPriorBoundDropPools,
        _bind_verified_drop_pool_resolution_policy,
    )
    models = _full_wire_models()
    stack = []
    result = []

    def put(value):
        while stack and stack[-1][0] in ("model", "policy", "rankmap"):
            kind, argument, _ = stack.pop()
            if kind == "model":
                value = models[argument](**value)
            elif kind == "policy":
                factory = (_bind_verified_drop_pool_resolution_policy
                           if argument else PolicyBoundDropPools)
                value = factory(*value)
            else:
                value = RankMapAlgorithmPriorBoundDropPools(
                    value[0], value[1], package_generation=value[2], authority_mode=value[3])
        (stack[-1][2] if stack else result).append(value)

    for raw in tokens:
        token = json.loads(raw)
        kind = token[0]
        if kind in ("value", "part"):
            put(token[1])
        elif kind == "end":
            if not stack:
                raise ValueError("full_channel_shape")
            container, _, values = stack.pop()
            if container == "dict":
                if len(values) % 2:
                    raise ValueError("full_channel_shape")
                value = dict(zip(values[::2], values[1::2]))
            elif container == "text":
                value = "".join(values)
            else:
                value = {"list": list, "tuple": tuple, "set": set,
                         "frozenset": frozenset}[container](values)
            put(value)
        elif kind in ("dict", "list", "tuple", "set", "frozenset", "text",
                      "model", "policy", "rankmap"):
            if len(stack) >= 64:
                raise ValueError("full_channel_depth")
            stack.append((kind, token[1] if len(token) > 1 else None, []))
        else:
            raise ValueError("full_channel_type")
        yield None
    if stack or len(result) != 1:
        raise ValueError("full_channel_shape")
    yield result[0]


def _full_tables_snapshot(tables: MonitorTables, session: str | None) -> dict[str, Any]:
    if type(tables) is not MonitorTables:
        raise ValueError("full_tables_type")
    version = _fresh_session_game_version(tables, session)
    bindings = tables.warehouse_session_versions
    with bindings.lock:
        first = dict(bindings.first_by_session)
        conflicts = set(bindings.conflicted_sessions)
    return {"fields": {name: getattr(tables, name) for name in MonitorTables.__dataclass_fields__
                       if name != "warehouse_session_versions"},
            "first": first, "conflicts": conflicts,
            "session_identity": (session, version)}


def _full_tables_restore(snapshot: Mapping[str, Any]) -> MonitorTables:
    tables = MonitorTables(**snapshot["fields"], warehouse_session_versions=
                           _SessionGameVersionBindings(snapshot["first"], snapshot["conflicts"]))
    # Keep live checking enabled for all existing consumers, but the child uses
    # the parent's bound result, never independently scans Steam/version leaves.
    object.__setattr__(tables, "_full_session_identity", snapshot["session_identity"])
    return tables


def _full_mmap_tokens(channel, size: int):
    if not 0 < size <= _FULL_WIRE_CAPACITY - 8:
        raise ValueError("full_channel_size")
    position = 8
    end = 8 + size
    while position < end:
        stop = channel.find(b"\n", position, min(end, position + _FULL_WIRE_TOKEN_LIMIT + 1))
        if stop < 0:
            raise ValueError("full_channel_token_size")
        yield channel[position:stop]
        position = stop + 1


def _full_artifact_child(channel_name: str) -> None:
    """Explicit Python -c entry: never imports/replays any capture main module."""
    import mmap
    import struct
    with mmap.mmap(-1, _FULL_WIRE_CAPACITY, tagname=channel_name) as channel:
        size = struct.unpack("<Q", channel[:8])[0]
        if not 0 < size <= _FULL_WIRE_CAPACITY - 8:
            raise ValueError("full_channel_size")
        input_digest = hashlib.sha256(channel[8:8 + size]).hexdigest()
        request = None
        for request in _full_wire_decode(_full_mmap_tokens(channel, size)):
            pass
        try:
            tables = _full_tables_restore(request["tables"])
            payload = json.dumps(request["rows"], ensure_ascii=False, separators=(",", ":"))
            artifact = build_monitor_artifact_from_payload(payload, tables=tables,
                                                           **request["parameters"])
            response = {"identity": request["identity"], "input_digest": input_digest, "artifact": artifact}
        except Exception as exc:
            partial = "invalid frame length" in str(exc) or "incomplete length" in str(exc)
            reason = ("full_partial" if partial else "full_input_error"
                      if isinstance(exc, (OSError, ValueError)) else "full_compute_error")
            response = {"identity": request["identity"], "input_digest": input_digest, "error": reason}
        channel[:8] = b"\0" * 8
        offset = 8
        for token in _full_wire_tokens(response):
            if offset + len(token) > _FULL_WIRE_CAPACITY:
                raise ValueError("full_result_size")
            channel[offset:offset + len(token)] = token
            offset += len(token)
        channel[:8] = struct.pack("<Q", offset - 8)


def _auction_hall_6001_support_receipt(
    map_id: Any,
    tables: MonitorTables,
) -> dict[str, Any]:
    """Project the exact provisional identity without borrowing pool 2601."""

    if (
        type(map_id) is not int  # noqa: E721
        or map_id != 6001
        or type(tables) is not MonitorTables  # noqa: E721
        or type(tables.maps) is not dict  # noqa: E721
    ):
        return {}
    bid_map = None
    for key, value in dict.items(tables.maps):
        if type(key) is not int:  # noqa: E721 - reject key protocol hooks
            return {}
        if key == 6001:
            bid_map = value
    skill_groups = (
        bid_map.round_skill_group_ids
        if type(bid_map) is BidMap  # noqa: E721
        else None
    )
    mapping_ok = (
        type(bid_map) is BidMap  # noqa: E721
        and type(bid_map.map_id) is int  # noqa: E721
        and bid_map.map_id == 6001
        and type(bid_map.auction_mode) is str  # noqa: E721
        and str.__eq__(bid_map.auction_mode, "auction_hall")
        and type(bid_map.drop_pool_id) is int  # noqa: E721
        and bid_map.drop_pool_id == 2601
        and type(bid_map.items_per_session_min) is int  # noqa: E721
        and bid_map.items_per_session_min == 22
        and type(bid_map.items_per_session_max) is int  # noqa: E721
        and bid_map.items_per_session_max == 44
        and type(skill_groups) is list  # noqa: E721
        and len(skill_groups) == 5
        and all(type(value) is int for value in skill_groups)  # noqa: E721
        and tuple(skill_groups) == (103, 103, 103, 104, 105)
    )
    if not mapping_ok:
        status = "mapping_conflict"
    elif type(tables.auction_hall_6001_status) is not str:  # noqa: E721
        status = "sidecar_status_invalid"
    elif not str.__eq__(tables.auction_hall_6001_status, "ok"):
        status = tables.auction_hall_6001_status
    else:
        receipt = canonical_auction_hall_6001_provisional_receipt(
            tables.auction_hall_6001_receipt,
        )
        if receipt is None:
            status = "receipt_scope_mismatch"
        else:
            return receipt
    return {
        "schema_version": 1,
        "status": status,
        "map_id": 6001,
        "provisional_distribution_available": False,
        "provisional_quote_available": False,
        "distribution_authority": False,
        "official_quote_authority": False,
    }


def _raven_chaotic_space_support_receipt(
    map_id: int,
    tables: MonitorTables,
) -> dict[str, Any]:
    """Project the exact unmodeled Raven map identity without authorizing a quote."""

    if type(map_id) is not int or map_id not in RAVEN_CHAOTIC_SPACE_MAP_IDS:  # noqa: E721
        return {}
    bid_map = tables.maps.get(map_id)
    expected_pool_id = map_id - 100
    expected_sub_pool_weights = (
        list(_RAVEN_ROOT_SELECTOR_ENTRIES) if map_id == 5101 else []
    )
    mapping_ok = (
        isinstance(bid_map, BidMap)
        and bid_map.map_id == map_id
        and bid_map.category == 501
        and bid_map.drop_pool_id == expected_pool_id
        and bid_map.items_per_session_min == 22
        and bid_map.items_per_session_max == 44
        and bid_map.round_skill_group_ids == [30105, 0, 0, 0, 0]
        and bid_map.sub_pool_weights == expected_sub_pool_weights
    )
    return {
        "schema_version": 1,
        "status": "recognized_unavailable" if mapping_ok else "mapping_conflict",
        "reason": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
        "activity_id": 40002,
        "activity_map_id": 20002,
        "hero_id": 301,
        "map_group_id": 501,
        "map_id": map_id,
        "model_map_id": map_id,
        "pool_id": expected_pool_id,
        "model_authority": False,
        "quote_available": False,
        "first_live_session_status": "pending",
    }


def _raven_preopen_template_alias(
    map_id: Any,
    tables: MonitorTables,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Bind one 510x route to its 250x runtime template without exact-rate claims."""

    if (
        type(enabled) is not bool  # noqa: E721 - reject truthy protocol objects
        or not enabled
        or type(map_id) is not int  # noqa: E721
        or map_id not in RAVEN_CHAOTIC_SPACE_MAP_IDS
    ):
        return {}

    # The switch is scope-atomic: a partial 510x identity or partial 250x runtime
    # template keeps every Raven route in recognized-unavailable rather than
    # enabling only the currently observed leaf.
    for source_map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS:
        support = _raven_chaotic_space_support_receipt(source_map_id, tables)
        if support.get("status") != "recognized_unavailable":
            return {}
    for source_map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS:
        model_map_id = source_map_id - 2600
        model_map = tables.maps.get(model_map_id)
        if (
            not isinstance(model_map, BidMap)
            or model_map.map_id != model_map_id
            or _map_family_from_id(model_map_id) != "shipwreck"
            or model_map.drop_pool_id not in tables.drops
        ):
            return {}

    model_map_id = map_id - 2600
    selector_entries = [
        {
            "ordinal": ordinal,
            "map_id": selected_map_id,
            "weight": weight,
            "role": "self" if selected_map_id == 5101 else "leaf",
        }
        for ordinal, (selected_map_id, weight) in enumerate(
            _RAVEN_ROOT_SELECTOR_ENTRIES,
            start=1,
        )
    ]
    selected_entries = [
        entry for entry in selector_entries if entry["map_id"] == map_id
    ]
    return {
        "schema_version": 1,
        "status": "estimated_template",
        "mode": RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE,
        "display_label": RAVEN_TEMPLATE_ESTIMATE_LABEL,
        "source_map_id": map_id,
        "model_map_id": model_map_id,
        "family": "shipwreck",
        "confidence": "preopen_template_estimate",
        "reason": "raven_preopen_use_corresponding_250x_runtime_template",
        "estimated": True,
        "observed_raven_games": 0,
        "pool_graph_claim": False,
        "exact_rate_claim": False,
        "source_generation": CURRENT_PACKAGE_GENERATION,
        "model_input_mode": "current_runtime_weights_priors_alias",
        "direct_pool_proxy_mode": "shadow_probe_only",
        "direct_pool_proxy_quote_authority": False,
        "selector": {
            "root_map_id": 5101,
            "selected_map_id": map_id,
            "selected_roles": (
                ["root", "self"] if map_id == 5101 else ["leaf"]
            ),
            "selected_entry_count": len(selected_entries),
            "selected_raw_weight": sum(
                int(entry["weight"]) for entry in selected_entries
            ),
            "raw_total_weight": sum(
                weight for _selected_map_id, weight in _RAVEN_ROOT_SELECTOR_ENTRIES
            ),
            "raw_entries": selector_entries,
        },
    }


_UPDATE606_LEGACY_UNKNOWN_LEAF_POLICY = (
    "update606_legacy_drop_known_support_v1"
)
_UPDATE606_LEGACY_UNKNOWN_LEAVES = frozenset({(1066, 1106013)})


def _load_package_plain_bytes(path: Path) -> bytes:
    """Read plaintext identity through the same BKENC-aware runtime boundary."""

    return load_text(path, encoding="utf-8").encode("utf-8")


def _exact_input(raw: bytes, identity: Mapping[str, Any]) -> bool:
    return (
        len(raw) == identity.get("bytes")
        and hashlib.sha256(raw).hexdigest() == identity.get("sha256")
    )


def _bind_update606_legacy_drop_policy(
    root: Path,
    drops: Mapping[int, DropPool],
) -> Mapping[int, DropPool]:
    """Bind the one approved legacy leaf gap to exact update606 inputs.

    The approved map roots come from the immutable, generation-filtered
    ``drop_weights`` output rather than from raw Drop reachability.  Missing or
    mixed inputs therefore leave the runtime in its strict fail-closed mode.
    """

    weights_contract = LEGACY_MAP_PAYLOADS["drop_weights_all_maps_0627"]
    weights_path = root.parent.parent / "processed" / "drop_weights_all_maps_0627.json"
    try:
        bid_map_raw = _load_package_plain_bytes(root / "BidMap.txt")
        item_raw = _load_package_plain_bytes(root / "Item.txt")
        drop_raw = _load_package_plain_bytes(root / "Drop.txt")
        weights_raw = _load_package_plain_bytes(weights_path)
    except (OSError, UnicodeError, ValueError):
        return drops
    package_generation = None
    if _exact_input(bid_map_raw, UPDATE606_PACKAGE_BID_MAP) and _exact_input(
        item_raw,
        UPDATE606_PACKAGE_ITEM,
    ):
        package_generation = BID_MAP_UPDATE606_GENERATION
    elif _exact_input(bid_map_raw, CURRENT_PACKAGE_BID_MAP) and _exact_input(
        item_raw,
        CURRENT_PACKAGE_ITEM,
    ):
        package_generation = CURRENT_PACKAGE_GENERATION
    if (
        package_generation is None
        or not _exact_input(drop_raw, LEGACY_PACKAGE_DROP)
        or len(weights_raw) != weights_contract["output_bytes"]
        or hashlib.sha256(weights_raw).hexdigest()
        != weights_contract["output_sha256"]
    ):
        return drops
    try:
        weights = json.loads(weights_raw)
    except json.JSONDecodeError:
        return drops
    if weights.__class__ is not dict or len(weights) != weights_contract["output_maps"]:
        return drops
    allowed_roots: set[int] = set()
    for raw_map_id, entry in weights.items():
        if (
            raw_map_id.__class__ is not str
            or not raw_map_id.isascii()
            or not raw_map_id.isdigit()
            or entry.__class__ is not dict
            or entry.get("items").__class__ is not dict
            or "1106013" in entry["items"]
        ):
            return drops
        allowed_roots.add(int(raw_map_id))
    if len(allowed_roots) != weights_contract["output_maps"]:
        return drops
    return _bind_verified_drop_pool_resolution_policy(
        drops,
        DropPoolResolutionPolicy(
            policy_id=_UPDATE606_LEGACY_UNKNOWN_LEAF_POLICY,
            package_generation=package_generation,
            allowed_root_pool_ids=frozenset(allowed_roots),
            allowed_unknown_leaves=_UPDATE606_LEGACY_UNKNOWN_LEAVES,
        ),
    )


def load_monitor_tables(
    *,
    tables_dir: str | Path | None = None,
) -> MonitorTables:
    """Load raw game tables from ``data/raw/tables`` or an explicit folder.

    ``load_item_table`` now returns the COMPLETE catalog (raw Item.txt + items.json's newer
    collectibles merged at the single loader — see extract.item_table._supplement_from_catalog),
    so the monitor minimap + settlement value see every item including the 75 added 2026-06-25."""
    root = Path(tables_dir) if tables_dir is not None else (
        project_root() / "data" / "raw" / "tables"
    )
    try:
        warehouse_package_generation = (
            warehouse_package_generation_from_exact_inputs(
                _load_package_plain_bytes(root / "BidMap.txt"),
                _load_package_plain_bytes(root / "Item.txt"),
            )
        )
    except (OSError, UnicodeError, ValueError):
        warehouse_package_generation = "unknown_warehouse_generation"
    warehouse_game_version_path = ""
    try:
        from bidking_lab.live.version_check import (
            detect_game_version_strict_disk_snapshot,
        )

        game_version_snapshot = detect_game_version_strict_disk_snapshot()
        warehouse_game_version = (
            game_version_snapshot.value
            if game_version_snapshot is not None
            else ""
        )
        warehouse_game_version_path = (
            str(game_version_snapshot.path)
            if game_version_snapshot is not None
            else ""
        )
    except Exception:  # noqa: BLE001 - source metadata must not stop monitor startup
        warehouse_game_version = ""
    maps = load_bid_map_table(root / "BidMap.txt")
    drops = load_drop_table(root / "Drop.txt")
    items = load_item_table(root / "Item.txt")
    drops = _bind_update606_legacy_drop_policy(root, drops)
    drops, rankmap_status, rankmap_receipt = bind_current_rankmap_algorithm_prior(
        package_root=root.parents[2],
        maps=maps,
        drops=drops,
        items=items,
    )
    auction_status, auction_receipt = (
        load_auction_hall_6001_provisional_reference(
            package_root=root.parents[2],
        )
    )
    return MonitorTables(
        maps=maps,
        drops=drops,
        items=items,
        rankmap_distribution_status=rankmap_status,
        rankmap_distribution_receipt=rankmap_receipt,
        auction_hall_6001_status=auction_status,
        auction_hall_6001_receipt=auction_receipt,
        warehouse_package_generation=warehouse_package_generation,
        warehouse_game_version=warehouse_game_version,
        warehouse_game_version_path=warehouse_game_version_path,
        warehouse_live_identity_check=True,
    )


def _fresh_session_game_version(
    tables: MonitorTables,
    session_id: str | None,
) -> tuple[str, bool]:
    """Read the selected Steam version leaf and bind it to one session.

    Manually constructed/offline ``MonitorTables`` retain their explicit static
    identity.  Real tables loaded by ``load_monitor_tables`` always recheck the
    disk leaf; a missing read or a mid-session generation change is sticky for
    that session and cannot borrow the startup generation.
    """

    bound = getattr(tables, "_full_session_identity", None)
    if bound is not None:
        return bound[1] if session_id == bound[0] else ("", False)
    if not tables.warehouse_live_identity_check:
        return tables.warehouse_game_version, True
    try:
        from bidking_lab.live.version_check import (
            detect_game_version_strict_disk_snapshot,
        )

        snapshot = detect_game_version_strict_disk_snapshot(
            tables.warehouse_game_version_path or None
        )
        fresh_version = snapshot.value if snapshot is not None else ""
    except Exception:  # noqa: BLE001 - identity uncertainty is fail-closed metadata
        fresh_version = ""
    return tables.warehouse_session_versions.resolve(session_id, fresh_version)


def _drop_pool_support_receipt(
    map_id: int,
    tables: MonitorTables,
) -> dict[str, Any]:
    """Return a support-safe receipt only when inference is conditional."""

    bid_map = tables.maps[map_id]
    weighted: list[tuple[float, Any]] = []
    if not bid_map.sub_pool_weights:
        weighted.append(
            (
                1.0,
                flatten_pool(bid_map.drop_pool_id, tables.drops, tables.items, map_id=bid_map.map_id),
            )
        )
    else:
        positive = [
            (sub_map_id, weight)
            for sub_map_id, weight in bid_map.sub_pool_weights
            if weight > 0 and sub_map_id in tables.maps
        ]
        total_weight = sum(weight for _, weight in positive)
        for sub_map_id, weight in positive:
            sub_map = tables.maps[sub_map_id]
            weighted.append(
                (
                    float(weight) / float(total_weight),
                    flatten_pool(
                        sub_map.drop_pool_id,
                        tables.drops,
                        tables.items,
                        map_id=sub_map.map_id,
                    ),
                )
            )
    degraded = [fp for _, fp in weighted if fp.resolution_status != "complete"]
    if not degraded:
        return {}
    policies = {fp.resolution_policy for fp in degraded}
    generations = {fp.resolution_generation for fp in degraded}
    if len(policies) != 1 or len(generations) != 1:
        raise ValueError("mixed drop resolution policy")
    unresolved_probability = sum(
        weight * float(fp.unresolved_probability) for weight, fp in weighted
    )
    return {
        "schema_version": 1,
        "status": "legacy_incomplete_conditional_known_support",
        "reason": "legacy_unknown_leaf",
        "policy": next(iter(policies)),
        "package_generation": next(iter(generations)),
        "unresolved_probability": round(unresolved_probability, 12),
        "unresolved_probability_ppm": int(round(unresolved_probability * 1_000_000)),
        "unresolved_leaf_count": sum(fp.unresolved_leaf_count for fp in degraded),
        "modeled_probability_normalized": True,
        "quote_quality": "degraded",
    }


def _format_quantile_interval(summary: Any) -> str:
    if summary is None:
        return ""
    return f"{summary.p10:,.0f} / {summary.p50:,.0f} / {summary.p90:,.0f}"


def _summary_p50_int(summary: Any) -> int | None:
    """P50 of a quantile summary as a plain int (对局记录的「估价」平铺数值)。

    估价口径 = 原始价值 P50(report.total_value 的中位, 与结算真值 apples-to-apples),
    取最终应用 formal mode(v3 practical 优先, 否则 v2)的 raw value summary。
    """
    p50 = getattr(summary, "p50", None)
    if p50 is None:
        return None
    try:
        return int(round(float(p50)))
    except (TypeError, ValueError):
        return None


def _format_quantile_interval_float(summary: Any, digits: int = 2) -> str:
    if summary is None:
        return ""
    return (
        f"{summary.p10:.{digits}f} / "
        f"{summary.p50:.{digits}f} / "
        f"{summary.p90:.{digits}f}"
    )


def _format_quantile_width(summary: Any) -> str:
    if summary is None:
        return ""
    return f"{summary.p90 - summary.p10:,.0f}"


def _raw_ceiling_risk_label(decision_summary: Any, raw_summary: Any) -> str:
    if decision_summary is None or raw_summary is None:
        return ""
    decision_p90 = getattr(decision_summary, "p90", None)
    raw_p90 = getattr(raw_summary, "p90", None)
    if decision_p90 is None or raw_p90 is None:
        return ""
    gap = int(round(raw_p90 - decision_p90))
    if gap <= 0:
        return "低"
    baseline = max(float(decision_p90), 1.0)
    ratio = gap / baseline
    if gap >= 700_000 or ratio >= 1.0:
        level = "高"
    elif gap >= 250_000 or ratio >= 0.45:
        level = "中"
    else:
        level = "低"
    return f"{level} / raw P90 +{gap:,.0f}"


def _candidate_map_ids_for_likelihood(
    map_id: int,
    maps: Mapping[int, BidMap],
) -> tuple[int, ...]:
    if map_id in maps:
        return (map_id,)
    return (map_id,)


def _activity_shipwreck_alias(
    map_id: Any,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
) -> dict[str, Any]:
    try:
        source_map_id = int(map_id)
    except (TypeError, ValueError):
        return {}
    source_map = maps.get(source_map_id)
    if source_map is not None and source_map.drop_pool_id in drops:
        return {}
    candidates: list[tuple[str, int]] = []
    if 2521 <= source_map_id <= 2530:
        candidates.append(("activity_shipwreck_minus20", source_map_id - 20))
        candidates.append(("activity_shipwreck_minus10", source_map_id - 10))
    elif 2531 <= source_map_id <= 2540:
        # New activity sealed-cabin (postdates the 06-11 tables). Only 2501-2510 carry a
        # configured base map, so -30 lands there; -20/-10 kept as fallbacks if a later
        # table ships the intermediate blocks.
        candidates.append(("activity_shipwreck_minus30", source_map_id - 30))
        candidates.append(("activity_shipwreck_minus20", source_map_id - 20))
        candidates.append(("activity_shipwreck_minus10", source_map_id - 10))
    elif 2541 <= source_map_id <= 2550:
        # 2026-07-16 red-crate activity: the ten sealed-cabin maps are another positional
        # copy of 2501-2510. This alias only borrows the base map/drop structure and preserves
        # source_map_id; the reference engine applies the independently calibrated red-crate
        # q6 prior from that source id (never the older X511-X520 factor).
        candidates.append(("red_crate_shipwreck_minus40", source_map_id - 40))
        candidates.append(("red_crate_shipwreck_minus30", source_map_id - 30))
        candidates.append(("red_crate_shipwreck_minus20", source_map_id - 20))
    elif 4521 <= source_map_id <= 4530:
        candidates.append(("activity_shipwreck_45xx_minus20", source_map_id - 20))
        candidates.append(("activity_shipwreck_45xx_minus10", source_map_id - 10))
    elif 4531 <= source_map_id <= 4540:
        # New activity deep-sea blind auction (e.g. 4532). Same shape as 25xx: -30 lands
        # in the 4501-4510 base which carries drops in the game tables.
        candidates.append(("activity_shipwreck_45xx_minus30", source_map_id - 30))
        candidates.append(("activity_shipwreck_45xx_minus20", source_map_id - 20))
        candidates.append(("activity_shipwreck_45xx_minus10", source_map_id - 10))
    elif 4541 <= source_map_id <= 4550:
        # Blind-auction sibling of the 2026-07-16 red-crate block. Keep the source id in
        # artifacts while borrowing only the corresponding 4501-4510 base map.
        candidates.append(("red_crate_shipwreck_45xx_minus40", source_map_id - 40))
        candidates.append(("red_crate_shipwreck_45xx_minus30", source_map_id - 30))
        candidates.append(("red_crate_shipwreck_45xx_minus20", source_map_id - 20))
    for mode, model_map_id in candidates:
        if model_map_id in maps and _map_family_from_id(model_map_id) == "shipwreck":
            return {
                "mode": mode,
                "source_map_id": source_map_id,
                "model_map_id": model_map_id,
                "family": "shipwreck",
                "confidence": "temporary_activity_alias",
                "reason": (
                    "missing_activity_drop_use_corresponding_old_shipwreck"
                    if source_map is not None
                    else "missing_activity_bidmap_use_corresponding_old_shipwreck"
                ),
                "candidate_model_map_ids": [
                    candidate
                    for _candidate_mode, candidate in candidates
                    if candidate in maps
                ],
            }
    return {}


def _relax_exact_bucket_constraints(obs: Any) -> Any:
    buckets = {}
    changed = False
    for quality, bucket in obs.buckets.items():
        total_cells_min = bucket.total_cells_min
        count_min = bucket.count_min
        if bucket.total_cells is not None:
            total_cells_min = max(total_cells_min or 0, bucket.total_cells)
            changed = True
        if bucket.count is not None:
            count_min = max(count_min or 0, bucket.count)
            changed = True
        buckets[quality] = replace(
            bucket,
            total_cells=None,
            count=None,
            total_cells_min=total_cells_min,
            count_min=count_min,
        )
    if not changed:
        return obs
    return replace(obs, buckets=buckets)


def _zero_match_fallback_session(obs: Any) -> Any:
    buckets = {}
    for quality, bucket in obs.buckets.items():
        if quality == 6 and (bucket.total_cells == 0 or bucket.count == 0):
            buckets[quality] = replace(
                bucket,
                total_cells=0,
                count=0,
                total_cells_min=0,
                count_min=0,
                value_sum=None,
                avg_value=None,
                value_range=None,
                huge_band="none",
                huge_cells_override=0,
            )
    return replace(
        obs,
        warehouse_total_cells=None,
        warehouse_total_cells_approx=None,
        warehouse_total_cells_tolerance=None,
        total_item_count=None,
        buckets=buckets,
        visible_outline_item_count_min=None,
        visible_outline_total_cells_min=None,
        visible_outline_bottom_row_min=None,
        category_items=(),
    )


def _map_likelihood_result_rows(
    results: Sequence[Any],
    label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results[:5]:
        rows.append(
            {
                "证据": label,
                "地图": f"{result.map_id} {result.map_name}",
                "匹配": f"{result.n_matched}/{result.n_total}",
                "后验": f"{result.posterior_probability:.1%}",
                "总格 P10/P50/P90": _format_quantile_interval(result.total_cells),
                "价值 P10/P50/P90": _format_quantile_interval(result.total_value),
            }
        )
    return rows


def _warehouse_estimate_rows(estimate: Any) -> list[dict[str, Any]]:
    if estimate is None:
        return []
    rows = [
        {
            "范围": "跨候选地图汇总",
            "匹配": f"{estimate.n_matched}/{estimate.n_total}",
            "置信度": estimate.confidence,
            "总格 P10/P50/P90": _format_quantile_interval(estimate.total_cells),
            "价值 P10/P50/P90": _format_quantile_interval(estimate.total_value),
            "说明": estimate.reason,
        }
    ]
    for row in estimate.map_contributions[:5]:
        rows.append(
            {
                "范围": f"{row.map_id} {row.map_name}",
                "匹配": f"{row.n_matched}/{row.n_total}",
                "置信度": f"地图后验 {row.posterior_probability:.1%}",
                "总格 P10/P50/P90": _format_quantile_interval(row.total_cells),
                "价值 P10/P50/P90": "",
                "说明": "",
            }
        )
    return rows


def _v2_posterior_rows(
    report: Any,
    *,
    q6_prior_gap: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if report is None:
        return []
    diagnostics = ";".join(getattr(report, "diagnostics", ()) or ())
    if q6_prior_gap is None:
        q6_prior_gap = _q6_prior_gap_summary(report)
    return [
        {
            "范围": f"{report.map_id} {report.map_name}",
            "匹配": f"{report.n_matched}/{report.n_total}",
            "价值口径": "decision_value",
            "决策价值 P10/P50/P90": _format_quantile_interval(report.decision_value),
            "替代决策价值 P10/P50/P90": _format_quantile_interval(
                getattr(report, "tail_replacement_decision_value", None)
            ),
            "原始价值 P10/P50/P90": _format_quantile_interval(report.total_value),
            "先验件数": (
                f"{report.prior_expected_count:.2f}"
                if getattr(report, "prior_expected_count", None) is not None
                else ""
            ),
            "先验格数": (
                f"{report.prior_expected_cells:.1f}"
                if getattr(report, "prior_expected_cells", None) is not None
                else ""
            ),
            "先验原始价值": (
                f"{report.prior_expected_value:,.0f}"
                if getattr(report, "prior_expected_value", None) is not None
                else ""
            ),
            "先验决策价值": (
                f"{report.prior_expected_decision_value:,.0f}"
                if getattr(report, "prior_expected_decision_value", None) is not None
                else ""
            ),
            "先验替代决策价值": (
                f"{report.prior_expected_tail_replacement_decision_value:,.0f}"
                if getattr(
                    report,
                    "prior_expected_tail_replacement_decision_value",
                    None,
                )
                is not None
                else ""
            ),
            "q6价值 P10/P50/P90": _format_quantile_interval(report.q6_value),
            "q6决策价值 P10/P50/P90": _format_quantile_interval(
                getattr(report, "q6_decision_value", None)
            ),
            "q6替代决策价值 P10/P50/P90": _format_quantile_interval(
                getattr(report, "q6_tail_replacement_decision_value", None)
            ),
            "q6件数 P10/P50/P90": _format_quantile_interval(
                getattr(report, "q6_count", None)
            ),
            "q6格数 P10/P50/P90": _format_quantile_interval(
                getattr(report, "q6_cells", None)
            ),
            "剩余空间 P10/P50/P90": _format_quantile_interval(
                getattr(report, "remaining_cells_after_layout", None)
            ),
            "q6空间压力 P10/P50/P90": _format_quantile_interval_float(
                getattr(report, "q6_space_pressure", None)
            ),
            "q6空间溢出率": (
                f"{report.q6_space_overflow_rate:.1%}"
                if getattr(report, "q6_space_overflow_rate", None) is not None
                else ""
            ),
            "q6样本率": (
                f"{report.q6_match_rate:.1%}"
                if report.q6_match_rate is not None
                else ""
            ),
            "q6掉落先验": (
                f"{report.q6_prior_match_rate:.1%}"
                if report.q6_prior_match_rate is not None
                else ""
            ),
            "q6先验件数": (
                f"{report.q6_prior_expected_count:.2f}"
                if getattr(report, "q6_prior_expected_count", None) is not None
                else ""
            ),
            "q6先验格数": (
                f"{report.q6_prior_expected_cells:.1f}"
                if getattr(report, "q6_prior_expected_cells", None) is not None
                else ""
            ),
            "q6先验价值": (
                f"{report.q6_prior_expected_value:,.0f}"
                if report.q6_prior_expected_value is not None
                else ""
            ),
            "q6先验缺口": q6_prior_gap["summary"],
            "q6先验风险参考": (
                f"{q6_prior_gap['floor_value']:,.0f}"
                if q6_prior_gap["floor_value"] is not None
                else ""
            ),
            "q6先验风险": "是" if q6_prior_gap["risk"] else "",
            "q6实战门控": q6_prior_gap["gate"],
            "q6实战参考P90": (
                f"{q6_prior_gap['practical_p90']:,.0f}"
                if q6_prior_gap["practical_p90"] is not None
                else ""
            ),
            "锚点数": getattr(report, "anchor_count", 0),
            "形状约束数": getattr(report, "shape_target_count", 0),
            "分类约束数": getattr(report, "category_target_count", 0),
            "分类反排数": getattr(report, "category_exclusion_count", 0),
            "随机样本均价": ";".join(
                f"n={sample_count}:avg={value:.2f}"
                for sample_count, value in getattr(
                    report,
                    "random_sample_avg_values",
                    (),
                )
            ),
            "随机样本均价信号": ";".join(
                f"n={sample_count}:avg={value:.2f}"
                for sample_count, value in actionable_random_sample_avg_values(
                    getattr(report, "random_sample_avg_values", ())
                )
            ),
            "诊断": diagnostics,
        }
    ]


def _q6_prior_gap_summary(report: Any) -> dict[str, Any]:
    parts: list[str] = []
    count_gap = _quantile_prior_gap(
        getattr(report, "q6_count", None),
        getattr(report, "q6_prior_expected_count", None),
    )
    cells_gap = _quantile_prior_gap(
        getattr(report, "q6_cells", None),
        getattr(report, "q6_prior_expected_cells", None),
    )
    if count_gap is not None and count_gap >= 0.25:
        parts.append(f"件数P90低{count_gap:.2f}")
    if cells_gap is not None and cells_gap >= 1.0:
        parts.append(f"格数P90低{cells_gap:.1f}")
    prior_gap_active = bool(parts)
    random_sample_signals = actionable_random_sample_avg_values(
        getattr(report, "random_sample_avg_values", ())
    )
    random_sample_floor = max(
        (
            int(round(float(sample_count) * float(value)))
            for sample_count, value in random_sample_signals
        ),
        default=None,
    )
    for sample_count, value in random_sample_signals:
        parts.append(f"随机{sample_count}件均价高{float(value):,.0f}")
    prior_floor = (
        getattr(report, "q6_prior_expected_value", None)
        if prior_gap_active
        else None
    )
    floor_candidates = [
        float(value)
        for value in (prior_floor, random_sample_floor)
        if value is not None
    ]
    floor_value = max(floor_candidates) if floor_candidates else None
    gate_parts: list[str] = []
    if prior_gap_active and _map_family_from_id(getattr(report, "map_id", None)) == "shipwreck":
        gate_parts.append("shipwreck_positive_net")
    if random_sample_signals:
        gate_parts.append("random_avg_signal")
    gate = "+".join(gate_parts)
    decision_p90 = _quantile_p90(getattr(report, "q6_decision_value", None))
    return {
        "risk": bool(parts),
        "summary": "；".join(parts),
        "floor_value": floor_value,
        "gate": gate,
        "practical_p90": (
            max(float(decision_p90 or 0), float(floor_value or 0))
            if floor_value is not None
            else None
        ),
    }


def _q6_risk_reference_text(q6_prior_gap: Mapping[str, Any] | None) -> str:
    if not q6_prior_gap or not q6_prior_gap.get("risk"):
        return ""
    parts = [str(q6_prior_gap.get("summary") or "").strip()]
    reference = q6_prior_gap.get("practical_p90")
    if reference is None:
        reference = q6_prior_gap.get("floor_value")
    if reference is not None:
        parts.append(f"参考P90 {float(reference):,.0f}")
    gate = str(q6_prior_gap.get("gate") or "").strip()
    if gate:
        parts.append(f"门控 {gate}")
    parts.append("q6单项风险只作参考；正式停止价由当前 formal_mode 统一重算")
    return "；".join(part for part in parts if part)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _q6_reference_with_shadow(
    q6_prior_gap: Mapping[str, Any],
    *shadows: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(q6_prior_gap)
    summary_parts = [
        part
        for part in str(merged.get("summary") or "").split("；")
        if part.strip()
    ]
    gate_parts = [
        part
        for part in str(merged.get("gate") or "").split("+")
        if part.strip()
    ]
    practical_values = [
        value
        for value in (
            _float_or_none(merged.get("practical_p90")),
            _float_or_none(merged.get("floor_value")),
        )
        if value is not None
    ]
    for shadow in shadows:
        if not shadow or not shadow.get("active"):
            continue
        q6_p90 = _float_or_none(shadow.get("q6_decision_value_p90"))
        if q6_p90 is None or q6_p90 <= 0:
            continue
        label = str(shadow.get("label") or "").strip()
        gate = str(shadow.get("gate") or "").strip()
        summary = f"{label or gate or 'shadow'} q6P90 {q6_p90:,.0f}"
        summary_parts.append(summary)
        gate_parts.append(gate or label or "shadow")
        practical_values.append(q6_p90)
    merged["summary"] = "；".join(dict.fromkeys(summary_parts))
    merged["gate"] = "+".join(dict.fromkeys(gate_parts))
    if practical_values:
        merged["practical_p90"] = max(practical_values)
    merged["risk"] = bool(merged.get("risk") or practical_values)
    return merged


def _quantile_value(summary: Any, name: str) -> int | None:
    if summary is None:
        return None
    value = getattr(summary, name, None)
    if value is None:
        return None
    return int(round(float(value)))


def _q6_residual_boost_shadow_summary(
    report: Any | None,
    *,
    label: str,
    requested_boost: float,
    active_boost: float,
    gate: str,
    evidence_profile_key: str,
    trials: int,
    requested_prior_floor_ratio: float = 0.0,
    active_prior_floor_ratio: float = 0.0,
    requested_conditional_target_count: float = 0.0,
    active_conditional_target_count: float = 0.0,
    requested_conditional_target_cells: float = 0.0,
    active_conditional_target_cells: float = 0.0,
    requested_conditional_value_power: float = 0.0,
    active_conditional_value_power: float = 0.0,
) -> dict[str, Any]:
    active_conditional_target = (
        active_conditional_target_count > 0.0
        or active_conditional_target_cells > 0.0
    )
    return {
        "label": label,
        "gate": gate,
        "requested_boost": requested_boost,
        "active_boost": active_boost,
        "requested_prior_floor_ratio": requested_prior_floor_ratio,
        "active_prior_floor_ratio": active_prior_floor_ratio,
        "requested_conditional_target_count": requested_conditional_target_count,
        "active_conditional_target_count": active_conditional_target_count,
        "requested_conditional_target_cells": requested_conditional_target_cells,
        "active_conditional_target_cells": active_conditional_target_cells,
        "requested_conditional_value_power": requested_conditional_value_power,
        "active_conditional_value_power": active_conditional_value_power,
        "active": (
            active_boost > 1.0
            or active_prior_floor_ratio > 0.0
            or active_conditional_target
        ),
        "trials": trials,
        "evidence_profile_key": evidence_profile_key,
        "n_matched": getattr(report, "n_matched", None),
        "n_total": getattr(report, "n_total", None),
        "decision_value_p50": _quantile_value(
            getattr(report, "decision_value", None),
            "p50",
        ),
        "decision_value_p90": _quantile_value(
            getattr(report, "decision_value", None),
            "p90",
        ),
        "q6_decision_value_p90": _quantile_value(
            getattr(report, "q6_decision_value", None),
            "p90",
        ),
        "q6_count_p90": _quantile_value(getattr(report, "q6_count", None), "p90"),
        "q6_cells_p90": _quantile_value(getattr(report, "q6_cells", None), "p90"),
        "diagnostics": ";".join(getattr(report, "diagnostics", ()) or ()),
    }


def _q6_residual_shadow_rows(
    shadows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shadow in shadows:
        if not shadow:
            continue
        rows.append(
            {
                "策略": shadow.get("label") or "",
                "门控": shadow.get("gate") or "",
                "激活": "是" if shadow.get("active") else "",
                "boost": shadow.get("active_boost"),
                "prior floor": shadow.get("active_prior_floor_ratio"),
                "target count": shadow.get("active_conditional_target_count"),
                "target cells": shadow.get("active_conditional_target_cells"),
                "value power": shadow.get("active_conditional_value_power"),
                "trials": shadow.get("trials"),
                "证据profile": shadow.get("evidence_profile_key") or "",
                "匹配": (
                    f"{shadow.get('n_matched')}/{shadow.get('n_total')}"
                    if shadow.get("n_total") is not None
                    else ""
                ),
                "q6决策P90": shadow.get("q6_decision_value_p90"),
                "q6件数P90": shadow.get("q6_count_p90"),
                "q6格数P90": shadow.get("q6_cells_p90"),
            }
        )
    return rows


def _map_family_from_id(map_id: Any) -> str:
    try:
        mid = int(map_id)
    except (TypeError, ValueError):
        return "unknown"
    prefix = mid // 100
    if mid == 2601:
        return "hidden"
    if prefix in {24, 34, 44}:
        return "villa"
    if prefix in {25, 35, 45}:
        return "shipwreck"
    return f"map_{prefix}xx"


def _is_activity_shipwreck_map(map_id: Any) -> bool:
    """Activity-exclusive shipwreck map numbers X511-X520 (2511-2520 / 4511-4520). Census 2026-07-06:
    these ids have ZERO pre-2026-07-02 samples (the activity introduced them) and settled red rate
    12.1% vs normal X501-X510 wreckage 7.3%. Canonical client-side classifier — mirror of the engine's
    ahmad_ref_engine._is_activity_shipwreck_map. X501-X510 (normal) / X531+ (old) are NOT activity."""
    if _map_family_from_id(map_id) != "shipwreck":
        return False
    lo = int(map_id) % 100
    return 11 <= lo <= 20


def _evidence_stage(round_no: Any) -> str:
    try:
        value = int(round_no)
    except (TypeError, ValueError):
        return "unknown"
    if value <= 2:
        return "early_1_2"
    if value <= 4:
        return "mid_3_4"
    return "full_5"


def _live_information_density_score(
    round_no: Any,
    *,
    anchor_count: Any,
    shape_target_count: Any,
    category_target_count: Any,
    category_exclusion_count: Any,
) -> int:
    try:
        round_value = int(round_no)
    except (TypeError, ValueError):
        round_value = 0
    evidence_count = sum(
        _safe_int(value)
        for value in (
            anchor_count,
            shape_target_count,
            category_target_count,
            category_exclusion_count,
        )
    )
    return round_value * 2 + min(evidence_count, 6) * 2


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _information_density_band(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score < 18:
        return "low"
    if score < 34:
        return "medium"
    return "high"


def _public_constraint_key(diagnostics: str) -> str:
    parts: list[str] = []
    if "public_max_quality:" in diagnostics:
        parts.append("max_quality")
    if "public_max_item_cells:" in diagnostics:
        parts.append("max_item_cells")
    return "+".join(parts) if parts else "none"


def _diagnostic_int(diagnostics: str, name: str) -> int | None:
    match = re.search(rf"(?:^|;){re.escape(name)}:(-?\d+)", diagnostics)
    if not match:
        return None
    return int(match.group(1))


def _zero_q6_proven_from_diagnostics(diagnostics: str) -> bool:
    max_quality = _diagnostic_int(diagnostics, "public_max_quality")
    return max_quality is not None and max_quality < 6


def _live_evidence_profile_key(
    *,
    public_constraint_key: str,
    random_sample_avg_values: str,
    category_target_count: Any,
    category_exclusion_count: Any,
    shape_target_count: Any,
) -> str:
    parts: list[str] = []
    if public_constraint_key != "none":
        parts.append(f"public:{public_constraint_key}")
    if random_sample_avg_values:
        parts.append("public:random_avg")
    if _safe_int(category_target_count) + _safe_int(category_exclusion_count) > 0:
        parts.append("tool:category")
    if _safe_int(shape_target_count) > 0:
        parts.append("shape")
    return "+".join(parts) if parts else "basic"


def _quantile_p90(quantile: Any) -> float | None:
    if quantile is None:
        return None
    p90 = getattr(quantile, "p90", None)
    if p90 is None:
        return None
    return float(p90)


def _quantile_prior_gap(quantile: Any, prior: Any) -> float | None:
    if quantile is None or prior is None:
        return None
    p90 = getattr(quantile, "p90", None)
    if p90 is None:
        return None
    return max(0.0, float(prior) - float(p90))


def _tool_info_roi_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:8]:
        out.append(
            {
                "道具": row.tool_name,
                "价格": f"{row.silver_cost:,}",
                "匹配样本": row.n_matched,
                "价值区间压缩": f"{row.value_width_gain:,.0f}",
                "仓储区间压缩": f"{row.cells_width_gain:,.0f}",
                "信息ROI": f"{row.roi_value:.2f}",
                "说明": row.note,
            }
        )
    return out


def _brief_layout_stage_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        estimate = row.get("样本拟合估计") or row.get("布局估计", "")
        confidence = row.get("样本拟合置信") or row.get("估计置信", "")
        out.append(
            {
                "阶段": f"R{row.get('R') or '?'} / sort {row.get('sort')}",
                "已知格": row.get("已知格", ""),
                "覆盖": row.get("已知覆盖", ""),
                "最深行": row.get("最深行", ""),
                "布局估计": estimate,
                "置信": confidence,
                "风险": row.get("风险", ""),
            }
        )
    return out


def _states_to_session(
    batches: Sequence[LiveObservationBatch],
) -> tuple[Any | None, Any | None, LiveSessionState, LiveSessionState]:
    final_state = LiveSessionState()
    pre_settlement_state = LiveSessionState()
    saw_pre_settlement = False
    for batch in batches:
        final_state = apply_observation_batch(final_state, batch)
        if batch.phase != "settled":
            pre_settlement_state = apply_observation_batch(
                pre_settlement_state,
                batch,
            )
            saw_pre_settlement = True
    base_session = (
        live_state_to_session_obs(pre_settlement_state)
        if saw_pre_settlement
        else None
    )
    if base_session is None:
        base_session = live_state_to_session_obs(final_state)
    return (
        base_session,
        live_state_to_session_obs(final_state),
        pre_settlement_state,
        final_state,
    )


def _trusted_pre_settlement_session_totals(
    state: LiveSessionState,
) -> dict[str, dict[str, Any]]:
    constraints: dict[str, dict[str, Any]] = {}
    for field in _SAFE_SESSION_TOTAL_FIELDS:
        observed = state.fields.get(("session", field))
        if observed is None:
            continue
        if observed.confidence not in _TRUSTED_SESSION_TOTAL_CONFIDENCES:
            continue
        value = _parse_int_text(observed.value)
        if value is None:
            continue
        constraints[field] = {
            "value": value,
            "source": observed.source,
            "confidence": observed.confidence,
            "sequence": observed.sequence,
        }
    return constraints


def _inference_session_with_safe_totals(
    base_session: Any,
    pre_settlement_state: LiveSessionState,
) -> tuple[Any, dict[str, Any]]:
    constraints = _trusted_pre_settlement_session_totals(pre_settlement_state)
    session = replace(
        base_session,
        warehouse_total_cells=(
            constraints.get("warehouse_total_cells", {}).get("value")
        ),
        warehouse_total_cells_approx=(
            constraints.get("warehouse_total_cells_approx", {}).get("value")
        ),
        warehouse_total_cells_tolerance=(
            constraints.get("warehouse_total_cells_tolerance", {}).get("value")
        ),
        total_item_count=constraints.get("total_item_count", {}).get("value"),
    )
    return (
        session,
        {
            "mode": (
                "pre_settlement_trusted_totals"
                if constraints
                else "session_totals_stripped"
            ),
            **constraints,
        },
    )


def _inventory_value(events: FatbeansCaptureEvents, items: Mapping[int, Item]) -> int | None:
    return summarize_catalog_values(
        _settlement_inventory_items(events),
        items,
    ).known_value_sum


def _latest_round(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.round_no is not None:
            return state.round_no
    return None


def _latest_state(events: FatbeansCaptureEvents) -> Any:
    return events.states[-1] if events.states else None


def _latest_phase(events: FatbeansCaptureEvents) -> str:
    state = _latest_state(events)
    if state is None:
        return "unknown"
    if state.message_id == 0x002D or state.inventory_items:
        return "settled"
    if state.message_id == 0x0021:
        return "reading"
    return "bidding"


def _action_round(events: FatbeansCaptureEvents) -> int | None:
    """Return the in-game round the user is about to act on.

    Packet round indices in ``0x0025`` are completed/revealed rounds. During
    bidding, the actionable UI has already advanced to the next round.
    """
    state = _latest_state(events)
    if state is None:
        return None
    if state.message_id == 0x002D or state.inventory_items:
        sent_bid_count = sum(1 for send in events.sends if send.kind == "bid")
        candidates = [
            candidate
            for candidate in (state.round_no, sent_bid_count or None)
            if candidate is not None
        ]
        return max(candidates) if candidates else None
    if state.message_id == 0x0021:
        return 1
    if state.round_no is None:
        sent_bid_count = sum(1 for send in events.sends if send.kind == "bid")
        return sent_bid_count + 1
    return state.round_no + 1


def _latest_map_id(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.map_id is not None:
            return state.map_id
    return None


def _latest_capture_time(events: FatbeansCaptureEvents) -> str:
    for state in reversed(events.states):
        if state.capture_time:
            return state.capture_time
    return ""


def _settlement_owner_evidence_status(
    events: FatbeansCaptureEvents,
    *,
    expected_session_id: str | None,
) -> str:
    """Collapse PW1 evidence for one session without exposing its aliases.

    Multiple settlement fragments may repeat the same valid block.  Evidence on
    an old/cross-session settlement, conflicting aliases, or any malformed block
    fails closed before the artifact is reduced to the selected session.
    """

    valid_fingerprints: set[tuple[object, ...]] = set()
    for state in events.states:
        if state.message_id != 0x002D:
            continue
        evidence = getattr(state, "settlement_auction_owner_evidence", None)
        status = getattr(evidence, "validation_status", None)
        if status == OWNER_EVIDENCE_ABSENT:
            continue
        if getattr(state, "session_id", None) != expected_session_id:
            return OWNER_EVIDENCE_CONFLICT
        if status != OWNER_EVIDENCE_VALID:
            return OWNER_EVIDENCE_CONFLICT
        fingerprint = (
            getattr(evidence, "owner_identity_alias", None),
            getattr(evidence, "owner_display_alias", None),
            getattr(evidence, "metadata_shape", None),
            tuple(getattr(evidence, "fields_present", ()) or ()),
        )
        if any(value in {None, "", ()} for value in fingerprint):
            return OWNER_EVIDENCE_CONFLICT
        valid_fingerprints.add(fingerprint)
        if len(valid_fingerprints) > 1:
            return OWNER_EVIDENCE_CONFLICT
    return OWNER_EVIDENCE_VALID if valid_fingerprints else OWNER_EVIDENCE_ABSENT


def _latest_session_id(events: FatbeansCaptureEvents) -> str | None:
    for state in reversed(events.states):
        if state.session_id:
            return state.session_id
    return None


def _is_plain_session_string(value: Any) -> bool:
    return type(value) is str  # noqa: E721 - subclasses can override comparison


_SESSION_NATIVE_TOKEN_MAX_DIGITS = 20
_SESSION_NATIVE_TOKEN_MAX = (1 << 64) - 1
_SESSION_SYNTHETIC_SEGMENT_MAX_LENGTH = 32
_SESSION_ID_MAX_LENGTH = 4 + 1 + _SESSION_SYNTHETIC_SEGMENT_MAX_LENGTH


def _is_ascii_session_letter(value: str) -> bool:
    return ("A" <= value <= "Z") or ("a" <= value <= "z")


def _is_ascii_session_digit(value: str) -> bool:
    return "0" <= value <= "9"


def _valid_synthetic_session_segment(value: str) -> bool:
    return (
        0 < len(value) <= _SESSION_SYNTHETIC_SEGMENT_MAX_LENGTH
        and _is_ascii_session_letter(value[0])
        and all(
            _is_ascii_session_letter(char)
            or _is_ascii_session_digit(char)
            or char in "_-"
            for char in value
        )
    )


def _session_descriptor(
    session_id: Any,
) -> tuple[str, int | None, int | None] | None:
    """Classify one bounded ASCII native or evaluator session id.

    Native numeric tokens and synthetic evaluator labels intentionally have
    disjoint grammars.  In particular, a malformed native-looking token must
    not survive by falling back to the synthetic branch.
    """

    if (
        not _is_plain_session_string(session_id)
        or not session_id
        or len(session_id) > _SESSION_ID_MAX_LENGTH
        or not session_id.isascii()
    ):
        return None

    if ":" not in session_id:
        if not _valid_synthetic_session_segment(session_id):
            return None
        return ("synthetic", None, None)

    if session_id.count(":") != 1:
        return None
    map_text, token = session_id.split(":", 1)
    if (
        len(map_text) != 4
        or not all(_is_ascii_session_digit(char) for char in map_text)
    ):
        return None
    map_id = int(map_text)

    if token and all(_is_ascii_session_digit(char) for char in token):
        if len(token) > _SESSION_NATIVE_TOKEN_MAX_DIGITS:
            return None
        generation = int(token)
        if generation > _SESSION_NATIVE_TOKEN_MAX:
            return None
        return ("native", map_id, generation)

    if not _valid_synthetic_session_segment(token):
        return None
    return ("synthetic", map_id, None)


def _session_generation(session_id: Any) -> int | None:
    descriptor = _session_descriptor(session_id)
    if descriptor is None or descriptor[0] != "native":
        return None
    return descriptor[2]


def _valid_semantic_session_id(session_id: Any) -> bool:
    return _session_descriptor(session_id) is not None


def _session_map_id(session_id: Any) -> int | None:
    descriptor = _session_descriptor(session_id)
    if descriptor is None:
        return None
    return descriptor[1]


def _state_has_valid_session(state: Any) -> bool:
    session_id = getattr(state, "session_id", None)
    if not _valid_semantic_session_id(session_id):
        return False
    session_map_id = _session_map_id(session_id)
    state_map_id = getattr(state, "map_id", None)
    if state_map_id is None or session_map_id is None:
        return True
    return (
        type(state_map_id) is int  # noqa: E721 - reject coercive subclasses
        and state_map_id == session_map_id
    )


def _latest_confirmed_session_id(events: FatbeansCaptureEvents) -> str | None:
    """Resolve the active game without letting late frames revive an older session.

    A confirmed id is either a native ``map:token`` string or a constrained
    synthetic id used by historical evaluators.  Native numeric tokens retain
    monotonic ordering; synthetic ids use sort order.  Legacy captures use
    ``None`` and are handled only after confirmed-session discovery; malformed
    strings and objects never become an artifact selector.
    """

    status_candidates: list[tuple[int, str]] = [
        (int(status.sort_id), status.session_id)
        for status in events.statuses
        if _valid_semantic_session_id(status.session_id)
    ]
    state_candidates: list[tuple[int, str]] = [
        (int(state.sort_id), state.session_id)
        for state in events.states
        if (
            state.message_id != 0x002D
            and _state_has_valid_session(state)
        )
    ]
    candidates = [*status_candidates, *state_candidates]
    if not candidates:
        candidates = [
            (int(state.sort_id), state.session_id)
            for state in events.states
            if _state_has_valid_session(state)
        ]
    if not candidates:
        return None

    generated = [
        (generation, sort_id, session_id)
        for sort_id, session_id in candidates
        if (generation := _session_generation(session_id)) is not None
    ]
    if generated:
        latest_generation = max(row[0] for row in generated)
        generated_statuses = [
            (sort_id, session_id)
            for sort_id, session_id in status_candidates
            if _session_generation(session_id) == latest_generation
        ]
        if generated_statuses:
            return max(generated_statuses, key=lambda row: row[0])[1]
        return max(generated)[2]
    return max(candidates, key=lambda row: row[0])[1]


def _session_scoped_events(
    events: FatbeansCaptureEvents,
    session_id: str | None,
) -> FatbeansCaptureEvents:
    """Return the single semantic session consumed by one monitor artifact.

    Packet/frame counts remain the capture-envelope diagnostics.  Every decoded
    semantic event is strict once a confirmed session exists; legacy ``None``
    events are retained only when the whole capture has no confirmed session.
    """

    if session_id is None:
        states = tuple(
            state for state in events.states if state.session_id is None
        )
        sends = tuple(
            send for send in events.sends if send.session_id is None
        )
        statuses = tuple(
            status for status in events.statuses if status.session_id is None
        )
    else:
        states = tuple(
            state
            for state in events.states
            if (
                _state_has_valid_session(state)
                and state.session_id == session_id
            )
        )
        sends = tuple(
            send
            for send in events.sends
            if (
                _valid_semantic_session_id(send.session_id)
                and send.session_id == session_id
            )
        )
        statuses = tuple(
            status
            for status in events.statuses
            if (
                _valid_semantic_session_id(status.session_id)
                and status.session_id == session_id
            )
        )
    if (
        states == events.states
        and sends == events.sends
        and statuses == events.statuses
    ):
        return events
    return replace(
        events,
        states=states,
        sends=sends,
        statuses=statuses,
    )


def _settlement_inventory_items(
    events: FatbeansCaptureEvents,
) -> tuple[FatbeansInventoryItem, ...]:
    """Union inventory rows from every 0x002D state in capture order.

    Live capture can split settlement across multiple REV frames (TCP
    reassembly, or late tails after the first partial 0x002D). Using only the
    last state under-counts when chunks carry disjoint item subsets.
    """
    merged: dict[tuple[int, int], FatbeansInventoryItem] = {}
    for state in events.states:
        if state.message_id != 0x002D or not state.inventory_items:
            continue
        for item in state.inventory_items:
            merged[(item.runtime_id, item.item_id)] = item
    if merged:
        return tuple(merged.values())
    for state in reversed(events.states):
        if state.inventory_items:
            return state.inventory_items
    return ()


def _inventory_totals(events: FatbeansCaptureEvents) -> tuple[int | None, int | None]:
    inventory_items = _settlement_inventory_items(events)
    if not inventory_items:
        return None, None
    return len(inventory_items), sum(item.cells for item in inventory_items)


def _settlement_category_authority(
    package_generation: str = POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
) -> ItemCategoryAuthority | None:
    """Load one category authority bound across all Item hash domains."""

    root = project_root()
    item_path = root / "data" / "raw" / "tables" / "Item.txt"
    authority_path = root / "data" / "processed" / "item_category_authority_0_3_2.json"
    try:
        profile = _current_category_authority_profile(package_generation)
        if profile is None:
            return None
        package_raw = load_text(item_path, encoding="utf-8").encode("utf-8")
        package_item = profile["package_item"]
        if not _exact_input(package_raw, package_item):
            return None
        canonical = decode_table_text(package_raw.decode("utf-8")).encode("utf-8")
        if (
            len(canonical) != package_item["decoded_bytes"]
            or hashlib.sha256(canonical).hexdigest()
            != package_item["decoded_sha256"]
        ):
            return None
        authority, authority_document = load_item_category_authority_document(
            authority_path
        )
        item_source = authority_document["sources"]["item_table"]
        if (
            authority.generation != profile["generation"]
            or authority.identity != profile["identity"]
            or item_source["rows"] != profile["item_rows"]
            or item_source["columns"] != profile.get("item_columns", 42)
            or item_source["sha256"] != package_item["raw_text_sha256"]
        ):
            return None
        return authority
    except (ItemCategoryAuthorityError, OSError, UnicodeError, ValueError):
        return None


def _settlement_category_model_map_matches_physical(
    physical_map_id: int,
    model_map_id: int,
) -> bool:
    """Accept only the frozen P3 physical-to-model relation.

    Raven's pre-open physical maps 5101..5110 use their matching 2501..2510
    model maps.  Other maps are not activity aliases for this settlement-only
    protocol: their trusted relation is exact identity.
    """

    if physical_map_id in _RAVEN_SETTLEMENT_PHYSICAL_MAP_IDS:
        return model_map_id == physical_map_id - _RAVEN_SETTLEMENT_MODEL_MAP_OFFSET
    return model_map_id == physical_map_id


def _settlement_category_fact_v1(
    events: FatbeansCaptureEvents,
    *,
    session_id: str | None,
    physical_map_id: int | None,
    model_map_id: int | None,
    tables: MonitorTables,
    warehouse_generation: str | None,
    settlement_incomplete: bool,
) -> dict[str, Any] | None:
    """Return a bounded, deidentified complete-settlement category fact.

    This is intentionally independent of bidding inference.  One malformed or
    duplicate inventory identity rejects the whole projection rather than
    emitting a partial category calibration sample.
    """

    if os.environ.get(SETTLEMENT_CATEGORY_FACTS_DISABLE) == "1":
        return None
    if (
        type(session_id) is not str  # noqa: E721 - current-session closure
        or not _valid_semantic_session_id(session_id)
        or type(physical_map_id) is not int  # noqa: E721
        or physical_map_id <= 0
        or type(model_map_id) is not int  # noqa: E721
        or model_map_id <= 0
        or physical_map_id not in tables.maps
        or model_map_id not in tables.maps
        or not _settlement_category_model_map_matches_physical(
            physical_map_id,
            model_map_id,
        )
        or _session_map_id(session_id) != physical_map_id
        or type(warehouse_generation) is not str  # noqa: E721
        or warehouse_generation not in {
            POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
            *_CURRENT_CATEGORY_AUTHORITY_GENERATIONS,
        }
        or settlement_incomplete
    ):
        return None
    authority = (
        _settlement_category_authority(
            getattr(
                tables,
                "warehouse_package_generation",
                "unknown_warehouse_generation",
            ),
        )
        if warehouse_generation in _CURRENT_CATEGORY_AUTHORITY_GENERATIONS
        else _settlement_category_authority()
    )
    if authority is None or not authority.generation:
        return None

    rows: list[dict[str, int]] = []
    seen_runtime_ids: set[int] = set()
    saw_settlement = False
    for state in events.states:
        if state.message_id != 0x002D:
            continue
        saw_settlement = True
        if (
            state.session_id != session_id
            or type(state.map_id) is not int  # noqa: E721 - raw map closure
            or state.map_id != physical_map_id
        ):
            return None
        for item in state.inventory_items:
            if (
                type(item.runtime_id) is not int  # noqa: E721
                or item.runtime_id <= 0
                or type(item.item_id) is not int  # noqa: E721
                or item.item_id <= 0
                or type(item.quality) is not int  # noqa: E721
                or not 1 <= item.quality <= 6
            ):
                return None
            if item.runtime_id in seen_runtime_ids:
                return None
            seen_runtime_ids.add(item.runtime_id)
            try:
                category = authority.require_random_drop_category(item.item_id)
            except ItemCategoryAuthorityError:
                return None
            rows.append(
                {
                    "item_id": item.item_id,
                    "quality": item.quality,
                    "primary_category": category,
                }
            )
            if len(rows) > MAX_SETTLEMENT_CATEGORY_ITEMS:
                return None
    if not saw_settlement or not rows:
        return None
    fact = {
        "schema": (
            SETTLEMENT_CATEGORY_FACT_SCHEMA_V2
            if warehouse_generation in _CURRENT_CATEGORY_AUTHORITY_GENERATIONS
            else SETTLEMENT_CATEGORY_FACT_SCHEMA
        ),
        "physical_map_id": physical_map_id,
        "model_map_id": model_map_id,
        "warehouse_generation": warehouse_generation,
        "category_authority_generation": authority.generation,
        SETTLEMENT_CATEGORY_ITEMS_SCHEMA: rows,
    }
    if warehouse_generation in _CURRENT_CATEGORY_AUTHORITY_GENERATIONS:
        fact["category_authority_identity"] = authority.identity
    return fact


def _resolve_live_formal_mode(value: str | None = None) -> str:
    raw = value if value is not None else os.environ.get(
        LIVE_FORMAL_MODE_ENV,
        LIVE_FORMAL_MODE_V2,
    )
    text = str(raw or LIVE_FORMAL_MODE_V2).strip().casefold()
    text = text.replace("-", "_")
    return _LIVE_FORMAL_MODE_ALIASES.get(text, LIVE_FORMAL_MODE_V3_PRACTICAL)


def _summary_from_flat_quantiles(
    row: Mapping[str, Any],
    prefix: str,
) -> QuantileSummary | None:
    p10 = _parse_float_text(row.get(f"{prefix}_p10"))
    p50 = _parse_float_text(row.get(f"{prefix}_p50"))
    p90 = _parse_float_text(row.get(f"{prefix}_p90"))
    if p10 is None or p50 is None or p90 is None:
        return None
    return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))


def _v3_flag_set(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    return {part for part in re.split(r"[+;,/\s]+", text) if part}


def _diagnostic_float_value(diagnostics: Any, key: str) -> float | None:
    pattern = rf"(?:^|;){re.escape(key)}=([-+]?\d+(?:\.\d+)?)"
    match = re.search(pattern, str(diagnostics or ""))
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _live_guard_v3_low_support_baseline_summary(
    v3_shadow: Mapping[str, Any],
    value_summary: QuantileSummary,
) -> tuple[QuantileSummary, str]:
    if str(v3_shadow.get("v3_practical_status") or "") != "baseline_passthrough":
        return value_summary, ""
    if str(v3_shadow.get("v3_practical_recommendation") or "") != "baseline_reference":
        return value_summary, ""
    if str(v3_shadow.get("v3_post_match_scope") or "") != "summary_likelihood":
        return value_summary, ""
    if bool(v3_shadow.get("v3_post_strict_ready")):
        return value_summary, ""
    risk_flags = _v3_flag_set(v3_shadow.get("v3_practical_risk_flags"))
    if risk_flags.intersection(_LIVE_V3_STRONG_RAISE_EVIDENCE_FLAGS):
        return value_summary, ""
    effective_samples = _diagnostic_float_value(
        v3_shadow.get("v3_post_diagnostics"),
        "summary_likelihood_effective_samples",
    )
    if (
        effective_samples is None
        or effective_samples > _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MAX_ESS
    ):
        return value_summary, ""
    p10 = float(value_summary.p10)
    p50 = float(value_summary.p50)
    p90 = float(value_summary.p90)
    if p50 - p10 < _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MIN_P50_GAP:
        return value_summary, ""
    guarded_p50_cap = max(
        p10 + _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_MIN_HEADROOM,
        p50 * _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P50_KEEP_RATIO,
    )
    guarded_p50 = max(p10, min(p50, guarded_p50_cap))
    if guarded_p50 >= p50:
        return value_summary, ""
    guarded_p90_cap = max(
        p50,
        guarded_p50 + _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P90_HEADROOM,
        guarded_p50 * _LIVE_V3_LOW_SUPPORT_BASELINE_GUARD_P90_MULTIPLIER,
    )
    guarded_p90 = max(guarded_p50, min(p90, guarded_p90_cap))
    guarded = QuantileSummary(
        p10=p10,
        p50=guarded_p50,
        p90=guarded_p90,
    )
    return (
        guarded,
        "live_low_support_baseline_guard:"
        f"ess={effective_samples:.3f};"
        f"p50 {p50:.0f}->{guarded_p50:.0f};"
        f"p90 {p90:.0f}->{guarded_p90:.0f}",
    )


def _live_guard_v3_practical_value_summary(
    v3_shadow: Mapping[str, Any],
    value_summary: QuantileSummary,
) -> tuple[QuantileSummary, str]:
    baseline_guarded, baseline_guard_reason = (
        _live_guard_v3_low_support_baseline_summary(v3_shadow, value_summary)
    )
    if baseline_guard_reason:
        return baseline_guarded, baseline_guard_reason
    if str(v3_shadow.get("v3_practical_recommendation") or "") != "raise_watch":
        return value_summary, ""
    confidence = str(v3_shadow.get("v3_practical_confidence") or "")
    if confidence not in _LIVE_V3_PRIOR_ONLY_RAISE_GUARD_CONFIDENCES:
        return value_summary, ""
    risk_flags = _v3_flag_set(v3_shadow.get("v3_practical_risk_flags"))
    if not risk_flags.intersection(_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_FLAGS):
        return value_summary, ""
    if risk_flags.intersection(_LIVE_V3_STRONG_RAISE_EVIDENCE_FLAGS):
        return value_summary, ""
    p90_gap = float(value_summary.p90) - float(value_summary.p50)
    if p90_gap < _LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MIN_GAP:
        return value_summary, ""
    p50 = float(value_summary.p50)
    baseline_p90 = _parse_float_text(
        v3_shadow.get("v3_practical_baseline_formal_decision_value_p90")
    )
    cap = p50 + max(
        _LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MIN_HEADROOM,
        p50 * (_LIVE_V3_PRIOR_ONLY_RAISE_GUARD_MAX_P50_MULTIPLIER - 1.0),
    )
    guarded_p90 = max(
        p50,
        baseline_p90 if baseline_p90 is not None else p50,
        min(float(value_summary.p90), cap),
    )
    if guarded_p90 >= float(value_summary.p90):
        return value_summary, ""
    guarded = QuantileSummary(
        p10=float(value_summary.p10),
        p50=p50,
        p90=guarded_p90,
    )
    return (
        guarded,
        "live_prior_only_raise_guard:"
        f"p90 {float(value_summary.p90):.0f}->{guarded_p90:.0f};"
        f"confidence={confidence};flags={'+'.join(sorted(risk_flags))}",
    )


def _build_v3_practical_bid_rows(
    *,
    latest_bids: Mapping[str, int],
    v3_shadow: Mapping[str, Any],
    session: Any,
    round_no: int | None,
    posterior_samples: int,
    warehouse_estimate: Any,
    q6_prior_gap: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if not v3_shadow.get("v3_practical_available"):
        return [], "v3_practical_unavailable"
    value_summary = _summary_from_flat_quantiles(
        v3_shadow,
        "v3_practical_formal_decision_value",
    )
    if value_summary is None:
        return [], "v3_practical_missing_formal_value"
    guarded_value_summary, live_guard_reason = _live_guard_v3_practical_value_summary(
        v3_shadow,
        value_summary,
    )
    raw_value_summary = _summary_from_flat_quantiles(
        v3_shadow,
        "v3_practical_total_value",
    )
    rows = _build_bid_rows(
        latest_bids=latest_bids,
        value_summary=guarded_value_summary,
        evidence_label="v3 practical formal",
        session=session,
        round_no=round_no,
        posterior_samples=posterior_samples,
        warehouse_estimate=warehouse_estimate,
        decision_value_summary=guarded_value_summary,
        raw_value_summary=raw_value_summary,
        posterior_diagnostics=(
            str(v3_shadow.get("v3_practical_diagnostics") or ""),
            live_guard_reason,
        ),
        q6_prior_gap=q6_prior_gap,
    )
    if not rows:
        return [], "v3_practical_no_bid_rows"
    recommendation = str(v3_shadow.get("v3_practical_recommendation") or "")
    for row in rows:
        row["formal_mode"] = LIVE_FORMAL_MODE_V3_PRACTICAL
        row["formal_mode_reason"] = (
            "v3_practical_ready_live_guarded"
            if live_guard_reason
            else "v3_practical_ready"
        )
        row["formal_baseline_source"] = LIVE_FORMAL_MODE_V3_PRACTICAL
        row["formal_override"] = "是"
        row["v3_practical_live_guard"] = "是" if live_guard_reason else "否"
        row["v3_practical_live_guard_reason"] = live_guard_reason
        if live_guard_reason:
            row["v3_practical_unguarded_decision_value"] = (
                _format_quantile_interval(value_summary)
            )
        row["v3_practical_recommendation"] = recommendation
        row["v3_practical_confidence"] = str(
            v3_shadow.get("v3_practical_confidence") or ""
        )
        row["v3_practical_source_lanes"] = str(
            v3_shadow.get("v3_practical_source_lanes") or ""
        )
        row["v3_practical_risk_flags"] = str(
            v3_shadow.get("v3_practical_risk_flags") or ""
        )
        row["v3_practical_reason"] = str(
            v3_shadow.get("v3_practical_reason") or ""
        )
    return (
        rows,
        "v3_practical_ready_live_guarded"
        if live_guard_reason
        else "v3_practical_ready",
    )


def _build_bid_rows(
    *,
    latest_bids: Mapping[str, int],
    value_summary: Any,
    evidence_label: str,
    session: Any,
    round_no: int | None,
    posterior_samples: int,
    warehouse_estimate: Any,
    decision_value_summary: Any = None,
    raw_value_summary: Any = None,
    posterior_diagnostics: Sequence[str] = (),
    q6_prior_gap: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    report = recommend_bid_strategy(
        latest_bids=latest_bids,
        value_summary=value_summary,
        evidence_label=evidence_label,
        session=session,
        round_no=round_no,
        total_rounds=5,
        posterior_samples=posterior_samples,
        warehouse_estimate=warehouse_estimate,
    )
    if report is None:
        return []
    thresholds = report.thresholds
    q6_risk_reference = _q6_risk_reference_text(q6_prior_gap)
    rows = [
        {
            "证据": report.evidence_label,
            "价值口径": "decision_value" if decision_value_summary is not None else "raw_value",
            "轮次": report.round_label,
            "信息强度": report.info_strength,
            "仓储": report.warehouse_status,
            "决策价值 P10/P50/P90": _format_quantile_interval(decision_value_summary),
            "原始价值 P10/P50/P90": _format_quantile_interval(raw_value_summary),
            "上界风险": _raw_ceiling_risk_label(
                decision_value_summary,
                raw_value_summary,
            ),
            "当前最高": f"{report.leader} {report.highest_bid:,}",
            "风险带": report.risk_band,
            "秒仓倍率": f"{thresholds.warehouse_multiplier:g}x",
            "探价(P10)": f"{thresholds.probe_bid:,}",
            "防守价": f"{thresholds.defend_bid:,}",
            "可追价(P90)": f"{thresholds.attack_bid:,}",
            "抢仓上限": f"{thresholds.attack_bid:,}",
            "停止价": f"{thresholds.stop_bid:,}",
            "依据": report.rationale,
            "补信息": report.next_info_hint,
            "后验诊断": ";".join(posterior_diagnostics),
            "红货风险参考": q6_risk_reference,
            "建议": report.action,
        }
    ]
    for player in report.player_risks:
        rows.append(
            {
                "证据": "玩家价位",
                "轮次": "",
                "信息强度": "",
                "仓储": "",
                "当前最高": f"{player.name} {player.bid:,}",
                "风险带": player.risk_band,
                "秒仓倍率": "",
                "探价(P10)": "",
                "防守价": "",
                "可追价(P90)": "",
                "抢仓上限": "",
                "停止价": "",
                "依据": "",
                "补信息": "",
                "建议": "",
                "红货风险参考": "",
            }
        )
    return rows


def _build_zero_match_fallback_rows(
    *,
    candidate_map_ids: Sequence[int],
    inference_session: Any,
    latest_bids: Mapping[str, int],
    round_no: int | None,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int,
    seed: int,
    cells_tol: int,
    count_tol: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    fallback_session = _zero_match_fallback_session(inference_session)
    fallback_cells_tol = max(cells_tol, 12)
    fallback_count_tol = max(count_tol, 3)
    fallback_results = estimate_map_likelihood(
        candidate_map_ids,
        fallback_session,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=n_trials,
        seed=seed,
        cells_tol=fallback_cells_tol,
        count_tol=fallback_count_tol,
        warehouse_tol=fallback_cells_tol,
        total_item_count_tol=fallback_count_tol,
    )
    fallback_warehouse = estimate_warehouse_cells(
        candidate_map_ids,
        fallback_session,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=n_trials,
        seed=seed,
        cells_tol=fallback_cells_tol,
        count_tol=fallback_count_tol,
        total_item_count_tol=fallback_count_tol,
    )
    fallback_map_rows = _map_likelihood_result_rows(
        fallback_results,
        "v1 fallback（v2无匹配，map prior）",
    )
    fallback_warehouse_rows = _warehouse_estimate_rows(fallback_warehouse)
    value_summary = next(
        (
            result.total_value
            for result in fallback_results
            if result.total_value is not None
        ),
        fallback_warehouse.total_value,
    )
    posterior_samples = max(
        [fallback_warehouse.n_matched]
        + [result.n_matched for result in fallback_results],
        default=0,
    )
    fallback_bid_rows: list[dict[str, Any]] = []
    if value_summary is not None:
        fallback_bid_rows = _build_bid_rows(
            latest_bids=latest_bids,
            value_summary=value_summary,
            evidence_label="v1 fallback（v2无匹配）",
            session=fallback_session,
            round_no=round_no,
            posterior_samples=posterior_samples,
            warehouse_estimate=fallback_warehouse,
            raw_value_summary=value_summary,
        )
        for row in fallback_bid_rows:
            row["fallback"] = "是"
            row["fallback_mode"] = "v1_map_prior_zero_match"
            row["fallback_note"] = (
                "v2 后验无匹配时的 map-prior 低置信参考；不替代 baseline v2"
            )
    return fallback_map_rows, fallback_warehouse_rows, fallback_bid_rows


def _parse_range_value(label: str, index: int) -> int | None:
    parts = [part.strip().replace(",", "") for part in label.split("/")]
    if len(parts) <= index or parts[index] in ("", "?"):
        return None
    try:
        return int(float(parts[index]))
    except ValueError:
        return None


def _parse_range_float_value(label: str, index: int) -> float | None:
    parts = [part.strip().replace(",", "") for part in label.split("/")]
    if len(parts) <= index or parts[index] in ("", "?"):
        return None
    try:
        return float(parts[index])
    except ValueError:
        return None


def _parse_range_p50(label: str) -> int | None:
    return _parse_range_value(label, 1)


def _parse_match_text(value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if not text or "/" not in text:
        return None, None
    left, right = text.split("/", 1)
    return _parse_int_text(left), _parse_int_text(right)


def _parse_percent_text(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def _parse_float_text(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _inventory_quality_breakdown(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Item],
    *,
    problem: ResidualProblem | None = None,
    maps: Mapping[int, BidMap] | None = None,
    drops: Mapping[int, DropPool] | None = None,
    map_id: int | None = None,
) -> dict[str, Any]:
    resolved_map_id = map_id if map_id is not None else (
        problem.map_id if problem is not None else None
    )
    map_item_weights = _effective_item_weights_for_map(
        resolved_map_id,
        maps=maps,
        drops=drops,
        items=items,
    )
    inventory_items = _settlement_inventory_items(events)
    if not inventory_items:
        return {}
    counts: Counter[int] = Counter()
    cells: Counter[int] = Counter()
    values: defaultdict[int, int] = defaultdict(int)
    top_item: dict[str, Any] = {}
    decision_value = 0
    trimmed_value = 0
    trimmed_items: list[str] = []
    q6_decision_value = 0
    q6_trimmed_value = 0
    q6_trimmed_items: list[str] = []
    q6_tail_replacement_value = 0
    q6_tail_replacement_count = 0
    q6_tail_replacement_items: list[str] = []
    q6_tail_replacement_sources: set[str] = set()
    exact_anchor_ids = set(problem.anchor_item_counts) if problem else set()
    for inv_item in inventory_items:
        item = items.get(inv_item.item_id)
        quality = inv_item.quality
        # BUG-C (2026-07-10, 5603 磁悬浮滑板): the settlement wire can DROP the quality field to 0
        # (not just None) while the catalog id survives — the red then vanished from
        # final_quality_counts/cells (红 2件3格 vs 应 3件6格) although its VALUE was catalog-priced.
        # Recover the quality from the catalog whenever the wire value is missing OR non-positive.
        if (quality is None or int(quality) <= 0) and item is not None and (item.quality or 0) > 0:
            quality = item.quality
        value = item.value if item is not None else 0
        if not top_item or value > int(top_item.get("value") or 0):
            top_item = {
                "id": inv_item.item_id,
                "name": item.name if item is not None else "",
                "quality": quality,
                "value": value,
                "cells": inv_item.cells,
            }
        if quality is None:
            continue
        q = int(quality)
        trim_item = False
        if (
            item is not None
            and item.item_id not in exact_anchor_ids
            and is_confusable_long_tail(item)
        ):
            trim_item = True
        if (
            item is not None
            and problem is not None
            and item.value >= DEFAULT_VALUE_FLOOR
            and not is_tail_supported_by_evidence(item, problem)
        ):
            trim_item = True
        if item is not None and trim_item:
            trimmed_value += value
            if len(trimmed_items) < 4:
                trimmed_items.append(f"{item.name}:{value}")
            if q == 6:
                q6_trimmed_value += value
                if len(q6_trimmed_items) < 4:
                    q6_trimmed_items.append(f"{item.name}:{value}")
                replacement = _tail_replacement_for_item(
                    item,
                    items,
                    map_item_weights=map_item_weights,
                )
                if replacement["value"] > 0:
                    q6_tail_replacement_count += 1
                    q6_tail_replacement_value += int(replacement["value"])
                    if replacement["source"]:
                        q6_tail_replacement_sources.add(
                            str(replacement["source"])
                        )
                    if len(q6_tail_replacement_items) < 4:
                        q6_tail_replacement_items.append(
                            f"{item.name}:{value}->{replacement['value']}"
                        )
        else:
            decision_value += value
            if q == 6:
                q6_decision_value += value
        counts[q] += 1
        cells[q] += inv_item.cells
        values[q] += value
    return {
        "final_quality_counts": _format_quality_map(counts),
        "final_quality_cells": _format_quality_map(cells),
        "final_quality_values": _format_quality_map(values),
        "final_q5_count": counts.get(5, 0),
        "final_q5_cells": cells.get(5, 0),
        "final_q5_value": values.get(5, 0),
        "final_q6_count": counts.get(6, 0),
        "final_q6_cells": cells.get(6, 0),
        "final_q6_value": values.get(6, 0),
        "final_q6_decision_value": q6_decision_value,
        "final_q6_trimmed_tail_value": q6_trimmed_value,
        "final_q6_trimmed_tail_items": ";".join(q6_trimmed_items),
        "final_q6_tail_replacement_value": q6_tail_replacement_value,
        "final_q6_tail_replacement_count": q6_tail_replacement_count,
        "final_q6_tail_replacement_items": ";".join(
            q6_tail_replacement_items
        ),
        "final_q6_tail_replacement_source": ";".join(
            sorted(q6_tail_replacement_sources)
        ),
        "final_q6_decision_value_with_tail_replacement": (
            q6_decision_value + q6_tail_replacement_value
        ),
        "final_decision_value": decision_value,
        "final_decision_value_with_tail_replacement": (
            decision_value + q6_tail_replacement_value
        ),
        "final_trimmed_tail_value": trimmed_value,
        "final_trimmed_tail_items": ";".join(trimmed_items),
        "final_top_item_id": top_item.get("id"),
        "final_top_item_name": top_item.get("name"),
        "final_top_item_quality": top_item.get("quality"),
        "final_top_item_value": top_item.get("value"),
        "final_top_item_cells": top_item.get("cells"),
    }


def _effective_item_weights_for_map(
    map_id: int | None,
    *,
    maps: Mapping[int, BidMap] | None,
    drops: Mapping[int, DropPool] | None,
    items: Mapping[int, Item],
) -> dict[int, float]:
    if map_id is None or maps is None or drops is None:
        return {}
    bid_map = maps.get(map_id)
    if bid_map is None:
        return {}
    out: defaultdict[int, float] = defaultdict(float)
    try:
        if not bid_map.sub_pool_weights:
            flat = flatten_pool(bid_map.drop_pool_id, drops, items, map_id=bid_map.map_id)
            for item_id, probability in zip(flat.item_ids, flat.probabilities):
                out[int(item_id)] += float(probability)
        else:
            total_weight = sum(
                weight
                for sub_map_id, weight in bid_map.sub_pool_weights
                if weight > 0 and sub_map_id in maps
            )
            if total_weight <= 0:
                return {}
            for sub_map_id, sub_weight in bid_map.sub_pool_weights:
                if sub_weight <= 0:
                    continue
                sub_map = maps.get(sub_map_id)
                if sub_map is None:
                    continue
                flat = flatten_pool(sub_map.drop_pool_id, drops, items, map_id=sub_map.map_id)
                sub_probability = float(sub_weight) / float(total_weight)
                for item_id, probability in zip(flat.item_ids, flat.probabilities):
                    out[int(item_id)] += sub_probability * float(probability)
    except Exception:
        return {}
    return dict(out)


def _tail_replacement_for_item(
    item: Item,
    items: Mapping[int, Item],
    *,
    map_item_weights: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in items.values()
        if candidate.item_id != item.item_id
        and candidate.quality == item.quality
        and candidate.shape_w == item.shape_w
        and candidate.shape_h == item.shape_h
        and 0 < candidate.value < DEFAULT_VALUE_FLOOR
    ]
    if not candidates:
        return {"value": 0, "source": "", "candidate_count": 0}

    if map_item_weights:
        weighted = [
            (candidate.value, float(map_item_weights.get(candidate.item_id) or 0.0))
            for candidate in candidates
            if float(map_item_weights.get(candidate.item_id) or 0.0) > 0.0
        ]
        weighted_value = _weighted_median_value(weighted)
        if weighted_value is not None:
            return {
                "value": weighted_value,
                "source": "map_weighted_p50",
                "candidate_count": len(weighted),
            }
        return {"value": 0, "source": "", "candidate_count": 0}

    values = sorted(candidate.value for candidate in candidates)
    return {
        "value": int(round(statistics.median(values))),
        "source": "item_table_median",
        "candidate_count": len(values),
    }


def _weighted_median_value(values: Sequence[tuple[int, float]]) -> int | None:
    positive = [(int(value), float(weight)) for value, weight in values if weight > 0]
    if not positive:
        return None
    total = sum(weight for _, weight in positive)
    midpoint = total / 2.0
    running = 0.0
    for value, weight in sorted(positive, key=lambda row: row[0]):
        running += weight
        if running >= midpoint:
            return value
    return positive[-1][0]


def _format_quality_map(values: Mapping[int, int]) -> str:
    return ";".join(
        f"q{quality}={value}"
        for quality, value in sorted(values.items())
        if value
    )


def _parse_quality_count_total(value: Any) -> int | None:
    if value is None:
        return None
    total = 0
    matched = False
    for part in str(value).split(";"):
        if "=" not in part:
            continue
        _quality, raw_count = part.split("=", 1)
        parsed = _parse_int_text(raw_count)
        if parsed is None:
            continue
        total += parsed
        matched = True
    return total if matched else None


def _count_source_and_target(
    *,
    exact: int | None,
    floor: int | None,
) -> tuple[str, int | None]:
    if exact is not None and exact > 0:
        if floor is not None and floor > exact:
            return ("floor_over_exact", floor)
        return ("exact", exact)
    if floor is not None and floor > 0:
        return ("floor", floor)
    return ("none", None)


def _numeric_delta(left: int | float | None, right: int | float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _numeric_ratio(left: int | float | None, right: int | float | None) -> float | None:
    if left is None or right is None or float(right) <= 0.0:
        return None
    return float(left) / float(right)


def _capacity_cases(
    *,
    total_count_source: str,
    target_prior_max_delta: float | None,
    truth_prior_max_delta: float | None,
    target_truth_delta: float | None,
) -> list[str]:
    target_above_prior = (
        target_prior_max_delta is not None and target_prior_max_delta > 0.0
    )
    truth_above_prior = (
        truth_prior_max_delta is not None and truth_prior_max_delta > 0.0
    )
    target_below_truth = target_truth_delta is not None and target_truth_delta < 0.0
    target_matches_truth = target_truth_delta is not None and target_truth_delta == 0.0
    target_above_truth = target_truth_delta is not None and target_truth_delta > 0.0
    cases: list[str] = []
    if target_above_prior and truth_above_prior and target_matches_truth:
        cases.append("direct_prior_max_conflict")
    if truth_above_prior and target_below_truth:
        cases.append("target_lower_bound_truth_above_prior")
    if target_above_prior and target_below_truth:
        cases.append("target_above_prior_but_below_truth")
    if target_above_prior and target_above_truth:
        cases.append("target_over_truth_capacity_risk")
    if truth_above_prior and total_count_source == "none":
        cases.append("truth_above_prior_without_count_target")
    if target_above_prior and not truth_above_prior:
        cases.append("target_above_prior_without_truth_support")
    if truth_above_prior and not target_above_prior and not target_below_truth:
        cases.append("truth_above_prior_without_target_prior_hit")
    if not cases:
        cases.append("no_capacity_prior_max_case")
    return cases


def _q6_top_size_band(truth_breakdown: Mapping[str, Any] | None) -> str:
    if int((truth_breakdown or {}).get("final_q6_count") or 0) <= 0:
        return "no_q6"
    top_quality = (truth_breakdown or {}).get("final_top_item_quality")
    if top_quality is None:
        return "q6_top_unknown_cells"
    if int(top_quality) != 6:
        return "q6_not_top_item"
    cells = (truth_breakdown or {}).get("final_top_item_cells")
    if cells is None:
        return "q6_top_unknown_cells"
    cells = int(cells)
    if cells <= 2:
        return "q6_top_small"
    if cells <= 4:
        return "q6_top_compact"
    if cells <= 9:
        return "q6_top_medium"
    if cells <= 12:
        return "q6_top_large"
    return "q6_top_huge"


def _model_eval_shadow_fields(
    prefix: str,
    shadow: Mapping[str, Any],
    *,
    baseline_q6_decision_value_p90: int | None,
    final_q6_decision_value: int,
    zero_q6_proven_control: bool,
) -> dict[str, Any]:
    active = bool(shadow.get("active"))
    shadow_q6_p90 = _parse_int_text(shadow.get("q6_decision_value_p90"))
    no_plannable_control = active and final_q6_decision_value <= 0
    no_plannable_positive = no_plannable_control and (shadow_q6_p90 or 0) > 0
    zero_q6_positive = (
        no_plannable_positive
        and zero_q6_proven_control
    )
    under_before = (
        active
        and baseline_q6_decision_value_p90 is not None
        and final_q6_decision_value > baseline_q6_decision_value_p90
    )
    covered_after = (
        active
        and shadow_q6_p90 is not None
        and final_q6_decision_value <= shadow_q6_p90
    )
    return {
        f"{prefix}_label": shadow.get("label"),
        f"{prefix}_gate": shadow.get("gate"),
        f"{prefix}_evidence_profile": shadow.get("evidence_profile_key"),
        f"{prefix}_active": active,
        f"{prefix}_trials": _parse_int_text(shadow.get("trials")),
        f"{prefix}_active_boost": _parse_float_text(shadow.get("active_boost")),
        f"{prefix}_active_prior_floor_ratio": _parse_float_text(
            shadow.get("active_prior_floor_ratio")
        ),
        f"{prefix}_active_conditional_target_count": _parse_float_text(
            shadow.get("active_conditional_target_count")
        ),
        f"{prefix}_active_conditional_target_cells": _parse_float_text(
            shadow.get("active_conditional_target_cells")
        ),
        f"{prefix}_active_conditional_value_power": _parse_float_text(
            shadow.get("active_conditional_value_power")
        ),
        f"{prefix}_decision_value_p50": _parse_int_text(
            shadow.get("decision_value_p50")
        ),
        f"{prefix}_decision_value_p90": _parse_int_text(
            shadow.get("decision_value_p90")
        ),
        f"{prefix}_q6_decision_value_p90": shadow_q6_p90,
        f"{prefix}_q6_count_p90": _parse_int_text(shadow.get("q6_count_p90")),
        f"{prefix}_q6_cells_p90": _parse_int_text(shadow.get("q6_cells_p90")),
        f"{prefix}_q6_p90_delta": (
            shadow_q6_p90 - baseline_q6_decision_value_p90
            if shadow_q6_p90 is not None
            and baseline_q6_decision_value_p90 is not None
            else None
        ),
        f"{prefix}_under_before": under_before,
        f"{prefix}_covered_after": covered_after,
        f"{prefix}_helped": under_before and covered_after,
        f"{prefix}_no_plannable_control": no_plannable_control,
        f"{prefix}_no_plannable_positive_proxy": no_plannable_positive,
        f"{prefix}_zero_q6_proven_control": (
            no_plannable_control and zero_q6_proven_control
        ),
        f"{prefix}_zero_q6_proven_false_positive": zero_q6_positive,
        f"{prefix}_false_positive_proxy": no_plannable_positive,
    }


def _model_eval_row(
    *,
    file: str,
    artifact: Mapping[str, Any],
    final_value: int | None,
    final_cells: int | None,
    truth_breakdown: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if final_value is None and final_cells is None:
        return None
    warehouse_rows = artifact.get("warehouse_rows") or []
    bid_rows = artifact.get("bid_rows") or []
    bid_row = next((row for row in bid_rows if isinstance(row, Mapping)), {})
    v3_practical_unguarded_decision_value = bid_row.get(
        "v3_practical_unguarded_decision_value"
    )
    v3_practical_unguarded_p10 = _parse_range_value(
        str(v3_practical_unguarded_decision_value or ""),
        0,
    )
    v3_practical_unguarded_p50 = _parse_range_value(
        str(v3_practical_unguarded_decision_value or ""),
        1,
    )
    v3_practical_unguarded_p90 = _parse_range_value(
        str(v3_practical_unguarded_decision_value or ""),
        2,
    )
    layout_rows = artifact.get("layout_replay_rows") or []
    v2_rows = artifact.get("v2_posterior_rows") or []
    v3_shadow = (
        artifact.get("v3_posterior_shadow")
        if isinstance(artifact.get("v3_posterior_shadow"), Mapping)
        else {}
    )
    shadow = artifact.get("q6_residual_boost_shadow") or {}
    deep_floor_shadow = artifact.get("q6_residual_deep_floor_shadow") or {}
    deep11_floor_shadow = artifact.get("q6_residual_deep11_floor_shadow") or {}
    hidden_floor_shadow = artifact.get("q6_residual_hidden_floor_shadow") or {}
    villa_floor_shadow = artifact.get("q6_residual_villa_floor_shadow") or {}
    ethan_villa_random_floor_shadow = (
        artifact.get("q6_residual_ethan_villa_random_floor_shadow") or {}
    )
    ethan_shipwreck_layout_conditional_shadow = (
        artifact.get("q6_residual_ethan_shipwreck_layout_conditional_shadow") or {}
    )
    formal_prior_floor = artifact.get("q6_formal_prior_floor") or {}
    bottom_row_risk = artifact.get("q6_aisha_bottom_row_risk") or {}
    quality_only_local_risk = artifact.get("q6_quality_only_local_risk") or {}
    input_constraints = (
        artifact.get("inference_input_constraints")
        if isinstance(artifact.get("inference_input_constraints"), Mapping)
        else {}
    )
    warehouse_p50 = None
    value_p50 = None
    decision_value_p50 = None
    decision_value_p90 = None
    raw_value_p50 = None
    raw_value_p90 = None
    q6_match_rate = None
    q6_prior_match_rate = None
    q6_prior_expected_count = None
    q6_prior_expected_cells = None
    q6_prior_expected_value = None
    prior_expected_count = None
    prior_expected_cells = None
    prior_expected_value = None
    prior_expected_decision_value = None
    prior_expected_tail_replacement_decision_value = None
    q6_value_p90 = None
    q6_decision_value_p90 = None
    q6_tail_replacement_estimate_p90 = None
    q6_count_p90 = None
    q6_cells_p90 = None
    remaining_cells_after_layout_p10 = None
    remaining_cells_after_layout_p50 = None
    remaining_cells_after_layout_p90 = None
    q6_space_pressure_p50 = None
    q6_space_pressure_p90 = None
    q6_space_overflow_rate = None
    q6_prior_gap_summary = ""
    q6_prior_floor_value = None
    q6_practical_gate = ""
    q6_practical_p90 = None
    v3_post_available = bool(v3_shadow.get("v3_post_available"))
    v3_post_ready = bool(v3_shadow.get("v3_post_ready"))
    v3_post_strict_ready = bool(v3_shadow.get("v3_post_strict_ready"))
    v3_post_affects_bid = bool(v3_shadow.get("v3_post_affects_bid"))
    v3_post_match_scope = v3_shadow.get("v3_post_match_scope")
    v3_post_n_total = _parse_int_text(v3_shadow.get("v3_post_n_total"))
    v3_post_n_matched = _parse_int_text(v3_shadow.get("v3_post_n_matched"))
    v3_post_n_strict_matched = _parse_int_text(
        v3_shadow.get("v3_post_n_strict_matched")
    )
    v3_post_match_rate = _parse_float_text(v3_shadow.get("v3_post_match_rate"))
    v3_post_strict_match_rate = _parse_float_text(
        v3_shadow.get("v3_post_strict_match_rate")
    )
    v3_post_q6_present_rate = _parse_float_text(
        v3_shadow.get("v3_post_q6_present_rate")
    )
    v3_post_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_post_formal_decision_value_p50")
    )
    v3_post_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_post_formal_decision_value_p90")
    )
    v3_post_tail_replacement_p50 = _parse_int_text(
        v3_shadow.get("v3_post_tail_replacement_decision_value_p50")
    )
    v3_post_tail_replacement_p90 = _parse_int_text(
        v3_shadow.get("v3_post_tail_replacement_decision_value_p90")
    )
    v3_post_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_post_q6_formal_decision_value_p50")
    )
    v3_post_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_post_q6_formal_decision_value_p90")
    )
    v3_post_q6_count_p50 = _parse_int_text(v3_shadow.get("v3_post_q6_count_p50"))
    v3_post_q6_count_p90 = _parse_int_text(v3_shadow.get("v3_post_q6_count_p90"))
    v3_post_q6_cells_p50 = _parse_int_text(v3_shadow.get("v3_post_q6_cells_p50"))
    v3_post_q6_cells_p90 = _parse_int_text(v3_shadow.get("v3_post_q6_cells_p90"))
    v3_post_total_cells_p50 = _parse_int_text(
        v3_shadow.get("v3_post_total_cells_p50")
    )
    v3_post_total_cells_p90 = _parse_int_text(
        v3_shadow.get("v3_post_total_cells_p90")
    )
    v3_summary_known_count_floor = _parse_int_text(
        v3_shadow.get("v3_summary_known_count_floor")
    )
    v3_summary_known_cells_floor = _parse_int_text(
        v3_shadow.get("v3_summary_known_cells_floor")
    )
    v3_summary_known_value_floor = _parse_int_text(
        v3_shadow.get("v3_summary_known_value_floor")
    )
    v3_summary_session_total_count_exact = _parse_int_text(
        v3_shadow.get("v3_summary_session_total_count_exact")
    )
    v3_summary_session_total_cells_exact = _parse_int_text(
        v3_shadow.get("v3_summary_session_total_cells_exact")
    )
    v3_summary_q6_count_floor = _parse_int_text(
        v3_shadow.get("v3_summary_q6_count_floor")
    )
    v3_summary_q6_cells_floor = _parse_int_text(
        v3_shadow.get("v3_summary_q6_cells_floor")
    )
    v3_summary_q6_value_floor = _parse_int_text(
        v3_shadow.get("v3_summary_q6_value_floor")
    )
    v3_prior_available = bool(v3_shadow.get("v3_prior_available"))
    v3_prior_error = v3_shadow.get("v3_prior_error")
    v3_prior_map_id = _parse_int_text(v3_shadow.get("v3_prior_map_id"))
    v3_prior_map_name = v3_shadow.get("v3_prior_map_name")
    v3_prior_items_per_session_min = _parse_int_text(
        v3_shadow.get("v3_prior_items_per_session_min")
    )
    v3_prior_items_per_session_max = _parse_int_text(
        v3_shadow.get("v3_prior_items_per_session_max")
    )
    v3_prior_pool_count = _parse_int_text(v3_shadow.get("v3_prior_pool_count"))
    v3_prior_expected_draws = _parse_float_text(
        v3_shadow.get("v3_prior_expected_draws")
    )
    v3_prior_expected_count = _parse_float_text(
        v3_shadow.get("v3_prior_expected_count")
    )
    v3_prior_expected_cells = _parse_float_text(
        v3_shadow.get("v3_prior_expected_cells")
    )
    v3_prior_expected_value = _parse_float_text(
        v3_shadow.get("v3_prior_expected_value")
    )
    v3_prior_q6_draw_probability = _parse_float_text(
        v3_shadow.get("v3_prior_q6_draw_probability")
    )
    v3_prior_q6_session_probability = _parse_float_text(
        v3_shadow.get("v3_prior_q6_session_probability")
    )
    v3_prior_q6_expected_count = _parse_float_text(
        v3_shadow.get("v3_prior_q6_expected_count")
    )
    v3_prior_q6_expected_cells = _parse_float_text(
        v3_shadow.get("v3_prior_q6_expected_cells")
    )
    v3_prior_q6_expected_value = _parse_float_text(
        v3_shadow.get("v3_prior_q6_expected_value")
    )
    v3_capacity_total_count_source, v3_capacity_total_count_target = (
        _count_source_and_target(
            exact=v3_summary_session_total_count_exact,
            floor=v3_summary_known_count_floor,
        )
    )
    v3_capacity_truth_item_count = _parse_quality_count_total(
        (truth_breakdown or {}).get("final_quality_counts")
    )
    v3_capacity_target_prior_max_delta = _numeric_delta(
        v3_capacity_total_count_target,
        v3_prior_items_per_session_max,
    )
    v3_capacity_truth_prior_max_delta = _numeric_delta(
        v3_capacity_truth_item_count,
        v3_prior_items_per_session_max,
    )
    v3_capacity_target_truth_delta = _numeric_delta(
        v3_capacity_total_count_target,
        v3_capacity_truth_item_count,
    )
    v3_capacity_flags: list[str] = []
    if (
        v3_prior_items_per_session_max is not None
        and v3_capacity_total_count_target is not None
        and v3_capacity_total_count_target > v3_prior_items_per_session_max
    ):
        v3_capacity_flags.append("target_count_above_prior_max")
    if (
        v3_prior_items_per_session_max is not None
        and v3_capacity_truth_item_count is not None
        and v3_capacity_truth_item_count > v3_prior_items_per_session_max
    ):
        v3_capacity_flags.append("truth_count_above_prior_max")
    if (
        v3_prior_items_per_session_min is not None
        and v3_capacity_truth_item_count is not None
        and v3_capacity_truth_item_count < v3_prior_items_per_session_min
    ):
        v3_capacity_flags.append("truth_count_below_prior_min")
    v3_capacity_cases = _capacity_cases(
        total_count_source=v3_capacity_total_count_source,
        target_prior_max_delta=v3_capacity_target_prior_max_delta,
        truth_prior_max_delta=v3_capacity_truth_prior_max_delta,
        target_truth_delta=v3_capacity_target_truth_delta,
    )
    v3_robust_available = bool(v3_shadow.get("v3_robust_available"))
    v3_robust_affects_bid = bool(v3_shadow.get("v3_robust_affects_bid"))
    v3_robust_status = v3_shadow.get("v3_robust_status")
    v3_robust_prior_usable = bool(v3_shadow.get("v3_robust_prior_usable"))
    v3_robust_prior_trusted = bool(v3_shadow.get("v3_robust_prior_trusted"))
    v3_robust_fallback_mode = v3_shadow.get("v3_robust_fallback_mode")
    v3_robust_activity_candidate = bool(
        v3_shadow.get("v3_robust_activity_candidate")
    )
    v3_robust_prior_stress_score = _parse_int_text(
        v3_shadow.get("v3_robust_prior_stress_score")
    )
    v3_robust_reasons = v3_shadow.get("v3_robust_reasons")
    v3_ccv_available = bool(v3_shadow.get("v3_ccv_available"))
    v3_ccv_ready = bool(v3_shadow.get("v3_ccv_ready"))
    v3_ccv_affects_bid = bool(v3_shadow.get("v3_ccv_affects_bid"))
    v3_ccv_match_scope = v3_shadow.get("v3_ccv_match_scope")
    v3_ccv_n_matched = _parse_int_text(v3_shadow.get("v3_ccv_n_matched"))
    v3_ccv_q6_present_rate = _parse_float_text(
        v3_shadow.get("v3_ccv_q6_present_rate")
    )
    v3_ccv_q6_count_p50 = _parse_int_text(v3_shadow.get("v3_ccv_q6_count_p50"))
    v3_ccv_q6_count_p90 = _parse_int_text(v3_shadow.get("v3_ccv_q6_count_p90"))
    v3_ccv_q6_cells_p50 = _parse_int_text(v3_shadow.get("v3_ccv_q6_cells_p50"))
    v3_ccv_q6_cells_p90 = _parse_int_text(v3_shadow.get("v3_ccv_q6_cells_p90"))
    v3_ccv_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_ccv_q6_formal_decision_value_p50")
    )
    v3_ccv_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_ccv_q6_formal_decision_value_p90")
    )
    v3_resid_available = bool(v3_shadow.get("v3_resid_available"))
    v3_resid_ready = bool(v3_shadow.get("v3_resid_ready"))
    v3_resid_affects_bid = bool(v3_shadow.get("v3_resid_affects_bid"))
    v3_resid_match_scope = v3_shadow.get("v3_resid_match_scope")
    v3_resid_n_matched = _parse_int_text(v3_shadow.get("v3_resid_n_matched"))
    v3_resid_q6_present_rate = _parse_float_text(
        v3_shadow.get("v3_resid_q6_present_rate")
    )
    v3_resid_q6_count_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_count_p50")
    )
    v3_resid_q6_count_p90 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_count_p90")
    )
    v3_resid_q6_cells_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_cells_p50")
    )
    v3_resid_q6_cells_p90 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_cells_p90")
    )
    v3_resid_q6_value_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_value_p50")
    )
    v3_resid_q6_value_p90 = _parse_int_text(
        v3_shadow.get("v3_resid_q6_value_p90")
    )
    v3_resid_gate_available = bool(v3_shadow.get("v3_resid_gate_available"))
    v3_resid_gate_ready = bool(v3_shadow.get("v3_resid_gate_ready"))
    v3_resid_gate_affects_bid = bool(
        v3_shadow.get("v3_resid_gate_affects_bid")
    )
    v3_resid_gate_active = bool(v3_shadow.get("v3_resid_gate_active"))
    v3_resid_gate_status = v3_shadow.get("v3_resid_gate_status")
    v3_resid_gate_gate_reason = v3_shadow.get("v3_resid_gate_gate_reason")
    v3_resid_gate_source = v3_shadow.get("v3_resid_gate_source")
    v3_resid_gate_q6_count_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_gate_q6_count_p50")
    )
    v3_resid_gate_q6_cells_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_gate_q6_cells_p50")
    )
    v3_resid_gate_q6_value_p50 = _parse_int_text(
        v3_shadow.get("v3_resid_gate_q6_value_p50")
    )
    v3_resid_gate_q6_count_delta_p50 = _parse_float_text(
        v3_shadow.get("v3_resid_gate_q6_count_delta_p50")
    )
    v3_resid_gate_q6_cells_delta_p50 = _parse_float_text(
        v3_shadow.get("v3_resid_gate_q6_cells_delta_p50")
    )
    v3_resid_gate_q6_value_delta_p50 = _parse_float_text(
        v3_shadow.get("v3_resid_gate_q6_value_delta_p50")
    )
    v3_cal_available = bool(v3_shadow.get("v3_cal_available"))
    v3_cal_ready = bool(v3_shadow.get("v3_cal_ready"))
    v3_cal_active = bool(v3_shadow.get("v3_cal_active"))
    v3_cal_affects_bid = bool(v3_shadow.get("v3_cal_affects_bid"))
    v3_cal_status = v3_shadow.get("v3_cal_status")
    v3_cal_gate_reason = v3_shadow.get("v3_cal_gate_reason")
    v3_cal_scale = _parse_float_text(v3_shadow.get("v3_cal_scale"))
    v3_cal_archive_sessions = _parse_int_text(
        v3_shadow.get("v3_cal_archive_sessions")
    )
    v3_cal_median_ratio = _parse_float_text(v3_shadow.get("v3_cal_median_ratio"))
    v3_cal_p90_ratio = _parse_float_text(v3_shadow.get("v3_cal_p90_ratio"))
    v3_cal_baseline_formal_mae = _parse_float_text(
        v3_shadow.get("v3_cal_baseline_formal_p50_mae")
    )
    v3_cal_baseline_formal_bias = _parse_float_text(
        v3_shadow.get("v3_cal_baseline_formal_p50_bias")
    )
    v3_cal_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_cal_formal_decision_value_p50")
    )
    v3_cal_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_cal_formal_decision_value_p90")
    )
    v3_cal_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_cal_q6_formal_decision_value_p50")
    )
    v3_cal_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_cal_q6_formal_decision_value_p90")
    )
    v3_under_available = bool(v3_shadow.get("v3_under_available"))
    v3_under_ready = bool(v3_shadow.get("v3_under_ready"))
    v3_under_active = bool(v3_shadow.get("v3_under_active"))
    v3_under_candidate = bool(v3_shadow.get("v3_under_candidate"))
    v3_under_affects_bid = bool(v3_shadow.get("v3_under_affects_bid"))
    v3_under_status = v3_shadow.get("v3_under_status")
    v3_under_gate_reason = v3_shadow.get("v3_under_gate_reason")
    v3_under_scale = _parse_float_text(v3_shadow.get("v3_under_scale"))
    v3_under_hero_map_id = v3_shadow.get("v3_under_hero_map_id")
    v3_under_source = v3_shadow.get("v3_under_source")
    v3_under_archive_sessions = _parse_int_text(
        v3_shadow.get("v3_under_archive_sessions")
    )
    v3_under_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_under_formal_decision_value_p50")
    )
    v3_under_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_under_formal_decision_value_p90")
    )
    v3_under_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_under_q6_formal_decision_value_p50")
    )
    v3_under_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_under_q6_formal_decision_value_p90")
    )
    v3_tail_review_available = bool(v3_shadow.get("v3_tail_review_available"))
    v3_tail_review_ready = bool(v3_shadow.get("v3_tail_review_ready"))
    v3_tail_review_active = bool(v3_shadow.get("v3_tail_review_active"))
    v3_tail_review_candidate = bool(v3_shadow.get("v3_tail_review_candidate"))
    v3_tail_review_hurt_guard = bool(v3_shadow.get("v3_tail_review_hurt_guard"))
    v3_tail_review_affects_bid = bool(
        v3_shadow.get("v3_tail_review_affects_bid")
    )
    v3_tail_review_status = v3_shadow.get("v3_tail_review_status")
    v3_tail_review_gate_reason = v3_shadow.get("v3_tail_review_gate_reason")
    v3_tail_review_hero_map_id = v3_shadow.get("v3_tail_review_hero_map_id")
    v3_tail_review_source = v3_shadow.get("v3_tail_review_source")
    v3_tail_review_archive_sessions = _parse_int_text(
        v3_shadow.get("v3_tail_review_archive_sessions")
    )
    v3_tail_review_tail_delta = _parse_float_text(
        v3_shadow.get("v3_tail_review_tail_delta_p50_mae")
    )
    v3_tail_review_q6_tail_delta = _parse_float_text(
        v3_shadow.get("v3_tail_review_q6_tail_delta_p50_mae")
    )
    v3_tail_review_tail_p50 = _parse_int_text(
        v3_shadow.get("v3_tail_review_tail_replacement_decision_value_p50")
    )
    v3_tail_review_tail_p90 = _parse_int_text(
        v3_shadow.get("v3_tail_review_tail_replacement_decision_value_p90")
    )
    v3_tail_review_q6_tail_p50 = _parse_int_text(
        v3_shadow.get("v3_tail_review_q6_tail_replacement_decision_value_p50")
    )
    v3_tail_review_q6_tail_p90 = _parse_int_text(
        v3_shadow.get("v3_tail_review_q6_tail_replacement_decision_value_p90")
    )
    v3_fv_available = bool(v3_shadow.get("v3_fv_available"))
    v3_fv_ready = bool(v3_shadow.get("v3_fv_ready"))
    v3_fv_active = bool(v3_shadow.get("v3_fv_active"))
    v3_fv_candidate = bool(v3_shadow.get("v3_fv_candidate"))
    v3_fv_affects_bid = bool(v3_shadow.get("v3_fv_affects_bid"))
    v3_fv_status = v3_shadow.get("v3_fv_status")
    v3_fv_gate_reason = v3_shadow.get("v3_fv_gate_reason")
    v3_fv_source = v3_shadow.get("v3_fv_source")
    v3_fv_stress_class = v3_shadow.get("v3_fv_stress_class")
    v3_fv_capacity_flags = v3_shadow.get("v3_fv_capacity_flags")
    v3_fv_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_fv_formal_decision_value_p50")
    )
    v3_fv_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_fv_formal_decision_value_p90")
    )
    v3_fv_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_fv_q6_formal_decision_value_p50")
    )
    v3_fv_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_fv_q6_formal_decision_value_p90")
    )
    v3_scp_available = bool(v3_shadow.get("v3_scp_available"))
    v3_scp_ready = bool(v3_shadow.get("v3_scp_ready"))
    v3_scp_affects_bid = bool(v3_shadow.get("v3_scp_affects_bid"))
    v3_scp_active = bool(v3_shadow.get("v3_scp_active"))
    v3_scp_candidate = bool(v3_shadow.get("v3_scp_candidate"))
    v3_scp_missing_table = bool(v3_shadow.get("v3_scp_missing_table"))
    v3_scp_status = v3_shadow.get("v3_scp_status")
    v3_scp_gate_reason = v3_shadow.get("v3_scp_gate_reason")
    v3_scp_scope = v3_shadow.get("v3_scp_scope")
    v3_scp_group = v3_shadow.get("v3_scp_group")
    v3_scp_inventory_p95 = _parse_float_text(
        v3_shadow.get("v3_scp_inventory_count_p95")
    )
    v3_scp_inventory_max = _parse_float_text(
        v3_shadow.get("v3_scp_inventory_count_max")
    )
    v3_scp_non_temp_p95 = _parse_float_text(
        v3_shadow.get("v3_scp_non_temp_inventory_count_p95")
    )
    v3_scp_non_temp_max = _parse_float_text(
        v3_shadow.get("v3_scp_non_temp_inventory_count_max")
    )
    v3_scp_above_drop_after_temp = _parse_int_text(
        v3_shadow.get("v3_scp_above_drop_after_temp_zodiac_rows")
    )
    v3_scp_above_round_after_temp = _parse_int_text(
        v3_shadow.get("v3_scp_above_round_after_temp_zodiac_rows")
    )
    v3_scp_prior_p95_delta = _parse_float_text(
        v3_shadow.get("v3_scp_prior_max_to_observed_p95_delta")
    )
    v3_scp_prior_max_delta = _parse_float_text(
        v3_shadow.get("v3_scp_prior_max_to_observed_max_delta")
    )
    v3_scp_flags = v3_shadow.get("v3_scp_flags")
    v3_cse_available = bool(v3_shadow.get("v3_cse_available"))
    v3_cse_ready = bool(v3_shadow.get("v3_cse_ready"))
    v3_cse_affects_bid = bool(v3_shadow.get("v3_cse_affects_bid"))
    v3_cse_active = bool(v3_shadow.get("v3_cse_active"))
    v3_cse_candidate = bool(v3_shadow.get("v3_cse_candidate"))
    v3_cse_pressure_candidate = bool(
        v3_shadow.get("v3_cse_pressure_candidate")
    )
    v3_cse_status = v3_shadow.get("v3_cse_status")
    v3_cse_gate_reason = v3_shadow.get("v3_cse_gate_reason")
    v3_cse_scope = v3_shadow.get("v3_cse_scope")
    v3_cse_group = v3_shadow.get("v3_cse_group")
    v3_cse_source_context_classes = v3_shadow.get(
        "v3_cse_source_context_classes"
    )
    v3_cse_unique_round_rows = _parse_int_text(
        v3_shadow.get("v3_cse_unique_round_overflow_rows")
    )
    v3_cse_server_rows = _parse_int_text(
        v3_shadow.get("v3_cse_server_side_expansion_rows")
    )
    v3_cse_session_rows = _parse_int_text(
        v3_shadow.get("v3_cse_session_capacity_source_semantics_rows")
    )
    v3_cse_public_rows = _parse_int_text(
        v3_shadow.get("v3_cse_public_total_match_rows")
    )
    v3_cse_full_action_rows = _parse_int_text(
        v3_shadow.get("v3_cse_full_action_rows")
    )
    v3_cse_unique_p95 = _parse_float_text(
        v3_shadow.get("v3_cse_unique_non_temp_p95")
    )
    v3_cse_unique_max = _parse_float_text(
        v3_shadow.get("v3_cse_unique_non_temp_max")
    )
    v3_cse_target_count_source = v3_shadow.get("v3_cse_target_count_source")
    v3_cse_target_count = _parse_float_text(
        v3_shadow.get("v3_cse_target_count")
    )
    v3_cse_prior_items_per_session_max = _parse_float_text(
        v3_shadow.get("v3_cse_prior_items_per_session_max")
    )
    v3_cse_target_p95_delta = _parse_float_text(
        v3_shadow.get("v3_cse_target_to_unique_non_temp_p95_delta")
    )
    v3_cse_prior_p95_delta = _parse_float_text(
        v3_shadow.get("v3_cse_prior_max_to_unique_non_temp_p95_delta")
    )
    v3_cse_target_prior_delta = _parse_float_text(
        v3_shadow.get("v3_cse_target_prior_max_delta")
    )
    v3_cse_prior_max_delta = _parse_float_text(
        v3_shadow.get("v3_cse_prior_max_to_unique_non_temp_max_delta")
    )
    v3_cse_flags = v3_shadow.get("v3_cse_flags")
    v3_practical_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_practical_formal_decision_value_p50")
    )
    v3_practical_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_practical_formal_decision_value_p90")
    )
    v3_practical_q6_formal_p50 = _parse_int_text(
        v3_shadow.get("v3_practical_q6_formal_decision_value_p50")
    )
    v3_practical_q6_formal_p90 = _parse_int_text(
        v3_shadow.get("v3_practical_q6_formal_decision_value_p90")
    )
    posterior_samples = None
    posterior_total_samples = None
    q6_shadow_active = bool(shadow.get("active"))
    q6_shadow_active_boost = _parse_float_text(shadow.get("active_boost"))
    q6_shadow_decision_value_p50 = _parse_int_text(
        shadow.get("decision_value_p50")
    )
    q6_shadow_decision_value_p90 = _parse_int_text(
        shadow.get("decision_value_p90")
    )
    q6_shadow_q6_decision_value_p90 = _parse_int_text(
        shadow.get("q6_decision_value_p90")
    )
    q6_shadow_q6_count_p90 = _parse_int_text(shadow.get("q6_count_p90"))
    q6_shadow_q6_cells_p90 = _parse_int_text(shadow.get("q6_cells_p90"))
    anchor_count = None
    posterior_diagnostics = ""
    if warehouse_rows:
        warehouse_p50 = _parse_range_p50(
            str(warehouse_rows[0].get("总格 P10/P50/P90", ""))
        )
        value_p50 = _parse_range_p50(
            str(warehouse_rows[0].get("价值 P10/P50/P90", ""))
        )
    if bid_rows:
        decision_value_p50 = _parse_range_p50(
            str(bid_rows[0].get("决策价值 P10/P50/P90", ""))
        )
        decision_value_p90 = _parse_range_value(
            str(bid_rows[0].get("决策价值 P10/P50/P90", "")),
            2,
        )
        raw_value_p50 = _parse_range_p50(
            str(bid_rows[0].get("原始价值 P10/P50/P90", ""))
        )
        raw_value_p90 = _parse_range_value(
            str(bid_rows[0].get("原始价值 P10/P50/P90", "")),
            2,
        )
    if v2_rows:
        posterior_samples, posterior_total_samples = _parse_match_text(
            v2_rows[0].get("匹配")
        )
        if decision_value_p50 is None:
            decision_value_p50 = _parse_range_p50(
                str(v2_rows[0].get("决策价值 P10/P50/P90", ""))
            )
            decision_value_p90 = _parse_range_value(
                str(v2_rows[0].get("决策价值 P10/P50/P90", "")),
                2,
            )
        if raw_value_p50 is None:
            raw_value_p50 = _parse_range_p50(
                str(v2_rows[0].get("原始价值 P10/P50/P90", ""))
            )
            raw_value_p90 = _parse_range_value(
                str(v2_rows[0].get("原始价值 P10/P50/P90", "")),
                2,
            )
        q6_match_rate = _parse_percent_text(v2_rows[0].get("q6样本率"))
        q6_prior_match_rate = _parse_percent_text(v2_rows[0].get("q6掉落先验"))
        q6_prior_expected_count = _parse_float_text(v2_rows[0].get("q6先验件数"))
        q6_prior_expected_cells = _parse_float_text(v2_rows[0].get("q6先验格数"))
        q6_prior_expected_value = _parse_int_text(v2_rows[0].get("q6先验价值"))
        prior_expected_count = _parse_float_text(v2_rows[0].get("先验件数"))
        prior_expected_cells = _parse_float_text(v2_rows[0].get("先验格数"))
        prior_expected_value = _parse_int_text(v2_rows[0].get("先验原始价值"))
        prior_expected_decision_value = _parse_int_text(
            v2_rows[0].get("先验决策价值")
        )
        prior_expected_tail_replacement_decision_value = _parse_int_text(
            v2_rows[0].get("先验替代决策价值")
        )
        shape_target_count = _parse_int_text(v2_rows[0].get("形状约束数"))
        category_target_count = _parse_int_text(v2_rows[0].get("分类约束数"))
        category_exclusion_count = _parse_int_text(v2_rows[0].get("分类反排数"))
        random_sample_avg_values = str(v2_rows[0].get("随机样本均价") or "")
        random_sample_avg_signal_values = str(
            v2_rows[0].get("随机样本均价信号") or ""
        )
        q6_value_p90 = _parse_range_value(
            str(v2_rows[0].get("q6价值 P10/P50/P90", "")),
            2,
        )
        q6_decision_value_p90 = _parse_range_value(
            str(v2_rows[0].get("q6决策价值 P10/P50/P90", "")),
            2,
        )
        q6_tail_replacement_estimate_p90 = _parse_range_value(
            str(v2_rows[0].get("q6替代决策价值 P10/P50/P90", "")),
            2,
        )
        q6_count_p90 = _parse_range_value(
            str(v2_rows[0].get("q6件数 P10/P50/P90", "")),
            2,
        )
        q6_cells_p90 = _parse_range_value(
            str(v2_rows[0].get("q6格数 P10/P50/P90", "")),
            2,
        )
        remaining_label = str(v2_rows[0].get("剩余空间 P10/P50/P90", ""))
        remaining_cells_after_layout_p10 = _parse_range_value(remaining_label, 0)
        remaining_cells_after_layout_p50 = _parse_range_value(remaining_label, 1)
        remaining_cells_after_layout_p90 = _parse_range_value(remaining_label, 2)
        pressure_label = str(v2_rows[0].get("q6空间压力 P10/P50/P90", ""))
        q6_space_pressure_p50 = _parse_range_float_value(pressure_label, 1)
        q6_space_pressure_p90 = _parse_range_float_value(pressure_label, 2)
        q6_space_overflow_rate = _parse_percent_text(
            v2_rows[0].get("q6空间溢出率")
        )
        q6_prior_gap_summary = str(v2_rows[0].get("q6先验缺口") or "")
        q6_prior_floor_value = _parse_int_text(v2_rows[0].get("q6先验风险参考"))
        q6_practical_gate = str(v2_rows[0].get("q6实战门控") or "")
        q6_practical_p90 = _parse_int_text(v2_rows[0].get("q6实战参考P90"))
        anchor_count = _parse_int_text(v2_rows[0].get("锚点数"))
        posterior_diagnostics = str(v2_rows[0].get("诊断") or "")
    else:
        shape_target_count = None
        category_target_count = None
        category_exclusion_count = None
        anchor_count = None
        random_sample_avg_values = ""
        random_sample_avg_signal_values = ""
    layout_root = layout_conflict_root(posterior_diagnostics)
    latest_layout_fit = next(
        (
            row for row in reversed(layout_rows)
            if row.get("最终格") and row.get("样本拟合估计")
        ),
        {},
    )
    layout_p50 = _parse_range_p50(str(latest_layout_fit.get("样本拟合估计", "")))
    stop_bid = None
    attack_bid = None
    highest_bid = None
    if bid_rows:
        row = bid_rows[0]
        stop_bid = _parse_int_text(row.get("停止价"))
        attack_bid = _parse_int_text(row.get("抢仓上限"))
        current = str(row.get("当前最高", ""))
        highest_bid = _parse_int_text(current.split(" ")[-1] if current else None)
    final_q6_value = int((truth_breakdown or {}).get("final_q6_value") or 0)
    final_q6_decision_value = int(
        (
            (truth_breakdown or {}).get("final_q6_decision_value")
            if (truth_breakdown or {}).get("final_q6_decision_value") is not None
            else final_q6_value
        )
        or 0
    )
    final_q6_decision_value_with_tail_replacement = int(
        (
            (truth_breakdown or {}).get(
                "final_q6_decision_value_with_tail_replacement"
            )
            if (truth_breakdown or {}).get(
                "final_q6_decision_value_with_tail_replacement"
            )
            is not None
            else final_q6_decision_value
        )
        or 0
    )
    final_q6_tail_replacement_value = int(
        (truth_breakdown or {}).get("final_q6_tail_replacement_value") or 0
    )
    no_plannable_q6_control = final_q6_decision_value <= 0
    zero_q6_proven_control = (
        no_plannable_q6_control
        and _zero_q6_proven_from_diagnostics(posterior_diagnostics)
    )
    q6_shadow_under_before = (
        q6_shadow_active
        and q6_decision_value_p90 is not None
        and final_q6_decision_value > q6_decision_value_p90
    )
    q6_shadow_covered_after = (
        q6_shadow_active
        and q6_shadow_q6_decision_value_p90 is not None
        and final_q6_decision_value <= q6_shadow_q6_decision_value_p90
    )
    q6_practical_gate_hit = bool(q6_practical_gate)
    q6_practical_under_before = (
        q6_practical_gate_hit
        and q6_decision_value_p90 is not None
        and final_q6_decision_value > q6_decision_value_p90
    )
    q6_practical_covered_after = (
        q6_practical_gate_hit
        and q6_practical_p90 is not None
        and final_q6_decision_value <= q6_practical_p90
    )
    deep_floor_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_deep_floor_shadow",
        deep_floor_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    deep11_floor_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_deep11_floor_shadow",
        deep11_floor_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    hidden_floor_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_hidden_floor_shadow",
        hidden_floor_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    villa_floor_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_villa_floor_shadow",
        villa_floor_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    ethan_villa_random_floor_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_ethan_villa_random_floor_shadow",
        ethan_villa_random_floor_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    ethan_shipwreck_layout_conditional_shadow_fields = _model_eval_shadow_fields(
        "q6_residual_ethan_shipwreck_layout_conditional_shadow",
        ethan_shipwreck_layout_conditional_shadow,
        baseline_q6_decision_value_p90=q6_decision_value_p90,
        final_q6_decision_value=final_q6_decision_value,
        zero_q6_proven_control=zero_q6_proven_control,
    )
    eval_round = artifact.get("observed_round", artifact.get("round"))
    action_round = artifact.get("action_round", artifact.get("round"))
    truth_breakdown = truth_breakdown or {}
    has_formal_decision_truth = truth_breakdown.get("final_decision_value") is not None
    has_replacement_decision_truth = (
        truth_breakdown.get("final_decision_value_with_tail_replacement") is not None
    )
    final_formal_decision_value = (
        truth_breakdown.get("final_decision_value")
        if has_formal_decision_truth
        else None
    )
    final_replacement_decision_value = (
        truth_breakdown.get("final_decision_value_with_tail_replacement")
        if has_replacement_decision_truth
        else final_formal_decision_value
        if has_formal_decision_truth
        else final_value
    )
    if has_replacement_decision_truth and (
        not has_formal_decision_truth
        or final_replacement_decision_value != final_formal_decision_value
    ):
        decision_value_truth_source = "tail_replacement"
    elif has_formal_decision_truth:
        decision_value_truth_source = "formal"
    else:
        decision_value_truth_source = "raw"
    evidence_stage = _evidence_stage(eval_round)
    density_score = _live_information_density_score(
        eval_round,
        anchor_count=anchor_count,
        shape_target_count=shape_target_count,
        category_target_count=category_target_count,
        category_exclusion_count=category_exclusion_count,
    )
    density_band = _information_density_band(density_score)
    public_constraint_key = _public_constraint_key(posterior_diagnostics)
    evidence_profile_key = str(
        artifact.get("evidence_profile_key")
        or _live_evidence_profile_key(
            public_constraint_key=public_constraint_key,
            random_sample_avg_values=random_sample_avg_signal_values,
            category_target_count=category_target_count,
            category_exclusion_count=category_exclusion_count,
            shape_target_count=shape_target_count,
        )
    )
    return {
        "ts": time.time(),
        "file": file,
        "hero": artifact.get("hero"),
        "map_id": artifact.get("map_id"),
        "round": eval_round,
        "action_round": action_round,
        "formal_mode_requested": artifact.get("formal_mode_requested"),
        "formal_mode": artifact.get("formal_mode"),
        "formal_mode_reason": artifact.get("formal_mode_reason"),
        "formal_baseline_source": artifact.get("formal_baseline_source"),
        "v3_practical_live_guard": bid_row.get("v3_practical_live_guard"),
        "v3_practical_live_guard_reason": bid_row.get(
            "v3_practical_live_guard_reason"
        ),
        "v3_practical_unguarded_decision_value": bid_row.get(
            "v3_practical_unguarded_decision_value"
        ),
        "v3_practical_unguarded_decision_value_p10": (
            v3_practical_unguarded_p10
        ),
        "v3_practical_unguarded_decision_value_p50": (
            v3_practical_unguarded_p50
        ),
        "v3_practical_unguarded_decision_value_p90": (
            v3_practical_unguarded_p90
        ),
        "final_value": final_value,
        "final_cells": final_cells,
        **dict(truth_breakdown),
        "value_p50": value_p50,
        "decision_value_p50": decision_value_p50,
        "decision_value_p90": decision_value_p90,
        "decision_value_truth": final_replacement_decision_value,
        "decision_value_truth_source": decision_value_truth_source,
        "decision_value_p50_error": (
            decision_value_p50 - final_replacement_decision_value
            if decision_value_p50 is not None
            and final_replacement_decision_value is not None
            else None
        ),
        "decision_value_p90_error": (
            decision_value_p90 - final_replacement_decision_value
            if decision_value_p90 is not None
            and final_replacement_decision_value is not None
            else None
        ),
        "decision_value_p50_error_vs_formal": (
            decision_value_p50 - final_formal_decision_value
            if decision_value_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "decision_value_p90_error_vs_formal": (
            decision_value_p90 - final_formal_decision_value
            if decision_value_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "decision_value_p50_error_vs_raw": (
            decision_value_p50 - final_value
            if decision_value_p50 is not None and final_value is not None
            else None
        ),
        "decision_value_p90_error_vs_raw": (
            decision_value_p90 - final_value
            if decision_value_p90 is not None and final_value is not None
            else None
        ),
        "raw_value_p50": raw_value_p50,
        "raw_value_p90": raw_value_p90,
        "raw_minus_decision_p90": (
            raw_value_p90 - decision_value_p90
            if raw_value_p90 is not None and decision_value_p90 is not None
            else None
        ),
        "value_p50_error": (
            value_p50 - final_value
            if value_p50 is not None and final_value is not None
            else None
        ),
        "warehouse_p50": warehouse_p50,
        "warehouse_p50_error": (
            warehouse_p50 - final_cells
            if warehouse_p50 is not None and final_cells is not None
            else None
        ),
        "layout_fit_p50": layout_p50,
        "layout_fit_p50_error": (
            layout_p50 - final_cells
            if layout_p50 is not None and final_cells is not None
            else None
        ),
        "highest_bid": highest_bid,
        "attack_bid": attack_bid,
        "stop_bid": stop_bid,
        "v2_q6_match_rate": q6_match_rate,
        "v2_q6_prior_match_rate": q6_prior_match_rate,
        "v2_q6_prior_expected_count": q6_prior_expected_count,
        "v2_q6_prior_expected_cells": q6_prior_expected_cells,
        "v2_q6_prior_expected_value": q6_prior_expected_value,
        "v2_prior_expected_count": prior_expected_count,
        "v2_prior_expected_cells": prior_expected_cells,
        "v2_prior_expected_value": prior_expected_value,
        "v2_prior_expected_decision_value": prior_expected_decision_value,
        "v2_prior_expected_tail_replacement_decision_value": (
            prior_expected_tail_replacement_decision_value
        ),
        "v2_q6_value_p90": q6_value_p90,
        "v2_q6_decision_value_p90": q6_decision_value_p90,
        "v2_q6_tail_replacement_estimate_p90": q6_tail_replacement_estimate_p90,
        "v2_q6_count_p90": q6_count_p90,
        "v2_q6_cells_p90": q6_cells_p90,
        "v2_remaining_cells_after_layout_p10": remaining_cells_after_layout_p10,
        "v2_remaining_cells_after_layout_p50": remaining_cells_after_layout_p50,
        "v2_remaining_cells_after_layout_p90": remaining_cells_after_layout_p90,
        "v2_q6_space_pressure_p50": q6_space_pressure_p50,
        "v2_q6_space_pressure_p90": q6_space_pressure_p90,
        "v2_q6_space_overflow_rate": q6_space_overflow_rate,
        "v2_q6_count_p90_under_prior_by": (
            max(0.0, q6_prior_expected_count - q6_count_p90)
            if q6_prior_expected_count is not None
            and q6_count_p90 is not None
            else None
        ),
        "v2_q6_cells_p90_under_prior_by": (
            max(0.0, q6_prior_expected_cells - q6_cells_p90)
            if q6_prior_expected_cells is not None
            and q6_cells_p90 is not None
            else None
        ),
        "q6_count_cell_prior_risk": bool(q6_prior_gap_summary),
        "q6_count_cell_prior_gap": q6_prior_gap_summary,
        "q6_count_cell_prior_floor_value": q6_prior_floor_value,
        "q6_practical_gate": q6_practical_gate,
        "q6_practical_p90": q6_practical_p90,
        "q6_practical_gate_hit": q6_practical_gate_hit,
        "q6_no_plannable_control": no_plannable_q6_control,
        "q6_zero_q6_proven_control": zero_q6_proven_control,
        "q6_practical_gate_no_plannable_control": (
            q6_practical_gate_hit and no_plannable_q6_control
        ),
        "q6_practical_gate_no_plannable_positive_proxy": (
            q6_practical_gate_hit
            and no_plannable_q6_control
            and (q6_practical_p90 or 0) > 0
        ),
        "q6_practical_gate_zero_q6_proven_false_positive": (
            q6_practical_gate_hit
            and zero_q6_proven_control
            and (q6_practical_p90 or 0) > 0
        ),
        "q6_practical_gate_false_positive_proxy": (
            q6_practical_gate_hit
            and no_plannable_q6_control
            and (q6_practical_p90 or 0) > 0
        ),
        "q6_practical_gate_under_before": q6_practical_under_before,
        "q6_practical_gate_covered_after": q6_practical_covered_after,
        "q6_practical_gate_helped": (
            q6_practical_under_before and q6_practical_covered_after
        ),
        "q6_practical_p90_under_by": (
            max(0, final_q6_decision_value - q6_practical_p90)
            if q6_practical_p90 is not None
            else None
        ),
        "q6_formal_prior_floor_label": formal_prior_floor.get("label"),
        "q6_formal_prior_floor_gate": formal_prior_floor.get("gate"),
        "q6_formal_prior_floor_evidence_profile": formal_prior_floor.get(
            "evidence_profile_key"
        ),
        "q6_formal_prior_floor_active": bool(formal_prior_floor.get("active")),
        "q6_formal_prior_floor_active_prior_floor_ratio": _parse_float_text(
            formal_prior_floor.get("active_prior_floor_ratio")
        ),
        "v3_post_shadow_label": v3_shadow.get("label"),
        "v3_post_shadow_error": v3_shadow.get("error"),
        "v3_post_shadow_trials": _parse_int_text(v3_shadow.get("trials")),
        "v3_post_available": v3_post_available,
        "v3_post_ready": v3_post_ready,
        "v3_post_strict_ready": v3_post_strict_ready,
        "v3_post_affects_bid": v3_post_affects_bid,
        "v3_post_match_scope": v3_post_match_scope,
        "v3_post_n_total": v3_post_n_total,
        "v3_post_n_matched": v3_post_n_matched,
        "v3_post_n_strict_matched": v3_post_n_strict_matched,
        "v3_post_match_rate": v3_post_match_rate,
        "v3_post_strict_match_rate": v3_post_strict_match_rate,
        "v3_post_q6_present_rate": v3_post_q6_present_rate,
        "v3_post_numeric_constraints": _parse_int_text(
            v3_shadow.get("numeric_constraints")
        ),
        "v3_post_item_anchors": _parse_int_text(v3_shadow.get("item_anchors")),
        "v3_post_shape_anchors": _parse_int_text(v3_shadow.get("shape_anchors")),
        "v3_post_quality_floor_anchors": _parse_int_text(
            v3_shadow.get("quality_floor_anchors")
        ),
        "v3_post_conflicts": _parse_int_text(v3_shadow.get("conflicts")),
        "v3_summary_feasible": bool(v3_shadow.get("v3_summary_feasible")),
        "v3_summary_known_count_floor": v3_summary_known_count_floor,
        "v3_summary_known_cells_floor": v3_summary_known_cells_floor,
        "v3_summary_known_value_floor": v3_summary_known_value_floor,
        "v3_summary_session_total_count_exact": (
            v3_summary_session_total_count_exact
        ),
        "v3_summary_session_total_cells_exact": (
            v3_summary_session_total_cells_exact
        ),
        "v3_summary_q6_count_floor": v3_summary_q6_count_floor,
        "v3_summary_q6_cells_floor": v3_summary_q6_cells_floor,
        "v3_summary_q6_value_floor": v3_summary_q6_value_floor,
        "v3_post_formal_decision_value_p50": v3_post_formal_p50,
        "v3_post_formal_decision_value_p90": v3_post_formal_p90,
        "v3_post_tail_replacement_decision_value_p50": (
            v3_post_tail_replacement_p50
        ),
        "v3_post_tail_replacement_decision_value_p90": (
            v3_post_tail_replacement_p90
        ),
        "v3_post_q6_formal_decision_value_p50": v3_post_q6_formal_p50,
        "v3_post_q6_formal_decision_value_p90": v3_post_q6_formal_p90,
        "v3_post_q6_count_p50": v3_post_q6_count_p50,
        "v3_post_q6_count_p90": v3_post_q6_count_p90,
        "v3_post_q6_cells_p50": v3_post_q6_cells_p50,
        "v3_post_q6_cells_p90": v3_post_q6_cells_p90,
        "v3_post_total_cells_p50": v3_post_total_cells_p50,
        "v3_post_total_cells_p90": v3_post_total_cells_p90,
        "v3_post_diagnostics": v3_shadow.get("v3_post_diagnostics"),
        "v3_prior_available": v3_prior_available,
        "v3_prior_error": v3_prior_error,
        "v3_prior_map_id": v3_prior_map_id,
        "v3_prior_map_name": v3_prior_map_name,
        "v3_prior_items_per_session_min": v3_prior_items_per_session_min,
        "v3_prior_items_per_session_max": v3_prior_items_per_session_max,
        "v3_capacity_total_count_source": v3_capacity_total_count_source,
        "v3_capacity_total_count_target": v3_capacity_total_count_target,
        "v3_capacity_truth_item_count": v3_capacity_truth_item_count,
        "v3_capacity_prior_items_per_session_min": v3_prior_items_per_session_min,
        "v3_capacity_prior_items_per_session_max": v3_prior_items_per_session_max,
        "v3_capacity_target_prior_max_delta": v3_capacity_target_prior_max_delta,
        "v3_capacity_truth_prior_max_delta": v3_capacity_truth_prior_max_delta,
        "v3_capacity_target_truth_delta": v3_capacity_target_truth_delta,
        "v3_capacity_target_prior_max_ratio": _numeric_ratio(
            v3_capacity_total_count_target,
            v3_prior_items_per_session_max,
        ),
        "v3_capacity_truth_prior_max_ratio": _numeric_ratio(
            v3_capacity_truth_item_count,
            v3_prior_items_per_session_max,
        ),
        "v3_capacity_flags": "+".join(v3_capacity_flags),
        "v3_capacity_cases": "+".join(v3_capacity_cases),
        "v3_prior_pool_count": v3_prior_pool_count,
        "v3_prior_expected_draws": v3_prior_expected_draws,
        "v3_prior_expected_count": v3_prior_expected_count,
        "v3_prior_expected_cells": v3_prior_expected_cells,
        "v3_prior_expected_value": v3_prior_expected_value,
        "v3_prior_q6_draw_probability": v3_prior_q6_draw_probability,
        "v3_prior_q6_session_probability": v3_prior_q6_session_probability,
        "v3_prior_q6_expected_count": v3_prior_q6_expected_count,
        "v3_prior_q6_expected_cells": v3_prior_q6_expected_cells,
        "v3_prior_q6_expected_value": v3_prior_q6_expected_value,
        "v3_robust_available": v3_robust_available,
        "v3_robust_affects_bid": v3_robust_affects_bid,
        "v3_robust_status": v3_robust_status,
        "v3_robust_prior_usable": v3_robust_prior_usable,
        "v3_robust_prior_trusted": v3_robust_prior_trusted,
        "v3_robust_fallback_mode": v3_robust_fallback_mode,
        "v3_robust_activity_candidate": v3_robust_activity_candidate,
        "v3_robust_prior_stress_score": v3_robust_prior_stress_score,
        "v3_robust_reasons": v3_robust_reasons,
        "v3_ccv_available": v3_ccv_available,
        "v3_ccv_ready": v3_ccv_ready,
        "v3_ccv_affects_bid": v3_ccv_affects_bid,
        "v3_ccv_match_scope": v3_ccv_match_scope,
        "v3_ccv_n_matched": v3_ccv_n_matched,
        "v3_ccv_q6_present_rate": v3_ccv_q6_present_rate,
        "v3_ccv_q6_count_p50": v3_ccv_q6_count_p50,
        "v3_ccv_q6_count_p90": v3_ccv_q6_count_p90,
        "v3_ccv_q6_cells_p50": v3_ccv_q6_cells_p50,
        "v3_ccv_q6_cells_p90": v3_ccv_q6_cells_p90,
        "v3_ccv_q6_formal_decision_value_p50": v3_ccv_q6_formal_p50,
        "v3_ccv_q6_formal_decision_value_p90": v3_ccv_q6_formal_p90,
        "v3_ccv_diagnostics": v3_shadow.get("v3_ccv_diagnostics"),
        "v3_resid_available": v3_resid_available,
        "v3_resid_ready": v3_resid_ready,
        "v3_resid_affects_bid": v3_resid_affects_bid,
        "v3_resid_match_scope": v3_resid_match_scope,
        "v3_resid_n_matched": v3_resid_n_matched,
        "v3_resid_q6_present_rate": v3_resid_q6_present_rate,
        "v3_resid_q6_count_p50": v3_resid_q6_count_p50,
        "v3_resid_q6_count_p90": v3_resid_q6_count_p90,
        "v3_resid_q6_cells_p50": v3_resid_q6_cells_p50,
        "v3_resid_q6_cells_p90": v3_resid_q6_cells_p90,
        "v3_resid_q6_value_p50": v3_resid_q6_value_p50,
        "v3_resid_q6_value_p90": v3_resid_q6_value_p90,
        "v3_resid_diagnostics": v3_shadow.get("v3_resid_diagnostics"),
        "v3_resid_gate_available": v3_resid_gate_available,
        "v3_resid_gate_ready": v3_resid_gate_ready,
        "v3_resid_gate_affects_bid": v3_resid_gate_affects_bid,
        "v3_resid_gate_active": v3_resid_gate_active,
        "v3_resid_gate_status": v3_resid_gate_status,
        "v3_resid_gate_gate_reason": v3_resid_gate_gate_reason,
        "v3_resid_gate_source": v3_resid_gate_source,
        "v3_resid_gate_q6_count_p50": v3_resid_gate_q6_count_p50,
        "v3_resid_gate_q6_cells_p50": v3_resid_gate_q6_cells_p50,
        "v3_resid_gate_q6_value_p50": v3_resid_gate_q6_value_p50,
        "v3_resid_gate_q6_count_delta_p50": v3_resid_gate_q6_count_delta_p50,
        "v3_resid_gate_q6_cells_delta_p50": v3_resid_gate_q6_cells_delta_p50,
        "v3_resid_gate_q6_value_delta_p50": v3_resid_gate_q6_value_delta_p50,
        "v3_resid_gate_diagnostics": v3_shadow.get("v3_resid_gate_diagnostics"),
        "v3_cal_available": v3_cal_available,
        "v3_cal_ready": v3_cal_ready,
        "v3_cal_active": v3_cal_active,
        "v3_cal_affects_bid": v3_cal_affects_bid,
        "v3_cal_status": v3_cal_status,
        "v3_cal_gate_reason": v3_cal_gate_reason,
        "v3_cal_scale": v3_cal_scale,
        "v3_cal_archive_sessions": v3_cal_archive_sessions,
        "v3_cal_median_ratio": v3_cal_median_ratio,
        "v3_cal_p90_ratio": v3_cal_p90_ratio,
        "v3_cal_baseline_formal_p50_mae": v3_cal_baseline_formal_mae,
        "v3_cal_baseline_formal_p50_bias": v3_cal_baseline_formal_bias,
        "v3_cal_formal_decision_value_p50": v3_cal_formal_p50,
        "v3_cal_formal_decision_value_p90": v3_cal_formal_p90,
        "v3_cal_q6_formal_decision_value_p50": v3_cal_q6_formal_p50,
        "v3_cal_q6_formal_decision_value_p90": v3_cal_q6_formal_p90,
        "v3_cal_diagnostics": v3_shadow.get("v3_cal_diagnostics"),
        "v3_under_available": v3_under_available,
        "v3_under_ready": v3_under_ready,
        "v3_under_active": v3_under_active,
        "v3_under_candidate": v3_under_candidate,
        "v3_under_affects_bid": v3_under_affects_bid,
        "v3_under_status": v3_under_status,
        "v3_under_gate_reason": v3_under_gate_reason,
        "v3_under_scale": v3_under_scale,
        "v3_under_hero_map_id": v3_under_hero_map_id,
        "v3_under_source": v3_under_source,
        "v3_under_archive_sessions": v3_under_archive_sessions,
        "v3_under_formal_decision_value_p50": v3_under_formal_p50,
        "v3_under_formal_decision_value_p90": v3_under_formal_p90,
        "v3_under_q6_formal_decision_value_p50": v3_under_q6_formal_p50,
        "v3_under_q6_formal_decision_value_p90": v3_under_q6_formal_p90,
        "v3_under_diagnostics": v3_shadow.get("v3_under_diagnostics"),
        "v3_tail_review_available": v3_tail_review_available,
        "v3_tail_review_ready": v3_tail_review_ready,
        "v3_tail_review_active": v3_tail_review_active,
        "v3_tail_review_candidate": v3_tail_review_candidate,
        "v3_tail_review_hurt_guard": v3_tail_review_hurt_guard,
        "v3_tail_review_affects_bid": v3_tail_review_affects_bid,
        "v3_tail_review_status": v3_tail_review_status,
        "v3_tail_review_gate_reason": v3_tail_review_gate_reason,
        "v3_tail_review_hero_map_id": v3_tail_review_hero_map_id,
        "v3_tail_review_source": v3_tail_review_source,
        "v3_tail_review_archive_sessions": v3_tail_review_archive_sessions,
        "v3_tail_review_tail_delta_p50_mae": v3_tail_review_tail_delta,
        "v3_tail_review_q6_tail_delta_p50_mae": v3_tail_review_q6_tail_delta,
        "v3_tail_review_tail_replacement_decision_value_p50": (
            v3_tail_review_tail_p50
        ),
        "v3_tail_review_tail_replacement_decision_value_p90": (
            v3_tail_review_tail_p90
        ),
        "v3_tail_review_q6_tail_replacement_decision_value_p50": (
            v3_tail_review_q6_tail_p50
        ),
        "v3_tail_review_q6_tail_replacement_decision_value_p90": (
            v3_tail_review_q6_tail_p90
        ),
        "v3_tail_review_diagnostics": v3_shadow.get("v3_tail_review_diagnostics"),
        "v3_fv_available": v3_fv_available,
        "v3_fv_ready": v3_fv_ready,
        "v3_fv_active": v3_fv_active,
        "v3_fv_candidate": v3_fv_candidate,
        "v3_fv_affects_bid": v3_fv_affects_bid,
        "v3_fv_status": v3_fv_status,
        "v3_fv_gate_reason": v3_fv_gate_reason,
        "v3_fv_source": v3_fv_source,
        "v3_fv_stress_class": v3_fv_stress_class,
        "v3_fv_capacity_flags": v3_fv_capacity_flags,
        "v3_fv_formal_decision_value_p50": v3_fv_formal_p50,
        "v3_fv_formal_decision_value_p90": v3_fv_formal_p90,
        "v3_fv_q6_formal_decision_value_p50": v3_fv_q6_formal_p50,
        "v3_fv_q6_formal_decision_value_p90": v3_fv_q6_formal_p90,
        "v3_fv_diagnostics": v3_shadow.get("v3_fv_diagnostics"),
        "v3_fv_total_count_source": v3_shadow.get("v3_fv_total_count_source"),
        "v3_fv_total_count_target": _parse_float_text(
            v3_shadow.get("v3_fv_total_count_target")
        ),
        "v3_fv_total_count_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_total_count_prior_expected")
        ),
        "v3_fv_total_count_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_total_count_target_prior_ratio")
        ),
        "v3_fv_total_cells_source": v3_shadow.get("v3_fv_total_cells_source"),
        "v3_fv_total_cells_target": _parse_float_text(
            v3_shadow.get("v3_fv_total_cells_target")
        ),
        "v3_fv_total_cells_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_total_cells_prior_expected")
        ),
        "v3_fv_total_cells_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_total_cells_target_prior_ratio")
        ),
        "v3_fv_q6_count_source": v3_shadow.get("v3_fv_q6_count_source"),
        "v3_fv_q6_count_target": _parse_float_text(
            v3_shadow.get("v3_fv_q6_count_target")
        ),
        "v3_fv_q6_count_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_q6_count_prior_expected")
        ),
        "v3_fv_q6_count_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_q6_count_target_prior_ratio")
        ),
        "v3_fv_q6_cells_source": v3_shadow.get("v3_fv_q6_cells_source"),
        "v3_fv_q6_cells_target": _parse_float_text(
            v3_shadow.get("v3_fv_q6_cells_target")
        ),
        "v3_fv_q6_cells_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_q6_cells_prior_expected")
        ),
        "v3_fv_q6_cells_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_q6_cells_target_prior_ratio")
        ),
        "v3_fv_total_value_source": v3_shadow.get("v3_fv_total_value_source"),
        "v3_fv_total_value_target": _parse_float_text(
            v3_shadow.get("v3_fv_total_value_target")
        ),
        "v3_fv_total_value_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_total_value_prior_expected")
        ),
        "v3_fv_total_value_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_total_value_target_prior_ratio")
        ),
        "v3_fv_q6_value_source": v3_shadow.get("v3_fv_q6_value_source"),
        "v3_fv_q6_value_target": _parse_float_text(
            v3_shadow.get("v3_fv_q6_value_target")
        ),
        "v3_fv_q6_value_prior_expected": _parse_float_text(
            v3_shadow.get("v3_fv_q6_value_prior_expected")
        ),
        "v3_fv_q6_value_target_prior_ratio": _parse_float_text(
            v3_shadow.get("v3_fv_q6_value_target_prior_ratio")
        ),
        "v3_scp_available": v3_scp_available,
        "v3_scp_ready": v3_scp_ready,
        "v3_scp_affects_bid": v3_scp_affects_bid,
        "v3_scp_active": v3_scp_active,
        "v3_scp_candidate": v3_scp_candidate,
        "v3_scp_missing_table": v3_scp_missing_table,
        "v3_scp_status": v3_scp_status,
        "v3_scp_gate_reason": v3_scp_gate_reason,
        "v3_scp_scope": v3_scp_scope,
        "v3_scp_group": v3_scp_group,
        "v3_scp_inventory_count_p95": v3_scp_inventory_p95,
        "v3_scp_inventory_count_max": v3_scp_inventory_max,
        "v3_scp_non_temp_inventory_count_p95": v3_scp_non_temp_p95,
        "v3_scp_non_temp_inventory_count_max": v3_scp_non_temp_max,
        "v3_scp_above_drop_after_temp_zodiac_rows": (
            v3_scp_above_drop_after_temp
        ),
        "v3_scp_above_round_after_temp_zodiac_rows": (
            v3_scp_above_round_after_temp
        ),
        "v3_scp_prior_max_to_observed_p95_delta": v3_scp_prior_p95_delta,
        "v3_scp_prior_max_to_observed_max_delta": v3_scp_prior_max_delta,
        "v3_scp_flags": v3_scp_flags,
        "v3_scp_diagnostics": v3_shadow.get("v3_scp_diagnostics"),
        "v3_cse_available": v3_cse_available,
        "v3_cse_ready": v3_cse_ready,
        "v3_cse_affects_bid": v3_cse_affects_bid,
        "v3_cse_active": v3_cse_active,
        "v3_cse_candidate": v3_cse_candidate,
        "v3_cse_pressure_candidate": v3_cse_pressure_candidate,
        "v3_cse_status": v3_cse_status,
        "v3_cse_gate_reason": v3_cse_gate_reason,
        "v3_cse_scope": v3_cse_scope,
        "v3_cse_group": v3_cse_group,
        "v3_cse_mechanism_classes": v3_shadow.get("v3_cse_mechanism_classes"),
        "v3_cse_source_evidence_classes": v3_shadow.get(
            "v3_cse_source_evidence_classes"
        ),
        "v3_cse_source_context_classes": v3_cse_source_context_classes,
        "v3_cse_unique_round_overflow_rows": v3_cse_unique_round_rows,
        "v3_cse_server_side_expansion_rows": v3_cse_server_rows,
        "v3_cse_session_capacity_source_semantics_rows": v3_cse_session_rows,
        "v3_cse_public_total_match_rows": v3_cse_public_rows,
        "v3_cse_full_action_rows": v3_cse_full_action_rows,
        "v3_cse_unique_non_temp_p95": v3_cse_unique_p95,
        "v3_cse_unique_non_temp_max": v3_cse_unique_max,
        "v3_cse_target_count_source": v3_cse_target_count_source,
        "v3_cse_target_count": v3_cse_target_count,
        "v3_cse_prior_items_per_session_max": v3_cse_prior_items_per_session_max,
        "v3_cse_target_prior_max_delta": v3_cse_target_prior_delta,
        "v3_cse_target_to_unique_non_temp_p95_delta": v3_cse_target_p95_delta,
        "v3_cse_prior_max_to_unique_non_temp_p95_delta": (
            v3_cse_prior_p95_delta
        ),
        "v3_cse_prior_max_to_unique_non_temp_max_delta": v3_cse_prior_max_delta,
        "v3_cse_flags": v3_cse_flags,
        "v3_cse_diagnostics": v3_shadow.get("v3_cse_diagnostics"),
        "v3_practical_available": bool(v3_shadow.get("v3_practical_available")),
        "v3_practical_ready": bool(v3_shadow.get("v3_practical_ready")),
        "v3_practical_affects_bid": bool(
            v3_shadow.get("v3_practical_affects_bid")
        ),
        "v3_practical_active": bool(v3_shadow.get("v3_practical_active")),
        "v3_practical_candidate": bool(v3_shadow.get("v3_practical_candidate")),
        "v3_practical_source": v3_shadow.get("v3_practical_source"),
        "v3_practical_mode": v3_shadow.get("v3_practical_mode"),
        "v3_practical_status": v3_shadow.get("v3_practical_status"),
        "v3_practical_recommendation": v3_shadow.get(
            "v3_practical_recommendation"
        ),
        "v3_practical_confidence": v3_shadow.get("v3_practical_confidence"),
        "v3_practical_source_lanes": v3_shadow.get("v3_practical_source_lanes"),
        "v3_practical_risk_flags": v3_shadow.get("v3_practical_risk_flags"),
        "v3_practical_reason": v3_shadow.get("v3_practical_reason"),
        "v3_practical_formal_decision_value_p50": v3_practical_formal_p50,
        "v3_practical_formal_decision_value_p90": v3_practical_formal_p90,
        "v3_practical_q6_formal_decision_value_p50": (
            v3_practical_q6_formal_p50
        ),
        "v3_practical_q6_formal_decision_value_p90": (
            v3_practical_q6_formal_p90
        ),
        "v3_practical_baseline_formal_decision_value_p50": _parse_int_text(
            v3_shadow.get("v3_practical_baseline_formal_decision_value_p50")
        ),
        "v3_practical_baseline_formal_decision_value_p90": _parse_int_text(
            v3_shadow.get("v3_practical_baseline_formal_decision_value_p90")
        ),
        "v3_practical_delta_formal_decision_value_p50": _parse_int_text(
            v3_shadow.get("v3_practical_delta_formal_decision_value_p50")
        ),
        "v3_practical_delta_formal_decision_value_p90": _parse_int_text(
            v3_shadow.get("v3_practical_delta_formal_decision_value_p90")
        ),
        "v3_practical_delta_q6_formal_decision_value_p50": _parse_int_text(
            v3_shadow.get("v3_practical_delta_q6_formal_decision_value_p50")
        ),
        "v3_practical_delta_q6_formal_decision_value_p90": _parse_int_text(
            v3_shadow.get("v3_practical_delta_q6_formal_decision_value_p90")
        ),
        "v3_practical_diagnostics": v3_shadow.get("v3_practical_diagnostics"),
        "v3_tail_review_tail_replacement_decision_value_p50_error": (
            v3_tail_review_tail_p50 - final_replacement_decision_value
            if v3_tail_review_tail_p50 is not None
            and final_replacement_decision_value is not None
            else None
        ),
        "v3_tail_review_tail_replacement_decision_value_p90_under_by": (
            max(0, final_replacement_decision_value - v3_tail_review_tail_p90)
            if v3_tail_review_tail_p90 is not None
            and final_replacement_decision_value is not None
            else None
        ),
        "v3_tail_review_q6_tail_replacement_decision_value_p50_error": (
            v3_tail_review_q6_tail_p50
            - final_q6_decision_value_with_tail_replacement
            if v3_tail_review_q6_tail_p50 is not None
            else None
        ),
        "v3_tail_review_q6_tail_replacement_decision_value_p90_under_by": (
            max(
                0,
                final_q6_decision_value_with_tail_replacement
                - v3_tail_review_q6_tail_p90,
            )
            if v3_tail_review_q6_tail_p90 is not None
            else None
        ),
        "v3_under_formal_decision_value_p50_error_vs_formal": (
            v3_under_formal_p50 - final_formal_decision_value
            if v3_under_formal_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_under_formal_decision_value_p90_under_by": (
            max(0, final_formal_decision_value - v3_under_formal_p90)
            if v3_under_formal_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_under_q6_formal_decision_value_p50_error": (
            v3_under_q6_formal_p50 - final_q6_decision_value
            if v3_under_q6_formal_p50 is not None
            else None
        ),
        "v3_under_q6_formal_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - v3_under_q6_formal_p90)
            if v3_under_q6_formal_p90 is not None
            else None
        ),
        "v3_fv_formal_decision_value_p50_error_vs_formal": (
            v3_fv_formal_p50 - final_formal_decision_value
            if v3_fv_formal_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_fv_formal_decision_value_p90_under_by": (
            max(0, final_formal_decision_value - v3_fv_formal_p90)
            if v3_fv_formal_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_fv_q6_formal_decision_value_p50_error": (
            v3_fv_q6_formal_p50 - final_q6_decision_value
            if v3_fv_q6_formal_p50 is not None
            else None
        ),
        "v3_fv_q6_formal_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - v3_fv_q6_formal_p90)
            if v3_fv_q6_formal_p90 is not None
            else None
        ),
        "v3_practical_formal_decision_value_p50_error_vs_formal": (
            v3_practical_formal_p50 - final_formal_decision_value
            if v3_practical_formal_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_practical_formal_decision_value_p90_under_by": (
            max(0, final_formal_decision_value - v3_practical_formal_p90)
            if v3_practical_formal_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_practical_q6_formal_decision_value_p50_error": (
            v3_practical_q6_formal_p50 - final_q6_decision_value
            if v3_practical_q6_formal_p50 is not None
            else None
        ),
        "v3_practical_q6_formal_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - v3_practical_q6_formal_p90)
            if v3_practical_q6_formal_p90 is not None
            else None
        ),
        "v3_cal_formal_decision_value_p50_error_vs_formal": (
            v3_cal_formal_p50 - final_formal_decision_value
            if v3_cal_formal_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_cal_formal_decision_value_p90_under_by": (
            max(0, final_formal_decision_value - v3_cal_formal_p90)
            if v3_cal_formal_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_cal_q6_formal_decision_value_p50_error": (
            v3_cal_q6_formal_p50 - final_q6_decision_value
            if v3_cal_q6_formal_p50 is not None
            else None
        ),
        "v3_cal_q6_formal_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - v3_cal_q6_formal_p90)
            if v3_cal_q6_formal_p90 is not None
            else None
        ),
        "v3_formal_decision_value_p50_error_vs_formal": (
            v3_post_formal_p50 - final_formal_decision_value
            if v3_post_formal_p50 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_formal_decision_value_p90_under_by": (
            max(0, final_formal_decision_value - v3_post_formal_p90)
            if v3_post_formal_p90 is not None
            and final_formal_decision_value is not None
            else None
        ),
        "v3_q6_formal_decision_value_p50_error": (
            v3_post_q6_formal_p50 - final_q6_decision_value
            if v3_post_q6_formal_p50 is not None
            else None
        ),
        "v3_q6_formal_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - v3_post_q6_formal_p90)
            if v3_post_q6_formal_p90 is not None
            else None
        ),
        "q6_residual_boost_shadow_label": shadow.get("label"),
        "q6_residual_boost_shadow_gate": shadow.get("gate"),
        "q6_residual_boost_shadow_evidence_profile": shadow.get(
            "evidence_profile_key"
        ),
        "q6_residual_boost_shadow_active": q6_shadow_active,
        "q6_residual_boost_shadow_trials": _parse_int_text(shadow.get("trials")),
        "q6_residual_boost_shadow_active_boost": q6_shadow_active_boost,
        "q6_residual_boost_shadow_decision_value_p50": (
            q6_shadow_decision_value_p50
        ),
        "q6_residual_boost_shadow_decision_value_p90": (
            q6_shadow_decision_value_p90
        ),
        "q6_residual_boost_shadow_q6_decision_value_p90": (
            q6_shadow_q6_decision_value_p90
        ),
        "q6_residual_boost_shadow_q6_count_p90": q6_shadow_q6_count_p90,
        "q6_residual_boost_shadow_q6_cells_p90": q6_shadow_q6_cells_p90,
        "q6_residual_boost_shadow_q6_p90_delta": (
            q6_shadow_q6_decision_value_p90 - q6_decision_value_p90
            if q6_shadow_q6_decision_value_p90 is not None
            and q6_decision_value_p90 is not None
            else None
        ),
        "q6_residual_boost_shadow_under_before": q6_shadow_under_before,
        "q6_residual_boost_shadow_covered_after": q6_shadow_covered_after,
        "q6_residual_boost_shadow_helped": (
            q6_shadow_under_before and q6_shadow_covered_after
        ),
        "q6_residual_boost_shadow_no_plannable_control": (
            q6_shadow_active and no_plannable_q6_control
        ),
        "q6_residual_boost_shadow_no_plannable_positive_proxy": (
            q6_shadow_active
            and no_plannable_q6_control
            and (q6_shadow_q6_decision_value_p90 or 0) > 0
        ),
        "q6_residual_boost_shadow_zero_q6_proven_control": (
            q6_shadow_active and zero_q6_proven_control
        ),
        "q6_residual_boost_shadow_zero_q6_proven_false_positive": (
            q6_shadow_active
            and zero_q6_proven_control
            and (q6_shadow_q6_decision_value_p90 or 0) > 0
        ),
        "q6_residual_boost_shadow_false_positive_proxy": (
            q6_shadow_active
            and no_plannable_q6_control
            and (q6_shadow_q6_decision_value_p90 or 0) > 0
        ),
        **deep_floor_shadow_fields,
        **deep11_floor_shadow_fields,
        **hidden_floor_shadow_fields,
        **villa_floor_shadow_fields,
        **ethan_villa_random_floor_shadow_fields,
        **ethan_shipwreck_layout_conditional_shadow_fields,
        "q6_aisha_bottom_row_risk": bool(bottom_row_risk.get("active")),
        "layout_bottom_row": _parse_int_text(bottom_row_risk.get("bottom_row")),
        "layout_bottom_row_risk_threshold": _parse_int_text(
            bottom_row_risk.get("bottom_row_threshold")
        ),
        "q6_quality_only_local_count": _parse_int_text(
            quality_only_local_risk.get("count")
        ),
        "q6_quality_only_deepest_local_index": _parse_int_text(
            quality_only_local_risk.get("deepest_local_index")
        ),
        "q6_quality_only_deepest_start_row": _parse_int_text(
            quality_only_local_risk.get("deepest_start_row")
        ),
        "q6_quality_only_deep_local_risk": bool(
            quality_only_local_risk.get("active")
        ),
        "q6_quality_only_deep_row_threshold": _parse_int_text(
            quality_only_local_risk.get("deep_row_threshold")
        ),
        "v2_q6_value_p90_under_by": (
            max(0, final_q6_value - q6_value_p90)
            if q6_value_p90 is not None
            else None
        ),
        "v2_q6_decision_value_p90_under_by": (
            max(0, final_q6_decision_value - q6_decision_value_p90)
            if q6_decision_value_p90 is not None
            else None
        ),
        "q6_top_size_band": _q6_top_size_band(truth_breakdown),
        "q6_p90_misses_truth": (
            q6_value_p90 < final_q6_value
            if q6_value_p90 is not None
            and final_q6_value > 0
            else None
        ),
        "q6_plannable_p90_misses_truth": (
            q6_decision_value_p90 < final_q6_decision_value
            if q6_decision_value_p90 is not None
            and final_q6_decision_value > 0
            else None
        ),
        "v2_q6_tail_replacement_decision_value_p90_under_by": (
            max(
                0,
                final_q6_decision_value_with_tail_replacement
                - q6_decision_value_p90,
            )
            if q6_decision_value_p90 is not None
            and final_q6_tail_replacement_value > 0
            else None
        ),
        "v2_q6_tail_replacement_estimate_p90_under_by": (
            max(
                0,
                final_q6_decision_value_with_tail_replacement
                - q6_tail_replacement_estimate_p90,
            )
            if q6_tail_replacement_estimate_p90 is not None
            and final_q6_tail_replacement_value > 0
            else None
        ),
        "q6_tail_replacement_p90_misses_truth": (
            q6_decision_value_p90
            < final_q6_decision_value_with_tail_replacement
            if q6_decision_value_p90 is not None
            and final_q6_decision_value_with_tail_replacement > 0
            and final_q6_tail_replacement_value > 0
            else None
        ),
        "q6_tail_replacement_estimate_p90_misses_truth": (
            q6_tail_replacement_estimate_p90
            < final_q6_decision_value_with_tail_replacement
            if q6_tail_replacement_estimate_p90 is not None
            and final_q6_decision_value_with_tail_replacement > 0
            and final_q6_tail_replacement_value > 0
            else None
        ),
        "q6_false_low_risk": (
            q6_match_rate < 0.10
            if q6_match_rate is not None
            and final_q6_value > 0
            else None
        ),
        "q6_below_drop_prior": "q6_below_drop_prior:" in posterior_diagnostics,
        "relaxed_exact_used": "relaxed_exact_bucket_targets:" in posterior_diagnostics,
        "shape_target_count": shape_target_count,
        "category_target_count": category_target_count,
        "category_exclusion_count": category_exclusion_count,
        "anchor_count": anchor_count,
        "random_sample_avg_values": random_sample_avg_values,
        "random_sample_avg_signal_values": random_sample_avg_signal_values,
        "public_constraint_key": public_constraint_key,
        "evidence_profile_key": evidence_profile_key,
        "evidence_stage": evidence_stage,
        "information_density_score": density_score,
        "information_density_band": density_band,
        "hero_information_density": f"{artifact.get('hero')}|{density_band}",
        "posterior_samples": posterior_samples,
        "posterior_total_samples": posterior_total_samples,
        "layout_conflict": bool(layout_root),
        "layout_conflict_root": layout_root,
        "posterior_diagnostics": posterior_diagnostics,
        **size_bucket_eval_fields(
            posterior_diagnostics=posterior_diagnostics,
            action_result_rows=artifact.get("action_result_rows"),
        ),
        "stop_minus_final_value": (
            stop_bid - final_value
            if stop_bid is not None and final_value is not None
            else None
        ),
        "monitor_processing_seconds": artifact.get("processing_seconds"),
        "monitor_n_trials": artifact.get("n_trials"),
        "monitor_roi_trials": artifact.get("roi_trials"),
        "monitor_shadow_trials": artifact.get("shadow_trials"),
        "input_constraints_mode": input_constraints.get("mode"),
        "input_warehouse_total_cells": (
            (input_constraints.get("warehouse_total_cells") or {}).get("value")
            if isinstance(input_constraints.get("warehouse_total_cells"), Mapping)
            else None
        ),
        "input_warehouse_total_cells_approx": (
            (input_constraints.get("warehouse_total_cells_approx") or {}).get("value")
            if isinstance(
                input_constraints.get("warehouse_total_cells_approx"),
                Mapping,
            )
            else None
        ),
        "input_total_item_count": (
            (input_constraints.get("total_item_count") or {}).get("value")
            if isinstance(input_constraints.get("total_item_count"), Mapping)
            else None
        ),
    }


def _grid_item_name(
    item_id: int | None,
    items: Mapping[int, Item],
) -> str:
    if item_id is None:
        return ""
    item = items.get(int(item_id))
    return item.name if item is not None else ""


def _item_names(items: Mapping[int, Item]) -> dict[int, str]:
    return {
        int(item_id): item.name
        for item_id, item in items.items()
        if item.name
    }


def _action_send_rows(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Item],
) -> list[dict[str, Any]]:
    item_names = _item_names(items)
    rows: list[dict[str, Any]] = []
    for send in events.sends:
        if send.kind != "action" or send.value is None:
            continue
        action_id = int(send.value)
        rows.append(
            {
                "sort": send.sort_id,
                "time": send.capture_time,
                "action_id": action_id,
                "tool": item_names.get(action_id, ""),
            }
        )
    return list(reversed(rows))


def _current_completed_action_ids(
    events: FatbeansCaptureEvents,
    *,
    session_id: str | None,
    action_round: int | None,
    observed_round: int | None,
    phase: str,
) -> tuple[int, ...]:
    """Return the action sends owned by the latest completed-round replay.

    ``0x0025`` state rows are cumulative: every replay repeats all earlier action
    results and rewrites their packet round/sort to the newest completed round.
    The original send/state sequence is therefore the authority for which result(s)
    are new in this replay. Duplicate or regressing sorts, duplicate actions inside
    one proven window, future/duplicate completed states, and cross-session rows in
    that window all fail closed; the producer never sorts or deduplicates them into
    an apparently clean lineage. ``()`` means that the current snapshot cannot
    safely publish a current action. The row-order fallback remains available only
    to callers that invoke ``_promote_current_action_rows`` without this product
    authority.
    """

    if type(phase) is not str or phase != "bidding":  # noqa: E721
        return ()
    if (
        type(session_id) is not str  # noqa: E721 - normalized session boundary
        or not session_id
        or session_id.strip() != session_id
        or type(action_round) is not int  # noqa: E721
        or type(observed_round) is not int  # noqa: E721
        or action_round < 1
        or observed_round < 1
        or action_round != observed_round + 1
    ):
        return ()

    completed_states: list[tuple[int, int]] = []
    foreign_completed_state_sorts: list[int] = []
    last_state_sort: int | None = None
    last_state_round: int | None = None
    for state in events.states:
        message_id = getattr(state, "message_id", None)
        if type(message_id) is not int:  # noqa: E721
            return ()
        if message_id != 0x0025:
            continue
        state_sort = getattr(state, "sort_id", None)
        state_session = getattr(state, "session_id", None)
        state_round = getattr(state, "round_no", None)
        if (
            type(state_sort) is not int  # noqa: E721
            or state_sort < 0
            or type(state_session) is not str  # noqa: E721
            or not state_session
            or state_session.strip() != state_session
            or type(state_round) is not int  # noqa: E721
            or state_round < 1
        ):
            return ()
        if state_session != session_id:
            foreign_completed_state_sorts.append(state_sort)
            continue
        if (
            (last_state_sort is not None and state_sort <= last_state_sort)
            or (last_state_round is not None and state_round <= last_state_round)
            or state_round > observed_round
        ):
            return ()
        completed_states.append((state_sort, state_round))
        last_state_sort = state_sort
        last_state_round = state_round

    if not completed_states or completed_states[-1][1] != observed_round:
        return ()
    if observed_round > 1 and (
        len(completed_states) < 2
        or completed_states[-2][1] != observed_round - 1
    ):
        return ()
    current_sort = completed_states[-1][0]
    previous_sort = completed_states[-2][0] if len(completed_states) > 1 else -1
    if any(
        previous_sort < foreign_sort <= current_sort
        for foreign_sort in foreign_completed_state_sorts
    ):
        return ()

    current_sends: list[int] = []
    current_action_ids: set[int] = set()
    last_send_sort: int | None = None
    for send in events.sends:
        kind = getattr(send, "kind", None)
        if type(kind) is not str:  # noqa: E721
            return ()
        if kind != "action":
            continue
        send_sort = getattr(send, "sort_id", None)
        send_session = getattr(send, "session_id", None)
        action_id = getattr(send, "value", None)
        if (
            type(send_sort) is not int  # noqa: E721
            or send_sort < 0
            or type(send_session) is not str  # noqa: E721
            or not send_session
            or send_session.strip() != send_session
            or type(action_id) is not int  # noqa: E721 - bool/subclasses rejected
            or action_id <= 0
        ):
            return ()
        if send_session != session_id:
            if previous_sort < send_sort <= current_sort:
                return ()
            continue
        if last_send_sort is not None and send_sort <= last_send_sort:
            return ()
        last_send_sort = send_sort
        if not previous_sort < send_sort <= current_sort:
            continue
        if action_id in current_action_ids:
            return ()
        current_action_ids.add(action_id)
        current_sends.append(action_id)
    return tuple(current_sends)


def _observed_action_summary(
    observed_items: Sequence[Any],
    item_names: Mapping[int, str],
) -> str:
    if not observed_items:
        return ""
    quality_counts = Counter(
        int(item.quality)
        for item in observed_items
        if getattr(item, "quality", None) is not None
    )
    parts = [
        f"Q{quality}x{count}"
        for quality, count in sorted(quality_counts.items(), reverse=True)
    ]
    named = [
        item_names.get(int(item.item_id), "")
        for item in observed_items
        if getattr(item, "item_id", None) is not None
    ]
    if named:
        parts.append("/".join(name for name in named[:3] if name))
    locals_ = [
        str(item.local_index)
        for item in observed_items
        if getattr(item, "local_index", None) is not None
    ]
    if locals_:
        parts.append("pos " + ",".join(locals_[:8]))
    return " / ".join(part for part in parts if part)


_ACTION_RESULT_SESSION_ID_RE = re.compile(r"^\d{4}:\d+$")


def _valid_action_result_session_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _ACTION_RESULT_SESSION_ID_RE.fullmatch(value) is not None
    )


def _action_result_rows(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Item],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    item_names = _item_names(items)
    latest_by_action: dict[int, tuple[int, dict[str, Any]]] = {}
    shape_variants_by_session_action: dict[
        tuple[str | None, int],
        dict[
            tuple[tuple[str, str], ...],
            tuple[int, dict[str, Any]],
        ],
    ] = {}
    zero_implied_action_ids = {
        100104,
        100105,
        100106,
        100107,
        100108,
        100110,
        100111,
        100112,
        100113,
        100114,
        100116,
        100117,
        100118,
        100119,
        100120,
        100122,
        100123,
        100124,
        100125,
        100126,
    }

    def session_matches(candidate: str | None) -> bool:
        return session_id is None or candidate is None or candidate == session_id

    def strict_session_matches(candidate: Any) -> bool:
        return (
            _valid_action_result_session_id(session_id)
            and _valid_action_result_session_id(candidate)
            and candidate == session_id
        )

    for state in events.states:
        if not session_matches(state.session_id):
            continue
        if state.message_id == 0x002D or state.inventory_items:
            continue
        priority = 2 if state.message_id == 0x0027 else 1
        for result in state.action_results:
            action_id = int(result.action_id)
            row = {
                "sort": state.sort_id,
                "time": state.capture_time,
                "action_id": action_id,
                "tool": item_names.get(action_id, ""),
                "result": result.result,
                "result_field": result.result_field,
                "result_wire_type": result.result_wire_type,
                "result_raw_hex": result.result_raw_hex,
                "session_id": state.session_id,
                "round": state.round_no,
                "source_message_id": state.message_id,
                "source_kind": (
                    "direct_action_response"
                    if state.message_id == 0x0027
                    else "state_replay"
                ),
                "revealed_items": len(result.observed_items),
                "revealed_items_detail": _observed_items_detail(
                    result.observed_items,
                    item_names,
                ),
                "revealed_summary": _observed_action_summary(
                    result.observed_items,
                    item_names,
                ),
            }
            if action_id in SIZE_AVG_ACTION_FOOTPRINT:
                fingerprint = shape_average_action_observation_fingerprint(row)
                variants = shape_variants_by_session_action.setdefault(
                    (state.session_id, action_id),
                    {},
                )
                existing_variant = variants.get(fingerprint)
                if (
                    existing_variant is None
                    or priority > existing_variant[0]
                    or (
                        priority == existing_variant[0]
                        and state.sort_id
                        >= int(existing_variant[1].get("sort") or 0)
                    )
                ):
                    variants[fingerprint] = (priority, row)
                continue
            existing = latest_by_action.get(action_id)
            if existing is not None:
                existing_priority, existing_row = existing
                existing_sort = int(existing_row.get("sort") or 0)
                if priority < existing_priority:
                    continue
                if priority == existing_priority and state.sort_id < existing_sort:
                    continue
            latest_by_action[action_id] = (
                priority,
                row,
            )
    for send in sorted(events.sends, key=lambda item: item.sort_id, reverse=True):
        if (
            send.kind != "action"
            or not isinstance(send.value, int)
            or isinstance(send.value, bool)
        ):
            continue
        if not strict_session_matches(send.session_id):
            continue
        action_id = int(send.value)
        if action_id not in zero_implied_action_ids:
            continue
        existing = latest_by_action.get(action_id)
        if existing is not None:
            _existing_priority, existing_row = existing
            has_numeric_result = existing_row.get("result") is not None
            has_revealed_items = int(existing_row.get("revealed_items") or 0) > 0
            if has_numeric_result or has_revealed_items:
                continue
        has_matching_later_empty_ack = any(
            state.sort_id > send.sort_id
            and state.message_id == 0x0027
            and strict_session_matches(state.session_id)
            and any(
                isinstance(result.action_id, int)
                and not isinstance(result.action_id, bool)
                and result.action_id == action_id
                and result.result is None
                and result.result_field is None
                and not result.observed_items
                for result in state.action_results
            )
            for state in events.states
        )
        if not has_matching_later_empty_ack:
            continue
        latest_by_action[action_id] = (
            0,
            {
                "sort": send.sort_id,
                "time": send.capture_time,
                "action_id": action_id,
                "tool": item_names.get(action_id, ""),
                "result": 0,
                "result_field": None,
                "inferred_zero": True,
                "result_inference": "sent_action_without_result_after_later_state",
                "revealed_items": 0,
                "revealed_items_detail": [],
                "revealed_summary": "",
            },
        )
    rows = [row for _priority, row in latest_by_action.values()]
    rows.extend(
        row
        for variants in shape_variants_by_session_action.values()
        for _priority, row in variants.values()
    )
    return sorted(
        rows,
        key=lambda row: int(row.get("sort") or 0),
        reverse=True,
    )


_ACTION_RESULT_INT_MAX = (1 << 63) - 1
_ACTION_RESULT_EXACT_INT_IDS = frozenset(
    _ACTION_SESSION_FIELDS
    | _ACTION_TOTAL_CELLS
    | _ACTION_VALUE_SUM
    | _ACTION_COUNT
    | _ACTION_GREEN_SPLIT_FIELDS
) | frozenset({100121})
_ACTION_RESULT_NONNEGATIVE_FLOAT_IDS = frozenset(_ACTION_AVG_CELLS)
_ACTION_RESULT_POSITIVE_FLOAT_IDS = frozenset(_ACTION_SIZE_AVG_VALUE)
_ACTION_RESULT_SCALAR_IDS = (
    _ACTION_RESULT_EXACT_INT_IDS
    | _ACTION_RESULT_NONNEGATIVE_FLOAT_IDS
    | _ACTION_RESULT_POSITIVE_FLOAT_IDS
)
_ACTION_RESULT_OUTLINE_DETAIL_IDS = frozenset(_CATEGORY_OUTLINE_ACTIONS) | frozenset(
    {100100, 100101, 100102, 100161, 100165}
)
_ACTION_RESULT_QUALITY_DETAIL_IDS = frozenset(
    {100134, 100135, 100136, 100137, 100138, 100139, 100140, 100166, 100174}
)
_ACTION_RESULT_VALUE_DETAIL_IDS = frozenset({100163, 100167})
_ACTION_RESULT_CELLS_DETAIL_IDS = frozenset({100162})
_ACTION_RESULT_FULL_ITEM_DETAIL_IDS = frozenset(
    {100127, 100128, 100129, 100130, 100131, 100132, 100133, 100164, 100168}
)
_ACTION_RESULT_ITEM_DETAIL_IDS = (
    _ACTION_RESULT_OUTLINE_DETAIL_IDS
    | _ACTION_RESULT_QUALITY_DETAIL_IDS
    | _ACTION_RESULT_VALUE_DETAIL_IDS
    | _ACTION_RESULT_CELLS_DETAIL_IDS
    | _ACTION_RESULT_FULL_ITEM_DETAIL_IDS
)
_ACTION_RESULT_DETAIL_KEYS = frozenset(
    {
        "local_index",
        "runtime_id",
        "item_id",
        "item_name",
        "quality",
        "value",
        "shape_code",
        "cells",
    }
)


def _valid_completed_action_item_detail(action_id: int, value: object) -> bool:
    """Validate one normalized observed item without coercing wire values."""
    if type(value) is not dict:  # noqa: E721 - normalized wire boundary
        return False
    keys = tuple(dict.keys(value))
    if (
        len(keys) != len(_ACTION_RESULT_DETAIL_KEYS)
        or any(type(key) is not str for key in keys)  # noqa: E721
        or frozenset(keys) != _ACTION_RESULT_DETAIL_KEYS
    ):
        return False

    local_index = dict.get(value, "local_index")
    runtime_id = dict.get(value, "runtime_id")
    item_id = dict.get(value, "item_id")
    item_name = dict.get(value, "item_name")
    quality = dict.get(value, "quality")
    item_value = dict.get(value, "value")
    shape_code = dict.get(value, "shape_code")
    cells = dict.get(value, "cells")
    if (
        (local_index is not None and (type(local_index) is not int or local_index < 0))  # noqa: E721
        or type(runtime_id) is not int  # noqa: E721
        or not 0 < runtime_id <= _ACTION_RESULT_INT_MAX
        or (
            item_id is not None
            and (
                type(item_id) is not int  # noqa: E721
                or not 0 < item_id <= _ACTION_RESULT_INT_MAX
            )
        )
        or type(item_name) is not str  # noqa: E721
        or (
            quality is not None
            and (type(quality) is not int or not 1 <= quality <= 6)  # noqa: E721
        )
        or (
            item_value is not None
            and (
                type(item_value) is not int  # noqa: E721
                or not 0 <= item_value <= _ACTION_RESULT_INT_MAX
            )
        )
        or (
            shape_code is not None
            and (
                type(shape_code) is not int  # noqa: E721
                or not 0 < shape_code <= 99
                or shape_code // 10 <= 0
                or shape_code % 10 <= 0
            )
        )
        or (
            cells is not None
            and (type(cells) is not int or not 0 < cells <= 81)  # noqa: E721
        )
    ):
        return False
    if (
        shape_code is not None
        and cells is not None
        and cells != (shape_code // 10) * (shape_code % 10)
    ):
        return False
    has_shape = shape_code is not None or cells is not None
    if action_id in _ACTION_RESULT_OUTLINE_DETAIL_IDS:
        return has_shape
    if action_id in _ACTION_RESULT_QUALITY_DETAIL_IDS:
        return quality is not None
    if action_id in _ACTION_RESULT_VALUE_DETAIL_IDS:
        return item_value is not None
    if action_id in _ACTION_RESULT_CELLS_DETAIL_IDS:
        return cells is not None
    if action_id in _ACTION_RESULT_FULL_ITEM_DETAIL_IDS:
        return item_id is not None and item_value is not None and has_shape
    return False


def _valid_completed_action_item_list(
    action_id: int, value: object
) -> tuple[Any, ...] | None:
    if type(value) is not list:  # noqa: E721 - normalized wire boundary
        return None
    count = list.__len__(value)
    if not 1 <= count <= 256:
        return None
    items = tuple(list.__getitem__(value, index) for index in range(count))
    if not all(_valid_completed_action_item_detail(action_id, item) for item in items):
        return None
    runtime_ids = tuple(dict.get(item, "runtime_id") for item in items)
    if len(set(runtime_ids)) != count:
        return None
    return items


def _completed_action_detail_scalar_matches(
    action_id: int,
    result: int | None,
    items: tuple[Any, ...],
) -> bool:
    if result is None:
        return True
    if action_id in _ACTION_RESULT_QUALITY_DETAIL_IDS:
        return False
    if action_id in _ACTION_RESULT_VALUE_DETAIL_IDS:
        return result == sum(dict.get(item, "value") for item in items)
    if action_id in (
        _ACTION_RESULT_OUTLINE_DETAIL_IDS
        | _ACTION_RESULT_CELLS_DETAIL_IDS
        | _ACTION_RESULT_FULL_ITEM_DETAIL_IDS
    ):
        total_cells = 0
        for item in items:
            cells = dict.get(item, "cells")
            shape_code = dict.get(item, "shape_code")
            if cells is None:
                if shape_code is None:
                    return False
                cells = (shape_code // 10) * (shape_code % 10)
            total_cells += cells
        return result == total_cells
    return False


def _has_authoritative_completed_action_result(row: object) -> bool:
    """Return whether a normalized action row carries a real completed result.

    ``result_field`` is wire metadata and never proves completion by itself.
    Scalar results stay exact builtin numerics inside the action domain.  Item
    reveals must be a complete normalized list whose declared count matches.
    """
    if type(row) is not dict:  # noqa: E721 - normalized wire boundary
        return False
    row_keys = tuple(dict.keys(row))
    if any(type(key) is not str for key in row_keys):  # noqa: E721
        return False
    action_id = dict.get(row, "action_id")
    if type(action_id) is not int or action_id <= 0:  # noqa: E721
        return False
    scalar_authorized = action_id in _ACTION_RESULT_SCALAR_IDS
    detail_authorized = action_id in _ACTION_RESULT_ITEM_DETAIL_IDS
    if not (scalar_authorized or detail_authorized):
        return False

    result = dict.get(row, "result")
    scalar_present = result is not None
    if action_id in _ACTION_RESULT_EXACT_INT_IDS and type(result) is int:  # noqa: E721
        scalar_valid = 0 <= result <= _ACTION_RESULT_INT_MAX
    elif (
        action_id in _ACTION_RESULT_NONNEGATIVE_FLOAT_IDS
        and type(result) is float  # noqa: E721
    ):
        scalar_valid = math.isfinite(result) and result >= 0.0
    elif (
        action_id in _ACTION_RESULT_POSITIVE_FLOAT_IDS
        and type(result) is float  # noqa: E721
    ):
        scalar_valid = math.isfinite(result) and result > 0.0
    elif action_id in _ACTION_RESULT_ITEM_DETAIL_IDS and type(result) is int:  # noqa: E721
        # Reveal actions may carry a scalar count/cell total beside their item
        # payload, but that scalar never authorizes completion on its own.
        scalar_valid = 0 <= result <= _ACTION_RESULT_INT_MAX
    else:
        scalar_valid = False
    if scalar_present and not scalar_valid:
        return False

    detail_lists: list[tuple[Any, ...]] = []
    detail_representation_present = False
    for key in ("revealed_items_detail", "observed_items"):
        if key not in row:
            continue
        detail_representation_present = True
        raw_items = dict.get(row, key)
        if type(raw_items) is not list:  # noqa: E721
            return False
        if list.__len__(raw_items) == 0:
            normalized: tuple[Any, ...] = ()
        else:
            parsed = _valid_completed_action_item_list(action_id, raw_items)
            if parsed is None:
                return False
            normalized = parsed
        detail_lists.append(normalized)
    declared_count_present = "revealed_items" in row
    declared_count = dict.get(row, "revealed_items")
    if declared_count_present and (
        type(declared_count) is not int  # noqa: E721
        or not 0 <= declared_count <= 256
    ):
        return False
    if detail_lists and (
        any(items != detail_lists[0] for items in detail_lists[1:])
        or (
            declared_count_present
            and any(declared_count != len(items) for items in detail_lists)
        )
    ):
        return False

    normalized_items = detail_lists[0] if detail_lists else ()
    if scalar_authorized:
        if detail_representation_present and normalized_items:
            return False
        if declared_count_present and declared_count != 0:
            return False
        return scalar_valid
    if not detail_authorized or not normalized_items:
        return False
    if not _completed_action_detail_scalar_matches(
        action_id,
        result if scalar_present else None,
        normalized_items,
    ):
        return False
    return True


def _promote_current_action_rows(
    rows: list[dict[str, Any]],
    *,
    session_id: str | None,
    action_round: int | None,
    observed_round: int | None,
    current_action_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Mark only a completed-round result that belongs to the current action turn.

    Newer official state replays deliver an accepted tool result under the
    completed ``0x0025`` round while the UI has already advanced by one.  Keep
    the declared packet round intact and add an explicit promotion marker; this
    never re-labels future, duplicate or cross-session rows.
    """
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if type(row) is not dict:  # noqa: E721 - normalized wire boundary
            cleaned.append(row)
            continue
        payload = dict(row)
        payload.pop("promoted_action_round", None)
        payload.pop("promotion_reason", None)
        cleaned.append(payload)
    if (
        type(session_id) is not str  # noqa: E721
        or not session_id
        or session_id.strip() != session_id
        or type(action_round) is not int  # noqa: E721
        or type(observed_round) is not int  # noqa: E721
        or action_round < 1
        or observed_round < 1
    ):
        return cleaned

    def candidate_key(payload: object) -> tuple[str, int, int] | None:
        if type(payload) is not dict:  # noqa: E721
            return None
        row_session = dict.get(payload, "session_id")
        source_kind = dict.get(payload, "source_kind")
        source_message_id = dict.get(payload, "source_message_id")
        action_id = dict.get(payload, "action_id")
        declared = dict.get(payload, "round")
        sort_id = dict.get(payload, "sort")
        if (
            type(row_session) is not str  # noqa: E721
            or row_session != session_id
            or type(source_kind) is not str  # noqa: E721
            or source_kind != "state_replay"
            or type(source_message_id) is not int  # noqa: E721
            or source_message_id != 0x0025
            or type(action_id) is not int  # noqa: E721
            or action_id <= 0
            or type(declared) is not int  # noqa: E721
            or declared != observed_round
            or declared + 1 != action_round
            or type(sort_id) is not int  # noqa: E721
            or sort_id < 0
            or not _has_authoritative_completed_action_result(payload)
        ):
            return None
        return (row_session, action_id, declared)

    candidates = [
        (index, payload, key)
        for index, payload in enumerate(cleaned)
        if (key := candidate_key(payload)) is not None
    ]
    candidate_counts = Counter(key for _index, _payload, key in candidates)
    selected: list[dict[str, Any]] = []
    if current_action_ids is not None:
        if (
            type(current_action_ids) is not tuple  # noqa: E721
            or any(
                type(action_id) is not int or action_id <= 0  # noqa: E721
                for action_id in current_action_ids
            )
            or len(set(current_action_ids)) != len(current_action_ids)
        ):
            return cleaned
        if not current_action_ids:
            return cleaned
        by_action: dict[int, list[dict[str, Any]]] = {}
        for _index, payload, key in candidates:
            by_action.setdefault(key[1], []).append(payload)
        if any(
            len(by_action.get(action_id, ())) != 1
            for action_id in current_action_ids
        ):
            return cleaned
        selected = [by_action[action_id][0] for action_id in current_action_ids]
    elif candidates:
        # Source-external callers may not carry the send ledger. Parser order is
        # old -> new for equal state sort, so only the final unique result may be
        # current. Never publish every repeated cumulative prefix row.
        current_sort = max(
            int(dict.get(payload, "sort")) for _, payload, _ in candidates
        )
        latest = [
            row for row in candidates if dict.get(row[1], "sort") == current_sort
        ]
        _index, payload, key = latest[-1]
        if candidate_counts[key] == 1:
            selected = [payload]
    for payload in selected:
        payload["promoted_action_round"] = action_round
        payload["promotion_reason"] = "completed_round_state_replay"
    return cleaned


_CURRENT_ACTION_REF_FIELDS = (
    "action_id",
    "result",
    "result_field",
    "result_wire_type",
    "result_raw_hex",
    "session_id",
    "round",
    "source_message_id",
    "source_kind",
    "sort",
    "promoted_action_round",
    "promotion_reason",
    "revealed_items",
)


def _current_action_ref_projection(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project only canonical promoted facts into structured engine input."""

    return [
        {
            key: dict.get(row, key)
            for key in _CURRENT_ACTION_REF_FIELDS
            if dict.__contains__(row, key)
        }
        for row in rows
        if (
            type(row) is dict  # noqa: E721 - normalized producer row
            and type(dict.get(row, "promoted_action_round")) is int  # noqa: E721
            and dict.get(row, "promotion_reason")
            == "completed_round_state_replay"
        )
    ]


def _observed_items_detail(
    observed_items: Sequence[Any],
    item_names: Mapping[int, str] | None = None,
) -> list[dict[str, Any]]:
    names = item_names or {}
    rows: list[dict[str, Any]] = []
    for item in observed_items:
        item_id = getattr(item, "item_id", None)
        item_name = ""
        if item_id is not None:
            try:
                item_name = names.get(int(item_id), "")
            except (TypeError, ValueError):
                item_name = ""
        row = {
            "local_index": item.local_index,
            "runtime_id": item.runtime_id,
            "item_id": item_id,
            "item_name": item_name,
            "quality": item.quality,
            "value": item.value,
            "shape_code": item.shape_code,
            "cells": item.cells,
        }
        rows.append(row)
    return rows


def _public_info_rows(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Item],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    item_names = _item_names(items)
    latest_by_info: dict[int, dict[str, Any]] = {}
    for state in events.states:
        if session_id is not None and state.session_id != session_id:
            continue
        for info in state.public_infos:
            info_id = int(info.info_id)
            row = {
                "sort": state.sort_id,
                "time": state.capture_time,
                "session_id": state.session_id,
                "info_id": info_id,
                "map_id": info.map_id,
                "value": info.value,
                "value_field": info.value_field,
                "value_wire_type": info.value_wire_type,
                "value_raw_hex": info.value_raw_hex,
                "value_endian": info.value_endian,
                "value_conflicts": list(info.value_conflicts),
                "revealed_items": len(info.observed_items),
                "revealed_items_detail": _observed_items_detail(
                    info.observed_items,
                    item_names,
                ),
                "revealed_summary": _observed_action_summary(
                    info.observed_items,
                    item_names,
                ),
            }
            previous = latest_by_info.get(info_id)
            if previous is not None:
                conflicts = {
                    str(value)
                    for value in (
                        *(previous.get("value_conflicts") or ()),
                        *(row.get("value_conflicts") or ()),
                    )
                    if value
                }

                def value_fingerprint(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
                    raw_hex = candidate.get("value_raw_hex")
                    return (
                        candidate.get("session_id"),
                        candidate.get("value_field"),
                        candidate.get("value_wire_type"),
                        raw_hex,
                        candidate.get("value_endian"),
                        None if raw_hex is not None else repr(candidate.get("value")),
                    )

                if value_fingerprint(previous) != value_fingerprint(row):
                    conflicts.add("cross_observation_value_conflict")
                row["value_conflicts"] = sorted(conflicts)
            latest_by_info[info_id] = row
    return sorted(
        latest_by_info.values(),
        key=lambda row: int(row.get("sort") or 0),
        reverse=True,
    )


def _skill_reveal_label(skill_id: int, hero_id: int | None) -> str:
    if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) != "1":
        postupdate_labels = {
            (102, 100102): "陈美·R1 珠宝+时尚轮廓",
            (102, 1001021): "陈美·R3 珠宝完整信息",
            (102, 1001022): "陈美·R4 珠宝完整信息",
            (102, 1001023): "陈美·R5 珠宝完整信息",
            (105, 100105): "塔蒂安娜·R1 时尚品质+轮廓",
            (105, 1001051): "塔蒂安娜·R3 时尚完整信息",
            (105, 1001052): "塔蒂安娜·R4 时尚完整信息",
            (105, 1001053): "塔蒂安娜·R5 时尚完整信息",
            (106, 100106): "娜奥米·R1 时尚+数码金红件数",
            (106, 1001061): "娜奥米·R1 时尚+数码轮廓",
            (106, 1001062): "娜奥米·R3 数码完整信息",
            (106, 1001063): "娜奥米·R4 数码完整信息",
            (106, 1001064): "娜奥米·R5 数码完整信息",
            (109, 100109): "海琳娜·R1 医疗品质",
            (109, 1001091): "海琳娜·R2 医疗轮廓",
            (109, 1001092): "海琳娜·R3 医疗轮廓",
            (109, 10010921): "海琳娜·R3 医疗完整信息",
            (109, 1001093): "海琳娜·R4 医疗轮廓",
            (109, 10010931): "海琳娜·R4 医疗完整信息",
            (109, 1001094): "海琳娜·R5 医疗轮廓",
            (109, 10010941): "海琳娜·R5 医疗完整信息",
            (201, 100201): "乔治·R1 武器品质+轮廓",
            (201, 1002011): "乔治·R2 武器完整信息",
            (201, 1002012): "乔治·R3 武器完整信息",
            (201, 1002013): "乔治·R4 武器完整信息",
            (201, 1002014): "乔治·R5 武器完整信息",
            (202, 100202): "卡洛斯·R1 家具+数码轮廓",
            (202, 1002021): "卡洛斯·R2 家具+数码品质",
            (202, 1002022): "卡洛斯·R3 家具+数码品质",
            (202, 10020221): "卡洛斯·R3 家具完整信息",
            (202, 1002023): "卡洛斯·R4 家具+数码品质",
            (202, 10020231): "卡洛斯·R4 家具完整信息",
            (202, 1002024): "卡洛斯·R5 家具+数码品质",
            (202, 10020241): "卡洛斯·R5 家具完整信息",
            (203, 100203): "莱昂纳德·R1 食品品质",
            (203, 1002031): "莱昂纳德·R1 古董品质",
            (203, 1002032): "莱昂纳德·R3 食品完整信息",
            (203, 1002033): "莱昂纳德·R4 食品完整信息",
            (203, 1002034): "莱昂纳德·R5 食品完整信息",
            (205, 100205): "伊万·R1 武器+能源轮廓",
            (205, 1002051): "伊万·R2 最大占格藏品品质",
            (205, 1002052): "伊万·R3 最大占格藏品品质",
            (206, 100206): "武田·R1 书画古籍轮廓",
            (206, 1002061): "武田·R1 书画古籍件数",
            (206, 1002062): "武田·R2 书画古籍品质",
            (206, 1002063): "武田·R3 书画古籍轮廓",
            (206, 10020631): "武田·R3 书画古籍完整信息",
            (206, 1002064): "武田·R4 书画古籍递进",
            (206, 10020641): "武田·R4 书画古籍完整信息",
            (206, 1002065): "武田·R5 书画古籍递进",
            (206, 10020651): "武田·R5 书画古籍完整信息",
            (303, 1003033): "印第安纳·R3 品质揭示",
            (303, 1003034): "印第安纳·R4 品质揭示",
            (303, 1003035): "印第安纳·R5 品质揭示",
        }
        label = postupdate_labels.get((hero_id, skill_id))
        if label is not None:
            return label
    if hero_id == 103:
        aisha_labels = {
            1001034: "艾莎·R1 白品轮廓",
            1001033: "艾莎·R2 绿品轮廓",
            1001032: "艾莎·R3 蓝品轮廓",
            1001031: "艾莎·R4 紫品轮廓",
        }
        if skill_id in aisha_labels:
            return aisha_labels[skill_id]
    if hero_id == 104:
        gabriela_labels = {
            1001041: "加布里·R1 揭示",
            1001042: "加布里·R2 揭示",
            1001043: "加布里·R3 揭示",
            1001044: "加布里·R4 揭示",
            1001045: "加布里·R5 揭示",
        }
        if skill_id in gabriela_labels:
            return gabriela_labels[skill_id]
    if hero_id == 108:
        if skill_id == 10010801:
            return "玛丽亚·品质"
        if skill_id in {100108, 10010802, 10010803}:
            return "玛丽亚·总价"
    if hero_id == 204:
        ahmed_labels = {
            100204: "艾哈迈德·R1 总件数",
            1002041: "艾哈迈德·R2 金品均格",
            1002042: "艾哈迈德·R3 紫品均格",
            1002043: "艾哈迈德·R4 蓝品均格",
            1002044: "艾哈迈德·R5 白绿件数",
        }
        if skill_id in ahmed_labels:
            return ahmed_labels[skill_id]
    if hero_id == 208:
        ethan_labels = {
            1002081: "伊森·R1 类别轮廓",
            1002082: "伊森·R2 已知品质轮廓",
            1002083: "伊森·R3 已知品质轮廓",
            1002084: "伊森·R4 已知品质轮廓",
            1002085: "伊森·R5 全仓轮廓",
        }
        if skill_id in ethan_labels:
            return ethan_labels[skill_id]
    if hero_id == 107:
        sophie_labels = {
            100107: "索菲·R1 揭示(基)",
            1001071: "索菲·R2 揭示",
            1001072: "索菲·R3 揭示",
            1001073: "索菲·R4 揭示",
            1001074: "索菲·R5 揭示",
        }
        if skill_id in sophie_labels:
            return sophie_labels[skill_id]
    if hero_id == 209:
        if skill_id == 100209:
            return "维克托·R1 紫金红件数"
    if hero_id == 301:
        if skill_id == 100301:
            return "拉文·R5 全品质"
    if hero_id == 303 and skill_id == 1003031:
        return "印第安纳·R1 未知箱轮廓"
    # 英雄技能映射没认领 -> 查道具表(玩家用的付费道具), 标「道具·名」与英雄技能区分。
    item_name = _battle_item_name(skill_id)
    if item_name:
        return f"道具·{item_name}"
    return f"技能 {skill_id}"


_BATTLE_ITEMS_CACHE: dict[int, str] | None = None


_POSTUPDATE_SKILL_PARENT_IDS: dict[int, int] = {
    # Display/action-stage hierarchy only. This mapping is not protocol proof
    # that a nested full-info row is the same physical item as one item in the
    # immediate parent row. Physical enrichment/dedup stays keyed by the
    # observed runtime identity (or the existing footprint fallback).
    10010921: 1001092,
    10010931: 1001093,
    10010941: 1001094,
    10020221: 1002022,
    10020231: 1002023,
    10020241: 1002024,
    10020631: 1002063,
    10020641: 1002064,
    10020651: 1002065,
    1003033: 1003031,
    1003034: 1003031,
    1003035: 1003031,
}


_CANONICAL_R1_SKILL_ROUND_INDEX: dict[tuple[int, int], int] = {
    # The current wire omits FatbeansSkillReveal.field_6 for these opening
    # reveals.  Their skill identities themselves are the narrow protocol
    # authority for zero-based R1; a later cumulative state round is not.
    (103, 1001034): 0,  # Aisha white outline
    (104, 1001041): 0,  # Gabriela opening reveal
    (303, 1003031): 0,  # Indiana unknown-box outline
}
_EXPECTED_WIRE_SKILL_ROUND_INDEX: dict[tuple[int, int], int] = {
    # Real 68171c frames prove these child IDs and zero-based wire rounds.  A
    # missing round remains unavailable; static Skill rows do not synthesize it.
    (303, 1003033): 2,
    (303, 1003034): 3,
    (303, 1003035): 4,
}


def _skill_reveal_round_projection(
    *,
    hero_id: int | None,
    skill_id: int,
    wire_round_index: object,
) -> tuple[int | None, str, int | None]:
    canonical = (
        _CANONICAL_R1_SKILL_ROUND_INDEX.get((hero_id, skill_id))
        if type(hero_id) is int  # noqa: E721 - reject bool/subclasses
        else None
    )
    expected_wire = (
        _EXPECTED_WIRE_SKILL_ROUND_INDEX.get((hero_id, skill_id))
        if type(hero_id) is int  # noqa: E721 - reject bool/subclasses
        else None
    )
    if wire_round_index is None:
        if canonical is not None:
            return canonical, "skill_canonical", None
        return None, "unavailable", None
    if type(wire_round_index) is not int or wire_round_index < 0:  # noqa: E721
        return None, "conflict", None
    if canonical is not None and wire_round_index != canonical:
        return None, "conflict", wire_round_index
    if expected_wire is not None and wire_round_index != expected_wire:
        return None, "conflict", wire_round_index
    return wire_round_index, "wire", None


def _battle_item_name(item_id: int | None) -> str | None:
    """道具 id -> 名(battle_items.json, 缓存)。仅给「英雄技能映射未认领」的 id 用。"""
    global _BATTLE_ITEMS_CACHE
    if _BATTLE_ITEMS_CACHE is None:
        _BATTLE_ITEMS_CACHE = {}
        try:
            from ..secure.data_crypt import load_json  # moat 透明解密(加密包)/ 明文直读(源码)
            p = Path(__file__).resolve().parents[3] / "data" / "processed" / "battle_items.json"
            for x in load_json(p):
                bid = x.get("battle_item_id")
                if isinstance(bid, int) and x.get("name"):
                    _BATTLE_ITEMS_CACHE[bid] = str(x["name"])
        except Exception:  # noqa: BLE001 - 缺表则不解析, 回退技能 id
            _BATTLE_ITEMS_CACHE = {}
    return _BATTLE_ITEMS_CACHE.get(item_id) if item_id is not None else None


def _skill_reveal_rows(
    events: FatbeansCaptureEvents,
    items: Mapping[int, Item],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    item_names = _item_names(items)
    latest_by_skill: dict[tuple[Any, ...], dict[str, Any]] = {}

    def session_matches(candidate: str | None) -> bool:
        return session_id is None or candidate is None or candidate == session_id

    for state in events.states:
        if not session_matches(state.session_id):
            continue
        if state.message_id == 0x002D or state.inventory_items:
            continue
        for reveal in state.skill_reveals:
            skill_id = int(reveal.skill_id)
            hero_id = reveal.hero_id
            if hero_id == 303:
                # Indiana may reuse its parent skill id for later reveal events.
                # Preserve distinct observed events by exact event/round/runtime
                # identity, without manufacturing the static child skill ids or
                # changing the historical last-wins public shape for old heroes.
                runtime_identity = tuple(
                    sorted(
                        item.runtime_id
                        for item in reveal.observed_items
                        if item.runtime_id is not None
                    )
                )
                key = (
                    skill_id,
                    hero_id,
                    state.sort_id,
                    reveal.round_index,
                    runtime_identity,
                )
            else:
                key = (skill_id, hero_id)
            # Last-wins stays within one actual lineage. A later legacy
            # unknown-session event cannot erase a confirmed current one.
            key = (state.session_id, *key)
            detail = _observed_items_detail(reveal.observed_items, item_names)
            latest_by_skill[key] = {
                "sort": state.sort_id,
                "time": state.capture_time,
                # Preserve the actual event lineage, including unknown. The
                # permissive legacy session filter must not stamp it current.
                "session_id": state.session_id,
                "state_round_index": state.round_index,
                "skill_id": skill_id,
                "hero_id": hero_id,
                "tool": _skill_reveal_label(skill_id, hero_id),
                "result": reveal.result,
                "result_field": reveal.result_field,
                "revealed_items": len(reveal.observed_items),
                "revealed_items_detail": detail,
                "observed_items": detail,
                "revealed_summary": _observed_action_summary(
                    reveal.observed_items,
                    item_names,
                ),
            }
            if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) != "1":
                round_index, round_index_state, conflicting_wire_round = (
                    _skill_reveal_round_projection(
                        hero_id=hero_id,
                        skill_id=skill_id,
                        wire_round_index=reveal.round_index,
                    )
                )
                latest_by_skill[key]["round_index"] = round_index
                latest_by_skill[key]["round_index_state"] = round_index_state
                if conflicting_wire_round is not None:
                    latest_by_skill[key]["round_index_wire"] = (
                        conflicting_wire_round
                    )
                parent_skill_id = _POSTUPDATE_SKILL_PARENT_IDS.get(skill_id)
                if parent_skill_id is not None:
                    latest_by_skill[key]["parent_skill_id"] = parent_skill_id
    return sorted(
        latest_by_skill.values(),
        key=lambda row: int(row.get("sort") or 0),
        reverse=True,
    )


def _skill_reveals_for_ref(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "skill_id": row.get("skill_id"),
            "hero_id": row.get("hero_id"),
            "session_id": row.get("session_id"),
            "state_round_index": row.get("state_round_index"),
            "result": row.get("result"),
            "result_field": row.get("result_field"),
            "observed_items": list(row.get("observed_items") or row.get("revealed_items_detail") or ()),
            **(
                {
                    "sort": row.get("sort"),
                    "round_index": row.get("round_index"),
                    "round_index_state": row.get("round_index_state"),
                    "round_index_wire": row.get("round_index_wire"),
                    "parent_skill_id": row.get("parent_skill_id"),
                }
                if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) != "1"
                else {}
            ),
        }
        for row in rows
        if isinstance(row, Mapping)
    ]


_STRUCTURED_REF_HERO_ALIASES = frozenset(
    {
        "aisha",
        "艾莎",
        "ahmed",
        "ahmad",
        "艾哈迈德",
        "gabriela",
        "加布里埃拉",
        "raven",
        "拉文",
        "victor",
        "维克托",
        "naomi",
        "娜奥米",
        "indiana",
        "印第安纳",
    }
)


def _is_unknown_live_hero(value: Any) -> bool:
    return str(value or "").strip().lower() in {"", "?", "unknown", "none", "null"}


def _normalize_structured_ref_hero(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in _STRUCTURED_REF_HERO_ALIASES:
        if text in {"ahmad", "艾哈迈德"}:
            return "ahmed"
        if text in {"艾莎"}:
            return "aisha"
        if text in {"加布里埃拉"}:
            return "gabriela"
        if text in {"拉文"}:
            return "raven"
        if text in {"维克托"}:
            return "victor"
        if text in {"娜奥米"}:
            return "naomi"
        if text in {"印第安纳"}:
            return "indiana"
        return text
    return None


def _infer_hero_from_batches(batches: Sequence[LiveObservationBatch]) -> str | None:
    for batch in reversed(batches):
        for update in batch.field_updates:
            if tuple(update.path[:2]) != ("session", "hero"):
                continue
            hero = _normalize_structured_ref_hero(update.value)
            if hero is not None:
                return hero
    return None


def _resolve_live_hero(
    events: FatbeansCaptureEvents,
    base_session: Any,
    *,
    session_id: str | None = None,
) -> str | None:
    if session_id is not None:
        for state in reversed(events.states):
            if state.session_id != session_id:
                continue
            inferred = hero_mode_from_state(state)
            if inferred is not None:
                return inferred
        return None
    hero = base_session.hero if base_session is not None else None
    if not _is_unknown_live_hero(hero):
        return str(hero)
    for state in reversed(events.states):
        inferred = hero_mode_from_state(state)
        if inferred is not None:
            return inferred
    return None


def _infer_hero_id_from_events(
    events: FatbeansCaptureEvents,
    *,
    session_id: str | None = None,
) -> int | None:
    seen: set[int] = set()
    for state in reversed(events.states):
        if session_id is not None and state.session_id != session_id:
            continue
        for reveal in state.skill_reveals or ():
            if reveal.hero_id is not None:
                seen.add(int(reveal.hero_id))
        for bid in state.bids or ():
            if bid.hero_id is not None:
                seen.add(int(bid.hero_id))
    if len(seen) == 1:
        return next(iter(seen))
    return None


# 精简揭示 footprint 字段(纯几何, 无 PII/无名): 供云端样本【所有英雄】回放。引擎
# _minimap_items_from_snapshot 读同名字段(row/col/cells/width/height/local_index/quality/shape_key)。
_LEGACY_REVEAL_FOOTPRINT_KEYS = (
    "quality", "item_id", "cells", "shape_key",
    "local_index", "row", "col", "width", "height", "category", "value",
)
_REVEAL_FOOTPRINT_KEYS = (
    "quality", "item_id", "cells", "shape_key",
    "runtime_id", "local_index", "row", "col", "width", "height",
    "category", "categories", "category_labels", "excluded_categories",
    "excluded_category_labels", "excluded_qualities", "quality_source", "value",
)


def _physical_category_domain_enabled() -> bool:
    return os.environ.get(PHYSICAL_CATEGORY_DOMAIN_DISABLE) != "1"


def _physical_candidate_domain_rows(
    domains: Sequence[PhysicalCandidateDomain | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain in domains:
        raw = (
            domain.as_dict()
            if isinstance(domain, PhysicalCandidateDomain)
            else domain
        )
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        if type(row.get("runtime_id")) is not int or not (
            row.get("required_categories") or row.get("excluded_categories")
        ):
            continue
        rows.append(row)
    return rows


def _shape_average_mandatory_units_by_cells(
    problem: ResidualProblem | None,
) -> dict[int, tuple[dict[str, Any], ...]]:
    """Project known physical identities into shape-average support units."""

    if problem is None:
        return {}
    # Keep this an exact builtin dict.  Cython narrows the PEP 526 ``dict``
    # annotation and rejects a ``defaultdict`` here even though CPython accepts
    # the subclass.  ``setdefault`` preserves the same identity-deduping
    # behavior on both source and protected-native paths.
    units_by_cells: dict[int, dict[str, dict[str, Any]]] = {}
    for domain in problem.physical_candidate_domains:
        if (
            not (domain.hard_eligible or domain.physical_support_eligible)
            or domain.catalog_status != "ok"
            or domain.cells not in SIZE_AVG_ACTION_FOOTPRINT.values()
            or not domain.candidate_item_ids
        ):
            continue
        units_by_cells.setdefault(domain.cells, {})[domain.key] = {
            "identity": domain.key,
            "candidate_item_ids": list(domain.candidate_item_ids),
            "catalog_generation": domain.catalog_generation,
        }
    # Exact item evidence is stronger than a category/shape option domain for
    # the same physical identity.  EvidenceStore excludes settlement inventory
    # on this pre-bid path, so these units never inject settlement truth.
    for anchor in problem.anchors:
        if anchor.cells not in SIZE_AVG_ACTION_FOOTPRINT.values():
            continue
        units_by_cells.setdefault(anchor.cells, {})[anchor.key] = {
            "identity": anchor.key,
            "candidate_item_ids": [anchor.item_id],
            "expected_quality": anchor.quality,
            "expected_cells": anchor.cells,
            "expected_value": anchor.value,
        }
    return {
        cells: tuple(by_identity[key] for key in sorted(by_identity))
        for cells, by_identity in units_by_cells.items()
    }


def _apply_physical_candidate_domains_to_minimap(
    rows: Sequence[Mapping[str, Any]],
    domains: Sequence[PhysicalCandidateDomain | Mapping[str, Any]],
    *,
    items: Mapping[int, Item] | None = None,
) -> list[dict[str, Any]]:
    by_runtime = {
        row["runtime_id"]: row
        for row in _physical_candidate_domain_rows(domains)
    }
    seen_runtime_ids: set[int] = set()
    out: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        domain = by_runtime.get(row.get("runtime_id"))
        if domain is None:
            out.append(row)
            continue
        seen_runtime_ids.add(domain["runtime_id"])
        categories = tuple(
            int(category)
            for category in domain.get("required_categories", ())
            if type(category) is int
        )
        excluded = tuple(
            int(category)
            for category in domain.get("excluded_categories", ())
            if type(category) is int
        )
        excluded_qualities = tuple(
            int(quality)
            for quality in domain.get("excluded_qualities", ())
            if type(quality) is int
        )
        row["categories"] = list(categories)
        row["category_labels"] = [
            _CATEGORY_LABELS.get(category, str(category))
            for category in categories
        ]
        row["excluded_categories"] = list(excluded)
        row["excluded_category_labels"] = [
            _CATEGORY_LABELS.get(category, str(category))
            for category in excluded
        ]
        row["excluded_qualities"] = list(excluded_qualities)
        if categories:
            row["category"] = categories[0]
            row["category_label"] = " + ".join(row["category_labels"])
        quality = domain.get("quality")
        if type(quality) is int and row.get("quality") in {None, quality}:
            row["quality"] = quality
            row["quality_source"] = domain.get("quality_source") or ""
        exact_item_id = domain.get("exact_item_id")
        exact_value = domain.get("exact_value")
        if type(exact_item_id) is int and type(exact_value) is int:
            row["item_id"] = exact_item_id
            row["value"] = exact_value
            if items is not None:
                row["item_name"] = _grid_item_name(exact_item_id, items)
        out.append(row)
    for runtime_id, domain in by_runtime.items():
        if runtime_id in seen_runtime_ids:
            continue
        shape_key = domain.get("shape_key")
        local_index = domain.get("local_index")
        cells = domain.get("cells")
        if (
            type(shape_key) is not str
            or len(shape_key) != 2
            or not shape_key.isdigit()
            or type(local_index) is not int
            or local_index < 0
            or type(cells) is not int
            or cells <= 0
        ):
            continue
        width = int(shape_key[0])
        height = int(shape_key[1])
        categories = [
            int(category)
            for category in domain.get("required_categories", ())
            if type(category) is int
        ]
        excluded = [
            int(category)
            for category in domain.get("excluded_categories", ())
            if type(category) is int
        ]
        excluded_qualities = [
            int(quality)
            for quality in domain.get("excluded_qualities", ())
            if type(quality) is int
        ]
        exact_item_id = domain.get("exact_item_id")
        exact_value = domain.get("exact_value")
        row = {
            "runtime_id": runtime_id,
            "local_index": local_index,
            "cells": cells,
            "shape_key": shape_key,
            "row": local_index // 10 + 1,
            "col": local_index % 10 + 1,
            "width": width,
            "height": height,
            "quality": domain.get("quality"),
            "category": categories[0] if categories else None,
            "category_label": " + ".join(
                _CATEGORY_LABELS.get(category, str(category))
                for category in categories
            ),
            "categories": categories,
            "category_labels": [
                _CATEGORY_LABELS.get(category, str(category))
                for category in categories
            ],
            "excluded_categories": excluded,
            "excluded_category_labels": [
                _CATEGORY_LABELS.get(category, str(category))
                for category in excluded
            ],
            "excluded_qualities": excluded_qualities,
            "quality_source": domain.get("quality_source") or "",
            "item_id": exact_item_id if type(exact_item_id) is int else None,
            "item_name": (
                _grid_item_name(exact_item_id, items)
                if type(exact_item_id) is int and items is not None
                else ""
            ),
            "value": exact_value if type(exact_value) is int else None,
            "display_label": "",
            "render_mode": "footprint",
            "source": "physical_category_domain",
            "layout_source": "live_grid",
        }
        out.append(row)
    return out


def _reveal_footprints_for_replay(
    batches: Sequence[LiveObservationBatch],
    items: Mapping[int, Item] | None,
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """最后一个【pre-settled】批的揭示 footprint(精简·纯几何)。

    为什么: `structured_ref_inputs` 只带【聚合数值契约】(每档 counts/avg_cells/...), 但 draw/揭示英雄
    (aisha/gabriela/sophie/ethan)的估计还依赖【每件揭示轮廓的 footprint/行列位置】(可见锚=可见最深行×列),
    聚合数值无法复现。故补这块 → 云端样本【所有英雄】都能忠实回放 live 估计(否则这类英雄的回放不可用)。
    刻意用【pre-settled 揭示态】(竞价期引擎所见), 非 settlement 全真相。字段名对齐引擎消费端。"""
    if items is None:
        return []
    for batch in reversed(batches):
        if session_id is not None and batch.session_id != session_id:
            continue
        if batch.phase == "settled" or batch.event_kind == "session_settled":
            continue
        if not getattr(batch, "grid_items", None):
            continue
        rows = _minimap_rows_from_batch(batch, items)  # 与 live snapshot 同源(目录增强 shape/row/col)
        keys = (
            _REVEAL_FOOTPRINT_KEYS
            if _physical_category_domain_enabled()
            else _LEGACY_REVEAL_FOOTPRINT_KEYS
        )
        return [{k: r.get(k) for k in keys} for r in rows]
    return []


def _ahmad_ref_inputs_from_batches(
    batches: Sequence[LiveObservationBatch],
    *,
    hero: str | None,
    items: Mapping[int, Item] | None = None,
    session_id: str | None = None,
    physical_candidate_domains: Sequence[
        PhysicalCandidateDomain | Mapping[str, Any]
    ] = (),
) -> dict[str, Any]:
    """Compact pre-settlement bridge for the isolated structured reference overlay."""

    scoped_batches = tuple(
        batch
        for batch in batches
        if session_id is None or batch.session_id == session_id
    )
    hero_key = (
        _normalize_structured_ref_hero(hero)
        or _infer_hero_from_batches(scoped_batches)
    )
    if hero_key is None:
        return {}
    field_updates: list[dict[str, Any]] = []
    latest_values: dict[tuple[str, ...], Any] = {}
    for batch in scoped_batches:
        if batch.phase == "settled" or batch.event_kind == "session_settled":
            continue
        split_paths_in_batch = {
            (str(update.path[1]), str(update.path[2]))
            for update in batch.field_updates
            if len(update.path) >= 3 and tuple(update.path[:1]) == ("bucket_split",)
        }
        for update in batch.field_updates:
            path = tuple(str(part) for part in update.path)
            if not path:
                continue
            if (
                path[0] == "bucket"
                and len(path) >= 3
                and path[1] in {"1", "2"}
                and (
                    ("white" if path[1] == "1" else "green"),
                    path[2],
                )
                in split_paths_in_batch
            ):
                continue
            if path[0] == "session":
                relevant = path[1:] in {
                    ("total_item_count",),
                    ("total_count",),
                    ("total_cells",),
                    ("warehouse_total_cells",),
                    ("total_avg_cells",),
                }
            elif path[0] == "bucket_group" and len(path) >= 3:
                relevant = path[2] == "count"
            elif path[0] == "category_quality_group" and len(path) >= 4:
                relevant = path[3] == "count"
            elif path[0] == "bucket" and len(path) >= 3:
                relevant = path[2] in {
                    "avg_cells",
                    "count",
                    "cells",
                    "total_cells",
                    "avg_value",
                    "average_value",
                    "value",
                    "value_sum",
                    "total_value",
                }
            elif path[0] == "bucket_split" and len(path) >= 3:
                relevant = path[2] in {
                    "avg_cells",
                    "count",
                    "cells",
                    "total_cells",
                    "value",
                    "value_sum",
                    "total_value",
                }
            else:
                relevant = False
            if not relevant:
                continue
            latest_values[path] = update.value
            field_updates.append(
                {
                    "path": list(path),
                    "value": update.value,
                    "source": update.source,
                    "confidence": update.confidence,
                    "sequence": update.sequence or batch.sequence,
                    "event_kind": batch.event_kind,
                    "phase": batch.phase,
                }
            )

    avg_cells: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    quality_cells: dict[str, Any] = {}
    avg_values: dict[str, Any] = {}
    quality_values: dict[str, Any] = {}
    count_sums: dict[str, Any] = {}
    scoped_count_sums: list[dict[str, Any]] = []
    split_avg_cells: dict[str, Any] = {}
    split_counts: dict[str, Any] = {}
    split_quality_cells: dict[str, Any] = {}
    quality_value_floors: dict[str, Any] = {}
    for path, value in sorted(latest_values.items()):
        if path[:1] == ("session",):
            continue
        if len(path) >= 3 and path[0] == "bucket_group":
            if path[2] == "count":
                count_sums[str(path[1])] = value
            continue
        if len(path) >= 4 and path[0] == "category_quality_group":
            if path[3] == "count":
                categories = [part for part in str(path[1]).split("+") if part]
                qualities = [
                    f"q{part}"
                    for part in str(path[2]).removeprefix("q").split("q")
                    if part
                ]
                count_sums[f"{path[2]}_min"] = value
                scoped_count_sums.append(
                    {
                        "categories": categories,
                        "qualities": qualities,
                        "count": value,
                        "relation": "exact_within_scope",
                        "global_relation": "minimum",
                    }
                )
            continue
        if len(path) >= 3 and path[0] == "bucket_split":
            split_key = str(path[1])
            if path[2] == "avg_cells":
                split_avg_cells[split_key] = value
            elif path[2] == "count":
                split_counts[split_key] = value
            elif path[2] in {"cells", "total_cells"}:
                split_quality_cells[split_key] = value
            elif path[2] in {"value", "value_sum", "total_value"} and split_key == "green":
                # Green-only 普品估价 is a lower bound for the merged white+green q1 bucket.
                quality_value_floors["q1"] = value
            continue
        if len(path) < 3 or path[0] != "bucket":
            continue
        quality = str(path[1])
        if quality == "2":
            continue
        key = "q1" if quality == "1" else f"q{quality}"
        if path[2] == "avg_cells":
            avg_cells[key] = value
        elif path[2] == "count":
            counts[key] = value
        elif path[2] in {"cells", "total_cells"}:
            quality_cells[key] = value
        elif path[2] in {"avg_value", "average_value"}:
            avg_values[key] = value
        elif path[2] in {"value", "value_sum", "total_value"}:
            quality_values[key] = value

    total_count = latest_values.get(("session", "total_item_count"))
    if total_count is None:
        total_count = latest_values.get(("session", "total_count"))
    total_cells = latest_values.get(("session", "total_cells"))
    if total_cells is None:
        total_cells = latest_values.get(("session", "warehouse_total_cells"))
    total_avg_cells = latest_values.get(("session", "total_avg_cells"))
    minimap_items = _reveal_footprints_for_replay(
        scoped_batches,
        items,
        session_id=session_id,
    )
    physical_domain_rows = _physical_candidate_domain_rows(
        physical_candidate_domains
    )
    if physical_domain_rows:
        minimap_items = _apply_physical_candidate_domains_to_minimap(
            minimap_items,
            physical_domain_rows,
            items=items,
        )
    result = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": hero_key,
        "total_count": total_count,
        "total_cells": total_cells,
        "total_avg_cells": total_avg_cells,
        "avg_cells": avg_cells,
        "counts": counts,
        "quality_cells": quality_cells,
        "avg_values": avg_values,
        "quality_values": quality_values,
        "quality_value_floors": quality_value_floors,
        "count_sums": count_sums,
        "scoped_count_sums": scoped_count_sums,
        "split_avg_cells": split_avg_cells,
        "split_counts": split_counts,
        "split_quality_cells": split_quality_cells,
        "field_updates": field_updates,
        # 揭示 footprint(pre-settled·纯几何): draw/揭示英雄回放可见锚/footprint 所需, 聚合数值无法复现。
        "minimap_items": minimap_items,
    }
    if physical_domain_rows:
        result["physical_candidate_domains"] = physical_domain_rows
    return result


def _grid_item_table_shape_key(
    item: GridItemObservation,
    items: Mapping[int, Item],
) -> str | None:
    if item.shape_key:
        return item.shape_key
    if item.local_index is None or item.item_id is None:
        return None
    table_item = items.get(int(item.item_id))
    if table_item is None or table_item.shape_w <= 0 or table_item.shape_h <= 0:
        return None
    if item.cells and table_item.shape_w * table_item.shape_h != item.cells:
        return None
    return f"{table_item.shape_w}{table_item.shape_h}"


def _grid_item_table_category(
    item: GridItemObservation,
    items: Mapping[int, Item],
) -> int | None:
    if item.category is not None:
        return item.category
    if item.item_id is None:
        return None
    table_item = items.get(int(item.item_id))
    if table_item is None:
        return None
    for tag in table_item.tags:
        if int(tag) in _CATEGORY_LABELS:
            return int(tag)
    return None


def _minimap_rows_from_batch(
    batch: LiveObservationBatch,
    items: Mapping[int, Item],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    layout_source = (
        "settlement_inventory" if batch.phase == "settled" else "live_grid"
    )
    for item in batch.grid_items:
        shape_key = _grid_item_table_shape_key(item, items)
        fallback_category = _grid_item_table_category(item, items)
        categories = tuple(
            sorted(
                {
                    *item.categories,
                    *(
                        (item.category,)
                        if item.category is not None
                        else ()
                    ),
                    *(
                        (fallback_category,)
                        if fallback_category is not None
                        else ()
                    ),
                }
            )
        )
        category = categories[0] if categories else None
        display_item = (
            item
            if (
                shape_key == item.shape_key
                and category == item.category
                and categories == item.categories
            )
            else replace(
                item,
                shape_key=shape_key,
                category=category,
                categories=categories,
            )
        )
        footprint = display_item.footprint()
        row = footprint.row if footprint is not None else None
        col = footprint.col if footprint is not None else None
        width = footprint.width if footprint is not None else None
        height = footprint.height if footprint is not None else None
        if shape_key in (None, "") and item.value is not None and item.item_id is None:
            shape_key = "11"
            width = width or 1
            height = height or 1
        # Only anchor an item to its local_index cell when we have a usable shape.
        # When the cell count does not match any known shape (shape_key stays None),
        # the item is not drawable, so leave row/col unset rather than guessing a
        # position from local_index alone.
        if (
            (row is None or col is None)
            and item.local_index is not None
            and shape_key not in (None, "")
        ):
            row = item.local_index // 10 + 1
            col = item.local_index % 10 + 1
            width = width or 1
            height = height or 1
        value_label = (
            f"{item.value:,}"
            if item.value is not None and item.quality is None and not item.item_id
            else ""
        )
        category_label = (
            _CATEGORY_LABELS.get(category, str(category))
            if category is not None
            else ""
        )
        category_fields: dict[str, Any] = {}
        if _physical_category_domain_enabled():
            category_label = " + ".join(
                _CATEGORY_LABELS.get(raw_category, str(raw_category))
                for raw_category in categories
            )
            category_fields = {
                "categories": list(categories),
                "category_labels": [
                    _CATEGORY_LABELS.get(raw_category, str(raw_category))
                    for raw_category in categories
                ],
                "excluded_categories": list(item.excluded_categories),
                "excluded_category_labels": [
                    _CATEGORY_LABELS.get(category, str(category))
                    for category in item.excluded_categories
                ],
                "excluded_qualities": list(item.excluded_qualities),
            }
        rows.append(
            {
                "category": category,
                "category_label": category_label,
                **category_fields,
                "quality": item.quality,
                "runtime_id": item.runtime_id,
                "item_id": item.item_id,
                "item_name": _grid_item_name(item.item_id, items) or value_label,
                "local_index": item.local_index,
                "cells": item.cells,
                "shape_key": shape_key,
                "row": row,
                "col": col,
                "width": width,
                "height": height,
                "value": item.value,
                "display_label": "",
                "render_mode": "footprint",
                "source": item.source,
                "layout_source": layout_source,
            }
        )
    return rows


def _category_grid_items(
    batches: Sequence[LiveObservationBatch],
    items: Mapping[int, Item],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    scoped_batches = tuple(
        batch
        for batch in batches
        if session_id is None or batch.session_id == session_id
    )
    batch = latest_grid_batch(scoped_batches)
    if batch is None:
        return []
    rows: list[dict[str, Any]] = []
    for item in batch.grid_items:
        categories = tuple(
            sorted(
                {
                    *item.categories,
                    *((item.category,) if item.category is not None else ()),
                }
            )
        )
        if not categories:
            continue
        footprint = item.footprint()
        category_fields: dict[str, Any] = {}
        category_label = _CATEGORY_LABELS.get(
            categories[0],
            str(categories[0]),
        )
        if _physical_category_domain_enabled():
            category_label = " + ".join(
                _CATEGORY_LABELS.get(category, str(category))
                for category in categories
            )
            category_fields = {
                "categories": list(categories),
                "category_labels": [
                    _CATEGORY_LABELS.get(category, str(category))
                    for category in categories
                ],
                "excluded_categories": list(item.excluded_categories),
                "excluded_category_labels": [
                    _CATEGORY_LABELS.get(category, str(category))
                    for category in item.excluded_categories
                ],
                "excluded_qualities": list(item.excluded_qualities),
            }
        rows.append(
            {
                "category": categories[0],
                "category_label": category_label,
                **category_fields,
                "quality": item.quality,
                "runtime_id": item.runtime_id,
                "item_id": item.item_id,
                "item_name": _grid_item_name(item.item_id, items),
                "local_index": item.local_index,
                "cells": item.cells,
                "shape_key": item.shape_key,
                "row": footprint.row if footprint is not None else None,
                "col": footprint.col if footprint is not None else None,
                "width": footprint.width if footprint is not None else None,
                "height": footprint.height if footprint is not None else None,
                "source": item.source,
            }
        )
    return rows


def _minimap_grid_items(
    batches: Sequence[LiveObservationBatch],
    items: Mapping[int, Item],
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    scoped_batches = tuple(
        batch
        for batch in batches
        if session_id is None or batch.session_id == session_id
    )
    for candidate in reversed(scoped_batches):
        if candidate.phase != "settled" or not candidate.grid_items:
            continue
        rows = _minimap_rows_from_batch(candidate, items)
        if any(row.get("row") is not None and row.get("col") is not None for row in rows):
            return rows

    batch = latest_grid_batch(scoped_batches)
    if batch is None:
        return []
    return _minimap_rows_from_batch(batch, items)


def _parse_int_text(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _resolve_shadow_trials(n_trials: int, shadow_trials: int | None) -> int:
    if shadow_trials is not None:
        return max(1, int(shadow_trials))
    return max(1, min(int(n_trials), DEFAULT_Q6_SHADOW_TRIALS_CAP))


def _empty_v3_posterior_shadow(
    *,
    trials: int,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "label": "v3_summary_likelihood",
        "affects_bid": False,
        "trials": max(0, int(trials)),
        "error": error,
        "numeric_constraints": 0,
        "item_anchors": 0,
        "shape_anchors": 0,
        "quality_floor_anchors": 0,
        "conflicts": 0,
        **empty_feasible_summary_flat_dict(),
        **empty_posterior_flat_dict(),
        **empty_posterior_flat_dict(prefix="v3_ccv_"),
        **empty_posterior_flat_dict(prefix="v3_resid_"),
        **empty_residual_gate_flat_dict(),
        **empty_prior_flat_dict(),
        **empty_prior_robustness_flat_dict(),
        **empty_prior_calibration_flat_dict(),
        **empty_underestimate_repair_flat_dict(),
        **empty_tail_value_review_flat_dict(),
        **empty_formal_value_sampler_flat_dict(),
        **empty_settlement_count_prior_flat_dict(),
        **empty_capacity_source_expansion_flat_dict(),
        **empty_practical_advisory_flat_dict(),
    }


def _default_v3_prior_calibration_entries() -> dict[int, Any]:
    global _V3_PRIOR_CALIBRATION_CACHE
    if _V3_PRIOR_CALIBRATION_CACHE is None:
        _V3_PRIOR_CALIBRATION_CACHE = load_prior_calibration_entries(
            project_root() / "data" / "processed" / "v3_prior_calibration_shadow.json"
        )
    return _V3_PRIOR_CALIBRATION_CACHE


def _default_v3_underestimate_repair_entries() -> dict[tuple[str, int], Any]:
    global _V3_UNDERESTIMATE_REPAIR_CACHE
    if _V3_UNDERESTIMATE_REPAIR_CACHE is None:
        _V3_UNDERESTIMATE_REPAIR_CACHE = load_underestimate_repair_entries(
            project_root()
            / "data"
            / "processed"
            / "v3_underestimate_repair_shadow.json"
        )
    return _V3_UNDERESTIMATE_REPAIR_CACHE


def _default_v3_tail_value_review_entries() -> dict[tuple[str, int], Any]:
    global _V3_TAIL_VALUE_REVIEW_CACHE
    if _V3_TAIL_VALUE_REVIEW_CACHE is None:
        _V3_TAIL_VALUE_REVIEW_CACHE = load_tail_value_review_entries(
            project_root()
            / "data"
            / "processed"
            / "v3_tail_value_review_shadow.json"
        )
    return _V3_TAIL_VALUE_REVIEW_CACHE


def _default_v3_settlement_count_prior_entries() -> dict[tuple[str, str], Any]:
    global _V3_SETTLEMENT_COUNT_PRIOR_CACHE
    if _V3_SETTLEMENT_COUNT_PRIOR_CACHE is None:
        _V3_SETTLEMENT_COUNT_PRIOR_CACHE = load_settlement_count_prior_entries(
            project_root()
            / "data"
            / "processed"
            / "v3_settlement_count_prior_shadow.json"
        )
    return _V3_SETTLEMENT_COUNT_PRIOR_CACHE


def _default_v3_capacity_source_expansion_entries() -> dict[tuple[str, str], Any]:
    global _V3_CAPACITY_SOURCE_EXPANSION_CACHE
    if _V3_CAPACITY_SOURCE_EXPANSION_CACHE is None:
        _V3_CAPACITY_SOURCE_EXPANSION_CACHE = load_capacity_source_expansion_entries(
            project_root()
            / "data"
            / "processed"
            / "v3_capacity_source_expansion_shadow.json"
        )
    return _V3_CAPACITY_SOURCE_EXPANSION_CACHE


def _v3_posterior_shadow_summary(
    events: FatbeansCaptureEvents,
    *,
    map_id: int | None,
    hero: str | None = None,
    tables: MonitorTables,
    trials: int,
    seed: int,
) -> dict[str, Any]:
    out = _empty_v3_posterior_shadow(trials=trials)
    prior_fields = summarize_drop_prior_flat_dict(
        map_id,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
    )
    out.update(prior_fields)

    def finish(summary: Any | None = None) -> dict[str, Any]:
        out.update(
            assess_prior_robustness(
                map_id=map_id,
                map_family=_map_family_from_id(map_id),
                summary=summary,
                prior_fields=prior_fields,
                posterior_fields=out,
            ).to_flat_dict()
        )
        return out

    if map_id is None:
        out["error"] = "missing_map_id"
        return finish()
    bid_map = tables.maps.get(int(map_id))
    if bid_map is None:
        out["error"] = "unknown_map_id"
        return finish()
    if trials <= 0:
        out["error"] = "no_trials"
        return finish()
    summary = None
    try:
        evidence_events = events_from_fatbeans(events)
        constraints = compile_hard_constraints(evidence_events)
        summary = compile_feasible_summary(constraints)
        truths = sample_truth_bank(
            int(map_id),
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            n_trials=trials,
            seed=seed,
        )
        replacement_values = ordinary_shape_replacement_values(
            int(map_id),
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
        )
        calibration_entry = _default_v3_prior_calibration_entries().get(int(map_id))
        underestimate_entry = underestimate_entry_for(
            _default_v3_underestimate_repair_entries(),
            hero=hero,
            map_id=map_id,
        )
        tail_review_entry = tail_value_review_entry_for(
            _default_v3_tail_value_review_entries(),
            hero=hero,
            map_id=map_id,
        )
        settlement_count_prior_entry = settlement_count_prior_entry_for(
            _default_v3_settlement_count_prior_entries(),
            map_id=map_id,
        )
        capacity_source_expansion_entry = capacity_source_expansion_entry_for(
            _default_v3_capacity_source_expansion_entries(),
            map_id=map_id,
            map_family=_map_family_from_id(map_id),
        )
        pipeline = estimate_shadow_pipeline(
            map_id=int(map_id),
            map_name=bid_map.name,
            summary=summary,
            truths=truths,
            constraints=constraints,
            replacement_values=replacement_values,
            calibration_entry=calibration_entry,
            underestimate_entry=underestimate_entry,
            tail_review_entry=tail_review_entry,
            settlement_count_prior_entry=settlement_count_prior_entry,
            capacity_source_expansion_entry=capacity_source_expansion_entry,
            hero=hero,
            prior_fields=prior_fields,
            evidence_events=evidence_events,
        )
    except Exception as exc:
        out["error"] = type(exc).__name__
        return finish(summary)
    out.update(
        {
            "numeric_constraints": len(constraints.numeric),
            "item_anchors": len(constraints.item_anchors),
            "shape_anchors": len(constraints.shape_anchors),
            "quality_floor_anchors": len(constraints.quality_floor_anchors),
            "conflicts": len(constraints.conflicts),
        }
    )
    out.update(summary.to_flat_dict())
    out.update(pipeline.to_flat_dict())
    return finish(summary)


def _rank_summary_from_changes(
    self_rank_points: float | None,
    self_rank_tier: int | None,
    rank_changes: Sequence[Any],
) -> dict[str, Any] | None:
    """Serialize ranked evidence and project only one internally consistent slot 8."""
    if self_rank_points is None and not rank_changes:
        return None

    def safe_number(value: Any, *, nonnegative: bool = False) -> float | None:
        if type(value) not in (int, float):
            return None
        try:
            numeric = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(numeric) or (nonnegative and numeric < 0.0):
            return None
        return numeric

    serialized_changes: list[dict[str, int | float]] = []
    slot8_rows: list[tuple[float, float, float]] = []
    invalid_evidence = False
    for change in rank_changes:
        if type(change) is not FatbeansRankScoreChange or type(change.slot) is not int:
            invalid_evidence = True
            continue
        before = safe_number(change.before, nonnegative=True)
        delta = safe_number(change.delta)
        after = safe_number(change.after, nonnegative=True)
        if before is None or delta is None or after is None:
            invalid_evidence = True
            continue
        serialized_changes.append(
            {"slot": change.slot, "before": before, "delta": delta, "after": after}
        )
        if change.slot == 8:
            slot8_rows.append((before, delta, after))

    safe_self_rank_points = safe_number(self_rank_points, nonnegative=True)
    safe_self_rank_tier = (
        self_rank_tier
        if type(self_rank_tier) is int and 1 <= self_rank_tier <= 32
        else None
    )
    summary: dict[str, Any] = {
        "self_rank_points": safe_self_rank_points,
        "self_rank_tier": safe_self_rank_tier,
        "changes": serialized_changes,
    }
    if invalid_evidence or safe_self_rank_points is None or len(slot8_rows) != 1:
        return summary

    before, delta, after = slot8_rows[0]
    expected_after = before + delta
    if not math.isfinite(expected_after):
        return summary
    if not math.isclose(before, safe_self_rank_points, rel_tol=0.0, abs_tol=1e-6):
        return summary
    if not math.isclose(after, expected_after, rel_tol=0.0, abs_tol=1e-6):
        return summary
    summary.update(
        self_slot=8,
        self_before=before,
        self_delta=delta,
        self_after=after,
    )
    return summary


def build_monitor_artifact_from_events(
    events: FatbeansCaptureEvents,
    *,
    file: str = "",
    tables: MonitorTables,
    n_trials: int = 500,
    roi_trials: int = 250,
    shadow_trials: int | None = None,
    run_debug_shadows: bool = True,
    seed: int = 20260530,
    formal_mode: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable live monitor artifact from parsed events.

    ⚠️激活门(moat): 本函数【跑推理但刻意不 gate should_run_full】。它是 dev/eval/标定的共享入口
    (compare_synth_vs_real / measure_* / lever1 / hero_new_health / diagnose_* 等都调它)——加硬 gate
    会击穿全部离线 eval。**出货不走此路**: 出货启动器(start_live_npcap_overlay.ps1)跑 npcap monitor
    只捕获原始帧、不带 --formal-mode; 只有 dev 的 `run_windivert_live_monitor.py --formal-mode` 这条路会
    带推理调它。付费 moat 的【用户面计算门在 overlay】(run_reference_engine 三路 gate should_run_full)。
    → 若日后把 formal-mode 推理接进【出货路径】, 必须在那个【出货入口】补 should_run_full gate,
    不要加在本函数(=会断 eval)。见 memory moat-enforcement-fail-closed-audit。
    """
    started_at = time.perf_counter()
    formal_mode_requested = _resolve_live_formal_mode(formal_mode)
    formal_mode_applied = LIVE_FORMAL_MODE_V2
    formal_mode_reason = "no_inference_session"
    latest_session_id = _latest_confirmed_session_id(events)
    owner_evidence_status = _settlement_owner_evidence_status(
        events,
        expected_session_id=latest_session_id,
    )
    events = _session_scoped_events(events, latest_session_id)
    fresh_game_version, session_game_version_stable = (
        _fresh_session_game_version(tables, latest_session_id)
    )
    warehouse_generation = warehouse_generation_from_live_evidence(
        _latest_capture_time(events),
        game_version=(
            fresh_game_version if session_game_version_stable else None
        ),
        package_generation=tables.warehouse_package_generation,
    )
    live_game_generation_authorized = (
        not tables.warehouse_live_identity_check
        or (
            session_game_version_stable
            and warehouse_generation == tables.warehouse_package_generation
            and warehouse_generation
            in {
                OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
                HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
                OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
                OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
            }
        )
    )
    effective_rankmap_distribution_status = tables.rankmap_distribution_status
    if not live_game_generation_authorized:
        effective_rankmap_distribution_status = "game_generation_unverified"
    batches = live_batches_from_fatbeans_events(
        events,
        current_generation=(
            warehouse_generation
            if session_game_version_stable
            and warehouse_generation == tables.warehouse_package_generation
            else None
        ),
        current_session_id=latest_session_id,
    )
    (
        base_session,
        final_session,
        pre_settlement_state,
        _final_state,
    ) = _states_to_session(batches)
    latest_bids = latest_player_bids(events.states)
    self_bid_summary = local_player_bid_summary(events.states)
    # 本场福利(亏损分红): 游戏在结算帧 f5 对本机未拿仓路径给出直接值，已解析进
    # state.settlement_loss_units。拿仓者本人同样参与 10% 分红，但该路径按确认规则由结算真值计算。
    settlement_welfare = next(
        (s.settlement_loss_units for s in reversed(events.states)
         if s.settlement_loss_units is not None),
        None,
    )
    # 0x2D 顶层 protobuf walk 未消费的尾部字节数，仅保留为解析器诊断。非零值不等于
    # 结算缺件：安全 body-union 可以在尾字节仍存在时恢复完整库存。完整性必须由
    # settlement_incomplete、captured/inventory、optional declared 和品质闭合共同决定。
    # 详见 FatbeansStateEvent.settlement_parse_residual。
    _residuals = [s.settlement_parse_residual for s in events.states
                  if isinstance(getattr(s, "settlement_parse_residual", None), int)]
    settlement_parse_residual = max(_residuals) if _residuals else None
    # 排位赛排位分摘要(对局记录/覆盖层 UI; 纯展示, 不进任何估价/件数/格子/出价推理)。
    # self_rank_points/tier: 本机玩家赛前排位分/段位(出价玩家块 f16/f17)。
    # changes: 本局排位分变化(结算帧 f7/f8: 赛前/增减/赛后, 全玩家)。self_* 只来自
    # 唯一、严格有效且与本机赛前分一致的 slot 8；其他 slot 永不作为回退。
    _self_rp = _self_tier = None
    _local_pid = self_bid_summary.get("local_player_id")
    for _s in events.states:
        for _b in (_s.bids or ()):
            if _b.player_id == _local_pid and _b.rank_points is not None:
                _self_rp, _self_tier = _b.rank_points, _b.rank_tier
    _rank_changes = next(
        (s.rank_score_changes for s in reversed(events.states) if s.rank_score_changes), ()
    )
    # 本机那条 = slot 8。slot 7 是另一分数体系，偶尔与 slot 8 的赛前分相同；选择权留在
    # producer，HTML/API 只消费 self_*，不得再次遍历 changes 猜测。
    rank_summary = _rank_summary_from_changes(_self_rp, _self_tier, _rank_changes)
    # 各玩家排位分(昵称 -> 赛前分p/段位t, 来自各自出价块 f16/f17; 供看板显示【非我方】玩家排位)。
    # 仅排位局有(经典/活动 rank_points=None -> 空)。last-wins 取该昵称最新一次观测。
    # id=该玩家 player_id(稳定): 暂不显示, 供日后玩家改名后仍能按 id 追踪同一人, 避免误评估。
    _player_ranks: dict[str, Any] = {}
    for _s in events.states:
        for _b in (_s.bids or ()):
            if _b.name and _b.rank_points is not None:
                _player_ranks[str(_b.name)] = {"p": _b.rank_points, "t": _b.rank_tier, "id": _b.player_id}
    observed_round = _latest_round(events)
    action_round = _action_round(events)
    phase = _latest_phase(events)
    layout_replay_rows = list(
        layout_replay_rows_from_stages(
            layout_replay_stages(events),
            comparison_policy=SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY,
        )
    )
    layout_stage_rows = _brief_layout_stage_rows(layout_replay_rows)

    map_rows: list[dict[str, Any]] = []
    warehouse_rows: list[dict[str, Any]] = []
    v2_posterior_rows: list[dict[str, Any]] = []
    q6_residual_boost_shadow: dict[str, Any] = {}
    q6_formal_prior_floor: dict[str, Any] = {}
    q6_residual_deep_floor_shadow: dict[str, Any] = {}
    q6_residual_deep11_floor_shadow: dict[str, Any] = {}
    q6_residual_hidden_floor_shadow: dict[str, Any] = {}
    q6_residual_villa_floor_shadow: dict[str, Any] = {}
    q6_residual_ethan_villa_random_floor_shadow: dict[str, Any] = {}
    q6_residual_ethan_shipwreck_layout_conditional_shadow: dict[str, Any] = {}
    q6_residual_sampler_shadows: list[dict[str, Any]] = []
    q6_residual_boost_shadow_rows: list[dict[str, Any]] = []
    q6_aisha_bottom_row_risk: dict[str, Any] = {}
    q6_quality_only_local_risk: dict[str, Any] = {}
    tool_rows: list[dict[str, Any]] = []
    bid_rows: list[dict[str, Any]] = []
    v2_bid_rows: list[dict[str, Any]] = []
    v3_practical_bid_rows: list[dict[str, Any]] = []
    headline_raw_value_summary: Any = None  # 最终应用路径的原始价值分位 -> 平铺 balanced
    fallback_map_rows: list[dict[str, Any]] = []
    fallback_warehouse_rows: list[dict[str, Any]] = []
    fallback_bid_rows: list[dict[str, Any]] = []
    evidence_label = "暂无"
    evidence_profile_key = ""
    resolved_shadow_trials = _resolve_shadow_trials(n_trials, shadow_trials)
    v3_posterior_shadow: dict[str, Any] = _empty_v3_posterior_shadow(
        trials=resolved_shadow_trials,
    )
    inference_input_constraints: dict[str, Any] = {
        "mode": "no_inference_session",
    }
    drop_pool_support: dict[str, Any] = {}
    rankmap_distribution_support: dict[str, Any] = {}
    raven_chaotic_space_support: dict[str, Any] = {}
    auction_hall_6001_provisional: dict[str, Any] = {}
    problem: ResidualProblem | None = None
    store: Any = None
    model_map_id: int | None = None
    activity_map_alias: dict[str, Any] = {}
    # One immutable decision per artifact. Environment changes during a render
    # cannot create a half-enabled Raven route.
    raven_template_estimate_enabled = (
        os.environ.get(RAVEN_TEMPLATE_ESTIMATE_DISABLE_ENV) != "1"
    )
    active_map_id = (
        base_session.map_id
        if base_session is not None
        else _latest_map_id(events)
    )
    auction_hall_6001_provisional = _auction_hall_6001_support_receipt(
        active_map_id,
        tables,
    )
    if base_session is not None:
        activity_map_alias = _activity_shipwreck_alias(
            base_session.map_id,
            tables.maps,
            tables.drops,
        )
        if base_session.map_id in CURRENT_RANKMAP_MAP_IDS:
            if effective_rankmap_distribution_status == "ok":
                rankmap_distribution_support = dict(
                    tables.rankmap_distribution_receipt
                )
                rankmap_distribution_support.update(
                    {
                        "active_map_id": base_session.map_id,
                        "map_rebound": False,
                        "pool_rebound": False,
                    }
                )
            elif effective_rankmap_distribution_status != "not_configured":
                rankmap_distribution_support = {
                    "schema_version": 1,
                    "status": effective_rankmap_distribution_status,
                    "active_map_id": base_session.map_id,
                    "map_rebound": False,
                    "pool_rebound": False,
                }
        if base_session.map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS:
            raven_chaotic_space_support = _raven_chaotic_space_support_receipt(
                base_session.map_id,
                tables,
            )
            activity_map_alias = _raven_preopen_template_alias(
                base_session.map_id,
                tables,
                enabled=raven_template_estimate_enabled,
            )
            if activity_map_alias:
                raven_chaotic_space_support = dict(raven_chaotic_space_support)
                raven_chaotic_space_support.update(
                    {
                        "status": "estimated_template",
                        "model_map_id": activity_map_alias["model_map_id"],
                        "quote_available": True,
                        "estimated": True,
                        "observed_raven_games": 0,
                        "pool_graph_claim": False,
                        "exact_rate_claim": False,
                        "source_generation": CURRENT_PACKAGE_GENERATION,
                        "selector": activity_map_alias["selector"],
                        "direct_pool_proxy_mode": "shadow_probe_only",
                        "direct_pool_proxy_quote_authority": False,
                    }
                )
    if (
        base_session is not None
        and not live_game_generation_authorized
    ):
        model_map_id = base_session.map_id
        evidence_label = "game_generation_unverified"
        formal_mode_reason = "game_generation_unverified"
        inference_input_constraints = {
            "mode": "game_generation_unverified",
            "map_id": base_session.map_id,
            "package_generation": tables.warehouse_package_generation,
            "observed_game_version": fresh_game_version or None,
            "session_game_version_stable": session_game_version_stable,
        }
    elif active_map_id in (2511,4511):
        # Hero Ref consumes the selected per-map empirical calibration after
        # current source qualification. Generic MC must not use old route data.
        model_map_id = active_map_id
        activity_map_alias = {}
        evidence_label = "shipwreck_calibrated_ref_only"
        formal_mode_reason = "shipwreck_calibrated_ref_only"
        inference_input_constraints = {
            "mode": formal_mode_reason, "map_id": active_map_id, "model_map_id": active_map_id,
            "distribution_authority": False, "official_distribution_eligible": False,
            "calibration_status": ("historical_train_b049_transferred_76b7ca"
                if tables.warehouse_package_generation == "76b7ca-2026-10-08-723" else "empirical_train_b049"),
            "parameter_generation": "b049c3-2026-09-10-597",
            "applied_generation": tables.warehouse_package_generation,
        }
    elif active_map_id == 5215:
        model_map_id = 5215
        evidence_label = "wuqilin_5215_provisional_ref_only"
        formal_mode_reason = "wuqilin_5215_provisional_ref_only"
        inference_input_constraints = {
            "mode": formal_mode_reason,
            "map_id": 5215,
            "model_map_id": 5215,
            "quote_available": True,
            "quote_consumer": "hero_ref_current_scope_model",
            "distribution_authority": False,
            "activity_map_id": 20018,
        }
    elif active_map_id == 6001:
        model_map_id = 6001
        auction_status = auction_hall_6001_provisional.get(
            "status",
            "sidecar_unavailable",
        )
        provisional_ready = (
            auction_status == "provisional_estimated"
            and auction_hall_6001_provisional.get(
                "provisional_quote_available"
            )
            is True
        )
        evidence_label = f"6001_provisional:{auction_status}"
        formal_mode_reason = (
            "auction_hall_6001_provisional_estimated"
            if provisional_ready
            else "auction_hall_6001_provisional_unavailable"
        )
        inference_input_constraints = {
            "mode": formal_mode_reason,
            "map_id": 6001,
            "model_map_id": 6001,
            "provisional_distribution_available": provisional_ready,
            "provisional_quote_available": provisional_ready,
            "distribution_authority": False,
            "official_quote_authority": False,
            "sidecar_status": auction_status,
        }
    elif (
        base_session is not None
        and base_session.map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS
        and not activity_map_alias
    ):
        model_map_id = base_session.map_id
        raven_status = raven_chaotic_space_support.get("status", "mapping_conflict")
        evidence_label = (
            f"{RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON}:{raven_status}"
        )
        formal_mode_reason = RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON
        inference_input_constraints = {
            "mode": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
            "map_id": base_session.map_id,
            "model_map_id": base_session.map_id,
            "activity_id": 40002,
            "activity_map_id": 20002,
            "hero_id": 301,
            "map_group_id": 501,
            "mapping_status": raven_status,
            "quote_available": False,
            "first_live_session_status": "pending",
        }
    elif (
        base_session is not None
        and base_session.map_id not in tables.maps
        and not activity_map_alias
    ):
        evidence_label = f"unsupported_map:{base_session.map_id}"
        formal_mode_reason = "unsupported_map"
        inference_input_constraints = {
            "mode": "unsupported_map",
            "map_id": base_session.map_id,
        }
    elif (
        base_session is not None
        and base_session.map_id in CURRENT_RANKMAP_MAP_IDS
        and effective_rankmap_distribution_status not in {"ok", "not_configured"}
    ):
        evidence_label = (
            "current_rankmap_authority:"
            f"{effective_rankmap_distribution_status}"
        )
        formal_mode_reason = "current_rankmap_authority_unavailable"
        inference_input_constraints = {
            "mode": "current_rankmap_authority_unavailable",
            "map_id": base_session.map_id,
            "authority_status": effective_rankmap_distribution_status,
        }
    elif base_session is not None:
        inference_base_session = base_session
        if activity_map_alias:
            model_map_id = int(activity_map_alias["model_map_id"])
            inference_base_session = replace(base_session, map_id=model_map_id)
        else:
            model_map_id = base_session.map_id
        drop_pool_support = _drop_pool_support_receipt(
            int(model_map_id),
            tables,
        )
        candidate_map_ids = _candidate_map_ids_for_likelihood(
            inference_base_session.map_id,
            tables.maps,
        )
        evidence_label = "结算前最后状态"
        if activity_map_alias:
            if activity_map_alias.get("mode") == RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE:
                evidence_label = (
                    f"{RAVEN_TEMPLATE_ESTIMATE_LABEL}"
                    f"（{activity_map_alias['source_map_id']}→{model_map_id}）"
                )
            else:
                evidence_label = (
                    f"结算前最后状态（活动图按旧沉船 {model_map_id} 估）"
                )
        cells_tol = 8
        count_tol = 3
        inference_session, inference_input_constraints = (
            _inference_session_with_safe_totals(
                inference_base_session,
                pre_settlement_state,
            )
        )
        if activity_map_alias:
            inference_input_constraints = dict(inference_input_constraints)
            inference_input_constraints.update(
                {
                    "source_map_id": activity_map_alias["source_map_id"],
                    "model_map_id": activity_map_alias["model_map_id"],
                    "map_alias_mode": activity_map_alias["mode"],
                    "map_alias_reason": activity_map_alias["reason"],
                    "map_alias": activity_map_alias,
                }
            )
        base_results = estimate_map_likelihood(
            candidate_map_ids,
            inference_session,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            n_trials=n_trials,
            seed=seed,
            cells_tol=cells_tol,
            count_tol=count_tol,
        )
        warehouse_estimate = estimate_warehouse_cells(
            candidate_map_ids,
            inference_session,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            n_trials=n_trials,
            seed=seed,
            cells_tol=cells_tol,
            count_tol=count_tol,
        )
        if not any(result.n_matched for result in base_results):
            relaxed_session = _relax_exact_bucket_constraints(inference_session)
            if relaxed_session is not inference_session:
                relaxed_results = estimate_map_likelihood(
                    candidate_map_ids,
                    relaxed_session,
                    maps=tables.maps,
                    drops=tables.drops,
                    items=tables.items,
                    n_trials=n_trials,
                    seed=seed,
                    cells_tol=cells_tol,
                    count_tol=count_tol,
                )
                relaxed_warehouse_estimate = estimate_warehouse_cells(
                    candidate_map_ids,
                    relaxed_session,
                    maps=tables.maps,
                    drops=tables.drops,
                    items=tables.items,
                    n_trials=n_trials,
                    seed=seed,
                    cells_tol=cells_tol,
                    count_tol=count_tol,
                )
                if any(result.n_matched for result in relaxed_results):
                    inference_session = relaxed_session
                    base_results = relaxed_results
                    warehouse_estimate = relaxed_warehouse_estimate
                    evidence_label = f"{evidence_label}（放宽精确桶约束）"
        map_rows = _map_likelihood_result_rows(base_results, evidence_label)
        warehouse_rows = _warehouse_estimate_rows(warehouse_estimate)
        store = evidence_store_from_fatbeans_events(
            events,
            session_id=latest_session_id,
        )
        problem = build_residual_problem(
            inference_session.map_id,
            store,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            obs=inference_session,
            physical_catalog_map_id=base_session.map_id,
        )
        evidence_profile_key = evidence_profile_key_from_problem(problem)
        q6_quality_only_local_risk = q6_quality_only_local_diagnostics(store)
        q6_quality_only_local_risk["active"] = (
            aisha_q6_quality_only_deep_local_risk(
                hero=inference_session.hero,
                map_family=_map_family_from_id(inference_session.map_id),
                evidence_profile_key=evidence_profile_key,
                deepest_start_row=q6_quality_only_local_risk.get(
                    "deepest_start_row"
                ),
            )
        )
        q6_quality_only_local_risk["deep_row_threshold"] = (
            AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD
        )
        q6_aisha_bottom_row_risk = {
            "active": aisha_bottom_row_risk(
                hero=inference_session.hero,
                map_family=_map_family_from_id(inference_session.map_id),
                bottom_row=problem.layout.bottom_row,
            ),
            "bottom_row": problem.layout.bottom_row,
            "bottom_row_threshold": AISHA_BOTTOM_ROW_RISK_THRESHOLD,
        }
        active_shadow_boost = q6_residual_boost_for_profile(
            hero=inference_session.hero,
            map_family=_map_family_from_id(inference_session.map_id),
            evidence_profile_key=evidence_profile_key,
            requested_boost=5.0,
            gate="shipwreck_profile_v1",
            bottom_row=problem.layout.bottom_row,
        )
        active_deep_floor_ratio = q6_residual_prior_floor_ratio_for_profile(
            hero=inference_session.hero,
            map_family=_map_family_from_id(inference_session.map_id),
            evidence_profile_key=evidence_profile_key,
            requested_ratio=1.0,
            gate="aisha_shipwreck_deep_v1",
            bottom_row=problem.layout.bottom_row,
        )
        active_deep11_floor_ratio = q6_residual_prior_floor_ratio_for_profile(
            hero=inference_session.hero,
            map_family=_map_family_from_id(inference_session.map_id),
            evidence_profile_key=evidence_profile_key,
            requested_ratio=1.0,
            gate="aisha_shipwreck_deep11_v1",
            bottom_row=problem.layout.bottom_row,
        )
        active_hidden_floor_ratio = q6_residual_prior_floor_ratio_for_profile(
            hero=inference_session.hero,
            map_family=_map_family_from_id(inference_session.map_id),
            evidence_profile_key=evidence_profile_key,
            requested_ratio=1.5,
            gate="aisha_hidden_v1",
            bottom_row=problem.layout.bottom_row,
        )
        active_villa_floor_ratio = q6_residual_prior_floor_ratio_for_profile(
            hero=inference_session.hero,
            map_family=_map_family_from_id(inference_session.map_id),
            evidence_profile_key=evidence_profile_key,
            requested_ratio=0.5,
            gate="aisha_villa_shape_layout_v1",
            bottom_row=problem.layout.bottom_row,
        )
        active_ethan_villa_random_floor_ratio = (
            q6_residual_prior_floor_ratio_for_profile(
                hero=inference_session.hero,
                map_family=_map_family_from_id(inference_session.map_id),
                evidence_profile_key=evidence_profile_key,
                requested_ratio=1.0,
                gate="ethan_villa_random_avg_v1",
                bottom_row=problem.layout.bottom_row,
            )
        )
        active_ethan_shipwreck_layout_conditional = (
            q6_conditional_target_active_for_profile(
                hero=inference_session.hero,
                map_family=_map_family_from_id(inference_session.map_id),
                evidence_profile_key=evidence_profile_key,
                gate="ethan_shipwreck_layout_v1",
            )
        )
        active_ethan_shipwreck_layout_target_count = (
            4.0 if active_ethan_shipwreck_layout_conditional else 0.0
        )
        active_ethan_shipwreck_layout_target_cells = (
            15.0 if active_ethan_shipwreck_layout_conditional else 0.0
        )
        formal_prior_floor_ratio = active_deep_floor_ratio
        q6_formal_prior_floor = {
            "label": "aisha_deep_floor1",
            "gate": "aisha_shipwreck_deep_v1",
            "evidence_profile_key": evidence_profile_key,
            "active": formal_prior_floor_ratio > 0.0,
            "active_prior_floor_ratio": formal_prior_floor_ratio,
        }
        formal_prior_floor_kwargs = (
            {"q6_residual_prior_floor_ratio": formal_prior_floor_ratio}
            if formal_prior_floor_ratio > 0.0
            else {}
        )
        v2_report = estimate_posterior_v2(
            inference_session.map_id,
            inference_session,
            store,
            maps=tables.maps,
            drops=tables.drops,
            items=tables.items,
            n_trials=n_trials,
            seed=seed + 2,
            cells_tol=cells_tol,
            count_tol=count_tol,
            physical_catalog_map_id=base_session.map_id,
            **formal_prior_floor_kwargs,
        )
        shadow_report = None
        emitted_shadow_boost = active_shadow_boost if run_debug_shadows else 1.0
        if run_debug_shadows and active_shadow_boost > 1.0:
            shadow_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_boost=active_shadow_boost,
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_boost_shadow = _q6_residual_boost_shadow_summary(
            shadow_report,
            label="profile_b5",
            requested_boost=5.0,
            active_boost=emitted_shadow_boost,
            gate="shipwreck_profile_v1",
            evidence_profile_key=evidence_profile_key,
            trials=resolved_shadow_trials,
        )
        deep_floor_report = None
        if active_deep_floor_ratio > 0.0:
            deep_floor_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_prior_floor_ratio=active_deep_floor_ratio,
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_deep_floor_shadow = _q6_residual_boost_shadow_summary(
            deep_floor_report,
            label="aisha_deep_floor1",
            requested_boost=1.0,
            active_boost=1.0,
            requested_prior_floor_ratio=1.0,
            active_prior_floor_ratio=active_deep_floor_ratio,
            gate="aisha_shipwreck_deep_v1",
            evidence_profile_key=evidence_profile_key,
            trials=resolved_shadow_trials,
        )
        deep11_floor_report = None
        if active_deep11_floor_ratio > 0.0 and resolved_shadow_trials > 1:
            deep11_floor_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_prior_floor_ratio=active_deep11_floor_ratio,
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_deep11_floor_shadow = _q6_residual_boost_shadow_summary(
            deep11_floor_report,
            label="aisha_deep11_floor1",
            requested_boost=1.0,
            active_boost=1.0,
            requested_prior_floor_ratio=1.0,
            active_prior_floor_ratio=active_deep11_floor_ratio,
            gate="aisha_shipwreck_deep11_v1",
            evidence_profile_key=evidence_profile_key,
            trials=resolved_shadow_trials,
        )
        hidden_floor_report = None
        if active_hidden_floor_ratio > 0.0:
            hidden_floor_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_prior_floor_ratio=active_hidden_floor_ratio,
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_hidden_floor_shadow = _q6_residual_boost_shadow_summary(
            hidden_floor_report,
            label="aisha_hidden_floor15",
            requested_boost=1.0,
            active_boost=1.0,
            requested_prior_floor_ratio=1.5,
            active_prior_floor_ratio=active_hidden_floor_ratio,
            gate="aisha_hidden_v1",
            evidence_profile_key=evidence_profile_key,
            trials=resolved_shadow_trials,
        )
        villa_floor_report = None
        if active_villa_floor_ratio > 0.0:
            villa_floor_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_prior_floor_ratio=active_villa_floor_ratio,
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_villa_floor_shadow = _q6_residual_boost_shadow_summary(
            villa_floor_report,
            label="aisha_villa_floor05",
            requested_boost=1.0,
            active_boost=1.0,
            requested_prior_floor_ratio=0.5,
            active_prior_floor_ratio=active_villa_floor_ratio,
            gate="aisha_villa_shape_layout_v1",
            evidence_profile_key=evidence_profile_key,
            trials=resolved_shadow_trials,
        )
        ethan_villa_random_floor_report = None
        if (
            active_ethan_villa_random_floor_ratio > 0.0
            and resolved_shadow_trials > 1
        ):
            ethan_villa_random_floor_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_residual_prior_floor_ratio=(
                    active_ethan_villa_random_floor_ratio
                ),
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_ethan_villa_random_floor_shadow = (
            _q6_residual_boost_shadow_summary(
                ethan_villa_random_floor_report,
                label="ethan_villa_random_avg_floor1",
                requested_boost=1.0,
                active_boost=1.0,
                requested_prior_floor_ratio=1.0,
                active_prior_floor_ratio=(
                    active_ethan_villa_random_floor_ratio
                ),
                gate="ethan_villa_random_avg_v1",
                evidence_profile_key=evidence_profile_key,
                trials=resolved_shadow_trials,
            )
        )
        ethan_shipwreck_layout_conditional_report = None
        if (
            active_ethan_shipwreck_layout_conditional
            and resolved_shadow_trials > 1
        ):
            ethan_shipwreck_layout_conditional_report = estimate_posterior_v2(
                inference_session.map_id,
                inference_session,
                store,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=resolved_shadow_trials,
                seed=seed + 2,
                cells_tol=cells_tol,
                count_tol=count_tol,
                q6_conditional_target_count=(
                    active_ethan_shipwreck_layout_target_count
                ),
                q6_conditional_target_cells=(
                    active_ethan_shipwreck_layout_target_cells
                ),
                physical_catalog_map_id=base_session.map_id,
            )
        q6_residual_ethan_shipwreck_layout_conditional_shadow = (
            _q6_residual_boost_shadow_summary(
                ethan_shipwreck_layout_conditional_report,
                label="ethan_shipwreck_layout_conditional_c4_cells15",
                requested_boost=1.0,
                active_boost=1.0,
                requested_conditional_target_count=4.0,
                active_conditional_target_count=(
                    active_ethan_shipwreck_layout_target_count
                ),
                requested_conditional_target_cells=15.0,
                active_conditional_target_cells=(
                    active_ethan_shipwreck_layout_target_cells
                ),
                gate="ethan_shipwreck_layout_v1",
                evidence_profile_key=evidence_profile_key,
                trials=resolved_shadow_trials,
            )
        )
        q6_residual_sampler_shadows = [
            q6_residual_boost_shadow,
            q6_residual_deep_floor_shadow,
            q6_residual_deep11_floor_shadow,
            q6_residual_hidden_floor_shadow,
            q6_residual_villa_floor_shadow,
            q6_residual_ethan_villa_random_floor_shadow,
            q6_residual_ethan_shipwreck_layout_conditional_shadow,
        ]
        q6_residual_boost_shadow_rows = _q6_residual_shadow_rows(
            q6_residual_sampler_shadows
        )
        q6_practical_reference = _q6_reference_with_shadow(
            _q6_prior_gap_summary(v2_report),
            q6_residual_ethan_shipwreck_layout_conditional_shadow,
        )
        v2_posterior_rows = _v2_posterior_rows(
            v2_report,
            q6_prior_gap=q6_practical_reference,
        )
        v3_posterior_shadow = _v3_posterior_shadow_summary(
            events,
            map_id=inference_session.map_id,
            hero=inference_session.hero,
            tables=tables,
            trials=resolved_shadow_trials,
            seed=seed + 6,
        )
        if roi_trials > 0:
            tool_rows = _tool_info_roi_rows(
                estimate_tool_info_roi(
                    candidate_map_ids,
                    inference_session,
                    maps=tables.maps,
                    drops=tables.drops,
                    items=tables.items,
                    n_trials=roi_trials,
                    seed=seed + 1,
                    cells_tol=cells_tol,
                    count_tol=count_tol,
                )
            )
        best_value_summary = v2_report.decision_value or (
            base_results[0].total_value
            if base_results and base_results[0].total_value is not None
            else None
        )
        # 默认应用路径=v2; 原始价值用于平铺 balanced(v3 应用时下面覆盖)。
        headline_raw_value_summary = v2_report.total_value
        if best_value_summary is not None:
            bid_rows = _build_bid_rows(
                latest_bids=latest_bids,
                value_summary=best_value_summary,
                evidence_label="v2 decision_value",
                session=inference_session,
                round_no=action_round,
                posterior_samples=v2_report.n_matched,
                warehouse_estimate=warehouse_estimate,
                decision_value_summary=v2_report.decision_value,
                raw_value_summary=v2_report.total_value,
                posterior_diagnostics=v2_report.diagnostics,
                q6_prior_gap=q6_practical_reference,
            )
        v2_bid_rows = [dict(row) for row in bid_rows]
        if formal_mode_requested == LIVE_FORMAL_MODE_V3_PRACTICAL:
            v3_practical_bid_rows, formal_mode_reason = _build_v3_practical_bid_rows(
                latest_bids=latest_bids,
                v3_shadow=v3_posterior_shadow,
                session=inference_session,
                round_no=action_round,
                posterior_samples=v2_report.n_matched,
                warehouse_estimate=warehouse_estimate,
                q6_prior_gap=q6_practical_reference,
            )
            if v3_practical_bid_rows:
                bid_rows = v3_practical_bid_rows
                headline_raw_value_summary = _summary_from_flat_quantiles(
                    v3_posterior_shadow, "v3_practical_total_value"
                )
                formal_mode_applied = LIVE_FORMAL_MODE_V3_PRACTICAL
            else:
                formal_mode_applied = LIVE_FORMAL_MODE_V2
                formal_mode_reason = f"{formal_mode_reason}_fallback_v2"
        else:
            formal_mode_applied = LIVE_FORMAL_MODE_V2
            formal_mode_reason = "v2_mode_requested"
        if v2_report.n_matched <= 0:
            (
                fallback_map_rows,
                fallback_warehouse_rows,
                fallback_bid_rows,
            ) = _build_zero_match_fallback_rows(
                candidate_map_ids=candidate_map_ids,
                inference_session=inference_session,
                latest_bids=latest_bids,
                round_no=action_round,
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=n_trials,
                seed=seed + 4,
                cells_tol=cells_tol,
                count_tol=count_tol,
            )

    if bid_rows:
        for row in bid_rows:
            row.setdefault("formal_mode", formal_mode_applied)
            row.setdefault("formal_mode_reason", formal_mode_reason)
            row.setdefault("formal_baseline_source", formal_mode_applied)
            row.setdefault(
                "formal_override",
                "是" if formal_mode_applied == LIVE_FORMAL_MODE_V3_PRACTICAL else "否",
            )
    if v2_bid_rows:
        for row in v2_bid_rows:
            row.setdefault("formal_mode", LIVE_FORMAL_MODE_V2)
            row.setdefault("formal_mode_reason", "v2_reference")
            row.setdefault("formal_baseline_source", LIVE_FORMAL_MODE_V2)
            row.setdefault("formal_override", "否")

    panel = tactical_panel_from_rows(
        bid_rows=bid_rows,
        warehouse_rows=warehouse_rows,
        tool_rows=tool_rows,
        layout_stage_rows=layout_stage_rows,
        layout_note="",
    )
    inventory_count, inventory_cells = _inventory_totals(events)
    catalog_values = summarize_catalog_values(
        _settlement_inventory_items(events),
        tables.items,
    )
    final_value = catalog_values.known_value_sum
    truth_breakdown = _inventory_quality_breakdown(
        events,
        tables.items,
        problem=problem,
        maps=tables.maps,
        drops=tables.drops,
        map_id=(
            problem.map_id
            if problem is not None
            else (
                model_map_id
                if model_map_id is not None
                else (base_session.map_id if base_session is not None else None)
            )
        ),
    )
    # Settlement-completeness guard (4409 live repro 2026-06-23): the captured settlement should
    # enumerate ~all warehouse items. When the game's EXACT total item count (skill reveal / public
    # packet, surfaced as inference_input_constraints.total_item_count, confidence=exact) is known
    # and the settled inventory has fewer items, the capture missed part of the settlement stream
    # (4409: declared 45 exact vs only 13 items captured -> known_value_sum 361k is ~34% of the
    # true ~1.07M). Flag it (non-destructive: warning only, value unchanged) so the overlay can
    # warn instead of presenting a partial sum as truth + a bogus "overestimate" diff.
    # Exact-equality gate (author-confirmed 2026-07-04): when this declaration exists it ALWAYS
    # equals the final settlement count for a clean capture — there is no legitimate "jitter". A
    # library sweep of the ahmed corpus (2870 settled+declared samples) found ANY declared!=captured
    # gap only ever means an incomplete capture (81/2870 = 2.8%, worst in shipwreck 4.4%), never a
    # benign count-definition difference (2508/374099586 live repro: declared 31, captured 27 —
    # game showed 战利品价格 875,353 vs our 537,204, ~13% items genuinely lost to a capture gap,
    # tail-recovery(3a63e60) found nothing extra to recover here — different mechanism, not a parser
    # bug). The prior <0.8 ratio was too lenient and silently missed this class of case. Rollback:
    # BIDKING_DISABLE_SETTLEMENT_COMPLETENESS=1.
    settlement_incomplete = False
    settlement_incomplete_detail: dict[str, Any] | None = None
    if phase == "settled" and os.environ.get("BIDKING_DISABLE_SETTLEMENT_COMPLETENESS") != "1":
        _tic = (
            inference_input_constraints.get("total_item_count")
            if isinstance(inference_input_constraints, Mapping)
            else None
        )
        if isinstance(_tic, Mapping) and _tic.get("confidence") == "exact":
            try:
                _declared = int(_tic.get("value"))
            except (TypeError, ValueError):
                _declared = None
            if (
                _declared is not None
                and _declared > 0
                and inventory_count is not None
                and int(inventory_count) < int(_declared)
            ):
                settlement_incomplete = True
                settlement_incomplete_detail = {
                    "captured_items": int(inventory_count),
                    "declared_items": int(_declared),
                    "captured_value": int(final_value) if final_value is not None else None,
                    "source": _tic.get("source"),
                }
    resolved_hero = _resolve_live_hero(
        events,
        base_session,
        session_id=latest_session_id,
    )
    resolved_hero_id = _infer_hero_id_from_events(
        events,
        session_id=latest_session_id,
    )
    warehouse_source = classify_from_map_table(
        _latest_map_id(events),
        tables.maps,
        source_generation=warehouse_generation,
        profile_generation=tables.warehouse_package_generation,
        game_version=fresh_game_version,
        owner_evidence_status=owner_evidence_status,
    )
    structured_ref_inputs = _ahmad_ref_inputs_from_batches(
        batches,
        hero=resolved_hero,
        items=tables.items,
        session_id=latest_session_id,
        physical_candidate_domains=(
            problem.physical_candidate_domains if problem is not None else ()
        ),
    )
    physical_candidate_domain_rows = _physical_candidate_domain_rows(
        problem.physical_candidate_domains if problem is not None else ()
    )
    skill_reveal_rows = _skill_reveal_rows(
        events,
        tables.items,
        session_id=latest_session_id,
    )
    action_result_rows = _action_result_rows(
        events,
        tables.items,
        session_id=latest_session_id,
    )
    action_send_rows = _action_send_rows(events, tables.items)
    current_action_ids = _current_completed_action_ids(
        events,
        session_id=latest_session_id,
        action_round=action_round,
        observed_round=observed_round,
        phase=phase,
    )
    action_result_rows = _promote_current_action_rows(
        action_result_rows,
        session_id=latest_session_id,
        action_round=action_round,
        observed_round=observed_round,
        current_action_ids=current_action_ids,
    )
    current_action_projection = _current_action_ref_projection(action_result_rows)
    if current_action_projection:
        structured_ref_inputs = dict(structured_ref_inputs)
        structured_ref_inputs["current_action_results"] = current_action_projection
    shape_average_cells = {
        SIZE_AVG_ACTION_FOOTPRINT[action_id]
        for row in action_result_rows
        if (
            type(action_id := row.get("action_id")) is int
            and action_id in SIZE_AVG_ACTION_FOOTPRINT
        )
    }
    shape_average_domains = (
        {
            cells: canonical_map_footprint_domain(base_session.map_id, cells)
            for cells in sorted(shape_average_cells)
        }
        if (
            base_session is not None
            and problem is not None
            and os.environ.get(SHAPE_AVG_EXACT_CONSTRAINT_DISABLE) != "1"
        )
        else {}
    )
    exact_total_item_count = (
        problem.total_item_count
        if problem is not None
        and isinstance(
            inference_input_constraints.get("total_item_count"),
            Mapping,
        )
        and inference_input_constraints["total_item_count"].get("confidence")
        == "exact"
        else None
    )
    exact_warehouse_total_cells = (
        problem.warehouse_total_cells
        if problem is not None
        and isinstance(
            inference_input_constraints.get("warehouse_total_cells"),
            Mapping,
        )
        and inference_input_constraints["warehouse_total_cells"].get(
            "confidence"
        )
        == "exact"
        else None
    )
    shape_average_value_evidence = shape_average_value_evidence_from_action_rows(
        action_result_rows,
        current_session_id=latest_session_id,
        posterior_targets=(
            size_bucket_count_targets_for_structured_evidence(
                store,
                footprint_cells=tuple(shape_average_cells),
                total_item_count=exact_total_item_count,
                layout_footprint_count=problem.layout.footprint_count,
                trusted_footprint_count=problem.layout.trusted_footprint_count,
            )
            if problem is not None and store is not None
            else ()
        ),
        candidate_domains_by_cells=shape_average_domains,
        mandatory_units_by_cells=_shape_average_mandatory_units_by_cells(
            problem
        ),
        exact_total_item_count=exact_total_item_count,
        exact_warehouse_total_cells=exact_warehouse_total_cells,
    )
    processing_seconds = round(time.perf_counter() - started_at, 4)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "created_at": time.time(),
        "processing_seconds": processing_seconds,
        "n_trials": n_trials,
        "roi_trials": roi_trials,
        "shadow_trials": resolved_shadow_trials,
        "file": file,
        "packets": len(events.packets),
        "frames": len(events.frames),
        "states": len(events.states),
        "batches": len(batches),
        "session_id": latest_session_id,
        "hero": resolved_hero,
        "hero_id": resolved_hero_id,
        "map_id": _latest_map_id(events),
        "model_map_id": model_map_id,
        "map_alias": activity_map_alias,
        "map_alias_mode": activity_map_alias.get("mode", ""),
        "warehouse_source": warehouse_source.as_dict(),
        "warehouse_source_qualification": warehouse_source.qualification,
        "warehouse_source_generation": warehouse_source.source_generation,
        "warehouse_game_version_session_stable": session_game_version_stable,
        "official_distribution_eligible": (
            warehouse_source.official_distribution_eligible
        ),
        "round": action_round,
        "action_round": action_round,
        "observed_round": observed_round,
        "phase": phase,
        "inventory_count": inventory_count,
        "inventory_cells": inventory_cells,
        "known_value_sum": final_value,
        "catalog_value_complete": catalog_values.catalog_value_complete,
        "catalog_unknown_item_ids": list(catalog_values.unknown_item_ids),
        "catalog_valued_item_count": catalog_values.valued_item_count,
        "catalog_settled_item_count": catalog_values.settled_item_count,
        "settlement_incomplete": settlement_incomplete,
        "settlement_incomplete_detail": settlement_incomplete_detail,
        "settlement_parse_residual": settlement_parse_residual,
        **truth_breakdown,
        "inference_input_constraints": inference_input_constraints,
        "formal_mode_requested": formal_mode_requested,
        "formal_mode": formal_mode_applied,
        "formal_mode_reason": formal_mode_reason,
        "formal_baseline_source": formal_mode_applied,
        "latest_bids": dict(latest_bids),
        # balanced: 估价平铺数值=原始价值 P50(最终应用 formal mode 的 raw value 中位),
        # 对局记录「展示盈亏=估价−结算真值」用; 无推断/无 bid 行时为 None。
        "balanced": _summary_p50_int(headline_raw_value_summary),
        # self_bid: 我的出价/是否我拍下(对局记录 UI 用; uid 来自 fatbeans 已 stamp 的
        # state.player_id, 识别不到本地玩家时全 None/False)。
        "local_player_id": self_bid_summary["local_player_id"],
        "self_name": self_bid_summary["self_name"],
        "self_bid": self_bid_summary["self_bid"],
        "winner_is_self": self_bid_summary["winner_is_self"],
        # 本场福利(=亏损分成地面真值, 游戏结算帧 f5; 仅未拍下且赢家亏损时有, 否则 None)。
        "settlement_welfare": settlement_welfare,
        # 排位赛排位分摘要(纯展示; 非排位/老样本为 None)。
        "rank_summary": rank_summary,
        # 各玩家排位分(昵称->赛前分/段位; 供看板显示非我方玩家排位)。非排位为空。
        "player_ranks": _player_ranks or None,
        "evidence_label": evidence_label,
        "map_rows": map_rows,
        "warehouse_rows": warehouse_rows,
        "v2_posterior_rows": v2_posterior_rows,
        "v2_bid_rows": v2_bid_rows,
        "v3_practical_bid_rows": v3_practical_bid_rows,
        "fallback_map_rows": fallback_map_rows,
        "fallback_warehouse_rows": fallback_warehouse_rows,
        "fallback_bid_rows": fallback_bid_rows,
        "v3_posterior_shadow": v3_posterior_shadow,
        "q6_residual_boost_shadow": q6_residual_boost_shadow,
        "q6_formal_prior_floor": q6_formal_prior_floor,
        "q6_residual_deep_floor_shadow": q6_residual_deep_floor_shadow,
        "q6_residual_deep11_floor_shadow": q6_residual_deep11_floor_shadow,
        "q6_residual_hidden_floor_shadow": q6_residual_hidden_floor_shadow,
        "q6_residual_villa_floor_shadow": q6_residual_villa_floor_shadow,
        "q6_residual_ethan_villa_random_floor_shadow": (
            q6_residual_ethan_villa_random_floor_shadow
        ),
        "q6_residual_ethan_shipwreck_layout_conditional_shadow": (
            q6_residual_ethan_shipwreck_layout_conditional_shadow
        ),
        "q6_residual_sampler_shadows": q6_residual_sampler_shadows,
        "q6_residual_boost_shadow_rows": q6_residual_boost_shadow_rows,
        "q6_aisha_bottom_row_risk": q6_aisha_bottom_row_risk,
        "q6_quality_only_local_risk": q6_quality_only_local_risk,
        "evidence_profile_key": evidence_profile_key,
        "tool_rows": tool_rows,
        "structured_ref_inputs": structured_ref_inputs,
        "ahmad_ref_inputs": structured_ref_inputs,
        "action_send_rows": action_send_rows,
        "action_result_rows": action_result_rows,
        "shape_average_value_evidence": shape_average_value_evidence,
        "skill_reveal_rows": skill_reveal_rows,
        "skill_reveals": _skill_reveals_for_ref(skill_reveal_rows),
        "public_info_rows": _public_info_rows(
            events,
            tables.items,
            session_id=latest_session_id,
        ),
        "bid_rows": bid_rows,
        "layout_replay_rows": layout_replay_rows,
        "layout_stage_rows": layout_stage_rows,
        "panel": asdict(panel),
        "category_grid_items": _apply_physical_candidate_domains_to_minimap(
            _category_grid_items(
                batches,
                tables.items,
                session_id=latest_session_id,
            ),
            physical_candidate_domain_rows,
            items=tables.items,
        ),
        "minimap_grid_items": _apply_physical_candidate_domains_to_minimap(
            _minimap_grid_items(
                batches,
                tables.items,
                session_id=latest_session_id,
            ),
            physical_candidate_domain_rows,
            items=tables.items,
        ),
        "layout_sample_rows": [
            row.as_dict()
            for row in evaluate_fatbeans_layout_events(events, file=file)
        ],
    }
    if physical_candidate_domain_rows:
        artifact["physical_candidate_domains"] = physical_candidate_domain_rows
        artifact["physical_category_catalog_status"] = (
            problem.physical_category_catalog_status
            if problem is not None
            else "not_requested"
        )
        artifact["physical_category_catalog_generation"] = (
            problem.physical_category_catalog_generation
            if problem is not None
            else ""
        )
    if drop_pool_support:
        artifact["drop_pool_support"] = drop_pool_support
    if rankmap_distribution_support:
        artifact["rankmap_distribution_support"] = rankmap_distribution_support
    if auction_hall_6001_provisional:
        artifact["auction_hall_6001_provisional"] = (
            auction_hall_6001_provisional
        )
    if raven_chaotic_space_support:
        artifact["raven_chaotic_space_support"] = raven_chaotic_space_support
    settlement_category_fact = _settlement_category_fact_v1(
        events,
        session_id=latest_session_id,
        physical_map_id=_latest_map_id(events),
        model_map_id=model_map_id,
        tables=tables,
        warehouse_generation=warehouse_generation,
        settlement_incomplete=settlement_incomplete,
    )
    if settlement_category_fact is not None:
        artifact["settlement_category_fact_v1"] = settlement_category_fact
    if settlement_incomplete or catalog_values.catalog_value_complete is False:
        # Preserve the partial inventory/value on the session artifact for diagnostics,
        # but never turn it into model error metrics: those require complete settlement
        # truth and otherwise invert under/over-estimate conclusions.
        artifact["model_eval_skipped_reason"] = (
            "settlement_incomplete"
            if settlement_incomplete
            else "catalog_value_incomplete"
        )
    else:
        eval_row = _model_eval_row(
            file=file,
            artifact=artifact,
            final_value=final_value,
            final_cells=inventory_cells,
            truth_breakdown=truth_breakdown,
        )
        if eval_row is not None:
            artifact["model_eval"] = eval_row
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    return artifact


def build_monitor_artifact_from_file(
    path: str | Path,
    *,
    tables: MonitorTables,
    n_trials: int = 500,
    roi_trials: int = 250,
    shadow_trials: int | None = None,
    run_debug_shadows: bool = True,
    seed: int = 20260530,
    formal_mode: str | None = None,
) -> dict[str, Any]:
    path = Path(path)
    return build_monitor_artifact_from_events(
        parse_fatbeans_capture(path),
        file=path.name,
        tables=tables,
        n_trials=n_trials,
        roi_trials=roi_trials,
        shadow_trials=shadow_trials,
        run_debug_shadows=run_debug_shadows,
        seed=seed,
        formal_mode=formal_mode,
    )


def build_monitor_artifact_from_payload(
    payload: str | bytes,
    *,
    file: str = "stdin",
    tables: MonitorTables,
    n_trials: int = 500,
    roi_trials: int = 250,
    shadow_trials: int | None = None,
    run_debug_shadows: bool = True,
    seed: int = 20260530,
    formal_mode: str | None = None,
    local_player_id_hint: int | None = None,
) -> dict[str, Any]:
    return build_monitor_artifact_from_events(
        parse_fatbeans_capture_payload(
            payload,
            local_player_id_hint=local_player_id_hint,
        ),
        file=file,
        tables=tables,
        n_trials=n_trials,
        roi_trials=roi_trials,
        shadow_trials=shadow_trials,
        run_debug_shadows=run_debug_shadows,
        seed=seed,
        formal_mode=formal_mode,
    )


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        fh.write("\n")


def write_monitor_logs(
    artifact: Mapping[str, Any],
    *,
    log_dir: str | Path | None = None,
    append_logs: bool = True,
) -> None:
    """Write latest snapshot and append long-running JSONL logs."""
    root = Path(log_dir) if log_dir is not None else project_root() / "data" / "logs" / "live"
    root.mkdir(parents=True, exist_ok=True)
    if append_logs and artifact.get("phase") == "settled":
        from bidking_lab.live.quote_preferences import (
            hash_session, read_applied, read_monitor_generation, validate_settlement_record,
        )
        artifact = dict(artifact)
        quote_session_hash = hash_session(artifact.get("session_id"))
        pending_quote = read_applied(
            root / "quote_last_applied.json", session_hash=quote_session_hash,
            generation_hash=read_monitor_generation(root / "monitor.lock"),
        )
        artifact["quote_application"] = validate_settlement_record(
            pending_quote, session_hash=quote_session_hash, settlement_cutoff=artifact.get("created_at"),
        )
    _atomic_write_json(root / "latest_snapshot.json", artifact)
    if not append_logs:
        return
    _append_jsonl(root / "sessions.jsonl", artifact)
    # 结算样本自动上传(opt-in:配 BIDKING_SAMPLE_UPLOAD_URL 才发;best-effort 后台线程,绝不阻塞)
    try:
        from bidking_lab.live.sample_upload import maybe_upload_settlement
        maybe_upload_settlement(artifact)
    except Exception:  # noqa: BLE001
        pass
    eval_row = artifact.get("model_eval")
    if isinstance(eval_row, Mapping):
        _append_jsonl(root / "model_eval.jsonl", eval_row)
    for row in artifact.get("layout_sample_rows", ()) or ():
        if isinstance(row, Mapping):
            _append_jsonl(root / "layout_samples.jsonl", row)


__all__ = (
    "MonitorTables",
    "build_monitor_artifact_from_events",
    "build_monitor_artifact_from_file",
    "build_monitor_artifact_from_payload",
    "load_monitor_tables",
    "write_monitor_logs",
)
