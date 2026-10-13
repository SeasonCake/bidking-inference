from __future__ import annotations

import gzip
import json
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bidking_lab.inference.quality_combo_presolve import load_quality_combo_presolve
from bidking_lab.secure import data_crypt
from bidking_lab.secure.data_crypt import (
    BKENC1_FORMAT,
    BKENC2_GZIP_FORMAT,
    bkenc2_member_aad,
    decrypt_bytes,
    encrypt_bytes,
    encrypt_compressed_bytes,
    encryption_format,
    load_json,
    load_text,
)


_KEY = bytes(range(32))
_MEMBER = "data/processed/quality_combo_presolve_q456.json"


class _BytesSubclass(bytes):
    pass


class _BlobProtocol:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.calls: list[str] = []

    def startswith(self, prefix: bytes) -> bool:
        self.calls.append("startswith")
        return self.value.startswith(prefix)

    def __len__(self) -> int:
        self.calls.append("len")
        return len(self.value)

    def __getitem__(self, index: object) -> object:
        self.calls.append("getitem")
        return self.value[index]  # type: ignore[index]

    def __bytes__(self) -> bytes:
        self.calls.append("bytes")
        return self.value


def test_bkenc2_compresses_roundtrips_and_binds_format_and_member() -> None:
    plain = json.dumps(
        {"maps": {str(index): [1, 2, 3] for index in range(5000)}},
        separators=(",", ":"),
    ).encode()

    blob = encrypt_compressed_bytes(plain, _KEY, member_path=_MEMBER)

    assert blob.startswith(b"BKENC2\n")
    assert len(blob) < len(plain) // 5
    assert decrypt_bytes(blob, _KEY, member_path=_MEMBER) == plain
    with pytest.raises(InvalidTag):
        decrypt_bytes(blob, _KEY, member_path="data/processed/maps.json")
    with pytest.raises(InvalidTag):
        decrypt_bytes(b"BKENC1\n" + blob[7:], _KEY, member_path=_MEMBER)


@pytest.mark.parametrize(
    "case",
    [
        "bytes-subclass-blob",
        "protocol-proxy-blob",
        "bytearray-blob",
        "bytes-subclass-key",
    ],
)
def test_decrypt_rejects_non_exact_blob_and_key_before_protocol_calls(
    case: str,
) -> None:
    blob = encrypt_bytes(b"{}", _KEY, member_path=_MEMBER)
    protocol: _BlobProtocol | None = None
    candidate_blob: object = blob
    candidate_key: object = _KEY
    if case == "bytes-subclass-blob":
        candidate_blob = _BytesSubclass(blob)
    elif case == "protocol-proxy-blob":
        protocol = _BlobProtocol(blob)
        candidate_blob = protocol
    elif case == "bytearray-blob":
        candidate_blob = bytearray(blob)
    else:
        candidate_key = _BytesSubclass(_KEY)

    with pytest.raises(ValueError, match="exact bytes"):
        decrypt_bytes(  # type: ignore[arg-type]
            candidate_blob,
            candidate_key,
            member_path=_MEMBER,
        )

    if protocol is not None:
        assert protocol.calls == []


@pytest.mark.parametrize(
    "encryptor",
    [encrypt_bytes, encrypt_compressed_bytes],
)
@pytest.mark.parametrize("invalid", ["plain", "key"])
def test_encryptors_reject_bytes_subclasses(
    encryptor: Callable[..., bytes],
    invalid: str,
) -> None:
    plain: object = b"{}"
    key: object = _KEY
    if invalid == "plain":
        plain = _BytesSubclass(plain)
    else:
        key = _BytesSubclass(key)

    with pytest.raises(ValueError, match="exact bytes"):
        encryptor(plain, key, member_path=_MEMBER)  # type: ignore[operator]


