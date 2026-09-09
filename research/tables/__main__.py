"""python -m research.tables --demo (summary), or explicit before/after/schema."""
import argparse
import json
from pathlib import Path
import sys

from .diff import InputError, Limits, compare_directories


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline table byte/text/shape comparison; no semantic compatibility verdict.")
    parser.add_argument("--demo", action="store_true", help="use committed artificial fixtures")
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--details", action="store_true", help="include filenames, hashes and columns, never row values")
    parser.add_argument("--max-files", type=int, default=64)
    parser.add_argument("--max-file-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=8 * 1024 * 1024)
    args = parser.parse_args(argv)
    if args.demo:
        if any((args.before, args.after, args.schema)):
            parser.error("--demo cannot be combined with explicit inputs")
        fixture = Path(__file__).parent / "fixtures"
        args.before, args.after, args.schema = fixture / "before", fixture / "after", fixture / "schema.json"
    elif not all((args.before, args.after, args.schema)):
        parser.error("choose --demo or provide --before, --after and --schema")
    try:
        result = compare_directories(args.before, args.after, args.schema,
            limits=Limits(args.max_files, args.max_file_bytes, args.max_total_bytes))
    except InputError as exc:
        print("input error: " + str(exc), file=sys.stderr)
        return 2
    if not args.details:
        result.pop("details")
        result.pop("absent_schema_entries")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
