"""Inference vs bid refresh signatures for live monitor + overlay.

Bid-only capture updates (auction quotes) must refresh the displayed current
highest bid without rerunning formal inference or Hero Ref — bids do not change
public info, skills, props, or inventory facts that drive recommendations.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from bidking_lab._atomic_json import atomic_write_json as _atomic_write_json
from bidking_lab.live.fatbeans import (
    FatbeansStateEvent,
    latest_player_bids,
    local_player_bid_summary,
    parse_fatbeans_capture_payload,
)
from bidking_lab.runtime.snapshot import ui_contract_from_artifact


def _observed_item_signature(item: Any) -> tuple[Any, ...]:
    return (
        getattr(item, "local_index", None),
        getattr(item, "runtime_id", None),
        getattr(item, "item_id", None),
        getattr(item, "quality", None),
        getattr(item, "value", None),
        getattr(item, "shape_code", None),
        getattr(item, "cells", None),
    )


def _sortable_facts(values: set[tuple[Any, ...]]) -> tuple[tuple[Any, ...], ...]:
    """Return a deterministic order without comparing heterogeneous wire values.

    Real action/public/inventory facts can use ``None``, integers and floats in
    the same tuple position.  Native tuple sorting eventually compares those
    values and raises ``TypeError``, which must not prevent the live monitor from
    recognizing and publishing a later snapshot.
    """
    return tuple(sorted(values, key=repr))


def inference_semantic_signature_from_states(
    states: Sequence[FatbeansStateEvent],
) -> tuple[Any, ...] | None:
    """Stable signature for facts that affect live recommendations (no bids)."""
    if not states:
        return None

    action_facts: set[tuple[Any, ...]] = set()
    skill_facts: set[tuple[Any, ...]] = set()
    public_facts: set[tuple[Any, ...]] = set()
    inventory_facts: set[tuple[Any, ...]] = set()

    latest_session_id = None
    latest_map_id = None
    latest_round = None
    latest_player_id = None
    latest_message_id = None

    for state in states:
        if state.session_id:
            latest_session_id = state.session_id
        if state.map_id is not None:
            latest_map_id = state.map_id
        if state.round_index is not None:
            latest_round = state.round_index
        if state.player_id is not None:
            latest_player_id = state.player_id
        latest_message_id = state.message_id

        for result in state.action_results:
            action_facts.add(
                (
                    result.action_id,
                    result.result,
                    result.result_field,
                    tuple(_observed_item_signature(item) for item in result.observed_items),
                )
            )
        for reveal in state.skill_reveals:
            skill_facts.add(
                (
                    reveal.skill_id,
                    reveal.hero_id,
                    reveal.round_index,
                    reveal.result,
                    reveal.result_field,
                    tuple(_observed_item_signature(item) for item in reveal.observed_items),
                )
            )
        for info in state.public_infos:
            public_facts.add(
                (
                    info.info_id,
                    info.map_id,
                    info.value,
                    info.value_field,
                    tuple(_observed_item_signature(item) for item in info.observed_items),
                )
            )
        for item in state.inventory_items:
            inventory_facts.add(
                (
                    item.runtime_id,
                    item.item_id,
                    item.quality,
                    item.cells,
                )
            )

    return (
        latest_session_id,
        latest_map_id,
        latest_round,
        latest_player_id,
        latest_message_id,
        _sortable_facts(action_facts),
        _sortable_facts(skill_facts),
        _sortable_facts(public_facts),
        _sortable_facts(inventory_facts),
    )


def inference_semantic_signature_from_payload(
    payload: str | bytes,
    *,
    local_player_id_hint: int | None = None,
) -> tuple[Any, ...] | None:
    events = parse_fatbeans_capture_payload(
        payload,
        local_player_id_hint=local_player_id_hint,
    )
    return inference_semantic_signature_from_states(events.states)


def bid_signature_from_latest_bids(latest_bids: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(name), int(value)) for name, value in latest_bids.items()))


def bid_signature_from_payload(
    payload: str | bytes,
    *,
    local_player_id_hint: int | None = None,
) -> tuple[tuple[str, int], ...]:
    events = parse_fatbeans_capture_payload(
        payload,
        local_player_id_hint=local_player_id_hint,
    )
    return bid_signature_from_latest_bids(latest_player_bids(events.states))


def format_current_highest_from_bids(latest_bids: Mapping[str, int]) -> str:
    if not latest_bids:
        return ""
    leader, value = max(latest_bids.items(), key=lambda item: item[1])
    return f"{leader} {value:,}"


def inference_signature_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    stored = snapshot.get("inference_signature")
    if isinstance(stored, list):
        return tuple(stored)
    return ()


def bid_signature_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    latest_bids = snapshot.get("latest_bids")
    if isinstance(latest_bids, Mapping):
        out: dict[str, int] = {}
        for key, value in latest_bids.items():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return bid_signature_from_latest_bids(out)
    return ()


def patch_artifact_bid_fields(
    artifact: dict[str, Any],
    *,
    latest_bids: Mapping[str, int],
    self_bid_summary: Mapping[str, Any],
) -> None:
    """Update bid display fields on an existing artifact without touching inference."""
    current_highest = format_current_highest_from_bids(latest_bids)
    artifact["latest_bids"] = dict(latest_bids)
    artifact["local_player_id"] = self_bid_summary.get("local_player_id")
    artifact["self_name"] = self_bid_summary.get("self_name")
    artifact["self_bid"] = self_bid_summary.get("self_bid")
    artifact["winner_is_self"] = self_bid_summary.get("winner_is_self")
    artifact["bid_updated_at"] = time.time()

    for key in ("bid_rows", "v2_bid_rows", "v3_practical_bid_rows", "fallback_bid_rows"):
        rows = artifact.get(key)
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            continue
        rows[0]["当前最高"] = current_highest

    artifact["ui_contract"] = ui_contract_from_artifact(artifact)


def refresh_latest_snapshot_bids(
    log_dir: Path,
    payload: str | bytes,
    *,
    local_player_id_hint: int | None = None,
) -> bool:
    """Patch ``latest_snapshot.json`` bid fields only; leave inference outputs intact."""
    events = parse_fatbeans_capture_payload(
        payload,
        local_player_id_hint=local_player_id_hint,
    )
    semantic = inference_semantic_signature_from_states(events.states)
    if semantic is None:
        return False
    session_id = semantic[0]
    if not session_id:
        return False

    latest_bids = latest_player_bids(events.states)
    if not latest_bids:
        return False

    path = log_dir / "latest_snapshot.json"
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(artifact, dict):
        return False
    if str(artifact.get("session_id") or "") != str(session_id):
        return False
    phase = str(artifact.get("phase") or "")
    if phase not in ("reading", "bidding"):
        return False

    patch_artifact_bid_fields(
        artifact,
        latest_bids=latest_bids,
        self_bid_summary=local_player_bid_summary(events.states),
    )
    try:
        _atomic_write_json(path, artifact)
    except Exception:  # noqa: BLE001 - monitor boundary; caller logs skip
        return False
    return True


# --- Hero Ref input signature (overlay-side memoize) -------------------------
#
# Monitor's ``inference_signature`` covers action/skill/public/inventory facts
# but EXCLUDES minimap layout and manual structured inputs, both of which the
# Hero Ref engine consumes (``_minimap_items_from_snapshot``, structured_ref_inputs).
# It also tracks ``player_id`` and ``message_id`` which do not affect ref output.
# This signature captures exactly the fields ``run_reference_engine`` and the
# aisha/hero dual/single-pass wrappers read from a snapshot, so the overlay can
# skip re-computation when ref inputs are semantically identical even though the
# monitor-side signature changed (e.g. a new public_info row that does not alter
# the recommendation, or a frame that only advanced ``message_id``).

# Kept light here: importing the numerical engine in the monitor signature path
# would couple capture startup to its data/import cost. A regression gate binds
# this map set to the engine's frozen empirical-prior recipe.
EMPIRICAL_COUNT_PRIOR_036_MAP_IDS = frozenset({
    2101, 2401, 2404, 2405, 2406, 2407, 2409, 2501, 2502, 2503,
    2504, 2505, 2506, 2507, 2508, 2509, 2510, 2601, 4401, 5601,
    5606, 5607, 5608, 5609, 5610, 5611,
})


def _typed_source_value(value: Any) -> Any:
    """Do not erase exact-type rejection when forming the prior's cache key."""
    kind = type(value)
    if kind is dict:
        pairs = [(_typed_source_value(key), _typed_source_value(item))
                 for key, item in dict.items(value)]
        return ("dict", sorted(pairs, key=lambda pair: json.dumps(pair[0], ensure_ascii=False)))
    if kind in (list, tuple):
        return (kind.__name__, [_typed_source_value(item) for item in value])
    if kind in (type(None), str, int, float, bool):
        return (kind.__name__, value)
    # The selector rejects these objects. Avoid running their string or mapping
    # protocols merely to decide whether its former valid result may be reused.
    return ("non_json", kind.__module__, kind.__qualname__)


