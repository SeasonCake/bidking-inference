from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_wss_addon_bootstrap import _BOOTSTRAP  # noqa: E402

MANIFEST_PATH = ROOT / "scripts" / "client_protection_manifest.json"
AUDITOR_PATH = ROOT / "scripts" / "audit_release_zip.py"
AUTHORITY_LOADER_SOURCE = "src/bidking_lab/data/item_category_authority.py"
CURRENT_RANKMAP_PROTECTED_SOURCES = {
    "src/bidking_lab/extract/current_rankmap_package_contract.py",
    "src/bidking_lab/live/current_rankmap_runtime.py",
}
CATALOG_VALUE_PROTECTED_SOURCE = "src/bidking_lab/live/catalog_value.py"
_SPEC = importlib.util.spec_from_file_location("audit_release_zip_protection", AUDITOR_PATH)
assert _SPEC and _SPEC.loader
_AUDIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUDIT)


def _native_member(source: str) -> str:
    return f"{source[:-3]}.cp313-win_amd64.pyd"


def _protected_members() -> dict[str, bytes]:
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    critical = manifest["critical_native"]
    wss = manifest["wss_operational_abi"]
    loader_bytes = b"native"
    members: dict[str, bytes] = {
        "BUILD_MANIFEST.txt": (
            "Encrypted: True\n"
            "ProductProfile: standard\n"
            f"ClientProtectionSchema: {manifest['schema']}\n"
            "ClientProtectionManifestSHA256: "
            f"{hashlib.sha256(manifest_bytes).hexdigest()}\n"
            f"WssOperationalABI: {wss['abi_version']}\n"
            f"WssOperationalGeneration: {wss['generation_sha256']}\n"
            f"WssAddonBootstrapSHA256: {wss['bootstrap_sha256']}\n"
            "WssAddonLoaderSHA256: "
            f"{hashlib.sha256(loader_bytes).hexdigest()}\n"
        ).encode(),
        critical["overlay"]["source"]: (
            "import multiprocessing\n"
            f"from {critical['overlay']['native_module']} import main\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    multiprocessing.freeze_support()\n"
            "    raise SystemExit(main())\n"
        ).encode(),
        (
            "hero_ref/tools/"
            f"{critical['overlay']['native_module']}.cp313-win_amd64.pyd"
        ): b"native",
        "scripts/local_compat_addon.py": _BOOTSTRAP.encode("utf-8"),
        "scripts/_local_compat_addon_loader.pyd": loader_bytes,
    }
    for module in critical["engine_modules"]:
        members[f"hero_ref/src/{module}.cp313-win_amd64.pyd"] = b"native"
    for module in critical["tool_modules"]:
        members[f"hero_ref/tools/{module}.cp313-win_amd64.pyd"] = b"native"
    for source in critical["package_modules"]:
        members[f"src/{_native_member(source)}"] = b"native"
    for tree in critical["package_trees"]:
        for relative in tree["protected_modules"]:
            members[f"src/{_native_member(relative)}"] = b"native"
    for source in manifest["packaged_plaintext_python_allowlist"]:
        members.setdefault(source, b"# allowed package glue\n")
    return members


def _declared_source_contract(manifest: dict[str, object]) -> set[str]:
    critical = manifest["critical_native"]
    return {
        *(f"hero_ref/src/{item}.py" for item in critical["engine_modules"]),
        *(f"hero_ref/tools/{item}.py" for item in critical["tool_modules"]),
        *(f"src/{item}" for item in critical["package_modules"]),
        *(
            f"src/{item}"
            for tree in critical["package_trees"]
            for item in tree["protected_modules"]
        ),
        *manifest["plaintext_allowlist"],
        *manifest["encrypted_stage_excludes"],
        *manifest["build_only_source_authorities"],
        critical["overlay"]["source"],
    }


def _actual_protected_source_inventory(manifest: dict[str, object]) -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for raw_root in manifest["source_roots"]
        for path in (ROOT / raw_root).rglob("*.py")
    }


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _issues(tmp_path: Path, members: dict[str, bytes]) -> list[str]:
    path = tmp_path / "candidate.zip"
    _write_zip(path, members)
    return _AUDIT.audit_zip(path, protection_manifest=MANIFEST_PATH)


