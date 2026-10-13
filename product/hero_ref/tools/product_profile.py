"""Auditable product-surface profiles for packaged Hero Ref clients.

This module controls presentation only.  It must never grant activation or Pet
entitlements; those remain authoritative service responses.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PRODUCT_PROFILE_STANDARD = "standard"
PRODUCT_PROFILE_NETWORK_DIAGNOSTIC = "network-diagnostic"
PRODUCT_PROFILES = (
    PRODUCT_PROFILE_STANDARD,
    PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
)

_MANIFEST_PATTERN = re.compile(r"^ProductProfile:\s*(\S+)\s*$", re.MULTILINE)
_STANDARD_MANUAL_TITLE = "BidKing Live 悬浮窗 — 公版使用说明"
_DIAGNOSTIC_MANUAL_TITLE = "BidKing Live 悬浮窗 — 网络诊断内测包使用说明"

NETWORK_DIAGNOSTIC_BADGE = "网络诊断内测"
NETWORK_DIAGNOSTIC_TOOLTIP = (
    "仅供网络问题群定向测试\n"
    "保留已有 token 激活与计算\n"
    "购买/再次购买与猫猫新权益不在本包启用"
)
NETWORK_DIAGNOSTIC_DIALOG_LINES = (
    "网络诊断内测包",
    "保留已有 token 激活与计算功能",
    "购买/再次购买与猫猫新权益暂不在本包启用",
    "90 天猫猫等新权益以线上正式服务为准",
)
STANDARD_TOKEN_ENTRY_ACTION_TEXT = "".join(("点此输入/", "购买 token"))
NETWORK_DIAGNOSTIC_TOKEN_ENTRY_ACTION_TEXT = "输入已有 token"


def _standard_price_text(days: str, major: str, minor: str) -> str:
    return "".join((days, "天 ¥", major, ".", minor))


STANDARD_SUBSCRIPTION_PRICE_ROWS = (
    (
        "绑定设备：",
        (
            _standard_price_text("30", "15", "88"),
            _standard_price_text("90", "35", "88"),
            _standard_price_text("365", "88", "88"),
        ),
    ),
    (
        "可换绑：",
        (
            _standard_price_text("30", "24", "88"),
            _standard_price_text("90", "48", "88"),
            _standard_price_text("365", "118", "88"),
        ),
    ),
)


def normalize_product_profile(value: object) -> str:
    """Return a known presentation profile; malformed values fail safe to standard."""
    text = str(value or "").strip().lower()
    if text == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC:
        return PRODUCT_PROFILE_NETWORK_DIAGNOSTIC
    return PRODUCT_PROFILE_STANDARD


def resolve_product_profile(start_path: Path | None = None) -> str:
    """Read the nearest packaged BUILD_MANIFEST, otherwise use the standard profile."""
    start = Path(start_path).resolve() if start_path is not None else Path(__file__).resolve()
    for parent in start.parents[:6]:
        manifest = parent / "BUILD_MANIFEST.txt"
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _MANIFEST_PATTERN.search(text)
        if match:
            return normalize_product_profile(match.group(1))
        # A packaged manifest without the explicit marker is a standard package.
        return PRODUCT_PROFILE_STANDARD
    return PRODUCT_PROFILE_STANDARD


def is_network_diagnostic_profile(value: object) -> bool:
    return normalize_product_profile(value) == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC


def token_entry_action_text(product_profile: object) -> str:
    if is_network_diagnostic_profile(product_profile):
        return NETWORK_DIAGNOSTIC_TOKEN_ENTRY_ACTION_TEXT
    return STANDARD_TOKEN_ENTRY_ACTION_TEXT


def _replace_section(
    text: str,
    *,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError(f"manual section missing: {start_marker!r} -> {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def render_package_manual_text(source: str, product_profile: object) -> str:
    """Render the staged manual while keeping the source/default copy unchanged."""
    if not is_network_diagnostic_profile(product_profile):
        return source
    if _STANDARD_MANUAL_TITLE not in source:
        raise ValueError("standard package manual title is missing")
    rendered = source.replace(
        _STANDARD_MANUAL_TITLE,
        _DIAGNOSTIC_MANUAL_TITLE,
        1,
    )
    rendered = _replace_section(
        rendered,
        start_marker="【激活与试用】",
        end_marker="【启动会做什么 · 需要什么】",
        replacement="""【激活与试用 · 网络诊断内测包】
