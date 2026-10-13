"""Resolve canonical sample paths from the repository's supported manifest shapes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]


def _path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = Path(value)
    return parsed if parsed.is_absolute() else ROOT / parsed


def _unique(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def load_sample_paths(
    manifest: str | Path,
    *,
    hero: str | None = None,
    paired_only: bool = False,
) -> list[Path]:
    """Load paths from an era, desktop-ingest, or focused ``samples`` manifest.

    Desktop-ingest manifests expose only ``accept_main`` here; activity samples remain in their
    separate calibration pool.  Era manifests expose only ``era=NEW``.  Focused manifests can be
    narrowed to records where ``paired=true``.
    """
    path = Path(manifest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    wanted_hero = hero.lower() if hero else None
    paths: list[Path] = []

    samples = payload.get("samples")
    if isinstance(samples, list):
        for row in samples:
            if not isinstance(row, dict):
                continue
            if wanted_hero and str(row.get("hero") or "").lower() != wanted_hero:
                continue
            if paired_only and row.get("paired") is not True:
                continue
            resolved = _path(row.get("path"))
            if resolved is not None:
                paths.append(resolved)
        return _unique(paths)

    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        for row in decisions:
            if not isinstance(row, dict) or row.get("decision") != "accept_main":
                continue
            selected = row.get("selected") if isinstance(row.get("selected"), dict) else {}
            if wanted_hero and str(selected.get("hero") or "").lower() != wanted_hero:
                continue
            if paired_only:
                continue
            resolved = _path(row.get("destination"))
            if resolved is not None:
                paths.append(resolved)
        return _unique(paths)

    rows = payload.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("era") != "NEW":
                continue
            row_hero = str(row.get("hero") or "").lower()
            if wanted_hero and row_hero and row_hero != wanted_hero:
                continue
            if paired_only:
                continue
            resolved = _path(row.get("path"))
            if resolved is not None:
                paths.append(resolved)
        return _unique(paths)

    raise ValueError(f"unsupported sample manifest shape: {path}")


__all__ = ["load_sample_paths"]
