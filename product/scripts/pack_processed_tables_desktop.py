# -*- coding: utf-8 -*-
"""Pack post-0625 processed tables (drop chain + companions) to Desktop zip."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path.home() / "Desktop"
STAMP = date.today().strftime("%Y%m%d")
OUT_NAME = f"bidking-processed-tables-{STAMP}"
OUT_DIR = DESKTOP / OUT_NAME
ZIP_PATH = DESKTOP / f"{OUT_NAME}.zip"

# 仅 0625 掉率链起的新表 + 0627 重建产物；不含 quality_weights_measured / maps / heroes 等旧 prior。
FILES: list[tuple[str, str, str]] = [
    (
        "data/processed/items.json",
        "items.json",
        "Item 目录(与 0627 drop 链配套; 非旧 prior)",
    ),
    (
        "data/processed/items_droppable.json",
        "items_droppable.json",
        "0.3.0 当前可解释目录(680；含九件新版藏品，排除四件退役世界杯红品)",
    ),
    (
        "data/processed/item_category_mem_0625.json",
        "item_category_mem_0625.json",
        "0625 item->掉落大类(eligibility)",
    ),
    (
        "data/processed/drop_weights_all_maps_0627.json",
        "drop_weights_all_maps_0627.json",
        "0.3.0 模型爆率母表(145图；九件新版藏品按全地图资格分摊)",
    ),
    (
        "data/processed/drop_table_priors_0627.json",
        "drop_table_priors_0627.json",
        "0627 引擎 sidecar(quality_probs/values)",
    ),
    (
        "data/processed/empirical_quality_values_NEW_0627.json",
        "empirical_quality_values_NEW_0627.json",
        "0627 逐图品质均价(长尾红归一)",
    ),
    (
        "data/processed/red_longtail_normalized_0627.json",
        "red_longtail_normalized_0627.json",
        "0627 q6 长尾红 raw->normalized",
    ),
    (
        "data/reports/drop_rates_readable_by_maptype_0627.md",
        "drop_rates_readable_by_maptype_0627.md",
        "0627 可读爆率摘要",
    ),
]


def _git_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                text=True,
            ).strip()
        )
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            text=True,
        )
        return bool(out.strip())
    except Exception:
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    rows: list[str] = []
    total = 0
    for src_rel, dst_rel, role in FILES:
        src = ROOT / src_rel
        if not src.is_file():
            raise SystemExit(f"Missing: {src}")
        dst = OUT_DIR / dst_rel
        shutil.copy2(src, dst)
        size = dst.stat().st_size
        total += size
        digest = _sha256(dst)
        rows.append(f"| `{dst_rel}` | {size:,} | `{digest}` | {role} |")

    head = _git_head()
    dirty = "(dirty worktree)" if _git_dirty() else "(clean)"
    manifest = f"""# BidKing processed 表 — 0625+ 掉率链 ({STAMP})

来源: bidking-lab @ `{head}` {dirty}

**范围:** 仅 0625 内存 DropMap / category 映射起的新掉率链与 0627 重建产物。  
**不含:** `quality_weights_measured.json`、`maps.json`、`heroes.json`、旧 reconstructed prior、shadow 表。

权威说明: `docs/handoffs/HANDOFF_drop_rate_closeout_2026-06-27.zh-CN.md`

## Live 引擎默认读取

| 文件 | 用途 |
|------|------|
| `drop_table_priors_0627.json` | 品质/价值先验 |
| `red_longtail_normalized_0627.json` | q6 长尾估值 |
| `items.json` | Item 目录价/形状 |

## 文件清单

| 路径 | 字节 | SHA256 | 说明 |
|------|------|--------|------|
"""
    manifest += "\n".join(rows)
    manifest += f"\n\n合计未压缩约 **{total:,}** 字节。\n"
    (OUT_DIR / "MANIFEST.zh-CN.md").write_text(manifest, encoding="utf-8")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(OUT_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(OUT_DIR.parent))

    zip_hash = _sha256(ZIP_PATH)
    print(f"Folder: {OUT_DIR}")
    print(f"Zip:    {ZIP_PATH} ({ZIP_PATH.stat().st_size:,} bytes)")
    print(f"SHA256: {zip_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
