from __future__ import annotations

import ast
import base64
from pathlib import Path
import re
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def overlay_tree():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.fixture
def copy_surface(overlay_tree):
    """Execute the production copy handlers without starting Tk or product services."""
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in overlay_tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("EXPORT_DIAGNOSTIC_")
            for target in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_diagnostic_export_tip":
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "DiagnosticExportPathError":
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "AhmadTkOverlay":
            nodes.extend(
                method for method in node.body
                if isinstance(method, ast.FunctionDef) and method.name == "export_diagnostic_package"
            )
    scope = {"time": time, "DEFAULT_DIAGNOSTIC_PROFILE": "portable", "ACCENT": "test-accent"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "copy-handlers", "exec"), scope)
    return scope


def _fake_overlay(tmp_path: Path, snapshot: dict, summary: dict):
    events = []

    def widget(name):
        return SimpleNamespace(configure=lambda **kwargs: events.append((name, kwargs["text"])))

    overlay = SimpleNamespace(
        snapshot_path=tmp_path / "latest_snapshot.json",
        _last_live_snapshot=snapshot,
        _last_summary=summary,
        _last_live_summary={},
        diagnostic_profile="portable",
        show_taskbar=False,
        status=widget("status"),
        manual_status=widget("manual"),
        export_diag_tip=SimpleNamespace(set_text=lambda text: events.append(("tip", text))),
        _mark_summary_performance=lambda *_args: events.append(("performance", "export_ms")),
    )
    return overlay, events


def test_export_tip_describes_available_contents_path_and_private_delivery(copy_surface, tmp_path):
    destination = tmp_path / "中文文档" / "BidKingLive" / "exports"
    copy_surface["_diagnostic_export_dir"] = lambda _path: destination

    text = copy_surface["_diagnostic_export_tip"](tmp_path / "latest_snapshot.json")

    assert "当前可用的对局状态和近期日志" in text
    assert str(destination) in text
    assert "仅私发给支持人员" in text
    assert "不要发群或公开上传" in text
    assert "脱敏" not in text
    assert "已导出" not in text


def test_unavailable_export_tip_preserves_destination_and_gives_recovery(copy_surface, tmp_path):
    def unavailable(_path):
        raise copy_surface["DiagnosticExportPathError"]("系统文档目录不可写或不安全")

    copy_surface["_diagnostic_export_dir"] = unavailable
    text = copy_surface["_diagnostic_export_tip"](tmp_path / "latest_snapshot.json")

    assert "系统“文档”" in text
    assert "BidKingLive\\exports" in text
    assert "导出会失败" in text
    assert "重试" in text
    assert "脱敏" not in text
    assert "已导出" not in text


@pytest.mark.parametrize("has_snapshot", (True, False))
@pytest.mark.parametrize("explorer_opened", (True, False))
def test_export_success_and_repeat_click_report_only_saved_file(
    copy_surface, tmp_path, has_snapshot, explorer_opened
):
    snapshot = {"session_id": "copy-test", "hero": "ahmed"} if has_snapshot else {}
    summary = {"status": "ok"} if has_snapshot else {}
    overlay, events = _fake_overlay(tmp_path, snapshot, summary)
    copy_surface["_read_json"] = lambda _path: {}
    writes = []

    def write_export(**kwargs):
        assert not events or events[-1][0] == "explorer"
        writes.append(kwargs)
        path = tmp_path / f"诊断-{len(writes)}.zip"
        path.touch()
        events.append(("write", path))
        return path

    def show_export(path):
        assert path.is_file()
        events.append(("explorer", path))
        return explorer_opened

    copy_surface["_write_diagnostic_export"] = write_export
    copy_surface["_show_export_in_explorer"] = show_export
    for click in (1, 2):
        assert copy_surface["export_diagnostic_package"](overlay) == "break"
        path = tmp_path / f"诊断-{click}.zip"
        assert overlay.last_diagnostic_export_path == path
        assert events[-6:] == [
            ("write", path),
            ("performance", "export_ms"),
            ("tip", f"已导出诊断：{path.resolve()}"),
            ("status", "已导出诊断"),
            ("manual", f"诊断包：{path.name}"),
            ("explorer", path),
        ]
        assert writes[-1]["snapshot_path"] == overlay.snapshot_path
        assert writes[-1]["current_summary"] == summary
        assert writes[-1]["diagnostic_profile"] == "portable"
        assert writes[-1]["show_taskbar"] is False
        assert "raw_forensics" not in writes[-1]
        if has_snapshot:
            assert writes[-1]["snapshot"] is snapshot
        else:
            assert writes[-1]["snapshot"]["phase"] == "missing_snapshot"
            assert writes[-1]["snapshot"]["session_id"] == "no_snapshot"


