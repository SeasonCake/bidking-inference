"""Base64/TSV helpers and explicit-schema offline comparison."""
from .codec import assert_uniform_columns, decode_table_text, decode_table_text_strict, iter_table_rows

__all__ = ["assert_uniform_columns", "decode_table_text", "decode_table_text_strict", "iter_table_rows"]
