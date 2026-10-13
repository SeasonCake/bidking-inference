"""Batch decompile managed DLLs from extracted bundle to C# source."""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REFERENCE_DIR = REPO / "external_references" / "AuctionAnalyzer4.13.3"
EXE = REFERENCE_DIR / "AuctionAnalyzer4.13.3.exe"
EXTRACTED = REFERENCE_DIR / "_extracted"
DECOMPILED = REFERENCE_DIR / "_decompiled"
ILSPY = REPO / "tools/ilspycmd/pkg/tools/net8.0/any/ilspycmd.dll"

BUNDLE_SIGNATURE = bytes(
    [
        0x8B,
        0x12,
        0x02,
        0xB9,
        0x6A,
        0x61,
        0x20,
        0x38,
        0x72,
        0x7B,
        0x93,
        0x02,
        0x14,
        0xD7,
        0xA0,
        0x32,
        0x13,
        0xF5,
        0xB9,
        0xE6,
        0xEF,
        0xAE,
        0x33,
        0x18,
        0xEE,
        0x3B,
        0x2D,
        0xCE,
        0x24,
        0xB3,
        0x6A,
        0xAE,
    ]
)

INTERESTING_PREFIXES = (
    "MapBidCalculator",
    "Sdcb.",
    "OpenCvSharp",
    "CsvHelper",
    "Newtonsoft.Json",
    "YamlDotNet",
)

SKIP_PREFIXES = (
    "System.",
    "Microsoft.",
    "WindowsBase",
    "Presentation",
    "DirectWrite",
    "UIAutomation",
    "Accessibility",
    "ReachFramework",
    "PenImc",
    "vcruntime",
    "hostfxr",
    "hostpolicy",
    "clrjit",
    "coreclr",
    "mscordac",
    "mscordbi",
    "createdump",
    "mscorrc",
    "D3DCompiler",
    "wpfgfx",
    "PresentationNative",
    "WebView2Loader",
    "api-ms-",
)


def read_7bit_int(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7


def read_dotnet_string(data: bytes, pos: int) -> tuple[str, int]:
    length, pos = read_7bit_int(data, pos)
    return data[pos : pos + length].decode("utf-8"), pos + length


def find_bundle_header(data: bytes) -> int:
    sig_len = len(BUNDLE_SIGNATURE)
    end = len(data) - sig_len
    for i in range(end):
        if data[i : i + sig_len] != BUNDLE_SIGNATURE:
            continue
        header_offset, = struct.unpack_from("<q", data, i - 8)
        if 0 < header_offset < len(data):
            return header_offset
    raise RuntimeError("bundle signature not found")


def read_manifest(data: bytes, header_offset: int) -> dict:
    pos = header_offset
    major, minor, file_count = struct.unpack_from("<III", data, pos)
    pos += 12
    _, pos = read_dotnet_string(data, pos)
    pos += 40
    entries = []
    for _ in range(file_count):
        off, size, comp_size = struct.unpack_from("<qqq", data, pos)
        pos += 24
        ftype = data[pos]
        pos += 1
        rel_path, pos = read_dotnet_string(data, pos)
        entries.append(
            {
                "offset": off,
                "size": size,
                "compressed_size": comp_size,
                "type": ftype,
                "path": rel_path.replace("/", "\\"),
            }
        )
    return {"major": major, "minor": minor, "entries": entries}


def extract_entry(data: bytes, entry: dict) -> bytes:
    off = entry["offset"]
    size = entry["size"]
    comp_size = entry["compressed_size"]
    if comp_size == 0:
        return data[off : off + size]
    chunk = data[off : off + comp_size]
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompress(chunk, wbits)
        except zlib.error:
            continue
    return data[off : off + size]


def reextract_all() -> None:
    data = EXE.read_bytes()
    manifest = read_manifest(data, find_bundle_header(data))
    if EXTRACTED.exists():
        shutil.rmtree(EXTRACTED)
    EXTRACTED.mkdir(parents=True, exist_ok=True)
    for entry in manifest["entries"]:
        target = EXTRACTED / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(extract_entry(data, entry))


def is_managed_dll(path: Path) -> bool:
    data = path.read_bytes()
    return len(data) >= 2 and data[:2] == b"MZ" and b"BSJB" in data


def pick_targets() -> list[Path]:
    targets: list[Path] = []
    for dll in sorted(EXTRACTED.rglob("*.dll")):
        name = dll.name
        if name.startswith(SKIP_PREFIXES):
            continue
        if not any(name.startswith(p) or name == p for p in INTERESTING_PREFIXES):
            continue
        if is_managed_dll(dll):
            targets.append(dll)
    return targets


def decompile_one(dll: Path) -> None:
    out = DECOMPILED / dll.stem
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        "dotnet",
        str(ILSPY),
        "-p",
        "--nested-directories",
        "-r",
        str(EXTRACTED),
        "-o",
        str(out),
        str(dll),
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    if not ILSPY.exists():
        raise SystemExit(f"ilspycmd not found: {ILSPY}")

    print("Re-extracting bundle with fixed decompression...")
    reextract_all()

    targets = pick_targets()
    print(f"Managed targets to decompile: {len(targets)}")
    for dll in targets:
        print(f"  {dll.name}")

    if DECOMPILED.exists():
        shutil.rmtree(DECOMPILED)
    DECOMPILED.mkdir(parents=True, exist_ok=True)

    for dll in targets:
        print(f"\nDecompiling {dll.name} -> {DECOMPILED / dll.stem}")
        decompile_one(dll)

    print(f"\nDone. C# source under: {DECOMPILED}")


if __name__ == "__main__":
    main()
