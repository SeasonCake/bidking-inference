"""L2 数据加密地基: 用 AES-256-GCM 加密 data/processed 里的 moat 数据(目录价值/掉率/参数)。

设计要点(承架构结论):
- AES-GCM = 认证加密: 改一个字节或用错 key 都会在解密时抛异常(防篡改 + 防错 key 静默出垃圾)。
- 【密钥铁律】resolve_key() 是接缝。**正式版密钥必须由激活 token 派生 / 由 Rust 内核返回,绝不明文
  存进 Python 常量** —— 明文 key 一旦被 dump 整个加密就废。当前 groundwork 从 env 取, 仅供打包流水线
  与测试; L4(激活)接上后改为激活派生。
- 透明加载 load_json(): BKENC1 继续兼容旧包；BKENC2 对静态 moat 数据先做 gzip 再认证
  加密，避免随机密文进入 ZIP 后完全失去压缩收益。明文源码仍直接读取。

本模块只是 plumbing(AES-GCM 是公开算法, 代码本身不机密); 真正的秘密是 KEY 和被加密的数据。
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import struct
import unicodedata
import zlib
from pathlib import Path
from pathlib import PurePosixPath

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_BKENC1_MAGIC = b"BKENC1\n"
_BKENC2_MAGIC = b"BKENC2\n"
_MAGIC_LEN = len(_BKENC1_MAGIC)
_NONCE_LEN = 12          # AES-GCM 推荐 96-bit nonce
_KEY_LEN = 32            # AES-256
_KEY_ENV = "BIDKING_DATA_KEY"   # groundwork 用; 正式版由激活派生, 不走 env
BKENC1_MEMBER_AAD_SCHEME = "bidking_bkenc1_member_path_v1"
BKENC_AUTHENTICATION_AAD_SCHEME = "bidking_bkenc_member_path_by_format_v1"
_BKENC1_MEMBER_AAD_DOMAIN = b"bidking-bkenc1-member-path-v1\x00"
_BKENC2_MEMBER_AAD_DOMAIN = b"bidking-bkenc2-gzip-member-path-v1\x00"
_BKENC1_MEMBER_ROOTS = frozenset({"data", "external_references"})
_BKENC2_LENGTH = struct.Struct(">Q")
_BKENC2_MAX_PLAINTEXT_BYTES = 128 * 1024 * 1024
BKENC1_FORMAT = "bkenc1"
BKENC2_GZIP_FORMAT = "bkenc2-gzip"


class DataKeyUnavailable(RuntimeError):
    """无可用解密密钥(未激活 / 缺密钥)。"""


def resolve_key() -> bytes:
    """返回 32 字节 AES-256 出厂数据主密钥。

    来源优先级:
      1) 【Rust 内核内嵌 K】(加密包形态): bidking_kernel.data_key() —— 启动即可用, 解密 moat 数据
         【无激活竞态】(密钥不依赖服务端下发)。K 只存在于 strip+LTO 的 .pyd 二进制里。
      2) 【env %BIDKING_DATA_KEY%(base64 32B)】: 打包加密流水线(用 -MasterKeyFile 设)+ 单测 + 源码 dev。
    两处 K 相同(打包时同一把 master_key: 加密文件 = 编内核内嵌 = 服务端下发), 故加密包与源码裸跑同一套代码。
    都拿不到 -> DataKeyUnavailable(明文文件不会走到这里: load_json/load_text 只在 BKENC 时才要 key)。
    """
    # 1) Rust 内核内嵌 K(加密包): 无竞态, 不依赖激活。
    try:
        from .rust_provider import load_kernel
        k = load_kernel()
        if k is not None:
            key = bytes(k.data_key())
            if len(key) == _KEY_LEN:
                return key
    except Exception:  # noqa: BLE001  dev 内核未嵌 key / 无内核 -> 回退 env
        pass
    # 2) env(打包流水线 / 单测 / 源码 dev)
    b64 = os.environ.get(_KEY_ENV)
    if not b64:
        raise DataKeyUnavailable(f"无数据密钥(未激活/无内核 K/缺 {_KEY_ENV})")
    try:
        key = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        raise DataKeyUnavailable("数据密钥 base64 解码失败") from exc
    if len(key) != _KEY_LEN:
        raise DataKeyUnavailable(f"数据密钥长度应为 {_KEY_LEN} 字节, 实为 {len(key)}")
    return key


def gen_key_b64() -> str:
    """生成一把随机 256-bit 密钥的 base64(打包/测试时用)。"""
    return base64.b64encode(os.urandom(_KEY_LEN)).decode("ascii")


def _require_exact_plain_bytes(plain: object) -> bytes:
    if type(plain) is not bytes:  # noqa: E721 - reject subclasses/protocols
        raise ValueError("BKENC plaintext必须为exact bytes")
    return plain


def _require_exact_key_bytes(key: object) -> bytes:
    if (
        type(key) is not bytes  # noqa: E721 - reject subclasses/protocols
        or len(key) != _KEY_LEN
    ):
        raise ValueError("BKENC key必须为32-byte exact bytes")
    return key


def _require_exact_blob_bytes(blob: object) -> bytes:
    if type(blob) is not bytes:  # noqa: E721 - reject subclasses/protocols
        raise ValueError("BKENC blob必须为exact bytes")
    return blob


def _canonical_member_path(member_path: str) -> str:
    if (
        type(member_path) is not str  # noqa: E721 - reject protocol/subclass input
        or not member_path
        or member_path != unicodedata.normalize("NFC", member_path)
        or "\\" in member_path
        or member_path.startswith("/")
        or member_path.endswith("/")
        or "\x00" in member_path
        or any(ord(char) < 0x20 for char in member_path)
    ):
        raise ValueError("BKENC member path非法")
    parts = member_path.split("/")
    if (
        parts[0] not in _BKENC1_MEMBER_ROOTS
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(member_path).as_posix() != member_path
    ):
        raise ValueError("BKENC member path非法")
    return member_path


def bkenc1_member_aad(member_path: str) -> bytes:
    """Bind a legacy BKENC1 envelope to one canonical release member identity."""
    return _BKENC1_MEMBER_AAD_DOMAIN + _canonical_member_path(member_path).encode(
        "utf-8"
    )


def bkenc2_member_aad(member_path: str) -> bytes:
    """Bind the compressed BKENC2 format and its canonical member identity."""
    return _BKENC2_MEMBER_AAD_DOMAIN + _canonical_member_path(member_path).encode(
        "utf-8"
    )


def _member_path_from_filesystem(path: str | Path) -> str:
    """Project an absolute/relative runtime path back to its release member."""
    parts = Path(path).parts
    indexes = [
        index
        for index, part in enumerate(parts)
        if part in _BKENC1_MEMBER_ROOTS
    ]
    if not indexes:
        raise ValueError("BKENC文件不在受支持的发布成员根")
    member = "/".join(parts[indexes[-1] :])
    return _canonical_member_path(member)


def encrypt_bytes(
    plain: bytes,
    key: bytes,
    *,
    member_path: str | None = None,
) -> bytes:
    plain = _require_exact_plain_bytes(plain)
    key = _require_exact_key_bytes(key)
    nonce = os.urandom(_NONCE_LEN)
    aad = None if member_path is None else bkenc1_member_aad(member_path)
    ct = AESGCM(key).encrypt(nonce, plain, aad)
    return _BKENC1_MAGIC + nonce + ct


def encrypt_compressed_bytes(
    plain: bytes,
    key: bytes,
    *,
    member_path: str,
) -> bytes:
    """Compress static package data before path-bound authenticated encryption."""
    plain = _require_exact_plain_bytes(plain)
    key = _require_exact_key_bytes(key)
    if len(plain) > _BKENC2_MAX_PLAINTEXT_BYTES:
        raise ValueError("BKENC2 plaintext size非法")
    compressed = gzip.compress(plain, compresslevel=6, mtime=0)
    payload = _BKENC2_LENGTH.pack(len(plain)) + compressed
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, payload, bkenc2_member_aad(member_path))
    return _BKENC2_MAGIC + nonce + ct


def _decompress_bkenc2(payload: bytes) -> bytes:
    if len(payload) < _BKENC2_LENGTH.size:
        raise ValueError("BKENC2 compressed payload非法")
    expected = _BKENC2_LENGTH.unpack(payload[: _BKENC2_LENGTH.size])[0]
    if expected > _BKENC2_MAX_PLAINTEXT_BYTES:
        raise ValueError("BKENC2 plaintext size非法")
    decoder = zlib.decompressobj(wbits=31)
    try:
        plain = decoder.decompress(payload[_BKENC2_LENGTH.size :], expected + 1)
    except zlib.error as exc:
        raise ValueError("BKENC2 compressed payload非法") from exc
    if (
        len(plain) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("BKENC2 compressed payload非法")
    return plain


def decrypt_bytes(
    blob: bytes,
    key: bytes,
    *,
    member_path: str | None = None,
) -> bytes:
    blob = _require_exact_blob_bytes(blob)
    key = _require_exact_key_bytes(key)
    if blob.startswith(_BKENC1_MAGIC):
        magic = _BKENC1_MAGIC
        aad = None if member_path is None else bkenc1_member_aad(member_path)
    elif blob.startswith(_BKENC2_MAGIC):
        if member_path is None:
            raise ValueError("BKENC2 必须绑定发布成员路径")
        magic = _BKENC2_MAGIC
        aad = bkenc2_member_aad(member_path)
    else:
        raise ValueError("不是受支持的 BKENC 加密数据")
    if len(blob) < len(magic) + _NONCE_LEN + 16:
        raise ValueError("BKENC envelope长度非法")
    body = blob[len(magic):]
    nonce, ct = body[:_NONCE_LEN], body[_NONCE_LEN:]
    plain = AESGCM(key).decrypt(nonce, ct, aad)  # 篡改/错 key/错路径/错格式 -> InvalidTag
    return _decompress_bkenc2(plain) if magic == _BKENC2_MAGIC else plain


def encryption_format(path: str | Path) -> str | None:
    try:
        with open(path, "rb") as stream:
            magic = stream.read(_MAGIC_LEN)
    except OSError:
        return None
    if magic == _BKENC1_MAGIC:
        return BKENC1_FORMAT
    if magic == _BKENC2_MAGIC:
        return BKENC2_GZIP_FORMAT
    return None


def is_encrypted(path: str | Path) -> bool:
    return encryption_format(path) is not None


def encrypt_file(src: str | Path, dst: str | Path | None = None, key: bytes | None = None) -> Path:
    """打包用: src(明文 json) -> dst(默认 src + '.enc')。"""
    src = Path(src)
    key = key if key is not None else resolve_key()
    dst = Path(dst) if dst is not None else src.with_suffix(src.suffix + ".enc")
    dst.write_bytes(
        encrypt_bytes(
            src.read_bytes(),
            key,
            member_path=_member_path_from_filesystem(dst),
        )
    )
    return dst


def load_json(path: str | Path, key: bytes | None = None):
    """透明加载: BKENC1/BKENC2 -> 解密解压再解析; 明文 -> 直接解析。
    (引擎各加载点后续改走此函数即可统一支持加密包与源码裸跑。)"""
    raw = Path(path).read_bytes()
    if raw.startswith((_BKENC1_MAGIC, _BKENC2_MAGIC)):
        raw = decrypt_bytes(
            raw,
            key if key is not None else resolve_key(),
            member_path=_member_path_from_filesystem(path),
        )
    return json.loads(raw.decode("utf-8-sig"))


def load_text(path: str | Path, key: bytes | None = None, *, encoding: str = "utf-8-sig") -> str:
    """透明加载文本: BKENC1/BKENC2 -> 解密解压; 明文 -> 直接 decode。
    用于 StaticData.cs / Item.txt(base64) 等非 JSON 的 moat 文本文件, 支持加密包与源码裸跑同一套代码。"""
    raw = Path(path).read_bytes()
    if raw.startswith((_BKENC1_MAGIC, _BKENC2_MAGIC)):
        raw = decrypt_bytes(
            raw,
            key if key is not None else resolve_key(),
            member_path=_member_path_from_filesystem(path),
        )
    return raw.decode(encoding)
