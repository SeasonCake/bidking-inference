from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(
    os.environ.get("BIDKING_HF3_TEST_ROOT", str(Path(__file__).resolve().parents[1]))
).resolve()


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("hf3_stable_export_input_overlay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    documents = tmp_path / "Redirected Documents 文档"
    documents.mkdir(parents=True)
    live = tmp_path / "LocalAppData" / "BidKingLive" / "logs" / "live"
    live.mkdir(parents=True)
    return documents, live, live / "latest_snapshot.json"


def _snapshot(path: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "session_id": "stable-input:test",
        "hero": "ahmed",
        "round": 2,
        "phase": "bidding",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _export(module, documents: Path, live: Path, snapshot_path: Path, snapshot, **kwargs):
    return _call_write(
        module,
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        current_summary={"status": "ok", "context": {"session_id": "stable-input:test"}},
        diagnostic_profile="public-safe",
        _documents_root_for_test=documents,
        _live_root_for_test=live,
        **kwargs,
    )


def _call_write(module, **kwargs):
    parameters = inspect.signature(module._write_diagnostic_export).parameters
    compatible = {
        key: value
        for key, value in kwargs.items()
        if key in parameters
    }
    return module._write_diagnostic_export(**compatible)


def _all_zip_bytes(documents: Path) -> bytes:
    payload = bytearray()
    for archive_path in documents.rglob("*.zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                payload.extend(archive.read(name))
    return bytes(payload)


def test_localappdata_known_folder_binds_only_the_exact_live_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    local_appdata = tmp_path / "LocalAppData 空格"
    live = local_appdata / "BidKingLive" / "logs" / "live"
    live.mkdir(parents=True)
    snapshot_path = live / "latest_snapshot.json"
    monkeypatch.setattr(
        module,
        "_windows_local_appdata_known_folder",
        lambda: local_appdata,
    )

    assert module._validated_external_live_root(snapshot_path) == live.resolve()
    prefix_sibling = live.parent / "live-foreign" / "latest_snapshot.json"
    prefix_sibling.parent.mkdir()
    with pytest.raises(module.DiagnosticExportPathError):
        module._validated_external_live_root(prefix_sibling)


def test_support_export_pins_normal_live_inputs_and_allows_no_snapshot(tmp_path: Path) -> None:
    module = _overlay_module()
    documents, live, snapshot_path = _roots(tmp_path)
    snapshot = _snapshot(snapshot_path)
    (live / "capture_source_status.json").write_text(
        '{"source":"wss_local","accepted_frames":17}',
        encoding="utf-8",
    )
    (live / "monitor.stdout.log").write_text(
        "[status] accepted_frames=17\n",
        encoding="utf-8",
    )

    with (live / "monitor.stdout.log").open("ab") as active_monitor_writer:
        active_monitor_writer.flush()
        exported = _export(module, documents, live, snapshot_path, snapshot)
    with zipfile.ZipFile(exported) as archive:
        assert "latest_snapshot.json" in archive.namelist()
        assert "capture_source_status.json" in archive.namelist()
        manifest = json.loads(archive.read("BUILD_EXPORT_MANIFEST.json"))
    assert manifest["capture_health"]["capture_source_status"]["accepted_frames"] == 17
    assert manifest["capture_health"]["last_status_line"] == "[status] accepted_frames=17"

    snapshot_path.unlink()
    no_snapshot = {
        "schema_version": None,
        "session_id": "no_snapshot",
        "phase": "missing_snapshot",
    }
    exported_without_snapshot = _export(
        module,
        documents,
        live,
        snapshot_path,
        no_snapshot,
    )
    with zipfile.ZipFile(exported_without_snapshot) as archive:
        assert "latest_snapshot.json" not in archive.namelist()
        assert "capture_source_status.json" in archive.namelist()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path/handle timestamp contract")
def test_normal_single_link_export_is_stable_across_thirty_repetitions(tmp_path: Path) -> None:
    module = _overlay_module()
    documents, live, snapshot_path = _roots(tmp_path)
    snapshot = _snapshot(snapshot_path)
    status = live / "capture_source_status.json"
    status.write_text('{"source":"wss_local","accepted_frames":17}', encoding="utf-8")

    exported: list[Path] = []
    for _index in range(30):
        exported.append(_export(module, documents, live, snapshot_path, snapshot))

    assert len({path.name for path in exported}) == 30
    assert all(path.is_file() and path.stat().st_nlink == 1 for path in exported)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path/handle timestamp contract")
def test_cross_interface_timestamp_skew_keeps_same_identity_input_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()
    _documents, live, snapshot_path = _roots(tmp_path)
    _snapshot(snapshot_path)
    status = live / "capture_source_status.json"
    expected = b'{"source":"wss_local","accepted_frames":17}'
    status.write_bytes(expected)
    real_validate = module._validate_live_input_path

    def path_stat_with_skew(root: Path, path: Path):
        resolved, info = real_validate(root, path)
        return resolved, SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_nlink=info.st_nlink,
            st_mtime=info.st_mtime,
            st_mtime_ns=info.st_mtime_ns,
            st_ctime=info.st_ctime + 0.001,
            st_ctime_ns=info.st_ctime_ns + 1_000_000,
        )

    monkeypatch.setattr(module, "_validate_live_input_path", path_stat_with_skew)
    actual, identity = module._read_stable_live_input(live, status)

    assert actual == expected
    assert identity == (status.stat().st_dev, status.stat().st_ino)


def test_support_export_rejects_foreign_hardlink_without_publishing_canary(
    tmp_path: Path,
) -> None:
    module = _overlay_module()
    documents, live, snapshot_path = _roots(tmp_path)
    snapshot = _snapshot(snapshot_path)
    foreign_canary = b'AUDIT_FOREIGN_HARDLINK_CANARY_8D3F'
    foreign = tmp_path / "foreign-private-status.json"
    foreign.write_bytes(b'{"unknown":"' + foreign_canary + b'"}')
    os.link(foreign, live / "capture_source_status.json")
    assert foreign.stat().st_nlink == 2

    with pytest.raises(module.DiagnosticExportPathError):
        _export(module, documents, live, snapshot_path, snapshot)

    assert foreign_canary not in _all_zip_bytes(documents)


@pytest.mark.parametrize("shape", ["outside-snapshot", "prefix-sibling", "outside-raw"])
def test_live_input_authority_rejects_outside_and_prefix_paths(
    tmp_path: Path,
    shape: str,
) -> None:
    module = _overlay_module()
    documents, live, snapshot_path = _roots(tmp_path)
    snapshot = _snapshot(snapshot_path)
    kwargs: dict[str, object] = {}
    if shape == "outside-snapshot":
        outside = tmp_path / "outside" / "latest_snapshot.json"
        outside.parent.mkdir()
        snapshot = _snapshot(outside)
        snapshot_path = outside
    elif shape == "prefix-sibling":
        outside = live.parent / "live-foreign" / "latest_snapshot.json"
        outside.parent.mkdir()
        snapshot = _snapshot(outside)
        snapshot_path = outside
    else:
        outside_raw = tmp_path / "foreign-private.jsonl"
        outside_raw.write_text('{"private":"OUTSIDE_RAW_CANARY"}\n', encoding="utf-8")
        snapshot["raw_capture_jsonl"] = str(outside_raw)
        kwargs.update(raw_forensics=True, diagnostic_profile="engineering")

    with pytest.raises(module.DiagnosticExportPathError):
        _call_write(
            module,
            snapshot=snapshot,
            snapshot_path=snapshot_path,
            _documents_root_for_test=documents,
            _live_root_for_test=live,
            **kwargs,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reparse/ADS contract")
def test_live_input_authority_rejects_symlink_junction_and_ads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _overlay_module()

    documents, live, snapshot_path = _roots(tmp_path / "symlink")
    snapshot = _snapshot(snapshot_path)
    foreign = tmp_path / "foreign-status.json"
    foreign.write_text('{"private":"SYMLINK_CANARY"}', encoding="utf-8")
    status_path = live / "capture_source_status.json"
    try:
        status_path.symlink_to(foreign)
    except OSError:
        status_path.write_text('{"source":"wss_local"}', encoding="utf-8")
        real_reparse = module._is_reparse_point
        monkeypatch.setattr(
            module,
            "_is_reparse_point",
            lambda path: Path(path) == status_path or real_reparse(Path(path)),
        )
    with pytest.raises(module.DiagnosticExportPathError):
        _export(module, documents, live, snapshot_path, snapshot)
    if "real_reparse" in locals():
        monkeypatch.setattr(module, "_is_reparse_point", real_reparse)

    documents, live, snapshot_path = _roots(tmp_path / "junction")
    snapshot = _snapshot(snapshot_path)
    outside_raw = tmp_path / "outside-raw"
    outside_raw.mkdir()
    raw_path = outside_raw / "private.jsonl"
    raw_path.write_text('{"private":"JUNCTION_CANARY"}\n', encoding="utf-8")
    junction = live / "raw"
    made = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside_raw)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    snapshot["raw_capture_jsonl"] = str(junction / "private.jsonl")
    try:
        with pytest.raises(module.DiagnosticExportPathError):
            _call_write(
                module,
                snapshot=snapshot,
                snapshot_path=snapshot_path,
                diagnostic_profile="engineering",
                raw_forensics=True,
                _documents_root_for_test=documents,
                _live_root_for_test=live,
            )
    finally:
        os.rmdir(junction)

    documents, live, snapshot_path = _roots(tmp_path / "nested-junction")
    snapshot = _snapshot(snapshot_path)
    archive_dir = live / "raw" / "archive"
    archive_dir.mkdir(parents=True)
    nested_outside = tmp_path / "nested-outside"
    nested_outside.mkdir()
    (nested_outside / "private.json").write_text(
        '{"private":"NESTED_JUNCTION_CANARY"}',
        encoding="utf-8",
    )
    nested_junction = archive_dir / "escape"
    made = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(nested_junction), str(nested_outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    try:
        with pytest.raises(module.DiagnosticExportPathError):
            module._write_diagnostic_export(
                snapshot=snapshot,
                snapshot_path=snapshot_path,
                diagnostic_profile="engineering",
                raw_forensics=True,
                _documents_root_for_test=documents,
                _live_root_for_test=live,
            )
    finally:
        os.rmdir(nested_junction)

    documents, live, snapshot_path = _roots(tmp_path / "ads")
    base = live / "latest_snapshot.json"
    base.write_text("base", encoding="utf-8")
    ads_path = Path(str(base) + ":private")
    snapshot = _snapshot(ads_path)
    with pytest.raises(module.DiagnosticExportPathError):
        _export(module, documents, live, ads_path, snapshot)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle sharing contract")
@pytest.mark.parametrize("race", ["rename-replace", "bitflip"])
def test_held_input_handle_rejects_concurrent_replacement_or_write(
    tmp_path: Path,
    race: str,
) -> None:
    module = _overlay_module()
    documents, live, snapshot_path = _roots(tmp_path)
    snapshot = _snapshot(snapshot_path)
    status = live / "capture_source_status.json"
    original = b'{"source":"wss_local","accepted_frames":17}'
    status.write_bytes(original)
    replacement = live / "replacement.tmp"
    replacement.write_bytes(b'{"source":"foreign","accepted_frames":99}')
    mutation_attempted = False

    def mutate(path: Path) -> None:
        nonlocal mutation_attempted
        if path.name != status.name or mutation_attempted:
            return
        mutation_attempted = True
        if race == "rename-replace":
            os.replace(replacement, path)
        else:
            with path.open("r+b") as stream:
                stream.write(b"X" * len(original))

    with pytest.raises(module.DiagnosticExportPathError):
        _export(
            module,
            documents,
            live,
            snapshot_path,
            snapshot,
            _after_input_read_for_test=mutate,
        )
    assert mutation_attempted is True
    assert b"accepted_frames\":99" not in _all_zip_bytes(documents)
    assert b"X" * len(original) not in _all_zip_bytes(documents)
