"""Discovery helper: print text-like files under StreamingAssets (cap 200)."""

from __future__ import annotations

import sys

from bidking_lab.config import get_game_root, streaming_assets_dir
from bidking_lab.extract.streaming_assets import list_streaming_assets_tree


def main() -> int:
    root = get_game_root()
    sa = streaming_assets_dir(root)
    if sa is None:
        print("BidKing not found. Set BIDKING_GAME_ROOT to steamapps/common/BidKing", file=sys.stderr)
        return 1

    print(f"Game root: {root}")
    print(f"StreamingAssets: {sa}\n")

    for line in list_streaming_assets_tree(game_root=root):
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
