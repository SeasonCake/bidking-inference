"""Tiny synthetic member-closure example, not a product packaging system."""

from __future__ import annotations

import hashlib
from typing import Mapping
import zlib


def fingerprints(contents: Mapping[str, bytes]) -> dict[str, str]:
    if not contents or any(
        type(k) is not str or not k or type(v) is not bytes for k, v in contents.items()
    ):
        raise ValueError("Named byte inputs are required")
    return {
        name: hashlib.sha256(data).hexdigest()
        for name, data in sorted(contents.items())
    }


def differences(
    expected: Mapping[str, str], actual: Mapping[str, str]
) -> dict[str, list[str]]:
    return {
        "missing": sorted(expected.keys() - actual.keys()),
        "unexpected": sorted(actual.keys() - expected.keys()),
        "changed": sorted(
            k for k in expected.keys() & actual.keys() if expected[k] != actual[k]
        ),
    }


def transform(
    expected: Mapping[str, str], inputs: Mapping[str, bytes]
) -> dict[str, bytes]:
    if any(differences(expected, fingerprints(inputs)).values()):
        raise ValueError(
            "Input members/bytes are not closed against the selected manifest"
        )
    return {name: zlib.compress(data) for name, data in inputs.items()}


def verify_transformed(
    expected: Mapping[str, str], transformed: Mapping[str, bytes]
) -> dict[str, list[str]]:
    restored = {}
    for name, compressed in transformed.items():
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, 1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or not decoder.eof or decoder.unused_data:
            raise ValueError("Expected one complete small synthetic compressed member")
        restored[name] = raw
    return differences(expected, fingerprints(restored))
