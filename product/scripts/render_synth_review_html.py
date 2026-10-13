"""抽样把已生成的合成样本渲染成一页 HTML, 供作者肉眼复核生成有效性(技能/道具/公开/真值/era)。
每个样本: 来源+era+轮次 + 逐轮揭示明细(技能逐档轮廓/道具/公开) + 彩色矩阵(真值底色+揭示叠加)
+ 引擎该轮估计 + 最终真值。OLD/NEW 分层抽样。

用法: python scripts/render_synth_review_html.py [n=25]
输出: data/reports/synth_aisha_review_sample.html (浏览器/VSCode 预览打开)
"""
from __future__ import annotations

import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from synthesize_draw_hero_samples import (  # noqa: E402
    QUALITY_SYMBOLS,
    _engine_est_md,
    _matrix_legend_html,
    _public_info_md,
    _reveal_matrices_html,
    _round_events_md,
    load_warehouse,
    reveal_trace,
    synthesize_snapshot,
)
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

SYNTH_DIR = ROOT / "data/samples/synthetic_draw/aisha"
OUT = ROOT / "data/reports/synth_aisha_review_sample.html"


def _find_source(name: str) -> Path | None:
    for d in ("data/samples/fatbeans", "data/samples/group_post0625_newrate_classic"):
        p = ROOT / d / name
        if p.is_file():
            return p
    hits = glob.glob(str(ROOT / "data/samples/**" / name), recursive=True)
    return Path(hits[0]) if hits else None


def _md_to_html(lines: list[str]) -> str:
    """轻量 md -> html(只处理 **bold**, 列表, 换行), 够复核用。"""
    import re
    out = []
    for ln in lines:
        ln = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        if ln.strip().startswith("- ") or ln.strip().startswith("  - "):
            out.append(f"<div style='margin-left:{12 if ln.startswith('  ') else 0}px'>{ln.strip()[2:]}</div>")
        else:
            out.append(f"<div>{ln}</div>")
    return "".join(out)


def main(argv: list[str]) -> int:
    n = int(argv[1]) if len(argv) > 1 else 25
    records = []
    for f in glob.glob(str(SYNTH_DIR / "synth_aisha_*.json")):
        records.append(json.loads(Path(f).read_text(encoding="utf-8")))
    # 3 桶: 56xx 排位(map//100==56, 高红·重点) / NEW 经典(era NEW 非56xx) / OLD。各取一份, 重点 56xx+NEW。
    def _fam(r):
        m = int(r.get("map_id") or 0)
        if m // 100 == 56:
            return "56xx排位"
        return "NEW经典" if r.get("era") == "NEW" else "OLD"
    buckets = {"56xx排位": [], "NEW经典": [], "OLD": []}
    for r in records:
        buckets[_fam(r)].append(r)
    rng = random.Random(7)
    for v in buckets.values():
        rng.shuffle(v)
    # 56xx 与 NEW经典 各取 ~2/5, OLD 取剩余
    n_each = max(8, n * 2 // 5)
    picks = (buckets["56xx排位"][:n_each] + buckets["NEW经典"][:n_each]
             + buckets["OLD"][: max(0, n - 2 * n_each)])
    fam_of = {id(r): _fam(r) for r in picks}
    blocks = []
    for i, rec in enumerate(picks):
        src = _find_source(rec["source"])
        if src is None:
            blocks.append(f"<h3>{rec['source']}</h3><p>源文件未找到</p><hr>")
            continue
        wh = load_warehouse(src, with_geometry=True)
        if wh is None:
            blocks.append(f"<h3>{rec['source']}</h3><p>加载失败</p><hr>")
            continue
        rnd = int(rec["round"])
        seed = random.Random((rec["source"], rnd).__hash__() & 0xFFFFFFFF)
        revealed, log = reveal_trace(wh, "aisha", rnd, seed)
        snap = synthesize_snapshot(wh, "aisha", rnd, random.Random((rec["source"], rnd).__hash__() & 0xFFFFFFFF))
        res = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=8000).as_dict()
        tc = {QUALITY_SYMBOLS[q]: wh.truth_counts.get(q, 0) for q in (1, 2, 3, 4, 5, 6) if wh.truth_counts.get(q, 0)}
        truth_cells = sum(int(it.get("cells") or 0) for it in wh.items)
        fam = fam_of.get(id(rec), rec.get("era", "?"))
        fam_color = {"56xx排位": "#ad1457", "NEW经典": "#c62828", "OLD": "#1565c0"}.get(fam, "#555")
        head = (f"<h3>{i+1}. <span style='color:{fam_color}'>[{fam}]</span> "
                f"{rec['source'][:50]} · map {wh.map_id} · 合成到 R{rnd}</h3>"
                f"<p><b>最终真值</b>: 总{wh.total_count}件 · {truth_cells}格 · "
                + " ".join(f"{k}{v}" for k, v in tc.items()) + f" · 总值{wh.truth_total_value:,}</p>")
        events_html = "".join(
            f"<div style='font:12px sans-serif;margin:2px 0'><b>R{e['round']}</b>: "
            + _md_to_html(_round_events_md(wh, e)) + "</div>"
            for e in log
        )
        est_html = _md_to_html(_engine_est_md(res, res).split("\n"))
        blocks.append(
            head
            + _md_to_html(_public_info_md(wh))
            + "<div style='display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap'>"
            + f"<div>{_reveal_matrices_html(wh, revealed)}</div>"
            + f"<div style='max-width:520px'>{events_html}<div style='margin-top:6px'>{est_html}</div></div>"
            + "</div><hr>"
        )
    from collections import Counter
    fam_ct = Counter(fam_of.get(id(r), "?") for r in picks)
    html = ("<html><head><meta charset='utf-8'><title>合成 aisha 抽样复核</title></head><body>"
            f"<h1>合成 aisha 样本抽样复核 — {len(picks)} 个 "
            f"(56xx排位={fam_ct.get('56xx排位',0)} / NEW经典={fam_ct.get('NEW经典',0)} / OLD={fam_ct.get('OLD',0)})</h1>"
            "<p>每个: 来源+族(紫红=56xx排位高红/红=NEW经典/蓝=OLD) + 逐轮揭示(技能逐档白绿蓝紫轮廓/道具/公开) + "
            "矩阵(底色=真值品质满铺, 格内=轮次+来源, 边框=粒度) + 引擎该轮估计 + 最终真值。</p>"
            + _matrix_legend_html() + "<hr>" + "".join(blocks) + "</body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[html] {OUT}  ({len(picks)} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
