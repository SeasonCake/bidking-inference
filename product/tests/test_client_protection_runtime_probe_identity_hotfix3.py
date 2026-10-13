from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "client_protection_runtime_probe.py"
MANIFEST = ROOT / "scripts" / "client_protection_manifest.json"

_SPEC = importlib.util.spec_from_file_location(
    "client_protection_runtime_probe_identity_hotfix3",
    PROBE,
)
assert _SPEC and _SPEC.loader
_PROBE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _PROBE
_SPEC.loader.exec_module(_PROBE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _visual_cpp_environment() -> dict[str, str]:
    vswhere = Path(
        r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    )
    if not vswhere.is_file():
        pytest.fail("Visual Studio locator is required for the real compile control")
    located = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    installation = Path(located.stdout.strip())
    vcvars = installation / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if located.returncode or not vcvars.is_file():
        pytest.fail("MSVC x64 build environment is required for the real compile control")
    exported = subprocess.run(
        ["cmd.exe", "/d", "/c", str(vcvars), "&&", "set"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if exported.returncode:
        pytest.fail(exported.stderr or exported.stdout)
    environment = dict(os.environ)
    for line in exported.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            name, value = line.split("=", 1)
            environment[name] = value
    return environment


def _contract(
    executable: Path,
    *,
    version: str = "3.13.15",
    release_eligible: bool = True,
) -> object:
    return _PROBE.BuildPythonContract(
        profile="test-runtime-source",
        version=version,
        cache_tag="cpython-313",
        abi_tag="cp313-win_amd64",
        platform="win-amd64",
        executable_sha256=_sha256(executable),
        contract_source="test-contract",
        contract_sha256="a" * 64,
        release_eligible=release_eligible,
    )


def _payload(executable: Path, *, version: str = "3.13.15") -> dict[str, object]:
    return {
        "executable": str(executable.resolve()),
        "version": version,
        "cache_tag": "cpython-313",
        "abi_tag": "cp313-win_amd64",
        "platform": "win-amd64",
        "machine": "AMD64",
        "pointer_bits": 64,
    }


def _fake_dependency_stage(stage: Path) -> object:
    root = stage / "runtime" / "python" / "Lib" / "site-packages"
    root.mkdir(parents=True)
    (root / "identity.txt").write_text("stage-owned\n", encoding="ascii")
    rows, inventory_sha256 = _PROBE._runtime_dependency_tree_inventory(root)
    return _PROBE.RuntimeDependencyStage(
        root=root.resolve(),
        runtime_dependencies=_PROBE._runtime_dependency_contract(),
        file_count=len(rows),
        total_bytes=sum(row[1] for row in rows),
        inventory_sha256=inventory_sha256,
    )


def test_probe_binds_explicit_or_current_python_without_legacy_fallback() -> None:
    source = PROBE.read_text(encoding="utf-8")

    assert r"C:\Python313\python.exe" not in source
    assert "--build-python" in source
    assert "sys.executable" in source
    assert "runtime_python_source_manifest.json" in source
    assert "CLIENT_PROTECTION_BUILD_PYTHON_IDENTITY" in source
    assert (
        '[str(executable), "-E", "-s", "-S", "-B", "-c", IDENTITY_PROBE]'
        in source
    )


def test_runtime_source_contract_is_exact_cp31315() -> None:
    contract = _PROBE._runtime_source_contract()

    assert contract.profile == "runtime-source"
    assert contract.version == "3.13.15"
    assert contract.cache_tag == "cpython-313"
    assert contract.abi_tag == "cp313-win_amd64"
    assert contract.platform == "win-amd64"
    assert contract.executable_sha256 == (
        "85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd"
    )
    assert contract.release_eligible is True


def test_runtime_dependency_authorities_and_builder_aliases_are_exact() -> None:
    dependencies = _PROBE._runtime_dependency_contract()
    assert dependencies == {
        "annotated-types": "0.7.0",
        "certifi": "2026.4.22",
        "cffi": "2.0.0",
        "cryptography": "48.0.1",
        "numpy": "2.4.4",
        "pillow": "12.2.0",
        "psutil": "7.0.0",
        "pycparser": "3.0",
        "pydantic": "2.13.3",
        "pydantic-core": "2.46.3",
        "typing-extensions": "4.15.0",
        "typing-inspection": "0.4.2",
    }
    builder = (ROOT / "scripts" / "build_live_portable.ps1").read_text("utf-8")
    package_block = builder.split("$pkgs = @(", 1)[1].split(
        "foreach ($pk in $pkgs)", 1
    )[0]
    builder_aliases = set(re.findall(r'"([A-Za-z0-9_]+)"', package_block))
    assert builder_aliases == {
        "PIL",
        "_cffi_backend",
        "annotated_types",
        "certifi",
        "cffi",
        "cryptography",
        "numpy",
        "pillow",
        "psutil",
        "pycparser",
        "pydantic",
        "pydantic_core",
        "typing_extensions",
        "typing_inspection",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "bool-dependencies",
        "list-dependencies",
        "missing-dependency",
        "extra-dependency",
        "wrong-version",
        "bool-version",
        "missing-runtime-wheel",
    ],
)
def test_runtime_dependency_manifest_shape_and_population_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    manifest = json.loads(_PROBE.RUNTIME_SOURCE_MANIFEST.read_text("utf-8"))
    if mutation == "bool-dependencies":
        manifest["runtime_dependencies"] = True
    elif mutation == "list-dependencies":
        manifest["runtime_dependencies"] = []
    elif mutation == "missing-dependency":
        manifest["runtime_dependencies"].pop("numpy")
    elif mutation == "extra-dependency":
        manifest["runtime_dependencies"]["ambient-extra"] = "1.0"
    elif mutation == "wrong-version":
        manifest["runtime_dependencies"]["numpy"] = "0.0.0-wrong"
    elif mutation == "bool-version":
        manifest["runtime_dependencies"]["numpy"] = True
    elif mutation == "missing-runtime-wheel":
        manifest["wheels"] = [
            item
            for item in manifest["wheels"]
            if item.get("distribution") != "numpy"
        ]
    else:
        raise AssertionError(mutation)
    candidate = tmp_path / "runtime_python_source_manifest.json"
    candidate.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(_PROBE.BuildPythonIdentityError):
        _PROBE._runtime_dependency_contract(candidate)


@pytest.mark.parametrize("duplicate_scope", ["document", "dependencies"])
def test_runtime_dependency_manifest_duplicate_keys_fail_closed(
    tmp_path: Path,
    duplicate_scope: str,
) -> None:
    raw = _PROBE.RUNTIME_SOURCE_MANIFEST.read_text("utf-8")
    if duplicate_scope == "document":
        raw = raw.replace(
            '  "runtime_dependencies": {',
            '  "runtime_dependencies": {},\n  "runtime_dependencies": {',
            1,
        )
    else:
        raw = raw.replace(
            '    "numpy": "2.4.4",',
            '    "numpy": "2.4.4",\n    "numpy": "2.4.4",',
            1,
        )
    candidate = tmp_path / "runtime_python_source_manifest.json"
    candidate.write_text(raw, encoding="utf-8")
    with pytest.raises(_PROBE.BuildPythonIdentityError, match="duplicate key"):
        _PROBE._runtime_dependency_contract(candidate)


def test_missing_build_python_fails_before_identity_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing" / "python.exe"
    called = False

    def unexpected_probe(_path: Path) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("identity subprocess must not start")

    monkeypatch.setattr(_PROBE, "_probe_executable", unexpected_probe)
    with pytest.raises(_PROBE.BuildPythonIdentityError, match="does not exist"):
        _PROBE._bind_build_python(missing, _contract(PROBE))
    assert called is False


def test_relative_build_python_is_rejected_without_path_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_probe(_path: Path) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("identity subprocess must not start")

    monkeypatch.setattr(_PROBE, "_probe_executable", unexpected_probe)
    with pytest.raises(_PROBE.BuildPythonIdentityError, match="PATH lookup"):
        _PROBE._bind_build_python(Path("python.exe"), _contract(PROBE))
    assert called is False


def test_wrong_executable_hash_fails_before_identity_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"selected")
    expected = tmp_path / "expected.exe"
    expected.write_bytes(b"expected")
    called = False

    def unexpected_probe(_path: Path) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("identity subprocess must not start")

    monkeypatch.setattr(_PROBE, "_probe_executable", unexpected_probe)
    with pytest.raises(_PROBE.BuildPythonIdentityError, match="SHA-256"):
        _PROBE._bind_build_python(selected, _contract(expected))
    assert called is False


def test_cp3133_cannot_masquerade_as_runtime_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"same-runtime-source-bytes")
    monkeypatch.setattr(
        _PROBE,
        "_probe_executable",
        lambda path: _payload(path, version="3.13.3"),
    )

    with pytest.raises(_PROBE.BuildPythonIdentityError, match="version='3.13.3'"):
        _PROBE._bind_build_python(selected, _contract(selected))


