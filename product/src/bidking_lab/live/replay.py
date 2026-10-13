"""Replay diagnostics for Fatbeans captures."""

from __future__ import annotations

from dataclasses import dataclass, replace

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansStateEvent,
    live_batches_from_fatbeans_events,
)
from bidking_lab.live.layout import LayoutEvidence, layout_evidence_from_batch
from bidking_lab.live.state import LiveSessionState, apply_observation_batch


@dataclass(frozen=True)
class ReplayFinalTruth:
    """Settlement truth used to score earlier replay stages."""

    sort_id: int
    total_cells: int
    total_items: int


@dataclass(frozen=True)
class LayoutReplayStage:
    """One point in time for layout evidence vs final settlement."""

    sort_id: int
    round_no: int | None
    message_id: int
    phase: str
    layout: LayoutEvidence
    final_total_cells: int | None = None
    final_total_items: int | None = None
    known_cell_ratio: float | None = None
    bounding_cell_error: int | None = None
    final_cell_error: int | None = None


def final_truth_from_events(
    events: FatbeansCaptureEvents,
) -> ReplayFinalTruth | None:
    """Return the latest settlement inventory truth, if present."""
    for state in reversed(events.states):
        if not state.inventory_items:
            continue
        return ReplayFinalTruth(
            sort_id=state.sort_id,
            total_cells=sum(item.cells for item in state.inventory_items),
            total_items=len(state.inventory_items),
        )
    return None


def _state_by_sort_id(events: FatbeansCaptureEvents) -> dict[int, FatbeansStateEvent]:
    return {state.sort_id: state for state in events.states}


def layout_replay_stages(
    events: FatbeansCaptureEvents,
) -> tuple[LayoutReplayStage, ...]:
    """Replay all layout-bearing batches and score them against final truth.

    The score is deliberately descriptive. ``bounding_cell_error`` compares
    ``max_row * 10`` with final total cells, but it is not a model prediction:
    packed layouts can contain holes.
    """
    final_truth = final_truth_from_events(events)
    states = _state_by_sort_id(events)
    rows: list[LayoutReplayStage] = []
    live_state = LiveSessionState()
    for batch in live_batches_from_fatbeans_events(events):
        live_state = apply_observation_batch(live_state, batch)
        if not batch.grid_items:
            continue
        layout = layout_evidence_from_batch(
            replace(batch, grid_items=live_state.grid_items)
        )
        if layout is None:
            continue
        state = states.get(batch.sequence or -1)
        final_cells = final_truth.total_cells if final_truth is not None else None
        final_items = final_truth.total_items if final_truth is not None else None
        rows.append(
            LayoutReplayStage(
                sort_id=batch.sequence or 0,
                round_no=state.round_no if state is not None else None,
                message_id=state.message_id if state is not None else 0,
                phase=batch.phase,
                layout=layout,
                final_total_cells=final_cells,
                final_total_items=final_items,
                known_cell_ratio=(
                    layout.total_cells / final_cells
                    if final_cells and final_cells > 0
                    else None
                ),
                bounding_cell_error=(
                    layout.bounding_cells - final_cells
                    if final_cells is not None
                    else None
                ),
                final_cell_error=(
                    layout.total_cells - final_cells
                    if final_cells is not None
                    else None
                ),
            )
        )
    return tuple(rows)


__all__ = (
    "LayoutReplayStage",
    "ReplayFinalTruth",
    "final_truth_from_events",
    "layout_replay_stages",
)
