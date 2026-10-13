from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
BOOTSTRAP_CRATE = ROOT / "native" / "bidking_trust_bootstrap"
BOOTSTRAP_SOURCE = BOOTSTRAP_CRATE / "src" / "main.rs"
BOOTSTRAP_BUILD = BOOTSTRAP_CRATE / "build.rs"
HARDENING_PATH = ROOT / "scripts" / "native_release_hardening.py"
NATIVE_CONTRACT_PATH = ROOT / "scripts" / "release_native_binary_contract.json"
PARENT = "3ef2f0e6ccb553167abaf127a7dd7ad519fe9e5e"
ROOT_EXE_PARENT = "3eeb45ff89c2be1d212fdff082da334b0a12512b"
WINDOWS_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")

_HARDENING_SPEC = importlib.util.spec_from_file_location(
    "native_release_hardening_public_bootstrap", HARDENING_PATH
)
assert _HARDENING_SPEC and _HARDENING_SPEC.loader
_HARDENING = importlib.util.module_from_spec(_HARDENING_SPEC)
_HARDENING_SPEC.loader.exec_module(_HARDENING)


def _git_show(spec: str) -> str:
    completed = subprocess.run(
        ["git", "show", spec],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _native_bootstrap(source: str) -> str:
    marker = 'const BOOTSTRAP: &str = r#"\n'
    start = source.index(marker) + len(marker)
    end = source.index('\n"#;', start)
    return source[start:end]


def _public_fragments(source: str) -> tuple[str, str, str]:
    bootstrap_marker = '$integrityBootstrap = @"\n'
    bootstrap_start = source.index(bootstrap_marker) + len(bootstrap_marker)
    bootstrap_end = source.index('\n"@\n$runnerCode', bootstrap_start)

    runner_marker = '$runnerCode = $integrityBootstrap + "`n" + @"\n'
    runner_start = source.index(runner_marker, bootstrap_end) + len(runner_marker)
    runner_end = source.index('\n"@\n$pythonBootstrapEntry', runner_start)

    diagnostics_marker = '$diagnosticsCode = $integrityBootstrap + "`n" + @"\n'
    diagnostics_start = source.index(diagnostics_marker, runner_end) + len(
        diagnostics_marker
    )
    diagnostics_end = source.index(
        '\n"@\n$pythonBootstrapEntry', diagnostics_start
    )
    return (
        source[bootstrap_start:bootstrap_end],
        source[runner_start:runner_end],
        source[diagnostics_start:diagnostics_end],
    )


def _bat_template(source: str) -> str:
    marker = "$bat = @'\n"
    start = source.index(marker) + len(marker)
    end = source.index("\n'@\n", start)
    return source[start:end]


def _public_launcher_shell(source: str) -> str:
    marker = "$publicLauncherShell = @'\n"
    start = source.index(marker) + len(marker)
    end = source.index('\n\'@.Replace("__PUBLIC_PROFILE__"', start)
    return source[start:end].replace("__PUBLIC_PROFILE__", "portable")


def _local_integrity_gate(source: str) -> str:
    marker = "$localIntegrityGateCode = @'\n"
    start = source.index(marker) + len(marker)
    end = source.index("\n'@\nfunction Invoke-LocalIntegrityGate", start)
    return source[start:end]


def _loader(source_kind: str):
    if source_kind == "native":
        bootstrap = _native_bootstrap(BOOTSTRAP_SOURCE.read_text(encoding="utf-8"))
        start = bootstrap.index("_MODULE_MISSING = object()")
        end = bootstrap.index("\nkernel = load_exact", start)
        namespace: dict[str, object] = {}
        exec("import importlib.util, sys\n" + bootstrap[start:end], namespace)
        return namespace["load_exact"]

    build = BUILD.read_text(encoding="utf-8-sig")
    if source_kind == "public":
        public, _runner, _diagnostics = _public_fragments(build)
        loader_source = public
        end = loader_source.index("\n_kernel = None")
    else:
        loader_source = _local_integrity_gate(build)
        end = loader_source.index("\ntry:\n    _root")
    namespace = {}
    exec(loader_source[:end], namespace)
    return namespace["_load_exact"]


@pytest.mark.parametrize("source_kind", ("native", "public", "build_gate"))
def test_exact_loader_registers_dataclass_module(source_kind: str, tmp_path: Path) -> None:
    loader = _loader(source_kind)
    name = f"_bootstrap_dataclass_{source_kind}_{uuid.uuid4().hex}"
    module_path = tmp_path / f"{name}.py"
    module_path.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class Record:\n"
        "    value: int\n",
        encoding="utf-8",
    )

    try:
        module = loader(name, module_path)
        assert sys.modules[name] is module
        assert module.Record(7).value == 7
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize("source_kind", ("native", "public", "build_gate"))
@pytest.mark.parametrize("failure", ("missing", "tamper", "wrong_module", "spec"))
def test_exact_loader_failures_restore_or_remove_sys_modules(
    source_kind: str,
    failure: str,
    tmp_path: Path,
) -> None:
    loader = _loader(source_kind)
    name = f"_bootstrap_failure_{source_kind}_{failure}_{uuid.uuid4().hex}"
    previous = object() if failure == "wrong_module" else None
    if previous is not None:
        sys.modules[name] = previous

    if failure == "missing":
        module_path = tmp_path / "missing.py"
        expected = (FileNotFoundError, OSError)
    elif failure == "tamper":
        module_path = tmp_path / "tampered.py"
        module_path.write_text("this is not valid python !\n", encoding="utf-8")
        expected = (SyntaxError,)
    elif failure == "wrong_module":
        module_path = tmp_path / "wrong_module.py"
        module_path.write_text(
            'raise RuntimeError("wrong_module_name")\n', encoding="utf-8"
        )
        expected = (RuntimeError,)
    else:
        module_path = tmp_path / "module.unsupported"
        module_path.write_text("VALUE = 1\n", encoding="utf-8")
        expected = (RuntimeError,)

    try:
        with pytest.raises(expected):
            loader(name, module_path)
        if previous is None:
            assert name not in sys.modules
        else:
            assert sys.modules[name] is previous
    finally:
        if previous is not None and sys.modules.get(name) is previous:
            sys.modules.pop(name)


