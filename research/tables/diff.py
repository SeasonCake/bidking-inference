"""Bounded, flat-directory table comparison using an explicit synthetic-style schema.

This new experiment compares byte identity, canonical text and table shape separately.
It does not infer business semantics, extract bundles or search for an installation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from .codec import assert_uniform_columns, decode_table_text_strict, iter_table_rows


class InputError(ValueError):
    """Invalid, inaccessible or over-budget explicit input."""


@dataclass(frozen=True)
class Limits:
    max_files: int = 64  # each flat directory; includes every entry, even non-tables
    max_file_bytes: int = 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024  # both directories and schema combined

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in
               (self.max_files, self.max_file_bytes, self.max_total_bytes)):
            raise InputError("limits must be positive integers")


def _checked_path(path: Path, *, directory: bool = False) -> Path:
    path = Path(os.path.abspath(path))
    # Do not resolve links first: inspect each existing lexical ancestor.
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputError("symbolic links and reparse points are not accepted")
    mode = path.lstat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise InputError("expected a flat directory" if directory else "expected a regular file")
    return path


class _Reader:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.total = 0

    def read(self, path: Path) -> bytes:
        path = _checked_path(path)
        before = path.stat()
        cap = min(self.limits.max_file_bytes, self.limits.max_total_bytes - self.total)
        if before.st_size > cap:
            raise InputError("file or combined byte budget exceeded")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise InputError("input identity changed while opening")
            data = stream.read(cap + 1)
            after = os.fstat(stream.fileno())
        if len(data) > cap:
            raise InputError("file or combined byte budget exceeded")
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
            raise InputError("input changed while reading; retry with a frozen copy")
        self.total += len(data)
        return data

    def directory(self, path: Path) -> dict[str, bytes]:
        root = _checked_path(path, directory=True)
        names = []
        with os.scandir(root) as entries:
            for entry in entries:
                if len(names) >= self.limits.max_files:
                    raise InputError("directory entry budget exceeded")
                names.append(entry.name)
        # Every entry must be a regular file: no recursion, no silently ignored folders.
        return {name: self.read(root / name) for name in sorted(names)}


def _schema(data: bytes, limits: Limits) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError("schema contains duplicate keys")
            result[key] = value
        return result

    try:
        obj = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("schema must be UTF-8 JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"version", "tables"} or type(obj["version"]) is not int or obj["version"] != 1:
        raise InputError("schema requires version=1 and tables")
    tables = obj["tables"]
    if not isinstance(tables, dict) or len(tables) > limits.max_files:
        raise InputError("invalid schema table count")
    for name, spec in tables.items():
        if not name or any(char in name for char in '/\\:') or name in (".", ".."):
            raise InputError("schema table names must be simple filenames")
        if not isinstance(spec, dict) or set(spec) - {"encoding", "columns", "id_column"}:
            raise InputError("unknown schema keys")
        if spec.get("encoding") not in ("tsv", "base64-tsv", "text"):
            raise InputError("encoding must be tsv, base64-tsv or text")
        columns = spec.get("columns")
        if spec["encoding"] == "text":
            if set(spec) != {"encoding"}:
                raise InputError("text entries cannot declare table columns")
        elif (not isinstance(columns, list) or len(columns) < 2 or
              any(not isinstance(c, str) or not c or '\t' in c or '\n' in c or '\r' in c for c in columns) or
              len(set(columns)) != len(columns) or spec.get("id_column") not in columns):
            raise InputError("table requires unique columns and an id_column in columns")
    return tables


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(raw: bytes, spec: dict | None) -> dict:
    out = {"bytes": len(raw), "sha256": _digest(raw), "canonical_sha256": None,
           "structure": None, "issues": []}
    if spec is None:
        out["issues"].append("unknown_table")
    try:
        text = raw.decode("utf-8-sig")
        if spec and spec["encoding"] == "base64-tsv":
            text = decode_table_text_strict(text).removeprefix("\ufeff")
    except (UnicodeError, ValueError):
        out["issues"].append("invalid_encoding")
        return out
    canonical = "\n".join(text.splitlines())
    out["canonical_sha256"] = _digest(canonical.encode("utf-8"))
    if spec is None or spec["encoding"] == "text":
        return out
    rows = list(iter_table_rows(text))
    if not rows or len(rows[0]) < 2:
        out["issues"].append("non_structured_text")
        return out
    try:
        width = assert_uniform_columns(rows)
    except ValueError:
        out["issues"].append("non_rectangular")
        return out
    header, body = rows[0], rows[1:]
    out["structure"] = {"columns": header, "column_count": width, "row_count": len(body)}
    if len(set(header)) != len(header) or any(not item for item in header):
        out["issues"].append("invalid_header")
    if set(header) - set(spec["columns"]):
        out["issues"].append("unknown_columns")
    if set(spec["columns"]) - set(header):
        out["issues"].append("missing_columns")
    if header != spec["columns"] and set(header) == set(spec["columns"]):
        out["issues"].append("column_order_changed")
    if spec["id_column"] in header:
        index = header.index(spec["id_column"])
        ids = [row[index] for row in body]
        duplicates = sum(count - 1 for count in Counter(ids).values())
        out["duplicate_id_rows"] = duplicates
        if duplicates:
            out["issues"].append("duplicate_ids")
        if "" in ids:
            out["issues"].append("empty_ids")
    return out


def compare_directories(before: Path, after: Path, schema: Path, *, limits: Limits | None = None) -> dict:
    """Read only selected inputs, return summaries plus value-free per-file details.

    added/removed/changed/unchanged are raw-byte classifications. Canonical equality
    ignores BOM and line separators/final newline; table shape includes header order
    and row count. None means that comparison is unavailable, not that it passed.
    """
    limits = limits or Limits()
    reader = _Reader(limits)
    try:
        specs = _schema(reader.read(schema), limits)
        left, right = reader.directory(before), reader.directory(after)
    except OSError as exc:
        raise InputError("input inaccessible: " + (exc.strerror or type(exc).__name__)) from exc
    counts = dict.fromkeys(("added", "removed", "changed", "unchanged"), 0)
    issues = Counter()
    details = []
    for name in sorted(left.keys() | right.keys()):
        old = _snapshot(left[name], specs.get(name)) if name in left else None
        new = _snapshot(right[name], specs.get(name)) if name in right else None
        kind = "added" if old is None else "removed" if new is None else "unchanged" if left[name] == right[name] else "changed"
        counts[kind] += 1
        for snapshot in (old, new):
            if snapshot:
                issues.update(snapshot["issues"])
        entry = {"name": name, "classification": kind, "before": old, "after": new,
                 "canonical_changed": None, "structure_changed": None}
        if old and new:
            if old["canonical_sha256"] is not None and new["canonical_sha256"] is not None:
                entry["canonical_changed"] = old["canonical_sha256"] != new["canonical_sha256"]
            if old["structure"] is not None and new["structure"] is not None:
                entry["structure_changed"] = old["structure"] != new["structure"]
        details.append(entry)
    absent = sorted(specs.keys() - (left.keys() | right.keys()))
    return {"schema_version": 1, "counts": counts, "issue_counts": dict(sorted(issues.items())),
            "schema_entries_absent_from_both": len(absent), "bytes_read": reader.total,
            "semantic_compatibility": "not_assessed", "details": details,
            "absent_schema_entries": absent}
