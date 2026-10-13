#!/usr/bin/env python3
"""Explicit-file search and bounded directory metadata inventory; never deletes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

FORBIDDEN_ROOT_NAMES = {".git", ".codex", "temp", "tmp", "scratch", "_wt",
                        "bidking-worktrees", "2026", "users"}
MAX_FILES = 128
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_HITS = 1000


class Refused(ValueError):
    pass


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def checked_path(raw):
    path = Path(raw)
    if not path.is_absolute() or any(c in str(path) for c in "*?[]"):
        raise Refused("absolute_literal_path_required")
    if ".." in path.parts:
        raise Refused("parent_segment_refused")
    for part in [*reversed(path.parents), path]:
        if reparse(part.lstat()):
            raise Refused("reparse_path_refused")
    return path.resolve(strict=True)


def checked_root(raw):
    root = checked_path(raw)
    if not root.is_dir():
        raise Refused("root_must_be_directory")
    if (root == Path(root.anchor) or root == Path.home().resolve()
            or root.name.casefold() in FORBIDDEN_ROOT_NAMES):
        raise Refused("broad_root_refused")
    return root


def stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def result(mode, root):
    return {"schema": "bounded_readonly_v1", "mode": mode, "root": str(root),
            "status": "complete", "complete": True, "absence_supported": False,
            "entries_seen": 0, "files_read": 0, "bytes_read": 0, "matches": [],
            "files": [], "not_run": ["deletion", "external_state_change"]}


def read_text_bytes(stream, expected_size, max_line_bytes):
    data = bytearray()
    line_bytes = 0
    while len(data) <= expected_size:
        chunk = stream.read(min(65536, expected_size + 1 - len(data)))
        if not chunk:
            break
        if b"\x00" in chunk:
            raise Refused("binary_file_refused")
        for index, part in enumerate(chunk.split(b"\n")):
            line_bytes = line_bytes + len(part) if index == 0 else len(part)
            if line_bytes > max_line_bytes:
                raise Refused("line_byte_budget_exceeded")
        data.extend(chunk)
    return bytes(data)


def search(root, files, needle, *, max_bytes, max_file_bytes, max_hits,
           max_line_bytes=65536):
    out = result("search", root)
    if not files or len(files) > MAX_FILES:
        raise Refused("explicit_file_count_out_of_range")
    if (not needle or len(needle) > 4096
            or any(char in needle for char in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")):
        raise Refused("single_line_literal_query_required")
    targets = []
    seen = set()
    total = 0
    # Complete preflight before opening any file for content.
    for raw in files:
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts or any(c in raw for c in "*?[]:"):
            raise Refused("relative_literal_file_required")
        target = checked_path(root / rel)
        target.relative_to(root)
        key = str(target).casefold() if os.name == "nt" else str(target)
        if key in seen:
            raise Refused("duplicate_file")
        seen.add(key)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise Refused("regular_file_required")
        if info.st_size > max_file_bytes:
            raise Refused("per_file_budget_exceeded")
        total += info.st_size
        if total > max_bytes:
            raise Refused("total_content_budget_exceeded")
        targets.append((target, info))
    hit_count = 0
    for target, before in targets:
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if stamp(opened) != stamp(before):
                raise Refused("file_changed_before_read")
            data = read_text_bytes(stream, before.st_size, max_line_bytes)
            after = os.fstat(stream.fileno())
        out["bytes_read"] += len(data)
        out["files_read"] += 1
        if len(data) != before.st_size or stamp(after) != stamp(before):
            raise Refused("file_changed_during_read")
        if b"\x00" in data:
            raise Refused("binary_file_refused")
        text = data.decode("utf-8-sig")  # Encoding failure is not a negative search.
        name = target.relative_to(root).as_posix()
        out["files"].append({"path": name, "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()})
        lines = []
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line:
                if hit_count >= max_hits:
                    out.update(status="limit", complete=False,
                               reason="hit_limit_reached")
                    if lines:
                        out["matches"].append({"path": name, "lines": lines})
                    return out
                lines.append(number)
                hit_count += 1
        if lines:
            out["matches"].append({"path": name, "lines": lines})
    out["absence_supported"] = not out["matches"]
    out["scope"] = "only_the_explicit_files"
    return out


def inventory(root, *, max_entries, max_depth):
    out = result("inventory", root)
    out.update(logical_bytes=0, file_count=0, directory_count=0, reparse_count=0)
    stack = [(root, 0)]
    while stack:
        parent, depth = stack.pop()
        # Recheck before opening each directory; never intentionally follow a link.
        if reparse(parent.lstat()):
            out["reparse_count"] += 1
            out.update(status="partial", complete=False)
            continue
        with os.scandir(parent) as children:
            for child in children:
                if out["entries_seen"] >= max_entries:
                    out.update(status="limit", complete=False, reason="entry_limit_reached")
                    return out
                out["entries_seen"] += 1
                info = child.stat(follow_symlinks=False)
                if reparse(info):
                    out["reparse_count"] += 1
                    out.update(status="partial", complete=False)
                elif stat.S_ISDIR(info.st_mode):
                    out["directory_count"] += 1
                    if depth >= max_depth:
                        out.update(status="partial", complete=False, reason="depth_limit_reached")
                    else:
                        stack.append((Path(child.path), depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    out["file_count"] += 1
                    out["logical_bytes"] += info.st_size
                else:
                    out.update(status="partial", complete=False, reason="non_regular_entry")
    out["cleanup_eligible"] = False  # Metadata completeness is not deletion authority.
    return out


def worker(args):
    root = checked_root(args.root)
    if args.mode == "search":
        return search(root, args.file, args.contains,
                      max_bytes=args.max_bytes, max_file_bytes=args.max_file_bytes,
                      max_hits=args.max_hits, max_line_bytes=args.max_line_bytes)
    if args.file or args.contains is not None:
        raise Refused("inventory_does_not_read_content")
    if (root / ".git").exists():
        raise Refused("repository_root_inventory_refused")
    return inventory(root, max_entries=args.max_entries, max_depth=args.max_depth)


def run_bounded(command, timeout):
    """The worker never spawns children; retain its process handle through cleanup."""
    started = time.monotonic()
    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _stderr = process.communicate(timeout=timeout)
        if len(stdout) > 256 * 1024 or process.returncode not in (0, 2, 3):
            payload = {"status": "error", "complete": False, "absence_supported": False,
                       "reason": "worker_failed_or_output_limit"}
        else:
            payload = json.loads(stdout.decode("utf-8"))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        payload = {"status": "timeout", "complete": False, "absence_supported": False,
                   "reason": "worker_deadline", "owned_worker_reaped": True}
    except (OSError, UnicodeError, ValueError):
        payload = {"status": "error", "complete": False, "absence_supported": False,
                   "reason": "worker_output_invalid"}
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.communicate()
            process.stdout.close()
            process.stderr.close()
    if process is not None:
        payload.update(worker_pid=process.pid, worker_parent_pid=os.getpid(),
                       worker_started_monotonic=started,
                       worker_exit_code=process.returncode, worker_reaped=True)
    payload["elapsed_seconds"] = round(time.monotonic() - started, 4)
    return payload


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("search", "inventory"))
    p.add_argument("--root", required=True)
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--contains")
    p.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024)
    p.add_argument("--max-file-bytes", type=int, default=1024 * 1024)
    p.add_argument("--max-line-bytes", type=int, default=65536)
    p.add_argument("--max-output-bytes", type=int, default=65536)
    p.add_argument("--max-hits", type=int, default=128)
    p.add_argument("--max-entries", type=int, default=4096)
    p.add_argument("--max-depth", type=int, default=8)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(tokens)
    valid = (1 <= args.max_bytes <= MAX_CONTENT_BYTES
             and 1 <= args.max_file_bytes <= MAX_FILE_BYTES
             and 1 <= args.max_line_bytes <= MAX_FILE_BYTES
             and 1024 <= args.max_output_bytes <= 256 * 1024
             and 1 <= args.max_hits <= MAX_HITS
             and 1 <= args.max_entries <= MAX_ENTRIES
             and 0 <= args.max_depth <= 32 and 0 < args.timeout <= 60)
    if not valid:
        payload = {"status": "refused", "complete": False,
                   "absence_supported": False, "reason": "invalid_budget"}
    elif args.worker:
        try:
            payload = worker(args)
        except (Refused, OSError, UnicodeError, ValueError) as error:
            payload = {"status": "refused", "complete": False,
                       "absence_supported": False, "reason": type(error).__name__,
                       "detail": str(error)[:500]}
    else:
        payload = run_bounded([sys.executable, str(Path(__file__).resolve()),
                               *tokens, "--worker"], args.timeout)
    encoded = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
    if len(encoded) > args.max_output_bytes:
        payload = {"status": "limit", "complete": False, "absence_supported": False,
                   "reason": "output_byte_limit", "output_bytes_before_limit": len(encoded)}
        encoded = (json.dumps(payload) + "\n").encode("utf-8")
    sys.stdout.buffer.write(encoded)
    return 0 if payload.get("complete") else (2 if payload["status"] in
        {"limit", "partial", "timeout"} else 3)


if __name__ == "__main__":
    raise SystemExit(main())
