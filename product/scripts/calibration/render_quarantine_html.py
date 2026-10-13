"""把隔离区(或任意目录)的坏真值样本渲染成一页 HTML, 让作者目视确认「结算不全」的具体原因:
每个样本显示 结算 minimap(真值满铺品质色, 1-based 10列) + 公开权威值 vs 结算真值的差异。
incomplete-settlement 的样本会明显看到 公开总格/总件数 远大于 minimap 实际填充的格/件。

用法:
  python scripts/calibration/render_quarantine_html.py            # 渲染隔离区全部坏样本
  python scripts/calibration/render_quarantine_html.py <dir|file> # 指定目录/文件
输出: data/reports/quarantine_badtruth_minimaps.html (用浏览器或 VSCode 预览打开)
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("scripts", "scripts/calibration", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import aisha_calib as C  # noqa: E402
from synthesize_draw_hero_samples import (  # noqa: E402
    GRID_COLS,
    QUALITY_BG,
    QUALITY_NAME,
    _item_footprint,
    load_warehouse,
)
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

QUARANTINE_DIR = ROOT / "data/samples/fatbeans/_quarantine_badtruth"
OUT = ROOT / "data/reports/quarantine_badtruth_minimaps.html"
_DARK_TEXT_Q = {1, 5}  # 白/金底用深字


def _minimap_html(wh) -> str:
    """结算 minimap: 底色=真值品质满铺(用真实 footprint geometry), 空格=无藏品。"""
    owner: dict[tuple[int, int], int] = {}
    max_row = 1
    for idx, it in enumerate(wh.items):
        for cell in _item_footprint(it):
            owner[cell] = idx
            max_row = max(max_row, cell[0])
    rows = ['<table style="border-collapse:collapse;font:11px/1.1 monospace;text-align:center">']
    rows.append('<tr><th style="width:20px"></th>'
                + "".join(f'<th style="width:30px;color:#888">{c}</th>' for c in range(1, GRID_COLS + 1)) + "</tr>")
    for r in range(1, max_row + 1):
        cells = [f'<th style="color:#888">r{r}</th>']
        for c in range(1, GRID_COLS + 1):
            idx = owner.get((r, c))
            if idx is None:
                cells.append('<td style="border:1px solid #eee;color:#ddd">·</td>')
                continue
            q = wh.items[idx]["quality"]
            bg = QUALITY_BG.get(q, "#fff")
            txt = "#222" if q in _DARK_TEXT_Q else "#fff"
            cells.append(f'<td style="background:{bg};border:1px solid #999;color:{txt}">'
                         f'{QUALITY_NAME.get(q, "?")}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</table>")
    return "".join(rows)


def _sample_block(path: Path) -> str:
    ev = parse_fatbeans_capture(path)
    wh = load_warehouse(path, with_geometry=True)
    if wh is None:
        return f"<h3>{path.name}</h3><p>无法加载</p>"
    mm = [m for m in C.public_exact_count_mismatches(ev) if int(m.get("info_id", 0)) > 0]
    filled = len({cell for it in wh.items for cell in _item_footprint(it)})
    tc = " ".join(f"{QUALITY_NAME[q]}{wh.truth_counts.get(q, 0)}"
                  for q in (1, 2, 3, 4, 5, 6) if wh.truth_counts.get(q, 0))
    # public info table
    pub_rows = "".join(
        f"<tr><td>{r['label']}({r['info_id']})</td><td>{r.get('value')}</td></tr>"
        for r in wh.public_info_rows if r.get("gran") is None
    )
    mm_html = "<br>".join(
        f"<b style='color:#c00'>{m['kind']}: 公开={m['public']} ≠ 结算真值={m['truth']}</b> (出现轮次 R{m.get('round')})"
        for m in mm
    )
    return (
        f"<h3>{path.name}</h3>"
        f"<div style='display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap'>"
        f"<div>{_minimap_html(wh)}</div>"
        f"<div style='font:13px sans-serif'>"
        f"<p>{mm_html}</p>"
        f"<p>结算真值: 总 <b>{wh.total_count}</b> 件(含q≤0则 {wh.raw_count}) · "
        f"实际填充 <b>{filled}</b> 格(含q≤0则 {wh.raw_cells}) · 品质 {tc}</p>"
        f"<table border=1 cellpadding=3 style='border-collapse:collapse;font:12px monospace'>"
        f"<tr><th>公开信息</th><th>值</th></tr>{pub_rows}</table>"
        f"<p style='color:#666;max-width:340px'>↑ 若公开总格/总件数/某品质件数 远大于结算填充, "
        f"即抓包结算漏件(真值不可信) —— minimap 会明显偏空。</p>"
        f"</div></div><hr>"
    )


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else str(QUARANTINE_DIR)
    tp = Path(target)
    files = [tp] if tp.is_file() else sorted(Path(p) for p in glob.glob(str(tp / "*.json"))
                                             if Path(p).name != "manifest.json")
    blocks = []
    for f in files:
        try:
            blocks.append(_sample_block(f))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"<h3>{f.name}</h3><p>渲染失败: {type(e).__name__}: {e}</p><hr>")
    html = ("<html><head><meta charset='utf-8'><title>坏真值样本 minimap 核对</title></head><body>"
            f"<h1>坏真值样本(公开权威值≠结算真值) — {len(files)} 个</h1>"
            "<p>每个样本: 左=结算 minimap(品质满铺, 空=无藏品), 右=公开声明 vs 结算真值的差异。"
            "incomplete-settlement = 公开值 >> minimap 实际填充。</p><hr>"
            + "".join(blocks) + "</body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[html] {OUT}  ({len(files)} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
