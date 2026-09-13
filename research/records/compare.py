"""Compare JSON records while preserving absent, null, zero and scalar types.

This independent implementation develops the field-path comparison idea in the
historical research notes. It has no private observation-class dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class Difference:
    path: tuple[str, ...]
    status: str
    left: Any
    right: Any


def _typed(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int):
        return type(value).__name__, value
    if type(value) is float and math.isfinite(value):
        return "float", value
    if isinstance(value, list):
        return "list", tuple(_typed(item) for item in value)
    if isinstance(value, dict) and all(type(key) is str for key in value):
        return "object", tuple((key, _typed(value[key])) for key in sorted(value))
    raise ValueError("Records must contain finite JSON values and string keys")


def flatten(
    record: dict[str, Any], *, max_depth: int = 24, max_nodes: int = 10000
) -> dict[tuple[str, ...], Any]:
    """Keep empty objects and arrays; tuple paths avoid dotted-key collisions."""
    if not isinstance(record, dict) or max_depth < 0 or max_nodes < 1:
        raise ValueError("A record and positive traversal budget are required")
    shapes = [(0, record)]
    total = 0
    while shapes:
        depth, value = shapes.pop()
        total += 1
        if total > max_nodes or depth > max_depth:
            raise ValueError("Record traversal budget exceeded")
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise ValueError("Record keys must be strings")
            shapes.extend((depth + 1, child) for child in value.values())
        elif isinstance(value, list):
            shapes.extend((depth + 1, child) for child in value)
        else:
            _typed(value)
    result: dict[tuple[str, ...], Any] = {}
    pending: list[tuple[tuple[str, ...], Any]] = [((), record)]
    nodes = 0
    while pending:
        path, value = pending.pop()
        nodes += 1
        if nodes > max_nodes or len(path) > max_depth:
            raise ValueError("Record traversal budget exceeded")
        if isinstance(value, dict) and value:
            if any(type(key) is not str for key in value):
                raise ValueError("Record keys must be strings")
            pending.extend(
                (path + (key,), value[key]) for key in sorted(value, reverse=True)
            )
        else:
            # Arrays are compared in order as a single field; bound their shape too.
            _typed(value)
            if path:  # The root container is the record, not a separately named field.
                result[path] = value
    return result


def compare(left: dict[str, Any], right: dict[str, Any]) -> tuple[Difference, ...]:
    a, b = flatten(left), flatten(right)
    differences = []
    for path in sorted(a.keys() | b.keys()):
        if path not in a:
            differences.append(Difference(path, "right_only", None, b[path]))
        elif path not in b:
            differences.append(Difference(path, "left_only", a[path], None))
        elif _typed(a[path]) != _typed(b[path]):
            differences.append(Difference(path, "different", a[path], b[path]))
    return tuple(differences)
