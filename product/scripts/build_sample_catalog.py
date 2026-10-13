"""Build a human-readable catalog of the fatbeans replay corpus (data/samples/fatbeans/).

The corpus has 600+ samples whose names encode hero/map/rounds but NOT truth-usability or
key attributes (gold-scan present? gold/red-heavy? settled depth?). This scans each sample,
parses settlement truth, and emits data/samples/CATALOG.md so any future work (esp. the
multi-hero / draw-frequency estimation) can pick test samples by attribute instead of
guessing from filenames. Re-run after adding samples:  python scripts/build_sample_catalog.py

Naming convention (decoded here): fatbeans_<valid|mixed>_<hero>_<map>_<N>rounds_<map>_<session>_<seq>.json
  valid = single clean capture; mixed = multi/merged-source capture (both settle with truth).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import _minimap_grid_items, load_monitor_tables  # noqa: E402
from geometry_eyeball_reports import _grid_bottom  # noqa: E402

FATBEANS = ROOT / "data/samples/fatbeans"
OUT = ROOT / "data/samples/CATALOG.md"
# gold-scan exact-info ids (lock q5 cells/count) — see fatbeans.py:146
GOLD_SCAN_IDS = {200011, 200019}
TABLES = load_monitor_tables()
NAME_RE = re.compile(r"fatbeans_(valid|mixed)_([a-z]+)_(\d+)_(\d+)rounds_")


def main() -> int:
    rows = []
    by_hero: dict[str, int] = defaultdict(int)
    n = parsed = 0
    for f in sorted(FATBEANS.glob("*.json")):
        n += 1
        m = NAME_RE.search(f.name)
        kind = m.group(1) if m else "?"
        hero = m.group(2) if m else "?"
        rounds = int(m.group(4)) if m else 0
        by_hero[hero] += 1
        tc = ti = inv = depth = 0
        gold_scan = False
        try:
            events = parse_fatbeans_capture(f)
            s = _settlement_breakdown(events)
            tc, ti = int(s.get("total_cells") or 0), int(s.get("total_items") or 0)
            cells = s.get("cells") or {}
            inv = int(cells.get(5, 0)) + int(cells.get(6, 0))
            batches = live_batches_from_fatbeans_events(events)
            depth = _grid_bottom(_minimap_grid_items(batches, TABLES.items)) or 0
            mid = _map_id_from_events(events)
            # gold-scan = any pre-settlement public_info row carrying a gold exact id
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            from regenerate_checkpoint_matrices import _snapshot_at_batch
            for i in range(1, len(pre) + 1):
                snap = _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=mid)
                if any(isinstance(r, dict) and r.get("info_id") in GOLD_SCAN_IDS
                       for r in (snap.get("public_info_rows") or [])):
                    gold_scan = True
                    break
            if tc > 0 and ti > 0:
                parsed += 1
        except Exception:
            pass
        inv_pct = (inv / tc * 100) if tc else 0
        rows.append((hero, kind, m.group(3) if m else "?", rounds, tc, ti, inv, inv_pct, depth, gold_scan, f.name))
        if n % 50 == 0:
            print(f"... {n} scanned", flush=True)

    rows.sort(key=lambda r: (r[0], r[2], r[3]))
    out = [
        "# Fatbeans 回放样本目录（自动生成）",
        "",
        f"`scripts/build_sample_catalog.py` 扫描 `data/samples/fatbeans/`（{n} 份，{parsed} 份有结算真值）。",
        "无效样本另见 `data/samples/fatbeans_invalid/`（no_bid/no_state/parse_error）。",
        "列：hero · kind(valid/mixed) · map · rounds · 真值总格/总件 · 金红不可见格(及%) · 结算深度 · 金扫(200011/19)",
        "**挑测样本**：truth>0 才可当 ground-truth；金红%高=艾莎盲区大；gold_scan=Y 可验金硬锁；depth≤8=浅仓。",
        "",
        "## 各英雄数量",
        "",
        "| hero | 样本数 |", "|---|---|",
    ]
    for h in sorted(by_hero):
        out.append(f"| {h} | {by_hero[h]} |")
    out += ["", "## 明细", "",
            "| hero | kind | map | R | 总格 | 总件 | 金红格(%) | 深 | 金扫 | 文件 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for hero, kind, mp, rounds, tc, ti, inv, inv_pct, depth, gs, name in rows:
        truth = f"{tc}/{ti}" if tc else "—(无真值)"
        out.append(f"| {hero} | {kind} | {mp} | {rounds} | {tc or '—'} | {ti or '—'} | "
                   f"{inv}({inv_pct:.0f}%) | {depth or '—'} | {'Y' if gs else ''} | `{name}` |")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"WROTE {OUT}  ({n} samples, {parsed} with truth)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
