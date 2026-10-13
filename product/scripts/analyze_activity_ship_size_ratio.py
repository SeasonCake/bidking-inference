"""Activity-ship warehouse-size ratio: base sealed-cabin (2501-2510) vs activity
(2521-2530 old activity, 2531 new) shipwreck maps.

Per memory the activity ship's only change is a larger warehouse (server-driven
total, engine reads it from the wire), so the settled total_cells distribution
is the ground truth for the "size boost ratio". We dedup by game session id and
take the deepest-round window per session to guarantee a settled frame.

Usage:
    python scripts/analyze_activity_ship_size_ratio.py [--limit-base N] [--timeout-s S]
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "hero_ref" / "src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from audit_aisha_gap import _settlement_breakdown, _map_id_from_events  # noqa: E402

SESSION_RE = re.compile(r"_(\d{4})_(\d+rounds)_\d{4}_(\d{15,})_(\d{4})\.json$")


def _family(mid: int) -> str | None:
    if 2501 <= mid <= 2510:
        return "明拍-base 2501-2510 (基础密封舱·活动前)"
    if 2521 <= mid <= 2530:
        return "明拍-activity 2521-2530 (老活动密封舱)"
    if 2531 <= mid <= 2540:
        return "明拍-activity 2531-2540 (新活动密封舱)"
    if 4501 <= mid <= 4510:
        return "暗拍-activity 4501-4510 (深海·无活动前基线)"
    if 4521 <= mid <= 4530:
        return "暗拍-activity 4521-4530 (深海老活动)"
    return None


def _rounds_num(name: str) -> int:
    m = re.search(r"_(\d+)rounds_", name)
    return int(m.group(1)) if m else 0


def _collect_files() -> dict[str, Path]:
    """One file per (session id): pick the deepest-round window."""
    best: dict[str, tuple[int, Path]] = {}
    dirs = [ROOT / "data/samples/fatbeans", ROOT / "data/samples/fatbeans_activity_20260605_shipwreck"]
    for d in dirs:
        if not d.exists():
            continue
        for fp in d.glob("*.json"):
            m = SESSION_RE.search(fp.name)
            if not m:
                continue
            mid = int(m.group(1))
            if _family(mid) is None:
                continue
            sess = m.group(3)
            r = _rounds_num(fp.name)
            if sess not in best or r > best[sess][0]:
                best[sess] = (r, fp)
    return {s: v[1] for s, v in best.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-base", type=int, default=0, help="cap base-ship sessions (0=all)")
    ap.add_argument("--timeout-s", type=float, default=240.0)
    args = ap.parse_args()

    files = _collect_files()
    # order: activity first (cheap, must-have), then base
    items = sorted(files.items(), key=lambda kv: _family(int(SESSION_RE.search(kv[1].name).group(1))) or "")
    print(f"unique sessions (deepest window each): {len(items)}", flush=True)

    by_fam: dict[str, list[tuple[int, int]]] = defaultdict(list)  # fam -> [(cells, mid)]
    t0 = time.time()
    done = 0
    base_seen = 0
    for sess, fp in items:
        mid = int(SESSION_RE.search(fp.name).group(1))
        fam = _family(mid)
        if fam is None:
            continue
        if fam.startswith("base") and args.limit_base and base_seen >= args.limit_base:
            continue
        if time.time() - t0 > args.timeout_s:
            print(f"[timeout {args.timeout_s}s after {done} parsed] stopping early", flush=True)
            break
        try:
            events = parse_fatbeans_capture(str(fp))
            sb = _settlement_breakdown(events)
            tc = int(sb.get("total_cells") or 0)
        except Exception as e:  # noqa: BLE001
            print(f"  ! parse fail {fp.name}: {e}", flush=True)
            continue
        if tc <= 0:
            continue
        by_fam[fam].append((tc, mid))
        if fam.startswith("base"):
            base_seen += 1
        done += 1
        if done % 25 == 0:
            print(f"  parsed {done} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n=== settled total_cells by family ({done} games, {time.time()-t0:.0f}s) ===")
    base_med = None
    for fam in sorted(by_fam):
        cells = sorted(c for c, _ in by_fam[fam])
        if not cells:
            continue
        md = median(cells)
        if "base" in fam:
            base_med = md
        print(f"\n{fam}  n={len(cells)}")
        print(f"  min={cells[0]}  p25={cells[len(cells)//4]}  median={md:.0f}  "
              f"mean={mean(cells):.0f}  p75={cells[3*len(cells)//4]}  max={cells[-1]}")
        # distinct map ids present
        mids = sorted({m for _, m in by_fam[fam]})
        print(f"  maps={mids}")

    if base_med:
        print(f"\n=== 提升比例 (median vs base median {base_med:.0f}) ===")
        for fam in sorted(by_fam):
            if "base" in fam or not by_fam[fam]:
                continue
            md = median([c for c, _ in by_fam[fam]])
            print(f"  {fam}: median {md:.0f}  →  ×{md/base_med:.2f}")


if __name__ == "__main__":
    main()
