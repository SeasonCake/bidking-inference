# -*- coding: utf-8 -*-
"""构建期助手: 把作者本机 scripts/sample_upload.local.ps1(明文 URL+KEY) 加密成
data/processed/upload_config.enc(BKENC1, AES-256-GCM with 出厂主密钥)进群发包。

群发版形态: 包内只放密文, 无明文 .local.ps1; 运行时 sample_upload._read_encrypted_upload_config
用激活后 activation.data_key()(=同一出厂主密钥, 服务端激活下发)解密 -> HMAC 上传。
moat: 提取包拿不到 key, 仅激活后可解密。

密钥来源: env BIDKING_DATA_KEY(base64(32B)) = 服务端 master_key.txt 内容。
  build_live_portable.ps1 -MasterKeyFile <path> 会读该文件并为本子进程设此 env。

用法(由 build_live_portable.ps1 调用):
  python scripts/encrypt_upload_config.py --local <repo>/scripts/sample_upload.local.ps1 \
      --out <pkg>/data/processed/upload_config.enc
退出码: 0=成功; 2=缺 BIDKING_DATA_KEY; 3=读/解析 .local.ps1 失败; 4=写出失败。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from bidking_lab.secure.data_crypt import (  # noqa: E402
    DataKeyUnavailable,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted,
    resolve_key,
)

_URL_RE = re.compile(r'BIDKING_SAMPLE_UPLOAD_URL\s*=\s*"([^"]+)"')
_KEY_RE = re.compile(r'BIDKING_SAMPLE_UPLOAD_KEY\s*=\s*"([^"]+)"')
_UPLOAD_CONFIG_MEMBER = "data/processed/upload_config.enc"


def _parse_local(ps1_path: Path) -> tuple[str, str]:
    """从 .local.ps1 解析 URL+KEY(regex 与 sample_upload._read_local_upload_config 同式)。"""
    txt = ps1_path.read_text(encoding="utf-8", errors="ignore")
    mu = _URL_RE.search(txt)
    mk = _KEY_RE.search(txt)
    url = mu.group(1) if mu else ""
    key = mk.group(1) if mk else ""
    if not url:
        raise ValueError(f"{ps1_path} 未解析出 BIDKING_SAMPLE_UPLOAD_URL")
    return url, key


def main() -> int:
    ap = argparse.ArgumentParser(description="加密上传配置 -> upload_config.enc (群发包)")
    ap.add_argument("--local", default=str(_REPO / "scripts" / "sample_upload.local.ps1"),
                    help="明文 .local.ps1 路径(默认 <repo>/scripts/sample_upload.local.ps1)")
    ap.add_argument("--out", required=True, help="输出 upload_config.enc 路径(群发包内 data/processed/)")
    args = ap.parse_args()

    try:
        mkey = resolve_key()   # env BIDKING_DATA_KEY(base64) -> 32B; 缺 -> DataKeyUnavailable
    except DataKeyUnavailable as exc:
        print(f"[encrypt_upload_config] 缺数据密钥: {exc} (需设 env BIDKING_DATA_KEY=master_key.txt 的 base64)",
              file=sys.stderr)
        return 2

    local = Path(args.local)
    if not local.exists():
        print(f"[encrypt_upload_config] 明文配置不存在: {local}", file=sys.stderr)
        return 3
    try:
        url, ikey = _parse_local(local)
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("群发包上传 URL 必须使用 HTTPS")
    except Exception as exc:  # noqa: BLE001
        print(f"[encrypt_upload_config] 解析 .local.ps1 失败: {exc}", file=sys.stderr)
        return 3

    plain = json.dumps({"url": url, "key": ikey}, ensure_ascii=False).encode("utf-8")
    blob = encrypt_bytes(plain, mkey, member_path=_UPLOAD_CONFIG_MEMBER)

    out = Path(args.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        # Build gate, not a unit-test-only assertion: prove the exact bytes that
        # will enter the package decrypt with this build's captured key and are
        # identical to the intended config.  This catches wrong-key/stale-output
        # packages before BUILD_MANIFEST can claim success.
        written = out.read_bytes()
        if (
            not is_encrypted(out)
            or decrypt_bytes(
                written,
                mkey,
                member_path=_UPLOAD_CONFIG_MEMBER,
            )
            != plain
        ):
            raise ValueError("upload_config.enc 解密回环不一致")
    except Exception as exc:  # noqa: BLE001
        print(f"[encrypt_upload_config] 写出失败 {out}: {exc}", file=sys.stderr)
        return 4

    print(f"[encrypt_upload_config] OK -> {out} ({len(blob)} bytes, BKENC1, roundtrip verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