def test_space_path_binds_and_receipt_records_complete_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "layout with spaces" / "python.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"python-layout")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))

    identity = _PROBE._bind_build_python(selected, _contract(selected))
    receipt = identity.receipt()

    assert identity.executable == selected.resolve()
    assert receipt["schema"] == _PROBE.IDENTITY_SCHEMA
    assert receipt["selection"] == "explicit"
    assert receipt["resolved_executable"] == str(selected.resolve())
    assert receipt["executable_sha256"] == _sha256(selected)
    assert receipt["version"] == "3.13.15"
    assert receipt["cache_tag"] == "cpython-313"
    assert receipt["abi_tag"] == "cp313-win_amd64"
    assert receipt["platform"] == "win-amd64"
    assert receipt["machine"] == "AMD64"
    assert receipt["pointer_bits"] == 64
    assert receipt["release_eligible"] is True
    assert receipt["windows_file_identity"] == identity.file_identity.receipt()
    assert len(receipt["windows_file_identity"]["volume_serial"]) == 8
    assert len(receipt["windows_file_identity"]["file_index"]) == 16


def test_symlink_or_same_file_alias_is_accepted_after_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"python-layout")
    alias = tmp_path / "python same-file alias.exe"
    try:
        alias.symlink_to(selected)
    except OSError:
        os.link(selected, alias)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))

    identity = _PROBE._bind_build_python(alias, _contract(selected))

    assert os.path.samefile(identity.executable, selected)
    assert _PROBE._require_bound_executable(identity, alias) == identity.executable


