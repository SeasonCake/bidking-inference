"""Smoke tests for base64 + TSV table decoder.

These are pure unit tests and do not require the game install.
"""

from __future__ import annotations

import base64

import pytest

from bidking_lab.extract.tables import (
    assert_uniform_columns,
    decode_table_text,
    iter_table_rows,
)


def _make_b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_decode_table_text_roundtrip_basic() -> None:
    text = "1\t2\t3\n4\t5\t6"
    encoded = _make_b64(text)
    assert decode_table_text(encoded) == text


def test_decode_table_text_ignores_whitespace() -> None:
    text = "a\tb"
    encoded = _make_b64(text)
    padded = "  " + "\n".join([encoded[i : i + 16] for i in range(0, len(encoded), 16)])
    assert decode_table_text(padded) == text


def test_decode_table_text_handles_utf8() -> None:
    text = "1\t中文\t描述"
    encoded = _make_b64(text)
    assert decode_table_text(encoded) == text


def test_iter_table_rows_splits_tabs() -> None:
    rows = list(iter_table_rows("a\tb\tc\nd\te\tf"))
    assert rows == [["a", "b", "c"], ["d", "e", "f"]]


def test_assert_uniform_columns_ok() -> None:
    rows = [["1", "2"], ["3", "4"]]
    assert assert_uniform_columns(rows) == 2


def test_assert_uniform_columns_empty() -> None:
    assert assert_uniform_columns([]) == 0


def test_assert_uniform_columns_raises_when_jagged() -> None:
    with pytest.raises(ValueError):
        assert_uniform_columns([["1", "2"], ["3"]])
