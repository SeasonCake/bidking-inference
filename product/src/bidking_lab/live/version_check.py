# -*- coding: utf-8 -*-
"""客户端版本检测:取本产品版本 + 探测游戏版本,启动时查服务器 GET /version 比对。

用途(版本发行/自动更新/会员功能的地基):
  · 让用户实时知道"我的工具版本 vs 最新版"、"是否匹配当前游戏版本";
  · 客户端把 app_version(+ game_version)随上传报给服务器,按版本统计/铺路会员 gating。

全程 best-effort:绝不抛异常、绝不阻塞对局——连不上版本服务就当"无版本信息"。
配合 scripts/sample_collector_server.py 的 GET /version(从 version_manifest.json 下发)。
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import ipaddress
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

from ..http_tls import urlopen_verified

_FALLBACK_VERSION = "0.2.1"   # 取不到 hero_ref_version 时的兜底(与当前测试线一致)


def app_version() -> str:
    """本产品版本。复用 hero_ref_version.resolve_hero_ref_version()(打包读 BUILD_MANIFEST,
    源码读模块常量);定位/导入失败则回退 _FALLBACK_VERSION,绝不抛。"""
    try:
        root = Path(__file__).resolve().parents[3]      # live -> bidking_lab -> src -> 仓库根
        hrv = root / "hero_ref" / "tools"
        if hrv.is_dir() and str(hrv) not in sys.path:
            sys.path.insert(0, str(hrv))
        import hero_ref_version  # type: ignore
        v = hero_ref_version.resolve_hero_ref_version()
        return str(v) if v else _FALLBACK_VERSION
    except Exception:  # noqa: BLE001
        return _FALLBACK_VERSION


_STEAM_APPID = "4128580"        # 竞拍之王 Steam appid
_GAME_INSTALLDIR = "BidKing"    # steamapps/common 下的安装目录名
# 游戏版本权威源(06-25 确认): YooAsset 包版本文件, 内容形如 "70e6e4-2026-06-25-762"。
_YOO_VERSION_REL = "BidKing_Data/StreamingAssets/yoo/DefaultPackage/DefaultPackage.version"


@dataclass(frozen=True)
class StrictGameVersionSnapshot:
    """One stable, exact read of the on-disk YooAsset generation file."""

    path: Path
    value: str
    size: int
    sha256: str
    mtime_ns: int


def _steam_libraries() -> list:
    """所有 Steam 库路径: 注册表 SteamPath + libraryfolders.vdf 里的 path。"""
    roots: list = []
    try:
        import winreg  # Windows only
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    p = winreg.QueryValueEx(k, "SteamPath")[0]
                    if p:
                        roots.append(Path(p))
            except OSError:
                continue
    except Exception:  # noqa: BLE001  非 Windows / 无注册表
        pass
    libs: list = []
    for r in roots:
        libs.append(r)
        vdf = r / "steamapps" / "libraryfolders.vdf"
        try:
            txt = vdf.read_text(encoding="utf-8", errors="ignore")
            libs.extend(Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s*"([^"]+)"', txt))
        except Exception:  # noqa: BLE001
            continue
    return libs


def _find_game_version_file() -> Path | None:
    """定位 DefaultPackage.version(遍历 Steam 库的 steamapps/common/BidKing)。找不到 -> None。"""
    for lib in _steam_libraries():
        p = lib / "steamapps" / "common" / _GAME_INSTALLDIR / _YOO_VERSION_REL
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def detect_game_version() -> str | None:
    """探测当前游戏版本。返回 None = 拿不到(客户端显示"游戏版本: 未知",不阻塞)。

    源(06-25 确认): YooAsset 包版本文件 DefaultPackage.version(StreamingAssets\\yoo\\
    DefaultPackage\\DefaultPackage.version),内容如 "70e6e4-2026-06-25-762"。经 Steam 库
    自动定位(注册表 SteamPath + libraryfolders.vdf)。env `BIDKING_GAME_VERSION` 可手动覆盖。
    配置表本身是 CDN 下载的 UnityFS bundle(不在磁盘), 但版本号在此文件里, 随版本更新而变。
    """
    v = os.environ.get("BIDKING_GAME_VERSION")
    if v and v.strip():
        return v.strip()
    try:
        f = _find_game_version_file()
        if f:
            s = f.read_text(encoding="utf-8", errors="ignore").strip()
            return s or None
    except Exception:  # noqa: BLE001  读盘失败静默, 不阻塞
        pass
    return None


def detect_game_version_strict_disk_snapshot(
    path: str | Path | None = None,
) -> StrictGameVersionSnapshot | None:
    """Return one race-checked snapshot without an environment override.

    Supplying ``path`` lets a long-running monitor recheck the exact Steam
    installation selected at startup without rescanning every library.  Any
    replacement, reparse point, partial write, lossy decode, or oversized value
    is represented as ``None`` so generation-sensitive consumers fail closed.
    """

    try:
        selected = Path(path) if path is not None else _find_game_version_file()
        if selected is None:
            return None
        before_path = selected.lstat()
        attributes = int(getattr(before_path, "st_file_attributes", 0))
        reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if (
            selected.is_symlink()
            or attributes & reparse
            or not stat.S_ISREG(before_path.st_mode)
            or not 0 < int(before_path.st_size) <= 128
        ):
            return None
        with selected.open("rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(129)
            after = os.fstat(stream.fileno())
        after_path = selected.lstat()
    except OSError:
        return None

    def identity(value) -> tuple[int, int, int, int, int]:  # noqa: ANN001
        return (
            int(value.st_dev),
            int(value.st_ino),
            int(value.st_size),
            int(value.st_mtime_ns),
            int(value.st_nlink),
        )

    def open_identity(value) -> tuple[int, int, int, int, int, int]:  # noqa: ANN001
        return (*identity(value), int(value.st_ctime_ns))

    if (
        identity(before_path) != identity(before)
        or open_identity(before) != open_identity(after)
        or identity(after) != identity(after_path)
        or len(raw) != int(after.st_size)
    ):
        return None
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeError:
        return None
    if not value or len(value) > 128 or "\x00" in value:
        return None
    try:
        resolved_path = selected.resolve(strict=True)
    except OSError:
        # The file may disappear after the checked read but before we persist
        # its selected location for later session/upload revalidation.
        return None
    return StrictGameVersionSnapshot(
        path=resolved_path,
        value=value,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        mtime_ns=int(after.st_mtime_ns),
    )


def detect_game_version_strict_disk(
    path: str | Path | None = None,
) -> str | None:
    """Read the on-disk YooAsset generation without an environment override.

    This stricter variant is for data-generation qualification, where an env
    convenience override or lossy decoding must not make an unfrozen game
    generation borrow a known PW2 profile.  Failure remains non-fatal and is
    represented as ``None``.
    """

    snapshot = detect_game_version_strict_disk_snapshot(path)
    return snapshot.value if snapshot is not None else None


_RELEASE_VERSION_RE = re.compile(
    r"^([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})"
    r"(?:-(r|hotfix)([0-9]{1,9})(?:-r([0-9]{1,9}))?)?$",
    re.IGNORECASE | re.ASCII,
)


def _vtuple(s) -> tuple[int, int, int, int, int, int] | None:
    """严格产品版本元组：``base < rN < hotfixN[-rN]``；畸形输入返回 ``None``。

    客户端必须与激活服务的最低版本门使用同一格式，不能把未知后缀、缺段、额外段、
    ``v`` 前缀或复合 preview 字符串容错成正式版本。该值来自客户端自身，版本比较只能
    用于更新提示和兼容性退役，不能作为防篡改或防伪报版本的安全证明。
    """
    if type(s) is not str:  # noqa: E721 - parser boundary rejects str subclasses
        return None
    match = _RELEASE_VERSION_RE.fullmatch(s)
    if match is None:
        return None
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    suffix = (match.group(4) or "").lower()
    reroll = int(match.group(6) or 0)
    if reroll and suffix != "hotfix":
        return None
    channel = 2 if suffix == "hotfix" else (1 if suffix == "r" else 0)
    revision = int(match.group(5) or 0)
    return major, minor, patch, channel, revision, reroll


def _ge(a, b) -> bool:
    """严格版本 ``a >= b``；任一输入畸形即返回 ``False``。"""
    ta, tb = _vtuple(a), _vtuple(b)
    return ta is not None and tb is not None and ta >= tb


def validate_service_url(url: str, *, purpose: str = "service") -> str:
    """Validate a client-to-service URL against the production transport contract.

    Production activation, upload, telemetry and version traffic must use HTTPS.
    Plain HTTP remains available only for explicit loopback development endpoints.
    """
    value = str(url or "").strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").strip().lower()
    if not value or not parsed.scheme or not host:
        raise ValueError(f"{purpose} URL 缺少 scheme/host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{purpose} URL 不允许内嵌凭据")
    if parsed.fragment:
        raise ValueError(f"{purpose} URL 不允许 fragment")

    scheme = parsed.scheme.lower()
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if scheme != "https" and not (scheme == "http" and loopback):
        raise ValueError(f"{purpose} URL 必须使用 HTTPS（仅 localhost/loopback 可用 HTTP）")

    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, "")).rstrip("/")


def version_url_from_ingest(ingest_url: str) -> str:
    """由上传地址(.../ingest)推出版本地址(.../version):同主机换最后一段路径。"""
    checked = validate_service_url(ingest_url, purpose="ingest")
    base = checked.rsplit("/", 1)[0] if "/" in checked else checked
    return base + "/version"


def resolve_ingest_url() -> str | None:
    """取【上传】地址:env 优先,回退读 .local.ps1(同 sample_upload 的自动提权回退逻辑)。
    ⚠️ 仅【上传】用:无私有配置返回 None = 群友包不上传(上传需私有 key)。激活/版本检查改用
    resolve_server_base()(那个总返回可用地址)。"""
    url = os.environ.get("BIDKING_SAMPLE_UPLOAD_URL")
    if url:
        return validate_service_url(url, purpose="upload")
    try:
        from .sample_upload import _read_local_upload_config  # 懒导入避免循环
        u, _ = _read_local_upload_config()
        return validate_service_url(u, purpose="upload") if u else None
    except Exception:  # noqa: BLE001
        return None


_ACTIVATION_SERVICE_ENDPOINTS_FILE = "activation_service_endpoints.json"
_ACTIVATION_SERVICE_ENDPOINTS_MAX_BYTES = 32 * 1024
_ACTIVATION_SERVICE_ENDPOINT_IDS = ("endpoint_1", "endpoint_2", "endpoint_3")
_ACTIVATION_SERVICE_ENDPOINT_CONFIG_KEYS = frozenset(
    {"schema_version", "generation", "endpoint_3_enabled", "endpoints"}
)
_ACTIVATION_SERVICE_ENDPOINT_KEYS = frozenset(
    {"endpoint_id", "base", "telemetry"}
)
_ACTIVATION_SERVICE_HOST_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.ASCII,
)


@dataclass(frozen=True)
class ActivationServiceEndpoint:
    """One immutable public activation origin and its optional telemetry route."""

    endpoint_id: str
    base: str
    telemetry: str | None


@dataclass(frozen=True)
class ActivationServiceEndpointConfig:
    """Validated endpoint generation used by activation, version and diagnostics."""

    generation: int
    endpoint_3_enabled: bool
    endpoints: tuple[ActivationServiceEndpoint, ...]
    raw_sha256: str


def activation_service_endpoints_path() -> Path:
    """Return the version-controlled endpoint file in source or package root."""
    return Path(__file__).resolve().parents[3] / _ACTIVATION_SERVICE_ENDPOINTS_FILE


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:  # noqa: E721 - trust boundary
            raise ValueError("activation endpoint config has invalid object keys")
        result[key] = value
    return result


def _strict_endpoint_base(value: object) -> tuple[str, str]:
    if type(value) is not str:  # noqa: E721 - reject str subclasses/magic
        raise ValueError("activation endpoint base must be an exact string")
    if not value or value != value.strip():
        raise ValueError("activation endpoint base is empty or padded")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("activation endpoint base must be ASCII") from exc
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("activation endpoint base has an invalid port") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or type(host) is not str
        or not host
        or host != host.lower()
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.netloc != host
        or parsed.path
        or parsed.query
        or parsed.fragment
        or _ACTIVATION_SERVICE_HOST_RE.fullmatch(host) is None
    ):
        raise ValueError("activation endpoint base must be a canonical root HTTPS origin")
    return value, host


def _strict_endpoint_telemetry(
    value: object,
    *,
    endpoint_id: str,
    base: str,
) -> str | None:
    if endpoint_id == "endpoint_3":
        if value is not None:
            raise ValueError("endpoint_3 telemetry must be null")
        return None
    if type(value) is not str:  # noqa: E721 - reject str subclasses/magic
        raise ValueError("activation endpoint telemetry must be an exact string")
    expected = base + "/telemetry"
    if value != expected:
        raise ValueError("activation endpoint telemetry must be its exact same-origin route")
    return value


def parse_activation_service_endpoint_config_bytes(
    raw: bytes,
) -> ActivationServiceEndpointConfig:
    """Parse the public endpoint input without coercion or duplicate-key fallback."""
    if type(raw) is not bytes:  # noqa: E721 - reject bytes subclasses/magic
        raise ValueError("activation endpoint config must be exact bytes")
    if not raw or len(raw) > _ACTIVATION_SERVICE_ENDPOINTS_MAX_BYTES:
        raise ValueError("activation endpoint config size is invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("activation endpoint config contains a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("activation endpoint config is invalid JSON") from exc
    if (
        type(value) is not dict
        or frozenset(dict.keys(value)) != _ACTIVATION_SERVICE_ENDPOINT_CONFIG_KEYS
        or any(type(key) is not str for key in dict.keys(value))
        or type(dict.get(value, "schema_version")) is not int
        or dict.get(value, "schema_version") != 1
        or type(dict.get(value, "generation")) is not int
        or not 1 <= dict.get(value, "generation") <= 2_147_483_647
        or type(dict.get(value, "endpoint_3_enabled")) is not bool
        or type(dict.get(value, "endpoints")) is not list
    ):
        raise ValueError("activation endpoint config root is invalid")
    generation = dict.get(value, "generation")
    enabled = dict.get(value, "endpoint_3_enabled")
    # schema v1 has exactly two assigned rollout generations.  Accepting an
    # arbitrary positive integer would let a future or mismatched generation
    # silently inherit today's endpoint semantics instead of failing closed.
    expected_generation = 2 if enabled else 1
    if generation != expected_generation:
        raise ValueError("activation endpoint config generation is inconsistent")
    raw_endpoints = dict.get(value, "endpoints")
    expected_ids = _ACTIVATION_SERVICE_ENDPOINT_IDS[: 3 if enabled else 2]
    if len(raw_endpoints) != len(expected_ids):
        raise ValueError("activation endpoint config enabled state is inconsistent")
    endpoints: list[ActivationServiceEndpoint] = []
    seen_bases: set[str] = set()
    seen_hosts: set[str] = set()
    for expected_id, raw_endpoint in zip(expected_ids, raw_endpoints):
        if (
            type(raw_endpoint) is not dict
            or frozenset(dict.keys(raw_endpoint)) != _ACTIVATION_SERVICE_ENDPOINT_KEYS
            or any(type(key) is not str for key in dict.keys(raw_endpoint))
            or type(dict.get(raw_endpoint, "endpoint_id")) is not str
            or dict.get(raw_endpoint, "endpoint_id") != expected_id
        ):
            raise ValueError("activation endpoint entry is invalid")
        base, host = _strict_endpoint_base(dict.get(raw_endpoint, "base"))
        if base in seen_bases or host in seen_hosts:
            raise ValueError("activation endpoint URL or host is duplicated")
        seen_bases.add(base)
        seen_hosts.add(host)
        endpoints.append(
            ActivationServiceEndpoint(
                endpoint_id=expected_id,
                base=base,
                telemetry=_strict_endpoint_telemetry(
                    dict.get(raw_endpoint, "telemetry"),
                    endpoint_id=expected_id,
                    base=base,
                ),
            )
        )
    return ActivationServiceEndpointConfig(
        generation=generation,
        endpoint_3_enabled=enabled,
        endpoints=tuple(endpoints),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_activation_service_endpoint_config(
    path: Path | None = None,
) -> ActivationServiceEndpointConfig:
    candidate = activation_service_endpoints_path() if path is None else path
    if not isinstance(candidate, Path):
        raise ValueError("activation endpoint config path must be an exact Path")
    before = candidate.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("activation endpoint config path is unsafe")
    if before.st_size <= 0 or before.st_size > _ACTIVATION_SERVICE_ENDPOINTS_MAX_BYTES:
        raise ValueError("activation endpoint config size is invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ValueError("activation endpoint config path cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino)
        identity_opened = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or identity_opened != identity_before
            or opened.st_size != before.st_size
            or opened.st_size <= 0
            or opened.st_size > _ACTIVATION_SERVICE_ENDPOINTS_MAX_BYTES
        ):
            raise ValueError("activation endpoint config identity changed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("activation endpoint config was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("activation endpoint config grew while reading")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != identity_opened
            or after.st_size != opened.st_size
            or getattr(after, "st_mtime_ns", None)
            != getattr(opened, "st_mtime_ns", None)
        ):
            raise ValueError("activation endpoint config changed while reading")
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    return parse_activation_service_endpoint_config_bytes(raw)


try:
    _CONTROLLED_ACTIVATION_SERVICE_ENDPOINT_CONFIG = (
        load_activation_service_endpoint_config()
    )
except Exception:  # package/config mismatch disables all public service requests
    _CONTROLLED_ACTIVATION_SERVICE_ENDPOINT_CONFIG = None


def iter_service_endpoints() -> list[ActivationServiceEndpoint]:
    """Return one immutable parse result; invalid config fails closed to no origins."""
    if _CONTROLLED_ACTIVATION_SERVICE_ENDPOINT_CONFIG is None:
        return []
    return list(_CONTROLLED_ACTIVATION_SERVICE_ENDPOINT_CONFIG.endpoints)


# Compatibility for older callers/tests; these values are derived from the same file,
# never maintained as a second URL list.
_SERVER_BASES: tuple[str, ...] = tuple(
    endpoint.base for endpoint in iter_service_endpoints()
)
_DEFAULT_SERVER_BASE = _SERVER_BASES[0] if _SERVER_BASES else None


def _validated_baked_server_bases() -> list[str]:
    return [endpoint.base for endpoint in iter_service_endpoints()]


def server_bases_from_ingest(ingest_url: str) -> list[str]:
    """从上传地址推导激活/版本服务候选。

    烤制主域名下的标准 ``/ingest`` 是群发包的已知入口，应保留主域名
    和独立 HTTPS 备用入口。自建/私有 ingest 只使用
    同源服务，避免把 token 发往与其配置无关的生产端点。
    """
    checked = validate_service_url(ingest_url, purpose="ingest")
    parsed = urlsplit(checked)
    parent_path = parsed.path.rstrip("/").rsplit("/", 1)[0]
    base = validate_service_url(
        urlunsplit((parsed.scheme, parsed.netloc, parent_path, "", "")),
        purpose="server base",
    )

    baked = _validated_baked_server_bases()
    standard_production_ingest = (
        not parsed.query
        and parsed.path.rstrip("/") == "/ingest"
        and bool(baked)
        and base == baked[0]
    )
    return baked if standard_production_ingest else [base]


def iter_server_bases() -> list[str]:
    """Production server bases from the version-controlled endpoint generation."""
    return _validated_baked_server_bases()


def resolve_server_base() -> str:
    """激活/版本检查的【首选】服务器基地址(无 scheme 末尾斜杠)。总返回可用地址。

    ⚠️ 仅在"只要一个地址"的场景用(如拼展示串)。真正发请求请用 iter_server_bases() 逐个试,
    否则域名一挂就连不上(见上方 fallback 说明)。"""
    bases = iter_server_bases()
    if not bases:
        raise ValueError("activation endpoint configuration is unavailable")
    return bases[0]


def check_against_server(ingest_url: str, timeout: float = 4.0) -> dict:
    """查服务器 /version 并与本地比对，返回状态 dict（永不抛）。

    ``reachable=False`` 表示 DNS/连接/超时/TLS 层失败，可尝试下一个候选；
    HTTP 4xx/5xx 或已连上但响应不合法均是 ``reachable=True``，不应换入口重试。
    """
    app = app_version()
    game = detect_game_version()
    st = {"app": app, "game": game, "ok": False, "reachable": False,
          "http_status": None, "latest": None, "min": None,
          "url": "", "notice": "", "server_game": None,
          "up_to_date": True, "supported": True, "game_match": None}
    try:
        with urlopen_verified(version_url_from_ingest(ingest_url), timeout=timeout) as r:
            st["reachable"] = True
            status = getattr(r, "status", None)
            if status is None:
                getcode = getattr(r, "getcode", None)
                status = getcode() if callable(getcode) else 200
            st["http_status"] = status
            raw = r.read()
    except urllib.error.HTTPError as exc:
        st["reachable"] = True
        st["http_status"] = exc.code
        return st
    except Exception:  # noqa: BLE001  网络层连不上 = 可尝试下一个候选
        return st
    try:
        m = json.loads(raw.decode("utf-8"))
        if not isinstance(m, dict):
            return st
    except Exception:  # noqa: BLE001  已连上但版本载荷不合法，不向其他入口重试
        return st
    st["ok"] = True
    st["latest"] = m.get("latest")
    st["min"] = m.get("min")
    st["url"] = m.get("url") or ""
    st["notice"] = m.get("notice") or ""
    st["server_game"] = m.get("game_version")
    if st["latest"]:
        st["up_to_date"] = _ge(app, st["latest"])
    if st["min"]:
        st["supported"] = _ge(app, st["min"])
    if game and st["server_game"]:
        st["game_match"] = (str(game) == str(st["server_game"]))
    return st


def check_from_local_config(timeout: float = 4.0) -> dict:
    """便捷入口:查 /version 比对。烤了默认地址 → 群友包也总能查版本。供 monitor/overlay 启动调用。

    逐个试候选地址(iter_server_bases): 第一个连得上的胜出; 全都连不上才返回 ok=False。
    → 主域名网络失败时自动退回备用 HTTPS Worker, 版本检查不瞎。"""
    st: dict = {}
    bases = iter_server_bases()
    if not bases:
        return {
            "app": app_version(),
            "game": detect_game_version(),
            "ok": False,
            "reachable": False,
            "configured": False,
        }
    for base in bases:
        # check_against_server 取 ingest_url 后 version_url_from_ingest 会 strip 末段再 +/version,
        # 故传 base+"/ingest" 等价于查 base+"/version"(/ingest 仅占位, 不真发上传)。
        st = check_against_server(base + "/ingest", timeout)
        if st.get("ok") or st.get("reachable"):
            st["server_base"] = base   # 实际用上的地址(诊断: 看是不是退到兜底了)
            break
    st["configured"] = True
    return st


def format_status_line(st: dict) -> str:
    """一行人类可读状态(给启动日志/UI)。"""
    app = st.get("app")
    if not st.get("ok"):
        return f"版本: 本机 {app} · 未连上版本服务(跳过更新检查)"
    parts = [f"本机 {app}"]
    latest = st.get("latest")
    if latest:
        parts.append("已是最新" if st.get("up_to_date") else f"有新版 {latest}")
    if not st.get("supported"):
        parts.append("⚠版本过低需更新")
    g, sg = st.get("game"), st.get("server_game")
    if g or sg:
        gm = "匹配" if st.get("game_match") else ("不匹配" if st.get("game_match") is False else "未知")
        parts.append(f"游戏版本 {g or '未知'}/{sg or '未知'}({gm})")
    line = "版本: " + " · ".join(parts)
    if st.get("notice"):
        line += " · " + str(st["notice"])
    return line
