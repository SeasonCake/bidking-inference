from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import local_compat_launcher  # noqa: E402
from prepare_client_lifecycle_payload import (  # noqa: E402
    LifecyclePayloadError,
    _PROCESS_DISCOVERY_BLOCK,
    _REPAIR_DISCOVERY_BLOCK,
    lifecycle_generation,
    prepare_lifecycle_payloads,
)


GENERATION = "a" * 64
WRAPPER_BUG_PARENT = "acf7d38f38df0b7174df28c63b664b2115f34553"
WINDOWS_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
LIVE_STARTUP = SCRIPTS / "start_live_npcap_overlay.ps1"


class FakeNativeLifecycle:
    def __init__(self) -> None:
        self.abi = 2
        self.generation = GENERATION
        self.caller = "_client_launcher"
        self.state = "idle"
        self.cross_process_busy = False
        self.payload = bytearray(b"private lifecycle payload")
        self.payload_hash = hashlib.sha256(self.payload).digest()
        self.calls: list[dict[str, object]] = []

    def client_lifecycle_contract(self):
        return (self.abi, self.generation, True)

    def run_client_lifecycle(self, **kwargs):
        if (
            kwargs["abi"] != self.abi
            or kwargs["generation"] != self.generation
            or kwargs["caller"] != self.caller
            or kwargs["mode"] not in {"launch", "diagnostics", "repair"}
        ):
            raise RuntimeError("client_lifecycle_request_rejected")
        if self.state != "idle" or self.cross_process_busy:
            raise RuntimeError("client_lifecycle_busy")
        if hashlib.sha256(self.payload).digest() != self.payload_hash:
            raise RuntimeError("client_lifecycle_payload_invalid")
        self.state = "running"
        try:
            self.calls.append(dict(kwargs))
            return 0
        finally:
            self.state = "idle"


def _install_fake(monkeypatch, fake: FakeNativeLifecycle) -> None:
    monkeypatch.setattr(local_compat_launcher, "_LIFECYCLE_GENERATION", GENERATION)
    monkeypatch.setitem(sys.modules, "bidking_kernel", fake)


def test_native_lifecycle_normal_launch_and_diagnostics(monkeypatch) -> None:
    fake = FakeNativeLifecycle()
    _install_fake(monkeypatch, fake)

    assert (
        local_compat_launcher.main(
            [
                "--python-path",
                sys.executable,
                "--diagnostic-profile",
                "stable",
                "--restart",
                "--no-taskbar",
            ]
        )
        == 0
    )
    assert local_compat_launcher.diagnostics_main() == 0
    assert local_compat_launcher.diagnostics_receipt_main() == (0, "ok")
    assert local_compat_launcher.repair_main(["--dry-run"]) == 0
    assert [call["mode"] for call in fake.calls] == [
        "launch",
        "diagnostics",
        "diagnostics",
        "repair",
    ]
    assert fake.calls[-1]["repair_dry_run"] is True
    assert all("source" not in call and "env" not in call for call in fake.calls)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("_LIFECYCLE_ABI", 0),
        ("_LIFECYCLE_GENERATION", "b" * 64),
        ("_LIFECYCLE_CALLER", "foreign_launcher"),
    ],
)
def test_native_lifecycle_rejects_malformed_abi_old_generation_and_wrong_caller(
    monkeypatch,
    capsys,
    attribute: str,
    value: object,
) -> None:
    fake = FakeNativeLifecycle()
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr(local_compat_launcher, attribute, value)

    assert local_compat_launcher.main([]) == 1
    assert fake.calls == []
    assert capsys.readouterr().err.strip() == local_compat_launcher._SAFE_FAILURE


@pytest.mark.parametrize("state", ["running", "complete"])
def test_native_lifecycle_rejects_duplicate_or_wrong_order(
    monkeypatch,
    state: str,
) -> None:
    fake = FakeNativeLifecycle()
    fake.state = state
    _install_fake(monkeypatch, fake)

    assert local_compat_launcher.diagnostics_main() == 1
    assert fake.calls == []


