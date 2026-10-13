"""⚠ 归档实验脚本(早期阶段)。校准请优先用 scripts/calibration/ 工具包(核心 aisha_calib.py)。
后续窗口注意:
  - 量 count/grid/avg gap(非价值)，长尾规范化不涉及；样本=archive 导出 zip。
  - grid-count 环结论已进 memory(value-accuracy-q6-count-undercount 等)。
  - 若改成评估 VALUE，必须改用 aisha_calib.normalized_truth(裸真值含长尾→假欠估)。

TASK C: quantify the grid-count loop + the avg gap for aisha R3/R4.

Per aisha R3/R4 sample (library exports), dump:
  engine count (total_count_estimate), engine grid (total_grid_range mid),
  engine avg (grid/count), total_avg_cells used (evidence.avg_cells['total'] if present),
  truth count, truth cells, truth avg (truth_cells/truth_count),
  visible-item-size features from the bidding-round minimap.

Reports: systematic avg gap (engine - truth) and how count_over correlates with
(a) the avg gap and (b) absence of large visible items.
"""
import sys, os, zipfile, json, glob, re, statistics
for p in ['scripts', 'hero_ref/src', 'hero_ref/tools', 'src']:
    if p not in sys.path:
        sys.path.insert(0, p)

from ahmad_ref_engine import (
    prepare_reference_engine_snapshot, run_reference_engine,
    _minimap_items_from_snapshot, _safe_int,
)

ARCHIVE = 'data/samples/hero_ref/archive/exports'


def item_cells(item):
    c = _safe_int(item.get('cells'))
    if c is not None and c > 0:
        return int(c)
    w = _safe_int(item.get('width')) or 1
    h = _safe_int(item.get('height')) or 1
    return max(1, int(w) * int(h))


def visible_size_feats(snap):
    items = _minimap_items_from_snapshot(snap)
    sizes = [item_cells(it) for it in items]
    sizes = [s for s in sizes if s > 0]
    if not sizes:
        return dict(n_vis=0, max_cells=0, mean_cells=0.0,
                    frac_large8=0.0, frac_large10=0.0, n_large8=0)
    n = len(sizes)
    return dict(
        n_vis=n,
        max_cells=max(sizes),
        mean_cells=sum(sizes) / n,
        frac_large8=sum(1 for s in sizes if s >= 8) / n,
        frac_large10=sum(1 for s in sizes if s >= 10) / n,
        n_large8=sum(1 for s in sizes if s >= 8),
    )


def session_id_of(path):
    # trailing _<digits>.zip after map id; use the long session digits to dedup
    m = re.search(r'_(\d{10,})\.zip$', path)
    return m.group(1) if m else os.path.basename(path)


def collect():
    rows = []
    seen = set()  # (session_id, round) dedup
    zips = sorted(glob.glob(os.path.join(ARCHIVE, '*', '*.zip')))
    for z in zips:
        try:
            zf = zipfile.ZipFile(z)
        except Exception:
            continue
        names = set(zf.namelist())
        sett_name = next((n for n in names if n.endswith('_settled.json')), None)
        if not sett_name:
            continue
        try:
            sett = json.loads(zf.read(sett_name))
        except Exception:
            continue
        if (sett.get('hero') or '').lower() != 'aisha':
            continue
        map_id = str(sett.get('map_id') or '')
        if map_id.startswith('26'):  # hidden 26xx maps excluded per knowability spec
            continue
        truth_count = sett.get('inventory_count')
        truth_cells = sett.get('inventory_cells')
        if not truth_count or not truth_cells:
            continue
        sid = session_id_of(z)
        for rnd in (3, 4):
            bn = f'round_snapshots/r0{rnd}_bidding.json'
            if bn not in names:
                continue
            key = (sid, rnd, map_id)
            if key in seen:
                continue
            try:
                snap = json.loads(zf.read(bn))
            except Exception:
                continue
            if (snap.get('hero') or '').lower() != 'aisha':
                continue
            try:
                res = run_reference_engine(prepare_reference_engine_snapshot(snap))
            except Exception:
                continue
            d = res.as_dict()
            ec = d.get('total_count_estimate')
            gr = d.get('total_grid_range')
            if ec is None or not gr or gr[1] is None:
                continue
            seen.add(key)
            egrid = gr[1]
            eavg = egrid / ec if ec else None
            tavg = truth_cells / truth_count
            total_avg_used = None
            try:
                total_avg_used = res.evidence.avg_cells.get('total')
            except Exception:
                pass
            feats = visible_size_feats(snap)
            rows.append(dict(
                map_id=map_id, sid=sid, round=rnd,
                engine_count=ec, truth_count=truth_count,
                count_over=ec - truth_count,
                engine_grid=egrid, truth_cells=truth_cells,
                grid_over=egrid - truth_cells,
                engine_avg=eavg, truth_avg=tavg,
                avg_gap=(eavg - tavg) if eavg is not None else None,
                total_avg_used=total_avg_used,
                **feats,
            ))
    return rows


