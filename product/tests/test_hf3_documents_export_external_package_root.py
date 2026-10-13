from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(
    os.environ.get("BIDKING_HF3_TEST_ROOT", str(Path(__file__).resolve().parents[1]))
).resolve()


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("hf3_documents_export_overlay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_package(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "程序 包-猫猫"
    tools = root / "hero_ref" / "tools"
    tools.mkdir(parents=True)
    module_file = tools / "ahmad_tk_overlay.cp313-win_amd64.pyd"
    module_file.write_bytes(b"MZ-module")
    return root, module_file


def _support_snapshot(path: Path) -> dict[str, object]:
    snapshot = {
        "schema_version": 1,
        "created_at": 1_723_000_000.0,
        "session_id": "external:test",
        "hero": "ahmed",
        "map_id": 2408,
        "round": 2,
        "phase": "bidding",
        "raw_capture_jsonl": str(path.parent / "raw" / "private.jsonl"),
    }
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return snapshot


def test_manual_support_export_uses_documents_and_preserves_external_live_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    documents = tmp_path / "Redirected 文档"
    documents.mkdir()
    live = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live"
    (live / "raw").mkdir(parents=True)
    snapshot_path = live / "latest_snapshot.json"
    snapshot = _support_snapshot(snapshot_path)
    (live / "capture_source_status.json").write_text(
        '{"source":"wss_local","accepted_frames":2}', encoding="utf-8"
    )
    (live / "raw" / "private.jsonl").write_text(
        '{"token":"PRIVATE_CANARY","Data":"PRIVATE_RAW"}\n', encoding="utf-8"
    )
    (live / "monitor.lock").write_text('{"pid":123}', encoding="utf-8")
    (live / "sessions.jsonl").write_text('{"session_id":"PRIVATE"}\n', encoding="utf-8")
    before = {
        path.relative_to(live).as_posix(): (path.stat().st_size, path.read_bytes())
        for path in live.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: documents)
    monkeypatch.setattr(
        module,
        "_windows_local_appdata_known_folder",
        lambda: tmp_path / "LocalAppData",
    )
    monkeypatch.setattr(module, "_candidate_package_roots", lambda _snapshot: [])

    exported = module._write_diagnostic_export(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "external:test"}},
        diagnostic_profile="public-safe",
    )

    assert exported.parent == documents / "BidKingLive" / "exports"
    assert exported.is_file()
    assert exported.stat().st_nlink == 1
    assert not (live / "exports").exists()
    assert not (tmp_path / "exports").exists()
    after = {
        path.relative_to(live).as_posix(): (path.stat().st_size, path.read_bytes())
        for path in live.rglob("*")
        if path.is_file()
    }
    assert after == before
    with zipfile.ZipFile(exported) as archive:
        names = set(archive.namelist())
        assert "latest_snapshot.json" in names
        assert "capture_source_status.json" in names
        assert "raw/private.jsonl" not in names
        payload = b"\n".join(archive.read(name) for name in names)
        assert b"PRIVATE_CANARY" not in payload
        assert b"PRIVATE_RAW" not in payload


def test_documents_authority_unavailable_missing_or_reparse_fails_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    snapshot = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live" / "latest_snapshot.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "_windows_documents_known_folder",
        lambda: (_ for _ in ()).throw(module.DiagnosticExportPathError("unavailable")),
    )
    assert "不会改写到其他位置" in module._diagnostic_export_tip(snapshot)
    with pytest.raises(module.DiagnosticExportPathError):
        module._diagnostic_export_dir(snapshot)
    assert not (snapshot.parent / "exports").exists()

    missing = tmp_path / "missing-documents"
    with pytest.raises(module.DiagnosticExportPathError):
        module._diagnostic_export_dir(snapshot, _documents_root_for_test=missing)

    documents = tmp_path / "Documents"
    documents.mkdir()
    real_reparse = module._is_reparse_point
    monkeypatch.setattr(
        module,
        "_is_reparse_point",
        lambda path: Path(path) == documents or real_reparse(Path(path)),
    )
    with pytest.raises(module.DiagnosticExportPathError):
        module._diagnostic_export_dir(snapshot, _documents_root_for_test=documents)


