"""批量签发激活 token(确认付款前可预生成一批, 卖出时在 CSV 备注栏记买家)。

一条命令出多档, 自动生成 uid + 写 CSV(uid / 类型 / 天数 / 到期日期 / token / 买家备注)。

用法(服务端工作目录, activation_key.txt 在那):
  python3 scripts/batch_mint.py 30d:10 365d:3 7d:5 90d:2
    -> 10 个 30天会员 + 3 个 365天会员 + 5 个 7天 + 2 个 90天

档位写法(冒号后是数量):
  <N>d:数量   N 天会员(日历, 如 30d:10;公版最长 365 天)
  <N>h:数量   N 小时会员(如 3h:20)
可选:
  --prefix vip          uid 前缀(默认 tok)
  --out tokens.csv      输出 CSV 路径(默认 tokens_<时间戳>.csv)
  --key-file 路径       激活密钥(默认 activation_key.txt)

撤销/换机同单发: revoked.txt 加 uid / 删 bindings.json 该 uid。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import activation_core


MAX_STANDARD_SECONDS = 365 * 86400
MAX_ROAMING_SECONDS = 365 * 86400


def _parse_spec(item: str) -> tuple[str, str, int, int]:
    """解析限期档位并返回 (label, tier_tag, duration_seconds, count)。"""
    if ":" not in item:
        raise ValueError(f"档位格式应为 <档>:<数量>, 如 30d:10 / 3h:20 (收到 {item!r})")
    tier, cnt_s = item.split(":", 1)
    tier = tier.strip().lower()
    count = int(cnt_s)
    if count <= 0:
        raise ValueError(f"数量须为正整数: {item!r}")
    if tier in ("perm", "permanent", "0", "0d"):
        raise ValueError("公版不再签发永久 token;历史永久 token 仅保留验证兼容")
    if tier.endswith("h"):
        hours = float(tier[:-1])
        if hours <= 0:
            raise ValueError(f"小时须为正数: {item!r}")
        duration = int(hours * 3600)
        if duration > MAX_STANDARD_SECONDS:
            raise ValueError(f"公版限期授权最长 365 天: {item!r}")
        return f"{hours:g}小时", f"{hours:g}h", duration, count
    if tier.endswith("d"):
        tier = tier[:-1]
    days = float(tier)
    if days <= 0:
        raise ValueError(f"天数须为正数: {item!r}")
    duration = int(days * 86400)
    if duration > MAX_STANDARD_SECONDS:
        raise ValueError(f"公版限期授权最长 365 天: {item!r}")
    return f"{days:g}天会员", f"{days:g}d", duration, count


def _validate_roaming_duration(parsed: list[tuple[str, str, int, int]]) -> None:
    for _label, _tier_tag, duration, _count in parsed:
        if duration > MAX_ROAMING_SECONDS:
            raise ValueError("可漫游授权最长 365 天")


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="批量签发限期 token(公版最长 365 天)")
    ap.add_argument("specs", nargs="+", help="档位:数量, 如 30d:10 365d:3 7d:5 3h:20(h=小时)")
    ap.add_argument("--roaming", action="store_true",
                    help="批内 token 全设【可换机】(公版可漫游授权最长 365 天)")
    ap.add_argument("--prefix", default="tok", help="uid 前缀(默认 tok)")
    ap.add_argument("--out", default=None, help="输出 CSV(默认 tokens_<时间戳>.csv)")
    ap.add_argument("--key-file", default="activation_key.txt", help="激活密钥文件")
    a = ap.parse_args(argv[1:])

    p = Path(a.key_file)
    if not p.is_file():
        print(f"找不到激活密钥文件 {p} (服务端工作目录下应有 activation_key.txt)", file=sys.stderr)
        return 1
    secret = p.read_bytes().strip()
    if not secret:
        print("activation_key.txt 为空", file=sys.stderr)
        return 1

    try:
        parsed = [_parse_spec(s) for s in a.specs]
        if a.roaming:
            _validate_roaming_duration(parsed)
    except ValueError as e:
        print(f"档位解析失败: {e}", file=sys.stderr)
        return 1

    now = int(time.time())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    out_path = Path(a.out) if a.out else Path(f"tokens_{stamp}.csv")

    rows = []
    seq = 0
    for label, tier_tag, dur, count in parsed:
        roaming = bool(a.roaming)
        for _ in range(count):
            seq += 1
            uid = f"{a.prefix}-{tier_tag}-{stamp}-{seq:03d}"
            exp = now + dur
            token = activation_core.mint_token(uid, secret, iat=now, exp=exp, roaming=roaming)
            # 到期"从首次激活+时长"算, CSV 只记时长(生成时不知激活时刻)
            rows.append({"uid": uid, "类型": label + ("·可换机" if roaming else ""),
                         "时长": label,
                         "可换机": ("是" if roaming else "否"),
                         "token": token, "买家备注": ""})

    # UTF-8 BOM 让 Excel 直接认中文
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["uid", "类型", "时长", "可换机", "token", "买家备注"])
        w.writeheader()
        w.writerows(rows)

    # 同时打印 token 到 stdout(便于直接复制)
    for r in rows:
        print(f"{r['类型']:<12} {r['uid']}  ->  {r['token']}")
    print(f"\n# 共 {len(rows)} 个 token, 已写入: {out_path.resolve()}", file=sys.stderr)
    print("# 限期到期【从买家首次激活起算】(故 CSV 只记时长, 不记固定到期日)", file=sys.stderr)
    print("# CSV 列: uid / 类型 / 时长 / 可换机 / token / 买家备注(卖出时填买家QQ)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