def test_same_bytes_different_file_cannot_replace_bound_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "selected" / "python.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"identical-python-bytes")
    alternate = tmp_path / "alternate" / "python.exe"
    alternate.parent.mkdir()
    shutil.copy2(selected, alternate)
    assert _sha256(selected) == _sha256(alternate)
    assert not os.path.samefile(selected, alternate)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))

    with pytest.raises(_PROBE.BuildPythonIdentityError, match="changed after"):
        _PROBE._require_bound_executable(identity, alternate)


@pytest.mark.parametrize("replacement_bytes", [b"different-bytes", b"bound-python"])
def test_same_path_replace_is_denied_for_bound_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_bytes: bytes,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(replacement_bytes)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))

    try:
        with pytest.raises(PermissionError):
            os.replace(replacement, selected)
        with pytest.raises(PermissionError):
            selected.write_bytes(b"in-place-tamper")
        assert _PROBE._require_bound_executable(identity, selected) == identity.executable
    finally:
        identity.close()

    os.replace(replacement, selected)
    assert selected.read_bytes() == replacement_bytes


def test_retargeted_candidate_is_rejected_by_bound_windows_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / "original"
    original.mkdir()
    (original / "python.exe").write_bytes(b"identical-python-bytes")
    retargeted = tmp_path / "retargeted"
    retargeted.mkdir()
    shutil.copy2(original / "python.exe", retargeted / "python.exe")
    junction = tmp_path / "selected-runtime"

    def create_junction(target: Path) -> None:
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    create_junction(original)
    selected = junction / "python.exe"
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    try:
        os.rmdir(junction)
        create_junction(retargeted)
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="changed after"):
            _PROBE._require_bound_executable(identity, selected)
    finally:
        identity.close()


def test_cross_child_replace_is_denied_and_each_child_revalidates_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement-python")
    probe_calls = 0

    def fake_probe(path: Path) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        return _payload(path)

    monkeypatch.setattr(_PROBE, "_probe_executable", fake_probe)
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    child_calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        child_calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_PROBE.subprocess, "run", fake_run)
    try:
        _PROBE._run_python(identity, ["-B", "-c", "pass"], tmp_path)
        with pytest.raises(PermissionError):
            os.replace(replacement, selected)
        _PROBE._run_python(identity, ["-B", "-c", "pass"], tmp_path)
    finally:
        identity.close()

    assert len(child_calls) == 2
    assert probe_calls == 5  # bind plus pre/post validation for both children


def test_runtime_metadata_change_is_rejected_before_child_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE,
        "_probe_executable",
        lambda path: _payload(path, version="3.13.3"),
    )
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("child must not spawn after metadata mismatch")
        ),
    )

    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="version='3.13.3'"):
            _PROBE._run_python(identity, ["-B", "-c", "pass"], tmp_path)
    finally:
        identity.close()


