#!/usr/bin/env python3
"""Offline administration for the private signed-upload identity database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from upload_identity_store import (
    UploadIdentityStateError,
    UploadIdentityStore,
    initialize_upload_identity_database,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    inspect = sub.add_parser("inspect-session")
    inspect.add_argument("--session-hash", required=True)
    args = parser.parse_args(argv)
    path = Path(args.db)
    try:
        if args.command == "init":
            initialize_upload_identity_database(path)
            UploadIdentityStore(path)
            payload = {"ok": True, "state": "initialized"}
        else:
            store = UploadIdentityStore(path)
            if args.command == "status":
                payload = {"ok": True, "state": "ready"}
            else:
                payload = {
                    "ok": True,
                    "state": "ready",
                    "records": store.redacted_session(args.session_hash),
                }
    except UploadIdentityStateError:
        print(json.dumps({"ok": False, "state": "state_error"}, sort_keys=True))
        return 3
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