def test_package_tree_manifest_classifies_every_python_source() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    plaintext = set(manifest["plaintext_allowlist"])
    build_only = set(manifest["build_only_source_authorities"])
    for tree in manifest["critical_native"]["package_trees"]:
        source_root = ROOT / "src" / tree["path"]
        discovered = {
            path.relative_to(ROOT).as_posix()
            for path in source_root.rglob("*.py")
        }
        protected = {
            f"src/{relative}"
            for relative in tree["protected_modules"]
        }
        classified = protected | {
            relative
            for relative in plaintext
            if relative.startswith(f"src/{tree['path']}/")
        } | {
            relative
            for relative in build_only
            if relative.startswith(f"src/{tree['path']}/")
        }
        assert discovered == classified, {
            "tree": tree["path"],
            "unclassified": sorted(discovered - classified),
            "missing": sorted(classified - discovered),
        }


def test_encrypted_candidate_with_complete_native_boundary_passes(tmp_path: Path) -> None:
    assert _issues(tmp_path, _protected_members()) == []


def test_wss_operational_members_are_manifest_bound_and_fail_closed(
    tmp_path: Path,
) -> None:
    bootstrap_tampered = _protected_members()
    bootstrap_tampered["scripts/local_compat_addon.py"] += b"# tampered\n"
    assert "wss_operational_bootstrap_invalid" in _issues(
        tmp_path, bootstrap_tampered
    )

    loader_tampered = _protected_members()
    loader_tampered["scripts/_local_compat_addon_loader.pyd"] += b"tampered"
    assert "wss_operational_loader_identity_mismatch" in _issues(
        tmp_path, loader_tampered
    )

    manifest_tampered = _protected_members()
    manifest_tampered["BUILD_MANIFEST.txt"] = manifest_tampered[
        "BUILD_MANIFEST.txt"
    ].replace(b"WssOperationalABI:", b"WssOperationalABI2:")
    assert "wss_operational_manifest_mismatch" in _issues(
        tmp_path, manifest_tampered
    )


