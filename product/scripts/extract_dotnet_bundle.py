"""Extract .NET single-file bundle (ILSpy / HostModel format)."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REFERENCE_DIR = REPO / "external_references" / "AuctionAnalyzer4.13.3"
EXE = REFERENCE_DIR / "AuctionAnalyzer4.13.3.exe"
OUT = REFERENCE_DIR / "_extracted"

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
    s = data[pos : pos + length].decode("utf-8")
    return s, pos + length


def find_bundle_header(data: bytes) -> int:
    sig_len = len(BUNDLE_SIGNATURE)
    end = len(data) - sig_len
    for i in range(end):
        if data[i] != BUNDLE_SIGNATURE[0]:
            continue
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
    bundle_id, pos = read_dotnet_string(data, pos)
    deps_off, deps_size, runtime_off, runtime_size, flags = struct.unpack_from("<qqqqQ", data, pos)
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
    return {
        "major": major,
        "minor": minor,
        "bundle_id": bundle_id,
        "entries": entries,
    }


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


def main() -> None:
    data = EXE.read_bytes()
    header_offset = find_bundle_header(data)
    manifest = read_manifest(data, header_offset)
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"bundle v{manifest['major']}.{manifest['minor']} id={manifest['bundle_id']}")
    print(f"entries={len(manifest['entries'])}")

    for entry in manifest["entries"]:
        rel = entry["path"]
        target = OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = extract_entry(data, entry)
        target.write_bytes(payload)
        print(f"  {rel} ({len(payload)} bytes)")

    main_dll = OUT / "MapBidCalculator.dll"
    if main_dll.exists():
        print(f"\nmain assembly: {main_dll}")


if __name__ == "__main__":
    main()