def test_exact_parent_native_loader_reproduces_dataclass_known_red(
    tmp_path: Path,
) -> None:
    parent = _native_bootstrap(_git_show(f"{PARENT}:native/bidking_trust_bootstrap/src/main.rs"))
    start = parent.index("def load_exact")
    end = parent.index("\nkernel = load_exact", start)
    namespace: dict[str, object] = {}
    exec("import importlib.util, sys\n" + parent[start:end], namespace)
    module_path = tmp_path / "parent_dataclass.py"
    module_path.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class ParentRecord:\n"
        "    value: int\n",
        encoding="utf-8",
    )
    name = f"_parent_dataclass_known_red_{uuid.uuid4().hex}"

    with pytest.raises(AttributeError, match="__dict__"):
        namespace["load_exact"](name, module_path)
    assert name not in sys.modules


def _compile_python_wrapper(output: Path, work: Path) -> None:
    rustc = shutil.which("rustc.exe")
    assert rustc is not None, "rustc.exe is required for the native bootstrap regression"
    python = str(Path(sys.executable).resolve())
    source = work / "python_wrapper.rs"
    source.write_text(
        textwrap.dedent(
            f'''
            use std::ffi::OsString;
            use std::process::Command;

            const SYSTEM_PYTHON: &str = r#"{python}"#;
            const PRELUDE: &str = r#"
import importlib.util as _bidking_util
import pathlib as _bidking_pathlib
import sys as _bidking_sys
_bidking_original_spec = _bidking_util.spec_from_file_location
def _bidking_probe_spec(name, location, *args, **kwargs):
    path = _bidking_pathlib.Path(location)
    if str(path).lower().endswith(".pyd"):
        path = _bidking_pathlib.Path(str(path) + ".py")
    return _bidking_original_spec(name, path, *args, **kwargs)
_bidking_util.spec_from_file_location = _bidking_probe_spec
_bidking_root = None
for _bidking_arg in _bidking_sys.argv[1:]:
    try:
        _bidking_candidate_root = _bidking_pathlib.Path(_bidking_arg)
        if _bidking_candidate_root.is_dir():
            _bidking_root = _bidking_candidate_root
            break
    except OSError:
        pass
if _bidking_root is None:
    raise RuntimeError("probe_root_argument_missing")
_bidking_sys.path[:0] = [str(_bidking_root / "scripts"), str(_bidking_root / "src")]
"#;

            fn main() {{
                let mut args: Vec<OsString> = std::env::args_os().skip(1).collect();
                let code_index = args
                    .iter()
                    .position(|arg| arg == "-c")
                    .map(|index| index + 1)
                    .expect("python wrapper requires -c code");
                let code = args[code_index].to_string_lossy().into_owned();
                args[code_index] = format!("{{PRELUDE}}\n{{code}}").into();
                let status = Command::new(SYSTEM_PYTHON)
                    .args(args)
                    .status()
                    .expect("start system Python");
                std::process::exit(status.code().unwrap_or(1));
            }}
            '''
        ).lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [rustc, str(source), "-O", "-o", str(output)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]


def _build_bootstrap(
    crate: Path,
    target: Path,
    source: str,
    build_script: str,
    public_key_b64: str,
    output: Path,
    *,
    rustflags: tuple[str, ...] = (),
) -> None:
    (crate / "src" / "main.rs").write_text(source, encoding="utf-8", newline="\n")
    (crate / "build.rs").write_text(build_script, encoding="utf-8", newline="\n")
    env = os.environ.copy()
    for name in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"):
        env.pop(name, None)
    if rustflags:
        env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(rustflags)
    env["BIDKING_INTEGRITY_PUBLIC_KEY_B64"] = public_key_b64
    env["CARGO_NET_OFFLINE"] = "true"
    completed = subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--offline",
            "--target-dir",
            str(target),
        ],
        cwd=crate,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert completed.returncode == 0, completed.stderr[-5000:]
    shutil.copy2(target / "release" / "bidking_trust_bootstrap.exe", output)