def test_real_source_stage_native_protects_authority_loader(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    critical = manifest["critical_native"]
    declared = _declared_source_contract(manifest)
    actual = _actual_protected_source_inventory(manifest)

    assert actual == declared
    assert "bidking_lab/data/item_category_authority.py" in critical["package_modules"]
    assert AUTHORITY_LOADER_SOURCE not in manifest["plaintext_allowlist"]
    assert AUTHORITY_LOADER_SOURCE not in manifest["packaged_plaintext_python_allowlist"]

    stage_members = _protected_members()
    authority_native = f"src/{_native_member('bidking_lab/data/item_category_authority.py')}"
    assert authority_native in stage_members
    assert AUTHORITY_LOADER_SOURCE not in stage_members
    assert _issues(tmp_path, stage_members) == []

    stage_members[AUTHORITY_LOADER_SOURCE] = (ROOT / AUTHORITY_LOADER_SOURCE).read_bytes()
    issues = _issues(tmp_path, stage_members)
    assert "plaintext_sensitive_module" in issues


def test_current_rankmap_runtime_chain_is_native_only_and_exactly_classified(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    critical = manifest["critical_native"]
    declared = _declared_source_contract(manifest)
    actual = _actual_protected_source_inventory(manifest)
    protected = {
        f"src/{relative}"
        for tree in critical["package_trees"]
        for relative in tree["protected_modules"]
    }

    assert manifest["manifest_version"] == 9
    assert actual == declared
    assert CURRENT_RANKMAP_PROTECTED_SOURCES <= protected
    assert CURRENT_RANKMAP_PROTECTED_SOURCES.isdisjoint(
        manifest["plaintext_allowlist"]
    )
    assert CURRENT_RANKMAP_PROTECTED_SOURCES.isdisjoint(
        manifest["packaged_plaintext_python_allowlist"]
    )
    assert CURRENT_RANKMAP_PROTECTED_SOURCES.isdisjoint(
        manifest["build_only_source_authorities"]
    )

    members = _protected_members()
    for source in CURRENT_RANKMAP_PROTECTED_SOURCES:
        native = f"src/{_native_member(source.removeprefix('src/'))}"
        assert native in members
        assert source not in members

        missing = dict(members)
        missing.pop(native)
        assert "protected_native_module_missing" in _issues(tmp_path, missing)

        near_abi = dict(members)
        near_abi.pop(native)
        near_abi[native.replace("cp313", "cp312")] = b"native"
        issues = _issues(tmp_path, near_abi)
        assert "protected_native_module_missing" in issues
        assert "protected_native_module_untrusted" in issues


def test_catalog_value_runtime_is_native_only_and_exactly_classified(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    critical = manifest["critical_native"]
    protected = {
        f"src/{relative}"
        for tree in critical["package_trees"]
        for relative in tree["protected_modules"]
    }

    assert CATALOG_VALUE_PROTECTED_SOURCE in protected
    assert CATALOG_VALUE_PROTECTED_SOURCE not in manifest["plaintext_allowlist"]
    assert CATALOG_VALUE_PROTECTED_SOURCE not in manifest[
        "packaged_plaintext_python_allowlist"
    ]
    assert CATALOG_VALUE_PROTECTED_SOURCE not in manifest[
        "build_only_source_authorities"
    ]

    members = _protected_members()
    native = f"src/{_native_member(CATALOG_VALUE_PROTECTED_SOURCE.removeprefix('src/'))}"
    assert native in members
    assert CATALOG_VALUE_PROTECTED_SOURCE not in members

    missing = dict(members)
    missing.pop(native)
    assert "protected_native_module_missing" in _issues(tmp_path, missing)

    near_abi = dict(members)
    near_abi.pop(native)
    near_abi[native.replace("cp313", "cp312")] = b"native"
    issues = _issues(tmp_path, near_abi)
    assert "protected_native_module_missing" in issues
    assert "protected_native_module_untrusted" in issues


def test_previous_manifest_leaves_real_authority_loader_plaintext(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    previous = json.loads(json.dumps(manifest))
    previous["manifest_version"] = manifest["manifest_version"] - 1
    previous["critical_native"]["package_modules"].remove(
        "bidking_lab/data/item_category_authority.py"
    )
    previous_path = tmp_path / "previous-client-protection.json"
    previous_bytes = json.dumps(previous, ensure_ascii=False).encode("utf-8")
    previous_path.write_bytes(previous_bytes)

    stage_members = _protected_members()
    authority_native = f"src/{_native_member('bidking_lab/data/item_category_authority.py')}"
    stage_members.pop(authority_native)
    stage_members[AUTHORITY_LOADER_SOURCE] = (ROOT / AUTHORITY_LOADER_SOURCE).read_bytes()
    stage_members["BUILD_MANIFEST.txt"] = (
        "Encrypted: True\n"
        "ProductProfile: standard\n"
        f"ClientProtectionSchema: {previous['schema']}\n"
        "ClientProtectionManifestSHA256: "
        f"{hashlib.sha256(previous_bytes).hexdigest()}\n"
    ).encode()
    candidate = tmp_path / "previous-stage.zip"
    _write_zip(candidate, stage_members)

    issues = _AUDIT.audit_zip(candidate, protection_manifest=previous_path)
    assert "plaintext_sensitive_module" in issues


def test_runtime_probe_imports_native_authority_and_calls_bkenc2_loader() -> None:
    probe = (ROOT / "tests" / "client_protection_runtime_probe.py").read_text(
        encoding="utf-8"
    )
    assert '"bidking_lab.data.item_category_authority"' in probe
    assert "encrypt_compressed_bytes" in probe
    assert "load_item_category_authority_document" in probe


def test_runtime_probe_uses_current_rankmap_without_fixture_authority_replacement() -> None:
    probe = (ROOT / "tests" / "client_protection_runtime_probe.py").read_text(
        encoding="utf-8"
    )

    for expected in (
        '"map_id": 5604',
        '"original_pool_id": 2704',
        '"expected_status": "sampleable"',
        '"map_id": 2508',
        '"original_pool_id": 2508',
        '"package_generation": "76b7ca-2026-10-08-723", "model_scope_count": 112',
        '"support_status": "", "support_unresolved_probability_ppm": 0',
    ):
        assert expected in probe
    for forbidden in (
        '"authority_map_id"',
        "approved_authority_pairs",
        "gate_maps",
        "dataclasses.replace",
        "flatten_pool(authority_map.drop_pool_id",
    ):
        assert forbidden not in probe
    fixture_parse = probe.index("parsed = parse_fatbeans_capture_payload(payload)")
    offline_mode = probe.index("tables = replace(")
    table_load = probe.index("load_monitor_tables()", offline_mode)
    assert offline_mode < table_load < fixture_parse
    assert "warehouse_live_identity_check=False" in probe
    assert "flatten_pool(1066, tables.drops, tables.items)" in probe
    assert "unauthorized root and leaf did not fail closed" in probe
    assert 'artifact["map_id"] == spec["map_id"]' in probe
    assert 'artifact["model_map_id"] == spec["map_id"]' in probe
    assert 'artifact["map_alias_mode"] == ""' in probe
    assert 'artifact["hero"] == spec["hero"]' in probe
    assert 'artifact["inventory_count"] == spec["inventory_count"]' in probe
    assert 'artifact.get("final_decision_value")' in probe
    assert "{1086008, 1096009}.issubset(recovered_ids)" in probe
    assert 'if spec["map_id"] == 2508:' in probe
    assert '"expected_status": "degraded_sampleable"' not in probe
    assert "require_current_fixture_authority(tables, spec['map_id']" in probe
    assert "client protection runtime fixture missing" in probe


def test_runtime_probe_fixtures_match_current_package_authority() -> None:
    from bidking_lab.live.fatbeans import parse_fatbeans_capture_payload
    from bidking_lab.live.monitor import (
        build_monitor_artifact_from_payload,
        load_monitor_tables,
    )
    from bidking_lab.simulation.basic_mc import (
        DropPoolResolutionError,
        flatten_pool,
    )

    def fixture_payload(name: str) -> tuple[str, object]:
        path = ROOT / "tests" / "fixtures" / "live" / name
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            json.loads(raw)
            payload = raw
        except json.JSONDecodeError:
            payload = "[" + ",".join(
                line for line in raw.splitlines() if line.strip()
            ) + "]"
        parsed = parse_fatbeans_capture_payload(payload)
        settled = next(
            state for state in reversed(parsed.states) if state.message_id == 0x002D
        )
        return payload, settled

    tables = replace(
        load_monitor_tables(tables_dir=ROOT / "data" / "raw" / "tables"),
        warehouse_live_identity_check=False,
    )
    receipt = tables.rankmap_distribution_receipt
    assert tables.rankmap_distribution_status == "ok"
    assert receipt["package_generation"] == "76b7ca-2026-10-08-723"
    assert receipt["scope_map_count"] == 112
    assert receipt["model_distribution_authority"] is True
    assert receipt["official_distribution_authority"] is False
    assert receipt["runtime_open_claim"] is False
    payload_5604, settled_5604 = fixture_payload(
        "settlement_5604_f3_split_npcap_live.jsonl"
    )
    assert settled_5604.map_id == 5604
    assert tables.maps[5604].drop_pool_id == 2704
    artifact = build_monitor_artifact_from_payload(
        payload_5604,
        file="protection-current-authority-source-test",
        tables=tables,
        n_trials=40,
        roi_trials=20,
    )
    assert artifact["map_id"] == 5604
    assert artifact["model_map_id"] == 5604
    assert artifact["map_alias_mode"] == ""
    assert artifact["inventory_count"] == 33
    assert artifact["settlement_incomplete"] is False
    assert (
        isinstance(artifact.get("final_decision_value"), int)
        and artifact["final_decision_value"] > 0
    )

    payload_2508, settled_2508 = fixture_payload(
        "settlement_2508_tail_desync_npcap_live.jsonl"
    )
    assert settled_2508.map_id == 2508
    assert tables.maps[2508].drop_pool_id == 2508
    artifact_2508 = build_monitor_artifact_from_payload(
        payload_2508,
        file="protection-current-authority-source-test",
        tables=tables,
        n_trials=40,
        roi_trials=20,
    )
    support = artifact_2508.get("drop_pool_support")
    assert artifact_2508["map_id"] == 2508
    assert artifact_2508["model_map_id"] == 2508
    assert artifact_2508["map_alias"] == {}
    assert artifact_2508["map_alias_mode"] == ""
    assert artifact_2508["inventory_count"] == 47
    assert artifact_2508["settlement_incomplete"] is True
    assert {1086008, 1096009}.issubset({item.item_id for item in settled_2508.inventory_items})
    assert artifact_2508["settlement_incomplete_detail"] == {
        "captured_items": 47,
        "declared_items": 51,
        "captured_value": 939234,
        "source": "packet",
    }
    assert artifact_2508["catalog_value_complete"] is True
    assert artifact_2508["catalog_unknown_item_ids"] == []
    assert artifact_2508["model_eval_skipped_reason"] == "settlement_incomplete"
    assert "model_eval" not in artifact_2508
    assert artifact_2508["ui_contract"]["truth"]["complete"] is False
    assert artifact_2508["ui_contract"]["truth"]["partial"] is True
    assert (
        artifact_2508["ui_contract"]["truth"]["source"]
        == "partial_settlement_capture"
    )
    assert (
        isinstance(artifact_2508.get("final_decision_value"), int)
        and artifact_2508["final_decision_value"] > 0
    )
    assert support is None

    with pytest.raises(DropPoolResolutionError) as failure:
        flatten_pool(1066, tables.drops, tables.items)
    assert failure.value.reason == "unknown_item"
    assert failure.value.pool_id == 1066
    assert failure.value.item_id == 1106013


def test_plaintext_sensitive_modules_fail_closed(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    probes = (
        f"hero_ref/src/{manifest['critical_native']['engine_modules'][0]}.py",
        f"hero_ref/tools/{manifest['critical_native']['tool_modules'][0]}.py",
        f"src/{manifest['critical_native']['package_modules'][0]}",
        "src/bidking_lab/inference/plaintext_probe.py",
    )
    for index, probe in enumerate(probes):
        members = _protected_members()
        members[probe] = b"SENSITIVE_IMPLEMENTATION"
        path = tmp_path / f"plain-{index}.zip"
        _write_zip(path, members)
        issues = _AUDIT.audit_zip(path, protection_manifest=MANIFEST_PATH)
        assert any(issue.startswith("plaintext_sensitive_") for issue in issues)


def test_missing_native_modules_fail_closed(tmp_path: Path) -> None:
    members = _protected_members()
    engine_pyd = next(
        name for name in members if name.startswith("hero_ref/src/ahmad_ref_engine.")
    )
    members.pop(engine_pyd)
    inference_pyds = [
        name
        for name in members
        if name.startswith("src/bidking_lab/inference/") and name.endswith(".pyd")
    ]
    members.pop(inference_pyds[0])
    issues = _issues(tmp_path, members)
    assert issues.count("protected_native_module_missing") == 2


def test_overlay_shell_and_excluded_tool_fail_closed(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    members = _protected_members()
    members[manifest["critical_native"]["overlay"]["source"]] = (
        b"class AhmadTkOverlay:\n    pass\n" + b"x" * 600
    )
    members[manifest["encrypted_stage_excludes"][0]] = b"developer helper"
    issues = _issues(tmp_path, members)
    assert "protected_overlay_shell_invalid" in issues
    assert "excluded_sensitive_tool_present" in issues


def test_generated_cython_source_and_debug_artifacts_fail_closed(
    tmp_path: Path,
) -> None:
    members = _protected_members()
    members["src/bidking_lab/inference/v2.c"] = b"generated C exposes source"
    members["hero_ref/tools/_protected_ahmad_tk_overlay.pdb"] = b"debug symbols"
    issues = _issues(tmp_path, members)
    assert "generated_native_source" in issues
    assert "project_native_build_artifact" in issues


def test_malformed_protection_manifest_fails_with_stable_label(tmp_path: Path) -> None:
    bad_manifest = tmp_path / "bad-protection.json"
    bad_manifest.write_text(
        json.dumps({"schema": "bidking_client_protection_v2", "critical_native": {}}),
        encoding="utf-8",
    )
    path = tmp_path / "candidate.zip"
    _write_zip(path, _protected_members())
    assert _AUDIT.audit_zip(path, protection_manifest=bad_manifest) == [
        "client_protection_manifest_invalid"
    ]


def test_build_script_wires_manifest_into_package_and_zip_audit() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    assert '"scripts/client_protection_manifest.json"' in build
    assert "packaged_plaintext_python_allowlist" in build
    protection_at = build.index("protection_manifest = if ($Encrypt)")
    protection_identity_at = build.index(
        "Get-BuildAuditSourceIdentity", protection_at
    )
    assert protection_at < protection_identity_at
    assert '"scripts/client_protection_manifest.json"' in build
    assert '"--build-receipt", $BuildAuditReceiptPath' in build
    assert "ClientProtectionManifestSHA256:" in build
    assert "WssOperationalABI:" in build
    assert "WssOperationalGeneration:" in build
    assert "WssAddonBootstrapSHA256:" in build
    assert "WssAddonLoaderSHA256:" in build
    assert "assert not hasattr(__import__('_local_compat_addon_loader'), 'source')" in build
    assert "_protected_ahmad_tk_overlay" in build


def test_current_activation_carrier_and_package_identity_have_explicit_boundaries() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    secure_tree = next(
        tree
        for tree in manifest["critical_native"]["package_trees"]
        if tree["path"] == "bidking_lab/secure"
    )
    assert (
        "bidking_lab/secure/activation_http_e2ee.py"
        in secure_tree["protected_modules"]
    )
    assert (
        "src/bidking_lab/secure/activation_http_e2ee.py"
        not in manifest["packaged_plaintext_python_allowlist"]
    )
    assert (
        "scripts/package_generation_identity.py"
        in manifest["packaged_plaintext_python_allowlist"]
    )


def test_explicit_manifest_forces_encrypted_boundary(tmp_path: Path) -> None:
    members = _protected_members()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    engine = manifest["critical_native"]["engine_modules"][0]
    pyd = next(
        name
        for name in members
        if name.startswith(f"hero_ref/src/{engine}.")
    )
    members.pop(pyd)
    members[f"hero_ref/src/{engine}.py"] = b"plain engine"
    members["BUILD_MANIFEST.txt"] = members["BUILD_MANIFEST.txt"].replace(
        b"Encrypted: True", b"Encrypted: False"
    )
    issues = _issues(tmp_path, members)
    assert "client_protection_encryption_required" in issues
    assert "protected_native_module_missing" in issues
    assert "plaintext_sensitive_module" in issues


def test_unlisted_plaintext_module_fails_closed(tmp_path: Path) -> None:
    for index, member in enumerate(
        (
            "hero_ref/tools/new_quote_engine.py",
            "scripts/new_quote_engine.py",
            "诊断工具/new_quote_engine.py",
        )
    ):
        members = _protected_members()
        members[member] = b"def quote(): return 1"
        path = tmp_path / f"candidate-{index}.zip"
        _write_zip(path, members)
        assert "plaintext_sensitive_module" in _AUDIT.audit_zip(
            path,
            protection_manifest=MANIFEST_PATH,
        )


def test_build_only_source_authorities_are_forbidden_from_candidate(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    members = _protected_members()
    for source in manifest["build_only_source_authorities"]:
        members[source] = (ROOT / source).read_bytes()
    issues = _issues(tmp_path, members)
    assert "build_only_source_present" in issues
    assert "plaintext_sensitive_module" in issues


def test_near_match_native_filename_cannot_impersonate_module(
    tmp_path: Path,
) -> None:
    members = _protected_members()
    real = next(
        name for name in members if name.startswith("hero_ref/src/ahmad_ref_engine.")
    )
    members.pop(real)
    members["hero_ref/src/ahmad_ref_engine.fake.pyd"] = b"native"
    issues = _issues(tmp_path, members)
    assert "protected_native_module_missing" in issues
    assert "protected_native_module_untrusted" in issues
    assert "protected_native_module_unexpected" in issues


def test_native_tree_requires_exact_expected_module_set(tmp_path: Path) -> None:
    members = _protected_members()
    real = next(
        name
        for name in members
        if name.startswith("src/bidking_lab/inference/posterior.")
    )
    members.pop(real)
    members["src/bidking_lab/inference/unrelated_count_padding.cp313-win_amd64.pyd"] = (
        b"native"
    )
    issues = _issues(tmp_path, members)
    assert "protected_native_module_missing" in issues
    assert "protected_native_module_unexpected" in issues


def test_overlay_shell_is_an_exact_normalized_template(tmp_path: Path) -> None:
    members = _protected_members()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = manifest["critical_native"]["overlay"]["source"]
    members[source] += b"\ndef calculate_quote(): return 1\n"
    assert "protected_overlay_shell_invalid" in _issues(tmp_path, members)


def test_manifest_validation_rejects_ambiguous_or_unsafe_contracts(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    variants: list[dict[str, object]] = []

    duplicate = json.loads(json.dumps(manifest))
    duplicate["plaintext_allowlist"].append(duplicate["plaintext_allowlist"][0])
    variants.append(duplicate)

    overlap = json.loads(json.dumps(manifest))
    overlap["plaintext_allowlist"].append(
        "hero_ref/src/ahmad_ref_engine.py"
    )
    variants.append(overlap)

    traversal = json.loads(json.dumps(manifest))
    traversal["encrypted_stage_excludes"] = ["hero_ref/tools/../secret.py"]
    variants.append(traversal)

    bool_version = json.loads(json.dumps(manifest))
    bool_version["manifest_version"] = True
    variants.append(bool_version)

    negative_version = json.loads(json.dumps(manifest))
    negative_version["manifest_version"] = -1
    variants.append(negative_version)

    bad_allowlist = json.loads(json.dumps(manifest))
    bad_allowlist["plaintext_allowlist"] = ["outside.py"]
    variants.append(bad_allowlist)

    missing_packaged_plaintext = json.loads(json.dumps(manifest))
    missing_packaged_plaintext["packaged_plaintext_python_allowlist"].remove(
        missing_packaged_plaintext["plaintext_allowlist"][0]
    )
    variants.append(missing_packaged_plaintext)

    duplicate_packaged_plaintext = json.loads(json.dumps(manifest))
    duplicate_packaged_plaintext["packaged_plaintext_python_allowlist"].append(
        duplicate_packaged_plaintext["packaged_plaintext_python_allowlist"][0]
    )
    variants.append(duplicate_packaged_plaintext)

    runtime_plaintext = json.loads(json.dumps(manifest))
    runtime_plaintext["packaged_plaintext_python_allowlist"].append(
        "runtime/python/unknown.py"
    )
    variants.append(runtime_plaintext)

    duplicate_build_only = json.loads(json.dumps(manifest))
    duplicate_build_only["build_only_source_authorities"] = [
        manifest["build_only_source_authorities"][0],
        manifest["build_only_source_authorities"][0],
    ]
    variants.append(duplicate_build_only)

    case_collision_build_only = json.loads(json.dumps(manifest))
    case_collision_build_only["build_only_source_authorities"] = [
        manifest["build_only_source_authorities"][0],
        manifest["build_only_source_authorities"][0].upper(),
    ]
    variants.append(case_collision_build_only)

    for unsafe in (
        "src/bidking_lab/extract/../secret.py",
        "/src/bidking_lab/extract/secret.py",
        "C:/src/bidking_lab/extract/secret.py",
        r"src\bidking_lab\extract\secret.py",
    ):
        unsafe_build_only = json.loads(json.dumps(manifest))
        unsafe_build_only["build_only_source_authorities"][0] = unsafe
        variants.append(unsafe_build_only)

    bool_build_only = json.loads(json.dumps(manifest))
    bool_build_only["build_only_source_authorities"] = True
    variants.append(bool_build_only)

    non_list_build_only = json.loads(json.dumps(manifest))
    non_list_build_only["build_only_source_authorities"] = (
        manifest["build_only_source_authorities"][0]
    )
    variants.append(non_list_build_only)

    missing_build_only = json.loads(json.dumps(manifest))
    missing_build_only.pop("build_only_source_authorities")
    variants.append(missing_build_only)

    overlapping_build_only = json.loads(json.dumps(manifest))
    overlapping_build_only["build_only_source_authorities"][0] = manifest[
        "plaintext_allowlist"
    ][0]
    variants.append(overlapping_build_only)

    candidate = tmp_path / "candidate.zip"
    _write_zip(candidate, _protected_members())
    for index, variant in enumerate(variants):
        path = tmp_path / f"bad-{index}.json"
        path.write_text(json.dumps(variant), encoding="utf-8")
        assert _AUDIT.audit_zip(candidate, protection_manifest=path) == [
            "client_protection_manifest_invalid"
        ]
