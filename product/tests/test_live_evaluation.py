from pathlib import Path

from bidking_lab.live.evaluation import (
    FatbeansLayoutEvaluation,
    FatbeansLayoutEvaluationError,
    evaluate_fatbeans_layout_path,
    fit_layout_estimate_policy,
    layout_policy_error_metrics,
    layout_sample_rows_from_stages,
    summarize_fatbeans_layout_evaluation,
)
from bidking_lab.live.fatbeans import FatbeansStateEvent
from bidking_lab.live.layout import LayoutEstimatePolicy, LayoutEvidence, LayoutItemEvidence
from bidking_lab.live.replay import LayoutReplayStage


def _layout() -> LayoutEvidence:
    return LayoutEvidence(
        sequence=10,
        items=(
            LayoutItemEvidence(
                cells=4,
                row=1,
                col=1,
                width=2,
                height=2,
                bottom_row=2,
                right_col=2,
                quality=4,
                shape_key="22",
                local_index=0,
            ),
            LayoutItemEvidence(
                cells=1,
                row=5,
                col=10,
                width=1,
                height=1,
                bottom_row=5,
                right_col=10,
                quality=2,
                shape_key="11",
                local_index=49,
            ),
        ),
        max_row=5,
        total_cells=5,
        bounding_cells=50,
        sparsity_ratio=0.9,
        bottom_tail_item_count=1,
        known_quality_count=2,
        unknown_quality_count=0,
    )