def test_child_exception_still_revalidates_bound_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    probe_calls = 0

    def fake_probe(path: Path) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        return _payload(path)

    monkeypatch.setattr(_PROBE, "_probe_executable", fake_probe)
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("child failed")),
    )
    try:
        with pytest.raises(RuntimeError, match="child failed"):
            _PROBE._run_python(identity, ["-B", "-c", "pass"], tmp_path)
    finally:
        identity.close()

    assert probe_calls == 3  # bind plus pre/post validation around the failed child


def test_reported_executable_must_be_selected_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "selected.exe"
    selected.write_bytes(b"identical-python-bytes")
    reported = tmp_path / "reported.exe"
    shutil.copy2(selected, reported)
    monkeypatch.setattr(
        _PROBE,
        "_probe_executable",
        lambda _path: _payload(reported),
    )

    with pytest.raises(_PROBE.BuildPythonIdentityError, match="not the selected"):
        _PROBE._bind_build_python(selected, _contract(selected))


def test_current_sys_executable_is_an_explicit_auditable_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "current-python.exe"
    selected.write_bytes(b"current-python")
    monkeypatch.setattr(_PROBE.sys, "executable", str(selected))
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))

    identity = _PROBE._bind_build_python(None, _contract(selected))

    assert identity.selection == "current-sys-executable"
    assert identity.requested_executable == str(selected)


def test_legacy_cp3133_is_identity_only_and_not_release_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python3133.exe"
    selected.write_bytes(b"legacy-control")
    legacy = _PROBE._legacy_cp3133_contract()
    monkeypatch.setattr(
        _PROBE._HeldExecutable,
        "sha256",
        lambda _self: legacy.executable_sha256,
    )
    monkeypatch.setattr(
        _PROBE,
        "_probe_executable",
        lambda path: _payload(path, version="3.13.3"),
    )

    identity = _PROBE._bind_build_python(selected, legacy)

    assert identity.version == "3.13.3"
    assert identity.contract.profile == "legacy-cp3133-identity-control"
    assert identity.receipt()["release_eligible"] is False


def test_identity_only_returns_before_stage_or_compile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"identity-only")
    contract = _contract(selected)
    monkeypatch.setattr(_PROBE, "_runtime_source_contract", lambda: contract)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    monkeypatch.setattr(
        _PROBE.tempfile,
        "mkdtemp",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("stage must not start")),
    )

    result = _PROBE.main(
        ["--build-python", str(selected), "--identity-only"]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert output.startswith("CLIENT_PROTECTION_BUILD_PYTHON_IDENTITY ")
    receipt = json.loads(output.split(" ", 1)[1])
    assert receipt["release_eligible"] is True
    selected.write_bytes(b"identity-only-handle-was-closed")


def test_run_python_prepends_bound_interpreter_and_isolated_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_PROBE.subprocess, "run", fake_run)

    _PROBE._run_python(identity, ["-B", "-c", "pass"], tmp_path)

    assert calls == [
        [str(selected.resolve()), "-E", "-s", "-S", "-B", "-c", "pass"]
    ]


def test_client_protection_manifest_count_and_cleanup_contract_remain_exact() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    critical = manifest["critical_native"]
    package = len(critical["package_modules"]) + sum(
        len(tree["protected_modules"]) for tree in critical["package_trees"]
    )
    source = PROBE.read_text(encoding="utf-8")

    assert len(critical["engine_modules"]) == 5
    assert len(critical["tool_modules"]) + 1 == 9
    assert package == 98
    assert 5 + 9 + package == 112
    package_targets = _PROBE._package_targets(ROOT / "unused", critical)
    assert (
        "bidking_lab/extract/current_rankmap_package_contract.py"
        in package_targets
    )
    assert "bidking_lab/live/current_rankmap_runtime.py" in package_targets
    assert "bidking_lab/live/catalog_value.py" in package_targets
    assert "bidking_lab/live/fact_receipts.py" in package_targets
    assert "all(str(mod.__file__).lower().endswith(\".pyd\")" in source
    assert "shutil.rmtree(temp, ignore_errors=True)" in source


