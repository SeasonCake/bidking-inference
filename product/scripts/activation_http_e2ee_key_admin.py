#!/usr/bin/env python3
"""Create or inspect the emergency activation X25519 key without printing it."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

try:
    import activation_http_e2ee
except ModuleNotFoundError as exc:
    if exc.name != "activation_http_e2ee":
        raise
    from bidking_lab.secure import activation_http_e2ee

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def _private_bytes(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _create(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = X25519PrivateKey.generate()
    payload = base64.b64encode(_private_bytes(key)) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("private key write did not progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "status"))
    parser.add_argument("--private-key-file", required=True)
    args = parser.parse_args()
    path = Path(args.private_key_file)
    state = "existing"
    if args.command == "init" and not path.exists():
        try:
            state = _create(path)
        except FileExistsError:
            state = "existing"
    try:
        _private, key_id, public_key = activation_http_e2ee.load_server_private_key(path)
    except activation_http_e2ee.ActivationHttpE2EEError:
        print(json.dumps({"ok": False, "reason": "state_error"}, sort_keys=True))
        return 3
    print(
        json.dumps(
            {
                "ok": True,
                "state": state,
                "key_id": key_id,
                "server_public_key": base64.b64encode(public_key).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