def test_native_lifecycle_treats_cross_process_busy_launch_as_idempotent_success(
    monkeypatch,
    capsys,
) -> None:
    fake = FakeNativeLifecycle()
    fake.cross_process_busy = True
    _install_fake(monkeypatch, fake)

    assert local_compat_launcher.main([]) == 0
    assert fake.calls == []
    assert capsys.readouterr().err == ""


def test_native_lifecycle_shared_mutation_busy_blocks_repair_but_launch_is_idempotent(
    monkeypatch,
    capsys,
) -> None:
    fake = FakeNativeLifecycle()
    fake.cross_process_busy = True
    _install_fake(monkeypatch, fake)

    assert local_compat_launcher.repair_main(["--dry-run"]) == 1
    assert fake.calls == []
    assert capsys.readouterr().err.strip() == local_compat_launcher._SAFE_FAILURE
    assert local_compat_launcher.main([]) == 0
    assert fake.calls == []


def test_native_lifecycle_rejects_payload_bitflip(monkeypatch) -> None:
    fake = FakeNativeLifecycle()
    fake.payload[5] ^= 0x40
    _install_fake(monkeypatch, fake)

    assert local_compat_launcher.diagnostics_main() == 1
    assert fake.calls == []


def test_native_lifecycle_error_projection_never_leaks_details(
    monkeypatch,
    capsys,
) -> None:
    class LeakyFailure(FakeNativeLifecycle):
        def run_client_lifecycle(self, **kwargs):
            raise RuntimeError(
                "C:\\private\\payload.ps1 token=secret internal-script-text"
            )

    _install_fake(monkeypatch, LeakyFailure())
    assert local_compat_launcher.main([]) == 1
    error = capsys.readouterr().err
    assert error.strip() == local_compat_launcher._SAFE_FAILURE
    for forbidden in ("private", "payload.ps1", "token=", "internal-script"):
        assert forbidden not in error


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (RuntimeError("client_lifecycle_payload_invalid"), "client_lifecycle_payload_invalid"),
        (PermissionError("C:\\private\\user"), "native_access_denied"),
        (ValueError("token=secret C:\\private\\user"), "native_exception"),
    ],
)
def test_diagnostic_receipt_projects_only_fixed_safe_native_exceptions(
    monkeypatch,
    capsys,
    failure: Exception,
    expected: str,
) -> None:
    class FailedNative(FakeNativeLifecycle):
        def run_client_lifecycle(self, **kwargs):
            raise failure

    _install_fake(monkeypatch, FailedNative())
    assert local_compat_launcher.diagnostics_receipt_main() == (1, expected)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (0, (0, "ok")),
        (47, (47, "native_status_nonzero")),
        (True, (1, "native_status_invalid")),
        ("0", (1, "native_status_invalid")),
    ],
)
def test_diagnostic_receipt_validates_exact_native_status(
    monkeypatch,
    capsys,
    status: object,
    expected: tuple[int, str],
) -> None:
    class StatusNative(FakeNativeLifecycle):
        def run_client_lifecycle(self, **kwargs):
            return status

    _install_fake(monkeypatch, StatusNative())
    assert local_compat_launcher.diagnostics_receipt_main() == expected
    assert capsys.readouterr().err == ""


def test_diagnostic_receipt_reason_normalizer_is_exact_bounded_and_allowlisted() -> None:
    assert local_compat_launcher._fixed_reason("native_status_nonzero") == "native_status_nonzero"
    for invalid in (True, 1, "NATIVE_STATUS_NONZERO", "x" * 65, "token=secret"):
        assert local_compat_launcher._fixed_reason(invalid) == "unknown"


