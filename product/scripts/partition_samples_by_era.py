"""Build full era manifest + move-plan for new-rate old-map samples.
Era boundary: match date >= 2026-06-25 => NEW drop rates (post-25 version).
Match date is parsed from the filename ONLY (npcap_live_<date> / desktop_rec_<date>),
NOT the import-batch group name. 56xx = new ranked content (always NEW).
"""
import re, glob, os, json
from collections import defaultdict
# 版本分水岭 = 2026-06-25 12:00:00(作者: 25号中午12点)。此前=旧爆率(OLD,用旧 drop 表),
# 此后=新爆率(NEW,用新 drop 表)。npcap_live 文件名带 HHMMSS 精确到秒;desktop_rec 仅日期。
RE_NPCAP=re.compile(r"npcap_live_(\d{4})-(\d{2})-(\d{2})_(\d{6})_(\d{3,4})_")
RE_NPCAP_HERO=re.compile(r"npcap_live_(\d{4})-(\d{2})-(\d{2})_(\d{6})_[a-z]+_(\d{3,4})_")
RE_DESK =re.compile(r"desktop_rec_(\d{4})(\d{2})(\d{2})_(\d{3,4})_")
# Canonical library name (organize_fatbeans_real_samples output) drops the date but KEEPS
# hero+map: fatbeans_valid_ahmed_2101_1rounds_2101_<sid>_0003.json. Parse the map from it so
# canonical files aren't left map=None; recover the DATE via the session_dates index below.
RE_CANON=re.compile(r"fatbeans_(?:valid|mixed)_[a-z]+_(\d{3,4})_")
SID=re.compile(r"_(\d{16})")
B=(2026,6,25,12,0,0)  # 中午12点
# session_id -> "YYYY-MM-DD HH:MM:SS" recovered from ORIGINAL npcap capture filenames (which carry
# the date the canonical rename strips). Lets nodate canonical files get their true era instead of
# defaulting to OLD. Built by build session_dates index from new_recordings originals; optional.
def _load_session_dates():
    for p in sorted(glob.glob('data/sample_manifests/session_dates_*.json'), reverse=True):
        try: return json.load(open(p, encoding='utf-8'))
        except Exception: pass
    return {}
SESSION_DATES=_load_session_dates()
def _dt6_from_str(s):
    try:
        d,t=s.split(' '); y,mo,dd=d.split('-'); hh,mi,ss=t.split(':')
        return (int(y),int(mo),int(dd),int(hh),int(mi),int(ss))
    except Exception: return None
def parse(bn):
    for r in (RE_NPCAP, RE_NPCAP_HERO):
        m=r.search(bn)
        if m:
            t=m.group(4)  # HHMMSS
            return (int(m.group(1)),int(m.group(2)),int(m.group(3)),int(t[:2]),int(t[2:4]),int(t[4:6])), m.group(5)
    m=RE_DESK.search(bn)  # desktop_rec 无时间: 用日期, 当天按中午(边界)处理
    if m:
        return (int(m.group(1)),int(m.group(2)),int(m.group(3)),12,0,0), m.group(4)
    return None
rows=[]; move=[]
for f in glob.glob('data/samples/*/*.json'):
    bn=os.path.basename(f); g=os.path.basename(os.path.dirname(f))
    p=parse(bn)
    sm=SID.search(bn); sid=sm.group(1) if sm else None
    if not p:
        # Recover date from the session_dates index (canonical rename stripped it).
        cm=RE_CANON.search(bn); cmap=cm.group(1) if cm else None
        sdt=SESSION_DATES.get(sid) if sid else None
        dt6=_dt6_from_str(sdt) if sdt else None
        if dt6 is not None:
            era='NEW' if dt6>=B else 'OLD'
            fam=cmap[:2] if cmap else None
            rows.append(dict(path=f.replace(os.sep,'/'),group=g,session=sid,map=cmap,family=fam,date=sdt,era=era,src='sessiondate'))
            if era=='NEW' and fam not in (None,'56'):
                move.append(f.replace(os.sep,'/'))
            continue
        # no-date + no session-date: 56xx group's manual ethan files are NEW; everything else = OLD pre-25
        if g=='group_0625_56xx':
            rows.append(dict(path=f.replace(os.sep,'/'),group=g,session=sid,map=cmap,family='56',date=None,era='NEW',src='nodate-56xx-manual'))
        else:
            rows.append(dict(path=f.replace(os.sep,'/'),group=g,session=sid,map=cmap,family=(cmap[:2] if cmap else None),date=None,era='OLD',src='nodate'))
        continue
    dt6,mp=p
    y,mn,d=dt6[0],dt6[1],dt6[2]
    era='NEW' if dt6>=B else 'OLD'
    fam=mp[:2]
    rows.append(dict(path=f.replace(os.sep,'/'),group=g,session=sid,map=mp,family=fam,date='%04d-%02d-%02d %02d:%02d:%02d'%dt6,era=era,src='dated'))
    # move plan: NEW-era, NON-56xx, currently sitting in a mixed/old group
    if era=='NEW' and fam!='56':
        move.append(f.replace(os.sep,'/'))

# summary
summ=defaultdict(int)
for r in rows: summ[(r['era'], r['family'])]+=1
manifest=dict(
    boundary='2026-06-25 12:00:00 (>= is NEW drop rates; before noon 06-25 = OLD)',
    generated_for='version-era distinction of drop-rate samples',
    total=len(rows),
    new_count=sum(1 for r in rows if r['era']=='NEW'),
    old_count=sum(1 for r in rows if r['era']=='OLD'),
    new_oldmap_count=len(move),
    rows=rows,
)
os.makedirs('data/sample_manifests', exist_ok=True)
with open('data/sample_manifests/era_partition_noon_2026-06-27.json','w',encoding='utf-8') as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=1)
with open('data/sample_manifests/newrate_oldmap_move_plan.txt','w',encoding='utf-8') as fh:
    fh.write("\n".join(move))
print('manifest rows=%d  NEW=%d OLD=%d  new-oldmap-to-move=%d'%(len(rows),manifest['new_count'],manifest['old_count'],len(move)))
print('move plan written:', len(move), 'files')
# show move plan family x source group
mg=defaultdict(lambda: defaultdict(int))
for r in rows:
    if r['era']=='NEW' and r['family']!='56' and r['family'] is not None:
        mg[r['group']][r['family']+'xx']+=1
for g in sorted(mg): print('  from %-24s %s'%(g, dict(mg[g])))
