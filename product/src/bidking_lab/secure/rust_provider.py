"""L4: 用 Rust 内核(bidking_kernel.pyd)实现 activation.SecurityProvider。

把安全关键原语放在 native(高逆向成本): 设备指纹 + 激活令牌校验在 Rust。
【架构修正·重要】出厂预加密的 data/processed 必须用【设备无关的主密钥】(各机都能解), 否则换机解不开;
所以 data_key() = 出厂主密钥(激活成功后由服务端交付, L4), 不混设备。设备绑定 = device_fingerprint +
verify_activation + 服务端 token↔设备映射(同一令牌换设备激活会被服务端拒) → 防一份激活转卖多机。
(kernel.derive_data_key(token, device) 仍有用: 把【缓存在本机的主密钥】按设备包一层=拷到别机解不开,
属"at-rest 包装", 与"出厂数据主密钥"是两码事。)

dev/源码裸跑: 内核 .pyd 不在 → load_kernel() 返回 None → activation 保持 DevStubProvider(非真实保护)。
打包版: .pyd 随包 → activate() 加载内核 + set_provider, 上层(data_crypt/integrity/antidebug)零改。
"""
from __future__ import annotations

import os
from typing import Any, Optional


class RustProvider:
    def __init__(
        self,
        kernel: Any,
        *,
        token: str = "",
        hmac_secret: bytes = b"",
        master_key: Optional[bytes] = None,
        integrity_digest: Optional[str] = None,
    ) -> None:
        self._k = kernel
        self._token = token
        self._secret = hmac_secret
        self._master = master_key
        self._digest = integrity_digest

    def device_fingerprint(self) -> str:
        return self._k.device_fingerprint()

    def is_activated(self) -> bool:
        # 激活 = 令牌 HMAC 签名有效(+ 正式版服务端确认该 token 已绑定本设备)
        return (
            bool(self._token)
            and bool(self._secret)
            and self._k.verify_activation(self._token, self._secret)
        )

    def data_key(self) -> bytes:
        # 出厂数据【主密钥】: 激活后由服务端交付(L4); groundwork 从 env 取。【绝不明文常量】
        if self._master is not None:
            return self._master
        from .data_crypt import resolve_key
        return resolve_key()

    def integrity_digest(self) -> Optional[str]:
        if self._digest is not None:
            return self._digest
        return os.environ.get("BIDKING_INTEGRITY_DIGEST")

    def report_suspicious(self, verdict: Any) -> None:
        # L4: POST device_fingerprint + verdict.summary() 到 collector /telemetry; groundwork no-op。
        return None


def load_kernel() -> Optional[Any]:
    """加载 Rust 内核 .pyd; 不在(源码 dev)返回 None。"""
    try:
        import bidking_kernel  # type: ignore  # noqa: F401
        return bidking_kernel
    except Exception:  # noqa: BLE001
        return None


def activate(
    *,
    token: str = "",
    hmac_secret: bytes = b"",
    master_key: Optional[bytes] = None,
    integrity_digest: Optional[str] = None,
) -> bool:
    """启动时调用: 内核在 → set_provider(RustProvider) 返回 True; 不在(dev)返回 False(保持 stub)。"""
    k = load_kernel()
    if k is None:
        return False
    from . import activation
    activation.set_provider(
        RustProvider(
            k,
            token=token,
            hmac_secret=hmac_secret,
            master_key=master_key,
            integrity_digest=integrity_digest,
        )
    )
    return True