def test_runtime_reason_protocol_requires_exact_builtin_single_string_arg(
    monkeypatch,
    capsys,
) -> None:
    class ForgedReason:
        def __str__(self) -> str:
            return "client_lifecycle_busy"

    class ExplodingReason:
        def __str__(self) -> str:
            raise ValueError("C:\\private\\User token=needle")

    class RuntimeSub(RuntimeError):
        pass

    class MissingArgsRuntime(RuntimeError):
        def __getattribute__(self, name: str):
            if name == "args":
                raise AttributeError("args unavailable")
            return super().__getattribute__(name)

    class StrSub(str):
        pass

    class TupleSub(tuple):
        pass

    malformed: list[BaseException] = [
        RuntimeError(),
        RuntimeError("client_lifecycle_busy", "extra"),
        RuntimeError(b"client_lifecycle_busy"),
        RuntimeError(True),
        RuntimeError(1),
        RuntimeError(StrSub("client_lifecycle_busy")),
        RuntimeError(ForgedReason()),
        RuntimeError(ExplodingReason()),
        RuntimeSub("client_lifecycle_busy"),
        MissingArgsRuntime("client_lifecycle_busy"),
        Exception("client_lifecycle_busy"),
        BaseException(ExplodingReason()),
    ]
    list_args = RuntimeError("client_lifecycle_busy")
    list_args.args = [True]
    malformed.append(list_args)
    tuple_sub_args = RuntimeError("client_lifecycle_busy")
    tuple_sub_args.args = TupleSub((StrSub("client_lifecycle_busy"),))
    malformed.append(tuple_sub_args)

    for failure in malformed:
        assert local_compat_launcher._exception_reason(failure) == "native_exception"

    assert (
        local_compat_launcher._exception_reason(
            RuntimeError("client_lifecycle_payload_invalid")
        )
        == "client_lifecycle_payload_invalid"
    )

    # CPython's BaseException.args setter normalizes a list/tuple subclass container
    # to an exact tuple.  Once its sole member is also an exact str, the result is
    # observably identical to the allowed builtin protocol; no reassignment history
    # remains for product code to inspect.
    normalized_args = RuntimeError("placeholder")
    normalized_args.args = ["client_lifecycle_busy"]
    assert type(normalized_args.args) is tuple
    assert type(normalized_args.args[0]) is str
    assert (
        local_compat_launcher._exception_reason(normalized_args)
        == "client_lifecycle_busy"
    )

    class FailedNative(FakeNativeLifecycle):
        def run_client_lifecycle(self, **kwargs):
            raise RuntimeError(ForgedReason())

    _install_fake(monkeypatch, FailedNative())
    assert local_compat_launcher.diagnostics_receipt_main() == (1, "native_exception")
    assert local_compat_launcher.main([]) == 1
    error = capsys.readouterr().err
    assert error == local_compat_launcher._SAFE_FAILURE + "\n"
    for forbidden in ("private", "User", "token=", "needle", "busy"):
        assert forbidden not in error


def test_exception_reason_classification_never_calls_unknown_string_protocol() -> None:
    class ProtocolTrap:
        def __str__(self) -> str:
            raise AssertionError("string protocol must not run")

        def __repr__(self) -> str:
            raise AssertionError("repr protocol must not run")

        def __format__(self, _spec: str) -> str:
            raise AssertionError("format protocol must not run")

    for failure in (
        RuntimeError(ProtocolTrap()),
        Exception(ProtocolTrap()),
        BaseException(ProtocolTrap()),
    ):
        assert local_compat_launcher._exception_reason(failure) == "native_exception"


def test_payload_preparer_binds_exact_launcher_diagnostics_and_generation(
    tmp_path: Path,
) -> None:
    startup = tmp_path / "start.ps1"
    discovery = tmp_path / "discovery.ps1"
    diagnostics = tmp_path / "diagnostics.ps1"
    repair = tmp_path / "repair.ps1"
    output = tmp_path / "out"
    startup.write_text(
        "param()\n" + _PROCESS_DISCOVERY_BLOCK + "Write-Output 'start'\n",
        encoding="utf-8",
    )
    discovery.write_text("function Get-Live { return 1 }\n", encoding="utf-8")
    diagnostics.write_text("Write-Output 'safe diagnostic'\n", encoding="utf-8")
    repair.write_text(
        _REPAIR_DISCOVERY_BLOCK + "Write-Output 'safe repair'\n",
        encoding="utf-8",
    )

    receipt = prepare_lifecycle_payloads(
        startup_path=startup,
        process_discovery_path=discovery,
        diagnostics_path=diagnostics,
        repair_path=repair,
        output_dir=output,
    )
    launcher = (output / "launcher.payload").read_bytes()
    diagnostic_payload = (output / "diagnostics.payload").read_bytes()
    repair_payload = (output / "repair.payload").read_bytes()
    persisted = json.loads((output / "receipt.json").read_text(encoding="ascii"))

    assert b"function Get-Live" in launcher
    assert _PROCESS_DISCOVERY_BLOCK.encode("utf-8") not in launcher
    assert b"function Get-Live" in repair_payload
    assert _REPAIR_DISCOVERY_BLOCK.encode("utf-8") not in repair_payload
    assert receipt == persisted
    assert receipt["generation"] == lifecycle_generation(
        launcher, diagnostic_payload, repair_payload
    )
    assert receipt["launcher"] == {
        "bytes": len(launcher),
        "sha256": hashlib.sha256(launcher).hexdigest(),
    }