def _empirical_count_source_signature(snapshot: Mapping[str, Any]) -> str:
    """Retain raw owner presence/types, including rejected mirror containers."""
    def accepted_owner_shape(owner: Any) -> bool:
        # Match the selector's existing exact-dict boundary without importing
        # the engine. Only eligibility changes, not unrelated key additions,
        # belong in this key (a new bid field alone must still hit the cache).
        return (type(owner) is dict and dict.__len__(owner) <= 512
                and all(type(key) is str and len(key) <= 128 for key in dict.keys(owner)))

    raw_ui = snapshot.get("ui_contract")
    ui = raw_ui if isinstance(raw_ui, Mapping) else {}
    fields = (
        "map_id", "model_map_id", "map_alias", "map_alias_mode",
        "hero", "hero_id", "session_id", "phase", "epoch", "session_epoch",
        "game_version", "warehouse_source_generation", "warehouse_source_qualification",
        "official_distribution_eligible", "warehouse_game_version_session_stable",
        "warehouse_source",
    )
    owners = [("root", True, snapshot)] + [
        (key, key in ui, ui.get(key)) for key in ("context", "source")
    ]
    raw = [
        (label, present, type(owner).__name__, accepted_owner_shape(owner),
         {key: owner[key] for key in fields if key in owner} if isinstance(owner, Mapping) else None)
        for label, present, owner in owners
    ]
    raw.append(("ui_contract", "ui_contract" in snapshot, type(raw_ui).__name__, accepted_owner_shape(raw_ui)))
    return json.dumps(_typed_source_value(raw), ensure_ascii=False)

