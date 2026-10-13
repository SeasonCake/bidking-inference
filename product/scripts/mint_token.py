"""管理员签发公版限期激活 token(确认付款后用)。撤销靠服务端 revoked.txt。

公版不再签发永久 token,必须显式给出且只给一个时长:
  · 【限期(天)】(--days N):  python scripts/mint_token.py --uid <买家标识> --days 30
  · 【限期(小时)】(--hours N):python scripts/mint_token.py --uid <买家标识> --hours 3
      限期 = 从【首次激活】起算 N 天/小时后失效(关着也走)。普通档最长 365 天。

  · 【网吧/换机】加 --roaming: 同一 token 换电脑也能用(换设备重新绑定, 不拒)。默认不加 = 设备绑定防转卖。
      典型: python scripts/mint_token.py --uid wangba01 --hours 3 --roaming

  # 买家标识建议用可追踪的: 订单号 / QQ号 / 群昵称, 便于日后撤销/对账。
输出一行 token, 发给买家。买家在计算器里激活时填入。

撤销某人: 把其 uid 追加到 revoked.txt(每行一个) -> 下次激活即被拒。
换设备(人工申诉, 非 roaming): 编辑 bindings.json 删掉该 uid 那条 -> 其下次激活会重新绑新设备。
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import activation_core


MAX_STANDARD_SECONDS = 365 * 86400
MAX_ROAMING_SECONDS = 365 * 86400


def _duration_seconds(days: float | None, hours: float | None, *, roaming: bool) -> tuple[int, str]:
    """验证单发档位并返回 (时长秒数,显示标签);不允许产生新的永久 token。"""
    if days is None and hours is None:
        raise ValueError("公版不再签发永久 token;必须给 --days 或 --hours")
    if days is not None and hours is not None:
        raise ValueError("--days 与 --hours 只能给一个")

    value = days if days is not None else hours
    assert value is not None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("授权时长须为有限正数")
    seconds = int(value * (86400 if days is not None else 3600))
    if seconds <= 0:
        raise ValueError("授权时长不足 1 秒")

    maximum = MAX_ROAMING_SECONDS if roaming else MAX_STANDARD_SECONDS
    if seconds > maximum:
        label = "365 天"
        raise ValueError(f"{'可漫游' if roaming else '公版限期'}授权最长 {label}")
    unit = "天" if days is not None else "小时"
    return seconds, f"限期 {value:g} {unit}"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="签发公版限期激活 token(普通/可漫游最长 365 天)")
    ap.add_argument("--uid", required=True, help="买家标识(订单号/QQ/昵称, 用于撤销与对账)")
    ap.add_argument("--days", type=float, default=None,
                    help="限期天数(从首次激活起算; 如 30=1个月会员)。与 --hours 二选一")
    ap.add_argument("--hours", type=float, default=None,
                    help="限期小时数(从首次激活起算; 如 3=3小时临时/网吧)。与 --days 二选一")
    ap.add_argument("--roaming", action="store_true",
                    help="可换机: 同一 token 换电脑也能用(网吧场景)。默认不加=设备绑定防转卖")
    ap.add_argument("--key-file", default="activation_key.txt", help="激活密钥文件(gitignore, 不入包)")
    a = ap.parse_args(argv[1:])
    try:
        duration, span = _duration_seconds(a.days, a.hours, roaming=bool(a.roaming))
    except ValueError as exc:
        print(f"档位错误: {exc}", file=sys.stderr)
        return 1
    p = Path(a.key_file)
    if not p.is_file():
        print(f"找不到激活密钥文件 {p} (服务端工作目录下应有 activation_key.txt)", file=sys.stderr)
        return 1
    secret = p.read_bytes().strip()
    if not secret:
        print("activation_key.txt 为空", file=sys.stderr)
        return 1
    iat = int(time.time())
    exp = iat + duration
    token = activation_core.mint_token(a.uid, secret, iat=iat, exp=exp, roaming=a.roaming)
    kind = span + ("·可换机" if a.roaming else "") + f"(到期 ts={exp})"
    print(token)
    print(f"# [{kind}] uid={a.uid}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