def test_payload_preparer_rejects_missing_or_duplicate_insertion_point(
    tmp_path: Path,
) -> None:
    startup = tmp_path / "start.ps1"
    discovery = tmp_path / "discovery.ps1"
    diagnostics = tmp_path / "diagnostics.ps1"
    repair = tmp_path / "repair.ps1"
    discovery.write_text("Get-Process\n", encoding="utf-8")
    diagnostics.write_text("Write-Output ok\n", encoding="utf-8")
    repair.write_text(_REPAIR_DISCOVERY_BLOCK, encoding="utf-8")
    for source in ("Write-Output no-marker\n", _PROCESS_DISCOVERY_BLOCK * 2):
        startup.write_text(source, encoding="utf-8")
        with pytest.raises(LifecyclePayloadError):
            prepare_lifecycle_payloads(
                startup_path=startup,
                process_discovery_path=discovery,
                diagnostics_path=diagnostics,
                repair_path=repair,
                output_dir=tmp_path / "out",
            )


def test_payload_generation_changes_on_launcher_or_diagnostics_bitflip() -> None:
    launcher = b"launcher"
    diagnostics = b"diagnostics"
    repair = b"repair"
    baseline = lifecycle_generation(launcher, diagnostics, repair)
    assert lifecycle_generation(b"launchdr", diagnostics, repair) != baseline
    assert lifecycle_generation(launcher, b"diagnosthcs", repair) != baseline
    assert lifecycle_generation(launcher, diagnostics, b"repais") != baseline


def _native_lifecycle_powershell_fragments() -> tuple[bytes, bytes, str]:
    source = (
        ROOT / "native" / "bidking_kernel" / "src" / "client_lifecycle.rs"
    ).read_text(encoding="utf-8")
    prefix_match = re.search(
        r"const LAUNCH_WRAPPER_PREFIX: &\[u8\] =\s*b(\"(?:\\.|[^\"])*\");",
        source,
    )
    suffix_match = re.search(
        r'const LAUNCH_WRAPPER_SUFFIX: &\[u8\] = br#\"(.*?)\"#;',
        source,
        re.DOTALL,
    )
    bootstrap_match = re.search(
        r'const POWERSHELL_STDIN_BOOTSTRAP: &str = r#\"(.*?)\"#;',
        source,
        re.DOTALL,
    )
    assert prefix_match and suffix_match and bootstrap_match
    prefix = ast.literal_eval("b" + prefix_match.group(1))
    return prefix, suffix_match.group(1).encode("utf-8"), bootstrap_match.group(1)