def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_hashable(value: Any) -> Any:
    """Coerce a possibly-unhashable snapshot field (list/dict) to a stable hashable form."""
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _minimap_item_signature(item: Mapping[str, Any]) -> tuple[Any, ...]:
    # cells is the genuine footprint only when the item is identified; the engine
    # treats (runtime_id, item_id, quality, cells) as the identity+footprint key.
    return (
        _as_int(item.get("runtime_id")),
        _as_int(item.get("item_id")),
        _as_str(item.get("quality")),
        _as_int(item.get("cells")),
        _as_int(item.get("local_index")),
    )


def _observed_item_signatures(items: Any) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    out: list[tuple[Any, ...]] = []
    for item in items:
        if isinstance(item, Mapping):
            out.append(_minimap_item_signature(item))
        elif isinstance(item, (list, tuple)):
            out.append(tuple(item))
    return tuple(out)


def _public_info_row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _as_int(row.get("info_id")),
        _as_int(row.get("map_id")),
        _as_hashable(row.get("value")),
        _as_str(row.get("value_field")),
        _observed_item_signatures(row.get("revealed_items_detail") or row.get("revealed_items") or row.get("observed_items")),
    )


def _skill_reveal_row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _as_int(row.get("skill_id")),
        _as_int(row.get("hero_id")),
        _as_hashable(row.get("result")),
        _as_str(row.get("result_field")),
        _as_hashable(row.get("sort")),
        _as_str(row.get("session_id")),
        _as_hashable(row.get("revealed_items")),
        _observed_item_signatures(row.get("observed_items") or row.get("revealed_items_detail") or row.get("revealed_items")),
    )


