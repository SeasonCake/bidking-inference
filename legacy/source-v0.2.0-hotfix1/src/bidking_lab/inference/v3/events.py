"""Canonical evidence events for v3 diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from bidking_lab.inference.v3.evidence_registry import (
    EvidenceSpec,
    action_result_spec,
    public_info_spec,
    skill_reveal_spec,
)


@dataclass(frozen=True)
class EvidenceEvent:
    event_id: str
    source_kind: str
    source_id: str
    semantic: str
    strength: str
    constraint: str
    session_id: str | None = None
    sort_id: int | None = None
    round_index: int | None = None
    hero_id: int | None = None
    map_id: int | None = None
    affects_formal: bool = False
    targets: tuple[str, ...] = ()
    payload: Mapping[str, Any] | None = None

    @classmethod
    def from_spec(
        cls,
        *,
        spec: EvidenceSpec,
        event_id: str,
        session_id: str | None,
        sort_id: int | None,
        round_index: int | None,
        hero_id: int | None,
        map_id: int | None,
        payload: Mapping[str, Any] | None = None,
    ) -> "EvidenceEvent":
        return cls(
            event_id=event_id,
            source_kind=spec.source_kind,
            source_id=spec.source_id,
            semantic=spec.semantic,
            strength=spec.strength,
            constraint=spec.constraint,
            session_id=session_id,
            sort_id=sort_id,
            round_index=round_index,
            hero_id=hero_id,
            map_id=map_id,
            affects_formal=spec.affects_formal,
            targets=spec.targets,
            payload=payload,
        )


def _event_key(prefix: str, sort_id: int | None, source_id: int | str, index: int) -> str:
    sort = "na" if sort_id is None else str(sort_id)
    return f"{prefix}:{sort}:{source_id}:{index}"


def _shape_cells(shape_code: Any) -> int | None:
    if shape_code in (None, ""):
        return None
    try:
        code = int(shape_code)
    except (TypeError, ValueError):
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width * height


def _observed_item_payload(item: Any) -> dict[str, Any]:
    shape_code = getattr(item, "shape_code", None)
    cells = getattr(item, "cells", None)
    if cells is None:
        cells = _shape_cells(shape_code)
    return {
        "runtime_id": getattr(item, "runtime_id", None),
        "local_index": getattr(item, "local_index", None),
        "item_id": getattr(item, "item_id", None),
        "quality": getattr(item, "quality", None),
        "value": getattr(item, "value", None),
        "shape_code": shape_code,
        "shape_key": str(shape_code) if shape_code else None,
        "cells": cells,
    }


def _item_payload(items: Any) -> dict[str, Any]:
    observed_items = tuple(items or ())
    payload_items = tuple(_observed_item_payload(item) for item in observed_items)
    return {
        "observed_item_count": len(observed_items),
        "with_item_id": sum(1 for item in observed_items if getattr(item, "item_id", None) is not None),
        "with_shape": sum(1 for item in observed_items if getattr(item, "shape_code", None) is not None),
        "with_local": sum(1 for item in observed_items if getattr(item, "local_index", None) is not None),
        "with_quality": sum(1 for item in observed_items if getattr(item, "quality", None) is not None),
        "items": payload_items,
    }


def events_from_fatbeans(events: Any) -> tuple[EvidenceEvent, ...]:
    """Extract v3 canonical evidence events from parsed Fatbeans capture events."""

    out: list[EvidenceEvent] = []
    for state_index, state in enumerate(getattr(events, "states", ()) or ()):
        sort_id = getattr(state, "sort_id", None)
        session_id = getattr(state, "session_id", None)
        round_index = getattr(state, "round_index", None)
        map_id = getattr(state, "map_id", None)
        hero_id = None

        for idx, info in enumerate(getattr(state, "public_infos", ()) or ()):
            info_id = int(getattr(info, "info_id", 0))
            spec = public_info_spec(info_id)
            payload = {
                "value": getattr(info, "value", None),
                "value_field": getattr(info, "value_field", None),
                **_item_payload(getattr(info, "observed_items", ())),
            }
            out.append(
                EvidenceEvent.from_spec(
                    spec=spec,
                    event_id=_event_key("public", sort_id, info_id, idx),
                    session_id=session_id,
                    sort_id=sort_id,
                    round_index=round_index,
                    hero_id=hero_id,
                    map_id=map_id,
                    payload=payload,
                )
            )

        for idx, result in enumerate(getattr(state, "action_results", ()) or ()):
            action_id = int(getattr(result, "action_id", 0))
            spec = action_result_spec(action_id)
            payload = {
                "result": getattr(result, "result", None),
                "result_field": getattr(result, "result_field", None),
                **_item_payload(getattr(result, "observed_items", ())),
            }
            out.append(
                EvidenceEvent.from_spec(
                    spec=spec,
                    event_id=_event_key("action", sort_id, action_id, idx),
                    session_id=session_id,
                    sort_id=sort_id,
                    round_index=round_index,
                    hero_id=hero_id,
                    map_id=map_id,
                    payload=payload,
                )
            )

        for idx, reveal in enumerate(getattr(state, "skill_reveals", ()) or ()):
            skill_id = int(getattr(reveal, "skill_id", 0))
            spec = skill_reveal_spec(skill_id)
            payload = {
                "reveal_round_index": getattr(reveal, "round_index", None),
                "result": getattr(reveal, "result", None),
                "result_field": getattr(reveal, "result_field", None),
                **_item_payload(getattr(reveal, "observed_items", ())),
            }
            out.append(
                EvidenceEvent.from_spec(
                    spec=spec,
                    event_id=_event_key("skill", sort_id, skill_id, idx),
                    session_id=session_id,
                    sort_id=sort_id,
                    round_index=round_index,
                    hero_id=getattr(reveal, "hero_id", None),
                    map_id=map_id,
                    payload=payload,
                )
            )

        if getattr(state, "inventory_items", None):
            payload = {
                "inventory_item_count": len(tuple(getattr(state, "inventory_items", ()) or ())),
                "message_id": getattr(state, "message_id", None),
                "state_index": state_index,
            }
            out.append(
                EvidenceEvent(
                    event_id=_event_key("settlement", sort_id, "inventory", 0),
                    source_kind="settlement",
                    source_id="inventory",
                    semantic="settlement_inventory_truth",
                    strength="diagnostic",
                    constraint="diagnostic",
                    session_id=session_id,
                    sort_id=sort_id,
                    round_index=round_index,
                    map_id=map_id,
                    affects_formal=False,
                    payload=payload,
                )
            )
    return tuple(out)


__all__ = ("EvidenceEvent", "events_from_fatbeans")