@pytest.mark.parametrize("reason", ("系统文档目录不可写或不安全", "合成写入失败"))
def test_export_failure_keeps_reason_without_success_or_explorer(copy_surface, tmp_path, reason):
    overlay, events = _fake_overlay(tmp_path, {"session_id": "copy-test"}, {})
    prior_path = tmp_path / "previous.zip"
    overlay.last_diagnostic_export_path = prior_path

    def fail(**_kwargs):
        raise copy_surface["DiagnosticExportPathError"](reason)

    copy_surface["_write_diagnostic_export"] = fail
    copy_surface["_show_export_in_explorer"] = lambda path: events.append(("explorer", path))

    assert copy_surface["export_diagnostic_package"](overlay) == "break"
    assert events == [("tip", f"导出失败: {reason}"), ("status", "导出失败")]
    assert overlay.last_diagnostic_export_path == prior_path
    assert not prior_path.exists()


@pytest.mark.parametrize("shell_name", ("powershell.exe", "pwsh.exe"))
def test_collection_copy_in_real_shell_without_running_machine_probes(shell_name, tmp_path):
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    source = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")
    functions = re.findall(
        r"(?ms)^function Write-BidKingDiagnostic(?:Progress\([^\n]*|Failure[^\n]*)\n.*?^}",
        source,
    )
    assert len(functions) == 2
    progress = re.findall(r"(?m)^Write-BidKingDiagnosticProgress [1-7] .+$", source)
    assert len(progress) == 7
    completion_start = source.index('Write-Host "[完成]')
    completion_end = source.index("# 顺手在资源管理器", completion_start)
    completion = source[completion_start:completion_end]
    assert all(line.startswith("Write-Host ") for line in completion.splitlines())
    report_path = str(tmp_path / "中文文档" / "BidKingLive" / "exports" / "诊断.txt")
    command = "\n".join([
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)",
        *functions,
        "$outFile = '" + report_path.replace("'", "''") + "'",
        *progress,
        completion,
        "Write-BidKingDiagnosticFailure",
    ])
    completed = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(command.encode("utf-16le")).decode("ascii")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert [line.split("]", 1)[0] for line in lines if line.startswith("[进度 ")] == [
        f"[进度 {step}/7" for step in range(1, 8)
    ]
    assert "[进度 1/7] 开始收集诊断" in lines
    assert f"[完成] 诊断报告已生成: {report_path}" in lines
    assert "仅私发给支持人员" in completed.stdout
    assert "不要发群或公开上传" in completed.stdout
    assert "系统、网卡与软件环境信息" in completed.stdout
    assert "[失败] 诊断收集未完成；请保留本窗口提示并联系支持人员。" in lines
    assert "脱敏" not in completed.stdout
    assert not Path(report_path).exists()


def test_package_instructions_match_contents_path_recovery_and_private_scope():
    text = (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(encoding="utf-8-sig")
    export_button = text.split("  导出      =", 1)[1].split("  地图      =", 1)[0]
    troubleshooting = text.split("· 估价不对 / 卡住 / 结算没进入:", 1)[1].split("· 激活显示", 1)[0]
    for section in (export_button, troubleshooting):
        assert "当前可用的对局状态和近期日志" in section
        assert "文档\\BidKingLive\\exports" in section
        assert "提示" in section and "路径" in section
        assert "脱敏" not in section
        assert "已打开" not in section
    assert "系统与日志诊断报告" in troubleshooting
    assert "失败" in troubleshooting and "错误提示" in troubleshooting
    assert "仅私发支持人员" in troubleshooting
    assert "不要发群或公开上传" in troubleshooting
    assert "不要发送活动日志目录里的原始文件" in troubleshooting
