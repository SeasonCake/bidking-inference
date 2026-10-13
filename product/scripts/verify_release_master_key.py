# -*- coding: utf-8 -*-
"""Release-only master-key gate.

The encrypted public package must use the same master key that the activation
service recently delivered to this machine.  This helper compares the selected
key file with the device-bound activation cache without printing either key or
its fingerprint.  It never contacts the server; release operators refresh the
cache by starting the currently deployed client before building.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))


class ReleaseKeyVerificationError(ValueError):
    """The selected release key cannot be tied to a recent activation cache."""


def _decode_key(value: str, *, source: str) -> bytes:
    compact = "".join(value.split())
    try:
        key = base64.b64decode(compact, validate=True)
    except Exception as exc:  # noqa: BLE001 - normalize secret parsing errors
        raise ReleaseKeyVerificationError(f"{source} 不是合法 base64") from exc
    if len(key) != 32:
        raise ReleaseKeyVerificationError(f"{source} 解码后不是 32 字节主密钥")
    return key


def verify_release_key(
    key_file: Path,
    *,
    max_cache_age_hours: float = 6.0,
    now: float | None = None,
) -> float:
    """Return activation-cache age after a secret-safe equality check."""
    if max_cache_age_hours <= 0:
        raise ReleaseKeyVerificationError("max_cache_age_hours 必须大于 0")
    try:
        selected = _decode_key(key_file.read_text(encoding="ascii"), source="候选主密钥文件")
    except OSError as exc:
        raise ReleaseKeyVerificationError("无法读取候选主密钥文件") from exc

    from bidking_lab.secure import client_activate

    device = client_activate.device_fingerprint()
    token = client_activate.resolve_token(None)
    cached = client_activate._load_cache(device=device, token=token)
    if not isinstance(cached, dict):
        raise ReleaseKeyVerificationError(
            "没有可解密的本机激活缓存；请先启动当前线上客户端并确认激活成功"
        )

    current = time.time() if now is None else float(now)
    saved_at = cached.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        raise ReleaseKeyVerificationError("激活缓存缺少 saved_at，无法证明密钥时效")
    age_seconds = current - float(saved_at)
    if age_seconds < -300:
        raise ReleaseKeyVerificationError("激活缓存时间晚于本机时间，拒绝使用")
    age_hours = max(0.0, age_seconds / 3600.0)
    if age_hours > max_cache_age_hours:
        raise ReleaseKeyVerificationError(
            f"激活缓存已超过 {max_cache_age_hours:g} 小时；请先启动当前线上客户端刷新"
        )

    lease = cached.get("lease")
    try:
        lease_exp = float(lease.get("exp") or 0) if isinstance(lease, dict) else 0.0
    except (TypeError, ValueError) as exc:
        raise ReleaseKeyVerificationError("激活缓存 lease.exp 非法") from exc
    if lease_exp <= current:
        raise ReleaseKeyVerificationError("激活缓存 lease 已过期")
    if lease.get("device") != device:
        raise ReleaseKeyVerificationError("激活缓存设备不匹配")

    delivered = _decode_key(str(cached.get("mk") or ""), source="激活缓存主密钥")
    if not hmac.compare_digest(selected, delivered):
        raise ReleaseKeyVerificationError(
            "候选主密钥与近期激活服务下发值不一致；拒绝生成无法上传的公版包"
        )
    return age_hours


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="验证候选主密钥与近期激活缓存一致")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--max-cache-age-hours", type=float, default=6.0)
    args = ap.parse_args(argv)
    try:
        age = verify_release_key(
            Path(args.key_file), max_cache_age_hours=args.max_cache_age_hours
        )
    except ReleaseKeyVerificationError as exc:
        print(f"[verify_release_master_key] FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        "[verify_release_master_key] OK: 候选主密钥匹配近期激活缓存 "
        f"(缓存年龄 {age:.2f} 小时；未输出密钥或指纹)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