def _action_result_row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _as_str(row.get("action_id")),
        _as_hashable(row.get("result")),
        _as_str(row.get("result_field")),
        _as_int(row.get("round")),
        _as_int(row.get("promoted_action_round")),
        _as_str(row.get("promotion_reason")),
        _as_str(row.get("source_kind")),
        _observed_item_signatures(row.get("revealed_items_detail") or row.get("revealed_items") or row.get("observed_items")),
    )


def _structured_inputs_signature(value: Any) -> tuple[tuple[str, Any], ...]:
    """Hashable signature for manual structured ref inputs.

    Values may be dicts (fixed_counts etc.), lists, or scalars; we serialize
    via json with sorted keys to stay hashable and order-independent.
    """
    if not isinstance(value, Mapping) or not value:
        return ()
    out: list[tuple[str, str]] = []
    for key in sorted(value.keys()):
        v = value[key]
        if v is None or v == "" or v == {} or v == []:
            continue
        try:
            out.append((str(key), json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)))
        except (TypeError, ValueError):
            out.append((str(key), repr(v)))
    return tuple(out)


def ref_input_signature_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable signature for Hero Ref engine inputs (no bids, no player_id, no mtime).

    Covers every snapshot field ``summarize_snapshot`` / ``run_reference_engine``
    / ``_aisha_dual_pass_ref_result`` / ``_hero_single_pass_ref_result`` consume
    to drive a recommendation: context (hero/map/round/phase/session), public
    info rows, skill reveals, action results, inventory, minimap items (both
    root ``minimap_grid_items`` and ``ui_contract.minimap``), and manual
    structured inputs. Bid-only updates and heartbeats do not change this
    signature, so the overlay can memoize ref results across them.
    """
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), Mapping) else {}
    context = uc.get("context") if isinstance(uc.get("context"), Mapping) else {}
    hero = _as_str(context.get("hero") or snapshot.get("hero"))
    map_id = _as_int(context.get("map_id") or snapshot.get("map_id"))
    round_no = _as_int(context.get("round") or snapshot.get("round"))
    phase = _as_str(context.get("phase") or snapshot.get("phase"))
    session_id = _as_str(context.get("session_id") or snapshot.get("session_id"))

    public_rows = snapshot.get("public_info_rows")
    public_facts = (
        tuple(sorted(_public_info_row_signature(r) for r in public_rows if isinstance(r, Mapping)))
        if isinstance(public_rows, list)
        else ()
    )

    skill_facts: set[tuple[Any, ...]] = set()
    for key in ("skill_reveal_rows", "skill_reveals"):
        rows = snapshot.get(key)
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, Mapping):
                    skill_facts.add(_skill_reveal_row_signature(r))

    action_rows_top = snapshot.get("action_result_rows")
    action_facts: set[tuple[Any, ...]] = set()
    if isinstance(action_rows_top, list):
        for r in action_rows_top:
            if isinstance(r, Mapping):
                action_facts.add(_action_result_row_signature(r))
    actions = uc.get("actions") if isinstance(uc.get("actions"), Mapping) else {}
    extra_action_rows = actions.get("results")
    if isinstance(extra_action_rows, list):
        for r in extra_action_rows:
            if isinstance(r, Mapping):
                action_facts.add(_action_result_row_signature(r))

    inventory_facts: set[tuple[Any, ...]] = set()
    for key in ("inventory_items", "inventory_rows"):
        rows = snapshot.get(key)
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, Mapping):
                    inventory_facts.add((
                        _as_int(r.get("runtime_id")),
                        _as_int(r.get("item_id")),
                        _as_str(r.get("quality")),
                        _as_int(r.get("cells")),
                    ))

    # Minimap: root minimap_grid_items + ui_contract.minimap.items + quality_counts.
    # The engine merges both and dedupes; signature must capture both to detect
    # layout changes the monitor-side inference_signature misses.
    minimap_facts: set[tuple[Any, ...]] = set()
    root_items = snapshot.get("minimap_grid_items")
    if isinstance(root_items, list):
        for item in root_items:
            if isinstance(item, Mapping):
                minimap_facts.add(_minimap_item_signature(item))
    mm = uc.get("minimap") if isinstance(uc.get("minimap"), Mapping) else None
    if isinstance(mm, Mapping):
        contract_items = mm.get("items")
        if isinstance(contract_items, list):
            for item in contract_items:
                if isinstance(item, Mapping):
                    minimap_facts.add(_minimap_item_signature(item))
        qc = mm.get("quality_counts")
        if isinstance(qc, Mapping):
            minimap_facts.add(("__quality_counts__", tuple(sorted((str(k), _as_int(v)) for k, v in qc.items() if v))))

    structured_facts = _structured_inputs_signature(snapshot.get("structured_ref_inputs"))
    manual_facts = _structured_inputs_signature(snapshot.get("manual_ref_inputs"))

    result = (
        session_id, map_id, round_no, phase, hero,
        _sortable_facts(public_facts), _sortable_facts(skill_facts),
        _sortable_facts(action_facts), _sortable_facts(inventory_facts),
        _sortable_facts(minimap_facts), structured_facts, manual_facts,
    )
    if any(_as_int(owner.get("map_id")) in EMPIRICAL_COUNT_PRIOR_036_MAP_IDS for owner in (snapshot, context)):
        # A source-only transition can withdraw the soft count prior without
        # changing revealed facts. It must withdraw the outer cached result too.
        result += (("empirical_count_036_source_owners", _empirical_count_source_signature(snapshot)),)
    if any(_as_int(owner.get("map_id")) in (2511,4511) for owner in (snapshot, context)):
        # Raw owners, not the coalesced context above: a later owner/version
        # conflict must invalidate cached provisional quotes in this session.
        source = uc.get("source") if isinstance(uc.get("source"), Mapping) else {}
        fields = ("map_id", "model_map_id", "map_alias", "map_alias_mode", "session_id", "phase", "epoch", "session_epoch",
            "warehouse_source", "warehouse_source_generation", "warehouse_source_qualification",
            "official_distribution_eligible", "warehouse_game_version_session_stable", "formal_mode_reason")
        raw = [{key: owner[key] for key in fields if key in owner} for owner in (snapshot, context, source)]
        result += (("activity_2511_source_owners", json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)),)
    if any(_as_int(owner.get("map_id")) == 5215 for owner in (snapshot, context)):
        source = uc.get("source") if isinstance(uc.get("source"), Mapping) else {}
        fields = ("map_id", "model_map_id", "physical_catalog_map_id", "map_alias", "map_alias_mode",
            "hero", "hero_id", "player_hero_id", "current_player_hero_id", "session_id", "phase", "epoch", "session_epoch",
            "warehouse_source", "warehouse_source_generation", "warehouse_game_version_session_stable", "formal_mode_reason")
        raw = [{key: owner[key] for key in fields if key in owner} for owner in (snapshot, context, source)]
        result += (("wuqilin_5215_source_owners", json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)),)
        facts = {key: snapshot[key] for key in ("skill_reveal_rows", "skill_reveals", "minimap_grid_items", "public_info_rows", "physical_candidate_domains") if key in snapshot}
        facts["constraints"] = uc.get("constraints")
        facts["minimap"] = uc.get("minimap")
        result += (("wuqilin_5215_actual_constraints", json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)),)
    return result
