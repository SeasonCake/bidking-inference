"""Build and run only self-owned Windows fixtures; outputs remain outside source.

Requires an installed MSVC x64 toolchain and .NET Framework compiler. No download,
game process, administrator rights, symbol server or global environment change.
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from research.records.events import EpochConsumer  # noqa: E402
from research.native_lab.pipe_session import PipeSession  # noqa: E402


def run_command(
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int = 30,
    expected: tuple[int, ...] = (0,),
) -> dict:
    started = time.perf_counter_ns()
    value = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if len(value.stdout) + len(value.stderr) > 2 * 1024 * 1024:
        raise ValueError("Fixture output exceeded its 2 MiB bound")
    result = {
        "exit_code": value.returncode,
        "elapsed_ms": (time.perf_counter_ns() - started) / 1e6,
        "stdout": value.stdout.decode("utf-8", "replace"),
        "stderr": value.stderr.decode("utf-8", "replace"),
    }
    if value.returncode not in expected:
        raise RuntimeError(
            f'Fixture command failed ({value.returncode}): {result["stderr"][-1600:]} {result["stdout"][-1600:]}'
        )
    return result


def compiler_environment(vcvars: Path | None) -> dict[str, str]:
    if os.name != "nt":
        raise RuntimeError("This native identity runner requires Windows")
    if vcvars is None:
        vswhere = (
            Path(os.environ.get("ProgramFiles(x86)", ""))
            / "Microsoft Visual Studio/Installer/vswhere.exe"
        )
        if not vswhere.is_file():
            raise FileNotFoundError("Supply --vcvars with an installed vcvars64.bat")
        found = subprocess.run(
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
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        vcvars = Path(found.stdout.strip()) / "VC/Auxiliary/Build/vcvars64.bat"
    vcvars = vcvars.resolve()
    if not vcvars.is_file() or any(ch in str(vcvars) for ch in '&|<>\r\n"%'):
        raise ValueError(
            "Choose a literal vcvars64.bat path without shell metacharacters"
        )
    # The batch initializes only this child environment. Do not log its variable values.
    completed = subprocess.run(
        f'cmd.exe /d /s /c ""{vcvars}" >nul && set"',
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    env = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("="):
            env[key] = value
    if not any(key.casefold() == "path" for key in env):
        raise RuntimeError("Compiler environment did not include PATH")
    for key in tuple(env):
        if key.casefold() in {"_nt_symbol_path", "_nt_alt_symbol_path"}:
            del env[key]
    return env


def image_size(path: Path) -> int:
    with path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) < 64 or len(data) > 16 * 1024 * 1024 or data[:2] != b"MZ":
        raise ValueError("Expected a bounded PE image")
    pe = struct.unpack_from("<I", data, 60)[0]
    if pe + 84 > len(data) or data[pe : pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE header")
    if struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
        raise ValueError("This fixture expects PE32+")
    return struct.unpack_from("<I", data, pe + 24 + 56)[0]


def verify_identity(
    value: dict, size: int, expected_variant: int = 0, expected_record_size: int = 24
) -> None:
    if (
        value["variant"] != expected_variant
        or value["call_result"] != 5 + expected_variant
    ):
        raise ValueError("Build/behavior identity mismatch")
    if (
        value["function_va"] != value["loaded_base"] + value["rva"]
        or not 0 <= value["rva"] < size
    ):
        raise ValueError("Address is not a valid RVA in this loaded image")
    if (
        value["pointer_size"],
        value["record_size"],
        value["sequence_offset"],
        value["count_offset"],
    ) != (8, expected_record_size, 8, 16):
        raise ValueError("ABI layout mismatch; do not call through a guessed signature")


def replay(lines: list[dict], epoch: int = 1) -> dict:
    expected = [
        "accepted",
        "duplicate",
        "stale_sequence",
        "stale_epoch",
        "accepted",
        "accepted",
        "future_epoch",
        "conflict",
    ]
    if not lines or len(lines) % 8:
        raise ValueError("Synthetic fixture must contain complete eight-event batches")
    counts: Counter[str] = Counter()
    durations = []
    for offset in range(0, len(lines), 8):
        consumer = EpochConsumer(epoch)
        statuses = []
        for event in lines[offset : offset + 8]:
            started = time.perf_counter_ns()
            statuses.append(consumer.accept(event))
            durations.append(time.perf_counter_ns() - started)
        if statuses != expected or consumer.facts != {}:
            raise ValueError("Event meaning differs from the hand-specified control")
        counts.update(statuses)
    ordered = sorted(durations)
    return {
        "counts": dict(counts),
        "consumer_ns": {
            name: ordered[round((len(ordered) - 1) * q)]
            for name, q in [("p50", 0.50), ("p95", 0.95), ("p99", 0.99)]
        },
    }


def execute(output: Path, env: dict[str, str], repeats: int, csc: Path) -> dict:
    search_path = next(value for key, value in env.items() if key.casefold() == "path")
    compiler = shutil.which("cl.exe", path=search_path)
    if not compiler:
        raise FileNotFoundError("MSVC environment did not resolve cl.exe")
    artifacts = {}
    for variant in (0, 1):
        folder = output / str(variant)
        folder.mkdir()
        exe, pdb = folder / "host.exe", folder / "host.pdb"
        compile_result = run_command(
            [
                compiler,
                "/nologo",
                "/EHsc",
                "/std:c++17",
                "/Od",
                "/Ob0",
                "/Zi",
                f"/DLAB_VARIANT={variant}",
                str(SOURCE / "host.cpp"),
                f"/Fe:{exe}",
                f"/Fo:{folder}/",
                f"/Fd:{folder}/compile.pdb",
                "/link",
                "/DEBUG",
                "/DYNAMICBASE",
                "/INCREMENTAL:NO",
                f"/PDB:{pdb}",
                "dbghelp.lib",
            ],
            folder,
            env,
            60,
        )
        (folder / "compile.log").write_text(
            compile_result["stdout"] + compile_result["stderr"], encoding="utf-8"
        )
        artifacts[variant] = {
            "exe": exe,
            "pdb": pdb,
            "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
            "image_size": image_size(exe),
        }
    a, b = artifacts[0], artifacts[1]
    identities = [
        json.loads(run_command([str(a["exe"]), "identity"], output, env)["stdout"])
        for _ in range(3)
    ]
    for value in identities:
        verify_identity(value, a["image_size"])
    second = json.loads(run_command([str(b["exe"]), "identity"], output, env)["stdout"])
    verify_identity(second, b["image_size"], 1)
    if a["sha256"] == b["sha256"]:
        raise ValueError("The two changed-build controls are identical")
    failures = []
    for label, record, record_size in [
        ("wrong_build", second, 24),
        ("packed_abi", identities[0], 20),
    ]:
        try:
            verify_identity(record, a["image_size"], 0, record_size)
        except ValueError:
            failures.append(label)
        else:
            raise ValueError(f"Negative control not detected: {label}")
    good = run_command(
        [str(a["exe"]), "symbols", str(a["exe"]), str(a["pdb"])], output, env
    )
    bad = run_command(
        [str(a["exe"]), "symbols", str(a["exe"]), str(b["pdb"])],
        output,
        env,
        expected=(3,),
    )
    if (
        json.loads(good["stdout"])["matched"] is not True
        or json.loads(bad["stdout"])["matched"] is not False
    ):
        raise ValueError("PDB matching controls did not discriminate")
    stack = json.loads(
        run_command([str(a["exe"]), "stack", str(a["exe"].parent)], output, env)[
            "stdout"
        ]
    )
    if not all(
        any(name in frame for frame in stack["fixture_frames"])
        for name in ["FixtureStackLeaf", "FixtureStackMiddle"]
    ):
        raise ValueError("Expected self-owned symbolized stack frames are missing")
    managed = []
    for variant in (0, 1):
        exe = output / str(variant) / "managed.exe"
        args = [
            str(csc),
            "/nologo",
            "/target:exe",
            f"/out:{exe}",
            str(SOURCE / "ManagedIdentity.cs"),
        ]
        if variant:
            args.insert(1, "/define:VARIANT_B")
        run_command(args, exe.parent, env)
        value = json.loads(run_command([str(exe)], exe.parent, env)["stdout"])
        again = json.loads(run_command([str(exe)], exe.parent, env)["stdout"])
        if value != again:
            raise ValueError("Same managed file changed identity across executions")
        managed.append(value)
    if managed[0]["mvid"] == managed[1]["mvid"]:
        raise ValueError("Changed managed builds share an MVID")
    benchmarks = {}
    common = None
    for mode in ["encoded", "typed"]:
        trials = []
        for _ in range(3):
            started = time.perf_counter_ns()
            result = run_command(
                [str(a["exe"]), "events", mode, str(repeats)], output, env, 20
            )
            lines = [json.loads(line) for line in result["stdout"].splitlines()]
            if common is None:
                common = lines
            elif common != lines:
                raise ValueError("Producer arms are not behaviorally equivalent")
            consumed = replay(lines)
            stats = json.loads(result["stderr"])
            if stats["events"] != 8 * repeats or stats["wire_decodes"] != (
                8 * repeats if mode == "encoded" else 0
            ):
                raise ValueError(
                    "Producer instrumentation did not exercise distinct paths"
                )
            trials.append(
                {
                    "end_to_end_ms": (time.perf_counter_ns() - started) / 1e6,
                    "process_ms": result["elapsed_ms"],
                    "producer": stats,
                    "consumer": consumed,
                }
            )
        benchmarks[mode] = {
            "trials": trials,
            "median_end_to_end_ms": statistics.median(
                t["end_to_end_ms"] for t in trials
            ),
        }
    invalid = run_command(
        [str(a["exe"]), "events", "unknown", "1"], output, env, expected=(2,)
    )
    warm = {}
    warm_reference = None
    for mode in ["encoded", "typed"]:
        pipe = PipeSession(a["exe"], mode, 1, env)
        try:
            first_started = time.perf_counter_ns()
            rows, _ = pipe.batch()
            first_ms = (time.perf_counter_ns() - first_started) / 1e6
            replay(rows)
            if warm_reference is None:
                warm_reference = rows
            elif rows != warm_reference:
                raise ValueError("Warm producer output differs")
            timings = []
            for _ in range(100):
                started = time.perf_counter_ns()
                rows, marker = pipe.batch()
                replay(rows)
                if rows != warm_reference or marker["wire_decodes"] != (
                    8 if mode == "encoded" else 0
                ):
                    raise ValueError("Warm producer instrumentation failed")
                timings.append((time.perf_counter_ns() - started) / 1e6)
            slow_rows, slow_marker = pipe.batch(1000, slow_consumer=True)
            replay(slow_rows)
            if not slow_marker["bounded_queue_full_observed"]:
                raise ValueError("Slow-consumer control did not reach queue capacity")
            ordered = sorted(timings)
            warm[mode] = {
                "first_batch_ms_including_startup": first_ms,
                "requests": len(timings),
                "batch_round_trip_ms": {
                    name: ordered[round((len(ordered) - 1) * q)]
                    for name, q in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
                },
                "slow_consumer_events": len(slow_rows),
                "queue_full_observed": True,
            }
        finally:
            pipe.close()
        if not pipe.closed_cleanly:
            raise ValueError("Native server did not close cleanly")
        warm[mode]["closed_cleanly"] = True
    reconnect = PipeSession(a["exe"], "typed", 2, env)
    try:
        reconnect_rows, _ = reconnect.batch()
        replay(reconnect_rows, 2)
        consumer = EpochConsumer(2)
        if (
            consumer.accept(warm_reference[0]) != "stale_epoch"
            or consumer.accept(reconnect_rows[0]) != "accepted"
        ):
            raise ValueError("Reconnect epoch control failed")
    finally:
        reconnect.close()
    if not reconnect.closed_cleanly:
        raise ValueError("Reconnected server did not close cleanly")
    return {
        "synthetic": True,
        "status": "passed",
        "source_sha256": hashlib.sha256((SOURCE / "host.cpp").read_bytes()).hexdigest(),
        "native_artifacts": {
            str(k): {"sha256": v["sha256"], "image_size": v["image_size"]}
            for k, v in artifacts.items()
        },
        "same_build_runs": identities,
        "changed_build": second,
        "negative_controls_rejected": failures + ["wrong_pdb", "unknown_mode"],
        "pdb_match": json.loads(good["stdout"]),
        "pdb_mismatch": json.loads(bad["stdout"]),
        "stack": stack,
        "managed": managed,
        "event_input_sha256": hashlib.sha256(
            json.dumps(common, sort_keys=True).encode()
        ).hexdigest(),
        "event_repetitions": repeats,
        "benchmarks": benchmarks,
        "warm_pipe": warm,
        "reconnect_rejects_previous_epoch": True,
        "invalid_input_exit": invalid["exit_code"],
        "unmeasured": [
            "CPU/RSS",
            "allocations/copies outside the explicit wire path",
            "long-duration endurance",
            "real-game integration",
            "cross-machine performance",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vcvars", type=Path)
    parser.add_argument("--csc", type=Path)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/native-lab")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 1000:
        parser.error("--repeats must be 1..1000")
    output_base = args.output.resolve()
    if any(
        output_base.is_relative_to(ROOT / name)
        for name in ["research", "src", "tests", "docs"]
    ):
        parser.error("Output must be outside maintained source/docs")
    try:
        env = compiler_environment(args.vcvars)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "status": "input_error",
                    "error_type": type(error).__name__,
                    "message": "Compiler environment unavailable; verify --vcvars and installed MSVC",
                },
                indent=2,
            )
        )
        return 2
    csc = (
        args.csc
        or Path(os.environ.get("WINDIR", ""))
        / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    if not csc.is_file():
        parser.error("Supply --csc with an installed .NET Framework compiler")
    output = output_base / (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:8]
    )
    output.mkdir(parents=True)
    try:
        receipt = execute(output, env, args.repeats, csc)
    except Exception as error:
        receipt = {
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error)[:2400],
        }
    (output / "receipt.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "receipt": str(output / "receipt.json"),
                "error": receipt.get("message"),
            },
            indent=2,
        )
    )
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
