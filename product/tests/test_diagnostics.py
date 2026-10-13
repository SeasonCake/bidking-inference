from pathlib import Path

from bidking_lab.inference.diagnostics import (
    has_layout_conflict,
    layout_conflict_root,
)


def test_layout_conflict_root_splits_overlap_overflow_and_trust() -> None:
    root = layout_conflict_root(
        (
            "footprint_overflow:1",
            "footprint_overlap_cells:4",
            "footprint_count_relaxed:2->0",
        ),
        footprint_count=2,
        trusted_footprint_count=0,
    )

    assert root == (
        "footprint_overlap;"
        "footprint_overflow;"
        "footprint_count_relaxed;"
        "all_footprints_untrusted"
    )
    assert has_layout_conflict(root)


def test_layout_conflict_root_empty_without_layout_markers() -> None:
    assert layout_conflict_root("q6_below_drop_prior:0.1<prior:0.5") == ""
    assert has_layout_conflict("") is False


def test_collection_script_prioritizes_capture_error_without_uninstall_claim() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8"
    )

    assert "明确抓包初始化错误(优先判据)" in script
    assert "npcap_device_open_failed" in script
    assert "仅安装不证明正在拦截" in script
    assert "实际 NDIS 过滤" in script
    assert "请【彻底退出/卸载】" not in script


def test_collection_script_does_not_treat_historical_logs_as_current_launch() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8"
    )

    assert "startup_session.json" in script
    assert "Test-DiagnosticFileForLaunch" in script
    assert "历史 monitor 日志不作为本次启动证据" in script