def test_package_root_authority_accepts_identity_aliases_and_rejects_bad_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    root, module_file = _fake_package(tmp_path)

    assert module._validated_package_root(
        package_root_value=None,
        module_file=module_file,
    ) == root.resolve()
    assert module._validated_package_root(
        package_root_value=str(root),
        module_file=module_file,
    ) == root.resolve()
    if os.name == "nt":
        extended = "\\\\?\\" + str(root.resolve())
        assert module._validated_package_root(
            package_root_value=extended,
            module_file=module_file,
        ) == root.resolve()
        assert module._validated_package_root(
            package_root_value=str(root).upper(),
            module_file=module_file,
        ) == root.resolve()

    for bad in ("", ".", str(tmp_path / "foreign")):
        with pytest.raises(module.PackageRootAuthorityError):
            module._validated_package_root(
                package_root_value=bad,
                module_file=module_file,
            )

    real_reparse = module._is_reparse_point
    monkeypatch.setattr(
        module,
        "_is_reparse_point",
        lambda path: Path(path) == root or real_reparse(Path(path)),
    )
    with pytest.raises(module.PackageRootAuthorityError):
        module._validated_package_root(
            package_root_value=str(root),
            module_file=module_file,
        )
    monkeypatch.setattr(module, "_is_reparse_point", real_reparse)

    alias = tmp_path / "module-hardlink.pyd"
    try:
        os.link(module_file, alias)
    except OSError:
        pytest.skip("hardlink unavailable")
    with pytest.raises(module.PackageRootAuthorityError):
        module._validated_package_root(
            package_root_value=str(root),
            module_file=module_file,
        )


def test_external_snapshot_package_consumers_use_only_module_bound_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    root, module_file = _fake_package(tmp_path)
    (root / "BidKingHeroMonitor").mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "start_live_npcap_overlay.ps1").write_text(
        "# package launcher", encoding="utf-8"
    )
    (root / "BidKingLive.exe").write_bytes(b"MZ")
    tables = root / "data" / "raw" / "tables"
    tables.mkdir(parents=True)
    for name in module.REQUIRED_RAW_TABLES:
        (tables / name).write_text(name, encoding="utf-8")

    live = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live"
    malicious = live / "data" / "raw" / "tables"
    malicious.mkdir(parents=True)
    (live / "BidKingLive.exe").write_bytes(b"MZ-foreign")
    for name in module.REQUIRED_RAW_TABLES:
        (malicious / name).write_text("FOREIGN", encoding="utf-8")
    snapshot = live / "latest_snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "__file__", str(module_file))
    monkeypatch.setenv(module.PACKAGE_ROOT_ENV, str(root))
    assert module._package_root_with_monitor(snapshot) == root.resolve()
    launcher = module._find_calculator_launcher(snapshot)
    assert launcher == ("exe", (root / "BidKingLive.exe").resolve())
    summary = module._raw_tables_summary(snapshot)
    assert summary["authority_status"] == "ok"
    assert summary["package_root"] == str(root.resolve())
    assert all(str(root.resolve()) in row["path"] for row in summary["files"])
    assert all("FOREIGN" not in json.dumps(row) for row in summary["files"])

    monkeypatch.setenv(module.PACKAGE_ROOT_ENV, str(live))
    assert module._package_root_with_monitor(snapshot) is None
    assert module._find_calculator_launcher(snapshot) is None
    rejected = module._raw_tables_summary(snapshot)
    assert rejected["authority_status"] == "package_root_unavailable"
    assert rejected["package_root"] is None


def test_hardlinked_launcher_and_raw_table_are_not_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    root, module_file = _fake_package(tmp_path)
    (root / "scripts").mkdir()
    ps1 = root / "scripts" / "start_live_npcap_overlay.ps1"
    ps1.write_text("# safe", encoding="utf-8")
    exe = root / "BidKingLive.exe"
    exe.write_bytes(b"MZ")
    tables = root / "data" / "raw" / "tables"
    tables.mkdir(parents=True)
    for name in module.REQUIRED_RAW_TABLES:
        (tables / name).write_text(name, encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(module_file))
    monkeypatch.setenv(module.PACKAGE_ROOT_ENV, str(root))

    os.link(exe, tmp_path / "exe-link")
    assert module._find_calculator_launcher(tmp_path / "outside-snapshot.json") == (
        "ps1",
        ps1.resolve(),
    )
    os.link(ps1, tmp_path / "ps1-link")
    assert module._find_calculator_launcher(tmp_path / "outside-snapshot.json") is None
    os.link(tables / "Drop.txt", tmp_path / "drop-link")
    summary = module._raw_tables_summary(tmp_path / "outside-snapshot.json")
    assert summary["authority_status"] == "required_files_missing"
    assert summary["missing_required"] == ["Drop.txt"]


