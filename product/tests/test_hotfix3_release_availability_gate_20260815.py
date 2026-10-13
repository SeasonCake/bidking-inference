from __future__ import annotations

import ast
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
CHECKER = ROOT / "scripts" / "check_hotfix3_real_drop_pool_quote_runtime.py"
CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
RUNTIME_PROBE = ROOT / "tests" / "client_protection_runtime_probe.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "hotfix3_release_availability_checker_test", CHECKER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formal_build_requires_external_exact_captures_before_stage_mutation() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    first_mutation = build.index("# --- 1. 清理 + 目录 ---")
    preflight = build[:first_mutation]

    assert "[string]$ReleaseCapture2536Path" in preflight
    assert "[string]$ReleaseCapture2408Path" in preflight
    assert "[string]$ReleaseCapture2409Path" in preflight
    assert "[string]$ReleaseCapture4501Path" in preflight
    assert "[string]$ReleaseCapture4538Path" in preflight
    assert "release_capture_2536" in preflight
    assert "release_capture_2408" in preflight
    assert "release_capture_2409" in preflight
    assert "release_capture_4501" in preflight
    assert "release_capture_4538" in preflight
    assert "capture_identity_mismatch" in preflight
    assert "capture_path" not in preflight.casefold()
    assert "Start-ReleaseCaptureLeaseHolder" in preflight
    assert "--hold-captures" in preflight
    assert "ReadLineAsync" in preflight
    assert "number_of_links" in preflight
    assert "file_index" in preflight
    assert "Get-FileHash -LiteralPath $captureFullPath" not in preflight
    assert build.index("Start-ReleaseCaptureLeaseHolder", build.index("if ($InspectOnly)")) < first_mutation
    assert "BIDKING_RELEASE_CAPTURE_IDENTITY_RECEIPT" in build
    assert build.count("Invoke-ReleaseAvailabilityGate") >= 3
    assert "finally {\n    Stop-ReleaseCaptureLeaseHolder" in build


def test_builder_abi_smokes_use_current_authority_without_rebind() -> None:
    build = BUILD.read_text(encoding="utf-8-sig")
    runtime_probe = RUNTIME_PROBE.read_text(encoding="utf-8")
    helper_start = build.index("function New-RuntimeFixtureGateCode")
    helper_end = build.index("$prevEAP2 =", helper_start)
    helper = build[helper_start:helper_end]

    assert "CURRENT_AUTHORITY_ABI_SMOKE_OK" in helper
    for forbidden in (
        "approved_authority_pairs",
        "authority_map_id",
        "gate_maps",
        "dataclasses.replace",
        "original incomplete Drop authority did not fail closed",
        "flatten_pool(authority_map.drop_pool_id",
    ):
        assert forbidden not in helper
    fixture_parse = helper.index("parsed = parse_fatbeans_capture_payload(payload)")
    offline_import = helper.index("from dataclasses import replace")
    offline_mode = helper.index("tables = replace(")
    table_load = helper.index("load_monitor_tables()", offline_mode)
    offline_flag = helper.index(
        "warehouse_live_identity_check=False", table_load
    )
    assert offline_import < offline_mode < table_load < offline_flag < fixture_parse
    assert helper.count("warehouse_live_identity_check=False") == 1
    assert "expected_status = 'sampleable'" not in helper
    assert "expected_status = '$([string]$approvedSpec.ExpectedStatus)'" in helper
    assert '$approvedGateSpecs.ContainsKey($mapId)' in helper
    assert 'throw "运行时 gate fixture 当前权威合同未批准: $mapId"' in helper
    assert "assert expected_status == 'sampleable'" in helper
    assert "assert support is None" in helper
    assert "degraded_sampleable" not in helper
    assert "require_current_fixture_authority(tables, expected_map_id" in helper
    assert "if expected_map_id == 2508:" in helper
    assert "assert summary.get('drop_pool_support') == support" in helper
    assert "assert summary.get('diagnostics', {}).get('drop_pool_support') == support" in helper
    assert "flatten_pool(1066, tables.drops, tables.items)" in helper
    assert "unauthorized root and leaf did not fail closed" in helper
    assert "RUNTIME_GATE_OK" not in helper
    assert "RELEASE_AVAILABILITY_OK" in build
    assert build.index("RELEASE_AVAILABILITY_OK") > helper_end
    assert "CLIENT_PROTECTION_RUNTIME_OK" in runtime_probe
    assert "CURRENT_AUTHORITY_ABI_SMOKE_OK" not in runtime_probe
    assert "approved_authority_pairs" not in runtime_probe
    assert '"authority_map_id"' not in runtime_probe
    assert '"expected_status": "sampleable"' in runtime_probe
    assert '"expected_status": "degraded_sampleable"' not in runtime_probe
    assert 'if spec["map_id"] == 2508:' in runtime_probe
    assert "require_approved_runtime_fixture_spec(spec)" in runtime_probe
    assert "flatten_pool(1066, tables.drops, tables.items)" in runtime_probe


