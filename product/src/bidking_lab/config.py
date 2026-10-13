"""Paths and environment (game install, data dirs)."""

from __future__ import annotations

import os
from pathlib import Path

# Default: common Steam library layout; override with BIDKING_GAME_ROOT
_DEFAULT_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files (x86)\Steam\steamapps\common\BidKing",
    r"C:\Program Files\Steam\steamapps\common\BidKing",
)

_ENV_GAME_ROOT = "BIDKING_GAME_ROOT"
_ENV_STEAM_LIBRARY = "STEAM_LIBRARY_PATH"
_ENV_PROJECT_ROOT = "BIDKING_PROJECT_ROOT"


def get_game_root() -> Path | None:
    """Return BidKing install folder if it exists, else None."""
    if p := os.environ.get(_ENV_GAME_ROOT):
        path = Path(p).expanduser()
        if path.is_dir():
            return path

    for c in _DEFAULT_CANDIDATES:
        path = Path(c)
        if path.is_dir():
            return path

    if lib := os.environ.get(_ENV_STEAM_LIBRARY):
        cand = Path(lib) / "steamapps" / "common" / "BidKing"
        if cand.is_dir():
            return cand

    return None


def streaming_assets_dir(game_root: Path | None = None) -> Path | None:
    root = game_root or get_game_root()
    if root is None:
        return None
    sa = root / "BidKing_Data" / "StreamingAssets"
    return sa if sa.is_dir() else None


def project_root() -> Path:
    """Repo root (directory containing pyproject.toml)."""
    if p := os.environ.get(_ENV_PROJECT_ROOT):
        path = Path(p).expanduser()
        if path.is_dir():
            return path

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parent.parent