def test_build_only_source_authority_contract_is_exact_and_fail_closed() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["build_only_source_authorities"] == [
        "src/bidking_lab/extract/official_20261008_model_profile.py",
        "src/bidking_lab/extract/update606_package_input_contract.py",
        "src/bidking_lab/extract/update606_release_contract.py",
    ]
    _PROBE._verify_exhaustive_source_contract(manifest)

    variants: list[dict[str, object]] = []
    for value in (
        [
            manifest["build_only_source_authorities"][0],
            manifest["build_only_source_authorities"][0],
        ],
        [
            manifest["build_only_source_authorities"][0],
            manifest["build_only_source_authorities"][0].upper(),
        ],
        [
            "src/bidking_lab/extract/../secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            "/src/bidking_lab/extract/secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            "C:/src/bidking_lab/extract/secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        [
            r"src\bidking_lab\extract\secret.py",
            manifest["build_only_source_authorities"][1],
        ],
        True,
        manifest["build_only_source_authorities"][0],
        [
            "src/bidking_lab/extract/stale_build_only.py",
            manifest["build_only_source_authorities"][1],
        ],
    ):
        variant = json.loads(json.dumps(manifest))
        variant["build_only_source_authorities"] = value
        variants.append(variant)
    missing = json.loads(json.dumps(manifest))
    missing.pop("build_only_source_authorities")
    variants.append(missing)

    for variant in variants:
        with pytest.raises((KeyError, RuntimeError)):
            _PROBE._verify_exhaustive_source_contract(variant)

    build_only = set(manifest["build_only_source_authorities"])
    critical = manifest["critical_native"]
    protected = {
        *(f"hero_ref/src/{item}.py" for item in critical["engine_modules"]),
        *(f"hero_ref/tools/{item}.py" for item in critical["tool_modules"]),
        critical["overlay"]["source"],
        *(f"src/{item}" for item in critical["package_modules"]),
        *(
            f"src/{item}"
            for tree in critical["package_trees"]
            for item in tree["protected_modules"]
        ),
    }
    assert build_only.isdisjoint(manifest["plaintext_allowlist"])
    assert build_only.isdisjoint(manifest["packaged_plaintext_python_allowlist"])
    assert build_only.isdisjoint(protected)
    assert len(protected) == 112

    missing_rankmap = json.loads(json.dumps(manifest))
    extract_tree = next(
        tree
        for tree in missing_rankmap["critical_native"]["package_trees"]
        if tree["path"] == "bidking_lab/extract"
    )
    extract_tree["protected_modules"].remove(
        "bidking_lab/extract/current_rankmap_package_contract.py"
    )
    with pytest.raises(RuntimeError, match="unlisted=1 stale=0"):
        _PROBE._verify_exhaustive_source_contract(missing_rankmap)

    stale_rankmap = json.loads(json.dumps(manifest))
    live_tree = next(
        tree
        for tree in stale_rankmap["critical_native"]["package_trees"]
        if tree["path"] == "bidking_lab/live"
    )
    live_tree["protected_modules"].append("bidking_lab/live/stale_rankmap_runtime.py")
    with pytest.raises(RuntimeError, match="unlisted=0 stale=1"):
        _PROBE._verify_exhaustive_source_contract(stale_rankmap)


def test_compile_child_has_a_separate_manifest_bound_toolchain_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    (tmp_path / "Lib" / "site-packages").mkdir(parents=True)
    setup_script = tmp_path / "_setup.py"
    setup_script.write_text("raise AssertionError('not executed')\n", encoding="ascii")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_PROBE.subprocess, "run", fake_run)
    try:
        _PROBE._run_compile_python(
            identity,
            setup_script,
            ["probe_module"],
            tmp_path,
        )
    finally:
        identity.close()

    assert len(calls) == 1
    command = calls[0]
    assert command[:6] == [
        str(selected.resolve()),
        "-E",
        "-s",
        "-S",
        "-B",
        "-c",
    ]
    assert command[-2:] == [str(setup_script.resolve()), "probe_module"]
    assert "setuptools" in command[6]
    assert "Cython" in command[6]


@pytest.mark.parametrize("injected", ["-S", "-c", "-I", "--help", ""])
def test_compile_child_rejects_caller_flag_or_empty_argument_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    injected: str,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    setup_script = tmp_path / "_setup.py"
    setup_script.write_text("pass\n", encoding="ascii")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid compile child must not spawn")
        ),
    )
    try:
        with pytest.raises(ValueError, match="compile argument"):
            _PROBE._run_compile_python(
                identity,
                setup_script,
                [injected],
                tmp_path,
            )
    finally:
        identity.close()