def test_release_checker_loads_only_explicit_package_root_modules() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert 'parser.add_argument("--package-root", type=Path)' in source
    assert 'parser.add_argument("--hold-captures", action="store_true")' in source
    assert "ROOT / \"src\"" not in source
    assert "ROOT / \"hero_ref\" / \"tools\"" not in source
    assert "_assert_module_within_package" in source
    assert "_assert_native_product_module" in source
    assert "require_native: bool = True" in source
    assert "accepted_scheduled_snapshot" in source
    assert "current_authority_2409" in source
    assert "_check_current_ranked_authority" in source
    assert 'ranked_map_ids = set(range(5601, 5612))' in source
    assert '"map_rebound": False' in source
    assert '"pool_rebound": False' in source
    assert "policy_census" in source
    assert "_StableCaptureLease.open" in source
    assert "_parse_capture_identity_receipt" in source
    assert "_FILE_SHARE_READ" in source
    assert "_FILE_FLAG_OPEN_REPARSE_POINT" in source
    assert "number_of_links" in source
    assert "file_index" in source
    assert "rebind" not in source.casefold()
    package_check = source[
        source.index("def _check_package_with_leases(") : source.index(
            "def check_package(", source.index("def _check_package_with_leases(")
        )
    ]
    offline_mode = package_check.index("tables = dataclasses.replace(")
    table_load = package_check.index("monitor.load_monitor_tables(", offline_mode)
    offline_flag = package_check.index(
        "warehouse_live_identity_check=False", table_load
    )
    capture_loop = package_check.index("for lease, contract in zip(")
    assert offline_mode < table_load < offline_flag < capture_loop
    assert package_check.count("warehouse_live_identity_check=False") == 1
    assert any(isinstance(node, ast.FunctionDef) and node.name == "check_package" for node in tree.body)


