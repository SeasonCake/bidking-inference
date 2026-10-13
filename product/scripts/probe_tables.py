"""Inspect raw StreamingAssets Tables/*.txt to guess their encoding.

Read-only probe. Prints:
  - file size and a hex preview of the first bytes
  - whether bytes look like printable ASCII / Base64
  - whether a TAB-separated multi-line text shape is visible
  - if Base64 candidate: decoded length, first decoded bytes, and what it
    looks like (likely UTF-8 string? gzip? zlib?)

Usage:
  python scripts/probe_tables.py
  python scripts/probe_tables.py --files Drop Item BidMap
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import string
import sys
import zlib
from pathlib import Path
from typing import Iterable

_BASE64_ALPHABET = set(string.ascii_letters + string.digits + "+/=")
_PRINTABLE = set(string.printable)


def repo_data_tables_dir() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "data" / "raw" / "tables"


def hex_preview(data: bytes, n: int = 32) -> str:
    head = data[:n]
    return " ".join(f"{b:02x}" for b in head)


def ascii_preview(data: bytes, n: int = 64) -> str:
    head = data[:n]
    return "".join(chr(b) if 32 <= b < 127 else "." for b in head)


def looks_like_base64(text: str, sample: int = 1024) -> bool:
    if not text:
        return False
    snippet = text[:sample].strip()
    if not snippet:
        return False
    nonalpha = sum(1 for ch in snippet if ch not in _BASE64_ALPHABET and ch not in "\n\r")
    return nonalpha == 0


def classify_decoded(buf: bytes) -> str:
    if not buf:
        return "empty"
    if buf[:2] == b"\x1f\x8b":
        return "gzip-magic"
    if buf[:2] in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"):
        return "zlib-magic"
    if buf[:4] == b"PK\x03\x04":
        return "zip-magic"
    printable = sum(1 for b in buf[:512] if b in (9, 10, 13) or 32 <= b < 127)
    ratio = printable / max(1, min(len(buf), 512))
    if ratio > 0.85:
        return f"likely-utf8/text ({ratio:.0%} printable in head)"
    return f"binary blob ({ratio:.0%} printable in head)"


def try_decompress(buf: bytes) -> tuple[str, bytes | None]:
    try:
        out = gzip.decompress(buf)
        return "gzip", out
    except (OSError, EOFError, zlib.error):
        pass
    for wbits, name in [(15, "zlib"), (-15, "deflate-raw")]:
        try:
            out = zlib.decompress(buf, wbits)
            return name, out
        except zlib.error:
            continue
    return "no-compress-match", None


def show_line_structure(text: str, max_lines: int = 6) -> None:
    lines = text.splitlines()
    print(f"  text lines: {len(lines)}")
    for i, line in enumerate(lines[:max_lines]):
        cols = line.split("\t")
        preview = line[:140].replace("\r", "")
        print(f"    line[{i}] tabs={len(cols) - 1} len={len(line)} text={preview!r}")


def probe_file(path: Path) -> None:
    size = path.stat().st_size
    print("=" * 78)
    print(f"FILE: {path.name}   size={size} bytes")
    raw = path.read_bytes()
    print(f"  hex head : {hex_preview(raw)}")
    print(f"  ascii    : {ascii_preview(raw)!r}")

    try:
        text = raw.decode("utf-8")
        is_text = True
    except UnicodeDecodeError:
        text = ""
        is_text = False
    print(f"  utf-8 decodable: {is_text}")

    if is_text:
        first_line = text.splitlines()[0] if text else ""
        b64_like = looks_like_base64(first_line)
        print(f"  first line: len={len(first_line)} base64-ish={b64_like}")
        if b64_like:
            sample = first_line[: min(len(first_line), 4096)]
            sample_clean = "".join(sample.split())
            try:
                decoded = base64.b64decode(sample_clean, validate=False)
                print(f"  base64-decoded sample bytes: {len(decoded)}")
                print(f"    decoded hex   : {hex_preview(decoded)}")
                print(f"    decoded ascii : {ascii_preview(decoded)!r}")
                method, dz = try_decompress(decoded)
                print(f"    decompress attempt: {method}")
                if dz is not None:
                    print(f"      decompressed bytes: {len(dz)}")
                    print(f"      decompressed hex  : {hex_preview(dz)}")
                    print(f"      decompressed ascii: {ascii_preview(dz)!r}")
                    print(f"      classify          : {classify_decoded(dz)}")
                else:
                    print(f"    classify decoded   : {classify_decoded(decoded)}")
            except binascii.Error as exc:
                print(f"  base64 decode failed: {exc}")

        if len(text.splitlines()) >= 2 or "\t" in text[:4096]:
            print("  -- showing line structure of raw text --")
            show_line_structure(text)

    print()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--files",
        nargs="*",
        default=["Drop", "Item", "BidMap", "Hero", "Cabinet", "Constant", "Item_Type"],
        help="Logical names (without .txt)",
    )
    args = parser.parse_args(argv)

    root = repo_data_tables_dir()
    if not root.is_dir():
        print(f"ERR: not found: {root}", file=sys.stderr)
        return 2

    for name in args.files:
        path = root / f"{name}.txt"
        if not path.is_file():
            print(f"-- SKIP {name}.txt (missing) --")
            continue
        probe_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