def test_runtime_child_keeps_no_site_and_uses_only_explicit_stage_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    stage = tmp_path / "stage with spaces"
    package = stage / "src"
    package.mkdir(parents=True)
    dependency_stage = _fake_dependency_stage(stage)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_PROBE.subprocess, "run", fake_run)
    try:
        _PROBE._run_runtime_python(
            identity,
            "pass",
            [],
            stage,
            module_paths=[stage, package],
            dependency_stage=dependency_stage,
        )
    finally:
        identity.close()

    assert len(calls) == 1
    command = calls[0]
    assert command[:6] == [
        str(selected.resolve()),
        "-E",
        "-s",
        "-S",
        "-B",
        "-c",
    ]
    assert "sitecustomize" in command[6]
    runtime_config = json.loads(command[7])
    assert runtime_config["stage"] == str(stage.resolve())
    assert runtime_config["module_paths"] == [
        str(stage.resolve()),
        str(package.resolve()),
    ]
    assert runtime_config["dependency_root"] == str(dependency_stage.root)
    assert runtime_config["runtime_dependencies"] == (
        _PROBE._runtime_dependency_contract()
    )


def test_runtime_child_rejects_module_path_outside_explicit_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    stage = tmp_path / "stage with spaces"
    stage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    dependency_stage = _fake_dependency_stage(stage)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    try:
        with pytest.raises(ValueError, match="outside the explicit Stage"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage,
                module_paths=[outside],
                dependency_stage=dependency_stage,
            )
    finally:
        identity.close()


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "tampered", "duplicate-dist-info"],
)
def test_runtime_dependency_stage_mutation_fails_before_child_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    stage = tmp_path / "stage"
    stage.mkdir()
    dependency_stage = _fake_dependency_stage(stage)
    identity_file = dependency_stage.root / "identity.txt"
    if mutation == "missing":
        identity_file.unlink()
    elif mutation == "extra":
        (dependency_stage.root / "extra.py").write_text("pass\n", encoding="ascii")
    elif mutation == "tampered":
        identity_file.write_text("tampered\n", encoding="ascii")
    elif mutation == "duplicate-dist-info":
        duplicate = dependency_stage.root / "numpy_duplicate-2.4.4.dist-info"
        duplicate.mkdir()
        (duplicate / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: numpy\nVersion: 2.4.4\n",
            encoding="ascii",
        )
    else:
        raise AssertionError(mutation)
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid runtime dependency Stage must not spawn")
        ),
    )
    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="inventory changed"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage,
                module_paths=[stage],
                dependency_stage=dependency_stage,
            )
    finally:
        identity.close()


def test_runtime_dependency_wrong_version_and_outside_stage_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    stage = tmp_path / "stage"
    stage.mkdir()
    dependency_stage = _fake_dependency_stage(stage)
    wrong_versions = dict(dependency_stage.runtime_dependencies)
    wrong_versions["numpy"] = "0.0.0-wrong"
    wrong = _PROBE.RuntimeDependencyStage(
        root=dependency_stage.root,
        runtime_dependencies=wrong_versions,
        file_count=dependency_stage.file_count,
        total_bytes=dependency_stage.total_bytes,
        inventory_sha256=dependency_stage.inventory_sha256,
    )
    ambient = _PROBE.RuntimeDependencyStage(
        root=Path(r"C:\Python31315\Lib\site-packages"),
        runtime_dependencies=dependency_stage.runtime_dependencies,
        file_count=dependency_stage.file_count,
        total_bytes=dependency_stage.total_bytes,
        inventory_sha256=dependency_stage.inventory_sha256,
    )
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="authority changed"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage,
                module_paths=[stage],
                dependency_stage=wrong,
            )
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="outside"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage,
                module_paths=[stage],
                dependency_stage=ambient,
            )
    finally:
        identity.close()


def test_runtime_dependency_reparse_member_fails_before_child_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    stage = tmp_path / "stage"
    stage.mkdir()
    dependency_stage = _fake_dependency_stage(stage)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = dependency_stage.root / "reparse-member"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reparse runtime dependency Stage must not spawn")
        ),
    )
    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="reparse point"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage,
                module_paths=[stage],
                dependency_stage=dependency_stage,
            )
    finally:
        identity.close()
        os.rmdir(junction)


