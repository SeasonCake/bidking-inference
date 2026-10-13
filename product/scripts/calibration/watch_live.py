"""实时监视: 边打边滚动看每次估计变化(轮次/阶段 + 格子 + 保/参/激报价 + 红估 + 当前最高价)。

数据源 = monitor 持续写的 hero_ref_ui_summary.jsonl(每次更新一条)。只在估计**变化**时打印一行，
避免 idle 重渲染刷屏。read-only、跟随式(tail -f 风格)。结算轮额外打印真值一行。

用法(打游戏时另开一个终端):
  python scripts/calibration/watch_live.py
  python scripts/calibration/watch_live.py --log data/logs/live/hero_ref_ui_summary.jsonl --all
"""
import argparse
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "data/logs/live/hero_ref_ui_summary.jsonl"

# ui_summary 是显示层: 字段多为格式化字符串("799,491" / "127 / 127 / 127格" / "6 件")。
_NUM = re.compile(r"-?[\d,]+")


def _nums(s):
    if s is None:
        return []
    if isinstance(s, (int, float)):
        return [int(s)]
    return [int(m.replace(",", "")) for m in _NUM.findall(str(s)) if m.strip(",")]


def _int(s):
    ns = _nums(s)
    return ns[0] if ns else None


def _mid(s):
    ns = _nums(s)
    return ns[len(ns) // 2] if ns else None


def _k(v):
    return f"{int(v)//1000}k" if isinstance(v, (int, float)) else "·"


def _row(rec: dict) -> tuple | None:
    ctx = rec.get("context") or {}
    ref = rec.get("reference") or {}
    rnd = ctx.get("round")
    phase = ctx.get("phase")
    grid = _mid(ref.get("total_grid_range"))
    cons, bal, aggr = _int(ref.get("conservative")), _int(ref.get("balanced")), _int(ref.get("aggressive"))
    high = _int(ref.get("current_highest"))
    red = rec.get("red") or {}
    red_txt = ""
    if isinstance(red, dict):
        rc = _mid(red.get("count_range")) if red.get("count_range") else _int(red.get("count"))
        rv = _int(red.get("value_range")) if red.get("value_range") else _int(red.get("value"))
        if rc is not None or rv is not None:
            red_txt = f"红{rc if rc is not None else '·'}件/{_k(rv)}"
    # 变化判定: 报价按 k 取整，避免亚千位抖动重复打印
    key = (rnd, phase, grid, (bal // 1000 if isinstance(bal, int) else None),
           (high // 1000 if isinstance(high, int) else None))
    line = (f"R{rnd}{phase or ''} | 格{grid if grid is not None else '·'} | "
            f"保/参/激 {_k(cons)}/{_k(bal)}/{_k(aggr)} | {red_txt} | 当前最高 {_k(high)}")
    return key, line, rec


def _truth_line(rec: dict) -> str | None:
    t = rec.get("truth") or {}
    vals = t.get("values") or t.get("final_quality_values")
    if not vals:
        return None
    tot = t.get("total_value") or (sum(vals.values()) if isinstance(vals, dict) else None)
    return f"   == 结算真值: {t.get('total_count','?')}件/{t.get('total_cells','?')}格 · {_k(tot)} =="


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--all", action="store_true", help="先回放历史，再跟随")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    log = Path(args.log)
    print(f"watch {log}  (Ctrl-C 退出)\n时间     轮/阶段估计")
    last_key = None
    pos = 0
    if not args.all and log.is_file():
        pos = log.stat().st_size  # 从末尾跟随
    while True:
        try:
            if log.is_file():
                with log.open("r", encoding="utf-8") as f:
                    f.seek(pos)
                    for raw in f:
                        pos = f.tell() if False else pos  # noqa
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        parsed = _row(rec)
                        if not parsed:
                            continue
                        key, line, r = parsed
                        if key == last_key:
                            continue
                        last_key = key
                        ts = (r.get("updated_at_text") or "")[-8:] or time.strftime("%H:%M:%S")
                        print(f"{ts} {line}")
                        tl = _truth_line(r)
                        if tl:
                            print(tl)
                    pos = log.stat().st_size
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n停止。")
            return 0
        except Exception:
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
