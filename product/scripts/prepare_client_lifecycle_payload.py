"""Prepare the private launcher/diagnostics/repair payloads for the native helper.

This is a build-only tool.  The public package receives neither these plaintext
files nor a generic decoder; ``bidking_kernel`` consumes the three files at Rust
compile time and exposes only the narrow lifecycle ABI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Final


LIFECYCLE_ABI: Final = 2
_GENERATION_DOMAIN: Final = b"bidking-client-lifecycle-v2\0"
_PROCESS_DISCOVERY_BLOCK: Final = """if (-not (Test-Path -LiteralPath $ProcessDiscovery)) {
  $missingMessage = "启动组件缺失:{0}{1}{0}{0}请重新完整解压当前版本安装包后重试。" -f [Environment]::NewLine, $ProcessDiscovery
  Show-FatalBox $missingMessage "BidKing Live · 启动组件缺失"
  exit 1
}
. $ProcessDiscovery
"""
_REPAIR_DISCOVERY_BLOCK: Final = """if (-not (Test-Path -LiteralPath $ProcessDiscovery)) {
  throw "repair discovery input missing"
}
. $ProcessDiscovery
"""


class LifecyclePayloadError(RuntimeError):
    """The tracked lifecycle inputs do not form one exact build bundle."""


def _read_utf8_script(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LifecyclePayloadError("lifecycle input is not strict UTF-8") from exc
    if "\x00" in text:
        raise LifecyclePayloadError("lifecycle input contains NUL")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compose_launcher_source(startup: str, process_discovery: str) -> str:
    if startup.count(_PROCESS_DISCOVERY_BLOCK) != 1:
        raise LifecyclePayloadError(
            "launcher process-discovery insertion point is not unique"
        )
    return startup.replace(_PROCESS_DISCOVERY_BLOCK, process_discovery, 1)


def compose_repair_source(repair: str, process_discovery: str) -> str:
    if repair.count(_REPAIR_DISCOVERY_BLOCK) != 1:
        raise LifecyclePayloadError(
            "repair process-discovery insertion point is not unique"
        )
    return repair.replace(_REPAIR_DISCOVERY_BLOCK, process_discovery, 1)


def lifecycle_generation(
    launcher: bytes, diagnostics: bytes, repair: bytes
) -> str:
    digest = hashlib.sha256()
    digest.update(_GENERATION_DOMAIN)
    for payload in (launcher, diagnostics, repair):
        digest.update(len(payload).to_bytes(8, "little"))
        digest.update(payload)
    return digest.hexdigest()


def prepare_lifecycle_payloads(
    *,
    startup_path: Path,
    process_discovery_path: Path,
    diagnostics_path: Path,
    repair_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    startup = _read_utf8_script(startup_path)
    process_discovery = _read_utf8_script(process_discovery_path)
    diagnostics = _read_utf8_script(diagnostics_path)
    repair = _read_utf8_script(repair_path)
    launcher = compose_launcher_source(startup, process_discovery).encode("utf-8")
    diagnostics_bytes = diagnostics.encode("utf-8")
    repair_bytes = compose_repair_source(repair, process_discovery).encode("utf-8")
    if not launcher or not diagnostics_bytes or not repair_bytes:
        raise LifecyclePayloadError("lifecycle payload is empty")

    generation = lifecycle_generation(launcher, diagnostics_bytes, repair_bytes)
    output_dir.mkdir(parents=True, exist_ok=True)
    launcher_output = output_dir / "launcher.payload"
    diagnostics_output = output_dir / "diagnostics.payload"
    repair_output = output_dir / "repair.payload"
    receipt_output = output_dir / "receipt.json"
    launcher_output.write_bytes(launcher)
    diagnostics_output.write_bytes(diagnostics_bytes)
    repair_output.write_bytes(repair_bytes)
    receipt: dict[str, object] = {
        "schema_version": 1,
        "abi": LIFECYCLE_ABI,
        "generation": generation,
        "launcher": {
            "bytes": len(launcher),
            "sha256": hashlib.sha256(launcher).hexdigest(),
        },
        "diagnostics": {
            "bytes": len(diagnostics_bytes),
            "sha256": hashlib.sha256(diagnostics_bytes).hexdigest(),
        },
        "repair": {
            "bytes": len(repair_bytes),
            "sha256": hashlib.sha256(repair_bytes).hexdigest(),
        },
    }
    receipt_output.write_text(
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="ascii",
        newline="\n",
    )
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--startup", type=Path, required=True)
    parser.add_argument("--process-discovery", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--repair", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    prepare_lifecycle_payloads(
        startup_path=args.startup,
        process_discovery_path=args.process_discovery,
        diagnostics_path=args.diagnostics,
        repair_path=args.repair,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
