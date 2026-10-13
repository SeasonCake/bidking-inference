"""Atomic UTF-8 JSON snapshots shared by live capture consumers."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
from typing import Any


def atomic_write_json(path: Path, value: Any) -> None:
    """Replace a snapshot, cleaning this call's temporary file even on encoding errors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            delete=False,
        ) as fh:
            tmp = Path(fh.name)
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        for attempt in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt >= 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
