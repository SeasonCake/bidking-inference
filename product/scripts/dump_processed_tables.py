"""Decode every Tables/*.txt and write human-readable TSV under data/processed/.

What this script writes (all gitignored — local browse only):

  data/processed/tables/<Name>.tsv         decoded, UTF-8-BOM, tab-separated
  data/processed/tables/_with_headers/     same rows with a header line on top
                                           (only for tables we have a tentative
                                           schema for: Drop, Item)
  data/processed/drop_entries.csv          Drop.txt's inner entries exploded
                                           one entry per row, joinable with Item

The "with_headers" outputs use column names from docs/item_table_schema.md;
"_?" suffix means the meaning is not yet confirmed.

Usage:
  python scripts/dump_processed_tables.py
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.tables import discover_tables, load_table_rows

# Tentative column names — keep in sync with docs/item_table_schema.md.
ITEM_TXT_HEADER: list[str] = [
    "item_id",
    "name_zh",
    "description_zh",
    "name_key",
    "display_key",
    "desc_key",
    "tags",
    "flags_u6",
    "quality_?",
    "value_?",
    "flag_a",
    "flag_b",
    "flag_c",
    "flag_d",
    "pair_14",
    "int_15",
    "price_tiers_?",
    "int_17",
    "int_18",
    "allowed_shelves_?",
    "subcat_list_?",
    "bundle_grants_?",
    "ref_id",
    "related_items_?",
    "icon_name",
    "icon_set_?",
    "int_26",
    "zero_col",
    "list_28",
    "rate_or_decay_?",
    "pair_30",
    "rare_flag_31",
    "list_32",
    "model_name",
    "flag_e",
    "list_35",
    "flag_f",
    "trailing_empty",
]

DROP_TXT_HEADER: list[str] = [
    "pool_id",
    "name",
    "description",
    "pool_type",
    "entries_json",
]

HEADER_MAP: dict[str, list[str]] = {
    "Item": ITEM_TXT_HEADER,
    "Drop": DROP_TXT_HEADER,
}


def _write_tsv(path: Path, rows: list[list[str]], header: list[str] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        if header is not None:
            f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join(row) + "\n")


def dump_raw_tables(tables_in: Path, processed_dir: Path) -> list[Path]:
    out_dir = processed_dir / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path in discover_tables(tables_in):
        stem = path.stem
        if stem in {"filelist", "fileVersion", "fileDiff"}:
            continue
        try:
            rows = load_table_rows(path)
        except Exception as exc:
            print(f"  {stem}: SKIP (decode failed: {exc})")
            continue

        # raw (no header)
        raw_out = out_dir / f"{stem}.tsv"
        _write_tsv(raw_out, rows, header=None)
        written.append(raw_out)

        # headered variant where we have a schema
        if stem in HEADER_MAP:
            header = HEADER_MAP[stem]
            ncols = len(rows[0]) if rows else len(header)
            if len(header) != ncols:
                print(
                    f"  {stem}: WARN header has {len(header)} cols, "
                    f"table has {ncols} — padding/truncating"
                )
                header = (header + [f"col_{i}" for i in range(len(header), ncols)])[:ncols]
            headered_out = out_dir / "_with_headers" / f"{stem}.tsv"
            _write_tsv(headered_out, rows, header=header)
            written.append(headered_out)

        print(f"  {stem}: rows={len(rows)} cols={len(rows[0]) if rows else 0}")
    return written


def dump_drop_entries(tables_in: Path, processed_dir: Path) -> Path | None:
    drop_path = tables_in / "Drop.txt"
    if not drop_path.is_file():
        return None
    pools = load_drop_table(drop_path)
    out_path = processed_dir / "drop_entries.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "pool_id",
                "pool_type",
                "pool_name",
                "pool_description",
                "entry_idx",
                "category",
                "item_id",
                "n_min",
                "n_max",
                "weight",
                "weight_share_in_pool",
            ]
        )
        for pool in pools.values():
            total = pool.total_weight or 1
            for i, e in enumerate(pool.entries):
                w.writerow(
                    [
                        pool.pool_id,
                        pool.pool_type,
                        pool.name,
                        pool.description,
                        i,
                        e.category,
                        e.item_id,
                        e.n_min,
                        e.n_max,
                        e.weight,
                        f"{e.weight / total:.6f}",
                    ]
                )
    return out_path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"
    processed_dir = repo_root / "data" / "processed"
    if not tables_in.is_dir():
        print(f"missing raw tables: {tables_in}", file=sys.stderr)
        return 2

    print(f"reading from : {tables_in}")
    print(f"writing into : {processed_dir}\n")

    print("[1] raw TSVs:")
    raw_outputs = dump_raw_tables(tables_in, processed_dir)
    print(f"  wrote {len(raw_outputs)} files\n")

    print("[2] drop_entries.csv:")
    de = dump_drop_entries(tables_in, processed_dir)
    print(f"  wrote {de}\n" if de else "  SKIP (no Drop.txt)\n")

    print("Done. Open in Excel / VSCode:")
    print(f"  - {processed_dir}\\tables\\Item.tsv               (no header)")
    print(f"  - {processed_dir}\\tables\\_with_headers\\Item.tsv (with column names)")
    print(f"  - {processed_dir}\\tables\\_with_headers\\Drop.tsv (with column names)")
    print(f"  - {processed_dir}\\drop_entries.csv               (flattened drops)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