@pytest.mark.parametrize(
    "host",
    [host for host in (WINDOWS_POWERSHELL, PWSH) if host.is_file()],
    ids=lambda host: host.name,
)
def test_real_powershell_launch_wrapper_preserves_base64_string_and_parameters(
    host: Path,
    tmp_path: Path,
) -> None:
    parent = subprocess.run(
        [
            "git",
            "show",
            f"{WRAPPER_BUG_PARENT}:native/bidking_kernel/src/client_lifecycle.rs",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert '$sourceBytes = [Convert]::FromBase64String("#;' in parent

    prefix, suffix, bootstrap = _native_lifecycle_powershell_fragments()
    assert prefix.endswith(b'FromBase64String("')
    payload = b"""param(
  [string]$PythonPath,
  [string]$LogDir,
  [string]$DiagnosticProfile,
  [switch]$NoAutoElevate,
  [switch]$Restart,
  [switch]$NoTaskbar
)
$value = \"$PythonPath|$LogDir|$DiagnosticProfile|$NoAutoElevate|$Restart|$NoTaskbar\"
[IO.File]::WriteAllText($env:BIDKING_LIFECYCLE_TEST_MARKER, $value)
"""
    wrapper = prefix + base64.b64encode(payload) + suffix
    encoded_source = base64.b64encode(wrapper)
    encoded_command = base64.b64encode(bootstrap.encode("utf-16le")).decode("ascii")
    marker = tmp_path / f"{host.stem}.txt"
    env = os.environ.copy()
    env.update(
        {
            "BIDKING_EMBED_PYTHON": "runtime-python",
            "BIDKING_EMBED_LOG_DIR": "runtime-log",
            "BIDKING_EMBED_DIAGNOSTIC_PROFILE": "portable",
            "BIDKING_EMBED_RESTART": "1",
            "BIDKING_EMBED_NO_TASKBAR": "1",
            "BIDKING_LIFECYCLE_TEST_MARKER": str(marker),
        }
    )
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_command,
        ],
        input=encoded_source,
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    assert marker.read_text(encoding="utf-8") == (
        "runtime-python|runtime-log|portable|True|True|True"
    )


@pytest.mark.parametrize(
    "host",
    [host for host in (WINDOWS_POWERSHELL, PWSH) if host.is_file()],
    ids=lambda host: host.name,
)
@pytest.mark.parametrize("root_source", ["absent", "extended"])
def test_in_memory_lifecycle_source_normalizes_locked_package_root(
    host: Path,
    tmp_path: Path,
    root_source: str,
) -> None:
    source = LIVE_STARTUP.read_text(encoding="utf-8-sig")
    preamble, separator, _tail = source.partition(
        '$NpcapMonitor = Join-Path $Repo "scripts\\run_npcap_live_monitor.py"'
    )
    assert separator
    probe = preamble + '\n[Console]::Out.Write($Repo)\n'
    encoded_source = base64.b64encode(probe.encode("utf-8")).decode("ascii")
    bootstrap = (
        "$source = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded_source}'))\n"
        "$entry = [ScriptBlock]::Create($source)\n"
        "Set-Location Env:\n"
        "& $entry\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() != "bidking_package_root"
    }
    if root_source == "extended":
        env["BIDKING_PACKAGE_ROOT"] = "\\\\?\\" + str(tmp_path)
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            bootstrap,
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout).resolve() == tmp_path.resolve()


@pytest.mark.parametrize(
    "host",
    [host for host in (WINDOWS_POWERSHELL, PWSH) if host.is_file()],
    ids=lambda host: host.name,
)
def test_in_memory_lifecycle_source_normalizes_all_native_extended_paths(
    host: Path,
    tmp_path: Path,
) -> None:
    source = LIVE_STARTUP.read_text(encoding="utf-8-sig")
    preamble, separator, _tail = source.partition(
        '$Snapshot = Join-Path $LogPath "latest_snapshot.json"'
    )
    assert separator
    probe = preamble + (
        '\n@($Repo, $PythonPath, $LogPath) | ConvertTo-Json -Compress\n'
    )
    encoded_source = base64.b64encode(probe.encode("utf-8")).decode("ascii")
    extended_root = "\\\\?\\" + str(tmp_path)
    python_path = str(tmp_path / "runtime" / "python" / "python.exe")
    log_path = str(tmp_path / "logs" / "live")
    bootstrap = (
        "$source = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded_source}'))\n"
        "$entry = [ScriptBlock]::Create($source)\n"
        "Set-Location Env:\n"
        f"$params = @{{ PythonPath = '{extended_root}\\runtime\\python\\python.exe'; "
        f"LogDir = '{extended_root}\\logs\\live' }}\n"
        "& $entry @params\n"
    )
    env = os.environ.copy()
    env["BIDKING_PACKAGE_ROOT"] = extended_root
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            bootstrap,
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        str(tmp_path),
        python_path,
        log_path,
    ]