@pytest.mark.parametrize("operation", ["encrypt-plain", "encrypt-key", "decrypt"])
def test_exact_type_gate_does_not_invoke_protocol_magic(operation: str) -> None:
    protocol = _BlobProtocol(b"K" * 32)
    with pytest.raises(ValueError, match="exact bytes"):
        if operation == "encrypt-plain":
            encrypt_bytes(
                protocol,  # type: ignore[arg-type]
                _KEY,
                member_path=_MEMBER,
            )
        elif operation == "encrypt-key":
            encrypt_compressed_bytes(
                b"{}",
                protocol,  # type: ignore[arg-type]
                member_path=_MEMBER,
            )
        else:
            decrypt_bytes(
                protocol,  # type: ignore[arg-type]
                _KEY,
                member_path=_MEMBER,
            )
    assert protocol.calls == []


def test_exact_builtin_bytes_remain_valid_for_both_envelopes() -> None:
    for encryptor in (encrypt_bytes, encrypt_compressed_bytes):
        blob = encryptor(b"exact", _KEY, member_path=_MEMBER)
        assert type(blob) is bytes  # noqa: E721 - exact built-in contract
        assert decrypt_bytes(blob, _KEY, member_path=_MEMBER) == b"exact"


def test_all_entrypoints_reject_wrong_length_exact_bytes_keys() -> None:
    blob = encrypt_bytes(b"{}", _KEY, member_path=_MEMBER)
    short_key = b"K" * 31
    operations = (
        lambda: encrypt_bytes(b"{}", short_key, member_path=_MEMBER),
        lambda: encrypt_compressed_bytes(b"{}", short_key, member_path=_MEMBER),
        lambda: decrypt_bytes(blob, short_key, member_path=_MEMBER),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="32-byte exact bytes"):
            operation()


def test_runtime_loaders_accept_bkenc2_and_legacy_bkenc1(tmp_path: Path) -> None:
    json_member = "data/processed/maps.json"
    json_path = tmp_path / Path(json_member)
    json_path.parent.mkdir(parents=True)
    json_path.write_bytes(
        encrypt_compressed_bytes(b'{"map":5608}', _KEY, member_path=json_member)
    )
    assert encryption_format(json_path) == BKENC2_GZIP_FORMAT
    assert load_json(json_path, _KEY) == {"map": 5608}

    text_member = "data/raw/tables/Item.txt"
    text_path = tmp_path / Path(text_member)
    text_path.parent.mkdir(parents=True)
    text_path.write_bytes(
        encrypt_bytes(b"legacy text", _KEY, member_path=text_member)
    )
    assert encryption_format(text_path) == BKENC1_FORMAT
    assert load_text(text_path, _KEY, encoding="utf-8") == "legacy text"


def test_quality_combo_product_loader_accepts_bkenc2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"version": 1, "qualities": [4, 5, 6], "maps": {"5608": {}}}
    path = tmp_path / Path(_MEMBER)
    path.parent.mkdir(parents=True)
    path.write_bytes(
        encrypt_compressed_bytes(
            json.dumps(payload).encode(),
            _KEY,
            member_path=_MEMBER,
        )
    )
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _KEY)

    assert load_quality_combo_presolve(path) == payload


@pytest.mark.parametrize(
    ("declared_size", "compressed"),
    [
        (1, b"not-a-gzip-stream"),
        (2, gzip.compress(b"one", mtime=0)),
        (3, gzip.compress(b"one", mtime=0)[:-1]),
        (3, gzip.compress(b"one", mtime=0) + gzip.compress(b"two", mtime=0)),
        (128 * 1024 * 1024 + 1, gzip.compress(b"", mtime=0)),
    ],
)
def test_bkenc2_authenticated_malformed_payload_fails_closed(
    declared_size: int,
    compressed: bytes,
) -> None:
    nonce = bytes(range(12))
    payload = struct.pack(">Q", declared_size) + compressed
    blob = b"BKENC2\n" + nonce + AESGCM(_KEY).encrypt(
        nonce,
        payload,
        bkenc2_member_aad(_MEMBER),
    )

    with pytest.raises(ValueError, match="BKENC2"):
        decrypt_bytes(blob, _KEY, member_path=_MEMBER)