def test_runtime_stage_reparse_root_fails_before_child_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    target_stage = tmp_path / "target-stage"
    target_stage.mkdir()
    dependency_stage = _fake_dependency_stage(target_stage)
    stage_alias = tmp_path / "stage-alias"
    created = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(stage_alias),
            str(target_stage),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="reparse point"):
            _PROBE._run_runtime_python(
                identity,
                "pass",
                [],
                stage_alias,
                module_paths=[stage_alias],
                dependency_stage=dependency_stage,
            )
    finally:
        identity.close()
        os.rmdir(stage_alias)


def test_compile_child_rejects_bound_runtime_metadata_change_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "python.exe"
    selected.write_bytes(b"bound-python")
    (tmp_path / "Lib" / "site-packages").mkdir(parents=True)
    setup_script = tmp_path / "_setup.py"
    setup_script.write_text("pass\n", encoding="ascii")
    monkeypatch.setattr(_PROBE, "_probe_executable", lambda path: _payload(path))
    identity = _PROBE._bind_build_python(selected, _contract(selected))
    monkeypatch.setattr(
        _PROBE,
        "_probe_executable",
        lambda path: _payload(path, version="3.13.3"),
    )
    monkeypatch.setattr(
        _PROBE.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("compile child must not spawn after identity mutation")
        ),
    )
    try:
        with pytest.raises(_PROBE.BuildPythonIdentityError, match="version='3.13.3'"):
            _PROBE._run_compile_python(
                identity,
                setup_script,
                ["probe_module"],
                tmp_path,
            )
    finally:
        identity.close()


def test_compile_child_rejects_manifest_toolchain_version_before_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = Path(r"C:\Python31315\python.exe")
    setup_marker = tmp_path / "setup-executed.txt"
    setup_script = tmp_path / "_setup.py"
    setup_script.write_text(
        "from pathlib import Path\n"
        f"Path({str(setup_marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="ascii",
    )
    manifest = json.loads(_PROBE.RUNTIME_SOURCE_MANIFEST.read_text("utf-8"))
    manifest["build_tools"]["cython"] = "0.0.0-wrong"
    wrong_manifest = tmp_path / "runtime_python_source_manifest.json"
    wrong_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(_PROBE, "RUNTIME_SOURCE_MANIFEST", wrong_manifest)

    identity = _PROBE._bind_build_python(selected, _PROBE._runtime_source_contract())
    try:
        with pytest.raises(RuntimeError, match="cython version mismatch"):
            _PROBE._run_compile_python(
                identity,
                setup_script,
                ["probe_module"],
                tmp_path,
            )
    finally:
        identity.close()
    assert not setup_marker.exists()