@pytest.mark.parametrize(
    "host",
    [host for host in (WINDOWS_POWERSHELL, PWSH) if host.is_file()],
    ids=lambda host: host.name,
)
def test_native_extended_path_normalizer_preserves_public_abi_shapes(
    host: Path,
) -> None:
    source = LIVE_STARTUP.read_text(encoding="utf-8-sig")
    preamble, separator, _tail = source.partition("$Repo = if")
    assert separator
    probe = preamble + r'''
$values = @(
  (ConvertFrom-WindowsExtendedPath ''),
  (ConvertFrom-WindowsExtendedPath 'C:\Bid King\package'),
  (ConvertFrom-WindowsExtendedPath '\\?\C:\Bid King\package'),
  (ConvertFrom-WindowsExtendedPath '\\?\UNC\server\share\Bid King')
)
ConvertTo-Json -Compress -InputObject $values
'''
    completed = subprocess.run(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            probe,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "",
        r"C:\Bid King\package",
        r"C:\Bid King\package",
        r"\\server\share\Bid King",
    ]


def test_public_python_abi_contains_no_private_payload_or_generic_transport() -> None:
    source = (SCRIPTS / "local_compat_launcher.py").read_text(encoding="utf-8")
    for forbidden in (
        "_POWERSHELL_PAYLOAD_B64",
        "_DIAGNOSTICS_PAYLOAD_B64",
        "_REPAIR_PAYLOAD_B64",
        "def _decode_payload",
        "def _run_powershell_source",
        "import gzip",
        "import base64",
        "import subprocess",
    ):
        assert forbidden not in source
    assert "run_client_lifecycle" in source
    assert "client_lifecycle_contract" in source


def test_rust_helper_owns_integrity_state_and_cross_process_gates() -> None:
    lifecycle = (
        ROOT / "native" / "bidking_kernel" / "src" / "client_lifecycle.rs"
    ).read_text(encoding="utf-8")
    build_rs = (ROOT / "native" / "bidking_kernel" / "build.rs").read_text(
        encoding="utf-8"
    )
    kernel = (
        ROOT / "native" / "bidking_kernel" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    for required in (
        "validate_contract(abi, generation, caller)",
        "LifecycleState::Running(mode)",
        "CreateMutexW",
        "ERROR_ALREADY_EXISTS",
        'Self::Launch | Self::Repair => "launch"',
        "actual_hash != expected_hash",
        "CLIENT_LIFECYCLE_GENERATION",
        "command.creation_flags(CREATE_NO_WINDOW)",
        "child.wait()",
    ):
        assert required in lifecycle
    assert "pub(crate) fn run_client_lifecycle" in lifecycle
    assert not re.search(r"pub\(crate\) fn .*payload.*->", lifecycle)
    assert "client lifecycle build inputs incomplete" in build_rs
    assert "client lifecycle generation mismatch" in build_rs
    assert "seal_payload" in build_rs
    assert 'join("embedded_key.rs")' in build_rs
    assert 'join("embedded_lifecycle.rs")' in build_rs
    for exported in (
        "verify_local_integrity",
        "recheck_local_integrity",
        "release_local_integrity",
        "client_lifecycle_contract",
        "run_client_lifecycle",
    ):
        assert f"wrap_pyfunction!({exported}, m)" in kernel


def test_build_routes_private_sources_only_into_native_helper() -> None:
    build = (SCRIPTS / "build_live_portable.ps1").read_text(encoding="utf-8-sig")

    assert "prepare_client_lifecycle_payload.py" in build
    assert "BIDKING_LIFECYCLE_LAUNCHER_FILE" in build
    assert "BIDKING_LIFECYCLE_DIAGNOSTICS_FILE" in build
    assert "BIDKING_LIFECYCLE_REPAIR_FILE" in build
    assert "BIDKING_LIFECYCLE_GENERATION" in build
    assert "native lifecycle 合同与公版诊断入口实跑通过" in build
    assert "client launcher 残留可逆 lifecycle payload 接口" in build
    assert "client lifecycle native helper 合同不完整" in build
    assert "_launcher_candidate_smoke.py" not in build
    assert "from _client_launcher import _run_powershell_source" not in build
    assert "ConvertTo-EmbeddedGzipPayload" not in build
