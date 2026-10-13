"""Inspect BidKing_Data/StreamingAssets without committing proprietary files."""

from __future__ import annotations

from pathlib import Path

from bidking_lab.config import streaming_assets_dir


def list_streaming_assets_tree(
    *,
    game_root: Path | None = None,
    max_entries: int = 200,
    suffixes: frozenset[str] | None = None,
) -> list[str]:
    """Return relative paths under StreamingAssets for exploration (cap for safety).

    Does not parse binary Unity bundles — discovery only.
    """
    sa = streaming_assets_dir(game_root)
    if sa is None:
        return []

    suf = suffixes or frozenset({".txt", ".json", ".xml", ".csv", ".manifest", ".bytes"})
    out: list[str] = []

    for p in sorted(sa.rglob("*")):
        if len(out) >= max_entries:
            break
        if not p.is_file():
            continue
        if p.suffix.lower() not in suf:
            continue
        try:
            rel = p.relative_to(sa)
        except ValueError:
            continue
        out.append(rel.as_posix())

    return out


def read_text_if_small(path: Path, *, max_bytes: int = 512_000) -> str | None:
    """Read UTF-8 text if file exists and is below max_bytes."""
    if not path.is_file():
        return None
    if path.stat().st_size > max_bytes:
        return None
    return path.read_text(encoding="utf-8", errors="replace")
