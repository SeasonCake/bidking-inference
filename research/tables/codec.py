"""Selected historical v0.3.0 Base64/TSV helpers, with no file or decryption layer."""
from __future__ import annotations

import base64
from typing import Iterator, Sequence


def decode_table_text(raw_text: str) -> str:
    """Decode a Base64-encoded table file body to its UTF-8 text payload.

    Whitespace inside the Base64 blob (e.g. newlines added by editors) is
    stripped before decoding.
    """
    clean = "".join(raw_text.split())
    payload = base64.b64decode(clean, validate=False)
    return payload.decode("utf-8")


def iter_table_rows(decoded_text: str) -> Iterator[list[str]]:
    """Yield each TSV row of an already-decoded table as a list of cells."""
    for line in decoded_text.splitlines():
        yield line.split("\t")


def assert_uniform_columns(rows: Sequence[Sequence[str]]) -> int:
    """Return the column count if every row has the same width, else raise.

    Empty input is allowed and returns 0.
    """
    if not rows:
        return 0
    expected = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != expected:
            raise ValueError(
                f"table is not rectangular: row[{i}] has {len(row)} cols, expected {expected}"
            )
    return expected


def decode_table_text_strict(raw_text: str) -> str:
    """New opt-in decoder: allow whitespace, reject non-Base64 characters.

    This is separate from the historical permissive function. Neither is decryption.
    """
    return base64.b64decode("".join(raw_text.split()), validate=True).decode("utf-8")
