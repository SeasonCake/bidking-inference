"""One-shot startup version status shared by the capture process and overlay UI.

The capture process already performs one best-effort ``/version`` check at startup.
Persisting its small, non-sensitive result lets the overlay surface an update without
issuing a second request or turning version discovery into another heartbeat.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable


STATUS_FILENAME = "startup_version_status.json"
STATUS_SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 120.0


def _optional_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def _version_tuple(value: Any) -> tuple[int, int, int, int, int, int] | None:
    """Parse one exact product version without letting parser failures escape."""
    if type(value) is not str or not value or value != value.strip():
        return None
    try:
        from .version_check import _vtuple

        return _vtuple(value)
    except Exception:
        return None


def _version_text(value: Any) -> str | None:
    """Accept only an exact, already-canonical product version string."""
    return value if _version_tuple(value) is not None else None


def _finite_number(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _optional_bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def startup_version_status_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / STATUS_FILENAME


def _status_payload(
    status: dict[str, Any],
    *,
    checked_at: float | None = None,
    writer_pid: int | None = None,
) -> dict[str, Any]:
    """Reduce a version response to the fields the UI needs.

    Do not persist server base candidates, request errors or response bodies here: the
    file is part of the user's diagnostic directory and is only an update-status bridge.
    """
    if type(status) is not dict:
        status = {}
    checked_value = _finite_number(time.time() if checked_at is None else checked_at)
    writer_value = _positive_int(os.getpid() if writer_pid is None else writer_pid)
    identity_valid = checked_value is not None and writer_value is not None
    app = _version_text(status.get("app"))
    latest = _version_text(status.get("latest"))
    minimum_raw = status.get("min")
    minimum = None if minimum_raw is None else _version_text(minimum_raw)
    versions_valid = (
        app is not None
        and latest is not None
        and (minimum_raw is None or minimum is not None)
    )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "checked_at": checked_value,
        "writer_pid": writer_value,
        "app": app,
        "latest": latest,
        "min": minimum,
        "url": _optional_text(status.get("url"), limit=2048) or "",
        "notice": _optional_text(status.get("notice"), limit=800) or "",
        "ok": status.get("ok") is True and versions_valid and identity_valid,
        "reachable": status.get("reachable") is True,
        "up_to_date": _optional_bool(status.get("up_to_date")),
        "supported": _optional_bool(status.get("supported")),
    }


def write_startup_version_status(
    log_dir: str | Path,
    status: dict[str, Any],
    *,
    checked_at: float | None = None,
    writer_pid: int | None = None,
) -> Path:
    """Atomically publish the one-shot startup check for the overlay."""
    path = startup_version_status_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _status_payload(status, checked_at=checked_at, writer_pid=writer_pid)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return path


def check_and_publish_startup_version(
    log_dir: str | Path,
    *,
    timeout: float = 4.0,
    checker: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run exactly one version-check operation and publish its UI-safe result."""
    if checker is None:
        from .version_check import check_from_local_config

        checker = check_from_local_config
    try:
        status = checker(timeout=timeout)
        if type(status) is not dict:
            status = {"ok": False, "reachable": False}
    except Exception:
        status = {"ok": False, "reachable": False}
    write_startup_version_status(log_dir, status)
    return status


def read_recent_startup_version_status(
    log_dir: str | Path,
    *,
    app_version: str,
    writer_pids: tuple[int, ...] = (),
    now: float | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    """Read only a fresh result from this launch/current monitor."""
    expected_app = _version_text(app_version)
    if expected_app is None:
        return None
    path = startup_version_status_path(log_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if (
            type(payload) is not dict
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != STATUS_SCHEMA_VERSION
        ):
            return None
        checked_at = _finite_number(payload.get("checked_at"))
        current_time = _finite_number(time.time() if now is None else now)
        maximum_age = _finite_number(max_age_seconds)
        if checked_at is None or current_time is None or maximum_age is None:
            return None
        age = current_time - checked_at
        if age < -5.0 or age > max(0.0, maximum_age):
            return None
        if _version_text(payload.get("app")) != expected_app:
            return None
        writer_pid = _positive_int(payload.get("writer_pid"))
        if writer_pid is None:
            return None
        expected_pids = {pid for pid in writer_pids if _positive_int(pid) is not None}
        if expected_pids and writer_pid not in expected_pids:
            return None
        return payload
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_update_notice(status: dict[str, Any] | None, *, app_version: str) -> dict[str, Any]:
    """Map startup status to the compact header badge contract."""
    current_text = _version_text(app_version)
    base_tip = f"Hero Ref v{current_text or ''}"
    if type(status) is not dict or status.get("ok") is not True:
        return {
            "visible": False,
            "required": False,
            "label": "",
            "url": "",
            "tooltip": base_tip + "\n本次未能检查更新，不影响计算器使用。",
        }

    latest = _version_text(status.get("latest"))
    status_app = _version_text(status.get("app"))
    current_tuple = _version_tuple(current_text)
    latest_tuple = _version_tuple(latest)
    if current_tuple is None or latest_tuple is None or status_app != current_text:
        return {
            "visible": False,
            "required": False,
            "label": "",
            "url": "",
            "tooltip": base_tip + "\n服务器版本信息格式无效，已忽略本次提示。",
        }

    if latest_tuple <= current_tuple:
        latest_text = latest or current_text
        return {
            "visible": False,
            "required": False,
            "label": "",
            "url": "",
            "tooltip": base_tip + f"\n已是最新版（服务器：v{latest_text}）。",
        }

    minimum_raw = status.get("min")
    minimum = None if minimum_raw is None else _version_text(minimum_raw)
    minimum_tuple = _version_tuple(minimum) if minimum is not None else None
    if minimum_raw is not None and minimum_tuple is None:
        return {
            "visible": False,
            "required": False,
            "label": "",
            "url": "",
            "tooltip": base_tip + "\n服务器版本信息格式无效，已忽略本次提示。",
        }
    required = bool(minimum_tuple is not None and current_tuple < minimum_tuple)
    notice = _optional_text(status.get("notice"), limit=800)
    download_url = _optional_text(status.get("url"), limit=2048) or ""
    tooltip_lines = [
        f"当前：v{current_text}",
        f"最新：v{latest}",
        (
            "当前版本已停止支持，请立即更新。"
            if required
            else (
                "发现新版本，点击打开下载地址。"
                if download_url
                else "发现新版本，请联系作者获取最新版。"
            )
        ),
    ]
    if notice:
        tooltip_lines.append(notice)
    return {
        "visible": True,
        "required": required,
        "label": "需更新" if required else "有新版",
        "url": download_url,
        "tooltip": "\n".join(tooltip_lines),
    }