@pytest.fixture(scope="module")
def bootstrap_probe_build(tmp_path_factory: pytest.TempPathFactory):
    if sys.platform != "win32":
        pytest.skip("real native bootstrap regression is Windows-only")
    if shutil.which("cargo.exe") is None or shutil.which("rustc.exe") is None:
        pytest.skip("Rust toolchain unavailable")

    work = tmp_path_factory.mktemp("public-bootstrap-native")
    crate = work / "native" / "bidking_trust_bootstrap"
    crate.parent.mkdir(parents=True)
    shutil.copytree(BOOTSTRAP_CRATE, crate)
    icon_target = work / "hero_ref" / "assets" / "bidking_cat.ico"
    icon_target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "hero_ref" / "assets" / "bidking_cat.ico", icon_target)
    target = work / "target"
    built = work / "built"
    built.mkdir()

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_key_b64 = base64.b64encode(public_key).decode("ascii")

    parent_source = _git_show(
        f"{PARENT}:native/bidking_trust_bootstrap/src/main.rs"
    )
    manifestless_build = _git_show(
        f"{ROOT_EXE_PARENT}:native/bidking_trust_bootstrap/build.rs"
    )
    _build_bootstrap(
        crate,
        target,
        parent_source,
        manifestless_build,
        public_key_b64,
        built / "parent.exe",
    )
    noninteractive_source = BOOTSTRAP_SOURCE.read_text(encoding="utf-8").replace(
        "if cfg!(debug_assertions) {", "if true {", 1
    )
    _build_bootstrap(
        crate,
        target,
        noninteractive_source,
        manifestless_build,
        public_key_b64,
        built / "source.exe",
    )
    source_build = BOOTSTRAP_BUILD.read_text(encoding="utf-8")
    hardened_flags = (
        "-Ccontrol-flow-guard=checks",
        "-Clink-arg=/GUARD:CF",
        "-Clink-arg=/DYNAMICBASE",
        "-Clink-arg=/NXCOMPAT",
        "-Clink-arg=/HIGHENTROPYVA",
        "-Clink-arg=/OPT:REF",
        "-Clink-arg=/OPT:ICF",
    )
    _build_bootstrap(
        crate,
        target,
        BOOTSTRAP_SOURCE.read_text(encoding="utf-8"),
        source_build,
        public_key_b64,
        built / "manifest.exe",
        rustflags=hardened_flags,
    )
    _build_bootstrap(
        crate,
        target,
        BOOTSTRAP_SOURCE.read_text(encoding="utf-8"),
        source_build.replace("requireAdministrator", "asInvoker"),
        public_key_b64,
        built / "as-invoker.exe",
    )
    _build_bootstrap(
        crate,
        target,
        BOOTSTRAP_SOURCE.read_text(encoding="utf-8"),
        source_build.replace("uiAccess='false'", "uiAccess='true'"),
        public_key_b64,
        built / "ui-access-true.exe",
    )
    _compile_python_wrapper(built / "python.exe", work)
    return work, built, private_key, public_key