def corr(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    xs2 = [p[0] for p in pts]; ys2 = [p[1] for p in pts]
    try:
        return statistics.correlation(xs2, ys2)
    except Exception:
        return None


def fmt(v, n=2):
    return 'NA' if v is None else f'{v:.{n}f}'


def main():
    rows = collect()
    print(f'aisha R3/R4 samples (deduped, 26xx excluded): {len(rows)}')
    for rnd in (3, 4):
        rr = [r for r in rows if r['round'] == rnd]
        if not rr:
            continue
        co = [r['count_over'] for r in rr]
        ag = [r['avg_gap'] for r in rr if r['avg_gap'] is not None]
        go = [r['grid_over'] for r in rr]
        print(f'\n=== R{rnd}  n={len(rr)} ===')
        print(f'  count_over  mean={statistics.mean(co):+.2f}  median={statistics.median(co):+.1f}')
        print(f'  grid_over   mean={statistics.mean(go):+.2f}  median={statistics.median(go):+.1f}')
        print(f'  avg_gap(eng-truth) mean={statistics.mean(ag):+.3f}  median={statistics.median(ag):+.3f}')
        print(f'  engine_avg mean={statistics.mean([r["engine_avg"] for r in rr]):.3f}'
              f'  truth_avg mean={statistics.mean([r["truth_avg"] for r in rr]):.3f}')
        ntot = sum(1 for r in rr if r['total_avg_used'] is not None)
        print(f'  samples with evidence avg_cells["total"] present: {ntot}/{len(rr)}')
        # correlations
        c_ag = corr([r['avg_gap'] for r in rr], [r['count_over'] for r in rr])
        c_max = corr([r['max_cells'] for r in rr], [r['count_over'] for r in rr])
        c_mean = corr([r['mean_cells'] for r in rr], [r['count_over'] for r in rr])
        c_fl = corr([r['frac_large8'] for r in rr], [r['count_over'] for r in rr])
        print(f'  corr(avg_gap, count_over)      = {fmt(c_ag)}')
        print(f'  corr(max_vis_cells, count_over)= {fmt(c_max)}')
        print(f'  corr(mean_vis_cells,count_over)= {fmt(c_mean)}')
        print(f'  corr(frac_large8,  count_over) = {fmt(c_fl)}')
        # bin by presence of large visible items
        no_large = [r for r in rr if r['max_cells'] < 8]
        has_large = [r for r in rr if r['max_cells'] >= 8]
        for label, grp in (('no large vis (max<8)', no_large), ('has large vis (max>=8)', has_large)):
            if grp:
                print(f'  [{label}] n={len(grp)} count_over mean={statistics.mean([r["count_over"] for r in grp]):+.2f}'
                      f'  avg_gap mean={statistics.mean([r["avg_gap"] for r in grp]):+.3f}'
                      f'  truth_avg mean={statistics.mean([r["truth_avg"] for r in grp]):.2f}')
    # combined R3+R4
    print('\n=== R3+R4 combined ===')
    co = [r['count_over'] for r in rows]
    ag = [r['avg_gap'] for r in rows]
    print(f'  count_over mean={statistics.mean(co):+.2f}  avg_gap mean={statistics.mean(ag):+.3f}')
    print(f'  corr(avg_gap, count_over) = {fmt(corr(ag, co))}')
    print(f'  corr(max_vis_cells, count_over) = {fmt(corr([r["max_cells"] for r in rows], co))}')
    # avg-gap binned by max visible item size
    print('\n  avg_gap & count_over by max visible item cells:')
    bins = [(0, 4, '1-3'), (4, 6, '4-5'), (6, 8, '6-7'), (8, 12, '8-11'), (12, 99, '12+')]
    for lo, hi, lab in bins:
        grp = [r for r in rows if lo <= r['max_cells'] < hi]
        if grp:
            print(f'    max_cells {lab:>5}: n={len(grp):2d}  count_over={statistics.mean([r["count_over"] for r in grp]):+.2f}'
                  f'  avg_gap={statistics.mean([r["avg_gap"] for r in grp]):+.3f}'
                  f'  engine_avg={statistics.mean([r["engine_avg"] for r in grp]):.2f}'
                  f'  truth_avg={statistics.mean([r["truth_avg"] for r in grp]):.2f}')
    # dump a few extreme rows
    print('\n  worst over-count rows:')
    for r in sorted(rows, key=lambda r: -r['count_over'])[:8]:
        print(f'    {r["map_id"]} R{r["round"]} ec={r["engine_count"]} tc={r["truth_count"]} '
              f'over={r["count_over"]:+d} eavg={r["engine_avg"]:.2f} tavg={r["truth_avg"]:.2f} '
              f'gap={r["avg_gap"]:+.2f} maxvis={r["max_cells"]} meanvis={r["mean_cells"]:.1f}')
    # persist raw
    with open('scripts/task_c_rows.json', 'w') as f:
        json.dump(rows, f, indent=0)
    print(f'\n  raw rows -> scripts/task_c_rows.json ({len(rows)})')


if __name__ == '__main__':
    main()