def test_release_checker_historical_captures_use_isolated_offline_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checker = _load_checker()

    @dataclass(frozen=True)
    class Tables:
        warehouse_live_identity_check: bool
        marker: str

    loaded_tables = Tables(warehouse_live_identity_check=True, marker="package")
    monitor = type(
        "MonitorModule",
        (),
        {"load_monitor_tables": staticmethod(lambda **_kwargs: loaded_tables)},
    )()
    modules = {
        "bidking_lab.live.monitor": monitor,
        "bidking_lab.simulation.basic_mc": object(),
        "ahmad_ref_engine": object(),
        "ahmad_live_panel_server": object(),
    }
    observed_tables: list[Tables] = []
    contract = {"bytes": 2, "sha256": "1" * 64, "prefix_rows": 1}
    lease = type(
        "Lease",
        (),
        {"raw": b"[]", "assert_unchanged": lambda self: None},
    )()

    monkeypatch.setattr(checker, "_CAPTURE_CONTRACTS", (contract,))
    monkeypatch.setattr(checker, "_activate_package", lambda _root: tmp_path)
    monkeypatch.setattr(
        checker.importlib,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(checker, "_load_exact_script", lambda *_args: object())
    monkeypatch.setattr(checker, "_assert_module_within_package", lambda *_args: None)
    monkeypatch.setattr(checker, "_parse_capture_raw", lambda *_args: [{}])
    monkeypatch.setattr(
        checker,
        "_check_monitor_capture",
        lambda **kwargs: observed_tables.append(kwargs["tables"]),
    )
    monkeypatch.setattr(
        checker,
        "_policy_census",
        lambda *_args: checker.EXPECTED_POLICY_CENSUS,
    )
    monkeypatch.setattr(
        checker,
        "_check_current_authority_2409",
        lambda **kwargs: observed_tables.append(kwargs["tables"]),
    )
    monkeypatch.setattr(
        checker,
        "_check_current_ranked_authority",
        lambda **kwargs: observed_tables.append(kwargs["tables"]),
    )
    monkeypatch.setattr(checker, "_package_identity", lambda *_args: "a" * 64)

    receipt = checker._check_package_with_leases(  # noqa: SLF001
        package_root=tmp_path,
        leases=(lease,),
        require_native=False,
    )

    assert receipt["status"] == "pass"
    assert loaded_tables.warehouse_live_identity_check is True
    assert len(observed_tables) == 3
    assert all(table.marker == "package" for table in observed_tables)
    assert all(table.warehouse_live_identity_check is False for table in observed_tables)


def test_release_checker_canonical_2409_input_has_no_private_identity() -> None:
    checker = _load_checker()
    canonical = checker._canonical_2409_snapshot()  # noqa: SLF001
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert hashlib.sha256(serialized).hexdigest() == checker.CURRENT_2409_INPUT_SHA256
    rendered = serialized.decode("utf-8")
    for forbidden in ("session_id", "player", "item_id", "raw_capture", "path"):
        assert forbidden not in rendered


def test_release_checker_proves_all_current_ranked_original_routes_to_ui() -> None:
    from bidking_lab.live import monitor
    from bidking_lab.simulation import basic_mc

    checker = _load_checker()
    panel = checker._load_exact_script(  # noqa: SLF001
        "hotfix4_rankmap_availability_panel",
        ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py",
        ROOT,
    )
    engine = checker._load_exact_script(  # noqa: SLF001
        "hotfix4_rankmap_availability_engine",
        ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py",
        ROOT,
    )
    tables = monitor.load_monitor_tables(
        tables_dir=ROOT / "data" / "raw" / "tables"
    )
    # This source tree now selects the exact 76b7ca physical-map model.  The
    # former b049 runtime-open contract remains tested separately as legacy.
    assert tables.rankmap_distribution_receipt["model_distribution_authority"] is True
    assert tables.rankmap_distribution_receipt["runtime_open_claim"] is False
    assert tables.rankmap_distribution_receipt["scope_map_count"] == 112

    assert checker._policy_census(  # noqa: SLF001
        tables,
        monitor,
        engine,
        basic_mc,
    ) == checker.CURRENT_MODEL_CENSUS
    checker._check_current_ranked_authority(  # noqa: SLF001
        tables=tables,
        monitor=monitor,
        panel=panel,
        drop_engine=basic_mc,
    )
    assert len(checker._CAPTURE_CONTRACTS) == 5  # noqa: SLF001
    for map_id in range(5601, 5612):
        snapshot = checker._canonical_current_ranked_snapshot(map_id)  # noqa: SLF001
        assert snapshot["map_id"] == map_id
        assert snapshot["model_map_id"] == map_id
        assert snapshot["map_alias"] == {}
        assert snapshot["map_alias_mode"] == ""
        rendered = json.dumps(snapshot, ensure_ascii=False)
        for forbidden in ("session_id", "player", "raw_capture", "path"):
            assert forbidden not in rendered


@pytest.mark.parametrize(
    ("field", "retired_value"),
    (
        ("ranked_5606", "model_scope_only"),
        ("runtime_open_claim", True),
    ),
)
def test_release_checker_rejects_retired_ranked_runtime_claims(
    field: str,
    retired_value: object,
) -> None:
    from bidking_lab.live import monitor
    from bidking_lab.simulation import basic_mc

    checker = _load_checker()
    panel = checker._load_exact_script(  # noqa: SLF001
        "hotfix4_rankmap_availability_panel_retired_claim",
        ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py",
        ROOT,
    )
    tables = monitor.load_monitor_tables(
        tables_dir=ROOT / "data" / "raw" / "tables"
    )
    receipt = {**tables.rankmap_distribution_receipt, field: retired_value}

    with pytest.raises(AssertionError, match="current_model_authority_contract_failed"):
        checker._check_current_ranked_authority(  # noqa: SLF001
            tables=replace(tables, rankmap_distribution_receipt=receipt),
            monitor=monitor,
            panel=panel,
            drop_engine=basic_mc,
        )


def test_release_checker_capture_identity_fails_closed_for_missing_wrong_and_swap(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(b"[{}]")
    second.write_bytes(b"[{},{}]")
    first_contract = {
        "bytes": first.stat().st_size,
        "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
        "prefix_rows": 1,
    }
    second_contract = {
        "bytes": second.stat().st_size,
        "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
        "prefix_rows": 1,
    }

    assert checker._read_exact_capture(first, first_contract) == [{}]  # noqa: SLF001
    with pytest.raises(FileNotFoundError):
        checker._read_exact_capture(tmp_path / "missing.json", first_contract)  # noqa: SLF001
    first.write_bytes(b"[{\"wrong\":true}]")
    with pytest.raises(ValueError, match="capture_identity_mismatch"):
        checker._read_exact_capture(first, first_contract)  # noqa: SLF001
    with pytest.raises(ValueError, match="capture_identity_mismatch"):
        checker._read_exact_capture(second, first_contract)  # noqa: SLF001
    with pytest.raises(ValueError, match="capture_identity_mismatch"):
        checker._read_exact_capture(first, second_contract)  # noqa: SLF001


def _capture_contract(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "prefix_rows": 1,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows stable handle contract")
def test_stable_capture_lease_blocks_write_rename_replace_and_rebinds_identity(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    capture = tmp_path / "capture.json"
    capture.write_bytes(b"[{\"value\":1}]")
    contract = _capture_contract(capture)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(capture.read_bytes())

    with checker._StableCaptureLease.open(capture, contract) as lease:  # noqa: SLF001
        receipt = lease.identity_receipt()
        assert receipt["number_of_links"] == 1
        assert receipt["bytes"] == contract["bytes"]
        assert receipt["sha256"] == contract["sha256"]
        assert "path" not in receipt
        with pytest.raises(OSError):
            capture.write_bytes(b"[{\"value\":2}]")
        with pytest.raises(OSError):
            capture.rename(tmp_path / "renamed.json")
        with pytest.raises(OSError):
            os.replace(replacement, capture)
        lease.assert_unchanged()

    original = tmp_path / "original.json"
    capture.rename(original)
    capture.write_bytes(original.read_bytes())
    with pytest.raises(ValueError, match="capture_stable_identity_mismatch"):
        checker._StableCaptureLease.open(capture, contract, receipt)  # noqa: SLF001

    capture.write_bytes(b"[{\"value\":9}]")
    assert capture.stat().st_size == contract["bytes"]
    with pytest.raises(ValueError, match="capture_identity_mismatch"):
        checker._StableCaptureLease.open(capture, contract)  # noqa: SLF001


@pytest.mark.skipif(os.name != "nt", reason="Windows stable handle contract")
def test_stable_capture_rejects_ancestor_junction_hardlink_and_path_escape(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    capture = target_dir / "capture.json"
    capture.write_bytes(b"[{}]")
    contract = _capture_contract(capture)

    hardlink = target_dir / "hardlink.json"
    os.link(capture, hardlink)
    with pytest.raises(ValueError, match="capture_identity_mismatch"):
        checker._StableCaptureLease.open(capture, contract)  # noqa: SLF001
    hardlink.unlink()

    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction unavailable")
    with pytest.raises(ValueError, match="capture_path_reparse"):
        checker._StableCaptureLease.open(junction / capture.name, contract)  # noqa: SLF001

    with pytest.raises(ValueError, match="capture_path_invalid"):
        checker._StableCaptureLease.open(Path("relative.json"), contract)  # noqa: SLF001
    nested = tmp_path / "nested"
    nested.mkdir()
    escaped = Path(str(nested) + "\\..\\target\\capture.json")
    with pytest.raises(ValueError, match="capture_path_invalid"):
        checker._StableCaptureLease.open(escaped, contract)  # noqa: SLF001


def test_capture_identity_receipt_is_strict_support_safe_and_ordered() -> None:
    checker = _load_checker()
    captures = [
        {
            "bytes": contract["bytes"],
            "sha256": contract["sha256"],
            "volume_serial_number": 1,
            "file_index": index + 10,
            "number_of_links": 1,
            "verdict": "pass",
        }
        for index, contract in enumerate(checker._CAPTURE_CONTRACTS)  # noqa: SLF001
    ]
    receipt = {
        "schema": checker.CAPTURE_IDENTITY_RECEIPT_SCHEMA,
        "receipt_version": 1,
        "status": "pass",
        "captures": captures,
    }
    raw = json.dumps(receipt, separators=(",", ":"))
    assert checker._parse_capture_identity_receipt(raw) == tuple(captures)  # noqa: SLF001
    assert "path" not in raw.casefold()
    assert "canary" not in raw.casefold()

    swapped = dict(receipt)
    swapped["captures"] = [captures[1], captures[0], *captures[2:]]
    with pytest.raises(ValueError, match="capture_identity_receipt_invalid"):
        checker._parse_capture_identity_receipt(  # noqa: SLF001
            json.dumps(swapped, separators=(",", ":"))
        )
    duplicate = raw.replace('"status":"pass"', '"status":"pass","status":"pass"')
    with pytest.raises(ValueError, match="capture_identity_receipt_invalid"):
        checker._parse_capture_identity_receipt(duplicate)  # noqa: SLF001


def test_release_contract_freezes_census_routes_and_exact_bat_bytes() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    availability = contract["release_availability"]
    wrappers = contract["launcher_wrappers"]

    assert contract["contract_version"] >= 16
    assert availability["policy_census"] == {
        "policy_roots": 112,
        "direct_root": {
            "complete": 112,
            "degraded": 0,
            "sampleable": 112,
            "missing": 0,
            "empty": 0,
            "unsupported": 0,
            "unsupported_ids_sha256": (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
        },
        "source_map": {
            "complete": 112,
            "degraded": 0,
            "sampleable": 112,
            "missing": 0,
            "empty": 0,
            "unsupported": 0,
            "unsupported_ids_sha256": (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
        },
    }
    assert availability["route_scope"]["changed_route_count"] == 16
    assert availability["route_scope"]["new_only_count"] == 42
    assert availability["route_scope"]["legacy_only_count"] == 8
    assert availability["route_scope"]["current_activity_alias_sampleable_count"] == 20
    assert availability["route_scope"]["ranked_production_unsupported_count"] == 0
    assert availability["route_scope"][
        "ranked_production_unsupported_ids_sha256"
    ] == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    assert availability["current_authority"]["map_id"] == 2409

    expected = (
        b"@echo off\r\n"
        b"setlocal\r\n"
        b'if not exist "%~dp0..\\BidKingLive.exe" exit /b 1\r\n'
        b'if exist "%~dp0..\\BidKingLive.exe\\*" exit /b 1\r\n'
        b'start "" "%~dp0..\\BidKingLive.exe"\r\n'
        b"exit /b %errorlevel%\r\n"
    )
    assert wrappers == {
        "members": ["其他启动方式/启动.bat", "其他启动方式/start.bat"],
        "target": "BidKingLive.exe",
        "encoding": "ascii",
        "line_endings": "crlf",
        "sha256": hashlib.sha256(expected).hexdigest(),
    }
    assert all(
        member in contract["required_members"]["common"]
        for member in wrappers["members"]
    )
    assert all(
        contract["fixed_member_sha256"][member] == wrappers["sha256"]
        for member in wrappers["members"]
    )
