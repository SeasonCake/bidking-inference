"""激活/安全策略【统一接缝】(L4 落点) —— 把 L2/L3 散落的接缝(解密key/完整性digest/C上报/是否激活)
收成一个 provider。L4 用 Rust 内核 + 服务端实现后, 启动时 set_provider(rust_provider) 【一次性替换】,
上层(data_crypt / integrity / antidebug 的调用方)代码全不用动 —— 这就是"铺垫"。

⚠️ 当前 _DevStubProvider 仅供开发/源码裸跑, 【不构成任何真实保护】(永远"已激活", key/digest 走 env)。
正式版(L4): 激活 token 校验 + 设备ID绑定 + 解密key派生 + 完整性可信digest 全进 Rust .pyd + 服务端权威,
key/digest 绝不明文落 Python。
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class SecurityProvider(Protocol):
    def is_activated(self) -> bool: ...
    def data_key(self) -> bytes: ...                       # AES-256 数据解密 key
    def integrity_digest(self) -> str | None: ...          # manifest 可信 digest(对比客户端 manifest)
    def report_suspicious(self, verdict: Any) -> None: ...  # C: 上报可疑设备(反调试/完整性命中)


class _DevStubProvider:
    """开发 stub: 永远已激活, key/digest 走 env, 上报 no-op。【非真实保护, L4 用 Rust+服务端替换】"""

    def is_activated(self) -> bool:
        return True  # 源码裸跑/开发期视为已激活

    def data_key(self) -> bytes:
        from .data_crypt import resolve_key
        return resolve_key()  # groundwork: env %BIDKING_DATA_KEY%

    def integrity_digest(self) -> str | None:
        return os.environ.get("BIDKING_INTEGRITY_DIGEST")

    def report_suspicious(self, verdict: Any) -> None:
        return None  # dev no-op; L4 接 collector /telemetry


_provider: SecurityProvider = _DevStubProvider()


def set_provider(provider: SecurityProvider) -> None:
    """L4 启动时注入 Rust/服务端 provider, 替换 dev stub。"""
    global _provider
    _provider = provider


def is_activated() -> bool:
    return _provider.is_activated()


def data_key() -> bytes:
    return _provider.data_key()


def integrity_digest() -> str | None:
    return _provider.integrity_digest()


def report_suspicious(verdict: Any) -> None:
    _provider.report_suspicious(verdict)
