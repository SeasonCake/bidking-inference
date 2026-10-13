"""坏真值样本隔离 —— 公开权威值 vs 结算真值不一致 = 结算不全, 移出样本库。

判据(aisha_calib.public_exact_count_mismatches): 公开面板的权威值与结算真值必须一致 ——
  · per-品质精确件数 200018紫 / 200019金(极品存量) / 200020红
  · 公开总格 200009 / 公开总件数 200017
全库扫描显示 ~90-97% 完全相等; 不符的是抓包结算不全(漏件) → 真值不可信, 且会让合成样本
R4「技能锁=坏真值」与公开值硬冲突。见 memory fatbeans-mixed-samples-bad-truth /
samples-version-era-partition-old-maps-new-rates。用户 2026-06-27 追加: 公开总格也查。

用法:
  python scripts/calibration/quarantine_badtruth_samples.py            # dry-run, 只报告
  python scripts/calibration/quarantine_badtruth_samples.py --apply    # 移到隔离区 + 写 manifest

隔离区: data/samples/fatbeans/_quarantine_badtruth/  (在 fatbeans 主目录之外的子目录, 不被
*.json 主扫描/valid_aisha glob 命中)。manifest 记录原路径+不符明细, 可逆。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("scripts", "scripts/calibration", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import aisha_calib as C  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
QUARANTINE_DIR = FATBEANS_DIR / "_quarantine_badtruth"


def _hero(name: str) -> str:
    for h in ("aisha", "ahmed", "tatiana", "gabriela", "sophie", "ethan", "raven", "ahmad"):
        if h in name:
            return h
    return "?"


def scan() -> list[dict]:
    bad: list[dict] = []
    for f in sorted(glob.glob(str(FATBEANS_DIR / "*.json"))):
        name = Path(f).name
        try:
            ev = parse_fatbeans_capture(Path(f))
        except Exception:
            continue
        # keep real mismatches (public info_id 200xxx + unrecoverable kind info_id=0); drop no_settlement(-1)
        mm = [m for m in C.public_exact_count_mismatches(ev) if int(m.get("info_id", -1)) >= 0]
        if mm:
            bad.append({"path": f, "name": name, "hero": _hero(name), "mismatches": mm})
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually move bad samples to the quarantine dir")
    args = ap.parse_args(argv[1:])

    bad = scan()
    by_hero: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    for b in bad:
        by_hero[b["hero"]] += 1
        for m in b["mismatches"]:
            by_kind[m["kind"]] += 1
    print(f"扫描 {FATBEANS_DIR}: 坏真值(公开权威值≠结算) = {len(bad)} 个")
    print(f"  by hero: {dict(by_hero)}")
    print(f"  by kind: {dict(by_kind)}")
    for b in bad:
        ms = "; ".join(f"{m['kind']}:pub{m['public']}≠真{m['truth']}(R{m.get('round')})" for m in b["mismatches"])
        print(f"  [{b['hero']}] {b['name'][:54]}  {ms}")

    if not args.apply:
        print("\n(dry-run; 加 --apply 实际移动到隔离区)")
        return 0

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = QUARANTINE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    moved = 0
    for b in bad:
        src = Path(b["path"])
        dst = QUARANTINE_DIR / src.name
        if not src.exists():
            continue
        src.rename(dst)
        manifest.append({"name": src.name, "hero": b["hero"], "mismatches": b["mismatches"],
                         "original": str(src.relative_to(ROOT))})
        moved += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[apply] 移动 {moved} 个到 {QUARANTINE_DIR}; manifest={manifest_path} (共 {len(manifest)} 条, 可逆)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
