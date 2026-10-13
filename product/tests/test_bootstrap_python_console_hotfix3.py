from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "native" / "bidking_trust_bootstrap" / "src" / "main.rs"
BOOTSTRAP_BUILD = ROOT / "native" / "bidking_trust_bootstrap" / "build.rs"
NATIVE_CONTRACT = ROOT / "scripts" / "release_native_binary_contract.json"
PARENT = "7c31a5574e1f19b45eb53879295592e84cad0ab8"
HELD_PYTHON = Path(r"C:\Python31315\python.exe")
CREATE_NO_WINDOW = 0x08000000


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


def _run_product_body(source: str) -> str:
    start = source.index("    fn run_product(")
    end = source.index("\n    pub fn entry()", start)
    return source[start:end]


def test_bootstrap_python_spawn_is_hidden_and_disables_bytecode() -> None:
    parent = _run_product_body(
        _git_show(f"{PARENT}:native/bidking_trust_bootstrap/src/main.rs")
    )
    source = BOOTSTRAP.read_text(encoding="utf-8")
    body = _run_product_body(source)

    assert ".creation_flags(CREATE_NO_WINDOW)" not in parent
    assert body.count(".creation_flags(CREATE_NO_WINDOW)") == 1
    assert source.count("const CREATE_NO_WINDOW: u32 = 0x0800_0000;") == 1
    assert source.count("use std::os::windows::process::CommandExt;") == 1

    preserved = (
        'Command::new(root.join("runtime/python/python.exe"))',
        '.args(["-E", "-s", "-S", "-B", "-c", BOOTSTRAP])',
        ".arg(root)",
        ".arg(root.join(kernel))",
        ".arg(root.join(launcher))",
        ".arg(mode)",
        ".args(forwarded)",
        ".current_dir(root)",
    )
    for contract in preserved:
        assert contract in parent
        assert contract in body
    positions = [body.index(contract) for contract in preserved]
    assert positions == sorted(positions)
    assert ".status()" in parent
    assert ".status()" in body
    assert '.env("PYTHONDONTWRITEBYTECODE", "1")' in body
    assert "INTEGRITY_FAILURE_EXITS" in body
    assert body.index(".current_dir(root)") < body.index(
        ".creation_flags(CREATE_NO_WINDOW)"
    ) < body.index(".status()")

    build = BOOTSTRAP_BUILD.read_text(encoding="utf-8")
    contract = json.loads(NATIVE_CONTRACT.read_text(encoding="utf-8"))
    assert "#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = \"windows\")]" in source
    assert "/MANIFEST:EMBED" in build
    assert (
        "/MANIFESTUAC:level='requireAdministrator' uiAccess='false'" in build
    )
    assert contract["distribution"]["mode"] == "unsigned"
    assert contract["distribution"]["publisher_claim"] is False


def _compile_spawn_probe(tmp_path: Path) -> Path:
    rustc = shutil.which("rustc.exe")
    assert rustc is not None, "rustc.exe is required for the bootstrap console fixture"
    source = tmp_path / "bootstrap_spawn_probe.rs"
    output = tmp_path / "bootstrap-spawn-probe.exe"
    source.write_text(
        textwrap.dedent(
            r'''
            #![windows_subsystem = "windows"]

            use std::os::windows::process::CommandExt;
            use std::path::PathBuf;
            use std::process::Command;

            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const CHILD: &str = r#"
import ctypes, json, os, sys
from pathlib import Path
receipt, root, kernel, launcher, mode, *forwarded = sys.argv[1:]
Path(receipt).write_text(json.dumps({
    "console": int(ctypes.windll.kernel32.GetConsoleWindow()),
    "cwd": os.getcwd(),
    "root": root,
    "kernel": kernel,
    "launcher": launcher,
    "mode": mode,
    "forwarded": forwarded,
    "ignore_environment": sys.flags.ignore_environment,
    "no_user_site": sys.flags.no_user_site,
    "no_site": sys.flags.no_site,
    "dont_write_bytecode": sys.flags.dont_write_bytecode,
}), encoding="utf-8")
raise SystemExit(23)
"#;

            fn main() {
                let args: Vec<_> = std::env::args_os().collect();
                let mode = args.get(1).expect("probe mode");
                let python = args.get(2).expect("python path");
                let receipt = args.get(3).expect("receipt path");
                let root = PathBuf::from(args.get(4).expect("package root"));
                let kernel = root.join("src/bidking_kernel.cp313-win_amd64.pyd");
                let launcher = root.join("scripts/_client_launcher.cp313-win_amd64.pyd");
                let mut child = Command::new(python);
                child
                    .args(["-E", "-s", "-S", "-B", "-c", CHILD])
                    .arg(receipt)
                    .arg(&root)
                    .arg(kernel)
                    .arg(launcher)
                    .arg("launch")
                    .args(["--forwarded", "value with spaces"])
                    .current_dir(&root);
                if mode == "source" {
                    child.creation_flags(CREATE_NO_WINDOW);
                }
                let status = child.status().expect("start held Python");
                std::process::exit(status.code().unwrap_or(1));
            }
            '''
        ).lstrip(),
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [rustc, "--edition", "2024", str(source), "-O", "-o", str(output)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    return output


def test_compiled_windows_spawn_parent_red_source_green(tmp_path: Path) -> None:
    assert HELD_PYTHON.is_file(), f"held interpreter missing: {HELD_PYTHON}"
    probe = _compile_spawn_probe(tmp_path)
    package_root = tmp_path / "package root with spaces"
    package_root.mkdir()
    receipts: dict[str, dict[str, object]] = {}

    for arm in ("parent", "source"):
        receipt = tmp_path / f"{arm} receipt.json"
        completed = subprocess.run(
            [str(probe), arm, str(HELD_PYTHON), str(receipt), str(package_root)],
            check=False,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        assert completed.returncode == 23
        receipts[arm] = json.loads(receipt.read_text(encoding="utf-8"))

    parent = receipts["parent"]
    source = receipts["source"]
    assert parent["console"] != 0
    assert source["console"] == 0
    for receipt in receipts.values():
        assert Path(receipt["cwd"]) == package_root
        assert Path(receipt["root"]) == package_root
        assert Path(receipt["kernel"]) == (
            package_root / "src/bidking_kernel.cp313-win_amd64.pyd"
        )
        assert Path(receipt["launcher"]) == (
            package_root / "scripts/_client_launcher.cp313-win_amd64.pyd"
        )
        assert receipt["mode"] == "launch"
        assert receipt["forwarded"] == ["--forwarded", "value with spaces"]
        assert receipt["ignore_environment"] == 1
        assert receipt["no_user_site"] == 1
        assert receipt["no_site"] == 1
        assert receipt["dont_write_bytecode"] == 1
