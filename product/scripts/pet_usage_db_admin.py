"""Offline-only initializer for the external Pet usage SQLite authority.

This command never migrates, replaces, resets, or repairs state.  Production must
stop the collector first, let systemd create the dedicated StateDirectory, and then
run one explicit empty initialization before deploying the A1 runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import activation_server


DEFAULT_DATABASE = Path(
    "/var/lib/bidking-activation-anchor/pet_usage.sqlite3"
)


def _read_secret(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise activation_server.StateFileError("activation key unavailable")
        secret = path.read_bytes().strip()
    except OSError as exc:
        raise activation_server.StateFileError("activation key unavailable") from exc
    if not secret:
        raise activation_server.StateFileError("activation key unavailable")
    return secret


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize an empty external Pet usage authority exactly once.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
    )
    parser.add_argument(
        "--activation-key-file",
        default="activation_key.txt",
    )
    parser.add_argument(
        "--legacy-ledger-file",
        default="pet_usage.json",
    )
    parser.add_argument(
        "--legacy-anchor-file",
        default="pet_usage.anchor.jsonl",
    )
    parser.add_argument(
        "--initialize-empty",
        action="store_true",
        help="Required explicit acknowledgement; never imports or resets old state.",
    )
    args = parser.parse_args(argv[1:])
    if not args.initialize_empty:
        print(
            json.dumps(
                {"ok": False, "reason": "explicit_empty_init_required"},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    try:
        secret = _read_secret(Path(args.activation_key_file))
        activation_server.initialize_pet_usage_database(
            Path(args.database),
            secret=secret,
            legacy_paths=(
                Path(args.legacy_ledger_file),
                Path(args.legacy_anchor_file),
            ),
        )
    except activation_server.StateFileError:
        print(
            json.dumps(
                {"ok": False, "reason": "state_error"},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "database": "pet_usage.sqlite3",
                "revision": 0,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