· 本包仅供网络问题群定向测试，保留已有 token 激活与计算功能。
· 软件标准新设备试用为【12 小时】；最终以悬浮窗显示的服务器额度为准。
· 已有 token：点标题下方「输入已有 token」→ 粘贴 token → 点「激活」，无需重启。
· 购买/再次购买与猫猫新权益暂不在本包启用，以线上正式服务为准。
· 本包不代表 90 天猫猫等新权益已获得服务端授权。""",
    )
    rendered = _replace_section(
        rendered,
        start_marker="【购买 / 续费 / 联系】",
        end_marker="祝竞拍愉快",
        replacement="""【网络诊断内测支持】
· 本包不提供购买、再次购买或更换档位入口，请勿根据本包截图判断线上价格或权益。
· 已有 token 可继续激活并正常使用计算器；猫猫新权益以线上正式服务为准。
· 联系方式：GitHub Issues · project discussion（请将诊断结果私发支持人员，不要公开上传）。""",
    )
    return rendered.replace(
        STANDARD_TOKEN_ENTRY_ACTION_TEXT,
        NETWORK_DIAGNOSTIC_TOKEN_ENTRY_ACTION_TEXT,
    )


def network_diagnostic_boundary_text() -> str:
    return """BidKing Live 网络诊断内测包
================================

ProductProfile: network-diagnostic

本包仅供网络问题群定向测试，保留已有 token 激活与计算功能。
购买、再次购买、更换档位与猫猫新权益暂不在本包启用。
90 天猫猫等新权益以线上正式服务为准；本包不代表服务端已授权。

首次使用或“能进游戏但不计算”时：
1. 完整解压程序包，不要在 ZIP 内直接运行。
2. 将整个文件夹（解压后的 Hero Ref/BidKingLive）加入火绒或 360 的信任区/白名单。
3. 完整退出火绒、360、游戏加速器；仅关闭主窗口可能仍有后台防护或服务。
4. 先启动 Hero Ref，等待悬浮窗出现，再启动游戏。
5. 仍不计算时按同一顺序重启，再收集脱敏诊断私发；不要反复更换 token。
6. 不要卸载或永久关闭安全软件；测试后可恢复防护。
   若恢复后再次失效，请保持安全设置并等待兼容更新。
Hero Ref 不会自动修改安全软件、Windows 安全功能、代理、路由或 DNS。

如需协助，请将「诊断工具」生成的报告私发支持人员，不要发群或公开上传。
联系：GitHub Issues ｜ project discussion
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render package product-profile materials.")
    parser.add_argument("--profile", choices=PRODUCT_PROFILES, required=True)
    parser.add_argument("--manual-source", type=Path, required=True)
    parser.add_argument("--manual-output", type=Path, required=True)
    parser.add_argument("--cohort-note-output", type=Path)
    args = parser.parse_args(argv)

    source = args.manual_source.read_text(encoding="utf-8-sig")
    rendered = render_package_manual_text(source, args.profile)
    args.manual_output.parent.mkdir(parents=True, exist_ok=True)
    args.manual_output.write_text(rendered, encoding="utf-8-sig")
    if args.cohort_note_output is not None:
        if not is_network_diagnostic_profile(args.profile):
            parser.error("--cohort-note-output is only valid for network-diagnostic")
        args.cohort_note_output.parent.mkdir(parents=True, exist_ok=True)
        args.cohort_note_output.write_text(
            network_diagnostic_boundary_text(),
            encoding="utf-8-sig",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
