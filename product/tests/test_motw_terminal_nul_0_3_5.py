"""Single-terminal-NUL compatibility; no product launch or user marker removal."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/local_integrity_namespace_guard.py"
BASE = b"[ZoneTransfer]\r\nZoneId=3\r\n"


@pytest.fixture(scope="module")
def parse_zone():
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_valid_windows_zone_identifier"
        ) or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_WINDOWS_ZONE_IDENTIFIER_MAX_BYTES"
                for target in node.targets
            )
        )
    ]
    assert len(nodes) == 2
    scope = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(GUARD), "exec"), scope)
    return scope["_valid_windows_zone_identifier"]


@pytest.mark.parametrize("text", [
    BASE,
    b"[ZoneTransfer]\nZoneId=4\n",
    b"\xef\xbb\xbf[zonetransfer]\nzoneid = 3\n",
    b"; comment\n[ZoneTransfer]\nHostUrl=https://example.test/a?b=c\nZoneId=3",
    "[ZoneTransfer]\nZoneId=3\nHostUrl=https://example.test/中文.zip\n".encode("utf-8"),
])
@pytest.mark.parametrize("terminator", [b"", b"\x00"])
def test_valid_metadata_with_optional_single_terminal_nul(parse_zone, text, terminator):
    payload = text + terminator
    before = hashlib.sha256(payload).digest()
    assert parse_zone(payload)
    assert hashlib.sha256(payload).digest() == before


@pytest.mark.parametrize("payload", [
    b"", b"\x00", BASE + b"\x00\x00", BASE + b"\x00hidden",
    BASE + b"\x00hidden\x00", BASE + b"\x00\r\n",
    b"[ZoneTransfer]\x00\nZoneId=3\n",
    b"[ZoneTransfer]\x00\nZoneId=3\n\x00",
    BASE + b"ZoneId=4\n\x00", b"[ZoneTransfer]\n\x00",
    BASE + b"[Other]\nKey=value\n\x00", BASE + b"Bad Key=value\n\x00",
    BASE + b"HostUrl=\xff\x00", b"\xff\xfe" + BASE.decode().encode("utf-16le"),
    b"[ZoneTransfer]\nZoneId=0\n\x00", b"[ZoneTransfer]\nZoneId=1\n\x00",
    b"[ZoneTransfer]\nZoneId=2\n\x00",
])
def test_invalid_metadata_stays_rejected(parse_zone, payload):
    assert not parse_zone(payload)


@pytest.mark.parametrize("size,expected", [(16383, True), (16384, True), (16385, False)])
def test_original_byte_limit_includes_terminator(parse_zone, size, expected):
    prefix = BASE + b";"
    payload = prefix + b"a" * (size - len(prefix) - 1) + b"\x00"
    assert len(payload) == size
    assert parse_zone(payload) is expected


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows ADS")
def test_windows_stream_preserves_terminal_byte_and_rejects_unknown_stream(tmp_path):
    spec = importlib.util.spec_from_file_location("motw_terminal_nul_guard", GUARD)
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    try:
        spec.loader.exec_module(guard)
        member = tmp_path / "signed-member.bin"
        member.write_bytes(b"SIGNED-DEFAULT-STREAM")
        default_sha = hashlib.sha256(member.read_bytes()).digest()
        zone = Path(str(member) + ":Zone.Identifier")
        original = BASE + b"\x00"
        zone.write_bytes(original)
        streams = guard._file_streams(member)
        assert (":Zone.Identifier:$DATA", len(original)) in streams
        assert guard._read_zone_identifier(member, ":Zone.Identifier:$DATA", len(original)) == original
        assert zone.read_bytes() == original
        assert hashlib.sha256(member.read_bytes()).digest() == default_sha

        # A now-compatible parser must not disguise a real stream length change.
        zone.write_bytes(BASE)
        with pytest.raises(guard.NamespaceGuardError, match="namespace_named_stream_rejected"):
            guard._read_zone_identifier(member, ":Zone.Identifier:$DATA", len(original))
        assert guard._read_zone_identifier(member, ":Zone.Identifier:$DATA", len(BASE)) == BASE
        Path(str(member) + ":unknown").write_bytes(b"NOT-ZONE-METADATA")
        with pytest.raises(guard.NamespaceGuardError, match="namespace_named_stream_rejected"):
            guard._file_streams(member)
        assert hashlib.sha256(member.read_bytes()).digest() == default_sha
    finally:
        sys.modules.pop(spec.name, None)