def test_layout_sample_rows_from_stages_are_stable_logs() -> None:
    stage = LayoutReplayStage(
        sort_id=10,
        round_no=2,
        message_id=0x25,
        phase="reading",
        layout=_layout(),
        final_total_cells=80,
        final_total_items=30,
        known_cell_ratio=5 / 80,
        bounding_cell_error=-30,
        final_cell_error=-75,
    )
    state = FatbeansStateEvent(
        sort_id=10,
        capture_time="2026-05-29T20:00:00+08:00",
        message_id=0x25,
        session_id="2401:test",
        map_id=2401,
        round_index=2,
    )

    rows = layout_sample_rows_from_stages(
        file="sample.json",
        stages=(stage,),
        states_by_sort={10: state},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.file == "sample.json"
    assert row.stage_index == 1
    assert row.map_id == 2401
    assert row.round_no == 2
    assert row.known_cells == 5
    assert row.max_row == 5
    assert row.final_total_cells == 80
    assert row.bounding_cell_error == -30
    assert row.estimate_min_cells == 5
    assert row.estimate_p50 is None
    assert row.estimate_policy == "conservative-v0"
    assert row.as_dict()["risk"].startswith("低")


def test_evaluate_fatbeans_layout_path_records_bad_files(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    ignored = tmp_path / "ignored.txt"
    ignored.write_text("not json either", encoding="utf-8")

    result = evaluate_fatbeans_layout_path(tmp_path)

    assert result.rows == ()
    assert len(result.errors) == 1
    assert result.errors[0].file == str(bad)
    assert result.files == ("bad.json",)


def test_evaluate_fatbeans_layout_path_can_filter_names(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    ignored = tmp_path / "legacy.json"
    ignored.write_text("not json", encoding="utf-8")

    result = evaluate_fatbeans_layout_path(
        tmp_path,
        name_regex=r"^bad",
    )

    assert result.rows == ()
    assert len(result.errors) == 1
    assert result.errors[0].file == str(bad)
    assert result.files == ("bad.json",)


def test_summarize_fatbeans_layout_evaluation_reports_coverage() -> None:
    stage_sparse = LayoutReplayStage(
        sort_id=10,
        round_no=1,
        message_id=0x25,
        phase="reading",
        layout=_layout(),
        final_total_cells=180,
        final_total_items=30,
        known_cell_ratio=5 / 180,
        bounding_cell_error=-130,
        final_cell_error=-175,
    )
    dense_layout = LayoutEvidence(
        sequence=20,
        items=(
            LayoutItemEvidence(
                cells=9,
                row=1,
                col=1,
                width=3,
                height=3,
                bottom_row=3,
                right_col=3,
            ),
            LayoutItemEvidence(
                cells=9,
                row=2,
                col=4,
                width=3,
                height=3,
                bottom_row=4,
                right_col=6,
            ),
            LayoutItemEvidence(
                cells=9,
                row=3,
                col=7,
                width=3,
                height=3,
                bottom_row=5,
                right_col=9,
            ),
        ),
        max_row=5,
        total_cells=27,
        bounding_cells=50,
        sparsity_ratio=0.2,
        bottom_tail_item_count=3,
        known_quality_count=0,
        unknown_quality_count=3,
    )
    stage_dense = LayoutReplayStage(
        sort_id=20,
        round_no=2,
        message_id=0x25,
        phase="reading",
        layout=dense_layout,
        final_total_cells=90,
        final_total_items=20,
        known_cell_ratio=27 / 90,
        bounding_cell_error=-40,
        final_cell_error=-63,
    )
    rows = (
        *layout_sample_rows_from_stages(
            file="ethan_sparse_bottom_01.json",
            stages=(stage_sparse,),
        ),
        *layout_sample_rows_from_stages(
            file="aisha_dense_bottom_01.json",
            stages=(stage_dense,),
        ),
    )
    evaluation = FatbeansLayoutEvaluation(
        rows=rows,
        errors=(
            FatbeansLayoutEvaluationError(
                file="bad.json",
                message="broken",
            ),
        ),
        files=(
            "ethan_sparse_bottom_01.json",
            "aisha_dense_bottom_01.json",
            "empty_layout.json",
            "bad.json",
        ),
    )

    summary = summarize_fatbeans_layout_evaluation(
        evaluation,
        target_files=2,
        min_sparse_rows=1,
        min_dense_rows=1,
    )

    assert summary.files == 4
    assert summary.files_with_rows == 2
    assert summary.rows == 2
    assert summary.errors == 1
    assert summary.rows_with_final_truth == 2
    assert summary.sparse_rows == 1
    assert summary.dense_rows == 1
    assert summary.large_warehouse_files == 1
    assert summary.max_final_total_cells == 180
    assert summary.mean_abs_bounding_error == 85
    assert summary.fit_readiness == "样本不足"
    assert summary.notes == ("存在 1 个解析错误文件",)


def test_fit_layout_estimate_policy_uses_group_medians() -> None:
    rows = []
    for idx, error in enumerate((4, 6, 8, 10, 12), start=1):
        final_cells = 100
        layout = LayoutEvidence(
            sequence=idx,
            items=(
                LayoutItemEvidence(
                    cells=35,
                    row=1,
                    col=1,
                    width=5,
                    height=7,
                    bottom_row=7,
                    right_col=5,
                ),
                LayoutItemEvidence(
                    cells=9,
                    row=8,
                    col=1,
                    width=3,
                    height=3,
                    bottom_row=10,
                    right_col=3,
                ),
                LayoutItemEvidence(
                    cells=9,
                    row=8,
                    col=4,
                    width=3,
                    height=3,
                    bottom_row=10,
                    right_col=6,
                ),
            ),
            max_row=(final_cells + error) // 10,
            total_cells=80,
            bounding_cells=final_cells + error,
            sparsity_ratio=0.25,
            bottom_tail_item_count=3,
            known_quality_count=0,
            unknown_quality_count=3,
        )
        rows += layout_sample_rows_from_stages(
            file=f"dense_{idx}.json",
            stages=(
                LayoutReplayStage(
                    sort_id=idx,
                    round_no=1,
                    message_id=0x25,
                    phase="reading",
                    layout=layout,
                    final_total_cells=final_cells,
                    final_total_items=10,
                    known_cell_ratio=0.8,
                    bounding_cell_error=error,
                    final_cell_error=-20,
                ),
            ),
        )
    for idx, error in enumerate((7, 9, 11, 13, 15), start=10):
        final_cells = 100
        layout = LayoutEvidence(
            sequence=idx,
            items=(
                LayoutItemEvidence(
                    cells=20,
                    row=1,
                    col=1,
                    width=5,
                    height=4,
                    bottom_row=4,
                    right_col=5,
                ),
            ),
            max_row=(final_cells + error) // 10,
            total_cells=70,
            bounding_cells=final_cells + error,
            sparsity_ratio=0.40,
            bottom_tail_item_count=2,
            known_quality_count=0,
            unknown_quality_count=1,
        )
        rows += layout_sample_rows_from_stages(
            file=f"medium_{idx}.json",
            stages=(
                LayoutReplayStage(
                    sort_id=idx,
                    round_no=1,
                    message_id=0x25,
                    phase="reading",
                    layout=layout,
                    final_total_cells=final_cells,
                    final_total_items=10,
                    known_cell_ratio=0.7,
                    bounding_cell_error=error,
                    final_cell_error=-30,
                ),
            ),
        )
    evaluation = FatbeansLayoutEvaluation(rows=tuple(rows))

    fit = fit_layout_estimate_policy(evaluation)

    assert fit.policy.name == "sample-fit-v1"
    assert fit.policy.dense_p50_margin == 8
    assert fit.policy.medium_p50_margin == 11
    assert fit.dense_samples == 5
    assert fit.medium_samples == 5


def test_layout_policy_error_metrics_reports_groups_and_skips_sparse() -> None:
    sparse_stage = LayoutReplayStage(
        sort_id=1,
        round_no=1,
        message_id=0x25,
        phase="reading",
        layout=_layout(),
        final_total_cells=80,
        final_total_items=20,
        known_cell_ratio=5 / 80,
        bounding_cell_error=-30,
        final_cell_error=-75,
    )
    dense_layout = LayoutEvidence(
        sequence=2,
        items=(
            LayoutItemEvidence(
                cells=16,
                row=1,
                col=1,
                width=4,
                height=4,
                bottom_row=4,
                right_col=4,
            ),
            LayoutItemEvidence(
                cells=9,
                row=8,
                col=1,
                width=3,
                height=3,
                bottom_row=10,
                right_col=3,
            ),
            LayoutItemEvidence(
                cells=9,
                row=8,
                col=4,
                width=3,
                height=3,
                bottom_row=10,
                right_col=6,
            ),
        ),
        max_row=10,
        total_cells=34,
        bounding_cells=100,
        sparsity_ratio=0.30,
        bottom_tail_item_count=3,
        known_quality_count=0,
        unknown_quality_count=3,
    )
    dense_stage = LayoutReplayStage(
        sort_id=2,
        round_no=1,
        message_id=0x25,
        phase="reading",
        layout=dense_layout,
        final_total_cells=94,
        final_total_items=20,
        known_cell_ratio=34 / 94,
        bounding_cell_error=6,
        final_cell_error=-60,
    )
    evaluation = FatbeansLayoutEvaluation(
        rows=(
            *layout_sample_rows_from_stages(
                file="sparse.json",
                stages=(sparse_stage,),
            ),
            *layout_sample_rows_from_stages(
                file="dense.json",
                stages=(dense_stage,),
            ),
        ),
    )

    metrics = layout_policy_error_metrics(
        evaluation,
        policy=LayoutEstimatePolicy(name="fit", dense_p50_margin=6),
    )

    assert metrics.all_rows.rows == 2
    assert metrics.all_rows.evaluated == 1
    assert metrics.all_rows.skipped == 1
    assert metrics.all_rows.mae == 0
    assert metrics.non_sparse.evaluated == 1
    assert metrics.dense.bias == 0
    assert metrics.sparse.skipped == 1
