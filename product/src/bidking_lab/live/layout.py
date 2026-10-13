"""Warehouse layout evidence derived from live grid observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from bidking_lab.live.types import (
    GRID_COLUMNS,
    GridFootprint,
    GridItemObservation,
    LiveObservationBatch,
)


@dataclass(frozen=True)
class LayoutItemEvidence:
    """One item with a trusted shape-bearing warehouse coordinate."""

    cells: int
    row: int
    col: int
    width: int
    height: int
    bottom_row: int
    right_col: int
    item_id: int | None = None
    quality: int | None = None
    shape_key: str | None = None
    local_index: int | None = None
    value: int | None = None


@dataclass(frozen=True)
class LayoutEvidence:
    """Compact evidence for current warehouse layout diagnostics."""

    sequence: int | None
    items: tuple[LayoutItemEvidence, ...]
    max_row: int
    total_cells: int
    bounding_cells: int
    sparsity_ratio: float
    bottom_tail_item_count: int
    known_quality_count: int
    unknown_quality_count: int


@dataclass(frozen=True)
class LayoutWarehouseEstimate:
    """Conservative warehouse estimate from layout evidence alone."""

    min_reasonable_cells: int
    p50_guess: int | None
    p90_guess: int | None
    confidence: str
    risk_reason: str
    locked: bool = False
    policy_name: str = "conservative-v0"


@dataclass(frozen=True)
class LayoutEstimatePolicy:
    """Tunable policy for layout-depth warehouse estimates."""

    name: str = "conservative-v0"
    sparse_tail_item_max: int = 1
    sparse_sparsity_min: float = 0.45
    high_sparsity_min: float = 0.55
    dense_tail_item_min: int = 3
    dense_sparsity_max: float = 0.35
    dense_p50_margin: int = 10
    medium_p50_margin: int = 20


DEFAULT_LAYOUT_ESTIMATE_POLICY = LayoutEstimatePolicy()
SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY = LayoutEstimatePolicy(
    name="sample-fit-v1",
    dense_p50_margin=6,
    medium_p50_margin=6,
)


@dataclass(frozen=True)
class LayoutGridItemView:
    """Frontend-neutral item placement for a warehouse grid view."""

    row: int
    col: int
    width: int
    height: int
    quality: int | None
    label: str
    tooltip: str
    z_index: int


@dataclass(frozen=True)
class LayoutGridView:
    """Frontend-neutral warehouse grid view model."""

    sequence: int | None
    rows: int
    columns: int
    items: tuple[LayoutGridItemView, ...]
    summary: str
    risk: str


def latest_grid_batch(
    batches: Sequence[LiveObservationBatch],
) -> LiveObservationBatch | None:
    """Return accumulated grid knowledge, preferring pre-settlement evidence."""
    candidates = [batch for batch in batches if batch.grid_items]
    if not candidates:
        return None
    pre_settlement = [batch for batch in candidates if batch.phase != "settled"]
    selected = pre_settlement[-1] if pre_settlement else candidates[-1]

    from bidking_lab.live.state import LiveSessionState, apply_observation_batch

    state = LiveSessionState()
    for batch in batches:
        state = apply_observation_batch(state, batch)
        if batch is selected:
            break
    return replace(selected, grid_items=state.grid_items)


def layout_item_from_grid_item(
    item: GridItemObservation,
) -> LayoutItemEvidence | None:
    """Convert one grid observation into layout evidence if it has a footprint."""
    footprint: GridFootprint | None = item.footprint()
    if footprint is None:
        return None
    return LayoutItemEvidence(
        cells=item.cells,
        row=footprint.row,
        col=footprint.col,
        width=footprint.width,
        height=footprint.height,
        bottom_row=footprint.bottom_row,
        right_col=footprint.right_col,
        item_id=item.item_id,
        quality=item.quality,
        shape_key=item.shape_key,
        local_index=item.local_index,
        value=item.value,
    )


def layout_evidence_from_batch(
    batch: LiveObservationBatch,
) -> LayoutEvidence | None:
    """Build layout evidence from one live observation batch."""
    items = tuple(
        item_evidence for item in batch.grid_items
        if (item_evidence := layout_item_from_grid_item(item)) is not None
    )
    if not items:
        return None
    max_row = max(item.bottom_row for item in items)
    total_cells = sum(item.cells for item in items)
    bounding_cells = max_row * 10
    sparsity_ratio = (
        max(0.0, 1.0 - total_cells / bounding_cells)
        if bounding_cells > 0
        else 0.0
    )
    bottom_tail_start = max(1, max_row - 1)
    return LayoutEvidence(
        sequence=batch.sequence,
        items=items,
        max_row=max_row,
        total_cells=total_cells,
        bounding_cells=bounding_cells,
        sparsity_ratio=sparsity_ratio,
        bottom_tail_item_count=sum(
            1 for item in items if item.bottom_row >= bottom_tail_start
        ),
        known_quality_count=sum(1 for item in items if item.quality is not None),
        unknown_quality_count=sum(1 for item in items if item.quality is None),
    )


def layout_evidence_from_batches(
    batches: Sequence[LiveObservationBatch],
) -> LayoutEvidence | None:
    """Build layout evidence from the latest useful grid batch."""
    batch = latest_grid_batch(batches)
    if batch is None:
        return None
    return layout_evidence_from_batch(batch)


def layout_risk_label(
    evidence: LayoutEvidence,
    *,
    policy: LayoutEstimatePolicy = DEFAULT_LAYOUT_ESTIMATE_POLICY,
) -> str:
    """Return a conservative reliability label for layout-depth evidence."""
    if (
        evidence.bottom_tail_item_count <= policy.sparse_tail_item_max
        and evidence.sparsity_ratio >= policy.sparse_sparsity_min
    ):
        return "低：底部证据稀疏，仓储可能被高估或低估"
    if evidence.sparsity_ratio >= policy.high_sparsity_min:
        return "中：空洞率高，最深行只作弱参考"
    if (
        evidence.bottom_tail_item_count >= policy.dense_tail_item_min
        and evidence.sparsity_ratio <= policy.dense_sparsity_max
    ):
        return "较高：底部证据较密，布局深度更可信"
    return "中：可作布局深度参考，但不作为硬约束"


def layout_grid_view(
    evidence: LayoutEvidence,
    *,
    columns: int = GRID_COLUMNS,
) -> LayoutGridView:
    """Build a frontend-neutral grid view from layout evidence.

    Streamlit, a future desktop overlay, or any other UI should render this
    structure instead of re-deriving labels and placement from raw evidence.
    """
    risk = layout_risk_label(evidence)
    items: list[LayoutGridItemView] = []
    for idx, item in enumerate(evidence.items):
        quality = item.quality if item.quality is not None else "?"
        item_label = str(item.item_id or "")
        label = f"Q{quality}"
        if item_label:
            label = f"{label}\n{item_label}"
        tooltip = " ".join(
            str(part)
            for part in (
                f"Q{quality}",
                f"item={item.item_id or ''}",
                f"shape={item.width}x{item.height}",
                f"row={item.row}-{item.bottom_row}",
                f"col={item.col}-{item.right_col}",
            )
        )
        items.append(
            LayoutGridItemView(
                row=item.row,
                col=item.col,
                width=item.width,
                height=item.height,
                quality=item.quality,
                label=label,
                tooltip=tooltip,
                z_index=10 + idx,
            )
        )
    return LayoutGridView(
        sequence=evidence.sequence,
        rows=evidence.max_row,
        columns=columns,
        items=tuple(items),
        summary=(
            f"仓位证据图：状态 {evidence.sequence}，已放置 {len(evidence.items)} 件 / "
            f"{evidence.total_cells} 格，最深第 {evidence.max_row} 行，"
            f"边界空洞率约 {evidence.sparsity_ratio:.0%}。"
            f"{risk}。空白格只是未确认区域，不代表真实为空。"
        ),
        risk=risk,
    )


def estimate_warehouse_from_layout(
    evidence: LayoutEvidence,
    *,
    final_total_cells: int | None = None,
    policy: LayoutEstimatePolicy = DEFAULT_LAYOUT_ESTIMATE_POLICY,
) -> LayoutWarehouseEstimate:
    """Return a deliberately conservative layout-only warehouse estimate.

    This is a scaffold for future sample-fitted likelihoods. It does not treat
    ``max_row * 10`` as a hard lower bound because real layouts can have holes.
    The policy object is intentionally explicit so later sample fitting can
    tune thresholds without changing UI or replay code.
    """
    if final_total_cells is not None and evidence.total_cells >= final_total_cells:
        return LayoutWarehouseEstimate(
            min_reasonable_cells=evidence.total_cells,
            p50_guess=final_total_cells,
            p90_guess=final_total_cells,
            confidence="锁定",
            risk_reason="已知轮廓覆盖最终结算总格",
            locked=True,
            policy_name=policy.name,
        )

    risk = layout_risk_label(evidence, policy=policy)
    if risk.startswith("低"):
        confidence = "低"
        p50 = None
        p90 = None
        reason = "底部证据稀疏；只采用已知格数作为下限，不给点估计"
    elif risk.startswith("较高"):
        confidence = "中"
        p50 = max(
            evidence.total_cells,
            evidence.bounding_cells - policy.dense_p50_margin,
        )
        p90 = max(p50, evidence.bounding_cells)
        reason = "底部证据较密；边界格可作为弱参考，仍需样本拟合校准"
    else:
        confidence = "低"
        p50 = max(
            evidence.total_cells,
            evidence.bounding_cells - policy.medium_p50_margin,
        )
        p90 = max(p50, evidence.bounding_cells)
        reason = "布局深度可参考但存在空洞；点估计仅用于诊断"

    return LayoutWarehouseEstimate(
        min_reasonable_cells=evidence.total_cells,
        p50_guess=p50,
        p90_guess=p90,
        confidence=confidence,
        risk_reason=reason,
        locked=False,
        policy_name=policy.name,
    )


__all__ = (
    "LayoutEvidence",
    "LayoutEstimatePolicy",
    "LayoutGridItemView",
    "LayoutGridView",
    "LayoutItemEvidence",
    "LayoutWarehouseEstimate",
    "DEFAULT_LAYOUT_ESTIMATE_POLICY",
    "SAMPLE_FIT_LAYOUT_ESTIMATE_POLICY",
    "estimate_warehouse_from_layout",
    "latest_grid_batch",
    "layout_evidence_from_batch",
    "layout_evidence_from_batches",
    "layout_grid_view",
    "layout_item_from_grid_item",
    "layout_risk_label",
)
