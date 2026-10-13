"""Batch evaluation helpers for Fatbeans layout samples."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import statistics
from typing import Iterable, Mapping, Sequence

from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansStateEvent,
    parse_fatbeans_capture,
)
from bidking_lab.live.layout import (
    LayoutEvidence,
    LayoutEstimatePolicy,
    estimate_warehouse_from_layout,
    layout_risk_label,
)
from bidking_lab.live.replay import LayoutReplayStage, layout_replay_stages


@dataclass(frozen=True)
class FatbeansLayoutSampleRow:
    """One structured log row for layout-depth model fitting."""

    file: str
    stage_index: int
    sort_id: int
    map_id: int | None
    round_no: int | None
    phase: str
    known_items: int
    known_cells: int
    max_row: int
    bounding_cells: int
    sparsity_ratio: float
    bottom_tail_item_count: int
    final_total_cells: int | None
    final_total_items: int | None
    known_cell_ratio: float | None
    bounding_cell_error: int | None
    final_cell_error: int | None
    estimate_min_cells: int
    estimate_p50: int | None
    estimate_p90: int | None
    estimate_confidence: str
    estimate_locked: bool
    estimate_policy: str
    risk: str

    def as_dict(self) -> dict[str, object]:
        """Return a stable serializable row for CSV/JSONL logs."""
        return {
            "file": self.file,
            "stage_index": self.stage_index,
            "sort_id": self.sort_id,
            "map_id": self.map_id,
            "round_no": self.round_no,
            "phase": self.phase,
            "known_items": self.known_items,
            "known_cells": self.known_cells,
            "max_row": self.max_row,
            "bounding_cells": self.bounding_cells,
            "sparsity_ratio": self.sparsity_ratio,
            "bottom_tail_item_count": self.bottom_tail_item_count,
            "final_total_cells": self.final_total_cells,
            "final_total_items": self.final_total_items,
            "known_cell_ratio": self.known_cell_ratio,
            "bounding_cell_error": self.bounding_cell_error,
            "final_cell_error": self.final_cell_error,
            "estimate_min_cells": self.estimate_min_cells,
            "estimate_p50": self.estimate_p50,
            "estimate_p90": self.estimate_p90,
            "estimate_confidence": self.estimate_confidence,
            "estimate_locked": self.estimate_locked,
            "estimate_policy": self.estimate_policy,
            "risk": self.risk,
        }


@dataclass(frozen=True)
class FatbeansLayoutEvaluationError:
    """Non-fatal batch evaluation error for one capture file."""

    file: str
    message: str


@dataclass(frozen=True)
class FatbeansLayoutEvaluation:
    """Batch evaluation result for a directory of Fatbeans captures."""

    rows: tuple[FatbeansLayoutSampleRow, ...]
    errors: tuple[FatbeansLayoutEvaluationError, ...] = ()
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class FatbeansLayoutEvaluationSummary:
    """Small coverage summary for deciding whether samples are fit-ready."""

    files: int
    files_with_rows: int
    rows: int
    errors: int
    rows_with_final_truth: int
    sparse_rows: int
    dense_rows: int
    large_warehouse_files: int
    max_final_total_cells: int | None
    mean_abs_bounding_error: float | None
    fit_readiness: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a stable serializable summary."""
        return {
            "files": self.files,
            "files_with_rows": self.files_with_rows,
            "rows": self.rows,
            "errors": self.errors,
            "rows_with_final_truth": self.rows_with_final_truth,
            "sparse_rows": self.sparse_rows,
            "dense_rows": self.dense_rows,
            "large_warehouse_files": self.large_warehouse_files,
            "max_final_total_cells": self.max_final_total_cells,
            "mean_abs_bounding_error": self.mean_abs_bounding_error,
            "fit_readiness": self.fit_readiness,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class LayoutPolicyFit:
    """Fitted layout estimate policy and diagnostics."""

    policy: LayoutEstimatePolicy
    dense_samples: int
    medium_samples: int
    sparse_samples: int
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a stable serializable policy fit."""
        return {
            "policy": {
                "name": self.policy.name,
                "sparse_tail_item_max": self.policy.sparse_tail_item_max,
                "sparse_sparsity_min": self.policy.sparse_sparsity_min,
                "high_sparsity_min": self.policy.high_sparsity_min,
                "dense_tail_item_min": self.policy.dense_tail_item_min,
                "dense_sparsity_max": self.policy.dense_sparsity_max,
                "dense_p50_margin": self.policy.dense_p50_margin,
                "medium_p50_margin": self.policy.medium_p50_margin,
            },
            "dense_samples": self.dense_samples,
            "medium_samples": self.medium_samples,
            "sparse_samples": self.sparse_samples,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class LayoutPolicyErrorBucket:
    """P50 error metrics for one layout-policy row group."""

    rows: int
    evaluated: int
    skipped: int
    mae: float | None
    bias: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "evaluated": self.evaluated,
            "skipped": self.skipped,
            "mae": self.mae, #mae is the mean absolute error, which can estimate the average error of the model.
            "bias": self.bias, #bias is the bias of the model, which can estimate the average error of the model.
        }


@dataclass(frozen=True)
class LayoutPolicyErrorMetrics:
    """Grouped P50 error metrics for one layout estimate policy."""

    policy_name: str
    all_rows: LayoutPolicyErrorBucket
    non_sparse: LayoutPolicyErrorBucket
    dense: LayoutPolicyErrorBucket
    medium: LayoutPolicyErrorBucket
    sparse: LayoutPolicyErrorBucket

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_name": self.policy_name,
            "all": self.all_rows.as_dict(),
            "non_sparse": self.non_sparse.as_dict(),
            "dense": self.dense.as_dict(),
            "medium": self.medium.as_dict(),
            "sparse": self.sparse.as_dict(),
        }


def _states_by_sort(
    states: Sequence[FatbeansStateEvent],
) -> Mapping[int, FatbeansStateEvent]:
    return {state.sort_id: state for state in states}


def layout_sample_rows_from_stages(
    *,
    file: str,
    stages: Sequence[LayoutReplayStage],
    states_by_sort: Mapping[int, FatbeansStateEvent] | None = None,
) -> tuple[FatbeansLayoutSampleRow, ...]:
    """Convert replay stages into stable rows for logs/model fitting."""
    rows: list[FatbeansLayoutSampleRow] = []
    states_by_sort = states_by_sort or {}
    for index, stage in enumerate(stages, start=1):
        state = states_by_sort.get(stage.sort_id)
        estimate = estimate_warehouse_from_layout(
            stage.layout,
            final_total_cells=stage.final_total_cells,
        )
        rows.append(
            FatbeansLayoutSampleRow(
                file=file,
                stage_index=index,
                sort_id=stage.sort_id,
                map_id=state.map_id if state is not None else None,
                round_no=stage.round_no,
                phase=stage.phase,
                known_items=len(stage.layout.items),
                known_cells=stage.layout.total_cells,
                max_row=stage.layout.max_row,
                bounding_cells=stage.layout.bounding_cells,
                sparsity_ratio=stage.layout.sparsity_ratio,
                bottom_tail_item_count=stage.layout.bottom_tail_item_count,
                final_total_cells=stage.final_total_cells,
                final_total_items=stage.final_total_items,
                known_cell_ratio=stage.known_cell_ratio,
                bounding_cell_error=stage.bounding_cell_error,
                final_cell_error=stage.final_cell_error,
                estimate_min_cells=estimate.min_reasonable_cells,
                estimate_p50=estimate.p50_guess,
                estimate_p90=estimate.p90_guess,
                estimate_confidence=estimate.confidence,
                estimate_locked=estimate.locked,
                estimate_policy=estimate.policy_name,
                risk=layout_risk_label(stage.layout),
            )
        )
    return tuple(rows)


def evaluate_fatbeans_layout_events(
    events: FatbeansCaptureEvents,
    *,
    file: str,
) -> tuple[FatbeansLayoutSampleRow, ...]:
    """Evaluate one parsed Fatbeans capture for layout fitting logs."""
    return layout_sample_rows_from_stages(
        file=file,
        stages=layout_replay_stages(events),
        states_by_sort=_states_by_sort(events.states),
    )


def evaluate_fatbeans_layout_capture(
    path: str | Path,
) -> tuple[FatbeansLayoutSampleRow, ...]:
    """Parse and evaluate one Fatbeans JSON capture."""
    capture_path = Path(path)
    return evaluate_fatbeans_layout_events(
        parse_fatbeans_capture(capture_path),
        file=capture_path.name,
    )


def _json_files(root: Path, *, name_regex: str | None = None) -> Iterable[Path]:
    pattern = re.compile(name_regex) if name_regex else None
    if root.is_file():
        if pattern is None or pattern.search(root.name):
            yield root
        return
    for path in sorted(root.rglob("*.json")):
        if pattern is None or pattern.search(path.name):
            yield path


def evaluate_fatbeans_layout_path(
    path: str | Path,
    *,
    name_regex: str | None = None,
) -> FatbeansLayoutEvaluation:
    """Evaluate one Fatbeans JSON file or a directory of JSON captures.

    Bad files are recorded as errors so long-running sample collection can
    continue and produce partial logs.
    """
    root = Path(path)
    rows: list[FatbeansLayoutSampleRow] = []
    errors: list[FatbeansLayoutEvaluationError] = []
    files: list[str] = []
    for capture_path in _json_files(root, name_regex=name_regex):
        files.append(capture_path.name)
        try:
            rows.extend(evaluate_fatbeans_layout_capture(capture_path))
        except Exception as exc:  # noqa: BLE001 - batch diagnostic boundary
            errors.append(
                FatbeansLayoutEvaluationError(
                    file=str(capture_path),
                    message=str(exc),
                )
            )
    return FatbeansLayoutEvaluation(
        rows=tuple(rows),
        errors=tuple(errors),
        files=tuple(files),
    )


def summarize_fatbeans_layout_evaluation(
    evaluation: FatbeansLayoutEvaluation,
    *,
    target_files: int = 40,
    min_sparse_rows: int = 5,
    min_dense_rows: int = 5,
    large_warehouse_threshold: int = 160,
) -> FatbeansLayoutEvaluationSummary:
    """Summarize sample coverage for fitting layout-depth estimates."""
    rows = evaluation.rows
    files = set(evaluation.files) if evaluation.files else {row.file for row in rows}
    files_with_rows = {row.file for row in rows}
    rows_with_final_truth = [
        row for row in rows if row.final_total_cells is not None
    ]
    sparse_rows = [
        row for row in rows
        if row.bottom_tail_item_count <= 1 and row.sparsity_ratio >= 0.45
    ]
    dense_rows = [
        row for row in rows
        if row.bottom_tail_item_count >= 3 and row.sparsity_ratio <= 0.35
    ]
    large_warehouse_files = {
        row.file
        for row in rows_with_final_truth
        if (
            row.final_total_cells is not None
            and row.final_total_cells >= large_warehouse_threshold
        )
    }
    final_cells = [
        row.final_total_cells
        for row in rows_with_final_truth
        if row.final_total_cells is not None
    ]
    bounding_errors = [
        abs(row.bounding_cell_error)
        for row in rows_with_final_truth
        if row.bounding_cell_error is not None
    ]

    notes: list[str] = []
    if len(files) < target_files:
        notes.append(f"文件数不足 {target_files}，当前 {len(files)}")
    if len(sparse_rows) < min_sparse_rows:
        notes.append(f"sparse 底部样本不足 {min_sparse_rows}，当前 {len(sparse_rows)}")
    if len(dense_rows) < min_dense_rows:
        notes.append(f"dense 底部样本不足 {min_dense_rows}，当前 {len(dense_rows)}")
    if not large_warehouse_files:
        notes.append(f"缺少 >= {large_warehouse_threshold} 格的大仓样本")
    if evaluation.errors:
        notes.append(f"存在 {len(evaluation.errors)} 个解析错误文件")

    fit_readiness = "可拟合v1" if not notes else "样本不足"
    return FatbeansLayoutEvaluationSummary(
        files=len(files),
        files_with_rows=len(files_with_rows),
        rows=len(rows),
        errors=len(evaluation.errors),
        rows_with_final_truth=len(rows_with_final_truth),
        sparse_rows=len(sparse_rows),
        dense_rows=len(dense_rows),
        large_warehouse_files=len(large_warehouse_files),
        max_final_total_cells=max(final_cells) if final_cells else None,
        mean_abs_bounding_error=(
            sum(bounding_errors) / len(bounding_errors)
            if bounding_errors
            else None
        ),
        fit_readiness=fit_readiness,
        notes=tuple(notes),
    )


def _is_sparse_row(row: FatbeansLayoutSampleRow) -> bool:
    return row.bottom_tail_item_count <= 1 and row.sparsity_ratio >= 0.45


def _is_dense_row(row: FatbeansLayoutSampleRow) -> bool:
    return row.bottom_tail_item_count >= 3 and row.sparsity_ratio <= 0.35


def _median_nonnegative_int(values: Sequence[int]) -> int | None:
    if not values:
        return None
    return max(0, int(round(statistics.median(values))))


def fit_layout_estimate_policy(
    evaluation: FatbeansLayoutEvaluation,
    *,
    name: str = "sample-fit-v1",
    min_group_samples: int = 5,
) -> LayoutPolicyFit:
    """Fit a first-cut layout estimate policy from collected samples.

    This only tunes the P50 margins for non-sparse rows. Sparse-tail rows stay
    conservative because their error distribution is multi-modal and can miss
    deep hidden inventory.
    """
    dense_errors: list[int] = []
    medium_errors: list[int] = []
    sparse_count = 0
    for row in evaluation.rows:
        if row.bounding_cell_error is None:
            continue
        if _is_sparse_row(row):
            sparse_count += 1
            continue
        if _is_dense_row(row):
            dense_errors.append(row.bounding_cell_error)
        else:
            medium_errors.append(row.bounding_cell_error)

    notes: list[str] = []
    dense_margin = _median_nonnegative_int(dense_errors)
    medium_margin = _median_nonnegative_int(medium_errors)
    if dense_margin is None or len(dense_errors) < min_group_samples:
        dense_margin = 10
        notes.append(
            f"dense 样本不足 {min_group_samples}，沿用默认 margin 10"
        )
    if medium_margin is None or len(medium_errors) < min_group_samples:
        medium_margin = 20
        notes.append(
            f"medium 样本不足 {min_group_samples}，沿用默认 margin 20"
        )
    if sparse_count < min_group_samples:
        notes.append(
            f"sparse 样本不足 {min_group_samples}，仍保持无 P50 点估计"
        )

    return LayoutPolicyFit(
        policy=LayoutEstimatePolicy(
            name=name,
            dense_p50_margin=dense_margin,
            medium_p50_margin=medium_margin,
        ),
        dense_samples=len(dense_errors),
        medium_samples=len(medium_errors),
        sparse_samples=sparse_count,
        notes=tuple(notes),
    )


def _layout_from_sample_row(row: FatbeansLayoutSampleRow) -> LayoutEvidence:
    return LayoutEvidence(
        sequence=row.sort_id,
        items=(),
        max_row=row.max_row,
        total_cells=row.known_cells,
        bounding_cells=row.bounding_cells,
        sparsity_ratio=row.sparsity_ratio,
        bottom_tail_item_count=row.bottom_tail_item_count,
        known_quality_count=0,
        unknown_quality_count=0,
    )


def _error_bucket(
    rows: Sequence[FatbeansLayoutSampleRow],
    *,
    policy: LayoutEstimatePolicy,
) -> LayoutPolicyErrorBucket:
    errors: list[int] = []
    skipped = 0
    rows_with_truth = [
        row for row in rows if row.final_total_cells is not None
    ]
    for row in rows_with_truth:
        estimate = estimate_warehouse_from_layout(
            _layout_from_sample_row(row),
            final_total_cells=row.final_total_cells,
            policy=policy,
        )
        if estimate.p50_guess is None:
            skipped += 1
            continue
        errors.append(estimate.p50_guess - row.final_total_cells)
    return LayoutPolicyErrorBucket(
        rows=len(rows),
        evaluated=len(errors),
        skipped=skipped,
        mae=(
            sum(abs(error) for error in errors) / len(errors)
            if errors
            else None
        ),
        bias=(sum(errors) / len(errors) if errors else None),
    )


def layout_policy_error_metrics(
    evaluation: FatbeansLayoutEvaluation,
    *,
    policy: LayoutEstimatePolicy,
) -> LayoutPolicyErrorMetrics:
    """Evaluate layout P50 estimate error for one policy.

    Sparse rows are reported separately because the policy intentionally often
    skips them instead of emitting a misleading point estimate.
    """
    sparse_rows = [row for row in evaluation.rows if _is_sparse_row(row)]
    dense_rows = [row for row in evaluation.rows if _is_dense_row(row)]
    medium_rows = [
        row for row in evaluation.rows
        if not _is_sparse_row(row) and not _is_dense_row(row)
    ]
    non_sparse_rows = [
        row for row in evaluation.rows if not _is_sparse_row(row)
    ]
    return LayoutPolicyErrorMetrics(
        policy_name=policy.name,
        all_rows=_error_bucket(evaluation.rows, policy=policy),
        non_sparse=_error_bucket(non_sparse_rows, policy=policy),
        dense=_error_bucket(dense_rows, policy=policy),
        medium=_error_bucket(medium_rows, policy=policy),
        sparse=_error_bucket(sparse_rows, policy=policy),
    )


__all__ = (
    "FatbeansLayoutEvaluation",
    "FatbeansLayoutEvaluationError",
    "FatbeansLayoutEvaluationSummary",
    "FatbeansLayoutSampleRow",
    "LayoutPolicyErrorBucket",
    "LayoutPolicyErrorMetrics",
    "LayoutPolicyFit",
    "evaluate_fatbeans_layout_capture",
    "evaluate_fatbeans_layout_events",
    "evaluate_fatbeans_layout_path",
    "fit_layout_estimate_policy",
    "layout_policy_error_metrics",
    "layout_sample_rows_from_stages",
    "summarize_fatbeans_layout_evaluation",
)