def test_real_root_exe_manifest_parent_red_source_is_pre_uac_740(
    bootstrap_probe_build,
    tmp_path: Path,
) -> None:
    _work, built, _private_key, _public_key = bootstrap_probe_build
    contract = _HARDENING.load_contract(NATIVE_CONTRACT_PATH)

    assert _HARDENING._pe_execution_manifest(
        (built / "source.exe").read_bytes()
    ) is None
    source_pe = _HARDENING.parse_pe_hardening(
        (built / "manifest.exe").read_bytes(), inspect_execution_manifest=True
    )
    _HARDENING.verify_bootstrap_execution_manifest(
        source_pe, contract
    )
    _HARDENING.verify_bootstrap_user_experience(source_pe, contract)
    source_manifest = source_pe["execution_manifest"]
    assert source_manifest["requested_execution_level"] == "requireAdministrator"
    assert source_manifest["ui_access"] is False
    assert source_pe["subsystem"] == 2
    assert source_pe["launcher_icon"] == {
        "resource_type": 14,
        "resource_name": 1,
        "resource_language": 1033,
        "codepage": 0,
        "image_count": 7,
        "group_bytes": 104,
        "group_sha256": "893589c5f4cc0ae85e517ce823f6b872c0dea66026c5f98c585a8d47e74369e3",
        "image_payload_sha256": "48d36cdbc76f7ba3be37787fa6d3e71c3fab54bdd54ad0c2c817602261d4be39",
    }

    assert _HARDENING._pe_launcher_icon((built / "source.exe").read_bytes()) is None

    parent_exe = tmp_path / "BidKingLive.exe"
    shutil.copy2(built / "source.exe", parent_exe)
    parent = subprocess.run(
        [str(parent_exe)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert parent.returncode == 1
    assert "bootstrap_file_missing" in parent.stderr

    _HARDENING.verify_bootstrap_pre_uac_createprocess(
        built / "manifest.exe", expected_winerror=740
    )


@pytest.mark.parametrize(
    "name", ("source.exe", "as-invoker.exe", "ui-access-true.exe")
)
def test_real_root_exe_manifest_rejects_missing_asinvoker_or_uiaccess(
    bootstrap_probe_build,
    name: str,
) -> None:
    _work, built, _private_key, _public_key = bootstrap_probe_build
    contract = _HARDENING.load_contract(NATIVE_CONTRACT_PATH)
    manifest = _HARDENING._pe_execution_manifest((built / name).read_bytes())
    with pytest.raises(
        _HARDENING.NativeContractError,
        match="native_bootstrap_execution_manifest_mismatch",
    ):
        _HARDENING.verify_bootstrap_execution_manifest(
            {"execution_manifest": manifest}, contract
        )


def _write_probe_tree(
    root: Path,
    bootstrap: Path,
    python_wrapper: Path,
    private_key: Ed25519PrivateKey,
    public_key: bytes,
) -> None:
    for relative in (
        "integrity",
        "runtime/python",
        "scripts",
        "hero_ref/src",
        "hero_ref/tools",
        "src",
        "data/processed",
        "data/raw/tables",
        "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    shutil.copy2(bootstrap, root / "BidKingLive.exe")
    shutil.copy2(python_wrapper, root / "runtime/python/python.exe")
    (root / "runtime/python/python313.dll").write_bytes(b"probe-python-dll")
    (root / "runtime/python/python313.zip").write_bytes(b"probe-stdlib")
    (root / "runtime/python/python313._pth").write_bytes(b"probe-path\n")
    (root / "activation_service_endpoints.json").write_text("{}\n", encoding="ascii")
    (root / "activation_http_e2ee.json").write_text("{}\n", encoding="ascii")
    (
        root
        / "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
    ).write_text("// probe\n", encoding="ascii")

    kernel = root / "src/bidking_kernel.probe.pyd"
    kernel.write_bytes(b"probe-kernel-preload")
    Path(str(kernel) + ".py").write_text(
        textwrap.dedent(
            '''
            import os

            def verify_local_integrity(root):
                print("PROBE_VERIFY_OK")
                return True, "ok", 1, "probe-manifest"

            def recheck_local_integrity(root):
                print("PROBE_RECHECK_AFTER_GUARD")
                if os.environ.get("BIDKING_TEST_RECHECK") == "fail":
                    return False, "probe_recheck_failclosed"
                return True, "ok"

            def release_local_integrity():
                print("PROBE_RELEASE")
                return True, "ok"
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    launcher = root / "scripts/_client_launcher.probe.pyd"
    launcher.write_bytes(b"probe-launcher-preload")
    Path(str(launcher) + ".py").write_text(
        "def main(args=None):\n"
        "    print('PROBE_EXACT_LAUNCHER_LOAD')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (root / "scripts/_client_launcher.py").write_text(
        "def main():\n"
        "    print('PROBE_PUBLIC_LAUNCH_MAIN')\n"
        "    return 0\n\n"
        "def diagnostics_main():\n"
        "    print('PROBE_PUBLIC_DIAGNOSTICS_MAIN')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (root / "scripts/local_integrity_namespace_guard.py").write_text(
        textwrap.dedent(
            '''
            from __future__ import annotations

            from dataclasses import dataclass

            @dataclass(frozen=True)
            class ProbeSecurityRecord:
                value: int

            print("PROBE_GUARD_DATACLASS_OK")

            class ProbeLease:
                def close(self):
                    print("PROBE_GUARD_LEASE_CLOSE")

            def begin_namespace_lease(root, *, timing_event=None):
                assert ProbeSecurityRecord(7).value == 7
                print("PROBE_GUARD_BEGIN")
                return ProbeLease()
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    fixed = {
        "BidKingLive.exe",
        "activation_service_endpoints.json",
        "activation_http_e2ee.json",
        "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
    }
    trees = (
        "runtime/python",
        "scripts",
        "hero_ref/src",
        "hero_ref/tools",
        "src",
        "data/processed",
        "data/raw/tables",
    )
    members = {root / relative for relative in fixed}
    for tree in trees:
        members.update(path for path in (root / tree).rglob("*") if path.is_file())
    entries = []
    for path in sorted(members, key=lambda item: item.relative_to(root).as_posix()):
        payload = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "role": "bootstrap-probe",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = json.dumps(
        {
            "schema": "bidking_local_integrity_manifest_v1",
            "manifest_version": 1,
            "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
            "release_id": "hotfix3-public-bootstrap-probe",
            "entries": entries,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (root / "integrity/local_integrity_manifest.json").write_bytes(manifest)
    (root / "integrity/local_integrity_manifest.sig").write_bytes(
        base64.b64encode(private_key.sign(manifest))
    )


def _native_run(root: Path, *, recheck: str = "fail") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WINDIR"] = r"C:\__bidking_invalid_windir_no_uac__"
    env["BIDKING_TEST_RECHECK"] = recheck
    return subprocess.run(
        [str(root / "BidKingLive.exe")],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_real_native_bootstrap_parent_red_source_crosses_guard_then_fails_closed(
    bootstrap_probe_build,
    tmp_path: Path,
) -> None:
    _work, built, private_key, public_key = bootstrap_probe_build
    parent_root = tmp_path / "parent"
    source_root = tmp_path / "source"
    _write_probe_tree(
        parent_root,
        built / "parent.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    _write_probe_tree(
        source_root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )

    parent = _native_run(parent_root)
    parent_output = parent.stdout + parent.stderr
    assert parent.returncode == 1
    assert "AttributeError" in parent_output and "__dict__" in parent_output
    assert "PROBE_GUARD_DATACLASS_OK" not in parent_output
    assert "PROBE_RECHECK_AFTER_GUARD" not in parent_output

    source = _native_run(source_root)
    source_output = source.stdout + source.stderr
    assert source.returncode == 1
    assert "PROBE_GUARD_DATACLASS_OK" in source_output
    assert "PROBE_GUARD_BEGIN" in source_output
    assert "PROBE_RECHECK_AFTER_GUARD" in source_output
    assert "probe_recheck_failclosed" in source_output
    assert "PROBE_EXACT_LAUNCHER_LOAD" not in source_output
    assert "PROBE_GUARD_LEASE_CLOSE" in source_output
    assert "PROBE_RELEASE" in source_output


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("missing", "bootstrap_file_missing"),
        ("tamper", "bootstrap_preload_identity_mismatch"),
    ),
)
def test_real_native_bootstrap_preload_negatives_fail_before_python(
    bootstrap_probe_build,
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    _work, built, private_key, public_key = bootstrap_probe_build
    root = tmp_path / mutation
    _write_probe_tree(
        root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    if mutation == "missing":
        (root / "runtime/python/python313.dll").unlink()
    else:
        (root / "src/bidking_kernel.probe.pyd").write_bytes(b"tampered")

    completed = _native_run(root)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert expected in output
    assert "PROBE_VERIFY_OK" not in output


def _powershell_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_public_harness(path: Path, *, mode: str) -> None:
    bootstrap, runner, diagnostics = _public_fragments(
        BUILD.read_text(encoding="utf-8-sig")
    )
    suffix = runner if mode == "launch" else diagnostics
    variable = "$runnerCode" if mode == "launch" else "$diagnosticsCode"
    body = (
        'param([string]$PythonPath = "", [string]$DiagnosticProfile = "portable")\n'
        '$ErrorActionPreference = "Stop"\n'
        "$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)\n"
        '$integrityBootstrap = @"\n'
        + bootstrap
        + '\n"@\n'
        + variable
        + ' = $integrityBootstrap + "`n" + @"\n'
        + suffix
        + '\n"@\n'
        + "$pythonBootstrapEntry = \"import base64,sys;exec(compile(base64.b64decode(sys.argv.pop(1),validate=True),'<bidking-public-bootstrap>','exec'))\"\n"
        + f"$payload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes({variable}))\n"
        + "& $PythonPath -E -s -S -B -c $pythonBootstrapEntry $payload $Repo\n"
        + "exit $LASTEXITCODE\n"
    )
    path.write_text(body, encoding="utf-8-sig")


@pytest.mark.parametrize("host", (WINDOWS_POWERSHELL, PWSH))
@pytest.mark.parametrize(
    ("mode", "marker"),
    (
        ("launch", "PROBE_PUBLIC_LAUNCH_MAIN"),
        ("diagnostics", "PROBE_PUBLIC_DIAGNOSTICS_MAIN"),
    ),
)
def test_generated_public_powershell_paths_cross_dataclass_guard(
    bootstrap_probe_build,
    tmp_path: Path,
    host: Path,
    mode: str,
    marker: str,
) -> None:
    if not host.is_file():
        pytest.skip(f"PowerShell host unavailable: {host}")
    _work, built, private_key, public_key = bootstrap_probe_build
    root = tmp_path / f"public-{mode}"
    _write_probe_tree(
        root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    harness = root / "scripts" / (
        "start_live_npcap_overlay.ps1"
        if mode == "launch"
        else "collect_diagnostics.ps1"
    )
    _write_public_harness(harness, mode=mode)
    env = os.environ.copy()
    env["WINDIR"] = r"C:\__bidking_invalid_windir_no_uac__"
    env["BIDKING_TEST_RECHECK"] = "ok"
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-PythonPath",
            str(built / "python.exe"),
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "PROBE_GUARD_DATACLASS_OK" in output
    assert "PROBE_GUARD_BEGIN" in output
    assert "PROBE_RECHECK_AFTER_GUARD" in output
    assert marker in output
    assert "PROBE_GUARD_LEASE_CLOSE" in output
    assert "PROBE_RELEASE" in output


@pytest.mark.parametrize("host", (WINDOWS_POWERSHELL, PWSH))
def test_generated_public_launcher_reaches_controlled_uac_sentinel(
    bootstrap_probe_build,
    tmp_path: Path,
    host: Path,
) -> None:
    if not host.is_file():
        pytest.skip(f"PowerShell host unavailable: {host}")
    _work, built, private_key, public_key = bootstrap_probe_build
    root = tmp_path / "public-uac"
    _write_probe_tree(
        root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    public_launcher = _public_launcher_shell(BUILD.read_text(encoding="utf-8-sig"))
    admin_branch = ")\nif (-not $IsAdmin) {"
    assert public_launcher.count(admin_branch) == 1
    public_launcher = public_launcher.replace(
        admin_branch,
        ")\n$IsAdmin = $false\nif (-not $IsAdmin) {",
        1,
    )
    launcher = root / "scripts/start_live_npcap_overlay.ps1"
    launcher.write_text(public_launcher, encoding="utf-8-sig")
    sentinel = root / "scripts/uac_sentinel.ps1"
    sentinel.write_text(
        "param([string]$Launcher, [string]$PythonPath)\n"
        "function Start-Process {\n"
        "  param($FilePath, $Verb, $WindowStyle, $WorkingDirectory, $ArgumentList)\n"
        "  Write-Output 'PROBE_CONTROLLED_UAC_SENTINEL'\n"
        "}\n"
        "& $Launcher -PythonPath $PythonPath\n",
        encoding="utf-8-sig",
    )
    env = os.environ.copy()
    env["WINDIR"] = r"C:\__bidking_invalid_windir_no_uac__"
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(sentinel),
            "-Launcher",
            str(launcher),
            "-PythonPath",
            str(built / "python.exe"),
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "PROBE_CONTROLLED_UAC_SENTINEL" in output
    assert "PROBE_GUARD_DATACLASS_OK" not in output


@pytest.mark.parametrize("host", (WINDOWS_POWERSHELL, PWSH))
@pytest.mark.parametrize("mutation", ("missing", "tamper", "exec"))
def test_generated_public_launcher_guard_negatives_fail_closed(
    bootstrap_probe_build,
    tmp_path: Path,
    host: Path,
    mutation: str,
) -> None:
    if not host.is_file():
        pytest.skip(f"PowerShell host unavailable: {host}")
    _work, built, private_key, public_key = bootstrap_probe_build
    root = tmp_path / f"public-guard-{mutation}"
    _write_probe_tree(
        root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    guard = root / "scripts/local_integrity_namespace_guard.py"
    if mutation == "missing":
        guard.unlink()
    elif mutation == "tamper":
        guard.write_text("this is not valid python !\n", encoding="utf-8")
    else:
        guard.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class ExecFailureRecord:\n"
            "    value: int\n"
            "assert ExecFailureRecord(3).value == 3\n"
            "raise RuntimeError('probe_guard_exec_failure')\n",
            encoding="utf-8",
        )
    harness = root / "scripts/start_live_npcap_overlay.ps1"
    _write_public_harness(harness, mode="launch")
    env = os.environ.copy()
    env["WINDIR"] = r"C:\__bidking_invalid_windir_no_uac__"
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-PythonPath",
            str(built / "python.exe"),
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "PROBE_PUBLIC_LAUNCH_MAIN" not in output
    assert "PROBE_RECHECK_AFTER_GUARD" not in output
    if mutation != "missing":
        assert "BIDKING_LOCAL_INTEGRITY=bootstrap_failed" in output


def _run_bat(root: Path, bat: Path, host: Path, shim: Path) -> subprocess.CompletedProcess[str]:
    shim.mkdir(parents=True, exist_ok=True)
    (shim / "powershell.cmd").write_text(
        f'@echo off\r\n"{host}" %*\r\n', encoding="ascii", newline=""
    )
    env = os.environ.copy()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    env["WINDIR"] = r"C:\__bidking_invalid_windir_no_uac__"
    env["BIDKING_TEST_RECHECK"] = "ok"
    return subprocess.run(
        [r"C:\Windows\System32\cmd.exe", "/d", "/c", "call start.bat"],
        cwd=bat.parent,
        env=env,
        input="\n",
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_parent_bat_bom_lf_known_red_and_source_crlf_runs_winps_and_pwsh(
    bootstrap_probe_build,
    tmp_path: Path,
) -> None:
    hosts = [host for host in (WINDOWS_POWERSHELL, PWSH) if host.is_file()]
    assert hosts, "at least one PowerShell host is required"
    _work, built, private_key, public_key = bootstrap_probe_build
    root = tmp_path / "bat-package"
    _write_probe_tree(
        root,
        built / "source.exe",
        built / "python.exe",
        private_key,
        public_key,
    )
    harness = root / "scripts/start_live_npcap_overlay.ps1"
    _write_public_harness(harness, mode="launch")
    bat_dir = root / "其他启动方式"
    bat_dir.mkdir()
    bat = bat_dir / "start.bat"

    parent_template = _bat_template(_git_show(f"{PARENT}:scripts/build_live_portable.ps1"))
    parent_payload = parent_template.encode("utf-8-sig")
    assert parent_payload.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in parent_payload
    bat.write_bytes(parent_payload)
    parent = _run_bat(root, bat, hosts[0], tmp_path / "parent-shim")
    parent_output = parent.stdout + parent.stderr
    assert parent.returncode != 0
    assert "PROBE_GUARD_DATACLASS_OK" not in parent_output

    source = BUILD.read_text(encoding="utf-8-sig")
    source_template = _bat_template(source)
    source_payload = source_template.replace("\r\n", "\n").replace(
        "\n", "\r\n"
    ).encode("utf-8")
    assert not source_payload.startswith(b"\xef\xbb\xbf")
    assert source_payload.isascii()
    assert all(
        index > 0 and source_payload[index - 1] == 0x0D
        for index, byte in enumerate(source_payload)
        if byte == 0x0A
    )
    bat.write_bytes(source_payload)

    for index, host in enumerate(hosts):
        completed = _run_bat(root, bat, host, tmp_path / f"source-shim-{index}")
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
        assert "PROBE_GUARD_DATACLASS_OK" in output
        assert "PROBE_PUBLIC_LAUNCH_MAIN" in output


def test_source_keeps_native_preload_identity_and_bat_encoding_contracts() -> None:
    native = BOOTSTRAP_SOURCE.read_text(encoding="utf-8")
    native_build = BOOTSTRAP_BUILD.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8-sig")
    assert ".share_mode(FILE_SHARE_READ)" in native
    assert "verify_preload_roots(root, &executable)?" in native
    assert "kernel.recheck_local_integrity(root)" in native
    assert "sys.modules[name] = module" in native
    assert "sys.modules.pop(name, None)" in native
    assert "sys.modules[name] = previous" in native
    assert (
        "cargo:rustc-link-arg-bin=bidking_trust_bootstrap=/MANIFEST:EMBED"
        in native_build
    )
    assert (
        "cargo:rustc-link-arg-bin=bidking_trust_bootstrap=/MANIFESTUAC:"
        "level='requireAdministrator' uiAccess='false'"
        in native_build
    )
    assert 'windows_subsystem = "windows"' in native
    assert "embed_windows_icon()" in native_build
    assert "bidking_cat.ico" in native_build
    assert "startup_failure.log" in native
    assert "bidking_public_startup_failure_v1" in native
    assert "MessageBoxW(" in native
    assert "_kernel = _load_exact(\"bidking_kernel\"" in build
    assert "_guard = _load_exact(" in build
    assert '$runnerCode = $integrityBootstrap + "`n" + @"' in build
    assert "base64.b64decode(sys.argv.pop(1),validate=True)" in build
    assert '$bat = $bat.Replace("`r`n", "`n").Replace("`n", "`r`n")' in build
    assert "[System.Text.UTF8Encoding]::new($false).GetBytes($bat)" in build
    assert '[System.IO.File]::WriteAllBytes((Join-Path $subDir "启动.bat")' in build