def test_real_compile_and_runtime_children_reject_python_path_and_customizers(
    tmp_path: Path,
) -> None:
    selected = Path(r"C:\Python31315\python.exe")
    assert selected.is_file()
    stage = tmp_path / "stage with spaces"
    stage.mkdir()
    poison = tmp_path / "pythonpath-poison"
    userbase = tmp_path / "userbase"
    poison.mkdir()
    user_site = userbase / "Python313" / "site-packages"
    user_site.mkdir(parents=True)
    poison_marker = tmp_path / "customizer-executed.txt"
    poison_source = (
        "from pathlib import Path\n"
        f"Path({str(poison_marker)!r}).write_text('executed', encoding='utf-8')\n"
    )
    (stage / "sitecustomize.py").write_text(poison_source, encoding="utf-8")
    (poison / "sitecustomize.py").write_text(poison_source, encoding="utf-8")
    (user_site / "usercustomize.py").write_text(poison_source, encoding="utf-8")
    (stage / "probe_minimal.pyx").write_text(
        "def answer():\n    return 31315\n",
        encoding="ascii",
    )
    (stage / "_setup.py").write_text(
        _PROBE.SETUP_COMMON
        + """\
import json
from pathlib import Path
import setuptools
import Cython
Path("toolchain-receipt.json").write_text(json.dumps({
    "setuptools": setuptools.__version__,
    "setuptools_file": setuptools.__file__,
    "cython": Cython.__version__,
    "cython_file": Cython.__file__,
    "no_site": sys.flags.no_site,
    "no_user_site": sys.flags.no_user_site,
    "ignore_environment": sys.flags.ignore_environment,
    "dont_write_bytecode": sys.flags.dont_write_bytecode,
    "sys_path": sys.path,
}, sort_keys=True), encoding="utf-8")
sys.argv = [sys.argv[0], "build_ext", "--inplace"]
ext = Extension("probe_minimal", ["probe_minimal.pyx"],
                extra_compile_args=COMPILE_FLAGS, extra_link_args=LINK_FLAGS)
setup(ext_modules=cythonize([ext], language_level=3, quiet=True,
                            compiler_directives={"embedsignature": False,
                                                 "emit_code_comments": False}))
""",
        encoding="ascii",
    )
    environment = _visual_cpp_environment()
    environment["PYTHONPATH"] = str(poison)
    environment["PYTHONUSERBASE"] = str(userbase)
    environment["PYTHONSTARTUP"] = str(poison / "sitecustomize.py")

    identity = _PROBE._bind_build_python(selected, _PROBE._runtime_source_contract())
    try:
        dependency_stage = _PROBE._assemble_runtime_dependency_stage(identity, stage)
        _PROBE._run_compile_python(
            identity,
            stage / "_setup.py",
            ["probe_minimal"],
            stage,
            env=environment,
        )
        receipt = json.loads((stage / "toolchain-receipt.json").read_text("utf-8"))
        toolchain_root = (selected.parent / "Lib" / "site-packages").resolve()
        assert receipt["setuptools"] == "82.0.1"
        assert receipt["cython"] == "3.2.5"
        assert Path(receipt["setuptools_file"]).resolve().is_relative_to(toolchain_root)
        assert Path(receipt["cython_file"]).resolve().is_relative_to(toolchain_root)
        assert receipt["no_site"] == 1
        assert receipt["no_user_site"] == 1
        assert receipt["ignore_environment"] == 1
        assert receipt["dont_write_bytecode"] == 1
        assert str(poison.resolve()) not in receipt["sys_path"]
        assert str(user_site.resolve()) not in receipt["sys_path"]
        natives = list(stage.glob("probe_minimal*.pyd"))
        assert len(natives) == 1
        assert "cp313-win_amd64" in natives[0].name
        assert not poison_marker.exists()

        runtime_receipt = stage / "runtime-receipt.json"
        runtime_code = (
            "import json,sys,probe_minimal,numpy,psutil,pydantic,pydantic_core; "
            "import certifi,PIL,cryptography,cffi,typing_extensions; "
            "import annotated_types,typing_inspection,pycparser,_cffi_backend; "
            "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; "
            "from PIL import Image; "
            "from pathlib import Path; "
            "assert probe_minimal.answer() == 31315; "
            "dependency_root=(Path.cwd()/'runtime'/'python'/'Lib'/'site-packages').resolve(); "
            "dependency_modules=(numpy,psutil,pydantic,pydantic_core,certifi,PIL,"
            "cryptography,cffi,typing_extensions,annotated_types,typing_inspection,"
            "pycparser,_cffi_backend); "
            "assert all(Path(module.__file__).resolve().is_relative_to(dependency_root) "
            "for module in dependency_modules); "
            "assert int(numpy.arange(3).sum()) == 3; "
            "assert Image.core is not None; "
            "assert len(AESGCM.generate_key(bit_length=128)) == 16; "
            "assert psutil.Process().pid > 0; "
            "assert Path(certifi.where()).resolve().is_relative_to(dependency_root); "
            "assert Path(certifi.where()).stat().st_size > 10000; "
            f"Path({str(runtime_receipt)!r}).write_text(json.dumps(dict("
            "no_site=sys.flags.no_site,no_user_site=sys.flags.no_user_site,"
            "ignore_environment=sys.flags.ignore_environment,"
            "dont_write_bytecode=sys.flags.dont_write_bytecode,"
            "module=probe_minimal.__file__,dependency_root=str(dependency_root),"
            "numpy=numpy.__version__,sys_path=sys.path),sort_keys=True),"
            "encoding='utf-8')"
        )
        _PROBE._run_runtime_python(
            identity,
            runtime_code,
            [],
            stage,
            module_paths=[stage],
            dependency_stage=dependency_stage,
            env=environment,
        )
        runtime = json.loads(runtime_receipt.read_text("utf-8"))
        assert runtime["no_site"] == 1
        assert runtime["no_user_site"] == 1
        assert runtime["ignore_environment"] == 1
        assert runtime["dont_write_bytecode"] == 1
        assert Path(runtime["module"]).resolve() == natives[0].resolve()
        assert runtime["numpy"] == "2.4.4"
        assert Path(runtime["dependency_root"]) == dependency_stage.root
        assert runtime["sys_path"][0] == str(stage.resolve())
        assert str(poison.resolve()) not in runtime["sys_path"]
        assert str(user_site.resolve()) not in runtime["sys_path"]
        assert not poison_marker.exists()
    finally:
        identity.close()