def test_restart_relauncher_revalidates_package_and_logs_to_external_live_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    root, module_file = _fake_package(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "start_live_npcap_overlay.ps1").write_text(
        "# launcher", encoding="utf-8"
    )
    exe = root / "BidKingLive.exe"
    exe.write_bytes(b"MZ")
    live = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live"
    live.mkdir(parents=True)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace()

    monkeypatch.setattr(module, "__file__", str(module_file))
    monkeypatch.setenv(module.PACKAGE_ROOT_ENV, str(root))
    monkeypatch.setattr(module.os, "name", "nt")
    module._spawn_restart_relauncher(
        ("exe", exe),
        [123],
        restart_log_path=live / "restart.log",
        _popen=fake_popen,
    )
    script = calls[0][0][-1]
    assert str(live / "restart.log") in script
    assert str(root / "data" / "logs" / "live" / "restart.log") not in script

    foreign = tmp_path / "foreign.exe"
    foreign.write_bytes(b"MZ")
    with pytest.raises(module.PackageRootAuthorityError):
        module._spawn_restart_relauncher(
            ("exe", foreign),
            [],
            restart_log_path=live / "restart.log",
            _popen=fake_popen,
        )


def test_ui_reports_and_opens_the_exact_documents_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    documents = tmp_path / "Documents"
    documents.mkdir()
    live = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live"
    live.mkdir(parents=True)
    snapshot_path = live / "latest_snapshot.json"
    snapshot = _support_snapshot(snapshot_path)
    monkeypatch.setattr(module, "_windows_documents_known_folder", lambda: documents)
    monkeypatch.setattr(
        module,
        "_windows_local_appdata_known_folder",
        lambda: tmp_path / "LocalAppData",
    )
    monkeypatch.setattr(module, "_candidate_package_roots", lambda _snapshot: [])
    opened: list[Path] = []
    monkeypatch.setattr(module, "_show_export_in_explorer", lambda path: opened.append(path) or True)

    class Widget:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def configure(self, **kwargs: object) -> None:
            self.kwargs.update(kwargs)

    class Tip:
        text = ""

        def set_text(self, text: str) -> None:
            self.text = text

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = snapshot_path
    overlay._last_live_snapshot = snapshot
    overlay._last_summary = {"status": "ok", "context": {"session_id": "external:test"}}
    overlay._last_live_summary = {}
    overlay.status = Widget()
    overlay.manual_status = Widget()
    overlay.export_diag_tip = Tip()
    overlay.show_taskbar = False
    overlay.diagnostic_profile = "public-safe"

    assert module.AhmadTkOverlay.export_diagnostic_package(overlay) == "break"
    exported = overlay.last_diagnostic_export_path
    assert opened == [exported]
    assert str(exported) in overlay.export_diag_tip.text
    assert exported.parent == documents / "BidKingLive" / "exports"


def test_explorer_selection_uses_the_validated_real_file(tmp_path: Path, monkeypatch) -> None:
    module = _overlay_module()
    export = tmp_path / "文档" / "BidKingLive" / "exports" / "HeroRefDiag.zip"
    export.parent.mkdir(parents=True)
    export.write_bytes(b"PK")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace()

    monkeypatch.setattr(module.os, "name", "nt")
    assert module._show_export_in_explorer(export, _popen=fake_popen) is True
    assert calls[0][0][0] == "explorer.exe"
    assert str(export.resolve()) in calls[0][0][1]
    assert calls[0][1]["creationflags"] & 0x08000000


def test_new_export_never_overwrites_same_second_name(tmp_path: Path) -> None:
    module = _overlay_module()
    existing = tmp_path / "HeroRefDiag-20260815-010203-session.zip"
    existing.write_bytes(b"EXISTING")
    with module._new_diagnostic_export_file(
        tmp_path,
        timestamp="20260815-010203",
        safe_session="session",
    ) as (created, stream):
        stream.write(b"NEW")
    assert existing.read_bytes() == b"EXISTING"
    assert created.name == "HeroRefDiag-20260815-010203-session-2.zip"
    assert created.read_bytes() == b"NEW"
    assert stat.S_ISREG(created.stat().st_mode)
    assert created.stat().st_nlink == 1


def test_windivert_cleanup_rejects_package_prefix_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    root = tmp_path / "package"
    root.mkdir()
    sibling_binary = str(root) + "-foreign\\WinDivert64.sys"
    calls: list[list[str]] = []

    def runner(command: list[str]):
        calls.append(command)
        if command[:2] == ["sc", "qc"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"BINARY_PATH_NAME : \\??\\{sibling_binary}\n",
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(module.os, "name", "nt")
    assert module._unload_windivert_driver_under(root, runner=runner) is False
    assert calls == [["sc", "qc", "WinDivert"]]
