"""L3 完整性自校验: 运行时核对关键文件(.pyd 引擎 / 加密数据 / 入口脚本)未被篡改或替换。

【行为中立】本模块只【计算并返回 verdict】, 不决定"检测到篡改怎么办"(硬退出/降级/上报由上层策略 +
L4 激活服务决定) → 因此本模块本身【无误判把正常用户锁死的风险】, 可独立先落地。

【信任根】verify_digest 用的 expected_digest 应来自【服务端 / 激活 token / Rust 内核】, 不是客户端
可改的明文常量 —— 否则攻击者改完文件后重算 manifest 就绕过了。manifest(逐文件 hash)随包发, 但它整份
的 digest 必须跟一个【客户端改不动的可信源】比对。当前 groundwork 走参数/env 接缝, 正式版接 L4。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_CHUNK = 1 << 20
_DIGEST_ENV = "BIDKING_INTEGRITY_DIGEST"   # groundwork; 正式版由激活/Rust 提供, 不走 env


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str | Path, rel_paths) -> dict[str, str]:
    """build-time: 关键文件(相对 root)-> sha256。打包时对最终 .pyd/加密数据生成。"""
    root = Path(root)
    out: dict[str, str] = {}
    for rel in rel_paths:
        rel_norm = str(rel).replace("\\", "/")
        p = root / rel
        if p.is_file():
            out[rel_norm] = file_sha256(p)
    return out


def manifest_digest(manifest: dict[str, str]) -> str:
    """整份 manifest 的单一摘要 —— 用于跟一个可信源比对【一个值】(而非逐项信任客户端 manifest)。"""
    canon = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


@dataclass
class IntegrityResult:
    ok: bool
    mismatched: list[str] = field(default_factory=list)   # hash 不符 = 被篡改/替换
    missing: list[str] = field(default_factory=list)       # 文件缺失
    digest_ok: bool | None = None                          # manifest 整份摘要是否匹配可信源(None=没校验)

    def __bool__(self) -> bool:
        return self.ok

    def summary(self) -> str:
        if self.ok and self.digest_ok in (None, True):
            return "intact"
        return f"TAMPERED mismatched={self.mismatched} missing={self.missing} digest_ok={self.digest_ok}"


def verify(root: str | Path, manifest: dict[str, str]) -> IntegrityResult:
    """逐文件: 重新 hash 跟 manifest 比。返回哪些被改/缺失。不抛异常(IO 缺失计入 missing)。"""
    root = Path(root)
    mism: list[str] = []
    miss: list[str] = []
    for rel, expected in manifest.items():
        p = root / rel
        try:
            if not p.is_file():
                miss.append(rel)
                continue
            if file_sha256(p) != expected:
                mism.append(rel)
        except OSError:
            miss.append(rel)
    return IntegrityResult(ok=(not mism and not miss), mismatched=mism, missing=miss)


def verify_digest(root: str | Path, manifest: dict[str, str], expected_digest: str | None = None) -> IntegrityResult:
    """更强校验: ① manifest 整份 digest == 可信源(防 manifest 被整份替换) ② 逐文件匹配。
    expected_digest 缺省时从 env 取(groundwork); 正式版由 L4 激活 / Rust 内核提供。"""
    if expected_digest is None:
        expected_digest = os.environ.get(_DIGEST_ENV)
    res = verify(root, manifest)
    if expected_digest is not None:
        res.digest_ok = (manifest_digest(manifest) == expected_digest)
        res.ok = res.ok and res.digest_ok
    return res
