"""Decode BidKing ``BidKing_Data/StreamingAssets/Tables/*.txt``.

Each table is stored as a single Base64 blob whose decoded payload is a
UTF-8 TSV: rows separated by ``\n``, columns separated by ``\t``. Column
counts are constant per table (header semantics are not embedded in the
file). We expose:

- :func:`decode_table_text`: raw -> decoded UTF-8 text
- :func:`iter_table_rows`: decoded text -> ``list[str]`` per row
- :func:`load_table_rows`: convenience for path -> rows

Schema interpretation (which column means what) is intentionally left
out of this module so we can keep raw decoding stable while column
mappings evolve under ``bidking_lab.extract.schemas``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ..secure.data_crypt import load_text  # moat 表透明解密(加密包)/ 明文直读(源码)

TABLES_SUBDIR = "Tables"


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


def load_table_rows(path: Path) -> list[list[str]]:
    """Read a ``Tables/<name>.txt`` file and return its decoded TSV rows."""
    raw_text = load_text(path, encoding="utf-8")   # 加密包: 内核K解密; 源码: 明文直读
    decoded = decode_table_text(raw_text)
    return list(iter_table_rows(decoded))


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


def discover_tables(tables_dir: Path) -> list[Path]:
    """Return ``Tables/*.txt`` files inside ``tables_dir`` sorted by name."""
    if not tables_dir.is_dir():
        return []
    return sorted(p for p in tables_dir.glob("*.txt") if p.is_file())


__all__: tuple[str, ...] = (
    "TABLES_SUBDIR",
    "assert_uniform_columns",
    "decode_table_text",
    "discover_tables",
    "iter_table_rows",
    "load_table_rows",
)
